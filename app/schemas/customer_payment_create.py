
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PaymentAllocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: int = Field(gt=0)
    amount_applied: Decimal = Field(
        gt=0,
        max_digits=19,
        decimal_places=2,
    )


class CustomerPaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int = Field(gt=0)
    payment_date: date
    amount: Decimal = Field(
        gt=0,
        max_digits=19,
        decimal_places=2,
    )
    reference: str | None = Field(
        default=None,
        max_length=100,
    )
    clearing_account_id: str = Field(
        min_length=1,
        max_length=20,
    )
    allocations: list[PaymentAllocationCreate] = Field(
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_allocations(self):
        invoice_ids = [
            allocation.invoice_id
            for allocation in self.allocations
        ]

        if len(invoice_ids) != len(set(invoice_ids)):
            raise ValueError(
                "Each invoice can appear only once in a payment."
            )

        total_allocated = sum(
            (
                allocation.amount_applied
                for allocation in self.allocations
            ),
            Decimal("0.00"),
        )

        if total_allocated != self.amount:
            raise ValueError(
                "Total allocated amount must equal the payment amount."
            )

        return self
