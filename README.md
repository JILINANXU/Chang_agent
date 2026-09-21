---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '58ef964c-4902-4f3c-9585-0568c759cb22'
  PropagateID: '58ef964c-4902-4f3c-9585-0568c759cb22'
  ReservedCode1: '40a29808-22e7-46b6-9d6e-1b0cf80a33db'
  ReservedCode2: '40a29808-22e7-46b6-9d6e-1b0cf80a33db'
---

# 畅Agent（ChangAgent）

> 版本 0.3.0　最后更新：2026-09-18

一个用 Python 从零实现的**本地编码智能体（Coding Agent）**：你用自然语言说需求，它自己找文件、读代码、改代码，并把每一步都留痕。

它不是一个"聊天机器人套壳"，而是一个带有**提示词工程层 + 工具执行层 + 安全沙箱层**的完整 Agent 骨架，核心能力是**根据提示修改文件**。

---

## 1. 它能做什么

| 你说 | 它做 |
| --- | --- |
| "在 `hello.py` 里加一个 `greet(name)` 函数" | 定位文件 → 读取 → 生成补丁 → 写入 → 回报改动 |
| "把 `utils.py` 里所有 `print` 换成 `logging`" | 检索引用 → 精确替换 → 展示 diff |
| "这个项目是干嘛的？" | 扫描目录树 → 读取关键文件 → 输出结构说明 |
| "把所有 `TODO` 找出来，列成清单写到 `TODO.md`" | grep 汇总 → 新建文件 → 写入 |

---

## 2. 核心特性

1. **可视化操作界面（Web UI）**
   在浏览器里直接下任务：Agent 的思考、每次工具调用、参数与返回、文件 diff 全部实时可见。零构建（FastAPI + 纯 HTML/CSS/JS），一条 `python run.py` 启动，不用装 Node；右上角「说明」页内置完整使用手册。

2. **分层提示词（Prompt Engineering）**
   `系统人设 + 工作原则 + 工具规范 + 动态环境 + 输出约束` 五层拼装。提示词以 Markdown 模板形式独立存放（`changagent/prompts/`），可随时调优、不碰代码，并支持运行时注入工作目录、文件树、可用工具等变量。

3. **七个原子工具（Tool Calling）**
   `read_file` / `write_file` / `edit_file` / `list_dir` / `glob_search` / `grep_search`，外加默认关闭的 `run_command`。每个工具附带标准 JSON Schema，走原生 Function Calling 协议。

4. **安全兜底（Sandbox）**
   所有文件操作强制经过路径沙箱校验，禁止越出工作目录；每次写入前自动备份到 `.changagent/backups/`；`.env`、`.git`、私钥等敏感目标默认拒写。

5. **模型无关（Model Agnostic）**
   基于 OpenAI 兼容协议，改 `.env` 里的 `CHANG_AGENT_BASE_URL` / `CHANG_AGENT_MODEL` 即可切换 DeepSeek / 通义 / Kimi / 本地 vLLM / Ollama，无需改一行业务代码。

6. **过程可回溯（Session）**
   每次任务的完整思考链、工具调用、参数、结果都会落盘到 `.changagent/sessions/`，随时可恢复与复盘。

---

## 3. 快速开始

### 3.1 启动网页界面（当前可用）

```powershell
python run.py
```

一条命令搞定：自动检查依赖、自动打开浏览器；端口默认 8765，被占用自动顺延，`python run.py --port 9000` 可指定。

界面有两种模式，由 `.env` 的 `CHANG_AGENT_WEB_MODE` 控制：

| 模式 | 需要 API Key | 说明 |
| --- | --- | --- |
| `demo` | 否 | 播放预置事件流，先看交互长什么样 |
| `live` | 是 | 真实调用模型，对工作目录做真实读写 |

两种模式吐的是**同一套 SSE 事件协议**，前端无需改动。配了 Key 走 `live`；没配则自动回退 `demo`，不会卡住或报错。

### 3.2 命令行方式

```powershell
# 1) 建虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 装依赖
pip install -r requirements.txt

# 3) 配密钥
Copy-Item .env.example .env
#    然后编辑 .env，填入 CHANG_AGENT_API_KEY 等

# 4) 跑一个任务（对示例工作区操作，安全）
python -m changagent --workspace .\examples\demo_workspace "在 hello.py 中新增 greet(name) 函数，并让它被 __main__ 调用"
```

写入类操作会先弹终端确认（`.env` 里设 `CHANG_AGENT_AUTO_APPROVE=true` 可跳过），改前自动备份到 `.changagent/backups/`，回滚就是把备份覆盖回去。

交互式模式（无 `--workspace` 后面的任务描述即进入 REPL）：

```powershell
python -m changagent --workspace .\examples\demo_workspace
> 先看看这个项目有哪些文件
> 把 hello.py 的问候语改成中文
> exit
```

---

## 4. 目录结构

