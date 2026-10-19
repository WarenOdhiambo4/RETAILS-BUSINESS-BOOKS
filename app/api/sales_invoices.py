
from decimal import Decimal, ROUND_HALF_UP
import logging
from re import search
logger = logging.getLogger(__name__)
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import engine
from app.schemas.sales_invoice import (
    SalesInvoiceResponse,
    SalesInvoiceDetailResponse,
)
from app.schemas.sales_invoice_create import SalesInvoiceCreate

router = APIRouter(
    prefix="/api/v1/sales-invoices",
    tags=["Sales Invoices"],
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_invoice_totals(lines: list[dict]) -> dict:
    subtotal = ZERO
    processed_lines = []

    for line in lines:
        if line.get("tax_rate_id") is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Tax calculation is not configured yet. "
                    "Confirm the tax-rate convention before using "
                    "taxed invoice lines."
                ),
            )

        quantity = Decimal(str(line["quantity"]))
        unit_price = Decimal(str(line["unit_price"]))
        discount = Decimal(str(line["discount"]))

        line_subtotal = money(quantity * unit_price - discount)
        subtotal += line_subtotal

        processed_lines.append({
            **line,
            "line_subtotal": line_subtotal,
        })

    subtotal = money(subtotal)

    return {
        "lines": processed_lines,
        "subtotal": subtotal,
        "tax_total": ZERO,
        "invoice_total": subtotal,
    }


@router.get("", response_model=list[SalesInvoiceResponse])
def list_sales_invoices(
    search: str | None = Query(default=None, max_length=100),
    invoice_status: str | None = Query(default=None, max_length=60),
    customer_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    
    sql = text("""
    SELECT
        i.InvoiceID AS invoice_id,
        i.InvoiceNumber AS invoice_number,
        i.CustomerID AS customer_id,
        c.FullName AS customer_name,
        i.InvoiceDate AS invoice_date,
        i.DueDate AS due_date,
        i.InvoiceStatus AS invoice_status,

        COALESCE(
            t.subtotal,
            CAST(0 AS DECIMAL(19, 2))
        ) AS subtotal,

        COALESCE(t.tax_line_count, 0) AS tax_line_count,

        COALESCE(
            payments.amount_paid,
            CAST(0 AS DECIMAL(19, 2))
        ) AS amount_paid,

        COALESCE(
            t.subtotal,
            CAST(0 AS DECIMAL(19, 2))
        ) - COALESCE(
            payments.amount_paid,
            CAST(0 AS DECIMAL(19, 2))
        ) AS outstanding_balance

    FROM dbo.SalesInvoices AS i

    INNER JOIN dbo.Customers AS c
        ON c.CustomerID = i.CustomerID

    OUTER APPLY (
        SELECT
            SUM(
                ROUND(
                    l.Quantity * l.UnitPrice - l.Discount,
                    2
                )
            ) AS subtotal,

            SUM(
                CASE
                    WHEN l.TaxRateID IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS tax_line_count

        FROM dbo.SalesInvoiceLines AS l
        WHERE l.InvoiceID = i.InvoiceID
    ) AS t

    OUTER APPLY (
        SELECT
            SUM(pa.AmountApplied) AS amount_paid
        FROM dbo.PaymentAllocations AS pa
        WHERE pa.InvoiceID = i.InvoiceID
          AND EXISTS (
              SELECT 1
              FROM dbo.JournalEntries AS je
              WHERE je.SourceType = 'CustomerPayment'
                AND je.SourceID = pa.PaymentID
                AND je.Status = 'Posted'
          )
    ) AS payments

    WHERE
        (
            :search IS NULL
            OR i.InvoiceNumber LIKE N'%' + :search + N'%'
            OR c.FullName LIKE N'%' + :search + N'%'
        )
        AND (
            :invoice_status IS NULL
            OR i.InvoiceStatus = :invoice_status
        )
        AND (
            :customer_id IS NULL
            OR i.CustomerID = :customer_id
        )

    ORDER BY i.InvoiceDate DESC, i.InvoiceID DESC
    OFFSET :offset ROWS
    FETCH NEXT :limit ROWS ONLY
""")


    try:
        with engine.connect() as connection:
            rows = connection.execute(
                sql,
                {
                    "search": search,
                    "invoice_status": invoice_status,
                    "customer_id": customer_id,
                    "limit": limit,
                    "offset": offset,
                },
            ).mappings().all()

        results = []

        for row in rows:
            item = dict(row)

            if item.pop("tax_line_count") or 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Tax calculation is not configured for invoices "
                        "with assigned tax rates."
                    ),
                )

            item["subtotal"] = money(Decimal(str(item["subtotal"])))
            item["tax_total"] = ZERO
            item["invoice_total"] = item["subtotal"]

            item["amount_paid"] = money(
             Decimal(str(item["amount_paid"]))
)
            item["outstanding_balance"] = money(
                 Decimal(str(item["outstanding_balance"]))
)

            results.append(item)

        return results

    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve sales invoices.",
        )


