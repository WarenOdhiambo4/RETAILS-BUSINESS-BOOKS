
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WarehouseDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse_document_id: int
    warehouse_id: int
    file_kind: str
    original_file_name: str
    content_type: str
    file_size_bytes: int
    description: str | None
    uploaded_at: datetime
    uploaded_by_user_id: int | None


class WarehouseDocumentUploadResponse(WarehouseDocumentResponse):
    pass


class WarehouseDocumentDeactivateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse_document_id: int
    is_active: bool
    deactivated_at: datetime
    deactivated_by_user_id: int
