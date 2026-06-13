import io
import os
from pathlib import Path

import qrcode
import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.urls import reverse
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def _bill_url(bill):
    base = getattr(settings, "BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    return f"{base}{reverse('bill_detail', kwargs={'pk': str(bill.id)})}"


def _bullet_seed(ai_analysis: dict, lang_key: str, fallback: str):
    block = (ai_analysis or {}).get(lang_key) or {}
    txt = (block.get("short_summary") or "").strip()
    if not txt:
        return fallback
    return txt


def _fit_line(text: str, limit: int = 100):
    t = (text or "").replace("\n", " ").strip()
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 1)].rstrip() + "…"


def _english_poster_bullets(bill):
    src = (bill.summary_en or "").strip() or _bullet_seed(bill.ai_analysis or {}, "english", "")
    parts = [p.strip(" -\n\r\t") for p in src.replace("!", ".").replace("?", ".").split(".") if p.strip()]
    out = []
    for p in parts:
        if len(out) == 3:
            break
        out.append(_fit_line(p, 95))
    while len(out) < 3:
        out.append(
            [
                "What the bill changes for citizens.",
                "Who is affected and why it matters.",
                "Read, discuss, and vote on Voiced.",
            ][len(out)]
        )
    return out


def _font_candidates():
    base = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    return [
        base / "static" / "fonts" / "Inter-Bold.ttf",
        base / "static" / "fonts" / "Inter-Regular.ttf",
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]


def _load_pil_font(size: int, bold: bool = False):
    candidates = _font_candidates()
    # prioritize bold variant when requested
    ordered = candidates if bold else [candidates[1], candidates[0], candidates[3], candidates[2]]
    for fp in ordered:
        try:
            if fp.exists():
                return ImageFont.truetype(str(fp), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _ensure_pdf_fonts_registered():
    normal_name = "BarazaSans"
    bold_name = "BarazaSansBold"
    if normal_name in pdfmetrics.getRegisteredFontNames() and bold_name in pdfmetrics.getRegisteredFontNames():
        return normal_name, bold_name

    normal_fp = None
    bold_fp = None
    for fp in _font_candidates():
        if not fp.exists():
            continue
        low = fp.name.lower()
        if "bold" in low or "bd" in low:
            bold_fp = bold_fp or fp
        else:
            normal_fp = normal_fp or fp

    try:
        if normal_fp:
            pdfmetrics.registerFont(TTFont(normal_name, str(normal_fp)))
        if bold_fp:
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_fp)))
    except Exception:
        return "Helvetica", "Helvetica-Bold"

    return (
        normal_name if normal_name in pdfmetrics.getRegisteredFontNames() else "Helvetica",
        bold_name if bold_name in pdfmetrics.getRegisteredFontNames() else "Helvetica-Bold",
    )