@router.get(
    "/{invoice_id}",
    response_model=SalesInvoiceDetailResponse,
)
def get_sales_invoice(invoice_id: int):
    header_sql = text("""
        SELECT
            i.InvoiceID AS invoice_id,
            i.InvoiceNumber AS invoice_number,
            i.CustomerID AS customer_id,
            c.FullName AS customer_name,
            i.InvoiceDate AS invoice_date,
            i.DueDate AS due_date,
            i.InvoiceStatus AS invoice_status
        FROM dbo.SalesInvoices AS i
        INNER JOIN dbo.Customers AS c
            ON c.CustomerID = i.CustomerID
        WHERE i.InvoiceID = :invoice_id
    """)

    lines_sql = text("""
        SELECT
            l.InvoiceLineID AS invoice_line_id,
            l.ProductID AS product_id,
            p.ProductName AS product_name,
            l.Quantity AS quantity,
            l.UnitPrice AS unit_price,
            l.Discount AS discount,
            l.TaxRateID AS tax_rate_id
        FROM dbo.SalesInvoiceLines AS l
        INNER JOIN dbo.Products AS p
            ON p.ProductID = l.ProductID
        WHERE l.InvoiceID = :invoice_id
        ORDER BY l.InvoiceLineID
    """)

    try:
        with engine.connect() as connection:
            header = connection.execute(
                header_sql,
                {"invoice_id": invoice_id},
            ).mappings().first()

            if header is None:
                raise HTTPException(
                    status_code=404,
                    detail="Sales invoice not found.",
                )

            lines = connection.execute(
                lines_sql,
                {"invoice_id": invoice_id},
            ).mappings().all()

        totals = calculate_invoice_totals(
            [dict(line) for line in lines]
        )

        return {
            **dict(header),
            "subtotal": totals["subtotal"],
            "tax_total": totals["tax_total"],
            "invoice_total": totals["invoice_total"],
            "lines": totals["lines"],
        }

    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve the sales invoice.",
        )


