"""
P0 - 数据源连通性探测（第二轮：补测失败项 + 替代方案）

第一轮结论：
  ✅ 腾讯 K线 一个源覆盖 A股/港股/美股（usAAPL.OQ 格式）
  ✅ Yahoo Chart 美股可用（备用）
  ✅ 东财 push2 快照（估值数据）可用
  ✅ 东财快讯、新浪7x24、华尔街见闻RSS 可用
  ❌ stooq 返回HTML（不可用）
  ❌ GDELT 超时/返回非JSON
  ❌ Binance 地域限制
  ❌ exchangerate.host 无数据

本轮目标：为每个失败项找替代，并确认长历史/基本面能力。
"""

import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

TIMEOUT = 25
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def sess(proxy: bool = False) -> requests.Session:
    s = requests.Session()
    s.trust_env = proxy
    s.headers.update({"User-Agent": UA})
    return s


S = sess(False)


# ---------------------------------------------------------------- 长历史能力
def t_tencent_us_history(s):
    """腾讯美股能拿多长历史？"""
    r = s.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
              params={"param": "usAAPL.OQ,day,2015-01-01,2026-12-31,640,qfq"}, timeout=TIMEOUT)
    d = r.json()["data"]["usAAPL.OQ"]
    arr = d.get("qfqday") or d.get("day")
    return True, f"{len(arr)}条 {arr[0][0]} ~ {arr[-1][0]}"


def t_tencent_hk_history(s):
    r = s.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
              params={"param": "hk00700,day,2015-01-01,2026-12-31,640,qfq"}, timeout=TIMEOUT)
    d = r.json()["data"]["hk00700"]
    arr = d.get("qfqday") or d.get("day")
    return True, f"{len(arr)}条 {arr[0][0]} ~ {arr[-1][0]}"


def t_yahoo_10y(s):
    r = s.get("https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
              params={"range": "10y", "interval": "1d"}, timeout=TIMEOUT)
    d = r.json()["chart"]["result"][0]
    return True, f"{len(d['timestamp'])}个点 10年"


def t_yahoo_hk(s):
    r = s.get("https://query1.finance.yahoo.com/v8/finance/chart/0700.HK",
              params={"range": "1y", "interval": "1d"}, timeout=TIMEOUT)
    d = r.json()["chart"]["result"][0]
    return True, f"{len(d['timestamp'])}个点 最新={d['indicators']['quote'][0]['close'][-1]}"


def t_yahoo_cn(s):
    """A股在 Yahoo 上是 600519.SS 格式"""
    r = s.get("https://query1.finance.yahoo.com/v8/finance/chart/600519.SS",
              params={"range": "1y", "interval": "1d"}, timeout=TIMEOUT)
    d = r.json()["chart"]["result"][0]
    return True, f"{len(d['timestamp'])}个点"


# ---------------------------------------------------------------- 基本面
def t_em_us_snapshot(s):
    r = s.get("https://push2.eastmoney.com/api/qt/stock/get",
              params={"secid": "105.AAPL",
                      "fields": "f57,f58,f162,f167,f173,f116,f117"}, timeout=TIMEOUT)
    d = r.json().get("data") or {}
    return bool(d), f"{d.get('f58')} PE={d.get('f162')} PB={d.get('f167')} 市值={d.get('f116')}"


def t_em_hk_snapshot(s):
    r = s.get("https://push2.eastmoney.com/api/qt/stock/get",
              params={"secid": "116.00700",
                      "fields": "f57,f58,f162,f167,f173,f116,f117"}, timeout=TIMEOUT)
    d = r.json().get("data") or {}
    return bool(d), f"{d.get('f58')} PE={d.get('f162')} PB={d.get('f167')}"


def t_em_f10_finance(s):
    """东财 F10 财务摘要（ROE/营收/净利等）"""
    url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    params = {
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": "ALL",
        "filter": '(SECUCODE="600519.SH")',
        "pageNumber": 1, "pageSize": 5,
        "sortTypes": -1, "sortColumns": "REPORT_DATE",
        "source": "HSF10", "client": "PC",
    }
    r = s.get(url, params=params, timeout=TIMEOUT)
    d = r.json()
    rows = ((d.get("result") or {}).get("data")) or []
    if not rows:
        return False, f"无数据 {str(d)[:100]}"
    x = rows[0]
    return True, (f"{x.get('REPORT_DATE','')[:10]} 营收={x.get('TOTAL_OPERATE_INCOME')} "
                  f"净利={x.get('PARENT_NETPROFIT')} ROE={x.get('WEIGHTAVG_ROE')}")


def t_yahoo_fundamentals(s):
    """Yahoo 基本面（新版需要 crumb，测试是否可用）"""
    r = s.get("https://query1.finance.yahoo.com/v10/finance/quoteSummary/AAPL",
              params={"modules": "summaryDetail,defaultKeyStatistics,financialData"},
              timeout=TIMEOUT)
    d = r.json()
    if "quoteSummary" in d and (d["quoteSummary"].get("result")):
        res = d["quoteSummary"]["result"][0]
        sd = res.get("summaryDetail", {})
        return True, f"trailingPE={sd.get('trailingPE', {}).get('raw')}"
    return False, f"需要crumb/被拒: {str(d)[:100]}"


