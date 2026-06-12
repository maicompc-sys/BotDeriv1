import numpy as np
from collections import deque

class StrategyBase:
    name     = "Base"
    min_ticks = 20
    def analyze(self, prices, ticks=None):
        return {"signal": None, "confidence": 0, "reason": "No signal"}

def ema(data, period):
    if len(data) < period: return np.array(data)
    result    = np.zeros(len(data))
    k         = 2 / (period + 1)
    result[period-1] = np.mean(data[:period])
    for i in range(period, len(data)):
        result[i] = data[i] * k + result[i-1] * (1 - k)
    return result

def rsi(data, period=14):
    if len(data) < period + 1: return 50
    deltas   = np.diff(data)
    gains    = np.where(deltas > 0, deltas, 0)
    losses   = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0: return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def bollinger(data, period=20, std_dev=2):
    if len(data) < period: return None, None, None
    arr = np.array(data[-period:])
    mid = np.mean(arr)
    std = np.std(arr)
    return mid - std_dev * std, mid, mid + std_dev * std

def macd(data, fast=12, slow=26, signal=9):
    if len(data) < slow + signal: return 0, 0
    ef   = ema(data, fast)
    es   = ema(data, slow)
    ml   = ef - es
    valid = ml[slow-1:]
    if len(valid) < signal: return 0, 0
    sl   = ema(list(valid), signal)
    return ml[-1], sl[-1]

def stochastic(data, period=14):
    if len(data) < period: return 50, 50
    window = data[-period:]
    lo, hi = min(window), max(window)
    if hi == lo: return 50, 50
    k_arr = []
    for i in range(period, len(data)+1):
        w = data[i-period:i]
        lo2, hi2 = min(w), max(w)
        k_arr.append(50 if hi2==lo2 else (data[i-1]-lo2)/(hi2-lo2)*100)
    k = k_arr[-1]
    d = np.mean(k_arr[-3:]) if len(k_arr) >= 3 else k
    return k, d

def atr(data, period=14):
    if len(data) < period + 1: return 0
    trs = [abs(data[i] - data[i-1]) for i in range(1, len(data))]
    return np.mean(trs[-period:])

# ── 1. RSI + Bollinger ────────────────────────────────────────────────────────
class RSIBollingerStrategy(StrategyBase):
    name = "RSI + Bollinger"
    min_ticks = 25
    def analyze(self, prices, ticks=None):
        if len(prices) < self.min_ticks:
            return {"signal": None, "confidence": 0, "reason": "Dados insuficientes"}
        r = rsi(prices, 14)
        lb, mb, ub = bollinger(prices, 20, 2)
        if lb is None:
            return {"signal": None, "confidence": 0, "reason": "BB insuficiente"}
        price = prices[-1]
        if price <= lb and r < 30:
            conf = min(95, 70 + (30 - r) * 0.8 + (lb - price) / max(lb, 1e-9) * 1000)
            return {"signal": "CALL", "confidence": round(conf, 1),
                    "reason": f"Preço abaixo BB inferior, RSI={r:.1f}"}
        elif price >= ub and r > 70:
            conf = min(95, 70 + (r - 70) * 0.8 + (price - ub) / max(ub, 1e-9) * 1000)
            return {"signal": "PUT", "confidence": round(conf, 1),
                    "reason": f"Preço acima BB superior, RSI={r:.1f}"}
        return {"signal": None, "confidence": 0, "reason": f"RSI={r:.1f} zona neutra"}

# ── 2. Fibonacci Martingale ───────────────────────────────────────────────────
class FibonacciMartingaleStrategy(StrategyBase):
    name = "Fibonacci Martingale"
    min_ticks = 10
    fib_levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    def analyze(self, prices, ticks=None):
        if len(prices) < self.min_ticks:
            return {"signal": None, "confidence": 0, "reason": "Dados insuficientes"}
        high  = max(prices[-20:])
        low   = min(prices[-20:])
        diff  = high - low
        if diff == 0:
            return {"signal": None, "confidence": 0, "reason": "Sem movimento"}
        pos = (prices[-1] - low) / diff
        r   = rsi(prices, 10)
        for level in self.fib_levels:
            if abs(pos - level) < 0.035:
                if level <= 0.382 and r < 45:
                    return {"signal": "CALL", "confidence": 78,
                            "reason": f"Suporte Fib {level*100:.1f}% RSI={r:.0f}"}
                elif level >= 0.618 and r > 55:
                    return {"signal": "PUT", "confidence": 78,
                            "reason": f"Resistência Fib {level*100:.1f}% RSI={r:.0f}"}
        return {"signal": None, "confidence": 0, "reason": f"Pos={pos*100:.1f}% sem nível Fib"}

