"""检索工具：list_dir / glob_search / grep_search。

三个工具都只读，永不写盘，且结果一律过滤沙箱敏感路径与忽略目录。
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from .base import Tool, ToolError, ToolResult, get_int, get_str

NAME_WIDTH = 22
GLOB_LIMIT = 200
GREP_LIMIT = 200
TEXT_FILE_BYTES = 1024 * 1024


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _text_line_count(path: Path) -> int | None:
    """文本文件返回行数，二进制或读取失败返回 None。"""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:2048]:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return len(text.splitlines())


def _entry_info(path: Path) -> str:
    lines = _text_line_count(path)
    if lines is not None:
        return f"{lines} 行"
    try:
        return _human_size(path.stat().st_size)
    except OSError:
        return ""


def _is_visible_file(sandbox: Any, path: Path) -> bool:
    rel = sandbox.relative(path)
    if sandbox.is_sensitive(rel) or sandbox.is_ignored(rel):
        return False
    return path.is_file()


# --------------------------------------------------------------------------- #
# list_dir
# --------------------------------------------------------------------------- #

class ListDirTool(Tool):
    """列出工作区内目录结构。"""

    name = "list_dir"
    description = "列出工作目录（或其中某个子目录）的结构，含文件大小或行数。不确定路径时先用本工具确认，不要猜。"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作目录的目录路径，默认 .（工作目录根）"},
            "depth": {"type": "integer", "description": "递归深度，1–3，默认 1"},
        },
        "required": [],
    }
    requires_approval = False
    read_only = True

    def run(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        raw_path = get_str(args, "path", default=".") or "."
        depth = get_int(args, "depth", 1, minimum=1, maximum=3)

        base = ctx.sandbox.resolve(raw_path, must_exist=True, expect_dir=True)
        rel = ctx.sandbox.relative(base)
        display = f"{rel}/" if rel != "." else f"{base.name}/"

        lines: list[str] = []
        self._walk(ctx.sandbox, base, depth, 1, lines, "")

        if not lines:
            lines.append("（空目录）")

        header = f"{display}（深度 {depth}）"
        return ToolResult.success("\n".join([header] + lines), path=rel, depth=depth)

    def _walk(self, sandbox: Any, directory: Path, max_depth: int, depth: int,
              lines: list[str], prefix: str) -> None:
        entries = sandbox.visible_entries(directory)
        for index, entry in enumerate(entries):
            last = index == len(entries) - 1
            branch = "└─ " if last else "├─ "
            child_prefix = prefix + ("   " if last else "│  ")

            if entry.is_dir():
                lines.append(f"{prefix}{branch}{entry.name}/")
                if depth < max_depth:
                    self._walk(sandbox, entry, max_depth, depth + 1, lines, child_prefix)
            else:
                info = _entry_info(entry)
                lines.append(f"{prefix}{branch}{entry.name.ljust(NAME_WIDTH)}{info}")


# --------------------------------------------------------------------------- #
# glob_search
# --------------------------------------------------------------------------- #

class GlobSearchTool(Tool):
    """按文件名模式查找文件。"""

    name = "glob_search"
    description = "按文件名模式查找工作目录内的文件，例如 **/*.py 或 src/*/test_*.py。知道文件名但不知道位置时使用。"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，例如 **/*.py"},
            "path": {"type": "string", "description": "搜索起点（相对工作目录），默认 ."},
        },
        "required": ["pattern"],
    }
    requires_approval = False
    read_only = True

    def run(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        pattern = get_str(args, "pattern", required=True)
        raw_path = get_str(args, "path", default=".") or "."

        base = ctx.sandbox.resolve(raw_path, must_exist=True, expect_dir=True)
        workspace = ctx.sandbox.workspace

        found: list[str] = []
        truncated = False
        try:
            candidates = base.glob(pattern)
        except (ValueError, OSError) as exc:
            raise ToolError(
                "PARSE_ERROR",
                f"glob 模式无法解析：{pattern}",
                detail=str(exc),
                suggestion="请使用标准 glob 语法，例如 **/*.py、src/*.md",
            ) from exc

        for item in candidates:
            try:
                resolved = item.resolve()
            except OSError:
                continue
            # 双保险：glob 可能通过 .. 越出工作区，这里再过滤一次
            if not resolved.is_relative_to(workspace):
                continue
            if not _is_visible_file(ctx.sandbox, item):
                continue
            found.append(ctx.sandbox.relative(item))
            if len(found) >= GLOB_LIMIT:
                truncated = True
                break

        if not found:
            return ToolResult.success(
                f"没有匹配 {pattern} 的文件（搜索起点：{ctx.sandbox.relative(base)}）",
                path=ctx.sandbox.relative(base), count=0,
            )

        found.sort()
        body = [f"匹配 {len(found)} 个文件" + ("（已达上限，请收窄模式）" if truncated else "")]
        body.extend(found)
        return ToolResult.success("\n".join(body), count=len(found), truncated=truncated)


# --------------------------------------------------------------------------- #
# grep_search
# --------------------------------------------------------------------------- #

class GrepSearchTool(Tool):
    """按内容正则检索。"""

    name = "grep_search"
    description = "按正则表达式检索文件内容，返回 `文件:行号: 内容`。想知道某个函数、变量或字符串在哪里被使用时使用。"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式，特殊字符需转义"},
            "include": {"type": "string", "description": "文件名过滤，例如 *.py；不传则搜索全部文本文件"},
            "path": {"type": "string", "description": "搜索起点（相对工作目录），默认 ."},
            "max_results": {"type": "integer", "description": "最多返回条数，默认 50，上限 200"},
        },
        "required": ["pattern"],
    }
    requires_approval = False
    read_only = True

    def run(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        pattern = get_str(args, "pattern", required=True)
        include = get_str(args, "include", default="").strip()
        raw_path = get_str(args, "path", default=".") or "."
        max_results = get_int(args, "max_results", 50, minimum=1, maximum=GREP_LIMIT)

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(
                "PARSE_ERROR",
                f"正则表达式非法：{pattern}",
                detail=str(exc),
                suggestion="请修正正则语法，特殊字符（如 ( ) [ ] . * + ?）需要转义",
            ) from exc

        base = ctx.sandbox.resolve(raw_path, must_exist=True, expect_dir=True)

        hits: list[str] = []
        scanned = 0
        for item in self._iter_files(base):
            if not _is_visible_file(ctx.sandbox, item):
                continue
            if include and not fnmatch.fnmatch(item.name, include):
                continue
            try:
                if item.stat().st_size > TEXT_FILE_BYTES:
                    continue
                raw = item.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:2048]:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue

            scanned += 1
            rel = ctx.sandbox.relative(item)
            for index, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{rel}:{index}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        break
            if len(hits) >= max_results:
                break

        if not hits:
            return ToolResult.success(
                f"没有命中 {pattern}（已扫描 {scanned} 个文本文件）",
                count=0, scanned=scanned,
            )

        body = [f"命中 {len(hits)} 处" + ("（已达上限，请收窄范围）" if len(hits) >= max_results else "")]
        body.extend(hits)
        return ToolResult.success("\n".join(body), count=len(hits), scanned=scanned)

    @staticmethod
    def _iter_files(base: Path):
        """广度优先遍历，跳过沙箱忽略目录。"""
        stack = [base]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir():
                        stack.append(entry)
                    else:
                        yield entry
                except OSError:
                    continue
