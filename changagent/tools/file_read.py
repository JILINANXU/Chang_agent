"""读文件工具：read_file（支持 offset/limit 分页）。"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolError, ToolResult, get_int, get_str

MAX_FILE_BYTES = 1024 * 1024  # 超过 1MB 且未指定区间时拒绝直接读
MAX_LIMIT = 2000
DEFAULT_LIMIT = 200


class ReadFileTool(Tool):
    """读取文本文件，带行号输出，便于模型精确复制片段。"""

    name = "read_file"
    description = (
        "读取工作目录内某个文本文件的指定行区间。修改文件前必须先用本工具看过原文。"
        "每行带行号，便于精确引用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作目录的文件路径，例如 src/main.py"},
            "offset": {"type": "integer", "description": "起始行号，从 1 开始，默认 1"},
            "limit": {"type": "integer", "description": "最多读取的行数，默认 200，上限 2000"},
        },
        "required": ["path"],
    }
    requires_approval = False
    read_only = True

    def run(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        raw_path = get_str(args, "path", required=True)
        offset = get_int(args, "offset", 1, minimum=1)
        limit = get_int(args, "limit", DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT)

        target = ctx.sandbox.resolve(raw_path, must_exist=True, expect_file=True)
        rel = ctx.sandbox.relative(target)

        size = target.stat().st_size
        explicit_range = "offset" in args or "limit" in args
        if size > MAX_FILE_BYTES and not explicit_range:
            raise ToolError(
                "FILE_TOO_LARGE",
                f"文件过大（{size / 1024:.0f} KB）：{rel}",
                suggestion="请用 offset / limit 分段读取，例如 offset=1 limit=200、offset=201 limit=200",
            )

        text = ctx.sandbox.read_text(target)
        lines = text.splitlines()
        total = len(lines)

        # 读过即登记：edit_file / write_file 的「读后写」校验依赖这份白名单
        ctx.mark_read(rel)

        if total == 0:
            return ToolResult.success(f"{rel}（空文件，共 0 行）", path=rel, lines=0, offset=1, returned=0)

        if offset > total:
            raise ToolError(
                "NOT_FOUND",
                f"起始行 {offset} 超出文件范围（共 {total} 行）：{rel}",
                suggestion=f"offset 最大可用 {total}，或改用更小的 offset",
            )

        start = offset - 1
        chunk = lines[start:start + limit]
        end = start + len(chunk)

        body = "\n".join(f"{start + index + 1}| {line}" for index, line in enumerate(chunk))
        output = f"{rel}（第 {offset}–{end} 行 / 共 {total} 行）\n{body}"
        if end < total:
            output += f"\n…还有 {total - end} 行，可用 offset={end + 1} 继续读取"

        return ToolResult.success(output, path=rel, lines=total, offset=offset, returned=len(chunk))
