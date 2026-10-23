"""
UniBot — Student query handling.
Routes registered students' messages to the right answer.
Starts with keyword routing; ML classifier (Model 1) plugs in
here once trained — same interface, swap the router.
"""

import re
import json
import logging
from datetime import datetime, timezone, timedelta

from database import get_db, sha256, log_engagement, _ph
from timetable import get_timetable_for, get_units_for_year
from auth import get_student

EAT = timezone(timedelta(hours=3))  # Kenya time — the server runs on UTC

# Words too common in unit names to identify one unit on their own
COMMON_WORDS = {"COMPUTER", "SCIENCE", "SYSTEMS", "SYSTEM", "AND", "OF", "THE", "TO",
                "IN", "FOR", "I", "II", "III", "DESIGN", "ANALYSIS", "COMMUNICATION",
                "SKILLS", "INTRODUCTION", "PRINCIPLES", "UNDERSTANDING", "CLASS"}
# Initials that would match ordinary words in a message
BAD_INITIALS = {"CS", "IT", "AT", "IN", "IS", "TO", "OF", "AS"}

logger = logging.getLogger("unibot.queries")

DAY_NAMES = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def handle_student_query(phone: str, message: str) -> str:
    """Main entry — identify the student, classify intent, answer."""
    student = get_student(phone)
    if not student:
        return "Please text JOIN to register first."

    # sqlite Row vs psycopg2 RealDict compatibility
    year = student["year_group"] if hasattr(student, "keys") else student[4]
    id_hash = student["student_id_hash"] if hasattr(student, "keys") else student[0]

    intent = classify_intent(message)
    # Just a unit name ("embedded systems", "HCI") means "when is that class?"
    if intent == "UNKNOWN" and find_unit_in_message(message.upper(), year):
        intent = "TIMETABLE_QUERY"
    log_engagement(id_hash, "QUERY", f"{intent}: {message[:100]}")

    if intent == "TIMETABLE_QUERY":
        return answer_timetable(message, year)
    if intent == "DEADLINE_QUERY":
        return answer_deadline(message, year)
    if intent == "FEE_QUERY":
        return answer_fee()
    if intent == "GREETING":
        example = example_unit_name(year)
        return ("👋 Hello! Ask me anything about your classes.\n\n"
                f"Try: \"When is {example}?\" or "
                "\"What classes do I have today?\"")
    if intent == "THANKS":
        return "👍 Anytime! Ask me about your classes whenever you need."
    if intent == "COMMUNITY":
        return answer_community(year)
    if intent == "CHANGE_YEAR":
        return ("To change your year group, text HELP and "
                "describe the correction needed — an admin "
                "will process it.")

    # UNKNOWN — low confidence path
    return ("🤔 I'm not sure what you're asking.\n\n"
            "Try:\n"
            "• \"When is [unit] class?\"\n"
            "• \"Classes today\"\n"
            "• \"Deadlines\"\n"
            "• MENU for all options")


# ─────────────────────────────────────────────────────────────
def classify_intent(message: str) -> str:
    """
    v0: keyword-based baseline router.
    v1 (ML): replace body with trained TF-IDF + LogisticRegression —
    this function IS the baseline for the classifier comparison.
    """
    m = message.lower()

    if re.search(r"\b(hi|hello|hey|sasa|mambo|niaje|habari|morning|afternoon)\b", m):
        return "GREETING"
    if re.fullmatch(r"\s*(ok|okay|k|kk|sawa|poa|fiti|cool|nice|thanks|thank you|thanx|asante|asante sana|"
                    r"noted|got it|alright|great|👍|🙏)[\s.!]*", m):
        return "THANKS"
    if "community" in m or "group link" in m or "peer group" in m:
        return "COMMUNITY"
    if "change year" in m:
        return "CHANGE_YEAR"
    if re.search(r"\b(fee|fees|balance|pay|paybill|lipa)\b", m):
        return "FEE_QUERY"
    if re.search(r"\b(deadlines?|due|assignments?|cats?|exams?|submit|submissions?|homework|projects?|tests?|quiz(zes)?|kazi|supps?|mtihani|papers?)\b", m):
        return "DEADLINE_QUERY"
    if re.search(r"\b(class|classes|timetable|lecture|lectures|venue|room|when|where|time|leo|today|tomorrow|kesho|saa|monday|tuesday|wednesday|thursday|friday|saturday)\b", m):
        return "TIMETABLE_QUERY"

    return "UNKNOWN"


