"""
UniBot — Broadcast system.
Lecturers and class reps send commands; bot delivers individually
to every registered student in the target group, logging delivery
per student (Objective ii — timestamped delivery confirmation).

Sender WhatsApp commands:
  BROADCAST <year|unit> <message>     e.g. BROADCAST 4 Class moved to Lab 3
  DEADLINE <unit> <title> — <date>    e.g. DEADLINE CCS4102 Assignment 2 — Friday 30 Oct

Admin-only WhatsApp commands (engagement model pauses, e.g. strikes):
  PAUSE ML [YYYY-MM-DD] <reason>      e.g. PAUSE ML 2026-10-13 lecturers strike
  RESUME ML                           classes are back — count activity again
  ML STATUS                           show current and past pauses
"""

import os
import re
import json
import logging
from datetime import datetime
from crypto import decrypt_phone
from datetime import datetime, timezone, timedelta

from database import get_db, sha256, log_engagement, _ph
from timetable import normalise_unit_code

EAT = timezone(timedelta(hours=3))  # East Africa Time (Kenya)

def now_kenya():
    return datetime.now(EAT)
logger = logging.getLogger("unibot.broadcast")

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# Twilio reports delivered / read back to this URL (WhatsApp read receipts).
# Railway sets RAILWAY_PUBLIC_DOMAIN automatically.
_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
STATUS_CALLBACK = os.getenv("STATUS_CALLBACK_URL") or (
    f"https://{_domain}/webhook/status" if _domain else "")

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
    sender_year = field(sender, "year_group", 4)

    m = message.strip()

    from staff import handle_staff_command, remember_sender_phone, rep_may_target
    remember_sender_phone(phone)

    # ── Lecturer / admin commands: REQUESTS, APPROVE, REJECT, SENDERS, REMOVE SENDER ──
    staff_reply = handle_staff_command(phone, name, role, m)
    if staff_reply:
        return staff_reply

    # ── ML pause commands (admins only) ──
    if re.match(r"^(PAUSE ML|RESUME ML|ML STATUS)\b", m, re.I):
        if role != "ADMIN":
            return "⛔ Only an admin can pause or resume the engagement model."
        from ml_pauses import handle_pause_command
        return handle_pause_command(m, name)

    # ── BROADCAST command ──
    bc = re.match(r"^BROADCAST\s+([A-Z]{3}\s?\d{4}|\S+)\s+(.+)$", m, re.I | re.S)
    if bc:
        target, body = bc.group(1), bc.group(2).strip()
        if role == "CLASS_REP":
            refused = rep_may_target(sender_year, target, _years_for)
            if refused:
                return refused
        return execute_broadcast(phone, name, role, target, body)

    # ── DEADLINE command ──
    dl = re.match(r"^DEADLINE\s+([A-Z]{3}\s?\d{4}|\S+)\s+(.+?)\s*[—-]\s*(.+)$", m, re.I | re.S)
    if dl:
        unit, title, due = dl.group(1).upper(), dl.group(2).strip(), dl.group(3).strip()
        if role == "CLASS_REP":
            refused = rep_may_target(sender_year, unit, _years_for)
            if refused:
                return refused
        return add_deadline(phone, name, role, unit, title, due)

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
        # Unit target: send to every year group that takes this unit on the live timetable
        unit = normalise_unit_code(target)
        years = years_taking_unit(unit) if unit else []
        if not years:
            return (f"⚠️ {target.upper()} is not on the current timetable, so nothing was sent.\n"
                    f"Check the unit code, or send to a year instead, e.g. BROADCAST 4 <message>")
        placeholders = ", ".join(["%s"] * len(years))
        query = (f"SELECT student_id_hash, phone_enc FROM students "
                 f"WHERE year_group IN ({placeholders}) AND status = 'ACTIVE'")
        params = tuple(years)
        target = unit
        target_label = f"{unit} (Year {', '.join(str(y) for y in years)})"

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
                     None if target.isdigit() else target))
        # Get broadcast id (works in both SQLite and PostgreSQL)
        cur.execute("SELECT MAX(id) AS max_id FROM broadcasts")
        row = cur.fetchone()
        broadcast_id = row["max_id"] if hasattr(row, "keys") else row[0]
    if not students:
        return f"⚠️ No registered students found in {target_label}. Broadcast saved but not delivered."

    tag = "🟡 CLASS REP" if sender_role == "CLASS_REP" else "🔵 OFFICIAL"
    formatted = (f"📢 {tag} — {sender_name}\n\n"
                 f"{body}\n\n"
                 f"Sent: {now_kenya().strftime('%a %d %b %Y, %I:%M %p')}\n"
                 f"— UniBot DKUT")

    # Send to everyone first, collect results
    results = []
    delivered, failed = 0, 0
    for s in students:
        def field(r, name, idx):
            return r[name] if hasattr(r, "keys") else r[idx]
        sid = field(s, "student_id_hash", 0)
        real_phone = decrypt_phone(field(s, "phone_enc", 1))
        ok = send_whatsapp(real_phone, formatted) if real_phone else None
        results.append((sid, "SENT" if ok else "FAILED", ok if isinstance(ok, str) else None))
        delivered += 1 if ok else 0
        failed += 0 if ok else 1

    # Log everything in ONE connection
    with get_db() as conn:
        cur = conn.cursor()
        for sid, status, msg_sid in results:
            cur.execute(_ph("""INSERT INTO delivery_log
                           (broadcast_id, student_id_hash, sent_at, status, message_sid)
                           VALUES (%s, %s, CURRENT_TIMESTAMP, %s, %s)"""),
                        (broadcast_id, sid, status, msg_sid))

    for sid, _, _ in results:
        log_engagement(sid, "BROADCAST_RECEIVED", f"broadcast:{broadcast_id}")

    return (f"✅ Broadcast sent to {target_label}\n"
            f"Sent: {delivered}\n"
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


def send_whatsapp(phone: str, message: str):
    """Send one WhatsApp message. Returns Twilio's message ID (truthy) or None."""
    if not _twilio_client:
        logger.info(f"DEV MODE — would send to {phone[-4:]}****:\n{message[:80]}")
        return "DEV"
    try:
        extra = {"status_callback": STATUS_CALLBACK} if STATUS_CALLBACK else {}
        msg = _twilio_client.messages.create(
            from_=TWILIO_FROM,
            to=f"whatsapp:{phone}",
            body=message,
            **extra
        )
        return msg.sid
    except Exception as e:
        logger.error(f"Twilio send failed: {e}")
        return None


def record_message_status(message_sid: str, status: str):
    """
    Twilio calls /webhook/status as a broadcast is delivered and then read.
    The first time a student reads a broadcast it is logged as BROADCAST_READ,
    which feeds the 'announcements_read' feature of the engagement model.
    """
    status = (status or "").lower()
    if status not in ("delivered", "read", "failed", "undelivered"):
        return
    new = {"delivered": "DELIVERED", "read": "READ"}.get(status, "FAILED")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""SELECT broadcast_id, student_id_hash, status FROM delivery_log
                       WHERE message_sid = %s"""), (message_sid,))
        row = cur.fetchone()
        if not row:
            return
        bid = row["broadcast_id"] if hasattr(row, "keys") else row[0]
        sid = row["student_id_hash"] if hasattr(row, "keys") else row[1]
        old = row["status"] if hasattr(row, "keys") else row[2]
        if old == "READ" or (old == "DELIVERED" and new == "FAILED"):
            return                      # never move backwards
        if new == "DELIVERED":
            cur.execute(_ph("""UPDATE delivery_log SET status = %s,
                           delivered_at = CURRENT_TIMESTAMP WHERE message_sid = %s"""),
                        (new, message_sid))
        else:
            cur.execute(_ph("UPDATE delivery_log SET status = %s WHERE message_sid = %s"),
                        (new, message_sid))
    if new == "READ":
        log_engagement(sid, "BROADCAST_READ", f"broadcast:{bid}")


# ─────────────────────────────────────────────────────────────
_WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def check_due_date(due: str, today=None):
    """
    Catch typos before a deadline goes out to a whole class.
    'Friday 24 Oct' when 24 Oct is a Saturday -> error message.
    A date that has already passed -> error message.
    Free text with no date ('next week') is allowed. Returns None if fine.
    """
    today = today or now_kenya().date()
    u = due.upper()
    dm = (re.search(r"\b(\d{1,2})(?:ST|ND|RD|TH)?\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\b", u)
          or re.search(r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s+(\d{1,2})(?:ST|ND|RD|TH)?\b", u))
    nm = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", u)   # 24/10 or 24/10/2026 (day first)
    if dm:
        a, b = dm.group(1), dm.group(2)
        day, month = (int(a), _MONTHS.index(b) + 1) if a.isdigit() else (int(b), _MONTHS.index(a) + 1)
    elif nm:
        day, month = int(nm.group(1)), int(nm.group(2))
    else:
        # A day number with no readable month ("Friday 24 Oc") is a typo, not free text.
        # Times (2PM, 10:30) and rooms (LH2, RC 18) don't count as day numbers.
        rest = re.sub(r"\b\d{1,2}(:\d{2})?\s*(AM|PM)\b|\b\d{1,2}:\d{2}\b", " ", u)
        rest = re.sub(r"\b(LH|RC|RCL|LAB|LT|ROOM|CR|WEEK|WK|LESSON|CHAPTER|CH|Q|QUESTION|PAGE|PG)\s*\d+\b", " ", rest)
        if re.search(r"\b([1-9]|[12]\d|3[01])(ST|ND|RD|TH)?\b", rest):
            return ("⚠️ I couldn't read the date in \"" + due.strip() + "\".\n"
                    "Nothing was sent. Write it like: Fri 30 Oct (or 30/10), then send the DEADLINE again.")
        return None
    ym = re.search(r"\b(20\d\d)\b", u)
    year = int(ym.group(1)) if ym else today.year
    try:
        when = datetime(year, month, day).date()
    except ValueError:
        return f"⚠️ {day}/{month} is not a real date. Check it and send the DEADLINE again."
    if not ym and (today - when).days > 180:      # e.g. "5 Jan" sent in November
        when = when.replace(year=year + 1)

    wd = re.search(r"\b(MON|TUE|WED|THU|FRI|SAT|SUN)(?:DAY|SDAY|NESDAY|RSDAY|URDAY|S|R|RS)?\b", u)
    if wd:
        said = next(d for d in _WEEKDAYS if d.startswith(wd.group(1)))
        real = _WEEKDAYS[when.weekday()]
        if said != real:
            return (f"⚠️ {when.day} {when:%b %Y} is a {real.title()}, not a {said.title()}.\n"
                    f"Nothing was sent. Check the date and send the DEADLINE again.")
    if when < today:
        return (f"⚠️ {when:%a} {when.day} {when:%b %Y} has already passed.\n"
                f"Nothing was sent. Check the date and send the DEADLINE again.")
    return None


def add_deadline(sender_phone, sender_name, sender_role, unit, title, due):
    """Save a deadline, then announce it to every year that takes the unit."""
    code = normalise_unit_code(unit)
    if not code or not years_taking_unit(code):
        return (f"⚠️ {unit} is not on the current timetable, so the deadline was not saved.\n"
                f"Format: DEADLINE CCS4102 Assignment 2 — Friday 30 Oct")
    problem = check_due_date(due)
    if problem:
        return problem
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""INSERT INTO deadlines (unit_code, title, due_date, created_by)
                       VALUES (%s, %s, %s, %s)"""),
                    (code, title, due, sender_name))
    body = f"📌 New deadline:\n{code} — {title}\nDue: {due}"
    return execute_broadcast(sender_phone, sender_name, sender_role, code, body)


def _years_for(target: str):
    unit = normalise_unit_code(target)
    return years_taking_unit(unit) if unit else []


def years_taking_unit(unit_code: str):
    """Year groups that have this unit on the live timetable, e.g. [4]."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""SELECT DISTINCT year_group FROM timetable
                       WHERE REPLACE(unit_code, ' ', '') = %s"""),
                    (unit_code.replace(" ", ""),))
        rows = cur.fetchall()
    return sorted(int(r["year_group"] if hasattr(r, "keys") else r[0]) for r in rows)