"""
UniBot — Timetable Excel parser.
Built for the DKUT CS timetable format:
  - Title row:  "TEACHING TIMETABLE FOR MAY-AUGUST 2026 SEMESTER"
  - Time slots in a header row (7AM-8AM ... 5PM-6PM)
  - Days in column 0 (MONDAY ... FRIDAY)
  - Groups in column 1 (e.g. "YEAR 4.2", "YEAR 1.1 (CS)", "YEAR 1.1 (FS)")
  - Cells contain "CCS 4101 RCL 1" (unit code + venue)
  - Grid ends at the "TIMETABLE CO-ORDINATOR" signature row
  - Below the grid: a unit list (code | name | ... | lecturer),
    sometimes two programmes side by side on one row

Unit names and lecturers are read from that unit list in the file
itself, so a new semester's timetable works without code changes.

Validated against: CS_MAY-AUG_2026 DRAFT 4 and CS_SEPT-DEC_2026 DRAFT 3
"""

import re
import logging
import pandas as pd

from database import get_db, _ph

logger = logging.getLogger("unibot.timetable")

DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"]

UNIT_CODE_RE = re.compile(r"\b([A-Z]{3})\s*(\d{4})\b")
TIME_RE = re.compile(r"\d{1,2}\s*(AM|PM)", re.I)
YEAR_LABEL_RE = re.compile(r"\b(?:YEAR|CS|YR)\s*(\d)", re.I)
GRID_END_RE = re.compile(r"CO-?ORDINATOR|^COD\b", re.I)
SEMESTER_RE = re.compile(r"TIMETABLE\s+FOR\s+(.+?)\s+SEMESTER", re.I)

# Words in the unit list that are not lecturer names
NOT_A_LECTURER = {"ALL STAFF", "ONLINE", "DONE", "TBA", "TBD",
                  # Departments that teach service units, not people
                  "IGS", "MPS", "SB", "NURSING", "SAS", "SMA", "SPH", "BEC", "HNS"}


