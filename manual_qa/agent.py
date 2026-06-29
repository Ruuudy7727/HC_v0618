#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from config import get_product_by_id
from core.gemini_chat import gemini_chat_once, gemini_chat_stream
from manual_qa.prompts import (
    GENERAL_USER_TEMPLATE,
    NO_RESULT_MESSAGE,
    PRODUCT_USER_TEMPLATE,
    SYSTEM_PROMPT,
)
from manual_qa.retriever import (
    RetrievedChunk,
    build_image_catalog,
    format_context,
    get_display_name,
    hybrid_search,
)

_MAX_VISION_IMAGES = int(os.getenv("GEMINI_MAX_IMAGES", "5"))


@dataclass
class ChatResult:
    answer: str
    sources: List[Dict[str, Any]] = field(default_factory=list)
    product_id: Optional[str] = None
    display_name: str = "全部产品"


@dataclass
class _ChatPrep:
    early_result: Optional[ChatResult] = None
    user_prompt: str = ""
    image_paths: Optional[List[str]] = None
    image_entries: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    product_id: Optional[str] = None
    display_name: str = "全部产品"


def _min_score() -> float:
    try:
        return float(os.getenv("RETRIEVAL_MIN_SCORE", "0.15"))
    except ValueError:
        return 0.15


def _chunks_to_sources(chunks: List[RetrievedChunk]) -> List[Dict[str, Any]]:
    return [
        {
            "product_id": chunk.product_id,
            "display_name": chunk.display_name,
            "section_title": chunk.section_title,
            "score": chunk.score,
            "snippet": chunk.content[:300],
            "source": chunk.source,
        }
        for chunk in chunks
    ]


def _prepare_chat(
    question: str,
    product_id: Optional[str] = None,
    history: Optional[List[str]] = None,
) -> _ChatPrep:
    question = (question or "").strip()
    display_name = get_display_name(product_id)

    if not question:
        return _ChatPrep(
            early_result=ChatResult(
                answer="请输入问题。",
                product_id=product_id,
                display_name=display_name,
            )
        )

    chunks = hybrid_search(question, product_id=product_id)
    if not chunks or chunks[0].score < _min_score():
        return _ChatPrep(
            early_result=ChatResult(
                answer=NO_RESULT_MESSAGE,
                sources=[],
                product_id=product_id,
                display_name=display_name,
            )
        )

    image_catalog = build_image_catalog(chunks, max_images=_MAX_VISION_IMAGES)
    context = format_context(chunks, catalog=image_catalog)
    image_abs_paths = image_catalog.abs_paths

    history_block = ""
    if history:
        recent = [h for h in history[-6:] if h]
        if recent:
            history_block = "\n\n最近对话:\n" + "\n".join(recent) + "\n"

    if product_id:
        product = get_product_by_id(product_id) or {}
        user_prompt = PRODUCT_USER_TEMPLATE.format(
            display_name=product.get("display_name", display_name),
            question=question,
            context=context,
        )
    else:
        user_prompt = GENERAL_USER_TEMPLATE.format(question=question, context=context)

    user_prompt += history_block

    image_entries = [
        {
            "image_id": entry.image_id,
            "display_url": entry.display_url,
            "abs_path": entry.abs_path,
            "caption_hint": entry.caption_hint,
        }
        for entry in image_catalog.entries
    ]

    return _ChatPrep(
        user_prompt=user_prompt,
        image_paths=image_abs_paths or None,
        image_entries=image_entries,
        sources=_chunks_to_sources(chunks),
        product_id=product_id,
        display_name=display_name,
    )


def answer_question(
    question: str,
    product_id: Optional[str] = None,
    history: Optional[List[str]] = None,
) -> ChatResult:
    prep = _prepare_chat(question, product_id=product_id, history=history)
    if prep.early_result:
        return prep.early_result

    answer, _usage = gemini_chat_once(
        prep.user_prompt,
        SYSTEM_PROMPT,
        temperature=0.2,
        max_tokens=4096,
        image_paths=prep.image_paths,
    )

    return ChatResult(
        answer=answer,
        sources=prep.sources,
        product_id=prep.product_id,
        display_name=prep.display_name,
    )


def answer_question_stream(
    question: str,
    product_id: Optional[str] = None,
    history: Optional[List[str]] = None,
) -> Iterator[Tuple[str, Any]]:
    """流式问答。依次 yield:
    - ('meta', {sources, product_id, display_name})
    - ('delta', str) 文本增量
    - ('done', ChatResult) 完整结果
    """
    prep = _prepare_chat(question, product_id=product_id, history=history)
    meta = {
        "sources": prep.sources,
        "product_id": prep.product_id,
        "display_name": prep.display_name,
        "images": prep.image_entries,
    }

    if prep.early_result:
        yield ("meta", meta)
        yield ("delta", prep.early_result.answer)
        yield ("done", prep.early_result)
        return

    yield ("meta", meta)

    answer_parts: List[str] = []
    for delta in gemini_chat_stream(
        prep.user_prompt,
        SYSTEM_PROMPT,
        temperature=0.2,
        max_tokens=4096,
        image_paths=prep.image_paths,
    ):
        answer_parts.append(delta)
        yield ("delta", delta)

    yield (
        "done",
        ChatResult(
            answer="".join(answer_parts),
            sources=prep.sources,
            product_id=prep.product_id,
            display_name=prep.display_name,
        ),
    )
