"""
东方财富适配器：估值快照 + A股基本面 + 中文搜索。

P0 探测结论：
  ✅ push2.eastmoney.com/api/qt/stock/get   实时快照（PE/PB/市值）
  ✅ datacenter.eastmoney.com F10 财务      165个字段，A股完整
  ✅ searchapi.eastmoney.com/suggest        中文搜索，返回 secid
  ❌ push2his.eastmoney.com K线             已限流（所以K线不用它）

重要坑：快照数值需要除以 100
    实测 600519: PE=1868 → 实际 18.68，PB=662 → 6.62
    但 ROE=16.75 不需要除。不同字段缩放不一致，必须逐字段处理。
"""

from __future__ import annotations

import re

import requests

from app.markets.base import (AdapterError, Fundamentals, MarketAdapter,
                              Snapshot)
from app.markets.symbols import (MARKET_BJ, MARKET_HK, MARKET_SH, MARKET_SZ,
                                 MARKET_US, Symbol, from_em_suggest,
                                 to_em_secid)

TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_session = requests.Session()
_session.trust_env = False
_session.headers.update({"User-Agent": UA})

_SUGGEST_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"


class EastmoneyAdapter(MarketAdapter):
    name = "eastmoney"

    def supports(self, sym: Symbol) -> bool:
        return sym.market in (MARKET_SH, MARKET_SZ, MARKET_BJ, MARKET_HK, MARKET_US)

    # ------------------------------------------------------------ K线
    def get_kline(self, sym: Symbol, beg: str = "20180101", end: str | None = None):
        """
        东财K线。P0 实测 push2his 已限流，仅作为最后的备用通道。
        """
        from app.markets.base import normalize_kline
        import pandas as pd

        params = {
            "secid": to_em_secid(sym),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "klt": 101, "fqt": 1,
            "beg": beg, "end": end or "20500101",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        try:
            r = _session.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                             params=params, timeout=TIMEOUT)
            r.raise_for_status()
            payload = r.json()
        except requests.RequestException as exc:
            raise AdapterError(f"东财K线请求失败（该源已限流）: {exc}") from exc

        data = payload.get("data") or {}
        klines = data.get("klines") or []
        if not klines:
            raise AdapterError(f"东财无K线: {sym.raw}")

        rows = []
        for line in klines:
            p = line.split(",")
            if len(p) < 8:
                continue
            rows.append({
                "trade_date": p[0], "open": _f(p[1]), "close": _f(p[2]),
                "high": _f(p[3]), "low": _f(p[4]), "volume": _f(p[5]),
                "amount": _f(p[6]), "pct_change": _f(p[7]),
            })
        df = normalize_kline(pd.DataFrame(rows))
        df.attrs["name"] = data.get("name", "")
        return df

    # ------------------------------------------------------------ 快照
    def get_snapshot(self, sym: Symbol) -> Snapshot:
        """
        实时快照。

        重要坑：不同市场的缩放倍数不一样（实测得出）
            A股  价格 ×100   （133075 -> 1330.75）
            港股  价格 ×1000  （387600 -> 387.6）
            美股  价格 ×1000  （345150 -> 345.15）
        PE/PB 统一 ×100。ROE 不缩放。市值不缩放。
        """
        params = {
            "secid": to_em_secid(sym),
            "fields": "f43,f57,f58,f116,f117,f162,f167,f173,f170",
        }
        try:
            r = _session.get("https://push2.eastmoney.com/api/qt/stock/get",
                             params=params, timeout=TIMEOUT)
            r.raise_for_status()
            data = (r.json() or {}).get("data") or {}
        except requests.RequestException as exc:
            raise AdapterError(f"东财快照失败: {exc}") from exc

        if not data:
            raise AdapterError(f"东财无快照: {sym.raw}")

        price_div = 100.0 if sym.is_a_share else 1000.0

        return Snapshot(
            symbol=sym.raw,
            name=data.get("f58") or "",
            price=_scaled(data.get("f43"), price_div),
            change_pct=_scaled(data.get("f170"), 100.0),
            market_cap=_nonzero(data.get("f116")),
            pe_ttm=_nonzero(_scaled(data.get("f162"), 100.0)),
            pb=_nonzero(_scaled(data.get("f167"), 100.0)),
            extra={"roe": _nonzero(data.get("f173"))},
        )

    # ------------------------------------------------------------ 基本面
    def get_fundamentals(self, sym: Symbol, periods: int = 1) -> Fundamentals:
        """
        取最近一期财务数据。仅 A 股支持（东财 F10 只覆盖内地）。
        """
        if not sym.is_a_share:
            raise AdapterError(f"东财基本面仅支持A股，当前: {sym.raw}")

        secucode = f"{sym.code}.{sym.market}"
        params = {
            "reportName": "RPT_F10_FINANCE_MAINFINADATA",
            "columns": "ALL",
            "filter": f'(SECUCODE="{secucode}")',
            "pageNumber": 1, "pageSize": periods,
            "sortTypes": -1, "sortColumns": "REPORT_DATE",
            "source": "HSF10", "client": "PC",
        }
        try:
            r = _session.get("https://datacenter.eastmoney.com/securities/api/data/v1/get",
                             params=params, timeout=TIMEOUT)
            r.raise_for_status()
            payload = r.json()
        except requests.RequestException as exc:
            raise AdapterError(f"东财财务请求失败: {exc}") from exc

        rows = ((payload.get("result") or {}).get("data")) or []
        if not rows:
            raise AdapterError(f"东财无财务数据: {sym.raw}")

        x = rows[0]
        return Fundamentals(
            symbol=sym.raw,
            report_date=str(x.get("REPORT_DATE") or "")[:10],
            report_type=x.get("REPORT_TYPE") or "",
            revenue=x.get("TOTALOPERATEREVE"),
            net_profit=x.get("PARENTNETPROFIT"),
            revenue_yoy=x.get("TOTALOPERATEREVETZ"),
            profit_yoy=x.get("PARENTNETPROFITTZ"),
            roe=x.get("ROEJQ"),
            roe_deducted=x.get("ROEKCJQ"),
            roic=x.get("ROIC"),
            gross_margin=x.get("XSMLL"),
            net_margin=x.get("XSJLL"),
            debt_ratio=x.get("ZCFZL"),
            current_ratio=x.get("LD"),
            quick_ratio=x.get("SD"),
            eps=x.get("EPSJB"),
            bps=x.get("BPS"),
            cash_per_share=x.get("MGJYXJJE"),
            ocf_to_profit=x.get("NCO_NETPROFIT"),
            extra={"name": x.get("SECURITY_NAME_ABBR", "")},
        )

    # ------------------------------------------------------------ 搜索
    def search(self, keyword: str, limit: int = 10) -> list[dict]:
        """
        中文优先的标的搜索。返回统一结构。
        """
        params = {
            "input": keyword, "type": "14",
            "token": _SUGGEST_TOKEN, "count": str(limit),
        }
        try:
            r = _session.get("https://searchapi.eastmoney.com/api/suggest/get",
                             params=params, timeout=TIMEOUT)
            r.raise_for_status()
            items = ((r.json().get("QuotationCodeTable") or {}).get("Data")) or []
        except Exception:  # noqa: BLE001
            return []

        out = []
        for it in items:
            sym = from_em_suggest(it)
            if not sym:
                continue
            name = it.get("Name") or ""
            if _is_noise(name, sym.code):
                continue
            out.append({
                "symbol": sym.raw,
                "code": sym.code,
                "market": sym.market,
                "name": name,
                "exchange": it.get("SecurityTypeName") or "",
                "type": it.get("Classify") or "",
                "source": "eastmoney",
            })
        return out


def _scaled(v, divisor: float = 100.0):
    if v is None or v == "-" or v == "":
        return None
    try:
        return float(v) / divisor
    except (TypeError, ValueError):
        return None


def _nonzero(v):
    """把 0 当作"没有数据"（东财对部分市场用 0 表示缺失）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# 搜索噪音过滤：东财会把债券、票据、权证也返回，需要剔除
_NOISE_KEYWORDS = ("Notes", "Bond", "票据", "债券", "权证", "CALL", "PUT", "Warrant")


def _is_noise(name: str, code: str) -> bool:
    """
    判断搜索结果是否是噪音。

    实测案例：搜索 "apple" 会返回
        AAPL22  Apple Inc Notes 2022   ← 债券，必须剔除
        AAPL    Apple Inc              ← 保留
    """
    if any(k.lower() in name.lower() for k in _NOISE_KEYWORDS):
        return True
    # 美股代码形如 AAPL22 / AAPL24（字母+2位数字）= 债券
    if re.fullmatch(r"[A-Z]{1,5}\d{2}", code):
        return True
    return False