# ─────────────────────────────────────────────────────────────
def answer_timetable(message: str, year: int) -> str:
    m = message.upper()

    # Which day(s) are being asked about? (Kenya time, not server time)
    now = datetime.now(EAT)
    days = []
    if "TODAY" in m or "LEO" in m:
        days.append(now.strftime("%A").upper())
    if "TOMORROW" in m or "KESHO" in m:
        days.append(DAY_NAMES[(now.weekday() + 1) % 7])
    for d in DAY_NAMES:
        if d in m and d not in days:
            days.append(d)
    days.sort(key=DAY_NAMES.index)

    # Which unit? (look for unit code fragments or names)
    unit = None
    code_match = re.search(r"\b([A-Z]{3})\s*(\d{4})\b", m)
    if code_match:
        unit = f"{code_match.group(1)} {code_match.group(2)}"
    else:
        unit = find_unit_in_message(m, year)

    if days:
        rows = [r for d in days for r in get_timetable_for(year, day=d, unit_code=unit)]
    else:
        rows = get_timetable_for(year, unit_code=unit)
    day_label = " & ".join(d.title() for d in days)

    if not rows and days and not unit:
        return f"📅 No classes on {day_label} for Year {year}. 🎉"

    if not rows:
        scope = f" for {unit}" if unit else ""
        scope += f" on {day_label}" if days else ""
        return (f"📅 No classes found{scope} for Year {year}.\n"
                "The timetable may not be loaded yet, or "
                "check your spelling. Text MENU for options.")

    def field(r, name, idx):
        return r[name] if hasattr(r, "keys") else r[idx]

    def group(r):
        return (r["group_label"] if hasattr(r, "keys") and "group_label" in r.keys() else "") or ""

    # Year 1 has two programmes (CS and FS) — label each class so students can tell
    several_groups = len({group(r) for r in rows}) > 1

    lines = [f"📅 Year {year} — " + (day_label if days else "Timetable")]
    for r in rows[:8]:
        tag = f"[{group(r)}] " if several_groups and group(r) else ""
        lines.append(
            f"\n{tag}{field(r,'unit_code',1)} {field(r,'unit_name',2) or ''}\n"
            f"  {field(r,'day',3).title()} {field(r,'time_slot',4)} → {field(r,'venue',5)}"
            + (f"\n  {field(r,'lecturer',6)}" if field(r, 'lecturer', 6) else "")
        )
    if len(rows) > 8:
        lines.append(f"\n(+{len(rows)-8} more — ask for a specific day)")
    return "\n".join(lines)


def example_unit_name(year: int) -> str:
    """A real unit from this semester for the greeting, e.g. 'Embedded Systems'."""
    for code, name in get_units_for_year(year):
        if name and code.startswith("CCS"):
            return name
    return "my next class"


def unit_aliases(units):
    """
    Build the names students might use for each unit, from the unit
    names in the timetable file — so a new semester needs no code edits.
    'Human Computer Interaction' -> HUMAN COMPUTER INTERACTION, HCI, INTERACTION
    'Machine Learning'           -> MACHINE LEARNING, ML, LEARNING, MACHINE
    A single word is only used if no other unit of that year shares it.
    """
    word_count = {}
    for _, name in units:
        for w in set(re.findall(r"[A-Z]+", (name or "").upper())):
            word_count[w] = word_count.get(w, 0) + 1

    aliases = []
    for code, name in units:
        name = re.sub(r"\s+", " ", (name or "").upper()).strip(" :")
        if not name:
            continue
        aliases.append((name, code))
        words = re.findall(r"[A-Z]+", name)
        initials = "".join(w[0] for w in words if w not in {"AND", "OF", "THE", "TO", "IN", "FOR"})
        if len(initials) >= 2 and initials not in BAD_INITIALS:
            aliases.append((initials, code))
        for w in words:
            if len(w) >= 4 and w not in COMMON_WORDS and word_count.get(w) == 1:
                aliases.append((w, code))
    # Longest first, so "MACHINE LEARNING" beats "LEARNING"
    return sorted(aliases, key=lambda a: -len(a[0]))


def find_unit_in_message(message_upper: str, year: int):
    """Match a unit name or nickname in the message to a unit code."""
    for alias, code in unit_aliases(get_units_for_year(year)):
        if re.search(rf"\b{re.escape(alias)}\b", message_upper):
            return code
    return None


EXAM_WORDS = re.compile(r"\b(exams?|cats?|tests?|quiz(zes)?|midterms?|finals?|mtihani|supps?|papers?)\b", re.I)


