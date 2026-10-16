
from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.warehouse import (
    WarehouseCreate,
    WarehouseReplace,
    WarehousePatch,
    WarehouseResponse,
)

router = APIRouter(
    prefix="/api/v1/warehouses",
    tags=["Warehouses"],
)


WAREHOUSE_COLUMNS = """
    WarehouseID AS warehouse_id,
    WarehouseName AS warehouse_name,
    WarehouseLocation AS warehouse_location
"""


@router.get("", response_model=list[WarehouseResponse])
def list_warehouses(
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    sql = f"""
        SELECT {WAREHOUSE_COLUMNS}
        FROM dbo.Warehouses
        WHERE (
            :search IS NULL
            OR WarehouseName LIKE N'%' + :search + N'%'
            OR WarehouseLocation LIKE N'%' + :search + N'%'
        )
        ORDER BY WarehouseID
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(sql),
                {
                    "search": search,
                    "limit": limit,
                    "offset": offset,
                },
            ).mappings().all()

        return [dict(row) for row in rows]

    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve warehouses.",
        )


@router.get("/{warehouse_id}", response_model=WarehouseResponse)
def get_warehouse(warehouse_id: int):
    sql = f"""
        SELECT {WAREHOUSE_COLUMNS}
        FROM dbo.Warehouses
        WHERE WarehouseID = :warehouse_id
    """

    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(sql),
                {"warehouse_id": warehouse_id},
            ).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Warehouse not found.",
            )

        return dict(row)

    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve the warehouse.",
        )


@router.post(
    "",
    response_model=WarehouseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_warehouse(payload: WarehouseCreate):
    warehouse_name = payload.warehouse_name.strip()

    if not warehouse_name:
        raise HTTPException(
            status_code=422,
            detail="Warehouse name cannot be blank.",
        )

    sql = f"""
        INSERT INTO dbo.Warehouses (
            WarehouseName,
            WarehouseLocation
        )
        OUTPUT
            INSERTED.WarehouseID AS warehouse_id,
            INSERTED.WarehouseName AS warehouse_name,
            INSERTED.WarehouseLocation AS warehouse_location
        VALUES (
            :warehouse_name,
            :warehouse_location
        )
    """

    try:
        with engine.begin() as connection:
            row = connection.execute(
                text(sql),
                {
                    "warehouse_name": warehouse_name,
                    "warehouse_location": payload.warehouse_location,
                },
            ).mappings().one()

        return dict(row)

    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="The warehouse conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to create the warehouse.",
        )


@router.put("/{warehouse_id}", response_model=WarehouseResponse)
def replace_warehouse(
    warehouse_id: int,
    payload: WarehouseReplace,
):
    warehouse_name = payload.warehouse_name.strip()

    if not warehouse_name:
        raise HTTPException(
            status_code=422,
            detail="Warehouse name cannot be blank.",
        )

    sql = """
        UPDATE dbo.Warehouses
        SET
            WarehouseName = :warehouse_name,
            WarehouseLocation = :warehouse_location
        OUTPUT INSERTED.WarehouseID
        WHERE WarehouseID = :warehouse_id
    """

    try:
        with engine.begin() as connection:
            updated = connection.execute(
                text(sql),
                {
                    "warehouse_id": warehouse_id,
                    "warehouse_name": warehouse_name,
                    "warehouse_location": payload.warehouse_location,
                },
            ).first()

            if updated is None:
                raise HTTPException(
                    status_code=404,
                    detail="Warehouse not found.",
                )

            row = connection.execute(
                text(f"""
                    SELECT {WAREHOUSE_COLUMNS}
                    FROM dbo.Warehouses
                    WHERE WarehouseID = :warehouse_id
                """),
                {"warehouse_id": warehouse_id},
            ).mappings().one()

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="The update conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to update the warehouse.",
        )


@router.patch("/{warehouse_id}", response_model=WarehouseResponse)
def update_warehouse_fields(
    warehouse_id: int,
    payload: WarehousePatch,
):
    changes = payload.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one field to update.",
        )

    if (
        "warehouse_name" in changes
        and (
            changes["warehouse_name"] is None
            or not changes["warehouse_name"].strip()
        )
    ):
        raise HTTPException(
            status_code=422,
            detail="Warehouse name cannot be blank or null.",
        )

    if "warehouse_name" in changes:
        changes["warehouse_name"] = changes["warehouse_name"].strip()

    column_map = {
        "warehouse_name": "WarehouseName",
        "warehouse_location": "WarehouseLocation",
    }

    assignments = ", ".join(
        f"{column_map[field]} = :{field}"
        for field in changes
    )

    sql = f"""
        UPDATE dbo.Warehouses
        SET {assignments}
        OUTPUT INSERTED.WarehouseID
        WHERE WarehouseID = :warehouse_id
    """

    parameters = {
        **changes,
        "warehouse_id": warehouse_id,
    }

    try:
        with engine.begin() as connection:
            updated = connection.execute(
                text(sql),
                parameters,
            ).first()

            if updated is None:
                raise HTTPException(
                    status_code=404,
                    detail="Warehouse not found.",
                )

            row = connection.execute(
                text(f"""
                    SELECT {WAREHOUSE_COLUMNS}
                    FROM dbo.Warehouses
                    WHERE WarehouseID = :warehouse_id
                """),
                {"warehouse_id": warehouse_id},
            ).mappings().one()

        return dict(row)

    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="The update conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to update the warehouse.",
        )


@router.delete(
    "/{warehouse_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_warehouse(warehouse_id: int):
    try:
        with engine.begin() as connection:
            result = connection.execute(
                text("""
                    DELETE FROM dbo.Warehouses
                    WHERE WarehouseID = :warehouse_id
                """),
                {"warehouse_id": warehouse_id},
            )

            if result.rowcount == 0:
                raise HTTPException(
                    status_code=404,
                    detail="Warehouse not found.",
                )

        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=(
                "This warehouse is referenced by existing records. "
                "It cannot be deleted."
            ),
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to delete the warehouse.",
        )
