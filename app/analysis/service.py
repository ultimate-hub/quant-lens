"""
分析服务：把一个标的从"原始数据"一路处理到"最终推荐"。

流程：
    1. 取数据（K线 + 快照 + 基本面）
    2. 算指标（价格30+ + 基本面18）
    3. 算价格锚点（目标价/买入区间/止损位候选）
    4. 量化评分（6维度，不含AI）
    5. 组装事实清单 → 调 AI 做定性分析
    6. 校验 AI 输出（防幻觉：目标价必须在候选里）
    7. 合成最终结论（量化分 + AI分 加权）
"""

from __future__ import annotations

import time

import pandas as pd

from app.ai import gateway, prompts
from app.analytics import anchors as anchor_mod
from app.analytics import scoring
from app.analytics.fundamental import compute_fundamental_metrics
from app.analytics.price import compute_price_metrics
from app.analytics.extended import compute_extended
from app.markets import registry
from app.markets.symbols import MARKET_LABELS, parse

# AI 评分在综合分里的默认权重（sentiment 维度权重）
DEFAULT_AI_WEIGHT = 0.10


def analyze(symbol: str, profile: str = "balanced", channel_id: int | None = None,
            use_ai: bool = True, beg: str = "20190101",
            news_text: str | None = None) -> dict:
    """
    完整分析一个标的。

    即使 AI 不可用也会返回量化结果（use_ai=False 或 AI 失败时），
    保证软件在没配 key 的情况下依然能用。
    """
    t0 = time.time()
    sym = parse(symbol)

    # ---------- 1. 取数据
    df, kline_src = registry.get_kline(sym.raw, beg=beg)

    snap = None
    try:
        snap, _ = registry.get_snapshot(sym.raw)
    except Exception:  # noqa: BLE001
        pass

    fund = None
    if sym.is_a_share:
        try:
            fund, _ = registry.get_fundamentals(sym.raw)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 2. 算指标
    bench_df = _benchmark_for(sym.market)
    price_m = compute_price_metrics(df, bench_df)
    fund_m = compute_fundamental_metrics(fund, snap)
    extended_m = compute_extended(df, bench_df)

    # ---------- 3. 价格锚点
    anc = anchor_mod.compute_anchors(df, pe_ttm=fund_m.get("pe_ttm"))

    # ---------- 4. 量化评分（不含AI）
    quant = scoring.score_all(price_m, fund_m, profile=profile)

    name = (snap.name if snap and snap.name else df.attrs.get("name", "")) or sym.code

    result = {
        "symbol": sym.raw,
        "name": name,
        "market": sym.market,
        "market_label": MARKET_LABELS.get(sym.market, sym.market),
        "price_metrics": price_m,
        "fundamental_metrics": fund_m,
        "extended_metrics": extended_m,
        "anchors": anc,
        "quant_score": quant,
        "price_series": _downsample(df, max_points=600),
        "ai": None,
        "final": None,
        "meta": {
            "kline_source": kline_src,
            "kline_rows": len(df),
            "has_fundamentals": bool(fund_m.get("has_fundamentals")),
            "profile": profile,
            "elapsed_ms": 0,
        },
    }

    # ---------- 5. 调 AI
    if use_ai:
        try:
            ai_raw = _call_ai(sym, name, price_m, fund_m, anc, quant, channel_id, news_text)
            result["ai"] = ai_raw["parsed"]
            result["meta"].update({
                "ai_channel": ai_raw["channel"],
                "ai_latency_ms": ai_raw["latency_ms"],
                "ai_parse_method": ai_raw["parse_method"],
                "ai_tokens": ai_raw["tokens"],
                "ai_warnings": ai_raw["warnings"],
            })
        except Exception as exc:  # noqa: BLE001
            result["meta"]["ai_error"] = str(exc)[:300]

    # ---------- 6. 合成最终结论
    result["final"] = _compose_final(quant, anc, result["ai"], profile)
    result["meta"]["elapsed_ms"] = int((time.time() - t0) * 1000)
    return result


