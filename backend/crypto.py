"""
UniBot — Phone number encryption.

Design:
  phone_hash  : SHA-256, one-way. Used for identity matching/lookups.
  phone_enc   : Fernet (AES) encrypted. Used ONLY when sending a message.
  
The encryption key lives in .env, never in code, never in git.
Phone numbers are therefore never stored in plaintext anywhere.
"""

import os
import base64
import hashlib
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger("unibot.crypto")

# Key comes from environment. In development, we derive a stable
# key so the app still runs without configuration.
_raw_key = os.getenv("ENCRYPTION_KEY", "")

if _raw_key:
    KEY = _raw_key.encode()
else:
    # Dev fallback: derive a consistent key from a fixed dev seed.
    # NEVER use this in production — set ENCRYPTION_KEY in .env
    seed = hashlib.sha256(b"unibot-dev-only-key").digest()
    KEY = base64.urlsafe_b64encode(seed)
    logger.warning("Using development encryption key. "
                   "Set ENCRYPTION_KEY in .env for production.")

_fernet = Fernet(KEY)


def encrypt_phone(phone: str) -> str:
    """Encrypt a phone number for storage."""
    return _fernet.encrypt(phone.strip().encode()).decode()


def decrypt_phone(token: str) -> str:
    """Decrypt a stored phone number. Returns None if it fails."""
    try:
        return _fernet.decrypt(token.encode()).decode()
    except Exception as e:
        logger.error(f"Failed to decrypt phone: {e}")
        return None


def generate_key() -> str:
    """Generate a new key — run once, put the result in .env"""
    return Fernet.generate_key().decode()


if __name__ == "__main__":
    print("Add this to your .env file:\n")
    print(f"ENCRYPTION_KEY={generate_key()}")