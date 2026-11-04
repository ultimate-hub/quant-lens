"""
走势推演：蒙特卡洛模拟。

必须先说清楚这件事的性质：
    这**不是预测**。没有任何方法能预测未来价格。
    这里做的是：假设"未来的日收益分布和过去相似"，用历史日收益做
    有放回抽样，生成 N 条可能的未来路径，画出它们的分布带。

为什么用自举（bootstrap）而不是正态分布（GBM）：
    股票日收益不是正态的，有厚尾、有波动聚集。直接用正态会低估极端行情。
    自举法不需要假设分布形状，直接复用真实历史收益样本，厚尾特性自然保留。

为什么固定随机种子：
    不固定的话，每次刷新页面数字都变，用户会以为程序在乱跳。
    种子由「代码 + 日期」生成 → 同一天内结果稳定，跨天自然更新。

已知局限（界面上也要写）：
    1. 只模拟价格，不模拟成交量、情绪、政策
    2. 假设未来收益分布与历史同分布 —— 结构性变化时失效
    3. 交易日按"跳过周末"计算，未考虑法定节假日
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd


def _seed_for(symbol: str, day: str) -> int:
    """由代码+日期生成稳定种子（同一天内结果固定）。"""
    raw = f"{symbol}|{day}".encode("utf-8")
    return int(hashlib.md5(raw).hexdigest()[:8], 16)


def _future_dates(start: date, n: int) -> list[str]:
    """生成未来 n 个"交易日"（只跳过周末，未考虑法定节假日）。"""
    out: list[str] = []
    d = start
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:          # 0-4 = 周一至周五
            out.append(d.isoformat())
    return out


def simulate(
    symbol: str,
    closes: pd.Series,
    days: int = 20,
    sims: int = 1000,
    target_price: float | None = None,
    stop_price: float | None = None,
    block: int = 5,
) -> dict:
    """
    蒙特卡洛推演。

    参数：
        closes        历史收盘价序列（前复权，按日期升序）
        days          推演未来多少个交易日
        sims          模拟路径条数（越大越平滑，1000 条足够）
        target_price  目标价（用于算"触及目标价的概率"）
        stop_price    止损价（用于算"先触及止损的概率"）
        block         块自举的块长度（5 = 保留一周内的短期相关性）

    返回：见模块文档
    """
    s = pd.Series(closes).dropna()
    if len(s) < 60:
        raise ValueError(f"历史数据不足（{len(s)} 条），至少需要 60 个交易日")

    prices = s.to_numpy(dtype=float)
    start_price = float(prices[-1])

    # 日对数收益
    rets = np.diff(np.log(prices))
    n = len(rets)

    rng = np.random.default_rng(_seed_for(symbol, date.today().isoformat()))

    # ---- 块自举：抽 block 天的连续片段，保留短期自相关 ----
    block = max(1, min(block, days))
    n_blocks = int(np.ceil(days / block))

    paths = np.empty((sims, days), dtype=float)
    for i in range(sims):
        starts = rng.integers(0, n - block, size=n_blocks)
        sampled = np.concatenate([rets[st:st + block] for st in starts])[:days]
        paths[i] = start_price * np.exp(np.cumsum(sampled))

    # ---- 分位数 ----
    pct = {
        "p05": np.percentile(paths, 5, axis=0),
        "p25": np.percentile(paths, 25, axis=0),
        "p50": np.percentile(paths, 50, axis=0),
        "p75": np.percentile(paths, 75, axis=0),
        "p95": np.percentile(paths, 95, axis=0),
    }

    end_prices = paths[:, -1]
    expected_return = float(np.mean(end_prices) / start_price - 1)
    prob_up = float(np.mean(end_prices > start_price))

    # ---- 触及目标价 / 止损价的概率 ----
    prob_target = None
    prob_stop = None
    if target_price and target_price > start_price:
        hit = np.max(paths, axis=1) >= target_price
        prob_target = float(np.mean(hit))
    if stop_price and stop_price < start_price:
        hit = np.min(paths, axis=1) <= stop_price
        prob_stop = float(np.mean(hit))

    # 先到哪个（用"路径上第一个触发的方向"判断）
    first_side = None
    if target_price and stop_price:
        t = np.argmax(paths >= target_price, axis=1)
        l = np.argmax(paths <= stop_price, axis=1)
        hit_t = np.max(paths, axis=1) >= target_price
        hit_l = np.min(paths, axis=1) <= stop_price
        # 两边都没碰到的记 None
        both_none = ~hit_t & ~hit_l
        first_t = hit_t & (~hit_l | (t < l))
        first_l = hit_l & (~hit_t | (l < t))
        first_side = {
            "target_first": int(np.sum(first_t)),
            "stop_first": int(np.sum(first_l)),
            "neither": int(np.sum(both_none)),
            "total": int(sims),
        }

    # ---- 历史参数（给界面显示用）----
    annual_vol = float(np.std(rets) * np.sqrt(252))
    annual_drift = float(np.mean(rets) * 252)

    return {
        "symbol": symbol,
        "method": "bootstrap",
        "block": block,
        "days": days,
        "sims": sims,
        "start_price": round(start_price, 4),
        "dates": [str(s.index[-1].date()) if isinstance(s.index[-1], pd.Timestamp)
                  else str(s.index[-1])] + _future_dates(date.today(), days),
        "bands": {k: [round(float(x), 4) for x in v] for k, v in pct.items()},
        "expected_price": round(float(np.mean(end_prices)), 4),
        "expected_return": round(expected_return, 6),
        "median_return": round(float(pct["p50"][-1] / start_price - 1), 6),
        "prob_up": round(prob_up, 4),
        "prob_hit_target": None if prob_target is None else round(prob_target, 4),
        "prob_hit_stop": None if prob_stop is None else round(prob_stop, 4),
        "first_hit": first_side,
        "annual_vol": round(annual_vol, 6),
        "annual_drift": round(annual_drift, 6),
        "history_days": int(len(s)),
        "note": "基于历史日收益自举抽样，不代表真实预测；未考虑节假日、政策与情绪",
    }


def scenarios(forecast: dict, target_price: float | None = None,
              stop_price: float | None = None) -> list[dict]:
    """
    把分位数翻译成三档情景，给建议栏用。

    悲观 = 5% 分位，中性 = 50% 分位，乐观 = 95% 分位
    """
    bands = forecast["bands"]
    start = forecast["start_price"]
    days = forecast["days"]

    def _ret(px: float) -> float:
        return px / start - 1

    out = [
        {"name": "悲观", "price": bands["p05"][-1], "ret": _ret(bands["p05"][-1]),
         "prob": "5% 的概率低于此价"},
        {"name": "中性", "price": bands["p50"][-1], "ret": _ret(bands["p50"][-1]),
         "prob": "最可能落到的位置"},
        {"name": "乐观", "price": bands["p95"][-1], "ret": _ret(bands["p95"][-1]),
         "prob": "5% 的概率高于此价"},
    ]
    for o in out:
        o["ret"] = round(o["ret"], 6)
        o["price"] = round(o["price"], 2)
        o["days"] = days
    return out