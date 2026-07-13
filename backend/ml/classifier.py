"""
UniBot — ML Model 1: Intent Classifier.
TF-IDF vectoriser + Logistic Regression.
Categories: TIMETABLE_QUERY, DEADLINE_QUERY, FEE_QUERY, GREETING, HELP, UNKNOWN
"""

import os
import csv
import logging
import joblib

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, f1_score

logger = logging.getLogger("unibot.ml.classifier")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "intent_model.joblib")
CATEGORIES = ["TIMETABLE_QUERY", "DEADLINE_QUERY", "FEE_QUERY",
              "GREETING", "HELP", "UNKNOWN"]


def load_training_data(csv_path):
    """Load labelled messages from CSV with columns: message,label"""
    messages, labels = [], []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            msg = (row.get("message") or "").strip()
            label = (row.get("label") or "").strip().upper()
            if msg and label in CATEGORIES:
                messages.append(msg)
                labels.append(label)
    return messages, labels


def train(csv_path, test_size=0.2, save=True):
    """Train the classifier and print evaluation metrics + baseline comparison."""
    messages, labels = load_training_data(csv_path)
    if len(messages) < 30:
        raise ValueError(
            f"Only {len(messages)} samples found. Collect at least 200 "
            f"for a reliable model (30 minimum to run at all)."
        )

    print(f"Loaded {len(messages)} labelled messages")
    print("Category counts:")
    for cat in CATEGORIES:
        print(f"  {cat:<18} {labels.count(cat)}")
    print()

    X_train, X_test, y_train, y_test = train_test_split(
        messages, labels, test_size=test_size, random_state=42
    )

    model = Pipeline([
        ("tfidf", TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=1,
            sublinear_tf=True,
        )),
        ("clf", LogisticRegression(
            class_weight="balanced",
        )),
    ])

    model.fit(X_train, y_train)
    preds = model.predict(X_test)

    print("=== ML MODEL — TF-IDF + Logistic Regression ===")
    print(classification_report(y_test, preds, zero_division=0))
    ml_f1 = f1_score(y_test, preds, average="weighted", zero_division=0)
    print(f"Weighted F1: {ml_f1:.3f}\n")

    baseline_preds = [keyword_baseline(m) for m in X_test]
    baseline_f1 = f1_score(y_test, baseline_preds, average="weighted", zero_division=0)
    print("=== BASELINE — Keyword Matching ===")
    print(f"Weighted F1: {baseline_f1:.3f}\n")
    print(f"Improvement: ML beats baseline by {(ml_f1 - baseline_f1):.3f} F1 "
          f"({(ml_f1 - baseline_f1) / max(baseline_f1, 0.01) * 100:.0f}% better)\n")

    if ml_f1 >= 0.75:
        print(f"Target F1 >= 0.75 MET ({ml_f1:.3f})")
    else:
        print(f"F1 {ml_f1:.3f} below 0.75 target — collect more data")

    if save:
        joblib.dump(model, MODEL_PATH)
        print(f"\nModel saved to {MODEL_PATH}")

    return model, ml_f1, baseline_f1


def keyword_baseline(message: str) -> str:
    """Simple keyword matching — the naive approach ML must beat."""
    m = message.lower()
    if any(w in m for w in ["hi", "hello", "sasa", "niaje", "mambo", "habari"]):
        return "GREETING"
    if any(w in m for w in ["fee", "balance", "pay", "lipa"]):
        return "FEE_QUERY"
    if any(w in m for w in ["deadline", "due", "assignment", "cat", "exam"]):
        return "DEADLINE_QUERY"
    if any(w in m for w in ["class", "timetable", "lecture", "venue", "when", "wapi"]):
        return "TIMETABLE_QUERY"
    if any(w in m for w in ["help", "support"]):
        return "HELP"
    return "UNKNOWN"


_loaded_model = None


def predict(message: str):
    """Classify a message. Returns (category, confidence). Falls back to baseline."""
    global _loaded_model
    if _loaded_model is None:
        if os.path.exists(MODEL_PATH):
            _loaded_model = joblib.load(MODEL_PATH)
        else:
            return keyword_baseline(message), 0.5

    proba = _loaded_model.predict_proba([message])[0]
    idx = proba.argmax()
    category = _loaded_model.classes_[idx]
    confidence = float(proba[idx])
    return category, confidence


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        train(sys.argv[1])
    else:
        print("Usage: python classifier.py <training_data.csv>")