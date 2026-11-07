"""
市场全景热力图。

数据来源：
    东方财富的批量行情接口（ulist.np/get），一次能查几十只股票的
    价格 / 涨跌幅 / 市值，不用一只一只请求。

    为什么不用现有的 registry.get_snapshot？
        那个是「一只股票一次请求」，136 只要 136 次，太慢。
        批量接口一次 50 只，3 次搞定。

分组逻辑：
    一级：行业（来自概念库里 kind='industry' 的概念）
    二级：该行业下的个股

    一只股票可能同时属于多个概念，但只属于一个行业。
    如果找不到它的行业，就归到「其他」。

每日封面：
    每天第一次打开时抓一次，存进 market_snapshots 表。
    当天再打开就直接用缓存 —— 保证「今天的封面」一整天不变，
    也避免频繁请求接口被限流。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from app.core.db import db_session

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    day        TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}

# 东方财富的 secid 前缀：1=沪 0=深 116=港股 105=美股纳斯达克 106=纽交所 107=美交所
_PREFIX = {"SH": ["1"], "SZ": ["0"], "BJ": ["0"], "HK": ["116"], "US": ["105", "106", "107"]}


def init() -> None:
    with db_session() as conn:
        conn.executescript(SCHEMA)


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update(_UA)
    return s


def _secids_for(symbols: list[str]) -> list[tuple[str, str]]:
    """把标准代码转成 (secid, symbol) 列表。美股会有多个候选前缀。"""
    out: list[tuple[str, str]] = []
    for sym in symbols:
        try:
            code, mkt = sym.split(".")
        except ValueError:
            continue
        for p in _PREFIX.get(mkt, []):
            out.append((f"{p}.{code}", sym))
    return out


def fetch_quotes(symbols: list[str], chunk: int = 50, timeout: int = 15) -> dict[str, dict]:
    """
    批量抓行情。返回 {symbol: {name, price, change, market_cap}}。

    自动处理美股前缀（105/106/107 都试一遍）。
    """
    sess = _session()
    pairs = _secids_for(symbols)
    got: dict[str, dict] = {}

    # 按 secid 批量查
    batches = [pairs[i:i + chunk] for i in range(0, len(pairs), chunk)]

    def _one(batch: list[tuple[str, str]]) -> list[dict]:
        ids = ",".join(sid for sid, _ in batch)
        try:
            r = sess.get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params={
                    "fltt": "2", "invt": "2",
                    "fields": "f12,f14,f2,f3,f20",
                    "secids": ids,
                },
                timeout=timeout,
            )
            return ((r.json() or {}).get("data") or {}).get("diff") or []
        except Exception:  # noqa: BLE001
            return []

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_one, batches))

    # 建立 「前缀.代码」→ symbol 的映射，便于回填
    sid_map = {sid: sym for sid, sym in pairs}

    for batch, diff in zip(batches, results):
        prefix_of = {sid: sid.split(".")[0] for sid, _ in batch}
        for item in diff:
            code = str(item.get("f12") or "")
            if not code:
                continue
            # 找到匹配的 secid
            for sid, sym in batch:
                if sid.endswith("." + code):
                    price = item.get("f2")
                    change = item.get("f3")
                    cap = item.get("f20")
                    if not isinstance(price, (int, float)) or price <= 0:
                        continue
                    got[sym] = {
                        "symbol": sym,
                        "name": item.get("f14") or sym,
                        "price": float(price),
                        "change": float(change) if isinstance(change, (int, float)) else 0.0,
                        "market_cap": float(cap) if isinstance(cap, (int, float)) else 0.0,
                    }
                    break

    return got


# 概念 → 行业的兜底映射。
#
# 为什么需要这个：
#    概念库里有 26 个「概念」（跨行业主题），它们不绑定行业。
#    如果一只股票只出现在概念里、没被任何行业收录，它会掉进「其他」这个大筐 ——
#    实测有 63 只股票、9.7% 的市值进了「其他」，热力图上一大块灰绿色，
#    既不好看也没信息量。
#
#    这张表按「这个概念主要属于哪个产业」做兜底归类，
#    只影响热力图的分组，不改概念库本身的数据。
_CONCEPT_FALLBACK: dict[str, str] = {
    # 电力设备 / 新能源
    "固态电池": "电力设备", "锂电池": "电力设备", "光伏": "电力设备",
    "储能": "电力设备", "氢能源": "电力设备", "特高压": "电力设备",
    "核电": "电力设备", "新能源汽车": "汽车",
    # TMT
    "人工智能": "计算机", "AI算力": "计算机", "液冷服务器": "计算机",
    "数据要素": "计算机", "信创": "计算机",
    "CPO光模块": "通信", "华为产业链": "电子", "量子科技": "电子",
    "机器人": "机械",
    # 医药
    "创新药": "医药", "减肥药": "医药", "合成生物": "医药", "脑机接口": "医药",
    # 材料
    "稀土永磁": "有色金属", "PEEK材料": "基础化工",
    # 风格
    "中特估": "综合", "高股息": "综合",
    # 军工
    "低空经济": "军工",
}


def _concept_to_industry() -> dict[str, str]:
    """symbol → 兜底行业名（通过它所属的概念推断）。"""
    from app.concepts import store

    store.ensure_seeded()
    with db_session() as conn:
        rows = conn.execute("""
            SELECT c.name AS cname, c.kind, i.symbol
            FROM concepts c
            JOIN concept_items i ON i.concept_id = c.id
        """).fetchall()

    out: dict[str, str] = {}
    for r in rows:
        if r["kind"] != "concept":
            continue
        target = _CONCEPT_FALLBACK.get(r["cname"])
        if target and r["symbol"] not in out:
            out[r["symbol"]] = target
    return out


def _industry_map() -> tuple[dict[str, str], dict[str, str]]:
    """
    返回：
        symbol → 行业名
        industry_order: 行业名 → 排序序号

    查找顺序：
        1. 概念库里 kind='industry' 的直接归属
        2. 按概念兜底映射推断（_CONCEPT_FALLBACK）
        3. 都没有 → 其他
    """
    from app.concepts import store

    store.ensure_seeded()
    with db_session() as conn:
        rows = conn.execute("""
            SELECT c.name AS cname, c.sort_order, i.symbol
            FROM concepts c
            JOIN concept_items i ON i.concept_id = c.id
            WHERE c.kind = 'industry'
            ORDER BY c.sort_order
        """).fetchall()

    sym2ind: dict[str, str] = {}
    order: dict[str, int] = {}
    for r in rows:
        sym2ind.setdefault(r["symbol"], r["cname"])
        order[r["cname"]] = r["sort_order"]

    # 兜底：只给还没归属的股票
    for sym, ind in _concept_to_industry().items():
        if sym not in sym2ind:
            sym2ind[sym] = ind
            order.setdefault(ind, 500)

    return sym2ind, order


def build_heatmap(force: bool = False) -> dict:
    """
    构造市场全景数据。当天已缓存且未强制刷新时直接用缓存。
    """
    init()
    today = time.strftime("%Y-%m-%d")

    if not force:
        with db_session() as conn:
            row = conn.execute(
                "SELECT payload FROM market_snapshots WHERE day=?", (today,)).fetchone()
        if row:
            try:
                return json.loads(row["payload"])
            except json.JSONDecodeError:
                pass

    payload = _build_payload()

    with db_session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO market_snapshots(day, payload) VALUES (?,?)",
            (today, json.dumps(payload, ensure_ascii=False)))

    return payload


def _build_payload() -> dict:
    from app.concepts import store

    store.ensure_seeded()
    sym2ind, order = _industry_map()

    # 取所有概念里的全部股票（去重）
    with db_session() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM concept_items").fetchall()
    symbols = [r["symbol"] for r in rows if r["symbol"]]

    quotes = fetch_quotes(symbols)

    groups: dict[str, dict] = {}
    for sym, q in quotes.items():
        ind = sym2ind.get(sym) or "其他"
        g = groups.setdefault(ind, {"name": ind, "stocks": [], "_cap": 0.0, "_wsum": 0.0})
        g["stocks"].append(q)
        g["_cap"] += q["market_cap"]
        g["_wsum"] += q["market_cap"] * q["change"]

    out_groups = []
    for name, g in groups.items():
        cap = g.pop("_cap")
        wsum = g.pop("_wsum")
        g["change"] = round(wsum / cap, 2) if cap > 0 else 0.0
        g["market_cap"] = cap
        g["count"] = len(g["stocks"])
        g["stocks"].sort(key=lambda x: -x["market_cap"])
        out_groups.append(g)

    # 行业按涨跌幅排序，红火的排前面
    out_groups.sort(key=lambda x: -x["change"])

    all_stocks = [s for g in out_groups for s in g["stocks"]]
    up = sum(1 for s in all_stocks if s["change"] > 0)
    down = sum(1 for s in all_stocks if s["change"] < 0)

    total_cap = sum(s["market_cap"] for s in all_stocks)
    avg = (sum(s["market_cap"] * s["change"] for s in all_stocks) / total_cap
           if total_cap > 0 else 0.0)

    # 按市场统计
    market_stats: dict[str, dict] = {}
    for s in all_stocks:
        mkt = s["symbol"].split(".")[-1]
        m = market_stats.setdefault(mkt, {"market": mkt, "count": 0,
                                          "up": 0, "down": 0, "_cap": 0.0, "_w": 0.0})
        m["count"] += 1
        m["_cap"] += s["market_cap"]
        m["_w"] += s["market_cap"] * s["change"]
        if s["change"] > 0:
            m["up"] += 1
        elif s["change"] < 0:
            m["down"] += 1

    for m in market_stats.values():
        m["change"] = round(m.pop("_w") / m["_cap"], 2) if m["_cap"] > 0 else 0.0
        m.pop("_cap")

    return {
        "day": time.strftime("%Y-%m-%d"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "groups": out_groups,
        "markets": sorted(market_stats.values(), key=lambda x: -x["count"]),
        "stats": {
            "total": len(all_stocks),
            "up": up,
            "down": down,
            "flat": len(all_stocks) - up - down,
            "avg_change": round(avg, 2),
            "total_cap": total_cap,
        },
    }