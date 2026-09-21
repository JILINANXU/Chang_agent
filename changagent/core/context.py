"""上下文管理：token 估算与超预算压缩。

估算方式（保守、无 tokenizer 依赖）：`tokens ≈ 汉字数 × 1.0 + 其他字符数 / 4`。
触发线：估算值 > 预算 × 0.7 时启动压缩，策略按代价从低到高（见 docs/DESIGN.md 第 6 节）：
1. 截断旧工具结果（保留前 30 行 + 后 20 行）；
2. 折叠旧轮次为一行摘要；
3. system 首条、原始用户需求、最近若干轮永不压缩。
"""

from __future__ import annotations

import json
from typing import Any

from ..llm.message import Message, ToolCall

COMPRESS_TRIGGER = 0.7   # 超过预算的该比例即压缩
KEEP_RECENT = 8          # 尾部保留的消息条数（约 4 轮）
HEAD_KEEP = 2            # system + 首个 user 需求，永久保留
TRUNCATE_HEAD = 30       # 截断时保留的前部行数
TRUNCATE_TAIL = 20       # 截断时保留的后部行数

_CJK_START = "\u3000"
_CJK_END = "\u9fff"


def estimate_tokens(text: str) -> int:
    """粗估一段文本的 token 数。"""
    if not text:
        return 0
    cjk = sum(1 for char in text if _CJK_START <= char <= _CJK_END)
    other = len(text) - cjk
    return int(cjk + other / 4) + 1


def estimate_messages(messages: list[Message]) -> int:
    """粗估整段历史的 token 数（含工具调用参数）。"""
    total = 0
    for message in messages:
        total += estimate_tokens(message.content or "")
        for call in message.tool_calls or []:
            total += estimate_tokens(call.name)
            total += estimate_tokens(call.raw_arguments or json.dumps(call.arguments, ensure_ascii=False))
    return total


def usage_ratio(messages: list[Message], budget: int) -> float:
    if budget <= 0:
        return 0.0
    return estimate_messages(messages) / budget


# --------------------------------------------------------------------------- #
# 压缩
# --------------------------------------------------------------------------- #

def _truncate(text: str, head: int = TRUNCATE_HEAD, tail: int = TRUNCATE_TAIL) -> str:
    lines = text.splitlines()
    if len(lines) <= head + tail + 3:
        return text
    omitted = len(lines) - head - tail
    return "\n".join(
        lines[:head] + [f"…（已省略 {omitted} 行）"] + lines[-tail:]
    )


def _arg_hint(call: ToolCall | None) -> str:
    if call is None:
        return ""
    args: dict[str, Any] = call.arguments or {}
    for key in ("path", "pattern", "command", "query"):
        if key in args:
            return str(args[key])[:60]
    if args:
        first_key = next(iter(args))
        return f"{first_key}={str(args[first_key])[:40]}"
    return ""


def _summarize_tool(message: Message, calls: dict[str, ToolCall]) -> str:
    call = calls.get(message.tool_call_id or "")
    name = call.name if call else "工具"
    hint = _arg_hint(call)
    text = message.content or ""
    ok = not text.lstrip().startswith("[error]")
    line_count = len(text.splitlines())
    status = "成功" if ok else "失败"
    return f"[历史] {name}({hint}) → {status}，约 {line_count} 行"


def compress(messages: list[Message], budget: int) -> list[Message]:
    """压缩历史。返回新列表，不修改入参。"""
    if len(messages) <= HEAD_KEEP + KEEP_RECENT:
        return list(messages)

    head = [message.clone() for message in messages[:HEAD_KEEP]]
    tail = [message.clone() for message in messages[-KEEP_RECENT:]]

    calls: dict[str, ToolCall] = {}
    for message in messages:
        for call in message.tool_calls or []:
            calls[call.id] = call

    # 第一层：截断旧工具结果
    middle: list[Message] = []
    for message in messages[HEAD_KEEP:len(messages) - KEEP_RECENT]:
        copy = message.clone()
        if copy.role == "tool" and copy.content and len(copy.content.splitlines()) > TRUNCATE_HEAD + TRUNCATE_TAIL + 3:
            copy.content = _truncate(copy.content)
        middle.append(copy)

    result = head + middle + tail
    if estimate_messages(result) <= budget:
        return result

    # 第二层：把旧工具消息整体折叠成一行摘要
    folded: list[Message] = []
    for message in middle:
        if message.role == "tool":
            folded.append(Message(role="tool", content=_summarize_tool(message, calls),
                                  tool_call_id=message.tool_call_id))
        else:
            folded.append(message)

    result = head + folded + tail
    if estimate_messages(result) <= budget:
        return result

    # 第三层：连 assistant 的中间推理也折叠，只留结论性一行
    terse: list[Message] = []
    for message in folded:
        if message.role == "assistant" and message.content and len(message.content) > 200:
            message = Message(role=message.role, content=message.content[:200] + "…",
                              tool_calls=message.tool_calls)
        terse.append(message)
    return head + terse + tail


def maybe_compress(messages: list[Message], ctx: Any) -> list[Message]:
    """主循环每轮调用：超预算才压缩，否则原样返回。"""
    budget = getattr(getattr(ctx, "config", None), "token_budget", 0) or 0
    if budget <= 0:
        return messages
    if usage_ratio(messages, budget) <= COMPRESS_TRIGGER:
        return messages
    return compress(messages, budget)
