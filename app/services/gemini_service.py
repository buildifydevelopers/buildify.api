import json
import logging
import asyncio
import time
from typing import Any, Type, TypeVar

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.cache import get_cached, set_cached, make_cache_key

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMParseError(ValueError):
    """Raised when the provider returns malformed JSON text."""


def _is_retryable_exception(exc: BaseException) -> bool:
    """Retry only for transient conditions (timeouts, 429, and 5xx)."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False

# Retry: up to 5 attempts, exponential backoff (1s, 2s, 4s, 8s, 10s)
_retry_decorator = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_retryable_exception),
    before_sleep=lambda state: logger.warning(
        "Retrying LLM call (attempt %d): %s",
        state.attempt_number,
        state.outcome.exception() if state.outcome else "unknown",
    ),
)


class GeminiService:
    """Async LLM client with retry, caching, and Pydantic enforcement."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._concurrency_limit = asyncio.Semaphore(1)
        self._last_cerebras_call_at: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── Core call with retry ──

    @_retry_decorator
    async def _call_gemini(
        self,
        system_instruction: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Make an async call to the Gemini API with retry logic."""
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not set.")

        client = await self._get_client()
        api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_model}:generateContent"
        )

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.3,
            },
        }
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": settings.gemini_api_key,
        }

        logger.info("Calling Gemini API for: %s", user_prompt[:80])
        self._last_cerebras_call_at = time.monotonic()
        response = await client.post(api_url, json=payload, headers=headers)
        response.raise_for_status()

        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return self._safe_parse_json(text)

    @_retry_decorator
    async def _call_cerebras(
        self,
        system_instruction: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Make an async call to Cerebras (OpenAI-compatible) with retry logic."""
        if not settings.cerebras_api_key:
            raise ValueError("CEREBRAS_API_KEY is not set.")

        client = await self._get_client()
        api_url = "https://api.cerebras.ai/v1/chat/completions"

        response_format = {"type": "json_object"}
        payload = {
            "model": settings.cerebras_model,
            "temperature": 0.3,
            "response_format": response_format,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.cerebras_api_key}",
        }

        min_interval = max(0.0, float(getattr(settings, "cerebras_min_interval_seconds", 0.0)))
        elapsed = time.monotonic() - self._last_cerebras_call_at
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)

        logger.info("Calling Cerebras API for: %s", user_prompt[:80])
        response = await client.post(api_url, json=payload, headers=headers)

        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            if retry_after:
                try:
                    await asyncio.sleep(max(1.0, float(retry_after)))
                except ValueError:
                    await asyncio.sleep(max(2.0, min_interval or 2.0))

        response.raise_for_status()

        data = response.json()
        text = data["choices"][0]["message"]["content"]
        return self._safe_parse_json(text)

    async def _call_provider(
        self,
        system_instruction: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Dispatch LLM call based on configured provider."""
        provider = settings.llm_provider.strip().lower()

        async with self._concurrency_limit:
            if provider == "auto":
                if settings.cerebras_api_key:
                    return await self._call_cerebras(system_instruction, user_prompt)
                if settings.gemini_api_key:
                    return await self._call_gemini(system_instruction, user_prompt)
                raise ValueError(
                    "No API key configured. Set CEREBRAS_API_KEY or GEMINI_API_KEY."
                )

            if provider == "cerebras":
                return await self._call_cerebras(system_instruction, user_prompt)

            if provider == "gemini":
                return await self._call_gemini(system_instruction, user_prompt)

            raise ValueError(
                "Invalid LLM_PROVIDER. Use one of: auto, cerebras, gemini."
            )

    # ── Structured call with caching + Pydantic validation ──

    async def structured_call(
        self,
        system_instruction: str,
        user_prompt: str,
        response_model: Type[T],
        cache_type: str = "generic",
    ) -> T:
        """
        Call configured LLM provider, cache the result, and validate with Pydantic model.
        Returns an instance of response_model.
        """
        # Check cache
        provider_cache_hint = settings.llm_provider.strip().lower() or "auto"
        cache_key = make_cache_key(user_prompt, provider_cache_hint, cache_type)
        cached = get_cached(cache_key)
        if cached is not None:
            try:
                return response_model(**cached)
            except (ValidationError, Exception):
                logger.warning("Cached data failed validation, re-fetching.")

        # Call configured LLM provider
        try:
            raw = await self._call_provider(system_instruction, user_prompt)
        except LLMParseError as parse_error:
            logger.warning("LLM returned malformed JSON for %s: %s", cache_type, parse_error)
            repair_prompt = (
                "The previous response was not valid JSON. Return a corrected JSON object only, "
                "preserving the intended meaning and schema as closely as possible. "
                "Do not add markdown or commentary.\n\n"
                f"Original prompt:\n{user_prompt}"
            )
            raw = await self._call_provider(system_instruction, repair_prompt)

        # Validate with Pydantic
        try:
            result = response_model(**raw)
        except ValidationError as e:
            logger.error("LLM returned invalid data for %s: %s", cache_type, e)
            repair_prompt = (
                "The following JSON did not validate against the required schema. "
                "Return a corrected JSON object only, with the same meaning if possible, "
                "and ensure every enum value exactly matches the schema.\n\n"
                f"Validation error: {e}\n\n"
                f"Invalid JSON:\n{json.dumps(raw, ensure_ascii=False, indent=2)}"
            )

            try:
                repaired_raw = await self._call_provider(system_instruction, repair_prompt)
                result = response_model(**repaired_raw)
            except Exception as repair_exc:
                logger.error("LLM repair failed for %s: %s", cache_type, repair_exc)
                raise ValueError(
                    f"LLM returned data that does not match expected schema for {cache_type}. "
                    f"Please try again."
                ) from e

        # Cache the validated dict
        set_cached(cache_key, result.model_dump())

        return result

    # ── JSON parsing ──

    @staticmethod
    def _safe_parse_json(text: str) -> dict[str, Any]:
        """Parse JSON from LLM response, with brace-extraction fallback."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                raise LLMParseError("LLM response did not contain valid JSON.")
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise LLMParseError("LLM response contained malformed JSON.") from exc


# ── Singleton ──
gemini_service = GeminiService()
