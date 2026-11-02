"""
新闻 AI 归纳：把一堆新闻变成结构化情报。

输出结构：
    {
      "overall_sentiment": "利好" | "中性" | "利空",
      "sentiment_score": -100 ~ 100,
      "summary": "一句话总结",
      "key_events": [{event, impact, sectors, sentiment}],
      "hot_sectors": ["板块名"],
      "related_symbols": [{symbol, name, reason}],
      "credibility": 0~1
    }

关键设计：**AI 不可用时必须能用**
    没有配 key 时自动走规则引擎（关键词情绪打分），
    虽然粗糙，但至少能给用户一个方向，而不是报错。
"""

from __future__ import annotations

import json

from app.ai import gateway

# 情绪词库（规则引擎用）
POSITIVE_WORDS = [
    "利好", "上涨", "大涨", "涨停", "突破", "增长", "超预期", "创新高", "回暖",
    "扩产", "中标", "获批", "订单", "签约", "合作", "增持", "回购", "扭亏",
    "放量", "提价", "涨价", "景气", "复苏", "翻倍", "领涨",
]
NEGATIVE_WORDS = [
    "利空", "下跌", "大跌", "跌停", "跌破", "下滑", "低于预期", "创新低", "亏损",
    "减持", "处罚", "退市", "暴跌", "裁员", "停产", "违约", "爆雷", "下调",
    "缩量", "降价", "过剩", "衰退", "领跌", "调查", "问询",
]


SYSTEM_SUMMARIZER = """你是一位财经新闻分析师，负责把新闻聚类成结构化情报。

铁律：
1. 只依据提供的新闻内容判断，不许补充新闻里没有的信息。
2. 必须区分"事实"和"观点"：新闻里没说的，不要推测。
3. 如果新闻质量差或信息量不足，credibility 要打低分。
4. 只输出 JSON，不要任何解释文字。"""


def summarize(keyword: str, items: list[dict], sectors: list[dict],
              channel_id: int | None = None, use_ai: bool = True) -> dict:
    """归纳新闻。优先用 AI，失败自动降级为规则引擎。"""
    if not items:
        return _empty_result(keyword, "没有检索到相关新闻")

    if use_ai:
        try:
            return _ai_summarize(keyword, items, sectors, channel_id)
        except Exception as exc:  # noqa: BLE001
            res = _rule_summarize(keyword, items, sectors)
            res["ai_error"] = str(exc)[:200]
            return res

    return _rule_summarize(keyword, items, sectors)


# ==================================================================
# AI 版本
# ==================================================================
def _ai_summarize(keyword: str, items: list[dict], sectors: list[dict],
                  channel_id: int | None) -> dict:
    news_text = _format_news(items[:20])
    sector_text = _format_sectors(sectors)

    schema = """{
  "overall_sentiment": "利好"|"中性"|"利空",
  "sentiment_score": -100到100的整数,
  "summary": "一句话总结，50字以内",
  "key_events": [
    {"event": "事件描述，30字以内", "impact": "影响分析，30字以内",
     "sectors": ["涉及板块"], "sentiment": "利好"|"利空"|"中性"}
  ],
  "hot_sectors": ["最受关注的前3个板块"],
  "related_symbols": [
    {"symbol": "股票代码", "name": "名称", "reason": "为什么相关，20字以内"}
  ],
  "credibility": 0到1的小数
}"""

    user = (
        f"# 检索关键词\n{keyword}\n\n"
        f"# 新闻列表（共{len(items)}条，按相关性排序）\n{news_text}\n\n"
        f"# 系统识别到的相关板块与代表个股\n{sector_text}\n\n"
        f"# 输出要求\n严格按以下JSON输出，key_events 给3-5条，"
        f"related_symbols 给3-5只（只能从上面板块的代表个股里选）：\n{schema}"
    )

    res = gateway.chat_with_fallback(
        [{"role": "system", "content": SYSTEM_SUMMARIZER},
         {"role": "user", "content": user}],
        task="news_summary", json_mode=True, preferred_id=channel_id)

    obj, method = res.as_json()
    if obj is None:
        raise gateway.AIError(f"新闻摘要无法解析为JSON: {res.text[:150]}")

    return _normalize_ai(obj, keyword, sectors, res)


