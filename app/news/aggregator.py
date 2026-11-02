"""
新闻聚合：多源抓取 → 去重 → 时间过滤 → 相关性排序 → 缓存。

为什么要去重？
    同一个新闻事件会被多家媒体转载，标题只差几个字。
    不去重的话，AI 摘要会重复分析同一件事，浪费 token 还降低质量。

为什么要有缓存？
    搜索一次要请求 4 个源（1-3秒）。用户反复搜索同一个词，
    或者 AI 分析时要用新闻，都走缓存（毫秒级）。
    缓存有效期 6 小时 —— 财经新闻半天内基本不会有大变化。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from difflib import SequenceMatcher

from app.core.db import db_session
from app.news import sector_map, sources
from app.news.sources import NewsItem

CACHE_TTL_HOURS = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_cache (
    keyword    TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


def _init() -> None:
    with db_session() as conn:
        conn.executescript(SCHEMA)


def search_news(keyword: str, days: int = 7, limit: int = 30,
                include_global: bool = True, use_cache: bool = True) -> dict:
    """
    搜索新闻并聚合。

    返回：
        {
          "keyword": ...,
          "count": 去重后的条数,
          "items": [新闻...],
          "sectors": [涉及的板块及其代表个股],
          "sources": {源: 条数},
          "cached": bool,
        }
    """
    _init()
    keyword = (keyword or "").strip()
    if not keyword:
        return {"keyword": "", "count": 0, "items": [], "sectors": [], "sources": {}}

    cache_key = f"{keyword}|{days}|{limit}|{int(include_global)}"

    if use_cache:
        cached = _read_cache(cache_key)
        if cached is not None:
            cached["cached"] = True
            return cached

    # ---------- 抓取
    collected: list[NewsItem] = []
    source_stat: dict[str, int] = {}

    def add(items: list[NewsItem], name: str):
        if items:
            source_stat[name] = len(items)
            collected.extend(items)

    add(sources.search_eastmoney(keyword, limit=limit), "东方财富")
    add(sources.fetch_wallstreetcn(limit=60), "华尔街见闻")
    add(sources.fetch_sina_flash(limit=50), "新浪快讯")
    if include_global:
        add(sources.search_google_news(keyword, limit=20), "Google News")

    # ---------- 去重 + 过滤 + 排序
    items = _dedupe(collected)
    items = _filter_recent(items, days)
    items = _rank(items, keyword)[:limit]

    # ---------- 板块映射
    text_all = " ".join(f"{i.title} {i.summary}" for i in items)
    sectors = sector_map.map_text(text_all)

    result = {
        "keyword": keyword,
        "count": len(items),
        "items": [i.to_dict() for i in items],
        "sectors": sectors,
        "sources": source_stat,
        "cached": False,
    }

    _write_cache(cache_key, result)
    return result


# ==================================================================
# 去重
# ==================================================================
def _norm_title(t: str) -> str:
    """标题归一化：去掉标点空格、统一小写，便于比较相似度。"""
    return re.sub(r"[\s\u3000,，。.、:：;；!！?？\"'“”‘’（）()\[\]【】\-—_/|]", "", t).lower()


def _dedupe(items: list[NewsItem], threshold: float = 0.82) -> list[NewsItem]:
    """
    相似标题去重。保留"信息量更大"的那条（摘要更长的优先）。

    用 SequenceMatcher 做模糊匹配，O(n²) 但对几十条数据完全够用。
    """
    kept: list[NewsItem] = []
    kept_norms: list[str] = []

    for item in items:
        if not item.title:
            continue
        norm = _norm_title(item.title)
        if not norm:
            continue

        dup_idx = None
        for i, existing in enumerate(kept_norms):
            if SequenceMatcher(None, norm, existing).ratio() >= threshold:
                dup_idx = i
                break

        if dup_idx is None:
            kept.append(item)
            kept_norms.append(norm)
        else:
            # 保留摘要更长的那条，但补上来源信息
            existing = kept[dup_idx]
            if len(item.summary) > len(existing.summary):
                item.source = _merge_sources(existing.source, item.source)
                kept[dup_idx] = item
            else:
                existing.source = _merge_sources(existing.source, item.source)

    return kept


def _merge_sources(a: str, b: str) -> str:
    """合并来源标签，避免出现 eastmoney+eastmoney 这种重复。"""
    parts = []
    for s in (a + "+" + b).split("+"):
        if s and s not in parts:
            parts.append(s)
    return "+".join(parts)


def _filter_recent(items: list[NewsItem], days: int) -> list[NewsItem]:
    """只保留最近 N 天的。没有时间信息的保留（宁可多不可少）。"""
    cutoff = dt.datetime.now() - dt.timedelta(days=days)
    out = []
    for i in items:
        if i.published_at is None or i.published_at >= cutoff:
            out.append(i)
    return out


# ==================================================================
# 排序
# ==================================================================
_SOURCE_WEIGHT = {
    "wallstreetcn": 1.2,
    "eastmoney": 1.0,
    "sina": 1.0,
    "google": 0.9,
}


def _rank(items: list[NewsItem], keyword: str) -> list[NewsItem]:
    """
    相关性排序。

    打分规则：
        标题命中关键词   +3/词
        摘要命中关键词   +1/词
        24小时内          +3
        3天内             +1
        来源权重          ×(0.9~1.2)
    """
    terms = [t for t in re.split(r"[\s,，]+", keyword) if t]
    now = dt.datetime.now()

    def score(item: NewsItem) -> float:
        s = 0.0
        title = item.title or ""
        summary = item.summary or ""
        for t in terms:
            if t in title:
                s += 3
            if t in summary:
                s += 1
        if item.published_at:
            age_h = (now - item.published_at).total_seconds() / 3600
            if age_h <= 24:
                s += 3
            elif age_h <= 72:
                s += 1
        src = item.source.split("+")[0]
        s *= _SOURCE_WEIGHT.get(src, 1.0)
        return s

    return sorted(items, key=score, reverse=True)


# ==================================================================
# 缓存
# ==================================================================
def _read_cache(key: str) -> dict | None:
    try:
        with db_session() as conn:
            row = conn.execute(
                "SELECT payload, created_at FROM news_cache WHERE keyword=?",
                (key,)).fetchone()
    except Exception:  # noqa: BLE001
        return None

    if not row:
        return None
    try:
        created = dt.datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None

    if (dt.datetime.now() - created).total_seconds() > CACHE_TTL_HOURS * 3600:
        return None

    try:
        return json.loads(row["payload"])
    except json.JSONDecodeError:
        return None


def _write_cache(key: str, payload: dict) -> None:
    try:
        with db_session() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO news_cache(keyword, payload, created_at)"
                " VALUES (?,?,datetime('now','localtime'))",
                (key, json.dumps(payload, ensure_ascii=False)))
    except Exception:  # noqa: BLE001
        pass


def clear_cache() -> None:
    _init()
    with db_session() as conn:
        conn.execute("DELETE FROM news_cache")
