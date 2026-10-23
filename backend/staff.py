"""
UniBot — Lecturers and class reps joining by themselves.

LECTURERS (verify with their staff email):
  Lecturer:  STAFF
  UniBot:    asks for their DKUT staff email (must end @<STAFF_EMAIL_DOMAIN>)
             → emails a 6-digit code → asks the name students should see
             → they can broadcast straight away. Admins get a notice.

CLASS REPS (a lecturer approves them):
  Rep:       REP            (must already be a registered student)
  UniBot:    asks the name students should see → sends a request to
             every lecturer and admin
  Lecturer:  APPROVE 7      → the rep can now broadcast to their own year
             REJECT 7

Lecturer / admin commands:
  REQUESTS              pending class rep requests
  APPROVE <n> / REJECT <n>
  SENDERS               everyone who can broadcast
  REMOVE SENDER <n>     admins: anyone · lecturers: class reps only

A class rep can only broadcast to their own year group.
"""

import os
import re
import random
import logging

from database import get_db, sha256, _ph
from session import get_session, set_session, clear_session
from crypto import encrypt_phone, decrypt_phone

logger = logging.getLogger("unibot.staff")

STAFF_EMAIL_DOMAIN = os.getenv("STAFF_EMAIL_DOMAIN", "dkut.ac.ke").lower().strip()
MAX_OTP_ATTEMPTS = 3

COMMANDS_HELP = {
    "LECTURER": ("You can send:\n"
                 "• BROADCAST 4 <message> — to a whole year\n"
                 "• BROADCAST CCS4204 <message> — to everyone taking a unit\n"
                 "• DEADLINE CCS4204 CAT 1 — Fri 6 Nov\n"
                 "• REQUESTS — class rep requests to approve\n"
                 "• SENDERS — who can broadcast"),
    "CLASS_REP": ("You can send:\n"
                  "• BROADCAST {year} <message> — to your class\n"
                  "• DEADLINE CCS4204 Assignment 1 — Fri 30 Oct"),
    "ADMIN": ("You can send:\n"
              "• BROADCAST / DEADLINE (any year)\n"
              "• REQUESTS · APPROVE <n> · REJECT <n>\n"
              "• SENDERS · REMOVE SENDER <n>\n"
              "• PAUSE ML / RESUME ML / ML STATUS"),
}


def _f(r, name, idx):
    return r[name] if hasattr(r, "keys") else r[idx]


def _email_ok(email: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9._%+-]+@" + re.escape(STAFF_EMAIL_DOMAIN), email))


def _clean_name(text: str) -> str:
    name = re.sub(r"\s+", " ", text).strip()
    return name if 3 <= len(name) <= 40 and re.search(r"[A-Za-z]", name) else ""


def _send(phone: str, text: str):
    from broadcast import send_whatsapp
    try:
        send_whatsapp(phone, text)
    except Exception as e:
        logger.warning(f"Notice not sent: {e}")


