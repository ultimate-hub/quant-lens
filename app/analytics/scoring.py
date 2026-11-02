"""
评分引擎：把几十个指标压缩成 6 个维度分 + 1 个综合分。

核心设计：可降级（graceful degradation）
  A股有完整基本面，美股只有价格数据。
  如果缺数据就记 0 分，美股会永远垫底 —— 这不公平。
  正确做法：**只在有数据的维度上加权平均**（权重重新归一化）。
  这样跨市场比较才公平，也诚实（界面上标注"该维度无数据"）。
"""

from __future__ import annotations

# ==================================================================
# 六个评分维度
# ==================================================================
DIMENSIONS = ["valuation", "growth", "quality", "momentum", "risk", "sentiment"]

DIMENSION_LABELS = {
    "valuation": "估值",
    "growth": "成长",
    "quality": "质量",
    "momentum": "动量",
    "risk": "风险",
    "sentiment": "AI情绪",
}

# 八套内置分析模式（一级权重，合计100）
PROFILES: dict[str, dict] = {
    "balanced": {
        "label": "均衡型", "desc": "不偏科，默认方案",
        "weights": {"valuation": 20, "growth": 20, "quality": 20,
                    "momentum": 15, "risk": 15, "sentiment": 10},
    },
    "value": {
        "label": "价值型", "desc": "找被低估的好公司",
        "weights": {"valuation": 35, "growth": 15, "quality": 25,
                    "momentum": 5, "risk": 15, "sentiment": 5},
    },
    "growth": {
        "label": "成长型", "desc": "找高增长，容忍高估值",
        "weights": {"valuation": 10, "growth": 40, "quality": 20,
                    "momentum": 20, "risk": 5, "sentiment": 5},
    },
    "quality": {
        "label": "质量型", "desc": "只买最优质的公司",
        "weights": {"valuation": 15, "growth": 15, "quality": 40,
                    "momentum": 5, "risk": 20, "sentiment": 5},
    },
    "momentum": {
        "label": "动量型", "desc": "追趋势，短线为主",
        "weights": {"valuation": 5, "growth": 15, "quality": 10,
                    "momentum": 45, "risk": 15, "sentiment": 10},
    },
    "lowvol": {
        "label": "低波型", "desc": "追求回撤小、拿得住",
        "weights": {"valuation": 20, "growth": 10, "quality": 25,
                    "momentum": 5, "risk": 35, "sentiment": 5},
    },
    "dividend": {
        "label": "红利型", "desc": "高股息、现金流稳",
        "weights": {"valuation": 30, "growth": 5, "quality": 25,
                    "momentum": 5, "risk": 20, "sentiment": 15},
    },
    "turnaround": {
        "label": "困境反转型", "desc": "极度低估+催化剂",
        "weights": {"valuation": 40, "growth": 20, "quality": 10,
                    "momentum": 10, "risk": 10, "sentiment": 10},
    },
}


# ==================================================================
# 打分工具
# ==================================================================
def scale(value, low: float, high: float) -> float:
    """
    把 value 从 [low, high] 线性映射到 [0, 100]，超出范围截断。

    反向指标（越小越好，如回撤）传 low > high 即可自动反向。
    """
    if value is None:
        return None
    if high == low:
        return 50.0
    x = (float(value) - low) / (high - low) * 100
    return max(0.0, min(100.0, x))


def band(value, points: list[tuple[float, float]]) -> float | None:
    """
    分段打分：points = [(阈值, 分数), ...] 按阈值升序。

    例：band(pe, [(10, 90), (20, 70), (30, 50), (50, 30), (999, 10)])
        表示 PE<10 得90分，10-20 得70分，以此类推。
    """
    if value is None:
        return None
    for threshold, score in points:
        if value <= threshold:
            return float(score)
    return float(points[-1][1])


def _avg(values: list[float | None]) -> float | None:
    """有效值的平均；全空返回 None。"""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