# ─────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────
def _clean(value) -> str:
    """Cell value as a single-spaced string ('' for empty cells)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalise_unit_code(text: str):
    """'CCS  3104' or 'CCS3104' -> 'CCS 3104'. None if no code found."""
    m = UNIT_CODE_RE.search(text.upper())
    return f"{m.group(1)} {m.group(2)}" if m else None


def normalise_venue(text: str) -> str:
    """
    Make the same room always look the same, so conflict detection
    can compare rooms: 'RCL1' -> 'RCL 1', '(RC18)' -> 'RC 18',
    '(FS+MATHS) - RC 18' -> 'RC 18', '- VENUE' -> 'TBD'.
    """
    v = re.sub(r"\([^)]*\)", " ", _clean(text))     # drop notes like (FS+MATHS)
    v = _clean(v).strip("()-–: ").upper()
    v = re.sub(r"^([A-Z]+)\s*(\d+)$", r"\1 \2", v)
    if v in ("", "VENUE", "TBA"):
        return "TBD"
    return v


def normalise_lecturer(text: str) -> str:
    """'MR  OPONDO' -> 'Mr. Opondo', 'DR.KAMAU' -> 'Dr. Kamau'."""
    t = _clean(text).upper()
    if not t or t in NOT_A_LECTURER:
        return ""
    m = re.match(r"^(MR|MRS|MS|DR|PROF)\.?\s*(.+)$", t)
    if m:
        title, name = m.group(1).title(), m.group(2)
        return f"{title}. {name.title()}"
    return t.title()


def parse_year_group(label: str):
    """Extract year number from 'YEAR 4.1', 'CS 3.1', 'YR 2'. None otherwise."""
    m = YEAR_LABEL_RE.search(str(label))
    return int(m.group(1)) if m else None


def parse_group_label(label: str) -> str:
    """'YEAR 1.1 (CS)' -> '1.1 (CS)', 'YEAR 4.2' -> '4.2'. Keeps programmes apart."""
    t = _clean(label).upper()
    t = re.sub(r"^(YEAR|YR|CS)\s*", "", t)
    return t


def parse_cell(cell_text: str):
    """
    Parse a timetable cell like 'CCS 4101 RCL 1' or 'CCS 4101(RCL1)'
    into (unit_code, venue). Returns (None, None) if no unit code.
    """
    text = _clean(cell_text)
    m = UNIT_CODE_RE.search(text.upper())
    if not m:
        return None, None
    unit_code = f"{m.group(1)} {m.group(2)}"
    venue = normalise_venue(text[m.end():])
    return unit_code, venue


# ─────────────────────────────────────────────────────────────
# Reading the whole file
# ─────────────────────────────────────────────────────────────
def find_semester_label(df) -> str:
    """Read 'MAY-AUGUST 2026' from the title row, or '' if missing."""
    for r in range(min(15, len(df))):
        for v in df.iloc[r].tolist():
            m = SEMESTER_RE.search(_clean(v))
            if m:
                return m.group(1).strip().upper()
    return ""


def parse_unit_list(df, start_row: int) -> dict:
    """
    Read the unit list below the grid. A row may hold one unit:
      '1.' | 'CCS 2103' | 'Operating Systems' | 2 | 3 | 'NAIVASHA'
    or two programmes side by side:
      '1.' | 'CCS 1101' | 'Intro to CS' | 3 | 'Mr. Gichohi' | 140 | 'CCS 1102' | 'Intro to Cyber Security' | 3 | 'Dr. Naivasha'
    Each unit code starts a new unit. Returns {code: {"unit_name", "lecturer"}}.
    """
    units = {}
    for r in range(start_row, len(df)):
        cells = [_clean(v) for v in df.iloc[r].tolist()]
        cells = [c for c in cells if c]

        # Split the row into segments, each starting at a cell that is a unit code
        segments, current = [], None
        for c in cells:
            code = normalise_unit_code(c)
            if code and re.fullmatch(r"[A-Z]{3}\s*\d{4}", c.upper()):
                current = [code]
                segments.append(current)
            elif current is not None:
                current.append(c)

        for seg in segments:
            code, rest = seg[0], seg[1:]
            name = next((c for c in rest if re.search(r"[A-Za-z]{3}", c)
                         and not c.upper().startswith("MOVED")), "")
            lecturer = ""
            for c in reversed(rest):
                cu = c.upper()
                if c == name or not re.search(r"[A-Za-z]", c):
                    continue
                if cu.startswith("MOVED") or cu == "DONE" or re.fullmatch(r"\d+\s*HRS?", cu):
                    continue
                lecturer = normalise_lecturer(c)
                break
            units.setdefault(code, {"unit_name": name.title() if name.isupper() else name,
                                    "lecturer": lecturer})
    return units


def parse_timetable_excel(file_path: str, sheet_name=None):
    """
    Parse a DKUT timetable Excel file.
    Returns a dict:
      sessions  — list of class sessions
      units     — {code: {unit_name, lecturer}} from the unit list
      semester  — e.g. 'MAY-AUGUST 2026' ('' if not found)
      sheet     — sheet that was read
      errors    — cells inside the grid that could not be read
    """
    xl = pd.ExcelFile(file_path)
    sheet = sheet_name or xl.sheet_names[0]
    df = pd.read_excel(xl, sheet_name=sheet, header=None)

    result = {"sessions": [], "units": {}, "semester": find_semester_label(df),
              "sheet": sheet, "errors": []}

    # 1. Find the header row containing time slots
    time_row_idx, time_slots = None, {}
    for r in range(min(15, len(df))):
        row = df.iloc[r].tolist()
        if sum(1 for v in row if TIME_RE.search(_clean(v))) >= 4:
            time_row_idx = r
            time_slots = {c: _clean(v).upper().replace(" ", "")
                          for c, v in enumerate(row) if TIME_RE.search(_clean(v))}
            break

    if time_row_idx is None:
        result["errors"].append("Could not find the row of time slots (e.g. 7AM-8AM).")
        return result

    # 2. Walk the grid until the signature row
    current_day, grid_end = None, len(df)
    for r in range(time_row_idx + 1, len(df)):
        row = df.iloc[r]
        row_text = " ".join(_clean(v) for v in row.tolist())
        if GRID_END_RE.search(row_text):
            grid_end = r
            break

        c0 = _clean(row[0]).upper()
        if c0 in DAYS:
            current_day = c0
        if not current_day:
            continue

        year_group = parse_year_group(row[1]) if len(row) > 1 else None
        if year_group is None:
            continue

        for col, slot in time_slots.items():
            if col >= len(row):
                continue
            cell = _clean(row[col])
            if not cell:
                continue
            unit_code, venue = parse_cell(cell)
            if unit_code is None:
                result["errors"].append(f"{current_day.title()} {slot}, Year {year_group}: '{cell}'")
                continue
            result["sessions"].append({
                "unit_code": unit_code,
                "day": current_day,
                "time_slot": slot,
                "venue": venue,
                "year_group": year_group,
                "group_label": parse_group_label(row[1]),
            })

    # 3. Unit names and lecturers from the list below the grid
    result["units"] = parse_unit_list(df, grid_end)
    for s in result["sessions"]:
        info = result["units"].get(s["unit_code"], {})
        s["unit_name"] = info.get("unit_name", "")
        s["lecturer"] = info.get("lecturer", "")

    logger.info(f"Parsed {len(result['sessions'])} sessions, {len(result['units'])} units, "
                f"{len(result['errors'])} unreadable cells from '{sheet}' "
                f"({result['semester'] or 'semester not found'})")
    return result


# ─────────────────────────────────────────────────────────────
# Saving to the database
# ─────────────────────────────────────────────────────────────
def save_timetable(parsed: dict, semester: str = "", filename: str = "",
                   uploaded_by: str = "", conflict_count: int = 0):
    """
    Replace the live timetable and unit list with a parsed file,
    and record the upload in timetable_uploads.
    Refuses to replace anything if the file produced no sessions,
    so a wrong file can never wipe the working timetable.
    """
    sessions = parsed["sessions"]
    if not sessions:
        raise ValueError("No classes were found in this file, so the current "
                         "timetable was kept. Check that it is the department "
                         "timetable in the usual layout.")

    semester = (semester or parsed.get("semester") or "UNNAMED SEMESTER").strip().upper()

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM timetable")
        for s in sessions:
            cur.execute(_ph("""INSERT INTO timetable
                (unit_code, unit_name, day, time_slot, venue, lecturer, year_group, group_label)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""),
                (s["unit_code"], s["unit_name"], s["day"], s["time_slot"],
                 s["venue"], s["lecturer"], s["year_group"], s.get("group_label", "")))

        cur.execute("DELETE FROM units")
        for code, info in parsed["units"].items():
            cur.execute(_ph("INSERT INTO units (unit_code, unit_name, lecturer) VALUES (%s, %s, %s)"),
                        (code, info["unit_name"], info["lecturer"]))

        cur.execute(_ph("""INSERT INTO timetable_uploads
            (semester, filename, sessions, units, conflicts, uploaded_by)
            VALUES (%s, %s, %s, %s, %s, %s)"""),
            (semester, filename[:200], len(sessions), len(parsed["units"]),
             conflict_count, uploaded_by[:100]))

    logger.info(f"Timetable replaced: {semester}, {len(sessions)} sessions")
    return {"semester": semester, "loaded": len(sessions), "units": len(parsed["units"])}


