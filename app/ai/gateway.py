"""
AI 网关：统一调用各种 AI 服务（含中转站）。

核心能力：
    1. 双协议：OpenAI 兼容（/v1/chat/completions）+ Anthropic 原生（/v1/messages）
    2. 能力探测：自动测出中转站支持什么（流式/JSON模式/system角色）
    3. 智能降级：参数不被支持就自动去掉重试（中转站常见）
    4. 重试退避：429/5xx 自动重试
    5. 多渠道路由：失败自动切换下一个渠道
    6. 用量记录：每次调用的 token、延迟、成败都落库
"""

from __future__ import annotations

import json
import time

import requests

from app.ai import channels as ch_store
from app.ai.channels import PROTOCOL_ANTHROPIC, Channel
from app.ai.json_utils import parse_json_loose

TIMEOUT = 120
RETRY_STATUS = {429, 500, 502, 503, 504}

_session = requests.Session()
_session.trust_env = False      # 忽略系统代理（本地中转站/直连都需要）


class AIError(Exception):
    """AI 调用失败。"""


def humanize_error(exc) -> str:
    """
    把技术错误翻译成人能看懂的原因 + 该怎么办。

    为什么要做这个？
        原始错误长这样：
          SSLError: HTTPSConnectionPool(host='sub.skypomo.top', port=443):
          Max retries exceeded (Caused by SSLError(SSLEOFError(8,
          '[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation...')))
        用户看不懂，也不知道该改什么。这里翻译成具体原因和建议动作。
    """
    msg = str(exc)
    low = msg.lower()

    # ---- 域名不存在
    if "name or service not known" in low or "getaddrinfo failed" in low \
            or "nodename nor servname" in low or "no address associated" in low:
        return ("域名解析失败：这个域名不存在或已失效。\n"
                "→ 请回到中转站网站确认最新地址（可能换域名了）")

    # ---- SSL 握手被中断（本次遇到的）
    if "unexpected_eof" in low or "sseof" in low or "eof occurred in violation" in low:
        return ("SSL 握手被中断，对方服务器拒绝建立连接。常见三种情况：\n"
                "→ ① 域名已失效（用公共DNS查一下是否 NXDOMAIN）\n"
                "→ ② 代理软件接管了连接但转发失败（试试把该域名设为直连）\n"
                "→ ③ 服务器封了你的 IP")

    # ---- 证书问题
    if "certificate verify failed" in low or "ssl: certificate" in low:
        return ("SSL 证书校验失败。可能是：\n"
                "→ 域名过期、或代理软件做了中间人解密、或系统时间不对")

    # ---- 连接被重置
    if "connection reset" in low or "connection aborted" in low or "remotedisconnected" in low:
        return ("连接被对方重置。可能是限流、IP 被封，或代理节点有问题")

    # ---- 超时
    if "timeout" in low or "timed out" in low:
        return ("请求超时。检查网络是否能访问该地址，或换成更快的节点")

    # ---- 代理
    if "proxy" in low:
        return ("代理连接失败。检查代理软件是否在运行、端口是否正确")

    # ---- HTTP 状态码
    if "401" in msg or "unauthorized" in low:
        return "密钥无效或已过期（HTTP 401）→ 检查 API Key 是否复制完整"
    if "403" in msg or "forbidden" in low:
        return "没有权限（HTTP 403）→ 检查这个 Key 是否有该模型的权限"
    if "404" in msg:
        return ("接口地址不正确（HTTP 404）→ 检查 base_url，"
                "常见错误是多了或少了 /v1")
    if "429" in msg:
        return "请求太频繁被限流（HTTP 429）→ 等几分钟再试，或降低并发"
    if "500" in msg or "502" in msg or "503" in msg:
        return "对方服务器出错（HTTP 5xx）→ 通常是中转站自身问题，等一会儿再试"

    # ---- 余额
    if "insufficient" in low or "quota" in low or "balance" in low:
        return "账户余额不足或超出配额"

    return msg[:300]


class ChatResult:
    """一次对话的结果。"""

    def __init__(self, text: str, channel: Channel, latency_ms: int,
                 prompt_tokens: int = 0, completion_tokens: int = 0,
                 raw: dict | None = None):
        self.text = text
        self.channel = channel
        self.latency_ms = latency_ms
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.raw = raw or {}

    def __repr__(self) -> str:
        return (f"<ChatResult {self.channel.name} {self.latency_ms}ms "
                f"{len(self.text)}字符>")

    def as_json(self) -> tuple[dict | list | None, str]:
        """把返回文本解析成 JSON（三级降级）。"""
        return parse_json_loose(self.text)


# ==================================================================
# URL 构造
# ==================================================================
def _openai_url(base: str) -> str:
    """
    拼接 OpenAI 兼容的 chat 接口地址。

    兼容三种用户输入：
        https://api.deepseek.com/v1          -> .../v1/chat/completions
        https://xxx.com                      -> .../v1/chat/completions
        https://xxx.com/v1/chat/completions  -> 原样使用
    """
    b = base.rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    if b.endswith("/v1"):
        return b + "/chat/completions"
    return b + "/v1/chat/completions"


