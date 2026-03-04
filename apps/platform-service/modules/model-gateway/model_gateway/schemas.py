from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    role: MessageRole
    content: str


class RoutingOptions(BaseModel):
    preferred_model: str | None = None
    allow_fallback: bool = True
    max_fallback_hops: int = Field(default=2, ge=0, le=10)


class GenerationParams(BaseModel):
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    stream: bool = False


class GenerationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    trace_id: str
    request_id: str
    app_id: str
    scene: str
    messages: list[Message]
    params: GenerationParams = Field(default_factory=GenerationParams)
    routing: RoutingOptions = Field(default_factory=RoutingOptions)
    deadline_ms: int = Field(default=12000, ge=1000, le=120000)


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    trace_id: str
    request_id: str
    app_id: str
    scene: str = "ingestion"
    texts: list[str] = Field(min_length=1)
    routing: RoutingOptions = Field(default_factory=lambda: RoutingOptions(max_fallback_hops=1))
    deadline_ms: int = Field(default=15000, ge=1000, le=120000)


class UsageStats(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    currency: str = "USD"


class AttemptTrace(BaseModel):
    attempt: int
    provider: str
    model: str
    status: Literal["success", "failed", "skipped"]
    latency_ms: int
    error_code: str | None = None
    fallback_reason: str | None = None


class GatewayErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool
    provider_error: str | None = None


class GatewayResponse(BaseModel):
    ok: bool
    provider: str | None = None
    model: str | None = None
    attempts: int = 0
    fallback_used: bool = False
    usage: UsageStats | None = None
    latency_ms: int | None = None
    data: dict[str, Any] | None = None
    error: GatewayErrorDetail | None = None
    attempt_chain: list[AttemptTrace] = Field(default_factory=list)
    final_model: str | None = None
    final_provider: str | None = None