def load_timetable_into_db(file_path: str, sheet_name=None, semester: str = "",
                           uploaded_by: str = "command line"):
    """Parse a file, check it for clashes, and make it the live timetable."""
    from conflict import scan_entries
    parsed = parse_timetable_excel(file_path, sheet_name)
    conflicts = scan_entries(parsed["sessions"])
    saved = save_timetable(parsed, semester=semester,
                           filename=file_path.replace("\\", "/").split("/")[-1],
                           uploaded_by=uploaded_by, conflict_count=len(conflicts))
    return {**saved, "errors": parsed["errors"], "conflicts": conflicts}


def get_current_timetable_info():
    """Latest upload record, or None if no timetable has been uploaded."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT semester, filename, sessions, units, conflicts,
                              uploaded_by, uploaded_at
                       FROM timetable_uploads ORDER BY id DESC LIMIT 1""")
        row = cur.fetchone()
    if not row:
        return None
    keys = ["semester", "filename", "sessions", "units", "conflicts", "uploaded_by", "uploaded_at"]
    return {k: (row[k] if hasattr(row, "keys") else row[i]) for i, k in enumerate(keys)}


# ─────────────────────────────────────────────────────────────
# Queries used by the bot
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
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph(q), tuple(params))
        rows = cur.fetchall()

    # Sort in Python: as text, "2PM" would come before "8AM"
    def order(r):
        day_ = r["day"] if hasattr(r, "keys") else r[3]
        slot = r["time_slot"] if hasattr(r, "keys") else r[4]
        return (DAYS.index(day_) if day_ in DAYS else 9, slot_start_hour(slot))
    return sorted(rows, key=order)


def slot_start_hour(slot: str) -> int:
    """'2PM-3PM' -> 14, '11AM-12PM' -> 11, '12PM-1PM' -> 12."""
    m = re.match(r"\s*(\d{1,2})\s*(AM|PM)", str(slot), re.I)
    if not m:
        return 99
    hour, half = int(m.group(1)) % 12, m.group(2).upper()
    return hour + 12 if half == "PM" else hour


def get_units_for_year(year_group: int):
    """Units on the live timetable for one year: [(code, name), ...]."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""SELECT DISTINCT unit_code, unit_name FROM timetable
                           WHERE year_group = %s"""), (year_group,))
        rows = cur.fetchall()
    return [((r["unit_code"], r["unit_name"]) if hasattr(r, "keys") else (r[0], r[1]))
            for r in rows]