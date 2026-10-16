from decimal import Decimal, ROUND_HALF_UP
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine

logger = logging.getLogger(__name__)

from app.database import engine

router = APIRouter(
    prefix="/api/v1/stock-counts",
    tags=["Stock Counts"],
)

CENT = Decimal("0.01")


class OpeningStockCost(BaseModel):
    product_id: int = Field(gt=0)
    unit_cost: Decimal = Field(gt=0, max_digits=19, decimal_places=2)


class OpeningStockPostRequest(BaseModel):
    costs: list[OpeningStockCost] = Field(min_length=1)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@router.post("/{stock_count_id}/approve")
def approve_stock_count(stock_count_id: int):
    try:
        with engine.begin() as connection:
            count = connection.execute(
                text("""
                    SELECT StockCountID, Status
                    FROM dbo.StockCounts WITH (UPDLOCK, HOLDLOCK)
                    WHERE StockCountID = :stock_count_id
                """),
                {"stock_count_id": stock_count_id},
            ).mappings().first()

            if count is None:
                raise HTTPException(
                    status_code=404,
                    detail="Stock count not found.",
                )

            if count["Status"] != "Draft":
                raise HTTPException(
                    status_code=409,
                    detail="Only a Draft stock count can be approved.",
                )

            lines = connection.execute(
                text("""
                    SELECT
                        ProductID,
                        SystemQuantity,
                        CountedQuantity
                    FROM dbo.StockCountLines
                    WHERE StockCountID = :stock_count_id
                """),
                {"stock_count_id": stock_count_id},
            ).mappings().all()

            if not lines:
                raise HTTPException(
                    status_code=422,
                    detail="A stock count must contain at least one line.",
                )

            for line in lines:
                system_qty = Decimal(str(line["SystemQuantity"]))
                counted_qty = Decimal(str(line["CountedQuantity"]))

                if system_qty != Decimal("0") or counted_qty <= 0:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "This endpoint only approves opening-stock "
                            "counts with zero system quantity and positive "
                            "physical quantity."
                        ),
                    )

            connection.execute(
                text("""
                    UPDATE dbo.StockCounts
                    SET Status = 'Approved'
                    WHERE StockCountID = :stock_count_id
                      AND Status = 'Draft'
                """),
                {"stock_count_id": stock_count_id},
            )

            connection.execute(
                text("""
                    INSERT INTO dbo.AuditLog (
                        UserID, Action, RecordType, RecordID, Reason
                    )
                    VALUES (
                        :user_id, :action, :record_type, :record_id, :reason
                    )
                """),
                {
                    "user_id": "Development System",
                    "action": "Approved",
                    "record_type": "StockCount",
                    "record_id": str(stock_count_id),
                    "reason": "Opening stock count approved in development.",
                },
            )

        return {
            "stock_count_id": stock_count_id,
            "status": "Approved",
        }

    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to approve the stock count.",
        )


