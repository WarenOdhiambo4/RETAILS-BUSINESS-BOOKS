
import hashlib
import secrets

from sqlalchemy import text

from app.database import engine


STATE_LIFETIME_MINUTES = 10
SUPPORTED_PROVIDERS = {
    "GoogleDrive",
    "OneDrive",
    "Dropbox",
}


def create_oauth_state(
    warehouse_id: int,
    user_id: int,
    provider: str = "GoogleDrive",
) -> str:
    """Create a short-lived OAuth state and persist only its digest."""

    if warehouse_id <= 0 or user_id <= 0:
        raise ValueError("Warehouse ID and user ID must be positive.")

    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError("Unsupported storage provider.")

    # The raw value is returned to the browser redirect.
    # Only its SHA-256 digest is stored in SQL Server.
    state = secrets.token_urlsafe(32)
    state_digest = hashlib.sha256(
        state.encode("utf-8")
    ).hexdigest()

    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO dbo.WarehouseStorageOAuthStates (
                    StateDigest,
                    WarehouseID,
                    UserID,
                    Provider,
                    ExpiresAt
                )
                VALUES (
                    :state_digest,
                    :warehouse_id,
                    :user_id,
                    :provider,
                    DATEADD(
                        MINUTE,
                        :lifetime_minutes,
                        SYSUTCDATETIME()
                    )
                );
            """),
            {
                "state_digest": state_digest,
                "warehouse_id": warehouse_id,
                "user_id": user_id,
                "provider": provider,
                "lifetime_minutes": STATE_LIFETIME_MINUTES,
            },
        )

    return state


def consume_oauth_state(
    state: str,
    provider: str = "GoogleDrive",
) -> dict | None:
    """Atomically consume a valid, unexpired OAuth state exactly once."""

    if not state or provider not in SUPPORTED_PROVIDERS:
        return None

    state_digest = hashlib.sha256(
        state.encode("utf-8")
    ).hexdigest()

    with engine.begin() as connection:
        result = connection.execute(
            text("""
                UPDATE dbo.WarehouseStorageOAuthStates
                SET ConsumedAt = SYSUTCDATETIME()
                OUTPUT
                    INSERTED.WarehouseID AS warehouse_id,
                    INSERTED.UserID AS user_id,
                    INSERTED.Provider AS provider
                WHERE StateDigest = :state_digest
                  AND Provider = :provider
                  AND ConsumedAt IS NULL
                  AND ExpiresAt > SYSUTCDATETIME();
            """),
            {
                "state_digest": state_digest,
                "provider": provider,
            },
        ).mappings().first()

    return dict(result) if result is not None else None
