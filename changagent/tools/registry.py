"""工具注册表：统一收口工具的注册、Schema 生成与分发执行。

分发时承担四件事（顺序不能变）：
1. 参数不是合法 JSON → 直接回灌，不进工具；
2. 工具名不存在 → 回灌可用清单，避免模型反复猜；
3. 需要人工确认的写操作 → 交给 ctx.approver（无确认通道时明确拒绝）；
4. 执行并捕获一切异常 → 转 ToolResult(ok=False)，绝不炸断主循环。
"""

from __future__ import annotations

import time
import traceback
from typing import Any, Iterable

from ..llm.message import ToolCall
from .base import Tool, ToolError, ToolResult


class ToolRegistry:
    """工具容器。"""

    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    # ------------------------------------------------------------------ #
    # 注册与查询
    # ------------------------------------------------------------------ #

    def add(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("工具必须有 name")
        if tool.name in self._tools:
            raise ValueError(f"工具名重复：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    # ------------------------------------------------------------------ #
    # 供模型与提示词使用
    # ------------------------------------------------------------------ #

    def schemas(self) -> list[dict[str, Any]]:
        """OpenAI function calling 的工具定义列表。"""
        return [tool.schema() for tool in self._tools.values()]

    def describe(self) -> str:
        """一行一个工具的清单，注入系统提示词的 {{TOOL_LIST}}。"""
        lines = [tool.one_line() for tool in self._tools.values()]
        if any(tool.requires_approval for tool in self._tools.values()):
            lines.append("")
            lines.append("> 写入类工具会真实改动磁盘，调用前请确认内容无误。")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 分发执行
    # ------------------------------------------------------------------ #

    def dispatch(self, call: ToolCall, ctx: Any) -> ToolResult:
        started = time.perf_counter()

        def finish(result: ToolResult) -> ToolResult:
            result.meta.setdefault("tool", call.name)
            result.meta["elapsed_ms"] = max(1, int((time.perf_counter() - started) * 1000))
            return result

        # ① 参数不是合法 JSON：让模型按 Schema 重来
        if call.parse_error:
            error = ToolError(
                "PARSE_ERROR",
                "工具参数不是合法 JSON",
                detail=call.parse_error,
                suggestion=(
                    f"请按 {call.name} 的 Schema 重新生成参数"
                    f"（原始内容：{call.raw_arguments[:200]}）"
                ),
            )
            return finish(ToolResult.from_error(error))

        tool = self.get(call.name)

        # ② 工具名不存在：回灌可用清单
        if tool is None:
            available = "、".join(self._tools) or "（当前没有可用工具）"
            error = ToolError(
                "NOT_FOUND",
                f"工具 {call.name} 不存在",
                suggestion=f"可用工具：{available}",
            )
            return finish(ToolResult.from_error(error))

        # ③ 需要人工确认的写操作
        if tool.requires_approval and not getattr(ctx, "auto_approve", False):
            approver = getattr(ctx, "approver", None)
            if approver is None:
                error = ToolError(
                    "APPROVAL_REQUIRED",
                    f"{call.name} 需要人工确认，但当前通道没有确认能力",
                    detail="当前运行通道（如 Web 界面）是单向事件流，无法暂停等待确认",
                    suggestion=(
                        "若确认信任本次改动，请在 .env 中设置 CHANG_AGENT_AUTO_APPROVE=true；"
                        "写入前会自动备份，可从备份回滚"
                    ),
                )
                return finish(ToolResult.from_error(error))
            if not approver(tool, call):
                return finish(
                    ToolResult.failure(
                        "[error] USER_REJECTED：用户拒绝了本次改动\n"
                        "建议：停止修改该文件，向用户说明情况并询问期望做法",
                        rejected=True,
                    )
                )

        # ④ 真正执行：所有异常统一转成失败结果
        try:
            result = tool.run(call.arguments or {}, ctx)
        except ToolError as error:
            return finish(ToolResult.from_error(error))
        except Exception as exc:  # noqa: BLE001 - 兜底，保证主循环不被炸断
            return finish(
                ToolResult.failure(
                    f"[error] TOOL_CRASH：{call.name} 执行时发生异常：{exc}\n"
                    "建议：换一种方式完成目标，或如实向用户说明该操作失败",
                    traceback=traceback.format_exc()[-800:],
                )
            )

        if not isinstance(result, ToolResult):
            return finish(
                ToolResult.failure(f"[error] TOOL_CRASH：{call.name} 未返回 ToolResult")
            )
        return finish(result)
