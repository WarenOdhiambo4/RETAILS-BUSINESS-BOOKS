
import logging
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.goods_receipts import GoodsReceiptCreate

logger = logging.getLogger(__name__)
def calculate_line_tax(
    quantity: Decimal,
    unit_cost: Decimal,
    tax_rate_percent: Decimal,
) -> Decimal:
    taxable_amount = quantity * unit_cost

    tax = taxable_amount * tax_rate_percent / Decimal("100")

    return tax.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
router = APIRouter(
    prefix="/api/v1/goods-receipts",
    tags=["Goods Receipts"],
)

RECEIPT_SELECT = """
    SELECT
        gr.ReceiptID AS receipt_id,
        gr.PurchaseOrderID AS purchase_order_id,
        gr.WarehouseID AS warehouse_id,
        w.WarehouseName AS warehouse_name,
        gr.ReceivedDate AS received_date,
        gr.Status AS status
    FROM dbo.GoodsReceipts AS gr
    INNER JOIN dbo.Warehouses AS w
        ON w.WarehouseID = gr.WarehouseID
"""


@router.get("")
def list_goods_receipts(
    purchase_order_id: int | None = Query(default=None, ge=1),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = RECEIPT_SELECT
    conditions = []
    params = {"limit": limit, "offset": offset}

    if purchase_order_id is not None:
        conditions.append(
            "gr.PurchaseOrderID = :purchase_order_id"
        )
        params["purchase_order_id"] = purchase_order_id

    if status_filter is not None:
        conditions.append("gr.Status = :receipt_status")
        params["receipt_status"] = status_filter

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += """
        ORDER BY gr.ReceivedDate DESC, gr.ReceiptID DESC
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(query), params
            ).mappings().all()

            return [dict(row) for row in rows]

    except SQLAlchemyError as exc:
        logger.exception("Failed to list goods receipts")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve goods receipts.",
        ) from exc


@router.get("/{receipt_id}")
def get_goods_receipt(receipt_id: int):
    try:
        with engine.connect() as connection:
            receipt = connection.execute(
                text(
                    RECEIPT_SELECT
                    + " WHERE gr.ReceiptID = :receipt_id"
                ),
                {"receipt_id": receipt_id},
            ).mappings().first()

            if receipt is None:
                raise HTTPException(
                    status_code=404,
                    detail="Goods receipt not found.",
                )

            lines = connection.execute(
                text("""
                    SELECT
                        grl.ReceiptLineID AS receipt_line_id,
                        grl.POLineID AS po_line_id,
                        grl.ProductID AS product_id,
                        p.ProductName AS product_name,
                        grl.QuantityReceived AS quantity_received,
                        grl.UnitCost AS unit_cost,
                        grl.QuantityReceived * grl.UnitCost
                            AS line_total
                    FROM dbo.GoodsReceiptLines AS grl
                    INNER JOIN dbo.Products AS p
                        ON p.ProductID = grl.ProductID
                    WHERE grl.ReceiptID = :receipt_id
                    ORDER BY grl.ReceiptLineID
                """),
                {"receipt_id": receipt_id},
            ).mappings().all()

            result = dict(receipt)
            result["lines"] = [dict(line) for line in lines]

            return result

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to retrieve goods receipt %s", receipt_id
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve goods receipt.",
        ) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_goods_receipt(payload: GoodsReceiptCreate):
    if len({
        line.po_line_id for line in payload.lines
    }) != len(payload.lines):
        raise HTTPException(
            status_code=422,
            detail="Each purchase-order line may appear only once per receipt.",
        )

    try:
        with engine.begin() as connection:
            # Confirm that the purchase order exists and is eligible
            # to receive goods.
            order = connection.execute(
                text("""
                    SELECT PurchaseOrderID, Status
                    FROM dbo.PurchaseOrders
                    WHERE PurchaseOrderID = :purchase_order_id
                """),
                {"purchase_order_id": payload.purchase_order_id},
            ).mappings().first()

            if order is None:
                raise HTTPException(
                    status_code=404,
                    detail="Purchase order not found.",
                )

            if order["Status"] not in ("Approved", "PartiallyReceived"):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Goods can only be received against an "
                        "approved or partially received purchase order."
                    ),
                )

            # Confirm the warehouse exists.
            warehouse = connection.execute(
                text("""
                    SELECT WarehouseID
                    FROM dbo.Warehouses
                    WHERE WarehouseID = :warehouse_id
                """),
                {"warehouse_id": payload.warehouse_id},
            ).first()

            if warehouse is None:
                raise HTTPException(
                    status_code=404,
                    detail="Warehouse not found.",
                )

            # Verify that each purchase-order line belongs to this order.
            # Derive ProductID from the database rather than trusting
            # a product ID supplied by the client.
            validated_lines = []

            for line in payload.lines:
                po_line = connection.execute(
                    text("""
                        SELECT
                            POLineID,
                            ProductID,
                            Quantity,
                            ExpectedCost
                        FROM dbo.PurchaseOrderLines
                        WHERE POLineID = :po_line_id
                          AND PurchaseOrderID = :purchase_order_id
                    """),
                    {
                        "po_line_id": line.po_line_id,
                        "purchase_order_id": payload.purchase_order_id,
                    },
                ).mappings().first()

                if po_line is None:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Purchase-order line {line.po_line_id} "
                            "does not belong to this purchase order."
                        ),
                    )

                validated_lines.append({
                     "po_line_id": line.po_line_id,
                     "product_id": po_line["ProductID"],
                     "quantity_received": line.  quantity_received,
                     "unit_cost": line.unit_cost,
                     "tax_rate_percent": line. tax_rate_percent,
                    "tax_amount": calculate_line_tax(
                         line.quantity_received,
                         line.unit_cost,
                         line.tax_rate_percent,
    ),
})

            # Create the receipt header as Draft.
            receipt_id = connection.execute(
                text("""
                    INSERT INTO dbo.GoodsReceipts (
                        PurchaseOrderID,
                        WarehouseID,
                        ReceivedDate,
                        Status
                    )
                    OUTPUT INSERTED.ReceiptID
                    VALUES (
                        :purchase_order_id,
                        :warehouse_id,
                        COALESCE(
                            :received_date,
                            SYSDATETIME()
                        ),
                        'Draft'
                    )
                """),
                {
                    "purchase_order_id": payload.purchase_order_id,
                    "warehouse_id": payload.warehouse_id,
                    "received_date": payload.received_date,
                },
            ).scalar_one()

            # Save receipt lines in the same transaction.
            for line in validated_lines:
                connection.execute(
                      text("""
                        INSERT INTO dbo.GoodsReceiptLines (
                             ReceiptID,
                             POLineID,
                             ProductID,
                             QuantityReceived,
                              UnitCost,
                             TaxRatePercent,
                              TaxAmount
                       )
                     VALUES (
                      :receipt_id,
                      :po_line_id,
                      :product_id,
                      :quantity_received,
                      :unit_cost,
                      :tax_rate_percent,
                      :tax_amount
        )
    """),
    {
        "receipt_id": receipt_id,
        **line,
    },
)

            receipt = connection.execute(
                text(
                    RECEIPT_SELECT
                    + " WHERE gr.ReceiptID = :receipt_id"
                ),
                {"receipt_id": receipt_id},
            ).mappings().one()

            lines = connection.execute(
                text("""
                    SELECT
                        grl.ReceiptLineID AS receipt_line_id,
                        grl.POLineID AS po_line_id,
                        grl.ProductID AS product_id,
                        p.ProductName AS product_name,
                        grl.QuantityReceived AS quantity_received,
                        grl.UnitCost AS unit_cost,
                        grl.QuantityReceived * grl.UnitCost
                            AS line_total
                    FROM dbo.GoodsReceiptLines AS grl
                    INNER JOIN dbo.Products AS p
                        ON p.ProductID = grl.ProductID
                    WHERE grl.ReceiptID = :receipt_id
                    ORDER BY grl.ReceiptLineID
                """),
                {"receipt_id": receipt_id},
            ).mappings().all()

            result = dict(receipt)
            result["lines"] = [dict(line) for line in lines]

            return result

    except HTTPException:
        raise

    except IntegrityError as exc:
        logger.exception("Goods receipt integrity error")
        raise HTTPException(
            status_code=409,
            detail=(
                "The goods receipt conflicts with a database "
                "constraint. Verify the related records."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception("Failed to create goods receipt")
        raise HTTPException(
            status_code=500,
            detail="Unable to create goods receipt.",
        ) from exc


@router.post("/{receipt_id}/approve")
def approve_goods_receipt(receipt_id: int):
    if receipt_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Receipt ID must be greater than zero.",
        )

    try:
        with engine.begin() as connection:
            # 1. Lock the receipt so concurrent requests cannot
            # approve the same receipt simultaneously.
            receipt = connection.execute(
                text("""
                    SELECT
                        ReceiptID,
                        PurchaseOrderID,
                        WarehouseID,
                        Status
                    FROM dbo.GoodsReceipts WITH (UPDLOCK, HOLDLOCK)
                    WHERE ReceiptID = :receipt_id
                """),
                {"receipt_id": receipt_id},
            ).mappings().first()

            if receipt is None:
                raise HTTPException(
                    status_code=404,
                    detail="Goods receipt not found.",
                )

            if receipt["Status"] != "Draft":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Only draft receipts can be approved. "
                        f"Current status: {receipt['Status']}."
                    ),
                )

            # 2. Lock the purchase order. Approvals of different
            # receipts against this order will be serialized.
            order = connection.execute(
                text("""
                    SELECT PurchaseOrderID, Status
                    FROM dbo.PurchaseOrders WITH (UPDLOCK, HOLDLOCK)
                    WHERE PurchaseOrderID = :purchase_order_id
                """),
                {
                    "purchase_order_id": receipt["PurchaseOrderID"],
                },
            ).mappings().first()

            if order is None or order["Status"] not in (
                "Approved",
                "PartiallyReceived",
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The purchase order must be approved "
                        "before its receipt can be approved."
                    ),
                )

            # 3. Lock and retrieve the receipt lines in a consistent
            # order to reduce the risk of deadlocks.
            lines = connection.execute(
                text("""
                    SELECT
                        ReceiptLineID,
                        POLineID,
                        ProductID,
                        QuantityReceived,
                        UnitCost
                    FROM dbo.GoodsReceiptLines WITH (UPDLOCK, HOLDLOCK)
                    WHERE ReceiptID = :receipt_id
                    ORDER BY POLineID, ReceiptLineID
                """),
                {"receipt_id": receipt_id},
            ).mappings().all()

            if not lines:
                raise HTTPException(
                    status_code=409,
                    detail="A receipt without lines cannot be approved.",
                )

            # 4. Prevent posting inventory twice for the same receipt.
            existing_movements = connection.execute(
                text("""
                    SELECT COUNT_BIG(*)
                    FROM dbo.InventoryMovements
                    WHERE SourceType = 'GoodsReceipt'
                      AND SourceID = :receipt_id
                """),
                {"receipt_id": receipt_id},
            ).scalar_one()

            if existing_movements > 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Inventory movements already exist for this "
                        "receipt. Reconcile the existing movements "
                        "before approving it."
                    ),
                )

            # 5. Validate each purchase-order line and ensure the
            # receipt does not exceed the remaining ordered quantity.
            # Aggregate by PO line in case duplicate rows exist in the DB.
            po_line_ids = sorted({
                line["POLineID"] for line in lines
            })

            for po_line_id in po_line_ids:
                po_line = connection.execute(
                    text("""
                        SELECT
                            POLineID,
                            ProductID,
                            Quantity
                        FROM dbo.PurchaseOrderLines
                             WITH (UPDLOCK, HOLDLOCK)
                        WHERE POLineID = :po_line_id
                          AND PurchaseOrderID = :purchase_order_id
                    """),
                    {
                        "po_line_id": po_line_id,
                        "purchase_order_id": receipt["PurchaseOrderID"],
                    },
                ).mappings().first()

                if po_line is None:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Receipt line references invalid "
                            f"purchase-order line {po_line_id}."
                        ),
                    )

                # A receipt line must use the product recorded
                # on the corresponding purchase-order line.
                for line in lines:
                    if line["POLineID"] != po_line_id:
                        continue

                    if line["ProductID"] != po_line["ProductID"]:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Product mismatch on purchase-order "
                                f"line {po_line_id}."
                            ),
                        )

                    if line["QuantityReceived"] <= 0:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Received quantity must be greater "
                                f"than zero on receipt line "
                                f"{line['ReceiptLineID']}."
                            ),
                        )

                    if line["UnitCost"] < 0:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Unit cost cannot be negative on "
                                f"receipt line {line['ReceiptLineID']}."
                            ),
                        )

                quantity_on_this_receipt = sum(
                    (
                        line["QuantityReceived"]
                        for line in lines
                        if line["POLineID"] == po_line_id
                    ),
                    Decimal("0"),
                )

                previously_approved = connection.execute(
                    text("""
                        SELECT COALESCE(
                            SUM(grl.QuantityReceived), 0
                        )
                        FROM dbo.GoodsReceiptLines AS grl
                        INNER JOIN dbo.GoodsReceipts AS gr
                            ON gr.ReceiptID = grl.ReceiptID
                        WHERE gr.PurchaseOrderID = :purchase_order_id
                          AND grl.POLineID = :po_line_id
                          AND gr.ReceiptID <> :receipt_id
                          AND gr.Status = 'Approved'
                    """),
                    {
                        "purchase_order_id": receipt["PurchaseOrderID"],
                        "po_line_id": po_line_id,
                        "receipt_id": receipt_id,
                    },
                ).scalar_one()

                if (
                    previously_approved + quantity_on_this_receipt
                    > po_line["Quantity"]
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Approving receipt {receipt_id} would "
                            f"exceed the ordered quantity for "
                            f"purchase-order line {po_line_id}."
                        ),
                    )

            # 6. Post exactly one inventory movement for each
            # receipt line. The movement and approval share a
            # transaction, so failures roll back both.
            for line in lines:
                connection.execute(
                    text("""
                        INSERT INTO dbo.InventoryMovements (
                            ProductID,
                            WarehouseID,
                            MovementType,
                            Quantity,
                            UnitCost,
                            SourceType,
                            SourceID
                        )
                        VALUES (
                            :product_id,
                            :warehouse_id,
                            'Receipt',
                            :quantity,
                            :unit_cost,
                            'GoodsReceipt',
                            :receipt_id
                        )
                    """),
                    {
                        "product_id": line["ProductID"],
                        "warehouse_id": receipt["WarehouseID"],
                        "quantity": line["QuantityReceived"],
                        "unit_cost": line["UnitCost"],
                        "receipt_id": receipt_id,
                    },
                )

            # 7. Mark the receipt Approved only after inventory
            # movements have been inserted successfully.
            update_result = connection.execute(
                text("""
                    UPDATE dbo.GoodsReceipts
                    SET Status = 'Approved'
                    WHERE ReceiptID = :receipt_id
                      AND Status = 'Draft'
                """),
                {"receipt_id": receipt_id},
            )

            if update_result.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The receipt status changed unexpectedly. "
                        "No changes have been committed."
                    ),
                )


                
            # 8. Recalculate the purchase-order status using
            # all approved receipts across all PO lines.
            order_totals = connection.execute(
                text("""
                    SELECT
                        COALESCE(SUM(pol.Quantity), 0)
                            AS TotalOrdered,
                        COALESCE(
                            SUM(received.TotalReceived), 0
                        ) AS TotalReceived
                    FROM dbo.PurchaseOrderLines AS pol
                    LEFT JOIN (
                        SELECT
                            grl.POLineID,
                            SUM(grl.QuantityReceived)
                                AS TotalReceived
                        FROM dbo.GoodsReceiptLines AS grl
                        INNER JOIN dbo.GoodsReceipts AS gr
                            ON gr.ReceiptID = grl.ReceiptID
                        WHERE gr.PurchaseOrderID = :purchase_order_id
                          AND gr.Status = 'Approved'
                        GROUP BY grl.POLineID
                    ) AS received
                        ON received.POLineID = pol.POLineID
                    WHERE pol.PurchaseOrderID = :purchase_order_id
                """),
                {
                    "purchase_order_id": receipt["PurchaseOrderID"],
                },
            ).mappings().one()

            total_ordered = order_totals["TotalOrdered"]
            total_received = order_totals["TotalReceived"]

            if total_ordered <= 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The purchase order has no valid ordered "
                        "quantity. Its status cannot be updated."
                    ),
                )

            if total_received >= total_ordered:
                new_order_status = "Received"
            elif total_received > 0:
                new_order_status = "PartiallyReceived"
            else:
                new_order_status = "Approved"

            order_update = connection.execute(
                text("""
                    UPDATE dbo.PurchaseOrders
                    SET Status = :new_status
                    WHERE PurchaseOrderID = :purchase_order_id
                      AND Status IN ('Approved', 'PartiallyReceived')
                """),
                {
                    "new_status": new_order_status,
                    "purchase_order_id": receipt["PurchaseOrderID"],
                },
            )

            if order_update.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The purchase-order status could not be "
                        "updated. No changes have been committed."
                    ),
                )


            # 8. Return the updated receipt using the existing
            # response structure.
            updated_receipt = connection.execute(
                text(
                    RECEIPT_SELECT
                    + " WHERE gr.ReceiptID = :receipt_id"
                ),
                {"receipt_id": receipt_id},
            ).mappings().one()

            return dict(updated_receipt)

    except HTTPException:
        raise

    except IntegrityError as exc:
        logger.warning(
            "Goods receipt approval constraint conflict for receipt %s",
            receipt_id,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "The goods receipt could not be approved because "
                "of a database constraint conflict."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to approve goods receipt %s",
            receipt_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to approve goods receipt.",
        ) from exc
