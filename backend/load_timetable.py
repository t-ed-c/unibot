"""
Load the DKUT timetable Excel file into the database.
Usage:  python load_timetable.py [path_to_excel]
"""

import sys
from database import init_db
from timetable import load_timetable_into_db

DEFAULT_PATH = "data/CS_MAY-AUG_2026_TEACHING_TIMETABLE_DRAFT_4__18_05_2026.xlsx"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    print(f"Initialising database...")
    init_db()
    print(f"Loading timetable from: {path}")
    result = load_timetable_into_db(path)
    print(f"\n✅ Loaded {result['loaded']} sessions")
    if result["errors"]:
        print(f"⚠️  {len(result['errors'])} cells could not be parsed "
              f"(usually merged cells or notes — safe to ignore)")


if __name__ == "__main__":
    main()
