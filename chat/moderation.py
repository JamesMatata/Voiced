import os
import json
import random
from openai import OpenAI
from openai.types.chat import ChatCompletionSystemMessageParam, ChatCompletionUserMessageParam
from openai.types.chat.completion_create_params import ResponseFormatJSONObject


def check_message_toxicity(message_text):
    client = OpenAI(
        api_key=os.getenv("LLMAPI_KEY"),
        base_url="https://api.llmapi.ai/v1",
        timeout=10.0
    )

    system_prompt = """
    You are a strict community moderator for 'Voiced', a Kenyan legislative platform.
    Analyze text in English, Swahili, and deep Nairobi Sheng.

    CRITICAL GUIDELINES:
    1. ALLOW: Strong political dissent, 'Maandamano' rhetoric, 'Reject' hashtags, and cursing at politicians/government.
    2. FLAG AS TOXIC (is_toxic: true): 
       - Direct calls for physical violence (e.g., 'kuwasha moto', 'piga huyu').
       - Tribalism/Ethnic slurs or dog-whistles against specific Kenyan communities.
       - Explicit hate speech or death threats.

    KENYAN CONTEXT:
    - 'Teargas' or 'Kimeumana' is usually NOT toxic; it's descriptive.
    - 'Watu fulani' used in a derogatory ethnic context IS toxic.

    Return ONLY a JSON object: {"is_toxic": boolean, "reason": "short explanation in English"}
    """

    try:
        messages = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=message_text)
        ]

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            response_format=ResponseFormatJSONObject(type="json_object"),
            temperature=0.1,
            max_tokens=100
        )

        result = json.loads(response.choices[0].message.content.strip())
        return result.get("is_toxic", False), result.get("reason", "")

    except Exception as e:
        # Fallback: In case of API failure, we allow the message but log the error
        print(f"🛑 MODERATION ENGINE ERROR: {str(e)}")
        return False, ""