"""UniBot — Local test script. Runs a full conversation offline."""
import sys
from session import get_session, GLOBAL_COMMANDS, handle_global_command
from auth import handle_registration_message, is_registered
from queries import handle_student_query
from broadcast import handle_sender_command, is_registered_sender


def route(phone, message):
    msg = message.strip()
    if msg.upper() in GLOBAL_COMMANDS:
        return handle_global_command(phone, msg.upper())
    if is_registered_sender(phone):
        reply = handle_sender_command(phone, msg)
        if reply:
            return reply
    if is_registered(phone):
        return handle_student_query(phone, msg)
    return handle_registration_message(phone, msg)


def send(phone, message):
    print(f"\n  You: {message}")
    print(f"  Bot: {route(phone, message)}")


def demo():
    phone = "+254712345678"
    print("=" * 50)
    print("  UniBot Demo")
    print("=" * 50)
    send(phone, "JOIN")
    send(phone, "C026-01-0722/2023")
    send(phone, "c026010722@students.dkut.ac.ke")
    otp = get_session(phone).get("otp")
    print(f"\n  (dev OTP: {otp})")
    send(phone, otp)
    send(phone, "4")
    print("\n--- QUERIES (real timetable) ---")
    send(phone, "When is my HCI class?")
    send(phone, "What classes do I have on Monday?")
    send(phone, "sasa")
    print("\n" + "=" * 50)


def chat():
    phone = "+254700000001"
    print("Type as a student. Start with JOIN. Type quit to exit.")
    while True:
        try:
            m = input("\n  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if m.lower() in ("quit", "exit"):
            break
        if not m:
            continue
        print(f"  Bot: {route(phone, m)}")
        s = get_session(phone)
        if s and s.get("state") == "AWAIT_OTP":
            print(f"  (dev hint: OTP is {s.get('otp')})")


if __name__ == "__main__":
    chat() if len(sys.argv) > 1 and sys.argv[1] == "chat" else demo()