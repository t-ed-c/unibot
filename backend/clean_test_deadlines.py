"""One-off: remove the test deadlines. Delete this file after running it."""
from database import get_db, _ph, USE_POSTGRES

TEST_TITLES = ["Week 3 lab", "Test", "4205 Week 3 lab"]

print("Database:", "Railway (live)" if USE_POSTGRES else "local SQLite — set DATABASE_URL first!")
with get_db() as conn:
    cur = conn.cursor()
    for title in TEST_TITLES:
        cur.execute(_ph("DELETE FROM deadlines WHERE title = %s"), (title,))
        print(f"Removed {cur.rowcount} deadline(s) titled '{title}'")
    cur.execute("SELECT unit_code, title, due_date FROM deadlines")
    left = cur.fetchall()
print("Deadlines left:", [tuple(r.values()) if hasattr(r, "values") else tuple(r) for r in left])