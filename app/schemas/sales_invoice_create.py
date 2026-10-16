from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SalesInvoiceLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=19, decimal_places=4)
    unit_price: Decimal = Field(ge=0, max_digits=19, decimal_places=2)
    discount: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        max_digits=19,
        decimal_places=2,
    )
    tax_rate_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_discount(self):
        if self.discount > self.quantity * self.unit_price:
            raise ValueError(
                "Discount cannot exceed quantity multiplied by unit price."
            )
        return self


class SalesInvoiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: str = Field(min_length=1, max_length=100)
    customer_id: int = Field(gt=0)
    invoice_date: date
    due_date: date
    lines: list[SalesInvoiceLineCreate] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.due_date < self.invoice_date:
            raise ValueError(
                "Due date cannot be earlier than invoice date."
            )
        return self