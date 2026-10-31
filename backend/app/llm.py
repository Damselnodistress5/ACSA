import json
import logging
import re
from typing import Any, Dict, List, Optional
from langchain_groq import ChatGroq
from app.config import settings

logger = logging.getLogger("acsa.llm")


class LLMFatalError(RuntimeError):
    """Raised for errors that retrying or falling back cannot fix (bad/missing API key)."""


def is_quota_exhausted(exc: BaseException) -> bool:
    """Groq daily quota (tokens/requests per day) - waiting a minute won't help."""
    text = str(exc).lower()
    return ("rate_limit" in text or "429" in text) and ("per day" in text or "(tpd)" in text or "(rpd)" in text)


def is_request_too_large(exc: BaseException) -> bool:
    """HTTP 413: a single request is bigger than the tokens-per-minute limit (free tier: 8k)."""
    text = str(exc).lower()
    return "413" in text or "request too large" in text or "payload too large" in text


def describe_fatal_llm_error(exc: BaseException) -> str:
    if is_quota_exhausted(exc):
        return f"Groq daily token/request quota exhausted - try again later or use another key/model: {exc}"
    return f"LLM unavailable (check GROQ_API_KEY): {exc}"


def is_fatal_llm_error(exc: BaseException) -> bool:
    """True for problems retrying cannot fix: auth (401, invalid/missing key) or daily quota exhausted."""
    if isinstance(exc, LLMFatalError):
        return True
    if is_quota_exhausted(exc):
        return True
    try:
        import groq
        if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)):
            return True
    except Exception:
        pass
    text = str(exc).lower()
    return "401" in text or "invalid api key" in text or "invalid_api_key" in text or "groq_api_key is not set" in text


_quota_exhausted_models: set = set()  # models whose daily quota ran out (process lifetime)


def _make_chat(selected_model: str, api_key: str, temperature: float, streaming: bool, max_tokens: int) -> ChatGroq:
    kwargs: Dict[str, Any] = {}
    # reasoning_effort only applies to reasoning models such as openai/gpt-oss-*
    if "gpt-oss" in selected_model and settings.groq_reasoning_effort:
        kwargs["reasoning_effort"] = settings.groq_reasoning_effort
    return ChatGroq(
        model=selected_model,
        groq_api_key=api_key,
        temperature=temperature,
        streaming=streaming,
        max_tokens=max_tokens,
        max_retries=settings.groq_max_retries,
        timeout=180.0,
        **kwargs,
    )


class ResilientChat:
    """ChatGroq wrapper: if the primary model's DAILY quota is exhausted (Groq free tier:
    200k tokens/day per model), switch to GROQ_FALLBACK_MODEL (its quota is separate)."""

    def __init__(self, models: List[str], api_key: str, temperature: float, streaming: bool, max_tokens: int):
        self.models = models
        self._args = (api_key, temperature, streaming, max_tokens)
        self._clients: Dict[str, ChatGroq] = {}

    def _client(self, model: str) -> ChatGroq:
        if model not in self._clients:
            api_key, temperature, streaming, max_tokens = self._args
            self._clients[model] = _make_chat(model, api_key, temperature, streaming, max_tokens)
        return self._clients[model]

    def invoke(self, messages, **kwargs):
        candidates = [m for m in self.models if m not in _quota_exhausted_models] or self.models[-1:]
        last_exc: Optional[BaseException] = None
        for model in candidates:
            try:
                resp = self._client(model).invoke(messages, **kwargs)
                # Some models (e.g. qwen3) put their reasoning inline as <think>...</think>.
                if isinstance(getattr(resp, "content", None), str) and "<think>" in resp.content:
                    resp.content = re.sub(r"<think>[\s\S]*?(</think>|$)", "", resp.content).strip()
                return resp
            except Exception as e:
                if is_quota_exhausted(e) and model != candidates[-1]:
                    _quota_exhausted_models.add(model)
                    logger.warning(f"Groq daily quota exhausted for {model}; switching to fallback model.")
                    last_exc = e
                    continue
                raise
        raise last_exc  # pragma: no cover


