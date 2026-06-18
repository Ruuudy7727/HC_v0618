#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config import get_product_by_id, match_products_in_query
from core.local_db import init_local_kb, retrieve_hybrid
from core.rerank_client import build_rerank_snippet, is_rerank_enabled, rerank_items

_KB_IMAGES_ROOT = Path(__file__).resolve().parent.parent / "rag_data" / "all"

# ────────────────────────────────────────────────────────────────────────────
# 话题感知查询扩展表
# 每行 = (检测正则, 额外子查询列表)
# 目的：绕过 BM25 短文档偏差，把与话题强相关的节名/关键词加入搜索
# ────────────────────────────────────────────────────────────────────────────
# 话题触发器：正则 → section_index.json 中的桶名（自动扩展子查询）
_TOPIC_TRIGGERS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"如何安装|安装方法|安装步骤|机械安装|怎么安装|安装流程|安装过程|安装要求|安装 ?储能|安装操作"), "安装"),
    (re.compile(r"如何接线|接线步骤|电气连接|配线|线缆|交流接线|怎么接线|接线方法|接线顺序"), "接线"),
    (re.compile(r"上电|开机|启动|投运|运行调试|怎么开|如何开"), "上电"),
    (re.compile(r"下电|关机|停机|断电|停运|如何关"), "下电"),
    (re.compile(r"维护|保养|维修|更换|检修|定期检查"), "维护"),
    (re.compile(r"故障|报警|告警|事件|排查|错误|异常|不正常"), "故障"),
    (re.compile(r"运输|搬运|叉车|起吊|移动"), "运输"),
    (re.compile(r"电池|储能系统|储能容量|放电容量|充放电|SOC|电量|电芯"), "概述"),
]

# 无 section_index 或桶为空时的静态兜底子查询
_TOPIC_FALLBACK: Dict[str, List[str]] = {
    "安装": ["3.5 固定安装", "4.4.1 安装工具", "3.4.1 安装地点选择", "3.4 安装环境要求"],
    "运输": ["3.2 叉车运输", "3.3.2 起吊作业", "3.1 运输条件"],
    "接线": ["接线总览 交流输出接线", "制作接线端子 铜线接入", "接地连接 等电位"],
    "上电": ["上电步骤 上电前检查"],
    "下电": ["下电操作 停机"],
    "维护": ["维护说明 更换防雷器"],
    "故障": ["事件 故障排查 告警码"],
    "概述": ["产品概述 外观介绍 机械参数"],
}

_SECTION_INDEX_PATH = Path(__file__).resolve().parent.parent / "rag_data" / "all" / "section_index.json"
_section_index_cache: Optional[Dict[str, Dict[str, List[str]]]] = None

