
import logging

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.purchase_orders import PurchaseOrderCreate


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/purchase-orders",
    tags=["Purchase Orders"],
)


PO_SELECT = """
    SELECT
        po.PurchaseOrderID AS purchase_order_id,
        po.SupplierID AS supplier_id,
        s.FullName AS supplier_name,
        po.OrderDate AS order_date,
        po.Status AS status,
        COALESCE(
            totals.subtotal,
            CAST(0 AS DECIMAL(19, 2))
        ) AS subtotal
    FROM dbo.PurchaseOrders AS po
    INNER JOIN dbo.Suppliers AS s
        ON s.SupplierID = po.SupplierID
    OUTER APPLY (
        SELECT
            SUM(pol.Quantity * pol.ExpectedCost) AS subtotal
        FROM dbo.PurchaseOrderLines AS pol
        WHERE pol.PurchaseOrderID = po.PurchaseOrderID
    ) AS totals
"""


PO_LINES_SELECT = """
    SELECT
        pol.POLineID AS po_line_id,
        pol.ProductID AS product_id,
        p.ProductName AS product_name,
        p.Unit AS unit,
        pol.Quantity AS quantity,
        pol.ExpectedCost AS expected_cost,
        pol.Quantity * pol.ExpectedCost AS line_total
    FROM dbo.PurchaseOrderLines AS pol
    INNER JOIN dbo.Products AS p
        ON p.ProductID = pol.ProductID
    WHERE pol.PurchaseOrderID = :purchase_order_id
    ORDER BY pol.POLineID
"""


