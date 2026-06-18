# 科陆用户手册 RAG 智能体

面向科陆储能产品用户手册的轻量级 RAG（检索增强生成）问答系统。支持在 **11 份产品手册** 内进行通用问答或指定产品问答，并结合手册配图进行多模态回答。

## 功能特性

- **通用问答**：不选产品时，检索全部手册并整合回答
- **指定产品问答**：按产品树下拉选择后，仅在对应手册内检索
- **混合检索**：向量（Chroma）+ BM25 关键词 + BGE Cross-Encoder 重排
- **话题感知扩展**：对「安装 / 接线 / 故障 / 维护」等意图自动扩展子查询
- **多模态回答**：检索到的示意图通过 Gemini Vision 参与生成，并在回答中 inline 插入 Markdown 图片
- **双入口**：Gradio Web UI（`app.py`）与 FastAPI REST API（`api_server.py`）

## 目录结构

```
GSC_v0617/
├── 用户手册/                    # PDF 原稿（11 份）
├── rag_output_manuals/          # MinerU 解析产物（Markdown + 图片）
├── rag_data/all/                # 知识库运行时数据
│   ├── kv_store_text_chunks.json   # Step1 切分后的 chunk 文本
│   ├── ingest_manifest.json        # 入库 manifest（增量同步用）
│   ├── section_index.json          # 章节索引（话题扩展用）
│   ├── images/                     # 手册配图（静态资源）
│   └── chroma.sqlite3              # Chroma 向量库（及关联文件）
├── config/
│   └── products.json            # 产品树、PDF/MD 映射、别名
├── ingest/                      # 离线入库流水线
│   ├── step1_build_knowledge.py # PDF/MD → chunk JSON
│   ├── step2_json2chroma.py     # chunk JSON → Chroma 向量
│   ├── manual_chunk_splitter.py # 语义切分
│   └── build_section_index.py   # 章节索引构建
├── core/                        # 基础设施层
│   ├── embedding_client.py      # 在线 / Ollama Embedding
│   ├── local_db.py              # Chroma + BM25 混合检索
│   ├── rerank_client.py         # BGE Cross-Encoder 重排
│   ├── gemini_chat.py           # Gemini 多模态调用
│   └── network_env.py           # 代理绕过配置
├── manual_qa/                   # 问答 Agent 层
│   ├── agent.py                 # 检索 → 组 prompt → 调 LLM
│   ├── retriever.py             # 混合检索编排
│   └── prompts.py               # System / User Prompt 模板
├── scripts/
│   ├── setup_server.sh          # 服务器一键准备（Reranker 等）
│   └── trace_retrieval.py       # 检索链路调试脚本
├── app.py                       # Gradio UI
├── api_server.py                # FastAPI 服务
├── .env.example                 # 环境变量模板
└── docs/
    └── RAG.md                   # RAG 流水线详细文档
```

> 仓库内 `用户手册/`、`rag_output_manuals/` 与预构建的 `rag_data/all/` 可直接使用，**clone 后通常无需重新入库即可问答**。

## 环境要求

| 组件 | 要求 |
|------|------|
| Python | 3.11+ |
| 运行环境 | 推荐 `thinkdepth` conda 环境 |
| Embedding（生产） | 美的 AIMP 在线 API（`Qwen3-Embedding-4B`） |
| LLM | 美的 AIMP Gemini 网关（`gemini-2.5-flash`） |
| Reranker | `BAAI/bge-reranker-v2-m3`（约 230MB，首次需下载） |
| Embedding（本地可选） | Ollama + `qwen3-embedding:4b` |

## 快速开始

### 1. 安装依赖

```bash
cd GSC_v0617
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

已有 `thinkdepth` 环境时，通常只需补装 Reranker 依赖：

```bash
bash scripts/setup_server.sh
# 或
pip install -r requirements-server.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少填入：

