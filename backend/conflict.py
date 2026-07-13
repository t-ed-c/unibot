"""
UniBot — Scheduling conflict detection.
Three conflict types checked when a timetable entry or event is added:
  1. VENUE     — same room, same day+time, different classes
  2. LECTURER  — same lecturer, same day+time, two events
  3. STUDENT   — same year group, same day+time, two events
"""

import logging
from database import get_db, _ph

logger = logging.getLogger("unibot.conflicts")


def check_all_conflicts(unit_code, day, time_slot, venue, lecturer, year_group):
    """Check a proposed entry against everything already scheduled."""
    conflicts = []
    conflicts += check_venue_conflict(venue, day, time_slot, year_group, unit_code)
    conflicts += check_lecturer_conflict(lecturer, day, time_slot, unit_code)
    conflicts += check_student_conflict(year_group, day, time_slot, unit_code)
    return conflicts


def check_venue_conflict(venue, day, time_slot, year_group, unit_code):
    """Same venue booked by a different class at the same day+time."""
    if not venue or venue.upper() in ("TBD", "TBA", ""):
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
        cur.execute("SELECT unit_code, day, time_slot, venue, lecturer, year_group FROM timetable")
        rows = cur.fetchall()

    all_entries = []
    for r in rows:
        if hasattr(r, "keys"):
            all_entries.append(dict(r))
        else:
            all_entries.append({
                "unit_code": r[0], "day": r[1], "time_slot": r[2],
                "venue": r[3], "lecturer": r[4], "year_group": r[5]
            })

    found = []
    seen = set()

    for i, a in enumerate(all_entries):
        for b in all_entries[i+1:]:
            if a["day"] != b["day"] or a["time_slot"] != b["time_slot"]:
                continue

            if (a["venue"] and b["venue"]
                    and a["venue"].upper() not in ("TBD", "TBA", "")
                    and a["venue"] == b["venue"]
                    and (a["unit_code"], a["year_group"]) != (b["unit_code"], b["year_group"])):
                key = ("VENUE", a["day"], a["time_slot"], a["venue"])
                if key not in seen:
                    seen.add(key)
                    found.append({
                        "type": "VENUE",
                        "detail": f"{a['venue']} on {a['day']} {a['time_slot']}: "
                                  f"{a['unit_code']} (Y{a['year_group']}) vs "
                                  f"{b['unit_code']} (Y{b['year_group']})"
                    })

            if (a["lecturer"] and b["lecturer"]
                    and a["lecturer"] == b["lecturer"]
                    and a["unit_code"] != b["unit_code"]):
                key = ("LECTURER", a["day"], a["time_slot"], a["lecturer"])
                if key not in seen:
                    seen.add(key)
                    found.append({
                        "type": "LECTURER",
                        "detail": f"{a['lecturer']} on {a['day']} {a['time_slot']}: "
                                  f"{a['unit_code']} (Y{a['year_group']}) and "
                                  f"{b['unit_code']} (Y{b['year_group']})"
                    })

    return found