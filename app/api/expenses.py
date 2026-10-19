from datetime import date
import logging

from fastapi import APIRouter, HTTPException, Path, Query, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.schemas.expenses import ExpenseCreate, ExpenseRead, ExpenseUpdate
from app.database import engine
from app.schemas.expenses import ExpenseCreate, ExpenseRead


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/expenses",
    tags=["Expenses"],
)


EXPENSE_SELECT = """
    SELECT
        e.ExpenseID AS expense_id,
        e.ExpenseDate AS expense_date,
        e.AccountCode AS account_code,
        e.SupplierID AS supplier_id,
        s.FullName AS supplier_name,
        e.Amount AS amount,
        e.TaxAmount AS tax_amount,
        CAST(
            e.Amount + e.TaxAmount AS DECIMAL(19, 4)
        ) AS total_amount,
        e.PaymentStatus AS payment_status
    FROM dbo.Expenses AS e
    LEFT JOIN dbo.Suppliers AS s
        ON s.SupplierID = e.SupplierID
"""


@router.get("", response_model=list[ExpenseRead])
def list_expenses(
    supplier_id: int | None = Query(default=None, ge=1),
    account_code: str | None = Query(default=None, max_length=40),
    payment_status: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    if date_from is not None and date_to is not None:
        if date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from cannot be later than date_to.",
            )

    allowed_statuses = {"Paid", "PartiallyPaid", "Unpaid"}
    if payment_status is not None:
        if payment_status not in allowed_statuses:
            raise HTTPException(
                status_code=422,
                detail=(
                    "payment_status must be Paid, "
                    "PartiallyPaid, or Unpaid."
                ),
            )

    query = EXPENSE_SELECT
    conditions = []
    params = {"limit": limit, "offset": offset}

    if supplier_id is not None:
        conditions.append("e.SupplierID = :supplier_id")
        params["supplier_id"] = supplier_id

    if account_code is not None:
        conditions.append("e.AccountCode = :account_code")
        params["account_code"] = account_code.strip()

    if payment_status is not None:
        conditions.append("e.PaymentStatus = :payment_status")
        params["payment_status"] = payment_status

    if date_from is not None:
        conditions.append("e.ExpenseDate >= :date_from")
        params["date_from"] = date_from

    if date_to is not None:
        conditions.append("e.ExpenseDate <= :date_to")
        params["date_to"] = date_to

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += """
        ORDER BY e.ExpenseDate DESC, e.ExpenseID DESC
        OFFSET :offset ROWS
        FETCH NEXT :limit ROWS ONLY
    """

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(query),
                params,
            ).mappings().all()

            return [dict(row) for row in rows]

    except SQLAlchemyError as exc:
        logger.exception("Failed to list expenses")
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve expenses.",
        ) from exc


@router.get("/{expense_id}", response_model=ExpenseRead)
def get_expense(
    expense_id: int = Path(ge=1),
):
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    EXPENSE_SELECT
                    + " WHERE e.ExpenseID = :expense_id"
                ),
                {"expense_id": expense_id},
            ).mappings().first()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Expense not found.",
                )

            return dict(row)

    except HTTPException:
        raise

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to retrieve expense %s",
            expense_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve expense.",
        ) from exc


