"""
Register a lecturer, class rep, or admin as a broadcast sender.
Usage:
    python add_sender.py "+254791741148" "Potason" "ADMIN"
    python add_sender.py "+254712345678" "Mr. Kaburu" "LECTURER" "CCS4102"
"""

import sys
from database import init_db, get_db, sha256, _ph
import json


def add_sender(phone, name, role, units=None, year_group=None):
    init_db()
    with get_db() as conn:
        cur = conn.cursor()
        # Remove if already exists (allows re-running)
        cur.execute(_ph("DELETE FROM senders WHERE phone_hash = %s"), (sha256(phone),))
        cur.execute(_ph("""INSERT INTO senders (phone_hash, name, role, units, year_group)
                       VALUES (%s, %s, %s, %s, %s)"""),
                    (sha256(phone), name, role, json.dumps(units or []), year_group))
    print(f"Sender registered:")
    print(f"  Name:  {name}")
    print(f"  Role:  {role}")
    print(f"  Phone: {phone[:6]}****")
    print(f"  Units: {units or 'all'}")


def list_senders():
    init_db()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name, role, units FROM senders")
        rows = cur.fetchall()
    if not rows:
        print("No senders registered yet.")
        return
    print("Registered senders:")
    for r in rows:
        name = r["name"] if hasattr(r, "keys") else r[0]
        role = r["role"] if hasattr(r, "keys") else r[1]
        print(f"  {name} ({role})")


if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1] == "list":
        list_senders()
    elif len(sys.argv) >= 4:
        phone = sys.argv[1]
        name = sys.argv[2]
        role = sys.argv[3].upper()
        units = sys.argv[4:] if len(sys.argv) > 4 else None
        add_sender(phone, name, role, units)
    else:
        print('Usage: python add_sender.py "+254XXXXXXXXX" "Name" "ADMIN"')
        print('       python add_sender.py list')