"""
UniBot — ML Model 2 trained and evaluated on a public dataset.

Why: the class questionnaire and the pilot give too few students to train
on (the model would memorise them — overfitting). So the engagement model
is trained and tested on the Kaggle "Students' Academic Performance"
dataset (xAPI-Edu-Data, 480 students), and UniBot's own pilot data is
used afterwards as the real-world comparison.

Dataset: https://www.kaggle.com/datasets/aljarah/xAPI-Edu-Data
Download xAPI-Edu-Data.csv and run (from the backend folder):

    python ml/train_kaggle_engagement.py path/to/xAPI-Edu-Data.csv

Outputs go to backend/ml/results/:
    metrics.txt                  all numbers for the report
    confusion_matrix_*.png       one per model (needs matplotlib)
    kaggle_engagement_model.joblib

It also saves backend/ml/engagement_lr.joblib — the Logistic Regression
model UniBot uses live (see ml/risk.py). Commit that file.
"""

import os
import sys
import warnings

warnings.filterwarnings("ignore", message="Unknown solver options")

import joblib
import numpy as np
import pandas as pd

from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, classification_report,
                             confusion_matrix, f1_score, precision_score,
                             recall_score, roc_auc_score)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
SEED = 42

# Kaggle column  ->  what it means in UniBot
FEATURE_MAP = {
    "raisedhands":       "questions_asked",      # ≈ queries sent to the bot
    "VisITedResources":  "resources_checked",    # ≈ timetable / deadline lookups
    "AnnouncementsView": "announcements_read",   # ≈ broadcasts read
    "Discussion":        "peer_activity",        # ≈ COMMUNITY / peer group use
    "absent_over_7":     "long_absence",         # ≈ days since last message is high
}
FEATURES = list(FEATURE_MAP.values())


