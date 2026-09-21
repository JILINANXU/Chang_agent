---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '745d79ad-ebc3-44f1-a913-e35889b49b09'
  PropagateID: '745d79ad-ebc3-44f1-a913-e35889b49b09'
  ReservedCode1: '3f3565de-24eb-4a16-bca3-32040ae26657'
  ReservedCode2: '3f3565de-24eb-4a16-bca3-32040ae26657'
---

# 畅Agent Web 层文档（WEB）

> 覆盖：服务端接口、SSE 事件协议、前端结构、演示模式与真实模式的切换方式。
> 代码位置：`run.py`（启动口）、`changagent/web/`（服务端 + 静态页面）。

---

## 1. 为什么是"零构建前端"

| 方案 | 启动成本 | 网络依赖 | 选择 |
| --- | --- | --- | --- |
| Vue3 + Vite | 需装 Node、`npm install`、两个服务 | 需要拉取 npm 包 | 否 |
| **FastAPI 托管纯 HTML/CSS/JS** | **一条 `python run.py`** | **无** | **是（当前）** |

理由：本阶段的验证目标是"界面能不能把 Agent 的工作过程讲清楚"，而不是工程化。零构建方案让启动口保持一条命令、无外部网络依赖（国内网络环境下 npm 安装经常是第一个坑）。后续要上 Vue，接口契约（第 3、4 节）可以直接复用。

---

## 2. 启动方式

```powershell
python run.py                  # 推荐：依赖自检 + 自动开浏览器
python run.py --port 9000      # 指定端口
python run.py --no-browser     # 只起服务，不开浏览器
python run.py --reload         # 开发模式：改代码自动重启

python -m changagent.web       # 等价入口（不带依赖自检）
```

启动口 `run.py` 做的四件事：

1. 校验 Python ≥ 3.10；
2. 检查 `fastapi` / `uvicorn`，缺失则自动 `pip install`；
3. 读取 `.env`（若存在）并打印当前模式与工作区；
4. 端口占用自动顺延（默认 8765，避开 ComfyUI 的 8000），然后启动并打开浏览器。

---

## 3. HTTP 接口

| 方法 | 路径 | 用途 | 返回 |
| --- | --- | --- | --- |
| GET | `/` | 主页面 | `index.html` |
| GET | `/guide` | 内置说明文档页 | `guide.html` |
| GET | `/static/*` | 静态资源 | CSS / JS |
| GET | `/api/health` | 健康检查（启动口自检用） | `{"ok": true, "app": "畅Agent", "mode": "demo"}` |
| GET | `/api/state` | 页面初始化数据 | 见下 |
| GET | `/api/run?task=...` | **SSE 事件流**（核心） | `text/event-stream` |

### 3.1 `/api/state` 返回结构

```json
{
  "app": "畅Agent",
  "version": "0.3.0",
  "mode": "live",
  "workspace": "C:/.../examples/demo_workspace",
  "workspace_exists": true,
  "model": "agnes-2.5-flash",
  "model_configured": true,
  "shell_enabled": false,
  "auto_approve": true,
  "demo_task": "在 hello.py 中新增 greet(name) 函数，并让 __main__ 调用它",
  "files": [
    { "path": "hello.py",  "type": "file", "depth": 0, "size": 176 },
    { "path": "data",      "type": "dir",  "depth": 0 },
    { "path": "data/a.json", "type": "file", "depth": 1, "size": 1200 }
  ]
}
```

字段说明：

- `version` 取自 `changagent.__version__`；
- `model` 在未配置 API Key 时返回 `"未配置"`，`model_configured` 供前端做提示；
- `workspace` 的解析规则与内核、启动口共用 `config.resolve_workspace()`（未配置时指向 `examples/demo_workspace`，相对路径按项目根解析）；
- `auto_approve` 告知前端当前是否跳过逐次写入确认（见第 6.2 节）。

