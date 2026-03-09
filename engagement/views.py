import random
import string
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
from bills.models import Bill, BillVote
from chat.models import ChatMessage


@csrf_exempt
def ussd_callback(request):
    phone_number = request.POST.get("phoneNumber")
    text = request.POST.get("text", "")

    user, created = User.objects.get_or_create(username=phone_number)
    if created or not hasattr(user, 'profile') or not user.profile.phone_number:
        user.set_password(''.join(random.choices(string.ascii_letters + string.digits, k=12)))
        user.save()
        profile = user.profile
        profile.phone_number = phone_number
        profile.save()
    else:
        profile = user.profile

    lang = profile.language or 'en'

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
            'sms_status': "CON SMS Alerts: {status}\n1. Turn ON\n2. Turn OFF\n3. Language\n0. Back",
            'lang_menu': "CON Select Language:\n1. English\n2. Swahili\n3. Sheng",
            'lang_done': "END Language updated!",
            'sms_on': "END SMS Alerts ON!",
            'sms_off': "END SMS Alerts OFF!",
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
            'sms_status': "CON SMS Alerts: {status}\n1. Washa\n2. Zima\n3. Lugha\n0. Nyuma",
            'lang_menu': "CON Chagua Lugha:\n1. Kiingereza\n2. Kiswahili\n3. Sheng",
            'lang_done': "END Lugha imebadilishwa!",
            'sms_on': "END SMS zimewashwa!",
            'sms_off': "END SMS zimezimwa.",
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
            'sms_status': "CON SMS Alerts: {status}\n1. Washa\n2. Zima\n3. Lugha\n0. Back",
            'lang_menu': "CON Chagua Lugha:\n1. English\n2. Swahili\n3. Sheng",
            'lang_done': "END Lugha imetiki!",
            'sms_on': "END SMS ziko rada!",
            'sms_off': "END SMS zimezimwa.",
            'error': "END ID ni mwitu."
        }
    }

    content = content_map[lang]
    parts = [p for p in text.split('*') if p != '']

    # Handle back logic (0)
    if text.endswith('*0'):
        if len(parts) >= 2:
            parts = parts[:-2]
        else:
            parts = []
        text = "*".join(parts)
        level = len(parts)
    else:
        level = len(parts)

    response = ""

    if text == "":
        response = content['main']

    elif parts[0] in ["1", "2"]:
        if parts[0] == "1" and level == 1:
            active_bills = Bill.objects.active_bills()[:5]
            response = content['list']
            for bill in active_bills:
                response += f"{bill.short_id}. {bill.title[:20]}\n"
        elif parts[0] == "2" and level == 1:
            response = content['search']
        elif level == 2:
            try:
                bill = Bill.objects.get(short_id=parts[1])
                response = content['dashboard'].format(title=bill.title[:30])
            except Bill.DoesNotExist:
                response = content['error']
        elif level == 3:
            try:
                bill = Bill.objects.get(short_id=parts[1])
                if parts[2] == "1":
                    summary = bill.ai_analysis.get(lang, bill.ai_analysis.get('en', 'No summary.'))
                    response = f"CON {summary[:140]}\n0. Back"
                elif parts[2] == "2":
                    response = content['vote_opts'].format(title=bill.title[:30])
                elif parts[2] == "3":
                    response = content['pulse'].format(title=bill.title[:30], s=bill.support_count, o=bill.oppose_count)
            except Bill.DoesNotExist:
                response = content['error']
        elif level == 4 and parts[2] == "2":
            response = content['reason']
        elif level == 5 and parts[2] == "2":
            try:
                bill = Bill.objects.get(short_id=parts[1])
                vt = 'support' if parts[3] == "1" else 'oppose'
                BillVote.objects.update_or_create(user=user, bill=bill, defaults={'vote_type': vt})
                if parts[4] != "0":
                    ChatMessage.objects.create(bill=bill, user=user, content=f"[USSD] {parts[4]}")
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