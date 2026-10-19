
from cryptography.fernet import Fernet, InvalidToken

from app.storage_settings import storage_settings


def _get_fernet() -> Fernet:
    """Create the encryption handler from the configured secret key."""

    secret = storage_settings.token_encryption_key

    if secret is None:
        raise RuntimeError(
            "Storage token encryption is not configured."
        )

    key = secret.get_secret_value()

    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "Storage token encryption key is invalid."
        ) from exc


def encrypt_refresh_token(refresh_token: str) -> str:
    """Encrypt a provider refresh token before database storage."""

    if not refresh_token:
        raise ValueError("Refresh token cannot be empty.")

    encrypted = _get_fernet().encrypt(
        refresh_token.encode("utf-8")
    )

    return encrypted.decode("utf-8")


def decrypt_refresh_token(encrypted_token: str) -> str:
    """Decrypt a stored provider refresh token for backend use."""

    if not encrypted_token:
        raise ValueError("Encrypted refresh token cannot be empty.")

    try:
        decrypted = _get_fernet().decrypt(
            encrypted_token.encode("utf-8")
        )
    except InvalidToken as exc:
        raise RuntimeError(
            "The stored refresh token cannot be decrypted. "
            "Verify the configured encryption key."
        ) from exc

    return decrypted.decode("utf-8")
