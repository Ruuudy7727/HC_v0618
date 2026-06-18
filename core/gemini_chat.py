#!/usr/bin/env python
# -*- coding: utf-8 -*-

import base64
import mimetypes
import os
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=str(_PROJECT_ROOT / ".env"), override=False)

MIDEA_API_KEY = os.getenv("MIDEA_API_KEY", "")
MIDEA_AIGC_USER = os.getenv("MIDEA_AIGC_USER", "user")
GEMINI_URL_SYNC = os.getenv(
    "GEMINI_URL_SYNC",
    "https://aimpapi.midea.com/t-aigc/mip-chat-app/gemini/official/standard/sync/v1/chat/completions",
)
GEMINI_AIMP_BIZ_ID = os.getenv("GEMINI_AIMP_BIZ_ID", "gemini-2.5-flash")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# 视觉开关（默认开启）；每次请求最多附带的图片数量
_VISION_ENABLED = os.getenv("GEMINI_VISION_ENABLED", "true").strip().lower() not in ("0", "false", "no")
_MAX_INLINE_IMAGES = int(os.getenv("GEMINI_MAX_IMAGES", "5"))
_MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 超过 4MB 的图片跳过


def _encode_image_part(path: str) -> Optional[Dict]:
    """读取本地图片并 base64 编码为 Gemini inlineData part，失败返回 None。"""
    try:
        p = Path(path)
        if not p.is_file():
            return None
        size = p.stat().st_size
        if size > _MAX_IMAGE_BYTES:
            print(f"[vision] 跳过过大图片 ({size // 1024}KB): {p.name}")
            return None
        mime, _ = mimetypes.guess_type(str(p))
        if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            mime = "image/jpeg"
        with open(p, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return {"inlineData": {"mimeType": mime, "data": data}}
    except Exception as e:
        print(f"[vision] 图片编码失败 {path}: {e}")
        return None


def _build_parts(user_text: str, image_paths: Optional[List[str]]) -> List[Dict]:
    """将文本和图片组装为 Gemini parts 列表（text 在前，图片紧随其后）。"""
    parts: List[Dict] = [{"text": user_text}]
    if not image_paths or not _VISION_ENABLED:
        return parts
    added = 0
    seen: set = set()
    for path in image_paths:
        if path in seen or added >= _MAX_INLINE_IMAGES:
            break
        seen.add(path)
        part = _encode_image_part(path)
        if part:
            parts.append(part)
            added += 1
    if added:
        print(f"[vision] 附加 {added} 张图片至请求")
    return parts


def _post_gemini(headers: Dict, body: Dict) -> Tuple[str, Dict]:
    resp = requests.post(
        GEMINI_URL_SYNC,
        headers=headers,
        json=body,
        timeout=180,
        proxies={"http": None, "https": None},
    )
    resp.raise_for_status()
    data = resp.json()
    candidate = data.get("candidates", [{}])[0]
    text = (
        candidate
        .get("content", {})
        .get("parts", [{}])[0]
        .get("text", "")
    )
    finish_reason = candidate.get("finishReason", "")
    if finish_reason and finish_reason != "STOP":
        print(f"[gemini] finishReason={finish_reason}, text_len={len(text)}")
    return text, data.get("usageMetadata", {})


def gemini_chat_once(
    user_text: str,
    system_instruction: str,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    image_paths: Optional[List[str]] = None,
) -> Tuple[str, Dict]:
    """调用 Gemini（经美的 AIMP 网关）。支持可选的图片多模态输入。

    若多模态请求失败（如网关不支持），自动降级为纯文本重试。
    """
    headers = {
        "Authorization": f"Bearer {MIDEA_API_KEY}",
        "Aimp-Biz-Id": GEMINI_AIMP_BIZ_ID,
        "AIGC-USER": MIDEA_AIGC_USER,
        "Content-Type": "application/json; charset=utf-8",
    }
    parts = _build_parts(user_text, image_paths)
    has_images = len(parts) > 1

    body = {
        "model": GEMINI_MODEL,
        "contents": [{"role": "user", "parts": parts}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }

    try:
        return _post_gemini(headers, body)
    except Exception as e:
        if has_images:
            print(f"[vision] 多模态请求失败，降级纯文本重试: {e}")
            body_text = {
                **body,
                "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            }
            try:
                return _post_gemini(headers, body_text)
            except Exception as e2:
                print(f"Gemini Sync Error (text-only fallback): {e2}")
                traceback.print_exc()
                return f"Error: {str(e2)}", {}
        print(f"Gemini Sync Error: {e}")
        traceback.print_exc()
        return f"Error: {str(e)}", {}
