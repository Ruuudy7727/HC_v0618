# 户储平台使用指南 — RAG 智能问答

面向 **户储平台使用指南** 的轻量级 RAG（检索增强生成）问答系统。支持通用问答或指定文档问答，结合手册配图进行多模态回答，并提供 **流式输出**。

## 代码仓库

| 项目 | 地址 |
|------|------|
| GitHub | https://github.com/Ruuudy7727/HC_v0618 |
| Clone | `git clone https://github.com/Ruuudy7727/HC_v0618.git` |

```bash
git clone https://github.com/Ruuudy7727/HC_v0618.git
cd HC_v0618
```

> Git 常用命令与服务器部署场景见 [docs/git-速查.md](docs/git-速查.md)。

---

## 功能特性

- **通用问答**：不选文档时，检索全部指南并整合回答
- **指定文档问答**：按产品树下拉选择后，仅在对应手册内检索
- **流式输出**：Gradio UI 与 `POST /api/v1/chat/stream` 支持 SSE 逐段返回答案
- **混合检索**：向量（Chroma）+ BM25 关键词，可选 BGE Cross-Encoder 重排
- **话题感知扩展**：对「安装 / 接线 / 故障 / 维护」等意图自动扩展子查询
- **多模态回答**：检索到的示意图通过 Gemini Vision 参与生成，并在回答中 inline 插入 Markdown 图片
- **双入口**：Gradio Web UI（`app.py`）与 FastAPI REST API（`api_server.py`）

---

## 目录结构

```
HC_v0618/
├── 户储手册/                         # PDF 原稿
├── rag_output_manuals/               # MinerU 解析产物（Markdown + 图片）
├── rag_data/all/                     # 知识库运行时数据（已入库，可直接问答）
│   ├── kv_store_text_chunks.json     # Step1 切分后的 chunk 文本
│   ├── ingest_manifest.json          # 入库 manifest（增量同步用）
│   ├── section_index.json            # 章节索引（话题扩展用）
│   ├── images/                       # 手册配图（静态资源）
│   └── chroma.sqlite3                # Chroma 向量库
├── config/
│   └── products.json                 # 产品树、PDF/MD 映射、别名
├── ingest/                           # 离线入库流水线
│   ├── step1_build_knowledge.py      # PDF/MD → chunk JSON
│   ├── step2_json2chroma.py          # chunk JSON → Chroma 向量
│   ├── manual_chunk_splitter.py      # 语义切分
│   └── build_section_index.py        # 章节索引构建
├── core/                             # 基础设施层
│   ├── embedding_client.py           # 在线 / Ollama Embedding
│   ├── local_db.py                   # Chroma + BM25 混合检索
│   ├── rerank_client.py              # BGE Cross-Encoder 重排（可选）
│   ├── gemini_chat.py                # Gemini 同步 / 流式多模态调用
│   └── network_env.py                # 代理绕过配置
├── manual_qa/                        # 问答 Agent 层
│   ├── agent.py                      # 检索 → 组 prompt → 调 LLM（含流式）
│   ├── retriever.py                  # 混合检索编排
│   └── prompts.py                    # System / User Prompt 模板
├── scripts/
│   ├── setup_server.sh               # 服务器一键准备
│   └── trace_retrieval.py            # 检索链路调试脚本
├── app.py                            # Gradio UI（流式对话）
├── api_server.py                     # FastAPI 服务
├── .env.example                      # 环境变量模板
└── docs/
    ├── RAG.md                        # RAG 流水线详细文档
    ├── API对接文档.md                 # 前端 API 对接说明
    └── git-速查.md                    # Git 命令速查
```

> 仓库内已包含预构建的 `rag_data/all/`，**clone 后配置好 `.env` 通常即可直接问答**，无需重新入库。

---

## 环境要求

| 组件 | 要求 |
|------|------|
| Python | 3.11+ |
| 运行环境 | 推荐 `thinkdepth` conda 环境 |
| Embedding（生产） | 美的 AIMP 在线 API（`Qwen3-Embedding-4B`） |
| LLM | 美的 AIMP Gemini 网关（`gemini-2.5-flash`） |
| Reranker（可选） | `BAAI/bge-reranker-v2-m3`，默认关闭（`RERANK_ENABLED=false`） |
| Embedding（本地可选） | Ollama + `qwen3-embedding:4b` |

---

## 快速开始

### 1. 克隆与安装依赖

