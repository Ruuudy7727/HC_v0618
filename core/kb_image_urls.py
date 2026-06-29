#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
from typing import Any, Dict, List, Optional

_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_FIGURE_REF_RE = re.compile(r"\[(图-\d+)\]")
_DOC_IMAGE_RE = re.compile(
    r"(?:https?://[^/]+/)?(?:kb[._-]?images/)?(doc-[a-f0-9]+/[a-f0-9]+\.(?:jpg|jpeg|png|webp|gif))",
    re.IGNORECASE,
)


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

    match = _DOC_IMAGE_RE.search(raw)
    if match:
        return match.group(1)
    return None


def public_kb_image_url(url_or_rel: str) -> str:
    """将 kb 图片引用规范化为前端可用的 URL。"""
    raw = (url_or_rel or "").strip()
    if not raw:
        return raw

    rel = kb_rel_from_url(raw)
    if not rel:
        if raw.startswith(("http://", "https://")):
            return raw
        return raw

    base = api_public_base_url()
    if base:
        return f"{base}/kb_images/{rel}"
    return f"/kb_images/{rel}"


def inject_catalog_images_into_markdown(
    text: str,
    entries: List[Dict[str, Any]],
) -> str:
    """将 LLM 回答中的 [图-N] 占位符替换为检索 catalog 中的 Markdown 图片。"""
    if not text or not entries:
        return text or ""

    id_to_entry: Dict[str, Dict[str, Any]] = {}
    for item in entries:
        image_id = str(item.get("image_id", "") or "").strip()
        if image_id:
            id_to_entry[image_id] = item

    def _repl_ref(match: re.Match) -> str:
        image_id = match.group(1)
        entry = id_to_entry.get(image_id)
        if not entry:
            return match.group(0)
        alt = str(entry.get("caption_hint") or image_id).strip()
        url = public_kb_image_url(str(entry.get("display_url", "") or ""))
        return f"![{alt}]({url})"

    return _FIGURE_REF_RE.sub(_repl_ref, text)


def rewrite_kb_image_urls_in_markdown(text: str) -> str:
    """修正回答 Markdown 中 LLM 误写的配图 URL（兼容旧输出）。"""

    def _repl(match: re.Match) -> str:
        alt, url = match.group(1), match.group(2)
        return f"![{alt}]({public_kb_image_url(url)})"

    return _MD_IMAGE_RE.sub(_repl, text or "")


def sanitize_all_kb_image_references(text: str) -> str:
    """修正正文中所有 kb 配图引用（含 LLM 幻觉的裸 URL，如 kb.aliyun.com/kb_images/...）。"""

    def _repl(match: re.Match) -> str:
        rel = match.group(1)
        return public_kb_image_url(rel)

    return _DOC_IMAGE_RE.sub(_repl, text or "")


def prepare_public_answer(text: str, entries: List[Dict[str, Any]]) -> str:
    """先用 catalog 注入 [图-N]，再修正 Markdown 图片与裸 URL（覆盖 LLM 幻觉域名）。"""
    text = inject_catalog_images_into_markdown(text, entries)
    text = rewrite_kb_image_urls_in_markdown(text)
    return sanitize_all_kb_image_references(text)


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
