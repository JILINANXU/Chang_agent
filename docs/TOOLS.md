---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '55de0b64-dd30-4de1-9eb6-8ee4b1c9a8bd'
  PropagateID: '55de0b64-dd30-4de1-9eb6-8ee4b1c9a8bd'
  ReservedCode1: 'd1482a9d-b9ed-4cf0-ad1b-9a185daae2de'
  ReservedCode2: 'd1482a9d-b9ed-4cf0-ad1b-9a185daae2de'
---

# 畅Agent 工具规格文档（TOOLS）

> 工具是 Agent 的"手"。本文件定义每一个工具的名称、参数、返回格式、错误语义与 JSON Schema。
> 代码实现位于 `changagent/tools/`，Schema 由 `Tool.parameters` 自动生成，**本文档为唯一事实来源**。
>
> 版本：v0.3（对齐 `changagent` 0.3.0）　最后更新：2026-09-18

---

## 1. 工具总览

| # | 工具名 | 类型 | 用途 | 需确认 | 默认启用 |
| --- | --- | --- | --- | --- | --- |
| 1 | `read_file` | 只读 | 读取文件内容（带行号、支持分页） | 否 | 是 |
| 2 | `list_dir` | 只读 | 列出目录结构 | 否 | 是 |
| 3 | `glob_search` | 只读 | 按文件名模式查找文件 | 否 | 是 |
| 4 | `grep_search` | 只读 | 按内容正则检索 | 否 | 是 |
| 5 | `edit_file` | **写入** | 精确字符串替换，修改已有文件 | 是 | 是 |
| 6 | `write_file` | **写入** | 新建文件或整体覆盖 | 是 | 是 |
| 7 | `run_command` | **执行** | 运行终端命令（编译、测试） | 是 | **否** |

设计约定：

- 只读工具（1–4）**永不产生副作用**，可在提示词中鼓励大胆调用；
- 写入工具（5–6）必须先经过沙箱校验 + 写前备份；
- 执行工具（7）默认不注册给模型，需 `.env` 显式开启；
- 写入与执行工具都带 `requires_approval = true`，落盘前必须过人工确认闸门（详见第 4 节）。

---

## 2. 统一返回格式

所有工具返回 `ToolResult`，回灌给模型时统一序列化为**纯文本**（对 LLM 比 JSON 更易读、更省 token）：

```text
[ok] <正文>
```

```text
[error] <错误码>：<人类可读原因>
原因：<为什么会这样>
建议：<下一步该怎么办>
```

对程序的元信息放在 `ToolResult.meta`，不进入模型上下文：

```python
ToolResult(ok=True, content="...", meta={"path": "a.py", "lines": 120, "backup": "..."}, error=None)
```

### 2.1 错误码总表

| 错误码 | 触发场景 | 模型应如何应对 |
| --- | --- | --- |
| `PATH_OUT_OF_SANDBOX` | 目标路径越出工作目录 | 改用工作目录内的相对路径 |
| `SENSITIVE_FILE` | 命中敏感文件规则或落在忽略目录（`.env` / `.git` / `.changagent` / 私钥 / `node_modules` 等） | 放弃该目标，向用户说明 |
| `NOT_FOUND` | 文件或目录不存在；`read_file` 的 `offset` 超出总行数；**工具名不存在** | 先用 `list_dir` / `glob_search` 确认真实路径；工具名改用提示词里给出的清单 |
| `NOT_A_FILE` | 期望是文件，实际是目录 | 修正路径类型，或改用 `list_dir` |
| `NOT_A_DIR` | 期望是目录，实际是文件 | 修正路径类型，或改用 `read_file` |
| `NOT_READ_YET` | 未读文件就直接 `edit_file` / 覆盖 `write_file` | 先 `read_file` |
| `NO_MATCH` | `edit_file` 的 `old_string` 未命中 | 重新 `read_file` 后复制精确原文（不要带 `行号|` 前缀） |
| `MULTI_MATCH` | `old_string` 命中多处 | 扩大上下文使其唯一，或显式传 `replace_all=true` |
| `ALREADY_EXISTS` | `write_file` 目标已存在且 `overwrite=false` | 确认要整体覆盖则传 `overwrite=true`，只改几行则改用 `edit_file` |
| `FILE_TOO_LARGE` | 文件超 1 MB 且未指定区间 | 用 `offset`/`limit` 分段读 |
| `BINARY_FILE` | 内容是二进制，或编码不是 UTF-8 / GBK | 放弃读取该文件，改用文本类文件 |
| `WRITE_FAILED` | 落盘失败，或写前备份失败（此时放弃写入） | 确认文件未被其它程序占用，或换个文件名 |
| `PARSE_ERROR` | 参数不是合法 JSON、类型不符、缺必填、`glob`/正则非法、命令为空、`old_string` 与 `new_string` 相同 | 按 Schema 重新生成参数 |
| `APPROVAL_REQUIRED` | 写操作需人工确认，但当前通道没有确认能力 | 提示用户开启 `CHANG_AGENT_AUTO_APPROVE`，或改用 CLI 确认后执行 |
| `CMD_BLOCKED` | 命令命中危险黑名单 / 首词不在允许列表 / `git` 子命令未放开 | 换成允许的非破坏性命令，或请用户手动执行 |
| `CMD_TIMEOUT` | 命令超时，已强杀进程树 | 缩小执行范围，或调大 `timeout` |
| `CMD_FAILED` ¹ | 命令正常结束但退出码非 0 | 读结果里的 stdout / stderr 定位问题 |
| `USER_REJECTED` ¹ | 用户在确认环节拒绝 | 停止改动，向用户说明并询问期望做法 |
| `TOOL_CRASH` ¹ | 工具内部出现未预期异常（兜底，不炸断主循环） | 换一种方式完成目标，或如实说明失败 |

