"""
UniBot — show what the engagement model actually predicts.

Trains Logistic Regression exactly as train_kaggle_engagement.py does
(same features, same 70/30 split, same seed), then prints the model's
prediction for students in the TEST set (never seen in training):
their behaviour, the predicted risk, and what really happened.

Run from the backend folder:

    python ml/show_predictions.py path/to/xAPI-Edu-Data.csv

Output: a table on screen and ml/results/example_predictions.csv
"""

import os
import sys

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
SEED = 42

FEATURE_MAP = {
    "raisedhands": "questions_asked",
    "VisITedResources": "resources_checked",
    "AnnouncementsView": "announcements_read",
    "Discussion": "peer_activity",
    "absent_over_7": "long_absence",
}
FEATURES = list(FEATURE_MAP.values())


def level(p):
    if p >= 0.70:
        return "HIGH"
    if p >= 0.40:
        return "MEDIUM"
    return "LOW"


def main(csv_path):
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    df["absent_over_7"] = (df["StudentAbsenceDays"].str.strip()
                           .str.lower().eq("above-7")).astype(int)
    X = df[list(FEATURE_MAP)].rename(columns=FEATURE_MAP).astype(float)
    y = (df["Class"].str.strip().str.upper() == "L").astype(int)

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.30, stratify=y, random_state=SEED)

    model = Pipeline([("scale", StandardScaler()),
                      ("clf", LogisticRegression(class_weight="balanced", max_iter=1000))])
    model.fit(X_tr, y_tr)

    out = X_te.copy()
    out["risk"] = model.predict_proba(X_te)[:, 1]
    out["predicted"] = out["risk"].apply(level)
    out["actually_at_risk"] = y_te.map({1: "Yes", 0: "No"})
    out["correct"] = ((out["risk"] >= 0.5).astype(int) == y_te).map({True: "Yes", False: "No"})
    out = out.sort_values("risk", ascending=False)

    # A readable sample: the 4 highest-risk, 2 in the middle and the 2 lowest
    n = len(out)
    sample = pd.concat([out.head(4), out.iloc[[n // 2 - 1, n // 2]], out.tail(2)])

    cols = ["questions_asked", "resources_checked", "announcements_read",
            "peer_activity", "long_absence", "risk", "predicted",
            "actually_at_risk", "correct"]
    pd.set_option("display.width", 200)
    print("Engagement model: predictions on unseen test students")
    print(f"Test students: {n}   (sorted by predicted risk, sample of 8)")
    print()
    print(sample[cols].round({"risk": 2}).to_string(index=False))
    print()
    flagged = out[out["risk"] >= 0.5]
    print(f"Flagged as at risk: {len(flagged)} of {n}")
    print(f"Of those flagged, really at risk: {(flagged['actually_at_risk'] == 'Yes').sum()}")
    print(f"At-risk students in the test set: {(out['actually_at_risk'] == 'Yes').sum()}")

    os.makedirs(RESULTS, exist_ok=True)
    out[cols].round({"risk": 3}).to_csv(os.path.join(RESULTS, "example_predictions.csv"), index=False)
    print()
    print("Saved: ml/results/example_predictions.csv")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python ml/show_predictions.py path/to/xAPI-Edu-Data.csv")
    main(sys.argv[1])