"""Agent 主循环：推理 → 工具调用 → 观察 → 收敛。

`run_stream()` 是唯一实现，它产出的**事件流**同时服务两个通道：
- 命令行：`ui/render.py` 把事件画成终端输出；
- Web 界面：`web/server.py` 把事件包成 SSE 帧推给浏览器。

协议见 docs/WEB.md 第 4 节，前端与 CLI 都只认事件、不认事件来源。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from ..config import AppConfig, load_config
from ..llm.client import LLMClient, LLMError
from ..llm.message import Message, ToolCall, Usage
from ..prompts import loader
from ..tools import ToolRegistry, build_default_registry
from ..tools.base import Tool
from . import context as context_module
from .sandbox import Sandbox
from .session import SessionRecorder

FAIL_STREAK_LIMIT = 3  # 同一个工具连续失败达到该次数即熔断，避免无效烧 token


class _Abort(RuntimeError):
    """内部信号：需要主动中止本次任务。"""


@dataclass
class RunContext:
    """一次运行的全部上下文，工具通过它访问沙箱、配置与已读白名单。"""

    workspace: Path
    config: AppConfig
    registry: ToolRegistry
    sandbox: Sandbox
    recorder: SessionRecorder | None = None
    read_files: set[str] = field(default_factory=set)
    approver: Callable[[Tool, ToolCall], bool] | None = None
    auto_approve: bool = False

    # 读后写白名单：edit_file / write_file 的硬约束来源
    def mark_read(self, rel: str) -> None:
        self.read_files.add(self._key(rel))

    def has_read(self, rel: str) -> bool:
        return self._key(rel) in self.read_files

    @staticmethod
    def _key(rel: str) -> str:
        return rel.replace("\\", "/").strip()


@dataclass
class RunResult:
    """一次运行的最终结果（供程序化调用与测试）。"""

    ok: bool
    answer: str
    steps: int
    usage: Usage = field(default_factory=Usage)
    error: str = ""
    elapsed_ms: int = 0


def build_context(
    config: AppConfig,
    task: str,
    *,
    registry: ToolRegistry | None = None,
    approver: Callable[[Tool, ToolCall], bool] | None = None,
) -> RunContext:
    """装配运行上下文（含沙箱与会话记录器）。"""
    config.ensure_dirs()
    active_registry = registry or build_default_registry(enable_shell=config.enable_shell)
    return RunContext(
        workspace=config.workspace,
        config=config,
        registry=active_registry,
        sandbox=Sandbox(config.workspace, backup=config.backup),
        recorder=SessionRecorder(config.workspace, task, config.llm.model),
        approver=approver,
        auto_approve=config.auto_approve,
    )


def run_stream(
    task: str,
    config: AppConfig | None = None,
    *,
    registry: ToolRegistry | None = None,
    client: Any | None = None,
    approver: Callable[[Tool, ToolCall], bool] | None = None,
    ctx: RunContext | None = None,
    messages: list[Message] | None = None,
) -> Iterator[dict[str, Any]]:
    """执行任务并逐步产出事件。

    - `messages` 传入时按原列表**原地**追加（REPL 借它保留多轮上下文，调用方需自行
      追加本次的 user 消息）；不传则新建 system + user 两条。
    - 事件类型见 docs/WEB.md 4.1；`done` 事件在任何情况下都会发送。
    """
    config = config or load_config()
    active_ctx = ctx or build_context(config, task, registry=registry, approver=approver)
    registry = active_ctx.registry
    recorder = active_ctx.recorder

    yield {
        "type": "agent_start",
        "task": task,
        "model": config.llm.model,
        "workspace": str(config.workspace),
    }

    ok = False
    steps = 0
    answer = ""
    error_text = ""
    usage = Usage()
    started = time.perf_counter()

    try:
        history = messages if messages is not None else loader.build_messages(config, registry, task)
        llm = client if client is not None else LLMClient(config.llm)
        fail_streak: dict[str, int] = {}

        for step in range(1, config.max_steps + 1):
            steps = step
            response = llm.chat(history, tools=registry.schemas())
            usage = usage + response.usage

            # 收敛条件：模型不再请求工具，说明它认为任务可以交付了
            if not response.tool_calls:
                # 把最终回复也写回历史：REPL 多轮时模型才能看到自己上一轮的结论
                history.append(response.to_assistant_message())
                answer = (response.content or "").strip() or "（模型没有返回内容）"
                ok = True
                yield {"type": "answer", "text": answer}
                break

            # 模型在调工具前说的话，作为"思考"展示
            if response.content and response.content.strip():
                yield {"type": "thinking", "text": response.content.strip()}

            history.append(response.to_assistant_message())

            for call in response.tool_calls:
                yield {
                    "type": "tool_call",
                    "id": call.id,
                    "name": call.name,
                    "args": call.arguments,
                }

                result = active_ctx.registry.dispatch(call, active_ctx)

                yield {
                    "type": "tool_result",
                    "id": call.id,
                    "ok": result.ok,
                    "elapsed_ms": result.meta.get("elapsed_ms", 0),
                    "content": result.to_model_text(),
                }

                history.append(
                    Message(role="tool", content=result.to_model_text(), tool_call_id=call.id)
                )

                if recorder is not None:
                    recorder.record(step, call, result)

                if result.ok:
                    fail_streak.pop(call.name, None)
                else:
                    fail_streak[call.name] = fail_streak.get(call.name, 0) + 1

                meta = result.meta or {}
                # file_change 只在文件真正落盘后发送
                if result.ok and meta.get("diff") is not None and meta.get("path"):
                    yield {
                        "type": "file_change",
                        "path": meta["path"],
                        "added": meta.get("added", 0),
                        "removed": meta.get("removed", 0),
                        "backup": meta.get("backup"),
                        "diff": meta["diff"],
                    }

                if fail_streak.get(call.name, 0) >= FAIL_STREAK_LIMIT:
                    raise _Abort(
                        f"工具 {call.name} 连续失败 {FAIL_STREAK_LIMIT} 次，已中止任务以避免无效消耗。"
                        "请人工检查工作区状态后再重试。"
                    )

            history = context_module.maybe_compress(history, active_ctx)

        else:
            error_text = f"已达最大步数（{config.max_steps}），任务未收敛，请拆分需求或调大 CHANG_AGENT_MAX_STEPS。"
            # 必须显式告知通道：否则用户只看到 done.ok=False，不知道卡在哪里
            yield {"type": "error", "text": error_text}

    except _Abort as exc:
        error_text = str(exc)
        yield {"type": "error", "text": error_text}
    except LLMError as exc:
        error_text = str(exc)
        yield {"type": "error", "text": error_text}
    except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都要给前端一个交代
        error_text = f"运行时异常：{exc}"
        yield {"type": "error", "text": error_text}

    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if recorder is not None:
        recorder.finish(
            ok=ok,
            answer=answer,
            steps=steps,
            usage=usage.to_dict(),
            error="" if ok else error_text,
        )

    yield {
        "type": "done",
        "ok": ok,
        "steps": steps,
        "usage": usage.to_dict(),
        "elapsed_ms": elapsed_ms,
    }


def run(task: str, config: AppConfig | None = None, **kwargs: Any) -> RunResult:
    """非流式执行：把事件流跑完并汇总为 RunResult。"""
    result = RunResult(ok=False, answer="", steps=0)
    for event in run_stream(task, config, **kwargs):
        kind = event.get("type")
        if kind == "answer":
            result.answer = event.get("text", "")
        elif kind == "error":
            result.error = event.get("text", "")
        elif kind == "done":
            result.ok = bool(event.get("ok"))
            result.steps = int(event.get("steps", 0))
            result.elapsed_ms = int(event.get("elapsed_ms", 0))
            usage = event.get("usage") or {}
            result.usage = Usage(
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
            )
    return result
