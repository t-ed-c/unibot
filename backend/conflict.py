"""
UniBot — Scheduling conflict detection.
Three conflict types checked when a timetable entry or event is added:
  1. VENUE     — same room, same day+time, different classes
  2. LECTURER  — same lecturer, same day+time, two events
  3. STUDENT   — same student group, same day+time, two events
"""

import re
import logging
from database import get_db, _ph

logger = logging.getLogger("unibot.conflicts")

ONLINE_VENUES = ("TBD", "TBA", "ONLINE", "")


def check_all_conflicts(unit_code, day, time_slot, venue, lecturer, year_group):
    """Check a proposed entry against everything already scheduled."""
    conflicts = []
    conflicts += check_venue_conflict(venue, day, time_slot, year_group, unit_code)
    conflicts += check_lecturer_conflict(lecturer, day, time_slot, unit_code)
    conflicts += check_student_conflict(year_group, day, time_slot, unit_code)
    return conflicts


def check_venue_conflict(venue, day, time_slot, year_group, unit_code):
    """Same venue booked by a different class at the same day+time."""
    if not venue or venue.upper() in ONLINE_VENUES:
        return []

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""
            SELECT unit_code, year_group, lecturer FROM timetable
            WHERE venue = %s AND day = %s AND time_slot = %s
              AND NOT (unit_code = %s AND year_group = %s)
        """), (venue, day, time_slot, unit_code, year_group))
        rows = cur.fetchall()

    out = []
    for r in rows:
        rc = r["unit_code"] if hasattr(r, "keys") else r[0]
        ry = r["year_group"] if hasattr(r, "keys") else r[1]
        out.append({
            "type": "VENUE",
            "detail": f"Venue {venue} already booked at {day} {time_slot} "
                      f"by {rc} (Year {ry})",
            "existing_unit": rc, "existing_year": ry,
        })
    return out


def check_lecturer_conflict(lecturer, day, time_slot, unit_code):
    """Same lecturer scheduled for two different classes at once."""
    if not lecturer or lecturer.strip() == "":
        return []

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""
            SELECT unit_code, year_group, venue FROM timetable
            WHERE lecturer = %s AND day = %s AND time_slot = %s
              AND unit_code != %s
        """), (lecturer, day, time_slot, unit_code))
        rows = cur.fetchall()

    out = []
    for r in rows:
        rc = r["unit_code"] if hasattr(r, "keys") else r[0]
        ry = r["year_group"] if hasattr(r, "keys") else r[1]
        out.append({
            "type": "LECTURER",
            "detail": f"{lecturer} is already teaching {rc} (Year {ry}) "
                      f"at {day} {time_slot} — cannot be in two places",
            "existing_unit": rc, "existing_year": ry,
        })
    return out


def check_student_conflict(year_group, day, time_slot, unit_code):
    """Same year group already has a class at this day+time."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""
            SELECT unit_code, venue FROM timetable
            WHERE year_group = %s AND day = %s AND time_slot = %s
              AND unit_code != %s
        """), (year_group, day, time_slot, unit_code))
        rows = cur.fetchall()

    out = []
    for r in rows:
        rc = r["unit_code"] if hasattr(r, "keys") else r[0]
        out.append({
            "type": "STUDENT",
            "detail": f"Year {year_group} already has {rc} "
                      f"at {day} {time_slot} — students cannot attend both",
            "existing_unit": rc,
        })
    return out


def scan_existing_timetable():
    """Scan the ENTIRE loaded timetable for conflicts that already exist."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT unit_code, day, time_slot, venue, lecturer, year_group, group_label FROM timetable")
        rows = cur.fetchall()

    all_entries = []
    for r in rows:
        if hasattr(r, "keys"):
            all_entries.append(dict(r))
        else:
            all_entries.append({
                "unit_code": r[0], "day": r[1], "time_slot": r[2],
                "venue": r[3], "lecturer": r[4], "year_group": r[5],
                "group_label": r[6]
            })

    return scan_entries(all_entries)


def same_person(a: str, b: str) -> bool:
    """'Mr. Kaburu' and 'Kaburu' are the same lecturer."""
    def key(name):
        return re.sub(r"^(MR|MRS|MS|DR|PROF)\.?\s*", "", (name or "").strip().upper())
    return bool(key(a)) and key(a) == key(b)


def group_of(entry) -> str:
    """'1.1 (CS)' and '1.1 (FS)' are different student groups in the same year."""
    return entry.get("group_label") or str(entry["year_group"])


def scan_entries(all_entries):
    """
    Find clashes in a list of sessions without touching the database.
    Used to check an uploaded timetable BEFORE it goes live, and by
    scan_existing_timetable() for the live one.
    """
    found = []
    seen = set()

    for i, a in enumerate(all_entries):
        for b in all_entries[i+1:]:
            if a["day"] != b["day"] or a["time_slot"] != b["time_slot"]:
                continue

            # Same room, different units (the same unit in one room is a shared class)
            if (a["venue"] and b["venue"]
                    and a["venue"].upper() not in ONLINE_VENUES
                    and a["venue"].upper() == b["venue"].upper()
                    and a["unit_code"] != b["unit_code"]):
                key = ("VENUE", a["day"], a["time_slot"], a["venue"])
                if key not in seen:
                    seen.add(key)
                    found.append({
                        "type": "VENUE",
                        "detail": f"{a['venue']} on {a['day']} {a['time_slot']}: "
                                  f"{a['unit_code']} (Y{group_of(a)}) vs "
                                  f"{b['unit_code']} (Y{group_of(b)})"
                    })

            # Same lecturer in two physical classes at once
            if (same_person(a["lecturer"], b["lecturer"])
                    and a["unit_code"] != b["unit_code"]
                    and "ONLINE" not in ((a["venue"] or "").upper(), (b["venue"] or "").upper())):
                key = ("LECTURER", a["day"], a["time_slot"], a["lecturer"])
                if key not in seen:
                    seen.add(key)
                    found.append({
                        "type": "LECTURER",
                        "detail": f"{a['lecturer']} on {a['day']} {a['time_slot']}: "
                                  f"{a['unit_code']} (Y{group_of(a)}) and "
                                  f"{b['unit_code']} (Y{group_of(b)})"
                    })

            # The same group of students booked into two classes at once
            if (group_of(a) == group_of(b)
                    and a["unit_code"] != b["unit_code"]):
                key = ("STUDENT", a["day"], a["time_slot"], group_of(a))
                if key not in seen:
                    seen.add(key)
                    found.append({
                        "type": "STUDENT",
                        "detail": f"Year {group_of(a)} on {a['day']} {a['time_slot']}: "
                                  f"{a['unit_code']} and {b['unit_code']} at the same time"
                    })

    return found