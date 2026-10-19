
import logging
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.customer_payment_create import CustomerPaymentCreate

router = APIRouter(
    prefix="/api/v1/customer-payments",
    tags=["Customer Payments"],
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@router.post("", status_code=201)
def create_customer_payment(payment: CustomerPaymentCreate):
    try:
        with engine.begin() as conn:
            # 1. Validate the customer.
            customer = conn.execute(
                text("""
                    SELECT CustomerID, FullName
                    FROM dbo.Customers
                    WHERE CustomerID = :customer_id
                """),
                {"customer_id": payment.customer_id},
            ).mappings().first()

            if customer is None:
                raise HTTPException(
                    status_code=422,
                    detail="The selected customer does not exist.",
                )

            # 2. Validate the account receiving the money.
            account = conn.execute(
                text("""
                    SELECT AccountCode, AccountType
                    FROM dbo.ChartOfAccounts
                    WHERE AccountCode = :account_code
                """),
                {"account_code": payment.clearing_account_id},
            ).mappings().first()

            if account is None:
                raise HTTPException(
                    status_code=422,
                    detail="The selected clearing account does not exist.",
                )

            if account["AccountType"] != "Asset":
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "The payment clearing account must be an asset "
                        "account, such as Bank Account or M-Pesa Clearing."
                    ),
                )

            # 3. Lock and validate every invoice.
            invoice_data = []
            total_allocated = ZERO

            for allocation in payment.allocations:
                invoice = conn.execute(
                    text("""
                        SELECT
                            InvoiceID,
                            InvoiceNumber,
                            CustomerID,
                            InvoiceDate,
                            InvoiceStatus
                        FROM dbo.SalesInvoices
                            WITH (UPDLOCK, HOLDLOCK)
                        WHERE InvoiceID = :invoice_id
                    """),
                    {"invoice_id": allocation.invoice_id},
                ).mappings().first()

                if invoice is None:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"Invoice ID {allocation.invoice_id} "
                            "does not exist."
                        ),
                    )

                if invoice["CustomerID"] != payment.customer_id:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Invoice {invoice['InvoiceNumber']} "
                            "does not belong to this customer."
                        ),
                    )

                if invoice["InvoiceStatus"] not in (
                    "Issued",
                    "Overdue",
                    "PartiallyPaid",
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Invoice {invoice['InvoiceNumber']} "
                            f"cannot receive payment in status "
                            f"{invoice['InvoiceStatus']}."
                        ),
                    )

                # Obtain the original invoice total.
                invoice_totals = conn.execute(
                    text("""
                        SELECT
                            COUNT(*) AS line_count,
                            SUM(
                                ROUND(
                                    Quantity * UnitPrice - Discount,
                                    2
                                )
                            ) AS invoice_total,
                            SUM(
                                CASE
                                    WHEN TaxRateID IS NOT NULL
                                    THEN 1 ELSE 0
                                END
                            ) AS tax_line_count
                        FROM dbo.SalesInvoiceLines
                        WHERE InvoiceID = :invoice_id
                    """),
                    {"invoice_id": allocation.invoice_id},
                ).mappings().one()

                if (
                    invoice_totals["line_count"] == 0
                    or invoice_totals["tax_line_count"] > 0
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "The invoice has no lines or contains "
                            "tax lines that are not supported yet."
                        ),
                    )

                invoice_total = money(
                    Decimal(str(invoice_totals["invoice_total"]))
                )

                # Lock existing allocations while calculating the balance.
                allocated_before = conn.execute(
                    text("""
                        SELECT COALESCE(
                            SUM(AmountApplied),
                            0
                        )
                        FROM dbo.PaymentAllocations
                            WITH (UPDLOCK, HOLDLOCK)
                        WHERE InvoiceID = :invoice_id
                    """),
                    {"invoice_id": allocation.invoice_id},
                ).scalar_one()

                outstanding = money(
                    invoice_total - Decimal(str(allocated_before))
                )

                amount_applied = money(
                    Decimal(str(allocation.amount_applied))
                )

                if outstanding <= ZERO:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Invoice {invoice['InvoiceNumber']} "
                            "has no outstanding balance."
                        ),
                    )

                if amount_applied > outstanding:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Allocation for invoice "
                            f"{invoice['InvoiceNumber']} exceeds its "
                            f"outstanding balance of {outstanding}."
                        ),
                    )

                invoice_data.append({
                    "invoice_id": invoice["InvoiceID"],
                    "invoice_number": invoice["InvoiceNumber"],
                    "invoice_date": invoice["InvoiceDate"],
                    "invoice_total": invoice_total,
                    "amount_applied": amount_applied,
                })

                total_allocated += amount_applied

            total_allocated = money(total_allocated)
            payment_amount = money(Decimal(str(payment.amount)))

            if total_allocated != payment_amount:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Total invoice allocations must equal "
                        "the payment amount."
                    ),
                )

            # 4. Require one open accounting period for the payment date.
            period = conn.execute(
                text("""
                    SELECT PeriodID
                    FROM dbo.AccountingPeriods
                    WHERE StartDate <= :payment_date
                      AND EndDate >= :payment_date
                      AND Status = 'Open'
                """),
                {"payment_date": payment.payment_date},
            ).mappings().first()

            if period is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No open accounting period covers the payment date."
                    ),
                )

            # 5. Save the payment.
            payment_id = conn.execute(
                text("""
                    SET NOCOUNT ON;

                    INSERT INTO dbo.CustomerPayments (
                        CustomerID,
                        PaymentDate,
                        Amount,
                        Reference,
                        ClearingAccountID
                    )
                    VALUES (
                        :customer_id,
                        :payment_date,
                        :amount,
                        :reference,
                        :clearing_account_id
                    );

                    SELECT CONVERT(int, SCOPE_IDENTITY());
                """),
                {
                    "customer_id": payment.customer_id,
                    "payment_date": payment.payment_date,
                    "amount": payment_amount,
                    "reference": payment.reference,
                    "clearing_account_id": payment.clearing_account_id,
                },
            ).scalar_one()

            # 6. Save allocations and calculate updated invoice statuses.
            for item in invoice_data:
                conn.execute(
                    text("""
                        INSERT INTO dbo.PaymentAllocations (
                            PaymentID,
                            InvoiceID,
                            AmountApplied
                        )
                        VALUES (
                            :payment_id,
                            :invoice_id,
                            :amount_applied
                        )
                    """),
                    {
                        "payment_id": payment_id,
                        "invoice_id": item["invoice_id"],
                        "amount_applied": item["amount_applied"],
                    },
                )

                # Recalculate using all allocations, including this payment.
                applied_total = conn.execute(
                    text("""
                        SELECT COALESCE(SUM(AmountApplied), 0)
                        FROM dbo.PaymentAllocations
                        WHERE InvoiceID = :invoice_id
                    """),
                    {"invoice_id": item["invoice_id"]},
                ).scalar_one()

                outstanding = money(
                    item["invoice_total"] - Decimal(str(applied_total))
                )

                new_status = (
                    "Paid" if outstanding == ZERO else "PartiallyPaid"
                )

                conn.execute(
                    text("""
                        UPDATE dbo.SalesInvoices
                        SET InvoiceStatus = :new_status
                        WHERE InvoiceID = :invoice_id
                    """),
                    {
                        "new_status": new_status,
                        "invoice_id": item["invoice_id"],
                    },
                )

            # 7. Create the journal in Draft status.
            journal_number = f"JRN-CP-{payment_id:06d}"

            journal_id = conn.execute(
                text("""
                    SET NOCOUNT ON;

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
                        'CustomerPayment',
                        :payment_id,
                        :description,
                        'Draft',
                        :period_id
                    );

                    SELECT CONVERT(int, SCOPE_IDENTITY());
                """),
                {
                    "journal_number": journal_number,
                    "posting_date": payment.payment_date,
                    "payment_id": payment_id,
                    "description": (
                        f"Customer payment {payment_id} "
                        f"from {customer['FullName']}"
                    ),
                    "period_id": period["PeriodID"],
                },
            ).scalar_one()

            # Debit the asset account receiving the payment.
            conn.execute(
                text("""
                    INSERT INTO dbo.JournalEntryLines (
                        JournalID,
                        AccountCode,
                        Debit,
                        Credit
                    )
                    VALUES (
                        :journal_id,
                        :account_code,
                        :amount,
                        0
                    )
                """),
                {
                    "journal_id": journal_id,
                    "account_code": payment.clearing_account_id,
                    "amount": payment_amount,
                },
            )

            # Credit accounts receivable for each invoice allocation.
            for item in invoice_data:
                conn.execute(
                    text("""
                        INSERT INTO dbo.JournalEntryLines (
                            JournalID,
                            AccountCode,
                            Debit,
                            Credit,
                            CustomerID
                        )
                        VALUES (
                            :journal_id,
                            '1200',
                            0,
                            :amount,
                            :customer_id
                        )
                    """),
                    {
                        "journal_id": journal_id,
                        "amount": item["amount_applied"],
                        "customer_id": payment.customer_id,
                    },
                )

            # The database trigger checks that the journal balances.
            conn.execute(
                text("""
                    UPDATE dbo.JournalEntries
                    SET Status = 'Posted'
                    WHERE JournalID = :journal_id
                      AND Status = 'Draft'
                """),
                {"journal_id": journal_id},
            )

            # 8. Record audit events.
            conn.execute(
                text("""
                    INSERT INTO dbo.AuditLog (
                        UserID,
                        Action,
                        RecordType,
                        RecordID,
                        Reason
                    )
                    VALUES (
                        :user_id,
                        'Received',
                        'CustomerPayment',
                        :record_id,
                        :reason
                    )
                """),
                {
                    "user_id": "Development System",
                    "record_id": str(payment_id),
                    "reason": (
                        f"Received KSh {payment_amount}; "
                        f"journal {journal_number}."
                    ),
                },
            )

            conn.execute(
                text("""
                    INSERT INTO dbo.AuditLog (
                        UserID,
                        Action,
                        RecordType,
                        RecordID,
                        Reason
                    )
                    VALUES (
                        :user_id,
                        'Posted',
                        'JournalEntry',
                        :record_id,
                        :reason
                    )
                """),
                {
                    "user_id": "Development System",
                    "record_id": str(journal_id),
                    "reason": (
                        f"Customer payment {payment_id} journal posted."
                    ),
                },
            )

            result = {
                "payment_id": payment_id,
                "customer_id": payment.customer_id,
                "customer_name": customer["FullName"],
                "payment_date": payment.payment_date.isoformat(),
                "amount": str(payment_amount),
                "reference": payment.reference,
                "clearing_account_id": payment.clearing_account_id,
                "journal_id": journal_id,
                "journal_number": journal_number,
                "journal_status": "Posted",
                "allocations": [
                    {
                        "invoice_id": item["invoice_id"],
                        "invoice_number": item["invoice_number"],
                        "amount_applied": str(item["amount_applied"]),
                    }
                    for item in invoice_data
                ],
                "currency": "KES",
            }

        # This return occurs after the transaction commits.
        return result

    except HTTPException:
        raise

    except IntegrityError as exc:
        logger.exception(
            "Customer payment integrity error for customer_id=%s",
            payment.customer_id,
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "The payment conflicted with a database constraint. "
                "The transaction was rolled back."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception(
            "Customer payment failed for customer_id=%s",
            payment.customer_id,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Customer payment processing failed. "
                "The transaction was rolled back."
            ),
        ) from exc
