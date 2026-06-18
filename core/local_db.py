#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(_PROJECT_ROOT / ".env"), override=False)
except Exception:
    pass

Chroma = None
try:
    from langchain_chroma import Chroma as _LCChroma
    Chroma = _LCChroma
except Exception:
    try:
        from langchain_community.vectorstores import Chroma as _LCChroma
        Chroma = _LCChroma
    except Exception:
        Chroma = None

try:
    import chromadb as _chromadb_mod
    _CHROMADB_AVAILABLE = True
except Exception:
    _chromadb_mod = None
    _CHROMADB_AVAILABLE = False

try:
    from core.embedding_client import build_embedding_client, get_embedding_settings
except Exception:
    build_embedding_client = None
    get_embedding_settings = None

try:
    from langchain_core.documents import Document
except Exception:
    class Document:
        def __init__(self, page_content: str, metadata: Optional[Dict[str, Any]] = None):
            self.page_content = page_content
            self.metadata = metadata or {}

try:
    from rank_bm25 import BM25Okapi
except Exception:
    BM25Okapi = None


def zh_tokenize(text: str) -> List[str]:
    try:
        import jieba
        return [w.strip() for w in jieba.cut(text, cut_all=False) if w and w.strip()]
    except Exception:
        t = (text or "").strip()
        if not t:
            return []
        if len(t) <= 2:
            return list(t)
        return [t[i:i + 2] for i in range(len(t) - 1)]


def build_doc_id(meta: Dict[str, Any], fallback_id: Optional[str] = None) -> str:
    if not meta:
        return fallback_id or ""
    for key in ["_id", "chunk_id", "id", "source_id"]:
        if key in meta and meta[key]:
            return str(meta[key])
    return fallback_id or ""


def minmax_norm(values: Dict[str, float]) -> Dict[str, float]:
    if not values:
        return {}
    vs = list(values.values())
    vmin, vmax = min(vs), max(vs)
    if vmax == vmin:
        return {k: 0.5 for k in values}
    return {k: (v - vmin) / (vmax - vmin) for k, v in values.items()}


