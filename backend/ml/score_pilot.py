"""
UniBot — weekly disengagement report for the advisor.

Run from the backend folder (against the live database, set DATABASE_URL
to Railway's DATABASE_PUBLIC_URL first):

    python ml/score_pilot.py            all years
    python ml/score_pilot.py 4          Year 4 only
    python ml/score_pilot.py 4 --csv    also save ml/results/pilot_risk.csv

Students are shown by the first 8 characters of their hashed ID only.
A flag is a prompt for a human check-in, never an automatic action.
Strike or holiday? An admin sends PAUSE ML on WhatsApp (see ml_pauses.py).
"""

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml.risk import score_class, load_pauses, WINDOW_DAYS  # noqa: E402


def main():
    args = sys.argv[1:]
    year = next((int(a) for a in args if a.isdigit()), None)
    results, skipped, warnings = score_class(year)

    pauses = load_pauses()
    current = next((p for p in pauses if p[1] is None), None)
    if current:
        print(f"⏸  Model paused since {current[0]:%d %b %Y} ({current[2]}). "
              f"Paused days are not counted.\n")
    elif pauses:
        print(f"{len(pauses)} past pause period(s) are left out of the scores.\n")
    for w in warnings:
        print(f"⚠  {w}\n")
    for yr, n in skipped:
        print(f"Year {yr}: only {n} active student(s) — need at least 5 to compare. Skipped.")
    if not results:
        print("No students to score yet. Wait until more students have used UniBot.")
        return

    print(f"Disengagement risk — last {WINDOW_DAYS} non-paused days\n")
    print(f"{'Student':<10}{'Yr':>3}{'Risk':>7}  {'Status':<7}{'Qs':>4}{'Look':>5}"
          f"{'Read':>5}{'Peer':>5}{'Quiet':>7}")
    for r in results:
        print(f"{r['student']:<10}{r['year']:>3}{r['risk']:>7.2f}  {r['status']:<7}"
              f"{r['questions_asked']:>4}{r['resources_checked']:>5}"
              f"{r['announcements_read']:>5}{r['peer_activity']:>5}{r['days_quiet']:>6}d")

    flagged = [r for r in results if r["status"] != "NORMAL"]
    print(f"\n{len(results)} students scored, {len(flagged)} flagged for a check-in "
          f"({sum(r['status'] == 'HIGH' for r in results)} HIGH).")
    print("Qs = questions, Look = timetable/deadline look-ups, Read = broadcasts read,")
    print("Peer = peer-group requests, Quiet = days since the student last messaged.")

    if "--csv" in args:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "pilot_risk.csv")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0]))
            w.writeheader()
            w.writerows(results)
        print(f"\nSaved {out}")


if __name__ == "__main__":
    main()