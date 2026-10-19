
import logging

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/supplier-payments",
    tags=["Supplier Payments"],
)


PAYMENT_SELECT = """
    SELECT
        sp.SupplierPaymentID AS payment_id,
        sp.SupplierID AS supplier_id,
        s.FullName AS supplier_name,
        sp.PaymentDate AS payment_date,
        sp.Amount AS amount,
        sp.Reference AS reference,
        sp.BankAccountID AS bank_account_id,
        ba.AccountName AS bank_account_name,
        sp.AccountCode AS account_code
    FROM dbo.SupplierPayments AS sp
    INNER JOIN dbo.Suppliers AS s
        ON s.SupplierID = sp.SupplierID
    LEFT JOIN dbo.BankAccounts AS ba
        ON ba.BankAccountID = sp.BankAccountID
"""


@router.get("")
def list_supplier_payments(
    supplier_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = PAYMENT_SELECT
    params = {
        "limit": limit,
        "offset": offset,
    }

    if supplier_id is not None:
        query += " WHERE sp.SupplierID = :supplier_id"
        params["supplier_id"] = supplier_id

    query += """
        ORDER BY sp.PaymentDate DESC, sp.SupplierPaymentID DESC
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
        logger.exception("Failed to list supplier payments")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve supplier payments.",
        ) from exc


@router.get("/{payment_id}")
def get_supplier_payment(payment_id: int):
    query = PAYMENT_SELECT + """
        WHERE sp.SupplierPaymentID = :payment_id
    """

    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(query),
                {"payment_id": payment_id},
            ).mappings().first()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Supplier payment not found.",
                )

            return dict(row)

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to retrieve supplier payment %s",
            payment_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve supplier payment.",
        ) from exc
