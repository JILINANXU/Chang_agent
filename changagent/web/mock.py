"""Web 演示事件流（Mock 数据源）。

在接入真实模型之前，Web 界面用这里的时间线演示 Agent 的完整工作过程：
「思考 → 调工具 → 看结果 → 改文件 → 复核 → 汇报」。

事件协议见 docs/WEB.md，与真实 Agent 的输出格式**完全一致**。
后续接入模型时，只需把 server.py 里的数据源从 demo_events 换成 core.agent
的事件生成器，前端一行都不用改。
"""

from __future__ import annotations

import json
import time
from typing import Iterator

# 演示任务：最典型的「读 → 改 → 汇报」闭环
DEMO_TASK = "在 hello.py 中新增 greet(name) 函数，并让 __main__ 调用它"

# 修改前后的完整文件内容（用于生成 diff，与 examples/demo_workspace/hello.py 对应）
_FILE_BEFORE = '''"""示例工作区文件：供畅Agent 练手使用，随便改，改坏了也不影响主项目。"""


def main():
    print("hello")


if __name__ == "__main__":
    main()
'''

_FILE_AFTER = '''"""示例工作区文件：供畅Agent 练手使用，随便改，改坏了也不影响主项目。"""


def greet(name: str) -> str:
    """返回一句问候。"""
    return f"你好，{name}"


def main():
    print(greet("世界"))


if __name__ == "__main__":
    main()
'''

_BACKUP_PATH = ".changagent/backups/20260918-143055/hello.py"

# diff 片段：只展示变更区域及其上下文，前端按 t 字段着色（ctx / - / +）
_DIFF = [
    {"t": "ctx", "text": '"""示例工作区文件：供畅Agent 练手使用，随便改，改坏了也不影响主项目。"""'},
    {"t": "ctx", "text": ""},
    {"t": "ctx", "text": ""},
    {"t": "-", "text": "def main():"},
    {"t": "-", "text": '    print("hello")'},
    {"t": "+", "text": "def greet(name: str) -> str:"},
    {"t": "+", "text": '    """返回一句问候。"""'},
    {"t": "+", "text": '    return f"你好，{name}"'},
    {"t": "+", "text": ""},
    {"t": "+", "text": ""},
    {"t": "+", "text": "def main():"},
    {"t": "+", "text": '    print(greet("世界"))'},
    {"t": "ctx", "text": ""},
    {"t": "ctx", "text": ""},
    {"t": "ctx", "text": 'if __name__ == "__main__":'},
]

_LIST_DIR_OUTPUT = """[ok] demo_workspace/（深度 1）
├─ hello.py            9 行
└─ README.md         1.1 KB"""

_READ_BEFORE_OUTPUT = """[ok] hello.py（第 1–9 行 / 共 9 行）
1| \"\"\"示例工作区文件：供畅Agent 练手使用，随便改，改坏了也不影响主项目。\"\"\"
2| 
3| 
4| def main():
5|     print("hello")
6| 
7| 
8| if __name__ == "__main__":
9|     main()"""

_EDIT_RESULT = f"""[ok] 已修改 hello.py（1 处替换，第 4–8 行）
备份：{_BACKUP_PATH}"""

_READ_AFTER_OUTPUT = """[ok] hello.py（第 1–14 行 / 共 14 行）
1| \"\"\"示例工作区文件：供畅Agent 练手使用，随便改，改坏了也不影响主项目。\"\"\"
2| 
3| 
4| def greet(name: str) -> str:
5|     \"\"\"返回一句问候。\"\"\"
6|     return f"你好，{name}"
7| 
8| 
9| def main():
10|     print(greet("世界"))
11| 
12| 
13| if __name__ == "__main__":
14|     main()"""

_FINAL_ANSWER = f"""已完成：在 hello.py 中新增 greet(name) 函数，并改为由 main() 调用。

变更清单：
1. hello.py — 新增 greet(name) 函数（第 4–6 行） — 新增
2. hello.py — main() 中 print("hello") 改为 print(greet("世界")) — 修改

说明：原文件已备份至 {_BACKUP_PATH}，如需回滚直接覆盖即可。"""

_THINKING_1 = "先确认工作区结构，不猜路径。"
_THINKING_2 = "路径确认了，读一下 hello.py 的原文，拿到精确内容再动手。"
_THINKING_3 = "拿到原文了。用 edit_file 精确替换 main() 之前的片段，把 greet 函数插进去。"
_THINKING_4 = "改完重新读一遍复核，确认没有破坏原有结构。"


def _timeline(task: str) -> list[tuple[float, dict]]:
    """返回 (延迟秒数, 事件对象) 列表，模拟真实 Agent 的工作节奏。"""
    return [
        (0.30, {
            "type": "agent_start",
            "task": task,
            "model": "demo（未接入模型）",
            "workspace": "examples/demo_workspace",
        }),

        (0.60, {"type": "thinking", "text": _THINKING_1}),
        (0.35, {"type": "tool_call", "id": "c1", "name": "list_dir",
                "args": {"path": ".", "depth": 1}}),
        (0.45, {"type": "tool_result", "id": "c1", "ok": True, "elapsed_ms": 2,
                "content": _LIST_DIR_OUTPUT}),

        (0.50, {"type": "thinking", "text": _THINKING_2}),
        (0.35, {"type": "tool_call", "id": "c2", "name": "read_file",
                "args": {"path": "hello.py", "offset": 1, "limit": 200}}),
        (0.45, {"type": "tool_result", "id": "c2", "ok": True, "elapsed_ms": 1,
                "content": _READ_BEFORE_OUTPUT}),

        (0.55, {"type": "thinking", "text": _THINKING_3}),
        (0.40, {"type": "tool_call", "id": "c3", "name": "edit_file",
                "args": {
                    "path": "hello.py",
                    "old_string": 'def main():\n    print("hello")\n\n\nif __name__ == "__main__":',
                    "new_string": (
                        'def greet(name: str) -> str:\n'
                        '    """返回一句问候。"""\n'
                        '    return f"你好，{name}"\n\n\n'
                        'def main():\n'
                        '    print(greet("世界"))\n\n\n'
                        'if __name__ == "__main__":'
                    ),
                    "replace_all": False,
                }}),
        (0.55, {"type": "tool_result", "id": "c3", "ok": True, "elapsed_ms": 4,
                "content": _EDIT_RESULT}),
        (0.25, {"type": "file_change", "path": "hello.py", "added": 7, "removed": 2,
                "backup": _BACKUP_PATH, "diff": _DIFF}),

        (0.45, {"type": "thinking", "text": _THINKING_4}),
        (0.30, {"type": "tool_call", "id": "c4", "name": "read_file",
                "args": {"path": "hello.py", "offset": 1, "limit": 200}}),
        (0.40, {"type": "tool_result", "id": "c4", "ok": True, "elapsed_ms": 1,
                "content": _READ_AFTER_OUTPUT}),

        (0.50, {"type": "answer", "text": _FINAL_ANSWER}),
        (0.20, {"type": "done", "ok": True, "steps": 4,
                "usage": {"prompt_tokens": 4211, "completion_tokens": 388}}),
    ]


def _sse(payload: dict) -> str:
    """包装为 SSE 帧。"""
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def demo_events(task: str) -> Iterator[str]:
    """按时间线产出 SSE 事件流（阻塞式，逐条推送）。"""
    for delay, payload in _timeline(task):
        time.sleep(delay)
        yield _sse(payload)


def file_before() -> str:
    return _FILE_BEFORE


def file_after() -> str:
    return _FILE_AFTER
