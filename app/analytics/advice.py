"""
配仓建议引擎。

设计原则（很重要）：
    1. **不用 AI 生成建议**。每个结论都由明确规则算出，并附上依据的数字，
       用户能逐条核对。AI 只在定性分析页写理由，不参与仓位决策。
    2. 给的是"区间 + 分批 + 触发条件"，不是"买 X 股"这种一次性指令。
       因为任何单点判断都可能是错的，分批能把错误成本摊薄。
    3. 说清楚"不知道的部分"：基本面缺失、模拟概率低，都要明说。

建议的等级：
    good    可以执行的正向信号
    info    中性说明
    warn    需要注意
    danger  风险警示
"""

from __future__ import annotations

# 动作定义：key -> (中文, 语气)
ACTIONS = {
    "build":  ("分批建仓", "good"),
    "add":    ("回踩加仓", "good"),
    "hold":   ("持有观望", "info"),
    "wait":   ("等待回落", "info"),
    "reduce": ("到目标价 · 减仓", "warn"),
    "stop":   ("止损离场", "danger"),
    "avoid":  ("不建议参与", "warn"),
}


def _pct(v, d=2) -> str:
    return "—" if v is None else f"{v * 100:.{d}f}%"


def _num(v, d=2) -> str:
    return "—" if v is None else f"{v:.{d}f}"


