from __future__ import annotations

from typing import Any

from ..config import ModelConfig, ProviderConfig
from ..errors import err_invalid_params, err_provider_config
from ..schemas import EmbeddingRequest, GenerationRequest
from .base import AdapterResult, BaseProviderAdapter
from .utils import classify_provider_exception


class LangChainOpenAIAdapter(BaseProviderAdapter):
    async def generate(
        self,
        *,
        provider: ProviderConfig,
        model: ModelConfig,
        request: GenerationRequest,
        timeout_ms: int,
    ) -> AdapterResult:
        if not provider.api_key:
            raise err_provider_config("openai api_key is empty")

        try:
            from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise err_invalid_params("langchain-openai is not installed") from exc

        lc_messages: list[Any] = []
        for msg in request.messages:
            if msg.role == "system":
                lc_messages.append(SystemMessage(content=msg.content))
            elif msg.role == "assistant":
                lc_messages.append(AIMessage(content=msg.content))
            else:
                lc_messages.append(HumanMessage(content=msg.content))

        llm = ChatOpenAI(
            model=model.id,
            api_key=provider.api_key,
            base_url=provider.base_url,
            timeout=max(timeout_ms / 1000.0, 0.1),
            temperature=request.params.temperature,
            max_tokens=request.params.max_tokens,
            model_kwargs={"top_p": request.params.top_p},
        )

        try:
            result = await llm.ainvoke(lc_messages)
        except Exception as exc:
            raise classify_provider_exception(exc) from exc

        usage = (result.response_metadata or {}).get("token_usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens))
        return AdapterResult(
            data={"content": _normalize_content(result.content)},
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    async def embed(
        self,
        *,
        provider: ProviderConfig,
        model: ModelConfig,
        request: EmbeddingRequest,
        timeout_ms: int,
    ) -> AdapterResult:
        if not provider.api_key:
            raise err_provider_config("openai api_key is empty")

        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as exc:
            raise err_invalid_params("langchain-openai is not installed") from exc

        embeddings = OpenAIEmbeddings(
            model=model.id,
            api_key=provider.api_key,
            base_url=provider.base_url,
            request_timeout=max(timeout_ms / 1000.0, 0.1),
        )
        try:
            vectors = await embeddings.aembed_documents(request.texts)
        except Exception as exc:
            raise classify_provider_exception(exc) from exc

        return AdapterResult(data={"vectors": vectors}, total_tokens=max(len(request.texts), 1))


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(item) for item in content)
    return str(content)

