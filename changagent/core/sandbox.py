"""路径沙箱：路径解析与越界拦截、敏感文件拦截、写前备份。

三道闸（见 docs/DESIGN.md 第 7 节）：
1. 路径沙箱：`resolve()` 后必须位于 workspace 之下，不靠字符串匹配；
2. 敏感拦截：`.env` / `.git/**` / `*.pem` / `.changagent/**` 等一律拒读拒写；
3. 变更备份：写入前把原文件复制到 `.changagent/backups/<时间戳>/<相对路径>`。

模型给的路径一律当作不可信输入，所有落盘操作都必须先经过这里。
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from ..tools.base import ToolError

# 扫描与操作都跳过的目录
IGNORE_DIRS = {
    ".git", ".changagent", "__pycache__", ".venv", "venv", "node_modules",
    ".idea", ".vscode", ".pytest_cache", ".ruff_cache", "dist", "build",
}

# 敏感文件名（精确匹配 / 前缀匹配）
SENSITIVE_EXACT = {".env", ".env.local", ".gitconfig", ".netrc", "credentials"}
SENSITIVE_PREFIX = (".env.", "id_rsa", "id_ed25519", "id_dsa")
SENSITIVE_SUFFIX = (".pem", ".key", ".pfx", ".p12", ".keystore")


class Sandbox:
    """工作区路径守卫。"""

    def __init__(self, workspace: str | Path, *, backup: bool = True) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.backup_enabled = backup
        self.violations = 0  # 违规计数，供会话记录与提示使用

    # ------------------------------------------------------------------ #
    # 路径校验
    # ------------------------------------------------------------------ #

    def resolve(
        self,
        raw: str | None,
        *,
        must_exist: bool = False,
        expect_file: bool = False,
        expect_dir: bool = False,
    ) -> Path:
        """把模型给的路径解析成工作区内的绝对路径，越界或敏感则抛 ToolError。"""
        text = (raw or "").strip() or "."
        candidate = Path(text).expanduser()

        if candidate.is_absolute():
            target = candidate
        else:
            target = self.workspace / candidate

        try:
            # strict=False：允许目标尚不存在（新建文件场景）
            target = target.resolve()
        except OSError:
            target = target.absolute()

        if not self._inside(target):
            self.violations += 1
            raise ToolError(
                "PATH_OUT_OF_SANDBOX",
                "拒绝访问：目标位于工作目录之外",
                detail=f"工作目录：{self.workspace}；请求路径：{text}",
                suggestion="只能使用工作目录内的相对路径，例如 hello.py 或 src/main.py",
            )

        rel = self.relative(target)
        if self.is_sensitive(rel):
            self.violations += 1
            raise ToolError(
                "SENSITIVE_FILE",
                "拒绝访问：该文件属于敏感内容",
                detail=f"{rel} 命中敏感文件规则（密钥 / 版本库 / 运行数据）",
                suggestion="请放弃该目标，改为操作普通业务文件，并向用户说明原因",
            )

        if self.is_ignored(rel) and not rel.startswith(".changagent/"):
            raise ToolError(
                "SENSITIVE_FILE",
                "拒绝访问：该目录不参与工作区扫描",
                detail=f"{rel} 位于忽略目录内",
                suggestion="请改为操作工作目录中的普通文件",
            )

        if must_exist and not target.exists():
            raise ToolError(
                "NOT_FOUND",
                f"路径不存在：{rel}",
                suggestion="先用 list_dir 或 glob_search 确认真实路径，不要猜测文件名",
            )

        if expect_file and target.exists() and not target.is_file():
            raise ToolError(
                "NOT_A_FILE",
                f"目标不是文件：{rel}",
                suggestion="该路径是一个目录，请改用 read_file 之外的方式",
            )
        if expect_file and not target.exists():
            raise ToolError(
                "NOT_FOUND",
                f"文件不存在：{rel}",
                suggestion="先用 list_dir 或 glob_search 确认真实路径",
            )

        if expect_dir:
            if not target.exists():
                raise ToolError(
                    "NOT_FOUND",
                    f"目录不存在：{rel}",
                    suggestion="先用 list_dir 确认工作目录结构",
                )
            if not target.is_dir():
                raise ToolError(
                    "NOT_A_DIR",
                    f"目标不是目录：{rel}",
                    suggestion="该路径是一个文件，请改用 read_file 读取",
                )

        return target

    def _inside(self, target: Path) -> bool:
        if target == self.workspace:
            return True
        try:
            return target.is_relative_to(self.workspace)
        except AttributeError:  # pragma: no cover - Python < 3.9 兜底
            try:
                target.relative_to(self.workspace)
                return True
            except ValueError:
                return False

    # ------------------------------------------------------------------ #
    # 相对路径与敏感判断
    # ------------------------------------------------------------------ #

    def relative(self, target: Path) -> str:
        """转成工作区相对路径（posix 风格），失败时退回文件名。"""
        try:
            return target.relative_to(self.workspace).as_posix()
        except ValueError:
            return target.name

    def is_sensitive(self, rel: str) -> bool:
        for part in Path(rel).parts:
            lowered = part.lower()
            if lowered in {".git", ".changagent"}:
                return True
            if lowered in SENSITIVE_EXACT:
                return True
            if lowered.startswith(SENSITIVE_PREFIX):
                return True
            if lowered.endswith(SENSITIVE_SUFFIX):
                return True
        return False

    def is_ignored(self, rel: str) -> bool:
        """是否位于扫描忽略目录内（忽略目录本身不算）。"""
        parts = Path(rel).parts
        return any(part in IGNORE_DIRS for part in parts[:-1])

    def visible_entries(self, directory: Path) -> list[Path]:
        """列出目录下参与工作的条目（按目录在前、名称排序）。"""
        try:
            entries = list(directory.iterdir())
        except OSError:
            return []
        out = []
        for entry in entries:
            if entry.name in IGNORE_DIRS:
                continue
            if self.is_sensitive(self.relative(entry)):
                continue
            out.append(entry)
        return sorted(out, key=lambda p: (p.is_file(), p.name.lower()))

    # ------------------------------------------------------------------ #
    # 读写与备份
    # ------------------------------------------------------------------ #

    def read_text(self, target: Path) -> str:
        """读取文本（换行归一化为 \\n），非文本或编码异常抛 ToolError。"""
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise ToolError("NOT_FOUND", f"读取失败：{self.relative(target)}", detail=str(exc)) from exc

        if b"\x00" in raw[:4096]:
            raise ToolError(
                "BINARY_FILE",
                f"该文件是二进制文件，无法作为文本读取：{self.relative(target)}",
                suggestion="请改为读取文本类文件",
            )

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                try:
                    text = raw.decode("gbk")
                except UnicodeDecodeError as exc:
                    raise ToolError(
                        "BINARY_FILE",
                        f"无法识别文件编码：{self.relative(target)}",
                        detail=str(exc),
                        suggestion="该文件不是 UTF-8 / GBK 文本，请放弃读取",
                    ) from exc

        return text.replace("\r\n", "\n").replace("\r", "\n")

    def detect_newline(self, target: Path) -> str:
        """探测原文件换行风格，写回时保持一致。"""
        try:
            raw = target.read_bytes()
        except OSError:
            return "\n"
        return "\r\n" if b"\r\n" in raw else "\n"

    def write_text(self, target: Path, text: str, *, newline: str = "\n") -> None:
        """落盘（保留原换行风格）。失败抛 ToolError。"""
        payload = text.replace("\n", "\r\n") if newline == "\r\n" else text
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="") as handle:
                handle.write(payload)
        except OSError as exc:
            raise ToolError(
                "WRITE_FAILED",
                f"写入失败：{self.relative(target)}",
                detail=str(exc),
                suggestion="检查该文件是否被其它程序占用，或改用其它文件名",
            ) from exc

    def backup(self, target: Path) -> str | None:
        """写入前备份原文件，返回相对工作区的备份路径；不备份时返回 None。"""
        if not self.backup_enabled or not target.is_file():
            return None

        rel = self.relative(target)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = self.workspace / ".changagent" / "backups" / stamp / rel

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, destination)
        except OSError as exc:
            raise ToolError(
                "WRITE_FAILED",
                "备份失败，已放弃本次写入",
                detail=str(exc),
                suggestion="请确认工作区可写（.changagent/backups 目录），或先在 .env 关闭 CHANG_AGENT_BACKUP",
            ) from exc

        return f".changagent/backups/{stamp}/{rel}"
