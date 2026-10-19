
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine
from app.security import create_session_digest


bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """Return the active user associated with a valid bearer session."""

    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required or the session has expired.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    token_digest = create_session_digest(credentials.credentials)

    try:
        with engine.connect() as connection:
            result = connection.execute(
                text("""
                    SELECT
                        s.SessionID,
                        u.UserID,
                        u.Email,
                        u.FirstName,
                        u.LastName,
                        u.EmailVerified,
                        u.AccountStatus
                    FROM dbo.UserSessions AS s
                    INNER JOIN dbo.Users AS u
                        ON u.UserID = s.UserID
                    WHERE s.TokenDigest = :token_digest
                      AND s.RevokedAt IS NULL
                      AND s.ExpiresAt > SYSUTCDATETIME()
                      AND u.EmailVerified = 1
                      AND u.AccountStatus = N'Active';
                """),
                {"token_digest": token_digest},
            ).mappings().first()

    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
        ) from exc

    if result is None:
        raise unauthorized

    return dict(result)


def require_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Require the authenticated user to have the Admin role."""

    try:
        with engine.connect() as connection:
            is_admin = connection.execute(
                text("""
                    SELECT 1
                    FROM dbo.UserRoles AS ur
                    INNER JOIN dbo.Roles AS r
                        ON r.RoleID = ur.RoleID
                    WHERE ur.UserID = :user_id
                      AND r.RoleName = N'Admin';
                """),
                {"user_id": current_user["UserID"]},
            ).first()

    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Permission verification is temporarily unavailable.",
        ) from exc

    if is_admin is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator permission is required.",
        )

    return current_user
