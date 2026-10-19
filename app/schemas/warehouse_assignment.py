
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


WarehouseAssignmentRole = Literal[
    "Manager",
    "Supervisor",
    "Storekeeper",
    "Viewer",
]


class WarehouseAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    assignment_role: WarehouseAssignmentRole
    notes: str | None = Field(default=None, max_length=1000)


class WarehouseAssignmentRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_role: WarehouseAssignmentRole


class WarehouseAssignmentEnd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=1000)


class WarehouseAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    warehouse_user_assignment_id: int
    warehouse_id: int
    user_id: int
    user_email: str
    first_name: str
    last_name: str
    assignment_role: WarehouseAssignmentRole
    is_active: bool
    assigned_at: datetime
    assigned_by_user_id: int | None
    ended_at: datetime | None
    notes: str | None