def answer_deadline(message: str, year: int) -> str:
    """
    Deadlines for the units this student's year takes.
    'When is ML exam?'  -> the Machine Learning paper from the exam timetable,
                           plus any ML CATs a lecturer or rep has announced.
    'My exams'          -> this year's upcoming papers in date order.
    'Any assignments?'  -> everything announced for the year.
    """
    m = message.upper()
    units = get_units_for_year(year)
    my_units = {code.replace(" ", ""): name for code, name in units}
    wants_exam = bool(EXAM_WORDS.search(message))

    exam_rows, exam_info = [], None
    if wants_exam:
        from exam_timetable import exams_for_year, get_exam_info
        exam_rows = exams_for_year(year, include_supp=bool(re.search(r"\bSUPP", m)))
        exam_info = get_exam_info()
        # Exam papers can be for units not on this semester's teaching timetable
        for e in exam_rows:
            my_units.setdefault(e["unit_code"].replace(" ", ""), e["unit_name"])

    # Did they ask about one unit? ("CCS 4102", "ML", "machine learning")
    code_match = re.search(r"\b([A-Z]{3})\s*(\d{4})\b", m)
    unit = (f"{code_match.group(1)} {code_match.group(2)}" if code_match
            else find_unit_in_message(m, year))
    if not unit and exam_rows:
        for alias, code in unit_aliases([(e["unit_code"], e["unit_name"]) for e in exam_rows]):
            if re.search(rf"\b{re.escape(alias)}\b", m):
                unit = code
                break

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT unit_code, title, due_date FROM deadlines ORDER BY id DESC")
        rows = cur.fetchall()

    def field(r, name, idx):
        return r[name] if hasattr(r, "keys") else r[idx]

    def code_of(r):
        return (field(r, "unit_code", 0) or "").replace(" ", "")

    items = [r for r in rows if code_of(r) in my_units]
    if unit:
        items = [r for r in items if code_of(r) == unit.replace(" ", "")]
        exam_rows = [e for e in exam_rows if e["unit_code"].replace(" ", "") == unit.replace(" ", "")]
    if wants_exam:
        items = [r for r in items if EXAM_WORDS.search(field(r, "title", 1) or "")]
    items = items[:8]

    unit_label = ""
    if unit:
        name = my_units.get(unit.replace(" ", ""), "")
        unit_label = f"{unit} {name}".strip() if name else unit

    if wants_exam:
        return _answer_exams(m, year, unit, unit_label, exam_rows, exam_info, items, field)

    if not items:
        scope = f" for {unit_label}" if unit_label else f" for Year {year}"
        return (f"📌 No deadlines recorded{scope} yet.\n"
                "They appear here as soon as your lecturer or class rep adds them, "
                "and you'll get a message when they do.")

    lines = [f"📌 Deadlines — {unit_label or f'Year {year}'}:"]
    for r in items:
        lines.append(f"\n• {field(r,'unit_code',0)} — {field(r,'title',1)}\n"
                     f"  Due: {field(r,'due_date',2)}")
    return "\n".join(lines)


def _answer_exams(m, year, unit, unit_label, exam_rows, exam_info, cats, field):
    """Exam papers (from the exam timetable) + CATs (announced by lecturers/reps)."""
    from exam_timetable import describe
    today = datetime.now(EAT).date().isoformat()
    upcoming = [e for e in exam_rows if e["exam_date"] >= today]
    first_only = bool(re.search(r"\b(FIRST|NEXT)\b", m))

    if unit:
        papers = exam_rows                       # one unit: show it even if already sat
    else:
        papers = upcoming[:1] if first_only else upcoming[:10]

    lines = []
    if papers:
        lines.append(f"📝 {'Next exam' if first_only and not unit else 'Exams'} — "
                     f"{unit_label or f'Year {year}'}:")
        for e in papers:
            done = " ✅ done" if e["exam_date"] < today else ""
            supp = " (supp)" if e["kind"] == "SUPP" else ""
            lines.append(f"\n• {e['unit_code']} {e['unit_name']}{supp}{done}\n  {describe(e)}")
    if cats:
        lines.append("\n📌 CATs & tests announced:" if papers else
                     f"📌 CATs & tests — {unit_label or f'Year {year}'}:")
        for r in cats:
            lines.append(f"\n• {field(r,'unit_code',0)} — {field(r,'title',1)}\n"
                         f"  Due: {field(r,'due_date',2)}")

    if not lines:
        scope = unit_label or f"Year {year}"
        if exam_info and exam_rows == [] and unit:
            msg = f"📝 {scope} is not on the current exam timetable ({exam_info['label']})."
        elif exam_info and not upcoming:
            msg = (f"📝 No upcoming exams for {scope} on the current exam timetable "
                   f"({exam_info['label']}).")
        else:
            msg = f"📝 No exams or CATs recorded for {scope} yet."
        return (msg + "\nCATs appear here when your lecturer or class rep announces them, "
                "and exams when the exam timetable is published.")

    if exam_info and papers:
        when = str(exam_info["uploaded_at"])[:10]
        lines.append(f"\n_{exam_info['label']} · updated {when}_")
        if exam_info.get("official_link"):
            lines.append(f"Confirm on the official timetable: {exam_info['official_link']}")
    return "\n".join(lines)


def answer_fee() -> str:
    return ("💰 Fee balances are on the student portal:\n"
            "https://students.dkut.ac.ke\n\n"
            "Log in with your registration number.\n"
            "UniBot does not store fee information.")


def answer_community(year: int) -> str:
    # Community group links are stored in FAQ table with key 'community_yearN'
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("SELECT answer FROM faq WHERE keywords = %s"),
                    (f"community_year{year}",))
        row = cur.fetchone()

    if not row:
        return ("📚 No peer group exists for your year yet. "
                "Check back soon.")
    link = row[0] if not hasattr(row, "keys") else row["answer"]
    return (f"📚 Year {year} CS Peer Community\n\n"
            f"🔗 {link}\n\n"
            f"⚠️ This is a non-official student space.\n"
            f"Official updates come through UniBot only.")