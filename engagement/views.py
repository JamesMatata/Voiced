import random
import string
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from django.db import transaction
from bills.models import Bill, BillVote
from chat.models import ChatMessage
from chat.moderation import check_message_toxicity

@csrf_exempt
def ussd_callback(request):
    phone_number = request.POST.get("phoneNumber")
    text = request.POST.get("text", "")

    user, _ = User.objects.get_or_create(username=phone_number)
    if not hasattr(user, 'profile') or not user.profile.phone_number:
        user.set_password(''.join(random.choices(string.ascii_letters + string.digits, k=12)))
        user.save()
        user.profile.phone_number = phone_number
        user.profile.save()

    profile = user.profile
    lang = profile.language or 'en'

    raw_parts = text.split('*')
    parts = []
    page = 0

    for p in raw_parts:
        if p == '0':
            if parts: parts.pop()
            page = 0
        elif p == '99':
            page += 1
        elif p != '':
            parts.append(p)
            page = 0

    level = len(parts)

    content_map = {
        'en': {
            'main': "CON Welcome to Voiced\n1. Trending Bills\n2. Search by ID\n3. Settings/SMS",
            'list': "CON Select Bill:\n",
            'search': "CON Enter Bill ID (3 digits):",
            'dashboard': "CON {title}\n1. AI Summary\n2. Vote Now\n3. Community Pulse\n0. Back",
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
            'error': "END Bill not found."
        },
        'sw': {
            'main': "CON Karibu Voiced\n1. Miswada Inayovuma\n2. Tafuta kwa ID\n3. Mipangilio/SMS",
            'list': "CON Chagua Mswada:\n",
            'search': "CON Weka ID ya Mswada (namba 3):",
            'dashboard': "CON {title}\n1. Muhtasari wa AI\n2. Piga Kura\n3. Maoni ya Wengi\n0. Nyuma",
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
            'error': "END Mswada haupatikani."
        },
        'sh': {
            'main': "CON Voiced: Rada ni gani?\n1. Bills Zinawika\n2. Tafuta na ID\n3. Settings/SMS",
            'list': "CON Chagua Bill:\n",
            'search': "CON Weka ID ya Bill (namba 3):",
            'dashboard': "CON {title}\n1. Summary ya AI\n2. Piga Kura\n3. Pulse ya Mtaa\n0. Back",
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
            'error': "END ID ni mwitu."
        }
    }

    content = content_map[lang]
    response = ""

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
                bill = Bill.objects.get(short_id=parts[1])
                response = content['dashboard'].format(title=bill.title[:70])
            except Bill.DoesNotExist:
                response = content['error']
        elif level == 3:
            try:
                bill = Bill.objects.get(short_id=parts[1])
                if parts[2] == "1":
                    mapping = {'en': 'english', 'sw': 'swahili', 'sh': 'sheng'}
                    target_key = mapping.get(lang, 'english')
                    lang_data = bill.ai_analysis.get(target_key)

                    full_text = "No summary available."
                    if isinstance(lang_data, dict):
                        full_text = lang_data.get('short_summary') or lang_data.get('long_description', "Summary in progress.")

                    chunk_size = 120
                    chunks = [full_text[i:i + chunk_size] for i in range(0, len(full_text), chunk_size)]

                    if page < len(chunks):
                        response = f"CON {chunks[page]}"
                        if page < len(chunks) - 1:
                            response += f"\n{content['next']}"
                        response += "\n0. Back"
                    else:
                        response = f"CON {chunks[-1]}\n0. Back"

                elif parts[2] == "2":
                    response = content['vote_opts'].format(title=bill.title[:60])
                elif parts[2] == "3":
                    response = content['pulse'].format(title=bill.title[:60], s=bill.support_count, o=bill.oppose_count)
            except Bill.DoesNotExist:
                response = content['error']
        elif level == 4 and parts[2] == "2":
            response = content['reason']
        elif level == 5 and parts[2] == "2":
            try:
                bill = Bill.objects.get(short_id=parts[1])
                vt = 'support' if parts[3] == "1" else 'oppose'
                maoni = parts[4]

                if maoni != "0":
                    is_toxic, _ = check_message_toxicity(maoni)
                    if is_toxic:
                        return HttpResponse(content['rejected'], content_type='text/plain')
                    ChatMessage.objects.create(bill=bill, user=user, content=f"[USSD] {maoni}")

                with transaction.atomic():
                    vote, created = BillVote.objects.get_or_create(
                        user=user,
                        bill=bill,
                        defaults={'vote_type': vt}
                    )

                    if not created and vote.vote_type != vt:
                        if vote.vote_type == 'support':
                            bill.support_count -= 1
                            bill.oppose_count += 1
                        else:
                            bill.oppose_count -= 1
                            bill.support_count += 1
                        vote.vote_type = vt
                        vote.save(update_fields=['vote_type'])
                        bill.save(update_fields=['support_count', 'oppose_count'])
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
            lang_codes = {"1": "en", "2": "sw", "3": "sh"}
            profile.language = lang_codes.get(parts[2], "en")
            profile.save()
            response = content['lang_done']

    return HttpResponse(response, content_type='text/plain')