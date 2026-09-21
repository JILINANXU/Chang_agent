"""畅Agent Web 服务层。

职责：
1. 托管前端静态页面（零构建：纯 HTML / CSS / JS，不需要 npm）
2. 通过 SSE 把 Agent 的工作过程实时推送给浏览器
3. 提供工作区状态接口，供页面渲染文件树

模式：
- demo（默认）：事件来自 mock.py，用于演示界面与交互
- live：接入真实 Agent 内核（core/agent.py，里程碑 M1 后可用）

启动方式：
    python run.py                    # 推荐：根目录启动口，自动装依赖检查 + 开浏览器
    python -m changagent.web         # 直接启动
    uvicorn changagent.web.server:app --port 8765
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import load_config, resolve_workspace
from ..core.sandbox import Sandbox
from .mock import DEMO_TASK, demo_events

APP_NAME = "畅Agent"
STATIC_DIR = Path(__file__).parent / "static"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765  # 避开 ComfyUI 的 8000

app = FastAPI(title=APP_NAME, version=__version__, docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------- #
# 基础信息
# --------------------------------------------------------------------------- #

def mode() -> str:
    """运行模式：demo（演示数据）| live（真实模型）。"""
    return load_config().web_mode


def workspace_root() -> Path:
    """沙箱根目录，所有文件操作都被限制在这里面。

    解析规则与内核、启动口共用 config.resolve_workspace()，避免多处定义漂移：
    未配置 CHANG_AGENT_WORKSPACE 时指向 examples/demo_workspace，
    相对路径一律按项目根解析，避免受启动目录影响。
    """
    return resolve_workspace()


def _scan_tree(root: Path, max_depth: int = 2) -> list[dict]:
    """扫描工作区，返回扁平条目列表（前端按 depth 缩进渲染）。

    忽略规则（依赖目录 / 敏感文件）由内核沙箱统一提供，
    保证页面上看到的文件与 Agent 能碰的文件是同一套范围。
    """
    sandbox = Sandbox(root, backup=False)
    out: list[dict] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for entry in sandbox.visible_entries(directory):
            try:
                rel = entry.relative_to(root).as_posix()
                if entry.is_dir():
                    out.append({"path": rel, "type": "dir", "depth": depth - 1})
                    walk(entry, depth + 1)
                else:
                    out.append({"path": rel, "type": "file", "depth": depth - 1,
                                "size": entry.stat().st_size})
            except OSError:
                continue

    if root.is_dir():
        walk(root, 1)
    return out


@app.get("/api/health")
def api_health() -> dict:
    """启动口用它判断服务是否真正就绪。"""
    return {"ok": True, "app": APP_NAME, "mode": mode()}


@app.get("/api/state")
def api_state() -> dict:
    """页面初始化数据：模式、工作区、模型状态、文件树。"""
    config = load_config()
    root = config.workspace
    return {
        "app": APP_NAME,
        "version": __version__,
        "mode": config.web_mode,
        "workspace": str(root),
        "workspace_exists": root.is_dir(),
        "model": config.llm.model if config.llm.configured else "未配置",
        "model_configured": config.llm.configured,
        "shell_enabled": config.enable_shell,
        "auto_approve": config.auto_approve,
        "demo_task": DEMO_TASK,
        "files": _scan_tree(root),
    }


# --------------------------------------------------------------------------- #
# 事件流
# --------------------------------------------------------------------------- #

def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def live_events(task: str) -> Iterator[str]:
    """真实 Agent 事件流：内核产出事件，本层只负责包成 SSE 帧。

    事件协议见 docs/WEB.md 第 4 节。内核保证 `done` 必定发送，
    这里再兜一层，确保任何意外都不会让前端永远停在“运行中”。
    """
    from ..core.agent import run_stream  # 延迟导入：演示模式无需加载内核

    generator = run_stream(task)
    try:
        for event in generator:
            yield _sse(event)
    except Exception as exc:  # noqa: BLE001 - 兜底：前端必须收到收尾事件
        yield _sse({"type": "error", "text": f"服务端异常：{exc}"})
        yield _sse({"type": "done", "ok": False, "steps": 0, "usage": {}})
    finally:
        generator.close()


@app.get("/api/run")
def api_run(task: str = Query(default="", max_length=2000)) -> StreamingResponse:
    """SSE 接口：浏览器用 EventSource 订阅，实时接收 Agent 的工作事件。"""
    text = task.strip() or DEMO_TASK
    source = live_events if mode() == "live" else demo_events
    return StreamingResponse(
        source(text),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# 静态页面
# --------------------------------------------------------------------------- #

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/guide")
def guide() -> FileResponse:
    """前端内置说明文档页：用法、模式、沙箱与备份、常见问题。"""
    return FileResponse(STATIC_DIR / "guide.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --------------------------------------------------------------------------- #
# 启动入口
# --------------------------------------------------------------------------- #

def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, reload: bool = False) -> None:
    """启动 Web 服务（阻塞）。"""
    import uvicorn

    if reload:
        uvicorn.run("changagent.web.server:app", host=host, port=port, reload=True)
    else:
        uvicorn.run(app, host=host, port=port, log_level="info")
