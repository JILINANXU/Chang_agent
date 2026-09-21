"""消息与工具调用数据结构：Message / ToolCall / ToolResult / LLMResponse。

这些结构是内核各层之间的通用语言：
- `core.agent` 负责编排，`llm.client` 负责与模型对话，`tools.*` 负责执行。
- 对外（OpenAI 协议）的序列化统一走 `to_openai()`，隔离 SDK 细节。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """兼容 dict 与对象两种形态（SDK 版本 / 第三方兼容端点差异）。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass
class ToolCall:
    """模型发起的一次工具调用。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    parse_error: str | None = None

    @classmethod
    def from_openai(cls, data: Any) -> "ToolCall":
        """从 SDK 返回的 tool_call 构造；参数解析失败不抛异常，记录原因由上层回灌。"""
        fn = _attr(data, "function")
        raw = _attr(fn, "arguments", "") or ""
        if not isinstance(raw, str):
            raw = json.dumps(raw, ensure_ascii=False)

        arguments: dict[str, Any] = {}
        parse_error: str | None = None
        if raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    arguments = parsed
                else:
                    parse_error = f"参数不是 JSON 对象，而是 {type(parsed).__name__}"
            except (ValueError, TypeError) as exc:
                parse_error = f"{exc}"

        return cls(
            id=str(_attr(data, "id", "") or ""),
            name=str(_attr(fn, "name", "") or ""),
            arguments=arguments,
            raw_arguments=raw,
            parse_error=parse_error,
        )

    def to_openai(self) -> dict[str, Any]:
        """回灌给模型时必须原样带回 id / name / arguments（字符串形态）。"""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.raw_arguments or json.dumps(self.arguments, ensure_ascii=False),
            },
        }

    def brief(self) -> str:
        """一行摘要，用于日志与终端渲染。"""
        if self.arguments:
            parts = []
            for key, value in self.arguments.items():
                text = str(value)
                if len(text) > 40:
                    text = text[:37] + "…"
                parts.append(f"{key}={text}")
            return f"{self.name}({', '.join(parts)})"
        return f"{self.name}()"


@dataclass
class Message:
    """一条对话消息。"""

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_openai(self) -> dict[str, Any]:
        if self.role == "assistant":
            payload: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
            if self.tool_calls:
                payload["tool_calls"] = [call.to_openai() for call in self.tool_calls]
            return payload
        if self.role == "tool":
            return {
                "role": "tool",
                "content": self.content or "",
                "tool_call_id": self.tool_call_id or "",
            }
        return {"role": self.role, "content": self.content or ""}

    def clone(self) -> "Message":
        return Message(
            role=self.role,
            content=self.content,
            tool_calls=list(self.tool_calls) if self.tool_calls else None,
            tool_call_id=self.tool_call_id,
        )


@dataclass
class Usage:
    """token 用量，可跨步累加。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass
class LLMResponse:
    """模型一次回复。"""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = ""

    def to_assistant_message(self) -> Message:
        """转为可写入历史的 assistant 消息。

        注意：只要存在 tool_calls，就必须带 content（部分兼容端点要求非空），
        否则后续 tool 消息回灌会被拒。
        """
        return Message(
            role="assistant",
            content=self.content or "",
            tool_calls=self.tool_calls or None,
        )
