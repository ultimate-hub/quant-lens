"""P3 验收：AI 网关在模拟中转站上的完整测试。"""

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai import channels as ch
from app.ai import gateway, secret_store
from app.ai.channels import Channel
from app.core.db import init_db

BASE = "http://127.0.0.1:8899"


def sec(t):
    print(f"\n{'='*72}\n  {t}\n{'='*72}")


def main():
    init_db()

    # ---------------------------------------------------------- 1. 密钥加密
    sec("1. 密钥加密存储（Windows DPAPI）")
    secret = "sk-test-1234567890abcdef"
    cipher = secret_store.encrypt(secret)
    back = secret_store.decrypt(cipher)
    print(f"原文:   {secret}")
    print(f"密文:   {cipher[:60]}...")
    print(f"解密:   {back}")
    print(f"一致性: {'✅' if back == secret else '❌'}")
    print(f"界面显示: {secret_store.mask(secret)}")

    # ---------------------------------------------------------- 2. 渠道管理
    sec("2. 渠道 CRUD")
    # 清理旧测试数据
    for c in ch.list_channels():
        if c.name.startswith("MOCK-"):
            ch.delete_channel(c.id)

    id_ok = ch.add_channel("MOCK-正常", f"{BASE}/ok", "mock-large", "sk-1",
                           is_default=True)
    id_picky = ch.add_channel("MOCK-挑刺", f"{BASE}/picky", "mock-large", "sk-2")
    id_fenced = ch.add_channel("MOCK-围栏", f"{BASE}/fenced", "mock-large", "sk-3")
    id_anth = ch.add_channel("MOCK-Claude", f"{BASE}", "claude-mock", "sk-4",
                             protocol=ch.PROTOCOL_ANTHROPIC)
    id_dead = ch.add_channel("MOCK-死链", "http://127.0.0.1:9/v1", "x", "sk-5")
    print(f"已创建5个渠道: {[c.name for c in ch.list_channels()]}")
    print(f"默认渠道: {ch.get_default_channel().name}")

    # ---------------------------------------------------------- 3. 基础调用
    sec("3. OpenAI 协议基础调用")
    c_ok = ch.get_channel(id_ok)
    res = gateway.chat(c_ok, [{"role": "user", "content": "测试"}])
    print(f"返回: {res.text}")
    print(f"延迟: {res.latency_ms}ms  tokens: {res.prompt_tokens}+{res.completion_tokens}")

    # ---------------------------------------------------------- 4. 参数降级
    sec("4. 中转站拒绝 response_format → 自动降级")
    c_picky = ch.get_channel(id_picky)
    print("  先手动模拟'能力探测认为支持JSON'的状态:")
    ch.save_capabilities(id_picky, {"json_mode": True})
    c_picky = ch.get_channel(id_picky)
    try:
        res = gateway.chat(c_picky, [{"role": "user", "content": "输出JSON"}], json_mode=True)
        print(f"  ✅ 自动去掉 response_format 后成功: {res.text}")
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ 失败: {e}")

    # ---------------------------------------------------------- 5. JSON 解析
    sec("5. 围栏文本 + 多余逗号 → JSON 三级降级解析")
    c_fenced = ch.get_channel(id_fenced)
    res = gateway.chat(c_fenced, [{"role": "user", "content": "分析"}])
    print(f"  原始返回: {res.text[:80]!r}...")
    obj, method = res.as_json()
    print(f"  解析方法: {method}")
    print(f"  解析结果: {obj}")
    print(f"  {'✅ 成功提取' if isinstance(obj, dict) and obj.get('ok') else '❌ 失败'}")

    # ---------------------------------------------------------- 6. Anthropic
    sec("6. Anthropic 原生协议")
    c_anth = ch.get_channel(id_anth)
    res = gateway.chat(c_anth, [
        {"role": "system", "content": "你是分析师"},
        {"role": "user", "content": "分析"}])
    print(f"  返回: {res.text}")
    obj, _ = res.as_json()
    print(f"  解析: {obj}")

    # ---------------------------------------------------------- 7. 探测
    sec("7. 能力探测")
    for cid, label in [(id_ok, "正常"), (id_picky, "挑刺")]:
        c = ch.get_channel(cid)
        caps = gateway.probe(c)
        print(f"  {label:4s} 模型列表={caps['models_ok']} system={caps['system_role']} "
              f"JSON模式={caps['json_mode']} 延迟={caps['latency_ms']}ms")

    # ---------------------------------------------------------- 8. 故障切换
    sec("8. 多渠道故障自动切换")
    ch.update_channel(id_dead, is_default=1)   # 把死链设为默认
    print("  已把'死链'渠道设为默认，测试能否自动切到可用渠道")
    try:
        res = gateway.chat_with_fallback([{"role": "user", "content": "测试故障切换"}])
        print(f"  ✅ 自动切换成功，实际使用: {res.channel.name}")
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ 切换失败: {e}")
    finally:
        ch.update_channel(id_ok, is_default=1)

    # ---------------------------------------------------------- 9. 用量统计
    sec("9. 用量统计")
    for row in ch.usage_summary(days=1):
        print(f"  渠道{row['channel_id']}: 调用{row['calls']}次 成功{row['ok_calls']} "
              f"平均{row['avg_ms']:.0f}ms tokens={row['pt']}+{row['ct']}")

    # ---------------------------------------------------------- 10. 无渠道
    sec("10. 未配置渠道时的错误提示")
    for c in ch.list_channels():
        if c.name.startswith("MOCK-"):
            ch.delete_channel(c.id)
    try:
        gateway.chat_with_fallback([{"role": "user", "content": "x"}])
        print("  ⚠️ 意外成功")
    except gateway.AIError as e:
        print(f"  ✅ 正确提示: {e}")

    print("\n" + "=" * 72)
    print("  P3 验收完成")
    print("=" * 72)


if __name__ == "__main__":
    main()
