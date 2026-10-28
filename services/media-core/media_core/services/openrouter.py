"""OpenRouter client used by summarization and clip selection."""

import json
import sys
import hashlib
import os
import socket
import time
import urllib.error
from media_core.config import CACHE_DIR
from media_core.runtime import stage
import urllib.request

from media_core.config import DEFAULT_LLM_MODEL, get_openrouter_key
from media_core.usage import record_usage


class OpenRouterError(RuntimeError):
    """Actionable upstream failure safe to expose to Media API and the UI."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _http_error(error: urllib.error.HTTPError) -> OpenRouterError:
    try:
        payload = json.loads(error.read().decode("utf-8", "replace"))
        detail = payload.get("error", {}).get("message") or payload.get("message")
    except (ValueError, AttributeError, OSError):
        detail = None
    suffix = f" Detalle: {str(detail)[:300]}" if detail else ""
    if error.code in {401, 403}:
        message = "OpenRouter rechazó la API key; comprueba que siga activa y correctamente configurada."
    elif error.code == 402:
        message = "OpenRouter rechazó la petición por saldo o límite de crédito insuficiente."
    elif error.code == 429:
        message = "OpenRouter limitó temporalmente las peticiones (HTTP 429)."
    elif error.code >= 500:
        message = f"OpenRouter no está disponible temporalmente (HTTP {error.code})."
    else:
        message = f"OpenRouter rechazó la petición (HTTP {error.code})."
    return OpenRouterError(
        message + suffix,
        status_code=error.code,
        retryable=error.code == 429 or error.code >= 500,
    )


def call_openrouter(
    messages: list[dict],
    model: str | None = None,
    json_mode: bool = False,
    temperature: float = 0.3,
    max_tokens: int = 1800,
    timeout_seconds: float | None = None,
    retry_delays: tuple[float, ...] = (),
    raise_errors: bool = False,
) -> str | None:
    """Send a chat-completion request to OpenRouter."""
    api_key = get_openrouter_key()
    if not api_key:
        return None

    target_model = model or DEFAULT_LLM_MODEL
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://watchity.com/media",
        "X-Title": "MedIA Core MCP Server",
    }
    payload = {
        "model": target_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    cache = CACHE_DIR / "llm" / (hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() + ".json")
    ttl = int(os.getenv("LLM_CACHE_TTL_SECONDS", "86400"))
    if ttl > 0 and cache.exists() and time.time() - cache.stat().st_mtime < ttl:
        try:
            with stage("llm_cache_hit"):
                return json.loads(cache.read_text(encoding="utf-8"))["content"]
        except (OSError, ValueError, KeyError):
            pass
    timeout = timeout_seconds or float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))
    result = None
    for attempt in range(len(retry_delays) + 1):
        try:
            request = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            with stage("llm_request"), urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            upstream = _http_error(error)
        except (TimeoutError, socket.timeout):
            upstream = OpenRouterError(
                f"OpenRouter superó el tiempo máximo de espera de {timeout:g} segundos.",
                retryable=True,
            )
        except urllib.error.URLError as error:
            upstream = OpenRouterError(
                f"No se pudo conectar temporalmente con OpenRouter: {str(error.reason)[:300]}",
                retryable=True,
            )
        except Exception as error:
            upstream = OpenRouterError(f"Respuesta no utilizable de OpenRouter: {str(error)[:300]}")
        if upstream.retryable and attempt < len(retry_delays):
            delay = retry_delays[attempt]
            print(
                f"[OpenRouter Retry] model={target_model} attempt={attempt + 1} wait={delay:g}s: {upstream}",
                file=sys.stderr,
            )
            time.sleep(delay)
            continue
        print(f"[OpenRouter Error] {upstream}", file=sys.stderr)
        if raise_errors:
            raise upstream
        return None

    try:
        assert result is not None
        usage = result.get("usage") or {}
        if usage:
            record_usage(
                "openrouter",
                result.get("model") or target_model,
                usage.get("cost"),
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                cost_type="actual",
            )
        choice = result["choices"][0]
        if choice.get("finish_reason") == "length":
            print(f"[OpenRouter Warning] model={target_model}: output reached max_tokens={max_tokens}", file=sys.stderr)
            return None
        content = choice["message"]["content"]
        cacheable = bool(content) and choice.get("finish_reason") != "length"
        if json_mode and content:
            candidate = content.strip()
            if candidate.startswith("```"):
                candidate = candidate.split("\n", 1)[-1].rsplit("```", 1)[0]
            try:
                json.loads(candidate)
            except ValueError:
                cacheable = False
        if cacheable and ttl > 0:
            try:
                import uuid
                cache.parent.mkdir(parents=True, exist_ok=True)
                temp = cache.with_suffix("." + uuid.uuid4().hex + ".tmp")
                temp.write_text(json.dumps({"content": content}), encoding="utf-8")
                temp.replace(cache)
            except OSError:
                pass
        return content
    except Exception as error:
        upstream = error if isinstance(error, OpenRouterError) else OpenRouterError(
            f"Respuesta no utilizable de OpenRouter: {str(error)[:300]}"
        )
        print(f"[OpenRouter Error] {upstream}", file=sys.stderr)
        if raise_errors:
            raise upstream
        return None
