"""Shared PDF builders for legislative / collective reports (used by core and payments)."""
import io
import json
import os

from google import genai
from google.genai import types
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from bills.models import Bill, BillVote


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing.")
    return genai.Client(api_key=api_key)


def build_legislative_report_pdf_buffer(bill: Bill) -> io.BytesIO:
    """Raises on AI or PDF failure. Uses verified Kenyan votes only."""
    votes = (
        BillVote.objects.filter(bill=bill, user__profile__is_kenyan=True)
        .exclude(reason__isnull=True)
        .exclude(reason__exact="")
    )
    if not votes.exists():
        raise ValueError("Insufficient participation data to generate a meaningful report.")

    client = get_gemini_client()
    perspective_data = "\n".join([f"- {v.reason}" for v in votes[:40]])

    config = types.GenerateContentConfig(
        system_instruction=(
            "You are a Kenyan policy analyst. Summarize public sentiment. "
            "Return JSON: 'executive_summary', 'top_concerns' (list), 'overall_sentiment'."
        ),
        response_mime_type="application/json",
        temperature=0.3,
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=f"Analyze perspectives for: {bill.title}\n\nData:\n{perspective_data}",
        config=config,
    )
    analysis = json.loads(response.text)

    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    p.saveState()
    p.setFont("Helvetica-Bold", 60)
    p.setFillAlpha(0.05)
    p.translate(width / 2, height / 2)
    p.rotate(45)
    p.drawCentredString(0, 0, "VOICED.")
    p.restoreState()

    p.setFont("Helvetica-Bold", 24)
    p.drawString(50, height - 80, "LEGISLATIVE REPORT")
    p.setStrokeColor(colors.red)
    p.setLineWidth(2)
    p.line(50, height - 90, 150, height - 90)

    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, height - 130, f"BILL: {bill.title.upper()}")
    p.setFont("Helvetica", 10)
    p.setFillColor(colors.grey)
    p.drawString(
        50,
        height - 150,
        f"Verified Support: {bill.verified_support_count} | Verified Oppose: {bill.verified_oppose_count} | "
        f"Sentiment: {analysis.get('overall_sentiment', 'N/A')}",
    )

    p.setFillColor(colors.black)
    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, height - 190, "EXECUTIVE SUMMARY")

    p.setFont("Helvetica", 11)
    text_object = p.beginText(50, height - 210)
    text_object.setLeading(14)

    summary = analysis.get("executive_summary", "")
    for line in summary.split("."):
        if line.strip():
            text_object.textLine(line.strip() + ".")

    text_object.moveCursor(0, 20)
    text_object.setFont("Helvetica-Bold", 11)
    text_object.textLine("PRIMARY CITIZEN CONCERNS:")
    text_object.setFont("Helvetica", 11)

    for concern in analysis.get("top_concerns", []):
        text_object.textLine(f"- {concern}")

    p.drawText(text_object)
    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer
