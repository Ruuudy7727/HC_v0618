#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""面向 MinerU 用户手册 Markdown 的语义切分器。

整体流水线
----------
MinerU 输出的 MD 里，几乎所有标题都是同级 ``# ``，且夹杂页眉（科陆）、
警示块（告警/注意）等噪声。本模块将其整理为适合 RAG 的 chunk。

::

    MD 全文
      → _split_raw_h1_blocks()   按 # 切成 RawBlock，打上 block_type
      → _merge_blocks()          合并页眉/警示/续接段，生成 section_path
      → _split_oversized()       超长块按「步骤 N」或段落再切
      → split_manual_semantic()  过滤过短块，输出最终 dict 列表

两套「类型」不要混淆
--------------------
block_type（RawBlock）
    切分**过程中**使用，描述 MinerU 每个 ``#`` 块的性质，决定**合并策略**。
    取值：numbered / warning / page_header / toc / front_matter / plain / toc_entry

chunk_type（MergedChunk → 最终输出）
    切分**完成后**使用，描述 chunk 的**内容语义**，写入 kv_store 与文本前缀。
    取值：overview / procedure / maintenance / troubleshoot / safety / spec /
          meta / toc / general
    由 ``_CHUNK_TYPE_RULES`` 根据 section_path + section_title 关键词推断；
    目录强制 toc，前言强制 meta。检索层目前尚未按 chunk_type 加权。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ── 正则与常量 ──────────────────────────────────────────────────────────────

# 匹配正式编号章节，如 "3.5 固定安装" → ("3.5", "固定安装")
_NUMBERED_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)$")
# 目录页条目常带页码点线，如 "4.6.2 接线步骤···· ··29"
_TOC_PAGE_RE = re.compile(r"[·\.]{2,}\s*\d*\s*$")

# block_type = warning：并入当前父 numbered 节，不单独成 chunk
_WARNING_TITLES = {
    "告警", "注意", "危险", "警告", "小心", "A 告警", "！ 告警",
}
# block_type = page_header：PDF 页眉，并入下一个 numbered 块
_PAGE_HEADER_TITLES = {
    "科陆", "Midea", "深圳市科陆电子科技股份有限公司",
}
# block_type = front_matter：手册前言，独立 chunk，chunk_type=meta
_FRONT_MATTER_TITLES = {
    "商标", "软件授权", "读者对象", "产品服务及咨询", "手册警示符号",
    "机体警示标贴", "修订记录", "订货说明：", "订货说明",
}
_TOC_TITLES = {"目录"}

# chunk_type 推断规则：按顺序匹配，第一个命中的生效
_CHUNK_TYPE_RULES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"产品概述|外观|机械参数|内部设计|产品描述|LED"), "overview"),
    (re.compile(r"安装|固定|运输|起吊|吊装|叉车|地基|地点"), "procedure"),
    (re.compile(r"接线|端子|接地|等电位|铜线|热缩"), "procedure"),
    (re.compile(r"上电|下电|开机|停机|投运"), "procedure"),
    (re.compile(r"维护|更换|检修|保养|清洁"), "maintenance"),
    (re.compile(r"故障|事件|排查|告警码"), "troubleshoot"),
    (re.compile(r"安全|危险|警告"), "safety"),
    (re.compile(r"参数|规格|接口|布局|概述"), "spec"),
]


@dataclass
class RawBlock:
    """MinerU MD 中按 ``# `` 切出的一块原始内容。"""

    title: str
    content: str
    block_type: str  # 见模块 docstring「block_type」说明
    section_number: Optional[str] = None  # numbered 块才有，如 "3.5"
    clean_title: Optional[str] = None     # 去掉编号后的标题，如 "固定安装"


@dataclass
class MergedChunk:
    """合并后的语义 chunk，尚未加产品前缀、尚未剥离图片。"""

    content: str
    section_title: str
    section_number: str = ""
    section_path: str = ""   # 面包屑，如 "3 机械安装 > 3.5 固定安装"
    chunk_type: str = "general"
    block_types: List[str] = field(default_factory=list)  # 调试用：含哪些 block_type


def _count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text, disallowed_special=()))
    except Exception:
        return len(re.findall(r"[\u4e00-\u9fa5]|\w+", text))


def _body_without_title(content: str) -> str:
    """去掉首行 # 标题后的正文，用于判断块是否过短。"""
    lines = (content or "").splitlines()
    if lines and lines[0].strip().startswith("#"):
        return "\n".join(lines[1:]).strip()
    return (content or "").strip()


def _has_image(content: str) -> bool:
    return "![" in (content or "")


def _has_table(content: str) -> bool:
    return "<table" in (content or "").lower()


def _is_substantial(block: RawBlock, min_chars: int = 80) -> bool:
    body = _body_without_title(block.content)
    return len(body) >= min_chars or _has_image(block.content) or _has_table(block.content)