# ==================================================================
# AI 调用
# ==================================================================
def _call_ai(sym, name, price_m, fund_m, anc, quant,
             channel_id, news_text) -> dict:
    facts = {
        "symbol": sym.raw,
        "name": name,
        "market_label": MARKET_LABELS.get(sym.market, sym.market),
        "price_text": prompts.format_price_facts(price_m),
        "fundamental_text": prompts.format_fundamental_facts(fund_m),
        "score_text": prompts.format_score_facts(quant),
        "anchors_text": anchor_mod.format_anchors_for_prompt(anc),
        "news_text": news_text,
    }
    messages = prompts.build_analysis_prompt(facts)

    res = gateway.chat_with_fallback(messages, task="analysis",
                                     json_mode=True, preferred_id=channel_id)
    obj, method = res.as_json()

    warnings = []
    if obj is None:
        raise gateway.AIError(
            f"AI 返回无法解析为JSON（方法={method}）：{res.text[:200]}")

    # 有些模型会把结果包在数组里：[{...}]，取第一个对象
    if isinstance(obj, list):
        first = next((x for x in obj if isinstance(x, dict)), None)
        if first is None:
            raise gateway.AIError(
                f"AI 返回的JSON是数组且不含对象（方法={method}）：{res.text[:200]}")
        warnings.append("AI返回的是数组，已提取第一个对象")
        obj = first

    if not isinstance(obj, dict):
        raise gateway.AIError(
            f"AI 返回的JSON不是对象（方法={method}，类型={type(obj).__name__}）")

    parsed = _validate_ai_output(obj, anc, warnings)

    return {
        "parsed": parsed,
        "channel": res.channel.name,
        "latency_ms": res.latency_ms,
        "parse_method": method,
        "tokens": f"{res.prompt_tokens}+{res.completion_tokens}",
        "warnings": warnings,
    }


