"""
价格类量化指标（从 K 线计算）。

设计原则：
  1. 纯函数，输入 DataFrame/Series，输出 dict，不碰网络和数据库
  2. 每个指标都写清楚公式和通俗解释
  3. 除零、数据不足等边界情况必须安全返回，不能崩

指标分四组：
  A. 收益与风险    B. 风险调整收益    C. 相对基准    D. 动量与技术
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252
RISK_FREE = 0.02


# ==================================================================
# 基础工具
# ==================================================================
def to_series(df_or_series) -> pd.Series:
    """把 K线 DataFrame 或 Series 统一成以日期为索引的收盘价序列。"""
    if isinstance(df_or_series, pd.DataFrame):
        s = df_or_series.set_index("trade_date")["close"].astype(float)
        s.index = pd.to_datetime(s.index)
        return s.sort_index()
    s = df_or_series.astype(float)
    if not isinstance(s.index, pd.DatetimeIndex):
        s.index = pd.to_datetime(s.index)
    return s.sort_index()


def daily_returns(s: pd.Series) -> pd.Series:
    """日收益率 = 今日/昨日 - 1"""
    r = s.pct_change(fill_method=None)
    return r.replace([np.inf, -np.inf], np.nan).dropna()


def _safe(x, default=0.0):
    """把 NaN/None/Inf 统一转成默认值。"""
    if x is None:
        return default
    try:
        f = float(x)
    except (TypeError, ValueError):
        return default
    if np.isnan(f) or np.isinf(f):
        return default
    return f


# ==================================================================
# A. 收益与风险
# ==================================================================
def total_return(s: pd.Series) -> float:
    """区间总收益 = 期末/期初 - 1"""
    if len(s) < 2:
        return 0.0
    return _safe(s.iloc[-1] / s.iloc[0] - 1)


def annual_return(s: pd.Series) -> float:
    """年化收益 = (1+总收益)^(252/天数) - 1"""
    if len(s) < 2:
        return 0.0
    total = total_return(s)
    years = len(s) / TRADING_DAYS
    if years <= 0 or total <= -1:
        return 0.0
    return _safe((1 + total) ** (1 / years) - 1)


def annual_volatility(s: pd.Series) -> float:
    """年化波动率 = 日收益率标准差 × √252"""
    r = daily_returns(s)
    if len(r) < 2:
        return 0.0
    return _safe(r.std(ddof=1) * np.sqrt(TRADING_DAYS))


def drawdown_series(s: pd.Series) -> pd.Series:
    """回撤序列 = 当前/历史最高 - 1（永远 ≤ 0）"""
    return s / s.cummax() - 1.0


def max_drawdown(s: pd.Series) -> float:
    """最大回撤：历史上最惨的一次跌幅"""
    if len(s) < 2:
        return 0.0
    return _safe(drawdown_series(s).min())


def drawdown_stats(s: pd.Series) -> dict:
    """
    回撤的三个补充统计：
      duration   当前/最长回撤持续了多久（交易日）
      recovery   从最深处恢复到前高用了多久
      current    当前是否还在回撤中
    """
    if len(s) < 2:
        return {"max_duration": 0, "recovery_days": None, "current_dd": 0.0}

    dd = drawdown_series(s)
    underwater = dd < -1e-9

    # 找最长连续水下区间
    max_dur = cur_dur = 0
    for flag in underwater:
        cur_dur = cur_dur + 1 if flag else 0
        max_dur = max(max_dur, cur_dur)

    # 最深处到恢复的交易日数
    trough_idx = dd.idxmin()
    after = s.loc[trough_idx:]
    peak_before = s.loc[:trough_idx].max()
    recovered = after[after >= peak_before]
    recovery = int(len(after.loc[:recovered.index[0]])) if len(recovered) else None

    return {
        "max_duration": int(max_dur),
        "recovery_days": recovery,
        "current_dd": _safe(dd.iloc[-1]),
    }


def downside_volatility(s: pd.Series) -> float:
    """下行波动率：只统计下跌日的波动（比总波动率更贴近真实风险感受）"""
    r = daily_returns(s)
    down = r[r < 0]
    if len(down) < 2:
        return 0.0
    return _safe(down.std(ddof=1) * np.sqrt(TRADING_DAYS))


def var_cvar(s: pd.Series, level: float = 0.95) -> tuple[float, float]:
    """
    VaR（在险价值）与 CVaR（条件在险价值/期望损失）。

    VaR(95%) = 最差的5%交易日里，损失的门槛值
    CVaR(95%) = 最差那5%的平均损失（比VaR更保守）

    通俗理解：VaR 告诉你"最差情况下会亏多少"，CVaR 告诉你"真的倒霉时平均亏多少"。
    """
    r = daily_returns(s)
    if len(r) < 20:
        return 0.0, 0.0
    var = _safe(np.percentile(r, (1 - level) * 100))
    tail = r[r <= var]
    cvar = _safe(tail.mean()) if len(tail) else var
    return var, cvar


def distribution_stats(s: pd.Series) -> dict:
    """
    收益分布形态：
      skewness 偏度：>0 右偏（大涨多于大跌）；<0 左偏（暴跌风险大）
      kurtosis 峰度：>3 尖峰厚尾（极端行情比正态分布更频繁）
    """
    r = daily_returns(s)
    if len(r) < 20:
        return {"skew": 0.0, "kurtosis": 0.0}
    return {"skew": _safe(r.skew()), "kurtosis": _safe(r.kurtosis() + 3)}


def win_rate(s: pd.Series) -> float:
    """日胜率"""
    r = daily_returns(s)
    if len(r) == 0:
        return 0.0
    return _safe((r > 0).sum() / len(r))


def profit_loss_ratio(s: pd.Series) -> float:
    """盈亏比 = 平均盈利幅度 / 平均亏损幅度"""
    r = daily_returns(s)
    wins, losses = r[r > 0], r[r < 0]
    if len(wins) == 0 or len(losses) == 0:
        return 0.0
    avg_loss = abs(losses.mean())
    if avg_loss == 0:
        return 0.0
    return _safe(wins.mean() / avg_loss)


def ulcer_index(s: pd.Series) -> float:
    """
    Ulcer 疼痛指数：回撤的"深度×持续时间"综合度量。

    比最大回撤更全面 —— 最大回撤只看最惨一刻，Ulcer 还考虑你痛苦了多久。
    """
    if len(s) < 2:
        return 0.0
    dd = drawdown_series(s) * 100
    return _safe(np.sqrt((dd ** 2).mean()))


# ==================================================================
# B. 风险调整收益
# ==================================================================
def sharpe(s: pd.Series) -> float:
    """夏普 = (年化收益 - 无风险利率) / 年化波动率"""
    vol = annual_volatility(s)
    if vol == 0:
        return 0.0
    return _safe((annual_return(s) - RISK_FREE) / vol)


def sortino(s: pd.Series) -> float:
    """索提诺 = (年化收益 - 无风险利率) / 下行波动率"""
    dv = downside_volatility(s)
    if dv == 0:
        return 0.0
    return _safe((annual_return(s) - RISK_FREE) / dv)


def calmar(s: pd.Series) -> float:
    """卡玛 = 年化收益 / |最大回撤|"""
    mdd = abs(max_drawdown(s))
    if mdd == 0:
        return 0.0
    return _safe(annual_return(s) / mdd)


def omega(s: pd.Series, threshold: float = 0.0) -> float:
    """Omega 比率 = 超过阈值的收益总和 / 低于阈值的损失总和"""
    r = daily_returns(s)
    if len(r) == 0:
        return 0.0
    gains = (r[r > threshold] - threshold).sum()
    losses = abs((r[r < threshold] - threshold).sum())
    if losses == 0:
        return 0.0
    return _safe(gains / losses)


# ==================================================================
# C. 相对基准
# ==================================================================
def beta_alpha(s: pd.Series, bench: pd.Series) -> tuple[float, float]:
    """
    Beta 与 Alpha（CAPM 模型）。

    Beta  = 标的与基准的协方差 / 基准方差
            >1 表示比大盘更激进，<1 更抗跌
    Alpha = 标的年化收益 - [无风险 + Beta×(基准年化 - 无风险)]
            超额收益，>0 表示跑赢了"应有的水平"
    """
    a, b = _align(s, bench)
    if len(a) < 30:
        return 0.0, 0.0

    ra, rb = daily_returns(a), daily_returns(b)
    idx = ra.index.intersection(rb.index)
    if len(idx) < 30:
        return 0.0, 0.0

    ra, rb = ra.loc[idx], rb.loc[idx]
    var_b = rb.var(ddof=1)
    if var_b == 0:
        return 0.0, 0.0

    beta = _safe(ra.cov(rb) / var_b)
    ra_ann = _safe((1 + ra.mean()) ** TRADING_DAYS - 1)
    rb_ann = _safe((1 + rb.mean()) ** TRADING_DAYS - 1)
    alpha = _safe(ra_ann - (RISK_FREE + beta * (rb_ann - RISK_FREE)))
    return beta, alpha


def tracking_error(s: pd.Series, bench: pd.Series) -> float:
    """跟踪误差 = 超额收益（标的-基准）的标准差 × √252"""
    a, b = _align(s, bench)
    if len(a) < 30:
        return 0.0
    ra, rb = daily_returns(a), daily_returns(b)
    idx = ra.index.intersection(rb.index)
    if len(idx) < 30:
        return 0.0
    diff = ra.loc[idx] - rb.loc[idx]
    return _safe(diff.std(ddof=1) * np.sqrt(TRADING_DAYS))


def information_ratio(s: pd.Series, bench: pd.Series) -> float:
    """信息比率 = 年化超额收益 / 跟踪误差"""
    te = tracking_error(s, bench)
    if te == 0:
        return 0.0
    a, b = _align(s, bench)
    if len(a) < 30:
        return 0.0
    excess = annual_return(a) - annual_return(b)
    return _safe(excess / te)


def treynor(s: pd.Series, bench: pd.Series) -> float:
    """特雷诺比率 = (年化收益 - 无风险利率) / Beta"""
    beta, _ = beta_alpha(s, bench)
    if beta == 0:
        return 0.0
    return _safe((annual_return(s) - RISK_FREE) / beta)


def relative_strength(s: pd.Series, bench: pd.Series, days: int = 60) -> float:
    """相对强弱 = 标的近N日涨幅 - 基准近N日涨幅（>0 表示跑赢）"""
    a, b = _align(s, bench)
    if len(a) <= days or len(b) <= days:
        return 0.0
    ra = _safe(a.iloc[-1] / a.iloc[-days] - 1)
    rb = _safe(b.iloc[-1] / b.iloc[-days] - 1)
    return _safe(ra - rb)


def _align(s: pd.Series, bench: pd.Series) -> tuple[pd.Series, pd.Series]:
    """按日期对齐标的与基准。"""
    if bench is None or len(bench) == 0:
        return s, pd.Series(dtype=float)
    idx = s.index.intersection(bench.index)
    if len(idx) == 0:
        return s, bench
    return s.loc[idx], bench.loc[idx]


# ==================================================================
# D. 动量与技术
# ==================================================================
def momentum(s: pd.Series, days: int) -> float:
    """N日动量 = 近N个交易日的涨幅"""
    if len(s) <= days:
        return 0.0
    return _safe(s.iloc[-1] / s.iloc[-days - 1] - 1)


def momentum_pack(s: pd.Series) -> dict:
    """1/3/6/12 月动量"""
    return {
        "mom_1m": momentum(s, 21),
        "mom_3m": momentum(s, 63),
        "mom_6m": momentum(s, 126),
        "mom_12m": momentum(s, 252),
    }


def rsi(s: pd.Series, n: int = 14) -> float:
    """
    RSI 相对强弱指标（0-100）。
        >70 超买（可能回调）
        <30 超卖（可能反弹）
    公式：RSI = 100 - 100/(1+RS)，RS = 平均涨幅/平均跌幅
    """
    if len(s) <= n:
        return 50.0
    delta = s.diff().dropna()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    last_gain, last_loss = gain.iloc[-1], loss.iloc[-1]
    if pd.isna(last_gain) or pd.isna(last_loss):
        return 50.0

    # 边界：完全没有下跌 -> RSI = 100（最强）；完全没有波动 -> 50（中性）
    if last_loss == 0:
        return 100.0 if last_gain > 0 else 50.0
    if last_gain == 0:
        return 0.0

    rs = last_gain / last_loss
    return _safe(100 - 100 / (1 + rs), 50.0)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    MACD 指标。
        DIF  = 12日EMA - 26日EMA
        DEA  = DIF 的9日EMA
        柱状 = (DIF - DEA) × 2
    DIF 上穿 DEA 叫"金叉"（看多），下穿叫"死叉"（看空）。
    """
    if len(s) < slow + signal:
        return {"dif": 0.0, "dea": 0.0, "hist": 0.0, "signal": "数据不足"}
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2

    d, e = _safe(dif.iloc[-1]), _safe(dea.iloc[-1])
    sig = "金叉偏多" if d > e else "死叉偏空"
    return {"dif": round(d, 4), "dea": round(e, 4), "hist": round(_safe(hist.iloc[-1]), 4), "signal": sig}