# ---------------------------------------------------------------- 新闻替代
def t_gdelt_alt(s):
    """GDELT 换 format=csv 或缩短查询"""
    for fmt in ("csv", "json"):
        try:
            r = s.get("https://api.gdeltproject.org/api/v2/doc/doc",
                      params={"query": "battery", "mode": "artlist",
                              "format": fmt, "maxrecords": "5"}, timeout=TIMEOUT)
            if r.status_code == 200 and len(r.text) > 50 and not r.text.startswith("<!"):
                return True, f"fmt={fmt} {len(r.text)}字节 前60={r.text[:60]}"
        except Exception:  # noqa: BLE001
            continue
    return False, "csv/json 都失败"


def t_wscn_rss_parse(s):
    """华尔街见闻 RSS 能否解析出条目"""
    import re
    r = s.get("https://dedicated.wallstreetcn.com/rss.xml", timeout=TIMEOUT)
    items = re.findall(r"<item>(.*?)</item>", r.text, re.S)
    titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", r.text, re.S)
    return len(items) > 0, f"{len(items)}条 首标题={(titles[1][:40] if len(titles)>1 else '-')}"


def t_sina_roll_news(s):
    """新浪财经滚动新闻 API"""
    r = s.get("https://feed.mix.sina.com.cn/api/roll/get",
              params={"pageid": 153, "lid": 1686, "num": 5, "page": 1}, timeout=TIMEOUT)
    d = r.json()
    items = ((d.get("result") or {}).get("data")) or []
    return len(items) > 0, f"{len(items)}条 首条={items[0].get('title', '')[:40] if items else '-'}"


def t_em_search_news(s):
    """东财新闻搜索（关键词检索）"""
    param = json.dumps({
        "uid": "", "keyword": "固态电池", "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                                       "pageIndex": 1, "pageSize": 5, "preTag": "", "postTag": ""}},
    }, ensure_ascii=False)
    r = s.get("https://search-api-web.eastmoney.com/search/jsonp",
              params={"cb": "jQuery", "param": param}, timeout=TIMEOUT)
    txt = r.text
    return "cmsArticleWebOld" in txt, f"{len(txt)}字节 前80={txt[:80]}"


def t_google_news_rss(s):
    r = s.get("https://news.google.com/rss/search",
              params={"q": "semiconductor", "hl": "en-US", "gl": "US", "ceid": "US:en"},
              timeout=TIMEOUT)
    return "<rss" in r.text.lower() or "<feed" in r.text.lower(), f"{len(r.text)}字节"


# ---------------------------------------------------------------- 加密/外汇替代
def t_binance_vision(s):
    r = s.get("https://data-api.binance.vision/api/v3/klines",
              params={"symbol": "BTCUSDT", "interval": "1d", "limit": 5}, timeout=TIMEOUT)
    d = r.json()
    return isinstance(d, list) and len(d) > 0, f"{len(d)}根"


def t_okx(s):
    r = s.get("https://www.okx.com/api/v5/market/candles",
              params={"instId": "BTC-USDT", "bar": "1D", "limit": 5}, timeout=TIMEOUT)
    d = r.json()
    return d.get("code") == "0", f"code={d.get('code')} {len(d.get('data') or [])}根"


def t_erapi(s):
    r = s.get("https://open.er-api.com/v6/latest/USD", timeout=TIMEOUT)
    d = r.json()
    return d.get("result") == "success", f"CNY={d.get('rates', {}).get('CNY')}"


TESTS = [
    ("腾讯美股 长历史",     t_tencent_us_history),
    ("腾讯港股 长历史",     t_tencent_hk_history),
    ("Yahoo 美股 10年",    t_yahoo_10y),
    ("Yahoo 港股 0700.HK", t_yahoo_hk),
    ("Yahoo A股 600519.SS", t_yahoo_cn),
    ("东财 美股快照",       t_em_us_snapshot),
    ("东财 港股快照",       t_em_hk_snapshot),
    ("东财 F10 财务数据",   t_em_f10_finance),
    ("Yahoo 基本面",       t_yahoo_fundamentals),
    ("GDELT 替代尝试",      t_gdelt_alt),
    ("华尔街见闻 RSS解析",   t_wscn_rss_parse),
    ("新浪 滚动新闻API",     t_sina_roll_news),
    ("东财 关键词新闻搜索",   t_em_search_news),
    ("Google News RSS",    t_google_news_rss),
    ("Binance 数据镜像",    t_binance_vision),
    ("OKX 加密K线",        t_okx),
    ("汇率 open.er-api",   t_erapi),
]


def main():
    results = []
    print(f"\n{'='*78}\n  第二轮探测（直连模式）\n{'='*78}")
    for name, fn in TESTS:
        t0 = time.time()
        try:
            ok, detail = fn(S)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {str(exc)[:70]}"
        ms = (time.time() - t0) * 1000
        print(f"[{'OK  ' if ok else 'FAIL'}] {name:22s} {ms:7.0f}ms  {str(detail)[:90]}")
        results.append({"name": name, "ok": ok, "ms": round(ms), "detail": str(detail)})

    ok_n = sum(1 for r in results if r["ok"])
    print(f"\n可用: {ok_n}/{len(results)}")
    out = Path(__file__).resolve().parent.parent / "data" / "probe_report2.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")


if __name__ == "__main__":
    main()
