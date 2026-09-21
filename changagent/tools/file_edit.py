"""改文件工具：edit_file（精确字符串替换，核心工具）。

三条硬规则（见 docs/TOOLS.md 3.5）：
1. 唯一性：old_string 必须命中且仅命中一次（除非显式 replace_all）；
2. 原文一致：空白、缩进、换行逐字符一致；
3. 读后写：目标文件必须已被 read_file 读过。
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolError, ToolResult, get_bool, get_str
from .diff import build_diff, count_changes


class EditFileTool(Tool):
    """精确替换已有文件中的片段。"""

    name = "edit_file"
    description = "对已有文件做精确字符串替换。old_string 必须从 read_file 的结果中逐字符复制（不要带行号前缀），且必须在文件中唯一命中。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目标文件（相对工作目录）"},
            "old_string": {
                "type": "string",
                "description": "要被替换的原文精确片段，含缩进与空行；必须与文件内容逐字符一致且唯一",
            },
            "new_string": {"type": "string", "description": "替换后的新片段；传空字符串表示删除该片段"},
            "replace_all": {"type": "boolean", "description": "是否替换全部命中，默认 false"},
        },
        "required": ["path", "old_string", "new_string"],
    }
    requires_approval = True
    read_only = False

    def run(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        raw_path = get_str(args, "path", required=True)
        old_string = get_str(args, "old_string", required=True)
        new_value = args.get("new_string")
        if new_value is None:
            raise ToolError(
                "PARSE_ERROR",
                "缺少必填参数 new_string",
                suggestion="要删除片段时请传空字符串 \"\"，不要省略该参数",
            )
        new_string = new_value if isinstance(new_value, str) else str(new_value)
        replace_all = get_bool(args, "replace_all", False)

        target = ctx.sandbox.resolve(raw_path, must_exist=True, expect_file=True)
        rel = ctx.sandbox.relative(target)

        if not ctx.has_read(rel):
            raise ToolError(
                "NOT_READ_YET",
                f"修改前必须先读取文件：{rel}",
                suggestion="先用 read_file 查看原文，再从中复制精确片段作为 old_string",
            )

        if old_string == new_string:
            raise ToolError(
                "PARSE_ERROR",
                "old_string 与 new_string 完全相同，无需修改",
                suggestion="请确认期望的改动内容后重新发起调用",
            )

        before = ctx.sandbox.read_text(target)
        occurrences = before.count(old_string)

        if occurrences == 0:
            raise ToolError(
                "NO_MATCH",
                f"old_string 在 {rel} 中未找到",
                detail="可能原文已被改动，或缩进 / 空行 / 换行与文件不一致",
                suggestion="重新用 read_file 读取原文，逐字符复制目标片段后再试；注意不要带 `行号|` 前缀",
            )

        if occurrences > 1 and not replace_all:
            raise ToolError(
                "MULTI_MATCH",
                f"old_string 在 {rel} 中命中 {occurrences} 处，无法确定改哪一处",
                suggestion="在 old_string 中多带几行上下文使其唯一；若确实要全部替换，请设置 replace_all=true",
            )

        after = before.replace(old_string, new_string)
        newline = ctx.sandbox.detect_newline(target)
        backup = ctx.sandbox.backup(target)
        ctx.sandbox.write_text(target, after, newline=newline)
        ctx.mark_read(rel)

        added, removed = count_changes(before, after)
        first_index = before.index(old_string)
        start_line = before[:first_index].count("\n") + 1
        end_line = start_line + old_string.count("\n")

        if occurrences == 1:
            summary = f"已修改 {rel}（1 处替换，第 {start_line}–{end_line} 行）"
        else:
            summary = f"已修改 {rel}（{occurrences} 处替换，首个位于第 {start_line} 行）"

        text = f"{summary}\n变更：+{added} 行 / -{removed} 行"
        if backup:
            text += f"\n备份：{backup}"

        return ToolResult.success(
            text,
            path=rel,
            backup=backup,
            added=added,
            removed=removed,
            diff=build_diff(before, after),
            lines=len(after.splitlines()),
            replacements=occurrences,
        )
