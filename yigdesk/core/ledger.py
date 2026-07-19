from __future__ import annotations
from pathlib import Path
from .ops import Op, op_to_json, op_from_json

class Ledger:
    """Append-only op-log. The single writer; assigns monotonic seq."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def read(self) -> list[Op]:
        text = self.path.read_text(encoding="utf-8")
        return [op_from_json(l) for l in text.splitlines() if l.strip()]

    def next_seq(self) -> int:
        ops = self.read()
        return ops[-1].seq + 1 if ops else 1

    def append(self, kind, actor, role, payload, base_seq=0) -> Op:
        op = Op(self.next_seq(), kind, actor, role, payload, base_seq)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(op_to_json(op) + "\n")
        return op