```env
# Gemini 回答生成
MIDEA_API_KEY=你的密钥
MIDEA_AIGC_USER=你的用户标识
GEMINI_MODEL=gemini-2.5-flash

# Embedding（生产推荐 online）
EMBED_BACKEND=online
EMBED_BASE_URL=https://aimpapi.midea.com/t-aigc/aimp-text-embedding/v1
EMBED_API_KEY=你的embedding密钥
EMBED_MODEL=Qwen3-Embedding-4B

# Chroma
CHROMA_PERSIST_DIR=./rag_data/all
CHROMA_COLLECTION_NAME=gsc_manual_kb

# API 鉴权
PUBLIC_API_TOKEN=changeme
```

> **注意**：`.env` 不要提交到 Git。同一文件中不要重复定义 `EMBED_BACKEND` / `EMBED_MODEL`，否则后者覆盖前者。

### 3. 启动服务

**Gradio UI（默认端口 7860，可用 `GRADIO_PORT` 覆盖）：**

```bash
python app.py
```

**FastAPI（默认端口 8000）：**

```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

浏览器访问 Gradio：`http://<host>:7860/`  
API 文档：`http://<host>:8000/docs`

### 4. 冒烟测试

```bash
curl "http://127.0.0.1:8000/api/v1/kb/status?token=changeme"
```

期望返回类似：

```json
{"chroma_ready": true, "bm25_ready": true, "chunk_count": 1234}
```

## 生产部署

```bash
git clone <仓库URL> GSC_v0617
cd GSC_v0617
conda activate thinkdepth

bash scripts/setup_server.sh

cp .env.example .env
# 编辑 .env：EMBED_BACKEND=online、API Key、RERANK_MODEL 等

# 推荐 API 方式
uvicorn api_server:app --host 0.0.0.0 --port 8000

# 或 Gradio UI
python app.py
```

### Reranker 模型路径

首次运行会从 HuggingFace 下载 `BAAI/bge-reranker-v2-m3`。服务器网络较慢时，`setup_server.sh` 默认使用 `hf-mirror.com` 镜像。也可在 `.env` 中指定本机路径：

```env
RERANK_MODEL=/home/user/work/models/bge-reranker-v2-m3
```

## API 参考

所有 API 均需 `token` 参数（对应 `.env` 中 `PUBLIC_API_TOKEN`）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/products?token=` | 获取产品树 |
| POST | `/api/v1/chat` | 问答（含 LLM 生成） |
| POST | `/api/v1/search` | 纯检索（不调用 LLM） |
| GET | `/api/v1/kb/status?token=` | 知识库状态 |
| POST | `/api/v1/admin/reindex` | 触发增量入库 |

### 问答示例

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "token": "changeme",
    "question": "电池过放如何处理？",
    "product_id": "aqua-e-261-125-2h-cn"
  }'
```

`product_id` 可省略，省略时为通用问答模式。

### 纯检索示例

```bash
curl -X POST http://127.0.0.1:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "token": "changeme",
    "query": "如何安装",
    "product_id": "aqua-e-261-125-2h-cn",
    "top_k": 5
  }'
```

## 配置说明

完整模板见 [`.env.example`](.env.example)。常用变量：

### LLM（Gemini）

| 变量 | 说明 | 默认 |
|------|------|------|
| `MIDEA_API_KEY` | 美的 AIMP API 密钥 | — |
| `MIDEA_AIGC_USER` | AIGC 用户标识 | — |
| `GEMINI_MODEL` | 模型名称 | `gemini-2.5-flash` |
| `GEMINI_VISION_ENABLED` | 是否启用多模态 | `true` |
| `GEMINI_MAX_IMAGES` | 每次最多附带图片数 | `5` |

### Embedding

