"""
UniBot — Registration and authentication.
Flow: JOIN → Student ID → DKUT email → OTP → year selection → registered.
Identity anchor: Student ID + institutional email (NOT the phone).
"""

import os
import re
import json
import random
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from crypto import encrypt_phone

from database import get_db, sha256, log_engagement, _ph
from session import get_session, set_session, clear_session

logger = logging.getLogger("unibot.auth")

STUDENT_ID_PATTERN = re.compile(r"^C026-\d{2}-\d{4}/\d{4}$")
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@students\.dkut\.ac\.ke$")

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")

MAX_OTP_ATTEMPTS = 3

# Consent shown before anyone registers. Change the version if the wording
# changes, so you can tell which text each student agreed to.
CONSENT_VERSION = "v1-2026-11"
CONSENT_TEXT = (
    "📄 Before you join: consent\n\n"
    "UniBot is a final-year project in the Department of Computer Science, "
    "DKUT (Ted Kimemia, supervised by Mr. Patrick Naivasha).\n\n"
    "If you join:\n"
    "• We store your student ID (hashed), phone number (encrypted), "
    "DKUT email and year.\n"
    "• We log your questions and when you read broadcasts, to answer you "
    "and to study student engagement.\n"
    "• A weekly report may flag students who seem disengaged so an advisor "
    "can check in. It never shows names and never affects marks.\n"
    "• Results are reported without names. Your data is deleted within "
    "3 months after the project is assessed.\n\n"
    "You can stop anytime by texting STOP. To have your data deleted, "
    "email unibot.dkut@gmail.com.\n\n"
    "Reply YES to agree and continue, or NO to cancel."
)
YES_WORDS = {"YES", "Y", "AGREE", "I AGREE", "NDIO", "OK", "OKAY"}
NO_WORDS = {"NO", "N", "DECLINE", "HAPANA"}


# ─────────────────────────────────────────────────────────────
def is_registered(phone: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("SELECT 1 FROM students WHERE phone_hash = %s AND status = 'ACTIVE'"),
                    (sha256(phone),))
        return cur.fetchone() is not None


def get_student(phone: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("SELECT * FROM students WHERE phone_hash = %s"), (sha256(phone),))
        return cur.fetchone()