_INTRO_QUERY_RE = re.compile(
    r"(介绍|概述|概况|是什么|什么样|功能|特点|用途|简介|讲讲|说说|这个设备|该产品)"
)
_OVERVIEW_SECTION_KEYWORDS = (
    "产品概述",
    "产品描述",
    "外观介绍",
    "机械参数",
    "内部设计",
    "核心功能",
    "产品信息概述",
    "读者对象",
)
_LOW_VALUE_SECTIONS: Set[str] = {
    "目录",
    "注意",
    "警告",
    "危险",
    "告警",
    "A 告警",
    "！ 告警",
    "商标",
    "软件授权",
    "证据",
    "条件",
    "手册警示符号",
    "科陆",
}
# 话题相关性：如果 section_title 包含这些词，说明与安装话题相关
_INSTALL_TOPIC_KEYWORDS = (
    "安装", "固定", "地基", "地点", "运输", "起吊",
    "接线", "电气", "工具", "端子",
)
_OPERATION_TOPIC_KEYWORDS = (
    "上电", "下电", "开机", "停机", "启动", "投运",
)
_INSTALL_BOOST_KEYWORDS = (
    "固定安装", "安装工具", "安装环境", "安装地点", "地基", "电气接线准备",
)
_INSTALL_OFF_TOPIC_KEYWORDS = (
    "质保", "免责", "商标", "软件授权", "证据", "目录",
)
_FAULT_BOOST_KEYWORDS = (
    "故障排查", "故障", "事件", "告警", "报警", "排查", "异常",
)
_FAULT_OFF_TOPIC_KEYWORDS = (
    "质量保证", "免责", "质保", "维护项目", "维护周期", "运行和调试",
    "运行调试", "商标", "软件授权", "证据", "目录",
)
_MAINT_BOOST_KEYWORDS = (
    "维护", "保养", "更换", "检修", "周期", "清洁",
)
_MAINT_OFF_TOPIC_KEYWORDS = (
    "质量保证", "免责", "质保", "故障排查", "安装", "商标",
)
# context 中内容太短且不含表格/图片的 chunk 跳过
_CONTEXT_MIN_CONTENT_LEN = 60
# 超短纯噪声 chunk 降权（无图片时）
_SHORT_CHUNK_THRESHOLD = 80
# 过滤手册内嵌小图标（告警符号、页眉图标等），宽高均低于此值的图片不展示/不传 vision
_MIN_IMAGE_DIMENSION = int(os.getenv("IMAGE_MIN_DIMENSION", "80"))
_TOPIC_EXTRA_MAX = int(os.getenv("RETRIEVAL_TOPIC_EXTRA_MAX", "3"))
_EMBED_QUERY_INTERVAL = float(os.getenv("EMBED_QUERY_INTERVAL", "0.35"))


@dataclass
class RetrievedChunk:
    content: str
    product_id: str = ""
    display_name: str = ""
    section_title: str = ""
    source: str = ""
    score: float = 0.0
    image_paths: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def _parse_image_paths(meta: Dict[str, Any]) -> List[str]:
    raw = meta.get("image_paths", "")
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str) and raw.strip():
        return [p.strip() for p in raw.split(";") if p.strip()]
    return []


def _to_chunk(item: Dict[str, Any]) -> RetrievedChunk:
    meta = item.get("metadata", {}) or {}
    return RetrievedChunk(
        content=item.get("content", ""),
        product_id=str(meta.get("product_id", "")),
        display_name=str(meta.get("display_name", "")),
        section_title=str(meta.get("section_title", "")),
        source=str(meta.get("source") or meta.get("file_path") or ""),
        score=float(meta.get("score", 0.0)),
        image_paths=_parse_image_paths(meta),
        metadata=meta,
    )


def _load_section_index() -> Dict[str, Dict[str, List[str]]]:
    global _section_index_cache
    if _section_index_cache is not None:
        return _section_index_cache
    if not _SECTION_INDEX_PATH.is_file():
        _section_index_cache = {}
        return _section_index_cache
    try:
        import json
        with _SECTION_INDEX_PATH.open("r", encoding="utf-8") as f:
            _section_index_cache = json.load(f)
    except Exception:
        _section_index_cache = {}
    return _section_index_cache


def _get_topic_queries(query: str, product_id: Optional[str] = None) -> List[str]:
    """根据话题检测 + section_index 返回扩展子查询（章节标题）。"""
    extra: List[str] = []
    index = _load_section_index()
    product_buckets = index.get(product_id or "", {}) if product_id else {}

    for pattern, bucket in _TOPIC_TRIGGERS:
        if not pattern.search(query):
            continue
        titles = product_buckets.get(bucket, [])
        if titles:
            extra.extend(titles[:_TOPIC_EXTRA_MAX])
        else:
            extra.extend(_TOPIC_FALLBACK.get(bucket, []))

    return list(dict.fromkeys(extra))


def _is_product_intro_query(query: str) -> bool:
    return bool(_INTRO_QUERY_RE.search(query or ""))


def _chunk_key(chunk: RetrievedChunk) -> str:
    meta = chunk.metadata or {}
    for key in ("_id", "chunk_id", "id"):
        if meta.get(key):
            return str(meta[key])
    return f"{chunk.product_id}:{chunk.section_title}:{chunk.content[:120]}"


