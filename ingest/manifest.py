#!/usr/bin/env python
# -*- coding: utf-8 -*-

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "rag_data" / "all" / "ingest_manifest.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Optional[Path] = None) -> Dict[str, Any]:
    manifest_path = path or DEFAULT_MANIFEST_PATH
    if not manifest_path.exists():
        return {}
    with manifest_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def save_manifest(manifest: Dict[str, Any], path: Optional[Path] = None) -> None:
    manifest_path = path or DEFAULT_MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def build_doc_entry(
    *,
    product_id: str,
    source_pdf: str,
    full_doc_id: str,
    chunk_ids: List[str],
    pdf_sha256: str,
    pdf_mtime: float,
) -> Dict[str, Any]:
    return {
        "product_id": product_id,
        "source_pdf": source_pdf,
        "full_doc_id": full_doc_id,
        "chunk_ids": chunk_ids,
        "pdf_sha256": pdf_sha256,
        "pdf_mtime": pdf_mtime,
    }


def detect_changed_products(
    pdf_dir: Path,
    products: List[Dict[str, Any]],
    manifest: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    changed: List[Dict[str, Any]] = []
    removed: List[str] = []

    seen_ids = set()
    for product in products:
        pdf_name = product.get("manual_pdf")
        if not pdf_name:
            continue
        pdf_path = pdf_dir / pdf_name
        product_id = product["id"]
        seen_ids.add(product_id)

        if not pdf_path.exists():
            if product_id in manifest:
                removed.append(product_id)
            continue

        stat = pdf_path.stat()
        current_hash = sha256_file(pdf_path)
        old = manifest.get(product_id, {})
        if (
            old.get("pdf_sha256") != current_hash
            or old.get("pdf_mtime") != stat.st_mtime
            or old.get("full_doc_id") is None
        ):
            changed.append({
                **product,
                "pdf_path": pdf_path,
                "pdf_sha256": current_hash,
                "pdf_mtime": stat.st_mtime,
            })

    for product_id in manifest:
        if product_id not in seen_ids:
            removed.append(product_id)

    return changed, removed
