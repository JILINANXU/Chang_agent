"""提示词装配：把 system.md / tool_guide.md 渲染成最终系统提示词。

分层与模板变量见 docs/PROMPTS.md：
- L1/L2/L5 来自 system.md 固定段落；
- L3 由 tool_guide.md + 注册表清单注入；
- L4 的占位符写在 system.md 第四节，取值由本模块在运行时注入（工作目录 / 系统 / 日期 / 文件树 / 参数）。
"""

from __future__ import annotations

import os
import platform
import re
import sys
from datetime import date
from pathlib import Path

from ..llm.message import Message

PROMPTS_DIR = Path(__file__).resolve().parent
SYSTEM_TEMPLATE = PROMPTS_DIR / "system.md"
TOOL_GUIDE_FILE = PROMPTS_DIR / "tool_guide.md"

FILE_TREE_LIMIT = 200
_VAR_PATTERN = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")
# 形似变量的写法（内层是标识符，允许多余空白）：用于抓“写错但不会被替换”的占位符
_IDENT_SHAPED = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
# 会被 _VAR_PATTERN 命中的变量名形状；不满足者才可能被静默留在提示词里
_REPLACEABLE_KEY = re.compile(r"[A-Z_][A-Z0-9_]*")


# --------------------------------------------------------------------------- #
# 模板读取
# --------------------------------------------------------------------------- #

def strip_front_matter(text: str) -> str:
    """剥离文档头部 YAML front matter（AIGC 标注等），避免污染提示词。"""
    if text.lstrip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return text


def load_system_template() -> str:
    return strip_front_matter(SYSTEM_TEMPLATE.read_text(encoding="utf-8"))


def load_tool_guide() -> str:
    return strip_front_matter(TOOL_GUIDE_FILE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 文件树
# --------------------------------------------------------------------------- #

def build_file_tree(workspace: Path, max_lines: int = FILE_TREE_LIMIT) -> str:
    """生成两级文件树；自动跳过依赖目录与敏感文件，超限则截断。"""
    from ..core.sandbox import Sandbox  # 延迟导入，避免子包初始化顺序问题

    sandbox = Sandbox(workspace, backup=False)
    if not sandbox.workspace.is_dir():
        return "（工作目录不存在，请在回复中提示用户确认工作区设置）"

    lines: list[str] = []

    def walk(directory: Path, depth: int, prefix: str) -> None:
        if depth > 2 or len(lines) >= max_lines:
            return
        entries = sandbox.visible_entries(directory)
        for index, entry in enumerate(entries):
            if len(lines) >= max_lines:
                return
            last = index == len(entries) - 1
            branch = "└─ " if last else "├─ "
            if entry.is_dir():
                lines.append(f"{prefix}{branch}{entry.name}/")
                walk(entry, depth + 1, prefix + ("   " if last else "│  "))
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                size_text = f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"
                lines.append(f"{prefix}{branch}{entry.name}  {size_text}")

    walk(sandbox.workspace, 1, "")

    if not lines:
        return "（空目录）"
    if len(lines) >= max_lines:
        lines.append("…（更多文件请用 list_dir 工具查看）")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #

def render(template: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """替换 {{VAR}}，返回 (渲染结果, 缺失变量列表)。缺失变量替换为空串，不抛异常。"""
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in values:
            return values[key]
        missing.append(key)
        return ""

    return _VAR_PATTERN.sub(replace, template), missing


def find_unresolved(template: str, values: dict[str, str]) -> list[str]:
    """返回模板中「不会被替换」的占位符——即 render() 会静默留下、且不产生缺失告警的那些。

    只认变量形状（`{{名字}}` 内是标识符），因此正文里用于说明的 `{{...}}`、
    `{{ }}` 这类写法不会被误报。判定分两种情况：

    - 全大写标识符（如 `{{CWDD}}`）会被 render() 匹配并替换为空串，同时计入
      「未定义变量」告警，故此处不重复报告；
    - 小写 / 混合大小写（如 `{{cwd}}`）、以及多带空白（如 `{{CWD }}`）的写法
      不会被替换，正是本函数要抓的。

    返回可读的问题描述列表，空列表代表无问题。只负责报告，**不抛异常**，
    保证 Agent 永远能启动。
    """
    problems: list[str] = []
    for match in _IDENT_SHAPED.finditer(template):
        raw, key = match.group(0), match.group(1)
        if not _REPLACEABLE_KEY.fullmatch(key):
            problems.append(f"{raw}（未定义：检查拼写与大小写）")
            continue
        expected = "{{" + key + "}}"
        if key in values and raw != expected:
            problems.append(f"{raw}（含多余空白，不会被替换，应写成 {expected}）")
    return problems


def environment_description() -> str:
    """操作系统描述，注入 {{OS}}。"""
    system = platform.system() or os.name
    release = platform.release()
    shell = "PowerShell" if os.name == "nt" else "bash"
    return f"{system} {release} ({shell}, Python {sys.version.split()[0]})"


def build_system_prompt(config, registry) -> str:
    """装配系统提示词（会话开始时调用一次，中途不重装）。"""
    values = {
        "CWD": str(config.workspace),
        "OS": environment_description(),
        "DATE": date.today().isoformat(),
        "FILE_TREE": build_file_tree(config.workspace),
        "TOOL_LIST": registry.describe(),
        "TOOL_GUIDE": load_tool_guide(),
        "MAX_STEPS": str(config.max_steps),
        "SHELL_ENABLED": "是" if config.enable_shell else "否",
    }
    template = load_system_template()
    text, missing = render(template, values)
    if missing:
        # 模板变量写错不应让 Agent 起不来，记录一下便于排查
        print(f"[警告] 系统提示词存在未定义变量：{'、'.join(sorted(set(missing)))}", file=sys.stderr)
    for problem in find_unresolved(template, values):
        # 同样只告警：把“写了但不会被替换”的占位符暴露出来，避免静默失效
        print(f"[警告] 系统提示词存在不会被替换的占位符：{problem}", file=sys.stderr)
    return text


def build_messages(config, registry, task: str) -> list[Message]:
    """构造初始消息：system（装配一次）+ user（本次任务）。"""
    return [
        Message(role="system", content=build_system_prompt(config, registry)),
        Message(role="user", content=task),
    ]
