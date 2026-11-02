"""
选股筛选器：对一批标的批量计算指标，按条件过滤，按综合分排序。

性能设计：
    单只标的需要 1-3 次网络请求（K线 + 快照 + 财务），约 0.5-1 秒。
    如果 20 只股票串行跑，要 10-20 秒，用户会以为卡死。
    所以用线程池并发（默认5个），实测 20 只约 4-6 秒。

筛选逻辑：
    先算（不调AI，保证速度）→ 再按条件过滤 → 最后按综合分排序。
    AI 分析只在用户点开某只股票时才调用。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from app.analytics import scoring
from app.analytics.fundamental import compute_fundamental_metrics
from app.analytics.price import compute_price_metrics
from app.markets import registry
from app.markets.symbols import parse

# 各市场基准（用于 Beta/Alpha），只在需要时取一次
_BENCH_CACHE: dict[str, object] = {}


def _benchmark(market: str):
    if market in _BENCH_CACHE:
        return _BENCH_CACHE[market]
    mapping = {"SH": "510300.SH", "SZ": "510300.SH", "BJ": "510300.SH",
               "US": "SPY.US", "HK": "2800.HK"}
    code = mapping.get(market)
    df = None
    if code:
        try:
            df, _ = registry.get_kline(code, beg="20190101")
        except Exception:  # noqa: BLE001
            df = None
    _BENCH_CACHE[market] = df
    return df


def evaluate_one(symbol: str, profile: str = "balanced") -> dict | None:
    """
    评估单个标的（不调 AI）。失败返回 None，不抛异常 ——
    批量筛选时个别标的失败不应影响整体。
    """
    try:
        sym = parse(symbol)
        df, src = registry.get_kline(sym.raw, beg="20190101")

        snap = None
        try:
            snap, _ = registry.get_snapshot(sym.raw)
        except Exception:  # noqa: BLE001
            pass

        fund = None
        if sym.is_a_share:
            try:
                fund, _ = registry.get_fundamentals(sym.raw)
            except Exception:  # noqa: BLE001
                pass

        pm = compute_price_metrics(df, _benchmark(sym.market))
        fm = compute_fundamental_metrics(fund, snap)
        score = scoring.score_all(pm, fm, profile=profile)

        name = (snap.name if snap and snap.name else df.attrs.get("name", "")) or sym.code

        return {
            "symbol": sym.raw,
            "name": name,
            "market": sym.market,
            "price": round(float(df["close"].iloc[-1]), 3),
            "composite": score["composite"],
            "grade": score["grade"],
            "dimensions": score["dimensions"],
            "coverage": score["coverage"],
            "pe_ttm": fm.get("pe_ttm"),
            "pb": fm.get("pb"),
            "roe": fm.get("roe"),
            "revenue_yoy": fm.get("revenue_yoy"),
            "profit_yoy": fm.get("profit_yoy"),
            "debt_ratio": fm.get("debt_ratio"),
            "market_cap_yi": (round(snap.market_cap / 1e8, 1)
                              if snap and snap.market_cap else None),
            "annual_return": pm["annual_return"],
            "max_drawdown": pm["max_drawdown"],
            "sharpe": pm["sharpe"],
            "mom_3m": pm["mom_3m"],
            "mom_12m": pm["mom_12m"],
            "rsi_14": pm["rsi_14"],
            "volatility": pm["annual_volatility"],
            "has_fundamentals": fm.get("has_fundamentals", False),
            "source": src,
        }
    except Exception:  # noqa: BLE001
        return None


def screen(symbols: list[str], profile: str = "balanced",
           conditions: dict | None = None, max_workers: int = 8,
           limit: int = 150) -> dict:
    """
    批量筛选。

    conditions 支持：
        pe_max / pe_min        市盈率区间
        pb_max                 PB 上限
        roe_min                ROE 下限（%）
        revenue_yoy_min        营收增速下限（%）
        debt_ratio_max         资产负债率上限（%）
        market_cap_min         市值下限（亿元）
        mom_3m_min             3月动量下限（小数，0.1 = 10%）
        max_drawdown_max       最大回撤上限（负数，-0.3 表示回撤不超过30%）
        sharpe_min             夏普下限
        score_min              综合分下限
        markets                限定市场列表
    """
    conditions = conditions or {}
    symbols = [s for s in dict.fromkeys(symbols) if s][:limit]
    if not symbols:
        return {"total": 0, "passed": 0, "results": [], "errors": []}

    # 市场过滤（不用抓数据就能排除）
    markets = conditions.get("markets")
    if markets:
        allowed = set(markets)
        symbols = [s for s in symbols if _safe_market(s) in allowed]

    results: list[dict] = []
    failed: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(evaluate_one, s, profile): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                row = fut.result()
            except Exception:  # noqa: BLE001
                row = None
            if row is None:
                failed.append(sym)
            else:
                results.append(row)

    passed = [r for r in results if _match(r, conditions)]
    passed.sort(key=lambda r: r["composite"], reverse=True)

    return {
        "total": len(symbols),
        "evaluated": len(results),
        "passed": len(passed),
        "results": passed,
        "errors": failed,
        "conditions": conditions,
        "profile": profile,
    }


def _safe_market(symbol: str) -> str:
    try:
        return parse(symbol).market
    except Exception:  # noqa: BLE001
        return ""


def _match(row: dict, c: dict) -> bool:
    """逐条判断是否满足条件。字段缺失时视为不满足（宁缺毋滥）。"""
    def num(key):
        v = row.get(key)
        return v if isinstance(v, (int, float)) else None

    checks = [
        ("pe_max", lambda v: v is not None and 0 < v <= c["pe_max"]),
        ("pe_min", lambda v: v is not None and v >= c["pe_min"]),
        ("pb_max", lambda v: v is not None and 0 < v <= c["pb_max"]),
        ("roe_min", lambda v: v is not None and v >= c["roe_min"]),
        ("revenue_yoy_min", lambda v: v is not None and v >= c["revenue_yoy_min"]),
        ("debt_ratio_max", lambda v: v is not None and v <= c["debt_ratio_max"]),
        ("market_cap_min", lambda v: v is not None and v >= c["market_cap_min"]),
        ("mom_3m_min", lambda v: v is not None and v >= c["mom_3m_min"]),
        ("max_drawdown_max", lambda v: v is not None and v >= c["max_drawdown_max"]),
        ("sharpe_min", lambda v: v is not None and v >= c["sharpe_min"]),
        ("score_min", lambda v: v is not None and v >= c["score_min"]),
    ]

    key_map = {
        "pe_max": "pe_ttm", "pe_min": "pe_ttm", "pb_max": "pb",
        "roe_min": "roe", "revenue_yoy_min": "revenue_yoy",
        "debt_ratio_max": "debt_ratio", "market_cap_min": "market_cap_yi",
        "mom_3m_min": "mom_3m", "max_drawdown_max": "max_drawdown",
        "sharpe_min": "sharpe", "score_min": "composite",
    }

    for cond_key, test in checks:
        if cond_key not in c or c[cond_key] is None:
            continue
        value = num(key_map[cond_key])
        try:
            if not test(value):
                return False
        except (TypeError, ValueError):
            return False
    return True
