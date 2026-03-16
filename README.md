# API 2 Cursor

让 Cursor 通过第三方中转站使用任意 LLM 模型的 API 代理服务。

## 它解决什么问题

Cursor 根据模型名发送不同格式的请求：

| Cursor 模型名风格 | 请求格式 |
|---|---|
| `claude-sonnet-*`、`glm-*` | `/v1/chat/completions` (OpenAI CC) |
| `gpt-*`、`claude-opus-*` | `/v1/responses` (OpenAI Responses) |

而中转站通常只支持 `/v1/chat/completions`、`/v1/messages` 或 `/v1/responses`。

本项目在中间做协议转换，**不管 Cursor 发什么格式，都能正确转发到中转站；不管中转站返回什么格式，都让 Cursor 能正确接收**。

## 架构

可以把这个项目理解成“三种入口协议 + 三种上游后端协议”的协议桥：

```text
Cursor                         API 2 Cursor                           中转站
  │                                 │                                   │
  ├─ /v1/chat/completions ─────→ chat.py ─────┬─ openai 后端 ─────────→ /v1/chat/completions
  │                                            ├─ anthropic 后端 ─────→ /v1/messages
  │                                            └─ responses 后端 ─────→ /v1/responses
  │
  ├─ /v1/responses ────────────→ responses.py ─┬─ openai 后端 ───────→ /v1/chat/completions
  │                                             ├─ anthropic 后端 ───→ /v1/messages
  │                                             └─ responses 后端 ───→ /v1/responses
  │
  └─ /v1/messages ─────────────→ messages.py ─────────────────────────→ /v1/messages
```

其中：
- `chat.py` 负责接住 Cursor 的 Chat Completions 请求，并根据模型映射决定发往哪种后端协议
- `responses.py` 负责接住 Cursor 的 Responses 请求，并在需要时做 `Responses ↔ CC` 或 `Responses ↔ Messages` 桥接
- `messages.py` 负责 Anthropic 原生消息的直通场景

## 快速开始

### 直接运行

```bash
cd api2cursor
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入中转站地址和密钥
python start.py
```

### Docker 部署

```bash
cd api2cursor
cp .env.example .env
# 编辑 .env
docker compose up -d
```

服务启动后访问 `http://localhost:3029/admin` 进入管理面板。

## 配置

### 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `PROXY_TARGET_URL` | 默认上游中转站地址（未配置中转站时使用） | `https://api.anthropic.com` |
| `PROXY_API_KEY` | 默认上游 API 密钥 | |
| `PROXY_PORT` | 服务监听端口 | `3029` |
| `API_TIMEOUT` | 请求超时（秒） | `300` |
| `ACCESS_API_KEY` | 访问鉴权密钥，留空不启用 | |
| `DEBUG` | 兼容旧版调试开关，开启后等价于 `DEBUG_MODE=simple` | `false` |
| `DEBUG_MODE` | 调试模式：`off` / `simple` / `verbose` | `off` |

### 管理面板

访问 `http://localhost:3029/admin` 进入全新的可视化管理面板，采用侧边栏导航布局，包含以下功能模块：

#### 📊 仪表盘
- 实时统计：请求总数、Token 用量、运行时长
- 各模型用量明细表

#### 🔗 中转站管理
- 统一管理多个上游中转站地址和密钥
- 一键切换当前激活的中转站
- 所有未单独绑定中转站的模型自动跟随全局激活中转站

**使用场景**：
- 你有多个中转站（如 A 站、B 站、C 站），可以在此统一添加
- 通过下拉框一键切换当前激活的中转站，所有模型立即生效
- 特定模型可以固定绑定到某个中转站，不受全局切换影响

#### 🗺 模型映射
在此配置 Cursor 模型名到上游模型的映射关系：

- **Cursor 模型名** — 在 Cursor 自定义模型中填入的名称
- **上游模型名** — 发送到中转站的实际模型名
- **后端类型** — `openai` / `anthropic` / `responses` / `gemini` / `auto`
- **中转站绑定** — 三种模式：
  - **跟随全局激活中转站**（默认）
  - **指定中转站**（固定使用某个中转站）
  - **自定义地址和密钥**（手动填写 URL/Key）
- **自定义指令** — 可选，注入到每次请求的 system prompt
- **Body/Header 修改** — 高级选项，对上游请求做字段级增删改

**示例**：
- 在 Cursor 中添加 `claude-sonnet-4-5-20250929`，映射到上游 `gpt-5.3-codex`，后端选 `openai`
- Cursor 会用 Chat Completions 格式发送请求，代理转发到中转站的 `/v1/chat/completions`

> **提示**：使用 Claude 风格的模型名（如 `claude-sonnet-*`）可以让 Cursor 显示思考过程（thinking）。

#### ⚙️ 全局设置
- 默认中转站地址和密钥（作为兜底配置）
- 日志模式切换：`off` / `simple` / `verbose`

#### 📋 对话日志
开启 `verbose` 日志模式后，可在此查看所有对话记录：
- 按日期分组的文件树
- 点击任意对话查看完整请求/响应详情
- 支持单条删除

### 调试日志模式