`files` 为扁平列表，`depth` 表示缩进层级（0 为顶层），由前端渲染成文件树；扫描时自动跳过 `.git`、`__pycache__`、`node_modules`、`.venv`、`.changagent` 等目录，最多两级。

---

## 4. SSE 事件协议

**这是 Web 层与 Agent 内核之间的唯一契约。** 前端只认事件，不关心事件是 mock 造的还是真实模型产生的 —— 因此把 `demo_events` 换成真实内核后，前端一行都不用改。

每帧格式（标准 SSE）：

```text
data: {"type": "...", ...}

```

### 4.1 事件类型总表

| type | 字段 | 触发时机 | 前端表现 |
| --- | --- | --- | --- |
| `agent_start` | `task` `model` `workspace` | 一次运行开始 | 创建运行分组 |
| `thinking` | `text` | 模型给出计划/推理 | 斜体思考块 |
| `tool_call` | `id` `name` `args` | 发起工具调用 | 工具卡片（运行中，蓝色脉冲） |
| `tool_result` | `id` `ok` `elapsed_ms` `content` | 工具返回 | 卡片转成功/失败，显示结果与耗时 |
| `file_change` | `path` `added` `removed` `backup` `diff[]` | 文件被修改 | 流内 diff 卡片 + 右侧变更面板 |
| `answer` | `text` | 任务完成，最终答复 | 绿色完成块 |
| `error` | `text` | 出错（含未接入模型） | 红色错误块 |
| `done` | `ok` `steps` `usage` | 运行结束（必然发送） | 收尾：更新统计、恢复输入 |

### 4.2 `diff` 行结构

```json
{ "t": "ctx", "text": "..." }     // 上下文行
{ "t": "+",   "text": "..." }     // 新增行
{ "t": "-",   "text": "..." }     // 删除行
```

配色由前端决定：`+` 绿底、`-` 红底、`ctx` 暗灰。

### 4.3 完整事件序列示例

```text
data: {"type":"agent_start","task":"在 hello.py 中新增 greet 函数","model":"demo（未接入模型）"}
data: {"type":"thinking","text":"先确认工作区结构，不猜路径。"}
data: {"type":"tool_call","id":"c1","name":"list_dir","args":{"path":".","depth":1}}
data: {"type":"tool_result","id":"c1","ok":true,"elapsed_ms":2,"content":"[ok] ..."}
data: {"type":"thinking","text":"读一下 hello.py 的原文。"}
data: {"type":"tool_call","id":"c2","name":"read_file","args":{"path":"hello.py"}}
data: {"type":"tool_result","id":"c2","ok":true,"elapsed_ms":1,"content":"[ok] hello.py（第 1–9 行 / 共 9 行）..."}
data: {"type":"tool_call","id":"c3","name":"edit_file","args":{"path":"hello.py","old_string":"...","new_string":"..."}}
data: {"type":"tool_result","id":"c3","ok":true,"elapsed_ms":4,"content":"[ok] 已修改 hello.py（1 处替换）"}
data: {"type":"file_change","path":"hello.py","added":7,"removed":2,"diff":[...]}
data: {"type":"answer","text":"已完成：..."}
data: {"type":"done","ok":true,"steps":4,"usage":{"prompt_tokens":4211,"completion_tokens":388}}
```

### 4.4 协议约定（实现真实内核时必须遵守）

1. `done` **必定发送**，无论成功、失败还是中断 —— 前端靠它收尾；
2. `tool_result.id` 必须与对应 `tool_call.id` 一致，否则卡片无法更新状态；
3. 一个 `tool_call` 只对应一个 `tool_result`（成功或失败都算）；
4. `file_change` 只在文件真正落盘后发送，且须带 `diff`；
5. 事件必须**按发生顺序**推送，不可乱序合并。

---

## 5. 前端文件结构

