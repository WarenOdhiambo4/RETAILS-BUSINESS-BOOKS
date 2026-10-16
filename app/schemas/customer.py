from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CustomerCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    credit_limit: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        max_digits=18,
        decimal_places=2,
    )
    payment_terms: int = Field(default=0, ge=0)


class CustomerReplace(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(max_length=30)
    credit_limit: Decimal = Field(
        ge=0,
        max_digits=18,
        decimal_places=2,
    )
    payment_terms: int = Field(ge=0)


class CustomerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    credit_limit: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=18,
        decimal_places=2,
    )
    payment_terms: int | None = Field(default=None, ge=0)


class CustomerResponse(BaseModel):
    customer_id: int
    full_name: str
    phone: str | None
    credit_limit: Decimal
    payment_terms: int