项目支持三档调试模式，可通过环境变量 `DEBUG_MODE` 或管理面板「全局设置」切换：

- `off` — 关闭调试日志
- `simple` — 仅输出控制台调试日志，不写文件
- `verbose` — 输出控制台调试日志，并写入详细的对话级文件日志

详细日志会写入 `data/conversations/YYYY-MM-DD/{conversation_id}.json`，可在管理面板「📋 对话日志」页面查看。

特性：
- 同一段多轮对话聚合到同一个文件
- 自动记录 client request、upstream request/response、client response、错误信息
- 流式事件只保留前 12 条和后 12 条，中间部分折叠计数，避免文件膨胀
- 流式 `client_response` 只记录 summary，不重复保存完整事件数组

### 在 Cursor 中配置

1. 打开 Cursor 设置 → Models
2. 添加自定义模型，名称填映射中配置的 Cursor 模型名
3. Override OpenAI Base URL 填 `http://localhost:3029`
4. API Key 填 `ACCESS_API_KEY` 的值（未配置则随意填）

## 项目结构

```text
api2cursor/
├── start.py                    # 启动入口
├── app.py                      # Flask 应用工厂
├── config.py                   # 环境变量配置
├── settings.py                 # 持久化配置管理（支持多中转站）
├── routes/                     # 路由层：按对外 API 入口拆分
│   ├── chat.py                 #   /v1/chat/completions
│   ├── responses.py            #   /v1/responses
│   ├── messages.py             #   /v1/messages（透传）
│   ├── admin.py                #   管理面板 + API（含日志查看接口）
│   └── common.py               #   路由公共上下文、日志与 SSE 辅助
├── adapters/                   # 适配层：按协议桥接职责拆分
│   ├── cc_anthropic_adapter.py #   Chat Completions ↔ Anthropic Messages
│   ├── cc_gemini_adapter.py    #   Chat Completions ↔ Gemini Contents
│   ├── openai_compat_fixer.py  #   OpenAI / Chat Completions 兼容修复
│   └── responses_cc_adapter.py #   Responses ↔ Chat Completions + 原生 Responses 流桥接
├── utils/                      # 通用工具层
│   ├── http.py                 #   请求转发、SSE 解析
│   ├── request_logger.py       #   对话级文件日志
│   ├── tool_fixer.py           #   工具参数修复
│   ├── think_tag.py            #   <think> 标签提取
│   ├── thinking_cache.py       #   thinking 内容缓存
│   └── usage_tracker.py        #   用量统计
└── static/                     # 管理面板前端（全新设计）
    ├── admin.html              #   侧边栏布局 + 多页面路由
    ├── admin.css               #   深紫/靛蓝主题样式
    └── admin.js                #   SPA 路由 + 日志查看器
```

## 兼容性修复

代理自动处理以下兼容性问题：

- Cursor 扁平格式 tools → 标准 OpenAI 嵌套格式
- `reasoningContent` → `reasoning_content`
- `<think>` 标签 → `reasoning_content`
- 旧版 `function_call` → 新版 `tool_calls`
- `tool_calls` 缺失 `id` / `index` / `type` 字段补全
- 智能引号 → 普通引号（StrReplace 工具精确匹配修复）
- `file_path` → `path` 字段映射
- `finish_reason` 修正

## 许可证

[MIT](LICENSE)

## 典型使用场景

### 场景一：使用单个中转站

这是最简单的场景，适合只有一个 OpenAI 兼容中转站的用户。

1. 启动服务后进入管理面板 `http://localhost:3029/admin`
2. 在「🔗 中转站管理」添加你的中转站地址和密钥，并激活它
3. 在「🗺 模型映射」添加你想在 Cursor 中使用的模型名和对应的上游模型
4. 在 Cursor 设置 → Models 中：
   - 添加自定义模型，名称填写上面配置的 Cursor 模型名
   - Override OpenAI Base URL 填 `http://localhost:3029`
   - API Key 填 `ACCESS_API_KEY` 的值（未配置则随便填）

### 场景二：多个中转站按需切换

适合同时持有多家中转站账号，需要灵活切换的场景。

1. 在「🔗 中转站管理」把所有中转站都添加进来（如 relay-a、relay-b、relay-c）
2. 通过顶部下拉框随时切换当前激活的中转站，所有模型立即生效
3. 无需修改任何模型映射，Cursor 侧配置也无需变动

### 场景三：不同模型走不同中转站

适合不同模型分散在不同中转站的场景（如 Claude 走 A 站，GPT 走 B 站）。

1. 添加所有中转站
2. 在每条模型映射中，将「中转站来源」设为「指定中转站」并绑定对应的中转站
3. 这些模型会固定走绑定的中转站，不受全局激活切换影响

### 场景四：让 Cursor 显示 Thinking（思考过程）

Cursor 对 Claude 风格的模型名有特殊处理，会显示 thinking 内容。

1. 在 Cursor 模型名中使用 `claude-*` 格式（如 `claude-sonnet-4-5-20250929`）
2. 即使上游实际模型不是 Claude，Cursor 也会渲染 thinking 内容
3. 代理会自动从 `<think>` 标签或 `reasoning_content` 字段中提取并转换
