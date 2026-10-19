
import logging
from fastapi import status
from sqlalchemy import bindparam
from sqlalchemy.exc import IntegrityError

from app.schemas.supplier_bills import SupplierBillCreate
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/supplier-bills",
    tags=["Supplier Bills"],
)


BILL_SELECT = """
    SELECT
        sb.BillID AS bill_id,
        sb.SupplierID AS supplier_id,
        s.FullName AS supplier_name,
        sb.SupplierInvoiceNo AS supplier_invoice_no,
        sb.BillDate AS bill_date,
        sb.DueDate AS due_date,
        sb.Status AS status,
        COALESCE(
            totals.subtotal,
            CAST(0 AS DECIMAL(19, 2))
        ) AS subtotal,
        COALESCE(
            totals.tax_total,
            CAST(0 AS DECIMAL(19, 2))
        ) AS tax_total,
        COALESCE(
            totals.bill_total,
            CAST(0 AS DECIMAL(19, 2))
        ) AS bill_total
    FROM dbo.SupplierBills AS sb
    INNER JOIN dbo.Suppliers AS s
        ON s.SupplierID = sb.SupplierID
    OUTER APPLY (
        SELECT
            SUM(sl.Quantity * sl.UnitCost) AS subtotal,
            SUM(sl.TaxAmount) AS tax_total,
            SUM(
                (sl.Quantity * sl.UnitCost) + sl.TaxAmount
            ) AS bill_total
        FROM dbo.SupplierBillLines AS sl
        WHERE sl.BillID = sb.BillID
    ) AS totals
"""


@router.get("")
def list_supplier_bills(
    supplier_id: int | None = Query(default=None, ge=1),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = BILL_SELECT
    conditions = []
    params = {"limit": limit, "offset": offset}

    if supplier_id is not None:
        conditions.append("sb.SupplierID = :supplier_id")
        params["supplier_id"] = supplier_id

    if status is not None:
        conditions.append("sb.Status = :status")
        params["status"] = status

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += """
        ORDER BY sb.BillDate DESC, sb.BillID DESC
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
        logger.exception("Failed to list supplier bills")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve supplier bills.",
        ) from exc


@router.get("/{bill_id}")
def get_supplier_bill(bill_id: int):
    try:
        with engine.connect() as connection:
            bill = connection.execute(
                text(BILL_SELECT + """
                    WHERE sb.BillID = :bill_id
                """),
                {"bill_id": bill_id},
            ).mappings().first()

            if bill is None:
                raise HTTPException(
                    status_code=404,
                    detail="Supplier bill not found.",
                )

            lines = connection.execute(
                text("""
                    SELECT
                        sl.BillLineID AS bill_line_id,
                        sl.ProductID AS product_id,
                        p.ProductName AS product_name,
                        sl.Quantity AS quantity,
                        sl.UnitCost AS unit_cost,
                        sl.TaxAmount AS tax_amount,
                        (
                            sl.Quantity * sl.UnitCost
                        ) + sl.TaxAmount AS line_total
                    FROM dbo.SupplierBillLines AS sl
                    INNER JOIN dbo.Products AS p
                        ON p.ProductID = sl.ProductID
                    WHERE sl.BillID = :bill_id
                    ORDER BY sl.BillLineID
                """),
                {"bill_id": bill_id},
            ).mappings().all()

            result = dict(bill)
            result["lines"] = [dict(line) for line in lines]

            return result

    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to retrieve supplier bill %s", bill_id
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve supplier bill.",
        ) from exc


@router.post("", status_code=status.HTTP_201_CREATED)
def create_supplier_bill(payload: SupplierBillCreate):
    if payload.due_date < payload.bill_date:
        raise HTTPException(
            status_code=422,
            detail="Due date cannot be earlier than bill date.",
        )

    invoice_number = payload.supplier_invoice_no.strip()
    if not invoice_number:
        raise HTTPException(
            status_code=422,
            detail="Supplier invoice number cannot be empty.",
        )

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

            existing_bill = connection.execute(
                text("""
                    SELECT BillID
                    FROM dbo.SupplierBills
                    WHERE SupplierID = :supplier_id
                      AND SupplierInvoiceNo = :invoice_number
                """),
                {
                    "supplier_id": payload.supplier_id,
                    "invoice_number": invoice_number,
                },
            ).first()

            if existing_bill is not None:
                raise HTTPException(
                    status_code=409,
                    detail="This supplier invoice number already exists.",
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
                            "Every bill line must reference an active product."
                        ),
                        "invalid_product_ids": invalid_product_ids,
                    },
                )

            bill_id = connection.execute(
                text("""
                    INSERT INTO dbo.SupplierBills (
                        SupplierID,
                        SupplierInvoiceNo,
                        BillDate,
                        DueDate,
                        Status
                    )
                    OUTPUT INSERTED.BillID
                    VALUES (
                        :supplier_id,
                        :invoice_number,
                        :bill_date,
                        :due_date,
                        'Draft'
                    )
                """),
                {
                    "supplier_id": payload.supplier_id,
                    "invoice_number": invoice_number,
                    "bill_date": payload.bill_date,
                    "due_date": payload.due_date,
                },
            ).scalar_one()

            for line in payload.lines:
                connection.execute(
                    text("""
                        INSERT INTO dbo.SupplierBillLines (
                            BillID,
                            ProductID,
                            Quantity,
                            UnitCost,
                            TaxAmount
                        )
                        VALUES (
                            :bill_id,
                            :product_id,
                            :quantity,
                            :unit_cost,
                            :tax_amount
                        )
                    """),
                    {
                        "bill_id": bill_id,
                        "product_id": line.product_id,
                        "quantity": line.quantity,
                        "unit_cost": line.unit_cost,
                        "tax_amount": line.tax_amount,
                    },
                )

            bill = connection.execute(
                text(BILL_SELECT + """
                    WHERE sb.BillID = :bill_id
                """),
                {"bill_id": bill_id},
            ).mappings().one()

            lines = connection.execute(
                text("""
                    SELECT
                        sl.BillLineID AS bill_line_id,
                        sl.ProductID AS product_id,
                        p.ProductName AS product_name,
                        sl.Quantity AS quantity,
                        sl.UnitCost AS unit_cost,
                        sl.TaxAmount AS tax_amount,
                        (
                            sl.Quantity * sl.UnitCost
                        ) + sl.TaxAmount AS line_total
                    FROM dbo.SupplierBillLines AS sl
                    INNER JOIN dbo.Products AS p
                        ON p.ProductID = sl.ProductID
                    WHERE sl.BillID = :bill_id
                    ORDER BY sl.BillLineID
                """),
                {"bill_id": bill_id},
            ).mappings().all()

            result = dict(bill)
            result["lines"] = [dict(line) for line in lines]

            return result

    except HTTPException:
        raise

    except IntegrityError as exc:
        logger.warning(
            "Supplier bill creation violated a database constraint."
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "The bill conflicts with an existing record "
                "or a database constraint."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception("Failed to create supplier bill")
        raise HTTPException(
            status_code=500,
            detail="Unable to create supplier bill.",
        ) from exc
