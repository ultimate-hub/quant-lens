"""
P0 - 数据源连通性探测

目的：在写任何业务代码之前，先把所有候选数据源实测一遍。
      每个源都测「直连」和「走系统代理」两种方式，看哪个通。

为什么必须先做这一步？
  海外数据源（Yahoo / stooq / GDELT）在国内网络环境下不一定可达。
  如果先写好代码再发现不通，要返工。
  探测 10 分钟，能省掉几小时的返工。

运行：
    python tools/probe_sources.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

TIMEOUT = 20
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def make_session(use_proxy: bool) -> requests.Session:
    """
    use_proxy=False -> trust_env=False，忽略系统代理，直连
    use_proxy=True  -> trust_env=True，读取 HTTPS_PROXY 环境变量走代理
    """
    s = requests.Session()
    s.trust_env = use_proxy
    s.headers.update({"User-Agent": UA})
    return s


# ------------------------------------------------------------------
# 每个测试返回 (ok: bool, detail: str)
# ------------------------------------------------------------------
def t_tencent_a(s):
    r = s.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
              params={"param": "sh600519,day,,,5,qfq"}, timeout=TIMEOUT)
    d = r.json()["data"]["sh600519"]
    arr = d.get("qfqday") or d.get("day")
    return True, f"{len(arr)}条 末={arr[-1][0]} {arr[-1][2]}"


def t_tencent_hk(s):
    r = s.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
              params={"param": "hk00700,day,,,5,qfq"}, timeout=TIMEOUT)
    d = r.json()["data"]["hk00700"]
    arr = d.get("qfqday") or d.get("day")
    return True, f"{len(arr)}条 末={arr[-1][0]} {arr[-1][2]}"


def t_tencent_us(s):
    for sym in ("usAAPL.OQ", "usAAPL", "usAAPL.N"):
        try:
            r = s.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                      params={"param": f"{sym},day,,,5,qfq"}, timeout=TIMEOUT)
            d = r.json().get("data") or {}
            if isinstance(d, dict) and sym in d:
                node = d[sym]
                arr = node.get("qfqday") or node.get("day")
                if arr:
                    return True, f"symbol={sym} {len(arr)}条 末={arr[-1][0]} {arr[-1][2]}"
        except Exception:  # noqa: BLE001
            continue
    return False, "所有 symbol 格式都失败"


def t_stooq_us(s):
    r = s.get("https://stooq.com/q/d/l/", params={"s": "aapl.us", "i": "d"}, timeout=TIMEOUT)
    lines = r.text.strip().splitlines()
    return True, f"{len(lines)-1}行 表头={lines[0]} 末行={lines[-1] if len(lines)>1 else ''}"


def t_stooq_index(s):
    r = s.get("https://stooq.com/q/d/l/", params={"s": "^spx", "i": "d"}, timeout=TIMEOUT)
    lines = r.text.strip().splitlines()
    return True, f"{len(lines)-1}行 末行={lines[-1] if len(lines)>1 else ''}"


def t_yahoo(s):
    r = s.get("https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
              params={"range": "5d", "interval": "1d"}, timeout=TIMEOUT)
    d = r.json()["chart"]["result"][0]
    return True, f"{len(d['timestamp'])}个点 最新收盘={d['indicators']['quote'][0]['close'][-1]}"


def t_em_us(s):
    r = s.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
              params={"secid": "105.AAPL", "fields1": "f1,f2", "fields2": "f51,f52,f53",
                      "klt": 101, "fqt": 1, "beg": "20240101", "end": "20500101", "lmt": 5},
              timeout=TIMEOUT)
    d = r.json().get("data") or {}
    return bool(d.get("klines")), f"名称={d.get('name')} 条数={len(d.get('klines') or [])}"


def t_em_hk(s):
    r = s.get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
              params={"secid": "116.00700", "fields1": "f1,f2", "fields2": "f51,f52,f53",
                      "klt": 101, "fqt": 1, "beg": "20240101", "end": "20500101", "lmt": 5},
              timeout=TIMEOUT)
    d = r.json().get("data") or {}
    return bool(d.get("klines")), f"名称={d.get('name')} 条数={len(d.get('klines') or [])}"


def t_em_snapshot(s):
    r = s.get("https://push2.eastmoney.com/api/qt/stock/get",
              params={"secid": "1.600519",
                      "fields": "f57,f58,f162,f167,f173,f116,f117,f85"},
              timeout=TIMEOUT)
    d = r.json().get("data") or {}
    return bool(d), f"名称={d.get('f58')} PE={d.get('f162')} PB={d.get('f167')} ROE={d.get('f173')}"


def t_tencent_snapshot(s):
    r = s.get("https://qt.gtimg.cn/q=sh600519,hk00700,usAAPL", timeout=TIMEOUT)
    txt = r.content.decode("gbk", "ignore")
    n = txt.count("~")
    return n > 10, f"返回{len(txt)}字节 字段数~{n} 前120字={txt[:120]}"


def t_em_news(s):
    r = s.get("https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html",
              timeout=TIMEOUT)
    txt = r.text
    return "{" in txt, f"{len(txt)}字节 前80字={txt[:80]}"


def t_gdelt(s):
    r = s.get("https://api.gdeltproject.org/api/v2/doc/doc",
              params={"query": '"solid state battery"', "mode": "artlist",
                      "format": "json", "maxrecords": "5"},
              timeout=TIMEOUT)
    d = r.json()
    arts = d.get("articles") or []
    return len(arts) > 0, f"{len(arts)}篇 首篇={arts[0]['title'][:40] if arts else '-'}"


def t_sina_7x24(s):
    r = s.get("https://zhibo.sina.com.cn/api/zhibo/feed",
              params={"page": 1, "page_size": 5, "zhibo_id": 152}, timeout=TIMEOUT)
    d = r.json()
    items = ((d.get("result") or {}).get("data") or {}).get("feed", {}).get("list") or []
    return len(items) > 0, f"{len(items)}条 首条={(items[0].get('rich_text') or '')[:40] if items else '-'}"


def t_wscn_rss(s):
    r = s.get("https://dedicated.wallstreetcn.com/rss.xml", timeout=TIMEOUT)
    return "<rss" in r.text.lower(), f"{len(r.text)}字节"


def t_binance(s):
    r = s.get("https://api.binance.com/api/v3/klines",
              params={"symbol": "BTCUSDT", "interval": "1d", "limit": 5}, timeout=TIMEOUT)
    d = r.json()
    return isinstance(d, list) and len(d) > 0, f"{len(d)}根 末={d[-1][0] if d else '-'}"


def t_fx(s):
    r = s.get("https://api.exchangerate.host/latest", params={"base": "USD"}, timeout=TIMEOUT)
    d = r.json()
    return bool(d.get("rates")), f"CNY={d.get('rates', {}).get('CNY')}"


TESTS = [
    ("A股 腾讯K线",        t_tencent_a),
    ("港股 腾讯K线",        t_tencent_hk),
    ("美股 腾讯K线",        t_tencent_us),
    ("美股 东财K线",        t_em_us),
    ("港股 东财K线",        t_em_hk),
    ("美股 stooq CSV",     t_stooq_us),
    ("全球指数 stooq",      t_stooq_index),
    ("美股 Yahoo Chart",   t_yahoo),
    ("A股 东财快照(估值)",  t_em_snapshot),
    ("三市场 腾讯快照",      t_tencent_snapshot),
    ("A股 东财快讯",        t_em_news),
    ("全球 GDELT新闻",      t_gdelt),
    ("A股 新浪7x24",       t_sina_7x24),
    ("财经 华尔街见闻RSS",   t_wscn_rss),
    ("加密 Binance",       t_binance),
    ("外汇 exchangerate",  t_fx),
]


def main():
    results = []
    for use_proxy in (False, True):
        mode = "代理" if use_proxy else "直连"
        print(f"\n{'='*70}\n  {mode}模式\n{'='*70}")
        sess = make_session(use_proxy)
        for name, fn in TESTS:
            t0 = time.time()
            try:
                ok, detail = fn(sess)
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"{type(exc).__name__}: {str(exc)[:70]}"
            ms = (time.time() - t0) * 1000
            mark = "OK  " if ok else "FAIL"
            print(f"[{mark}] {name:20s} {ms:7.0f}ms  {detail[:80]}")
            results.append({"mode": mode, "name": name, "ok": ok, "ms": round(ms), "detail": detail})

    # 汇总：每个源在哪种模式下可用
    print(f"\n{'='*70}\n  汇总：每个源的最佳访问方式\n{'='*70}")
    names = [n for n, _ in TESTS]
    for name in names:
        direct = next((r for r in results if r["name"] == name and r["mode"] == "直连"), None)
        proxy = next((r for r in results if r["name"] == name and r["mode"] == "代理"), None)
        if direct and direct["ok"]:
            verdict = "直连可用"
        elif proxy and proxy["ok"]:
            verdict = "需代理"
        else:
            verdict = "不可用"
        print(f"{name:22s} -> {verdict}")

    out = Path(__file__).resolve().parent.parent / "data" / "probe_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n详细报告已保存: {out}")


if __name__ == "__main__":
    main()
