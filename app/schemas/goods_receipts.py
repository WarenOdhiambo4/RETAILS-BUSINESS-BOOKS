from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class GoodsReceiptLineCreate(BaseModel):
    po_line_id: int = Field(gt=0)

    quantity_received: Decimal = Field(
        gt=0,
        max_digits=19,
        decimal_places=4,
    )

    unit_cost: Decimal = Field(
        ge=0,
        max_digits=19,
        decimal_places=2,
    )

    tax_rate_percent: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        le=100,
        max_digits=7,
        decimal_places=4,
    )


class GoodsReceiptCreate(BaseModel):
    purchase_order_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    received_date: datetime | None = None
    lines: list[GoodsReceiptLineCreate] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")