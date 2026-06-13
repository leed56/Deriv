"""Strategy base and signal types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from src.synthetic.pairs import SyntheticPair


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass
class Signal:
    strategy: str
    pair: SyntheticPair | None
    side: Side
    strength: float  # 0-1 confidence
    entry_z: float
    target_z: float = 0.0
    stop_z: float = 3.5
    metadata: dict = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.side != Side.FLAT and self.strength > 0.3


class Strategy(ABC):
    name: str

    @abstractmethod
    def generate(
        self,
        prices: pd.DataFrame,
        pairs: list[SyntheticPair],
        context: dict | None = None,
    ) -> list[Signal]:
        ...