# ── 3. Mean Reversion Z-Score ─────────────────────────────────────────────────
class MeanReversionStrategy(StrategyBase):
    name = "Mean Reversion Z-Score"
    min_ticks = 30
    def analyze(self, prices, ticks=None):
        if len(prices) < self.min_ticks:
            return {"signal": None, "confidence": 0, "reason": "Dados insuficientes"}
        arr  = np.array(prices[-30:])
        mean = np.mean(arr)
        std  = np.std(arr)
        if std == 0:
            return {"signal": None, "confidence": 0, "reason": "Std=0"}
        z    = (prices[-1] - mean) / std
        conf = min(92, 60 + abs(z) * 15)
        if z < -2.0:
            return {"signal": "CALL", "confidence": round(conf, 1),
                    "reason": f"Z-Score={z:.2f} → reversão alta"}
        elif z > 2.0:
            return {"signal": "PUT", "confidence": round(conf, 1),
                    "reason": f"Z-Score={z:.2f} → reversão baixa"}
        return {"signal": None, "confidence": 0, "reason": f"Z-Score={z:.2f} neutro"}

# ── 4. Momentum Breakout ATR ──────────────────────────────────────────────────
class MomentumBreakoutStrategy(StrategyBase):
    name = "Momentum Breakout ATR"
    min_ticks = 20
    def analyze(self, prices, ticks=None):
        if len(prices) < self.min_ticks:
            return {"signal": None, "confidence": 0, "reason": "Dados insuficientes"}
        at   = atr(prices, 14)
        if at == 0:
            return {"signal": None, "confidence": 0, "reason": "ATR=0"}
        rh   = max(prices[-10:-1])
        rl   = min(prices[-10:-1])
        mom  = prices[-1] - prices[-5]
        if prices[-1] > rh + at * 0.3 and mom > 0:
            conf = min(88, 70 + abs(mom) / at * 10)
            return {"signal": "CALL", "confidence": round(conf, 1),
                    "reason": f"Breakout acima {rh:.4f}, mom={mom:.4f}"}
        elif prices[-1] < rl - at * 0.3 and mom < 0:
            conf = min(88, 70 + abs(mom) / at * 10)
            return {"signal": "PUT", "confidence": round(conf, 1),
                    "reason": f"Breakout abaixo {rl:.4f}, mom={mom:.4f}"}
        return {"signal": None, "confidence": 0, "reason": "Sem breakout"}

# ── 5. Tick Pattern ───────────────────────────────────────────────────────────
class TickPatternStrategy(StrategyBase):
    name = "Tick Pattern"
    min_ticks = 8
    def analyze(self, prices, ticks=None):
        if len(prices) < 8:
            return {"signal": None, "confidence": 0, "reason": "Dados insuficientes"}
        last8   = prices[-8:]
        diffs   = [last8[i] - last8[i-1] for i in range(1, len(last8))]
        ups     = sum(1 for d in diffs if d > 0)
        downs   = sum(1 for d in diffs if d < 0)
        pattern = "".join(["U" if d > 0 else "D" for d in diffs])
        r       = rsi(prices, 10)
        if downs >= 6 and r < 40:
            return {"signal": "CALL", "confidence": 76,
                    "reason": f"6+ ticks DOWN ({pattern[-6:]})"}
        if ups >= 6 and r > 60:
            return {"signal": "PUT", "confidence": 76,
                    "reason": f"6+ ticks UP ({pattern[-6:]})"}
        reversal_up = ["DDDDDD", "DDDDD", "DDUDD"]
        reversal_dn = ["UUUUUU", "UUUUU", "UUDUU"]
        for pat in reversal_up:
            if pat in pattern and r < 40:
                return {"signal": "CALL", "confidence": 74,
                        "reason": f"Padrão reversal ALTA: {pattern[-6:]}"}
        for pat in reversal_dn:
            if pat in pattern and r > 60:
                return {"signal": "PUT", "confidence": 74,
                        "reason": f"Padrão reversal BAIXA: {pattern[-6:]}"}
        return {"signal": None, "confidence": 0, "reason": f"{pattern[-6:]} ({ups}U/{downs}D)"}

