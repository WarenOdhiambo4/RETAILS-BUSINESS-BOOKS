
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PaymentAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    allocation_id: int
    invoice_id: int
    invoice_number: str
    amount_applied: Decimal


class CustomerPaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: int
    customer_id: int
    customer_name: str
    payment_date: date
    amount: Decimal
    reference: str | None
    clearing_account_id: str
    allocations: list[PaymentAllocationResponse]
