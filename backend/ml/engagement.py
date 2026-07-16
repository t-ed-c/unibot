"""
UniBot — ML Model 2: Student Disengagement Detector.
Isolation Forest (unsupervised anomaly detection).
6 features per student per week. NO automated action — advisor reviews every flag.
"""

import os
import logging
import joblib
import numpy as np

from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

logger = logging.getLogger("unibot.ml.engagement")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "engagement_model.joblib")

FEATURE_NAMES = [
    "days_since_last_message",
    "broadcast_read_rate",
    "query_volume_trend",
    "midnight_query_rate",
    "deadline_panic_score",
    "peer_activity",
]

CONTAMINATION = 0.10


def train(feature_matrix, save=True):
    """Train the Isolation Forest on weekly engagement features."""
    X = np.array(feature_matrix, dtype=float)
    if len(X) < 20:
        raise ValueError(
            f"Only {len(X)} students. Need at least ~20 for a stable model."
        )

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("iforest", IsolationForest(
            contamination=CONTAMINATION,
            random_state=42,
            n_estimators=100,
        )),
    ])
    model.fit(X)

    if save:
        joblib.dump(model, MODEL_PATH)
        logger.info(f"Engagement model saved to {MODEL_PATH}")

    return model


def risk_score(features, model=None):
    """Score one student. Returns (risk_score_0_to_1, status)."""
    if model is None:
        if not os.path.exists(MODEL_PATH):
            return None, "MODEL_NOT_TRAINED"
        model = joblib.load(MODEL_PATH)

    X = np.array([features], dtype=float)
    raw = model.decision_function(X)[0]
    risk = float(1 / (1 + np.exp(raw * 4)))

    if risk >= 0.8:
        status = "HIGH"
    elif risk >= 0.5:
        status = "MEDIUM"
    else:
        status = "NORMAL"

    return round(risk, 3), status


def score_all_students(students_features, model=None):
    """Score a batch. Returns list sorted by risk (highest first)."""
    if model is None and os.path.exists(MODEL_PATH):
        model = joblib.load(MODEL_PATH)

    results = []
    for sid, feats in students_features.items():
        risk, status = risk_score(feats, model)
        results.append({
            "student_id": sid,
            "risk_score": risk,
            "status": status,
            "days_inactive": feats[0],
            "read_rate": feats[1],
        })

    results.sort(key=lambda r: (r["risk_score"] or 0), reverse=True)
    return results


def extract_features_from_logs(student_id_hash):
    """Compute the 6 weekly features from the engagement_log table."""
    from database import get_db, _ph
    from datetime import datetime, timedelta

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("""
            SELECT event_type, detail, created_at, exclude_from_ml
            FROM engagement_log
            WHERE student_id_hash = %s
            ORDER BY created_at DESC
        """), (student_id_hash,))
        rows = cur.fetchall()

    if not rows:
        return None

    def field(r, name, idx):
        return r[name] if hasattr(r, "keys") else r[idx]

    if any(field(r, "exclude_from_ml", 3) for r in rows):
        return None

    events = [{"type": field(r, "event_type", 0),
               "detail": field(r, "detail", 1),
               "at": field(r, "created_at", 2)} for r in rows]

    now = datetime.now()

    def parse(ts):
        try:
            return datetime.fromisoformat(str(ts).replace("Z", ""))
        except Exception:
            return now

    last = parse(events[0]["at"])
    days_since = (now - last).days

    received = sum(1 for e in events if e["type"] == "BROADCAST_RECEIVED")
    read = sum(1 for e in events if e["type"] == "BROADCAST_READ")
    read_rate = (read / received) if received else 0.5

    this_wk = sum(1 for e in events if e["type"] == "QUERY"
                  and (now - parse(e["at"])).days <= 7)
    prev_wk = sum(1 for e in events if e["type"] == "QUERY"
                  and 7 < (now - parse(e["at"])).days <= 14)
    trend = (this_wk - prev_wk)

    queries = [e for e in events if e["type"] == "QUERY"]
    midnight = sum(1 for e in queries if parse(e["at"]).hour in (23, 0, 1, 2, 3, 4))
    midnight_rate = (midnight / len(queries)) if queries else 0

    deadline_panic = 0.0
    peer = sum(1 for e in events if e["type"] == "COMMUNITY")

    return [days_since, read_rate, trend, midnight_rate, deadline_panic, peer]


if __name__ == "__main__":
    print("Run:  python ml/simulate_engagement.py   to see it in action.")