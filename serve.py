"""
只启动 Web 服务，不打开任何窗口。

给 启动.bat 用的：bat 负责起服务、等就绪、再开浏览器。
这样职责单一，出问题也容易定位：
    服务起不来 -> 看这个窗口的输出
    浏览器没开 -> 地址已经打印出来了，手动粘贴即可

用法：
    python serve.py            监听 8765
    python serve.py 9000       指定端口
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from app.core.db import init_db

HOST = "127.0.0.1"


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

    print("=" * 58)
    print("  量化终端 v2 服务端")
    print("=" * 58)

    init_db()
    print(f"  数据库就绪")

    from app.main import app as fastapi_app

    print(f"  服务地址: http://{HOST}:{port}")
    print(f"  接口文档: http://{HOST}:{port}/docs")
    print()
    print("  请用浏览器打开上面的「服务地址」")
    print("  关闭本窗口即停止服务")
    print("=" * 58)
    print()

    uvicorn.run(fastapi_app, host=HOST, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已停止")
