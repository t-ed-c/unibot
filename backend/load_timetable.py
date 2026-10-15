"""
Load a DKUT timetable Excel file into the database from the command line.
The normal way is the upload page at /admin/timetable; this script is
the fallback for the server console.

Usage:
    python load_timetable.py <path_to_excel> [semester name]
    python load_timetable.py --check <path_to_excel>     (read only, changes nothing)
"""

import sys
from database import init_db
from timetable import parse_timetable_excel, load_timetable_into_db
from conflict import scan_entries


def main():
    args = sys.argv[1:]
    check_only = "--check" in args
    args = [a for a in args if a != "--check"]
    if not args:
        print(__doc__)
        sys.exit(1)

    path = args[0]
    semester = " ".join(args[1:])
    init_db()

    if check_only:
        parsed = parse_timetable_excel(path)
        conflicts = scan_entries(parsed["sessions"])
        print(f"Semester: {semester or parsed['semester'] or '(not found in file)'}")
        print(f"Classes: {len(parsed['sessions'])}   Units: {len(parsed['units'])}")
        result = {"conflicts": conflicts, "errors": parsed["errors"]}
        print("\nNothing was changed (--check).")
    else:
        result = load_timetable_into_db(path, semester=semester)
        print(f"\n✅ Published {result['semester']}: {result['loaded']} classes, "
              f"{result['units']} units")

    for c in result["conflicts"]:
        print(f"  ⚠️  [{c['type']}] {c['detail']}")
    if not result["conflicts"]:
        print("  No clashes found.")
    for e in result["errors"]:
        print(f"  Could not read: {e}")


if __name__ == "__main__":
    main()