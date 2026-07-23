"""
UniBot — Broadcast system.
Lecturers and class reps send commands; bot delivers individually
to every registered student in the target group, logging delivery
per student (Objective ii — timestamped delivery confirmation).

Sender WhatsApp commands:
  BROADCAST <year|unit> <message>     e.g. BROADCAST 4 Class moved to Lab 3
  DEADLINE <unit> <title> — <date>    e.g. DEADLINE CCS4102 Assignment 2 — Friday 20 July
"""

import os
import re
import json
import logging
from datetime import datetime
from crypto import decrypt_phone

from database import get_db, sha256, log_engagement, _ph

logger = logging.getLogger("unibot.broadcast")

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

_twilio_client = None
if TWILIO_SID and TWILIO_TOKEN:
    from twilio.rest import Client
    _twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)


# ─────────────────────────────────────────────────────────────
def is_registered_sender(phone: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("SELECT 1 FROM senders WHERE phone_hash = %s"), (sha256(phone),))
        return cur.fetchone() is not None


def get_sender(phone: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("SELECT * FROM senders WHERE phone_hash = %s"), (sha256(phone),))
        return cur.fetchone()


def add_sender(phone: str, name: str, role: str, units=None, year_group=None):
    """Admin function — register a lecturer or class rep as a sender."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""INSERT INTO senders (phone_hash, name, role, units, year_group)
                       VALUES (%s, %s, %s, %s, %s)"""),
                    (sha256(phone), name, role, json.dumps(units or []), year_group))
    logger.info(f"Sender added: {name} ({role})")


# ─────────────────────────────────────────────────────────────
def handle_sender_command(phone: str, message: str):
    """
    Parse sender commands. Returns a reply string, or None if the
    message isn't a sender command (falls through to student handling).
    """
    sender = get_sender(phone)
    if not sender:
        return None

    def field(r, name, idx):
        return r[name] if hasattr(r, "keys") else r[idx]

    name = field(sender, "name", 1)
    role = field(sender, "role", 2)

    m = message.strip()

    # ── BROADCAST command ──
    bc = re.match(r"^BROADCAST\s+(\S+)\s+(.+)$", m, re.I | re.S)
    if bc:
        target, body = bc.group(1), bc.group(2).strip()
        return execute_broadcast(phone, name, role, target, body)

    # ── DEADLINE command ──
    dl = re.match(r"^DEADLINE\s+(\S+)\s+(.+?)\s*[—-]\s*(.+)$", m, re.I | re.S)
    if dl:
        unit, title, due = dl.group(1).upper(), dl.group(2).strip(), dl.group(3).strip()
        return add_deadline(phone, name, unit, title, due)

    # Not a sender command → None means fall through
    return None


