
from decimal import Decimal

from pydantic import BaseModel, Field


class SupplierBillReceiptMatchCreate(BaseModel):
    bill_line_id: int = Field(gt=0)
    receipt_line_id: int = Field(gt=0)
    matched_quantity: Decimal = Field(
        gt=0,
        max_digits=19,
        decimal_places=4,
    )
