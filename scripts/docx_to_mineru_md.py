#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将 Word (.docx) 转为 MinerU 兼容的 Markdown 目录结构，供 step1 切分入库。"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

PROJECT_ROOT = Path(__file__).resolve().parent.parent


_SECTION_NUMBER_RE = re.compile(r"^\d+\.\S")
_IMPLICIT_HEADING_RE = re.compile(
    r"^(用户登录|关机操作|开机操作|充放电模式切换|充放电模式设置|"
    r"服务商注册|创建场站|系统自检|工单|反馈)$"
)


def _is_implicit_heading(text: str) -> bool:
    text = text.strip()
    if not text or len(text) > 40:
        return False
    if _SECTION_NUMBER_RE.match(text):
        return True
    if _IMPLICIT_HEADING_RE.match(text):
        return True
    if text.endswith("操作") and len(text) <= 12 and not text.startswith("·"):
        return True
    return False


def _heading_level(style_name: str) -> int | None:
    if not style_name:
        return None
    name = style_name.strip()
    if name.startswith("Heading"):
        suffix = name.replace("Heading", "").strip()
        if suffix.isdigit():
            return max(1, min(int(suffix), 6))
    if name in {"Title", "标题"}:
        return 1
    return None


def _iter_block_items(document: Document):
    from docx.table import Table

    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _extract_images_from_paragraph(paragraph: Paragraph, images_dir: Path, counter: list[int]) -> list[str]:
    rel_paths: list[str] = []
    for run in paragraph.runs:
        blips = run._element.xpath(".//a:blip")
        for blip in blips:
            embed = blip.get(qn("r:embed"))
            if not embed:
                continue
            part = paragraph.part.related_parts.get(embed)
            if part is None:
                continue
            blob = part.blob
            ext = part.content_type.split("/")[-1].replace("jpeg", "jpg")
            if ext not in {"png", "jpg", "gif", "webp", "bmp"}:
                ext = "png"
            counter[0] += 1
            digest = hashlib.md5(blob).hexdigest()
            filename = f"{digest}.{ext}"
            out_path = images_dir / filename
            if not out_path.exists():
                out_path.write_bytes(blob)
            rel_paths.append(f"images/{filename}")
    return rel_paths


def _table_to_md(table) -> str:
    rows: list[list[str]] = []
    for row in table.rows:
        cells = [re.sub(r"\s+", " ", (cell.text or "").strip()) for cell in row.cells]
        rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(norm[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in norm[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def convert_docx(docx_path: Path, output_root: Path, md_stem: str) -> Path:
    doc = Document(str(docx_path))
    auto_dir = output_root / md_stem / "auto"
    images_dir = auto_dir / "images"
    auto_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    md_lines: list[str] = []
    img_counter = [0]

    for block in _iter_block_items(doc):
        if block.__class__.__name__ == "Table":
            table_md = _table_to_md(block)
            if table_md:
                md_lines.append(table_md)
                md_lines.append("")
            continue

        paragraph: Paragraph = block
        text = re.sub(r"\s+", " ", (paragraph.text or "")).strip()
        image_refs = _extract_images_from_paragraph(paragraph, images_dir, img_counter)

        if not text and not image_refs:
            continue

        level = _heading_level(paragraph.style.name if paragraph.style else "")
        if (level or _is_implicit_heading(text)) and text:
            # 与 MinerU 手册一致：统一用一级 #，便于 manual_chunk_splitter 切分
            md_lines.append("# " + text)
            md_lines.append("")
        elif text:
            md_lines.append(text)
            md_lines.append("")

        for ref in image_refs:
            caption = Path(ref).stem[:12]
            md_lines.append(f"![{caption}]({ref})")
            md_lines.append("")

    md_path = auto_dir / f"{md_stem}.md"
    content = "\n".join(md_lines).strip() + "\n"
    md_path.write_text(content, encoding="utf-8")
    print(f"已生成 Markdown: {md_path} ({len(content)} chars, {img_counter[0]} images)")
    return md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Word docx → MinerU 风格 Markdown")
    parser.add_argument("--docx", required=True, help="输入 .docx 路径")
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "rag_output_manuals"))
    parser.add_argument("--md-stem", default="", help="输出目录名，默认取 docx 文件名")
    args = parser.parse_args()

    docx_path = Path(args.docx).resolve()
    if not docx_path.exists():
        print(f"文件不存在: {docx_path}", file=sys.stderr)
        sys.exit(1)
    md_stem = args.md_stem.strip() or docx_path.stem
    convert_docx(docx_path, Path(args.output_dir), md_stem)


if __name__ == "__main__":
    main()
