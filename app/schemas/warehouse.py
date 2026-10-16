
from pydantic import BaseModel, ConfigDict, Field


class WarehouseCreate(BaseModel):
    warehouse_name: str = Field(min_length=1, max_length=100)
    warehouse_location: str | None = Field(
        default=None,
        max_length=50,
    )


class WarehouseReplace(BaseModel):
    warehouse_name: str = Field(min_length=1, max_length=100)
    warehouse_location: str | None = Field(max_length=50)


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


class WarehouseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    warehouse_id: int
    warehouse_name: str
    warehouse_location: str | None