def _parse_numbered_title(title: str) -> Optional[Tuple[str, str]]:
    """从标题解析 (section_number, clean_title)，非编号节返回 None。"""
    clean = re.sub(r"[·\.]+\s*\d+\s*$", "", title).strip()
    m = _NUMBERED_RE.match(clean)
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def _classify_title(title: str) -> str:
    """根据标题文本判定 block_type（切分过程用，不是 chunk_type）。"""
    t = (title or "").strip()
    if t in _TOC_TITLES:
        return "toc"
    if t in _WARNING_TITLES:
        return "warning"
    if t in _PAGE_HEADER_TITLES:
        return "page_header"
    if t in _FRONT_MATTER_TITLES:
        return "front_matter"
    if _parse_numbered_title(t):
        if _TOC_PAGE_RE.search(t):
            return "toc_entry"  # 目录里的页码行，丢弃不入库
        return "numbered"
    if t in {"科陆"}:
        return "page_header"
    return "plain"


def _split_raw_h1_blocks(md_text: str) -> List[RawBlock]:
    """Step 1：按 ``^# `` 将 MD 切成 RawBlock 列表。"""
    if not md_text or not md_text.strip():
        return []
    parts = re.split(r"(?m)^#\s", md_text)
    blocks: List[RawBlock] = []
    if parts[0].strip():
        first_lines = parts[0].strip().splitlines()
        title = first_lines[0].strip() if first_lines else "前言"
        blocks.append(RawBlock(
            title=title,
            content=parts[0].strip(),
            block_type="plain",
        ))
    for part in parts[1:]:
        if not part.strip():
            continue
        content = f"# {part.strip()}"
        title_line = content.splitlines()[0]
        title = re.sub(r"^#+\s*", "", title_line).strip()
        block_type = _classify_title(title)
        section_number = None
        clean_title = None
        if block_type == "numbered":
            parsed = _parse_numbered_title(title)
            if parsed:
                section_number, clean_title = parsed
        blocks.append(RawBlock(
            title=title,
            content=content,
            block_type=block_type,
            section_number=section_number,
            clean_title=clean_title,
        ))
    return blocks


def _number_parts(num: str) -> List[int]:
    return [int(x) for x in num.split(".")]


def _should_flush_chunk(anchor_num: Optional[str], new_num: str) -> bool:
    """遇到新 numbered 节时，判断是否要结束上一个 chunk。

    例如 3.4.1 → 3.4.2（兄弟）或 3.4.x → 3.5（换大节）时应 flush；
    3.4 → 3.4.1（子节）时不 flush，继续往同一 buffer 追加。
    """
    if not anchor_num:
        return False
    old_p = _number_parts(anchor_num)
    new_p = _number_parts(new_num)
    min_len = min(len(old_p), len(new_p))
    if old_p[:min_len] != new_p[:min_len]:
        return True
    if len(new_p) <= len(old_p):
        return True
    return False


def _update_section_stack(stack: Dict[str, str], section_number: str, clean_title: str) -> None:
    """维护 section_number → 标题 的映射，用于生成 section_path 面包屑。"""
    parts = section_number.split(".")
    for i in range(1, len(parts) + 1):
        key = ".".join(parts[:i])
        if key == section_number:
            stack[key] = clean_title
    to_del = [
        k for k in list(stack.keys())
        if k != section_number and not section_number.startswith(k + ".") and not k.startswith(section_number + ".")
    ]
    for k in to_del:
        if len(k.split(".")) >= len(parts):
            stack.pop(k, None)


def _build_section_path(stack: Dict[str, str], section_number: str) -> str:
    """生成面包屑路径，如 ``3 机械安装 > 3.5 固定安装``。"""
    if not section_number:
        return ""
    parts = section_number.split(".")
    path_parts: List[str] = []
    for i in range(1, len(parts) + 1):
        key = ".".join(parts[:i])
        if key in stack:
            path_parts.append(f"{key} {stack[key]}")
    return " > ".join(path_parts)


def _infer_chunk_type(section_title: str, section_path: str) -> str:
    """根据章节标题/路径中的关键词推断 chunk_type（内容语义标签）。

    按 ``_CHUNK_TYPE_RULES`` 顺序匹配，第一个命中即返回；都不命中则 general。
    """
    text = f"{section_path} {section_title}"
    for pattern, ctype in _CHUNK_TYPE_RULES:
        if pattern.search(text):
            return ctype
    return "general"


def _blocks_to_content(blocks: List[RawBlock]) -> str:
    return "\n\n".join(b.content.strip() for b in blocks if b.content.strip())


