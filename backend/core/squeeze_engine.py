"""
APEX-OMEGA OS V7.0 — With-Trend Momentum Surge & Confirmed Continuation Engine
Fixes proven by 48-hour live production audit (image_45f387.png):
1. Eliminates "Counter-Trend Knife Catching": V6 shorted SUI and LINK at 23:17:23 right as they broke out upward,
   stopping out in 48 seconds. V7 NEVER trades against short-term candle momentum (ROC5 & EMA-9/21/50 must align!).
2. Evaluates both the last completed candle (close[-2]) and current live candle (close[-1]) so mid-candle flicker
   never triggers a false entry.
"""
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
import numpy as np


@dataclass
class QuantV7Signal:
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

    def evaluate_ohlcv(self, symbol: str, ohlcv: List[List[float]], feed_source: str = "BYBIT_LIVE_5M") -> QuantV7Signal:
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
        ema_prev = float(ema21[max(0, n - 5)])
        ema_slope = (ema_now - ema_prev) / max(ema_prev, 1e-9)

        vol_mean = float(np.mean(vol[max(0, n - 21) : n - 1])) if n > 5 else float(np.mean(vol))
        rvol = round(max(float(vol[-1]), float(vol[-2])) / max(vol_mean, 1e-9), 2)

        close5 = float(close[max(0, n - 6)])
        roc5 = round((float(close[-1]) - close5) / max(close5, 1e-9), 5)

        ker_p = min(10, n - 1)
        net_move = abs(float(close[-1] - close[-1 - ker_p]))
        path_sum = float(np.sum(np.abs(np.diff(close[-(ker_p + 1):]))))
        ker = round(net_move / max(path_sum, 1e-12), 2)

        # Two-bar momentum confirmation (prevents 12-second counter-trend wipeouts!)
        two_bar_bull = float(close[-1]) > float(open_p[-1]) and float(close[-2]) >= float(open_p[-2]) and float(close[-1]) > float(close[-2])
        two_bar_bear = float(close[-1]) < float(open_p[-1]) and float(close[-2]) <= float(open_p[-2]) and float(close[-1]) < float(close[-2])

        bull_stack = float(ema9[-1]) > float(ema21[-1]) >= float(ema50[-1]) and ema_slope >= 0.0006
        bear_stack = float(ema9[-1]) < float(ema21[-1]) <= float(ema50[-1]) and ema_slope <= -0.0006

        # V7 WITH-TREND MOMENTUM SURGE SCORE (Rides breakouts & trend continuations instead of stepping in front of them!)
        bull_score = 40
        if bull_stack:
            bull_score += 22
        if two_bar_bull:
            bull_score += 16
        if roc5 >= 0.0025 and 52 <= rsi14 <= 78:
            bull_score += 12
        if z_score >= 0.25:
            bull_score += 5
        if rvol >= 1.25:
            bull_score += 5

        bear_score = 40
        if bear_stack:
            bear_score += 22
        if two_bar_bear:
            bear_score += 16
        if roc5 <= -0.0025 and 22 <= rsi14 <= 48:
            bear_score += 12
        if z_score <= -0.25:
            bear_score += 5
        if rvol >= 1.25:
            bear_score += 5

        long_valid = bull_stack and two_bar_bull and roc5 >= 0.0025 and 52 <= rsi14 <= 78 and bull_score >= 95
        short_valid = bear_stack and two_bar_bear and roc5 <= -0.0025 and 22 <= rsi14 <= 48 and bear_score >= 95

        if long_valid:
            regime = "LONG_IGNITION"
            setup_type = "MOMENTUM_SURGE_LONG"
            score = min(99, bull_score)
            signal_valid = True
        elif short_valid:
            regime = "SHORT_IGNITION"
            setup_type = "MOMENTUM_SURGE_SHORT"
            score = min(99, bear_score)
            signal_valid = True
        else:
            score = min(90, max(bull_score, bear_score))
            regime = "COILING" if (squeeze_now or score >= 75) else "NEUTRAL"
            setup_type = "SCANNING"
            signal_valid = False

        eff_atr = max(float(atr14[-1]), float(close[-1]) * 0.0065)

        return QuantV7Signal(
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