> ¹ 这三个以 `ToolResult(ok=false)` 返回，不走 `ToolError` 流程，错误码直接写在回灌文本首行。
>
> **已移除的错误码**：`SHELL_DISABLED`。`run_command` 未开启时**根本不注册**，模型强行调用只会得到 `NOT_FOUND`。

---

## 3. 工具详细规格

### 3.1 `read_file` — 读取文件

**参数**

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `path` | string | 是 | — | 相对工作目录的路径 |
| `offset` | integer | 否 | 1 | 起始行号（1-based） |
| `limit` | integer | 否 | 200 | 最多读取行数，上限 2000 |

**返回示例**

```text
[ok] hello.py（第 1–14 行 / 共 14 行）
1| def main():
2|     print("hello")
...
14| if __name__ == "__main__":
15|     main()
```

**行为约定**

- 每行前缀 `行号|`，便于后续 `edit_file` 精确复制；
- 内容被截断时，末尾提示剩余行数与继续读取的 `offset`；
- 二进制或非 UTF-8 / GBK 编码的文件 → `BINARY_FILE`；超 1 MB 且未指定区间 → `FILE_TOO_LARGE`；
- `offset` 超出文件总行数 → `NOT_FOUND`，并在建议里给出最大可用 offset。

**JSON Schema**

```json
{
  "type": "function",
  "function": {
    "name": "read_file",
    "description": "读取工作目录内某个文本文件的指定行区间。修改文件前必须先用本工具看过原文。每行带行号，便于精确引用。",
    "parameters": {
      "type": "object",
      "properties": {
        "path":   { "type": "string",  "description": "相对工作目录的文件路径，例如 src/main.py" },
        "offset": { "type": "integer", "description": "起始行号，从 1 开始，默认 1" },
        "limit":  { "type": "integer", "description": "最多读取的行数，默认 200，上限 2000" }
      },
      "required": ["path"]
    }
  }
}
```

---

### 3.2 `list_dir` — 列出目录

**参数**

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `path` | string | 否 | `.` | 相对工作目录的目录路径 |
| `depth` | integer | 否 | 1 | 递归深度，1–3 |

**返回示例**

```text
[ok] examples/demo_workspace/（深度 2）
├─ hello.py            14 行
├─ data/
│  ├─ users.json      1.2 KB
│  └─ config.toml     0.4 KB
└─ notes.md           120 行
```

**行为约定**：自动跳过 `.git`、`__pycache__`、`node_modules`、`.venv`、`.changagent`；目录在前、文件在后，按名称排序。

---

### 3.3 `glob_search` — 按文件名查找

**参数**

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `pattern` | string | 是 | — | glob 模式，如 `**/*.py`、`src/*/test_*.py` |
| `path` | string | 否 | `.` | 搜索起点 |

**返回示例**

```text
[ok] 匹配 4 个文件
changagent/cli.py
changagent/config.py
changagent/core/agent.py
tests/test_agent_loop.py
```

**行为约定**：结果上限 200 条，超出时提示收窄模式；不返回目录；glob 语法非法时返回 `PARSE_ERROR`。

---

### 3.4 `grep_search` — 按内容检索

**参数**

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `pattern` | string | 是 | — | 正则表达式 |
| `include` | string | 否 | 全部文本文件 | 文件过滤，如 `*.py` |
| `path` | string | 否 | `.` | 搜索起点 |
| `max_results` | integer | 否 | 50 | 最多返回条数（≤200） |