@router.post(
    "",
    response_model=SalesInvoiceDetailResponse,
    status_code=201,
)
def create_sales_invoice(invoice: SalesInvoiceCreate):
    customer_sql = text("""
        SELECT FullName
        FROM dbo.Customers
        WHERE CustomerID = :customer_id
    """)

    product_sql = text("""
        SELECT ProductName
        FROM dbo.Products
        WHERE ProductID = :product_id
    """)

    header_sql = text("""
        INSERT INTO dbo.SalesInvoices (
            InvoiceNumber,
            CustomerID,
            InvoiceDate,
            DueDate,
            InvoiceStatus
        )
        OUTPUT
            INSERTED.InvoiceID AS invoice_id,
            INSERTED.InvoiceNumber AS invoice_number,
            INSERTED.CustomerID AS customer_id,
            INSERTED.InvoiceDate AS invoice_date,
            INSERTED.DueDate AS due_date,
            INSERTED.InvoiceStatus AS invoice_status
        VALUES (
            :invoice_number,
            :customer_id,
            :invoice_date,
            :due_date,
            'Draft'
        )
    """)

    line_sql = text("""
        INSERT INTO dbo.SalesInvoiceLines (
            InvoiceID,
            ProductID,
            Quantity,
            UnitPrice,
            Discount,
            TaxRateID
        )
        OUTPUT
            INSERTED.InvoiceLineID AS invoice_line_id,
            INSERTED.ProductID AS product_id,
            INSERTED.Quantity AS quantity,
            INSERTED.UnitPrice AS unit_price,
            INSERTED.Discount AS discount,
            INSERTED.TaxRateID AS tax_rate_id
        VALUES (
            :invoice_id,
            :product_id,
            :quantity,
            :unit_price,
            :discount,
            :tax_rate_id
        )
    """)

    try:
        # Reject tax-bearing lines until the tax convention is confirmed.
        if any(line.tax_rate_id is not None for line in invoice.lines):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Tax rates are not configured for calculation yet. "
                    "Submit null tax_rate_id until the tax convention "
                    "has been verified."
                ),
            )

        with engine.begin() as connection:
            customer_name = connection.execute(
                customer_sql,
                {"customer_id": invoice.customer_id},
            ).scalar_one_or_none()

            if customer_name is None:
                raise HTTPException(
                    status_code=422,
                    detail="The selected customer does not exist.",
                )

            product_names = {}

            for line in invoice.lines:
                product_name = connection.execute(
                    product_sql,
                    {"product_id": line.product_id},
                ).scalar_one_or_none()

                if product_name is None:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Product ID {line.product_id} does not exist."
                        ),
                    )

                product_names[line.product_id] = product_name

            header = connection.execute(
                header_sql,
                {
                    "invoice_number": invoice.invoice_number,
                    "customer_id": invoice.customer_id,
                    "invoice_date": invoice.invoice_date,
                    "due_date": invoice.due_date,
                },
            ).mappings().one()

            saved_lines = []

            for line in invoice.lines:
                inserted = connection.execute(
                    line_sql,
                    {
                        "invoice_id": header["invoice_id"],
                        **line.model_dump(),
                    },
                ).mappings().one()

                saved_lines.append({
                    **dict(inserted),
                    "product_name": product_names[line.product_id],
                })

            totals = calculate_invoice_totals(saved_lines)

            return {
                **dict(header),
                "customer_name": customer_name,
                "subtotal": totals["subtotal"],
                "tax_total": totals["tax_total"],
                "invoice_total": totals["invoice_total"],
                "lines": totals["lines"],
            }

    except HTTPException:
        raise
    except IntegrityError as exc:
        message = str(exc.orig).lower()

        if (
            "unique" in message
            or "duplicate" in message
            or "2601" in message
            or "2627" in message
        ):
            raise HTTPException(
                status_code=409,
                detail="That invoice number already exists.",
            )

        raise HTTPException(
            status_code=409,
            detail="Invoice conflicts with a database constraint.",
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Unable to create the sales invoice.",
        )



