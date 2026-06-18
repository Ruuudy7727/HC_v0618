#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(dotenv_path=str(PROJECT_ROOT / ".env"), override=False)

from core.network_env import configure_runtime_network_env

configure_runtime_network_env()

from config import load_products_config
from core.local_db import init_local_kb, local_kb_status, reset_local_kb_cache, search_local_kb
from manual_qa.agent import answer_question

PUBLIC_API_TOKEN = os.getenv("PUBLIC_API_TOKEN", "changeme")
KB_IMAGES_DIR = PROJECT_ROOT / "rag_data" / "all" / "images"
KB_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Manual QA Agent API", version="1.0.0")
app.mount("/kb_images", StaticFiles(directory=str(KB_IMAGES_DIR)), name="kb_images")


def verify_token(token: str) -> None:
    if PUBLIC_API_TOKEN and token != PUBLIC_API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


class ChatRequest(BaseModel):
    question: str
    product_id: Optional[str] = None
    session_id: Optional[str] = None
    token: str = ""


class SearchRequest(BaseModel):
    query: str
    product_id: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=20)
    token: str = ""


class ReindexRequest(BaseModel):
    token: str = ""
    sync: bool = True


@app.on_event("startup")  # noqa: deprecated but simple for Phase 1
async def startup() -> None:
    init_local_kb()


@app.get("/")
async def root() -> Dict[str, str]:
    return {"message": "Manual QA Agent API", "docs": "/docs"}


@app.get("/api/v1/products")
async def get_products(token: str = Query(...)) -> Dict[str, Any]:
    verify_token(token)
    return load_products_config()


@app.post("/api/v1/chat")
async def chat(req: ChatRequest) -> Dict[str, Any]:
    verify_token(req.token)
    result = await asyncio.to_thread(
        answer_question,
        req.question,
        req.product_id,
        None,
    )
    return {
        "answer": result.answer,
        "sources": result.sources,
        "product_id": result.product_id,
        "display_name": result.display_name,
    }


@app.post("/api/v1/search")
async def search(req: SearchRequest) -> Dict[str, Any]:
    verify_token(req.token)
    results = await asyncio.to_thread(
        search_local_kb,
        req.query,
        req.top_k,
        req.product_id,
    )
    return {
        "query": req.query,
        "product_id": req.product_id,
        "results": [
            {
                "content": item.get("content", "")[:500],
                "metadata": item.get("metadata", {}),
            }
            for item in results
        ],
    }


@app.get("/api/v1/kb/status")
async def kb_status(token: str = Query(...)) -> Dict[str, Any]:
    verify_token(token)
    return local_kb_status()


@app.post("/api/v1/admin/reindex")
async def admin_reindex(req: ReindexRequest) -> Dict[str, Any]:
    verify_token(req.token)

    step1_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "ingest" / "step1_build_knowledge.py"),
        "--skip-parse",
    ]
    if req.sync:
        step1_cmd.append("--sync")

    step2_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "ingest" / "step2_json2chroma.py"),
    ]
    if req.sync:
        step2_cmd.append("--sync")

    for cmd in [step1_cmd, step2_cmd]:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
        if proc.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail={
                    "command": " ".join(cmd),
                    "stdout": proc.stdout[-2000:],
                    "stderr": proc.stderr[-2000:],
                },
            )

    reset_local_kb_cache()
    init_local_kb(force=True)
    return {"status": "ok", "sync": req.sync, "kb": local_kb_status()}
