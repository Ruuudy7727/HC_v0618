#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(dotenv_path=str(PROJECT_ROOT / ".env"), override=False)

from core.network_env import configure_runtime_network_env

configure_runtime_network_env()

from config import list_all_products
from core.local_db import init_local_kb
from manual_qa.agent import answer_question

KB_IMAGES_DIR = PROJECT_ROOT / "rag_data" / "all" / "images"
KB_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Gradio Dropdown 元组格式为 (显示名, 传值)
GENERAL_OPTION = ("通用问答（全部产品）", "")


def build_product_choices() -> List[Tuple[str, str]]:
    """返回 Gradio Dropdown 选项，元组格式为 (显示名, product_id)。"""
    choices = [GENERAL_OPTION]
    current_series = ""
    for product in list_all_products():
        series_name = product.get("series_name", "")
        if series_name != current_series:
            current_series = series_name
        label = f"[{series_name}] {product.get('display_name', product.get('id'))}"
        choices.append((label, product["id"]))
    return choices


def format_sources(sources: List[dict]) -> str:
    if not sources:
        return ""
    lines = ["**参考来源：**"]
    for i, src in enumerate(sources, 1):
        lines.append(
            f"{i}. {src.get('display_name', '')} / {src.get('section_title', '')} "
            f"(score={src.get('score', 0):.3f})"
        )
    return "\n".join(lines)


def _history_to_text(history: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for item in history[-6:]:
        role = item.get("role", "")
        content = str(item.get("content", "") or "")
        if role == "user":
            lines.append(f"User: {content}")
        elif role == "assistant":
            lines.append(f"AI: {content}")
    return lines


def chat_fn(
    message: str,
    history: List[Dict[str, Any]],
    product_id: Optional[str],
) -> Tuple[List[Dict[str, Any]], str, str]:
    if not message.strip():
        return history, "", ""

    result = answer_question(
        message,
        product_id=product_id if product_id else None,
        history=_history_to_text(history),
    )
    answer = result.answer
    sources_md = format_sources(result.sources)

    history = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": answer},
    ]
    return history, "", sources_md


def build_demo() -> gr.Blocks:
    choices = build_product_choices()
    choice_labels = [c[0] for c in choices]
    choice_values = [c[1] for c in choices]
    id_to_label = dict(zip(choice_values, choice_labels))

    with gr.Blocks(title="科陆用户手册智能问答") as demo:
        gr.Markdown("# 科陆用户手册智能问答")
        gr.Markdown("支持通用问答，也可选择指定产品后仅在对应手册内检索。")

        with gr.Row():
            with gr.Column(scale=1):
                product_dropdown = gr.Dropdown(
                    choices=choices,
                    value=None,
                    label="产品选择",
                    allow_custom_value=False,
                    interactive=True,
                )
                current_product = gr.Markdown("当前模式：**通用问答（全部产品）**")
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(label="对话", height=480, render_markdown=True, type="messages")
                sources_box = gr.Markdown("")
                with gr.Row():
                    msg = gr.Textbox(label="输入问题", scale=4, placeholder="例如：电池过放如何处理？")
                    send = gr.Button("发送", variant="primary")

        def on_product_change(product_id: Optional[str]):
            if not product_id:
                return "当前模式：**通用问答（全部产品）**"
            label = id_to_label.get(product_id, product_id)
            return f"当前模式：**{label}**"

        product_dropdown.change(on_product_change, inputs=product_dropdown, outputs=current_product)

        send.click(
            chat_fn,
            inputs=[msg, chatbot, product_dropdown],
            outputs=[chatbot, msg, sources_box],
        )
        msg.submit(
            chat_fn,
            inputs=[msg, chatbot, product_dropdown],
            outputs=[chatbot, msg, sources_box],
        )

    return demo


def create_app() -> FastAPI:
    demo = build_demo()
    fastapi_app = FastAPI(title="科陆用户手册智能问答")
    fastapi_app.mount("/kb_images", StaticFiles(directory=str(KB_IMAGES_DIR)), name="kb_images")
    return gr.mount_gradio_app(fastapi_app, demo, path="/")


def main() -> None:
    init_local_kb()
    port = int(os.getenv("GRADIO_PORT", "7860"))
    uvicorn.run(create_app(), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
