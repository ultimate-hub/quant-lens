"""
新闻源适配：从多个公开源抓取财经资讯。

四个源各有定位：
    东财关键词搜索  中文财经，能按关键词检索（最有用）
    华尔街见闻RSS   中文深度财经，64条/次
    新浪7x24        中文实时快讯，适合突发
    Google News     英文全球新闻，适合海外标的

统一返回 NewsItem，上层不关心来源差异。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field

import requests

TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_session = requests.Session()
_session.trust_env = False
_session.headers.update({"User-Agent": UA})


@dataclass
class NewsItem:
    """一条新闻。"""

    title: str
    url: str = ""
    summary: str = ""
    source: str = ""
    media: str = ""
    published_at: dt.datetime | None = None
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "summary": self.summary[:300],
            "source": self.source,
            "media": self.media,
            "published_at": (self.published_at.strftime("%Y-%m-%d %H:%M")
                             if self.published_at else ""),
        }


# ==================================================================
# 1. 东方财富关键词搜索
# ==================================================================
def search_eastmoney(keyword: str, limit: int = 20) -> list[NewsItem]:
    """
    东财站内搜索。实测：搜"固态电池"命中 3474 条。
    """
    param = json.dumps({
        "uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default",
            "pageIndex": 1, "pageSize": limit, "preTag": "", "postTag": ""}},
    }, ensure_ascii=False)

    try:
        r = _session.get("https://search-api-web.eastmoney.com/search/jsonp",
                         params={"cb": "jQuery", "param": param}, timeout=TIMEOUT)
        r.raise_for_status()
        m = re.search(r"jQuery\((.*)\)\s*;?\s*$", r.text, re.S)
        data = json.loads(m.group(1)) if m else {}
    except (requests.RequestException, json.JSONDecodeError, AttributeError):
        return []

    items = ((data.get("result") or {}).get("cmsArticleWebOld")) or []
    out = []
    for it in items:
        out.append(NewsItem(
            title=_clean(it.get("title", "")),
            url=it.get("url", ""),
            summary=_clean(it.get("content", "")),
            source="eastmoney",
            media=it.get("mediaName", ""),
            published_at=_parse_dt(it.get("date")),
            keywords=[keyword],
        ))
    return out


# ==================================================================
# 2. 华尔街见闻 RSS
# ==================================================================
def fetch_wallstreetcn(limit: int = 40) -> list[NewsItem]:
    try:
        r = _session.get("https://dedicated.wallstreetcn.com/rss.xml", timeout=TIMEOUT)
        r.raise_for_status()
        text = r.text
    except requests.RequestException:
        return []

    out = []
    for block in re.findall(r"<item>(.*?)</item>", text, re.S)[:limit]:
        title = _tag(block, "title")
        if not title:
            continue
        out.append(NewsItem(
            title=title,
            url=_tag(block, "link"),
            summary=_strip_html(_tag(block, "description"))[:300],
            source="wallstreetcn",
            media="华尔街见闻",
            published_at=_parse_dt(_tag(block, "pubDate")),
        ))
    return out


# ==================================================================
# 3. 新浪 7x24 快讯
# ==================================================================
def fetch_sina_flash(limit: int = 30) -> list[NewsItem]:
    try:
        r = _session.get("https://zhibo.sina.com.cn/api/zhibo/feed",
                         params={"page": 1, "page_size": limit, "zhibo_id": 152},
                         timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, json.JSONDecodeError):
        return []

    lst = (((data.get("result") or {}).get("data") or {})
           .get("feed", {}).get("list")) or []
    out = []
    for it in lst:
        text = _clean(it.get("rich_text", ""))
        if not text:
            continue
        out.append(NewsItem(
            title=text[:60],
            summary=text,
            source="sina",
            media="新浪财经",
            published_at=_parse_dt(it.get("create_time")),
        ))
    return out


# ==================================================================
# 4. Google News RSS（英文/全球）
# ==================================================================
def search_google_news(keyword: str, limit: int = 20,
                       hl: str = "en-US", gl: str = "US") -> list[NewsItem]:
    try:
        r = _session.get("https://news.google.com/rss/search",
                         params={"q": keyword, "hl": hl, "gl": gl,
                                 "ceid": f"{gl}:en"}, timeout=TIMEOUT)
        r.raise_for_status()
        text = r.text
    except requests.RequestException:
        return []

    out = []
    for block in re.findall(r"<item>(.*?)</item>", text, re.S)[:limit]:
        title = _tag(block, "title")
        if not title:
            continue
        out.append(NewsItem(
            title=title,
            url=_tag(block, "link"),
            summary="",
            source="google",
            media="Google News",
            published_at=_parse_dt(_tag(block, "pubDate")),
            keywords=[keyword],
        ))
    return out


# ==================================================================
# 工具
# ==================================================================
def _tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", block, re.S)
    return _clean(m.group(1)) if m else ""


def _clean(s: str) -> str:
    s = _strip_html(str(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return (s.replace("&nbsp;", " ").replace("&amp;", "&")
             .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))


def _parse_dt(v) -> dt.datetime | None:
    """解析各种时间格式。"""
    if not v:
        return None
    s = str(v).strip()

    formats = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in formats:
        try:
            d = dt.datetime.strptime(s, fmt)
            if d.tzinfo:
                d = d.astimezone().replace(tzinfo=None)
            return d
        except ValueError:
            continue
    return None