def _postrank(
    chunks: List[RetrievedChunk],
    is_intro: bool,
    topic_re: Optional[re.Pattern] = None,
    topic_bucket: Optional[str] = None,
) -> List[RetrievedChunk]:
    """统一后处理：介绍类查询提升概述节，低价值/偏题 chunk 降权。"""
    reranked: List[RetrievedChunk] = []
    for chunk in chunks:
        title = (chunk.section_title or "").strip()
        score = chunk.score
        if is_intro and any(kw in title for kw in _OVERVIEW_SECTION_KEYWORDS):
            score = min(1.0, score + 0.30)
        if topic_bucket == "安装":
            if any(kw in title for kw in _INSTALL_BOOST_KEYWORDS):
                score = min(1.0, score + 0.25)
            if any(kw in title for kw in ("运输", "叉车", "起吊", "吊装")):
                score = max(0.0, score - 0.20)
            if any(kw in title for kw in _OPERATION_TOPIC_KEYWORDS):
                score = max(0.0, score - 0.50)
            if any(kw in title for kw in _INSTALL_OFF_TOPIC_KEYWORDS):
                score = max(0.0, score - 0.45)
        elif topic_bucket == "故障":
            if any(kw in title for kw in _FAULT_BOOST_KEYWORDS):
                score = min(1.0, score + 0.25)
            if any(kw in title for kw in _MAINT_BOOST_KEYWORDS):
                score = max(0.0, score - 0.30)
            if any(kw in title for kw in _FAULT_OFF_TOPIC_KEYWORDS):
                score = max(0.0, score - 0.45)
        elif topic_bucket == "维护":
            if any(kw in title for kw in _MAINT_BOOST_KEYWORDS):
                score = min(1.0, score + 0.25)
            if any(kw in title for kw in _FAULT_BOOST_KEYWORDS):
                score = max(0.0, score - 0.15)
            if any(kw in title for kw in ("质量保证", "免责", "质保", "安装")):
                score = max(0.0, score - 0.40)
        # 精准低价值节名
        if title in _LOW_VALUE_SECTIONS or re.match(r"^目录", title):
            score = max(0.0, score - 0.40)
        body = chunk.content.strip()
        # 内容过短且无图片：降权（BM25 偏好短文档的系统性偏差修正）
        if (len(body) < _SHORT_CHUNK_THRESHOLD and not chunk.image_paths
                and "<table" not in body.lower()):
            score = max(0.0, score - 0.25)
        # 查询话题不匹配：若当前问的是安装但 chunk 是操作类，轻微降权
        if topic_re is not None:
            # 检测 chunk 是否属于明显偏离话题的操作章节
            is_op = any(kw in title for kw in _OPERATION_TOPIC_KEYWORDS)
            if is_op and not topic_re.search(title):
                score = max(0.0, score - 0.20)
        reranked.append(replace(chunk, score=score))
    reranked.sort(key=lambda c: c.score, reverse=True)
    return reranked


def _title_has_any(title: str, keywords: Tuple[str, ...]) -> bool:
    return any(kw in title for kw in keywords)


def _apply_topic_hard_filter(
    chunks: List[RetrievedChunk],
    topic_bucket: Optional[str],
) -> List[RetrievedChunk]:
    """话题硬过滤：剔除与当前意图明显无关的章节。"""
    if not topic_bucket:
        return chunks
    filtered: List[RetrievedChunk] = []
    for chunk in chunks:
        title = (chunk.section_title or "").strip()
        if topic_bucket == "安装":
            if (
                (_title_has_any(title, _OPERATION_TOPIC_KEYWORDS) or _title_has_any(title, _INSTALL_OFF_TOPIC_KEYWORDS))
                and not _title_has_any(title, _INSTALL_TOPIC_KEYWORDS)
            ):
                continue
        elif topic_bucket == "故障":
            if _title_has_any(title, ("质量保证", "免责", "质保", "商标", "软件授权")):
                continue
            if _title_has_any(title, ("运行和调试", "运行调试")) and not _title_has_any(title, _FAULT_BOOST_KEYWORDS):
                continue
        elif topic_bucket == "维护":
            if _title_has_any(title, ("质量保证", "免责", "质保")):
                continue
        filtered.append(chunk)
    return filtered


