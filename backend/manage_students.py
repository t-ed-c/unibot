"""
Student management helper — list and remove student registrations.
Usage (in Railway Console):
    python manage_students.py list
    python manage_students.py remove <email>
"""
import sys
from database import init_db, get_db, _ph
from crypto import decrypt_phone


def list_students():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT phone_enc, email, year_group, status FROM students")
        rows = cur.fetchall()
    if not rows:
        print("No students registered.")
        return
    print(f"{len(rows)} student(s):\n")
    for r in rows:
        enc = r["phone_enc"] if hasattr(r, "keys") else r[0]
        email = r["email"] if hasattr(r, "keys") else r[1]
        year = r["year_group"] if hasattr(r, "keys") else r[2]
        status = r["status"] if hasattr(r, "keys") else r[3]
        phone = decrypt_phone(enc) if enc else "?"
        print(f"  {phone}  |  {email}  |  Year {year}  |  {status}")


def remove_student(email):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("DELETE FROM students WHERE email = %s"), (email,))
    print(f"Removed student with email: {email}")


if __name__ == "__main__":
    init_db()
    if len(sys.argv) >= 3 and sys.argv[1] == "remove":
        remove_student(sys.argv[2])
    else:
        list_students()