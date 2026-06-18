#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

MODEL_DIR="${RERANK_MODEL_DIR:-$HOME/work/models/bge-reranker-v2-m3}"

echo "==> 安装缺失依赖（FlagEmbedding）"
pip install -r requirements-server.txt

echo "==> 准备 reranker 模型: $MODEL_DIR"
mkdir -p "$(dirname "$MODEL_DIR")"

if [ ! -f "$MODEL_DIR/config.json" ]; then
  echo "==> 下载 BAAI/bge-reranker-v2-m3 ..."
  pip install -q "huggingface_hub[cli]"
  if [ -z "${HF_ENDPOINT:-}" ]; then
    export HF_ENDPOINT=https://hf-mirror.com
  fi
  huggingface-cli download BAAI/bge-reranker-v2-m3 --local-dir "$MODEL_DIR"
else
  echo "==> reranker 已存在，跳过下载"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> 已生成 .env，请编辑 API Key 和 RERANK_MODEL"
fi

echo ""
echo "完成。请确认 .env 中："
echo "  EMBED_BACKEND=online"
echo "  RERANK_MODEL=$MODEL_DIR"
echo ""
echo "启动 API："
echo "  uvicorn api_server:app --host 0.0.0.0 --port 8000"
