# 户储平台使用指南智能问答 — 前端 API 对接文档

> 版本：v1.0  
> 后端技术栈：FastAPI + RAG（检索增强生成）  
> 在线 Swagger 文档：`{BASE_URL}/docs`

---

## 1. 概述

本服务为「户储平台使用指南」智能问答的后端 API。前端通过 HTTP 请求调用，无需使用 Gradio 测试页面。

**典型对接流程：**

1. 调用 `GET /api/v1/products` 获取文档列表，渲染「文档选择」下拉框
2. 用户输入问题后，调用 `POST /api/v1/chat/stream`（**推荐，流式输出**）或 `POST /api/v1/chat`（一次性返回）获取 AI 回答
3. 将返回的 `answer`（Markdown）渲染到聊天气泡
4. 将 `sources` 渲染为「参考来源」区域
5. 回答中的图片链接需拼接 Base URL 后展示

---

## 2. 服务信息

| 项目 | 值 |
|------|-----|
| Base URL | `https://aiops.szclou.com:50200` |
| 协议 | HTTPS |
| 数据格式 | JSON |
| 字符编码 | UTF-8 |
| 在线文档 | `https://aiops.szclou.com:50200/docs` |

> Base URL 以实际部署为准，联调前请向后端同学确认。

---

## 3. 鉴权

所有接口均需要 `token` 参数，值由后端同学私下提供（对应服务端 `.env` 中的 `PUBLIC_API_TOKEN`）。

| 请求方式 | token 传递位置 |
|----------|----------------|
| GET | Query 参数，如 `?token=xxx` |
| POST | JSON Body 字段 `"token": "xxx"` |

**鉴权失败响应：**

```json
HTTP 401
{
  "detail": "Invalid token"
}
```

> Token 请勿提交到 Git 仓库或写在前端公开代码中。生产环境建议通过后端代理转发，或由前端自己的服务端持有 Token。

---

## 4. 接口列表

| 方法 | 路径 | 说明 | 前端是否常用 |
|------|------|------|-------------|
| GET | `/api/v1/products` | 获取产品/文档树 | ✅ 是 |
| POST | `/api/v1/chat/stream` | 智能问答（SSE 流式输出，**推荐**） | ✅ 是 |
| POST | `/api/v1/chat` | 智能问答（一次性返回，兼容旧版） | 可选 |
| POST | `/api/v1/search` | 纯检索，不调用 LLM | 可选 |
| GET | `/api/v1/kb/status` | 知识库就绪状态 | 可选（健康检查） |
| GET | `/kb_images/{filename}` | 手册配图静态资源 | ✅ 是（渲染回答内图片） |
| POST | `/api/v1/admin/reindex` | 触发知识库重建 | ❌ 仅运维 |

---

## 5. 接口详情

### 5.1 获取文档列表

用于构建「文档选择」下拉框。不传 `product_id` 时即为「通用问答（全部指南）」模式。

**请求**

```
GET /api/v1/products?token={token}
```

**响应示例**

```json
{
  "series": [
    {
      "id": "residential-platform",
      "name": "户储平台",
      "products": [
        {
          "id": "residential-platform-guide",
          "display_name": "户储平台使用指南",
          "manual_pdf": "商储平台使用指南.pdf",
          "md_stem": "商储平台使用指南",
          "aliases": ["户储平台", "商储平台", "平台使用指南"]
        }
      ]
    }
  ]
}
```

**前端用法建议**

- 下拉框第一项：`{ label: "通用问答（全部指南）", value: null }`
- 其余选项：遍历 `series[].products[]`，`label` 建议为 `[{series.name}] {display_name}`，`value` 为 `product.id`
- 用户选中某文档后，问答时将对应 `product.id` 作为 `product_id` 传入

---

### 5.2 智能问答 — 流式（推荐）

**请求**

```
POST /api/v1/chat/stream
Content-Type: application/json
Accept: text/event-stream
```

**请求 Body**（与 5.3 相同）

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `token` | string | ✅ | 鉴权 Token |
| `question` | string | ✅ | 用户问题 |
| `product_id` | string \| null | ❌ | 指定文档 ID；省略或 `null` 为通用问答 |
| `session_id` | string \| null | ❌ | 预留字段，当前后端未启用多轮记忆 |

**SSE 事件流**

响应 `Content-Type: text/event-stream`，按顺序推送以下事件：

| 事件名 | 说明 | data JSON 示例 |
|--------|------|----------------|
| `meta` | 检索完成，携带参考来源 | `{"sources":[...],"product_id":"...","display_name":"..."}` |
| `delta` | LLM 文本增量 | `{"text":"登录"}` |
| `done` | 生成结束 | `{"answer":"完整 Markdown 回答"}` |
| `error` | 服务端异常 | `{"error":"错误信息"}` |

**前端处理建议**

1. 收到 `meta` 后立即渲染 `sources`
2. 每收到 `delta` 将 `text` 追加到当前回答气泡
3. 收到 `done` 后关闭 Loading；可用 `answer` 做最终校验
4. 使用 `fetch` + `ReadableStream` 或 `EventSource` 均可（POST 需用 fetch）