@router.post("/{invoice_id}/post")
def post_sales_invoice(
    invoice_id: int,
    warehouse_id: int = Query(..., gt=0),
):
    """
    Post a draft sales invoice.

    This endpoint:
    - Validates the invoice and accounting period.
    - Checks stock in the selected warehouse.
    - Calculates perpetual weighted-average inventory cost.
    - Creates inventory movements and a balanced journal.
    - Issues the invoice and records audit events.
    - Commits all changes in one transaction.
    """

    from decimal import Decimal

    inbound_types = (
        "Receipt",
        "SalesReturn",
        "AdjustmentIn",
        "TransferIn",
    )
    outbound_types = (
        "Sale",
        "SupplierReturn",
        "AdjustmentOut",
        "TransferOut",
    )

    try:
        with engine.begin() as conn:
            # Protect the invoice from simultaneous posting.
           

            invoice = conn.execute(
                text("""
                    SELECT
                        InvoiceID,
                        InvoiceNumber,
                        CustomerID,
                        InvoiceDate,
                        DueDate,
                        InvoiceStatus
                    FROM dbo.SalesInvoices
                        WITH (UPDLOCK, HOLDLOCK)
                    WHERE InvoiceID = :invoice_id
                """),
                {"invoice_id": invoice_id},
            ).mappings().first()

            if invoice is None:
                raise HTTPException(
                    status_code=404,
                    detail="Sales invoice not found.",
                )

            if invoice["InvoiceStatus"] != "Draft":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Only Draft invoices can be posted. "
                        f"Current status: {invoice['InvoiceStatus']}."
                    ),
                )

            # The invoice date must belong to an open accounting period.
            period = conn.execute(
                text("""
                    SELECT PeriodID
                    FROM dbo.AccountingPeriods
                    WHERE StartDate <= :invoice_date
                      AND EndDate >= :invoice_date
                      AND Status = 'Open'
                """),
                {"invoice_date": invoice["InvoiceDate"]},
            ).mappings().first()

            if period is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "No open accounting period covers "
                        "the invoice date."
                    ),
                )

            # Confirm that the selected warehouse exists.
            warehouse = conn.execute(
                text("""
                    SELECT WarehouseID
                    FROM dbo.Warehouses
                    WHERE WarehouseID = :warehouse_id
                """),
                {"warehouse_id": warehouse_id},
            ).first()

            if warehouse is None:
                raise HTTPException(
                    status_code=404,
                    detail="Warehouse not found.",
                )

            # Lock and load all invoice lines.
            lines = conn.execute(
                text("""
                    SELECT
                        InvoiceLineID,
                        ProductID,
                        Quantity,
                        UnitPrice,
                        Discount,
                        TaxRateID
                    FROM dbo.SalesInvoiceLines
                        WITH (UPDLOCK, HOLDLOCK)
                    WHERE InvoiceID = :invoice_id
                    ORDER BY InvoiceLineID
                """),
                {"invoice_id": invoice_id},
            ).mappings().all()

            if not lines:
                raise HTTPException(
                    status_code=409,
                    detail="An invoice must contain at least one line.",
                )

            if any(line["TaxRateID"] is not None for line in lines):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Tax calculation is not configured. "
                        "This invoice cannot be posted with tax lines."
                    ),
                )

            # Calculate invoice revenue using the existing helper.
            invoice_lines = [
                {
                    "product_id": line["ProductID"],
                    "quantity": line["Quantity"],
                    "unit_price": line["UnitPrice"],
                    "discount": line["Discount"],
                    "tax_rate_id": line["TaxRateID"],
                }
                for line in lines
            ]

            totals = calculate_invoice_totals(invoice_lines)
            invoice_total = totals["invoice_total"]

            if invoice_total <= ZERO:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The invoice total must be greater than zero "
                        "before posting."
                    ),
                )

            # Aggregate quantities by product. This prevents duplicate
            # invoice lines for the same product bypassing stock checks.
            quantities_by_product = {}

            for line in lines:
                product_id = line["ProductID"]
                quantity = Decimal(str(line["Quantity"]))

                quantities_by_product[product_id] = (
                    quantities_by_product.get(product_id, Decimal("0"))
                    + quantity
                )

            # Validate the products before changing the ledger.
            product_ids = list(quantities_by_product)

            product_rows = conn.execute(
                text("""
                    SELECT ProductID, ProductName, Active
                    FROM dbo.Products
                    WHERE ProductID IN :product_ids
                """.replace("IN :product_ids", "IN (SELECT value FROM OPENJSON(:product_json))")),
                {
                    "product_json": __import__("json").dumps(product_ids)
                },
            ).mappings().all()

            products = {
                row["ProductID"]: row for row in product_rows
            }

            if len(products) != len(product_ids):
                raise HTTPException(
                    status_code=409,
                    detail="One or more invoice products no longer exist.",
                )

            if any(not products[pid]["Active"] for pid in product_ids):
                raise HTTPException(
                    status_code=409,
                    detail="An inactive product cannot be sold.",
                )

            inventory_cost_total = ZERO
            inventory_movements = []

            for product_id, sale_quantity in quantities_by_product.items():
                # Read the ledger under serializable isolation.
                # HOLDLOCK retains locks until this transaction finishes.
                movements = conn.execute(
                    text("""
                        SELECT
                            MovementType,
                            Quantity,
                            UnitCost
                        FROM dbo.InventoryMovements
                            WITH (UPDLOCK, HOLDLOCK)
                        WHERE ProductID = :product_id
                          AND WarehouseID = :warehouse_id
                    """),
                    {
                        "product_id": product_id,
                        "warehouse_id": warehouse_id,
                    },
                ).mappings().all()

                on_hand = Decimal("0")
                carrying_value = Decimal("0")

                for movement in movements:
                    quantity = Decimal(str(movement["Quantity"]))
                    unit_cost = Decimal(str(movement["UnitCost"]))
                    movement_type = movement["MovementType"]

                    if movement_type in inbound_types:
                        on_hand += quantity
                        carrying_value += quantity * unit_cost

                    elif movement_type in outbound_types:
                        on_hand -= quantity
                        carrying_value -= quantity * unit_cost

                if on_hand < 0:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Inventory ledger is inconsistent for "
                            f"product {product_id} in warehouse "
                            f"{warehouse_id}: calculated stock is negative."
                        ),
                    )

                if sale_quantity > on_hand:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Insufficient stock for "
                            f"{products[product_id]['ProductName']}. "
                            f"Available: {on_hand}; "
                            f"requested: {sale_quantity}."
                        ),
                    )

                if on_hand == 0:
                    if sale_quantity > 0:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Product {product_id} has no stock "
                                "available for costing."
                            ),
                        )

                    average_cost = ZERO
                else:
                    if carrying_value < 0:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Inventory carrying value is negative "
                                f"for product {product_id}. "
                                "Resolve the ledger before posting."
                            ),
                        )

                    average_cost = money(carrying_value / on_hand)

                line_cost = money(sale_quantity * average_cost)
                inventory_cost_total += line_cost

                inventory_movements.append({
                    "product_id": product_id,
                    "quantity": sale_quantity,
                    "unit_cost": average_cost,
                    "line_cost": line_cost,
                })

            inventory_cost_total = money(inventory_cost_total)

            # A predictable number per invoice prevents a second journal
            # from being created for the same invoice.
            journal_number = f"JRN-SI-{invoice_id:06d}"

            existing_journal = conn.execute(
                text("""
                    SELECT JournalID
                    FROM dbo.JournalEntries
                    WHERE JournalNumber = :journal_number
                """),
                {"journal_number": journal_number},
            ).first()

            if existing_journal:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A journal already exists for this invoice. "
                        "Investigate the existing journal before retrying."
                    ),
                )

            # Create the journal as Draft. The database trigger requires
            # its lines to exist and balance before it becomes Posted.
            
            
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
                        'SalesInvoice',
                        :invoice_id,
                        :description,
                        'Draft',
                        :period_id
                    );

                    SELECT CONVERT(int, SCOPE_IDENTITY()) AS JournalID;
                """),
                {
                    "journal_number": journal_number,
                    "posting_date": invoice["InvoiceDate"],
                    "invoice_id": invoice_id,
                    "description": (
                        f"Sales invoice {invoice['InvoiceNumber']}"
                    ),
                    "period_id": period["PeriodID"],
                },
            ).scalar_one()



            # Accounts receivable: debit.
            conn.execute(
                text("""
                    INSERT INTO dbo.JournalEntryLines (
                        JournalID, AccountCode, Debit, Credit, CustomerID
                    )
                    VALUES (
                        :journal_id, '1200', :amount, 0, :customer_id
                    )
                """),
                {
                    "journal_id": journal_id,
                    "amount": invoice_total,
                    "customer_id": invoice["CustomerID"],
                },
            )

            # Sales revenue: credit.
            conn.execute(
                text("""
                    INSERT INTO dbo.JournalEntryLines (
                        JournalID, AccountCode, Debit, Credit
                    )
                    VALUES (
                        :journal_id, '4100', 0, :amount
                    )
                """),
                {
                    "journal_id": journal_id,
                    "amount": invoice_total,
                },
            )

            # Record cost of goods sold and inventory reduction.
            if inventory_cost_total > 0:
                conn.execute(
                    text("""
                        INSERT INTO dbo.JournalEntryLines (
                            JournalID, AccountCode, Debit, Credit
                        )
                        VALUES (
                            :journal_id, '5100', :amount, 0
                        )
                    """),
                    {
                        "journal_id": journal_id,
                        "amount": inventory_cost_total,
                    },
                )

                conn.execute(
                    text("""
                        INSERT INTO dbo.JournalEntryLines (
                            JournalID, AccountCode, Debit, Credit
                        )
                        VALUES (
                            :journal_id, '1300', 0, :amount
                        )
                    """),
                    {
                        "journal_id": journal_id,
                        "amount": inventory_cost_total,
                    },
                )

            # Record one stock-out movement per product.
            for movement in inventory_movements:
                conn.execute(
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
                            'Sale',
                            :quantity,
                            :unit_cost,
                            'SalesInvoice',
                            :invoice_id
                        )
                    """),
                    {
                        "product_id": movement["product_id"],
                        "warehouse_id": warehouse_id,
                        "quantity": movement["quantity"],
                        "unit_cost": movement["unit_cost"],
                        "invoice_id": invoice_id,
                    },
                )

            # Post only after all journal lines have been inserted.
            conn.execute(
                text("""
                    UPDATE dbo.JournalEntries
                    SET Status = 'Posted'
                    WHERE JournalID = :journal_id
                      AND Status = 'Draft'
                """),
                {"journal_id": journal_id},
            )

            # Issue the invoice after its inventory and journal entries
            # have been recorded in the same transaction.
            conn.execute(
                text("""
                    UPDATE dbo.SalesInvoices
                    SET InvoiceStatus = 'Issued'
                    WHERE InvoiceID = :invoice_id
                      AND InvoiceStatus = 'Draft'
                """),
                {"invoice_id": invoice_id},
            )

            # Development identity only. Replace with the authenticated
            # user's identity when authentication is implemented.
            conn.execute(
                text("""
                    INSERT INTO dbo.AuditLog (
                        UserID, Action, RecordType, RecordID, Reason
                    )
                    VALUES (
                        :user_id, 'Posted', 'SalesInvoice',
                        :record_id, :reason
                    )
                """),
                {
                    "user_id": "Development System",
                    "record_id": str(invoice_id),
                    "reason": (
                        f"Invoice issued; journal {journal_number}; "
                        f"warehouse {warehouse_id}."
                    ),
                },
            )

            conn.execute(
                text("""
                    INSERT INTO dbo.AuditLog (
                        UserID, Action, RecordType, RecordID, Reason
                    )
                    VALUES (
                        :user_id, 'Posted', 'JournalEntry',
                        :record_id, :reason
                    )
                """),
                {
                    "user_id": "Development System",
                    "record_id": str(journal_id),
                    "reason": (
                        f"Sales invoice {invoice['InvoiceNumber']} "
                        "journal posted."
                    ),
                },
            )

            result = {
                "invoice_id": invoice_id,
                "invoice_number": invoice["InvoiceNumber"],
                "invoice_status": "Issued",
                "warehouse_id": warehouse_id,
                "journal_id": journal_id,
                "journal_number": journal_number,
                "journal_status": "Posted",
                "subtotal": str(invoice_total),
                "tax_total": "0.00",
                "invoice_total": str(invoice_total),
                "inventory_cost": str(inventory_cost_total),
                "gross_profit": str(
                    money(invoice_total - inventory_cost_total)
                ),
                "currency": "KES",
                "inventory_movements": [
                    {
                        "product_id": item["product_id"],
                        "quantity": str(item["quantity"]),
                        "unit_cost": str(item["unit_cost"]),
                        "total_cost": str(item["line_cost"]),
                    }
                    for item in inventory_movements
                ],
            }

        # Reached only after the SQL transaction commits successfully.
        return result

    except HTTPException:
        raise

    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "The invoice could not be posted because a database "
                "constraint was violated. Check the server log."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception(
            "Sales invoice posting failed for invoice_id=%s",
            invoice_id,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Sales invoice posting failed. The transaction was "
                "rolled back. Check the server log for details."
            ),
        ) from exc


