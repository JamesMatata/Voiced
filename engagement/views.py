import random
import string
import os
from decimal import Decimal
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F
from django.core.cache import cache
from accounts.models import VerificationAttempt, Wallet
from bills.models import Bill, BillVote
from bills.tasks import (
    place_voice_summary_call_task,
    send_ussd_bill_summary_sms,
    send_voice_listen_charge_sms_task,
    send_vote_feedback_sms_task,
)
from bills.services.sms_gateway import send_sms_via_africastalking
from chat.moderation import check_message_toxicity
from payments.constants import VERIFICATION_FEE
from payments.models import Transaction
from payments.services.mpesa_flow import start_stk_topup


VOICE_SUMMARY_FEE = Decimal("5.00")


def _apply_vote(user, bill, vote_type: str, reason: str = ""):
    with transaction.atomic():
        bill = Bill.objects.select_for_update().get(pk=bill.pk)
        vote = BillVote.objects.filter(user=user, bill=bill).first()
        is_kenyan = bool(getattr(getattr(user, "profile", None), "is_kenyan", False))
        created = False
        if not vote:
            vote = BillVote.objects.create(
                user=user,
                bill=bill,
                vote_type=vote_type,
                reason=reason,
            )
            created = True
            updates = {
                "total_votes": F("total_votes") + 1,
                "support_count": F("support_count") + (1 if vote_type == "support" else 0),
                "oppose_count": F("oppose_count") + (1 if vote_type == "oppose" else 0),
            }
            if is_kenyan:
                updates["verified_citizen_votes"] = F("verified_citizen_votes") + 1
                updates["verified_support_count"] = F("verified_support_count") + (1 if vote_type == "support" else 0)
                updates["verified_oppose_count"] = F("verified_oppose_count") + (1 if vote_type == "oppose" else 0)
            Bill.objects.filter(pk=bill.pk).update(**updates)
        else:
            old = vote.vote_type
            vote.vote_type = vote_type
            vote.reason = reason
            vote.save(update_fields=["vote_type", "reason"])
            if old != vote_type:
                updates = {
                    "support_count": F("support_count") + (1 if vote_type == "support" else -1),
                    "oppose_count": F("oppose_count") + (1 if vote_type == "oppose" else -1),
                }
                if is_kenyan:
                    updates["verified_support_count"] = F("verified_support_count") + (1 if vote_type == "support" else -1)
                    updates["verified_oppose_count"] = F("verified_oppose_count") + (1 if vote_type == "oppose" else -1)
                Bill.objects.filter(pk=bill.pk).update(**updates)
    return vote, created


def _normalize_sms_phone(phone: str) -> str:
    raw = "".join(ch for ch in str(phone or "").strip() if ch.isdigit())
    if raw.startswith("0") and len(raw) == 10:
        raw = "254" + raw[1:]
    elif raw.startswith("7") and len(raw) == 9:
        raw = "254" + raw
    elif raw.startswith("1") and len(raw) == 9:
        raw = "254" + raw
    return raw


