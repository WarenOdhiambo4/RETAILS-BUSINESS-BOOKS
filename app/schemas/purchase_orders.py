
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PurchaseOrderLineCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(
        gt=0,
        max_digits=19,
        decimal_places=4
    )
    expected_cost: Decimal = Field(
        ge=0,
        max_digits=19,
        decimal_places=2
    )


class PurchaseOrderCreate(BaseModel):
    supplier_id: int = Field(gt=0)
    order_date: date | None = None
    lines: list[PurchaseOrderLineCreate] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class PurchaseOrderLineResponse(BaseModel):
    po_line_id: int
    product_id: int
    product_name: str
    unit: str
    quantity: Decimal
    expected_cost: Decimal
    line_total: Decimal


class PurchaseOrderResponse(BaseModel):
    purchase_order_id: int
    supplier_id: int
    supplier_name: str
    order_date: date
    status: str
    subtotal: Decimal
    lines: list[PurchaseOrderLineResponse]
