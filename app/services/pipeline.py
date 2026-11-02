"""
数据管道模块：把"网络上的数据"变成"数据库里的数据"，以及从数据库读取。

为什么要有管道这一层？
  如果网页每次查询都去东方财富抓一遍，会有三个问题：
    1. 慢（一次请求几百毫秒到几秒）
    2. 被封（请求太频繁会被限制）
    3. 不稳定（对方服务器挂了你的软件就不能用了）
  正确做法：定时把数据抓到本地数据库（"同步"），网页只查本地库，毫秒级响应。

核心概念：幂等（idempotent）
  同一份数据重复同步多次，结果完全一样（靠 INSERT OR REPLACE + 主键实现）。
  这样"同步失败重跑一次"永远安全，不会产生重复数据。
"""

import datetime as dt

import pandas as pd

from app.core.db import db_session
from app.services import datasource


# ==================================================================
# 同步（网络 -> 数据库）
# ==================================================================
def sync_fund(code: str) -> dict:
    """同步一只基金：基础信息 + 全部历史净值。返回本次同步结果摘要。"""
    code = str(code).strip()
    try:
        info = datasource.fetch_fund_info(code)
        df = datasource.fetch_fund_nav(code)
    except datasource.DataSourceError as exc:
        _log_sync(f"fund:{code}", 0, "failed", str(exc))
        raise

    rows = [
        (code, r.trade_date, r.unit_nav, r.acc_nav, r.pct_change)
        for r in df.itertuples(index=False)
    ]

    with db_session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fund_info(code, name, fund_type, updated_at) VALUES (?,?,?,?)",
            (code, info["name"], info["fund_type"],
             dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO fund_nav"
            "(fund_code, trade_date, unit_nav, acc_nav, pct_change) VALUES (?,?,?,?,?)",
            rows,
        )

    _log_sync(f"fund:{code}", len(rows), "success", info["name"])
    return {
        "code": code,
        "name": info["name"],
        "fund_type": info["fund_type"],
        "rows": len(rows),
        "start_date": df["trade_date"].iloc[0],
        "end_date": df["trade_date"].iloc[-1],
    }


def sync_stock(code: str, beg: str = "20200101") -> dict:
    """同步一只股票的日线（前复权）。"""
    code = str(code).strip()
    try:
        df = datasource.fetch_stock_kline(code, beg=beg)
    except datasource.DataSourceError as exc:
        _log_sync(f"stock:{code}", 0, "failed", str(exc))
        raise

    rows = [
        (code, r.trade_date, r.open, r.close, r.high, r.low,
         r.volume, r.amount, r.pct_change)
        for r in df.itertuples(index=False)
    ]
    with db_session() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO stock_daily"
            "(code, trade_date, open, close, high, low, volume, amount, pct_change)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )

    name = df.attrs.get("name", "")
    _log_sync(f"stock:{code}", len(rows), "success", name)
    return {"code": code, "name": name, "rows": len(rows),
            "start_date": df["trade_date"].iloc[0], "end_date": df["trade_date"].iloc[-1]}


def _log_sync(dataset: str, rows: int, status: str, message: str) -> None:
    """写一条同步日志。"""
    with db_session() as conn:
        conn.execute(
            "INSERT INTO sync_log(dataset, rows, status, message) VALUES (?,?,?,?)",
            (dataset, rows, status, message[:500]),
        )


# ==================================================================
# 读取（数据库 -> pandas）
# ==================================================================
def load_fund_info(code: str) -> dict | None:
    with db_session() as conn:
        row = conn.execute(
            "SELECT code, name, fund_type, updated_at FROM fund_info WHERE code=?", (code,)
        ).fetchone()
    return dict(row) if row else None


def load_fund_nav(code: str, start: str | None = None, end: str | None = None,
                  use_acc_nav: bool = False) -> pd.Series:
    """
    读取基金净值序列（索引为日期）。

    参数 use_acc_nav：
      False -> 用单位净值（适合看短期走势）
      True  -> 用累计净值（含分红再投，算长期收益更准确）
    """
    sql = "SELECT trade_date, unit_nav, acc_nav FROM fund_nav WHERE fund_code=?"
    params: list = [code]
    if start:
        sql += " AND trade_date >= ?"
        params.append(start)
    if end:
        sql += " AND trade_date <= ?"
        params.append(end)
    sql += " ORDER BY trade_date"

    with db_session() as conn:
        df = pd.read_sql_query(sql, conn, params=params)

    if df.empty:
        return pd.Series(dtype="float64")

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    col = "acc_nav" if use_acc_nav else "unit_nav"
    s = df.set_index("trade_date")[col].astype(float).dropna()
    s.name = code
    return s


def load_fund_nav_frame(code: str) -> pd.DataFrame:
    """读取完整净值表（含涨跌幅），供网页画图用。"""
    with db_session() as conn:
        df = pd.read_sql_query(
            "SELECT trade_date, unit_nav, acc_nav, pct_change FROM fund_nav"
            " WHERE fund_code=? ORDER BY trade_date",
            conn, params=[code],
        )
    return df


def load_stock_close(code: str, start: str | None = None) -> pd.Series:
    """读取股票收盘价序列。"""
    sql = "SELECT trade_date, close FROM stock_daily WHERE code=?"
    params: list = [code]
    if start:
        sql += " AND trade_date >= ?"
        params.append(start)
    sql += " ORDER BY trade_date"
    with db_session() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return pd.Series(dtype="float64")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    s = df.set_index("trade_date")["close"].astype(float)
    s.name = code
    return s


def has_fund_data(code: str) -> bool:
    """判断本地库里是否已有这只基金的数据。"""
    with db_session() as conn:
        row = conn.execute(
            "SELECT COUNT(1) AS n FROM fund_nav WHERE fund_code=?", (code,)
        ).fetchone()
    return bool(row and row["n"] > 0)
