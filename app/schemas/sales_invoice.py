
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class SalesInvoiceLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invoice_line_id: int
    product_id: int
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    tax_rate_id: int | None

    line_subtotal: Decimal


class SalesInvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invoice_id: int
    invoice_number: str
    customer_id: int
    customer_name: str
    invoice_date: date
    due_date: date
    invoice_status: str

    subtotal: Decimal
    tax_total: Decimal
    invoice_total: Decimal


class SalesInvoiceDetailResponse(SalesInvoiceResponse):
    lines: list[SalesInvoiceLineResponse]

