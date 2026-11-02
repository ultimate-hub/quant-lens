"""
一键打包成 Windows exe。

用法：
    python build_exe.py

产物：
    dist/量化终端/量化终端.exe   （one-folder 模式）

为什么用 one-folder 而不是 one-file？
    1. 启动快：one-file 每次启动都要把几十MB解压到临时目录，要等 5-10 秒
    2. 杀毒软件误报少：one-file 的自解压行为很像病毒
    3. 便于替换资源：改了页面不用重新打包，直接换 static 里的文件

代价：产物是一个文件夹，不能只拷一个 exe。分发时压缩成 zip 即可。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "量化终端"
SEP = ";" if sys.platform == "win32" else ":"


def main() -> int:
    # 清理旧产物
    for d in ("build", "dist"):
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--console",   # 保留控制台窗口（--windowed 在本机被静默拦截，见文档）
        "--name", NAME,
        "--add-data", f"static{SEP}static",
        # uvicorn 的循环/协议实现是动态加载的，必须显式声明
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.loops.asyncio",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.lifespan.off",
        # pywebview 在 Windows 上走 EdgeChromium(WebView2)
        "--hidden-import", "webview.platforms.edgechromium",
        "--hidden-import", "webview.platforms.winforms",
        "--hidden-import", "clr",
        # 排除用不到的大包，减小体积
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "tkinter",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PySide2",
        # 关键：排除 pywebview/pythonnet
        # 原因：pythonnet 在含中文的路径下无法加载 Python.Runtime.dll，
        # 打包进去只会让 exe 启动失败。桌面窗口改用"浏览器应用模式"实现。
        "--exclude-module", "webview",
        "--exclude-module", "clr",
        "--exclude-module", "pythonnet",
        "--exclude-module", "clr_loader",
        "desktop.py",
    ]

    print("=" * 60)
    print("  开始打包（首次约 3-8 分钟）")
    print("=" * 60)

    result = subprocess.run(args, cwd=str(ROOT))
    if result.returncode != 0:
        print("\n❌ 打包失败，请查看上面的错误信息")
        return result.returncode

    exe = ROOT / "dist" / NAME / f"{NAME}.exe"
    if exe.exists():
        size = sum(f.stat().st_size for f in (ROOT / "dist" / NAME).rglob("*")
                   if f.is_file()) / 1024 / 1024
        print("\n" + "=" * 60)
        print(f"  ✅ 打包成功")
        print(f"  程序: {exe}")
        print(f"  总大小: {size:.1f} MB")
        print(f"  分发方式: 把 dist/{NAME} 整个文件夹压缩成 zip 发给别人")
        print("=" * 60)
    else:
        print("\n⚠️ 打包完成但没找到 exe，请检查 dist 目录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
