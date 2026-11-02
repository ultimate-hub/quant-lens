"""
指标计算模块：把"一串净值数字"变成"这只基金好不好"的结论。

这是整个软件的核心价值。所有函数只依赖 pandas/numpy，不碰网络和数据库，
因此可以单独测试、单独复用。

输入统一约定：
    s = 一个 pandas.Series，索引是日期（升序），值是净值/收盘价
输出：dict（字典，键值对），可直接转成 JSON 返回给网页
"""

import numpy as np
import pandas as pd

from app.core.config import RISK_FREE_RATE, TRADING_DAYS_PER_YEAR


# ==================================================================
# 基础工具
# ==================================================================
def daily_returns(nav: pd.Series) -> pd.Series:
    """
    计算每日收益率。

    公式：今日收益率 = 今日净值 / 昨日净值 - 1
    例：昨天 1.00，今天 1.02 → 收益率 2%

    为什么要用收益率而不是净值？
      净值受基金成立时间和分红影响，不同基金不能直接比。
      收益率是"标准化"后的数字，任何基金都能横向比较。
    """
    rets = nav.pct_change(fill_method=None)
    return rets.replace([np.inf, -np.inf], np.nan).dropna()


def drawdown_series(nav: pd.Series) -> pd.Series:
    """
    计算回撤序列（当前值相对历史最高点跌了多少）。

    公式：回撤 = 当前净值 / 历史最高净值 - 1
    结果永远 ≤ 0。例如 -0.15 表示"从最高点跌了15%"
    """
    running_max = nav.cummax()      # cummax = 累计最大值，即"到目前为止的最高点"
    return nav / running_max - 1.0


# ==================================================================
# 单个指标
# ==================================================================
def total_return(nav: pd.Series) -> float:
    """区间总收益率：(期末/期初 - 1)"""
    if len(nav) < 2:
        return 0.0
    return float(nav.iloc[-1] / nav.iloc[0] - 1.0)


def annualized_return(nav: pd.Series) -> float:
    """
    年化收益率：把整段收益换算成"平均每年赚多少"。

    公式：(1 + 总收益率) ^ (252 / 交易日数) - 1
    为什么用 252？A股一年大约 250 个交易日，金融界统一用 252。
    """
    n = len(nav)
    if n < 2:
        return 0.0
    total = total_return(nav)
    years = n / TRADING_DAYS_PER_YEAR
    if years <= 0 or total <= -1:
        return 0.0
    return float((1 + total) ** (1 / years) - 1)