def _merge_blocks(blocks: List[RawBlock]) -> List[MergedChunk]:
    """Step 2：按合并规则将 RawBlock 串成语义完整的 MergedChunk。

    合并规则摘要
    ------------
    - toc_entry   → 跳过（目录页码行）
    - toc         → 独立 chunk，chunk_type=toc
    - front_matter→ 独立 chunk，chunk_type=meta
    - page_header → 追加到 buffer，随下一 numbered 节一起 flush
    - warning     → 追加到 buffer，并入当前父 numbered 节
    - numbered    → 新锚点；兄弟/换节时 flush 旧 buffer
    - plain       → 有 buffer 则并入，否则新建
    """
    merged: List[MergedChunk] = []
    buffer: List[RawBlock] = []
    anchor_num: Optional[str] = None
    anchor_title: str = ""
    section_stack: Dict[str, str] = {}
    in_toc = False

    def flush(force_title: str = "", force_type: str = "", force_num: str = "") -> None:
        nonlocal buffer, anchor_num, anchor_title
        if not buffer:
            return
        content = _blocks_to_content(buffer)
        if not content.strip():
            buffer = []
            return
        title = force_title or anchor_title or buffer[0].title
        num = force_num or anchor_num or ""
        path = _build_section_path(section_stack, num) if num else title
        # force_type 用于目录/前言；否则走关键词推断
        ctype = force_type or _infer_chunk_type(title, path)
        merged.append(MergedChunk(
            content=content,
            section_title=title,
            section_number=num,
            section_path=path,
            chunk_type=ctype,
            block_types=[b.block_type for b in buffer],
        ))
        buffer = []

    for block in blocks:
        if block.block_type == "toc_entry":
            continue

        if block.block_type == "toc":
            flush()
            anchor_num = None
            anchor_title = ""
            in_toc = True
            buffer = [block]
            flush(force_title="目录", force_type="toc", force_num="")
            buffer = []
            continue

        if in_toc and block.block_type == "numbered":
            in_toc = False

        if block.block_type == "page_header":
            buffer.append(block)
            continue

        if block.block_type == "warning":
            buffer.append(block)
            continue

        if block.block_type == "front_matter":
            flush()
            buffer = [block]
            flush(force_title=block.title, force_type="meta", force_num="")
            anchor_num = None
            anchor_title = ""
            continue

        if block.block_type == "numbered" and block.section_number:
            num = block.section_number
            title = block.clean_title or block.title
            if anchor_num and _should_flush_chunk(anchor_num, num):
                flush()
            _update_section_stack(section_stack, num, title)
            anchor_num = num
            anchor_title = f"{num} {title}".strip()
            buffer.append(block)
            continue

        # plain 或未编号块（如 LED 指示灯）：并入当前章节
        if buffer:
            buffer.append(block)
        else:
            buffer = [block]
            anchor_title = block.title

    flush()
    return merged


def _split_on_steps(content: str) -> List[str]:
    """在「步骤 N」行处切分，保持每个步骤序列相对完整。"""
    lines = content.splitlines()
    chunks: List[str] = []
    current: List[str] = []
    step_re = re.compile(r"^步骤\s*\d+")
    for line in lines:
        if step_re.match(line.strip()) and current:
            chunks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append("\n".join(current).strip())
    return [c for c in chunks if c.strip()]


def _split_oversized(chunk: MergedChunk, max_tokens: int) -> List[MergedChunk]:
    """Step 3：合并后超过 max_tokens 的块做二次切分。

    优先按「步骤 N」边界切，其次按空行（段落）切；表格不单独拆开。
    子块继承父块的 section_path / chunk_type。
    """
    if _count_tokens(chunk.content) <= max_tokens:
        return [chunk]

    parts = _split_on_steps(chunk.content)
    if len(parts) <= 1:
        paragraphs = re.split(r"\n\s*\n", chunk.content)
        parts = []
        buf: List[str] = []
        for para in paragraphs:
            if not para.strip():
                continue
            candidate = "\n\n".join(buf + [para])
            if buf and _count_tokens(candidate) > max_tokens:
                parts.append("\n\n".join(buf).strip())
                buf = [para]
            else:
                buf.append(para)
        if buf:
            parts.append("\n\n".join(buf).strip())

    if len(parts) <= 1:
        return [chunk]

    result: List[MergedChunk] = []
    for i, part in enumerate(parts, 1):
        suffix = f" ({i}/{len(parts)})" if len(parts) > 1 else ""
        result.append(MergedChunk(
            content=part,
            section_title=chunk.section_title + suffix,
            section_number=chunk.section_number,
            section_path=chunk.section_path,
            chunk_type=chunk.chunk_type,
            block_types=list(chunk.block_types),
        ))
    return result


def split_manual_semantic(
    md_text: str,
    *,
    max_tokens: int = 800,
    min_body_chars: int = 80,
) -> List[Dict[str, object]]:
    """将 MinerU Markdown 切为语义完整的 chunk 列表（供 step1 调用）。

    返回每个元素为 dict，字段：
    - content: 原始 MD 片段（含 # 标题，图片路径尚未剥离）
    - section_title / section_number / section_path
    - chunk_type: 内容语义标签（见模块 docstring）

    过短且无图无表的块会被丢弃；toc / meta 类型不受最短长度限制。
    """
    raw_blocks = _split_raw_h1_blocks(md_text)
    merged = _merge_blocks(raw_blocks)

    output: List[Dict[str, object]] = []
    for chunk in merged:
        for sub in _split_oversized(chunk, max_tokens):
            body = _body_without_title(sub.content)
            if (
                sub.chunk_type not in {"toc", "meta"}
                and len(body) < min_body_chars
                and not _has_image(sub.content)
                and not _has_table(sub.content)
            ):
                continue
            output.append({
                "content": sub.content,
                "section_title": sub.section_title,
                "section_number": sub.section_number,
                "section_path": sub.section_path,
                "chunk_type": sub.chunk_type,
            })
    return output
