from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaxApplicability = Literal["Purchase", "Sale", "Both"]


class TaxRateCreate(BaseModel):
    tax_code: str = Field(min_length=1, max_length=30)
    rate: Decimal = Field(
        ge=0,
        le=100,
        max_digits=7,
        decimal_places=4,
    )
    effective_from: date
    effective_to: date | None = None
    tax_type: str = Field(min_length=1, max_length=30)
    applies_to: TaxApplicability
    is_active: bool = True

    model_config = ConfigDict(str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_effective_dates(self):
        if (
            self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError(
                "Effective end date cannot be before effective start date."
            )

        if not self.tax_code:
            raise ValueError("Tax code cannot be blank.")

        if not self.tax_type:
            raise ValueError("Tax type cannot be blank.")

        return self


class TaxRateUpdate(BaseModel):
    tax_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=30,
    )
    rate: Decimal | None = Field(
        default=None,
        ge=0,
        le=100,
        max_digits=7,
        decimal_places=4,
    )
    effective_from: date | None = None
    effective_to: date | None = None
    tax_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=30,
    )
    applies_to: TaxApplicability | None = None
    is_active: bool | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class TaxRateReplace(BaseModel):
    tax_code: str = Field(min_length=1, max_length=30)
    rate: Decimal = Field(
        ge=0,
        le=100,
        max_digits=7,
        decimal_places=4,
    )
    effective_from: date
    effective_to: date | None
    tax_type: str = Field(min_length=1, max_length=30)
    applies_to: TaxApplicability
    is_active: bool

    model_config = ConfigDict(str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_effective_dates(self):
        if (
            self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError(
                "Effective end date cannot be before effective start date."
            )

        if not self.tax_code:
            raise ValueError("Tax code cannot be blank.")

        if not self.tax_type:
            raise ValueError("Tax type cannot be blank.")

        return self