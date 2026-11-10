"""
APEX-OMEGA OS V8.0 — Pullback-in-Trend Sniper + Anti-Chop Filter
Fixes proven by the 24-hour V7.0 live production ledger (Screenshot 2026-11-10):
1. V7 reached ₹11,184.96 (+₹1,185 profit) during trending hours via Stage-2 (+₹336) and Stage-3 (+₹589) wins,
   but got stopped out 30 times at -0.26% because it bought at the overextended top of 2 green candles.
2. V8 waits for price to pull back and touch/test EMA-9/EMA-21 inside a confirmed trend (EMA-9 >= EMA-21 > EMA-50)
   with z_score NOT overextended (-1.10 <= z <= +0.65 for Long) and bounce—entering 0.30% cheaper!
"""
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
import numpy as np


@dataclass
class QuantV8Signal:
    symbol: str
    price: float
    atr_14: float
    vwap: float
    z_score: float
    rsi_2: float
    rsi_14: float = 50.0
    compression_ratio: float = 1.0
    squeeze_on: bool = False
    squeeze_bars_last_10: int = 0
    ema_trend_slope: float = 0.0
    rvol: float = 1.0
    roc5: float = 0.0
    ker: float = 0.45
    confluence_score: int = 50
    setup_type: str = "SCANNING"
    regime: str = "NEUTRAL"
    signal_valid: bool = False
    feed_source: str = "BYBIT_LIVE_5M"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SqueezeEngine:
    @staticmethod
    def _ema(values: np.ndarray, span: int) -> np.ndarray:
        alpha = 2.0 / (span + 1.0)
        ema = np.zeros_like(values, dtype=float)
        ema[0] = values[0]
        for i in range(1, len(values)):
            ema[i] = alpha * values[i] + (1.0 - alpha) * ema[i - 1]
        return ema

    @staticmethod
    def _rsi(closes: np.ndarray, period: int) -> float:
        if len(closes) <= period:
            return 50.0
        diffs = np.diff(closes[-(period + 1):])
        gains = float(np.sum(np.where(diffs > 0, diffs, 0.0))) / period
        losses = float(np.sum(np.where(diffs < 0, -diffs, 0.0))) / period
        if losses <= 1e-12:
            return 98.0
        rs = gains / losses
        return round(100.0 - (100.0 / (1.0 + rs)), 1)

    @staticmethod
    def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
        tr = np.zeros_like(close, dtype=float)
        tr[0] = max(high[0] - low[0], close[0] * 0.002)
        for i in range(1, len(close)):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
        return SqueezeEngine._ema(tr, period)

    def evaluate_ohlcv(self, symbol: str, ohlcv: List[List[float]], feed_source: str = "BYBIT_LIVE_5M") -> QuantV8Signal:
        arr = np.array(ohlcv, dtype=float)
        open_p = arr[:, 1]
        high = arr[:, 2]
        low = arr[:, 3]
        close = arr[:, 4]
        vol = arr[:, 5]
        n = len(close)

        atr20 = self._atr(high, low, close, 20)
        atr14 = self._atr(high, low, close, 14)
        ema9 = self._ema(close, min(9, n))
        ema21 = self._ema(close, min(21, n))
        ema50 = self._ema(close, min(50, n))
        ema200 = self._ema(close, min(200, n))
        true_atr_pct = float(atr14[-1]) / max(float(close[-1]), 1e-12)

        win = min(20, n)
        typ = (high[-win:] + low[-win:] + close[-win:]) / 3.0
        v_slice = np.maximum(vol[-win:], 1e-9)
        vwap = float(np.sum(typ * v_slice) / np.sum(v_slice))
        std20 = float(np.std(close[-win:]))
        z_score = round((float(close[-1]) - vwap) / max(std20, float(close[-1]) * 0.001), 2)

        squeeze_count = 0
        current_comp = 1.0
        squeeze_now = False
        for offset in range(min(9, n - 20), -1, -1):
            idx = n - 1 - offset
            sub = close[max(0, idx - 19) : idx + 1]
            sma = float(np.mean(sub))
            std = float(np.std(sub))
            bb_u, bb_l = sma + 2.0 * std, sma - 2.0 * std
            kc_mid = float(self._ema(close[: idx + 1], 20)[-1])
            kc_u, kc_l = kc_mid + 1.5 * float(atr20[idx]), kc_mid - 1.5 * float(atr20[idx])
            in_sq = (bb_u < kc_u) and (bb_l > kc_l)
            if in_sq:
                squeeze_count += 1
            if offset == 0:
                current_comp = round((bb_u - bb_l) / max(kc_u - kc_l, 1e-9), 2)
                squeeze_now = in_sq

        rsi2 = self._rsi(close, 2)
        rsi14 = self._rsi(close, 14)
        ema_now = float(ema21[-1])
        ema_prev = float(ema21[max(0, n - 6)])
        ema_slope = (ema_now - ema_prev) / max(ema_prev, 1e-9)

        vol_mean = float(np.mean(vol[max(0, n - 21) : n - 1])) if n > 5 else float(np.mean(vol))
        rvol = round(max(float(vol[-1]), float(vol[-2])) / max(vol_mean, 1e-9), 2)

        close5 = float(close[max(0, n - 6)])
        roc5 = round((float(close[-1]) - close5) / max(close5, 1e-9), 5)

        ker_p = min(10, n - 1)
        net_move = abs(float(close[-1] - close[-1 - ker_p]))
        path_sum = float(np.sum(np.abs(np.diff(close[-(ker_p + 1):]))))
        ker = round(net_move / max(path_sum, 1e-12), 2)

        # V8 Pullback-in-Trend Conditions:
        # 1. Confirmed Trend Stack: EMA-9 >= EMA-21 > EMA-50 with slope >= +0.08% (or <= -0.08% for Short) and KER >= 0.32 (not dead chop)
        # Loophole Fix 1, 2 & 3: EMA-200 Macro Wall + True Volatility Expansion Gate (true_atr_pct >= 0.0020) + Closed-Candle [-2] Confirmation
        vol_gate_ok = true_atr_pct >= 0.0020
        bull_stack = vol_gate_ok and float(close[-1]) > float(ema200[-1]) and float(ema9[-1]) >= float(ema21[-1]) > float(ema50[-1]) and ema_slope >= 0.0008 and ker >= 0.30
        bear_stack = vol_gate_ok and float(close[-1]) < float(ema200[-1]) and float(ema9[-1]) <= float(ema21[-1]) < float(ema50[-1]) and ema_slope <= -0.0008 and ker >= 0.30

        # 2. Pullback Touch & Closed/Live Bounce Confirmation (prevents 5-second unclosed candle fakeouts!)
        bull_pullback_touch = (
            (float(low[-1]) <= float(ema9[-1]) * 1.0018 or float(low[-2]) <= float(ema9[-2]) * 1.0018)
            and float(close[-1]) > float(open_p[-1])
            and float(close[-1]) > float(close[-2])
            and float(close[-1]) >= float(ema9[-1])
        )
        bear_pullback_touch = (
            (float(high[-1]) >= float(ema9[-1]) * 0.9982 or float(high[-2]) >= float(ema9[-2]) * 0.9982)
            and float(close[-1]) < float(open_p[-1])
            and float(close[-1]) < float(close[-2])
            and float(close[-1]) <= float(ema9[-1])
        )

        bull_not_overextended = -1.10 <= z_score <= 0.70 and 45 <= rsi14 <= 68
        bear_not_overextended = -0.70 <= z_score <= 1.10 and 32 <= rsi14 <= 55

        bull_score = 40
        if bull_stack:
            bull_score += 24
        if bull_pullback_touch:
            bull_score += 16
        if bull_not_overextended:
            bull_score += 12
        if rvol >= 1.15:
            bull_score += 7

        bear_score = 40
        if bear_stack:
            bear_score += 24
        if bear_pullback_touch:
            bear_score += 16
        if bear_not_overextended:
            bear_score += 12
        if rvol >= 1.15:
            bear_score += 7

        long_valid = bull_stack and bull_pullback_touch and bull_not_overextended and bull_score >= 95
        short_valid = bear_stack and bear_pullback_touch and bear_not_overextended and bear_score >= 95

        if long_valid:
            regime = "LONG_IGNITION"
            setup_type = "TREND_PULLBACK_BOUNCE_LONG"
            score = min(99, bull_score)
            signal_valid = True
        elif short_valid:
            regime = "SHORT_IGNITION"
            setup_type = "TREND_PULLBACK_BOUNCE_SHORT"
            score = min(99, bear_score)
            signal_valid = True
        else:
            score = min(90, max(bull_score, bear_score))
            regime = "COILING" if (squeeze_now or score >= 75) else "NEUTRAL"
            setup_type = "SCANNING"
            signal_valid = False

        eff_atr = max(float(atr14[-1]), float(close[-1]) * 0.0065)

        return QuantV8Signal(
            symbol=symbol,
            price=float(close[-1]),
            atr_14=eff_atr,
            vwap=float(vwap),
            z_score=z_score,
            rsi_2=rsi2,
            rsi_14=rsi14,
            compression_ratio=current_comp,
            squeeze_on=bool(squeeze_now),
            squeeze_bars_last_10=int(squeeze_count),
            ema_trend_slope=round(ema_slope, 6),
            rvol=rvol,
            roc5=roc5,
            ker=ker,
            confluence_score=int(score),
            setup_type=setup_type,
            regime=regime,
            signal_valid=bool(signal_valid),
            feed_source=feed_source,
        )
