
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class ExpenseCreate(BaseModel):
    expense_date: date
    account_code: str = Field(min_length=1, max_length=40)
    supplier_id: int | None = Field(default=None, ge=1)

    # Amount is the expense before tax.
    amount: Decimal = Field(
        gt=Decimal("0"),
        max_digits=19,
        decimal_places=4,
    )

    # Tax is added to Amount to calculate the total payable.
    tax_amount: Decimal = Field(
        default=Decimal("0.0000"),
        ge=Decimal("0"),
        max_digits=19,
        decimal_places=4,
    )


class ExpenseRead(BaseModel):
    expense_id: int
    expense_date: date
    account_code: str
    supplier_id: int | None
    supplier_name: str | None
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    payment_status: str

class ExpenseUpdate(BaseModel):
    expense_date: date | None = None
    account_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
    )
    supplier_id: int | None = Field(default=None, ge=1)
    amount: Decimal | None = Field(
        default=None,
        gt=Decimal("0"),
        max_digits=19,
        decimal_places=4,
    )
    tax_amount: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        max_digits=19,
        decimal_places=4,
    )
