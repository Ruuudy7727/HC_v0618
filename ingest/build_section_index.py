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
    "登录": ["登录", "注册", "账号", "密码", "验证码", "退出"],
    "设备": ["设备", "电站", "绑定", "添加", "解绑", "分组", "站点"],
    "监控": ["监控", "实时", "曲线", "数据", "SOC", "功率", "运行状态", "首页"],
    "告警": ["告警", "报警", "故障", "异常", "事件", "提醒"],
    "报表": ["报表", "统计", "导出", "下载", "历史数据"],
    "权限": ["权限", "角色", "用户管理", "组织", "成员"],
    "工单": ["工单", "维修", "派工", "验收", "安装维修"],
    "反馈": ["反馈", "帮助中心", "产品咨询", "产品问题"],
    "场站": ["场站", "建站", "站点", "SN", "防逆流", "工作模式"],
    "概述": ["概述", "简介", "功能", "平台介绍", "使用说明", "模块", "操作指南"],
}

_SKIP_CHUNK_TYPES = {"toc", "meta"}
_SKIP_SECTION_RE = re.compile(r"^目录|修订记录|版本记录")


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
        for bucket in buckets:
            if title not in prod[bucket]:
                prod[bucket].append(title)
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
