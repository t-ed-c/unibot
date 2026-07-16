"""
UniBot — Google Form response converter.
Turns the Google Forms CSV export into clean (message,label) training data.
"""

import sys
import os
import re
import csv

LABEL_ORDER = [
    "TIMETABLE_QUERY",   # Q1
    "DEADLINE_QUERY",    # Q2
    "FEE_QUERY",         # Q3
    "GREETING",          # Q4
    "UNKNOWN",           # Q5
]


def split_messages(cell: str):
    if not cell:
        return []
    text = cell.replace(";", "\n").replace("\r", "\n")
    parts = re.split(r"\n+", text)
    out = []
    for p in parts:
        p = re.sub(r"^\s*[\d]+\s*[.\)\-:]\s*", "", p)
        p = re.sub(r"^\s*[-*•]\s*", "", p)
        p = p.strip().strip('"').strip()
        if p and len(p) <= 120:
            out.append(p)
    return out


def convert(input_csv, output_csv=None):
    if output_csv is None:
        output_csv = os.path.join(os.path.dirname(__file__), "real_training_data.csv")

    rows_out = []
    with open(input_csv, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            print("Empty file.")
            return

        question_cols = list(range(1, min(6, len(header))))
        print(f"Detected {len(header)} columns.")
        print("Mapping question columns to labels:")
        for i, col_idx in enumerate(question_cols):
            if i < len(LABEL_ORDER):
                print(f"  '{header[col_idx][:40]}...' -> {LABEL_ORDER[i]}")

        for row in reader:
            for i, col_idx in enumerate(question_cols):
                if i >= len(LABEL_ORDER) or col_idx >= len(row):
                    continue
                label = LABEL_ORDER[i]
                for msg in split_messages(row[col_idx]):
                    rows_out.append((msg, label))

    seen = set()
    unique = []
    for msg, label in rows_out:
        key = (msg.lower(), label)
        if key not in seen:
            seen.add(key)
            unique.append((msg, label))

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["message", "label"])
        w.writerows(unique)

    print(f"\nWrote {len(unique)} training messages to:")
    print(f"   {output_csv}")
    print(f"\nLabel counts:")
    for label in LABEL_ORDER:
        n = sum(1 for _, l in unique if l == label)
        print(f"  {label:<18} {n}")
    print(f"\nNext: python ml/classifier.py ml/real_training_data.csv")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ml/convert_form.py <form_responses.csv>")
        sys.exit(1)
    convert(sys.argv[1])