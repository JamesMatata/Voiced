from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.forms import UserCreationForm
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.contrib.sites.shortcuts import get_current_site
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required

from accounts.tokens import account_activation_token


def register(request):
    email_value = ""
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        email_value = request.POST.get('email', "").strip()

        existing_user = User.objects.filter(email=email_value).first()

        if existing_user:
            if not existing_user.is_active:
                current_site = get_current_site(request)
                mail_subject = 'Activate your Voiced Account'
                message = render_to_string('accounts/acc_active_email.html', {
                    'user': existing_user,
                    'domain': current_site.domain,
                    'uid': urlsafe_base64_encode(force_bytes(existing_user.pk)),
                    'token': account_activation_token.make_token(existing_user),
                })

                send_mail(mail_subject, message, 'noreply@voiced.co.ke', [email_value])

                messages.info(request, 'This email is already registered. A new activation link has been sent.')
                return redirect('login')
            else:
                messages.warning(request, 'This email is already active. Please log in to your account.')
                return redirect('login')

        if form.is_valid() and email_value:
            user = form.save(commit=False)
            user.email = email_value
            user.is_active = False
            user.save()

            current_site = get_current_site(request)
            mail_subject = 'Activate your Voiced Account'
            message = render_to_string('accounts/acc_active_email.html', {
                'user': user,
                'domain': current_site.domain,
                'uid': urlsafe_base64_encode(force_bytes(user.pk)),
                'token': account_activation_token.make_token(user),
            })

            send_mail(mail_subject, message, 'noreply@voiced.co.ke', [email_value])

            messages.success(request, 'Confirm your email to complete registration.')
            return redirect('login')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = UserCreationForm()

    return render(request, 'accounts/register.html', {
        'form': form,
        'email_value': email_value
    })


def activate(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist) as e:
        print(f"Activation Error (User/UID): {e}")
        user = None

    if user is not None and account_activation_token.check_token(user, token):
        user.is_active = True
        user.save()
        from django.contrib.auth import login
        login(request, user)
        messages.success(request, 'Identity verified. Welcome to the platform.')
        return redirect('home')
    else:
        if user:
            print(f"Token Check Failed for user: {user.username}")
        else:
            print("User was not found during activation")

        messages.error(request, 'This activation link is invalid or has expired.')
        return redirect('register')

@login_required(login_url='login')
def profile_view(request):
    user = request.user
    profile = user.profile

    password_form = PasswordChangeForm(user)

    if request.method == 'POST':
        form_type = request.POST.get('form_type')

        if form_type == 'update_preferences':
            user.email = request.POST.get('email', user.email)
            user.save()

            profile.language = request.POST.get('language', 'en')
            profile.use_alias = request.POST.get('use_alias') == 'on'
            profile.email_notifications = request.POST.get('email_notifications') == 'on'
            profile.save()

            messages.success(request, 'Your preferences have been updated successfully.')
            return redirect('profile')

        elif form_type == 'change_password':
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Your password was successfully updated!')
                return redirect('profile')
            else:
                messages.error(request, 'Please correct the error below to change your password.')

    context = {
        'profile': profile,
        'password_form': password_form
    }
    return render(request, 'accounts/profile.html', context)