```text
畅agent/
├─ README.md                  ← 本文件：项目总览
├─ pyproject.toml             ← 包元信息、依赖、工具配置
├─ requirements.txt           ← 运行依赖（pip 安装用）
├─ .env.example               ← 配置模板（复制为 .env 使用）
├─ .gitignore
├─ run.py                     ← 启动口：一键启动网页界面
│
├─ docs/                      ← 设计文档
│  ├─ DESIGN.md               ← 总体架构、主循环、数据结构、安全模型
│  ├─ TOOLS.md                ← 工具规格：参数、返回值、JSON Schema
│  ├─ PROMPTS.md              ← 提示词设计：分层结构、模板、调优方法
│  └─ WEB.md                  ← Web 层：接口、SSE 事件协议、模式切换
│
├─ changagent/                ← 主包（唯一源码目录）
│  ├─ __init__.py             ← 版本号与核心入口
│  ├─ __main__.py             ← python -m changagent 入口
│  ├─ cli.py                  ← 命令行：参数解析、REPL、单次任务
│  ├─ config.py               ← 配置加载（.env → AppConfig 对象）
│  │
│  ├─ llm/                    ← 模型接入层（只负责"说话"）
│  │  ├─ client.py            ←   OpenAI 兼容客户端，含重试与超时
│  │  └─ message.py           ←   Message / ToolCall / LLMResponse 数据结构
│  │
│  ├─ prompts/                ← 提示词层（只负责"怎么想"）
│  │  ├─ loader.py            ←   模板装载 + 变量注入 + 拼装
│  │  ├─ system.md            ←   系统提示词主模板
│  │  └─ tool_guide.md        ←   工具使用规范（供系统提示词引用）
│  │
│  ├─ tools/                  ← 工具层（只负责"动手"）
│  │  ├─ base.py              ←   Tool 基类 / ToolResult 统一返回
│  │  ├─ registry.py          ←   注册表 + 分发 + Schema 生成
│  │  ├─ file_read.py         ←   read_file
│  │  ├─ file_write.py        ←   write_file
│  │  ├─ file_edit.py         ←   edit_file（精确替换式补丁）
│  │  ├─ diff.py              ←   unified diff 生成与增删行统计
│  │  ├─ search.py            ←   list_dir / glob_search / grep_search
│  │  └─ shell.py             ←   run_command（默认关闭）
│  │
│  ├─ core/                   ← 内核层（负责"串起来 + 兜底"）
│  │  ├─ agent.py             ←   Agent 主循环
│  │  ├─ context.py           ←   上下文窗口管理与压缩
│  │  ├─ session.py           ←   会话落盘与恢复
│  │  └─ sandbox.py           ←   路径沙箱与写前备份
│  │
│  ├─ ui/                     ← 终端展示层
│  │  └─ render.py            ←   思考流、工具卡片、diff 高亮
│  │
│  └─ web/                    ← Web 界面层（FastAPI + 零构建前端）
│     ├─ server.py            ←   静态托管、/api/state、SSE /api/run、/guide
│     ├─ mock.py              ←   演示事件流（web_mode=demo 时使用）
│     └─ static/              ←   index.html / guide.html / style.css / app.js
│
├─ tests/                     ← 单元测试
│  ├─ test_sandbox.py         ←   越界拦截
│  ├─ test_file_edit.py       ←   替换与冲突
│  ├─ test_registry.py        ←   注册与 Schema
│  └─ test_agent_loop.py      ←   循环收敛与步数保护
│
└─ examples/
   └─ demo_workspace/         ← 练手用沙盒（乱改也不心疼）
```

**分层依赖方向（单向，不允许反向 import）：**

```text
cli  →  core  →  tools  →  (sandbox)
         ↓        ↓
        llm     prompts
         ↓
       config
```

---

## 5. 一次任务的生命周期

```text
用户输入需求
   │
   ▼
[prompts] 拼装 system prompt（人设+原则+工具规范+当前工作目录+文件树）
   │
   ▼
[llm] 请求模型 ──► 模型决定：直接回答 / 调用工具
   │                        │
   │              调用工具 ◄─┘
   │                  │
   │                  ▼
   │            [tools] 沙箱校验 → 执行 → 统一 ToolResult
   │                  │
   │                  ▼
   │            结果回灌对话历史（观察）
   │                  │
   └──── 循环（上限 max_steps）───┘
                      │
                      ▼
            模型不再调用工具 → 输出最终答复
                      │
                      ▼
            [session] 全过程落盘 + [ui] 渲染 diff
```

---

## 6. 当前状态

- [x] **M0 文档与结构定稿**：README、DESIGN、TOOLS、PROMPTS + 完整目录骨架
- [x] **M0.5 Web 界面可运行**：FastAPI 服务 + 零构建前端 + 演示事件流 + 一键启动口 `run.py`
- [x] **M1 内核跑通**：config / llm.client / 七个工具 / agent 主循环 / CLI，Web 已接入真实内核
- [x] **M2 安全与记忆**：sandbox 全量接入、写前备份、session 落盘（随 M1 一并落地）
- [~] **M3 体验**：rich 终端渲染、diff 预览、写前确认已实现，提示词调优待补
- [ ] **M4 测试与扩展**：pytest 全覆盖（`tests/` 目前仍是占位）

> 当前版本 `0.3.0`。已用真实模型（OpenAI 兼容端点）在 `examples/demo_workspace` 上跑通「读 + 改」端到端任务；离线探针 61 项全绿。

---

## 7. 设计文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/DESIGN.md](docs/DESIGN.md) | 目标、架构分层、主循环、数据结构、上下文压缩、安全模型、里程碑 |
| [docs/TOOLS.md](docs/TOOLS.md) | 每个工具的参数、返回格式、错误码、JSON Schema、新增工具步骤 |
| [docs/PROMPTS.md](docs/PROMPTS.md) | 提示词五层结构、模板变量、完整提示词、调优 SOP |
| [docs/WEB.md](docs/WEB.md) | Web 层接口、SSE 事件协议、前端结构、演示/真实模式切换 |
| [web/static/guide.html](changagent/web/static/guide.html) | 界面使用手册：导览、安全边界与回滚、配置项一览（启动后点右上角「说明」） |