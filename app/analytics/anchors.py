"""
价格锚点引擎：先算出候选价位，再让 AI 从里面挑。

为什么必须这样设计？
    如果直接问 AI"目标价多少"，它会编一个看起来合理的数字（幻觉）。
    正确做法：程序先算出有数学依据的候选价位，把清单交给 AI，
    AI 只能"选择并解释"，不能凭空创造数字。

四个价位怎么算：
    目标价   估值回归法（PE 回到不同水平对应的价格）+ 技术阻力位
    买入区间 支撑位法（均线、前低）
    止损位   ATR 法 + 前低法，取更保守的
    仓位建议 风险预算法（单笔最大亏损 2%）
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _round(v, digits=2):
    return round(float(v), digits) if v is not None else None


def compute_anchors(df: pd.DataFrame, pe_ttm: float | None = None,
                    max_loss_pct: float = 0.02) -> dict:
    """
    计算价格锚点。

    df:     K线（至少 120 条）
    pe_ttm: 当前市盈率（用于估值回归法；没有则只用技术面）
    max_loss_pct: 单笔最大可承受亏损比例（用于仓位计算）
    """
    if df is None or len(df) < 30:
        return {"error": "K线数据不足，无法计算价位"}

    close = df["close"].astype(float)
    price = float(close.iloc[-1])

    # ---------- 均线（支撑/阻力）
    ma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else price
    ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else price
    ma120 = float(close.rolling(120).mean().iloc[-1]) if len(close) >= 120 else price

    # ---------- ATR（波动幅度，用于止损）
    high, low = df["high"].astype(float), df["low"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else price * 0.02

    # ---------- 区间高低点
    def window_low(n):
        return float(close.iloc[-n:].min()) if len(close) >= n else float(close.min())

    def window_high(n):
        return float(close.iloc[-n:].max()) if len(close) >= n else float(close.max())

    low_60, low_250 = window_low(60), window_low(250)
    high_60, high_250, high_500 = window_high(60), window_high(250), window_high(500)

    # ---------- 支撑位候选（买入区间）
    supports = sorted({_round(x) for x in
                       [ma20, ma60, ma120, low_60, low_250]
                       if x and 0 < x < price})
    buy_low = supports[0] if supports else _round(price * 0.92)
    buy_high = supports[min(2, len(supports) - 1)] if supports else _round(price * 0.97)

    # ---------- 阻力位候选（目标价技术法）
    resistances = sorted({_round(x) for x in
                          [high_60, high_250, high_500]
                          if x and x > price})

    # ---------- 估值回归法（目标价）
    # TTM每股收益 = 现价 / 市盈率；目标价 = TTM每股收益 × 目标PE
    valuation_targets = []
    if pe_ttm and pe_ttm > 0:
        ttm_eps = price / pe_ttm
        for label, target_pe in [("PE回落至15倍", 15), ("PE回落至20倍", 20),
                                 ("PE回落至25倍", 25), ("PE回落至30倍", 30)]:
            tp = ttm_eps * target_pe
            if tp > price * 1.05:      # 只保留有上涨空间的
                valuation_targets.append({"method": label, "price": _round(tp)})
        valuation_targets.sort(key=lambda x: x["price"])

    target_candidates = []
    target_candidates += [{"method": "技术阻力位(近3月高点)", "price": _round(high_60)}] if high_60 > price else []
    target_candidates += [{"method": "技术阻力位(近1年高点)", "price": _round(high_250)}] if high_250 > price else []
    target_candidates += valuation_targets

    # ---------- 止损位
    stop_atr = price - 2 * atr
    stop_low = low_60 * 0.97
    stop_loss = _round(max(min(stop_atr, stop_low), price * 0.75))

    # ---------- 仓位建议（风险预算法）
    # 单笔最多亏 max_loss_pct 的账户资金；每股风险 = 现价 - 止损价
    risk_per_share = price - stop_loss
    if risk_per_share > 0:
        position_pct = min(30.0, max_loss_pct / (risk_per_share / price) * 100)
    else:
        position_pct = 10.0

    # ---------- 风险收益比
    first_target = target_candidates[0]["price"] if target_candidates else None
    rr = None
    if first_target and risk_per_share > 0:
        rr = _round((first_target - price) / risk_per_share, 2)

    return {
        "current_price": _round(price),
        "ma": {"ma20": _round(ma20), "ma60": _round(ma60), "ma120": _round(ma120)},
        "atr": _round(atr, 4),
        "atr_pct": _round(atr / price * 100, 2),
        "support_levels": supports[:4],
        "resistance_levels": resistances[:4],
        "buy_range": {"low": buy_low, "high": buy_high},
        "target_candidates": target_candidates[:4],
        "stop_loss": stop_loss,
        "stop_loss_pct": _round((stop_loss / price - 1) * 100, 2),
        "position_pct": _round(position_pct, 1),
        "risk_reward": rr,
        "pe_ttm": pe_ttm,
    }


def format_anchors_for_prompt(a: dict) -> str:
    """把锚点转成给 AI 看的文本（要求它只能从中选择）。"""
    if a.get("error"):
        return f"（无法计算价位：{a['error']}）"

    lines = [
        f"现价：{a['current_price']}",
        f"均线：MA20={a['ma']['ma20']}  MA60={a['ma']['ma60']}  MA120={a['ma']['ma120']}",
        f"ATR(14)={a['atr']}（占价格{a['atr_pct']}%）",
        f"支撑位候选：{a['support_levels']}",
        f"阻力位候选：{a['resistance_levels']}",
        f"系统计算的买入区间建议：{a['buy_range']['low']} ~ {a['buy_range']['high']}",
        "目标价候选（必须从这里选，或说明为何都不用）：",
    ]
    for t in a["target_candidates"]:
        lines.append(f"    - {t['method']}：{t['price']}")
    lines += [
        f"系统计算的止损位：{a['stop_loss']}（{a['stop_loss_pct']}%）",
        f"按风险预算法建议仓位：{a['position_pct']}%",
        f"风险收益比：{a['risk_reward']}",
    ]
    return "\n".join(lines)
