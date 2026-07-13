"""
UniBot — Session management.
Uses Redis when available (production), in-memory dict for local dev.
Also handles global commands: HELP, MENU, CANCEL, STOP, SUPPORT.
"""

import os
import json
import time
import logging

logger = logging.getLogger("unibot.session")

GLOBAL_COMMANDS = ["HELP", "SUPPORT", "MENU", "CANCEL", "STOP"]

REDIS_URL = os.getenv("REDIS_URL", "")
_redis = None
_memory_store = {}  # fallback: {phone: (data_dict, expiry_timestamp)}

if REDIS_URL:
    try:
        import redis
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
        _redis.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.warning(f"Redis unavailable, using in-memory sessions: {e}")
        _redis = None


DEFAULT_EXPIRY = 600  # 10 minutes


def set_session(phone: str, data: dict, expire_seconds: int = DEFAULT_EXPIRY):
    key = f"session:{phone}"
    if _redis:
        _redis.setex(key, expire_seconds, json.dumps(data))
    else:
        _memory_store[key] = (data, time.time() + expire_seconds)


def get_session(phone: str):
    key = f"session:{phone}"
    if _redis:
        raw = _redis.get(key)
        return json.loads(raw) if raw else None
    entry = _memory_store.get(key)
    if not entry:
        return None
    data, expiry = entry
    if time.time() > expiry:
        del _memory_store[key]
        return None
    return data


def clear_session(phone: str):
    key = f"session:{phone}"
    if _redis:
        _redis.delete(key)
    else:
        _memory_store.pop(key, None)


# ─────────────────────────────────────────────────────────────
def handle_global_command(phone: str, command: str) -> str:
    """These commands work at ANY point in ANY flow."""
    from database import get_db, sha256, _ph

    if command in ("HELP", "SUPPORT"):
        return ("👋 UniBot Help\n\n"
                "1. Text JOIN to register\n"
                "2. Ask questions like:\n"
                "   \"When is my HCI class?\"\n"
                "   \"DSA deadline?\"\n"
                "3. Text MENU to see options\n"
                "4. Text CANCEL to stop current action\n\n"
                "Still stuck? Describe your issue in one "
                "message and we will log it for support.")

    if command == "MENU":
        return ("📋 UniBot Menu\n\n"
                "• Ask about your timetable\n"
                "• Ask about deadlines\n"
                "• COMMUNITY — get your class peer group link\n"
                "• CHANGE YEAR — correct your year group\n"
                "• HELP — get support\n"
                "• STOP — unsubscribe")

    if command == "CANCEL":
        clear_session(phone)
        return "✅ Current action cancelled. Text MENU to see options."

    if command == "STOP":
        # Deactivate the student and clear session
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(_ph("UPDATE students SET status = 'OPTED_OUT' WHERE phone_hash = %s"),
                        (sha256(phone),))
        clear_session(phone)
        return ("You have been unsubscribed from UniBot. "
                "Your data will be removed. "
                "Text JOIN anytime to re-register.")

    return "Unknown command. Text MENU for options."
