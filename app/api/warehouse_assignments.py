
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.warehouse_assignment import (
    WarehouseAssignmentCreate,
    WarehouseAssignmentResponse,
    WarehouseAssignmentRoleUpdate,
)
from app.security_dependencies import require_admin


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/warehouses",
    tags=["Warehouse Assignments"],
)


ASSIGNMENT_SELECT = """
    SELECT
        a.WarehouseUserAssignmentID AS warehouse_user_assignment_id,
        a.WarehouseID AS warehouse_id,
        a.UserID AS user_id,
        u.Email AS user_email,
        u.FirstName AS first_name,
        u.LastName AS last_name,
        a.AssignmentRole AS assignment_role,
        a.IsActive AS is_active,
        a.AssignedAt AS assigned_at,
        a.AssignedByUserID AS assigned_by_user_id,
        a.EndedAt AS ended_at,
        a.Notes AS notes
    FROM dbo.WarehouseUserAssignments AS a
    INNER JOIN dbo.Users AS u
        ON u.UserID = a.UserID
"""


def validate_id(value: int, label: str) -> None:
    if value <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label} must be greater than zero.",
        )


@router.get(
    "/{warehouse_id}/assignments",
    response_model=list[WarehouseAssignmentResponse],
)
def list_warehouse_assignments(
    warehouse_id: int,
    include_inactive: bool = Query(default=False),
    current_user: dict = Depends(require_admin),
):
    validate_id(warehouse_id, "Warehouse ID")

    try:
        with engine.connect() as connection:
            warehouse_exists = connection.execute(
                text("""
                    SELECT 1
                    FROM dbo.Warehouses
                    WHERE WarehouseID = :warehouse_id;
                """),
                {"warehouse_id": warehouse_id},
            ).first()

            if warehouse_exists is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Warehouse not found.",
                )

            query = ASSIGNMENT_SELECT + """
                WHERE a.WarehouseID = :warehouse_id
            """

            if not include_inactive:
                query += " AND a.IsActive = 1"

            query += " ORDER BY a.AssignedAt DESC, a.WarehouseUserAssignmentID DESC"

            rows = connection.execute(
                text(query),
                {"warehouse_id": warehouse_id},
            ).mappings().all()

            return [dict(row) for row in rows]

    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to list warehouse assignments.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Warehouse assignments are temporarily unavailable.",
        ) from exc


@router.post(
    "/{warehouse_id}/assignments",
    response_model=WarehouseAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_warehouse_assignment(
    warehouse_id: int,
    payload: WarehouseAssignmentCreate,
    current_user: dict = Depends(require_admin),
):
    validate_id(warehouse_id, "Warehouse ID")

    try:
        with engine.begin() as connection:
            warehouse_exists = connection.execute(
                text("""
                    SELECT 1
                    FROM dbo.Warehouses
                    WHERE WarehouseID = :warehouse_id;
                """),
                {"warehouse_id": warehouse_id},
            ).first()

            if warehouse_exists is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Warehouse not found.",
                )

            user_exists = connection.execute(
                text("""
                    SELECT 1
                    FROM dbo.Users
                    WHERE UserID = :user_id
                      AND EmailVerified = 1
                      AND AccountStatus = N'Active';
                """),
                {"user_id": payload.user_id},
            ).first()

            if user_exists is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Active, verified user not found.",
                )

            assignment_id = connection.execute(
                text("""
                    INSERT INTO dbo.WarehouseUserAssignments (
                        WarehouseID,
                        UserID,
                        AssignmentRole,
                        IsActive,
                        AssignedByUserID,
                        Notes
                    )
                    OUTPUT INSERTED.WarehouseUserAssignmentID
                    VALUES (
                        :warehouse_id,
                        :user_id,
                        :assignment_role,
                        1,
                        :assigned_by_user_id,
                        :notes
                    );
                """),
                {
                    "warehouse_id": warehouse_id,
                    "user_id": payload.user_id,
                    "assignment_role": payload.assignment_role,
                    "assigned_by_user_id": current_user["UserID"],
                    "notes": payload.notes,
                },
            ).scalar_one()

            row = connection.execute(
                text(
                    ASSIGNMENT_SELECT
                    + """
                    WHERE a.WarehouseUserAssignmentID = :assignment_id;
                    """
                ),
                {"assignment_id": assignment_id},
            ).mappings().one()

            return dict(row)

    except HTTPException:
        raise
    except IntegrityError as exc:
        logger.info("Warehouse assignment violated a database constraint.")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This user may already have an active assignment "
                "to this warehouse."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Failed to create warehouse assignment.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The warehouse assignment could not be created.",
        ) from exc


