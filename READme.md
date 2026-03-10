# Voiced 🇰🇪

### *The Nation Is Talking*

**Voiced** is a legislative accessibility engine designed to bridge the gap between **government policy** and **citizen understanding**.

Across many African nations, laws are published as dense **100-page PDFs written in Legal English**, making them difficult for ordinary citizens to read, understand, or discuss.

**Voiced uses AI to simplify, translate, and distribute these laws to every Kenyan** — whether they use a high-end smartphone or a **500-shilling feature phone**.

---

# The Problem

### 1. The Jargon Barrier

Legislation is written in **complex Legal English**, creating a barrier that prevents the average citizen from understanding laws that affect their lives.

### 2. The Digital Access Gap

Government documents are often **massive PDF files** that:

* consume expensive mobile data
* crash low-memory phones
* are difficult to navigate on small screens

This disproportionately affects citizens in **rural areas and informal settlements**.

### 3. Language Exclusion

National conversations are often limited to people comfortable with **formal English**, excluding millions of citizens who communicate primarily in **Sheng** or **Kiswahili**.

---

# The Solution — A Multi-Channel AI Bridge

Voiced transforms complex legislation into a **real-time, community-driven conversation platform**.

---

# AI-Powered Legislative Engine

### Gemini-Powered Summaries

Complex bills are processed using AI to generate **“The Bottom Line”** — simple summaries explaining **how the law actually affects citizens**.

### Sheng & Kiswahili Translation

Legal insights are translated into **everyday language**, ensuring accessibility for grassroots communities.

### AI Moderation Layer

A moderation layer automatically filters:

* hate speech
* incitement
* toxic content

This ensures the platform remains a **safe environment for civic debate**.

---

# Real-Time Engagement

### Live Notifications

Citizens receive **instant alerts** the moment a bill moves in Parliament.

No page refresh needed.

### Community Live Chat

Users can discuss bills **in real time**, allowing the public to debate national policy together.

---

# The USSD & SMS Handshake

Voiced ensures **every Kenyan can participate**, even without internet.

### USSD Portal

Dial:

```
*384*86584#
```

Users can:

* read AI-generated bill summaries
* navigate legislation easily
* cast a verified vote

All **without internet access**.

---

### SMS Alerts

When new legislation is processed, the system sends notifications through **Short Code 66160**.

Users receive updates in their **preferred language**.

---

# Technical Architecture

| Layer              | Technology                                             |
| ------------------ | ------------------------------------------------------ |
| Backend            | Django (Python)                                        |
| Frontend           | Django Templates (HTML / CSS / JS)                     |
| Real-Time          | Django Channels & WebSockets                           |
| AI Processing      | Gemini                                                 |
| Content Moderation | LLMAPI / OpenAI                                        |
| Connectivity       | Africa's Talking (USSD, SMS Gateway, Short Code 66160) |

---

# Installation & Setup

## Prerequisites

* Python **3.10+**
* Africa’s Talking API Key
* Gemini API Key
* LLMAPI Key

---

# Backend Setup

## 1. Clone the Repository

```bash
git clone https://github.com/JamesMatata/Voiced
cd Voiced
```

---

## 2. Environment Configuration

Create a `.env` file:

```
DJANGO_SECRET_KEY=your-key
GEMINI_API_KEY=your-gemini-key
LLMAPI_KEY=your-llmapi-key

AT_USERNAME=sandbox
AT_API_KEY=your-at-key
AT_USSD_CODE=*384*86584#
AT_SENDER_ID=66160

BASE_URL=your-base-url
```

---

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

---

## 4. Run Migrations

```bash
python manage.py migrate
```

---

## 5. Start the Development Server

```bash
python manage.py runserver
```

Visit:

```
http://127.0.0.1:8000
```

---

# Mission

Voiced exists to **democratize legislation**.

Every citizen deserves to:

* understand the laws that govern them
* participate in national conversations
* shape the future of their country

**The Nation Is Talking.**

---

# Founders

### James Matata

**Co-Founder & Developer**

GitHub: [https://github.com/JamesMatata](https://github.com/JamesMatata)

---

### Gloria Nduta

**Co-Founder & Developer**

Role: Software Development & Platform Development

---

# Contributing

We welcome contributions that help improve **legislative accessibility and civic engagement**.

Feel free to open issues or submit pull requests.

---

# License

This project is licensed under the **MIT License**.
