"""畅Agent Web 层：FastAPI 服务 + 零构建前端（纯 HTML/CSS/JS）。"""

from __future__ import annotations

from .server import DEFAULT_HOST, DEFAULT_PORT, app, serve

__all__ = ["app", "serve", "DEFAULT_HOST", "DEFAULT_PORT"]