def _apply_score_gap_filter(
    chunks: List[RetrievedChunk],
    max_keep: int,
    min_keep: int = 2,
    gap: Optional[float] = None,
) -> List[RetrievedChunk]:
    """动态截断：与 top1 分数差距过大的候选不再保留。"""
    if not chunks:
        return []
    gap_val = gap if gap is not None else float(os.getenv("RETRIEVAL_SCORE_GAP", "0.25"))
    min_keep_val = int(os.getenv("RETRIEVAL_MIN_KEEP", str(min_keep)))
    top_score = chunks[0].score
    kept: List[RetrievedChunk] = []
    for i, chunk in enumerate(chunks):
        if i < min_keep_val:
            kept.append(chunk)
        elif i < max_keep and (top_score - chunk.score) <= gap_val:
            kept.append(chunk)
        else:
            break
    return kept


def _apply_cross_encoder_rerank(
    query: str,
    chunks: List[RetrievedChunk],
    top_n: int,
) -> List[RetrievedChunk]:
    if not is_rerank_enabled() or not chunks:
        return chunks

    def _snippet(chunk: RetrievedChunk) -> str:
        return build_rerank_snippet(chunk.section_title, chunk.content)

    candidate_k = int(os.getenv("RERANK_CANDIDATE_K", "15"))
    ranked = rerank_items(
        query,
        chunks,
        snippet_fn=_snippet,
        top_n=top_n,
        candidate_k=candidate_k,
    )
    return [replace(chunk, score=float(score)) for chunk, score in ranked]


def hybrid_search(
    query: str,
    product_id: Optional[str] = None,
    top_k: Optional[int] = None,
) -> List[RetrievedChunk]:
    init_local_kb()
    k = top_k or int(os.getenv("RETRIEVAL_TOP_K", "6"))

    is_intro = product_id and _is_product_intro_query(query)
    topic_extras = _get_topic_queries(query, product_id) if product_id else []
    boost_ids = None if product_id else match_products_in_query(query)

    # 构造所有查询：原始 + 介绍类扩展 + 话题类扩展
    base_queries: List[str] = [query]
    if is_intro:
        base_queries += [
            f"{query} 产品概述 产品描述 外观设计 机械参数 功能 应用场景",
            "2.1 产品概述 2.2 外观介绍 2.3 机械参数 2.4 内部设计",
        ]
    base_queries += topic_extras
    queries = list(dict.fromkeys(base_queries))

    if is_rerank_enabled():
        fetch_k = int(os.getenv("RETRIEVAL_FETCH_K", str(max(k * 4, 20))))
    else:
        fetch_k = max(k * 3, 12) if len(queries) > 1 else max(k * 5, 10)

    merged: Dict[str, RetrievedChunk] = {}
    for i, search_query in enumerate(queries):
        if i > 0 and _EMBED_QUERY_INTERVAL > 0:
            time.sleep(_EMBED_QUERY_INTERVAL)
        raw = retrieve_hybrid(
            query=search_query,
            top_k=fetch_k,
            product_id=product_id,
            boost_product_ids=boost_ids,
        )
        for item in raw:
            chunk = _to_chunk(item)
            key = _chunk_key(chunk)
            existing = merged.get(key)
            if existing is None or chunk.score > existing.score:
                merged[key] = chunk

    active_topic_re: Optional[re.Pattern] = None
    active_topic_bucket: Optional[str] = None
    for pattern, bucket in _TOPIC_TRIGGERS:
        if pattern.search(query):
            active_topic_re = pattern
            active_topic_bucket = bucket
            break

    chunks = _postrank(
        list(merged.values()),
        bool(is_intro),
        topic_re=active_topic_re,
        topic_bucket=active_topic_bucket,
    )
    chunks = _apply_topic_hard_filter(chunks, active_topic_bucket)
    chunks = _apply_cross_encoder_rerank(query, chunks, top_n=k)
    chunks = _apply_score_gap_filter(chunks, max_keep=k)

    min_score = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.15"))
    chunks = [c for c in chunks if c.score >= min_score]
    return chunks[:k]


