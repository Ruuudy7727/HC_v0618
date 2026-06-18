#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IDE 断点调试用：在 retriever.py 的 hybrid_search 内设断点后 F5 启动本脚本。"""

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from manual_qa.retriever import hybrid_search

QUERY = os.getenv("TRACE_QUERY", "设备故障如何排查")
PRODUCT = os.getenv("TRACE_PRODUCT", "clmg-1125")

chunks = hybrid_search(QUERY, product_id=PRODUCT)
print(f"query={QUERY!r} product={PRODUCT!r} -> {len(chunks)} chunks")
for i, c in enumerate(chunks, 1):
    print(f"  {i}. score={c.score:.4f} | {c.section_title}")
