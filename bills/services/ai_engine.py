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
        except Exception as e:
            return None

    def generate_comprehensive_analysis(self, pdf_text):
        system_instruction = """
        You are a Kenyan civic AI. Return a JSON object with keys: 'english', 'swahili', 'sheng', and 'closing_date'.
        'closing_date' must be extracted from the text if a deadline for public participation or memorandum submission is explicitly mentioned. Format as YYYY-MM-DD. If no exact deadline is found, return null.
        Inside each language object, provide: "short_summary", "long_description", "markdown_overview", 
        and an "impact" object containing: "who_is_affected" (list), "what_is_affected" (list), 
        "how_they_are_affected", and "the_bottom_line".
        Sheng must be modern Nairobi street Sheng.
        """

        try:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.2
            )

            prompt = f"Analyze this bill:\n\n{pdf_text}"
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=config
            )

            return json.loads(response.text)

        except Exception as e:
            return None