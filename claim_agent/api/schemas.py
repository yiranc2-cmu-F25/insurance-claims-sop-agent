"""Validation for client requests, separate from LLM output schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class EmailChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    choice: Literal["send", "skip"]


class EmailStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_id: str = Field(pattern=r"^[a-f0-9]{32}$")
