"""Refactoring engine for the GESPER refactoring agent.

This module implements the OpenAI-driven refactoring engine with support for
prompt construction, validation, caching, retry/backoff, logging, and local
fallback behavior in case of API failures.
"""

import dataclasses
import hashlib
import json
import logging
import os
import time
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from openai import OpenAI
from openai.error import APIError, RateLimitError, ServiceUnavailableError, Timeout

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

DEFAULT_MODEL = os.getenv("REFRACTOR_MODEL", "gpt-4.1")
DEFAULT_TEMPERATURE = float(os.getenv("REFRACTOR_TEMPERATURE", "0.2"))
DEFAULT_MAX_TOKENS = int(os.getenv("REFRACTOR_MAX_TOKENS", "2300"))
DEFAULT_RETRY_COUNT = int(os.getenv("REFRACTOR_RETRY_COUNT", "3"))
DEFAULT_BACKOFF_BASE = float(os.getenv("REFRACTOR_BACKOFF_BASE", "1.0"))
DEFAULT_BATCH_SIZE = int(os.getenv("REFRACTOR_BATCH_SIZE", "10"))
DEFAULT_CACHE_PATH = os.getenv("REFRACTOR_CACHE_PATH", "refactor_plan_cache.json")
DEFAULT_FALLBACK_ENABLED = os.getenv("REFRACTOR_FALLBACK_ENABLED", "true").lower() in {"1", "true", "yes"}
DEFAULT_MAX_PROMPT_CHARS = int(os.getenv("REFRACTOR_MAX_PROMPT_CHARS", "15000"))

SYSTEM_PROMPT = """
Sei un agente di refactoring professionale.
Devi migliorare il codice (leggibilità, struttura, error handling) SENZA cambiare il comportamento.
Rispondi SOLO con JSON valido, nel formato:

[
  {
    "path": "percorso/del/file.py",
    "new_content": "NUOVO CONTENUTO COMPLETO DEL FILE"
  }
]
"""


@dataclass
class RefactorEngineConfig:
    model: str = DEFAULT_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    retry_count: int = DEFAULT_RETRY_COUNT
    backoff_base: float = DEFAULT_BACKOFF_BASE
    cache_path: str = DEFAULT_CACHE_PATH
    fallback_enabled: bool = DEFAULT_FALLBACK_ENABLED
    prompt_max_chars: int = DEFAULT_MAX_PROMPT_CHARS
    batch_size: int = DEFAULT_BATCH_SIZE


class RefactorCache:
    """Cache for refactor plan results keyed by batch signature."""

    def __init__(self, cache_path: str) -> None:
        self.cache_path = Path(cache_path)
        self.entries: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.cache_path.exists():
            return

        try:
            with self.cache_path.open("r", encoding="utf-8") as cache_file:
                data = json.load(cache_file)
            if isinstance(data, dict):
                self.entries = data
            else:
                logger.warning("Invalid cache structure in %s, discarding.", self.cache_path)
        except Exception as exc:
            logger.warning("Unable to load refactor cache %s: %s", self.cache_path, exc)
            self.entries = {}

    def save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("w", encoding="utf-8") as cache_file:
                json.dump(self.entries, cache_file, indent=2)
            logger.info("Refactor cache saved to %s", self.cache_path)
        except Exception as exc:
            logger.warning("Unable to save refactor cache %s: %s", self.cache_path, exc)

    def get(self, batch_hash: str) -> Optional[str]:
        return self.entries.get(batch_hash)

    def set(self, batch_hash: str, raw_plan: str) -> None:
        self.entries[batch_hash] = raw_plan


class RefactorPromptBuilder:
    """Builds prompts for the refactoring API requests."""

    @staticmethod
    def chunk_files(files: List[Dict[str, str]], max_chars: int) -> List[List[Dict[str, str]]]:
        chunks: List[List[Dict[str, str]]] = []
        current_chunk: List[Dict[str, str]] = []
        current_size = 0

        for file_entry in files:
            entry_size = len(file_entry["path"]) + len(file_entry["content"])
            if entry_size > max_chars:
                file_entry = {
                    "path": file_entry["path"],
                    "content": RefactorPromptBuilder._trim_content(file_entry["content"]),
                }
                entry_size = len(file_entry["path"]) + len(file_entry["content"])

            if current_size + entry_size > max_chars and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_size = 0

            current_chunk.append(file_entry)
            current_size += entry_size

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    @staticmethod
    def _trim_content(content: str, head_chars: int = 4000, tail_chars: int = 4000) -> str:
        if len(content) <= head_chars + tail_chars + 100:
            return content
        return (
            content[:head_chars]
            + "\n\n# ... contenuto troncato per ridurre il contesto ...\n\n"
            + content[-tail_chars:]
        )

    @staticmethod
    def build_messages(files: List[Dict[str, str]]) -> List[Dict[str, str]]:
        snippet = ""
        for file_entry in files:
            snippet += f"\n### FILE: {file_entry['path']}\n{file_entry['content']}\n"

        user_prompt = textwrap.dedent(
            f"""
            Analizza questi file Python e proponi un refactoring profondo ma sicuro.
            Mantieni il comportamento invariato, migliora struttura, nomi, docstring, gestione errori.

            {snippet}
            """
        )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]


class RefactorPlanValidator:
    """Validates refactoring plans returned by the API."""

    @staticmethod
    def validate_raw(raw: str) -> List[Dict[str, str]]:
        try:
            plan = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON returned by refactor engine: %s", exc)
            raise ValueError("Invalid JSON response from refactor engine.") from exc

        if not isinstance(plan, list):
            raise ValueError("Refactor plan must be a list of file objects.")

        seen_paths = set()
        for entry in plan:
            if not isinstance(entry, dict):
                raise ValueError("Each plan item must be a dictionary.")
            if "path" not in entry or "new_content" not in entry:
                raise ValueError("Each plan item must contain 'path' and 'new_content'.")
            if not isinstance(entry["path"], str) or not isinstance(entry["new_content"], str):
                raise ValueError("Both 'path' and 'new_content' must be strings.")
            if entry["path"] in seen_paths:
                raise ValueError(f"Duplicate path in refactor plan: {entry['path']}")
            seen_paths.add(entry["path"])

        return plan


class LocalFallbackRefactorExecutor:
    """Generates a safe fallback refactor plan when the API is unavailable."""

    @staticmethod
    def create_fallback_plan(files: List[Dict[str, str]]) -> str:
        logger.warning("Using local fallback refactor plan for %s files.", len(files))
        plan = [
            {"path": file_entry["path"], "new_content": file_entry["content"]}
            for file_entry in files
        ]
        return json.dumps(plan, indent=2, ensure_ascii=False)


