"""
UniBot — Timetable Excel parser.
Built for the real DKUT CS timetable format:
  - Time slots in a header row (7AM-8AM ... 5PM-6PM)
  - Days in column 0 (MONDAY ... FRIDAY)
  - Year groups in column 1 (e.g. "CS 4.1")
  - Cells contain "CCS 4101 RCL 1" (unit code + venue)

Validated against: CS_MAY-AUG_2026_TEACHING_TIMETABLE_DRAFT_4
"""

import re
import json
import logging
import pandas as pd

from database import get_db, _ph

logger = logging.getLogger("unibot.timetable")

DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"]

# Known unit names — extend as more units are added
UNIT_NAMES = {
    "CCS 4101": "Human Computer Interaction",
    "CCS 4102": "Machine Learning",
    "CCS 4103": "Computer Security",
    "CCS 4199": "Computer Science Project",
}

UNIT_LECTURERS = {
    "CCS 4101": "Mr. Opondo",
    "CCS 4102": "Mr. Kaburu",
    "CCS 4103": "Mr. Naivasha",
    "CCS 4199": "Mr. Naivasha",
}


def parse_year_group(label: str):
    """Extract year number from labels like 'CS 4.1', 'CS 3.1', 'YEAR 2'."""
    m = re.search(r"(\d)", str(label))
    return int(m.group(1)) if m else None


def parse_cell(cell_text: str):
    """
    Parse a timetable cell like 'CCS 4101 RCL 1' into (unit_code, venue).
    Returns (None, None) if the cell doesn't contain a recognisable unit.
    """
    text = str(cell_text).strip()
    m = re.match(r"([A-Z]{2,4})\s*(\d{4})\s*(.*)", text)
    if not m:
        return None, None
    unit_code = f"{m.group(1)} {m.group(2)}"
    venue = m.group(3).strip() or "TBD"
    return unit_code, venue


def parse_timetable_excel(file_path: str, sheet_name=None):
    """
    Parse the DKUT timetable Excel into a list of session dicts.
    Returns (sessions, errors).
    """
    xl = pd.ExcelFile(file_path)
    sheet = sheet_name or xl.sheet_names[0]
    df = pd.read_excel(file_path, sheet_name=sheet, header=None)

    sessions = []
    errors = []

    # 1. Find the header row containing time slots
    time_row_idx = None
    time_slots = {}
    for r in range(min(15, len(df))):
        row_vals = [str(v) for v in df.iloc[r].tolist()]
        matches = [v for v in row_vals if re.search(r"\d{1,2}\s*(AM|PM)", str(v), re.I)]
        if len(matches) >= 4:
            time_row_idx = r
            for c, v in enumerate(df.iloc[r].tolist()):
                if pd.notna(v) and re.search(r"\d{1,2}\s*(AM|PM)", str(v), re.I):
                    time_slots[c] = str(v).strip()
            break

    if time_row_idx is None:
        return [], ["Could not find the time-slot header row"]

    # 2. Walk the remaining rows
    current_day = None
    for r in range(time_row_idx + 1, len(df)):
        row = df.iloc[r]

        # Day label in column 0?
        c0 = str(row[0]).strip().upper() if pd.notna(row[0]) else ""
        if c0 in DAYS:
            current_day = c0

        if not current_day:
            continue

        # Year group in column 1
        year_group = parse_year_group(row[1]) if pd.notna(row[1]) else None
        if year_group is None:
            continue

        # Each time-slot column
        for col, slot in time_slots.items():
            if col >= len(row):
                continue
            cell = row[col]
            if pd.isna(cell) or not str(cell).strip():
                continue
            unit_code, venue = parse_cell(cell)
            if unit_code is None:
                errors.append(f"Unparseable cell at row {r}, {slot}: '{cell}'")
                continue
            sessions.append({
                "unit_code": unit_code,
                "unit_name": UNIT_NAMES.get(unit_code, ""),
                "day": current_day,
                "time_slot": slot,
                "venue": venue,
                "lecturer": UNIT_LECTURERS.get(unit_code, ""),
                "year_group": year_group,
            })

    logger.info(f"Parsed {len(sessions)} sessions, {len(errors)} errors from {sheet}")
    return sessions, errors


def load_timetable_into_db(file_path: str, sheet_name=None, replace=True):
    """Parse and store into the timetable table. Returns summary dict."""
    sessions, errors = parse_timetable_excel(file_path, sheet_name)

    with get_db() as conn:
        cur = conn.cursor()
        if replace:
            cur.execute("DELETE FROM timetable")
        for s in sessions:
            cur.execute(_ph("""INSERT INTO timetable
                (unit_code, unit_name, day, time_slot, venue, lecturer, year_group)
                VALUES (%s, %s, %s, %s, %s, %s, %s)"""),
                (s["unit_code"], s["unit_name"], s["day"], s["time_slot"],
                 s["venue"], s["lecturer"], s["year_group"]))

    return {"loaded": len(sessions), "errors": errors}


# ─────────────────────────────────────────────────────────────
def get_timetable_for(year_group: int, day: str = None, unit_code: str = None):
    """Query timetable for a student's year, optionally filtered."""
    q = "SELECT * FROM timetable WHERE year_group = %s"
    params = [year_group]
    if day:
        q += " AND day = %s"
        params.append(day.upper())
    if unit_code:
        q += " AND unit_code LIKE %s"
        params.append(f"%{unit_code}%")
    q += " ORDER BY CASE day WHEN 'MONDAY' THEN 1 WHEN 'TUESDAY' THEN 2 WHEN 'WEDNESDAY' THEN 3 WHEN 'THURSDAY' THEN 4 WHEN 'FRIDAY' THEN 5 ELSE 6 END, time_slot"

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph(q), tuple(params))
        return cur.fetchall()