def _validate_ai_output(obj: dict, anc: dict, warnings: list) -> dict:
    """
    校验并规范化 AI 输出。

    最重要的一条：**目标价必须来自候选价位**。
    如果 AI 编了一个候选里没有的价格，标记出来并回退到第一个候选。
    """
    out: dict = {}

    # 评分与评级
    try:
        score = float(obj.get("ai_score", 50))
    except (TypeError, ValueError):
        score = 50.0
    out["ai_score"] = max(0.0, min(100.0, score))

    rating = str(obj.get("rating") or "中性")
    if rating not in ("强烈关注", "关注", "中性", "回避", "强烈回避"):
        warnings.append(f"评级'{rating}'不合法，已改为中性")
        rating = "中性"
    out["rating"] = rating

    try:
        conf = float(obj.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    out["confidence"] = max(0.0, min(1.0, conf))

    out["horizon"] = str(obj.get("horizon") or "")

    for key in ("bull_points", "bear_points", "catalysts", "risks"):
        val = obj.get(key)
        out[key] = [str(x) for x in val][:5] if isinstance(val, list) else []

    out["summary"] = str(obj.get("summary") or "")[:120]

    # ---------- 目标价校验（核心防幻觉）
    candidates = anc.get("target_candidates") or []
    cand_prices = [c["price"] for c in candidates]
    chosen = obj.get("chosen_target") or {}
    try:
        chosen_price = float(chosen.get("price"))
    except (TypeError, ValueError):
        chosen_price = None

    if chosen_price is None:
        out["chosen_target"] = None
        if candidates:
            warnings.append("AI未给出目标价，已使用系统第一候选")
            out["chosen_target"] = {**candidates[0], "source": "system"}
    elif cand_prices and not any(abs(chosen_price - p) / p < 0.005 for p in cand_prices):
        nearest = min(cand_prices, key=lambda p: abs(p - chosen_price))
        warnings.append(
            f"⚠️ AI给出的目标价{chosen_price}不在候选列表中（疑似编造），"
            f"已回退到最近的候选{nearest}")
        out["chosen_target"] = {
            "price": nearest,
            "method": "系统回退（AI目标价不在候选内）",
            "reason": str(chosen.get("reason") or "")[:60],
            "source": "fallback",
        }
    else:
        out["chosen_target"] = {
            "price": chosen_price,
            "method": str(chosen.get("method") or "")[:40],
            "reason": str(chosen.get("reason") or "")[:80],
            "source": "ai",
        }

    # ---------- 买入区间
    #
    # 校验：必须落在现价的 50%~105% 之间。
    # 实测过 AI 会给出完全离谱的区间（比如现价1355却给85~92），
    # 不加校验会把荒谬建议直接展示给用户。
    er = obj.get("entry_range") or {}
    price = anc.get("current_price")
    try:
        lo, hi = float(er.get("low")), float(er.get("high"))
        if lo > hi:
            lo, hi = hi, lo
        if price and not (price * 0.5 <= lo <= price * 1.05
                          and price * 0.5 <= hi <= price * 1.05):
            warnings.append(
                f"⚠️ AI给出的买入区间{lo}~{hi}偏离现价{price}过远，已改用系统支撑位区间")
            out["entry_range"] = anc.get("buy_range")
        else:
            out["entry_range"] = {"low": lo, "high": hi}
    except (TypeError, ValueError):
        out["entry_range"] = anc.get("buy_range")

    # ---------- 止损
    try:
        out["stop_loss"] = float(obj.get("stop_loss"))
    except (TypeError, ValueError):
        out["stop_loss"] = anc.get("stop_loss")

    price = anc.get("current_price")
    if price and out.get("stop_loss") and out["stop_loss"] >= price:
        warnings.append("止损价高于现价，已改用系统止损")
        out["stop_loss"] = anc.get("stop_loss")

    # ---------- 仓位
    try:
        pos = float(obj.get("position_pct"))
    except (TypeError, ValueError):
        pos = anc.get("position_pct", 10)
    out["position_pct"] = max(0.0, min(30.0, pos))

    return out


# ==================================================================
# 最终合成
# ==================================================================
def _compose_final(quant: dict, anc: dict, ai: dict | None, profile: str) -> dict:
    """
    最终结论 = 量化分 + AI分（AI分作为 sentiment 维度参与加权）。

    这样设计的好处：AI 不是"覆盖"量化结论，而是作为一个维度融入，
    权重的控制权始终在用户手里（通过分析模式）。
    """
    ai_score = ai.get("ai_score") if ai else None

    if ai_score is not None:
        merged = scoring.score_all(
            price_m={}, fund_m={}, profile=profile, ai_score=ai_score)
        # 上面只是取带AI的权重结构，实际分项沿用 quant 的维度分
        merged["dimensions"] = {**quant["dimensions"], "sentiment": ai_score}
        avail = {k: v for k, v in merged["dimensions"].items() if v is not None}
        weights = scoring.PROFILES.get(profile, scoring.PROFILES["balanced"])["weights"]
        total_w = sum(weights.get(k, 0) for k in avail)
        composite = (sum(avail[k] * weights.get(k, 0) for k in avail) / total_w
                     if total_w else 0)
        final_score = round(composite, 1)
    else:
        merged = quant
        final_score = quant["composite"]

    target = (ai or {}).get("chosen_target") or (
        (anc.get("target_candidates") or [{}])[0])

    return {
        "composite": final_score,
        "grade": scoring._grade(final_score),
        "quant_only": quant["composite"],
        "ai_only": ai_score,
        "rating": (ai or {}).get("rating") or "中性",
        "confidence": (ai or {}).get("confidence"),
        "horizon": (ai or {}).get("horizon"),
        "current_price": anc.get("current_price"),
        "target_price": target.get("price") if target else None,
        "target_method": target.get("method") if target else None,
        "entry_range": (ai or {}).get("entry_range") or anc.get("buy_range"),
        "stop_loss": (ai or {}).get("stop_loss") or anc.get("stop_loss"),
        "position_pct": (ai or {}).get("position_pct") or anc.get("position_pct"),
        "risk_reward": anc.get("risk_reward"),
        "summary": (ai or {}).get("summary", ""),
        "dimensions": merged["dimensions"],
        "weights_used": quant.get("weights_used"),
        "missing_dimensions": quant.get("missing"),
    }


# ==================================================================
# 工具
# ==================================================================
def _downsample(df, max_points: int = 600) -> list[dict]:
    """
    把K线降采样成前端画图用的点列。

    为什么要降采样？
        16年日线有4000+个点，全部传给前端会让 JSON 变大十几倍，
        而且屏幕像素根本画不出这么多点。600 个点足够画出形状。
    """
    if df is None or len(df) == 0:
        return []

    step = max(1, len(df) // max_points)
    sub = df.iloc[::step]
    # 确保最后一个点包含在内（否则图上会缺最新价）
    if sub.iloc[-1]["trade_date"] != df.iloc[-1]["trade_date"]:
        sub = pd.concat([sub, df.iloc[[-1]]])

    return [
        {"date": str(r.trade_date), "close": round(float(r.close), 3)}
        for r in sub.itertuples(index=False)
        if r.close is not None
    ]


def _benchmark_for(market: str):
    """
    按市场选基准指数。

    为什么用 ETF 而不是指数？
        实测发现 000300.SH（沪深300指数）在腾讯接口上取不到（指数不支持复权参数），
        Yahoo 上也只返回 1 个点。而 510300.SH（沪深300ETF）能正常取到 1892 条。
        ETF 走势与指数几乎完全一致，做 Beta/Alpha 计算完全够用。
    """
    mapping = {
        "SH": "510300.SH",     # 沪深300ETF
        "SZ": "510300.SH",
        "BJ": "510300.SH",
        "US": "SPY.US",        # 标普500ETF
        "HK": "2800.HK",       # 盈富基金（恒生指数ETF）
    }
    code = mapping.get(market)
    if not code:
        return None
    try:
        df, _ = registry.get_kline(code, beg="20190101")
        # 少于30条说明数据异常，不如不用（否则会算出假的Beta）
        return df if df is not None and len(df) >= 30 else None
    except Exception:  # noqa: BLE001
        return None
