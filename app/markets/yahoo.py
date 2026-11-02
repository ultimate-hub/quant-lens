"""
Yahoo Finance 适配器：长历史行情 + 全球搜索。

定位：美股主通道（一次请求拿10年），港股/A股备用通道。
注意：Yahoo 的基本面接口需要 crumb（P0 实测被拒），所以本适配器不提供基本面。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

from app.markets.base import AdapterError, MarketAdapter, normalize_kline
from app.markets.symbols import (MARKET_HK, MARKET_SH, MARKET_SZ, MARKET_US,
                                 Symbol, from_em_suggest, to_yahoo)

TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_session = requests.Session()
_session.trust_env = False
_session.headers.update({"User-Agent": UA})


class YahooAdapter(MarketAdapter):
    name = "yahoo"

    def supports(self, sym: Symbol) -> bool:
        return sym.market in (MARKET_SH, MARKET_SZ, MARKET_HK, MARKET_US)

    # ------------------------------------------------------------ K线
    def get_kline(self, sym: Symbol, beg: str = "20180101",
                  end: str | None = None, adjusted: bool = True) -> pd.DataFrame:
        """
        取日线。Yahoo 一次请求就能返回全部历史，不需要分页。

        adjusted=True 使用复权收盘价（含分红拆股调整），做收益分析必须用这个。
        """
        ticker = to_yahoo(sym)
        p1 = int(dt.datetime.strptime(beg, "%Y%m%d").timestamp())
        p2 = int((dt.datetime.strptime(end, "%Y%m%d") if end
                  else dt.datetime.now()).timestamp())

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {
            "period1": p1, "period2": p2, "interval": "1d",
            "events": "div,splits", "includeAdjustedClose": "true",
        }
        try:
            r = _session.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            payload = r.json()
        except requests.RequestException as exc:
            raise AdapterError(f"Yahoo请求失败: {exc}") from exc
        except ValueError as exc:
            raise AdapterError("Yahoo返回非JSON") from exc

        chart = payload.get("chart") or {}
        if chart.get("error"):
            raise AdapterError(f"Yahoo错误: {chart['error']}")
        results = chart.get("result") or []
        if not results:
            raise AdapterError(f"Yahoo无数据: {sym.raw}")

        res = results[0]
        ts = res.get("timestamp") or []
        quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        adj = ((res.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose")

        closes = quote.get("close") or []
        if adjusted and adj:
            closes = adj

        records = []
        for i, t in enumerate(ts):
            c = closes[i] if i < len(closes) else None
            if c is None:
                continue
            records.append({
                "trade_date": dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                "open": _pick(quote.get("open"), i),
                "high": _pick(quote.get("high"), i),
                "low": _pick(quote.get("low"), i),
                "close": c,
                "volume": _pick(quote.get("volume"), i),
                "amount": None,
                "pct_change": None,
            })

        if not records:
            raise AdapterError(f"Yahoo数据为空: {sym.raw}")

        df = normalize_kline(pd.DataFrame(records))
        meta = res.get("meta") or {}
        df.attrs["name"] = meta.get("longName") or meta.get("shortName") or ""
        df.attrs["currency"] = meta.get("currency", "")
        return df

    # ------------------------------------------------------------ 搜索
    def search(self, keyword: str, limit: int = 10) -> list[dict]:
        """
        全球标的搜索。返回统一结构：
            {symbol, code, market, name, exchange, type}
        """
        url = "https://query1.finance.yahoo.com/v1/finance/search"
        try:
            r = _session.get(url, params={"q": keyword, "quotesCount": limit,
                                          "newsCount": 0, "listsCount": 0},
                             timeout=TIMEOUT)
            r.raise_for_status()
            quotes = (r.json().get("quotes")) or []
        except Exception:  # noqa: BLE001
            return []

        out = []
        for q in quotes:
            if q.get("quoteType") not in ("EQUITY", "ETF", "INDEX"):
                continue
            sym = q.get("symbol", "")
            parsed = _yahoo_symbol_to_symbol(sym)
            if not parsed:
                continue
            out.append({
                "symbol": parsed.raw,
                "code": parsed.code,
                "market": parsed.market,
                "name": q.get("shortname") or q.get("longname") or "",
                "exchange": q.get("exchange", ""),
                "type": q.get("quoteType", ""),
                "source": "yahoo",
            })
        return out


def _yahoo_symbol_to_symbol(ticker: str) -> Symbol | None:
    """把 Yahoo 代码转成标准 Symbol。"""
    t = ticker.upper()
    if t.endswith(".SS"):
        return Symbol(t[:-3], MARKET_SH)
    if t.endswith(".SZ"):
        return Symbol(t[:-3], MARKET_SZ)
    if t.endswith(".HK"):
        return Symbol(t[:-3].zfill(5), MARKET_HK)
    # 其他带后缀的（.TO/.L/.DE 等）暂不纳入一期
    if "." in t:
        return None
    return Symbol(t, MARKET_US)


def _pick(arr, i):
    if not arr or i >= len(arr):
        return None
    return arr[i]
