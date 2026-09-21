"""python -m changagent.web 的入口。

用法：
    python -m changagent.web
    python -m changagent.web --port 9000
    python -m changagent.web --reload      # 开发模式，改代码自动重启
"""

from __future__ import annotations

import argparse

from .server import DEFAULT_HOST, DEFAULT_PORT, serve


def main() -> None:
    parser = argparse.ArgumentParser(description="畅Agent Web 服务")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 8765")
    parser.add_argument("--reload", action="store_true", help="开发模式：文件改动自动重启")
    args = parser.parse_args()
    serve(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
