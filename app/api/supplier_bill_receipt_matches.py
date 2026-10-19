
import logging

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.supplier_bill_receipt_matches import (
    SupplierBillReceiptMatchCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/supplier-bill-receipt-matches",
    tags=["Supplier Bill Receipt Matches"],
)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_supplier_bill_receipt_match(
    payload: SupplierBillReceiptMatchCreate,
):
    try:
        with engine.begin() as connection:
            # Lock the bill line and its bill while validating.
            bill_line = connection.execute(
                text("""
                    SELECT
                        bl.BillLineID,
                        bl.BillID,
                        bl.ProductID,
                        bl.Quantity,
                        bl.UnitCost,
                        sb.SupplierID,
                        sb.Status AS BillStatus
                    FROM dbo.SupplierBillLines AS bl
                        WITH (UPDLOCK, HOLDLOCK)
                    JOIN dbo.SupplierBills AS sb
                        WITH (UPDLOCK, HOLDLOCK)
                      ON sb.BillID = bl.BillID
                    WHERE bl.BillLineID = :bill_line_id
                """),
                {"bill_line_id": payload.bill_line_id},
            ).mappings().first()

            if bill_line is None:
                raise HTTPException(
                    status_code=404,
                    detail="Supplier bill line was not found.",
                )

            if bill_line["BillStatus"] != "Draft":
                raise HTTPException(
                    status_code=409,
                    detail="Only draft supplier bills can be matched.",
                )

            # Lock the receipt line and the receipt while validating.
            receipt_line = connection.execute(
                text("""
                    SELECT
                        grl.ReceiptLineID,
                        grl.ReceiptID,
                        grl.ProductID,
                        grl.QuantityReceived,
                        grl.UnitCost AS ReceiptUnitCost,
                        grl.POLineID,
                        gr.Status AS ReceiptStatus,
                        gr.PurchaseOrderID,
                        po.SupplierID AS ReceiptSupplierID,
                        pol.ProductID AS OrderedProductID,
                        pol.ExpectedCost
                    FROM dbo.GoodsReceiptLines AS grl
                        WITH (UPDLOCK, HOLDLOCK)
                    JOIN dbo.GoodsReceipts AS gr
                        WITH (UPDLOCK, HOLDLOCK)
                      ON gr.ReceiptID = grl.ReceiptID
                    JOIN dbo.PurchaseOrders AS po
                      ON po.PurchaseOrderID = gr.PurchaseOrderID
                    JOIN dbo.PurchaseOrderLines AS pol
                      ON pol.POLineID = grl.POLineID
                    WHERE grl.ReceiptLineID = :receipt_line_id
                """),
                {"receipt_line_id": payload.receipt_line_id},
            ).mappings().first()

            if receipt_line is None:
                raise HTTPException(
                    status_code=404,
                    detail="Goods receipt line was not found.",
                )

            if receipt_line["ReceiptStatus"] != "Approved":
                raise HTTPException(
                    status_code=409,
                    detail="Only approved goods receipt lines can be matched.",
                )

            if bill_line["SupplierID"] != receipt_line["ReceiptSupplierID"]:
                raise HTTPException(
                    status_code=409,
                    detail="The bill supplier does not match the receipt supplier.",
                )

            if bill_line["ProductID"] != receipt_line["ProductID"]:
                raise HTTPException(
                    status_code=409,
                    detail="The bill product does not match the receipt product.",
                )

            if receipt_line["ProductID"] != receipt_line["OrderedProductID"]:
                raise HTTPException(
                    status_code=409,
                    detail="The receipt product does not match its purchase order line.",
                )

            # Do not silently create a duplicate line-to-line match.
            duplicate = connection.execute(
                text("""
                    SELECT MatchID
                    FROM dbo.SupplierBillReceiptMatches
                    WHERE BillLineID = :bill_line_id
                      AND ReceiptLineID = :receipt_line_id
                """),
                {
                    "bill_line_id": payload.bill_line_id,
                    "receipt_line_id": payload.receipt_line_id,
                },
            ).first()

            if duplicate is not None:
                raise HTTPException(
                    status_code=409,
                    detail="This bill line is already matched to this receipt line.",
                )

            # Quantity already allocated to this bill line.
            bill_allocated = connection.execute(
                text("""
                    SELECT COALESCE(SUM(MatchedQuantity), 0)
                    FROM dbo.SupplierBillReceiptMatches
                        WITH (UPDLOCK, HOLDLOCK)
                    WHERE BillLineID = :bill_line_id
                """),
                {"bill_line_id": payload.bill_line_id},
            ).scalar_one()

            # Quantity already allocated from this receipt line.
            receipt_allocated = connection.execute(
                text("""
                    SELECT COALESCE(SUM(MatchedQuantity), 0)
                    FROM dbo.SupplierBillReceiptMatches
                        WITH (UPDLOCK, HOLDLOCK)
                    WHERE ReceiptLineID = :receipt_line_id
                """),
                {"receipt_line_id": payload.receipt_line_id},
            ).scalar_one()

            bill_remaining = bill_line["Quantity"] - bill_allocated
            receipt_remaining = (
                receipt_line["QuantityReceived"] - receipt_allocated
            )

            if payload.matched_quantity > bill_remaining:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Matched quantity exceeds the remaining unallocated "
                        f"bill quantity ({bill_remaining})."
                    ),
                )

            if payload.matched_quantity > receipt_remaining:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Matched quantity exceeds the remaining unallocated "
                        f"receipt quantity ({receipt_remaining})."
                    ),
                )

            inserted = connection.execute(
                text("""
                    INSERT INTO dbo.SupplierBillReceiptMatches
                    (
                        BillLineID,
                        ReceiptLineID,
                        MatchedQuantity
                    )
                    OUTPUT
                        INSERTED.MatchID,
                        INSERTED.BillLineID,
                        INSERTED.ReceiptLineID,
                        INSERTED.MatchedQuantity,
                        INSERTED.CreatedAt
                    VALUES
                    (
                        :bill_line_id,
                        :receipt_line_id,
                        :matched_quantity
                    )
                """),
                {
                    "bill_line_id": payload.bill_line_id,
                    "receipt_line_id": payload.receipt_line_id,
                    "matched_quantity": payload.matched_quantity,
                },
            ).mappings().one()

            return {
                "message": "Supplier bill line matched to receipt line.",
                "match": dict(inserted),
                "remaining_bill_quantity": (
                    bill_remaining - payload.matched_quantity
                ),
                "remaining_receipt_quantity": (
                    receipt_remaining - payload.matched_quantity
                ),
            }

    except HTTPException:
        raise
    except IntegrityError as exc:
        logger.warning(
            "Supplier bill receipt match violated a database constraint: %s",
            exc,
        )
        raise HTTPException(
            status_code=409,
            detail="The match conflicts with an existing database constraint.",
        ) from exc
    except SQLAlchemyError as exc:
        logger.exception("Failed to create supplier bill receipt match.")
        raise HTTPException(
            status_code=500,
            detail="Database error while creating the match.",
        ) from exc