@router.get("")
def list_purchase_orders(
    supplier_id: int | None = Query(default=None, ge=1),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = PO_SELECT
    conditions = []
    params = {"limit": limit, "offset": offset}

    if supplier_id is not None:
        conditions.append("po.SupplierID = :supplier_id")
        params["supplier_id"] = supplier_id

    if status_filter is not None:
        conditions.append("po.Status = :status")
        params["status"] = status_filter

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += """
        ORDER BY po.OrderDate DESC, po.PurchaseOrderID DESC
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
        logger.exception("Failed to list purchase orders")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve purchase orders.",
        ) from exc


@router.get("/{purchase_order_id}")
def get_purchase_order(purchase_order_id: int):
    try:
        with engine.connect() as connection:
            order = connection.execute(
                text(
                    PO_SELECT
                    + """
                    WHERE po.PurchaseOrderID = :purchase_order_id
                    """
                ),
                {"purchase_order_id": purchase_order_id},
            ).mappings().first()

            if order is None:
                raise HTTPException(
                    status_code=404,
                    detail="Purchase order not found.",
                )

            lines = connection.execute(
                text(PO_LINES_SELECT),
                {"purchase_order_id": purchase_order_id},
            ).mappings().all()

            result = dict(order)
            result["lines"] = [dict(line) for line in lines]

            return result

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to retrieve purchase order %s",
            purchase_order_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve purchase order.",
        ) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_purchase_order(payload: PurchaseOrderCreate):
    try:
        with engine.begin() as connection:
            supplier = connection.execute(
                text("""
                    SELECT SupplierID
                    FROM dbo.Suppliers
                    WHERE SupplierID = :supplier_id
                """),
                {"supplier_id": payload.supplier_id},
            ).first()

            if supplier is None:
                raise HTTPException(
                    status_code=404,
                    detail="Supplier not found.",
                )

            product_ids = list({
                line.product_id for line in payload.lines
            })

            product_rows = connection.execute(
                text("""
                    SELECT ProductID
                    FROM dbo.Products
                    WHERE Active = 1
                      AND ProductID IN :product_ids
                """).bindparams(
                    bindparam("product_ids", expanding=True)
                ),
                {"product_ids": product_ids},
            ).scalars().all()

            active_product_ids = set(product_rows)
            invalid_product_ids = sorted(
                set(product_ids) - active_product_ids
            )

            if invalid_product_ids:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": (
                            "Every purchase order line must "
                            "reference an active product."
                        ),
                        "invalid_product_ids": invalid_product_ids,
                    },
                )

            purchase_order_id = connection.execute(
                text("""
                    INSERT INTO dbo.PurchaseOrders (
                        SupplierID,
                        OrderDate,
                        Status
                    )
                    OUTPUT INSERTED.PurchaseOrderID
                    VALUES (
                        :supplier_id,
                        COALESCE(
                            :order_date,
                            CAST(GETDATE() AS date)
                        ),
                        'Draft'
                    )
                """),
                {
                    "supplier_id": payload.supplier_id,
                    "order_date": payload.order_date,
                },
            ).scalar_one()

            for line in payload.lines:
                connection.execute(
                    text("""
                        INSERT INTO dbo.PurchaseOrderLines (
                            PurchaseOrderID,
                            ProductID,
                            Quantity,
                            ExpectedCost
                        )
                        VALUES (
                            :purchase_order_id,
                            :product_id,
                            :quantity,
                            :expected_cost
                        )
                    """),
                    {
                        "purchase_order_id": purchase_order_id,
                        "product_id": line.product_id,
                        "quantity": line.quantity,
                        "expected_cost": line.expected_cost,
                    },
                )

            order = connection.execute(
                text(
                    PO_SELECT
                    + """
                    WHERE po.PurchaseOrderID = :purchase_order_id
                    """
                ),
                {"purchase_order_id": purchase_order_id},
            ).mappings().one()

            lines = connection.execute(
                text(PO_LINES_SELECT),
                {"purchase_order_id": purchase_order_id},
            ).mappings().all()

            result = dict(order)
            result["lines"] = [dict(line) for line in lines]

            return result

    except HTTPException:
        raise

    except IntegrityError as exc:
        logger.warning(
            "Purchase order creation violated a database constraint."
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "The purchase order conflicts with a database constraint."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception("Failed to create purchase order")
        raise HTTPException(
            status_code=500,
            detail="Unable to create purchase order.",
        ) from exc



@router.post("/{purchase_order_id}/approve")
def approve_purchase_order(purchase_order_id: int):
    if purchase_order_id <= 0:
        raise HTTPException(
            status_code=422,
            detail="Purchase order ID must be greater than zero.",
        )

    try:
        with engine.begin() as connection:
            order = connection.execute(
                text("""
                    SELECT PurchaseOrderID, Status
                    FROM dbo.PurchaseOrders
                    WHERE PurchaseOrderID = :purchase_order_id
                """),
                {"purchase_order_id": purchase_order_id},
            ).mappings().first()

            if order is None:
                raise HTTPException(
                    status_code=404,
                    detail="Purchase order not found.",
                )

            if order["Status"] != "Draft":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Only draft purchase orders can be approved. "
                        f"Current status: {order['Status']}."
                    ),
                )

            connection.execute(
                text("""
                    UPDATE dbo.PurchaseOrders
                    SET Status = 'Approved'
                    WHERE PurchaseOrderID = :purchase_order_id
                      AND Status = 'Draft'
                """),
                {"purchase_order_id": purchase_order_id},
            )

            updated_order = connection.execute(
                text(
                    PO_SELECT + """
                    WHERE po.PurchaseOrderID = :purchase_order_id
                    """
                ),
                {"purchase_order_id": purchase_order_id},
            ).mappings().one()

            lines = connection.execute(
                text(PO_LINES_SELECT),
                {"purchase_order_id": purchase_order_id},
            ).mappings().all()

            result = dict(updated_order)
            result["lines"] = [dict(line) for line in lines]

            return result

    except HTTPException:
        raise

    except IntegrityError as exc:
        logger.warning(
            "Purchase order approval violated a database constraint."
        )
        raise HTTPException(
            status_code=409,
            detail="The purchase order could not be approved.",
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to approve purchase order %s",
            purchase_order_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to approve purchase order.",
        ) from exc