@csrf_exempt
def sms_callback(request):
    # Temporary sandbox diagnostic log (keep during callback testing).
    print(f"DEBUG: SMS Request received: {request.POST}")

    if request.method != "POST":
        return HttpResponse(status=200)

    from_phone = (
        request.POST.get("from")
        or request.POST.get("From")
        or request.POST.get("phoneNumber")
        or ""
    ).strip()
    to_phone = (request.POST.get("to") or request.POST.get("To") or "").strip()
    text = (request.POST.get("text") or request.POST.get("Text") or "").strip()

    if not from_phone or not text:
        return HttpResponse(status=200)

    normalized = _normalize_sms_phone(from_phone)
    user = (
        User.objects.filter(profile__phone_number__icontains=normalized[-9:]).first()
        if normalized
        else None
    )
    if not user:
        user = User.objects.filter(username__icontains=normalized[-9:]).first() if normalized else None
    if not user:
        return HttpResponse(status=200)

    parts = text.split()
    command = (parts[0] if parts else "").upper()

    if command == "VOICE":
        bill_code = parts[1] if len(parts) > 1 else ""
        bill = None
        if bill_code.isdigit():
            bill = Bill.objects.filter(short_id=bill_code, status=Bill.Status.PUBLISHED).first()
            if not bill:
                bill = Bill.objects.filter(id=bill_code, status=Bill.Status.PUBLISHED).first()
        if not bill:
            send_sms_via_africastalking(
                from_phone,
                "Voiced: Usage VOICE <BillID>. Example: VOICE 123",
            )
            return HttpResponse(status=200)
        place_voice_summary_call_task.delay(user.id, str(bill.id))
        send_sms_via_africastalking(
            from_phone,
            f"Voiced: Calling now for Bill #{bill.short_id}. Follow keypad prompts to vote.",
        )
        return HttpResponse(status=200)

    if command == "VOTE":
        if len(parts) < 3:
            send_sms_via_africastalking(
                from_phone,
                "Voiced: Usage VOTE <BillID> SUPPORT or VOTE <BillID> OPPOSE",
            )
            return HttpResponse(status=200)
        bill_code = parts[1]
        choice = parts[2].upper()
        vote_type = "support" if choice in {"SUPPORT", "YES", "1"} else "oppose" if choice in {"OPPOSE", "NO", "2"} else ""
        if not vote_type:
            send_sms_via_africastalking(
                from_phone,
                "Voiced: Vote option must be SUPPORT or OPPOSE.",
            )
            return HttpResponse(status=200)
        bill = None
        if bill_code.isdigit():
            bill = Bill.objects.filter(short_id=bill_code, status=Bill.Status.PUBLISHED).first()
            if not bill:
                bill = Bill.objects.filter(id=bill_code, status=Bill.Status.PUBLISHED).first()
        if not bill:
            send_sms_via_africastalking(from_phone, "Voiced: Bill not found.")
            return HttpResponse(status=200)
        vote, _ = _apply_vote(user, bill, vote_type, "")
        send_vote_feedback_sms_task.delay(user.id, str(bill.id), vote.receipt_id)
        return HttpResponse(status=200)

    send_sms_via_africastalking(
        from_phone,
        f"Voiced: Unknown command '{command}'. Use VOICE <BillID> or VOTE <BillID> SUPPORT/OPPOSE.",
    )
    return HttpResponse(status=200)

