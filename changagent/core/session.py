"""会话持久化：把每一步工具调用与最终结果落盘。

落盘位置（见 docs/DESIGN.md 第 8 节）::

    <workspace>/.changagent/
    ├─ sessions/20260918-143012-a1b2.json   完整对话与工具调用记录
    ├─ backups/20260918-143055/hello.py     写入前的原始内容（由 sandbox 负责）
    └─ state.json                            最近会话指针

落盘失败不抛异常：记录会话是"锦上添花"，不能因此中断用户任务。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..llm.message import ToolCall
from ..tools.base import ToolResult


def _summarize(result: ToolResult) -> str:
    """一句话摘要，供会话文件与终端展示。"""
    meta = result.meta or {}

    if not result.ok:
        first_line = (result.content or "执行失败").splitlines()[0]
        return first_line[:80]

    tool = meta.get("tool", "")
    if tool == "read_file":
        return f"读取 {meta.get('lines', 0)} 行"
    if tool in ("edit_file", "write_file"):
        action = "创建" if meta.get("created") else "修改"
        return f"{action}，+{meta.get('added', 0)}/-{meta.get('removed', 0)} 行"
    if tool == "grep_search":
        return f"命中 {meta.get('count', 0)} 处"
    if tool == "glob_search":
        return f"匹配 {meta.get('count', 0)} 个文件"
    if tool == "list_dir":
        return "列出目录"

    first_line = (result.content or "完成").splitlines()[0]
    return first_line[:80]


@dataclass
class SessionRecorder:
    """会话记录器。"""

    workspace: Path
    task: str
    model: str
    session_id: str = ""
    started_at: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    saved_path: Path | None = None

    def __post_init__(self) -> None:
        now = datetime.now()
        self.started_at = now.isoformat(timespec="seconds")
        self.session_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"

    # ------------------------------------------------------------------ #

    def record(self, step: int, call: ToolCall, result: ToolResult) -> None:
        """记录一次工具调用。"""
        meta = result.meta or {}
        entry: dict[str, Any] = {
            "step": step,
            "tool": call.name,
            "args": call.arguments,
            "ok": result.ok,
            "elapsed_ms": meta.get("elapsed_ms", 0),
            "summary": _summarize(result),
        }
        for key in ("path", "backup", "added", "removed"):
            if key in meta:
                entry[key] = meta[key]
        if not result.ok and result.error:
            entry["error"] = result.error.splitlines()[0][:200]
        self.steps.append(entry)

    # ------------------------------------------------------------------ #

    def finish(self, *, ok: bool, answer: str, steps: int, usage: dict[str, int],
               error: str = "") -> Path | None:
        """写入会话文件并更新 state.json。"""
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "task": self.task,
            "workspace": str(self.workspace),
            "model": self.model,
            "started_at": self.started_at,
            "steps": self.steps,
            "result": {"ok": ok, "answer": answer, "steps": steps, "error": error},
            "usage": usage,
        }
        return self._save(payload)

    def _save(self, payload: dict[str, Any]) -> Path | None:
        directory = self.workspace / ".changagent" / "sessions"
        path = directory / f"{self.session_id}.json"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            return None

        self.saved_path = path
        self._update_state(payload)
        return path

    def _update_state(self, payload: dict[str, Any]) -> None:
        """维护 state.json：最近会话指针。"""
        state_path = self.workspace / ".changagent" / "state.json"
        try:
            state = {
                "last_session_id": self.session_id,
                "last_task": self.task,
                "last_ok": payload.get("result", {}).get("ok", False),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
