from __future__ import annotations
from typing import Protocol
from yigdesk.core.model import Consequence

class Evaluator(Protocol):
    revision: str
    def price(self, action: dict, source) -> Consequence: ...
    def ground(self, ref: str, source) -> bool: ...