def get_llm(
    temperature: float = 0.2,
    model: Optional[str] = None,
    streaming: bool = False,
    max_tokens: Optional[int] = None,
) -> ResilientChat:
    """Returns a configured Groq chat model (with daily-quota fallback model)."""
    api_key = settings.groq_api_key
    if not api_key:
        raise LLMFatalError(
            "GROQ_API_KEY is not set. Please add it to backend/.env or your environment variables."
        )

    selected_model = model or settings.groq_model
    models = [selected_model]
    fb = (settings.groq_fallback_model or "").strip()
    if fb and fb != selected_model:
        models.append(fb)
    logger.info(
        f"Initializing ChatGroq with model: {selected_model} (fallback: {fb or 'none'}, "
        f"max_tokens={max_tokens or settings.groq_max_tokens}, key from {settings.groq_api_key_source})"
    )
    return ResilientChat(models, api_key, temperature, streaming, max_tokens or settings.groq_max_tokens)


def was_truncated(response: Any) -> bool:
    """True if the model stopped because it hit max_tokens (finish_reason == 'length')."""
    meta = getattr(response, "response_metadata", None) or {}
    return meta.get("finish_reason") == "length"


def extract_code_block(text: str) -> str:
    """Return the body of the first fenced code block, or the raw text if none.
    Handles a missing closing fence."""
    text = (text or "").strip()
    m = re.search(r"```[a-zA-Z0-9_+-]*[ \t]*\n([\s\S]*?)\n?```", text)
    if m:
        return m.group(1).strip("\n")
    if text.startswith("```"):
        first_nl = text.find("\n")
        return text[first_nl + 1:].strip("\n") if first_nl != -1 else ""
    return text


def extract_json(text: str) -> Any:
    """
    Robustly extract and parse JSON from an LLM response string.
    Handles raw JSON, markdown ```json code blocks, unescaped characters, and truncated objects.
    """
    text = text.strip()

    # 1. Try direct standard json parsing
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2. Try json_repair on raw text
    try:
        import json_repair
        parsed = json_repair.loads(text)
        if parsed is not None:
            return parsed
    except Exception:
        pass

    # 3. Look for fenced markdown json block: ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        snippet = match.group(1).strip()
        try:
            return json.loads(snippet)
        except Exception:
            pass
        try:
            import json_repair
            parsed = json_repair.loads(snippet)
            if parsed is not None:
                return parsed
        except Exception:
            pass

    # 4. Find outer braces {...} or brackets [...]
    start_brace = text.find("{")
    end_brace = text.rfind("}")
    if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
        candidate = text[start_brace : end_brace + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass
        try:
            import json_repair
            parsed = json_repair.loads(candidate)
            if parsed is not None:
                return parsed
        except Exception:
            pass

    # 5. Last ditch: regex sanitize common trailing commas
    if start_brace != -1 and end_brace != -1:
        candidate = text[start_brace : end_brace + 1]
        cleaned = re.sub(r",\s*([\]}])", r"\1", candidate)
        try:
            return json.loads(cleaned)
        except Exception:
            pass
        try:
            import json_repair
            return json_repair.loads(cleaned)
        except Exception:
            pass

    raise ValueError(f"Could not extract valid JSON from LLM output: {text[:200]}...")


def extract_files_from_markdown(text: str) -> list[dict]:
    """
    Robustly extracts file blocks formatted with markdown headings such as:
    ### FILE: app/models.py
    ```python
    code...
    ```
    Also works even if the closing code fence is missing due to token boundaries.
    """
    files = []
    pattern = re.compile(
        r"(?:^|\n)(?:###|##|\*\*|#)\s*(?:FILE|File):\s*`?([^\n`\r]+)`?",
        re.MULTILINE | re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return []

    for i, m in enumerate(matches):
        raw_path = m.group(1).strip()
        clean_path = raw_path.strip("*: `\"'")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()

        # Remove leading ```lang
        code = chunk
        if code.startswith("```"):
            first_nl = code.find("\n")
            if first_nl != -1:
                code = code[first_nl + 1 :]
        # Remove trailing ```
        if "```" in code:
            last_fence = code.rfind("```")
            code = code[:last_fence].rstrip()

        code = code.strip()
        if clean_path and code:
            files.append({
                "path": clean_path,
                "content": code,
                "description": f"File generated for {clean_path}",
            })
    return files