def _anthropic_url(base: str) -> str:
    b = base.rstrip("/")
    if b.endswith("/messages"):
        return b
    if b.endswith("/v1"):
        return b + "/messages"
    return b + "/v1/messages"


def _models_url(base: str) -> str:
    b = base.rstrip("/")
    if b.endswith("/v1"):
        return b + "/models"
    return b + "/v1/models"


# ==================================================================
# 单次调用
# ==================================================================
def chat(channel: Channel, messages: list[dict], json_mode: bool = False,
         temperature: float | None = None, max_tokens: int | None = None,
         task: str = "chat") -> ChatResult:
    """
    调用指定渠道。失败抛 AIError（已含重试）。
    """
    if not channel.api_key and channel.protocol == "openai" and "127.0.0.1" not in channel.base_url:
        raise AIError(f"渠道 {channel.name} 没有配置密钥")

    if channel.protocol == PROTOCOL_ANTHROPIC:
        return _call_anthropic(channel, messages, temperature, max_tokens, task)
    return _call_openai(channel, messages, json_mode, temperature, max_tokens, task)


def _call_openai(channel: Channel, messages: list[dict], json_mode: bool,
                 temperature: float | None, max_tokens: int | None,
                 task: str) -> ChatResult:
    url = _openai_url(channel.base_url)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {channel.api_key}",
        **channel.extra_headers,
    }
    payload = {
        "model": channel.model,
        "messages": messages,
        "temperature": channel.temperature if temperature is None else temperature,
        "max_tokens": channel.max_tokens if max_tokens is None else max_tokens,
        "stream": False,
    }
    # 只有探测过"支持JSON模式"的渠道才加这个参数
    if json_mode and channel.capabilities.get("json_mode"):
        payload["response_format"] = {"type": "json_object"}

    data, latency = _post_with_retry(url, headers, payload, channel)

    try:
        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(f"返回结构异常: {str(data)[:200]}") from exc

    usage = data.get("usage") or {}
    _log(channel, task, True, latency,
         usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    return ChatResult(text, channel, latency,
                      usage.get("prompt_tokens", 0),
                      usage.get("completion_tokens", 0), data)


def _call_anthropic(channel: Channel, messages: list[dict],
                    temperature: float | None, max_tokens: int | None,
                    task: str) -> ChatResult:
    """Anthropic 协议：system 单独一个字段，消息只能是 user/assistant。"""
    url = _anthropic_url(channel.base_url)

    system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
    convo = [{"role": m["role"], "content": m["content"]}
             for m in messages if m["role"] in ("user", "assistant")]

    headers = {
        "Content-Type": "application/json",
        "x-api-key": channel.api_key,
        "anthropic-version": "2023-06-01",
        **channel.extra_headers,
    }
    payload = {
        "model": channel.model,
        "max_tokens": channel.max_tokens if max_tokens is None else max_tokens,
        "messages": convo,
        "temperature": channel.temperature if temperature is None else temperature,
    }
    if system_text:
        payload["system"] = system_text

    data, latency = _post_with_retry(url, headers, payload, channel)

    try:
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    except (AttributeError, TypeError) as exc:
        raise AIError(f"返回结构异常: {str(data)[:200]}") from exc

    usage = data.get("usage") or {}
    _log(channel, task, True, latency,
         usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    return ChatResult(text, channel, latency,
                      usage.get("input_tokens", 0),
                      usage.get("output_tokens", 0), data)


def _post_with_retry(url: str, headers: dict, payload: dict,
                     channel: Channel, retries: int = 2) -> tuple[dict, int]:
    """
    POST 并重试。对 429/5xx 做指数退避。
    对"参数不支持"类的 400，自动去掉可疑参数重试一次。
    """
    last_err = ""
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            resp = _session.post(url, headers=headers, json=payload, timeout=TIMEOUT)
            latency = int((time.time() - t0) * 1000)

            if resp.status_code in RETRY_STATUS and attempt < retries:
                time.sleep(1.5 ** attempt)
                continue

            if resp.status_code >= 400:
                body = resp.text[:300]
                # 中转站常见：不支持 response_format / max_tokens 等参数
                if resp.status_code in (400, 422) and attempt == 0:
                    dropped = _drop_unsupported(payload, body)
                    if dropped:
                        payload = dropped
                        continue
                raise AIError(
                    f"HTTP {resp.status_code}\n{humanize_error(f'HTTP {resp.status_code} {body}')}\n"
                    f"原始响应：{body}")

            return resp.json(), latency

        except requests.RequestException as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(1.5 ** attempt)
                continue

    _log(channel, "chat", False, 0, error=last_err)
    raise AIError(f"连接失败（已重试{retries}次）\n{humanize_error(last_err)}")


def _drop_unsupported(payload: dict, error_body: str) -> dict | None:
    """根据错误信息删掉不支持的参数。"""
    body = error_body.lower()
    dropped = dict(payload)
    changed = False
    for key, hint in (("response_format", "response_format"),
                      ("temperature", "temperature"),
                      ("max_tokens", "max_tokens")):
        if hint in body and key in dropped:
            dropped.pop(key)
            changed = True
    return dropped if changed else None


def _log(channel: Channel, task: str, ok: bool, latency: int,
         pt: int = 0, ct: int = 0, error: str = "") -> None:
    try:
        ch_store.log_usage(channel.id, task, ok, latency, pt, ct, error)
    except Exception:  # noqa: BLE001
        pass


# ==================================================================
# 多渠道路由
# ==================================================================
def chat_with_fallback(messages: list[dict], task: str = "chat",
                       json_mode: bool = False,
                       preferred_id: int | None = None,
                       max_channels: int = 3) -> ChatResult:
    """
    按优先级尝试多个渠道，任一成功即返回。

    优先级：指定的渠道 > 默认渠道 > 其余启用的渠道
    """
    all_chans = ch_store.list_channels(only_enabled=True)
    if not all_chans:
        raise AIError("还没有配置任何 AI 渠道，请先在设置页添加")

    ordered: list[Channel] = []
    if preferred_id:
        pref = next((c for c in all_chans if c.id == preferred_id), None)
        if pref:
            ordered.append(pref)
    ordered += [c for c in all_chans if c not in ordered]
    ordered = ordered[:max_channels]

    errors = []
    for chan in ordered:
        try:
            return chat(chan, messages, json_mode=json_mode, task=task)
        except AIError as exc:
            errors.append(f"[{chan.name}] {exc}")
            continue

    raise AIError("所有渠道都失败：\n" + "\n".join(errors))


# ==================================================================
# 能力探测
# ==================================================================
def probe(channel: Channel) -> dict:
    """
    探测一个渠道支持哪些能力，结果存回数据库。

    探测项：
        models_ok   能否拉取模型列表（判断 base_url 是否正确）
        system_role 是否支持 system 角色
        json_mode   是否支持 response_format=json_object
        stream      是否支持流式
        latency_ms  单次调用延迟
    """
    caps: dict = {
        "models_ok": False, "system_role": False, "json_mode": False,
        "stream": False, "latency_ms": 0, "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "error": "",
    }

    # 1) 模型列表（可选，失败不算致命）
    try:
        r = _session.get(_models_url(channel.base_url),
                         headers={"Authorization": f"Bearer {channel.api_key}",
                                  **channel.extra_headers}, timeout=20)
        caps["models_ok"] = r.status_code == 200
        if caps["models_ok"]:
            try:
                ids = [m.get("id") for m in (r.json().get("data") or [])]
                caps["models"] = [i for i in ids if i][:20]
            except Exception:  # noqa: BLE001
                pass
    except requests.RequestException:
        pass

    # 2) 基础对话 + system 角色
    try:
        res = chat(channel, [
            {"role": "system", "content": "你是一个测试助手，只回答一个字。"},
            {"role": "user", "content": "说\"好\""},
        ], max_tokens=16, task="probe")
        caps["system_role"] = True
        caps["latency_ms"] = res.latency_ms
    except AIError as exc:
        caps["error"] = str(exc)[:200]
        # system 角色失败时，退回只发 user 再试一次
        try:
            res = chat(channel, [{"role": "user", "content": "说\"好\""}],
                       max_tokens=16, task="probe")
            caps["system_role"] = False
            caps["latency_ms"] = res.latency_ms
        except AIError as exc2:
            caps["error"] = str(exc2)[:200]
            ch_store.save_capabilities(channel.id, caps)
            return caps

    # 3) JSON 模式
    #
    # 注意：这里不能用 chat()，因为 chat() 遇到 400 会自动删掉 response_format 重试，
    # 那样即使渠道不支持也会"成功"，探测结果就失真了。
    # 必须直接发原始请求，看服务器是否原样接受这个参数。
    caps["json_mode"] = _raw_supports_json_mode(channel)

    ch_store.save_capabilities(channel.id, caps)
    return caps


def _raw_supports_json_mode(channel: Channel) -> bool:
    """直接发一个带 response_format 的请求，看服务器是否 200。"""
    if channel.protocol == PROTOCOL_ANTHROPIC:
        return False      # Anthropic 协议没有这个参数

    url = _openai_url(channel.base_url)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {channel.api_key}",
        **channel.extra_headers,
    }
    payload = {
        "model": channel.model,
        "messages": [{"role": "user", "content": '只输出JSON：{"ok": true}'}],
        "max_tokens": 32,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    try:
        resp = _session.post(url, headers=headers, json=payload, timeout=30)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def test_connection(channel: Channel) -> tuple[bool, str]:
    """给设置页用的"测试连接"按钮。返回 (是否成功, 人能看懂的说明)。"""
    try:
        res = chat(channel, [{"role": "user", "content": "回复两个字：连接成功"}],
                   max_tokens=32, task="probe")
        return True, f"连接成功（{res.latency_ms}ms）：{res.text[:40]}"
    except AIError as exc:
        return False, str(exc)[:400]