@csrf_exempt
def ussd_callback(request):
    phone_number = request.POST.get("phoneNumber")
    text = request.POST.get("text", "")
    print(f"DEBUG: USSD logic reached for user {phone_number} | text='{text}'")

    user, _ = User.objects.get_or_create(username=phone_number)
    if not hasattr(user, 'profile') or not user.profile.phone_number:
        user.set_password(''.join(random.choices(string.ascii_letters + string.digits, k=12)))
        user.save()
        user.profile.phone_number = phone_number
        user.profile.save()

    profile = user.profile
    lang = (profile.language or "en").split("-")[0]
    if lang == "sh":
        lang = "sr"
    if lang not in {"en", "sw", "sr"}:
        lang = "en"

    raw_parts = text.split('*')
    parts = []

    for p in raw_parts:
        if p == '0':
            if parts:
                parts.pop()
        elif p != '':
            parts.append(p)

    level = len(parts)

    content_map = {
        'en': {
            'main': "CON Welcome to Voiced\n1. Trending Bills\n2. Search by ID\n3. Settings/SMS\n4. Verify My Identity (60/-)\n5. Top-up Wallet",
            'list': "CON Select Bill:\n",
            'search': "CON Enter Bill ID (3 digits):",
            'dashboard': "CON {title}\n1. AI Summary\n2. Vote Now\n3. Community Pulse\n0. Back",
            'ai_summary_menu': "CON {title}\n1. Get AI Summary (SMS)\n2. Listen to Audio Summary (5/-)\n0. Back",
            'vote_opts': "CON {title}\n1. Support\n2. Oppose\n0. Back",
            'pulse': "CON {title}\nSupport: {s} | Oppose: {o}\n0. Back",
            'reason': "CON Why did you vote that way? (Type reason or 0 to skip)",
            'done': "END Your voice is live in the debate!",
            'rejected': "END Your comment was rejected for inciting content.",
            'sms_status': "CON SMS Alerts: {status}\n1. Turn ON\n2. Turn OFF\n3. Language\n0. Back",
            'lang_menu': "CON Select Language:\n1. English\n2. Swahili\n3. Sheng",
            'lang_done': "END Language updated!",
            'sms_on': "END SMS Alerts ON!",
            'sms_off': "END SMS Alerts OFF!",
            'next': "99. Read More",
            'error': "END Bill not found.",
            'summary_sent': "END The summary for Bill #{code} has been sent to your phone via SMS. Please check your messages shortly.",
            'summary_duplicate': "END The summary for Bill #{code} was already sent to your phone. Please check your messages.",
            'verify_id_prompt': "CON Enter your 8-digit National ID Number:",
            'verify_id_invalid': "END Invalid ID format. Please dial again and enter exactly 8 digits.",
            'verify_confirm': "CON A KES 60 STK Push will be sent to your phone.\n1. Confirm & Pay\n2. Cancel",
            'verify_cancelled': "END Verification cancelled. Dial again when ready.",
            'verify_initiated': "END Please enter your M-Pesa PIN on the prompt. Once paid, we will verify your ID and send a confirmation SMS in 60 seconds.",
            'verify_payment_error': "END Could not start M-Pesa prompt right now. Please try again shortly.",
            'topup_amount': "CON Enter top-up amount (KES):",
            'topup_invalid': "END Invalid amount. Please dial again and enter a valid KES value.",
            'topup_pending': "END Please complete the M-Pesa prompt on your phone. Your balance will update shortly.",
            'topup_error': "END Could not start M-Pesa prompt. Please try again shortly.",
            'listen_insufficient': "END Insufficient balance (Needed: 5/-). Your balance is KES {bal}. Please select 'Top-up Wallet' from the main menu to continue.",
            'listen_processing': "END Playing summary call now. KES 5.00 deducted. Follow keypad prompts to vote.",
        },
        'sw': {
            'main': "CON Karibu Voiced\n1. Miswada Inayovuma\n2. Tafuta kwa ID\n3. Mipangilio/SMS\n4. Thibitisha Utambulisho (60/-)\n5. Jaza Wallet",
            'list': "CON Chagua Mswada:\n",
            'search': "CON Weka ID ya Mswada (namba 3):",
            'dashboard': "CON {title}\n1. Muhtasari wa AI\n2. Piga Kura\n3. Maoni ya Wengi\n0. Nyuma",
            'ai_summary_menu': "CON {title}\n1. Pata Muhtasari wa AI (SMS)\n2. Sikiza Muhtasari wa Sauti (5/-)\n0. Nyuma",
            'vote_opts': "CON {title}\n1. Unga mkono\n2. Pinga\n0. Nyuma",
            'pulse': "CON {title}\nKura za Ndio: {s} | Hapana: {o}\n0. Nyuma",
            'reason': "CON Toa sababu ya uamuzi wako? (Andika au 0)",
            'done': "END Sauti yako imerekodiwa!",
            'rejected': "END Ujumbe umekataliwa: unachochea vurugu.",
            'sms_status': "CON SMS Alerts: {status}\n1. Washa\n2. Zima\n3. Lugha\n0. Nyuma",
            'lang_menu': "CON Chagua Lugha:\n1. Kiingereza\n2. Kiswahili\n3. Sheng",
            'lang_done': "END Lugha imebadilishwa!",
            'sms_on': "END SMS zimewashwa!",
            'sms_off': "END SMS zimezimwa.",
            'next': "99. Endelea Kusoma",
            'error': "END Mswada haupatikani.",
            'summary_sent': "END Muhtasari wa Mswada #{code} umetumwa kwa SMS. Angalia ujumbe wako hivi karibuni.",
            'summary_duplicate': "END Muhtasari wa Mswada #{code} tayari umetumwa. Angalia ujumbe wako.",
            'verify_id_prompt': "CON Weka Namba ya Kitambulisho (tarakimu 8):",
            'verify_id_invalid': "END Muundo wa ID si sahihi. Tafadhali ingiza tarakimu 8 kamili.",
            'verify_confirm': "CON STK Push ya KES 60 itatumwa kwa simu yako.\n1. Thibitisha & Lipa\n2. Ghairi",
            'verify_cancelled': "END Uthibitisho umeghairiwa. Jaribu tena ukiwa tayari.",
            'verify_initiated': "END Tafadhali weka PIN yako ya M-Pesa kwenye prompt. Ukishalipa, tutathibitisha ID yako na kutuma SMS ndani ya sekunde 60.",
            'verify_payment_error': "END Hatukuweza kuanzisha STK sasa. Jaribu tena baada ya muda mfupi.",
            'topup_amount': "CON Weka kiasi cha kujaza Wallet (KES):",
            'topup_invalid': "END Kiasi si sahihi. Tafadhali jaribu tena na kiasi halali.",
            'topup_pending': "END Kamilisha prompt ya M-Pesa kwenye simu yako. Salio lako litasasishwa muda mfupi.",
            'topup_error': "END Hatukuweza kuanzisha STK. Tafadhali jaribu tena baadaye kidogo.",
            'listen_insufficient': "END Salio haitoshi (Unahitaji: 5/-). Salio lako ni KES {bal}. Tafadhali chagua 'Jaza Wallet' kwenye menu kuu.",
            'listen_processing': "END Simu ya muhtasari inaanza sasa. KES 5.00 imekatwa. Fuata keypad kupiga kura.",
        },
        'sr': {
            'main': "CON Voiced: Rada ni gani?\n1. Bills Zinawika\n2. Tafuta na ID\n3. Settings/SMS\n4. Verify ID Yangu (60/-)\n5. Top-up Wallet",
            'list': "CON Chagua Bill:\n",
            'search': "CON Weka ID ya Bill (namba 3):",
            'dashboard': "CON {title}\n1. Summary ya AI\n2. Piga Kura\n3. Pulse ya Mtaa\n0. Back",
            'ai_summary_menu': "CON {title}\n1. Pata Summary ya AI (SMS)\n2. Sikiza Audio Summary (5/-)\n0. Back",
            'vote_opts': "CON {title}\n1. Support\n2. Kataa\n0. Back",
            'pulse': "CON {title}\nWamekubali: {s} | Wamekataa: {o}\n0. Back",
            'reason': "CON Niaje umechagua hivo? (Chapa reason au 0)",
            'done': "END Sauti yako imefika!",
            'rejected': "END Zimeshtuliwa: hio ni kuchochea fujo.",
            'sms_status': "CON SMS Alerts: {status}\n1. Washa\n2. Zima\n3. Lugha\n0. Nyuma",
            'lang_menu': "CON Chagua Lugha:\n1. English\n2. Swahili\n3. Sheng",
            'lang_done': "END Lugha imetiki!",
            'sms_on': "END SMS ziko rada!",
            'sms_off': "END SMS zimezimwa.",
            'next': "99. More Rada",
            'error': "END ID ni mwitu.",
            'summary_sent': "END Summary ya Bill #{code} imetumwa SMS. Check messages mapema.",
            'summary_duplicate': "END Summary ya Bill #{code} tayari imetumwa. Check messages.",
            'verify_id_prompt': "CON Weka ID Number yako (digits 8):",
            'verify_id_invalid': "END Hiyo ID si poa. Ingiza digits 8 kamili.",
            'verify_confirm': "CON STK Push ya KES 60 itatumwa kwa simu yako.\n1. Confirm & Pay\n2. Cancel",
            'verify_cancelled': "END Verification imecanceliwa. Jaribu tena ukiwa ready.",
            'verify_initiated': "END Weka M-Pesa PIN kwa prompt. Ukilipia, tutaverify ID yako na kutuma SMS ndani ya sekunde 60.",
            'verify_payment_error': "END STK haijaanza sai. Jaribu tena baadaye kidogo.",
            'topup_amount': "CON Weka doo ya top-up (KES):",
            'topup_invalid': "END Kiasi si valid. Jaribu tena na amount poa.",
            'topup_pending': "END Maliza M-Pesa prompt kwa simu. Balance itaupdate shortly.",
            'topup_error': "END STK haijaanza sai. Jaribu tena baadaye kidogo.",
            'listen_insufficient': "END Balance haitoshi (Needed: 5/-). Balance yako ni KES {bal}. Chagua 'Top-up Wallet' kwa menu kuu.",
            'listen_processing': "END Tunakupigia summary sai. KES 5.00 imekatwa. Tumia keypad kupiga kura.",
        }
    }

    content = content_map[lang]
    response = ""
    sandbox_mode = (
        (os.getenv("AT_USERNAME") or os.getenv("AFRICASTALKING_USERNAME") or "").strip().lower()
        == "sandbox"
    )

    if level == 0:
        response = content['main']

    elif parts[0] in ["1", "2"]:
        if parts[0] == "1" and level == 1:
            active_bills = Bill.objects.active_bills()[:5]
            response = content['list']
            for bill in active_bills:
                response += f"{bill.short_id}. {bill.title[:45]}\n"
        elif parts[0] == "2" and level == 1:
            response = content['search']
        elif level == 2:
            try:
                bill = Bill.objects.get(short_id=parts[1], status=Bill.Status.PUBLISHED)
                response = content['dashboard'].format(title=bill.title[:70])
            except Bill.DoesNotExist:
                response = content['error']
        elif level == 3:
            try:
                bill = Bill.objects.get(short_id=parts[1], status=Bill.Status.PUBLISHED)
                if parts[2] == "1":
                    response = content['ai_summary_menu'].format(title=bill.title[:60])
                elif parts[2] == "2":
                    response = content['vote_opts'].format(title=bill.title[:60])
                elif parts[2] == "3":
                    response = content['pulse'].format(title=bill.title[:60], s=bill.support_count, o=bill.oppose_count)
            except Bill.DoesNotExist:
                response = content['error']
        elif level == 4 and parts[2] == "1":
            if parts[3] == "1":
                try:
                    bill = Bill.objects.get(short_id=parts[1], status=Bill.Status.PUBLISHED)
                    cache_key = f"ussd_sms_summary:{user.id}:{bill.id}"
                    if not cache.add(cache_key, True, timeout=300):
                        response = content['summary_duplicate'].format(code=bill.short_id)
                    else:
                        print(f"DEBUG: Attempting to call send_voiced_sms for {phone_number} | bill={bill.short_id}")
                        if sandbox_mode:
                            print("DEBUG: Sandbox mode detected; sending summary SMS synchronously.")
                            send_ussd_bill_summary_sms(user.id, str(bill.id))
                        else:
                            try:
                                send_ussd_bill_summary_sms.delay(user.id, str(bill.id))
                            except Exception as exc:
                                # Fallback when Celery broker/worker is unavailable.
                                print(f"DEBUG: Celery unavailable for summary SMS, sending sync. Error: {exc}")
                                send_ussd_bill_summary_sms(user.id, str(bill.id))
                        response = content['summary_sent'].format(code=bill.short_id)
                except Bill.DoesNotExist:
                    response = content['error']
            elif parts[3] == "2":
                try:
                    bill = Bill.objects.get(short_id=parts[1], status=Bill.Status.PUBLISHED)
                    with transaction.atomic():
                        wallet = Wallet.objects.select_for_update().get(user=user)
                        updated = Wallet.objects.filter(
                            id=wallet.id,
                            balance__gte=F("reserved_balance") + VOICE_SUMMARY_FEE,
                        ).update(balance=F("balance") - VOICE_SUMMARY_FEE)
                        if not updated:
                            wallet.refresh_from_db()
                            response = content['listen_insufficient'].format(bal=f"{wallet.available_balance:.2f}")
                        else:
                            wallet.refresh_from_db()
                            Transaction.objects.create(
                                user=user,
                                wallet=wallet,
                                amount=VOICE_SUMMARY_FEE,
                                transaction_type=Transaction.TransactionType.DEDUCTION,
                                status=Transaction.Status.COMPLETED,
                                description=f"Voice summary listen fee | bill:{bill.id}",
                            )
                            send_voice_listen_charge_sms_task.delay(user.id, f"{wallet.available_balance:.2f}")
                            place_voice_summary_call_task.delay(user.id, str(bill.id))
                            response = content['listen_processing']
                except Bill.DoesNotExist:
                    response = content['error']
                except Wallet.DoesNotExist:
                    response = content['listen_insufficient'].format(bal="0.00")
            else:
                try:
                    bill = Bill.objects.get(short_id=parts[1], status=Bill.Status.PUBLISHED)
                    response = content['ai_summary_menu'].format(title=bill.title[:60])
                except Bill.DoesNotExist:
                    response = content['error']
        elif level == 4 and parts[2] == "2":
            if parts[3] in {"1", "2"}:
                response = content['reason']
            else:
                try:
                    bill = Bill.objects.get(short_id=parts[1], status=Bill.Status.PUBLISHED)
                    response = content['vote_opts'].format(title=bill.title[:60])
                except Bill.DoesNotExist:
                    response = content['error']
        elif level == 5 and parts[2] == "2":
            try:
                bill = Bill.objects.get(short_id=parts[1], status=Bill.Status.PUBLISHED)
                vt = 'support' if parts[3] == "1" else 'oppose'
                maoni = parts[4]

                if maoni != "0":
                    is_toxic, _ = check_message_toxicity(maoni)
                    if is_toxic:
                        return HttpResponse(content['rejected'], content_type='text/plain')

                vote, _ = _apply_vote(user, bill, vt, '' if maoni == "0" else maoni)
                print(f"DEBUG: Attempting to call send_voiced_sms for {phone_number} | vote_receipt={vote.receipt_id}")
                if sandbox_mode:
                    print("DEBUG: Sandbox mode detected; sending vote SMS synchronously.")
                    send_vote_feedback_sms_task(user.id, str(bill.id), vote.receipt_id)
                else:
                    try:
                        send_vote_feedback_sms_task.delay(user.id, str(bill.id), vote.receipt_id)
                    except Exception as exc:
                        print(f"DEBUG: Celery unavailable for vote SMS, sending sync. Error: {exc}")
                        send_vote_feedback_sms_task(user.id, str(bill.id), vote.receipt_id)
                response = content['done']
            except Bill.DoesNotExist:
                response = content['error']

    elif parts[0] == "3":
        if level == 1:
            stat = "ON" if profile.sms_notifications else "OFF"
            response = content['sms_status'].format(status=stat)
        elif level == 2:
            if parts[1] == "1":
                profile.sms_notifications = True
                profile.save()
                response = content['sms_on']
            elif parts[1] == "2":
                profile.sms_notifications = False
                profile.save()
                response = content['sms_off']
            elif parts[1] == "3":
                response = content['lang_menu']
        elif level == 3 and parts[1] == "3":
            lang_codes = {"1": "en", "2": "sw", "3": "sr"}
            profile.language = lang_codes.get(parts[2], "en")
            profile.save()
            response = content['lang_done']

    elif parts[0] == "4":
        if level == 1:
            response = content['verify_id_prompt']
        elif level == 2:
            id_number = (parts[1] or "").strip()
            if not id_number.isdigit() or len(id_number) != 8:
                response = content['verify_id_invalid']
            else:
                response = content['verify_confirm']
        elif level == 3:
            id_number = (parts[1] or "").strip()
            decision = (parts[2] or "").strip()
            if decision == "2":
                response = content['verify_cancelled']
            elif decision == "1":
                if not id_number.isdigit() or len(id_number) != 8:
                    response = content['verify_id_invalid']
                else:
                    attempt = VerificationAttempt.objects.create(
                        user=user,
                        payment_status=VerificationAttempt.PaymentStatus.PENDING,
                        status=VerificationAttempt.Status.PENDING,
                        amount_paid=Decimal("0.00"),
                        id_number=id_number,  # encrypted via EncryptedCharField
                        full_name=(f"{user.first_name} {user.last_name}".strip() or user.username),
                    )
                    stk = start_stk_topup(user=user, phone=phone_number, amount=VERIFICATION_FEE)
                    if not stk.get("ok"):
                        attempt.status = VerificationAttempt.Status.FAILED
                        attempt.failure_reason = stk.get("error") or "M-Pesa request failed."
                        attempt.save(update_fields=["status", "failure_reason", "updated_at"])
                        response = content['verify_payment_error']
                    else:
                        checkout_id = stk.get("checkout_request_id") or ""
                        txn_id = stk.get("transaction_id")
                        if txn_id:
                            Transaction.objects.filter(id=txn_id, user=user).update(
                                description=f"KYC verification fee | attempt:{attempt.id} | source:ussd"
                            )
                        attempt.mpesa_checkout_request_id = checkout_id
                        attempt.payment_reference = checkout_id
                        attempt.save(update_fields=["mpesa_checkout_request_id", "payment_reference", "updated_at"])
                        response = content['verify_initiated']
            else:
                response = content['verify_cancelled']

    elif parts[0] == "5":
        if level == 1:
            response = content['topup_amount']
        elif level == 2:
            try:
                amount = Decimal(parts[1])
            except Exception:
                amount = Decimal("0")
            if amount <= 0:
                response = content['topup_invalid']
            else:
                stk = start_stk_topup(user=user, phone=phone_number, amount=amount)
                if not stk.get("ok"):
                    response = content['topup_error']
                else:
                    response = content['topup_pending']

    return HttpResponse(response, content_type='text/plain')


