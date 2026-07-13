# UniBot — DKUT

A WhatsApp-based academic communication and student engagement monitoring
system for the Department of Computer Science, Dedan Kimathi University of
Technology.

**Author:** Potason
**Supervisor:** Mr. Patrick Naivasha

---

## What it does

- Lecturers and class reps broadcast messages that reach every student
  individually on WhatsApp, with delivery confirmation
- Students query their timetable, deadlines and FAQs directly
- Detects scheduling conflicts (venue, lecturer, student overlaps)
- Machine learning flags disengaged students for academic advisors

---

## Quick Start (Windows)

### 1. Create a virtual environment
```
cd unibot-dkut
python -m venv venv
venv\Scripts\activate
```
You should now see `(venv)` at the start of your command line.

### 2. Install dependencies
```
pip install -r requirements.txt
```

### 3. Set up environment variables
```
copy .env.example .env
```
Leave everything blank for now — the bot runs in development mode
(OTPs and messages print to the console instead of actually sending).

### 4. Run the bot
```
cd backend
uvicorn main:app --reload
```
Open http://localhost:8000 in your browser — you should see
`{"status": "UniBot is running"}`.

### 5. Load the timetable
With the server running, in a second terminal:
```
cd unibot-dkut\backend
venv\Scripts\activate
python load_timetable.py
```

---

## Project Structure

```
unibot-dkut/
├── backend/
│   ├── main.py          FastAPI app + WhatsApp webhook
│   ├── database.py      Tables (SQLite dev / PostgreSQL prod)
│   ├── auth.py          Registration + OTP verification
│   ├── session.py       Sessions + global commands
│   ├── timetable.py     Excel parser + timetable queries
│   ├── queries.py       Student query routing + answers
│   ├── broadcast.py     Lecturer/class rep broadcasting
│   └── ml/              Machine learning models
├── data/                Timetable file + ML training data
├── tests/               Test scripts
├── requirements.txt
├── .env.example
└── README.md
```

---

## Development Mode

With `EMAIL_ADDRESS`, `TWILIO_*`, `DATABASE_URL` and `REDIS_URL` all blank:
- OTP codes print to the console (no real email sent)
- WhatsApp messages print to the console (no real message sent)
- Data stored in a local `unibot.db` SQLite file
- Sessions kept in memory

This lets you develop and test the entire bot on your laptop with zero
external accounts. Add real credentials later to go live.
