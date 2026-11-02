"""
心跳追踪：判断前端窗口是否还活着。

为什么需要？
    桌面版用"浏览器应用模式"开窗口。如果 Edge 已经开着别的窗口，
    我们再启动 msedge.exe --app=... 时，启动器进程会**立刻退出**
    （它只是把命令转交给已有实例），窗口却还开着。
    如果这时按"启动器进程结束"就关闭后端服务，窗口立刻变白。

    正确做法：让前端页面定时 ping 服务端，服务端超过一定时间没收到 ping
    就认为窗口已关闭，然后退出。
"""

from __future__ import annotations

import time

_last_seen: float = 0.0
_first_seen: float = 0.0


def touch() -> None:
    """前端每次请求健康检查时调用。"""
    global _last_seen, _first_seen
    now = time.time()
    if _first_seen == 0.0:
        _first_seen = now
    _last_seen = now


def last_seen() -> float:
    """最后一次心跳的时间戳（0 表示还没收到过）。"""
    return _last_seen


def has_connected() -> bool:
    return _first_seen > 0.0


def idle_seconds() -> float:
    """距离上次心跳过了多少秒。"""
    if _last_seen == 0.0:
        return 0.0
    return time.time() - _last_seen