def ma_bias(s: pd.Series, n: int = 20) -> float:
    """
    均线乖离率 = (现价 - N日均线) / N日均线 × 100%
        >+10% 严重偏离（追高风险）
        <-10% 严重超跌
    """
    if len(s) < n:
        return 0.0
    ma = s.rolling(n).mean().iloc[-1]
    if pd.isna(ma) or ma == 0:
        return 0.0
    return _safe((s.iloc[-1] / ma - 1) * 100)


def position_52w(s: pd.Series) -> float:
    """
    52周价格位置（0-1）。
        1.0 = 处于一年最高点附近
        0.0 = 处于一年最低点附近
    """
    window = s.iloc[-252:] if len(s) >= 252 else s
    lo, hi = window.min(), window.max()
    if hi == lo:
        return 0.5
    return _safe((s.iloc[-1] - lo) / (hi - lo), 0.5)


def atr(df: pd.DataFrame, n: int = 14) -> float:
    """
    ATR 平均真实波幅：衡量日常波动幅度，用于设止损。
    真实波幅 = max(最高-最低, |最高-昨收|, |最低-昨收|)
    """
    if df is None or len(df) < n + 1:
        return 0.0
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return _safe(tr.rolling(n).mean().iloc[-1])


def atr_percent(df: pd.DataFrame, n: int = 14) -> float:
    """ATR 占价格的比例（%），便于跨标的比较波动大小。"""
    if df is None or len(df) == 0:
        return 0.0
    a = atr(df, n)
    price = float(df["close"].iloc[-1])
    if price == 0:
        return 0.0
    return _safe(a / price * 100)


