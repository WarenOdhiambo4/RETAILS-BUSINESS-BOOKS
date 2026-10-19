
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AdminSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    user_id: int
    email: str
    first_name: str
    last_name: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None
    is_current_session: bool
