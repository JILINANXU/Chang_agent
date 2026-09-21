"""命令工具：run_command（默认关闭，需在 .env 显式开启）。

三重约束（见 docs/TOOLS.md 3.7）：
1. CHANG_AGENT_ENABLE_SHELL=true 才把该工具注册给模型；
2. 命令首词必须在允许列表内；
3. 命中危险黑名单直接拒绝。

工作目录固定为工作区根，超时杀进程树。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any

from .base import Tool, ToolError, ToolResult, get_int, get_str

# 允许的命令首词（不做任意命令执行）
ALLOWED_COMMANDS = {
    "python", "python3", "py", "pytest", "pip", "pip3",
    "node", "npm", "npx", "deno", "bun",
    "git", "dir", "type", "echo", "where", "ls", "cat", "find", "findstr",
    "ruff", "black", "mypy", "tsc", "cargo", "go",
}

# git 只放开只读子命令，避免误提交 / 误回滚
ALLOWED_GIT_SUBCOMMANDS = {"status", "diff", "log", "show", "branch", "remote", "rev-parse", "ls-files"}

# 危险模式：命中即拒绝
BLOCKED_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*\s+)*(-rf|-fr|-r\s+-f|-f\s+-r)",
    r"\brmdir\s+/s",
    r"\bdel\s+/[a-zA-Z]*[fs]",
    r"\bformat\s+[a-zA-Z]:",
    r"\bmkfs\b",
    r"\bdiskpart\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\breg\s+(delete|add)\b",
    r"\bnet\s+user\b",
    r"\btakeown\b",
    r"\bicacls\b",
    r"\bcipher\s+/w\b",
    r"\bremove-item\b.*(-recurse|-r)\b.*(-force|-f)\b",
    r"\biex\b",
    r"\binvoke-expression\b",
    r"\bstart-process\b",
    r"\bcurl\b.*\|",
    r"\bwget\b.*\|",
    r"\bgit\s+(push|reset\s+--hard|clean\s+-[a-zA-Z]*f|checkout\s+--\s)",
    r"[;&|]\s*(rm|del|format|shutdown)\b",
    r"\bnpm\s+(publish|install\s+-g)\b",
    r"\bpip\s+install\b.*(--user|-g|--global)",
]

MAX_OUTPUT_CHARS = 4000
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 300


class RunCommandTool(Tool):
    """在工作区内执行受限命令（编译、测试、校验）。"""

    name = "run_command"
    description = "在工作目录下执行命令（如运行测试、语法检查）。仅支持允许列表内的命令，破坏性命令会被拒绝。"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "完整命令，例如 python -m pytest -q"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 60，上限 300"},
        },
        "required": ["command"],
    }
    requires_approval = True
    read_only = False

    def run(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        command = get_str(args, "command", required=True).strip()
        timeout = get_int(args, "timeout", DEFAULT_TIMEOUT, minimum=1, maximum=MAX_TIMEOUT)

        first = self._first_token(command)

        for pattern in BLOCKED_PATTERNS:
            if re.search(pattern, command, flags=re.IGNORECASE):
                raise ToolError(
                    "CMD_BLOCKED",
                    f"命令被安全策略拒绝：{command}",
                    detail=f"命中危险模式：{pattern}",
                    suggestion="请改用非破坏性的命令；删除、格式化、系统设置类操作一律不允许",
                )

        if first not in ALLOWED_COMMANDS:
            raise ToolError(
                "CMD_BLOCKED",
                f"命令 `{first}` 不在允许列表内",
                detail="只允许运行开发相关命令，例如 " + "、".join(sorted(list(ALLOWED_COMMANDS))[:12]) + " 等",
                suggestion="若必须执行其它命令，请改为由用户在终端手动执行",
            )

        if first == "git":
            parts = command.split()
            sub = parts[1].lower() if len(parts) > 1 else ""
            if sub not in ALLOWED_GIT_SUBCOMMANDS:
                raise ToolError(
                    "CMD_BLOCKED",
                    f"git 子命令 `{sub}` 不允许由 Agent 执行",
                    suggestion="只允许只读的 git 查询（status / diff / log / show 等）",
                )

        return self._execute(command, timeout, ctx)

    @staticmethod
    def _first_token(command: str) -> str:
        text = command.strip()
        if not text:
            raise ToolError("PARSE_ERROR", "命令为空", suggestion="请提供要执行的命令")
        token = text.split()[0].strip("\"'")
        if "\\" in token or "/" in token:
            token = os.path.basename(token.replace("\\", "/"))
        if token.lower().endswith(".exe"):
            token = token[:-4]
        if token.lower().endswith(".py"):
            token = "python"
        return token.lower()

    def _execute(self, command: str, timeout: int, ctx: Any) -> ToolResult:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        started = time.perf_counter()
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=str(ctx.sandbox.workspace),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except OSError as exc:
            raise ToolError(
                "CMD_BLOCKED",
                f"命令无法启动：{command}",
                detail=str(exc),
                suggestion="请确认该命令已安装并在 PATH 中",
            ) from exc

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill_tree(process)
            raise ToolError(
                "CMD_TIMEOUT",
                f"命令超时（{timeout}s），已终止：{command}",
                suggestion="请改用更小的范围（如只跑单个测试文件），或调大 timeout",
            ) from None

        elapsed = time.perf_counter() - started
        code = process.returncode

        out_text = (stdout or "").strip()
        err_text = (stderr or "").strip()
        if len(out_text) > MAX_OUTPUT_CHARS:
            out_text = out_text[:MAX_OUTPUT_CHARS] + "\n…（输出已截断）"
        if len(err_text) > MAX_OUTPUT_CHARS:
            err_text = err_text[:MAX_OUTPUT_CHARS] + "\n…（输出已截断）"

        body = (
            f"退出码 {code}（耗时 {elapsed:.1f}s）\n"
            f"--- stdout ---\n{out_text or '（空）'}\n"
            f"--- stderr ---\n{err_text or '（空）'}"
        )
        meta = {"command": command, "exit_code": code, "elapsed_ms": int(elapsed * 1000)}

        if code != 0:
            return ToolResult.failure(
                "[error] CMD_FAILED：命令以非零退出码结束，输出如下\n" + body,
                **meta,
            )
        return ToolResult.success(body, **meta)

    @staticmethod
    def _kill_tree(process: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                process.kill()
        except Exception:  # noqa: BLE001 - 清理失败不影响主流程
            pass
