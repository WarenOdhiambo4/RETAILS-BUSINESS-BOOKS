
import logging
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.inventory_transfers import InventoryTransferCreate

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/inventory-transfers",
    tags=["Inventory Transfers"],
)

CENT = Decimal("0.01")

# These types increase stock at a warehouse.
INBOUND_TYPES = (
    "Receipt",
    "AdjustmentIn",
    "SalesReturn",
    "TransferIn",
)

# These types decrease stock at a warehouse.
OUTBOUND_TYPES = (
    "Sale",
    "AdjustmentOut",
    "SupplierReturn",
    "TransferOut",
)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def inventory_balance(connection, product_id: int, warehouse_id: int):
    """
    Calculate quantity and inventory value from existing movements.

    UPDLOCK + HOLDLOCK protects the product/warehouse movement range
    during posting. The supporting index helps SQL Server target it.
    """
    row = connection.execute(
        text("""
            SELECT
                COALESCE(
                    SUM(
                        CASE
                            WHEN MovementType IN (
                                'Receipt', 'AdjustmentIn',
                                'SalesReturn', 'TransferIn'
                            ) THEN Quantity

                            WHEN MovementType IN (
                                'Sale', 'AdjustmentOut',
                                'SupplierReturn', 'TransferOut'
                            ) THEN -Quantity

                            ELSE 0
                        END
                    ),
                    0
                ) AS AvailableQuantity,

                COALESCE(
                    SUM(
                        CASE
                            WHEN MovementType IN (
                                'Receipt', 'AdjustmentIn',
                                'SalesReturn', 'TransferIn'
                            ) THEN Quantity * UnitCost

                            WHEN MovementType IN (
                                'Sale', 'AdjustmentOut',
                                'SupplierReturn', 'TransferOut'
                            ) THEN -(Quantity * UnitCost)

                            ELSE 0
                        END
                    ),
                    0
                ) AS InventoryValue

            FROM dbo.InventoryMovements
                 WITH (UPDLOCK, HOLDLOCK)
            WHERE ProductID = :product_id
              AND WarehouseID = :warehouse_id
        """),
        {
            "product_id": product_id,
            "warehouse_id": warehouse_id,
        },
    ).mappings().one()

    return (
        Decimal(str(row["AvailableQuantity"])),
        Decimal(str(row["InventoryValue"])),
    )


def transfer_detail(connection, transfer_id: int):
    transfer = connection.execute(
        text("""
            SELECT
                t.TransferID AS transfer_id,
                t.SourceWarehouseID AS source_warehouse_id,
                sw.WarehouseName AS source_warehouse_name,
                t.DestinationWarehouseID AS destination_warehouse_id,
                dw.WarehouseName AS destination_warehouse_name,
                t.TransferDate AS transfer_date,
                t.Status AS status
            FROM dbo.InventoryTransfers AS t
            INNER JOIN dbo.Warehouses AS sw
                ON sw.WarehouseID = t.SourceWarehouseID
            INNER JOIN dbo.Warehouses AS dw
                ON dw.WarehouseID = t.DestinationWarehouseID
            WHERE t.TransferID = :transfer_id
        """),
        {"transfer_id": transfer_id},
    ).mappings().first()

    if transfer is None:
        return None

    lines = connection.execute(
        text("""
            SELECT
                l.TransferLineID AS transfer_line_id,
                l.ProductID AS product_id,
                p.ProductName AS product_name,
                l.Quantity AS quantity
            FROM dbo.InventoryTransferLines AS l
            INNER JOIN dbo.Products AS p
                ON p.ProductID = l.ProductID
            WHERE l.TransferID = :transfer_id
            ORDER BY l.TransferLineID
        """),
        {"transfer_id": transfer_id},
    ).mappings().all()

    result = dict(transfer)
    result["lines"] = [dict(line) for line in lines]
    return result