def build_advice(
    symbol: str,
    name: str,
    current_price: float,
    anchors: dict,
    scoring: dict,
    price_metrics: dict,
    fundamental_metrics: dict | None = None,
    forecast: dict | None = None,
) -> dict:
    """
    生成配仓建议。

    anchors / scoring / price_metrics 的结构和 /analyze 接口返回一致，
    这样前端不用做二次转换。
    """
    f = fundamental_metrics or {}
    items: list[dict] = []

    target = anchors.get("target_price")
    entry = anchors.get("entry_range") or {}
    stop = anchors.get("stop_loss")
    base_pos = anchors.get("position_pct")
    rr = anchors.get("risk_reward")

    e_low = entry.get("low")
    e_high = entry.get("high")

    composite = scoring.get("composite")
    grade = scoring.get("grade") or ""
    dims = scoring.get("dimensions") or {}

    rsi = price_metrics.get("rsi_14")
    vol = price_metrics.get("annual_volatility")
    mdd = price_metrics.get("max_drawdown")
    mom3 = price_metrics.get("mom_3m")
    pos52 = price_metrics.get("position_52w")

    # ------------------------------------------------------------
    # 1. 判断价格位置 → 主动作
    #
    #    视角说明：这个软件主要给"还没买、准备建仓"的人用。
    #    所以"现价高于买入区间"时，正确结论是「等待回落」而不是「持有」；
    #    "已到目标价"时，对未持仓的人是「不追高」，对已持仓的人才是「减仓」。
    # ------------------------------------------------------------
    action_key = "wait"

    if stop and current_price <= stop * 1.005:
        action_key = "stop"
    elif e_low and e_high:
        if current_price < e_low * 0.97:
            # 比买入区间还低：可能是机会，也可能是趋势坏了
            action_key = "build" if (composite is None or composite >= 50) else "wait"
        elif current_price <= e_high * 1.03:
            action_key = "build"
        elif target and current_price >= target * 0.95:
            action_key = "reduce"
        elif current_price <= e_high * 1.08:
            action_key = "add"
        else:
            action_key = "wait"          # 在区间上方、目标下方：不追高
    elif target and target > 0 and current_price >= target * 0.95:
        action_key = "reduce"

    if composite is not None and composite < 40 and action_key in ("build", "add"):
        action_key = "wait"

    action_label, action_tone = ACTIONS[action_key]

    # ------------------------------------------------------------
    # 2. 计算建议仓位
    # ------------------------------------------------------------
    pos = float(base_pos) if base_pos else 8.0

    # 评分调节
    if composite is not None:
        if composite >= 70:
            pos *= 1.0
        elif composite >= 55:
            pos *= 0.9
        elif composite >= 45:
            pos *= 0.7
        else:
            pos *= 0.45
            items.append({
                "level": "warn",
                "title": "综合评分偏低",
                "detail": f"综合分 {composite:.1f}（{grade}），按规则把建议仓位下调到基准的 45%",
                "basis": "评分 < 45 时降低仓位",
            })

    # 波动调节
    if vol is not None and vol > 0.35:
        pos *= 0.8
        items.append({
            "level": "warn",
            "title": "波动偏高",
            "detail": f"年化波动 {_pct(vol)}，超过 35% 的阈值，仓位再打 8 折",
            "basis": "高波动标的仓位 ×0.8",
        })

    # 超买调节
    if rsi is not None and rsi > 75:
        pos *= 0.7
        items.append({
            "level": "warn",
            "title": "短期超买",
            "detail": f"RSI(14) = {_num(rsi, 1)}，已进入超买区（>75），追高风险大，仓位 ×0.7",
            "basis": "RSI > 75 时降低仓位",
        })

    # 模拟概率调节
    if forecast and forecast.get("prob_hit_stop") is not None:
        if forecast["prob_hit_stop"] > 0.35:
            pos *= 0.8
            items.append({
                "level": "warn",
                "title": "触及止损的概率偏高",
                "detail": f"模拟 {forecast['sims']} 条路径中，有 "
                          f"{_pct(forecast['prob_hit_stop'], 1)} 在 {forecast['days']} 天内触及止损价",
                "basis": "蒙特卡洛模拟（非预测）",
            })

    pos = max(3.0, min(25.0, pos))
    pos = round(pos, 1)

    # ------------------------------------------------------------
    # 3. 分批计划
    # ------------------------------------------------------------
    plan: list[dict] = []
    if e_low and e_high and stop:
        # 三档：区间上沿 / 区间中值 / 区间下沿（贴近止损）
        # 不管当前动作是建仓还是等待，都把"该在哪买"画出来 —— 等待的人也需要知道目标位
        tiers = [
            (e_high, 0.4, "第一档：先建底仓，不赌方向"),
            (round((e_low + e_high) / 2, 2), 0.35, "第二档：回踩区间中值再补"),
            (round(max(stop * 1.02, e_low * 0.96), 2), 0.25,
             "第三档：靠近支撑/止损上方，最后的一笔"),
        ]
        for idx, (px, ratio, note) in enumerate(tiers, 1):
            plan.append({
                "tier": idx,
                "price": round(px, 2),
                "ratio": ratio,
                "amount_pct": round(pos * ratio, 1),
                "note": note,
            })

    # ------------------------------------------------------------
    # 4. 触发条件
    # ------------------------------------------------------------
    triggers: list[dict] = []
    if stop:
        triggers.append({
            "type": "stop", "price": round(stop, 2),
            "action": "跌破即离场，不摊平、不补仓",
        })
    if target:
        triggers.append({
            "type": "target", "price": round(target, 2),
            "action": "触及先减一半，剩余部分把止损上移到成本价",
        })
    if e_low and current_price > e_low * 1.1:
        triggers.append({
            "type": "pullback", "price": round(e_low, 2),
            "action": "回落到买入区间下沿附近，是第二档的加仓点",
        })

    # ------------------------------------------------------------
    # 5. 依据条目
    # ------------------------------------------------------------
    if e_low and e_high:
        if current_price <= e_high and current_price >= e_low:
            items.append({
                "level": "good",
                "title": "现价位于建议买入区间内",
                "detail": f"现价 {_num(current_price)}，买入区间 "
                          f"{_num(e_low)} ~ {_num(e_high)}",
                "basis": "由支撑位（均线 / 近 60 日低点）算出",
            })
        elif current_price > e_high:
            gap = (current_price / e_high - 1) * 100
            items.append({
                "level": "info",
                "title": "现价高于买入区间",
                "detail": f"现价 {_num(current_price)} 比区间上沿 {_num(e_high)} 高 {gap:.1f}%，"
                          f"此时买入等于追高",
                "basis": "价格位置对比",
            })
        else:
            gap = (e_low / current_price - 1) * 100
            items.append({
                "level": "info",
                "title": "现价低于买入区间",
                "detail": f"比区间下沿 {_num(e_low)} 还低 {gap:.1f}%，"
                          f"要么是机会，要么是基本面/趋势出了问题",
                "basis": "价格位置对比",
            })

    if target and current_price > target * 1.01:
        items.append({
            "level": "info",
            "title": "已突破原目标价",
            "detail": f"现价 {_num(current_price)} 已高于系统算出的目标价 {_num(target)}，"
                      f"说明原来的阻力位被突破，锚点需要重算（当前价往往变成新的支撑）",
            "basis": "目标价由技术阻力位 / 估值回归位算出",
        })

    if rr is not None:
        if rr >= 1.5:
            items.append({
                "level": "good",
                "title": "风险收益比较优",
                "detail": f"风险收益比 {_num(rr)}（每承担 1 单位下行风险，对应 {_num(rr)} 单位上行空间）",
                "basis": "(目标价 - 现价) / (现价 - 止损价)",
            })
        elif rr < 1:
            items.append({
                "level": "warn",
                "title": "风险收益比不划算",
                "detail": f"风险收益比 {_num(rr)}，低于 1 意味着上行空间小于下行风险",
                "basis": "(目标价 - 现价) / (现价 - 止损价)",
            })

    if mdd is not None and mdd < -0.4:
        items.append({
            "level": "warn",
            "title": "历史回撤较深",
            "detail": f"区间最大回撤 {_pct(mdd)}，说明这只股票跌起来会很猛，"
                      f"实际持仓时要能承受同量级回撤",
            "basis": "历史价格序列计算",
        })

    if pos52 is not None:
        if pos52 > 0.85:
            items.append({
                "level": "warn",
                "title": "处于 52 周高位",
                "detail": f"52 周位置 {_pct(pos52, 0)}，接近一年最高点",
                "basis": "(现价 - 52周低) / (52周高 - 52周低)",
            })
        elif pos52 < 0.2:
            items.append({
                "level": "info",
                "title": "处于 52 周低位",
                "detail": f"52 周位置 {_pct(pos52, 0)}，接近一年最低点，"
                          f"需要判断是「被错杀」还是「基本面恶化」",
                "basis": "(现价 - 52周低) / (52周高 - 52周低)",
            })

    if mom3 is not None and abs(mom3) > 0.2:
        direction = "上涨" if mom3 > 0 else "下跌"
        items.append({
            "level": "info",
            "title": f"近 3 个月{direction} {abs(mom3) * 100:.1f}%",
            "detail": "动量较强，但动量策略在 A 股容易在反转时受伤，"
                      "建议配合止损使用",
            "basis": "3 个月动量",
        })

    if dims:
        weakest = min(((k, v) for k, v in dims.items()
                       if v is not None and k != "sentiment"), key=lambda x: x[1],
                      default=None)
        labels = {"valuation": "估值", "growth": "成长", "quality": "质量",
                  "momentum": "动量", "risk": "风险"}
        if weakest and weakest[1] < 45:
            items.append({
                "level": "warn",
                "title": f"{labels.get(weakest[0], weakest[0])}维度是明显短板",
                "detail": f"该维度只有 {weakest[1]:.1f} 分，是六维度里最低的一项",
                "basis": "六维度评分",
            })

    if fundamental_metrics is not None and not f.get("has_fundamentals"):
        items.append({
            "level": "info",
            "title": "该标的没有完整财务数据",
            "detail": "港股/美股等市场只提供 PE/PB，质量与成长维度会缺失，"
                      "评分可比性下降",
            "basis": "数据源能力限制",
        })

    if forecast:
        items.append({
            "level": "info",
            "title": f"{forecast['days']} 日模拟结果",
            "detail": f"中性路径 {_num(forecast['bands']['p50'][-1])}"
                      f"（{_pct(forecast['median_return'])}），"
                      f"上涨概率 {_pct(forecast['prob_up'], 1)}，"
                      f"5%~95% 区间 {_num(forecast['bands']['p05'][-1])} ~ "
                      f"{_num(forecast['bands']['p95'][-1])}",
            "basis": "历史日收益自举抽样，非预测",
        })

    # ------------------------------------------------------------
    # 6. 一句话总结
    # ------------------------------------------------------------
    if action_key == "stop":
        summary = (f"现价已跌破止损位 {_num(stop)}。未持仓不要抄底；"
                   f"已持仓按规则离场，不摊平")
    elif action_key == "reduce":
        summary = (f"现价已接近目标价 {_num(target)}。已持仓可先减一半锁定利润；"
                   f"未持仓不建议在这个位置新建仓")
    elif action_key == "build":
        summary = f"现价落在买入区间内，可按 {pos}% 的目标仓位分三档建仓"
    elif action_key == "add":
        summary = f"距买入区间上沿不远，回踩可加仓，总仓位控制在 {pos}% 以内"
    elif action_key == "wait":
        if e_high and current_price > e_high:
            gap = (current_price / e_high - 1) * 100
            summary = (f"现价高于买入区间上沿 {gap:.1f}%，此时买入属于追高。"
                       f"等回落到 {_num(e_high)} 附近再考虑，目标仓位 {pos}%")
        else:
            summary = (f"当前位置或评分不理想，建议等待。"
                       f"参考买入位 {_num(e_low)}，目标仓位 {pos}%")
    else:
        bits = ["持有为主，不加不减"]
        if stop:
            bits.append(f"跌破 {_num(stop)} 离场")
        if target:
            bits.append(f"触及 {_num(target)} 减仓")
        summary = "；".join(bits)

    return {
        "symbol": symbol,
        "name": name,
        "action": action_label,
        "action_key": action_key,
        "action_tone": action_tone,
        "position_pct": pos,
        "summary": summary,
        "items": items,
        "plan": plan,
        "triggers": triggers,
        "reference": {
            "current_price": current_price,
            "entry_low": e_low,
            "entry_high": e_high,
            "target_price": target,
            "stop_loss": stop,
            "risk_reward": rr,
            "composite": composite,
            "grade": grade,
        },
        "disclaimer": "以上为算法按固定规则生成的仓位参考，不构成投资建议。"
                      "分批与止损的具体执行需自行判断。",
    }