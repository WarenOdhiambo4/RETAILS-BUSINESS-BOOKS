
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine
from app.schemas.admin_session import AdminSessionResponse
from app.security_dependencies import require_admin


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/sessions",
    tags=["Admin Sessions"],
)


@router.get("", response_model=list[AdminSessionResponse])
def list_active_sessions(
    user_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: dict = Depends(require_admin),
):
    """List active sessions, optionally filtered by user."""

    query = """
        SELECT
            s.SessionID AS session_id,
            u.UserID AS user_id,
            u.Email AS email,
            u.FirstName AS first_name,
            u.LastName AS last_name,
            s.CreatedAt AS created_at,
            s.ExpiresAt AS expires_at,
            s.LastSeenAt AS last_seen_at,
            CAST(
                CASE
                    WHEN s.SessionID = :current_session_id THEN 1
                    ELSE 0
                END
                AS bit
            ) AS is_current_session
        FROM dbo.UserSessions AS s
        INNER JOIN dbo.Users AS u
            ON u.UserID = s.UserID
        WHERE s.RevokedAt IS NULL
          AND s.ExpiresAt > SYSUTCDATETIME()
          AND u.EmailVerified = 1
          AND u.AccountStatus = N'Active'
    """

    parameters = {
        "current_session_id": current_user["SessionID"],
        "limit": limit,
    }

    if user_id is not None:
        query += " AND u.UserID = :user_id"
        parameters["user_id"] = user_id

    query += """
        ORDER BY s.CreatedAt DESC
        OFFSET 0 ROWS
        FETCH NEXT :limit ROWS ONLY;
    """

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(query),
                parameters,
            ).mappings().all()

        return [dict(row) for row in rows]

    except SQLAlchemyError as exc:
        logger.exception("Failed to list active sessions")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session information is temporarily unavailable.",
        ) from exc


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_session(
    session_id: UUID,
    current_user: dict = Depends(require_admin),
):
    """Revoke one active session by its session ID."""

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    UPDATE dbo.UserSessions
                    SET RevokedAt = SYSUTCDATETIME()
                    WHERE SessionID = :session_id
                      AND RevokedAt IS NULL;
                """),
                {"session_id": str(session_id)},
            )

            if result.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="An unrevoked session with that ID was not found.",
                )

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception("Failed to revoke session")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The session could not be revoked at this time.",
        ) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
