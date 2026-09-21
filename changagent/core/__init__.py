"""Agent 内核子包。

对外只暴露两件事：怎么跑（run / run_stream）、怎么装配（build_context）。
展示层（cli / web）从这里取能力，内核不反向依赖任何展示层。
"""

from __future__ import annotations

from .agent import RunContext, RunResult, build_context, run, run_stream
from .sandbox import Sandbox
from .session import SessionRecorder

__all__ = [
    "RunContext",
    "RunResult",
    "build_context",
    "run",
    "run_stream",
    "Sandbox",
    "SessionRecorder",
]
