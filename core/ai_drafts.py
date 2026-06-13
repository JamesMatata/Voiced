"""AI legal draft generation (shared by core and payments)."""
import json
import os

from google import genai
from google.genai import types

from bills.models import Bill, BillVote


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing.")
    return genai.Client(api_key=api_key)


def generate_submission_draft_text(bill: Bill, user) -> str:
    """Raises if bill closed, no vote reason, or AI fails."""
    if bill.is_closed:
        raise PermissionError("closed")

    user_vote = BillVote.objects.filter(bill=bill, user=user).first()
    if not user_vote or not user_vote.reason:
        raise ValueError("no_vote_reason")

    client = get_gemini_client()
    config = types.GenerateContentConfig(
        system_instruction=(
            "Draft a formal petition letter to the Clerk of the National Assembly of Kenya. "
            "Return JSON with key: 'draft'."
        ),
        response_mime_type="application/json",
        temperature=0.5,
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=f"Bill: {bill.title}\nUser Opinion: {user_vote.reason}",
        config=config,
    )
    draft_data = json.loads(response.text)
    return draft_data["draft"]
