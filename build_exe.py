"""
一键打包成 Windows 免安装版。

用法：
    python build_exe.py

产物：
    dist/QuantLens/QuantLens.exe   （one-folder 模式，整个文件夹压缩成 zip 分发）

为什么用 one-folder 而不是 one-file？
    1. 启动快：one-file 每次启动都要把 100+MB 解压到临时目录，要等十几秒
    2. 杀毒误报少：one-file 的自解压行为很像病毒
    3. 便于替换资源：改了前端页面不用重新打包，直接换 static 里的文件

为什么用 ASCII 名字 QuantLens 而不是「量镜」？
    exe 路径里带中文，在某些机器上会被安全软件误判，或者被别的工具当成
    编码问题处理。界面里的中文名不受影响（那是 UI 里的字，不是路径）。

打包 PyQt6 + QtWebEngine 的关键坑：
    1. QtWebEngineProcess.exe 必须被打进去，而且要放在正确位置。
       这个由 PyInstaller 的 PyQt6 hook 处理，但必须让 hook 真的被触发
       ——所以 desktop_qt.py 里要有明确的 `from PyQt6.QtWebEngineWidgets import ...`
    2. Qt 的资源文件（qtwebengine_resources*.pak、icudtl.dat）要一起带上。
       用 --collect-all PyQt6 最稳，代价是体积大。
    3. --windowed 模式下 sys.stdout 是 None。desktop_qt.py 已经在最顶部
       做过防御（否则 uvicorn 初始化日志会崩在 sys.stdout.isatty()）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "QuantLens"
SEP = ";" if sys.platform == "win32" else ":"


def trim(out_dir: Path) -> tuple[float, list[str]]:
    """
    删掉运行时用不到的资源。

    这两类占了 130MB 左右，而且删了完全不影响功能：
      1. qtwebengine_devtools_resources*.pak   —— 开发者工具的资源包。
         我们不给用户开 F12 调试面板，用不上。光这一项就 83MB。
      2. Qt 翻译文件里的非中文/英文语言包 —— 163 个 .qm 文件共 50MB。
         界面文字是我们自己写在网页里的，Qt 自带翻译只影响极少数系统对话框。

    返回：(节省的 MB, 删掉的类别说明)
    """
    saved = 0
    notes: list[str] = []

    devtools = list(out_dir.rglob("qtwebengine_devtools_resources*.pak"))
    if devtools:
        size = sum(f.stat().st_size for f in devtools)
        for f in devtools:
            f.unlink(missing_ok=True)
        saved += size
        notes.append(f"devtools 资源 {len(devtools)} 个（{size / 1024 / 1024:.1f} MB）")

    removed = 0
    trans_size = 0
    for td in out_dir.rglob("translations"):
        if not td.is_dir():
            continue
        for f in td.rglob("*.qm"):
            name = f.name
            # 保留中文和英文
            if "_zh_CN" in name or "_zh_TW" in name or "_en" in name:
                continue
            trans_size += f.stat().st_size
            f.unlink(missing_ok=True)
            removed += 1
    if removed:
        saved += trans_size
        notes.append(f"非中英文翻译 {removed} 个（{trans_size / 1024 / 1024:.1f} MB）")

    return saved / 1024 / 1024, notes


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
        "--windowed",              # 无控制台窗口（GUI 程序）
        "--name", NAME,
        # 前端页面与图表库
        "--add-data", f"static{SEP}static",

        # ---- uvicorn：循环/协议/lifespan 都是动态加载，必须显式声明 ----
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.loops.asyncio",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.lifespan.off",

        # ---- 本项目里「函数内部 import」的模块 ----
        # PyInstaller 一般能扫到函数内的 import，但业务模块漏一个就要报错，
        # 这里显式列出来，避免打了半天包才发现某个接口 500。
        "--hidden-import", "app.concepts.seed",
        "--hidden-import", "app.concepts.store",
        "--hidden-import", "app.userdata.store",
        "--hidden-import", "app.library.knowledge",
        "--hidden-import", "app.library.catalog",
        "--hidden-import", "app.analytics.forecast",
        "--hidden-import", "app.analytics.advice",
        "--hidden-import", "app.analytics.garch",
        "--hidden-import", "app.news.aggregator",
        "--hidden-import", "app.news.ai_summary",
        "--hidden-import", "app.news.sector_map",
        "--hidden-import", "app.news.sources",

        # ---- Qt / WebEngine ----
        # 注意：这里**不能**用 --collect-all PyQt6。
        # 实测那样会把 QtQuick3D / Multimedia / Designer / Pdf / Sql 等
        # 十几个用不到的模块全打进去，体积从 300MB 直接涨到 650MB。
        # PyInstaller 的 hook-PyQt6.* 会按「实际 import 到的模块」收集对应的
        # DLL 和资源，这才是正确做法。
        "--hidden-import", "PyQt6.QtWebEngineWidgets",
        "--hidden-import", "PyQt6.QtWebEngineCore",
        "--hidden-import", "PyQt6.QtWebChannel",
        "--hidden-import", "PyQt6.QtNetwork",
        "--hidden-import", "PyQt6.QtPrintSupport",

        # ---- 排除用不到的大包 ----
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "sklearn",
        "--exclude-module", "tkinter",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PySide2",
        "--exclude-module", "PySide6",
        "--exclude-module", "webview",
        "--exclude-module", "clr",
        "--exclude-module", "pythonnet",
        "--exclude-module", "clr_loader",
        "--exclude-module", "IPython",
        "--exclude-module", "pytest",
        # pandas 会顺带拉进这几个，但我们从不做 Excel / HTML 解析
        "--exclude-module", "openpyxl",
        "--exclude-module", "lxml",
        "--exclude-module", "bs4",
        "--exclude-module", "html5lib",

        # ---- 排除用不到的 Qt 模块（体积大户）----
        "--exclude-module", "PyQt6.QtQuick",
        "--exclude-module", "PyQt6.QtQml",
        "--exclude-module", "PyQt6.QtQuick3D",
        "--exclude-module", "PyQt6.QtQuickWidgets",
        "--exclude-module", "PyQt6.QtMultimedia",
        "--exclude-module", "PyQt6.QtMultimediaWidgets",
        "--exclude-module", "PyQt6.QtDesigner",
        "--exclude-module", "PyQt6.QtHelp",
        "--exclude-module", "PyQt6.QtPdf",
        "--exclude-module", "PyQt6.QtPdfWidgets",
        "--exclude-module", "PyQt6.QtSql",
        "--exclude-module", "PyQt6.QtTest",
        "--exclude-module", "PyQt6.QtBluetooth",
        "--exclude-module", "PyQt6.QtNfc",
        "--exclude-module", "PyQt6.QtPositioning",
        "--exclude-module", "PyQt6.QtSensors",
        "--exclude-module", "PyQt6.QtSerialPort",
        "--exclude-module", "PyQt6.QtSpatialAudio",
        "--exclude-module", "PyQt6.QtStateMachine",
        "--exclude-module", "PyQt6.QtRemoteObjects",
        "--exclude-module", "PyQt6.QtTextToSpeech",
        "--exclude-module", "PyQt6.QtWebEngineQuick",
        "--exclude-module", "PyQt6.QtWebSockets",
        "--exclude-module", "PyQt6.QAxContainer",
        "--exclude-module", "PyQt6.QtDBus",

        "desktop_qt.py",
    ]

    print("=" * 62)
    print(f"  开始打包 {NAME}（首次约 5-10 分钟）")
    print("=" * 62)

    result = subprocess.run(args, cwd=str(ROOT))
    if result.returncode != 0:
        print("\n❌ 打包失败，请查看上面的错误信息")
        return result.returncode

    out_dir = ROOT / "dist" / NAME
    exe = out_dir / f"{NAME}.exe"
    if not exe.exists():
        print("\n⚠️ 打包完成但没找到 exe，请检查 dist 目录")
        return 1

    # ---- 裁掉用不到的资源 ----
    saved_mb, notes = trim(out_dir)
    if notes:
        print(f"\n  已裁掉 {saved_mb:.1f} MB 无用资源:")
        for n in notes:
            print(f"    - {n}")

    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1024 / 1024

    # 检查关键文件是否真的打进去了
    checks = {
        "QtWebEngineProcess.exe": list(out_dir.rglob("QtWebEngineProcess.exe")),
        "qtwebengine_resources.pak": list(out_dir.rglob("qtwebengine_resources.pak")),
        "icudtl.dat": list(out_dir.rglob("icudtl.dat")),
        "static/app/index.html": list(out_dir.rglob("index.html")),
    }

    print("\n" + "=" * 62)
    print("  ✅ 打包成功")
    print(f"  程序: {exe}")
    print(f"  总大小: {size:.1f} MB")
    print()
    print("  关键文件检查:")
    for name, found in checks.items():
        print(f"    {'✓' if found else '✗'} {name}"
              + (f"  ({len(found)} 个)" if found else "  ← 缺失！"))
    print()
    print(f"  分发方式: 把 dist/{NAME} 整个文件夹压缩成 zip")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())