| 变量 | 说明 | 默认 |
|------|------|------|
| `EMBED_BACKEND` | `online` 或 `ollama` | `ollama`（未设置时） |
| `EMBED_BASE_URL` | 在线 API 地址 | 美的 AIMP |
| `EMBED_API_KEY` | Embedding API 密钥 | — |
| `EMBED_MODEL` | 模型名 | 依 backend 而定 |
| `EMBED_MAX_RETRIES` | 429 限流重试次数 | `8` |
| `EMBED_RETRY_BASE_DELAY` | 重试基础延迟（秒） | `2.0` |
| `EMBED_QUERY_INTERVAL` | 子查询间隔（秒） | `0.35` |
| `EMBED_QUERY_CACHE_SIZE` | query embedding 缓存条数 | `256` |

### 检索

| 变量 | 说明 | 默认 |
|------|------|------|
| `RETRIEVAL_TOP_K` | 最终返回 chunk 数 | `4` |
| `RETRIEVAL_MIN_SCORE` | 最低相关度阈值 | `0.15` |
| `RETRIEVAL_TOPIC_EXTRA_MAX` | 话题扩展子查询上限 | `3` |
| `RERANK_ENABLED` | 是否启用 Cross-Encoder | `true` |
| `RERANK_MODEL` | Reranker 模型路径或 HF id | `BAAI/bge-reranker-v2-m3` |

### 向量库

| 变量 | 说明 | 默认 |
|------|------|------|
| `CHROMA_PERSIST_DIR` | Chroma 持久化目录 | `./rag_data/all` |
| `CHROMA_COLLECTION_NAME` | 集合名称 | `gsc_manual_kb` |

## 重新入库

更新手册内容或切换 Embedding 后端后，需重新入库。详见 [docs/RAG.md](docs/RAG.md)。

```bash
# 已有 MinerU 解析结果，增量更新
python ingest/step1_build_knowledge.py --skip-parse --sync
python ingest/step2_json2chroma.py --sync

# 从 PDF 全量解析（需安装 MinerU）
python ingest/step1_build_knowledge.py --input-dir ./用户手册
python ingest/step2_json2chroma.py
```

> **重要**：入库与查询必须使用同一套 Embedding 后端和模型，否则向量空间不一致会导致检索质量严重下降。

## 本地开发（Ollama Embedding）

无需在线 Embedding 配额时，可使用本地 Ollama：

```env
EMBED_BACKEND=ollama
OLLAMA_HOST=http://127.0.0.1:11434
EMBED_MODEL=qwen3-embedding:4b
```

安装 Ollama 并拉取模型后，**仍需用 Ollama 重跑 step2** 重建向量库。

## 调试

**检索链路调试：**

```bash
TRACE_QUERY="如何安装" TRACE_PRODUCT="aqua-e-261-125-2h-cn" python scripts/trace_retrieval.py
```

**常见问题：**

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `向量检索失败: 429` | Embedding API 限流 | 调大 `EMBED_QUERY_INTERVAL`；申请提高配额；见 [docs/RAG.md#限流与降级](docs/RAG.md#限流与降级) |
| `未找到相关信息` | 检索分数低于阈值 | 换问法；降低 `RETRIEVAL_MIN_SCORE`；检查 product_id |
| Reranker 加载失败 | 模型未下载 | 运行 `setup_server.sh` 或设置 `RERANK_MODEL` 本机路径 |
| 图片不显示 | 静态资源未挂载 | 确认 `/kb_images` 路由正常（app / api 均已挂载） |

## 架构概览

```
用户问题
   │
   ▼
hybrid_search（retriever.py）
   ├─ 话题扩展 / 介绍类扩展 → 多条子查询
   ├─ retrieve_hybrid（local_db.py）→ 向量 + BM25 融合
   ├─ 话题后处理 / 硬过滤
   └─ BGE Cross-Encoder 重排
   │
   ▼
format_context + build_image_catalog
   │
   ▼
gemini_chat_once（Gemini + 可选 Vision）
   │
   ▼
结构化中文回答 + 参考来源
```

更详细的 RAG 设计、数据格式、切分策略与调参指南见 **[docs/RAG.md](docs/RAG.md)**。

## 许可证与数据

- 用户手册 PDF 版权归科陆电子所有，仅供内部问答使用
- API 密钥等敏感信息请勿提交至版本控制
