"""
腾讯行情适配器：覆盖 A股 / 港股 / 美股。

为什么用腾讯做主通道？
  P0 探测实测：三个市场都能取到，延迟 100-250ms，比东财稳定（东财 push2his 已限流）。

限制：
  单次最多 640~800 条（约 2.5-3 年），长历史需要倒序分页。
"""

from __future__ import annotations

import json
import re
import requests
import pandas as pd

from app.markets.base import AdapterError, KLINE_COLUMNS, MarketAdapter, normalize_kline
from app.markets.symbols import (MARKET_BJ, MARKET_HK, MARKET_SH, MARKET_SZ,
                                 MARKET_US, Symbol, to_tencent)

TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_session = requests.Session()
_session.trust_env = False          # 忽略系统代理（v1 踩过的坑）
_session.headers.update({"User-Agent": UA})

# 缓存美股交易所后缀的探测结果，避免每次都试
_US_SUFFIX_CACHE: dict[str, str] = {}


class TencentAdapter(MarketAdapter):
    name = "tencent"

    def supports(self, sym: Symbol) -> bool:
        return sym.market in (MARKET_SH, MARKET_SZ, MARKET_BJ, MARKET_HK, MARKET_US)

    # ------------------------------------------------------------ K线
    def get_kline(self, sym: Symbol, beg: str = "20180101",
                  end: str | None = None, period: str = "day") -> pd.DataFrame:
        """
        取K线（前复权），自动倒序分页补齐历史。

        period: day / week / month
        """
        if sym.market == MARKET_US:
            code = self._us_tencent_code(sym)
        else:
            code = to_tencent(sym)

        beg_dt = pd.to_datetime(beg, format="%Y%m%d")
        end_dt = pd.to_datetime(end, format="%Y%m%d") if end else pd.Timestamp.today().normalize()

        collected: dict[str, list] = {}
        cursor = end_dt

        for _ in range(40):
            if cursor < beg_dt:
                break
            # 每次往回取约3年（640个交易日上限），不足继续翻
            window_start = max(beg_dt, cursor - pd.Timedelta(days=1100))
            params = {
                "param": (f"{code},{period},"
                          f"{window_start.strftime('%Y-%m-%d')},"
                          f"{cursor.strftime('%Y-%m-%d')},640,qfq")
            }
            try:
                r = _session.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                                 params=params, timeout=TIMEOUT)
                r.raise_for_status()
                payload = r.json()
            except requests.RequestException as exc:
                raise AdapterError(f"腾讯行情请求失败: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise AdapterError("腾讯返回非JSON") from exc

            data = payload.get("data")
            if not isinstance(data, dict) or code not in data:
                if not collected:
                    raise AdapterError(f"腾讯未返回数据（{payload.get('msg')}）: {sym.raw}")
                break

            node = data[code]
            rows = node.get(f"qfq{period}") or node.get(period) or []
            if not rows:
                break

            for row in rows:
                if len(row) >= 6:
                    collected[row[0]] = row

            earliest = pd.to_datetime(rows[0][0])
            if earliest <= beg_dt or len(rows) < 10:
                break
            cursor = earliest - pd.Timedelta(days=1)

        if not collected:
            raise AdapterError(f"腾讯无K线数据: {sym.raw}")

        # 腾讯行格式：[日期, 开, 收, 高, 低, 成交量]
        records = [{
            "trade_date": d,
            "open": _f(collected[d][1]),
            "close": _f(collected[d][2]),
            "high": _f(collected[d][3]),
            "low": _f(collected[d][4]),
            "volume": _f(collected[d][5]),
            "amount": None,
            "pct_change": None,
        } for d in sorted(collected)]

        df = normalize_kline(pd.DataFrame(records))
        df.attrs["name"] = self._extract_name(code, data)
        return df

    # ------------------------------------------------------------ 快照
    def get_snapshot(self, sym: Symbol) -> "Snapshot":  # noqa: F821
        from app.markets.base import Snapshot
        if sym.market == MARKET_US:
            code = self._us_tencent_code(sym)
        else:
            code = to_tencent(sym)

        try:
            r = _session.get("https://qt.gtimg.cn/q=" + code, timeout=TIMEOUT)
            r.raise_for_status()
            text = r.content.decode("gbk", "ignore")
        except requests.RequestException as exc:
            raise AdapterError(f"腾讯快照失败: {exc}") from exc

        # 格式：v_sh600519="1~贵州茅台~600519~1330.75~1355.90~...";
        m = re.search(r'="([^"]*)"', text)
        if not m:
            raise AdapterError(f"腾讯快照格式异常: {text[:80]}")
        f = m.group(1).split("~")
        if len(f) < 50:
            raise AdapterError(f"腾讯快照字段不足: {len(f)}")

        def g(i):
            try:
                return float(f[i])
            except (ValueError, IndexError):
                return None

        return Snapshot(
            symbol=sym.raw,
            name=f[1] if len(f) > 1 else "",
            price=g(3),
            change_pct=g(32),
            market_cap=g(45) * 1e8 if g(45) else None,   # 单位：亿
            pe_ttm=g(39),
            pb=g(46),
            extra={"raw_fields": len(f)},
        )

    # ------------------------------------------------------------ 内部
    def _us_tencent_code(self, sym: Symbol) -> str:
        """
        腾讯美股代码必须带交易所后缀（.OQ 纳斯达克 / .N 纽交所）。
        我们不知道后缀，就依次试，成功一次后缓存。
        """
        if sym.code in _US_SUFFIX_CACHE:
            return to_tencent(sym, _US_SUFFIX_CACHE[sym.code])

        for suffix in ("OQ", "N", "AMEX"):
            probe = to_tencent(sym, suffix)
            try:
                r = _session.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                                 params={"param": f"{probe},day,,,5,qfq"}, timeout=15)
                d = r.json().get("data") or {}
                if isinstance(d, dict) and probe in d:
                    node = d[probe]
                    if node.get("qfqday") or node.get("day"):
                        _US_SUFFIX_CACHE[sym.code] = suffix
                        return probe
            except Exception:  # noqa: BLE001
                continue
        # 都失败也返回默认，让上层拿到明确的错误
        return to_tencent(sym, "OQ")

    @staticmethod
    def _extract_name(code: str, data: dict) -> str:
        try:
            qt = (data[code].get("qt") or {}).get(code)
            if isinstance(qt, list) and len(qt) > 1:
                return qt[1]
        except Exception:  # noqa: BLE001
            pass
        return ""


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