```bash
git clone https://github.com/Ruuudy7727/HC_v0618.git
cd HC_v0618

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

已有 `thinkdepth` 环境时，若需启用 Reranker：

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
CHROMA_COLLECTION_NAME=hc_residential_platform_kb

# API 鉴权
PUBLIC_API_TOKEN=changeme
```

> **注意**：`.env` 不要提交到 Git。流式 LLM 默认使用 `stream/v2` 接口，由 `sync/v1` URL 自动推导；也可在 `.env` 中显式设置 `GEMINI_URL_STREAM`。

### 3. 启动服务

**Gradio UI（默认端口 7860，可用 `GRADIO_PORT` 覆盖）：**

```bash
python app.py
```

浏览器访问：`http://<host>:7860/`

**FastAPI（生产推荐，示例端口 50200）：**

```bash
uvicorn api_server:app --host 0.0.0.0 --port 50200
```

- API 文档：`http://<host>:50200/docs`
- 生产环境 Base URL：`https://aiops.szclou.com:50200`（以实际部署为准）

### 4. 冒烟测试

```bash
# 知识库状态
curl "http://127.0.0.1:50200/api/v1/kb/status?token=changeme"

# 流式问答（推荐）
curl -N -X POST "http://127.0.0.1:50200/api/v1/chat/stream" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"token":"changeme","question":"如何登录平台？"}'
```

期望 `kb/status` 返回类似：

```json
{"chroma_ready": true, "bm25_ready": true, "chunk_count": 123}
```

---

## 生产部署

```bash
git clone https://github.com/Ruuudy7727/HC_v0618.git
cd HC_v0618
conda activate thinkdepth

cp .env.example .env
# 编辑 .env：API Key、PUBLIC_API_TOKEN、GRADIO_PORT 等

# 若本地 rag_data 与 Git 冲突，先备份再 pull
# mv rag_data rag_data.bak && git pull origin main

uvicorn api_server:app --host 0.0.0.0 --port 50200
```

服务器更新代码：

```bash
git pull origin main
git branch --set-upstream-to=origin/main main   # 首次设置跟踪分支
# 重启 uvicorn 服务
```

详见 [docs/git-速查.md](docs/git-速查.md)。

---

## API 参考

所有 API 均需 `token` 参数（对应 `.env` 中 `PUBLIC_API_TOKEN`）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/products?token=` | 获取文档 / 产品树 |
| POST | `/api/v1/chat/stream` | 智能问答（**SSE 流式，推荐**） |
| POST | `/api/v1/chat` | 智能问答（一次性返回，兼容旧版） |
| POST | `/api/v1/search` | 纯检索（不调用 LLM） |
| GET | `/api/v1/kb/status?token=` | 知识库状态 |
| GET | `/kb_images/{filename}` | 手册配图静态资源 |
| POST | `/api/v1/admin/reindex` | 触发增量入库（运维） |

### 流式问答示例

```bash
curl -N -X POST http://127.0.0.1:50200/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{
    "token": "changeme",
    "question": "如何查看告警？",
    "product_id": "residential-platform-guide"
  }'
```

SSE 事件顺序：`meta`（参考来源）→ `delta`（文本增量）→ `done`（完成）。

### 一次性问答示例

```bash
curl -X POST http://127.0.0.1:50200/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "token": "changeme",
    "question": "如何登录平台？"
  }'