def annualized_volatility(nav: pd.Series) -> float:
    """
    年化波动率：衡量"净值上下颠簸的剧烈程度"，也就是风险。

    公式：日收益率的标准差 × √252
    标准差衡量离散程度；乘以 √252 是因为"方差随时长线性增长，标准差随√时长增长"。
    """
    rets = daily_returns(nav)
    if len(rets) < 2:
        return 0.0
    return float(rets.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def max_drawdown(nav: pd.Series) -> float:
    """
    最大回撤：历史上从最高点跌到最低点，最惨的一次跌了多少。

    这是投资者最该看的指标 —— 因为它决定"你能不能拿得住"。
    回撤 40% 意味着 100 万变 60 万，多数人会在中途割肉。
    """
    if len(nav) < 2:
        return 0.0
    return float(drawdown_series(nav).min())


def sharpe_ratio(nav: pd.Series, risk_free: float = RISK_FREE_RATE) -> float:
    """
    夏普比率：每承担 1 单位风险，能换来多少超额收益。

    公式：(年化收益 - 无风险利率) / 年化波动率
    经验判断：
      < 0    亏钱
      0 ~ 1  一般
      1 ~ 2  不错
      > 2    优秀（但要警惕回测过拟合）
    """
    vol = annualized_volatility(nav)
    if vol == 0:
        return 0.0
    return float((annualized_return(nav) - risk_free) / vol)


def sortino_ratio(nav: pd.Series, risk_free: float = RISK_FREE_RATE) -> float:
    """
    索提诺比率：夏普的改良版，只惩罚"下跌"的波动，不惩罚上涨的波动。

    公式：(年化收益 - 无风险利率) / 下行波动率
    比夏普更合理 —— 因为净值暴涨不该算作风险。
    """
    rets = daily_returns(nav)
    if len(rets) < 2:
        return 0.0
    downside = rets[rets < 0]
    if len(downside) < 2:
        return 0.0
    downside_vol = downside.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
    if downside_vol == 0:
        return 0.0
    return float((annualized_return(nav) - risk_free) / downside_vol)


def calmar_ratio(nav: pd.Series) -> float:
    """
    卡玛比率：年化收益 / 最大回撤绝对值。

    含义："为了赚这些钱，你最多要忍受多大痛苦"。
    经验：> 0.5 尚可，> 1 优秀。
    """
    mdd = abs(max_drawdown(nav))
    if mdd == 0:
        return 0.0
    return float(annualized_return(nav) / mdd)


def win_rate(nav: pd.Series) -> float:
    """日胜率：上涨的天数占全部交易日的比例。"""
    rets = daily_returns(nav)
    if len(rets) == 0:
        return 0.0
    return float((rets > 0).sum() / len(rets))


def best_worst_day(nav: pd.Series) -> tuple[float, float]:
    """单日最大涨幅、单日最大跌幅。"""
    rets = daily_returns(nav)
    if len(rets) == 0:
        return 0.0, 0.0
    return float(rets.max()), float(rets.min())


# ==================================================================
# 组合调用：一次性算出所有指标
# ==================================================================
def compute_all_metrics(nav: pd.Series) -> dict:
    """
    输入净值序列，输出完整的指标字典。
    网页端拿到这个字典直接展示成"指标卡片"。
    """
    nav = nav.dropna()
    if len(nav) < 2:
        raise ValueError("净值数据不足（至少需要2个交易日）")

    best, worst = best_worst_day(nav)
    return {
        "start_date": str(nav.index[0].date()) if hasattr(nav.index[0], "date") else str(nav.index[0]),
        "end_date": str(nav.index[-1].date()) if hasattr(nav.index[-1], "date") else str(nav.index[-1]),
        "days": int(len(nav)),
        "total_return": round(total_return(nav), 6),
        "annual_return": round(annualized_return(nav), 6),
        "annual_volatility": round(annualized_volatility(nav), 6),
        "max_drawdown": round(max_drawdown(nav), 6),
        "sharpe": round(sharpe_ratio(nav), 4),
        "sortino": round(sortino_ratio(nav), 4),
        "calmar": round(calmar_ratio(nav), 4),
        "win_rate": round(win_rate(nav), 4),
        "best_day": round(best, 6),
        "worst_day": round(worst, 6),
        "latest_nav": round(float(nav.iloc[-1]), 4),
    }


def score_fund(metrics: dict) -> dict:
    """
    基金综合评分（0-100）。这是产品的差异化卖点，权重可以按业务需求调整。

    评分维度与权重：
      收益能力 40%   —— 年化收益越高越好
      风险控制 30%   —— 最大回撤越小越好
      风险调整 30%   —— 夏普比率越高越好

    打分方式：分段线性映射（把指标值压缩到 0-100 分区间）
      - 年化收益：-10% 得 0 分，+30% 得 100 分
      - 最大回撤：0% 得 100 分，-50% 得 0 分
      - 夏普比率：-1 得 0 分，+2.5 得 100 分
    """
    def clamp(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))

    def scale(value: float, low: float, high: float) -> float:
        """把 value 从 [low, high] 映射到 [0, 100]"""
        if high == low:
            return 50.0
        return clamp((value - low) / (high - low) * 100, 0, 100)

    ret_score = scale(metrics["annual_return"], -0.10, 0.30)
    dd_score = scale(-abs(metrics["max_drawdown"]), -0.50, 0.0)   # 回撤越小分越高
    sharpe_score = scale(metrics["sharpe"], -1.0, 2.5)

    total = ret_score * 0.4 + dd_score * 0.3 + sharpe_score * 0.3

    if total >= 80:
        grade = "优秀"
    elif total >= 65:
        grade = "良好"
    elif total >= 50:
        grade = "一般"
    else:
        grade = "较差"

    return {
        "total_score": round(total, 1),
        "grade": grade,
        "breakdown": {
            "收益能力": round(ret_score, 1),
            "风险控制": round(dd_score, 1),
            "风险调整收益": round(sharpe_score, 1),
        },
    }
