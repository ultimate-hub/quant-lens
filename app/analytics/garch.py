"""
GARCH(1,1) 波动率估计与预测（纯 numpy 实现，不依赖 scipy）。

**为什么只做波动率，不做方向**

    价格方向几乎不可预测：学术上单只股票次日涨跌的方向准确率最好也就 50-55%，
    扣掉交易成本后没有稳定优势。任何人声称能预测方向，基本都不能复现。

    但**波动率是真能被预测的**。波动聚集（volatility clustering）是金融时间序列里
    最稳健的实证事实之一：大涨大跌之后往往跟着一段高波动，平静期之后往往是低波动。
    GARCH 就是专门吃这个规律的模型，样本外通常能明显优于"用历史平均波动"的朴素做法。

    所以这里做的是：预测"接下来一段时间有多颠"，而不是"往哪走"。
    这直接改善了另外两件事的可靠性：
        1. 蒙特卡洛模拟的置信带宽度（高波动期不该画得和平时一样窄）
        2. 止损距离与仓位大小

**为什么用方差目标法（variance targeting）**

    标准 GARCH 要估 3 个参数（ω, α, β）。样本不长时 ω 估得很不稳。
    方差目标法直接把 ω 绑定到样本方差：ω = s² × (1 - α - β)。
    只剩 2 个参数要搜，估计更稳，而且保证模型的无条件方差和样本一致。
    这是很多成熟实现（如 R 的 rugarch）都提供的选项。

**为什么用网格搜索而不是梯度法**

    没有 scipy 就要自己写优化器。2 个参数的网格搜索（20×20 + 局部细化）
    在这个问题上完全够用，而且不会因为初值不好而发散。
"""

from __future__ import annotations

import numpy as np


def _garch_filter(rets: np.ndarray, omega: float, alpha: float, beta: float,
                  var0: float) -> tuple[np.ndarray, float]:
    """
    跑一遍 GARCH(1,1) 方差递推，返回每日条件方差序列和最终的对数似然。

        σ²_t = ω + α·r²_{t-1} + β·σ²_{t-1}
    """
    n = len(rets)
    sig2 = np.empty(n, dtype=float)
    prev = var0
    ll = 0.0
    for t in range(n):
        sig2[t] = prev
        # 高斯对数似然（去掉常数项，只保留影响优化目标的部分）
        ll += -0.5 * (np.log(prev) + rets[t] * rets[t] / prev)
        prev = omega + alpha * rets[t] * rets[t] + beta * prev
    return sig2, ll


def estimate_garch11(returns: np.ndarray) -> dict:
    """
    估计 GARCH(1,1) 参数（方差目标法 + 网格搜索 + 局部细化）。

    参数：
        returns  日收益率序列（小数，不是百分数），长度建议 ≥ 250

    返回：
        {
          "omega", "alpha", "beta",
          "persistence": alpha+beta,       持续度，越接近 1 波动记忆越长
          "uncond_vol": 无条件年化波动,
          "cond_vol": 当前条件年化波动,      ← 用来调整模拟宽度
          "forecast_vol": [h 天后的年化波动预测...],
          "half_life": 波动冲击的半衰期（交易日）,
          "loglik": 对数似然,
          "ok": True/False
        }
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 120:
        return {"ok": False, "reason": f"样本太短（{len(r)} 条），至少 120"}

    var = float(np.var(r))
    if var <= 0:
        return {"ok": False, "reason": "收益方差为 0"}

    # ---- 粗网格 ----
    best = None
    for alpha in np.linspace(0.01, 0.20, 20):
        for beta in np.linspace(0.60, 0.97, 20):
            if alpha + beta >= 0.999:
                continue
            omega = var * (1.0 - alpha - beta)
            if omega <= 0:
                continue
            _, ll = _garch_filter(r, omega, alpha, beta, var)
            if best is None or ll > best[0]:
                best = (ll, alpha, beta)

    if best is None:
        return {"ok": False, "reason": "参数搜索失败"}

    # ---- 局部细化 ----
    _, a0, b0 = best
    step_a, step_b = 0.01, 0.015
    for _ in range(3):
        for alpha in np.linspace(max(0.005, a0 - step_a), min(0.30, a0 + step_a), 9):
            for beta in np.linspace(max(0.5, b0 - step_b), min(0.995, b0 + step_b), 9):
                if alpha + beta >= 0.999 or alpha <= 0 or beta <= 0:
                    continue
                omega = var * (1.0 - alpha - beta)
                if omega <= 0:
                    continue
                _, ll = _garch_filter(r, omega, alpha, beta, var)
                if ll > best[0]:
                    best = (ll, alpha, beta)
        _, a0, b0 = best
        step_a *= 0.4
        step_b *= 0.4

    ll, alpha, beta = best
    omega = var * (1.0 - alpha - beta)
    persistence = alpha + beta

    sig2, _ = _garch_filter(r, omega, alpha, beta, var)

    # 当前条件方差：用最后一个观测的平方残差再更新一步
    last_var = float(sig2[-1])
    cond_var = omega + alpha * float(r[-1] ** 2) + beta * last_var

    uncond_vol = float(np.sqrt(var * 252))
    cond_vol = float(np.sqrt(cond_var * 252))

    # ---- 未来波动路径（解析解）----
    forecast_vol: list[float] = []
    for h in range(1, 6):
        if persistence < 1:
            v = var * (1 - persistence ** h) + cond_var * (persistence ** h)
        else:                                  # 持久度≈1 时退化为随机游走
            v = cond_var
        forecast_vol.append(round(float(np.sqrt(max(v, 0) * 252)), 6))

    # 波动冲击衰减到一半需要的天数
    half_life = round(float(np.log(0.5) / np.log(max(persistence, 1e-6))), 2) \
        if 0 < persistence < 1 else None

    return {
        "ok": True,
        "omega": round(float(omega), 10),
        "alpha": round(float(alpha), 6),
        "beta": round(float(beta), 6),
        "persistence": round(float(persistence), 6),
        "uncond_vol": round(uncond_vol, 6),
        "cond_vol": round(cond_vol, 6),
        "forecast_vol": forecast_vol,
        "half_life": half_life,
        "loglik": round(float(ll), 2),
        "n_obs": int(len(r)),
        # 当前波动相对历史均值的倍数：>1 表示现在比平时更颠
        "vol_ratio": round(float(cond_vol / uncond_vol), 4) if uncond_vol > 0 else 1.0,
    }