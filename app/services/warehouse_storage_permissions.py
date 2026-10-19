
import logging

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine


logger = logging.getLogger(__name__)


def require_warehouse_storage_manager(
    current_user: dict,
    warehouse_id: int,
) -> None:
    """Allow Admins or active Managers to manage warehouse storage."""

    if warehouse_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Warehouse ID must be greater than zero.",
        )

    user_id = current_user.get("UserID")

    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Authentication is required.",
        )

    try:
        with engine.connect() as connection:
            authorized = connection.execute(
                text("""
                   
SELECT 1
WHERE EXISTS (
    SELECT 1
    FROM dbo.Users AS u
    WHERE u.UserID = :user_id
      AND u.EmailVerified = 1
      AND u.AccountStatus = N'Active'
      AND (
          EXISTS (
              SELECT 1
              FROM dbo.UserRoles AS ur
              INNER JOIN dbo.Roles AS r
                  ON r.RoleID = ur.RoleID
              WHERE ur.UserID = u.UserID
                AND r.RoleName = N'Admin'
          )
          OR EXISTS (
              SELECT 1
              FROM dbo.WarehouseUserAssignments AS a
              WHERE a.UserID = u.UserID
                AND a.WarehouseID = :warehouse_id
                AND a.AssignmentRole = N'Manager'
                AND a.IsActive = 1
          )
      )
);

                """),
                {
                    "user_id": user_id,
                    "warehouse_id": warehouse_id,
                },
            ).first()

    except SQLAlchemyError as exc:
        logger.exception(
            "Warehouse storage permission verification failed."
        )
        raise HTTPException(
            status_code=503,
            detail="Permission verification is temporarily unavailable.",
        ) from exc

    if authorized is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "Administrator or active warehouse Manager "
                "permission is required."
            ),
        )
