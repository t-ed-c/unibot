"""
UniBot — Database layer.
PostgreSQL in production (Railway), SQLite for local development.
All personally identifiable data is stored as SHA-256 hashes.
"""

import os
import sqlite3
import hashlib
import logging
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger("unibot.db")

# Use DATABASE_URL from Railway in production, SQLite locally
DATABASE_URL = os.getenv("DATABASE_URL", "")
USE_POSTGRES = DATABASE_URL.startswith("postgres")

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor


def sha256(value: str) -> str:
    """Hash any identifier — phone numbers, student IDs — before storage."""
    return hashlib.sha256(value.strip().encode()).hexdigest()


@contextmanager
def get_db():
    """Yields a database connection. Works for both SQLite and PostgreSQL."""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    else:
        conn = sqlite3.connect("unibot.db")
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ph(query: str) -> str:
    """Convert %s placeholders to ? for SQLite."""
    return query if USE_POSTGRES else query.replace("%s", "?")


def init_db():
    """Create all tables if they do not exist."""
    with get_db() as conn:
        cur = conn.cursor()

        # ── Students ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id_hash TEXT PRIMARY KEY,
            phone_hash      TEXT UNIQUE NOT NULL,
            previous_phone_hash TEXT,
            email           TEXT NOT NULL,
            year_group      INTEGER NOT NULL,
            department      TEXT DEFAULT 'C026',
            units           TEXT DEFAULT '[]',
            registered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            year_changes_this_semester INTEGER DEFAULT 0,
            status          TEXT DEFAULT 'ACTIVE'
        )""")

        # ── Senders (lecturers + class reps) ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS senders (
            phone_hash   TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            role         TEXT NOT NULL CHECK (role IN ('LECTURER','CLASS_REP','ADMIN')),
            units        TEXT DEFAULT '[]',
            year_group   INTEGER,
            added_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        # ── Timetable (parsed from DKUT Excel) ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS timetable (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_code   TEXT NOT NULL,
            unit_name   TEXT,
            day         TEXT NOT NULL,
            time_slot   TEXT NOT NULL,
            venue       TEXT,
            lecturer    TEXT,
            year_group  INTEGER NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""" if not USE_POSTGRES else """
        CREATE TABLE IF NOT EXISTS timetable (
            id          SERIAL PRIMARY KEY,
            unit_code   TEXT NOT NULL,
            unit_name   TEXT,
            day         TEXT NOT NULL,
            time_slot   TEXT NOT NULL,
            venue       TEXT,
            lecturer    TEXT,
            year_group  INTEGER NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        # ── Deadlines ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS deadlines (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            unit_code   TEXT NOT NULL,
            title       TEXT NOT NULL,
            due_date    TEXT NOT NULL,
            created_by  TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""" if not USE_POSTGRES else """
        CREATE TABLE IF NOT EXISTS deadlines (
            id          SERIAL PRIMARY KEY,
            unit_code   TEXT NOT NULL,
            title       TEXT NOT NULL,
            due_date    TEXT NOT NULL,
            created_by  TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        # ── Broadcasts ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS broadcasts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_hash TEXT NOT NULL,
            sender_name TEXT,
            sender_role TEXT,
            message     TEXT NOT NULL,
            target_year INTEGER,
            target_unit TEXT,
            sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""" if not USE_POSTGRES else """
        CREATE TABLE IF NOT EXISTS broadcasts (
            id          SERIAL PRIMARY KEY,
            sender_hash TEXT NOT NULL,
            sender_name TEXT,
            sender_role TEXT,
            message     TEXT NOT NULL,
            target_year INTEGER,
            target_unit TEXT,
            sent_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        # ── Per-student delivery log ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS delivery_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            broadcast_id    INTEGER NOT NULL,
            student_id_hash TEXT NOT NULL,
            sent_at         TIMESTAMP,
            delivered_at    TIMESTAMP,
            status          TEXT DEFAULT 'QUEUED'
        )""" if not USE_POSTGRES else """
        CREATE TABLE IF NOT EXISTS delivery_log (
            id              SERIAL PRIMARY KEY,
            broadcast_id    INTEGER NOT NULL,
            student_id_hash TEXT NOT NULL,
            sent_at         TIMESTAMP,
            delivered_at    TIMESTAMP,
            status          TEXT DEFAULT 'QUEUED'
        )""")

        # ── Engagement log (feeds ML Model 2) ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS engagement_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id_hash TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            detail          TEXT,
            exclude_from_ml INTEGER DEFAULT 0,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""" if not USE_POSTGRES else """
        CREATE TABLE IF NOT EXISTS engagement_log (
            id              SERIAL PRIMARY KEY,
            student_id_hash TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            detail          TEXT,
            exclude_from_ml INTEGER DEFAULT 0,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        # ── FAQ (static knowledge) ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS faq (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            keywords TEXT NOT NULL,
            answer   TEXT NOT NULL
        )""" if not USE_POSTGRES else """
        CREATE TABLE IF NOT EXISTS faq (
            id       SERIAL PRIMARY KEY,
            keywords TEXT NOT NULL,
            answer   TEXT NOT NULL
        )""")

        # ── Support tickets ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_hash TEXT NOT NULL,
            issue      TEXT NOT NULL,
            status     TEXT DEFAULT 'OPEN',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""" if not USE_POSTGRES else """
        CREATE TABLE IF NOT EXISTS support_tickets (
            id         SERIAL PRIMARY KEY,
            phone_hash TEXT NOT NULL,
            issue      TEXT NOT NULL,
            status     TEXT DEFAULT 'OPEN',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

    logger.info("All tables created/verified")


def log_engagement(student_id_hash: str, event_type: str, detail: str = ""):
    """Record an engagement event — every interaction feeds ML Model 2."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph(
            "INSERT INTO engagement_log (student_id_hash, event_type, detail) VALUES (%s, %s, %s)"
        ), (student_id_hash, event_type, detail[:200]))
