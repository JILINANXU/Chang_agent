"""工具基类与统一返回结构。

设计约定（见 docs/TOOLS.md）：
- 一个工具 = 一次原子操作，不做"万能 exec"；
- 只读工具永不写盘；
- 任何失败都以 ToolResult(ok=False) 返回给模型，让它自己纠错，而不是抛异常炸掉主循环。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ToolError(Exception):
    """工具执行失败，携带错误码（错误码总表见 docs/TOOLS.md 第 2.1 节）。"""

    def __init__(
        self,
        code: str,
        reason: str,
        *,
        detail: str = "",
        suggestion: str = "",
    ) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.detail = detail
        self.suggestion = suggestion

    def to_text(self) -> str:
        """转成给模型看的纯文本（含原因与建议，便于模型自我纠正）。"""
        lines = [f"[error] {self.code}：{self.reason}"]
        if self.detail:
            lines.append(f"原因：{self.detail}")
        if self.suggestion:
            lines.append(f"建议：{self.suggestion}")
        return "\n".join(lines)


@dataclass
class ToolResult:
    """工具执行结果。

    content 是给模型看的正文（人类可读，不含 [ok] 前缀）；
    meta 是给程序看的元信息，不进入模型上下文。
    """

    ok: bool
    content: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def success(cls, content: str, **meta: Any) -> "ToolResult":
        return cls(ok=True, content=content, meta=meta)

    @classmethod
    def failure(cls, message: str, **meta: Any) -> "ToolResult":
        return cls(ok=False, content=message, meta=meta, error=message)

    @classmethod
    def from_error(cls, error: ToolError, **meta: Any) -> "ToolResult":
        return cls(ok=False, content=error.to_text(), meta=meta, error=error.to_text())

    def to_model_text(self) -> str:
        """回灌给模型的最终文本。"""
        if self.ok:
            return f"[ok] {self.content}" if self.content else "[ok] 完成"
        return self.content or "[error] 未知错误"


class Tool:
    """工具基类。

    新增工具只需继承本类，并在 `tools/__init__.py::build_default_registry()` 中注册。
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}
    requires_approval: bool = False
    read_only: bool = True

    def run(self, args: dict[str, Any], ctx: Any) -> ToolResult:  # pragma: no cover - 抽象方法
        raise NotImplementedError(f"工具 {self.name} 未实现 run()")

    def schema(self) -> dict[str, Any]:
        """生成 OpenAI function calling 的工具定义。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def one_line(self) -> str:
        """一行说明，用于系统提示词里的 {{TOOL_LIST}}。"""
        first = self.description.split("。")[0].strip()
        return f"- `{self.name}`：{first}。"

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Tool {self.name}>"


# --------------------------------------------------------------------------- #
# 参数读取辅助：模型给的参数一律视为不可信输入
# --------------------------------------------------------------------------- #

def _type_error(key: str, expected: str) -> ToolError:
    return ToolError(
        "PARSE_ERROR",
        f"参数 {key} 类型不正确，应为{expected}",
        detail="模型生成的参数不符合工具 Schema",
        suggestion="请按工具定义的参数类型重新发起调用",
    )


def get_str(args: dict[str, Any], key: str, *, required: bool = False, default: str = "") -> str:
    value = args.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        else:
            raise _type_error(key, "字符串")
    if required and not value.strip():
        raise ToolError(
            "PARSE_ERROR",
            f"缺少必填参数 {key}",
            suggestion="请按工具 Schema 补全参数后重试",
        )
    return value


def get_int(
    args: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = args.get(key, default)
    if value is None or value == "":
        value = default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise _type_error(key, "整数") from None
    if minimum is not None and number < minimum:
        number = minimum
    if maximum is not None and number > maximum:
        number = maximum
    return number


def get_bool(args: dict[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "是"}:
            return True
        if lowered in {"false", "0", "no", "n", "否"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default
