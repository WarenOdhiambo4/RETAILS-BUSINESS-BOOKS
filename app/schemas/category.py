
from pydantic import BaseModel, ConfigDict, Field


class CategoryCreate(BaseModel):
    category_name: str = Field(min_length=1, max_length=100)


class CategoryReplace(BaseModel):
    category_name: str = Field(min_length=1, max_length=100)


class CategoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )


class CategoryResponse(BaseModel):
    category_id: int
    category_name: str