def _senders_with_roles(roles):
    """(name, role, real phone) for senders whose number we can message."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name, role, phone_enc FROM senders")
        rows = cur.fetchall()
    out = []
    for r in rows:
        enc = _f(r, "phone_enc", 2)
        if _f(r, "role", 1) in roles and enc:
            phone = decrypt_phone(enc)
            if phone:
                out.append((_f(r, "name", 0), _f(r, "role", 1), phone))
    return out


def get_sender_row(phone: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("SELECT name, role, year_group, phone_enc FROM senders WHERE phone_hash = %s"),
                    (sha256(phone),))
        return cur.fetchone()


def remember_sender_phone(phone: str):
    """Senders added from the command line have no encrypted number yet;
    store it the first time they message, so they can receive notices."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""UPDATE senders SET phone_enc = %s
                       WHERE phone_hash = %s AND (phone_enc IS NULL OR phone_enc = '')"""),
                    (encrypt_phone(phone), sha256(phone)))


def sender_help(phone: str) -> str:
    row = get_sender_row(phone)
    if not row:
        return ""
    role, year = _f(row, "role", 1), _f(row, "year_group", 2)
    return (f"👋 Hi {_f(row, 'name', 0)}.\n\n"
            + COMMANDS_HELP.get(role, "").format(year=year or "<year>"))


# ─────────────────────────────────────────────────────────────
# Sign-up flows (run before student registration in main.py)
# ─────────────────────────────────────────────────────────────
def handle_staff_message(phone: str, message: str):
    """Returns a reply if this message belongs to a staff or rep sign-up, else None."""
    text = message.strip()
    up = text.upper()
    session = get_session(phone) or {}
    state = session.get("state", "")

    if up == "STAFF":
        if get_sender_row(phone):
            return "You're already set up.\n\n" + sender_help(phone)
        set_session(phone, {"state": "STAFF_EMAIL"})
        return ("👩‍🏫 Staff sign-up\n\n"
                f"Enter your DKUT staff email (ends with @{STAFF_EMAIL_DOMAIN}).\n"
                "Text CANCEL to stop.")

    if up == "REP":
        from auth import is_registered, get_student
        if get_sender_row(phone):
            return "You can already broadcast.\n\n" + sender_help(phone)
        if not is_registered(phone):
            return "Register as a student first: text JOIN. Then send REP."
        student = get_student(phone)
        year = _f(student, "year_group", 4)
        if _pending_request(phone):
            return "⏳ Your class rep request is already waiting for a lecturer to approve it."
        set_session(phone, {"state": "REP_NAME", "year": year})
        return (f"🟡 Class rep request (Year {year})\n\n"
                "What name should students see on your announcements?\n"
                "e.g. Jane Wanjiku")

    if state == "STAFF_EMAIL":
        email = text.lower()
        if not _email_ok(email):
            return (f"❌ Please use your DKUT staff email (ends with @{STAFF_EMAIL_DOMAIN}).\n"
                    "Student and personal emails can't be used for staff accounts.")
        from auth import send_otp_email
        otp = str(random.randint(100000, 999999))
        ok, err = send_otp_email(email, otp)
        if not ok:
            logger.error(f"Staff OTP email failed: {err}")
            return "❌ Could not send the code. Check the address and try again."
        set_session(phone, {"state": "STAFF_OTP", "email": email, "otp": otp, "attempts": 0},
                    expire_seconds=300)
        return (f"📧 A 6-digit code has been sent to {email}.\n"
                "Enter it here. It expires in 5 minutes (check Spam too).")

    if state == "STAFF_OTP":
        if text != session.get("otp"):
            session["attempts"] = session.get("attempts", 0) + 1
            if session["attempts"] >= MAX_OTP_ATTEMPTS:
                clear_session(phone)
                return "❌ Too many wrong attempts. Send STAFF to start again."
            set_session(phone, session, expire_seconds=300)
            return f"❌ Wrong code. {MAX_OTP_ATTEMPTS - session['attempts']} attempt(s) left."
        session["state"] = "STAFF_NAME"
        set_session(phone, session)
        return "✅ Email verified.\n\nWhat name should students see? (e.g. Mr. Kagiri)"

    if state == "STAFF_NAME":
        name = _clean_name(text)
        if not name:
            return "Please send a name of 3–40 letters, e.g. Dr. Moso"
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(_ph("""INSERT INTO senders (phone_hash, name, role, units, phone_enc, email)
                           VALUES (%s, %s, 'LECTURER', '[]', %s, %s)"""),
                        (sha256(phone), name, encrypt_phone(phone), session["email"]))
        clear_session(phone)
        logger.info(f"Lecturer self-registered: {name}")
        for admin_name, _, admin_phone in _senders_with_roles({"ADMIN"}):
            _send(admin_phone, f"👩‍🏫 New lecturer joined UniBot: {name} ({session['email']}).\n"
                               "Send SENDERS to see everyone, or REMOVE SENDER <n> to undo.")
        return f"✅ You're set up, {name}.\n\n" + COMMANDS_HELP["LECTURER"]

    if state == "REP_NAME":
        name = _clean_name(text)
        if not name:
            return "Please send a name of 3–40 letters, e.g. Jane Wanjiku"
        year = session["year"]
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(_ph("""INSERT INTO sender_requests (phone_hash, phone_enc, name, year_group)
                           VALUES (%s, %s, %s, %s)"""),
                        (sha256(phone), encrypt_phone(phone), name, year))
            cur.execute(_ph("SELECT MAX(id) AS id FROM sender_requests WHERE phone_hash = %s"),
                        (sha256(phone),))
            req_id = _f(cur.fetchone(), "id", 0)
        clear_session(phone)
        for _, _, approver in _senders_with_roles({"LECTURER", "ADMIN"}):
            _send(approver, f"🟡 Class rep request #{req_id}\n{name} — Year {year}\n\n"
                            f"Reply APPROVE {req_id} or REJECT {req_id}.")
        return ("⏳ Request sent. A lecturer needs to approve it — "
                "you'll get a message as soon as they do.")

    return None


def _pending_request(phone: str) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""SELECT 1 FROM sender_requests
                       WHERE phone_hash = %s AND status = 'PENDING'"""), (sha256(phone),))
        return cur.fetchone() is not None


# ─────────────────────────────────────────────────────────────
# Commands for lecturers and admins (called from broadcast.py)
# ─────────────────────────────────────────────────────────────
STAFF_COMMAND_RE = re.compile(r"^(REQUESTS|APPROVE\s+\d+|REJECT\s+\d+|SENDERS|REMOVE SENDER\s+\d+)$", re.I)