# ==================================================================
# 各维度打分
# ==================================================================
def score_valuation(m: dict) -> float | None:
    """
    估值分：PE/PB 越低越好，股息率越高越好，PEG 越低越好。
    小盘股 PE 天然更高，这里用较宽松的分段，避免一刀切。
    """
    parts = [
        band(m.get("pe_ttm"), [(10, 92), (15, 82), (20, 70), (30, 55), (45, 35), (70, 20), (1e9, 8)]),
        band(m.get("pb"), [(1, 90), (2, 78), (3, 65), (5, 48), (8, 30), (15, 15), (1e9, 8)]),
        band(-(m.get("dividend_yield") or 0), [(-6, 95), (-4, 85), (-2.5, 72), (-1.5, 60), (-0.5, 45), (0, 30)]),
        band(m.get("peg"), [(0.5, 92), (0.8, 82), (1.2, 70), (1.8, 52), (2.5, 35), (1e9, 18)]),
    ]
    return _avg(parts)


def score_growth(m: dict) -> float | None:
    """
    成长分：营收/净利同比增速越高越好。
    参考标准：增速>30% 优秀，15-30% 良好，0-15% 一般，负增长差。
    """
    parts = [
        band(m.get("revenue_yoy"), [(-20, 5), (0, 25), (10, 50), (20, 68), (35, 85), (60, 95), (1e9, 100)]),
        band(m.get("profit_yoy"), [(-30, 5), (0, 25), (10, 50), (20, 68), (35, 85), (60, 95), (1e9, 100)]),
    ]
    return _avg(parts)


def score_quality(m: dict) -> float | None:
    """
    质量分：ROE/ROIC 高、毛利净利高、负债低、现金流实。
    这是最"硬"的维度 —— 好公司长期一定在这上面得分高。
    """
    parts = [
        band(m.get("roe"), [(0, 5), (5, 25), (10, 50), (15, 68), (20, 82), (30, 95), (1e9, 100)]),
        band(m.get("roic"), [(0, 5), (5, 30), (10, 55), (15, 72), (25, 90), (1e9, 100)]),
        band(m.get("gross_margin"), [(10, 10), (20, 35), (30, 55), (45, 75), (60, 90), (1e9, 100)]),
        band(m.get("net_margin"), [(0, 5), (5, 30), (10, 52), (20, 75), (30, 90), (1e9, 100)]),
        # 负债率反向：越低越好
        band(m.get("debt_ratio"), [(20, 95), (35, 82), (50, 65), (65, 45), (80, 25), (1e9, 8)]),
        # 经营现金流/净利润：≥1 说明利润是真金白银
        band(m.get("ocf_to_profit"), [(0, 10), (0.5, 35), (0.8, 60), (1.0, 78), (1.5, 92), (1e9, 100)]),
    ]
    return _avg(parts)


def score_momentum(m: dict) -> float | None:
    """
    动量分：近3/6/12月涨幅 + RSI + 均线乖离 + 52周位置。
    注意 RSI 不是越高越好 —— 70以上超买反而扣分（避免追高）。
    """
    rsi = m.get("rsi_14")
    if rsi is None:
        rsi_score = None
    elif rsi >= 80:
        rsi_score = 25.0      # 严重超买
    elif rsi >= 70:
        rsi_score = 50.0      # 超买
    elif rsi >= 45:
        rsi_score = 85.0      # 健康强势
    elif rsi >= 30:
        rsi_score = 55.0
    else:
        rsi_score = 65.0      # 超卖，可能是机会

    parts = [
        band(m.get("mom_3m"), [(-0.3, 5), (-0.1, 25), (0, 45), (0.1, 62), (0.25, 82), (0.5, 95), (1e9, 100)]),
        band(m.get("mom_6m"), [(-0.4, 5), (-0.15, 25), (0, 45), (0.15, 62), (0.35, 82), (0.7, 95), (1e9, 100)]),
        band(m.get("mom_12m"), [(-0.5, 5), (-0.2, 25), (0, 45), (0.2, 62), (0.5, 82), (1.0, 95), (1e9, 100)]),
        rsi_score,
        band(m.get("position_52w"), [(0.1, 20), (0.3, 40), (0.5, 58), (0.7, 72), (0.9, 82), (1.0, 88)]),
    ]
    return _avg(parts)


