"""
时序预测模型。

**先说清楚预期**：
    日频股票价格近似随机游走，朴素预测（直接用今天价当明天价）很难被显著打败。
    这一点在金融时间序列文献里是共识，不是我保守。

    所以这个模块的设计目标不是「造一个很准的模型」，
    而是：**把几个主流方法都跑一遍，用真实的样本外误差比出谁更好，然后如实告诉用户**。
    如果所有模型都没赢过随机游走，界面会直接说「没有模型显著优于随机游走」，
    而不是硬拿一个模型去画线骗人。

实现的模型（都只用 numpy，不引第三方统计库）：

    naive       随机游走：明天 = 今天。作为基准。
    drift       漂移项：今天 + 历史平均日涨幅 × h
    holt        阻尼线性趋势（指数平滑，带阻尼）
    ols_trend   对数价格对时间做最小二乘直线
    ar          自回归：用过去 p 天的收益预测未来收益，再累积成价格

评估方式：
    留出最后 holdout 天做样本外检验，计算各模型的 MAE / MAPE / 方向准确率。
    按 MAE 排序，只有明显优于 naive 的才推荐使用。
"""

from __future__ import annotations

import numpy as np


# ==================================================================
# 各模型的一次性预测
# ==================================================================
def _naive(y: np.ndarray, h: int) -> np.ndarray:
    return np.full(h, y[-1], dtype=float)


def _drift(y: np.ndarray, h: int) -> np.ndarray:
    n = len(y)
    slope = (y[-1] - y[0]) / max(n - 1, 1)
    return y[-1] + slope * np.arange(1, h + 1)


def _holt_damped(y: np.ndarray, h: int, alpha: float = 0.3,
                 beta: float = 0.05, phi: float = 0.9) -> np.ndarray:
    """阻尼趋势的 Holt 线性方法。用对数价格跑，保证预测为正。"""
    ly = np.log(np.maximum(y, 1e-9))
    level = ly[0]
    trend = ly[1] - ly[0] if len(ly) > 1 else 0.0
    for t in range(1, len(ly)):
        prev_level = level
        level = alpha * ly[t] + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - prev_level) + (1 - beta) * phi * trend

    out = []
    cum = 0.0
    for k in range(1, h + 1):
        cum += phi ** k
        out.append(level + trend * cum)
    return np.exp(np.array(out))


def _ols_trend(y: np.ndarray, h: int) -> np.ndarray:
    ly = np.log(np.maximum(y, 1e-9))
    t = np.arange(len(ly), dtype=float)
    # 最小二乘拟合 ly = a + b t
    b, a = np.polyfit(t, ly, 1)
    fut = a + b * (len(ly) + np.arange(1, h + 1))
    return np.exp(fut)


def _ar(y: np.ndarray, h: int, p: int = 5) -> np.ndarray:
    """
    对**收益率**做 AR(p)，再把预测的收益累积成价格。

    直接对价格做 AR 会得到近乎单位根的结果（等价于随机游走），
    对收益建模至少能捕捉短期的动量/反转。
    """
    r = np.diff(np.log(np.maximum(y, 1e-9)))
    if len(r) <= p + 5:
        return _naive(y, h)

    # 构造设计矩阵
    X = np.column_stack([r[p - i - 1: len(r) - i - 1] for i in range(p)])
    target = r[p:]
    # 加截距
    X = np.column_stack([np.ones(len(X)), X])
    try:
        coef, *_ = np.linalg.lstsq(X, target, rcond=None)
    except np.linalg.LinAlgError:
        return _naive(y, h)

    hist = list(r[-p:])
    preds = []
    for _ in range(h):
        x = np.concatenate([[1.0], np.array(hist[-p:][::-1])])
        rt = float(x @ coef)
        # 夹一下，避免极端外推
        rt = max(min(rt, 0.15), -0.15)
        preds.append(rt)
        hist.append(rt)

    price = y[-1] * np.exp(np.cumsum(preds))
    return price


def _clamp_path(path: np.ndarray, last_price: float, max_move: float = 0.4) -> np.ndarray:
    """
    给预测路径上保险。

    像 OLS 趋势外推这种模型，遇到强趋势样本会外推出离谱的值
    （实测有模型预测出比现价低 60% 的结果）。
    这里把整段预测的累计涨跌幅夹在 ±40% 内 —— 不是为了美化结果，
    而是因为「一支股票 20 天腰斩」这种预测在实盘里没有任何决策价值。
    """
    lo = last_price * (1 - max_move)
    hi = last_price * (1 + max_move)
    return np.clip(path, lo, hi)


MODELS = {
    "naive": ("随机游走（基准）", _naive),
    "drift": ("带漂移", _drift),
    "holt": ("Holt 阻尼趋势", _holt_damped),
    "ols_trend": ("OLS 趋势外推", _ols_trend),
    "ar": ("AR(5) 收益自回归", _ar),
}


