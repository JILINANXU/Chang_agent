"""行级 diff 与变更行数统计。

纯函数模块，供写类工具生成 `file_change` 事件所需的 diff（见 docs/WEB.md 4.2）。
输出结构固定为 `{"t": "ctx"|"+"|"-", "text": "..."}`，前端据此着色。
"""

from __future__ import annotations

import difflib
from typing import Any

DIFF_CONTEXT = 3


def build_diff(before: str, after: str, context: int = DIFF_CONTEXT) -> list[dict[str, Any]]:
    """生成紧凑 diff：只输出变更区域及其上下文，区域之间以省略行分隔。"""
    old_lines = before.splitlines()
    new_lines = after.splitlines()

    if old_lines == new_lines:
        return []

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    out: list[dict[str, Any]] = []

    for group in matcher.get_grouped_opcodes(context):
        if out:
            out.append({"t": "ctx", "text": "…"})
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for line in old_lines[i1:i2]:
                    out.append({"t": "ctx", "text": line})
                continue
            for line in old_lines[i1:i2]:
                out.append({"t": "-", "text": line})
            for line in new_lines[j1:j2]:
                out.append({"t": "+", "text": line})

    return out


def count_changes(before: str, after: str) -> tuple[int, int]:
    """返回 (新增行数, 删除行数)。"""
    added = removed = 0
    matcher = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed
