"""
UniBot — Engagement simulator + demo for ML Model 2.
Generates a realistic class (mostly engaged, a few disengaged),
trains the Isolation Forest, and shows what it flags.
"""

import random
import numpy as np
from engagement import train, score_all_students, FEATURE_NAMES

random.seed(42)
np.random.seed(42)


def make_engaged_student():
    return [
        random.randint(0, 4),
        random.uniform(0.6, 1.0),
        random.randint(-1, 3),
        random.uniform(0.0, 0.2),
        random.uniform(0.0, 0.3),
        random.randint(1, 6),
    ]


def make_disengaged_student():
    return [
        random.randint(14, 30),
        random.uniform(0.0, 0.25),
        random.randint(-6, -2),
        random.uniform(0.4, 0.9),
        random.uniform(0.6, 1.0),
        random.randint(0, 1),
    ]


def simulate_class(n_engaged=45, n_disengaged=5):
    students = {}
    truth = {}

    for i in range(n_engaged):
        sid = f"C026-01-{1000+i:04d}/2023"
        students[sid] = make_engaged_student()
        truth[sid] = "ENGAGED"

    for i in range(n_disengaged):
        sid = f"C026-01-{9000+i:04d}/2023"
        students[sid] = make_disengaged_student()
        truth[sid] = "DISENGAGED"

    return students, truth


def main():
    print("=" * 62)
    print("  UniBot — ML Model 2: Disengagement Detector Demo")
    print("=" * 62)

    students, truth = simulate_class(45, 5)
    print(f"\nSimulated class: {len(students)} students")
    print(f"  Truly engaged:    45")
    print(f"  Truly disengaged:  5  (the model should flag these)")

    feature_matrix = list(students.values())
    train(feature_matrix, save=True)
    print(f"\nIsolation Forest trained on {len(feature_matrix)} students")
    print(f"  Contamination assumption: 10%")

    results = score_all_students(students)

    print("\n" + "=" * 62)
    print("  ADVISOR DASHBOARD — Students Flagged for Review")
    print("=" * 62)
    flagged = [r for r in results if r["status"] in ("HIGH", "MEDIUM")]
    for r in flagged:
        actual = truth[r["student_id"]]
        mark = "correctly flagged" if actual == "DISENGAGED" else "false positive"
        print(f"\n  {r['student_id']}  [{r['status']}]  risk={r['risk_score']}")
        print(f"     {int(r['days_inactive'])} days inactive · "
              f"{r['read_rate']*100:.0f}% read rate · {mark}")

    print("\n" + "=" * 62)
    print("  EVALUATION")
    print("=" * 62)
    disengaged_ids = [s for s, t in truth.items() if t == "DISENGAGED"]
    flagged_ids = [r["student_id"] for r in flagged]
    caught = sum(1 for s in disengaged_ids if s in flagged_ids)
    false_pos = sum(1 for s in flagged_ids if truth[s] == "ENGAGED")

    print(f"\n  Disengaged students caught:  {caught}/{len(disengaged_ids)}")
    print(f"  False positives:             {false_pos}")
    print(f"  Detection rate:              {caught/len(disengaged_ids)*100:.0f}%")
    print("\n  NOTE: No automated action taken. Every flag is surfaced")
    print("  to a human advisor who decides whether to intervene.")
    print("=" * 62)


if __name__ == "__main__":
    main()