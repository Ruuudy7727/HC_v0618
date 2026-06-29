#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
from typing import Any, Dict, List, Optional

_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def api_public_base_url() -> str:
    """API 对外 Base URL，用于把 /kb_images/... 转为前端可访问的完整地址。"""
    for key in ("API_PUBLIC_URL", "GRADIO_PUBLIC_URL"):
        value = os.getenv(key, "").strip().rstrip("/")
        if value:
            return value
    return ""


def kb_rel_from_url(url: str) -> Optional[str]:
    """从各类 kb 图片引用中提取相对路径（doc_id/filename）。"""
    raw = (url or "").strip().replace("\\", "/")
    if not raw:
        return None

    marker = "/kb_images/"
    idx = raw.find(marker)
    if idx >= 0:
        return raw[idx + len(marker) :].lstrip("/")
    if raw.startswith("kb_images/"):
        return raw[len("kb_images/") :].lstrip("/")
    if raw.startswith("/kb_images/"):
        return raw[len("/kb_images/") :].lstrip("/")
    return None


def public_kb_image_url(url_or_rel: str) -> str:
    """将 kb 图片引用规范化为前端可用的 URL。"""
    raw = (url_or_rel or "").strip()
    if not raw:
        return raw
    if raw.startswith(("http://", "https://")):
        return raw

    rel = kb_rel_from_url(raw)
    if not rel:
        return raw

    base = api_public_base_url()
    if base:
        return f"{base}/kb_images/{rel}"
    return f"/kb_images/{rel}"


def rewrite_kb_image_urls_in_markdown(text: str) -> str:
    """修正回答 Markdown 中的配图 URL（含 LLM 漏写前导 / 的情况）。"""

    def _repl(match: re.Match) -> str:
        alt, url = match.group(1), match.group(2)
        return f"![{alt}]({public_kb_image_url(url)})"

    return _MD_IMAGE_RE.sub(_repl, text or "")


def public_image_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """流式 meta 中的 images 列表，仅返回前端需要的字段。"""
    public: List[Dict[str, Any]] = []
    for item in entries or []:
        public.append(
            {
                "image_id": item.get("image_id", ""),
                "display_url": public_kb_image_url(str(item.get("display_url", "") or "")),
                "caption_hint": item.get("caption_hint", ""),
            }
        )
    return public