def _normalize_ai(obj: dict, keyword: str, sectors: list[dict], res) -> dict:
    """校验 AI 输出，防止字段缺失或类型错误。"""
    sentiment = str(obj.get("overall_sentiment") or "中性")
    if sentiment not in ("利好", "中性", "利空"):
        sentiment = "中性"

    try:
        score = int(float(obj.get("sentiment_score", 0)))
    except (TypeError, ValueError):
        score = 0
    score = max(-100, min(100, score))

    events = []
    for e in (obj.get("key_events") or [])[:6]:
        if not isinstance(e, dict):
            continue
        events.append({
            "event": str(e.get("event", ""))[:80],
            "impact": str(e.get("impact", ""))[:80],
            "sectors": [str(s) for s in (e.get("sectors") or [])][:3],
            "sentiment": str(e.get("sentiment", "中性")),
        })

    # 相关个股必须是系统给出的板块成分股（防止 AI 编造代码）
    valid_codes = {code for s in sectors for code, _ in s.get("stocks", [])}
    name_map = {code: name for s in sectors for code, name in s.get("stocks", [])}

    related = []
    for r in (obj.get("related_symbols") or [])[:5]:
        if not isinstance(r, dict):
            continue
        code = str(r.get("symbol", "")).strip()
        if code not in valid_codes:
            continue      # 不在候选池的直接丢弃
        related.append({
            "symbol": code,
            "name": name_map.get(code, str(r.get("name", ""))),
            "reason": str(r.get("reason", ""))[:60],
        })

    try:
        cred = float(obj.get("credibility", 0.5))
    except (TypeError, ValueError):
        cred = 0.5

    return {
        "method": "ai",
        "keyword": keyword,
        "overall_sentiment": sentiment,
        "sentiment_score": score,
        "summary": str(obj.get("summary", ""))[:150],
        "key_events": events,
        "hot_sectors": [str(s) for s in (obj.get("hot_sectors") or [])][:5],
        "related_symbols": related,
        "credibility": max(0.0, min(1.0, cred)),
        "channel": res.channel.name,
        "parse_method": res.parse_method if hasattr(res, "parse_method") else "",
    }


# ==================================================================
# 规则引擎版本（无 AI 时兜底）
# ==================================================================
def _rule_summarize(keyword: str, items: list[dict], sectors: list[dict]) -> dict:
    """
    基于关键词情绪词打分。

    虽然粗糙，但能给出方向。而且完全可解释 —— 用户能看到是哪些词贡献了分数。
    """
    pos_hits, neg_hits = [], []
    for it in items:
        text = f"{it.get('title', '')} {it.get('summary', '')}"
        for w in POSITIVE_WORDS:
            if w in text:
                pos_hits.append(w)
        for w in NEGATIVE_WORDS:
            if w in text:
                neg_hits.append(w)

    total = len(pos_hits) + len(neg_hits)
    if total == 0:
        score, sentiment = 0, "中性"
    else:
        score = int((len(pos_hits) - len(neg_hits)) / total * 100)
        if score >= 20:
            sentiment = "利好"
        elif score <= -20:
            sentiment = "利空"
        else:
            sentiment = "中性"

    # 用命中板块做"热点"
    hot = [s["sector"] for s in sectors[:3]]

    # 代表个股取前两个板块
    related = []
    for s in sectors[:2]:
        for code, name in s.get("stocks", [])[:2]:
            related.append({
                "symbol": code, "name": name,
                "reason": f"属于{s['sector']}板块（命中词：{'/'.join(s['matched_terms'][:2])}）",
            })

    # 摘要：取相关性最高的3条标题
    top_titles = [i.get("title", "")[:40] for i in items[:3]]
    summary = "；".join(t for t in top_titles if t)[:140]

    return {
        "method": "rule",
        "keyword": keyword,
        "overall_sentiment": sentiment,
        "sentiment_score": score,
        "summary": summary or "暂无足够信息生成摘要",
        "key_events": [
            {"event": i.get("title", "")[:60], "impact": "（规则引擎未分析影响）",
             "sectors": [], "sentiment": "中性"}
            for i in items[:5]
        ],
        "hot_sectors": hot,
        "related_symbols": related,
        "credibility": 0.3,      # 规则引擎可信度天然低
        "positive_words": pos_hits[:8],
        "negative_words": neg_hits[:8],
    }


# ==================================================================
# 格式化
# ==================================================================
def _format_news(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        when = it.get("published_at", "")
        src = it.get("media") or it.get("source", "")
        lines.append(f"{i}. [{when}] [{src}] {it.get('title', '')}")
        summ = (it.get("summary") or "").strip()
        if summ:
            lines.append(f"   摘要：{summ[:150]}")
    return "\n".join(lines)


def _format_sectors(sectors: list[dict]) -> str:
    if not sectors:
        return "（未识别到明确板块）"
    lines = []
    for s in sectors:
        stocks = "、".join(f"{code}({name})" for code, name in s.get("stocks", [])[:5])
        lines.append(f"- {s['sector']}（命中词：{'/'.join(s['matched_terms'])}）"
                     f"代表个股：{stocks}")
    return "\n".join(lines)


def _empty_result(keyword: str, reason: str) -> dict:
    return {
        "method": "none",
        "keyword": keyword,
        "overall_sentiment": "中性",
        "sentiment_score": 0,
        "summary": reason,
        "key_events": [],
        "hot_sectors": [],
        "related_symbols": [],
        "credibility": 0.0,
    }
