"""
基本面指标（从财务数据 + 估值快照计算）。

覆盖：估值、盈利质量、成长性、财务健康。
注意：仅 A 股有完整基本面（东财 F10），港股/美股只有 PE/PB/市值。
"""

from __future__ import annotations

from app.markets.base import Fundamentals, Snapshot


def _safe(v, default=None):
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f


def _div(a, b):
    """安全除法：分母为 0 或 None 时返回 None。"""
    a, b = _safe(a), _safe(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def compute_fundamental_metrics(f: Fundamentals | None, snap: Snapshot | None) -> dict:
    """
    把原始财务数据加工成指标字典。

    参数可以是 None（例如美股没有财务数据），此时相关字段返回 None，
    上层评分会自动跳过这些维度 —— 这叫"优雅降级"，比返回 0 分更诚实。
    """
    out: dict = {
        # 估值类
        "pe_ttm": None, "pb": None, "dividend_yield": None, "peg": None,
        "market_cap": None,
        # 盈利质量
        "roe": None, "roic": None, "gross_margin": None, "net_margin": None,
        "ocf_to_profit": None,
        # 成长
        "revenue_yoy": None, "profit_yoy": None, "revenue_cagr_3y": None,
        # 财务健康
        "debt_ratio": None, "current_ratio": None, "quick_ratio": None,
        # 每股
        "eps": None, "bps": None,
        # 元信息
        "report_date": None, "report_type": None, "has_fundamentals": False,
    }

    if snap:
        out["pe_ttm"] = _safe(snap.pe_ttm)
        out["pb"] = _safe(snap.pb)
        out["dividend_yield"] = _safe(snap.dividend_yield)
        out["market_cap"] = _safe(snap.market_cap)

    if not f:
        return out

    out.update({
        "roe": _safe(f.roe),
        "roic": _safe(f.roic),
        "gross_margin": _safe(f.gross_margin),
        "net_margin": _safe(f.net_margin),
        "ocf_to_profit": _safe(f.ocf_to_profit),
        "revenue_yoy": _safe(f.revenue_yoy),
        "profit_yoy": _safe(f.profit_yoy),
        "debt_ratio": _safe(f.debt_ratio),
        "current_ratio": _safe(f.current_ratio),
        "quick_ratio": _safe(f.quick_ratio),
        "eps": _safe(f.eps),
        "bps": _safe(f.bps),
        "report_date": f.report_date,
        "report_type": f.report_type,
        "has_fundamentals": True,
    })

    # PEG = PE / 净利润增速(%)。增速 ≤0 时 PEG 无意义，返回 None
    pe, growth = out["pe_ttm"], out["profit_yoy"]
    if pe and growth and growth > 0:
        out["peg"] = round(pe / growth, 4)

    return out
