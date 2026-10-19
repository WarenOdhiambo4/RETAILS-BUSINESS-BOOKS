
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SupplierBillLineCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=19, decimal_places=4)
    unit_cost: Decimal = Field(ge=0, max_digits=19, decimal_places=2)
    tax_amount: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        max_digits=19,
        decimal_places=2,
    )


class SupplierBillCreate(BaseModel):
    supplier_id: int = Field(gt=0)
    supplier_invoice_no: str = Field(min_length=1, max_length=200)
    bill_date: date
    due_date: date
    lines: list[SupplierBillLineCreate] = Field(min_length=1)

    model_config = ConfigDict(str_strip_whitespace=True)
