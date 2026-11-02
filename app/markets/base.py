"""
市场适配器基类：定义所有数据源必须遵守的统一接口。

设计模式：策略模式（Strategy Pattern）
  上层业务只调 `adapter.get_kline(sym)`，不关心数据是腾讯来的还是Yahoo来的。
  换数据源时只改注册表，业务代码一行不动。

统一数据结构：
  所有适配器的 K 线返回同一个 DataFrame 结构，列为：
      trade_date  str    YYYY-MM-DD
      open        float
      high        float
      low         float
      close       float
      volume      float  成交量
      amount      float  成交额（没有则 None）
      pct_change  float  涨跌幅%
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

from app.markets.symbols import Symbol

# 统一K线列
KLINE_COLUMNS = ["trade_date", "open", "high", "low", "close",
                 "volume", "amount", "pct_change"]


class AdapterError(Exception):
    """数据源异常（网络、改版、限流）。"""


@dataclass
class Snapshot:
    """实时快照 / 基础估值信息。"""

    symbol: str = ""
    name: str = ""
    price: float | None = None
    change_pct: float | None = None
    market_cap: float | None = None      # 总市值（元）
    pe_ttm: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None  # 股息率 %
    currency: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Fundamentals:
    """基本面数据（以 A 股最完整）。"""

    symbol: str = ""
    report_date: str = ""
    report_type: str = ""          # 年报/中报/一季报
    revenue: float | None = None            # 营业总收入
    net_profit: float | None = None         # 归母净利润
    revenue_yoy: float | None = None        # 营收同比 %
    profit_yoy: float | None = None         # 净利同比 %
    roe: float | None = None                # 加权ROE %
    roe_deducted: float | None = None       # 扣非ROE %
    roic: float | None = None
    gross_margin: float | None = None       # 毛利率 %
    net_margin: float | None = None         # 净利率 %
    debt_ratio: float | None = None         # 资产负债率 %
    current_ratio: float | None = None      # 流动比率
    quick_ratio: float | None = None        # 速动比率
    eps: float | None = None
    bps: float | None = None
    cash_per_share: float | None = None
    ocf_to_profit: float | None = None      # 经营现金流/净利润
    extra: dict = field(default_factory=dict)


class MarketAdapter(ABC):
    """所有数据源适配器的接口。"""

    name: str = "base"

    @abstractmethod
    def supports(self, sym: Symbol) -> bool:
        """这个适配器是否支持该市场。"""

    @abstractmethod
    def get_kline(self, sym: Symbol, beg: str, end: str | None = None) -> pd.DataFrame:
        """取日线。beg/end 格式 YYYYMMDD。"""

    def get_snapshot(self, sym: Symbol) -> Snapshot:
        raise AdapterError(f"{self.name} 不支持快照")

    def get_fundamentals(self, sym: Symbol) -> Fundamentals:
        raise AdapterError(f"{self.name} 不支持基本面")

    def search(self, keyword: str, limit: int = 10) -> list[dict]:
        return []


# ==================================================================
# 工具函数
# ==================================================================
def empty_kline() -> pd.DataFrame:
    return pd.DataFrame(columns=KLINE_COLUMNS)


def normalize_kline(df: pd.DataFrame) -> pd.DataFrame:
    """
    统一整理 K 线数据：
      - 只保留标准列
      - 按日期升序
      - 去重
      - 补齐 pct_change
    """
    if df is None or df.empty:
        return empty_kline()

    for col in KLINE_COLUMNS:
        if col not in df.columns:
            df[col] = None

    df = df[KLINE_COLUMNS].copy()
    df = df.dropna(subset=["trade_date", "close"])
    df = df.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)

    # 涨跌幅缺失就用收盘价自己算
    if df["pct_change"].isna().all():
        df["pct_change"] = (df["close"].pct_change(fill_method=None) * 100).round(2)

    return df