@router.get("")
def list_inventory_transfers(
    status_filter: str | None = Query(
        default=None,
        alias="status",
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    try:
        with engine.connect() as connection:
            query = """
                SELECT
                    t.TransferID AS transfer_id,
                    t.SourceWarehouseID AS source_warehouse_id,
                    sw.WarehouseName AS source_warehouse_name,
                    t.DestinationWarehouseID AS destination_warehouse_id,
                    dw.WarehouseName AS destination_warehouse_name,
                    t.TransferDate AS transfer_date,
                    t.Status AS status
                FROM dbo.InventoryTransfers AS t
                INNER JOIN dbo.Warehouses AS sw
                    ON sw.WarehouseID = t.SourceWarehouseID
                INNER JOIN dbo.Warehouses AS dw
                    ON dw.WarehouseID = t.DestinationWarehouseID
            """
            params = {"limit": limit, "offset": offset}

            if status_filter is not None:
                query += " WHERE t.Status = :transfer_status"
                params["transfer_status"] = status_filter

            query += """
                ORDER BY t.TransferDate DESC, t.TransferID DESC
                OFFSET :offset ROWS
                FETCH NEXT :limit ROWS ONLY
            """

            rows = connection.execute(
                text(query), params
            ).mappings().all()

            return [dict(row) for row in rows]

    except SQLAlchemyError as exc:
        logger.exception("Failed to list inventory transfers")
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve inventory transfers.",
        ) from exc


@router.get("/{transfer_id}")
def get_inventory_transfer(transfer_id: int):
    if transfer_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Transfer ID must be greater than zero.",
        )

    try:
        with engine.connect() as connection:
            result = transfer_detail(connection, transfer_id)

            if result is None:
                raise HTTPException(
                    status_code=404,
                    detail="Inventory transfer not found.",
                )

            return result

    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to retrieve inventory transfer %s",
            transfer_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve inventory transfer.",
        ) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_inventory_transfer(payload: InventoryTransferCreate):
    try:
        with engine.begin() as connection:
            warehouse_ids = connection.execute(
                text("""
                    SELECT WarehouseID
                    FROM dbo.Warehouses
                    WHERE WarehouseID IN (
                        :source_id,
                        :destination_id
                    )
                """),
                {
                    "source_id": payload.source_warehouse_id,
                    "destination_id": payload.destination_warehouse_id,
                },
            ).scalars().all()

            if payload.source_warehouse_id not in warehouse_ids:
                raise HTTPException(
                    status_code=404,
                    detail="Source warehouse not found.",
                )

            if payload.destination_warehouse_id not in warehouse_ids:
                raise HTTPException(
                    status_code=404,
                    detail="Destination warehouse not found.",
                )

            # Validate every product against the database.
            for line in payload.lines:
                product = connection.execute(
                    text("""
                        SELECT ProductID
                        FROM dbo.Products
                        WHERE ProductID = :product_id
                    """),
                    {"product_id": line.product_id},
                ).first()

                if product is None:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"Product {line.product_id} not found."
                        ),
                    )

            connection.execute(
                text("""
                    INSERT INTO dbo.InventoryTransfers (
                        SourceWarehouseID,
                        DestinationWarehouseID,
                        Status
                    )
                    VALUES (
                        :source_id,
                        :destination_id,
                        N'Draft'
                    )
                """),
                {
                    "source_id": payload.source_warehouse_id,
                    "destination_id": payload.destination_warehouse_id,
                },
            )

            transfer_id = connection.execute(
                text("""
                    SELECT TOP 1 TransferID
                    FROM dbo.InventoryTransfers
                    WHERE SourceWarehouseID = :source_id
                      AND DestinationWarehouseID = :destination_id
                      AND Status = N'Draft'
                    ORDER BY TransferID DESC
                """),
                {
                    "source_id": payload.source_warehouse_id,
                    "destination_id": payload.destination_warehouse_id,
                },
            ).scalar_one()

            for line in payload.lines:
                connection.execute(
                    text("""
                        INSERT INTO dbo.InventoryTransferLines (
                            TransferID,
                            ProductID,
                            Quantity
                        )
                        VALUES (
                            :transfer_id,
                            :product_id,
                            :quantity
                        )
                    """),
                    {
                        "transfer_id": transfer_id,
                        "product_id": line.product_id,
                        "quantity": line.quantity,
                    },
                )

            result = transfer_detail(connection, transfer_id)
            return result

    except HTTPException:
        raise
    except IntegrityError as exc:
        logger.warning("Inventory transfer creation constraint conflict")
        raise HTTPException(
            status_code=409,
            detail="The transfer conflicts with a database constraint.",
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Failed to create inventory transfer")
        raise HTTPException(
            status_code=503,
            detail="Unable to create inventory transfer.",
        ) from exc


@router.post("/{transfer_id}/post")
def post_inventory_transfer(transfer_id: int):
    if transfer_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Transfer ID must be greater than zero.",
        )

    try:
        with engine.begin() as connection:
            # Lock the transfer header to prevent duplicate posting
            # of the same transfer by concurrent requests.
            transfer = connection.execute(
                text("""
                    SELECT
                        TransferID,
                        SourceWarehouseID,
                        DestinationWarehouseID,
                        TransferDate,
                        Status
                    FROM dbo.InventoryTransfers
                         WITH (UPDLOCK, HOLDLOCK)
                    WHERE TransferID = :transfer_id
                """),
                {"transfer_id": transfer_id},
            ).mappings().first()

            if transfer is None:
                raise HTTPException(
                    status_code=404,
                    detail="Inventory transfer not found.",
                )

            if transfer["Status"] != "Draft":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Only Draft transfers can be posted. "
                        f"Current status: {transfer['Status']}."
                    ),
                )

            # Lock and read transfer lines in a stable order.
            lines = connection.execute(
                text("""
                    SELECT
                        TransferLineID,
                        ProductID,
                        Quantity
                    FROM dbo.InventoryTransferLines
                         WITH (UPDLOCK, HOLDLOCK)
                    WHERE TransferID = :transfer_id
                    ORDER BY ProductID, TransferLineID
                """),
                {"transfer_id": transfer_id},
            ).mappings().all()

            if not lines:
                raise HTTPException(
                    status_code=409,
                    detail="A transfer without lines cannot be posted.",
                )

            # Detect any previous or partial posting for this source.
            existing_movements = connection.execute(
                text("""
                    SELECT COUNT_BIG(*)
                    FROM dbo.InventoryMovements
                         WITH (UPDLOCK, HOLDLOCK)
                    WHERE SourceType = N'Transfer'
                      AND SourceID = :transfer_id
                """),
                {"transfer_id": transfer_id},
            ).scalar_one()

            if existing_movements:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Inventory movements already exist for this "
                        "transfer. Reconcile them before retrying."
                    ),
                )

            # Validate stock and determine the source's weighted-
            # average unit cost for each product before inserting
            # either side of the transfer.
            movements = []

            for line in lines:
                quantity = Decimal(str(line["Quantity"]))

                available_quantity, inventory_value = inventory_balance(
                    connection,
                    line["ProductID"],
                    transfer["SourceWarehouseID"],
                )

                if available_quantity < quantity:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Insufficient stock for product "
                            f"{line['ProductID']} in source warehouse. "
                            f"Available: {available_quantity}; "
                            f"requested: {quantity}."
                        ),
                    )

                if available_quantity <= 0:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Product {line['ProductID']} has no "
                            "available stock in the source warehouse."
                        ),
                    )

                if inventory_value < 0:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Inventory value is negative for product "
                            f"{line['ProductID']} in the source warehouse. "
                            "Reconcile inventory valuation before posting."
                        ),
                    )

                unit_cost = money(inventory_value / available_quantity)

                movements.append({
                    "product_id": line["ProductID"],
                    "quantity": quantity,
                    "unit_cost": unit_cost,
                })

            # Both movements per line share the same unit cost.
            # They and the final status update commit atomically.
            for movement in movements:
                common = {
                    **movement,
                    "transfer_date": transfer["TransferDate"],
                    "transfer_id": transfer_id,
                }

                connection.execute(
                    text("""
                        INSERT INTO dbo.InventoryMovements (
                            ProductID,
                            WarehouseID,
                            MovementDate,
                            MovementType,
                            Quantity,
                            UnitCost,
                            SourceType,
                            SourceID
                        )
                        VALUES (
                            :product_id,
                            :source_warehouse_id,
                            :transfer_date,
                            N'TransferOut',
                            :quantity,
                            :unit_cost,
                            N'Transfer',
                            :transfer_id
                        )
                    """),
                    {
                        **common,
                        "source_warehouse_id": (
                            transfer["SourceWarehouseID"]
                        ),
                    },
                )

                connection.execute(
                    text("""
                        INSERT INTO dbo.InventoryMovements (
                            ProductID,
                            WarehouseID,
                            MovementDate,
                            MovementType,
                            Quantity,
                            UnitCost,
                            SourceType,
                            SourceID
                        )
                        VALUES (
                            :product_id,
                            :destination_warehouse_id,
                            :transfer_date,
                            N'TransferIn',
                            :quantity,
                            :unit_cost,
                            N'Transfer',
                            :transfer_id
                        )
                    """),
                    {
                        **common,
                        "destination_warehouse_id": (
                            transfer["DestinationWarehouseID"]
                        ),
                    },
                )

            update_result = connection.execute(
                text("""
                    UPDATE dbo.InventoryTransfers
                    SET Status = N'Posted'
                    WHERE TransferID = :transfer_id
                      AND Status = N'Draft'
                """),
                {"transfer_id": transfer_id},
            )

            if update_result.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Transfer status changed unexpectedly. "
                        "No changes have been committed."
                    ),
                )

            result = transfer_detail(connection, transfer_id)

            result["posted_movements"] = [
                {
                    "product_id": item["product_id"],
                    "quantity": item["quantity"],
                    "unit_cost": item["unit_cost"],
                    "transfer_value": money(
                        item["quantity"] * item["unit_cost"]
                    ),
                }
                for item in movements
            ]

            return result

    except HTTPException:
        raise
    except IntegrityError as exc:
        logger.warning(
            "Inventory transfer posting constraint conflict: %s",
            transfer_id,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "The transfer conflicts with an inventory database "
                "constraint. No transfer changes were committed."
            ),
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to post inventory transfer %s",
            transfer_id,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Unable to post inventory transfer. "
                "The transaction was rolled back."
            ),
        ) from exc