**返回示例**

```text
[ok] 命中 3 处（显示 3 条）
changagent/core/agent.py:42:  messages = context.maybe_compress(messages, ctx)
changagent/core/context.py:17:  def maybe_compress(messages, ctx):
tests/test_agent_loop.py:9:  def test_maybe_compress_keeps_system():
```

**行为约定**：格式固定为 `文件:行号: 该行内容`；自动跳过二进制与忽略目录；正则非法时返回 `PARSE_ERROR`。

---

### 3.5 `edit_file` — 精确替换（**核心工具**）

**参数**

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `path` | string | 是 | — | 目标文件 |
| `old_string` | string | 是 | — | 要被替换的**原文精确片段**（含缩进） |
| `new_string` | string | 是 | — | 替换后的新片段；传空字符串表示删除 |
| `replace_all` | boolean | 否 | false | 是否替换全部命中 |

**返回示例**

```text
[ok] 已修改 hello.py（1 处替换，第 3–5 行）
  3 -     print("hello")
  3 +     print("你好")
备份：.changagent/backups/20260918-143055/hello.py
```

**行为约定（四条硬规则）**

1. **唯一性**：`old_string` 必须在文件中出现**且仅出现一次**，否则分别返回 `NO_MATCH` / `MULTI_MATCH`；
2. **原文一致**：空白、缩进、换行必须逐字符一致（提示词要求模型从 `read_file` 结果中复制）；
3. **读后写**：目标文件必须已被 `read_file` 读过，否则 `NOT_READ_YET`；
4. **不得空转**：`old_string` 与 `new_string` 完全相同时返回 `PARSE_ERROR`，不做无意义写入与备份。

> 为什么不用"行号替换"？行号会因前一次编辑而漂移，字符串锚定天然幂等、更适合模型生成。

**内部实现要点**

```python
count = content.count(old_string)
if count == 0: raise ToolError("NO_MATCH", ...)
if count > 1 and not replace_all: raise ToolError("MULTI_MATCH", ...)
new_content = content.replace(old_string, new_string)
```

---

### 3.6 `write_file` — 新建 / 覆盖

**参数**

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `path` | string | 是 | — | 目标文件 |
| `content` | string | 是 | — | 完整文件内容 |
| `overwrite` | boolean | 否 | true | 目标已存在时是否允许覆盖 |

**返回示例**

```text
[ok] 已创建 NOTES.md（42 行，1.8 KB）
```

```text
[ok] 已覆盖 NOTES.md（原 30 行 → 现 42 行）
备份：.changagent/backups/20260918-144001/NOTES.md
```

**行为约定**

- 目标存在且 `overwrite=false` → `ALREADY_EXISTS`，提示改用 `edit_file`；
- 覆盖已有文件前**必须**已读过该文件；
- 自动创建缺失的父目录（仅限工作目录内）；
- 覆盖前自动备份。

---

### 3.7 `run_command` — 执行命令（默认关闭）

**参数**

| 参数 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `command` | string | 是 | — | 完整命令 |
| `timeout` | integer | 否 | 60 | 超时秒数（≤300） |

**返回示例**

```text
[ok] 退出码 0（耗时 1.2s）
--- stdout ---
5 passed in 1.20s
--- stderr ---
（空）
```

**四重约束**

1. `CHANG_AGENT_ENABLE_SHELL=true` 才把该工具注册给模型；**未开启时工具不存在**，模型强行调用只会收到 `NOT_FOUND`；
2. 命令首词必须在允许列表内：`python` / `python3` / `py` / `pytest` / `pip` / `node` / `npm` / `npx` / `deno` / `bun` / `git` / `dir` / `type` / `echo` / `where` / `ls` / `cat` / `find` / `findstr` / `ruff` / `black` / `mypy` / `tsc` / `cargo` / `go`；
3. `git` 只放开只读子命令（`status` / `diff` / `log` / `show` / `branch` / `remote` / `rev-parse` / `ls-files`），`push` / `reset --hard` / `clean -f` 一律拒绝；
4. 命中危险黑名单（`rm -rf` / `rmdir /s` / `del /f|/s` / `format X:` / `mkfs` / `diskpart` / `shutdown` / `reg add|delete` / `net user` / `takeown` / `icacls` / `iex` / `Invoke-Expression` / `Start-Process` / 管道执行 `curl`·`wget` / `npm publish` / `npm install -g` / `pip install --user|-g` 等）→ `CMD_BLOCKED`；
5. 命中后仍由写入类审批闸门做第二道确认（见第 4 节）。

