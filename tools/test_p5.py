"""P5 验收：新闻聚合与 AI 归纳。"""

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai import channels as ch
from app.core.db import init_db
from app.news import aggregator, ai_summary, sector_map

MOCK = "http://127.0.0.1:8899"


def sec(t):
    print(f"\n{'='*74}\n  {t}\n{'='*74}")


def show_bundle(b, limit=6):
    print(f"关键词: {b['keyword']}   去重后 {b['count']} 条   缓存={b['cached']}")
    print(f"来源分布: {b['sources']}")
    print(f"\n前{limit}条新闻:")
    for it in b["items"][:limit]:
        print(f"  [{it['published_at'][:16]}] ({it['source']}) {it['title'][:52]}")
    print(f"\n识别到的板块:")
    for s in b["sectors"]:
        stocks = "、".join(n for _, n in s["stocks"][:3])
        print(f"  {s['sector']:8s} 命中词={'/'.join(s['matched_terms'][:3])}  → {stocks}")


def main():
    init_db()
    aggregator.clear_cache()

    # ---------- 1. 真实抓取
    sec("1. 真实抓取：'固态电池'")
    t0 = time.time()
    b1 = aggregator.search_news("固态电池", days=30, limit=20)
    print(f"耗时 {time.time()-t0:.2f}s")
    show_bundle(b1)

    # ---------- 2. 缓存
    sec("2. 缓存命中")
    t0 = time.time()
    b2 = aggregator.search_news("固态电池", days=30, limit=20)
    print(f"第二次耗时 {time.time()-t0:.3f}s  缓存={b2['cached']}  条数={b2['count']}")
    print(f"{'✅ 缓存生效' if b2['cached'] and time.time()-t0 < 0.5 else '⚠️ 缓存未生效'}")

    # ---------- 3. 另一个关键词
    sec("3. 真实抓取：'人工智能'")
    b3 = aggregator.search_news("人工智能", days=30, limit=15)
    show_bundle(b3, limit=4)

    # ---------- 4. 规则引擎摘要
    sec("4. 规则引擎摘要（无AI时兜底）")
    rule = ai_summary.summarize("固态电池", b1["items"], b1["sectors"], use_ai=False)
    print(f"  方法: {rule['method']}")
    print(f"  情绪: {rule['overall_sentiment']} (分数 {rule['sentiment_score']})")
    print(f"  摘要: {rule['summary'][:80]}")
    print(f"  热点板块: {rule['hot_sectors']}")
    rel = [f"{r['symbol']}({r['name']})" for r in rule["related_symbols"]]
    print(f"  相关个股: {rel}")
    print(f"  正向词: {rule.get('positive_words')}")
    print(f"  负向词: {rule.get('negative_words')}")
    print(f"  可信度: {rule['credibility']}")

    # ---------- 5. AI 摘要
    sec("5. AI 摘要（模拟中转站）")
    for c in ch.list_channels():
        if c.name.startswith("MOCK-"):
            ch.delete_channel(c.id)
    ch.add_channel("MOCK-正常", f"{MOCK}/ok", "mock-large", "sk-1", is_default=True)

    ai_res = ai_summary.summarize("固态电池", b1["items"], b1["sectors"], use_ai=True)
    print(f"  方法: {ai_res['method']}   渠道={ai_res.get('channel')}")
    print(f"  情绪: {ai_res['overall_sentiment']} (分数 {ai_res['sentiment_score']})")
    print(f"  摘要: {ai_res['summary']}")
    print(f"  热点板块: {ai_res['hot_sectors']}")
    print(f"  关键事件 {len(ai_res['key_events'])} 条:")
    for e in ai_res["key_events"][:3]:
        print(f"    - {e['event'][:50]} | {e['sentiment']}")
    print(f"  相关个股: {[(r['symbol'], r['name']) for r in ai_res['related_symbols']]}")
    print(f"  可信度: {ai_res['credibility']}")

    # ---------- 6. 板块映射
    sec("6. 板块映射表")
    print(f"  内置板块数: {len(sector_map.all_sectors())}")
    for kw in ["白酒动销回暖", "算力需求爆发", "锂电隔膜涨价"]:
        hits = sector_map.map_keyword(kw)
        print(f"  '{kw}' -> {[(h['sector'], h['matched_terms'][:2]) for h in hits]}")

    # ---------- 7. AI 失败降级
    sec("7. AI 不可用时自动降级")
    for c in ch.list_channels():
        if c.name.startswith("MOCK-"):
            ch.delete_channel(c.id)
    degraded = ai_summary.summarize("固态电池", b1["items"], b1["sectors"], use_ai=True)
    print(f"  方法: {degraded['method']}  (应为 rule)")
    print(f"  情绪: {degraded['overall_sentiment']}")
    print(f"  降级原因: {degraded.get('ai_error', '')[:80]}")
    print(f"  {'✅ 正确降级' if degraded['method'] == 'rule' else '❌ 降级失败'}")

    print("\n" + "=" * 74)
    print("  P5 验收完成")
    print("=" * 74)


if __name__ == "__main__":
    main()
