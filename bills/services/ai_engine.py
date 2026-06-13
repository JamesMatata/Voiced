import os
import json
import requests
import PyPDF2
from io import BytesIO
from google import genai
from google.genai import types


class BillAnalyzer:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is missing from environment variables.")
        self.client = genai.Client(api_key=api_key)

    def extract_text_from_pdf(self, pdf_url):
        try:
            response = requests.get(pdf_url, timeout=30)
            response.raise_for_status()
            reader = PyPDF2.PdfReader(BytesIO(response.content))
            return "".join(page.extract_text() + "\n" for page in reader.pages)
        except Exception:
            return None

    def generate_comprehensive_analysis(self, pdf_text, bill_code: str | None = None, bill_web_url: str | None = None):
        code_line = ""
        if bill_code:
            code_line = f"The bill public code is #{bill_code}. It MUST appear in sms_summary."
        url_line = ""
        if bill_web_url:
            url_line = (
                f"If the narrative does not fit in 160 characters, end sms_summary with this exact URL "
                f"for the web version (shorten other text first): {bill_web_url}"
            )

        system_instruction = f"""
        You are a Kenyan civic AI. Return a JSON object with keys: 'english', 'swahili', 'sheng', 'closing_date', and 'sms_summary'.
        'closing_date' must be extracted from the text if a deadline for public participation or memorandum submission is explicitly mentioned. Format as YYYY-MM-DD. If no exact deadline is found, return null.
        Inside each language object, provide: "translated_title", "short_summary", "long_description", "markdown_overview",
        and an "impact" object containing: "who_is_affected" (list), "what_is_affected" (list),
        "how_they_are_affected", and "the_bottom_line".
        Sheng must be modern Nairobi street Sheng.

        'sms_summary' is a SINGLE string for SMS (GSM 7-bit safe if possible): ultra-concise, plain language,
        MUST include the bill code as #{bill_code or "CODE"} (use the real code when provided).
        Maximum length 160 characters total — count carefully. No markdown, no newlines.
        {code_line}
        {url_line}
        """

        try:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.2,
            )

            prompt = f"Analyze this bill:\n\n{pdf_text}"
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=config,
            )

            return json.loads(response.text)

        except Exception:
            return None

    def generate_sms_summary_repair(self, context_text: str, bill_code: str, bill_web_url: str | None = None):
        """One-shot repair when an existing bill has no sms_summary (uses short English + metadata)."""
        url_line = ""
        if bill_web_url:
            url_line = (
                f"If needed, end with this URL only (no extra words): {bill_web_url}"
            )
        system_instruction = f"""
        Write ONE SMS string for Kenyan citizens about this bill.
        Rules: plain text, include #{bill_code}, max 160 characters total, no newlines.
        {url_line}
        """
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
            )
            prompt = f"Context from existing analysis or bill text:\n\n{context_text[:8000]}"
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=config,
            )
            text = (response.text or "").strip()
            # Model may wrap in quotes
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            return text[:160]
        except Exception:
            return ""