# ==================================================================
# 对外接口
# ==================================================================
def forecast_all(closes, days: int = 20) -> dict[str, list[float]]:
    """所有模型各给一条未来路径。"""
    y = np.asarray(closes, dtype=float)
    y = y[np.isfinite(y) & (y > 0)]
    if len(y) < 30:
        raise ValueError(f"历史数据太短（{len(y)} 条），至少 30")

    out: dict[str, list[float]] = {}
    for key, (_label, fn) in MODELS.items():
        try:
            path = fn(y, days)
            path = np.nan_to_num(path, nan=y[-1], posinf=y[-1], neginf=y[-1])
            path = np.maximum(path, 1e-6)
            path = _clamp_path(path, float(y[-1]))
            out[key] = [round(float(v), 4) for v in path]
        except Exception:  # noqa: BLE001
            out[key] = [round(float(y[-1]), 4)] * days
    return out


def backtest(closes, days: int = 20, holdout: int = 20, windows: int = 5) -> dict:
    """
    多窗口样本外回测。

    为什么不用单窗口：
        只挖掉最后 20 天做检验的话，**谁在这 20 天碰巧猜对谁就赢**，
        完全是运气。实测同一批模型在不同股票上排名天差地别
        （OLS 趋势在茅台改善 22%，在比亚迪是基准误差的 8.6 倍）。

        改用 5 个不重叠的检验窗口，取平均误差，排名才有意义。

    返回每个模型的 MAE / MAPE / 方向命中率，以及是否稳定优于基准。
    """
    y = np.asarray(closes, dtype=float)
    y = y[np.isfinite(y) & (y > 0)]
    n = len(y)

    if n < days + holdout + 60:
        return {"ok": False, "reason": f"历史数据不足（{n} 条）无法回测"}

    # 收集每个模型在每个窗口的误差
    acc: dict[str, dict] = {k: {"mae": [], "mape": [], "dir": []} for k in MODELS}
    used = 0

    for w in range(windows):
        end = n - w * holdout
        start_test = end - holdout
        if start_test < days + 40:
            break

        train = y[:start_test]
        actual = y[start_test:end]
        if len(actual) < 2:
            break

        used += 1
        for key, (_label, fn) in MODELS.items():
            try:
                pred = fn(train, holdout)
                pred = _clamp_path(pred, float(train[-1]))
            except Exception:  # noqa: BLE001
                continue

            acc[key]["mae"].append(float(np.mean(np.abs(pred - actual))))
            acc[key]["mape"].append(float(np.mean(np.abs((pred - actual) / actual))) * 100)

            # 方向命中：只看「模型有方向」的那些步（随机游走永远预测持平，不参与）
            p_dir = np.sign(np.diff(np.concatenate([[train[-1]], pred])))
            a_dir = np.sign(np.diff(np.concatenate([[train[-1]], actual])))
            mask = p_dir != 0
            if mask.any():
                acc[key]["dir"].append(float(np.mean(p_dir[mask] == a_dir[mask])) * 100)

    if not used:
        return {"ok": False, "reason": "没有可用的检验窗口"}

    results = []
    for key, (_label, fn) in MODELS.items():
        a = acc[key]
        if not a["mae"]:
            continue
        results.append({
            "key": key,
            "name": MODELS[key][0],
            "mae": round(float(np.mean(a["mae"])), 4),
            "mape": round(float(np.mean(a["mape"])), 2),
            "dir_hit": round(float(np.mean(a["dir"])), 1) if a["dir"] else None,
        })

    if not results:
        return {"ok": False, "reason": "所有模型都拟合失败"}

    base = next((r for r in results if r["key"] == "naive"), None)
    for r in results:
        if base and r["key"] != "naive" and base["mae"] > 0:
            r["vs_naive"] = round((base["mae"] - r["mae"]) / base["mae"] * 100, 2)
        else:
            r["vs_naive"] = 0.0

    results.sort(key=lambda x: x["mae"])
    best = results[0]

    # 判定「可用」的门槛：MAE 要比随机游走好 3% 以上
    # 单窗口时 2% 就能骗人，多窗口平均后 3% 才算有点东西
    best_usable = bool(best["key"] != "naive" and best["vs_naive"] >= 3.0)

    if best_usable:
        conclusion = (
            f"{best['name']} 在 {used} 个窗口上的平均误差最低"
            f"（MAE {best['mae']:.2f}，比随机游走改善 {best['vs_naive']:.1f}%），"
            f"可以参考。但它仍不是价格预测，只是统计外推。"
        )
    else:
        conclusion = (
            f"在 {used} 个检验窗口上，没有任何模型显著优于「随机游走」基准。"
            f"这说明这段时间里价格的可预测性很弱 —— 这是日频股票的常态。"
            f"建议只看置信区间，不要盯某条预测线。"
        )

    return {
        "ok": True,
        "days": days,
        "holdout": holdout,
        "windows": used,
        "models": results,
        "best": best["key"],
        "best_name": best["name"],
        "best_usable": best_usable,
        "conclusion": conclusion,
    }