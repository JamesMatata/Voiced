# Voiced

Voiced is a multi-channel civic participation platform that helps citizens understand legislation, discuss it, and submit verifiable sentiment through Web, USSD, and Voice.

## Core Capabilities

- AI-powered bill summaries and analysis (English, Kiswahili, Sheng)
- Public participation through:
  - Web UI
  - USSD sessions
  - Voice call (IVR keypad vote capture)
- Identity verification flow with paid KYC
- Wallet + M-Pesa payment rails with pending-state reliability handling
- Public vote receipt ledger (anonymized, searchable, CSV export)
- Bill outcome feedback loop across in-app, email, and SMS
- Printable outreach posters (PDF/PNG)

## Feature Map

### 1) Bill Intelligence
- Scrapes bill sources
- Runs AI analysis and translations
- Produces:
  - Short summaries
  - Full markdown overviews
  - SMS-friendly summary
  - Tri-lingual bill audio summaries

### 2) Voting and Receipts
- Vote channels: Web, USSD, Voice
- Receipt generation: deterministic `receipt_id` hash per vote
- Public ledger:
  - Receipt search
  - Pagination
  - CSV export

### 3) Identity and Official Vote Status
- Paid KYC verification flow (wallet or M-Pesa STK)
- National ID encrypted at rest (`EncryptedCharField`)
- `is_kenyan` acts as the official-vote gate
- On successful verification, ongoing bill votes are promoted to verified counters

### 4) Payments and Wallet
- Wallet top-up via M-Pesa STK
- Service deductions (reports, drafts, voice summary)
- Reservation/release pattern for paid services
- Stale pending transaction sweeper and reliability notifications

### 5) USSD + Voice
- USSD menu supports:
  - Browse/search bills
  - Vote
  - Identity verification
  - Wallet top-up
  - Paid “Listen to Summary”
- Voice callback:
  - Plays language audio summary
  - Captures keypad vote (1 support / 2 oppose)

### 6) Notifications
- Channels: websocket/in-app, email, SMS
- Outcome notifications with resend admin action
- Post-vote feedback SMS with receipt + verification nudge (if unverified)

## Tech Stack

| Area | Tools |
|---|---|
| Backend | Django |
| Realtime | Django Channels + channels_redis |
| Task Queue | Celery + Redis |
| DB | SQLite (default), ORM-compatible with relational DBs |
| AI Text | Gemini |
| KYC | Smile ID Enhanced KYC API |
| Mobile Money | Safaricom M-Pesa STK integration |
| Telco/Comms | Africa's Talking (USSD, SMS, Voice) |
| Media/Docs | Pillow, ReportLab, qrcode |
| Frontend | Django Templates + HTMX + Tailwind CDN |

## Environment Variables

Create `.env` in project root:

```env
# Django
DJANGO_SECRET_KEY=change-me
BASE_URL=http://127.0.0.1:8000
VOTE_RECEIPT_SALT=optional-custom-salt
USSD_SHORTCODE=*384*86584#
USSD_VERIFY_OPTION_INDEX=4
SITE_ID=1

# AI
GEMINI_API_KEY=your-gemini-key
LLMAPI_KEY=your-llmapi-key

# Smile ID KYC
SMILE_ID_BASE_URL=https://your-smile-host
SMILE_ID_API_KEY=your-smile-api-key

# M-Pesa
MPESA_ENV=sandbox
MPESA_CONSUMER_KEY=...
MPESA_CONSUMER_SECRET=...
MPESA_SHORTCODE=...
MPESA_PASSKEY=...
MPESA_INITIATOR_NAME=...
MPESA_INITIATOR_PASSWORD=...

# Africa's Talking
AFRICASTALKING_USERNAME=sandbox
AFRICASTALKING_API_KEY=your-at-api-key
AFRICASTALKING_SENDER_ID=66160
AFRICASTALKING_VOICE_NUMBER=+2547XXXXXXXX
AT_USSD_CODE=*384*86584#

# ElevenLabs (bill audio)
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=default-voice-id
ELEVENLABS_VOICE_ID_EN=optional
ELEVENLABS_VOICE_ID_SW=optional
ELEVENLABS_VOICE_ID_SH=optional
ELEVENLABS_MODEL_ID=eleven_multilingual_v2

# Redis / cache
DJANGO_CACHE_REDIS_URL=redis://127.0.0.1:6379/1
DJANGO_USE_LOCAL_CACHE=0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# OAuth (optional)
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
APPLE_OAUTH_CLIENT_ID=
APPLE_OAUTH_CLIENT_SECRET=
```

## Local Setup

```bash
# 1) Create + activate virtualenv
python -m venv .venv
.\.venv\Scripts\activate

# 2) Install dependencies
pip install -r requirements.txt

# 3) Migrations
python manage.py makemigrations
python manage.py migrate

# 4) i18n compile
python manage.py makemessages -l sw -l sh
python manage.py compilemessages

# 5) Sanity check
python manage.py check

# 6) Run app
python manage.py runserver
```

## Required Background Services

Run each in separate terminal:

```bash
# Redis
redis-server
```

```bash
# Celery worker
celery -A Voiced worker -l info
```

```bash
# Celery beat (scheduled jobs)
celery -A Voiced beat -l info
```

## High-Value Admin/Ops Commands

```bash
# list migration state
python manage.py showmigrations

# sweep stale pending payment states manually
python manage.py sweep_pending_transactions
```

## Reliability + Integrity Notes

- National ID values are encrypted at rest in model fields.
- Financial deductions use transactional guards and `F()` updates where needed.
- STK callback is status-aware and linked to payment intent.
- Background tasks process SMS/audio/report generation and retries.
- Vote receipts are generated consistently at model level.

## Project Structure (Key Apps)

- `bills/` - bill ingestion, AI processing, poster/audio generation, admin workflows
- `core/` - primary web views and templates (bill detail, ledger, exports)
- `engagement/` - USSD and voice callbacks
- `accounts/` - profiles, KYC, wallet view, auth flows
- `payments/` - transactions, M-Pesa callbacks, wallet operations, reliability tasks
- `notifications/` - participant and outcome notification logic

## Contributing

Contributions are welcome. For significant changes:
- open an issue first
- include reproducible steps
- include test/verification notes for Web + USSD + payment impact

## License

MIT
