"""
UniBot — ML Model 2 (live): disengagement risk.

Logistic Regression trained on the Kaggle xAPI-Edu-Data set
(ml/train_kaggle_engagement.py), applied to what UniBot logs about each
student. Replaces the original Isolation Forest (ml/engagement.py), which
scored no better than guessing on the Kaggle test set (AUC 0.51).

The same five behaviours are measured in UniBot:
    questions_asked     every question the student sends the bot
    resources_checked   timetable + deadline look-ups
    announcements_read  broadcasts the student opened (WhatsApp read receipts)
    peer_activity       requests for the class peer group (COMMUNITY)
    long_absence        1 if the student has not messaged in over 7 days

Putting pilot numbers on the Kaggle scale: a pilot student's count is
turned into a percentile within their own class, and that percentile is
looked up in the Kaggle data. "Asked more questions than 80% of the
class" becomes the Kaggle value at the 80th percentile.

Strikes and holidays: an admin can pause the model (PAUSE ML / RESUME ML
on WhatsApp, see ml_pauses.py). Paused days are skipped completely: they
do not count as "quiet" days, and activity inside them is ignored. If most
of a class goes quiet with no pause set, the report warns about it.

Flags are for an advisor to review. UniBot never acts on a flag by itself.
"""

import os
from datetime import datetime, timedelta, timezone

import joblib
import numpy as np
import pandas as pd

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engagement_lr.joblib")
WINDOW_DAYS = 28          # look at the last four weeks
ABSENCE_DAYS = 7          # quiet for more than a week counts as a long absence
MIN_CLASS_SIZE = 5        # percentiles mean nothing in a smaller group
HIGH, MEDIUM = 0.70, 0.40
CLASS_QUIET_SHARE = 0.60  # if 60%+ of a class is quiet, suspect a strike/holiday

_bundle = None


def _load():
    global _bundle
    if _bundle is None:
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                "ml/engagement_lr.joblib not found. Run "
                "python ml/train_kaggle_engagement.py <xAPI-Edu-Data.csv> first.")
        _bundle = joblib.load(MODEL_PATH)
    return _bundle


def _parse(ts):
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────
# Pause periods (strikes, holidays)
def _overlap_days(a, b, pauses, now):
    """How many days between a and b fall inside pause periods."""
    total = 0.0
    for start, end, _ in pauses:
        end = end or now
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            total += (hi - lo).total_seconds() / 86400
    return total


def active_days(a, b, pauses, now):
    """Days between a and b, not counting paused days."""
    if a is None or b <= a:
        return 0
    return int(((b - a).total_seconds() / 86400) - _overlap_days(a, b, pauses, now))


def window_start(now, pauses):
    """Go back far enough to cover WINDOW_DAYS of non-paused time."""
    since = now - timedelta(days=WINDOW_DAYS)
    for _ in range(20):
        needed = now - timedelta(days=WINDOW_DAYS + _overlap_days(since, now, pauses, now))
        if abs((needed - since).total_seconds()) < 60:
            break
        since = needed
    return since


def in_pause(at, pauses, now):
    return any(start <= at <= (end or now) for start, end, _ in pauses)


def load_pauses():
    try:
        from ml_pauses import get_pauses
        return get_pauses()
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
def load_events(since, pauses=(), now=None):
    """All engagement events since a date, grouped by student."""
    from database import get_db, _ph
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT student_id_hash, year_group FROM students WHERE status = 'ACTIVE'")
        people = cur.fetchall()
        cur.execute("""SELECT student_id_hash, event_type, detail, created_at, exclude_from_ml
                       FROM engagement_log""")
        rows = cur.fetchall()

    def f(r, name, idx):
        return r[name] if hasattr(r, "keys") else r[idx]

    # Every active student is included, even one who has never messaged
    students = {f(p, "student_id_hash", 0): {"year": f(p, "year_group", 1), "events": [],
                                              "excluded": False, "joined": None}
                for p in people}
    for r in rows:
        st = students.get(f(r, "student_id_hash", 0))
        if st is None:
            continue
        if f(r, "exclude_from_ml", 4):
            st["excluded"] = True
        at = _parse(f(r, "created_at", 3))
        etype = f(r, "event_type", 1)
        if etype == "REGISTERED" and at:
            st["joined"] = at
        if at and at >= since and not in_pause(at, pauses, now):
            st["events"].append((etype, f(r, "detail", 2) or "", at))
    return students