# ==================================================================
# 汇总
# ==================================================================
def compute_price_metrics(df: pd.DataFrame, bench: pd.DataFrame | None = None) -> dict:
    """
    一次性算完所有价格类指标。

    df:    标的K线（必须含 trade_date/open/high/low/close/volume）
    bench: 基准K线（可选，如沪深300），用于 Beta/Alpha 等相对指标
    """
    s = to_series(df)
    if len(s) < 2:
        raise ValueError("K线数据不足（至少2个交易日）")

    bench_s = to_series(bench) if bench is not None and len(bench) > 0 else None

    r = daily_returns(s)
    var95, cvar95 = var_cvar(s, 0.95)
    dd = drawdown_stats(s)
    dist = distribution_stats(s)

    out = {
        # 基础
        "start_date": s.index[0].strftime("%Y-%m-%d"),
        "end_date": s.index[-1].strftime("%Y-%m-%d"),
        "days": int(len(s)),
        "latest_close": round(float(s.iloc[-1]), 4),
        # 最后一个交易日的涨跌幅（前端显示在现价旁边用这个，
        # 不能用 total_return ——那是整段区间的收益，会误导用户以为是当日涨跌）
        "daily_change": (round(float(s.iloc[-1] / s.iloc[-2] - 1), 6)
                         if len(s) >= 2 else 0.0),

        # 收益与风险
        "total_return": round(total_return(s), 6),
        "annual_return": round(annual_return(s), 6),
        "annual_volatility": round(annual_volatility(s), 6),
        "max_drawdown": round(max_drawdown(s), 6),
        "max_dd_duration": dd["max_duration"],
        "recovery_days": dd["recovery_days"],
        "current_drawdown": round(dd["current_dd"], 6),
        "downside_volatility": round(downside_volatility(s), 6),
        "var_95": round(var95, 6),
        "cvar_95": round(cvar95, 6),
        "skew": round(dist["skew"], 4),
        "kurtosis": round(dist["kurtosis"], 4),
        "win_rate": round(win_rate(s), 4),
        "profit_loss_ratio": round(profit_loss_ratio(s), 4),
        "ulcer_index": round(ulcer_index(s), 4),

        # 风险调整
        "sharpe": round(sharpe(s), 4),
        "sortino": round(sortino(s), 4),
        "calmar": round(calmar(s), 4),
        "omega": round(omega(s), 4),

        # 动量与技术
        **{k: round(v, 6) for k, v in momentum_pack(s).items()},
        "rsi_14": round(rsi(s), 2),
        "macd": macd(s),
        "ma_bias_20": round(ma_bias(s, 20), 4),
        "position_52w": round(position_52w(s), 4),
        "atr_14": round(atr(df), 4),
        "atr_pct": round(atr_percent(df), 4),
    }

    # 相对基准（有基准且数据足够才算）
    #
    # 注意：数据不足时必须返回 None 而不是 0。
    # Beta=0 在金融上是"与大盘完全无关"，这是个有意义的结论；
    # 而"算不出来"是另一回事。混在一起会让用户误读。
    enough = (bench_s is not None and len(bench_s) >= 30
              and len(s.index.intersection(bench_s.index)) >= 30)

    if enough:
        beta, alpha = beta_alpha(s, bench_s)
        out.update({
            "beta": round(beta, 4),
            "alpha": round(alpha, 6),
            "tracking_error": round(tracking_error(s, bench_s), 6),
            "information_ratio": round(information_ratio(s, bench_s), 4),
            "treynor": round(treynor(s, bench_s), 4),
            "relative_strength_60d": round(relative_strength(s, bench_s, 60), 6),
        })
    else:
        out.update({
            "beta": None, "alpha": None, "tracking_error": None,
            "information_ratio": None, "treynor": None, "relative_strength_60d": None,
        })

    return out