def generate_bill_baraza_poster_pdf(bill) -> bytes:
    """
    Generate a printable A4 civic flyer (Baraza style) for offline outreach.
    """
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4)
    w, h = A4
    font_regular, font_bold = _ensure_pdf_fonts_registered()

    bill_url = _bill_url(bill)
    ussd_code = (os.getenv("AT_USSD_CODE") or "*483*XYZ#").strip()
    ai = bill.ai_analysis or {}

    bullets = _english_poster_bullets(bill)

    # Header
    y = h - 55
    pdf.setFont(font_bold, 26)
    pdf.drawString(40, y, _fit_line((bill.title_en or bill.title or "").upper(), 52))
    y -= 30
    pdf.setFont(font_bold, 11)
    pdf.drawString(40, y, f"BILL #{bill.short_id}")

    # Lowdown block
    y -= 32
    pdf.setFont(font_bold, 16)
    pdf.drawString(40, y, "THE LOWDOWN")
    y -= 20
    pdf.setFont(font_regular, 12)
    for item in bullets:
        pdf.drawString(52, y, f"- {item}")
        y -= 24

    # QR
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(bill_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    qr_buf = io.BytesIO()
    qr_img.save(qr_buf, format="PNG")
    qr_buf.seek(0)
    qr_reader = ImageReader(qr_buf)

    qr_size = 200
    qr_x = w - qr_size - 45
    qr_y = h - qr_size - 280
    pdf.drawImage(qr_reader, qr_x, qr_y, qr_size, qr_size, preserveAspectRatio=True, mask='auto')
    pdf.setFont(font_bold, 10)
    pdf.drawString(qr_x, qr_y - 14, "SCAN TO PARTICIPATE")

    # USSD fallback
    ussd_y = qr_y - 54
    pdf.setFont(font_bold, 15)
    pdf.drawString(40, ussd_y, "NO INTERNET?")
    ussd_y -= 20
    pdf.setFont(font_bold, 13)
    pdf.drawString(40, ussd_y, f"Dial {ussd_code} and select Bill #{bill.short_id}")

    # Footer
    pdf.setFont(font_bold, 20)
    pdf.drawString(40, 60, "Your Voice. Your Law. Voiced.ke")
    pdf.setFont(font_regular, 9)
    pdf.drawString(40, 44, f"Poster generated on {bill.updated_at.strftime('%Y-%m-%d %H:%M')}")

    pdf.save()
    out.seek(0)
    return out.getvalue()


def generate_bill_baraza_poster_png(bill) -> bytes:
    """
    Generate a social-share friendly PNG flyer with the same outreach content.
    """
    width, height = 1240, 1754  # A4-ish at 150dpi
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    title_font = _load_pil_font(44, bold=True)
    section_font = _load_pil_font(28, bold=True)
    body_font = _load_pil_font(22, bold=False)

    bill_url = _bill_url(bill)
    ussd_code = (os.getenv("AT_USSD_CODE") or "*483*XYZ#").strip()
    ai = bill.ai_analysis or {}

    bullets = _english_poster_bullets(bill)

    y = 50
    draw.rectangle([0, 0, width, 132], fill=(6, 78, 59))
    draw.text((36, y), _fit_line((bill.title_en or bill.title or "").upper(), 70), fill="white", font=title_font)
    y = 145
    draw.text((36, y), f"BILL #{bill.short_id}", fill=(6, 78, 59), font=section_font)

    y += 40
    draw.text((36, y), "THE LOWDOWN", fill=(6, 78, 59), font=section_font)
    y += 36
    for bullet in bullets:
        draw.text((52, y), f"- {bullet}", fill=(24, 24, 27), font=body_font)
        y += 34

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(bill_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_size = 360
    qr_img = qr_img.resize((qr_size, qr_size))
    qr_x = width - qr_size - 46
    qr_y = 430
    img.paste(qr_img, (qr_x, qr_y))
    draw.text((qr_x, qr_y + qr_size + 14), "SCAN TO PARTICIPATE", fill=(24, 24, 27), font=section_font)

    ussd_y = qr_y + qr_size + 90
    draw.text((36, ussd_y), "NO INTERNET?", fill=(24, 24, 27), font=section_font)
    draw.text((36, ussd_y + 26), f"Dial {ussd_code} and select Bill #{bill.short_id}", fill=(24, 24, 27), font=body_font)

    draw.rectangle([0, height - 96, width, height], fill=(6, 78, 59))
    draw.text((36, height - 70), "Your Voice. Your Law. Voiced.ke", fill="white", font=section_font)

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out.getvalue()


def _elevenlabs_tts(text: str, *, language_code: str = "en") -> bytes:
    api_key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    voice_id = (
        os.getenv(f"ELEVENLABS_VOICE_ID_{language_code.upper()}")
        or os.getenv("ELEVENLABS_VOICE_ID")
        or ""
    ).strip()
    model_id = (os.getenv("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2").strip()
    if not api_key or not voice_id or not text:
        return b""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "text": text[:900],
        "model_id": model_id,
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.75},
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.ok and resp.content:
            return resp.content
    except Exception:
        return b""
    return b""


def generate_bill_audio(bill) -> dict:
    """
    Generate tri-lingual MP3 summaries for a bill and save to media storage.
    """
    mapping = [
        ("en", (bill.summary_en or "").strip(), "audio_summary_en"),
        ("sw", (bill.summary_sw or "").strip(), "audio_summary_sw"),
        ("sh", (bill.summary_sh or "").strip(), "audio_summary_sh"),
    ]
    updated_fields = []
    generated = {}

    for lang, summary, field_name in mapping:
        if not summary:
            continue
        audio = _elevenlabs_tts(summary, language_code=lang)
        if not audio:
            continue
        filename = f"bill_{bill.short_id}_{lang}.mp3"
        getattr(bill, field_name).save(filename, ContentFile(audio), save=False)
        updated_fields.append(field_name)
        generated[lang] = getattr(bill, field_name).name

    if updated_fields:
        bill.save(update_fields=updated_fields)
    return generated
