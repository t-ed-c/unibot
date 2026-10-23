"""
UniBot — WhatsApp Academic Communication System
Main FastAPI application entry point.

Author: Potason (DKUT Computer Science)
Supervisor: Mr. Patrick Naivasha
"""
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import PlainTextResponse
from contextlib import asynccontextmanager
import logging

from database import init_db
from auth import handle_registration_message, is_registered
from queries import handle_student_query
from broadcast import handle_sender_command, is_registered_sender, record_message_status
from session import get_session, GLOBAL_COMMANDS, handle_global_command
from admin import router as admin_router
from admin_exams import router as admin_exams_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("unibot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise database tables on startup."""
    init_db()
    logger.info("UniBot started — database initialised")
    yield


app = FastAPI(title="UniBot DKUT", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(admin_exams_router)

@app.get("/")
def health_check():
    """Health check endpoint for Railway."""
    return {"status": "UniBot is running", "version": "0.1.0"}


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    From: str = Form(...),      # e.g. "whatsapp:+254712345678"
    Body: str = Form(...),      # the message text
):
    """
    Twilio sends every incoming WhatsApp message here.
    This is the single entry point for ALL student/lecturer messages.
    """
    phone = From.replace("whatsapp:", "").strip()
    message = Body.strip()

    logger.info(f"Incoming message from {phone[-4:]}****: {message[:50]}")

    # ── 1. Global commands work anywhere, anytime ──
    if message.upper() in GLOBAL_COMMANDS:
        reply = handle_global_command(phone, message.upper())
        return PlainTextResponse(twiml(reply), media_type="application/xml")

    # ── 2. Registered senders (lecturers / class reps) ──
    if is_registered_sender(phone):
        reply = handle_sender_command(phone, message)
        if reply:  # sender command was handled
            return PlainTextResponse(twiml(reply), media_type="application/xml")

    # ── 3. Registered students → answer their query ──
    if is_registered(phone):
        reply = handle_student_query(phone, message)
        return PlainTextResponse(twiml(reply), media_type="application/xml")

    # ── 4. Unregistered → registration flow ──
    reply = handle_registration_message(phone, message)
    return PlainTextResponse(twiml(reply), media_type="application/xml")


@app.post("/webhook/status")
async def message_status(
    MessageSid: str = Form(""),
    MessageStatus: str = Form(""),
):
    """Twilio reports delivered / read for each broadcast message here."""
    if MessageSid:
        record_message_status(MessageSid, MessageStatus)
    return PlainTextResponse("", status_code=204)


def twiml(message: str) -> str:
    """Wrap a reply in Twilio's TwiML XML format."""
    # Escape XML special characters
    safe = (message.replace("&", "&amp;")
                   .replace("<", "&lt;")
                   .replace(">", "&gt;"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response><Message>{safe}</Message></Response>"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)