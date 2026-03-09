import africastalking
from django.conf import settings


def send_at_sms(phone_number, message):
    """
    Sends an SMS via Africa's Talking gateway.
    """
    username = settings.AT_USERNAME
    api_key = settings.AT_API_KEY

    africastalking.initialize(username, api_key)
    sms = africastalking.SMS

    try:
        response = sms.send(message, [phone_number])
        return response
    except Exception as e:
        print(f"Africa's Talking SMS Error: {str(e)}")
        return None