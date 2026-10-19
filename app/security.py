
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from app.security_settings import security_settings


password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password using Argon2id."""
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password without exposing hashing errors."""
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def generate_verification_code() -> str:
    """Generate a cryptographically secure six-digit code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def create_verification_digest(
    user_id: int,
    code: str,
) -> bytes:
    """Create a keyed SHA-256 digest bound to one user."""
    secret = (
        security_settings.verification_code_secret
        .get_secret_value()
        .encode("utf-8")
    )

    message = f"email-verification:{user_id}:{code}".encode("utf-8")

    return hmac.new(
        secret,
        message,
        hashlib.sha256,
    ).digest()


def verify_code_digest(
    user_id: int,
    submitted_code: str,
    stored_digest: bytes,
) -> bool:
    """Compare a submitted code against its stored digest safely."""
    expected_digest = create_verification_digest(
        user_id,
        submitted_code,
    )

    return hmac.compare_digest(expected_digest, stored_digest)


def generate_session_token() -> str:
    """Generate a cryptographically secure opaque session token."""
    return secrets.token_urlsafe(32)


def create_session_digest(token: str) -> bytes:
    """Return the SHA-256 digest stored in the database, never the raw token."""
    return hashlib.sha256(token.encode("utf-8")).digest()



def generate_password_reset_code() -> str:
    """Generate a cryptographically secure six-digit reset code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def digest_password_reset_code(
    user_id: int,
    code: str,
) -> bytes:
    """Return the HMAC-SHA-256 digest of a password-reset code."""
    message = f"password-reset:{user_id}:{code}".encode("utf-8")

    secret = (
        security_settings.verification_code_secret
        .get_secret_value()
        .encode("utf-8")
    )

    return hmac.new(
        secret,
        message,
        hashlib.sha256,
    ).digest()


def verify_password_reset_code(
    user_id: int,
    code: str,
    stored_digest: bytes,
) -> bool:
    """Compare a submitted code against its stored digest."""
    expected_digest = digest_password_reset_code(
        user_id,
        code,
    )

    return hmac.compare_digest(
        expected_digest,
        stored_digest,
    )
