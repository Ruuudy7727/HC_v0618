#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""科陆用户手册知识库构建 — Step 1：PDF 解析（MinerU）+ MD 语义切分 + 写 kv JSON。

切分由 ingest.manual_chunk_splitter.split_manual_semantic 完成：
合并页眉/警示噪声、保留图文完整节，产出 section_path / section_number / chunk_type。

常用命令
--------
全量切分（已有 MinerU MD）::

    python ingest/step1_build_knowledge.py --skip-parse --min-chunk-tokens 80
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import get_product_by_id, get_product_by_md_stem, list_all_products
from ingest.build_section_index import save_section_index
from ingest.manual_chunk_splitter import split_manual_semantic
from ingest.manifest import (
    build_doc_entry,
    detect_changed_products,
    load_manifest,
    save_manifest,
)


class MineruExecutionError(Exception):
    def __init__(self, return_code: int, errors: List[str]):
        self.return_code = return_code
        self.errors = errors or []
        super().__init__(self.__str__())

    def __str__(self):
        msg = f"MinerU 执行失败，返回码={self.return_code}"
        if self.errors:
            msg += f"，错误信息（截断）：{self.errors[:3]}"
        return msg


class MineruParser:
    IMAGE_FORMATS = {".png", ".jpeg", ".jpg", ".bmp", ".tiff", ".tif", ".gif", ".webp"}
    OFFICE_FORMATS = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
    TEXT_FORMATS = {".txt", ".md"}

    @staticmethod
    def check_installation() -> bool:
        try:
            subprocess.run(
                ["mineru", "--version"],
                capture_output=True,
                text=True,
                check=True,
                encoding="utf-8",
                errors="ignore",
            )
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            logging.error("未检测到 MinerU 2.0。请先安装：pip install -U 'mineru[core]'")
            return False

    @staticmethod
    def _run_mineru_command(cmd: List[str]) -> None:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=1,
        )
        stdout_queue, stderr_queue = Queue(), Queue()

        def enqueue_output(pipe, queue):
            try:
                for line in iter(pipe.readline, ""):
                    if line.strip():
                        queue.put(line.strip())
                pipe.close()
            except Exception as e:
                queue.put(f"读取错误：{e}")

        threading.Thread(target=enqueue_output, args=(process.stdout, stdout_queue), daemon=True).start()
        threading.Thread(target=enqueue_output, args=(process.stderr, stderr_queue), daemon=True).start()

        error_lines = []
        while process.poll() is None:
            for q, err_list in [(stdout_queue, None), (stderr_queue, error_lines)]:
                try:
                    while True:
                        line = q.get_nowait()
                        if "error" in line.lower():
                            logging.error(f"[MinerU] {line}")
                            if err_list is not None:
                                err_list.append(line)
                        else:
                            logging.info(f"[MinerU] {line}")
                except Empty:
                    pass
            time.sleep(0.1)

        process.wait()
        if process.returncode != 0 or error_lines:
            raise MineruExecutionError(process.returncode, error_lines)

    def parse_document(self, file_path: Path, output_dir: Path, **kwargs) -> None:
        ext = file_path.suffix.lower()
        method = kwargs.get("method", "auto")
        input_to_mineru = file_path

        if ext in self.IMAGE_FORMATS:
            method = "ocr"

        cmd = ["mineru", "-p", str(input_to_mineru), "-o", str(output_dir), "-m", method]
        for key, val in kwargs.items():
            if key in ["method"]:
                continue
            if val is not None:
                if isinstance(val, bool):
                    if val:
                        cmd.append(f"--{key.replace('_', '-')}")
                else:
                    cmd.extend([f"--{key.replace('_', '-')}", str(val)])
        self._run_mineru_command(cmd)


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def get_output_paths(file_path: Path, out_dir_for_dir: Path, method: str) -> Tuple[Optional[Path], Optional[Path]]:
    stem = file_path.stem
    new_style_dir = out_dir_for_dir / stem / method
    if new_style_dir.is_dir():
        md = new_style_dir / f"{stem}.md"
        json_file = new_style_dir / f"{stem}_content_list.json"
        return (md if md.exists() else None, json_file if json_file.exists() else None)

    old_md = out_dir_for_dir / f"{stem}.md"
    old_json = out_dir_for_dir / f"{stem}_content_list.json"
    if old_json.exists():
        return (old_md if old_md.exists() else None, old_json)
    return None, None


