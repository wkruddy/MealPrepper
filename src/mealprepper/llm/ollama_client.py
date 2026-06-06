from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from mealprepper.config import Settings, get_settings
from mealprepper.context.budget import CallType, ContextBudget, load_context_budget

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class OllamaClient:
    """OpenAI-compatible client for local Ollama."""

    def __init__(
        self,
        settings: Settings | None = None,
        budget: ContextBudget | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.ollama_base_url.rstrip("/")
        self.model = self.settings.ollama_model
        self.embedding_model = self.settings.ollama_embedding_model
        self.timeout = self.settings.ollama_timeout
        self.max_context_chars = self.settings.max_context_chars
        self.budget = budget or load_context_budget(self.settings)
        merged = self.settings.merged_config()
        self._warn_at_pct = float(merged.get("context", {}).get("warn_at_pct", 0.85))

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        json_mode: bool = False,
        call_type: CallType | str = CallType.DEFAULT,
    ) -> str:
        messages = self._enforce_context_budget(messages, call_type)
        total = self.prompt_char_count(messages)
        limit = self.budget.limit_for(call_type)
        logger.debug(
            "Ollama %s prompt: %d chars (budget %d)",
            call_type.value if isinstance(call_type, CallType) else call_type,
            total,
            limit,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            payload["format"] = "json"

        url = f"{self.base_url}/v1/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            logger.warning("Ollama unavailable (%s), using fallback parser", exc)
            raise OllamaUnavailableError(str(exc)) from exc

    def chat_json(
        self,
        messages: list[dict[str, str]],
        model_type: type[T],
        *,
        temperature: float = 0.3,
        call_type: CallType | str = CallType.DEFAULT,
    ) -> T:
        content = self.chat(
            messages, temperature=temperature, json_mode=True, call_type=call_type
        )
        parsed = _extract_json(content)
        return model_type.model_validate(parsed)

    def chat_json_list(
        self,
        messages: list[dict[str, str]],
        model_type: type[T],
        *,
        temperature: float = 0.3,
        call_type: CallType | str = CallType.DEFAULT,
    ) -> list[T]:
        content = self.chat(
            messages, temperature=temperature, json_mode=True, call_type=call_type
        )
        parsed = _extract_json(content)
        if isinstance(parsed, list):
            return [model_type.model_validate(item) for item in parsed]
        if isinstance(parsed, dict) and "items" in parsed:
            return [model_type.model_validate(item) for item in parsed["items"]]
        raise ValueError(f"Expected JSON list, got: {type(parsed)}")

    def embed(self, text: str) -> list[float]:
        """Generate embeddings via Ollama /api/embeddings (optional semantic search)."""
        url = f"{self.base_url}/api/embeddings"
        payload = {"model": self.embedding_model, "prompt": text}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data.get("embedding", [])
        except httpx.HTTPError as exc:
            logger.warning("Ollama embeddings unavailable: %s", exc)
            raise OllamaUnavailableError(str(exc)) from exc

    def prompt_char_count(self, messages: list[dict[str, str]]) -> int:
        return sum(len(m.get("content", "")) for m in messages)

    def _enforce_context_budget(
        self,
        messages: list[dict[str, str]],
        call_type: CallType | str,
    ) -> list[dict[str, str]]:
        limit = self.budget.limit_for(call_type)
        total = self.prompt_char_count(messages)
        warn_threshold = int(limit * self._warn_at_pct)

        if total <= warn_threshold:
            return messages

        if total <= limit:
            logger.warning(
                "Prompt size %d chars near budget %d for %s",
                total,
                limit,
                call_type,
            )
            return messages

        logger.warning(
            "Prompt size %d exceeds budget %d for %s — truncating user message",
            total,
            limit,
            call_type,
        )
        trimmed: list[dict[str, str]] = []
        used = 0
        for msg in messages:
            content = msg.get("content", "")
            role = msg.get("role", "user")
            if role == "system":
                trimmed.append(msg)
                used += len(content)
                continue
            remaining = max(256, limit - used)
            if len(content) > remaining:
                content = self.budget.truncate(content, call_type, label=role)
            trimmed.append({"role": role, "content": content})
            used += len(content)
        return trimmed

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except httpx.HTTPError:
            return False


class OllamaUnavailableError(Exception):
    """Raised when Ollama cannot be reached."""


def _extract_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        return json.loads(fence.group(1).strip())

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return json.loads(text[start : end + 1])

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        return json.loads(text[start : end + 1])

    raise ValueError(f"Could not parse JSON from LLM response: {text[:200]}...")
