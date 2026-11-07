"""
持仓日志。

设计目标：像记账一样记下「什么时候、多少钱、买了多少」，
然后自动算出现在值多少、赚了还是亏了、占总仓多少。

几个设计决定：

1. **同一只股票可以有多笔记录**
   分批建仓是常态，强行合并成一笔会丢失买入时间信息。
   所以不做唯一约束，每笔是独立一条记录。

2. **股数用 REAL 而不是 INTEGER**
   A 股是整手，但港股有碎股、美股可以买零股、场内基金按份。
   用浮点兼容所有情况，显示时按市场格式化。

3. **允许设「目标配比」**
   实际占比 = 市值 / 总市值；目标占比 = 用户填的数。
   两者相减就能看出超配/低配多少 —— 这是调仓的依据。

4. **盈亏只算浮动盈亏**
   没有实现卖出记录，所以不算已实现盈亏。
   这样定位清晰：这是一个「当前持仓快照」工具，不是完整的交易账本。
"""

from __future__ import annotations

import time

from app.core.db import db_session

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    name         TEXT DEFAULT '',
    buy_date     TEXT NOT NULL,
    buy_price    REAL NOT NULL,
    shares       REAL NOT NULL,
    target_ratio REAL,                 -- 目标占比（百分数，25 表示 25%）
    note         TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_pos_symbol ON positions(symbol);
"""


def init() -> None:
    with db_session() as conn:
        conn.executescript(SCHEMA)


# ==================================================================
# 增删改
# ==================================================================
def add(symbol: str, buy_date: str, buy_price: float, shares: float,
        name: str = "", target_ratio: float | None = None,
        note: str = "") -> int:
    init()
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("股票代码不能为空")
    if buy_price is None or buy_price <= 0:
        raise ValueError("买入价必须大于 0")
    if shares is None or shares <= 0:
        raise ValueError("数量必须大于 0")
    if not buy_date:
        raise ValueError("买入日期不能为空")

    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO positions(symbol, name, buy_date, buy_price, shares,"
            " target_ratio, note) VALUES (?,?,?,?,?,?,?)",
            (symbol, name, buy_date, float(buy_price), float(shares),
             target_ratio, note))
        return int(cur.lastrowid)


def update(pid: int, **fields) -> None:
    init()
    allowed = ("symbol", "name", "buy_date", "buy_price", "shares", "target_ratio", "note")
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k}=?")
            params.append(v)
    if not sets:
        return
    params.append(pid)
    with db_session() as conn:
        conn.execute(f"UPDATE positions SET {','.join(sets)} WHERE id=?", params)


def remove(pid: int) -> None:
    init()
    with db_session() as conn:
        conn.execute("DELETE FROM positions WHERE id=?", (pid,))


def clear_all() -> None:
    init()
    with db_session() as conn:
        conn.execute("DELETE FROM positions")


# ==================================================================
# 查询 + 计算
# ==================================================================
def list_positions() -> dict:
    """
    返回持仓明细和汇总。

    需要联网抓当前价；抓不到的行会标记 price_ok=False，
    市值按买入价估，并在界面上提示 —— 宁可显示「不确定」也不给错数。
    """
    from app import market_overview

    init()
    with db_session() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, symbol, name, buy_date, buy_price, shares,"
            " target_ratio, note, created_at FROM positions ORDER BY buy_date, id"
        ).fetchall()]

    if not rows:
        return {"items": [], "summary": _empty_summary(), "by_symbol": []}

    symbols = sorted({r["symbol"] for r in rows})
    quotes = market_overview.fetch_quotes(symbols)

    items = []
    for r in rows:
        q = quotes.get(r["symbol"])
        cost = r["buy_price"] * r["shares"]

        if q and q["price"] > 0:
            price = q["price"]
            price_ok = True
            change = q["change"]
        else:
            price = r["buy_price"]
            price_ok = False
            change = None

        value = price * r["shares"]
        pnl = value - cost

        hold_days = None
        try:
            import datetime as dt
            d0 = dt.date.fromisoformat(r["buy_date"])
            hold_days = (dt.date.today() - d0).days
        except (ValueError, TypeError):
            pass

        items.append({
            **r,
            "current_price": round(price, 4),
            "price_ok": price_ok,
            "change": change,
            "cost": round(cost, 2),
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / cost * 100, 2) if cost > 0 else 0.0,
            "hold_days": hold_days,
            "name": q["name"] if q and q.get("name") else r["name"],
        })

    total_value = sum(i["value"] for i in items)
    total_cost = sum(i["cost"] for i in items)
    total_pnl = total_value - total_cost

    # 按股票汇总（同一只多笔合并）
    by_sym: dict[str, dict] = {}
    for i in items:
        b = by_sym.setdefault(i["symbol"], {
            "symbol": i["symbol"], "name": i["name"],
            "shares": 0.0, "cost": 0.0, "value": 0.0,
            "current_price": i["current_price"], "price_ok": i["price_ok"],
            "lots": 0, "first_buy": i["buy_date"], "change": i["change"],
            "_target": 0.0,
        })
        b["shares"] += i["shares"]
        b["cost"] += i["cost"]
        b["value"] += i["value"]
        b["lots"] += 1
        b["first_buy"] = min(b["first_buy"], i["buy_date"])
        if i["target_ratio"]:
            b["_target"] += i["target_ratio"]

    by_list = []
    for b in by_sym.values():
        b["pnl"] = round(b["value"] - b["cost"], 2)
        b["pnl_pct"] = round(b["pnl"] / b["cost"] * 100, 2) if b["cost"] > 0 else 0.0
        b["avg_cost"] = round(b["cost"] / b["shares"], 4) if b["shares"] > 0 else 0.0
        b["weight"] = round(b["value"] / total_value * 100, 2) if total_value > 0 else 0.0
        b["target_ratio"] = round(b.pop("_target"), 2) or None
        b["gap"] = (round(b["weight"] - b["target_ratio"], 2)
                    if b["target_ratio"] else None)
        by_list.append(b)

    by_list.sort(key=lambda x: -x["value"])

    summary = {
        "count": len(items),
        "symbol_count": len(by_list),
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0.0,
        "price_missing": sum(1 for i in items if not i["price_ok"]),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    return {"items": items, "summary": summary, "by_symbol": by_list}


def _empty_summary() -> dict:
    return {
        "count": 0, "symbol_count": 0, "total_cost": 0.0, "total_value": 0.0,
        "total_pnl": 0.0, "total_pnl_pct": 0.0, "price_missing": 0,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }