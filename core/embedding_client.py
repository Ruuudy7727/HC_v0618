#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

import requests
from openai import OpenAI

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ONLINE_BASE_URL = "https://aimpapi.midea.com/t-aigc/aimp-text-embedding/v1"
_DEFAULT_ONLINE_MODEL = "Qwen3-Embedding-4B"
_DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
_DEFAULT_OLLAMA_MODEL = "qwen3-embedding:4b"
_DEFAULT_TIMEOUT = 120.0
_QUERY_EMBED_CACHE: Dict[str, List[float]] = {}


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=str(_PROJECT_ROOT / ".env"), override=False)
    except Exception:
        pass


def _parse_optional_int(value: str) -> Optional[int]:
    text = (value or "").strip()
    if not text:
        return None
    return int(text)


def _embedding_retry_settings() -> Dict[str, float]:
    _load_env()
    return {
        "max_retries": int(os.getenv("EMBED_MAX_RETRIES", "8")),
        "base_delay": float(os.getenv("EMBED_RETRY_BASE_DELAY", "2.0")),
    }


def _query_cache_size() -> int:
    _load_env()
    return max(0, int(os.getenv("EMBED_QUERY_CACHE_SIZE", "256")))


def _is_retryable_embedding_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "限流" in msg


def _cache_get_query(key: str) -> Optional[List[float]]:
    return _QUERY_EMBED_CACHE.get(key)


def _cache_set_query(key: str, vec: List[float]) -> None:
    max_size = _query_cache_size()
    if max_size <= 0:
        return
    if len(_QUERY_EMBED_CACHE) >= max_size:
        _QUERY_EMBED_CACHE.pop(next(iter(_QUERY_EMBED_CACHE)))
    _QUERY_EMBED_CACHE[key] = vec


def get_embedding_settings() -> Dict[str, object]:
    _load_env()
    backend = (os.getenv("EMBED_BACKEND", "").strip().lower() or "ollama")
    model = os.getenv("EMBED_MODEL", "").strip()
    if not model:
        model = _DEFAULT_OLLAMA_MODEL if backend == "ollama" else _DEFAULT_ONLINE_MODEL
    return {
        "backend": backend,
        "model": model,
        "ollama_host": (os.getenv("OLLAMA_HOST", "").strip() or _DEFAULT_OLLAMA_HOST).rstrip("/"),
        "base_url": (os.getenv("EMBED_BASE_URL", "").strip() or _DEFAULT_ONLINE_BASE_URL).rstrip("/"),
        "api_key": os.getenv("EMBED_API_KEY", "").strip() or os.getenv("QWEN_API_KEY", "").strip(),
        "user": os.getenv("MIDEA_AIGC_USER", "").strip(),
        "dimensions": _parse_optional_int(os.getenv("EMBED_DIMENSIONS", "")),
        "timeout": float(os.getenv("EMBED_TIMEOUT", str(_DEFAULT_TIMEOUT)).strip() or _DEFAULT_TIMEOUT),
    }


class OllamaEmbeddingClient:
    def __init__(self, *, base_url: str, model: str, timeout: float = _DEFAULT_TIMEOUT) -> None:
        if not base_url:
            raise ValueError("OLLAMA_HOST 未设置，无法调用本地 Ollama embedding 服务。")
        if not model:
            raise ValueError("EMBED_MODEL 未设置，无法调用本地 Ollama embedding 服务。")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._session = requests.Session()
        self._session.trust_env = False

    def _embed(self, texts: List[str]) -> List[List[float]]:
        clean_texts = [text if isinstance(text, str) else str(text) for text in texts]
        response = self._session.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": clean_texts},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list):
            raise RuntimeError(f"Ollama 返回结构异常: {data}")
        if embeddings and isinstance(embeddings[0], (int, float)):
            return [list(embeddings)]
        return [list(vec) for vec in embeddings]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return self._embed(texts)

    def embed_query(self, text: str) -> List[float]:
        cache_key = f"{self.model}:{text}"
        cached = _cache_get_query(cache_key)
        if cached is not None:
            return cached
        vectors = self._embed([text])
        vec = vectors[0] if vectors else []
        if vec:
            _cache_set_query(cache_key, vec)
        return vec


class OnlineEmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        user: str,
        dimensions: Optional[int] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        if not api_key:
            raise ValueError("EMBED_API_KEY 未设置，无法调用在线 embedding 服务。")
        if not user:
            raise ValueError("MIDEA_AIGC_USER 未设置，无法调用在线 embedding 服务。")
        if not base_url:
            raise ValueError("EMBED_BASE_URL 未设置，无法调用在线 embedding 服务。")
        if not model:
            raise ValueError("EMBED_MODEL 未设置，无法调用在线 embedding 服务。")

        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self._client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            default_headers={"AIGC-USER": user},
            timeout=timeout,
            max_retries=0,
        )

    def _embed_once(self, texts: List[str]) -> List[List[float]]:
        clean_texts = [text if isinstance(text, str) else str(text) for text in texts]
        request_args = {"model": self.model, "input": clean_texts}
        if self.dimensions is not None:
            request_args["dimensions"] = self.dimensions
        response = self._client.embeddings.create(**request_args)
        data = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in data]

    def _embed(self, texts: List[str]) -> List[List[float]]:
        retry_cfg = _embedding_retry_settings()
        max_retries = int(retry_cfg["max_retries"])
        base_delay = float(retry_cfg["base_delay"])
        last_error: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                return self._embed_once(texts)
            except Exception as exc:
                last_error = exc
                if not _is_retryable_embedding_error(exc) or attempt >= max_retries:
                    raise
                wait_seconds = base_delay * (2 ** (attempt - 1))
                print(
                    f"[embedding] 限流重试 {attempt}/{max_retries}，"
                    f"{wait_seconds:.1f}s 后重试: {exc}"
                )
                time.sleep(wait_seconds)

        raise RuntimeError(f"嵌入失败: {last_error}")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return self._embed(texts)

    def embed_query(self, text: str) -> List[float]:
        cache_key = f"{self.model}:{text}"
        cached = _cache_get_query(cache_key)
        if cached is not None:
            return cached
        vectors = self._embed([text])
        vec = vectors[0] if vectors else []
        if vec:
            _cache_set_query(cache_key, vec)
        return vec


def build_embedding_client(
    *,
    model: Optional[str] = None,
    dimensions: Optional[int] = None,
) -> Union[OllamaEmbeddingClient, OnlineEmbeddingClient]:
    try:
        from core.network_env import configure_runtime_network_env
        configure_runtime_network_env()
    except Exception:
        pass
    settings = get_embedding_settings()
    backend = str(settings["backend"])
    chosen_model = model or str(settings["model"])
    if backend == "ollama":
        return OllamaEmbeddingClient(
            base_url=str(settings["ollama_host"]),
            model=chosen_model,
            timeout=float(settings["timeout"]),
        )
    return OnlineEmbeddingClient(
        base_url=str(settings["base_url"]),
        api_key=str(settings["api_key"]),
        model=chosen_model,
        user=str(settings["user"]),
        dimensions=dimensions if dimensions is not None else settings["dimensions"],
        timeout=float(settings["timeout"]),
    )
