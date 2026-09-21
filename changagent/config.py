"""配置加载：读取 .env / 环境变量，产出 AppConfig 与 LLMConfig。

配置优先级：环境变量 > .env 文件 > 代码默认值。
所有默认值集中在本文件，其它模块（含 web 层、run.py）一律从这里取，避免多处漂移。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# 项目根：changagent/config.py → 上一级
PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_WORKSPACE = "examples/demo_workspace"
DEFAULT_MAX_STEPS = 25
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TOKEN_BUDGET = 32000
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_RETRIES = 3

_TRUE_WORDS = {"1", "true", "yes", "on", "y", "是", "开"}
_FALSE_WORDS = {"0", "false", "no", "off", "n", "否", "关"}


# --------------------------------------------------------------------------- #
# 读取工具
# --------------------------------------------------------------------------- #

def _get(name: str, default: str = "") -> str:
    """取字符串配置：空串视为未配置，回落默认值。"""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _get_int(name: str, default: int) -> int:
    raw = _get(name, "")
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = _get(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name, "").lower()
    if raw in _TRUE_WORDS:
        return True
    if raw in _FALSE_WORDS:
        return False
    return default


# --------------------------------------------------------------------------- #
# .env 加载
# --------------------------------------------------------------------------- #

def _parse_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析：支持 KEY=VALUE、# 注释、成对引号。不处理多行值。"""
    data: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return data

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            data[key] = value
    return data


def load_env_file(path: str | Path | None = None) -> Path | None:
    """把 .env 读进 os.environ。已存在的环境变量优先，不被覆盖。

    优先用 python-dotenv；未安装时退回内置解析器，保证零依赖也能启动。
    """
    env_path = Path(path) if path else PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return None

    try:
        from dotenv import load_dotenv
    except ImportError:
        for key, value in _parse_env_file(env_path).items():
            os.environ.setdefault(key, value)
    else:
        load_dotenv(env_path, override=False)

    return env_path


# --------------------------------------------------------------------------- #
# 配置对象
# --------------------------------------------------------------------------- #

@dataclass
class LLMConfig:
    """模型接入配置（OpenAI 兼容协议）。"""

    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    temperature: float = DEFAULT_TEMPERATURE
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES

    @property
    def configured(self) -> bool:
        """是否具备调用真实模型的最小条件。"""
        return bool(self.api_key)

    def describe(self) -> str:
        if not self.configured:
            return "未配置（缺少 API Key）"
        return f"{self.model} @ {self.base_url}"


@dataclass
class AppConfig:
    """应用级配置。"""

    workspace: Path
    llm: LLMConfig = field(default_factory=LLMConfig)
    max_steps: int = DEFAULT_MAX_STEPS
    token_budget: int = DEFAULT_TOKEN_BUDGET
    enable_shell: bool = False
    auto_approve: bool = False
    backup: bool = True
    web_mode: str = "demo"

    @property
    def runtime_dir(self) -> Path:
        """运行数据目录（会话、备份），位于工作区内。"""
        return self.workspace / ".changagent"

    def ensure_dirs(self) -> None:
        """确保运行目录存在（失败不抛异常，交由后续操作报错）。"""
        try:
            (self.runtime_dir / "sessions").mkdir(parents=True, exist_ok=True)
            (self.runtime_dir / "backups").mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def resolve_workspace(raw: str | Path | None = None) -> Path:
    """把工作区配置解析为绝对路径：相对路径一律按项目根解析。"""
    value = str(raw).strip() if raw else _get("CHANG_AGENT_WORKSPACE", DEFAULT_WORKSPACE)
    if not value:
        value = DEFAULT_WORKSPACE
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def load_config(workspace: str | Path | None = None, *, load_env: bool = True) -> AppConfig:
    """加载完整配置。workspace 显式传入时优先于环境变量。"""
    if load_env:
        load_env_file()

    llm = LLMConfig(
        api_key=_get("CHANG_AGENT_API_KEY", ""),
        base_url=_get("CHANG_AGENT_BASE_URL", DEFAULT_BASE_URL),
        model=_get("CHANG_AGENT_MODEL", DEFAULT_MODEL),
        temperature=_get_float("CHANG_AGENT_TEMPERATURE", DEFAULT_TEMPERATURE),
        timeout=_get_float("CHANG_AGENT_TIMEOUT", DEFAULT_TIMEOUT),
        max_retries=_get_int("CHANG_AGENT_MAX_RETRIES", DEFAULT_MAX_RETRIES),
    )

    return AppConfig(
        workspace=resolve_workspace(workspace),
        llm=llm,
        max_steps=max(1, _get_int("CHANG_AGENT_MAX_STEPS", DEFAULT_MAX_STEPS)),
        token_budget=max(2000, _get_int("CHANG_AGENT_TOKEN_BUDGET", DEFAULT_TOKEN_BUDGET)),
        enable_shell=_get_bool("CHANG_AGENT_ENABLE_SHELL", False),
        auto_approve=_get_bool("CHANG_AGENT_AUTO_APPROVE", False),
        backup=_get_bool("CHANG_AGENT_BACKUP", True),
        web_mode=_get("CHANG_AGENT_WEB_MODE", "demo").lower(),
    )
