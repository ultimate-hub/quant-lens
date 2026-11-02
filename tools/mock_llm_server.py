"""
模拟 AI 中转站服务器：用于在拿不到真实 key 的情况下验证网关。

模拟四种真实中转站行为：
    /ok/v1/chat/completions      完全规范的 OpenAI 兼容接口
    /picky/v1/chat/completions   拒绝 response_format 参数（返回400）—— 很常见
    /fenced/v1/chat/completions  忽略 JSON 模式，返回带 ```json 围栏和解释文字
    /v1/messages                 Anthropic 原生协议

启动：python tools/mock_llm_server.py [port]
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 静音
        pass

    # ------------------------------------------------------------ GET
    def do_GET(self):
        if self.path.endswith("/v1/models") or self.path.endswith("/models"):
            self._json(200, {"data": [
                {"id": "mock-model-large"}, {"id": "mock-model-small"}]})
            return
        self._json(404, {"error": "not found"})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", "ignore")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}

        path = self.path
        # 整个请求体的文本（用于判断是哪种任务）
        text_all = json.dumps(body, ensure_ascii=False)

        # ---------- Anthropic 协议
        if path.endswith("/v1/messages") or path.endswith("/messages"):
            self._json(200, {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": '{"ok": true, "from": "anthropic"}'}],
                "usage": {"input_tokens": 12, "output_tokens": 8},
            })
            return

        # ---------- 新闻摘要请求
        if "财经新闻分析师" in text_all:
            import re
            codes = re.findall(r"([0-9]{6}\.(?:SH|SZ|HK)|[A-Z]{1,5}\.US)\(([^)]+)\)", text_all)
            related = [{"symbol": c, "name": n, "reason": "板块代表个股"}
                       for c, n in codes[:3]]
            news = {
                "overall_sentiment": "利好",
                "sentiment_score": 62,
                "summary": "固态电池产业化提速，机器人应用率先落地，产业链公司受关注",
                "key_events": [
                    {"event": "小鹏机器人专用固态电池小批量下线",
                     "impact": "标志固态电池进入量产验证阶段",
                     "sectors": ["固态电池", "机器人"], "sentiment": "利好"},
                    {"event": "恩捷股份称全固态电池将用于深空深海深地",
                     "impact": "应用场景拓展至特种领域",
                     "sectors": ["固态电池"], "sentiment": "中性"},
                ],
                "hot_sectors": ["固态电池", "机器人", "锂电池"],
                "related_symbols": related,
                "credibility": 0.72,
            }
            self._openai_ok(body, content=json.dumps(news, ensure_ascii=False))
            return

        # ---------- 分析类请求：返回结构化分析 JSON
        if "证券投资分析师" in text_all:
            import re
            # 从 prompt 里抠出第一个目标价候选，模拟"遵守规则"的AI
            # 注意：text_all 是 json.dumps 后的文本，换行被转义成 \n，所以要用 re.S
            m = re.search(r"目标价候选.*?- [^：]*：([\d.]+)", text_all, re.S)
            price = float(m.group(1)) if m else 100.0
            # 幻觉模式：故意给一个候选里没有的价格
            if "/hallucinate/" in path:
                price = 999999.0

            analysis = {
                "ai_score": 72,
                "rating": "关注",
                "confidence": 0.65,
                "horizon": "3-12个月",
                "bull_points": ["ROE长期维持高位，盈利质量扎实",
                                "经营现金流覆盖净利润，利润含金量高"],
                "bear_points": ["营收增速仅个位数，成长性偏弱",
                                "估值处于历史中位偏上"],
                "catalysts": ["旺季动销数据超预期"],
                "risks": ["消费需求走弱", "政策监管变化"],
                "chosen_target": {"price": price, "method": "技术阻力位(近1年高点)",
                                  "reason": "接近前高压力位"},
                "entry_range": {"low": price * 0.85, "high": price * 0.92},
                "stop_loss": price * 0.75,
                "position_pct": 12,
                "summary": "质地优秀但成长放缓，适合回调分批建仓",
            }
            self._openai_ok(body, content=json.dumps(analysis, ensure_ascii=False))
            return

        # ---------- 挑刺的中转：不支持 response_format
        if "/picky/" in path:
            if "response_format" in body:
                self._json(400, {"error": {
                    "message": "response_format is not supported by this model",
                    "type": "invalid_request_error"}})
                return
            self._openai_ok(body, content='{"ok": true, "from": "picky"}')
            return

        # ---------- 忽略 JSON 模式、返回围栏文本的中转
        if "/fenced/" in path:
            text = ('好的，以下是分析结果：\n\n```json\n'
                    '{"ok": true, "from": "fenced", "score": 88,}\n'   # 故意带多余逗号
                    '```\n\n希望对你有帮助！')
            self._openai_ok(body, content=text)
            return

        # ---------- 限流测试
        if "/ratelimit/" in path:
            self._json(429, {"error": {"message": "rate limited"}})
            return

        # ---------- 正常
        self._openai_ok(body, content='{"ok": true, "from": "ok", "score": 75}')

    # ------------------------------------------------------------ helpers
    def _openai_ok(self, body, content: str):
        self._json(200, {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": body.get("model", "mock"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        })

    def _json(self, code: int, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"模拟中转站已启动: http://127.0.0.1:{PORT}")
    print("  正常:     /ok/v1/chat/completions")
    print("  拒绝JSON: /picky/v1/chat/completions")
    print("  围栏文本: /fenced/v1/chat/completions")
    print("  限流:     /ratelimit/v1/chat/completions")
    print("  模型列表: /v1/models")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