def extract_image_paths_and_clean_markdown(text: str) -> Tuple[List[str], str]:
    if not text:
        return [], ""
    image_paths = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    cleaned_text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text).strip()
    return image_paths, cleaned_text


def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text, disallowed_special=()))
    except (ImportError, Exception):
        return len(re.findall(r"[\u4e00-\u9fa5]|\w+", text))


def make_doc_id(file_name: str) -> str:
    return "doc-" + hashlib.md5(file_name.encode("utf-8")).hexdigest()


def make_chunk_id(doc_id: str, order_idx: int, content_hash_base: str) -> str:
    base = f"{doc_id}|{order_idx}|{content_hash_base}"
    return "chunk-" + hashlib.md5(base.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


def inject_chunk_prefix(
    content: str,
    display_name: str,
    section_title: str,
    section_path: str = "",
    chunk_type: str = "",
) -> str:
    """在 chunk 正文前注入结构化前缀，供 embedding / BM25 / LLM 理解上下文。

    示例前缀::

        [产品: Aqua-E 261-125-2h CN | 章节路径: 3.5 固定安装 | 类型: procedure]
    """
    path_label = section_path or section_title
    prefix = f"[产品: {display_name} | 章节路径: {path_label}"
    if chunk_type:
        prefix += f" | 类型: {chunk_type}"
    prefix += "]\n"
    if content.startswith(prefix):
        return content
    return prefix + content


def find_md_for_product(output_root: Path, md_stem: str) -> Optional[Path]:
    candidates = list(output_root.rglob(f"{md_stem}.md"))
    if not candidates:
        candidates = [p for p in output_root.rglob("*.md") if p.stem == md_stem]
    return candidates[0] if candidates else None


def remove_doc_chunks(kv_store: Dict[str, Dict[str, Any]], full_doc_id: str) -> None:
    to_delete = [cid for cid, obj in kv_store.items() if obj.get("full_doc_id") == full_doc_id]
    for cid in to_delete:
        kv_store.pop(cid, None)


def process_md_file(
    md_path: Path,
    output_root: Path,
    kb_images_root: Path,
    kv_store: Dict[str, Dict[str, Any]],
    now_ts: int,
    min_chunk_tokens: int,
    target_products: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """处理单份 MinerU MD：语义切分 → 抽图 → 写 kv_store。"""
    original_filename = md_path.stem
    product = get_product_by_md_stem(original_filename)
    if product is None:
        logging.warning(f"未在 products.json 中找到 md_stem={original_filename}，跳过。")
        return {}

    if target_products is not None and product["id"] not in target_products:
        return {}

    md_text = md_path.read_text(encoding="utf-8", errors="ignore")
    relative_md_path = md_path.relative_to(output_root)
    semantic_sections = split_manual_semantic(md_text, min_body_chars=max(60, min_chunk_tokens))

    doc_id = make_doc_id(original_filename)
    remove_doc_chunks(kv_store, doc_id)

    doc_image_paths_set = set()
    doc_output_root = md_path.parent
    doc_images_src = doc_output_root / "images"
    doc_images_dst = kb_images_root / doc_id
    chunk_ids: List[str] = []

    for idx, section_obj in enumerate(semantic_sections):
        section = section_obj["content"]
        image_paths, cleaned_content = extract_image_paths_and_clean_markdown(section)
        chunk_image_paths = []
        for rel_img in image_paths:
            norm_rel = rel_img.strip()
            if norm_rel:
                doc_image_paths_set.add(norm_rel)
                chunk_image_paths.append(f"images/{doc_id}/{Path(norm_rel).name}")

        if not cleaned_content:
            continue

        section_title = str(section_obj.get("section_title", ""))
        section_path = str(section_obj.get("section_path", ""))
        section_number = str(section_obj.get("section_number", ""))
        chunk_type = str(section_obj.get("chunk_type", ""))

        enriched_content = inject_chunk_prefix(
            cleaned_content,
            product.get("display_name", original_filename),
            section_title,
            section_path=section_path,
            chunk_type=chunk_type,
        )
        tokens = count_tokens(enriched_content)
        # 有图或表格的短块仍保留（工具清单等关键信息常在图里）
        if tokens < min_chunk_tokens and not chunk_image_paths and "<table" not in cleaned_content.lower():
            continue

        chunk_id = make_chunk_id(doc_id, idx, enriched_content)
        chunk_ids.append(chunk_id)
        kv_store[chunk_id] = {
            "_id": chunk_id,
            "tokens": tokens,
            "content": enriched_content,
            "chunk_order_index": idx,
            "full_doc_id": doc_id,
            "file_path": str(relative_md_path),
            "image_paths": sorted(set(chunk_image_paths)),
            "product_id": product["id"],
            "series_id": product.get("series_id"),
            "display_name": product.get("display_name"),
            "section_title": section_title,
            "section_number": section_number,
            "section_path": section_path,
            "chunk_type": chunk_type,
            "source_pdf": product.get("manual_pdf"),
            "content_hash": content_hash(enriched_content),
            "create_time": now_ts,
            "update_time": now_ts,
        }

    if doc_images_src.exists() and doc_images_src.is_dir():
        doc_images_dst.mkdir(parents=True, exist_ok=True)
        for rel_img in sorted(doc_image_paths_set):
            src_path = doc_output_root / rel_img
            if src_path.exists() and src_path.is_file():
                shutil.copy2(src_path, doc_images_dst / src_path.name)

    return {
        "product_id": product["id"],
        "full_doc_id": doc_id,
        "chunk_ids": chunk_ids,
        "source_pdf": product.get("manual_pdf"),
    }


def run_parse_phase(input_root: Path, output_root: Path, args: argparse.Namespace) -> None:
    mineru = MineruParser()
    if not mineru.check_installation():
        sys.exit(1)

    all_files = [p for p in input_root.rglob("*") if p.is_file()]
    logging.info(f"开始解析阶段，发现 {len(all_files)} 个文件")
    for i, fpath in enumerate(all_files):
        rel_dir = fpath.parent.relative_to(input_root)
        out_dir_for_dir = output_root / rel_dir
        out_dir_for_dir.mkdir(parents=True, exist_ok=True)
        _, json_path = get_output_paths(fpath, out_dir_for_dir, args.method)
        if not args.force_reparse and json_path and json_path.exists():
            logging.info(f"[{i+1}/{len(all_files)}] 已解析，跳过: {fpath.name}")
            continue
        try:
            mineru.parse_document(
                fpath,
                out_dir_for_dir,
                method=args.method,
                lang=args.lang,
                device=args.device,
                backend=args.backend,
                source=args.source,
                vram=args.vram,
                formula=args.formula,
                table=args.table,
            )
            logging.info(f"[{i+1}/{len(all_files)}] 解析成功: {fpath.name}")
        except (MineruExecutionError, RuntimeError) as e:
            logging.error(f"解析失败: {fpath.name}, 错误: {e}")


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="科陆用户手册知识库构建")
    parser.add_argument("--input-dir", default=str(PROJECT_ROOT / "用户手册"))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "rag_output_manuals"))
    parser.add_argument("--kb-dir", default=str(PROJECT_ROOT / "rag_data" / "all"))
    parser.add_argument("--min-chunk-tokens", type=int, default=80)
    parser.add_argument("--product-id", default="", help="仅处理指定 product_id")
    parser.add_argument("--force-reparse", action="store_true")
    parser.add_argument("--skip-parse", action="store_true", help="跳过 MinerU 解析，仅从已有 MD 建库")
    parser.add_argument("--sync", action="store_true", help="仅处理变更的 PDF/文档")
    parser.add_argument("--method", default="auto", choices=["auto", "txt", "ocr"])
    parser.add_argument("--lang", default=None)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--backend", default=None)
    parser.add_argument("--source", default="modelscope")
    parser.add_argument("--vram", type=int, default=None)
    parser.add_argument("--formula", default=None)
    parser.add_argument("--table", default=None)
    args = parser.parse_args()

    input_root = Path(args.input_dir)
    output_root = Path(args.output_dir)
    kb_root = Path(args.kb_dir)
    kv_store_path = kb_root / "kv_store_text_chunks.json"
    kb_images_root = kb_root / "images"
    manifest_path = kb_root / "ingest_manifest.json"

    for p in [input_root, output_root, kb_root]:
        p.mkdir(parents=True, exist_ok=True)

    products = list_all_products()
    if args.product_id:
        one = get_product_by_id(args.product_id.strip())
        if not one:
            logging.error(f"未找到 product_id={args.product_id}")
            sys.exit(1)
        products = [one]
        logging.info(f"单产品模式: {one['display_name']} ({one['id']})")
    manifest = load_manifest(manifest_path)
    changed_products: List[Dict[str, Any]] = []
    removed_product_ids: List[str] = []

    if args.sync:
        changed_products, removed_product_ids = detect_changed_products(input_root, products, manifest)
        logging.info(f"增量模式：变更 {len(changed_products)} 个，移除 {len(removed_product_ids)} 个")
        if not changed_products and not removed_product_ids:
            logging.info("无变更，退出。")
            return
    else:
        changed_products = products

    if not args.skip_parse and changed_products:
        files_to_parse = [input_root / p["manual_pdf"] for p in changed_products if (input_root / p["manual_pdf"]).exists()]
        if files_to_parse:
            class _Args:
                pass
            parse_args = _Args()
            parse_args.force_reparse = args.force_reparse
            parse_args.method = args.method
            parse_args.lang = args.lang
            parse_args.device = args.device
            parse_args.backend = args.backend
            parse_args.source = args.source
            parse_args.vram = args.vram
            parse_args.formula = args.formula
            parse_args.table = args.table

            mineru = MineruParser()
            if mineru.check_installation():
                for fpath in files_to_parse:
                    out_dir_for_dir = output_root
                    out_dir_for_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        mineru.parse_document(fpath, out_dir_for_dir, method=args.method, device=args.device)
                        logging.info(f"解析成功: {fpath.name}")
                    except Exception as e:
                        logging.error(f"解析失败: {fpath.name}, {e}")
    elif not args.skip_parse:
        run_parse_phase(input_root, output_root, args)

    kv_store: Dict[str, Dict[str, Any]] = {}
    if args.sync and kv_store_path.exists():
        with kv_store_path.open("r", encoding="utf-8") as f:
            kv_store = json.load(f)

    pending_delete_ids: List[str] = []
    for product_id in removed_product_ids:
        old = manifest.get(product_id, {})
        full_doc_id = old.get("full_doc_id")
        pending_delete_ids.extend(old.get("chunk_ids", []))
        if full_doc_id:
            remove_doc_chunks(kv_store, full_doc_id)
        manifest.pop(product_id, None)

    now_ts = int(time.time())
    target_map = {p["id"]: p for p in changed_products} if args.sync else None

    if args.sync or args.product_id:
        md_files = []
        for product in changed_products:
            md_path = find_md_for_product(output_root, product.get("md_stem", ""))
            if md_path:
                md_files.append(md_path)
            else:
                logging.warning(f"未找到 MD: {product.get('md_stem')}")
    else:
        md_files = list(output_root.rglob("*.md"))

    for md_path in md_files:
        result = process_md_file(
            md_path=md_path,
            output_root=output_root,
            kb_images_root=kb_images_root,
            kv_store=kv_store,
            now_ts=now_ts,
            min_chunk_tokens=args.min_chunk_tokens,
            target_products=target_map,
        )
        if not result:
            continue
        product_id = result["product_id"]
        old_entry = manifest.get(product_id, {})
        pending_delete_ids.extend(old_entry.get("chunk_ids", []))
        pdf_path = input_root / result["source_pdf"]
        pdf_sha = ""
        pdf_mtime = 0.0
        if pdf_path.exists():
            from ingest.manifest import sha256_file
            pdf_sha = sha256_file(pdf_path)
            pdf_mtime = pdf_path.stat().st_mtime
        manifest[product_id] = build_doc_entry(
            product_id=product_id,
            source_pdf=result["source_pdf"],
            full_doc_id=result["full_doc_id"],
            chunk_ids=result["chunk_ids"],
            pdf_sha256=pdf_sha,
            pdf_mtime=pdf_mtime,
        )
        logging.info(f"已处理: {result['source_pdf']} -> {len(result['chunk_ids'])} chunks")

    with kv_store_path.open("w", encoding="utf-8") as f:
        json.dump(kv_store, f, ensure_ascii=False, indent=2)
    section_index_path = kb_root / "section_index.json"
    save_section_index(kv_store_path, section_index_path)
    logging.info(f"章节索引完成: {section_index_path}")
    save_manifest(manifest, manifest_path)
    if pending_delete_ids:
        delete_path = kb_root / "pending_chroma_deletes.json"
        with delete_path.open("w", encoding="utf-8") as f:
            json.dump(sorted(set(pending_delete_ids)), f, ensure_ascii=False, indent=2)
    logging.info(f"KV 分片完成: {kv_store_path} (共 {len(kv_store)} chunks)")


if __name__ == "__main__":
    main()