```

`product_id` 可省略，省略时为通用问答模式。

> 前端对接细节、跨域说明、JavaScript 示例见 **[docs/API对接文档.md](docs/API对接文档.md)**。

---

## 配置说明

完整模板见 [`.env.example`](.env.example)。

### LLM（Gemini）

| 变量 | 说明 | 默认 |
|------|------|------|
| `MIDEA_API_KEY` | 美的 AIMP API 密钥 | — |
| `MIDEA_AIGC_USER` | AIGC 用户标识 | — |
| `GEMINI_MODEL` | 模型名称 | `gemini-2.5-flash` |
| `GEMINI_URL_SYNC` | 非流式接口 | `.../sync/v1/chat/completions` |
| `GEMINI_URL_STREAM` | 流式接口 v2 | 由 sync URL 自动推导 |
| `GEMINI_VISION_ENABLED` | 是否启用多模态 | `true` |
| `GEMINI_MAX_IMAGES` | 每次最多附带图片数 | `5` |

### Embedding

| 变量 | 说明 | 默认 |
|------|------|------|
| `EMBED_BACKEND` | `online` 或 `ollama` | `online` |
| `EMBED_BASE_URL` | 在线 API 地址 | 美的 AIMP |
| `EMBED_API_KEY` | Embedding API 密钥 | — |
| `EMBED_MODEL` | 模型名 | `Qwen3-Embedding-4B` |
| `EMBED_QUERY_INTERVAL` | 子查询间隔（秒） | `0.35` |

### 检索

| 变量 | 说明 | 默认 |
|------|------|------|
| `RETRIEVAL_TOP_K` | 最终返回 chunk 数 | `4` |
| `RETRIEVAL_MIN_SCORE` | 最低相关度阈值 | `0.15` |
| `RERANK_ENABLED` | 是否启用 Cross-Encoder | `false` |

### 向量库

| 变量 | 说明 | 默认 |
|------|------|------|
| `CHROMA_PERSIST_DIR` | Chroma 持久化目录 | `./rag_data/all` |
| `CHROMA_COLLECTION_NAME` | 集合名称 | `hc_residential_platform_kb` |

---

## 重新入库

更新手册内容或切换 Embedding 后端后，需重新入库。详见 [docs/RAG.md](docs/RAG.md)。

```bash
# 已有 MinerU 解析结果，增量更新
python ingest/step1_build_knowledge.py --skip-parse --sync
python ingest/step2_json2chroma.py --sync

# 从 PDF 全量解析（需安装 MinerU）
python ingest/step1_build_knowledge.py --input-dir ./户储手册
python ingest/step2_json2chroma.py
```

> **重要**：入库与查询必须使用同一套 Embedding 后端和模型，否则向量空间不一致会导致检索质量严重下降。

---

## 本地开发（Ollama Embedding）

无需在线 Embedding 配额时，可使用本地 Ollama：

```env
EMBED_BACKEND=ollama
OLLAMA_HOST=http://127.0.0.1:11434
EMBED_MODEL=qwen3-embedding:4b
```

安装 Ollama 并拉取模型后，**仍需用 Ollama 重跑 step2** 重建向量库。

---

## 架构概览

```
用户问题
   │
   ▼
hybrid_search（retriever.py）          ← Embedding + Chroma + BM25（非流式，一次性完成）
   ├─ 话题扩展 / 介绍类扩展
   ├─ retrieve_hybrid（local_db.py）
   └─ 可选 Cross-Encoder 重排
   │
   ▼
format_context + build_image_catalog
   │
   ▼
gemini_chat_stream / gemini_chat_once   ← Gemini stream/v2 或 sync/v1
   │
   ▼
结构化中文回答 + 参考来源（流式逐段 / 一次性返回）
```

更详细的 RAG 设计、数据格式、切分策略与调参指南见 **[docs/RAG.md](docs/RAG.md)**。

---

## 调试

**检索链路调试：**

```bash
TRACE_QUERY="如何登录平台" TRACE_PRODUCT="residential-platform-guide" python scripts/trace_retrieval.py
```

**常见问题：**

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `向量检索失败: 429` | Embedding API 限流 | 调大 `EMBED_QUERY_INTERVAL`；见 [docs/RAG.md](docs/RAG.md) |
| `未找到相关信息` | 检索分数低于阈值 | 换问法；降低 `RETRIEVAL_MIN_SCORE`；检查 product_id |
| 流式无输出 / 报错 | 流式 URL 不正确 | 确认 `GEMINI_URL_STREAM` 为 `stream/v2` |
| `git pull` 失败 | 本地未跟踪的 `rag_data/` 冲突 | 先 `mv rag_data rag_data.bak` 再 pull，见 [docs/git-速查.md](docs/git-速查.md) |
| 图片不显示 | 静态资源未挂载 | 确认 `/kb_images` 路由正常（app / api 均已挂载） |

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [docs/RAG.md](docs/RAG.md) | RAG 流水线、入库、检索调参 |
| [docs/API对接文档.md](docs/API对接文档.md) | 前端 API 对接、流式 SSE、跨域 |
| [docs/git-速查.md](docs/git-速查.md) | Git 命令与服务器部署场景 |

---

## 许可证与数据

- 用户手册 PDF 版权归相关方所有，仅供内部问答使用
- API 密钥等敏感信息请勿提交至版本控制（`.env` 已在 `.gitignore` 中）