def has_consent(phone: str) -> bool:
    """True if this registered student has agreed to the consent notice."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("SELECT consent_at FROM students WHERE phone_hash = %s"),
                    (sha256(phone),))
        row = cur.fetchone()
    if not row:
        return False
    value = row["consent_at"] if hasattr(row, "keys") else row[0]
    return bool(value)


def handle_existing_consent(phone: str, message: str) -> str:
    """Students who registered before the consent step are asked once.
    Their next message shows the notice; YES records consent, NO opts them out."""
    session = get_session(phone) or {}
    if session.get("state") != "RECONSENT":
        set_session(phone, {"state": "RECONSENT"}, expire_seconds=3600)
        return ("UniBot now asks every student for consent.\n\n" + CONSENT_TEXT)

    answer = message.upper().strip()
    if answer in YES_WORDS:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(_ph("""UPDATE students SET consent_at = %s, consent_version = %s
                               WHERE phone_hash = %s"""),
                        (_now_utc(), CONSENT_VERSION, sha256(phone)))
        clear_session(phone)
        return ("✅ Thank you, consent recorded.\n\n"
                "Ask me anything, e.g. \"When is my next class?\"")
    if answer in NO_WORDS:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(_ph("UPDATE students SET status = 'OPTED_OUT' WHERE phone_hash = %s"),
                        (sha256(phone),))
        clear_session(phone)
        return ("You have been unsubscribed from UniBot. To have your data deleted, "
                "email unibot.dkut@gmail.com. Text JOIN anytime to come back.")
    return "Please reply YES to agree and continue, or NO to leave UniBot."


# ─────────────────────────────────────────────────────────────
def handle_registration_message(phone: str, message: str) -> str:
    """
    State machine for registration. State lives in Redis (or in-memory fallback).
    States: START → AWAIT_CONSENT → AWAIT_ID → AWAIT_EMAIL → AWAIT_OTP → AWAIT_YEAR → DONE
    """
    session = get_session(phone) or {}
    state = session.get("state", "START")

    # ── START ──
    if state == "START":
        if message.upper() == "JOIN":
            set_session(phone, {"state": "AWAIT_CONSENT"})
            return CONSENT_TEXT
        return ("👋 Hello! This is UniBot — the DKUT CS "
                "academic assistant.\n\n"
                "Text JOIN to register and receive class "
                "updates, timetables and deadline reminders.")

    # ── AWAIT_CONSENT ──
    if state == "AWAIT_CONSENT":
        answer = message.upper().strip()
        if answer in NO_WORDS:
            clear_session(phone)
            return ("No problem — you have not been registered and nothing "
                    "about you has been stored.\n\n"
                    "Text JOIN anytime if you change your mind.")
        if answer not in YES_WORDS:
            return "Please reply YES to agree and continue, or NO to cancel."
        set_session(phone, {"state": "AWAIT_ID", "consent": CONSENT_VERSION})
        return ("✅ Thank you.\n\n"
                "Welcome to UniBot DKUT 🎓\n\n"
                "To register, enter your Student ID.\n"
                "Format: C026-XX-XXXX/XXXX (check your student card).")

    # ── AWAIT_ID ──
    if state == "AWAIT_ID":
        student_id = message.upper().strip()
        if not STUDENT_ID_PATTERN.match(student_id):
            return ("❌ Invalid format.\n"
                    "Student ID should look like: C026-XX-XXXX/XXXX\n"
                    "Check your student card and try again.")

        # Check if this ID is already registered (phone-change case)
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(_ph("SELECT phone_hash FROM students WHERE student_id_hash = %s"),
                        (sha256(student_id),))
            existing = cur.fetchone()

        session.update({"state": "AWAIT_EMAIL", "student_id": student_id,
                        "is_transfer": existing is not None})
        set_session(phone, session)

        if existing:
            return ("We found an existing account for this Student ID "
                    "on a different phone number.\n\n"
                    "To transfer your account to this device, "
                    "enter your DKUT student email:")
        return ("✅ ID format accepted.\n\n"
                "Now enter your DKUT student email:\n"
                "(ends with @students.dkut.ac.ke)")

    # ── AWAIT_EMAIL ──
    if state == "AWAIT_EMAIL":
        email = message.lower().strip()
        if not EMAIL_PATTERN.match(email):
            return ("❌ Please use your DKUT student email.\n"
                    "It must end with @students.dkut.ac.ke")

        otp = str(random.randint(100000, 999999))
        ok, err = send_otp_email(email, otp)
        if not ok:
            logger.error(f"OTP email failed: {err}")
            return ("❌ Could not send the verification email. "
                    "Check the address and try again, or text HELP.")

        session.update({"state": "AWAIT_OTP", "email": email,
                        "otp": otp, "attempts": 0})
        set_session(phone, session, expire_seconds=300)  # OTP valid 5 min
        return (f"✅ A 6-digit code has been sent to:\n{email}\n\n"
                "Enter the code below. It expires in 5 minutes.\n"
                "(Check your spam folder if you don't see it)")

    # ── AWAIT_OTP ──
    if state == "AWAIT_OTP":
        if session.get("attempts", 0) >= MAX_OTP_ATTEMPTS:
            clear_session(phone)
            return ("❌ Too many wrong attempts. "
                    "Wait 30 minutes then text JOIN to start again.")

        if message.strip() != session.get("otp"):
            session["attempts"] = session.get("attempts", 0) + 1
            set_session(phone, session, expire_seconds=300)
            left = MAX_OTP_ATTEMPTS - session["attempts"]
            return f"❌ Wrong code. {left} attempt(s) remaining."

        # OTP correct
        session["state"] = "AWAIT_YEAR"
        set_session(phone, session)
        return ("✅ Email verified!\n\n"
                "Which year group are you currently in?\n"
                "1. Year 1\n2. Year 2\n3. Year 3\n4. Year 4\n\n"
                "Reply with the number.")

    # ── AWAIT_YEAR ──
    if state == "AWAIT_YEAR":
        if message.strip() not in ("1", "2", "3", "4"):
            return "Please reply with 1, 2, 3 or 4 to select your year."

        year = int(message.strip())
        student_id = session["student_id"]
        email = session["email"]
        complete_registration(phone, student_id, email, year,
                              is_transfer=session.get("is_transfer", False),
                              consent_version=session.get("consent", CONSENT_VERSION))
        clear_session(phone)

        return (f"🎉 Registered successfully!\n\n"
                f"Welcome, Year {year} CS student.\n\n"
                f"You will now receive:\n"
                f"📢 Broadcasts from your lecturers\n"
                f"📅 Timetable info — just ask!\n"
                f"⏰ Deadline reminders\n\n"
                f"Try asking: \"When is my next class?\"")

    # Fallback — unknown state
    clear_session(phone)
    return "Something went wrong. Text JOIN to start again."


# ─────────────────────────────────────────────────────────────
def _now_utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def complete_registration(phone, student_id, email, year, is_transfer=False,
                          consent_version=CONSENT_VERSION):
    id_hash = sha256(student_id)
    phone_hash = sha256(phone)
    consent_at = _now_utc()

    with get_db() as conn:
        cur = conn.cursor()
        if is_transfer:
            # Phone change — keep identity, swap phone, revoke old
            cur.execute(_ph("SELECT phone_hash FROM students WHERE student_id_hash = %s"),
                        (id_hash,))
            row = cur.fetchone()
            old_phone_hash = (list(row.values())[0] if hasattr(row, "keys") else row[0]) if row else None
            cur.execute(_ph("""UPDATE students
                       SET phone_hash = %s, phone_enc = %s, previous_phone_hash = %s,
                           email = %s, year_group = %s, status = 'ACTIVE',
                           consent_at = %s, consent_version = %s
                       WHERE student_id_hash = %s"""),
                        (phone_hash, encrypt_phone(phone), old_phone_hash, email, year,
                         consent_at, consent_version, id_hash))
        else:
            cur.execute(_ph("""INSERT INTO students
                       (student_id_hash, phone_hash, phone_enc, email, year_group,
                        consent_at, consent_version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)"""),
                        (id_hash, phone_hash, encrypt_phone(phone), email, year,
                         consent_at, consent_version))

    # Log AFTER the connection is closed — avoids SQLite lock
    event = "PHONE_TRANSFER" if is_transfer else "REGISTERED"
    detail = "Account moved to new device" if is_transfer else f"Year {year}"
    log_engagement(id_hash, event, detail)

    # Store phone mapping for broadcast delivery (hash → real number in Redis/memory)
    from broadcast import store_phone_mapping
    store_phone_mapping(phone)

    logger.info(f"Student registered (year {year}), transfer={is_transfer}")


# ──────────────────────────────────────────────────────────
def send_otp_email(to_email: str, otp: str):
    """Send the OTP over HTTPS (Railway blocks SMTP). Returns (success, error).
    Uses Brevo if BREVO_API_KEY is set, otherwise Resend, otherwise dev mode."""
    subject = "UniBot DKUT — Verification Code"
    body = (f"Your UniBot verification code is:\n\n"
            f"        {otp}\n\n"
            f"This code expires in 5 minutes.\n"
            f"Do not share it with anyone.\n\n"
            f"— UniBot, Department of Computer Science, DKUT")

    brevo_key = os.getenv("BREVO_API_KEY", "")
    sender = os.getenv("EMAIL_ADDRESS", "")

    if brevo_key and sender:
        import json
        import urllib.request
        import urllib.error
        payload = json.dumps({
            "sender": {"name": "UniBot DKUT", "email": sender},
            "to": [{"email": to_email}],
            "subject": subject,
            "textContent": body,
        }).encode()
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=payload, method="POST",
            headers={"api-key": brevo_key, "Content-Type": "application/json",
                     "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201, 202):
                    return True, ""
                return False, f"Brevo returned {resp.status}"
        except urllib.error.HTTPError as e:
            err = e.read().decode(errors="ignore")[:200]
            logger.error(f"Brevo email failed: {e.code} {err}")
            return False, err
        except Exception as e:
            logger.error(f"Brevo email failed: {e}")
            return False, str(e)

    resend_key = os.getenv("RESEND_API_KEY", "")
    if resend_key:
        try:
            import resend
            resend.api_key = resend_key
            resend.Emails.send({
                "from": "UniBot DKUT <onboarding@resend.dev>",
                "to": [to_email],
                "subject": subject,
                "text": body,
            })
            return True, ""
        except Exception as e:
            logger.error(f"Resend email failed: {e}")
            return False, str(e)

    logger.warning(f"DEV MODE — OTP for {to_email}: {otp}")
    return True, "dev-mode"