@router.post("/{stock_count_id}/post")
def post_opening_stock(
    stock_count_id: int,
    request: OpeningStockPostRequest,
):
    try:
        with engine.begin() as connection:
            count = connection.execute(
                text("""
                    SELECT
                        sc.StockCountID,
                        sc.WarehouseID,
                        sc.CountDate,
                        sc.Status
                    FROM dbo.StockCounts AS sc WITH (UPDLOCK, HOLDLOCK)
                    WHERE sc.StockCountID = :stock_count_id
                """),
                {"stock_count_id": stock_count_id},
            ).mappings().first()

            if count is None:
                raise HTTPException(
                    status_code=404,
                    detail="Stock count not found.",
                )

            if count["Status"] != "Approved":
                raise HTTPException(
                    status_code=409,
                    detail="The stock count must be Approved before posting.",
                )

            lines = connection.execute(
                text("""
                    SELECT
                        scl.ProductID,
                        p.ProductName,
                        scl.SystemQuantity,
                        scl.CountedQuantity
                    FROM dbo.StockCountLines AS scl
                    INNER JOIN dbo.Products AS p
                        ON p.ProductID = scl.ProductID
                    WHERE scl.StockCountID = :stock_count_id
                """),
                {"stock_count_id": stock_count_id},
            ).mappings().all()

            if not lines:
                raise HTTPException(
                    status_code=422,
                    detail="The stock count has no lines.",
                )

            costs = {
                item.product_id: item.unit_cost
                for item in request.costs
            }

            if len(costs) != len(request.costs):
                raise HTTPException(
                    status_code=422,
                    detail="Each product must have only one unit cost.",
                )

            line_product_ids = {line["ProductID"] for line in lines}

            if set(costs) != line_product_ids:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Provide exactly one verified unit cost for "
                        "every product in the stock count."
                    ),
                )

            total_value = Decimal("0.00")
            movement_data = []

            for line in lines:
                system_qty = Decimal(str(line["SystemQuantity"]))
                counted_qty = Decimal(str(line["CountedQuantity"]))
                unit_cost = costs[line["ProductID"]]

                if system_qty != Decimal("0") or counted_qty <= 0:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "Only positive opening-stock quantities "
                            "with zero system quantity can be posted "
                            "through this endpoint."
                        ),
                    )

                quantity = counted_qty - system_qty
                line_value = money(quantity * unit_cost)
                total_value += line_value

                movement_data.append({
                    "product_id": line["ProductID"],
                    "quantity": quantity,
                    "unit_cost": unit_cost,
                })

            total_value = money(total_value)

            if total_value <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="The opening-stock value must be positive.",
                )

            # Find the open accounting period covering the count date.
            period_id = connection.execute(
                text("""
                    SELECT PeriodID
                    FROM dbo.AccountingPeriods
                    WHERE :posting_date BETWEEN StartDate AND EndDate
                      AND Status = 'Open'
                """),
                {"posting_date": count["CountDate"].date()},
            ).scalar_one_or_none()

            if period_id is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No open accounting period covers the stock "
                        "count date."
                    ),
                )

            # Ensure the two required accounts exist.
            account_count = connection.execute(
                text("""
                    SELECT COUNT(*)
                    FROM dbo.ChartOfAccounts
                    WHERE AccountCode IN (N'1300', N'3990')
                """)
            ).scalar_one()

            if account_count != 2:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The Inventory (1300) and Development Opening "
                        "Equity (3990) accounts must both exist."
                    ),
                )

            # Stable source-based journal number.
            journal_number = f"JRN-SC-{stock_count_id:06d}"

            number_exists = connection.execute(
                text("""
                    SELECT COUNT(*)
                    FROM dbo.JournalEntries
                    WHERE JournalNumber = :journal_number
                """),
                {"journal_number": journal_number},
            ).scalar_one()

            if number_exists:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This journal number already exists. "
                        "Review the existing journal before retrying."
                    ),
                )

            # Create a draft journal header first.
            connection.execute(
                text("""
                    INSERT INTO dbo.JournalEntries (
                        JournalNumber,
                        PostingDate,
                        SourceType,
                        SourceID,
                        Description,
                        Status,
                        PeriodID
                    )
                    VALUES (
                        :journal_number,
                        :posting_date,
                        N'StockCount',
                        :source_id,
                        :description,
                        N'Draft',
                        :period_id
                    );
                """),
                {
                    "journal_number": journal_number,
                    "posting_date": count["CountDate"].date(),
                    "source_id": stock_count_id,
                    "description": (
                        f"Opening inventory for stock count "
                        f"{stock_count_id}"
                    ),
                    "period_id": period_id,
                },
            )

            journal_id = connection.execute(
                text("""
                    SELECT JournalID
                    FROM dbo.JournalEntries
                    WHERE JournalNumber = :journal_number;
                """),
                {"journal_number": journal_number},
            ).scalar_one()

            # Record each product's opening inventory movement.
            for movement in movement_data:
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
                            :warehouse_id,
                            :movement_date,
                            N'AdjustmentIn',
                            :quantity,
                            :unit_cost,
                            N'StockCount',
                            :source_id
                        )
                    """),
                    {
                        **movement,
                        "warehouse_id": count["WarehouseID"],
                        "movement_date": count["CountDate"],
                        "source_id": stock_count_id,
                    },
                )

            # Debit inventory and credit opening equity.
            connection.execute(
                text("""
                    INSERT INTO dbo.JournalEntryLines (
                        JournalID, AccountCode, Debit, Credit
                    )
                    VALUES (
                        :journal_id, N'1300', :amount, 0
                    )
                """),
                {
                    "journal_id": journal_id,
                    "amount": total_value,
                },
            )

            connection.execute(
                text("""
                    INSERT INTO dbo.JournalEntryLines (
                        JournalID, AccountCode, Debit, Credit
                    )
                    VALUES (
                        :journal_id, N'3990', 0, :amount
                    )
                """),
                {
                    "journal_id": journal_id,
                    "amount": total_value,
                },
            )

            # SQL Server's posting triggers validate the journal.
            connection.execute(
                text("""
                    UPDATE dbo.JournalEntries
                    SET Status = N'Posted'
                    WHERE JournalID = :journal_id
                      AND Status = N'Draft'
                """),
                {"journal_id": journal_id},
            )

            connection.execute(
                text("""
                    UPDATE dbo.StockCounts
                    SET Status = N'Posted'
                    WHERE StockCountID = :stock_count_id
                      AND Status = N'Approved'
                """),
                {"stock_count_id": stock_count_id},
            )

            connection.execute(
                text("""
                    INSERT INTO dbo.AuditLog (
                        UserID, Action, RecordType, RecordID, Reason
                    )
                    VALUES (
                        :user_id, :action, :record_type, :record_id, :reason
                    )
                """),
                {
                    "user_id": "Development System",
                    "action": "Posted",
                    "record_type": "StockCount",
                    "record_id": str(stock_count_id),
                    "reason": (
                        f"Opening stock posted. Journal {journal_number}; "
                        f"value KSh {total_value}."
                    ),
                },
            )

            connection.execute(
                text("""
                    INSERT INTO dbo.AuditLog (
                        UserID, Action, RecordType, RecordID, Reason
                    )
                    VALUES (
                        :user_id, :action, :record_type, :record_id, :reason
                    )
                """),
                {
                    "user_id": "Development System",
                    "action": "Posted",
                    "record_type": "JournalEntry",
                    "record_id": str(journal_id),
                    "reason": (
                        f"Opening inventory journal for stock count "
                        f"{stock_count_id}."
                    ),
                },
            )

        return {
            "stock_count_id": stock_count_id,
            "stock_count_status": "Posted",
            "journal_id": journal_id,
            "journal_number": journal_number,
            "journal_status": "Posted",
            "total_quantity": str(
                sum(
                    item["quantity"]
                    for item in movement_data
                )
            ),
            "total_value": str(total_value),
            "currency": "KES",
            "debit_account": "1300",
            "credit_account": "3990",
        }

    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception(
            "Database error while posting stock count ID %s",
            stock_count_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Unable to post opening stock. The transaction was rolled back.",
        )