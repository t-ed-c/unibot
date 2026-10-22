"""
UniBot — pause periods for the engagement model.

Strikes, closures and holidays make a whole class go quiet at once.
That says nothing about any one student, so those days are left out of
engagement scoring (see ml/risk.py). End dates are often unknown, so a
pause stays open until an admin ends it.

Admin WhatsApp commands:
  PAUSE ML [YYYY-MM-DD] <reason>   start a pause (today, or from a past date)
  RESUME ML                        end the open pause today
  ML STATUS                        list pauses
"""

import re
from datetime import datetime, timezone

from database import get_db, _ph


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)   # stored as UTC


def _f(r, name, idx):
    return r[name] if hasattr(r, "keys") else r[idx]


def _to_dt(v):
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def get_pauses():
    """All pauses as [(start, end_or_None, reason)], oldest first."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT started_at, ended_at, reason FROM ml_pauses ORDER BY started_at")
        rows = cur.fetchall()
    return [(_to_dt(_f(r, "started_at", 0)), _to_dt(_f(r, "ended_at", 1)),
             _f(r, "reason", 2) or "") for r in rows]


def open_pause():
    return next(((s, e, r) for s, e, r in get_pauses() if e is None), None)


def handle_pause_command(message: str, admin_name: str) -> str:
    m = message.strip()
    up = m.upper()

    if up.startswith("ML STATUS"):
        pauses = get_pauses()
        if not pauses:
            return "📊 Engagement model: running. No pauses recorded."
        lines = ["📊 Engagement model pauses:"]
        for s, e, r in pauses[-6:]:
            end = e.strftime("%d %b %Y") if e else "still paused"
            lines.append(f"\n• {s.strftime('%d %b %Y')} → {end}\n  {r}")
        return "\n".join(lines)

    if up.startswith("RESUME ML"):
        current = open_pause()
        if not current:
            return "▶️ The engagement model is not paused."
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(_ph("UPDATE ml_pauses SET ended_at = %s WHERE ended_at IS NULL"), (_now(),))
        days = (_now() - current[0]).days
        return (f"▶️ Engagement model resumed.\n"
                f"The {days}-day pause ({current[2]}) is left out of all scoring.")

    # PAUSE ML [YYYY-MM-DD] <reason>
    if open_pause():
        s, _, r = open_pause()
        return (f"⏸️ Already paused since {s.strftime('%d %b %Y')} ({r}).\n"
                f"Send RESUME ML when classes are back.")
    rest = m[len("PAUSE ML"):].strip()
    start = _now()
    dm = re.match(r"^(\d{4}-\d{2}-\d{2})\s*(.*)$", rest)
    if dm:
        try:
            start = datetime.strptime(dm.group(1), "%Y-%m-%d")
        except ValueError:
            return "⚠️ Date must look like 2026-10-13."
        if start > _now():
            return "⚠️ The start date cannot be in the future."
        rest = dm.group(2).strip()
    reason = rest or "No reason given"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""INSERT INTO ml_pauses (started_at, reason, started_by)
                       VALUES (%s, %s, %s)"""), (start, reason[:120], admin_name))
    return (f"⏸️ Engagement model paused from {start.strftime('%d %b %Y')} — {reason}.\n"
            f"Paused days won't count against any student.\n"
            f"Send RESUME ML when classes are back.")