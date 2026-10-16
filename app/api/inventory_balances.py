
import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/inventory-balances",
    tags=["Inventory Balances"],
)


@router.get("")
def get_inventory_balances(
    product_id: int | None = Query(default=None, gt=0),
    warehouse_id: int | None = Query(default=None, gt=0),
):
    query = text("""
        SELECT
            p.ProductID,
            p.ProductName,
            w.WarehouseID,
            w.WarehouseName,

            CAST(
                COALESCE(
                    SUM(
                        CASE
                            WHEN m.MovementType IN (
                                N'Receipt',
                                N'SalesReturn',
                                N'AdjustmentIn',
                                N'TransferIn'
                            )
                            THEN m.Quantity

                            WHEN m.MovementType IN (
                                N'Sale',
                                N'SupplierReturn',
                                N'AdjustmentOut',
                                N'TransferOut'
                            )
                            THEN -m.Quantity

                            ELSE 0
                        END
                    ),
                    0
                )
                AS decimal(19, 4)
            ) AS QuantityOnHand,

            CAST(
                COALESCE(
                    SUM(
                        CASE
                            WHEN m.MovementType IN (
                                N'Receipt',
                                N'SalesReturn',
                                N'AdjustmentIn',
                                N'TransferIn'
                            )
                            THEN m.Quantity * m.UnitCost

                            WHEN m.MovementType IN (
                                N'Sale',
                                N'SupplierReturn',
                                N'AdjustmentOut',
                                N'TransferOut'
                            )
                            THEN -(m.Quantity * m.UnitCost)

                            ELSE 0
                        END
                    ),
                    0
                )
                AS decimal(19, 2)
            ) AS MovementValueEstimate

        FROM dbo.Products AS p
        CROSS JOIN dbo.Warehouses AS w

        LEFT JOIN dbo.InventoryMovements AS m
            ON m.ProductID = p.ProductID
            AND m.WarehouseID = w.WarehouseID

        WHERE
            (:product_id IS NULL OR p.ProductID = :product_id)
            AND
            (:warehouse_id IS NULL OR w.WarehouseID = :warehouse_id)

        GROUP BY
            p.ProductID,
            p.ProductName,
            w.WarehouseID,
            w.WarehouseName

        ORDER BY
            p.ProductName,
            w.WarehouseName;
    """)

    try:
        with engine.connect() as connection:
            result = connection.execute(
                query,
                {
                    "product_id": product_id,
                    "warehouse_id": warehouse_id,
                },
            )

            rows = result.mappings().all()

        balances = [
            {
                "product_id": row["ProductID"],
                "product_name": row["ProductName"],
                "warehouse_id": row["WarehouseID"],
                "warehouse_name": row["WarehouseName"],
                "quantity_on_hand": str(row["QuantityOnHand"]),
                "movement_value_estimate": str(
                    row["MovementValueEstimate"]
                ),
                "currency": "KES",
            }
            for row in rows
        ]

        return {
            "count": len(balances),
            "balances": balances,
        }

    except SQLAlchemyError:
        logger.exception("Unable to retrieve inventory balances")
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve inventory balances.",
        )
