"""
Real-time digit frequency tracker.
Shared by all agents — single source of truth for digit statistics.
"""

from collections import deque, defaultdict
from dataclasses import dataclass, field


@dataclass
class DigitStats:
    digit:      int
    count:      int
    frequency:  float   # observed  0-1
    expected:   float   # 0.10 always
    deviation:  float   # observed - expected  (positive = hot, negative = cold)
    drought:    int     # ticks since this digit last appeared
    is_hot:     bool    # freq > threshold
    is_cold:    bool    # drought > threshold


class DigitTracker:
    """
    Maintains a sliding window of last N tick digits.
    Provides live stats used by Match / Differ agents.
    """

    HOT_FREQ_THRESHOLD  = 0.15   # digit appearing >15% is "hot"
    COLD_DROUGHT_TICKS  = 25     # digit missing for 25+ ticks is "cold"

    def __init__(self, window: int = 100):
        self.window  = window
        self._digits: deque[int] = deque(maxlen=window)
        self._last_seen: dict[int, int] = {d: 0 for d in range(10)}
        self._tick_count = 0

    # ── feed ──────────────────────────────────────────────────────
    def push(self, price: float):
        digit = self._extract_digit(price)
        self._digits.append(digit)
        self._tick_count += 1
        self._last_seen[digit] = self._tick_count

    @staticmethod
    def _extract_digit(price: float) -> int:
        """Last digit of price rounded to 2 decimal places."""
        s = f"{price:.2f}".replace(".", "")
        return int(s[-1])

    # ── queries ───────────────────────────────────────────────────
    def stats(self) -> dict[int, DigitStats]:
        n = len(self._digits)
        if n == 0:
            return {}
        counts = defaultdict(int)
        for d in self._digits:
            counts[d] += 1
        result = {}
        for d in range(10):
            freq    = counts[d] / n
            drought = self._tick_count - self._last_seen.get(d, 0)
            result[d] = DigitStats(
                digit     = d,
                count     = counts[d],
                frequency = round(freq, 4),
                expected  = 0.10,
                deviation = round(freq - 0.10, 4),
                drought   = drought,
                is_hot    = freq  > self.HOT_FREQ_THRESHOLD,
                is_cold   = drought > self.COLD_DROUGHT_TICKS,
            )
        return result

    def hottest(self) -> int:
        """Digit with highest recent frequency."""
        s = self.stats()
        return max(s, key=lambda d: s[d].frequency)

    def coldest(self) -> int:
        """Digit with longest drought (most overdue)."""
        s = self.stats()
        return max(s, key=lambda d: s[d].drought)

    def last_digit(self) -> int | None:
        return self._digits[-1] if self._digits else None

    def ready(self, min_ticks: int = 30) -> bool:
        return len(self._digits) >= min_ticks

    # ── display ───────────────────────────────────────────────────
    def summary_line(self) -> str:
        if not self._digits:
            return "no data"
        s = self.stats()
        parts = []
        for d in range(10):
            st = s[d]
            tag = "🔥" if st.is_hot else ("❄️" if st.is_cold else "  ")
            parts.append(f"{d}:{st.frequency:.0%}{tag}")
        return "  ".join(parts)