def handle_staff_command(phone: str, name: str, role: str, message: str):
    m = message.strip().upper()
    if not STAFF_COMMAND_RE.match(m):
        return None
    if role not in ("LECTURER", "ADMIN"):
        return "⛔ Only lecturers and admins can do that."

    if m == "REQUESTS":
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT id, name, year_group FROM sender_requests
                           WHERE status = 'PENDING' ORDER BY id""")
            rows = cur.fetchall()
        if not rows:
            return "No class rep requests waiting."
        lines = ["🟡 Class rep requests:"]
        for r in rows:
            lines.append(f"#{_f(r,'id',0)} {_f(r,'name',1)} — Year {_f(r,'year_group',2)}")
        lines.append("\nReply APPROVE <n> or REJECT <n>.")
        return "\n".join(lines)

    if m.startswith(("APPROVE", "REJECT")):
        approve = m.startswith("APPROVE")
        req_id = int(m.split()[1])
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(_ph("""SELECT phone_hash, phone_enc, name, year_group, status
                           FROM sender_requests WHERE id = %s"""), (req_id,))
            r = cur.fetchone()
            if not r:
                return f"No request #{req_id}."
            if _f(r, "status", 4) != "PENDING":
                return f"Request #{req_id} was already {_f(r, 'status', 4).lower()}."
            cur.execute(_ph("""UPDATE sender_requests SET status = %s, decided_by = %s,
                           decided_at = CURRENT_TIMESTAMP WHERE id = %s"""),
                        ("APPROVED" if approve else "REJECTED", name, req_id))
            if approve:
                cur.execute(_ph("DELETE FROM senders WHERE phone_hash = %s"), (_f(r, "phone_hash", 0),))
                cur.execute(_ph("""INSERT INTO senders (phone_hash, name, role, units, year_group, phone_enc)
                               VALUES (%s, %s, 'CLASS_REP', '[]', %s, %s)"""),
                            (_f(r, "phone_hash", 0), _f(r, "name", 2), _f(r, "year_group", 3),
                             _f(r, "phone_enc", 1)))
        rep_phone = decrypt_phone(_f(r, "phone_enc", 1))
        rep_name, year = _f(r, "name", 2), _f(r, "year_group", 3)
        if approve:
            if rep_phone:
                _send(rep_phone, f"✅ You're now the Year {year} class rep on UniBot "
                                 f"(approved by {name}).\n\n"
                                 + COMMANDS_HELP["CLASS_REP"].format(year=year))
            logger.info(f"Class rep approved: {rep_name} (Y{year}) by {name}")
            return f"✅ {rep_name} is now a Year {year} class rep."
        if rep_phone:
            _send(rep_phone, "Your class rep request was not approved. "
                             "Talk to your lecturer if you think this is a mistake.")
        return f"Request #{req_id} rejected."

    senders = _list_senders()
    if m == "SENDERS":
        if not senders:
            return "No senders yet."
        lines = ["📋 Who can broadcast:"]
        for i, s in enumerate(senders, 1):
            year = f" (Year {s['year']})" if s["year"] else ""
            lines.append(f"{i}. {s['name']} — {s['role'].replace('_', ' ').title()}{year}")
        lines.append("\nREMOVE SENDER <n> to remove someone.")
        return "\n".join(lines)

    # REMOVE SENDER <n>
    n = int(m.split()[-1])
    if not 1 <= n <= len(senders):
        return "No sender with that number. Send SENDERS to see the list."
    target = senders[n - 1]
    if target["phone_hash"] == sha256(phone):
        return "You can't remove yourself."
    if role == "LECTURER" and target["role"] != "CLASS_REP":
        return "⛔ Lecturers can only remove class reps."
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("DELETE FROM senders WHERE phone_hash = %s"), (target["phone_hash"],))
    logger.info(f"Sender removed: {target['name']} by {name}")
    return f"Removed {target['name']}. They can no longer broadcast."


def _list_senders():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT phone_hash, name, role, year_group FROM senders ORDER BY added_at, name")
        rows = cur.fetchall()
    return [{"phone_hash": _f(r, "phone_hash", 0), "name": _f(r, "name", 1),
             "role": _f(r, "role", 2), "year": _f(r, "year_group", 3)} for r in rows]


def rep_may_target(sender_year, target: str, years_for_unit) -> str:
    """A class rep can only reach their own year. Returns an error message or ''."""
    if not sender_year:
        return ""
    if target.isdigit():
        return "" if int(target) == int(sender_year) else \
            f"⛔ As Year {sender_year} class rep you can only broadcast to Year {sender_year}."
    years = years_for_unit(target)
    if years and int(sender_year) not in years:
        return f"⛔ {target.upper()} isn't a Year {sender_year} unit, so you can't post to it."
    return ""