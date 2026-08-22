from __future__ import annotations

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard error body returned for every non-2xx response."""

    detail: str = Field(
        description="Human-readable error message.",
        examples=["origin_stop_id and dest_stop_id must be different"],
    )
