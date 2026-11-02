"""
一键启动脚本。

用法（在 quant-platform 目录下）：
    python run.py

它做三件事：
  1. 把当前目录加入模块搜索路径（这样 app 包能被找到）
  2. 初始化数据库
  3. 启动 Web 服务，监听 127.0.0.1:8000
"""

import sys
from pathlib import Path

# 把项目根目录加入 Python 的模块搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

from app.core.db import init_db  # noqa: E402

if __name__ == "__main__":
    init_db()
    print("=" * 56)
    print("  量化分析平台已启动")
    print("  网页界面: http://127.0.0.1:8000")
    print("  接口文档: http://127.0.0.1:8000/docs")
    print("  按 Ctrl+C 停止")
    print("=" * 56)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False, log_level="info")