def _metadata_list_value(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
        return [part.strip() for part in s.split(";") if part.strip()]
    return [str(value).strip()]


def _kb_image_url(image_path: str) -> str:
    p = (image_path or "").strip().replace("\\", "/")
    if not p:
        return ""
    if p.startswith(("http://", "https://", "/kb_images/")):
        return p
    if p.startswith("images/"):
        p = p[len("images/"):]
    return "/kb_images/" + quote(p.lstrip("/"), safe="/:@")


def format_image_markdown(meta: Dict[str, Any]) -> str:
    image_paths = _metadata_list_value((meta or {}).get("image_paths"))
    if not image_paths:
        return ""
    lines = ["Related images:"]
    seen = set()
    for idx, image_path in enumerate(image_paths, 1):
        url = _kb_image_url(image_path)
        if not url or url in seen:
            continue
        seen.add(url)
        lines.append(f"![Manual image {idx}]({url})")
    return "\n".join(lines) if len(lines) > 1 else ""


def _detect_legacy_sqlite(persist_dir: str) -> bool:
    try:
        return os.path.exists(os.path.join(persist_dir, "chroma.sqlite3"))
    except Exception:
        return False


def _resolve_chroma_dir(path_candidate: str) -> Optional[str]:
    try:
        if not path_candidate:
            return None
        pc = path_candidate.strip()
        if os.path.isdir(pc) and os.path.exists(os.path.join(pc, "chroma.sqlite3")):
            return pc
        if (not os.path.exists(pc)) and pc.endswith(os.sep + "chroma_kb"):
            parent = os.path.dirname(pc)
            if os.path.isdir(parent) and os.path.exists(os.path.join(parent, "chroma.sqlite3")):
                return parent
        cand = os.path.join(pc, "chroma_kb")
        if os.path.isdir(cand) and os.path.exists(os.path.join(cand, "chroma.sqlite3")):
            return cand
        return None
    except Exception:
        return None


_vectordb: Optional["Chroma"] = None
_bm25_index: Optional["BM25Okapi"] = None
_bm25_docs: List["Document"] = []
_bm25_doc_ids: List[str] = []
_bm25_ready: bool = False

_BM25_ALPHA = 0.5


def _pick_existing_collection(client, prefer_name: Optional[str]) -> Optional[str]:
    try:
        cols = client.list_collections()
        names = [c.name for c in cols] if cols else []
        if prefer_name and prefer_name in names:
            return prefer_name
        if names:
            return names[0]
        return prefer_name or "default"
    except Exception:
        return prefer_name or "default"


def _init_chroma(persist_dir_override: Optional[str] = None) -> Optional["Chroma"]:
    if Chroma is None or build_embedding_client is None:
        print("缺少 Chroma 或 embedding 客户端依赖。")
        return None
    try:
        main_env = os.getenv("CHROMA_PERSIST_DIR", "").strip()
        chosen_raw = (persist_dir_override or "").strip() or main_env
        if not chosen_raw:
            print("未设置 CHROMA_PERSIST_DIR。")
            return None

        resolved_dir = _resolve_chroma_dir(chosen_raw)
        if not resolved_dir:
            print(f"无法解析 Chroma 目录: {chosen_raw}")
            return None

        prefer_collection = os.getenv("CHROMA_COLLECTION_NAME", "").strip() or None
        embeddings = build_embedding_client()

        if _CHROMADB_AVAILABLE and hasattr(_chromadb_mod, "PersistentClient"):
            try:
                client = _chromadb_mod.PersistentClient(path=resolved_dir)
                chosen_collection = _pick_existing_collection(client, prefer_collection)
                return Chroma(collection_name=chosen_collection, client=client, embedding_function=embeddings)
            except Exception:
                pass

        chroma_db_impl = os.getenv("CHROMA_DB_IMPL", "").strip()
        if not chroma_db_impl and _detect_legacy_sqlite(resolved_dir):
            os.environ["CHROMA_DB_IMPL"] = "sqlite"

        collection_name = prefer_collection or "default"
        return Chroma(
            collection_name=collection_name,
            persist_directory=resolved_dir,
            embedding_function=embeddings,
        )
    except Exception as e:
        print(f"Chroma 初始化失败: {e}")
        traceback.print_exc()
        return None


def _build_bm25(vs: "Chroma") -> None:
    global _bm25_index, _bm25_docs, _bm25_doc_ids, _bm25_ready
    _bm25_index = None
    _bm25_docs = []
    _bm25_doc_ids = []
    _bm25_ready = False

    if BM25Okapi is None:
        return
    try:
        if not hasattr(vs, "_collection") or vs._collection is None:
            return
        collection = vs._collection
        if collection.count() == 0:
            return

        data = collection.get()
        docs_raw = data.get("documents", [])
        metas_raw = data.get("metadatas", [])
        ids_raw = data.get("ids", [])

        token_corpus: List[List[str]] = []
        for i, text in enumerate(docs_raw or []):
            if text is None:
                continue
            meta = metas_raw[i] if metas_raw and i < len(metas_raw) else {}
            doc = Document(page_content=text, metadata=meta or {})
            _bm25_docs.append(doc)
            fallback_id = str(ids_raw[i]) if ids_raw and i < len(ids_raw) else f"doc_{i}"
            _bm25_doc_ids.append(build_doc_id(doc.metadata, fallback_id=fallback_id))
            token_corpus.append(zh_tokenize(text))

        if not token_corpus:
            return
        _bm25_index = BM25Okapi(token_corpus)
        _bm25_ready = True
    except Exception as e:
        print(f"BM25 构建失败: {e}")
        _bm25_ready = False


def init_local_kb(persist_dir: Optional[str] = None, force: bool = False) -> str:
    global _vectordb
    if _vectordb is not None and not force:
        return "Local KB already initialized."
    if force:
        _vectordb = None
    _vectordb = _init_chroma(persist_dir_override=persist_dir)
    if _vectordb is None:
        return "Failed to initialize local KB."
    _build_bm25(_vectordb)
    return "Local KB initialized."


def reset_local_kb_cache() -> None:
    global _vectordb, _bm25_index, _bm25_docs, _bm25_doc_ids, _bm25_ready
    _vectordb = None
    _bm25_index = None
    _bm25_docs = []
    _bm25_doc_ids = []
    _bm25_ready = False


def _doc_matches_product(meta: Dict[str, Any], product_id: Optional[str]) -> bool:
    if not product_id:
        return True
    return str(meta.get("product_id", "")) == product_id


def _apply_alias_boost(
    scores: Dict[str, float],
    boost_product_ids: List[str],
    emb_docs_map: Dict[str, Document],
    bm25_id_to_doc: Dict[str, Document],
) -> Dict[str, float]:
    if not boost_product_ids:
        return scores
    boosted = dict(scores)
    boost_set = set(boost_product_ids)
    for did, score in list(boosted.items()):
        doc = emb_docs_map.get(did) or bm25_id_to_doc.get(did)
        if doc and str((doc.metadata or {}).get("product_id", "")) in boost_set:
            boosted[did] = min(1.0, score + 0.15)
    return boosted


def retrieve_hybrid(
    query: str,
    top_k: int = 5,
    product_id: Optional[str] = None,
    boost_product_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    if _vectordb is None:
        return []

    fetch_k = max(1, top_k * 5)
    chroma_filter = {"product_id": product_id} if product_id else None

    emb_scores_raw: Dict[str, float] = {}
    emb_docs_map: Dict[str, Document] = {}
    try:
        if chroma_filter:
            emb_raw = _vectordb.similarity_search_with_score(
                query, k=fetch_k, filter=chroma_filter
            )
        else:
            emb_raw = _vectordb.similarity_search_with_score(query, k=fetch_k)
        for doc, dist in emb_raw or []:
            if not _doc_matches_product(doc.metadata or {}, product_id):
                continue
            did = build_doc_id(doc.metadata, fallback_id=f"emb_{hash(doc.page_content)}")
            emb_docs_map[did] = doc
            emb_scores_raw[did] = 1.0 - float(dist)
        emb_scores = minmax_norm(emb_scores_raw)
    except Exception as e:
        err = str(e)
        if "429" in err or "限流" in err:
            print(f"向量检索失败（Embedding API 限流，已降级 BM25）: {e}")
        else:
            print(f"向量检索失败: {e}")
        emb_scores, emb_docs_map = {}, {}

    bm25_scores: Dict[str, float] = {}
    bm25_id_to_doc = {did: doc for did, doc in zip(_bm25_doc_ids, _bm25_docs)}
    if _bm25_ready and _bm25_index is not None:
        try:
            q_tokens = zh_tokenize(query)
            all_scores = _bm25_index.get_scores(q_tokens)
            idx_scores = sorted(enumerate(all_scores), key=lambda x: x[1], reverse=True)
            for idx, sc in idx_scores:
                if sc <= 0 or idx >= len(_bm25_doc_ids):
                    continue
                doc = _bm25_docs[idx]
                if not _doc_matches_product(doc.metadata or {}, product_id):
                    continue
                bm25_scores[_bm25_doc_ids[idx]] = float(sc)
                if len(bm25_scores) >= fetch_k:
                    break
            bm25_scores = minmax_norm(bm25_scores)
        except Exception as e:
            print(f"BM25 检索失败: {e}")

    # 向量检索失败时将 BM25 权重提升至 1.0，避免所有分数被压到 0.5 以下
    _effective_bm25_alpha = 1.0 if not emb_scores else _BM25_ALPHA
    all_ids = set(emb_scores.keys()) | set(bm25_scores.keys())
    fused: List[Tuple[float, Document]] = []
    for did in all_ids:
        score = (_effective_bm25_alpha * bm25_scores.get(did, 0.0)) + ((1.0 - _effective_bm25_alpha) * emb_scores.get(did, 0.0))
        doc = emb_docs_map.get(did) or bm25_id_to_doc.get(did)
        if doc:
            fused.append((score, doc))

    if boost_product_ids:
        score_map = {build_doc_id(d.metadata, fallback_id=f"f_{i}"): s for i, (s, d) in enumerate(fused)}
        score_map = _apply_alias_boost(score_map, boost_product_ids, emb_docs_map, bm25_id_to_doc)
        fused = [(score_map.get(build_doc_id(d.metadata, fallback_id=f"f_{i}"), s), d) for i, (s, d) in enumerate(fused)]

    fused_sorted = sorted(fused, key=lambda x: x[0], reverse=True)[:max(1, top_k)]
    out: List[Dict[str, Any]] = []
    for score, doc in fused_sorted:
        meta = dict(doc.metadata or {})
        source = meta.get("source") or meta.get("file_path") or "local"
        title = meta.get("section_title") or meta.get("title") or os.path.basename(str(source))
        meta["source"] = source
        meta["title"] = title
        meta["score"] = float(score)
        out.append({"content": doc.page_content, "metadata": meta})
    return out


def search_local_kb(
    query: str,
    top_k: int = 5,
    product_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if _vectordb is None:
        init_local_kb()
    if _vectordb is None:
        return []
    try:
        return retrieve_hybrid(query=query, top_k=top_k, product_id=product_id)
    except Exception as e:
        print(f"检索失败: {e}")
        traceback.print_exc()
        return []


def local_kb_status() -> Dict[str, Any]:
    count = 0
    if _vectordb is not None and hasattr(_vectordb, "_collection") and _vectordb._collection is not None:
        try:
            count = _vectordb._collection.count()
        except Exception:
            count = 0
    return {
        "chroma_ready": _vectordb is not None,
        "bm25_ready": _bm25_ready,
        "chunk_count": count,
    }
