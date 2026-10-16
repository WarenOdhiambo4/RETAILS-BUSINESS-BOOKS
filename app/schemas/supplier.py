from pydantic import BaseModel, ConfigDict, Field


class SupplierCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    contact: str | None = Field(default=None, max_length=150)
    payment_terms: int = Field(default=0, ge=0)


class SupplierReplace(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    contact: str | None = Field(max_length=150)
    payment_terms: int = Field(ge=0)


class SupplierPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    contact: str | None = Field(default=None, max_length=150)
    payment_terms: int | None = Field(default=None, ge=0)


class SupplierResponse(BaseModel):
    supplier_id: int
    full_name: str
    contact: str | None
    payment_terms: int