def score_risk(m: dict) -> float | None:
    """
    风险分：波动率低、回撤小、夏普高、卡玛高 → 得分高。
    这是反向指标：风险越低分越高。
    """
    parts = [
        band(m.get("annual_volatility"), [(0.10, 95), (0.15, 85), (0.20, 72), (0.28, 55), (0.38, 35), (0.55, 15), (1e9, 5)]),
        band(m.get("max_drawdown"), [(-0.10, 95), (-0.20, 82), (-0.30, 68), (-0.45, 48), (-0.60, 28), (-0.80, 10)]),
        band(m.get("sharpe"), [(-1, 5), (0, 25), (0.5, 50), (1, 72), (1.5, 88), (2.5, 98), (1e9, 100)]),
        band(m.get("calmar"), [(0, 10), (0.3, 35), (0.6, 58), (1, 75), (2, 92), (1e9, 100)]),
        band(m.get("ulcer_index"), [(2, 95), (5, 80), (10, 62), (18, 42), (28, 22), (1e9, 8)]),
    ]
    return _avg(parts)


def score_sentiment(ai_score: float | None) -> float | None:
    """AI情绪分：直接采用 AI 给的 0-100 评分。"""
    if ai_score is None:
        return None
    return max(0.0, min(100.0, float(ai_score)))


# ==================================================================
# 综合评分
# ==================================================================
def score_all(price_m: dict, fund_m: dict, profile: str = "balanced",
              ai_score: float | None = None) -> dict:
    """
    计算全部维度分与综合分。

    price_m: compute_price_metrics 的输出
    fund_m:  compute_fundamental_metrics 的输出
    profile: PROFILES 中的键，或直接传自定义权重 dict
    ai_score: AI 定性评分（0-100），没有则 sentiment 维度缺席

    返回：
        {
          "dimensions": {维度: 分数 or None},
          "weights_used": {维度: 实际使用的权重},
          "composite": 综合分,
          "grade": 等级,
          "coverage": 有数据的维度数,
          "missing": [缺数据的维度],
        }
    """
    merged = {**fund_m, **price_m}      # 价格指标优先级更高（同名覆盖）

    dim_scores = {
        "valuation": score_valuation(merged),
        "growth": score_growth(merged),
        "quality": score_quality(merged),
        "momentum": score_momentum(merged),
        "risk": score_risk(merged),
        "sentiment": score_sentiment(ai_score),
    }

    if isinstance(profile, dict):
        raw_weights = profile
    else:
        raw_weights = PROFILES.get(profile, PROFILES["balanced"])["weights"]

    # 关键：只在有数据的维度上归一化权重
    available = {k: v for k, v in dim_scores.items() if v is not None}
    total_w = sum(raw_weights.get(k, 0) for k in available)

    if not available or total_w == 0:
        composite = 0.0
    else:
        composite = sum(available[k] * raw_weights.get(k, 0) for k in available) / total_w

    grade = _grade(composite)

    return {
        "dimensions": {k: (round(v, 1) if v is not None else None) for k, v in dim_scores.items()},
        "weights_used": {
            k: (round(raw_weights.get(k, 0) / total_w * 100, 1) if total_w else 0)
            for k in available
        },
        "composite": round(composite, 1),
        "grade": grade,
        "coverage": len(available),
        "missing": [k for k, v in dim_scores.items() if v is None],
    }


def _grade(score: float) -> str:
    if score >= 80:
        return "优秀"
    if score >= 65:
        return "良好"
    if score >= 50:
        return "一般"
    if score >= 35:
        return "偏弱"
    return "较差"


def list_profiles() -> list[dict]:
    """给前端用的模式列表。"""
    return [
        {"key": k, "label": v["label"], "desc": v["desc"], "weights": v["weights"]}
        for k, v in PROFILES.items()
    ]
