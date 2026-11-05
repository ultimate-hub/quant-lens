"""
真正的独立桌面窗口（PyQt6 + QtWebEngine）。

和前几版的区别：
    之前用系统浏览器（Chrome/Edge 的 --app 参数）显示界面 —— 那不算独立软件，
    而且 Chrome 156 起 --app 失效、Edge 有实例时也开不出来。

    这一版用 **QtWebEngine**：Qt 自带的 Chromium 内核，嵌在程序进程里。
        - 不依赖系统装了什么浏览器
        - 没有地址栏、没有标签页、没有书签栏
        - 不受中文路径影响（之前的 pythonnet 就是死在这）
        - 打包后就是一个真正的 .exe

架构没变，还是"本地服务 + 界面"，只是把"显示界面"这件事
从"调用外部浏览器"换成了"程序内嵌浏览器内核"。

用法：
    python desktop_qt.py
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ------------------------------------------------------------------
# 关键修复：pythonw / 打包后的窗口程序里 sys.stdout 和 sys.stderr 是 None，
# 而 uvicorn 初始化日志时会调用 sys.stdout.isatty()，直接崩：
#     AttributeError: 'NoneType' object has no attribute 'isatty'
# 必须在任何会用到 stdout 的模块之前把它们指向一个安全位置。
# ------------------------------------------------------------------
if sys.stdout is None or sys.stderr is None:
    _null_stream = open(os.devnull, "w", encoding="utf-8", errors="ignore")
    if sys.stdout is None:
        sys.stdout = _null_stream
    if sys.stderr is None:
        sys.stderr = _null_stream

# QtWebEngine 的一些必要设置，必须在导入 PyQt 之前设好
#
# 关于 --no-sandbox（打包版）：
#   实测在某些环境里（受管企业机、虚拟机、容器，以及受限的沙箱环境），
#   Chromium 的渲染进程无法初始化。表现是：
#       窗口能打开，但页面立刻加载失败（loadFinished(False)），
#       任务管理器里看不到 QtWebEngineProcess 子进程。
#   加上 --no-sandbox 后正常。
#
#   风险评估：本程序只加载 http://127.0.0.1:<port> 上我们自己产出的页面，
#   不渲染任何用户提供的 HTML。沙箱主要防的是「不可信网页」，
#   这个场景在本程序里不存在，因此关闭沙箱的额外风险极低，
#   换来的是在各种机器上都能正常打开。
_flags = "--disable-gpu-driver-bug-workarounds --enable-features=NetworkService"
if getattr(sys, "frozen", False):
    _flags += " --no-sandbox"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", _flags)
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

HOST = "127.0.0.1"
DEFAULT_PORT = 8765


# ------------------------------------------------------------------
# 日志（窗口程序没有控制台）
# ------------------------------------------------------------------
_LOG_PATH = (Path(sys.executable).resolve().parent / "运行日志.log"
             if getattr(sys, "frozen", False)
             else Path(__file__).resolve().parent / "运行日志.log")


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass
    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            stream.write(line + "\n")
            stream.flush()
        except Exception:  # noqa: BLE001
            pass


# ------------------------------------------------------------------
# 本地服务
# ------------------------------------------------------------------
def find_free_port(start: int = DEFAULT_PORT, tries: int = 30) -> int:
    for offset in range(tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"{start}-{start + tries} 端口全部被占用")


def start_server(port: int):
    import uvicorn
    from app.main import app as fastapi_app

    config = uvicorn.Config(
        fastapi_app, host=HOST, port=port,
        log_level="warning", access_log=False,
        log_config=None,          # 不开日志配置，避免依赖 stdout（窗口程序里是 None）
    )
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    return server


def wait_healthy(port: int, timeout: float = 40.0) -> bool:
    url = f"http://{HOST}:{port}/api/v2/ping"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------
def main() -> int:
    log("=" * 50)
    log("量镜 QuantLens 启动中…")

    from app.core.db import init_db
    init_db()
    log("数据库就绪")

    port = find_free_port()
    server = start_server(port)
    log(f"本地服务端口 {port}，等待就绪…")

    if not wait_healthy(port):
        log("服务启动超时")
        _fatal("服务启动失败，请查看 运行日志.log")
        return 1
    log("服务就绪")

    from PyQt6.QtCore import Qt, QUrl
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    app = QApplication(sys.argv)
    app.setApplicationName("量镜")
    app.setApplicationDisplayName("量镜 QuantLens")

    url = f"http://{HOST}:{port}"

    view = QWebEngineView()
    view.setUrl(QUrl(url))

    window = QMainWindow()
    window.setCentralWidget(view)
    window.setWindowTitle("量镜 QuantLens")
    window.resize(1480, 960)
    window.setMinimumSize(1100, 720)

    # 诊断：渲染进程挂掉时把原因记下来。
    # 没有这条日志的话，"页面加载失败"完全查不出原因 ——
    # 是网络问题、渲染进程崩溃、还是被安全软件拦了，看不出来。
    try:
        view.page().renderProcessTerminated.connect(
            lambda status, code: log(f"渲染进程异常退出 status={status} exitCode={code}"))
    except Exception:  # noqa: BLE001
        pass

    # 页面加载失败时给提示，而不是一片空白
    def on_load_finished(ok: bool) -> None:
        if ok:
            log("页面加载完成")
            return

        log("页面加载失败")
        log(f"URL: {url}")
        log(f"Chromium 参数: {os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '(未设置)')}")
        QMessageBox.warning(
            window, "界面加载失败",
            f"页面没能加载出来。\n\n"
            f"常见原因：\n"
            f"1. 渲染进程被安全软件拦截（在受限的机器上常见）\n"
            f"2. 端口 {port} 上有别的程序在跑\n"
            f"3. 缺少 Qt 运行库\n\n"
            f"你可以先用浏览器访问下面的地址，确认服务本身是好的：\n"
            f"{url}\n\n"
            f"详细信息见同目录的 运行日志.log。")

    view.loadFinished.connect(on_load_finished)

    window.show()
    log("窗口已显示")

    exit_code = app.exec()

    server.should_exit = True
    log(f"程序退出（code={exit_code}）")
    return exit_code


def _fatal(text: str) -> None:
    """没有 GUI 时用系统消息框报错。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "量镜 QuantLens", 0x10)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        import traceback
        log("启动失败:\n" + traceback.format_exc())
        _fatal("启动失败，详情见 运行日志.log")
        sys.exit(1)
