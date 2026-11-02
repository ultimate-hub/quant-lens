"""
配置模块：集中管理路径和常量。

为什么要有这个文件？
  软件里到处都要用到"数据库在哪""数据存哪个文件夹"这类信息。
  如果散落在每个文件里，将来搬家（比如换服务器）就要改几十处。
  集中放在这里，改一处即可 —— 这叫"配置与代码分离"。
"""

from pathlib import Path
import sys

# 判断是否运行在 PyInstaller 打包后的 exe 里
FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # 打包模式：
    #   sys._MEIPASS       -> 解压出来的只读资源目录（static 在这里）
    #   sys.executable.parent -> exe 所在目录（可写，数据库放这里）
    # 为什么不能都放 _MEIPASS？因为它是临时目录，程序退出就删了，
    # 数据库和 AI 渠道配置会丢失。
    RESOURCE_DIR = Path(sys._MEIPASS)                    # noqa: SLF001
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    # 源码模式：app/core/config.py -> app/core -> app -> quant-platform
    RESOURCE_DIR = Path(__file__).resolve().parent.parent.parent
    BASE_DIR = RESOURCE_DIR

DATA_DIR = BASE_DIR / "data"              # 数据文件（可写）
STATIC_DIR = RESOURCE_DIR / "static"      # 前端页面（只读资源）
DB_PATH = DATA_DIR / "quant.db"           # SQLite 数据库

# 确保目录存在（exist_ok=True 表示已存在也不报错）
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 无风险利率，用于计算夏普比率。取 2%（近似一年期国债收益率）
RISK_FREE_RATE = 0.02

# 一年按 252 个交易日算（A股一年约 244-250 个交易日，金融界惯例用 252）
TRADING_DAYS_PER_YEAR = 252

# 网络请求超时秒数
HTTP_TIMEOUT = 20
