from database import init_db
from timetable import load_timetable_into_db
from conflict import scan_existing_timetable

init_db()
load_timetable_into_db("../data/CS_MAY-AUG_2026_TEACHING_TIMETABLE_DRAFT_4__18_05_2026.xlsx")

print("Scanning real timetable for conflicts...\n")
found = scan_existing_timetable()
print(f"Found {len(found)} conflict(s):\n")
for c in found:
    print(f"  [{c['type']}] {c['detail']}")