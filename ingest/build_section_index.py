#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 kv_store 生成 section_index.json，供检索时自动扩展子查询。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

# 话题桶：章节标题/path 含任一关键词则归入该桶
_TOPIC_BUCKETS: Dict[str, List[str]] = {
    "安装": ["固定安装", "安装工具", "安装环境", "安装地点", "地基", "防护措施", "电气接线准备"],
    "运输": ["运输", "叉车", "起吊", "吊装", "运输条件", "起吊作业"],
    "接线": ["接线", "端子", "接地", "等电位", "铜线", "热缩", "电气连接", "交流接线"],
    "上电": ["上电", "开机", "投运", "启动"],
    "下电": ["下电", "停机", "断电", "停运"],
    "维护": ["维护", "更换", "检修", "保养", "清洁", "准备工具"],
    "故障": ["故障", "事件", "排查", "告警码"],
    "概述": ["产品概述", "外观", "机械参数", "内部设计", "产品描述"],
}

_SKIP_CHUNK_TYPES = {"toc", "meta"}
_SKIP_SECTION_RE = re.compile(r"^目录|商标|读者对象|修订记录")


def _bucket_for_chunk(section_title: str, section_path: str, chunk_type: str) -> List[str]:
    if chunk_type in _SKIP_CHUNK_TYPES:
        return []
    text = f"{section_path} {section_title}"
    if _SKIP_SECTION_RE.search(section_title or ""):
        return []
    hits: List[str] = []
    for bucket, keywords in _TOPIC_BUCKETS.items():
        if any(kw in text for kw in keywords):
            hits.append(bucket)
    return hits


def build_section_index(kv_store: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, List[str]]]:
    """返回 {product_id: {bucket: [section_title, ...]}}。"""
    index: Dict[str, Dict[str, List[str]]] = {}
    for obj in kv_store.values():
        if not isinstance(obj, dict):
            continue
        pid = str(obj.get("product_id", ""))
        if not pid:
            continue
        title = str(obj.get("section_title", "")).strip()
        path = str(obj.get("section_path", "")).strip()
        ctype = str(obj.get("chunk_type", "")).strip()
        if not title:
            continue
        buckets = _bucket_for_chunk(title, path, ctype)
        if not buckets:
            continue
        prod = index.setdefault(pid, {k: [] for k in _TOPIC_BUCKETS})
        label = title
        if path and title not in path:
            label = f"{path.split('>')[-1].strip()} {title}".strip()
        for bucket in buckets:
            if title not in prod[bucket]:
                prod[bucket].append(title)
    # 去掉空桶
    for pid in list(index.keys()):
        index[pid] = {k: v for k, v in index[pid].items() if v}
    return index


def save_section_index(kv_path: Path, out_path: Path) -> Dict[str, Dict[str, List[str]]]:
    with kv_path.open("r", encoding="utf-8") as f:
        kv_store = json.load(f)
    index = build_section_index(kv_store)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    return index
