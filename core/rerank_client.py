#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Cross-Encoder 重排客户端（BGE reranker）。"""

import os
from pathlib import Path
from typing import List, Optional, Tuple, TypeVar

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(_PROJECT_ROOT / ".env"), override=False)
except Exception:
    pass

T = TypeVar("T")

_reranker = None
_reranker_load_failed = False

_DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"
_SNIPPET_MAX_CHARS = int(os.getenv("RERANK_SNIPPET_CHARS", "512"))


def is_rerank_enabled() -> bool:
    return os.getenv("RERANK_ENABLED", "true").strip().lower() not in ("0", "false", "no")


def _get_reranker():
    global _reranker, _reranker_load_failed
    if _reranker_load_failed:
        return None
    if _reranker is not None:
        return _reranker
    try:
        from FlagEmbedding import FlagReranker
        model_name = os.getenv("RERANK_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
        use_fp16 = os.getenv("RERANK_USE_FP16", "true").strip().lower() not in ("0", "false", "no")
        _reranker = FlagReranker(model_name, use_fp16=use_fp16)
        print(f"[rerank] 已加载模型: {model_name}")
        return _reranker
    except Exception as e:
        _reranker_load_failed = True
        print(f"[rerank] 模型加载失败，将跳过重排: {e}")
        return None


def build_rerank_snippet(section_title: str, content: str) -> str:
    """构造送入 Cross-Encoder 的短文本（标题 + 正文前 N 字）。"""
    title = (section_title or "").strip()
    body = (content or "").strip()
    if body.startswith("["):
        first_nl = body.find("\n")
        if first_nl > 0:
            body = body[first_nl + 1 :].strip()
    if len(body) > _SNIPPET_MAX_CHARS:
        body = body[:_SNIPPET_MAX_CHARS]
    if title and body:
        return f"{title}\n{body}"
    return title or body


def rerank_passages(
    query: str,
    passages: List[str],
    normalize: bool = True,
) -> Optional[List[float]]:
    """对 query-passage 对打分，失败返回 None。"""
    if not passages or not (query or "").strip():
        return None
    reranker = _get_reranker()
    if reranker is None:
        return None
    pairs = [[query, p] for p in passages]
    try:
        scores = reranker.compute_score(pairs, normalize=normalize)
        if isinstance(scores, (int, float)):
            return [float(scores)]
        return [float(s) for s in scores]
    except Exception as e:
        print(f"[rerank] 打分失败，跳过重排: {e}")
        return None


def rerank_items(
    query: str,
    items: List[T],
    snippet_fn,
    top_n: int,
    candidate_k: Optional[int] = None,
) -> List[Tuple[T, float]]:
    """对任意对象列表重排，snippet_fn(item) -> str。"""
    if not items:
        return []
    k = candidate_k or int(os.getenv("RERANK_CANDIDATE_K", "15"))
    candidates = items[: max(1, k)]
    passages = [snippet_fn(c) for c in candidates]
    scores = rerank_passages(query, passages)
    if scores is None or len(scores) != len(candidates):
        return [(c, getattr(c, "score", 0.0) if hasattr(c, "score") else 0.0) for c in candidates[:top_n]]
    ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    return ranked[:top_n]