# ─────────────────────────────────────────────────────────────
def execute_broadcast(sender_phone, sender_name, sender_role, target, body):
    """
    Deliver a broadcast to every student in the target group.
    Target can be a year number (2/3/4) or a unit code.
    Each delivery is logged individually with timestamps.
    """
    # Resolve target students
    if target.isdigit():
        year = int(target)
        query = "SELECT student_id_hash, phone_enc FROM students WHERE year_group = %s AND status = 'ACTIVE'"
        params = (year,)
        target_label = f"Year {year}"
    else:
        # unit targeting: naive v0 — students whose units JSON contains it
        query = "SELECT student_id_hash, phone_enc FROM students WHERE units LIKE %s AND status = 'ACTIVE'"
        params = (f"%{target.upper()}%",)
        target_label = target.upper()

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph(query), params)
        students = cur.fetchall()

        # Record the broadcast
        cur.execute(_ph("""INSERT INTO broadcasts
                       (sender_hash, sender_name, sender_role, message, target_year, target_unit)
                       VALUES (%s, %s, %s, %s, %s, %s)"""),
                    (sha256(sender_phone), sender_name, sender_role, body,
                     int(target) if target.isdigit() else None,
                     None if target.isdigit() else target.upper()))
        # Get broadcast id (works in both DBs)
        cur.execute("SELECT MAX(id) FROM broadcasts")
        broadcast_id = cur.fetchone()[0]

    if not students:
        return f"⚠️ No registered students found in {target_label}. Broadcast saved but not delivered."

    tag = "🔵 OFFICIAL" if sender_role == "LECTURER" else "🟡 CLASS REP"
    formatted = (f"📢 {tag} — {sender_name}\n\n"
                 f"{body}\n\n"
                 f"Sent: {datetime.now().strftime('%a %d %b %Y, %I:%M %p')}\n"
                 f"— UniBot DKUT")

    delivered, failed = 0, 0
    for s in students:
        def field(r, name, idx):
            return r[name] if hasattr(r, "keys") else r[idx]
        sid = field(s, "student_id_hash", 0)
        # NOTE: we store phone HASHES — for actual delivery Twilio needs the
        # real number. In production, store an encrypted phone alongside the
        # hash (AES) OR keep a delivery mapping in Redis at registration.
        # v0 approach: deliveries executed via Redis-stored plain mapping.
        real_phone = decrypt_phone(field(s, "phone_enc", 1))
        ok = send_whatsapp(real_phone, formatted) if real_phone else False
        cur = conn.cursor()
        cur.execute(_ph("""INSERT INTO delivery_log
                           (broadcast_id, student_id_hash, sent_at, delivered_at, status)
                           VALUES (%s, %s, CURRENT_TIMESTAMP,
                                   CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
                                   %s)""") if False else _ph("""INSERT INTO delivery_log
                           (broadcast_id, student_id_hash, sent_at, status)
                           VALUES (%s, %s, CURRENT_TIMESTAMP, %s)"""),
                        (broadcast_id, sid, "DELIVERED" if ok else "FAILED"))
        log_engagement(sid, "BROADCAST_RECEIVED", f"broadcast:{broadcast_id}")
        delivered += 1 if ok else 0
        failed += 0 if ok else 1

    return (f"✅ Broadcast sent to {target_label}\n"
            f"Delivered: {delivered}\n"
            f"Failed: {failed}\n"
            f"Broadcast ID: {broadcast_id}")


# ─────────────────────────────────────────────────────────────
# Phone delivery mapping.
# Privacy design: DB stores hashes only. The hash→real-number mapping
# lives in Redis (or memory in dev), set at registration time, so the
# relational DB never holds plaintext numbers.
from session import _redis, _memory_store
import time as _time


def store_phone_mapping(phone: str):
    key = f"phonemap:{sha256(phone)}"
    if _redis:
        _redis.set(key, phone)
    else:
        _memory_store[key] = (phone, _time.time() + 10**9)


def get_phone_from_hash(phone_hash: str):
    key = f"phonemap:{phone_hash}"
    if _redis:
        return _redis.get(key)
    entry = _memory_store.get(key)
    return entry[0] if entry else None


def send_whatsapp_by_hash(phone_hash: str, message: str) -> bool:
    real = get_phone_from_hash(phone_hash)
    if not real:
        logger.warning(f"No phone mapping for hash {phone_hash[:8]}…")
        return False
    return send_whatsapp(real, message)


def send_whatsapp(phone: str, message: str) -> bool:
    if not _twilio_client:
        logger.info(f"DEV MODE — would send to {phone[-4:]}****:\n{message[:80]}")
        return True
    try:
        _twilio_client.messages.create(
            from_=TWILIO_FROM,
            to=f"whatsapp:{phone}",
            body=message
        )
        return True
    except Exception as e:
        logger.error(f"Twilio send failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────
def add_deadline(sender_phone, sender_name, unit, title, due):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""INSERT INTO deadlines (unit_code, title, due_date, created_by)
                       VALUES (%s, %s, %s, %s)"""),
                    (unit, title, due, sender_name))
    # Also broadcast it to affected students
    body = f"📌 New deadline:\n{unit} — {title}\nDue: {due}"
    # naive: broadcast by unit
    return execute_broadcast(sender_phone, sender_name, "LECTURER", unit, body)
