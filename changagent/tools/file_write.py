"""写文件工具：write_file（新建或整体覆盖，写前自动备份）。

与 edit_file 的分工：
- 新建文件、或确需整体重写 → write_file
- 只改几行局部内容 → edit_file（避免摧毁用户没让动的部分）
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolError, ToolResult, get_bool, get_str
from .diff import build_diff, count_changes


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


class WriteFileTool(Tool):
    """新建文件或整体覆盖。"""

    name = "write_file"
    description = "新建文件，或整体覆盖已有文件（内容为完整文件内容）。仅在新建文件或确需整体重写时使用；只改几行请用 edit_file。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目标文件（相对工作目录），父目录不存在会自动创建"},
            "content": {"type": "string", "description": "完整文件内容"},
            "overwrite": {"type": "boolean", "description": "目标已存在时是否允许覆盖，默认 true"},
        },
        "required": ["path", "content"],
    }
    requires_approval = True
    read_only = False

    def run(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        raw_path = get_str(args, "path", required=True)
        raw_content = args.get("content")
        if raw_content is None:
            raise ToolError(
                "PARSE_ERROR",
                "缺少必填参数 content",
                suggestion="请提供完整文件内容；即使是要清空文件，也应传空字符串",
            )
        content = raw_content if isinstance(raw_content, str) else str(raw_content)
        overwrite = get_bool(args, "overwrite", True)

        target = ctx.sandbox.resolve(raw_path, must_exist=False)
        rel = ctx.sandbox.relative(target)

        if target.exists() and target.is_dir():
            raise ToolError(
                "NOT_A_FILE",
                f"目标是一个目录：{rel}",
                suggestion="请提供具体的文件名，例如 dir/notes.md",
            )

        exists = target.exists()

        if exists and not overwrite:
            raise ToolError(
                "ALREADY_EXISTS",
                f"文件已存在且 overwrite=false：{rel}",
                suggestion="若只需改动其中几行，请用 edit_file；若确要整体覆盖，请设置 overwrite=true",
            )

        before = ""
        if exists:
            if not ctx.has_read(rel):
                raise ToolError(
                    "NOT_READ_YET",
                    f"覆盖已有文件前必须先读取：{rel}",
                    suggestion="先用 read_file 查看原文件内容，确认不会丢失需要保留的部分",
                )
            before = ctx.sandbox.read_text(target)

        newline = ctx.sandbox.detect_newline(target) if exists else "\n"
        backup = ctx.sandbox.backup(target)
        ctx.sandbox.write_text(target, content, newline=newline)
        ctx.mark_read(rel)

        lines = len(content.splitlines())

        if exists:
            added, removed = count_changes(before, after=content)
            text = (
                f"已覆盖 {rel}（原 {len(before.splitlines())} 行 → 现 {lines} 行）\n"
                f"变更：+{added} 行 / -{removed} 行"
            )
            if backup:
                text += f"\n备份：{backup}"
            return ToolResult.success(
                text,
                path=rel,
                created=False,
                backup=backup,
                added=added,
                removed=removed,
                diff=build_diff(before, content),
                lines=lines,
            )

        diff = [{"t": "+", "text": line} for line in content.splitlines()]
        text = f"已创建 {rel}（{lines} 行，{_human_size(len(content.encode('utf-8')))})"
        return ToolResult.success(
            text,
            path=rel,
            created=True,
            backup=None,
            added=lines,
            removed=0,
            diff=diff,
            lines=lines,
        )
