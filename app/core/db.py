"""
数据库模块：管理 SQLite 数据库的建表与读写。

为什么用 SQLite？
  - 整个数据库就是一个文件（data/quant.db），拷走这个文件就完成备份
  - 不需要安装任何数据库服务，Python 自带 sqlite3 模块
  - 数据量在百万行级别以内完全够用（MVP 阶段绰绰有余）

核心概念：
  - 表(table)：类似 Excel 的一个工作表
  - 字段(column)：表里的列
  - 主键(PRIMARY KEY)：唯一标识一行的字段组合，重复插入会覆盖而不是报错
"""

import sqlite3
from contextlib import contextmanager

from app.core.config import DB_PATH

# ---------------------------------------------------------------- 建表语句
SCHEMA = """
-- 基金基础信息表
CREATE TABLE IF NOT EXISTS fund_info (
    code        TEXT PRIMARY KEY,   -- 基金代码，如 110022
    name        TEXT,               -- 基金名称
    fund_type   TEXT,               -- 基金类型（股票型/混合型/债券型等）
    updated_at  TEXT                -- 本条记录更新时间
);

-- 基金净值历史表（核心数据）
CREATE TABLE IF NOT EXISTS fund_nav (
    fund_code   TEXT NOT NULL,      -- 基金代码
    trade_date  TEXT NOT NULL,      -- 净值日期 YYYY-MM-DD
    unit_nav    REAL,               -- 单位净值（1份基金值多少钱）
    acc_nav     REAL,               -- 累计净值（含历史分红，衡量真实收益）
    pct_change  REAL,               -- 当日涨跌幅 %
    PRIMARY KEY (fund_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_fund_nav_code ON fund_nav(fund_code);

-- 股票日线行情表
CREATE TABLE IF NOT EXISTS stock_daily (
    code        TEXT NOT NULL,      -- 股票代码，如 600519
    trade_date  TEXT NOT NULL,      -- 交易日期
    open        REAL,
    close       REAL,
    high        REAL,
    low         REAL,
    volume      REAL,               -- 成交量（手）
    amount      REAL,               -- 成交额（元）
    pct_change  REAL,               -- 涨跌幅 %
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_stock_daily_code ON stock_daily(code);

-- 数据同步日志：记录每次抓取的情况，便于排查"数据为什么没更新"
CREATE TABLE IF NOT EXISTS sync_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset     TEXT,               -- 数据集名称，如 fund_nav:110022
    rows        INTEGER,            -- 本次写入/更新的行数
    status      TEXT,               -- success / failed
    message     TEXT,               -- 备注或错误信息
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
"""


def get_conn() -> sqlite3.Connection:
    """打开数据库连接。每次调用返回一个新连接，用完要关闭。"""
    conn = sqlite3.connect(DB_PATH)
    # row_factory 让查询结果可以用 列名 访问，而不只是下标
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_session():
    """
    上下文管理器：自动提交和关闭连接。
    用法： with db_session() as conn: conn.execute(...)
    """
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """初始化数据库：执行建表语句。程序启动时调用一次即可（可重复调用）。"""
    with db_session() as conn:
        conn.executescript(SCHEMA)
