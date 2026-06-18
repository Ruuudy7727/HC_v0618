#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config import get_product_by_id
from core.gemini_chat import gemini_chat_once
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


def answer_question(
    question: str,
    product_id: Optional[str] = None,
    history: Optional[List[str]] = None,
) -> ChatResult:
    question = (question or "").strip()
    if not question:
        return ChatResult(
            answer="请输入问题。",
            product_id=product_id,
            display_name=get_display_name(product_id),
        )

    chunks = hybrid_search(question, product_id=product_id)
    if not chunks or chunks[0].score < _min_score():
        return ChatResult(
            answer=NO_RESULT_MESSAGE,
            sources=[],
            product_id=product_id,
            display_name=get_display_name(product_id),
        )

    image_catalog = build_image_catalog(chunks, max_images=_MAX_VISION_IMAGES)
    context = format_context(chunks, catalog=image_catalog)
    display_name = get_display_name(product_id)
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

    answer, _usage = gemini_chat_once(
        user_prompt,
        SYSTEM_PROMPT,
        temperature=0.2,
        max_tokens=4096,
        image_paths=image_abs_paths or None,
    )

    return ChatResult(
        answer=answer,
        sources=_chunks_to_sources(chunks),
        product_id=product_id,
        display_name=display_name,
    )