@router.patch(
    "/{warehouse_id}/assignments/{assignment_id}/role",
    response_model=WarehouseAssignmentResponse,
)
def change_assignment_role(
    warehouse_id: int,
    assignment_id: int,
    payload: WarehouseAssignmentRoleUpdate,
    current_user: dict = Depends(require_admin),
):
    validate_id(warehouse_id, "Warehouse ID")
    validate_id(assignment_id, "Assignment ID")

    try:
        with engine.begin() as connection:
            existing = connection.execute(
                text("""
                    SELECT
                        WarehouseUserAssignmentID,
                        UserID,
                        AssignmentRole,
                        Notes
                    FROM dbo.WarehouseUserAssignments
                    WHERE WarehouseUserAssignmentID = :assignment_id
                      AND WarehouseID = :warehouse_id
                      AND IsActive = 1;
                """),
                {
                    "assignment_id": assignment_id,
                    "warehouse_id": warehouse_id,
                },
            ).mappings().first()

            if existing is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Active warehouse assignment not found.",
                )

            if existing["AssignmentRole"] == payload.assignment_role:
                row = connection.execute(
                    text(
                        ASSIGNMENT_SELECT
                        + """
                        WHERE a.WarehouseUserAssignmentID = :assignment_id;
                        """
                    ),
                    {"assignment_id": assignment_id},
                ).mappings().one()

                return dict(row)

            connection.execute(
                text("""
                    UPDATE dbo.WarehouseUserAssignments
                    SET IsActive = 0,
                        EndedAt = SYSUTCDATETIME()
                    WHERE WarehouseUserAssignmentID = :assignment_id
                      AND IsActive = 1;
                """),
                {"assignment_id": assignment_id},
            )

            new_assignment_id = connection.execute(
                text("""
                    INSERT INTO dbo.WarehouseUserAssignments (
                        WarehouseID,
                        UserID,
                        AssignmentRole,
                        IsActive,
                        AssignedByUserID,
                        Notes
                    )
                    OUTPUT INSERTED.WarehouseUserAssignmentID
                    VALUES (
                        :warehouse_id,
                        :user_id,
                        :assignment_role,
                        1,
                        :assigned_by_user_id,
                        :notes
                    );
                """),
                {
                    "warehouse_id": warehouse_id,
                    "user_id": existing["UserID"],
                    "assignment_role": payload.assignment_role,
                    "assigned_by_user_id": current_user["UserID"],
                    "notes": existing["Notes"],
                },
            ).scalar_one()

            row = connection.execute(
                text(
                    ASSIGNMENT_SELECT
                    + """
                    WHERE a.WarehouseUserAssignmentID = :assignment_id;
                    """
                ),
                {"assignment_id": new_assignment_id},
            ).mappings().one()

            return dict(row)

    except HTTPException:
        raise
    except IntegrityError as exc:
        logger.exception("Failed to change warehouse assignment role.")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The role change conflicted with a database constraint.",
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Failed to change warehouse assignment role.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The assignment role could not be changed.",
        ) from exc


@router.post(
    "/{warehouse_id}/assignments/{assignment_id}/end",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def end_warehouse_assignment(
    warehouse_id: int,
    assignment_id: int,
    current_user: dict = Depends(require_admin),
):
    validate_id(warehouse_id, "Warehouse ID")
    validate_id(assignment_id, "Assignment ID")

    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    UPDATE dbo.WarehouseUserAssignments
                    SET IsActive = 0,
                        EndedAt = SYSUTCDATETIME()
                    WHERE WarehouseUserAssignmentID = :assignment_id
                      AND WarehouseID = :warehouse_id
                      AND IsActive = 1;
                """),
                {
                    "assignment_id": assignment_id,
                    "warehouse_id": warehouse_id,
                },
            )

            if result.rowcount != 1:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Active warehouse assignment not found.",
                )

    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Failed to end warehouse assignment.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The warehouse assignment could not be ended.",
        ) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
