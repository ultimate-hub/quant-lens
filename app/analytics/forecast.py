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

from app.analytics import garch


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
    seed_salt: str = "",
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

    rng = np.random.default_rng(
        _seed_for(symbol + seed_salt, date.today().isoformat()))

    # ---- GARCH 波动率调整 ----
    # 自举用的是"历史平均"的波动水平，如果当前正处在高波动或低波动阶段，
    # 画出来的带子就会偏窄或偏宽。用 GARCH 估出的当前条件波动 / 历史均值
    # 作为缩放系数，把带子宽度校正到"当下这个状态"。
    g = garch.estimate_garch11(rets)
    vol_scale = 1.0
    if g.get("ok"):
        vol_scale = float(g["vol_ratio"])
        # 夹一下，防止极端值把模拟放大到不合理的程度
        vol_scale = float(min(max(vol_scale, 0.5), 2.5))

    # ---- 块自举：抽 block 天的连续片段，保留短期自相关 ----
    block = max(1, min(block, days))
    n_blocks = int(np.ceil(days / block))

    paths = np.empty((sims, days), dtype=float)
    for i in range(sims):
        starts = rng.integers(0, n - block, size=n_blocks)
        sampled = np.concatenate([rets[st:st + block] for st in starts])[:days]
        if vol_scale != 1.0:
            sampled = sampled * vol_scale
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
        # 波动率模型：用来自检"当下的波动比平时高还是低"
        "vol_model": {
            "method": "GARCH(1,1) 方差目标法",
            "ok": bool(g.get("ok")),
            "cond_vol": g.get("cond_vol"),
            "uncond_vol": g.get("uncond_vol"),
            "vol_ratio": g.get("vol_ratio"),
            "persistence": g.get("persistence"),
            "half_life": g.get("half_life"),
            "alpha": g.get("alpha"),
            "beta": g.get("beta"),
            "forecast_vol": g.get("forecast_vol"),
            "scale_applied": round(vol_scale, 4),
            "reason": g.get("reason") if not g.get("ok") else None,
        },
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


# ==================================================================
# 校准检验：回答「这个预测到底能不能信」
# ==================================================================
def calibrate(
    symbol: str,
    closes: pd.Series,
    days: int = 20,
    sims: int = 300,
    n_tests: int = 12,
    step: int = 5,
    min_history: int = 250,
) -> dict:
    """
    走查式校准检验（walk-forward calibration）。

    做法：
        在历史上取若干个「截止点 T」，只用 T 之前的数据做模拟，
        然后看真实的 T+days 收盘价落在哪个分位区间里。
        重复 n_tests 次，统计命中率。

    怎么读结果：
        50% 区间应该有约 50% 的次数罩住真实价；
        90% 区间应该有约 90%。
        命中率明显偏低 → 区间画窄了（模型过度自信，危险）
        命中率明显偏高 → 区间画宽了（模型过度保守，不够锐利）

    这是判断"预测靠不靠谱"的唯一诚实办法 ——
    不看模型多复杂，只看它过去罩住实际值的比例对不对。
    """
    s = pd.Series(closes).dropna()
    n = len(s)
    needed = min_history + days
    if n < needed:
        return {"ok": False,
                "reason": f"历史数据不足（{n} 条），至少需要 {needed} 条才能做校准"}

    hit50 = 0
    hit90 = 0
    tests = 0
    details: list[dict] = []

    for k in range(n_tests):
        cut = n - days - k * step
        if cut < min_history:
            break

        hist = s.iloc[:cut]
        actual = float(s.iloc[cut + days - 1])

        try:
            fc = simulate(symbol, hist, days=days, sims=sims,
                          block=5, seed_salt=f"|cal|{cut}")
        except (ValueError, Exception):  # noqa: BLE001
            continue

        b = fc["bands"]
        lo50, hi50 = b["p25"][-1], b["p75"][-1]
        lo90, hi90 = b["p05"][-1], b["p95"][-1]

        in50 = lo50 <= actual <= hi50
        in90 = lo90 <= actual <= hi90
        hit50 += int(in50)
        hit90 += int(in90)
        tests += 1

        details.append({
            "cutoff": str(hist.index[-1].date()) if isinstance(hist.index[-1], pd.Timestamp)
                      else str(hist.index[-1]),
            "start": round(float(hist.iloc[-1]), 2),
            "actual": round(actual, 2),
            "band50": [round(lo50, 2), round(hi50, 2)],
            "band90": [round(lo90, 2), round(hi90, 2)],
            "in50": bool(in50),
            "in90": bool(in90),
        })

    if not tests:
        return {"ok": False, "reason": "没有可用的回测样本"}

    r50 = hit50 / tests
    r90 = hit90 / tests

    # 给一个人话结论
    def _verdict(rate: float, expect: float) -> str:
        diff = rate - expect
        if abs(diff) <= 0.12:
            return "校准良好"
        if diff < -0.12:
            return "区间偏窄（高估自信）"
        return "区间偏宽（偏保守）"

    return {
        "ok": True,
        "tests": tests,
        "days": days,
        "sims": sims,
        "hit50": round(r50, 4),
        "hit90": round(r90, 4),
        "expected50": 0.50,
        "expected90": 0.90,
        "verdict50": _verdict(r50, 0.50),
        "verdict90": _verdict(r90, 0.90),
        "details": details,
        "how_to_read": (
            "50% 区间应当罩住约 50% 的实际值，90% 区间约 90%。"
            "命中率明显低于预期说明区间画窄了（模型过度自信），"
            "明显高于预期说明区间画宽了（偏保守）。"
        ),
    }