**超时与输出**：默认 60s，上限 300s；超时强杀进程树 → `CMD_TIMEOUT`；退出码非 0 → `CMD_FAILED`（输出随结果带回）；stdout / stderr 各截断至 4000 字符。

**工作目录**：始终在 `workspace` 下执行，超时强杀进程树。

---

## 4. 沙箱规则（所有工具共用）

```text
allowed(p)  =  p.resolve() 位于 workspace 之下   （用 is_relative_to，不靠字符串匹配）
              AND p 不在敏感名单
              AND p 不落在忽略目录内
```

| 检查项 | 时机 | 失败结果 |
| --- | --- | --- |
| 路径越界 | 工具执行前 | `PATH_OUT_OF_SANDBOX` |
| 敏感文件 / 忽略目录 | 工具执行前 | `SENSITIVE_FILE` |
| 存在性与类型 | 工具执行前 | `NOT_FOUND` / `NOT_A_FILE` / `NOT_A_DIR` |
| 读后写 | 写入类工具执行前 | `NOT_READ_YET` |
| 人工确认 | 写入类工具落盘前（`requires_approval`） | `APPROVAL_REQUIRED` / `USER_REJECTED` |
| 写前备份 | 真正落盘前 | 备份失败 → `WRITE_FAILED`，放弃写入 |

**敏感名单**（路径任一段命中即拒读写）：

| 类别 | 内容 |
| --- | --- |
| 精确名 | `.env`、`.env.local`、`.gitconfig`、`.netrc`、`credentials` |
| 前缀 | `.env.*`、`id_rsa*`、`id_ed25519*`、`id_dsa*` |
| 后缀 | `*.pem`、`*.key`、`*.pfx`、`*.p12`、`*.keystore` |
| 目录 | `.git/**`、`.changagent/**` |

**忽略目录**（不参与扫描，也不会被工具读写）：`.git`、`.changagent`、`__pycache__`、`.venv`、`venv`、`node_modules`、`.idea`、`.vscode`、`.pytest_cache`、`.ruff_cache`、`dist`、`build`。

### 4.1 审批闸门（写入类工具）

`edit_file` / `write_file` / `run_command` 均带 `requires_approval = true`，落盘前必须过闸：

| 运行通道 | `CHANG_AGENT_AUTO_APPROVE` | 行为 |
| --- | --- | --- |
| CLI / REPL | `false` | 终端弹出确认，用户同意才写；拒绝 → `USER_REJECTED` |
| Web（单向 SSE） | `false` | 没有确认通道 → `APPROVAL_REQUIRED`，**坚决不落盘** |
| 任意通道 | `true` | 跳过确认直接写（靠写前备份兜底） |

### 4.2 写前备份与回滚

1. 任何覆盖 / 修改前，原文件复制到 `.changagent/backups/<YYYYmmdd-HHMMSS>/<相对路径>`；
2. **备份失败即放弃写入**——宁可不改，也不留下无法回滚的改动；
3. 路径同时放在 `ToolResult.meta["backup"]` 与返回文本里，终端与 Web 都会展示；
4. 回滚（PowerShell）：

```powershell
Copy-Item ".changagent\backups\20260918-143055\hello.py" "hello.py" -Force
```

5. 关闭备份：`.env` 设 `CHANG_AGENT_BACKUP=false`（不推荐）；
6. 写回时自动探测并保留原文件换行风格（`\n` / `\r\n`）。

---

## 5. 新增工具的步骤

1. 在 `changagent/tools/` 下新建文件，继承 `Tool`，填写 `name` / `description` / `parameters` / `requires_approval`；
2. 实现 `run(args, ctx) -> ToolResult`，**只读工具不得写盘**；
3. 如需沙箱，调用 `ctx.sandbox.resolve(path)`（不要自己拼路径）；
4. 在 `tools/__init__.py::build_default_registry()` 中挂上 `register(registry)`；
5. 在**本文档第 1 节总览表**与工具详细规格中补一行（文档与代码同步是硬要求）；
6. 若引入了新的错误码，同步更新**第 2.1 节错误码总表**与第 4 节沙箱规则；
7. 在 `tests/test_registry.py` 中加一条 Schema 断言（M4 起生效）。

---

## 6. 提示词与工具的对应关系

| 工具 | 提示词中强调的约束 |
| --- | --- |
| `read_file` | "改之前必须读；不要凭记忆写代码" |
| `edit_file` | "优先小步精确替换，不要整文件重写" |
| `write_file` | "仅用于新建文件或必须整体重写时" |
| `list_dir` / `glob_search` | "不确定路径时先查，不要猜" |
| `grep_search` | "改公共函数前先搜引用" |
| `run_command` | "修改后可跑测试验证，但不要执行破坏性命令" |