@router.post(
    "",
    response_model=ExpenseRead,
    status_code=status.HTTP_201_CREATED,
)
def create_expense(payload: ExpenseCreate):
    account_code = payload.account_code.strip()

    if not account_code:
        raise HTTPException(
            status_code=422,
            detail="Account code cannot be empty.",
        )

    try:
        with engine.begin() as connection:
            # Only accounts classified as expenses can be selected.
            account = connection.execute(
                text("""
                    SELECT AccountCode
                    FROM dbo.ChartOfAccounts
                    WHERE AccountCode = :account_code
                      AND AccountType = 'Expense'
                """),
                {"account_code": account_code},
            ).first()

            if account is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Account code does not exist or is not "
                        "classified as an expense account."
                    ),
                )

            # Supplier is optional, but must exist if provided.
            if payload.supplier_id is not None:
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

            # New expenses start unpaid. Payment must be recorded
            # through a separate, properly controlled workflow.
            connection.execute(
                text("""
                    INSERT INTO dbo.Expenses (
                        ExpenseDate,
                        AccountCode,
                        SupplierID,
                        Amount,
                        TaxAmount,
                        PaymentStatus
                    )
                    VALUES (
                        :expense_date,
                        :account_code,
                        :supplier_id,
                        :amount,
                        :tax_amount,
                        'Unpaid'
                    )
                """),
                {
                    "expense_date": payload.expense_date,
                    "account_code": account_code,
                    "supplier_id": payload.supplier_id,
                    "amount": payload.amount,
                    "tax_amount": payload.tax_amount,
                },
            )

            row = connection.execute(
                text(
                    EXPENSE_SELECT
                    + """
                        WHERE e.AccountCode = :account_code
                          AND e.ExpenseDate = :expense_date
                        ORDER BY e.ExpenseID DESC
                    """
                ),
                {
                    "account_code": account_code,
                    "expense_date": payload.expense_date,
                },
            ).mappings().first()

            result = dict(row)

        return result

    except HTTPException:
        raise

    except IntegrityError as exc:
        logger.warning(
            "Expense creation violated a database constraint."
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "The expense conflicts with a database constraint "
                "or a referenced record."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception("Failed to create expense")
        raise HTTPException(
            status_code=500,
            detail="Unable to create expense.",
        ) from exc


@router.patch(
    "/{expense_id}",
    response_model=ExpenseRead,
)
def update_expense(
    payload: ExpenseUpdate,
    expense_id: int = Path(ge=1),
):
    changes = payload.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one field to update.",
        )

    # These fields cannot be NULL in dbo.Expenses.
    required_fields = {
        "expense_date",
        "account_code",
        "amount",
        "tax_amount",
    }

    for field in required_fields.intersection(changes):
        if changes[field] is None:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null.",
            )

    if "account_code" in changes:
        changes["account_code"] = changes["account_code"].strip()

        if not changes["account_code"]:
            raise HTTPException(
                status_code=422,
                detail="Account code cannot be empty.",
            )

    allowed_columns = {
        "expense_date": "ExpenseDate",
        "account_code": "AccountCode",
        "supplier_id": "SupplierID",
        "amount": "Amount",
        "tax_amount": "TaxAmount",
    }

    try:
        with engine.begin() as connection:
            expense = connection.execute(
                text("""
                    SELECT
                        ExpenseID,
                        ExpenseDate,
                        AccountCode,
                        SupplierID,
                        Amount,
                        TaxAmount,
                        PaymentStatus
                    FROM dbo.Expenses WITH (UPDLOCK, HOLDLOCK)
                    WHERE ExpenseID = :expense_id
                """),
                {"expense_id": expense_id},
            ).mappings().first()

            if expense is None:
                raise HTTPException(
                    status_code=404,
                    detail="Expense not found.",
                )

            if expense["PaymentStatus"] != "Unpaid":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Only unpaid expenses can be edited. "
                        "This expense has a different payment status."
                    ),
                )

            journal = connection.execute(
                text("""
                    SELECT TOP (1) JournalID, Status
                    FROM dbo.JournalEntries
                    WHERE SourceType = 'Expense'
                      AND SourceID = :expense_id
                """),
                {"expense_id": expense_id},
            ).first()

            if journal is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This expense is linked to a journal entry "
                        "and cannot be edited through this endpoint."
                    ),
                )

            if "account_code" in changes:
                account = connection.execute(
                    text("""
                        SELECT AccountCode
                        FROM dbo.ChartOfAccounts
                        WHERE AccountCode = :account_code
                          AND AccountType = 'Expense'
                    """),
                    {"account_code": changes["account_code"]},
                ).first()

                if account is None:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "Account code does not exist or is not "
                            "classified as an expense account."
                        ),
                    )

            if (
                "supplier_id" in changes
                and changes["supplier_id"] is not None
            ):
                supplier = connection.execute(
                    text("""
                        SELECT SupplierID
                        FROM dbo.Suppliers
                        WHERE SupplierID = :supplier_id
                    """),
                    {"supplier_id": changes["supplier_id"]},
                ).first()

                if supplier is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Supplier not found.",
                    )

            # Build assignments only from the fixed column whitelist.
            assignments = ", ".join(
                f"{allowed_columns[field]} = :{field}"
                for field in changes
            )

            params = dict(changes)
            params["expense_id"] = expense_id

            connection.execute(
                text(f"""
                    UPDATE dbo.Expenses
                    SET {assignments}
                    WHERE ExpenseID = :expense_id
                """),
                params,
            )

            changed_fields = ", ".join(sorted(changes))

            connection.execute(
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
                        'Updated',
                        'Expense',
                        :record_id,
                        :reason
                    )
                """),
                {
                    "user_id": "Development System",
                    "record_id": str(expense_id),
                    "reason": (
                        "Expense updated. Fields: "
                        + changed_fields
                    ),
                },
            )

            updated = connection.execute(
                text(
                    EXPENSE_SELECT
                    + " WHERE e.ExpenseID = :expense_id"
                ),
                {"expense_id": expense_id},
            ).mappings().one()

            result = dict(updated)

        return result

    except HTTPException:
        raise

    except IntegrityError as exc:
        logger.warning(
            "Expense update violated a database constraint."
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "The update conflicts with a database constraint "
                "or a referenced record."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to update expense %s",
            expense_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to update expense.",
        ) from exc


@router.delete(
    "/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_expense(
    expense_id: int = Path(ge=1),
):
    try:
        with engine.begin() as connection:
            expense = connection.execute(
                text("""
                    SELECT
                        ExpenseID,
                        PaymentStatus,
                        Amount,
                        TaxAmount
                    FROM dbo.Expenses WITH (UPDLOCK, HOLDLOCK)
                    WHERE ExpenseID = :expense_id
                """),
                {"expense_id": expense_id},
            ).mappings().first()

            if expense is None:
                raise HTTPException(
                    status_code=404,
                    detail="Expense not found.",
                )

            if expense["PaymentStatus"] != "Unpaid":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Only unpaid expenses can be deleted. "
                        "Paid or partially paid expenses must be "
                        "handled through a controlled accounting process."
                    ),
                )

            journal = connection.execute(
                text("""
                    SELECT TOP (1) JournalID, Status
                    FROM dbo.JournalEntries
                    WHERE SourceType = 'Expense'
                      AND SourceID = :expense_id
                """),
                {"expense_id": expense_id},
            ).first()

            if journal is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This expense is linked to a journal entry "
                        "and cannot be deleted."
                    ),
                )

            connection.execute(
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
                        'Deleted',
                        'Expense',
                        :record_id,
                        :reason
                    )
                """),
                {
                    "user_id": "Development System",
                    "record_id": str(expense_id),
                    "reason": (
                        "Unpaid, unposted expense deleted. "
                        f"Amount: {expense['Amount']}; "
                        f"Tax: {expense['TaxAmount']}."
                    ),
                },
            )

            connection.execute(
                text("""
                    DELETE FROM dbo.Expenses
                    WHERE ExpenseID = :expense_id
                """),
                {"expense_id": expense_id},
            )

    except HTTPException:
        raise

    except IntegrityError as exc:
        logger.warning(
            "Expense deletion violated a database constraint."
        )
        raise HTTPException(
            status_code=409,
            detail=(
                "The expense cannot be deleted because it is "
                "referenced by another record."
            ),
        ) from exc

    except SQLAlchemyError as exc:
        logger.exception(
            "Failed to delete expense %s",
            expense_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Unable to delete expense.",
        ) from exc
