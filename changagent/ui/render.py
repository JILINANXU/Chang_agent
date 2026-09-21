"""终端渲染：思考过程、工具调用卡片、diff 预览、最终答复。

只做一件事：把 `core.agent.run_stream()` 产出的事件画成人能看的东西。
事件字段见 docs/WEB.md 4.1，与 Web 界面完全同源，因此改渲染不影响内核。
"""

from __future__ import annotations

import os
import sys
from typing import Any, TextIO

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"

RESULT_PREVIEW_LINES = 24


def _default_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CHANG_AGENT_COLOR", "").lower() in {"0", "false", "no"}:
        return False
    try:
        return sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


class EventRenderer:
    """把事件流转成终端输出。"""

    def __init__(self, stream: TextIO | None = None, *, color: bool | None = None,
                 result_lines: int = RESULT_PREVIEW_LINES) -> None:
        self.stream = stream or sys.stdout
        self.color = _default_color() if color is None else color
        self.result_lines = result_lines

    # ------------------------------------------------------------------ #

    def _paint(self, text: str, color: str) -> str:
        return f"{color}{text}{RESET}" if self.color else text

    def _write(self, text: str = "") -> None:
        print(text, file=self.stream, flush=True)

    # ------------------------------------------------------------------ #

    def render(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            return
        handler(event)

    # ------------------------------------------------------------------ #
    # 各事件
    # ------------------------------------------------------------------ #

    def _on_agent_start(self, event: dict[str, Any]) -> None:
        self._write()
        self._write(self._paint("─" * 60, DIM))
        self._write(self._paint("畅Agent", BOLD) + self._paint(f"  模型 {event.get('model', '-')}", DIM))
        self._write(self._paint(f"任务 {event.get('task', '')}", CYAN))
        self._write(self._paint(f"工作区 {event.get('workspace', '')}", DIM))
        self._write(self._paint("─" * 60, DIM))

    def _on_thinking(self, event: dict[str, Any]) -> None:
        text = (event.get("text") or "").strip()
        if not text:
            return
        self._write()
        self._write(self._paint("思考", BLUE))
        for line in text.splitlines():
            self._write(self._paint(f"  {line}", DIM))

    def _on_tool_call(self, event: dict[str, Any]) -> None:
        name = event.get("name", "")
        args = event.get("args") or {}
        self._write()
        self._write(f"{self._paint('▶', YELLOW)} {self._paint(name, BOLD)} {self._format_args(args)}")

    def _on_tool_result(self, event: dict[str, Any]) -> None:
        ok = event.get("ok", False)
        elapsed = event.get("elapsed_ms", 0)
        tag = self._paint("完成", GREEN) if ok else self._paint("失败", RED)
        self._write(f"  {tag}{self._paint(f'  {elapsed}ms', DIM)}")

        content = event.get("content") or ""
        lines = content.splitlines()
        limit = max(1, self.result_lines)
        for line in lines[:limit]:
            self._write(self._paint(f"  │ {line}", GREEN if ok else RED))
        if len(lines) > limit:
            self._write(self._paint(f"  │ …（其余 {len(lines) - limit} 行已省略）", DIM))

    def _on_file_change(self, event: dict[str, Any]) -> None:
        path = event.get("path", "")
        added = event.get("added", 0)
        removed = event.get("removed", 0)
        self._write()
        self._write(
            f"{self._paint('✎', CYAN)} {self._paint(path, BOLD)} "
            f"{self._paint(f'+{added}', GREEN)} {self._paint(f'-{removed}', RED)}"
        )
        for line in (event.get("diff") or [])[:40]:
            mark = {"+": "+", "-": "-"}.get(line.get("t", ""), " ")
            color = {"+": GREEN, "-": RED}.get(line.get("t", ""), DIM)
            self._write(self._paint(f"  {mark} {line.get('text', '')}", color))

        backup = event.get("backup")
        if backup:
            self._write(self._paint(f"  备份 {backup}", DIM))

    def _on_answer(self, event: dict[str, Any]) -> None:
        self._write()
        self._write(self._paint("完成", BOLD + GREEN))
        for line in (event.get("text") or "").splitlines():
            self._write(f"  {line}" if line else "")

    def _on_error(self, event: dict[str, Any]) -> None:
        self._write()
        self._write(self._paint("出错", BOLD + RED))
        for line in (event.get("text") or "").splitlines():
            self._write(self._paint(f"  {line}", RED))

    def _on_done(self, event: dict[str, Any]) -> None:
        usage = event.get("usage") or {}
        parts = [
            f"步数 {event.get('steps', 0)}",
            f"耗时 {event.get('elapsed_ms', 0)}ms",
            f"tokens {usage.get('prompt_tokens', 0)}+{usage.get('completion_tokens', 0)}",
        ]
        self._write()
        self._write(self._paint(" · ".join(parts), DIM))
        self._write(self._paint("─" * 60, DIM))
        self._write()

    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_args(args: dict[str, Any]) -> str:
        if not args:
            return ""
        parts = []
        for key, value in args.items():
            text = str(value).replace("\n", "\\n")
            if len(text) > 60:
                text = text[:57] + "…"
            parts.append(f"{key}={text}")
        return " ".join(parts)
