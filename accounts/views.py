from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods, require_POST
from decimal import Decimal
from django.contrib.auth import login, authenticate
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from django.core.mail import EmailMultiAlternatives
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.core.validators import validate_email
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
import random
from datetime import timedelta

from accounts.models import AccountOtp, EmailChangeAttempt, VerificationAttempt
from accounts.services import (
    promote_user_ongoing_votes_to_verified,
    send_identity_verified_sms,
    verify_smile_identity,
)
from payments.models import Transaction
from payments.services.wallet_ops import get_or_create_wallet
from payments.services.mpesa_flow import start_stk_topup

OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 30


def _send_code_email(*, email: str, code: str, title: str):
    context = {
        "title": title,
        "code": code,
        "minutes": 10,
        "support_email": getattr(settings, "DEFAULT_FROM_EMAIL", "info@voiced.co.ke"),
    }
    subject = "Your Voiced verification code"
    text_body = render_to_string("emails/otp_code.txt", context)
    html_body = render_to_string("emails/otp_code.html", context)
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "info@voiced.co.ke"),
        to=[email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def login_view(request):
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()

        user = authenticate(request, username=username, password=password)
        # Fallback 1: case-insensitive username lookup then authenticate with canonical username.
        if user is None and username:
            canonical_user = User.objects.filter(username__iexact=username).first()
            if canonical_user:
                user = authenticate(request, username=canonical_user.username, password=password)
        # Fallback 2: allow login identifier to be email.
        if user is None and "@" in username:
            email_user = User.objects.filter(email__iexact=username).first()
            if email_user:
                user = authenticate(request, username=email_user.username, password=password)

        if user is not None:
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect("home")

        existing = User.objects.filter(username__iexact=username).first()
        if existing and check_password(password, existing.password) and not existing.is_active:
            cooldown = _otp_cooldown_remaining(request, AccountOtp.Purpose.EMAIL_ACTIVATION)
            if cooldown > 0:
                messages.error(request, f"Please wait {cooldown}s before requesting another verification code.")
            else:
                _issue_user_otp(existing, AccountOtp.Purpose.EMAIL_ACTIVATION, existing.email)
                _mark_otp_sent_now(request, AccountOtp.Purpose.EMAIL_ACTIVATION)
                messages.info(request, "Your account is pending email verification. A new OTP has been sent.")
            request.session["verify_email_user_id"] = existing.id
            return redirect("verify_email_otp")

        return render(
            request,
            "accounts/login.html",
            {"login_error": "Your username and password didn't match. Please try again."},
            status=200,
        )

    return render(request, "accounts/login.html")


def register(request):
    email_value = ""
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        email_value = request.POST.get('email', "").strip()
        username_value = request.POST.get("username", "").strip()

        existing_user = User.objects.filter(email__iexact=email_value).first()
        if not existing_user and username_value:
            existing_user = User.objects.filter(username__iexact=username_value).first()

        if existing_user:
            if not existing_user.is_active:
                cooldown = _otp_cooldown_remaining(request, AccountOtp.Purpose.EMAIL_ACTIVATION)
                if cooldown > 0:
                    messages.error(request, f"Please wait {cooldown}s before requesting another verification code.")
                    request.session["verify_email_user_id"] = existing_user.id
                    return redirect('verify_email_otp')
                target_email = existing_user.email or email_value
                _issue_user_otp(existing_user, AccountOtp.Purpose.EMAIL_ACTIVATION, target_email)
                _mark_otp_sent_now(request, AccountOtp.Purpose.EMAIL_ACTIVATION)
                messages.info(request, 'A new verification code has been sent.')
                return redirect('verify_email_otp')
            else:
                messages.warning(request, 'This email is already active. Please log in to your account.')
                return redirect('login')

        if form.is_valid() and email_value:
            user = form.save(commit=False)
            user.email = email_value
            user.is_active = False
            user.save()
            cooldown = _otp_cooldown_remaining(request, AccountOtp.Purpose.EMAIL_ACTIVATION)
            if cooldown > 0:
                messages.error(request, f"Please wait {cooldown}s before requesting another verification code.")
                request.session["verify_email_user_id"] = user.id
                return redirect('verify_email_otp')
            _issue_user_otp(user, AccountOtp.Purpose.EMAIL_ACTIVATION, email_value)
            _mark_otp_sent_now(request, AccountOtp.Purpose.EMAIL_ACTIVATION)
            request.session["verify_email_user_id"] = user.id
            messages.success(request, 'A verification code was sent to your email.')
            return redirect('verify_email_otp')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = UserCreationForm()

    return render(request, 'accounts/register.html', {
        'form': form,
        'email_value': email_value
    })


def activate(request, uidb64, token):
    messages.info(request, 'Email verification now uses OTP code. Please enter your code.')
    return redirect('verify_email_otp')


def _issue_user_otp(user, purpose: str, email: str):
    AccountOtp.objects.filter(
        user=user,
        purpose=purpose,
        status=AccountOtp.Status.PENDING,
    ).update(status=AccountOtp.Status.CANCELLED)
    code = f"{random.randint(0, 999999):06d}"
    AccountOtp.objects.create(
        user=user,
        purpose=purpose,
        email=email,
        code_hash=make_password(code),
        status=AccountOtp.Status.PENDING,
        attempts=0,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    _send_code_email(email=email, code=code, title="Verify your Voiced account")


def _consume_otp(user, purpose: str, code: str):
    otp = (
        AccountOtp.objects.filter(user=user, purpose=purpose, status=AccountOtp.Status.PENDING)
        .order_by("-created_at")
        .first()
    )
    if not otp:
        return False, "No active code found. Request a new one."
    if otp.expires_at < timezone.now():
        otp.status = AccountOtp.Status.EXPIRED
        otp.save(update_fields=["status", "updated_at"])
        return False, "Code expired. Request a new one."
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        otp.status = AccountOtp.Status.CANCELLED
        otp.save(update_fields=["status", "updated_at"])
        return False, "Too many failed attempts. Request a new OTP code."
    otp.attempts += 1
    if not check_password(code, otp.code_hash):
        attempts_left = max(0, OTP_MAX_ATTEMPTS - otp.attempts)
        if attempts_left == 0:
            otp.status = AccountOtp.Status.CANCELLED
            otp.save(update_fields=["attempts", "status", "updated_at"])
            return False, "Too many failed attempts. OTP locked. Request a new code."
        otp.save(update_fields=["attempts", "updated_at"])
        return False, f"Invalid code. {attempts_left} attempt(s) left."
    otp.status = AccountOtp.Status.VERIFIED
    otp.save(update_fields=["attempts", "status", "updated_at"])
    return True, otp


def _otp_cooldown_remaining(request, purpose: str) -> int:
    sent_map = request.session.get("otp_last_sent_at", {})
    raw = sent_map.get(purpose)
    if not raw:
        return 0
    parsed = parse_datetime(raw)
    if not parsed:
        return 0
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    elapsed = int((timezone.now() - parsed).total_seconds())
    remaining = OTP_RESEND_COOLDOWN_SECONDS - elapsed
    return max(0, remaining)


def _mark_otp_sent_now(request, purpose: str) -> None:
    sent_map = request.session.get("otp_last_sent_at", {})
    sent_map[purpose] = timezone.now().isoformat()
    request.session["otp_last_sent_at"] = sent_map


def _password_change_verified(request):
    raw = request.session.get("password_change_otp_verified_at")
    if not raw:
        return False
    parsed = parse_datetime(raw)
    if not parsed:
        return False
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return (timezone.now() - parsed) <= timedelta(minutes=10)


@require_http_methods(["GET", "POST"])
def verify_email_otp(request):
    user = None
    user_id = request.session.get("verify_email_user_id")
    if user_id:
        user = User.objects.filter(id=user_id).first()
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            email = (request.POST.get("email") or "").strip()
            if not user and email:
                user = User.objects.filter(email__iexact=email).first()
            if not user:
                messages.error(request, "User not found for this email.")
                return redirect("verify_email_otp")
            cooldown = _otp_cooldown_remaining(request, AccountOtp.Purpose.EMAIL_ACTIVATION)
            if cooldown > 0:
                messages.error(request, f"Please wait {cooldown}s before requesting another code.")
                request.session["verify_email_user_id"] = user.id
                return redirect("verify_email_otp")
            _issue_user_otp(user, AccountOtp.Purpose.EMAIL_ACTIVATION, user.email)
            _mark_otp_sent_now(request, AccountOtp.Purpose.EMAIL_ACTIVATION)
            request.session["verify_email_user_id"] = user.id
            messages.success(request, "A new verification code has been sent.")
            return redirect("verify_email_otp")
        email = (request.POST.get("email") or "").strip()
        code = (request.POST.get("code") or "").strip()
        if not user and email:
            user = User.objects.filter(email__iexact=email).first()
        if not user:
            messages.error(request, "User not found for this email.")
            return redirect("verify_email_otp")
        ok, result = _consume_otp(user, AccountOtp.Purpose.EMAIL_ACTIVATION, code)
        if not ok:
            messages.error(request, result)
            request.session["verify_email_user_id"] = user.id
            return redirect("verify_email_otp")
        user.is_active = True
        user.save(update_fields=["is_active"])
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session.pop("verify_email_user_id", None)
        messages.success(request, "Email verified successfully.")
        return redirect("home")
    return render(request, "accounts/verify_email_otp.html", {"email_hint": getattr(user, "email", "")})


@require_http_methods(["GET", "POST"])
def password_reset_request_otp(request):
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if user:
            cooldown = _otp_cooldown_remaining(request, AccountOtp.Purpose.PASSWORD_RESET)
            if cooldown > 0:
                messages.error(request, f"Please wait {cooldown}s before requesting another reset code.")
                request.session["password_reset_user_id"] = user.id
                return redirect("password_reset_done")
            _issue_user_otp(user, AccountOtp.Purpose.PASSWORD_RESET, email)
            _mark_otp_sent_now(request, AccountOtp.Purpose.PASSWORD_RESET)
            request.session["password_reset_user_id"] = user.id
        messages.success(request, "If the email exists, a reset code has been sent.")
        return redirect("password_reset_done")
    return render(request, "accounts/password_reset_form.html")


@require_http_methods(["GET", "POST"])
def password_reset_verify_otp(request):
    uid = request.session.get("password_reset_user_id")
    user = User.objects.filter(id=uid).first() if uid else None
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            if not user:
                messages.error(request, "Start password reset again.")
                return redirect("password_reset")
            cooldown = _otp_cooldown_remaining(request, AccountOtp.Purpose.PASSWORD_RESET)
            if cooldown > 0:
                messages.error(request, f"Please wait {cooldown}s before requesting another code.")
                return redirect("password_reset_done")
            _issue_user_otp(user, AccountOtp.Purpose.PASSWORD_RESET, user.email)
            _mark_otp_sent_now(request, AccountOtp.Purpose.PASSWORD_RESET)
            messages.success(request, "A new reset code has been sent to your email.")
            return redirect("password_reset_done")
        code = (request.POST.get("code") or "").strip()
        if not user:
            messages.error(request, "Start password reset again.")
            return redirect("password_reset")
        ok, result = _consume_otp(user, AccountOtp.Purpose.PASSWORD_RESET, code)
        if not ok:
            messages.error(request, result)
            return redirect("password_reset_done")
        request.session["password_reset_verified_user_id"] = user.id
        messages.success(request, "Code verified. Set your new password.")
        return redirect("password_reset_confirm")
    return render(request, "accounts/password_reset_done.html", {"email_hint": getattr(user, "email", "")})


@require_http_methods(["GET", "POST"])
def password_reset_set_new(request):
    uid = request.session.get("password_reset_verified_user_id")
    user = User.objects.filter(id=uid).first() if uid else None
    if not user:
        messages.error(request, "Password reset session expired. Start again.")
        return redirect("password_reset")
    if request.method == "POST":
        p1 = (request.POST.get("new_password1") or "").strip()
        p2 = (request.POST.get("new_password2") or "").strip()
        if not p1 or p1 != p2:
            messages.error(request, "Passwords do not match.")
            return redirect("password_reset_confirm")
        user.set_password(p1)
        user.save(update_fields=["password"])
        request.session.pop("password_reset_user_id", None)
        request.session.pop("password_reset_verified_user_id", None)
        messages.success(request, "Password updated successfully. Please log in.")
        return redirect("login")
    return render(request, "accounts/password_reset_confirm.html")

@login_required(login_url='login')
def profile_view(request):
    user = request.user
    profile = user.profile
    wallet = get_or_create_wallet(user)
    latest_verification_attempt = (
        VerificationAttempt.objects.filter(user=user).order_by("-attempted_at").first()
    )

    if request.method == 'POST':
        form_type = request.POST.get('form_type')

        if form_type == 'update_preferences':
            # Email changes are handled via verified code modal flow.

            selected_language = (request.POST.get('language', 'en') or 'en').split('-')[0]
            if selected_language == "sh":
                selected_language = "sr"
            if selected_language not in {"en", "sw", "sr"}:
                selected_language = "en"
            profile.language = selected_language
            profile.use_alias = request.POST.get('use_alias') == 'on'
            profile.email_notifications = request.POST.get('email_notifications') == 'on'
            profile.save()
            request.session["django_language"] = selected_language

            messages.success(request, 'Your preferences have been updated successfully.')
            return redirect('profile')

        elif form_type == 'change_password':
            messages.info(request, 'Use the OTP password flow to change your password.')
            return redirect('profile')

        elif form_type == 'submit_kyc':
            attempt = VerificationAttempt.objects.filter(user=user).order_by("-attempted_at").first()
            if not attempt or attempt.payment_status != VerificationAttempt.PaymentStatus.PAID:
                messages.error(request, 'Pay KES 60 verification fee first.')
                return redirect('profile')

            first_name = (request.POST.get('first_name') or '').strip()
            last_name = (request.POST.get('last_name') or '').strip()
            id_number = (request.POST.get('id_number') or '').strip()
            if not first_name or not last_name or not id_number:
                messages.error(request, 'First name, last name and ID number are required.')
                return redirect('profile')

            result = verify_smile_identity(
                id_number=id_number,
                first_name=first_name,
                last_name=last_name,
            )
            if not result.get("ok"):
                attempt.status = VerificationAttempt.Status.FAILED
                attempt.failure_reason = result.get("message") or "KYC request failed."
                attempt.save(update_fields=["status", "failure_reason", "updated_at"])
                messages.error(request, attempt.failure_reason)
                return redirect('profile')

            if result.get("matched"):
                was_kenyan = bool(profile.is_kenyan)
                profile.is_kenyan = True
                profile.id_number = id_number
                profile.save(update_fields=["is_kenyan", "id_number"])
                if not was_kenyan:
                    promote_user_ongoing_votes_to_verified(user=user)
                attempt.status = VerificationAttempt.Status.VERIFIED
                attempt.failure_reason = ""
                attempt.save(update_fields=["status", "failure_reason", "updated_at"])
                messages.success(request, 'Identity verification successful. You can now vote officially.')
                send_identity_verified_sms(user=user, first_name=first_name)
                request.session["kyc_just_verified"] = True
            else:
                attempt.status = VerificationAttempt.Status.FAILED
                attempt.failure_reason = "The ID details provided do not match the National Registry. Please check and try again."
                attempt.save(update_fields=["status", "failure_reason", "updated_at"])
                messages.error(request, attempt.failure_reason)
            return redirect('profile')

    context = {
        'profile': profile,
        'wallet': wallet,
        'verification_fee': Decimal('60.00'),
        'latest_verification_attempt': latest_verification_attempt,
        'kyc_just_verified': bool(request.session.pop("kyc_just_verified", False)),
    }
    return render(request, 'accounts/profile.html', context)


@login_required(login_url='login')
@require_http_methods(["GET"])
def email_change_state(request):
    attempt = (
        EmailChangeAttempt.objects.filter(
            user=request.user,
            status=EmailChangeAttempt.Status.PENDING,
        )
        .order_by("-created_at")
        .first()
    )
    if not attempt:
        return JsonResponse({"ok": True, "has_pending": False, "current_email": request.user.email})
    if attempt.expires_at < timezone.now():
        attempt.status = EmailChangeAttempt.Status.EXPIRED
        attempt.save(update_fields=["status", "updated_at"])
        return JsonResponse({"ok": True, "has_pending": False, "current_email": request.user.email})
    return JsonResponse(
        {
            "ok": True,
            "has_pending": True,
            "current_email": request.user.email,
            "pending_email": attempt.new_email,
            "attempts_left": max(0, 5 - attempt.attempts),
            "expires_at": attempt.expires_at.isoformat(),
        }
    )


@login_required(login_url='login')
@require_POST
def email_change_request_code(request):
    new_email = (request.POST.get("new_email") or "").strip().lower()
    try:
        validate_email(new_email)
    except Exception:
        return JsonResponse({"ok": False, "error": "Enter a valid email address."}, status=400)
    if new_email == (request.user.email or "").lower():
        return JsonResponse({"ok": False, "error": "This is already your current email."}, status=400)
    if User.objects.filter(email__iexact=new_email).exclude(pk=request.user.pk).exists():
        return JsonResponse({"ok": False, "error": "That email is already in use."}, status=400)

    EmailChangeAttempt.objects.filter(
        user=request.user,
        status=EmailChangeAttempt.Status.PENDING,
    ).update(status=EmailChangeAttempt.Status.CANCELLED)

    code = f"{random.randint(0, 999999):06d}"
    attempt = EmailChangeAttempt.objects.create(
        user=request.user,
        new_email=new_email,
        code_hash=make_password(code),
        status=EmailChangeAttempt.Status.PENDING,
        attempts=0,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    _send_code_email(email=new_email, code=code, title="Confirm your new email")
    return JsonResponse(
        {
            "ok": True,
            "message": f"A verification code has been sent to {new_email}.",
            "pending_email": new_email,
            "attempts_left": 5,
            "expires_at": attempt.expires_at.isoformat(),
        }
    )


@login_required(login_url='login')
@require_POST
def email_change_verify_code(request):
    code = (request.POST.get("code") or "").strip()
    if not code.isdigit() or len(code) != 6:
        return JsonResponse({"ok": False, "error": "Enter the 6-digit verification code."}, status=400)
    attempt = (
        EmailChangeAttempt.objects.filter(
            user=request.user,
            status=EmailChangeAttempt.Status.PENDING,
        )
        .order_by("-created_at")
        .first()
    )
    if not attempt:
        return JsonResponse({"ok": False, "error": "No pending email change request."}, status=404)
    if attempt.expires_at < timezone.now():
        attempt.status = EmailChangeAttempt.Status.EXPIRED
        attempt.save(update_fields=["status", "updated_at"])
        return JsonResponse({"ok": False, "error": "Code expired. Request a new code."}, status=410)

    attempt.attempts += 1
    if not check_password(code, attempt.code_hash):
        attempt.save(update_fields=["attempts", "updated_at"])
        return JsonResponse(
            {"ok": False, "error": "Invalid code.", "attempts_left": max(0, 5 - attempt.attempts)},
            status=422,
        )

    request.user.email = attempt.new_email
    request.user.save(update_fields=["email"])
    attempt.status = EmailChangeAttempt.Status.VERIFIED
    attempt.save(update_fields=["attempts", "status", "updated_at"])
    return JsonResponse({"ok": True, "message": "Email updated successfully.", "email": request.user.email})


@login_required(login_url='login')
@require_http_methods(["GET"])
def password_change_state(request):
    attempt = (
        AccountOtp.objects.filter(
            user=request.user,
            purpose=AccountOtp.Purpose.PASSWORD_CHANGE,
            status=AccountOtp.Status.PENDING,
        )
        .order_by("-created_at")
        .first()
    )
    has_pending = False
    attempts_left = OTP_MAX_ATTEMPTS
    expires_at = ""
    lockout = False
    if attempt:
        if attempt.expires_at < timezone.now():
            attempt.status = AccountOtp.Status.EXPIRED
            attempt.save(update_fields=["status", "updated_at"])
        else:
            has_pending = True
            attempts_left = max(0, OTP_MAX_ATTEMPTS - attempt.attempts)
            lockout = attempts_left == 0
            expires_at = attempt.expires_at.isoformat()
    cooldown_remaining = _otp_cooldown_remaining(request, AccountOtp.Purpose.PASSWORD_CHANGE)
    return JsonResponse(
        {
            "ok": True,
            "has_pending": has_pending,
            "attempts_left": attempts_left,
            "expires_at": expires_at,
            "otp_verified": _password_change_verified(request),
            "lockout": lockout,
            "cooldown_remaining": cooldown_remaining,
        }
    )


@login_required(login_url='login')
@require_POST
def password_change_request_code(request):
    cooldown = _otp_cooldown_remaining(request, AccountOtp.Purpose.PASSWORD_CHANGE)
    if cooldown > 0:
        return JsonResponse(
            {"ok": False, "error": f"Please wait {cooldown}s before requesting a new OTP.", "cooldown_remaining": cooldown},
            status=429,
        )
    _issue_user_otp(request.user, AccountOtp.Purpose.PASSWORD_CHANGE, request.user.email)
    _mark_otp_sent_now(request, AccountOtp.Purpose.PASSWORD_CHANGE)
    request.session["password_change_otp_verified_at"] = ""
    return JsonResponse(
        {
            "ok": True,
            "message": f"OTP code sent to {request.user.email}.",
            "cooldown_remaining": OTP_RESEND_COOLDOWN_SECONDS,
        }
    )


@login_required(login_url='login')
@require_POST
def password_change_verify_code(request):
    code = (request.POST.get("code") or "").strip()
    if not code.isdigit() or len(code) != 6:
        return JsonResponse({"ok": False, "error": "Enter the 6-digit OTP code."}, status=400)
    ok, result = _consume_otp(request.user, AccountOtp.Purpose.PASSWORD_CHANGE, code)
    if not ok:
        return JsonResponse({"ok": False, "error": result}, status=422)
    request.session["password_change_otp_verified_at"] = timezone.now().isoformat()
    return JsonResponse({"ok": True, "message": "OTP verified. You can now set your new password."})


@login_required(login_url='login')
@require_POST
def password_change_submit(request):
    if not _password_change_verified(request):
        return JsonResponse({"ok": False, "error": "Verify OTP first before changing your password."}, status=403)
    current_password = (request.POST.get("current_password") or "").strip()
    new_password1 = (request.POST.get("new_password1") or "").strip()
    new_password2 = (request.POST.get("new_password2") or "").strip()
    form = PasswordChangeForm(
        request.user,
        {
            "old_password": current_password,
            "new_password1": new_password1,
            "new_password2": new_password2,
        },
    )
    if not form.is_valid():
        first_error = next(iter(form.errors.values()))[0] if form.errors else "Invalid password details."
        return JsonResponse({"ok": False, "error": first_error}, status=422)
    user = form.save()
    update_session_auth_hash(request, user)
    request.session["password_change_otp_verified_at"] = ""
    return JsonResponse({"ok": True, "message": "Password updated successfully."})


@login_required(login_url='login')
def wallet_view(request):
    wallet = get_or_create_wallet(request.user)
    transactions = (
        Transaction.objects.filter(user=request.user)
        .select_related('bill')
        .order_by('-created_at')[:100]
    )

    if request.method == 'POST' and request.POST.get('action') == 'topup':
        phone = (request.POST.get('phone') or '').strip()
        try:
            amount = Decimal(str(request.POST.get('amount') or '0'))
        except Exception:
            amount = Decimal('0')

        result = start_stk_topup(user=request.user, phone=phone, amount=amount)

        if request.headers.get('HX-Request'):
            if result.get('ok'):
                msg = result.get('message') or 'Check your phone to complete payment.'
                tid = result.get('transaction_id')
                meta = (
                    f'<span id="wallet-topup-txn-meta" data-transaction-id="{tid}" hidden></span>'
                    if tid
                    else ''
                )
                return HttpResponse(
                    f'<p class="text-green-800 text-xs font-bold leading-relaxed">{msg}</p>{meta}'
                )
            err = result.get('error') or 'Top-up failed.'
            return HttpResponse(
                f'<p class="text-red-700 text-xs font-bold leading-relaxed">{err}</p>',
                status=422,
            )

        if result.get('ok'):
            messages.success(request, result.get('message') or 'Check your phone to complete payment.')
        else:
            messages.error(request, result.get('error') or 'Top-up failed.')
        return redirect('wallet')

    return render(
        request,
        'accounts/wallet.html',
        {'wallet': wallet, 'transactions': transactions},
    )


@login_required(login_url='login')
@require_http_methods(["GET"])
def verification_state(request):
    attempt = VerificationAttempt.objects.filter(user=request.user).order_by("-attempted_at").first()
    if not attempt:
        return JsonResponse(
            {
                "ok": True,
                "is_kenyan": bool(request.user.profile.is_kenyan),
                "payment_required": True,
                "payment_status": "PENDING",
                "kyc_attempts": 0,
                "remaining_attempts": 2,
                "can_submit_kyc": False,
                "message": "",
            }
        )

    if (
        attempt.payment_status == VerificationAttempt.PaymentStatus.PAID
        and attempt.kyc_attempts >= 2
        and attempt.status != VerificationAttempt.Status.VERIFIED
    ):
        attempt.payment_status = VerificationAttempt.PaymentStatus.PENDING
        attempt.status = VerificationAttempt.Status.FAILED
        attempt.failure_reason = (
            "Maximum attempts reached for previous payment. A new verification fee is required."
        )
        attempt.save(update_fields=["payment_status", "status", "failure_reason", "updated_at"])

    remaining_attempts = max(0, 2 - attempt.kyc_attempts)
    can_submit = (
        attempt.payment_status == VerificationAttempt.PaymentStatus.PAID
        and remaining_attempts > 0
        and attempt.status != VerificationAttempt.Status.VERIFIED
    )
    return JsonResponse(
        {
            "ok": True,
            "is_kenyan": bool(request.user.profile.is_kenyan),
            "payment_required": not can_submit,
            "payment_status": attempt.payment_status,
            "status": attempt.status,
            "kyc_attempts": attempt.kyc_attempts,
            "remaining_attempts": remaining_attempts,
            "can_submit_kyc": can_submit,
            "message": attempt.failure_reason or "",
            "full_name": attempt.full_name or "",
            "id_number": "",
            "id_number_masked": request.user.profile.masked_id_number,
        }
    )


@login_required(login_url='login')
@require_POST
def submit_kyc_modal(request):
    first_name = (request.POST.get("first_name") or "").strip()
    last_name = (request.POST.get("last_name") or "").strip()
    id_number = (request.POST.get("id_number") or "").strip()

    if not first_name or not last_name or not id_number:
        return JsonResponse(
            {"ok": False, "error": "First name, last name, and ID number are required."},
            status=400,
        )
    if not id_number.isdigit() or len(id_number) < 6 or len(id_number) > 10:
        return JsonResponse(
            {"ok": False, "error": "Enter a valid National ID format."},
            status=400,
        )

    attempt = VerificationAttempt.objects.filter(user=request.user).order_by("-attempted_at").first()
    if not attempt or attempt.payment_status != VerificationAttempt.PaymentStatus.PAID:
        return JsonResponse({"ok": False, "error": "Payment required before verification."}, status=402)
    if attempt.kyc_attempts >= 2:
        attempt.payment_status = VerificationAttempt.PaymentStatus.PENDING
        attempt.status = VerificationAttempt.Status.FAILED
        attempt.failure_reason = "Verification failed. Payment exhausted. Please pay again to retry."
        attempt.save(update_fields=["payment_status", "status", "failure_reason", "updated_at"])
        return JsonResponse(
            {
                "ok": False,
                "error": attempt.failure_reason,
                "payment_exhausted": True,
                "remaining_attempts": 0,
            },
            status=422,
        )

    attempt.kyc_attempts += 1
    attempt.id_number = id_number
    attempt.full_name = f"{first_name} {last_name}".strip()
    attempt.save(update_fields=["kyc_attempts", "id_number", "full_name", "updated_at"])

    result = verify_smile_identity(id_number=id_number, first_name=first_name, last_name=last_name)
    if not result.get("ok"):
        attempt.status = VerificationAttempt.Status.FAILED
        attempt.failure_reason = result.get("message") or "KYC verification failed."
        attempt.save(update_fields=["status", "failure_reason", "updated_at"])
        return JsonResponse({"ok": False, "error": attempt.failure_reason}, status=502)

    if result.get("matched"):
        profile = request.user.profile
        was_kenyan = bool(profile.is_kenyan)
        profile.is_kenyan = True
        profile.id_number = id_number
        profile.save(update_fields=["is_kenyan", "id_number"])
        if not was_kenyan:
            promote_user_ongoing_votes_to_verified(user=request.user)

        attempt.status = VerificationAttempt.Status.VERIFIED
        attempt.failure_reason = ""
        attempt.save(update_fields=["status", "failure_reason", "updated_at"])
        send_identity_verified_sms(user=request.user, first_name=first_name)
        return JsonResponse({"ok": True, "verified": True, "message": "Identity verified successfully."})

    attempt.status = VerificationAttempt.Status.FAILED
    if attempt.kyc_attempts >= 2:
        attempt.payment_status = VerificationAttempt.PaymentStatus.PENDING
        attempt.failure_reason = "Verification failed. Payment exhausted. Please pay again to retry."
        attempt.save(update_fields=["status", "payment_status", "failure_reason", "updated_at"])
        return JsonResponse(
            {
                "ok": False,
                "error": attempt.failure_reason,
                "payment_exhausted": True,
                "remaining_attempts": 0,
                "first_name": first_name,
                "last_name": last_name,
                "id_number": id_number,
            },
            status=422,
        )

    attempt.failure_reason = (
        "Verification failed. This is your last attempt for this payment. Please confirm your details carefully."
    )
    attempt.save(update_fields=["status", "failure_reason", "updated_at"])
    return JsonResponse(
        {
            "ok": False,
            "error": attempt.failure_reason,
            "remaining_attempts": 1,
            "first_name": first_name,
            "last_name": last_name,
            "id_number": id_number,
        },
        status=422,
    )