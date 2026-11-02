"""
扩展指标（第二批）：统计、技术、行为金融类指标。

这批指标不参与综合评分（评分只用6个维度），
定位是"研究参考"——给愿意深挖的用户看更多证据。

实现原则：
    - 全部用 numpy/pandas 手写，不引入 statsmodels/arch/TA-Lib
      （这些库在 Windows 上安装容易失败，且大多只用到一两个函数）
    - 每个指标都写清楚公式和"怎么看"
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _safe(v, default=None):
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if np.isnan(f) or np.isinf(f):
        return default
    return f


def _r(x, digits=4):
    v = _safe(x)
    return round(v, digits) if v is not None else None


# ==================================================================
# 一、统计特征
# ==================================================================
def hurst_exponent(s: pd.Series) -> float | None:
    """
    Hurst 指数：判断价格序列是"趋势性"还是"均值回归"。

        H > 0.5  趋势持续（涨了还会涨）
        H = 0.5  随机游走
        H < 0.5  均值回归（涨多了会跌）

    算法：R/S 分析法，对多个时间尺度求 (极差/标准差) 的幂律指数。
    """
    if len(s) < 100:
        return None
    prices = np.asarray(s, dtype=float)
    lags = [int(n) for n in (8, 16, 32, 64) if n < len(prices) // 2]
    if len(lags) < 2:
        return None

    rs_values = []
    for lag in lags:
        chunks = [prices[i:i + lag] for i in range(0, len(prices) - lag, lag)]
        ratios = []
        for c in chunks:
            dev = c - c.mean()
            cum = np.cumsum(dev)
            r = cum.max() - cum.min()
            sd = c.std(ddof=1)
            if sd > 0:
                ratios.append(r / sd)
        if ratios:
            rs_values.append((lag, np.mean(ratios)))

    if len(rs_values) < 2:
        return None
    x = np.log([v[0] for v in rs_values])
    y = np.log([v[1] for v in rs_values])
    slope = np.polyfit(x, y, 1)[0]
    # R/S 分析法在短样本/强趋势下会估出 >1 的值（理论范围 0~1），需要钳制
    return _r(max(0.0, min(1.0, slope)), 3)


def autocorrelation(s: pd.Series, lag: int = 1) -> float | None:
    """
    自相关系数：今天的收益和昨天有多大关系。
        接近 0  无记忆（随机）
        > 0     动量效应
        < 0     反转效应
    """
    r = s.pct_change(fill_method=None).dropna()
    if len(r) < 30:
        return None
    return _r(r.autocorr(lag=lag), 4)


def variance_ratio(s: pd.Series, q: int = 5) -> float | None:
    """
    方差比：q 天收益的方差 / (q × 1天收益的方差)。
        = 1  随机游走
        > 1  趋势性
        < 1  均值回归
    """
    r = s.pct_change(fill_method=None).dropna()
    if len(r) < q * 20:
        return None
    var1 = r.var(ddof=1)
    if var1 == 0:
        return None
    varq = r.rolling(q).sum().dropna().var(ddof=1)
    return _r(varq / (q * var1), 3)


def half_life(s: pd.Series) -> float | None:
    """
    均值回归半衰期：偏离均值后，大约多少天回到一半。

    算法：对价格偏离做 AR(1) 回归，λ = 回归系数，
          半衰期 = -ln(2) / ln(λ)
    """
    if len(s) < 60:
        return None
    prices = np.asarray(s, dtype=float)
    lagged = prices[:-1]
    current = prices[1:]
    if lagged.std() == 0:
        return None
    beta = np.polyfit(lagged, current, 1)[0]
    if beta <= 0 or beta >= 1:
        return None
    return _r(-np.log(2) / np.log(beta), 1)


def jarque_bera(s: pd.Series) -> tuple[float, bool] | None:
    """
    Jarque-Bera 正态性检验。
        JB 越大、p 越小 → 越不符合正态分布
        不服从正态分布意味着"用标准差衡量风险不够"
    """
    r = s.pct_change(fill_method=None).dropna()
    n = len(r)
    if n < 30:
        return None
    skew = r.skew()
    kurt = r.kurtosis()          # pandas 已减 3，即超额峰度
    jb = n / 6 * (skew ** 2 + (kurt ** 2) / 4)
    # 卡方分布临界值：自由度2，95% 置信度 = 5.991
    return _r(jb, 2), bool(jb > 5.991)


def max_streaks(s: pd.Series) -> tuple[int, int]:
    """最长连涨天数、最长连跌天数。"""
    r = s.pct_change(fill_method=None).dropna()
    if len(r) == 0:
        return 0, 0
    signs = np.sign(r.values)
    best_win = best_loss = cur = 0
    prev = 0
    for sg in signs:
        if sg == prev and sg != 0:
            cur += 1
        else:
            cur = 1
            prev = sg
        if sg > 0:
            best_win = max(best_win, cur)
        elif sg < 0:
            best_loss = max(best_loss, cur)
    return int(best_win), int(best_loss)


# ==================================================================
# 二、技术指标
# ==================================================================
def kdj(df: pd.DataFrame, n: int = 9) -> dict:
    """
    KDJ 指标（A股最常用的超买超卖指标）。
        K > 80 超买，K < 20 超卖
        K 上穿 D 为金叉
    """
    if df is None or len(df) < n:
        return {"k": None, "d": None, "j": None, "signal": "数据不足"}
    low_n = df["low"].astype(float).rolling(n).min()
    high_n = df["high"].astype(float).rolling(n).max()
    rsv = (df["close"].astype(float) - low_n) / (high_n - low_n) * 100
    rsv = rsv.fillna(50)

    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d

    kk, dd = _safe(k.iloc[-1]), _safe(d.iloc[-1])
    sig = "金叉偏多" if (kk is not None and dd is not None and kk > dd) else "死叉偏空"
    return {"k": _r(kk, 2), "d": _r(dd, 2), "j": _r(j.iloc[-1], 2), "signal": sig}


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0) -> dict:
    """
    布林带。
        %B = (现价 - 下轨) / (上轨 - 下轨)
             >1 突破上轨（超买），<0 跌破下轨（超卖）
        带宽 = (上轨 - 下轨) / 中轨
             带宽极窄 = 波动收敛，往往预示变盘
    """
    if len(s) < n:
        return {"pct_b": None, "bandwidth": None, "upper": None, "lower": None}
    ma = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=1)
    upper = ma + k * sd
    lower = ma - k * sd
    last_close = float(s.iloc[-1])
    u, l, m = float(upper.iloc[-1]), float(lower.iloc[-1]), float(ma.iloc[-1])
    width = (u - l) / m if m else None
    pct_b = (last_close - l) / (u - l) if (u - l) != 0 else None
    return {"pct_b": _r(pct_b, 4), "bandwidth": _r(width, 4),
            "upper": _r(u, 2), "lower": _r(l, 2)}


def atr_adx(df: pd.DataFrame, n: int = 14) -> dict:
    """
    ADX / DMI 趋势强度。
        ADX > 25 趋势明确
        ADX < 20 震荡市（趋势策略容易亏）
        +DI > -DI 多头占优
    """
    if df is None or len(df) < n * 2:
        return {"adx": None, "plus_di": None, "minus_di": None, "trend": "数据不足"}

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)

    atr = tr.rolling(n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(n).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(n).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(n).mean()

    a = _safe(adx.iloc[-1])
    p = _safe(plus_di.iloc[-1])
    m = _safe(minus_di.iloc[-1])
    if a is None:
        trend = "数据不足"
    elif a >= 25:
        trend = "强趋势"
    elif a >= 20:
        trend = "弱趋势"
    else:
        trend = "震荡"
    return {"adx": _r(a, 2), "plus_di": _r(p, 2), "minus_di": _r(m, 2), "trend": trend}


def obv(df: pd.DataFrame) -> dict:
    """
    OBV 能量潮：成交量按涨跌方向累加。
        价涨量增 = 资金流入（健康）
        价涨量减 = 量价背离（警惕）
    """
    if df is None or len(df) < 20:
        return {"obv_trend": "数据不足", "divergence": None}
    close = df["close"].astype(float)
    vol = df["volume"].astype(float).fillna(0)
    direction = np.sign(close.diff().fillna(0))
    obv_series = (direction * vol).cumsum()

    # 用最近20天OBV斜率判断趋势
    recent = obv_series.iloc[-20:]
    x = np.arange(len(recent))
    slope = np.polyfit(x, recent.values, 1)[0] if recent.std() > 0 else 0

    price_slope = np.polyfit(x, close.iloc[-20:].values, 1)[0]

    if slope > 0 and price_slope > 0:
        trend, div = "量价齐升", False
    elif slope < 0 and price_slope < 0:
        trend, div = "量价齐跌", False
    elif slope < 0 < price_slope:
        trend, div = "价涨量缩（背离）", True
    elif slope > 0 > price_slope:
        trend, div = "价跌量增（吸筹）", True
    else:
        trend, div = "无明显信号", False
    return {"obv_trend": trend, "divergence": div}


def cci(df: pd.DataFrame, n: int = 20) -> float | None:
    """
    CCI 顺势指标。
        > 100 强势，< -100 弱势
    """
    if df is None or len(df) < n:
        return None
    tp = (df["high"].astype(float) + df["low"].astype(float)
          + df["close"].astype(float)) / 3
    ma = tp.rolling(n).mean()
    md = (tp - ma).abs().rolling(n).mean()
    denom = 0.015 * md
    if denom.iloc[-1] == 0:
        return None
    return _r((tp.iloc[-1] - ma.iloc[-1]) / denom.iloc[-1], 2)


def williams_r(df: pd.DataFrame, n: int = 14) -> float | None:
    """
    Williams %R：-100 ~ 0。
        > -20 超买，< -80 超卖
    """
    if df is None or len(df) < n:
        return None
    high_n = df["high"].astype(float).rolling(n).max().iloc[-1]
    low_n = df["low"].astype(float).rolling(n).min().iloc[-1]
    close = float(df["close"].iloc[-1])
    if high_n == low_n:
        return None
    return _r(-100 * (high_n - close) / (high_n - low_n), 2)


def volume_ratio(df: pd.DataFrame) -> float | None:
    """
    量比：最近5日平均成交量 / 最近20日平均成交量。
        > 1.5 明显放量，< 0.7 缩量
    """
    if df is None or len(df) < 20:
        return None
    vol = df["volume"].astype(float).fillna(0)
    v5 = vol.iloc[-5:].mean()
    v20 = vol.iloc[-20:].mean()
    if v20 == 0:
        return None
    return _r(v5 / v20, 3)


def amihud_illiquidity(df: pd.DataFrame) -> float | None:
    """
    Amihud 非流动性：|日收益| / 成交额，取平均。
        数值越大 = 越难买卖 = 流动性越差
    """
    if df is None or len(df) < 20 or "amount" not in df.columns:
        return None
    amount = df["amount"].astype(float)
    if amount.isna().all() or (amount <= 0).all():
        return None
    rets = df["close"].astype(float).pct_change(fill_method=None).abs()
    ratio = (rets / amount.replace(0, np.nan)).dropna()
    if len(ratio) == 0:
        return None
    # 放大到可读量级（乘以 1e8）
    return _r(ratio.mean() * 1e8, 4)


# ==================================================================
# 三、相对基准与行为特征
# ==================================================================
def benchmark_relations(s: pd.Series, bench: pd.Series | None) -> dict:
    """
    与基准的关系：
        相关系数    和基准同涨同跌的程度
        R²          基准能解释多少波动（越低说明越独立）
        上行捕获率  基准上涨时，标的涨了多少倍
        下行捕获率  基准下跌时，标的跌了多少倍
    """
    out = {"correlation": None, "r_squared": None,
           "up_capture": None, "down_capture": None}
    if bench is None or len(bench) < 30:
        return out

    idx = s.index.intersection(bench.index)
    if len(idx) < 30:
        return out

    a = s.loc[idx].pct_change(fill_method=None).dropna()
    b = bench.loc[idx].pct_change(fill_method=None).dropna()
    common = a.index.intersection(b.index)
    if len(common) < 30:
        return out
    a, b = a.loc[common], b.loc[common]

    corr = a.corr(b)
    out["correlation"] = _r(corr, 3)
    out["r_squared"] = _r(corr ** 2, 3) if _safe(corr) is not None else None

    up = b > 0
    down = b < 0
    if up.sum() > 5:
        out["up_capture"] = _r(a[up].mean() / b[up].mean(), 3) if b[up].mean() != 0 else None
    if down.sum() > 5:
        out["down_capture"] = _r(a[down].mean() / b[down].mean(), 3) if b[down].mean() != 0 else None
    return out


def seasonality(s: pd.Series) -> dict:
    """
    季节性效应：按月、按星期统计平均收益。
        月份效应：某些月份历史上偏强
        星期效应：周一/周五效应
    """
    r = s.pct_change(fill_method=None).dropna()
    if len(r) < 120:
        return {"best_month": None, "worst_month": None,
                "best_weekday": None, "worst_weekday": None}

    by_month = r.groupby(r.index.month).mean()
    by_weekday = r.groupby(r.index.dayofweek).mean()

    month_names = ["", "1月", "2月", "3月", "4月", "5月", "6月",
                   "7月", "8月", "9月", "10月", "11月", "12月"]
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    return {
        "best_month": month_names[int(by_month.idxmax())],
        "worst_month": month_names[int(by_month.idxmin())],
        "best_weekday": weekday_names[int(by_weekday.idxmax())],
        "worst_weekday": weekday_names[int(by_weekday.idxmin())],
        "month_returns": {month_names[int(k)]: _r(v * 100, 2)
                          for k, v in by_month.items()},
    }


def performance_ratios(s: pd.Series) -> dict:
    """
    几个业界常用的补充比率：
        疼痛指数    回撤深度的均方根（比最大回撤更全面）
        恢复因子    总收益 / |最大回撤|
        尾部比率    95分位收益 / |5分位损失|
        M²测度      把波动率调整到基准水平后的收益
    """
    if len(s) < 60:
        return {"pain_index": None, "recovery_factor": None,
                "tail_ratio": None, "m2": None}

    dd = s / s.cummax() - 1
    pain = np.sqrt((dd ** 2).mean())

    total = float(s.iloc[-1] / s.iloc[0] - 1)
    mdd = abs(float(dd.min()))
    recovery = total / mdd if mdd > 0 else None

    r = s.pct_change(fill_method=None).dropna()
    p95 = np.percentile(r, 95)
    p5 = np.percentile(r, 5)
    tail = p95 / abs(p5) if p5 != 0 else None

    # M²：年化收益 - 无风险 + (波动率基准/自身波动率) × (年化收益 - 无风险)
    vol = float(r.std(ddof=1) * np.sqrt(TRADING_DAYS))
    years = len(s) / TRADING_DAYS
    ann = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else 0
    rf = 0.02
    bench_vol = 0.20      # 假设基准波动率 20%
    m2 = ann - rf + (bench_vol / vol) * (ann - rf) if vol > 0 else None

    return {"pain_index": _r(pain, 4),
            "recovery_factor": _r(recovery, 3),
            "tail_ratio": _r(tail, 3),
            "m2": _r(m2, 4)}


# ==================================================================
# 汇总
# ==================================================================
def compute_extended(df: pd.DataFrame, bench: pd.DataFrame | None = None) -> dict:
    """一次性算出全部扩展指标。"""
    if df is None or len(df) < 30:
        return {}

    s = df.set_index("trade_date")["close"].astype(float)
    s.index = pd.to_datetime(s.index)
    s = s.sort_index()

    bench_s = None
    if bench is not None and len(bench) > 0:
        bench_s = bench.set_index("trade_date")["close"].astype(float)
        bench_s.index = pd.to_datetime(bench_s.index)
        bench_s = bench_s.sort_index()

    jb = jarque_bera(s)
    win_streak, loss_streak = max_streaks(s)

    out = {
        # 统计
        "hurst": hurst_exponent(s),
        "autocorr_1": autocorrelation(s, 1),
        "variance_ratio_5": variance_ratio(s, 5),
        "half_life_days": half_life(s),
        "jarque_bera": jb[0] if jb else None,
        "is_normal_dist": (not jb[1]) if jb else None,
        "max_win_streak": win_streak,
        "max_loss_streak": loss_streak,

        # 技术
        "kdj": kdj(df),
        "bollinger": bollinger(s),
        "adx": atr_adx(df),
        "obv": obv(df),
        "cci_20": cci(df),
        "williams_r": williams_r(df),
        "volume_ratio": volume_ratio(df),
        "amihud": amihud_illiquidity(df),

        # 相对基准
        **benchmark_relations(s, bench_s),

        # 行为
        **seasonality(s),
        **performance_ratios(s),
    }
    return out


EXTENDED_LABELS = {
    "hurst": ("Hurst指数", ">0.5趋势持续，<0.5均值回归"),
    "autocorr_1": ("收益自相关", ">0动量效应，<0反转效应"),
    "variance_ratio_5": ("方差比(5日)", ">1趋势性，<1均值回归"),
    "half_life_days": ("均值回归半衰期", "偏离后回到一半所需天数"),
    "jarque_bera": ("JB统计量", "越大越偏离正态分布"),
    "is_normal_dist": ("收益是否正态", "否=标准差不足以衡量风险"),
    "max_win_streak": ("最长连涨", "天数"),
    "max_loss_streak": ("最长连跌", "天数"),
    "kdj": ("KDJ", "K>80超买，K<20超卖"),
    "bollinger": ("布林带", "%B>1突破上轨，带宽收窄预示变盘"),
    "adx": ("ADX趋势强度", ">25强趋势，<20震荡市"),
    "obv": ("OBV能量潮", "量价是否配合"),
    "cci_20": ("CCI(20)", ">100强势，<-100弱势"),
    "williams_r": ("Williams %R", ">-20超买，<-80超卖"),
    "volume_ratio": ("量比", ">1.5放量，<0.7缩量"),
    "amihud": ("Amihud非流动性", "越大越难买卖"),
    "correlation": ("与基准相关性", "同涨同跌程度"),
    "r_squared": ("R²", "基准能解释多少波动"),
    "up_capture": ("上行捕获率", "基准涨1%，标的涨多少%"),
    "down_capture": ("下行捕获率", "基准跌1%，标的跌多少%"),
    "best_month": ("历史最强月份", "季节性"),
    "worst_month": ("历史最弱月份", "季节性"),
    "best_weekday": ("历史最强星期", "星期效应"),
    "worst_weekday": ("历史最弱星期", "星期效应"),
    "pain_index": ("疼痛指数", "回撤深度的均方根"),
    "recovery_factor": ("恢复因子", "总收益/最大回撤"),
    "tail_ratio": ("尾部比率", "95分位收益/5分位损失"),
    "m2": ("M²测度", "波动率调整后的收益"),
}


def count_indicators() -> dict:
    """统计指标数量（用于文档和界面展示）。"""
    return {
        "core_price": 30,
        "fundamental": 18,
        "extended": len(EXTENDED_LABELS),
        "total": 30 + 18 + len(EXTENDED_LABELS),
    }
