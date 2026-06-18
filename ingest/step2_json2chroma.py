#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import chromadb
from chromadb.config import Settings
from chromadb.errors import NotFoundError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.embedding_client import build_embedding_client, get_embedding_settings
from ingest.manifest import load_manifest

KV_JSON_PATH = PROJECT_ROOT / "rag_data" / "all" / "kv_store_text_chunks.json"
MANIFEST_PATH = PROJECT_ROOT / "rag_data" / "all" / "ingest_manifest.json"

BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "16"))
EMBED_BATCH_INTERVAL = float(os.getenv("EMBED_BATCH_INTERVAL", "1.0"))
EMBED_MAX_RETRIES = int(os.getenv("EMBED_MAX_RETRIES", "8"))
EMBED_RETRY_BASE_DELAY = float(os.getenv("EMBED_RETRY_BASE_DELAY", "2.0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def get_chroma_config() -> Tuple[str, str]:
    chroma_dir = os.getenv("CHROMA_PERSIST_DIR", str(PROJECT_ROOT / "rag_data" / "all"))
    collection_name = os.getenv("CHROMA_COLLECTION_NAME", "gsc_manual_kb")
    return chroma_dir, collection_name


def load_kv_json(path: Path) -> Dict[str, Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"JSON 文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("kv_store_text_chunks.json 应为字典")
    return data


def sanitize_metadata(meta: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in meta.items():
        if k == "content":
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, (list, tuple, set)):
            out[k] = ";".join(str(item) for item in v if item is not None)
        elif isinstance(v, dict):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = str(v)
    return out


def build_payloads_from_data(
    data: Dict[str, Dict[str, Any]],
    chunk_ids: List[str] | None = None,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    allow = set(chunk_ids) if chunk_ids else None
    items: List[Tuple[str, str, Dict[str, Any]]] = []
    for _id, obj in data.items():
        if allow is not None and _id not in allow:
            continue
        if not isinstance(obj, dict):
            continue
        content = obj.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        items.append((str(_id), content, sanitize_metadata(obj)))
    logging.info(f"有效条目数: {len(items)}")
    return items


def embed_texts(
    emb_model: Any,
    payloads: List[Tuple[str, str, Dict[str, Any]]],
) -> List[Tuple[str, str, Dict[str, Any], List[float]]]:
    total = len(payloads)
    results: List[Tuple[str, str, Dict[str, Any], List[float]]] = []
    completed = 0

    for start_index in range(0, total, BATCH_SIZE):
        batch_payloads = payloads[start_index:start_index + BATCH_SIZE]
        batch_texts = [p[1] for p in batch_payloads]
        batch_embeddings = None
        last_error = None

        for attempt in range(1, EMBED_MAX_RETRIES + 1):
            try:
                batch_embeddings = emb_model.embed_documents(batch_texts)
                break
            except Exception as e:
                last_error = e
                wait_seconds = EMBED_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logging.warning(f"批次 {start_index} 重试 {attempt}/{EMBED_MAX_RETRIES}: {e}")
                time.sleep(wait_seconds)

        if batch_embeddings is None:
            raise RuntimeError(f"嵌入失败: {last_error}")

        for payload, vec in zip(batch_payloads, batch_embeddings):
            _id, content, meta = payload
            if not vec:
                raise RuntimeError(f"空向量: {_id}")
            results.append((_id, content, meta, vec))

        completed += len(batch_payloads)
        logging.info(f"嵌入进度: {completed}/{total}")
        if completed < total and EMBED_BATCH_INTERVAL > 0:
            time.sleep(EMBED_BATCH_INTERVAL)

    return results


def get_or_create_collection(chroma_dir: str, collection_name: str):
    os.makedirs(chroma_dir, exist_ok=True)
    client = chromadb.PersistentClient(path=chroma_dir, settings=Settings(anonymized_telemetry=False))
    try:
        return client.get_collection(name=collection_name)
    except NotFoundError:
        return client.create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})


def write_full_to_chroma(chroma_dir: str, collection_name: str, records: List[Tuple[str, str, Dict[str, Any], List[float]]]) -> None:
    client = chromadb.PersistentClient(path=chroma_dir, settings=Settings(anonymized_telemetry=False))
    try:
        client.delete_collection(name=collection_name)
        logging.info(f"已删除集合: {collection_name}")
    except NotFoundError:
        pass
    collection = client.create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})

    total_written = 0
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        collection.add(
            ids=[r[0] for r in batch],
            documents=[r[1] for r in batch],
            metadatas=[r[2] for r in batch],
            embeddings=[r[3] for r in batch],
        )
        total_written += len(batch)
        logging.info(f"已写入 {total_written}/{len(records)}")
    logging.info(f"全量写入完成: {collection.count()} 条")