def load(path):
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    needed = ["raisedhands", "VisITedResources", "AnnouncementsView",
              "Discussion", "StudentAbsenceDays", "Class"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        sys.exit(f"This does not look like xAPI-Edu-Data.csv — missing columns: {missing}")

    df["absent_over_7"] = (df["StudentAbsenceDays"].str.strip()
                           .str.lower().eq("above-7")).astype(int)
    X = df[list(FEATURE_MAP)].rename(columns=FEATURE_MAP).astype(float)
    # Label: low performers (Class = L) are the "at risk / disengaged" group
    y = (df["Class"].str.strip().str.upper() == "L").astype(int)
    return X, y


def scores_for(name, y_true, y_pred, y_score=None):
    out = {
        "model": name,
        "accuracy": accuracy_score(y_true, y_pred),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "f1": f1_score(y_true, y_pred, zero_division=0),
    }
    if y_score is not None:
        out["roc_auc"] = roc_auc_score(y_true, y_score)
    return out


def save_confusion(name, y_true, y_pred):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    fig, ax = plt.subplots(figsize=(4.2, 3.8))
    ax.imshow(cm, cmap="Blues")
    labels = ["Engaged", "At risk"]
    ax.set_xticks([0, 1], labels)
    ax.set_yticks([0, 1], labels)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title(name, fontsize=11)
    for i in range(2):
        for j in range(2):
            ax.text(j, i, cm[i, j], ha="center", va="center", fontsize=14,
                    color="white" if cm[i, j] > cm.max() / 2 else "black")
    fig.tight_layout()
    path = os.path.join(RESULTS, f"confusion_matrix_{name.split()[0].lower()}.png")
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def main(csv_path):
    os.makedirs(RESULTS, exist_ok=True)
    X, y = load(csv_path)
    lines = []

    def say(text=""):
        print(text)
        lines.append(text)

    say("UniBot — Engagement model on Kaggle xAPI-Edu-Data")
    say("=" * 52)
    say(f"Students: {len(X)}   At risk (Class L): {y.sum()} ({y.mean():.0%})")
    say(f"Features: {', '.join(FEATURES)}")
    say()

    # 70 / 30 split, same proportion of at-risk students in both halves
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.30, stratify=y, random_state=SEED)
    say(f"Train: {len(X_tr)} students   Test (never seen in training): {len(X_te)}")
    say()

    results, cms = [], []

    # 1) Isolation Forest — UniBot's design: no labels needed, flags unusual students
    iso = Pipeline([("scale", StandardScaler()),
                    ("iforest", IsolationForest(n_estimators=200,
                                                contamination=float(y_tr.mean()),
                                                random_state=SEED))])
    iso.fit(X_tr)                                   # labels are NOT used
    iso_pred = (iso.predict(X_te) == -1).astype(int)
    iso_score = -iso.decision_function(X_te)        # higher = more unusual
    results.append(scores_for("Isolation Forest (unsupervised)", y_te, iso_pred, iso_score))
    cms.append(("Isolation Forest", iso_pred))

    # 2) Logistic Regression — simple supervised comparison
    logreg = Pipeline([("scale", StandardScaler()),
                       ("clf", LogisticRegression(class_weight="balanced", max_iter=1000))])
    logreg.fit(X_tr, y_tr)
    results.append(scores_for("Logistic Regression (supervised)", y_te,
                              logreg.predict(X_te), logreg.predict_proba(X_te)[:, 1]))
    cms.append(("Logistic Regression", logreg.predict(X_te)))

    # 3) Random Forest — stronger supervised comparison
    forest = RandomForestClassifier(n_estimators=300, max_depth=6, min_samples_leaf=5,
                                    class_weight="balanced", random_state=SEED)
    forest.fit(X_tr, y_tr)
    results.append(scores_for("Random Forest (supervised)", y_te,
                              forest.predict(X_te), forest.predict_proba(X_te)[:, 1]))
    cms.append(("Random Forest", forest.predict(X_te)))

    # 4) Baseline — a single hand rule, the thing ML has to beat
    rule = ((X_te["long_absence"] == 1) &
            (X_te["announcements_read"] < X_tr["announcements_read"].median())).astype(int)
    results.append(scores_for("Rule baseline (absent >7 days AND reads few announcements)",
                              y_te, rule))
    cms.append(("Rule baseline", rule))

    say("Results on the TEST set")
    say("-" * 52)
    say(f"{'Model':<34}{'Acc':>6}{'Prec':>7}{'Rec':>7}{'F1':>7}{'AUC':>7}")
    for r in results:
        auc = f"{r['roc_auc']:.3f}" if "roc_auc" in r else "  -  "
        say(f"{r['model'][:33]:<34}{r['accuracy']:>6.3f}{r['precision']:>7.3f}"
            f"{r['recall']:>7.3f}{r['f1']:>7.3f}{auc:>7}")
    say()
    say("Acc = accuracy, Prec = of students flagged, how many were really at risk,")
    say("Rec = of at-risk students, how many were caught, F1 = balance of the two,")
    say("AUC = how well the model ranks at-risk students above engaged ones (0.5 = guessing).")
    say()

    # Overfitting check: train vs test, and 5-fold cross-validation
    say("Overfitting check")
    say("-" * 52)
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
    for name, model in [("Logistic Regression", logreg), ("Random Forest", forest)]:
        train_f1 = f1_score(y_tr, model.predict(X_tr))
        test_f1 = f1_score(y_te, model.predict(X_te))
        cv_f1 = cross_val_score(model, X, y, cv=cv, scoring="f1")
        say(f"{name:<20} train F1 {train_f1:.3f} | test F1 {test_f1:.3f} | "
            f"5-fold CV F1 {cv_f1.mean():.3f} ± {cv_f1.std():.3f}")
    say("A small gap between train and test F1 means the model is not just memorising.")
    say()

    # Which behaviours matter most
    say("What predicts disengagement (Random Forest importance)")
    say("-" * 52)
    for feat, imp in sorted(zip(FEATURES, forest.feature_importances_), key=lambda t: -t[1]):
        say(f"  {feat:<20} {imp:.3f}")
    say()

    say("Detailed report — Random Forest")
    say("-" * 52)
    say(classification_report(y_te, forest.predict(X_te),
                              target_names=["Engaged", "At risk"], zero_division=0))

    images = [p for p in (save_confusion(n, y_te, pred) for n, pred in cms) if p]
    if images:
        say("Confusion matrices saved:")
        for p in images:
            say(f"  {os.path.relpath(p, os.path.dirname(HERE))}")
    else:
        say("(Install matplotlib to also save confusion-matrix images: pip install matplotlib)")

    joblib.dump({"model": forest, "features": FEATURES},
                os.path.join(RESULTS, "kaggle_engagement_model.joblib"))

    # The model UniBot uses live: Logistic Regression retrained on ALL 480 students,
    # plus each feature's spread in the Kaggle data so pilot numbers can be put on
    # the same scale (see ml/risk.py).
    live = Pipeline([("scale", StandardScaler()),
                     ("clf", LogisticRegression(class_weight="balanced", max_iter=1000))])
    live.fit(X, y)
    quantiles = {f: np.percentile(X[f], np.arange(0, 101)).tolist() for f in FEATURES}
    joblib.dump({"model": live, "features": FEATURES, "kaggle_quantiles": quantiles,
                 "trained_on": f"xAPI-Edu-Data, {len(X)} students"},
                os.path.join(HERE, "engagement_lr.joblib"))
    say()
    say("Weights (Logistic Regression, all 480 students) — positive = more likely at risk")
    say("-" * 52)
    for feat, w in sorted(zip(FEATURES, live.named_steps["clf"].coef_[0]), key=lambda t: -t[1]):
        say(f"  {feat:<20} {w:+.2f}")
    say("Live model saved to ml/engagement_lr.joblib")
    with open(os.path.join(RESULTS, "metrics.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nAll numbers saved to ml/results/metrics.txt")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])