@router.get("")
def get_supplier_bill_receipt_matches(
    bill_line_id: int = Query(gt=0),
):
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("""
                    SELECT
                        m.MatchID,
                        m.BillLineID,
                        m.ReceiptLineID,
                        m.MatchedQuantity,
                        m.CreatedAt,
                        sb.BillID,
                        sb.SupplierInvoiceNo,
                        gr.ReceiptID,
                        gr.PurchaseOrderID,
                        grl.ProductID,
                        grl.UnitCost AS ReceiptUnitCost
                    FROM dbo.SupplierBillReceiptMatches AS m
                    JOIN dbo.SupplierBillLines AS bl
                      ON bl.BillLineID = m.BillLineID
                    JOIN dbo.SupplierBills AS sb
                      ON sb.BillID = bl.BillID
                    JOIN dbo.GoodsReceiptLines AS grl
                      ON grl.ReceiptLineID = m.ReceiptLineID
                    JOIN dbo.GoodsReceipts AS gr
                      ON gr.ReceiptID = grl.ReceiptID
                    WHERE m.BillLineID = :bill_line_id
                    ORDER BY m.MatchID
                """),
                {"bill_line_id": bill_line_id},
            ).mappings().all()

            return {
                "bill_line_id": bill_line_id,
                "matches": [dict(row) for row in rows],
            }

    except SQLAlchemyError as exc:
        logger.exception("Failed to retrieve supplier bill receipt matches.")
        raise HTTPException(
            status_code=500,
            detail="Database error while retrieving matches.",
        ) from exc