**性能说明**

- 检索阶段（`meta` 之前）通常 **2~10 秒**，此期间请展示 Loading
- `meta` 之后回答逐字/逐段流出，首字延迟明显低于一次性接口
- 建议总超时 **120 秒**

---

### 5.3 智能问答 — 一次性返回（兼容）

**请求**

```
POST /api/v1/chat
Content-Type: application/json
```

**请求 Body**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `token` | string | ✅ | 鉴权 Token |
| `question` | string | ✅ | 用户问题 |
| `product_id` | string \| null | ❌ | 指定文档 ID；省略或 `null` 为通用问答 |
| `session_id` | string \| null | ❌ | 预留字段，当前后端未启用多轮记忆 |

**请求示例 — 通用问答**

```json
{
  "token": "your-token-here",
  "question": "如何登录平台？"
}
```

**请求示例 — 指定文档**

```json
{
  "token": "your-token-here",
  "question": "如何查看告警？",
  "product_id": "residential-platform-guide"
}
```

**响应 Body**

| 字段 | 类型 | 说明 |
|------|------|------|
| `answer` | string | AI 回答，Markdown 格式，可能含图片链接 |
| `sources` | array | 参考来源列表 |
| `product_id` | string \| null | 实际检索使用的产品 ID |
| `display_name` | string | 产品显示名；通用模式为「全部产品」 |

**响应示例**

```json
{
  "answer": "根据手册，登录平台步骤如下：\n\n1. 打开浏览器访问平台地址\n2. 输入账号密码\n\n![登录界面](/kb_images/xxx.png)",
  "sources": [
    {
      "product_id": "residential-platform-guide",
      "display_name": "户储平台使用指南",
      "section_title": "登录平台",
      "score": 0.852,
      "snippet": "打开浏览器，输入平台地址...",
      "source": "商储平台使用指南.md"
    }
  ],
  "product_id": "residential-platform-guide",
  "display_name": "户储平台使用指南"
}
```

**sources 单条字段说明**

| 字段 | 类型 | 说明 |
|------|------|------|
| `product_id` | string | 来源文档 ID |
| `display_name` | string | 来源文档名称 |
| `section_title` | string | 来源章节标题 |
| `score` | number | 相关度分数（0~1，越高越相关） |
| `snippet` | string | 原文片段摘要（约 300 字） |
| `source` | string | 原始 Markdown 文件名 |

**性能说明**

- 单次问答通常需要 **10~60 秒**（含向量检索 + LLM 生成）
- 前端请展示 Loading 状态，建议请求超时设为 **120 秒**
- 回答完成后一次性返回；新接入请优先使用 5.2 流式接口

**多轮对话说明**

- 当前版本每次请求独立处理，**不携带历史上下文**
- `session_id` 字段已预留但未实现；如需多轮对话，需与后端协商后续扩展

---

### 5.4 纯检索（可选）

仅返回知识库检索结果，不调用 LLM。可用于调试或「仅看原文片段」场景。

**请求**

```
POST /api/v1/search
Content-Type: application/json
```

**请求 Body**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `token` | string | ✅ | 鉴权 Token |
| `query` | string | ✅ | 检索关键词 |
| `product_id` | string \| null | ❌ | 限定文档范围 |
| `top_k` | integer | ❌ | 返回条数，默认 5，范围 1~20 |

**请求示例**

```json
{
  "token": "your-token-here",
  "query": "如何安装",
  "product_id": "residential-platform-guide",
  "top_k": 5
}
```

**响应示例**

```json
{
  "query": "如何安装",
  "product_id": "residential-platform-guide",
  "results": [
    {
      "content": "安装前请确认...",
      "metadata": {
        "product_id": "residential-platform-guide",
        "section_title": "安装说明"
      }
    }
  ]
}
```

---

### 5.5 知识库状态（健康检查）

**请求**

```
GET /api/v1/kb/status?token={token}
```

**响应示例**

```json
{
  "chroma_ready": true,
  "bm25_ready": true,
  "chunk_count": 1234
}
```

联调前可先调用此接口确认服务正常。

---

### 5.6 手册配图

回答中的 Markdown 可能包含相对路径图片，例如：

```markdown
![示意图](/kb_images/abc123.png)
```

前端渲染时需将相对路径转为完整 URL：

```
https://aiops.szclou.com:50200/kb_images/abc123.png
```

图片接口无需 Token，可直接 `<img>` 引用。

---

## 6. 错误码

| HTTP 状态码 | 含义 | 处理建议 |
|-------------|------|----------|
| 200 | 成功 | 正常解析响应 |
| 401 | Token 无效 | 检查 Token 是否正确 |
| 422 | 请求参数格式错误 | 检查 JSON 字段名和类型 |
| 500 | 服务端内部错误 | 提示用户稍后重试，联系后端排查 |

**500 响应示例（问答处理失败时）**

后端异常时可能返回 500，前端应做兜底提示，如「服务暂时不可用，请稍后重试」。