# ── 6. EMA Triple Cross ───────────────────────────────────────────────────────
class EMATripleCrossStrategy(StrategyBase):
    name = "EMA Triple Cross"
    min_ticks = 40
    def analyze(self, prices, ticks=None):
        if len(prices) < self.min_ticks:
            return {"signal": None, "confidence": 0, "reason": "Dados insuficientes"}
        e5      = ema(prices, 5)[-1]
        e13     = ema(prices, 13)[-1]
        e34     = ema(prices, 34)[-1]
        e5_prev = ema(prices[:-1], 5)[-1]
        e13_prev= ema(prices[:-1], 13)[-1]
        if e5 > e13 > e34 and e5 > e5_prev:
            conf = min(85, 72 + (e5 - e34) / max(e34, 1e-9) * 5000)
            return {"signal": "CALL", "confidence": round(conf, 1),
                    "reason": f"EMA5>EMA13>EMA34 Bull align"}
        elif e5 < e13 < e34 and e5 < e5_prev:
            conf = min(85, 72 + (e34 - e5) / max(e34, 1e-9) * 5000)
            return {"signal": "PUT", "confidence": round(conf, 1),
                    "reason": f"EMA5<EMA13<EMA34 Bear align"}
        if e5 > e13 and e5_prev <= e13_prev:
            return {"signal": "CALL", "confidence": 79, "reason": "Golden Cross EMA5/13"}
        elif e5 < e13 and e5_prev >= e13_prev:
            return {"signal": "PUT", "confidence": 79, "reason": "Death Cross EMA5/13"}
        return {"signal": None, "confidence": 0, "reason": "EMAs não alinhadas"}

# ── 7. Stoch + MACD ───────────────────────────────────────────────────────────
class StochMACDStrategy(StrategyBase):
    name = "Stoch + MACD"
    min_ticks = 35
    def analyze(self, prices, ticks=None):
        if len(prices) < self.min_ticks:
            return {"signal": None, "confidence": 0, "reason": "Dados insuficientes"}
        k, d   = stochastic(prices, 14)
        mc, ms = macd(prices)
        bull   = 0; bear = 0; reasons = []
        if k < 25 and d < 30: bull += 1; reasons.append(f"Stoch SB K={k:.0f}")
        if k > 75 and d > 70: bear += 1; reasons.append(f"Stoch SC K={k:.0f}")
        if mc > ms and mc > 0: bull += 1; reasons.append(f"MACD bull")
        if mc < ms and mc < 0: bear += 1; reasons.append(f"MACD bear")
        if bull >= 2:
            return {"signal": "CALL", "confidence": 82, "reason": " + ".join(reasons)}
        if bear >= 2:
            return {"signal": "PUT",  "confidence": 82, "reason": " + ".join(reasons)}
        return {"signal": None, "confidence": 0, "reason": "Sem confluência"}

# ── 8. Support/Resistance ─────────────────────────────────────────────────────
class SupportResistanceStrategy(StrategyBase):
    name = "Suporte/Resistência"
    min_ticks = 50
    def analyze(self, prices, ticks=None):
        if len(prices) < self.min_ticks:
            return {"signal": None, "confidence": 0, "reason": "Dados insuficientes"}
        arr    = np.array(prices[-50:])
        price  = prices[-1]
        hist, bins = np.histogram(arr, bins=20)
        peaks  = np.argsort(hist)[-5:]
        levels = [(bins[i] + bins[i+1]) / 2 for i in peaks]
        at     = atr(prices, 14) or price * 0.0001
        r      = rsi(prices, 14)
        for lvl in sorted(levels):
            if abs(price - lvl) < at * 0.5:
                if price < lvl and r < 50:
                    return {"signal": "CALL", "confidence": 77,
                            "reason": f"Suporte {lvl:.4f} RSI={r:.0f}"}
                elif price > lvl and r > 50:
                    return {"signal": "PUT", "confidence": 77,
                            "reason": f"Resistência {lvl:.4f} RSI={r:.0f}"}
        return {"signal": None, "confidence": 0, "reason": "Sem S/R próximo"}