@csrf_exempt
def voice_callback(request):
    """
    Africa's Talking Voice callback:
    - Play localized bill summary
    - Prompt 1 Support / 2 Oppose via GetDigits
    """
    bill_id = request.GET.get("bill_id") or request.POST.get("bill_id")
    user_id = request.GET.get("user_id") or request.POST.get("user_id")
    if not bill_id or not user_id:
        return HttpResponse("<Response><Say>Unable to continue.</Say></Response>", content_type="text/xml")
    try:
        bill = Bill.objects.get(id=bill_id, status=Bill.Status.PUBLISHED)
        user = User.objects.get(id=user_id)
    except (Bill.DoesNotExist, User.DoesNotExist):
        return HttpResponse("<Response><Say>Bill or user not found.</Say></Response>", content_type="text/xml")

    lang = getattr(getattr(user, "profile", None), "language", "en") or "en"
    audio_url = ""
    if lang == "sw":
        audio_url = bill.audio_summary_sw.url if bill.audio_summary_sw else ""
    elif lang in {"sr", "sh"}:
        audio_url = bill.audio_summary_sh.url if bill.audio_summary_sh else ""
    else:
        audio_url = bill.audio_summary_en.url if bill.audio_summary_en else ""

    base = request.build_absolute_uri("/")[:-1]
    absolute_audio = f"{base}{audio_url}" if audio_url.startswith("/") else audio_url
    action_url = f"{base}/engagement/voice/vote/?bill_id={bill.id}&user_id={user.id}"

    xml = [
        "<Response>",
        f'<Play url="{absolute_audio}"/>' if absolute_audio else "<Say>Summary audio is not available yet.</Say>",
        f'<GetDigits timeout="12" finishOnKey="#" numDigits="1" callbackUrl="{action_url}">',
        "<Say>Press 1 to Support. Press 2 to Oppose.</Say>",
        "</GetDigits>",
        "<Say>No vote received. Goodbye.</Say>",
        "</Response>",
    ]
    return HttpResponse("".join(xml), content_type="text/xml")


@csrf_exempt
def voice_vote_callback(request):
    bill_id = request.GET.get("bill_id") or request.POST.get("bill_id")
    user_id = request.GET.get("user_id") or request.POST.get("user_id")
    digits = (request.POST.get("dtmfDigits") or request.GET.get("dtmfDigits") or "").strip()
    if not bill_id or not user_id:
        return HttpResponse("<Response><Say>Unable to continue.</Say></Response>", content_type="text/xml")
    try:
        bill = Bill.objects.get(id=bill_id, status=Bill.Status.PUBLISHED)
        user = User.objects.get(id=user_id)
    except (Bill.DoesNotExist, User.DoesNotExist):
        return HttpResponse("<Response><Say>Bill or user not found.</Say></Response>", content_type="text/xml")

    if digits not in {"1", "2"}:
        return HttpResponse("<Response><Say>Invalid selection. Goodbye.</Say></Response>", content_type="text/xml")
    vote_type = "support" if digits == "1" else "oppose"
    vote, _ = _apply_vote(user, bill, vote_type, "")
    send_vote_feedback_sms_task.delay(user.id, str(bill.id), vote.receipt_id)
    return HttpResponse("<Response><Say>Thank you. Your vote is recorded.</Say></Response>", content_type="text/xml")