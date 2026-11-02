"""
统一代码体系：跨市场标的的标准化表示与格式转换。

为什么需要这个模块？
  同一个股票在不同数据源里的写法完全不同：
      贵州茅台 = sh600519（腾讯） = 600519.SS（Yahoo） = 1.600519（东财）
  如果业务代码里到处写这些转换，改一个数据源就要改几十处。
  所以集中在这里，一处转换、处处复用。

标准格式：{代码}.{市场}
    600519.SH   沪市
    000001.SZ   深市
    430047.BJ   北交所
    00700.HK    港股
    AAPL.US     美股
    BTC.CRYPTO  加密货币
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 市场代码
MARKET_CN = "CN"       # 中国内地（沪/深/北）
MARKET_SH = "SH"
MARKET_SZ = "SZ"
MARKET_BJ = "BJ"
MARKET_HK = "HK"
MARKET_US = "US"
MARKET_CRYPTO = "CRYPTO"

SUPPORTED_MARKETS = [MARKET_SH, MARKET_SZ, MARKET_BJ, MARKET_HK, MARKET_US, MARKET_CRYPTO]

# 界面展示用名称
MARKET_LABELS = {
    MARKET_SH: "沪市A股",
    MARKET_SZ: "深市A股",
    MARKET_BJ: "北交所",
    MARKET_HK: "港股",
    MARKET_US: "美股",
    MARKET_CRYPTO: "加密货币",
}


class SymbolError(ValueError):
    """代码格式无法识别。"""


@dataclass(frozen=True)
class Symbol:
    """一个标准化的标的。"""

    code: str      # 纯代码，如 600519 / 00700 / AAPL
    market: str    # SH / SZ / BJ / HK / US / CRYPTO

    @property
    def raw(self) -> str:
        """标准写法：600519.SH"""
        return f"{self.code}.{self.market}"

    @property
    def label(self) -> str:
        return MARKET_LABELS.get(self.market, self.market)

    @property
    def is_a_share(self) -> bool:
        return self.market in (MARKET_SH, MARKET_SZ, MARKET_BJ)


# ==================================================================
# 解析
# ==================================================================
def parse(raw: str) -> Symbol:
    """
    把各种写法统一解析成 Symbol。

    支持输入：
        600519 / 600519.SH / sh600519 / SH600519 / 1.600519（东财secid）
        00700 / 00700.HK / hk00700
        AAPL / AAPL.US / usAAPL.OQ
    """
    if not raw or not str(raw).strip():
        raise SymbolError("代码不能为空")

    s = str(raw).strip().upper()

    # 1) 带点号的：600519.SH / 00700.HK / AAPL.US / 1.600519
    if "." in s:
        head, _, tail = s.rpartition(".")
        # 东财 secid 格式：市场号.代码
        if head in ("0", "1", "105", "106", "107", "116", "128", "90", "100"):
            return _from_em_secid(head, tail)
        # 标准后缀
        if tail in SUPPORTED_MARKETS:
            return Symbol(_normalize_code(head, tail), tail)
        # 腾讯美股格式 usAAPL.OQ -> 后缀 OQ/N
        if tail in ("OQ", "N", "AMEX") and head.startswith("US"):
            return Symbol(head[2:], MARKET_US)
        raise SymbolError(f"无法识别的市场后缀: {tail}")

    # 2) 前缀式：sh600519 / hk00700 / usAAPL
    m = re.match(r"^(SH|SZ|BJ|HK|US)(.+)$", s)
    if m:
        prefix, code = m.group(1), m.group(2)
        return Symbol(_normalize_code(code, prefix), prefix)

    # 3) 纯代码：靠规则推断市场
    return Symbol(s, _infer_market(s))


def _normalize_code(code: str, market: str) -> str:
    """统一代码位数：港股补齐5位，A股保持6位，美股保持字母。"""
    code = code.strip().upper()
    if market == MARKET_HK:
        return code.zfill(5)
    return code


def _infer_market(code: str) -> str:
    """
    纯代码推断市场（尽力而为，可能不准，建议调用方显式指定）。

    规则：
        6位数字 6开头 -> 沪市
        6位数字 0/3开头 -> 深市
        6位数字 4/8开头 -> 北交所
        5位数字 -> 港股
        纯字母 -> 美股
    """
    if code.isdigit():
        if len(code) == 5:
            return MARKET_HK
        if len(code) == 6:
            if code[0] == "6":
                return MARKET_SH
            if code[0] in ("0", "3"):
                return MARKET_SZ
            if code[0] in ("4", "8"):
                return MARKET_BJ
            if code[0] == "9":
                return MARKET_SH   # B股
            if code[0] == "2":
                return MARKET_SZ   # B股
        raise SymbolError(f"无法推断 {code} 的市场，请显式指定（如 {code}.HK）")

    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", code):
        return MARKET_US

    raise SymbolError(f"无法识别的代码: {code}")


def _from_em_secid(mkt_num: str, code: str) -> Symbol:
    """从东财 secid 反解。"""
    mapping = {
        "1": MARKET_SH,
        "0": MARKET_SZ,
        "116": MARKET_HK,
        "105": MARKET_US,
        "106": MARKET_US,
        "107": MARKET_US,
    }
    market = mapping.get(mkt_num)
    if not market:
        raise SymbolError(f"暂不支持的东财市场号: {mkt_num}")
    return Symbol(_normalize_code(code, market), market)


# ==================================================================
# 各数据源格式转换
# ==================================================================
def to_tencent(sym: Symbol, us_suffix: str = "OQ") -> str:
    """
    转腾讯格式。
        600519.SH -> sh600519
        00700.HK  -> hk00700
        AAPL.US   -> usAAPL.OQ（腾讯美股必须带交易所后缀）
    """
    if sym.market == MARKET_SH:
        return f"sh{sym.code}"
    if sym.market == MARKET_SZ:
        return f"sz{sym.code}"
    if sym.market == MARKET_BJ:
        return f"bj{sym.code}"
    if sym.market == MARKET_HK:
        return f"hk{sym.code}"
    if sym.market == MARKET_US:
        return f"us{sym.code}.{us_suffix}"
    raise SymbolError(f"腾讯不支持该市场: {sym.market}")


def to_yahoo(sym: Symbol) -> str:
    """
    转 Yahoo 格式。
        600519.SH -> 600519.SS
        000001.SZ -> 000001.SZ
        00700.HK  -> 0700.HK（Yahoo 港股是4位）
        AAPL.US   -> AAPL
    """
    if sym.market == MARKET_SH:
        return f"{sym.code}.SS"
    if sym.market == MARKET_SZ:
        return f"{sym.code}.SZ"
    if sym.market == MARKET_HK:
        return f"{sym.code.lstrip('0').zfill(4)}.HK"
    if sym.market == MARKET_US:
        return sym.code
    raise SymbolError(f"Yahoo不支持该市场: {sym.market}")


def to_em_secid(sym: Symbol) -> str:
    """
    转东财 secid。
        600519.SH -> 1.600519
        000001.SZ -> 0.000001
        00700.HK  -> 116.00700
        AAPL.US   -> 105.AAPL（105=纳斯达克，106=纽交所）
    """
    if sym.market == MARKET_SH:
        return f"1.{sym.code}"
    if sym.market in (MARKET_SZ, MARKET_BJ):
        return f"0.{sym.code}"
    if sym.market == MARKET_HK:
        return f"116.{sym.code}"
    if sym.market == MARKET_US:
        return f"105.{sym.code}"
    raise SymbolError(f"东财不支持该市场: {sym.market}")


def from_em_suggest(item: dict) -> Symbol | None:
    """
    把东财搜索建议的条目转成 Symbol。

    东财返回的 MktNum 含义：
        0 = 深市, 1 = 沪市, 105 = 纳斯达克, 106 = 纽交所, 116 = 港股
    """
    code = str(item.get("Code") or "").strip()
    mkt = str(item.get("MktNum") or "").strip()
    if not code:
        return None
    mapping = {"0": MARKET_SZ, "1": MARKET_SH, "116": MARKET_HK,
               "105": MARKET_US, "106": MARKET_US, "107": MARKET_US}
    market = mapping.get(mkt)
    if not market:
        return None
    return Symbol(_normalize_code(code, market), market)


def us_suffix_candidates() -> list[str]:
    """腾讯美股交易所后缀的尝试顺序。"""
    return ["OQ", "N", "AMEX"]
