"""工具子包：导出工具基类与默认注册表。

新增工具的挂载点就是本文件的 `build_default_registry()`，
步骤见 docs/TOOLS.md 第 5 节（记得同步文档与测试）。
"""

from __future__ import annotations

from .base import Tool, ToolError, ToolResult
from .file_edit import EditFileTool
from .file_read import ReadFileTool
from .file_write import WriteFileTool
from .registry import ToolRegistry
from .search import GlobSearchTool, GrepSearchTool, ListDirTool
from .shell import RunCommandTool

__all__ = [
    "Tool",
    "ToolError",
    "ToolResult",
    "ToolRegistry",
    "ReadFileTool",
    "ListDirTool",
    "GlobSearchTool",
    "GrepSearchTool",
    "EditFileTool",
    "WriteFileTool",
    "RunCommandTool",
    "build_default_registry",
]


def build_default_registry(*, enable_shell: bool = False) -> ToolRegistry:
    """装配默认工具集。

    只读工具永远注册；写入工具需要人工确认（见 core/agent.py 的 approver）；
    run_command 仅在显式开启时注册。
    """
    registry = ToolRegistry()
    registry.add(ReadFileTool())
    registry.add(ListDirTool())
    registry.add(GlobSearchTool())
    registry.add(GrepSearchTool())
    registry.add(EditFileTool())
    registry.add(WriteFileTool())
    if enable_shell:
        registry.add(RunCommandTool())
    return registry