---

## 7. 前端集成示例

### 7.1 JavaScript / fetch

```javascript
const BASE_URL = 'https://aiops.szclou.com:50200';
const TOKEN = 'your-token-here';  // 生产环境勿硬编码

// 获取文档列表
async function fetchProducts() {
  const res = await fetch(`${BASE_URL}/api/v1/products?token=${TOKEN}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// 发送问题（流式，推荐）
async function askQuestionStream(question, productId = null, onMeta, onDelta, onDone) {
  const res = await fetch(`${BASE_URL}/api/v1/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      token: TOKEN,
      question,
      product_id: productId,
    }),
    signal: AbortSignal.timeout(120_000),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const chunks = buffer.split('\n\n');
    buffer = chunks.pop() || '';

    for (const chunk of chunks) {
      const lines = chunk.split('\n');
      let event = 'message';
      let data = '';
      for (const line of lines) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === 'meta') onMeta?.(payload);
      else if (event === 'delta') onDelta?.(payload.text);
      else if (event === 'done') onDone?.(payload.answer);
      else if (event === 'error') throw new Error(payload.error);
    }
  }
}

// 发送问题（一次性返回，兼容旧版）
async function askQuestion(question, productId = null) {
  const res = await fetch(`${BASE_URL}/api/v1/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      token: TOKEN,
      question,
      product_id: productId,
    }),
    signal: AbortSignal.timeout(120_000),  // 120 秒超时
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// 流式使用示例
let answer = '';
await askQuestionStream(
  '如何登录平台？',
  'residential-platform-guide',
  (meta) => renderSources(meta.sources),
  (text) => { answer += text; renderMarkdown(answer); },
  (finalAnswer) => console.log('done', finalAnswer),
);

// 一次性使用示例
const data = await askQuestion('如何登录平台？', 'residential-platform-guide');
console.log(data.answer);    // Markdown 字符串
console.log(data.sources);   // 参考来源数组
```

### 7.2 图片路径处理

```javascript
function fixImageUrls(markdown, baseUrl) {
  return markdown.replace(
    /(\!\[.*?\]\()(\/kb_images\/[^)]+)(\))/g,
    `$1${baseUrl}$2$3`
  );
}

const html = renderMarkdown(fixImageUrls(data.answer, BASE_URL));
```

### 7.3 参考来源展示

```javascript
function renderSources(sources) {
  if (!sources?.length) return '';
  return sources.map((s, i) =>
    `${i + 1}. ${s.display_name} / ${s.section_title} (score=${s.score.toFixed(3)})`
  ).join('\n');
}
```

---

## 8. 跨域（CORS）说明

当前 API **未配置 CORS 响应头**。

| 场景 | 方案 |
|------|------|
| 前端页面与 API 同域（如都在 `aiops.szclou.com`） | 浏览器可直接调用 |
| 前端页面在不同域名 | 需后端添加 CORS，或前端通过自己的服务端代理转发 |

联调前请确认前端页面部署域名，如有跨域需求请提前告知后端同学。

---

## 9. 联调检查清单

- [ ] 已拿到 Token
- [ ] `GET /api/v1/kb/status` 返回 `chroma_ready: true`
- [ ] `GET /api/v1/products` 能正常返回文档列表
- [ ] `POST /api/v1/chat/stream` 流式问答有逐段输出
- [ ] `POST /api/v1/chat` 通用问答（不传 product_id）有正常回答
- [ ] `POST /api/v1/chat` 指定 product_id 有正常回答
- [ ] Markdown 渲染正常（标题、列表、加粗等）
- [ ] `/kb_images/` 图片能正常显示
- [ ] 已确认跨域方案（如需要）
- [ ] Loading 和超时（建议 120s）已处理

---

## 10. curl 快速测试

```bash
# 健康检查
curl "https://aiops.szclou.com:50200/api/v1/kb/status?token=YOUR_TOKEN"

# 获取文档列表
curl "https://aiops.szclou.com:50200/api/v1/products?token=YOUR_TOKEN"

# 流式问答（推荐）
curl -N -X POST "https://aiops.szclou.com:50200/api/v1/chat/stream" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"token":"YOUR_TOKEN","question":"如何登录平台？"}'

# 通用问答（一次性）
curl -X POST "https://aiops.szclou.com:50200/api/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"token":"YOUR_TOKEN","question":"如何登录平台？"}'

# 指定文档问答
curl -X POST "https://aiops.szclou.com:50200/api/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{"token":"YOUR_TOKEN","question":"如何查看告警？","product_id":"residential-platform-guide"}'
```

---

## 11. 联系方式

| 事项 | 联系人 |
|------|--------|
| Token 申请 | （后端同学填写） |
| 接口问题 / 500 报错 | （后端同学填写） |
| 跨域配置 | （后端同学填写） |
| 新增文档 / 知识库更新 | （后端同学填写） |

---

*文档对应后端入口：`api_server.py`，启动命令：`uvicorn api_server:app --host 0.0.0.0 --port 50200`*