```text
changagent/web/static/
├─ index.html    页面骨架：顶栏 / 左栏(文件树+示例任务) / 中栏(对话流+输入) / 右栏(变更+统计)
├─ guide.html    内置说明文档页（/guide，静态长文，带目录锚点）
├─ style.css     暗色主题，配色全部走 :root 的 CSS 变量（不硬编码颜色）
└─ app.js        状态管理、SSE 订阅、事件渲染、diff 构建
```

界面分区：

| 区域 | 内容 |
| --- | --- |
| 顶栏 | 品牌、工作区路径、模型名、模式徽章、重置 |
| 左栏 | 工作区文件树（点击可看）、示例任务（一键运行） |
| 中栏 | 对话流（用户消息 / 思考 / 工具卡片 / diff / 答复）+ 输入区 |
| 右栏 | 文件变更列表（含 diff）+ 本次运行统计（状态/步数/工具调用/耗时） |

交互细节：

- `Ctrl + Enter` 运行；运行中输入框保留可编辑，按钮变为"停止"；
- 工具卡片点击标题栏可折叠；结果超过 28 行自动折叠；
- 对话流智能滚动：用户手动上滚后不再强制到底部；
- 响应式：< 1220px 隐藏右栏（diff 在流内仍有展示），< 820px 隐藏左栏。

---

## 6. 演示模式与真实模式

### 6.1 接入已完成（M1）

`live_events()` 已接入内核，实现就是文档第 4 节协议的一句话版本：

```python
def live_events(task: str) -> Iterator[str]:
    from ..core.agent import run_stream   # 延迟导入：演示模式无需加载内核
    generator = run_stream(task)
    try:
        for event in generator:
            yield _sse(event)
    except Exception as exc:              # 兜底：前端必须收到收尾事件
        yield _sse({"type": "error", "text": f"服务端异常：{exc}"})
        yield _sse({"type": "done", "ok": False, "steps": 0, "usage": {}})
    finally:
        generator.close()
```

实测（`agnes-2.5-flash`，`examples/demo_workspace`）：

| 任务 | 事件序列 | 结果 |
| --- | --- | --- |
| 只读：列出文件并说明 `hello.py` | `agent_start → tool_call/result ×2 → answer → done` | `done.ok=true, steps=2` |
| 写入：给 `main()` 加一行 print | `agent_start → tool_call/result → tool_call/result → file_change → tool_call/result → answer → done` | `done.ok=true, steps=4`，备份已生成 |

### 6.2 切换到真实模式

在 `.env` 里设置：

```ini
CHANG_AGENT_WEB_MODE=live
CHANG_AGENT_API_KEY=...
CHANG_AGENT_AUTO_APPROVE=true     # Web 是单向事件流，无法暂停等你点确认
```

重启 `python run.py`，顶部徽章会变成绿色的「真实模式」。

> **`AUTO_APPROVE` 的语义**：点击「运行」即视为授权本次全部写操作。
> 它不绕过沙箱与备份，但不会逐次询问。重要目录建议先整体备份，或改用 CLI 逐次确认。
>
> 若 `live` 模式下缺少 API Key 或 `openai` 库，`run.py` 会自动回退到演示模式，
> **并把 `CHANG_AGENT_WEB_MODE=demo` 写回环境变量**，保证 Web 层的模式判断与启动口一致，
> 不会出现「终端说演示、页面却按真实模式去调模型」的错位。

---

## 7. 已知限制

| 限制 | 说明 |
| --- | --- |
| 单次运行不支持并发 | 前端同时只允许一个任务在跑；内核的 `run_stream()` 也没有全局锁，多标签页并跑会互相干扰 |
| 页面不保留历史 | 刷新即清空；会话已按次落盘到 `.changagent/sessions/`，但界面上暂无历史列表 |
| SSE 单向 | 运行中不支持向 Agent 追加指令（预留：后续可加 `POST /api/steer`） |
| 文件树只读 | 页面上不能直接编辑文件，改动一律由 Agent 执行 |
| 断线不落盘 | 客户端中途断开时，本次会话记录不会写入（内核在收敛后才调用 `finish()`） |