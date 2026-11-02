"""
适配器注册表：根据市场自动选择数据源，失败自动降级到备用源。

设计要点：
  1. 每个市场有独立的「主通道 → 备用通道」顺序（P0 探测结论决定）
  2. 任一通道失败自动尝试下一个，全部失败才抛异常
  3. 返回数据时带上「实际用了哪个源」，便于界面上标注数据来源

为什么美股主通道是 Yahoo 而不是腾讯？
  Yahoo 一次请求能拿 10 年历史，腾讯单次只有 640 条需要翻页。
  对美股这种动辄几十年历史的标的，Yahoo 效率高得多。
"""

from __future__ import annotations

import pandas as pd

from app.markets.base import (AdapterError, Fundamentals, MarketAdapter,
                              Snapshot)
from app.markets.eastmoney import EastmoneyAdapter
from app.markets.symbols import (MARKET_BJ, MARKET_HK, MARKET_SH, MARKET_SZ,
                                 MARKET_US, Symbol, parse)
from app.markets.tencent import TencentAdapter
from app.markets.yahoo import YahooAdapter

_tencent = TencentAdapter()
_yahoo = YahooAdapter()
_eastmoney = EastmoneyAdapter()

ALL_ADAPTERS: list[MarketAdapter] = [_tencent, _yahoo, _eastmoney]

# 各市场的K线通道优先级（P0 探测实测结果）
KLINE_CHAIN: dict[str, list[MarketAdapter]] = {
    MARKET_SH: [_tencent, _yahoo, _eastmoney],
    MARKET_SZ: [_tencent, _yahoo, _eastmoney],
    MARKET_BJ: [_tencent, _eastmoney],
    MARKET_HK: [_tencent, _yahoo],
    MARKET_US: [_yahoo, _tencent, _eastmoney],
}

# 快照通道（东财字段最全，腾讯兜底）
SNAPSHOT_CHAIN: dict[str, list[MarketAdapter]] = {
    MARKET_SH: [_eastmoney, _tencent],
    MARKET_SZ: [_eastmoney, _tencent],
    MARKET_BJ: [_eastmoney, _tencent],
    MARKET_HK: [_eastmoney, _tencent],
    MARKET_US: [_eastmoney, _tencent],
}

# 基本面通道（只有东财支持，且仅A股）
FUNDAMENTALS_CHAIN: dict[str, list[MarketAdapter]] = {
    MARKET_SH: [_eastmoney],
    MARKET_SZ: [_eastmoney],
    MARKET_BJ: [_eastmoney],
}


class AllSourcesFailed(AdapterError):
    """所有数据源都失败。"""


def _run_chain(chain: list[MarketAdapter], op: str, sym: Symbol, *args, **kwargs):
    """按顺序尝试每个适配器，返回 (结果, 源名称)。"""
    errors = []
    for adapter in chain:
        if not adapter.supports(sym):
            continue
        try:
            fn = getattr(adapter, op)
            result = fn(sym, *args, **kwargs)
            return result, adapter.name
        except AdapterError as exc:
            errors.append(f"{adapter.name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{adapter.name}: {type(exc).__name__}: {exc}")
    detail = " | ".join(errors) if errors else "无可用数据源"
    raise AllSourcesFailed(f"{sym.raw} {op} 全部失败 -> {detail}")


# ==================================================================
# 对外接口
# ==================================================================
def get_kline(symbol: str, beg: str = "20180101", end: str | None = None
              ) -> tuple[pd.DataFrame, str]:
    """
    取日线。返回 (DataFrame, 数据源名称)。
    """
    sym = parse(symbol)
    chain = KLINE_CHAIN.get(sym.market)
    if not chain:
        raise AllSourcesFailed(f"暂不支持的市场: {sym.market}")
    return _run_chain(chain, "get_kline", sym, beg=beg, end=end)


def get_snapshot(symbol: str) -> tuple[Snapshot, str]:
    sym = parse(symbol)
    chain = SNAPSHOT_CHAIN.get(sym.market, [])
    if not chain:
        raise AllSourcesFailed(f"暂不支持的市场: {sym.market}")
    return _run_chain(chain, "get_snapshot", sym)


def get_fundamentals(symbol: str) -> tuple[Fundamentals, str]:
    sym = parse(symbol)
    chain = FUNDAMENTALS_CHAIN.get(sym.market, [])
    if not chain:
        raise AllSourcesFailed(f"{sym.label} 暂不支持基本面数据")
    return _run_chain(chain, "get_fundamentals", sym)


def search(keyword: str, markets: list[str] | None = None, limit: int = 15) -> list[dict]:
    """
    跨市场搜索。合并东财（中文强）和 Yahoo（英文强）的结果，去重后返回。

    markets: 限定市场列表，如 ["SH","SZ","HK","US"]；None 表示全部
    """
    if not keyword or not keyword.strip():
        return []

    merged: dict[str, dict] = {}

    # 东财优先（中文搜索质量高）
    for item in _eastmoney.search(keyword, limit):
        merged.setdefault(item["symbol"], item)

    # Yahoo 补充（英文/全球）
    for item in _yahoo.search(keyword, limit):
        merged.setdefault(item["symbol"], item)

    results = list(merged.values())
    if markets:
        wanted = set(markets)
        results = [r for r in results if r["market"] in wanted]

    # 排序：A股优先，然后港股、美股
    order = {MARKET_SH: 0, MARKET_SZ: 1, MARKET_BJ: 2, MARKET_HK: 3, MARKET_US: 4}
    results.sort(key=lambda r: order.get(r["market"], 9))
    return results[:limit]


def market_coverage() -> dict:
    """返回各市场的数据能力矩阵，供前端展示。"""
    return {
        MARKET_SH: {"kline": True, "snapshot": True, "fundamentals": True},
        MARKET_SZ: {"kline": True, "snapshot": True, "fundamentals": True},
        MARKET_BJ: {"kline": True, "snapshot": True, "fundamentals": True},
        MARKET_HK: {"kline": True, "snapshot": True, "fundamentals": False},
        MARKET_US: {"kline": True, "snapshot": True, "fundamentals": False},
    }