@router.get("/{invoice_id}/balance")
def get_invoice_balance(invoice_id: int):
    with engine.connect() as connection:
        # 1. Retrieve the invoice header.
        invoice = connection.execute(
            text("""
                SELECT
                    InvoiceID,
                    InvoiceNumber,
                    CustomerID,
                    InvoiceDate,
                    DueDate,
                    InvoiceStatus
                FROM dbo.SalesInvoices
                WHERE InvoiceID = :invoice_id
            """),
            {"invoice_id": invoice_id}
        ).mappings().first()

        if invoice is None:
            raise HTTPException(
                status_code=404,
                detail="Sales invoice not found."
            )

        # 2. Calculate the invoice total from its lines.
        totals = connection.execute(
            text("""
                SELECT
                    COUNT(*) AS LineCount,
                    SUM(
                        ROUND(
                            Quantity * UnitPrice - Discount,
                            2
                        )
                    ) AS InvoiceTotal,
                    SUM(
                        CASE
                            WHEN TaxRateID IS NOT NULL THEN 1
                            ELSE 0
                        END
                    ) AS TaxLineCount
                FROM dbo.SalesInvoiceLines
                WHERE InvoiceID = :invoice_id
            """),
            {"invoice_id": invoice_id}
        ).mappings().one()

        if totals["LineCount"] == 0:
            invoice_total = money(Decimal("0.00"))
        else:
            invoice_total = money(
                Decimal(str(totals["InvoiceTotal"]))
            )

        if totals["TaxLineCount"] > 0:
            raise HTTPException(
                status_code=409,
                detail="Tax calculation is not configured yet."
            )

        # 3. Calculate the amount allocated from customer payments.
        amount_paid = connection.execute(
            text("""
        SELECT COALESCE(SUM(pa.AmountApplied), 0)
        FROM dbo.PaymentAllocations AS pa
        WHERE pa.InvoiceID = :invoice_id
          AND EXISTS (
              SELECT 1
              FROM dbo.JournalEntries AS je
              WHERE je.SourceType = 'CustomerPayment'
                AND je.SourceID = pa.PaymentID
                AND je.Status = 'Posted'
          )
    """),
    {"invoice_id": invoice_id}
).scalar_one()

        amount_paid = money(Decimal(str(amount_paid)))
        outstanding = money(invoice_total - amount_paid)

        # 4. Return the invoice balance.
        return {
            "invoice_id": invoice["InvoiceID"],
            "invoice_number": invoice["InvoiceNumber"],
            "customer_id": invoice["CustomerID"],
            "invoice_date": invoice["InvoiceDate"],
            "due_date": invoice["DueDate"],
            "invoice_status": invoice["InvoiceStatus"],
            "currency": "KES",
            "invoice_total": invoice_total,
            "amount_paid": amount_paid,
            "outstanding_balance": outstanding
        }
