from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    upc: str = Field(min_length=1, max_length=50)
    product_name: str = Field(min_length=1, max_length=100)
    category_id: int = Field(gt=0)
    unit: str = Field(min_length=1, max_length=20)
    barcode: str | None = Field(default=None, max_length=50)
    active: bool | None = True


class ProductReplace(BaseModel):
    upc: str = Field(min_length=1, max_length=50)
    product_name: str = Field(min_length=1, max_length=100)
    category_id: int = Field(gt=0)
    unit: str = Field(min_length=1, max_length=20)
    barcode: str | None = Field(max_length=50)
    active: bool | None


class ProductPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upc: str | None = Field(default=None, min_length=1, max_length=50)
    product_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    category_id: int | None = Field(default=None, gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    barcode: str | None = Field(default=None, max_length=50)
    active: bool | None = None


class ProductResponse(BaseModel):
    product_id: int
    upc: str
    product_name: str
    category_id: int
    unit: str
    barcode: str | None
    active: bool | None
