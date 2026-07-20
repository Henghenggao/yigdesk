from __future__ import annotations
import hashlib
from decimal import Decimal
from pathlib import Path
import openpyxl
from openpyxl.utils.cell import coordinate_to_tuple

def _read_cell(path, ref: str):
    sheet, addr = ref.split("!", 1)
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        v = wb[sheet][addr].value
    finally:
        wb.close()
    return None if v is None else Decimal(str(v))

class ModelSource:
    """Read-only source of base inputs (from workbook cells) + fingerprint + ref existence."""
    def __init__(self, path, input_refs: dict[str, str]):
        self.path = path
        self.input_refs = input_refs
        self.fingerprint = hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def base_inputs(self) -> dict:
        out = {}
        for name, ref in self.input_refs.items():
            v = _read_cell(self.path, ref)
            if v is not None:
                out[name] = v
        return out

    def exists(self, ref: str) -> bool:
        try:
            sheet, addr = ref.split("!", 1)
            if not sheet or not addr or ":" in addr:
                return False
            coordinate_to_tuple(addr)
            return _read_cell(self.path, ref) is not None
        except (AttributeError, KeyError, TypeError, ValueError):
            return False
