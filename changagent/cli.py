"""命令行交互层：参数解析、单次任务模式、REPL 循环。

用法::

    python -m changagent "在 hello.py 中新增 greet 函数并调用它"
    python -m changagent                      # 进入 REPL（保留多轮上下文）
    python -m changagent -w ./examples/demo_workspace "把 README 补上用法说明"
    python -m changagent --task-file task.txt
    python -m changagent -y "..."             # 跳过写前人工确认（等价 .env 的 AUTO_APPROVE）

写入类工具默认逐次询问；确认后才会落盘，且落盘前自动备份。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config
from .core.agent import RunContext, run_stream
from .llm.client import LLMError
from .llm.message import Message, ToolCall
from .prompts import loader
from .tools import build_default_registry
from .tools.base import Tool
from .ui.render import EventRenderer

BANNER = "畅Agent · 本地编码智能体（命令行）"


# --------------------------------------------------------------------------- #
# 写前确认
# --------------------------------------------------------------------------- #

def _preview_call(tool: Tool, call: ToolCall) -> str:
    """把写操作的参数渲染成可读预览，供人工确认。"""
    args = call.arguments or {}
    path = args.get("path", "?")
    lines = [f"  工具：{tool.name}", f"  目标：{path}"]

    if tool.name == "edit_file":
        old = str(args.get("old_string", ""))
        new = str(args.get("new_string", ""))
        lines.append("  ── 将被替换的内容 ──")
        lines.extend(f"  - {line}" for line in old.splitlines()[:15])
        lines.append("  ── 替换为 ──")
        lines.extend(f"  + {line}" for line in new.splitlines()[:15])
    elif tool.name == "write_file":
        content = str(args.get("content", ""))
        total = len(content.splitlines())
        lines.append(f"  将写入 {total} 行内容（整体覆盖）")
        lines.extend(f"    {line}" for line in content.splitlines()[:10])
        if total > 10:
            lines.append(f"    …（其余 {total - 10} 行）")
    elif tool.name == "run_command":
        lines.append(f"  命令：{args.get('command', '')}")

    return "\n".join(lines)


def make_approver(enabled: bool):
    """构造人工确认回调；enabled=False 时返回 None（走 AUTO_APPROVE 分支）。"""
    if not enabled:
        return None

    def approver(tool: Tool, call: ToolCall) -> bool:
        print()
        print("需要确认的写入操作：")
        print(_preview_call(tool, call))
        try:
            answer = input("  允许本次修改？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        return answer in {"y", "yes", "是", "1"}

    return approver


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #

def run_once(task: str, config: AppConfig, *, auto_approve: bool, color: bool | None,
             history: list[Message] | None = None) -> int:
    """跑一个任务，返回进程退出码。"""
    renderer = EventRenderer(color=color)
    approver = make_approver(not auto_approve)

    ok = False
    try:
        stream = run_stream(
            task,
            config,
            approver=approver,
            messages=history,
        )
        for event in stream:
            renderer.render(event)
            if event.get("type") == "done":
                ok = bool(event.get("ok"))
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except LLMError as exc:
        print(f"\n模型调用失败：{exc}", file=sys.stderr)
        return 2

    return 0 if ok else 1


def repl(config: AppConfig, *, auto_approve: bool, color: bool | None) -> int:
    """交互式循环，历史消息跨轮保留。"""
    registry = build_default_registry(enable_shell=config.enable_shell)
    history: list[Message] = []

    print(BANNER)
    print(f"模型：{config.llm.describe()}")
    print(f"工作区：{config.workspace}")
    print("输入任务后回车执行；输入 exit / quit 退出。")
    print()

    while True:
        try:
            task = input("畅Agent> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not task:
            continue
        if task.lower() in {"exit", "quit", ":q"}:
            return 0

        if not history:
            history.extend(loader.build_messages(config, registry, task))
        else:
            history.append(Message(role="user", content=task))

        run_once(task, config, auto_approve=auto_approve, color=color, history=history)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="changagent",
        description="畅Agent · 本地编码智能体（命令行）",
    )
    parser.add_argument("task", nargs="?", help="要执行的任务描述；不传则进入交互模式")
    parser.add_argument("-w", "--workspace", help="工作区目录（沙箱根），默认读 .env")
    parser.add_argument("--task-file", help="从文件读取任务描述")
    parser.add_argument("-y", "--yes", action="store_true", help="跳过写前人工确认")
    parser.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    parser.add_argument("--max-steps", type=int, help="覆盖单任务最大步数")
    parser.add_argument("--check", action="store_true", help="只打印配置与工具清单，不调用模型")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = load_config(args.workspace)
    if args.max_steps:
        config.max_steps = max(1, args.max_steps)
    config.ensure_dirs()

    if args.check:
        registry = build_default_registry(enable_shell=config.enable_shell)
        print(BANNER)
        print(f"模型：{config.llm.describe()}")
        print(f"工作区：{config.workspace}（存在：{config.workspace.is_dir()}）")
        print(f"最大步数：{config.max_steps}　上下文预算：{config.token_budget}")
        print(f"写前备份：{'开' if config.backup else '关'}　人工确认：{'关（自动放行）' if config.auto_approve else '开'}")
        print(f"命令工具：{'启用' if config.enable_shell else '禁用'}")
        print()
        print("可用工具：")
        for tool in registry.tools():
            flag = "写入" if tool.requires_approval else "只读"
            print(f"  - {tool.name:<14}{flag}  {tool.description.split('。')[0]}。")
        return 0

    task = args.task
    if args.task_file:
        try:
            task = Path(args.task_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"读取任务文件失败：{exc}", file=sys.stderr)
            return 2

    auto_approve = args.yes or config.auto_approve
    color = False if args.no_color else None

    if not config.llm.configured:
        print("警告：未配置 CHANG_AGENT_API_KEY，无法调用模型。", file=sys.stderr)
        print("请在项目根目录的 .env 中填写后重试（可参考 .env.example）。", file=sys.stderr)
        print("提示：只想检查配置可用 python -m changagent --check", file=sys.stderr)
        return 2

    if task:
        return run_once(task, config, auto_approve=auto_approve, color=color)
    return repl(config, auto_approve=auto_approve, color=color)