def write_sync_to_chroma(
    chroma_dir: str,
    collection_name: str,
    records: List[Tuple[str, str, Dict[str, Any], List[float]]],
    delete_ids: List[str],
) -> None:
    collection = get_or_create_collection(chroma_dir, collection_name)
    if delete_ids:
        try:
            collection.delete(ids=delete_ids)
            logging.info(f"已删除 {len(delete_ids)} 条旧 chunk")
        except Exception as e:
            logging.warning(f"删除旧 chunk 时出错: {e}")

    if not records:
        logging.info("无新记录需要写入")
        return

    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        collection.upsert(
            ids=[r[0] for r in batch],
            documents=[r[1] for r in batch],
            metadatas=[r[2] for r in batch],
            embeddings=[r[3] for r in batch],
        )
        logging.info(f"增量写入进度: {min(i + BATCH_SIZE, len(records))}/{len(records)}")
    logging.info(f"增量写入完成，当前集合共 {collection.count()} 条")


def collect_delete_ids(manifest: Dict[str, Any], changed_product_ids: List[str] | None = None) -> List[str]:
    delete_ids: List[str] = []
    for product_id, entry in manifest.items():
        if changed_product_ids is not None and product_id not in changed_product_ids:
            continue
        delete_ids.extend(entry.get("chunk_ids", []))
    return delete_ids


def main() -> None:
    parser = argparse.ArgumentParser(description="将 kv_store 写入 Chroma")
    parser.add_argument("--sync", action="store_true", help="增量模式，仅更新 manifest 中记录的 chunks")
    parser.add_argument("--product-ids", default="", help="逗号分隔，仅同步指定 product_id")
    args = parser.parse_args()

    chroma_dir, collection_name = get_chroma_config()
    data = load_kv_json(KV_JSON_PATH)
    manifest = load_manifest(MANIFEST_PATH)

    changed_product_ids = None
    if args.product_ids.strip():
        changed_product_ids = [x.strip() for x in args.product_ids.split(",") if x.strip()]

    pending_delete_path = PROJECT_ROOT / "rag_data" / "all" / "pending_chroma_deletes.json"
    if args.sync:
        chunk_ids: List[str] = []
        for product_id, entry in manifest.items():
            if changed_product_ids is not None and product_id not in changed_product_ids:
                continue
            chunk_ids.extend(entry.get("chunk_ids", []))
        payloads = build_payloads_from_data(data, chunk_ids=chunk_ids)
        delete_ids = []
        if pending_delete_path.exists():
            with pending_delete_path.open("r", encoding="utf-8") as f:
                delete_ids = json.load(f)
        else:
            delete_ids = collect_delete_ids(manifest, changed_product_ids)
    else:
        payloads = build_payloads_from_data(data)
        delete_ids = []

    if not payloads and not delete_ids:
        logging.error("没有可写入的数据")
        return

    embed_settings = get_embedding_settings()
    embeddings = build_embedding_client()
    backend = embed_settings["backend"]
    endpoint = (
        embed_settings["ollama_host"]
        if backend == "ollama"
        else embed_settings["base_url"]
    )
    logging.info(f"Embedding ({backend}): {endpoint} / {embed_settings['model']}")

    records = embed_texts(embeddings, payloads) if payloads else []
    if args.sync:
        write_sync_to_chroma(chroma_dir, collection_name, records, delete_ids)
    else:
        if not records:
            logging.error("全量模式需要有效 payloads")
            return
        write_full_to_chroma(chroma_dir, collection_name, records)

    if pending_delete_path.exists():
        pending_delete_path.unlink()
        logging.info("已清理 pending_chroma_deletes.json")

    logging.info("完成")


if __name__ == "__main__":
    main()