def raw_features(st, now, pauses=()):
    """The five behaviours for one student, as plain counts."""
    queries = [(d, at) for t, d, at in st["events"] if t == "QUERY"]
    reads = sum(1 for t, _, _ in st["events"] if t == "BROADCAST_READ")
    lookups = sum(1 for d, _ in queries
                  if d.startswith("TIMETABLE_QUERY") or d.startswith("DEADLINE_QUERY"))
    peer = sum(1 for d, _ in queries if d.startswith("COMMUNITY"))

    last = max((at for _, at in queries), default=st["joined"])
    days_quiet = active_days(last, now, pauses, now) if last else WINDOW_DAYS
    days_quiet = min(days_quiet, WINDOW_DAYS)
    return {
        "questions_asked": len(queries),
        "resources_checked": lookups,
        "announcements_read": reads,
        "peer_activity": peer,
        "long_absence": 1 if days_quiet > ABSENCE_DAYS else 0,
        "days_quiet": days_quiet,
    }


def to_kaggle_scale(values, kaggle_q):
    """Percentile within the class -> the Kaggle value at that percentile."""
    values = np.asarray(values, dtype=float)
    if values.max() == values.min():           # everyone the same: put them mid-scale
        pct = np.full(len(values), 50.0)
    else:
        pct = np.array([(values < v).mean() * 100 + (values == v).mean() * 50
                        for v in values])
    return np.interp(pct, np.arange(0, 101), kaggle_q)


def score_class(year=None, now=None):
    """
    Risk for every active student (optionally one year group), highest first.
    Each student is compared with classmates in the same year.
    Returns (results, skipped, warnings).
    """
    bundle = _load()
    model, features, kq = bundle["model"], bundle["features"], bundle["kaggle_quantiles"]
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)   # DB times are UTC
    pauses = load_pauses()
    students = load_events(window_start(now, pauses), pauses, now)

    by_year = {}
    for sid, st in students.items():
        if st["excluded"] or (year is not None and st["year"] != year):
            continue
        by_year.setdefault(st["year"], []).append((sid, raw_features(st, now, pauses)))

    results, skipped, warnings = [], [], []
    for yr, group in by_year.items():
        if len(group) < MIN_CLASS_SIZE:
            skipped.append((yr, len(group)))
            continue
        quiet = sum(g[1]["long_absence"] for g in group) / len(group)
        if quiet >= CLASS_QUIET_SHARE:
            warnings.append(
                f"Year {yr}: {quiet:.0%} of the class has been quiet for over "
                f"{ABSENCE_DAYS} days. That looks like a strike or holiday, not individual "
                f"disengagement. An admin can send 'PAUSE ML <start date> <reason>' on WhatsApp.")
        X = pd.DataFrame({
            f: ([g[1]["long_absence"] for g in group] if f == "long_absence"
                else to_kaggle_scale([g[1][f] for g in group], kq[f]))
            for f in features
        }).astype(float)
        probs = model.predict_proba(X)[:, 1]
        for (sid, raw), p in zip(group, probs):
            status = "HIGH" if p >= HIGH else "MEDIUM" if p >= MEDIUM else "NORMAL"
            results.append({"student": sid[:8], "year": yr, "risk": round(float(p), 3),
                            "status": status, **raw})

    results.sort(key=lambda r: -r["risk"])
    return results, skipped, warnings