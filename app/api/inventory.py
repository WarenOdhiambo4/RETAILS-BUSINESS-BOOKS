import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/inventory",
    tags=["Inventory"],
)


class InventoryMovementResponse(BaseModel):
    movement_id: int
    product_id: int
    warehouse_id: int
    movement_date: datetime
    movement_type: str
    quantity: Decimal
    unit_cost: Decimal
    source_type: str
    source_id: int


MOVEMENT_COLUMNS = """
    MovementID AS movement_id,
    ProductID AS product_id,
    WarehouseID AS warehouse_id,
    MovementDate AS movement_date,
    MovementType AS movement_type,
    Quantity AS quantity,
    UnitCost AS unit_cost,
    SourceType AS source_type,
    SourceID AS source_id
"""


@router.get(
    "/movements",
    response_model=list[InventoryMovementResponse],
)
def list_inventory_movements(
    product_id: int | None = Query(default=None, gt=0),
    warehouse_id: int | None = Query(default=None, gt=0),
    movement_type: str | None = Query(default=None, max_length=60),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = text(f"""
        SELECT {MOVEMENT_COLUMNS}
        FROM dbo.InventoryMovements
        WHERE (:product_id IS NULL OR ProductID = :product_id)
          AND (:warehouse_id IS NULL OR WarehouseID = :warehouse_id)
          AND (:movement_type IS NULL OR MovementType = :movement_type)
        ORDER BY MovementDate DESC, MovementID DESC
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY;
    """)

    try:
        with engine.connect() as connection:
            result = connection.execute(
                query,
                {
                    "product_id": product_id,
                    "warehouse_id": warehouse_id,
                    "movement_type": movement_type,
                    "offset": offset,
                    "limit": limit,
                },
            )

            return [dict(row) for row in result.mappings()]

    except SQLAlchemyError:
        logger.exception("Failed to list inventory movements.")
        raise HTTPException(
            status_code=503,
            detail="Inventory information is temporarily unavailable.",
        )


@router.get(
    "/movements/{movement_id}",
    response_model=InventoryMovementResponse,
)
def get_inventory_movement(movement_id: int):
    if movement_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Movement ID must be positive.",
        )

    query = text(f"""
        SELECT {MOVEMENT_COLUMNS}
        FROM dbo.InventoryMovements
        WHERE MovementID = :movement_id;
    """)

    try:
        with engine.connect() as connection:
            row = connection.execute(
                query,
                {"movement_id": movement_id},
            ).mappings().first()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Inventory movement not found.",
            )

        return dict(row)

    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception("Failed to retrieve inventory movement.")
        raise HTTPException(
            status_code=503,
            detail="Inventory information is temporarily unavailable.",
        )