# ── 9. Volatility Squeeze ─────────────────────────────────────────────────────
class VolatilitySqueezeStrategy(StrategyBase):
    name = "Volatility Squeeze"
    min_ticks = 30
    def analyze(self, prices, ticks=None):
        if len(prices) < self.min_ticks:
            return {"signal": None, "confidence": 0, "reason": "Dados insuficientes"}
        lb, mb, ub = bollinger(prices, 20, 2)
        if lb is None:
            return {"signal": None, "confidence": 0, "reason": "BB insuficiente"}
        at       = atr(prices, 14)
        kc_upper = mb + at * 1.5
        kc_lower = mb - at * 1.5
        squeeze  = lb > kc_lower and ub < kc_upper
        momentum = prices[-1] - prices[-5]
        if squeeze:
            if momentum > 0:
                return {"signal": "CALL", "confidence": 80,
                        "reason": f"Squeeze + momentum ALTA ({momentum:.4f})"}
            elif momentum < 0:
                return {"signal": "PUT", "confidence": 80,
                        "reason": f"Squeeze + momentum BAIXA ({momentum:.4f})"}
        return {"signal": None, "confidence": 0, "reason": "Sem squeeze ativo"}

# ── 10. Neural Pattern ────────────────────────────────────────────────────────
class NeuralPatternStrategy(StrategyBase):
    name = "Neural Pattern ML"
    min_ticks = 20
    def analyze(self, prices, ticks=None):
        if len(prices) < self.min_ticks:
            return {"signal": None, "confidence": 0, "reason": "Dados insuficientes"}
        arr     = np.array(prices)
        returns = np.diff(arr) / np.maximum(arr[:-1], 1e-9)
        if len(returns) < 10:
            return {"signal": None, "confidence": 0, "reason": "Returns insuficientes"}
        ret10   = returns[-10:]
        mean_r  = np.mean(ret10)
        std_r   = np.std(ret10)
        if std_r == 0:
            return {"signal": None, "confidence": 0, "reason": "Std=0"}
        norm    = (ret10 - mean_r) / std_r
        skew    = np.mean(norm ** 3)
        kurt    = np.mean(norm ** 4) - 3
        mom3    = np.mean(returns[-3:])
        mom7    = np.mean(returns[-7:])
        bull = 0; bear = 0
        if mean_r < -0.0001: bull += 2
        if skew    < -0.5:   bull += 1
        if kurt    >  1:     bull += 1
        if mom3    < mom7:   bull += 1
        if mean_r  >  0.0001: bear += 2
        if skew    >  0.5:    bear += 1
        if kurt    >  1:      bear += 1
        if mom3    > mom7:    bear += 1
        if bull >= 3:
            return {"signal": "CALL", "confidence": min(88, 65 + bull * 5),
                    "reason": f"Neural CALL={bull} skew={skew:.2f}"}
        if bear >= 3:
            return {"signal": "PUT",  "confidence": min(88, 65 + bear * 5),
                    "reason": f"Neural PUT={bear} skew={skew:.2f}"}
        return {"signal": None, "confidence": 0, "reason": f"Bull={bull} Bear={bear}"}

# ── Registry ──────────────────────────────────────────────────────────────────
ALL_STRATEGIES = [
    RSIBollingerStrategy(),
    FibonacciMartingaleStrategy(),
    MeanReversionStrategy(),
    MomentumBreakoutStrategy(),
    TickPatternStrategy(),
    EMATripleCrossStrategy(),
    StochMACDStrategy(),
    SupportResistanceStrategy(),
    VolatilitySqueezeStrategy(),
    NeuralPatternStrategy(),
]

def run_all_strategies(prices: list, ticks: list = None) -> list:
    results = []
    for strat in ALL_STRATEGIES:
        try:
            r = strat.analyze(prices, ticks)
            r["strategy"] = strat.name
            results.append(r)
        except Exception as e:
            results.append({"strategy": strat.name, "signal": None,
                            "confidence": 0, "reason": f"Erro: {e}"})
    return results

def get_consensus(results: list, min_confidence=70) -> dict:
    calls = [r for r in results if r["signal"] == "CALL" and r["confidence"] >= min_confidence]
    puts  = [r for r in results if r["signal"] == "PUT"  and r["confidence"] >= min_confidence]
    if not calls and not puts:
        return {"signal": None, "confidence": 0, "votes_call": 0, "votes_put": 0}
    if len(calls) > len(puts):
        return {"signal": "CALL",
                "confidence": round(np.mean([r["confidence"] for r in calls]), 1),
                "votes_call": len(calls), "votes_put": len(puts), "strategies": calls}
    elif len(puts) > len(calls):
        return {"signal": "PUT",
                "confidence": round(np.mean([r["confidence"] for r in puts]), 1),
                "votes_call": len(calls), "votes_put": len(puts), "strategies": puts}
    return {"signal": None, "confidence": 0, "votes_call": len(calls), "votes_put": len(puts)}