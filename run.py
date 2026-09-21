#!/usr/bin/env python
"""畅Agent 启动口。

用法：
    python run.py                 一键启动（演示模式），自动打开浏览器
    python run.py --port 9000     指定端口
    python run.py --no-browser    不自动打开浏览器
    python run.py --reload        开发模式：改代码自动重启

启动后在浏览器里操作界面，按 Ctrl+C 停止服务。
"""

from __future__ import annotations

import argparse
import importlib.util
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# (pip 包名, 导入名)
REQUIRED = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
]
OPTIONAL = [
    ("openai", "openai"),
    ("python-dotenv", "dotenv"),
]

LINE = "  " + "-" * 52


def say(text: str = "") -> None:
    print(text)


def step(index: int, total: int, label: str, result: str = "") -> None:
    head = f"  [{index}/{total}] {label}"
    print(f"{head:<34}{result}")


def missing_packages(pairs: list[tuple[str, str]]) -> list[str]:
    return [pip for pip, mod in pairs if importlib.util.find_spec(mod) is None]


def pip_install(packages: list[str]) -> bool:
    say(f"  正在安装依赖：{', '.join(packages)}")
    try:
        code = subprocess.call([sys.executable, "-m", "pip", "install", *packages])
    except OSError as exc:
        say(f"  安装失败：{exc}")
        return False
    if code != 0:
        say("  安装未成功。请手动执行：")
        say(f"    {sys.executable} -m pip install -r requirements.txt")
        return False
    return True


def pick_port(host: str, port: int, tries: int = 20) -> int:
    """端口被占用时顺延，避免与 ComfyUI(8000) 等服务冲突。"""
    for candidate in range(port, port + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex((host, candidate)) != 0:
                return candidate
    return port


def main() -> int:
    parser = argparse.ArgumentParser(description="畅Agent 一键启动")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--no-install", action="store_true", help="不自动安装缺失依赖")
    parser.add_argument("--reload", action="store_true", help="开发模式：改动自动重启")
    args = parser.parse_args()

    total = 4
    say()
    say("  畅Agent · 本地编码智能体")
    say(LINE)

    # 保证能 import 到本项目的包（脚本直接运行时 sys.path[0] 已是项目根，这里兜底）
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    # 1) Python 版本
    version = sys.version.split()[0]
    if sys.version_info < (3, 10):
        step(1, total, "运行环境", f"需要 3.10+，当前 {version}")
        say("  请升级 Python 后重试。")
        return 1
    step(1, total, "运行环境", f"Python {version}  OK")

    # 2) 依赖
    missing = missing_packages(REQUIRED)
    if missing:
        if args.no_install:
            step(2, total, "依赖检查", "缺失：" + ", ".join(missing))
            say(f"  请执行：{sys.executable} -m pip install -r requirements.txt")
            return 1
        step(2, total, "依赖检查", "缺失：" + ", ".join(missing))
        if not pip_install(missing):
            return 1
        step(2, total, "依赖检查", "安装完成  OK")
    else:
        step(2, total, "依赖检查", "OK")

    # 3) 配置（与内核、Web 层共用 config.py 的同一套解析规则，避免多处漂移）
    from os import environ

    from changagent.config import load_env_file, resolve_workspace

    load_env_file()  # 优先用 python-dotenv，未安装时自动退回内置解析器

    mode = environ.get("CHANG_AGENT_WEB_MODE", "demo").strip().lower()

    # 真实模式的两个前提：openai 库 + API Key，任一不满足就回退演示模式
    fallback = ""
    if mode == "live":
        missing_openai = missing_packages([("openai", "openai")])
        if missing_openai:
            if args.no_install or not pip_install(missing_openai):
                fallback = "未安装 openai 库"
        if not fallback and not environ.get("CHANG_AGENT_API_KEY"):
            fallback = "未配置 API Key"

    if fallback:
        mode = "demo"
        # 必须写回环境变量：Web 层用自己的 config 读同一个值，否则两边结论不一致
        environ["CHANG_AGENT_WEB_MODE"] = "demo"

    workspace_path = resolve_workspace()

    if mode == "live":
        note = "真实模型"
    elif fallback:
        note = f"{fallback}，回退演示模式"
    else:
        note = "演示模式（无需 API Key）"

    step(3, total, "运行配置", note)
    say(f"  {'':6}工作区：{workspace_path}")
    if not workspace_path.is_dir():
        say(f"  {'':6}提示：工作区目录不存在，界面会显示为空")

    # 4) 启动
    port = pick_port(args.host, args.port)
    url = f"http://{args.host}:{port}"
    step(4, total, "启动服务", url)
    if port != args.port:
        say(f"  {'':6}端口 {args.port} 已被占用，自动改用 {port}")

    say(LINE)
    say("  浏览器即将打开，按 Ctrl+C 停止服务")
    say()

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    from changagent.web.server import serve

    try:
        serve(host=args.host, port=port, reload=args.reload)
    except KeyboardInterrupt:
        say("\n  服务已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
