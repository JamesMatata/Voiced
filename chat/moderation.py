import os
import json
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
    You are a strict community moderator for a Kenyan political platform. 
    Analyze the text (English, Swahili, or Sheng).
    Flag as toxic ONLY if it contains: hate speech, tribalism, explicit violence, or direct threats.
    Strong political disagreement or cursing at the government is ALLOWED.
    Return ONLY a JSON object: {"is_toxic": boolean, "reason": "explanation if toxic, else empty"}
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
            max_tokens=50
        )

        result = json.loads(response.choices[0].message.content.strip())
        return result.get("is_toxic", False), result.get("reason", "")

    except Exception as e:
        print(f"🛑 MODERATION ENGINE ERROR: {str(e)}")
        return False, ""