def _read_image_dimensions(path: Path) -> Optional[Tuple[int, int]]:
    """读取 JPEG/PNG 宽高，失败返回 None。"""
    try:
        with path.open("rb") as f:
            header = f.read(24)
        if header[:2] == b"\xff\xd8":
            with path.open("rb") as f:
                f.read(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    if marker[1] in (
                        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
                    ):
                        f.read(3)
                        h = int.from_bytes(f.read(2), "big")
                        w = int.from_bytes(f.read(2), "big")
                        return w, h
                    seg_len = int.from_bytes(f.read(2), "big")
                    f.read(seg_len - 2)
        if header[:8] == b"\x89PNG\r\n\x1a\n" and len(header) >= 24:
            w = int.from_bytes(header[16:20], "big")
            h = int.from_bytes(header[20:24], "big")
            return w, h
    except OSError:
        return None
    return None


def _is_meaningful_manual_image(abs_path: Path) -> bool:
    """跳过告警符号等小图标，保留安装示意图、工具图等。"""
    dims = _read_image_dimensions(abs_path)
    if dims is None:
        return True
    w, h = dims
    return w >= _MIN_IMAGE_DIMENSION and h >= _MIN_IMAGE_DIMENSION


def _rel_path_to_display_url(rel_path: str) -> str:
    url_part = rel_path[len("images/"):] if rel_path.startswith("images/") else rel_path
    return f"/kb_images/{url_part}"


_FIGURE_CAPTION_RE = re.compile(r"图\s*[\d][\d\-\+\s]*[^\n]{0,50}")


@dataclass
class ImageCatalogEntry:
    image_id: str
    rel_path: str
    display_url: str
    abs_path: str
    section_title: str
    vision_index: int
    caption_hint: str = ""


@dataclass
class ImageCatalog:
    entries: List[ImageCatalogEntry] = field(default_factory=list)

    @property
    def abs_paths(self) -> List[str]:
        return [e.abs_path for e in self.entries]

    @property
    def display_urls(self) -> List[str]:
        return [e.display_url for e in self.entries]

    def by_rel_path(self) -> Dict[str, ImageCatalogEntry]:
        return {e.rel_path: e for e in self.entries}


def _extract_figure_captions(text: str) -> List[str]:
    if not text:
        return []
    return [m.strip() for m in _FIGURE_CAPTION_RE.findall(text)]


def build_image_catalog(
    chunks: List[RetrievedChunk],
    max_images: int = 5,
) -> ImageCatalog:
    """按检索顺序收集有意义配图，分配全局 [图-N] 编号（与 vision 输入顺序一致）。"""
    seen: Set[str] = set()
    entries: List[ImageCatalogEntry] = []
    for chunk in chunks:
        captions = _extract_figure_captions(chunk.content)
        cap_idx = 0
        for rel_path in chunk.image_paths:
            if rel_path in seen:
                continue
            abs_path = _KB_IMAGES_ROOT / rel_path
            if not abs_path.is_file() or not _is_meaningful_manual_image(abs_path):
                continue
            seen.add(rel_path)
            vision_index = len(entries) + 1
            caption_hint = ""
            if cap_idx < len(captions):
                caption_hint = captions[cap_idx]
                cap_idx += 1
            entries.append(
                ImageCatalogEntry(
                    image_id=f"图-{vision_index}",
                    rel_path=rel_path,
                    display_url=_rel_path_to_display_url(rel_path),
                    abs_path=str(abs_path),
                    section_title=(chunk.section_title or "").strip(),
                    vision_index=vision_index,
                    caption_hint=caption_hint,
                )
            )
            if len(entries) >= max_images:
                return ImageCatalog(entries=entries)
    return ImageCatalog(entries=entries)


def collect_filtered_images(
    chunks: List[RetrievedChunk],
    max_images: int = 5,
) -> Tuple[List[str], List[str]]:
    """按检索顺序收集有意义配图，返回 (vision 绝对路径, 前端展示 URL)。"""
    catalog = build_image_catalog(chunks, max_images=max_images)
    return catalog.abs_paths, catalog.display_urls


def collect_chunk_image_paths(chunks: List[RetrievedChunk], max_images: int = 3) -> List[str]:
    """收集 chunks 中存在于本地文件系统的图片绝对路径，去重，最多 max_images 张。"""
    abs_paths, _ = collect_filtered_images(chunks, max_images=max_images)
    return abs_paths


def collect_display_image_urls(chunks: List[RetrievedChunk], max_images: int = 5) -> List[str]:
    """返回前端可访问 URL，与 vision 输入使用同一过滤规则。"""
    _, display_urls = collect_filtered_images(chunks, max_images=max_images)
    return display_urls


def _format_chunk_image_lines(
    chunk: RetrievedChunk,
    catalog_map: Dict[str, ImageCatalogEntry],
) -> str:
    lines: List[str] = []
    for rel_path in chunk.image_paths:
        entry = catalog_map.get(rel_path)
        if not entry:
            continue
        hint = f"（{entry.caption_hint}）" if entry.caption_hint else ""
        lines.append(
            f"  - [{entry.image_id}] {entry.display_url}{hint} "
            f"[随附视觉输入第{entry.vision_index}张]"
        )
    if not lines:
        return ""
    return "配图（回答中请用 ![图注](URL) 紧跟对应段落后插入）:\n" + "\n".join(lines) + "\n"


def format_context(
    chunks: List[RetrievedChunk],
    catalog: Optional[ImageCatalog] = None,
) -> str:
    """将检索片段转为 LLM prompt 中的文本 context。

    - 配图 URL 写入各片段，供模型在正文中 inline 插入 Markdown 图片
    - 实际图片同时通过 inlineData 传给 Gemini（由 agent.py 处理）
    """
    if not chunks:
        return ""
    if catalog is None:
        catalog = build_image_catalog(
            chunks, max_images=int(os.getenv("GEMINI_MAX_IMAGES", "5"))
        )
    catalog_map = catalog.by_rel_path()
    parts: List[str] = [
        "以下片段按相关度排序，请围绕用户问题筛选整合，不必全部写入回答。\n"
    ]
    if catalog.entries:
        id_list = "、".join(e.image_id for e in catalog.entries)
        parts.append(
            f"随附视觉输入共 {len(catalog.entries)} 张，顺序与编号一致：{id_list}。\n"
        )
    for i, chunk in enumerate(chunks, 1):
        body = chunk.content.strip()
        has_image = any(catalog_map.get(p) for p in chunk.image_paths)
        if len(body) < _CONTEXT_MIN_CONTENT_LEN and not has_image and "<table" not in body.lower():
            continue
        img_block = _format_chunk_image_lines(chunk, catalog_map)
        parts.append(
            f"--- 片段 {i} ---\n"
            f"产品: {chunk.display_name or '未知'}\n"
            f"章节: {chunk.section_title or '未知'}\n"
            f"相关度: {chunk.score:.3f}\n"
            f"{img_block}"
            f"内容:\n{body}\n"
        )
    return "\n".join(parts)


def get_display_name(product_id: Optional[str]) -> str:
    if not product_id:
        return "全部产品"
    product = get_product_by_id(product_id)
    return product.get("display_name", product_id) if product else product_id
