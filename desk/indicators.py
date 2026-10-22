"""Streaming indicators. Pure Python, no installs needed.

Every indicator is updated one bar at a time, so the exact same code runs
in a backtest and in live paper trading (no look-ahead possible).
"""
from __future__ import annotations

from collections import deque
import math


class EMA:
    def __init__(self, period: int):
        self.k = 2 / (period + 1)
        self.period = period
        self.value: float | None = None
        self.count = 0
        self.prev: float | None = None

    def update(self, x: float) -> float:
        self.prev = self.value
        self.count += 1
        self.value = x if self.value is None else self.value + self.k * (x - self.value)
        return self.value

    @property
    def ready(self) -> bool:
        return self.count >= self.period

    @property
    def slope(self) -> float:
        if self.prev is None or self.value is None or self.prev == 0:
            return 0.0
        return (self.value - self.prev) / self.prev


class SessionVWAP:
    """VWAP that resets every session (each new calendar day)."""

    def __init__(self):
        self.session = None
        self.pv = 0.0
        self.vol = 0.0
        self.value: float | None = None

    def update(self, session, high, low, close, volume) -> float:
        if session != self.session:
            self.session, self.pv, self.vol = session, 0.0, 0.0
        typical = (high + low + close) / 3
        self.pv += typical * volume
        self.vol += volume
        self.value = self.pv / self.vol if self.vol > 0 else close
        return self.value


class ATR:
    def __init__(self, period: int = 14):
        self.period = period
        self.value: float | None = None
        self.prev_close: float | None = None
        self.count = 0

    def update(self, high, low, close) -> float:
        tr = high - low if self.prev_close is None else max(
            high - low, abs(high - self.prev_close), abs(low - self.prev_close))
        self.prev_close = close
        self.count += 1
        if self.value is None:
            self.value = tr
        else:  # Wilder smoothing
            self.value = (self.value * (self.period - 1) + tr) / self.period
        return self.value

    @property
    def ready(self) -> bool:
        return self.count >= self.period


class RSI:
    def __init__(self, period: int = 14):
        self.period = period
        self.avg_gain = 0.0
        self.avg_loss = 0.0
        self.prev: float | None = None
        self.count = 0
        self.value = 50.0

    def update(self, close) -> float:
        if self.prev is not None:
            change = close - self.prev
            gain, loss = max(change, 0), max(-change, 0)
            self.count += 1
            n = min(self.count, self.period)
            self.avg_gain += (gain - self.avg_gain) / n
            self.avg_loss += (loss - self.avg_loss) / n
            if self.avg_loss == 0:
                self.value = 100.0 if self.avg_gain > 0 else 50.0
            else:
                rs = self.avg_gain / self.avg_loss
                self.value = 100 - 100 / (1 + rs)
        self.prev = close
        return self.value


class RollingZ:
    """Z-score of the latest value vs a rolling window (used for volume surges)."""

    def __init__(self, window: int = 30):
        self.buf: deque[float] = deque(maxlen=window)
        self.value = 0.0

    def update(self, x: float) -> float:
        if len(self.buf) >= 5:
            mean = sum(self.buf) / len(self.buf)
            var = sum((v - mean) ** 2 for v in self.buf) / len(self.buf)
            sd = math.sqrt(var)
            self.value = (x - mean) / sd if sd > 0 else 0.0
        self.buf.append(x)
        return self.value
