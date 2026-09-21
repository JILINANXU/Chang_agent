"""LLMClient：OpenAI 兼容协议的 chat + tools 调用封装，含重试。

只做三件事：拼请求、解析响应、按错误类型重试。
不关心工具怎么执行，也不关心消息从哪来——那是 core 层的事。
"""

from __future__ import annotations

import time
from typing import Any

from ..config import LLMConfig
from .message import LLMResponse, Message, ToolCall, Usage

# 这些 HTTP 状态码值得重试（限流 / 网关抖动 / 服务端临时故障）
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
RETRY_BASE_DELAY = 1.0  # 退避基数：1s → 2s → 4s


class LLMError(RuntimeError):
    """模型调用失败：已重试仍不成功，或属于不可重试的配置/协议错误。"""


class LLMClient:
    """OpenAI 兼容客户端。

    用法::

        client = LLMClient(config.llm)
        resp = client.chat(messages, tools=registry.schemas())
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._client: Any = None

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    @property
    def client(self) -> Any:
        """惰性创建 SDK 客户端：只有真正要调用模型时才要求 openai 库存在。"""
        if self._client is not None:
            return self._client

        if not self.config.configured:
            raise LLMError(
                "未配置 API Key。请在项目根目录的 .env 中设置 CHANG_AGENT_API_KEY，"
                "或改用演示模式（CHANG_AGENT_WEB_MODE=demo）。"
            )

        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise LLMError(
                "未安装 openai 库。请执行：pip install openai"
            ) from exc

        self._client = OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            max_retries=0,  # 重试逻辑自己控制，便于记录与提示
        )
        return self._client

    @staticmethod
    def _status_code(exc: BaseException) -> int | None:
        for name in ("status_code", "http_status", "code"):
            value = getattr(exc, name, None)
            if isinstance(value, int):
                return value
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
        return code if isinstance(code, int) else None

    def _retryable(self, exc: BaseException) -> bool:
        """判断异常是否值得重试。"""
        try:
            import openai
        except ImportError:  # pragma: no cover
            return False

        for name in ("APIConnectionError", "APITimeoutError", "InternalServerError"):
            cls = getattr(openai, name, None)
            if cls is not None and isinstance(exc, cls):
                return True

        status = self._status_code(exc)
        if status is not None:
            return status in RETRYABLE_STATUS
        return False

    @staticmethod
    def _parse(response: Any) -> LLMResponse:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return LLMResponse(content="", tool_calls=[], usage=Usage())

        choice = choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        if content is not None and not isinstance(content, str):
            content = str(content)

        raw_calls = getattr(message, "tool_calls", None) or []
        tool_calls = [ToolCall.from_openai(item) for item in raw_calls]

        raw_usage = getattr(response, "usage", None)
        usage = Usage(
            prompt_tokens=int(getattr(raw_usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(raw_usage, "completion_tokens", 0) or 0),
        )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=str(getattr(choice, "finish_reason", "") or ""),
        )

    # ------------------------------------------------------------------ #
    # 对外
    # ------------------------------------------------------------------ #

    def chat(self, messages: list[Message], tools: list[dict] | None = None) -> LLMResponse:
        """发起一次对话（带工具）。失败按指数退避重试，最终失败抛 LLMError。"""
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": [message.to_openai() for message in messages],
        }
        # 推理型模型可能不支持 temperature，配置为非 0 时才下发，减少兼容问题
        if self.config.temperature:
            kwargs["temperature"] = self.config.temperature
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        attempts = max(1, self.config.max_retries + 1)
        last_error: BaseException | None = None

        for attempt in range(attempts):
            try:
                response = self.client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - 统一转成 LLMError 更利于上层处理
                last_error = exc
                if attempt >= attempts - 1 or not self._retryable(exc):
                    break
                time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
            else:
                return self._parse(response)

        status = self._status_code(last_error) if last_error else None
        detail = str(last_error) if last_error else "未知错误"
        hint = ""
        if status == 401:
            hint = "（API Key 无效或已过期，请检查 .env 的 CHANG_AGENT_API_KEY）"
        elif status == 404:
            hint = "（模型名或 Base URL 可能不对，请检查 .env 的 CHANG_AGENT_MODEL / BASE_URL）"
        elif status == 429:
            hint = "（触发限流或额度不足，请稍后重试或检查账户余额）"

        raise LLMError(
            f"模型调用失败（{self.config.model} @ {self.config.base_url}）：{detail}{hint}"
        ) from last_error
