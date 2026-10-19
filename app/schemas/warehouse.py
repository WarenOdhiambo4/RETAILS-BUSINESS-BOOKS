
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WarehouseFields(BaseModel):
    warehouse_name: str = Field(min_length=1, max_length=100)
    warehouse_location: str | None = Field(
        default=None,
        max_length=50,
    )
    warehouse_code: str | None = Field(
        default=None,
        max_length=50,
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
    )
    status: str = Field(default="Active", max_length=30)
    address_line1: str | None = Field(
        default=None,
        max_length=400,
    )
    address_line2: str | None = Field(
        default=None,
        max_length=400,
    )
    city: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    country_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
    )
    contact_phone: str | None = Field(
        default=None,
        max_length=40,
    )
    contact_email: str | None = Field(
        default=None,
        max_length=320,
    )


class WarehouseCreate(WarehouseFields):
    pass


class WarehouseReplace(WarehouseFields):
    # PUT requires the caller to supply the complete profile.
    # Nullable fields may still be explicitly set to null.
    warehouse_location: str | None = Field(max_length=50)
    warehouse_code: str | None = Field(max_length=50)
    description: str | None = Field(max_length=2000)
    address_line1: str | None = Field(max_length=400)
    address_line2: str | None = Field(max_length=400)
    city: str | None = Field(max_length=200)
    region: str | None = Field(max_length=200)
    country_code: str | None = Field(
        min_length=2,
        max_length=2,
    )
    contact_phone: str | None = Field(max_length=40)
    contact_email: str | None = Field(max_length=320)


class WarehousePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    warehouse_location: str | None = Field(
        default=None,
        max_length=50,
    )
    warehouse_code: str | None = Field(
        default=None,
        max_length=50,
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
    )
    status: str | None = Field(
        default=None,
        max_length=30,
    )
    address_line1: str | None = Field(
        default=None,
        max_length=400,
    )
    address_line2: str | None = Field(
        default=None,
        max_length=400,
    )
    city: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    country_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
    )
    contact_phone: str | None = Field(
        default=None,
        max_length=40,
    )
    contact_email: str | None = Field(
        default=None,
        max_length=320,
    )


class WarehouseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    warehouse_id: int
    warehouse_name: str
    warehouse_location: str | None
    warehouse_code: str | None
    description: str | None
    status: str
    address_line1: str | None
    address_line2: str | None
    city: str | None
    region: str | None
    country_code: str | None
    contact_phone: str | None
    contact_email: str | None
    created_at: datetime
    updated_at: datetime
    created_by_user_id: int | None
    updated_by_user_id: int | None
