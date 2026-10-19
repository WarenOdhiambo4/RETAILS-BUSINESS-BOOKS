
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class InventoryTransferLineCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(
        gt=0,
        max_digits=19,
        decimal_places=4,
    )


class InventoryTransferCreate(BaseModel):
    source_warehouse_id: int = Field(gt=0)
    destination_warehouse_id: int = Field(gt=0)
    lines: list[InventoryTransferLineCreate] = Field(
        min_length=1,
    )

    @model_validator(mode="after")
    def validate_transfer(self):
        if (
            self.source_warehouse_id
            == self.destination_warehouse_id
        ):
            raise ValueError(
                "Source and destination warehouses must differ."
            )

        product_ids = [
            line.product_id for line in self.lines
        ]

        if len(product_ids) != len(set(product_ids)):
            raise ValueError(
                "Each product may appear only once per transfer."
            )

        return self