class OpenAIRefactorExecutor:
    """Executes refactor prompts against the OpenAI API with retries and backoff."""

    def __init__(self, config: RefactorEngineConfig, client: OpenAI) -> None:
        self.config = config
        self.client = client

    def execute(self, files: List[Dict[str, str]]) -> Tuple[str, Dict[str, Any]]:
        chunked_files = RefactorPromptBuilder.chunk_files(files, self.config.prompt_max_chars)
        raw_results: List[str] = []
        usage_summary: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for index, chunk in enumerate(chunked_files, start=1):
            messages = RefactorPromptBuilder.build_messages(chunk)
            raw, usage = self._request_with_retries(messages, index)
            raw_results.append(raw)
            self._accumulate_usage(usage_summary, usage)

        if len(raw_results) == 1:
            return raw_results[0], usage_summary

        combined_plan = []
        for raw in raw_results:
            plan_segment = RefactorPlanValidator.validate_raw(raw)
            combined_plan.extend(plan_segment)

        return json.dumps(combined_plan, indent=2, ensure_ascii=False), usage_summary

    def _request_with_retries(self, messages: List[Dict[str, str]], chunk_number: int) -> Tuple[str, Dict[str, Any]]:
        for attempt in range(1, self.config.retry_count + 1):
            start_time = time.time()
            try:
                logger.info("Calling OpenAI API for chunk %s (attempt %s/%s).", chunk_number, attempt, self.config.retry_count)
                response = self.client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                )
                elapsed = time.time() - start_time
                raw = self._extract_raw_response(response)
                usage = self._extract_usage(response)
                logger.info(
                    "OpenAI call succeeded for chunk %s in %.2fs (tokens: prompt=%s, completion=%s, total=%s).",
                    chunk_number,
                    elapsed,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("total_tokens", 0),
                )
                return raw, usage
            except (RateLimitError, Timeout, ServiceUnavailableError, APIError) as exc:
                backoff = self.config.backoff_base * (2 ** (attempt - 1))
                logger.warning(
                    "OpenAI request failed on attempt %s/%s: %s. Backing off %.1fs.",
                    attempt,
                    self.config.retry_count,
                    exc,
                    backoff,
                )
                if attempt == self.config.retry_count:
                    raise
                time.sleep(backoff)
            except Exception as exc:
                logger.error("Unexpected OpenAI error: %s", exc)
                raise

        raise RuntimeError("Exceeded maximum retry attempts for OpenAI request.")

    @staticmethod
    def _extract_raw_response(response: Any) -> str:
        content = getattr(response.choices[0].message, "content", None)
        if content is None:
            raise ValueError("OpenAI response contained no text content.")
        return content

    @staticmethod
    def _extract_usage(response: Any) -> Dict[str, int]:
        usage = getattr(response, "usage", {}) or {}
        return {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

    @staticmethod
    def _accumulate_usage(summary: Dict[str, int], usage: Dict[str, int]) -> None:
        summary["prompt_tokens"] += usage.get("prompt_tokens", 0)
        summary["completion_tokens"] += usage.get("completion_tokens", 0)
        summary["total_tokens"] += usage.get("total_tokens", 0)


class RefactorEngine:
    """High-level engine coordinating prompt building, validation, execution, and caching."""

    def __init__(self, config: Optional[RefactorEngineConfig] = None, client: Optional[OpenAI] = None) -> None:
        self.config = config or RefactorEngineConfig()
        self.client = client or OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.cache = RefactorCache(self.config.cache_path)
        self.executor = OpenAIRefactorExecutor(self.config, self.client)

    def generate_refactor_plan(self, files: List[Dict[str, str]]) -> Optional[str]:
        batch_hash = self._hash_batch(files)
        cached = self.cache.get(batch_hash)
        if cached is not None:
            logger.info("Reusing cached refactor plan for batch %s.", batch_hash)
            return cached

        try:
            raw_plan, usage = self.executor.execute(files)
            validated_plan = RefactorPlanValidator.validate_raw(raw_plan)
            self.cache.set(batch_hash, raw_plan)
            self.cache.save()
            logger.info("Generated and validated refactor plan for batch %s.", batch_hash)
            return raw_plan
        except Exception as exc:
            logger.error("Failed to generate refactor plan: %s", exc)
            if self.config.fallback_enabled:
                return LocalFallbackRefactorExecutor.create_fallback_plan(files)
            return None

    def _hash_batch(self, files: List[Dict[str, str]]) -> str:
        combined = "".join(f"{entry['path']}:{entry['content']}" for entry in sorted(files, key=lambda x: x["path"]))
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def generate_refactor_plan(
    files: List[Dict[str, str]],
    config: Optional[RefactorEngineConfig] = None,
) -> Optional[str]:
    """Generate a refactor plan for the provided files.

    Args:
        files: List of dictionaries containing 'path' and 'content'.
        config: Optional engine configuration.

    Returns:
        A JSON string containing the refactor plan, or None if generation failed.
    """
    engine = RefactorEngine(config=config)
    return engine.generate_refactor_plan(files)
