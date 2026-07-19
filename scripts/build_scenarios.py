from pathlib import Path
from openpyxl import Workbook
ROOT = Path(__file__).resolve().parents[1] / "data/scenarios"

def _sheet(path, title, rows):
    wb = Workbook(); ws = wb.active; ws.title = title
    for addr, val in rows.items(): ws[addr] = val
    path.parent.mkdir(parents=True, exist_ok=True); wb.save(path)

_sheet(ROOT/"discount_approval/deal.xlsx", "Deal Inputs", {"B2":1000,"B3":10,"B4":480,"B5":40})
_sheet(ROOT/"saas_margin/margin.xlsx", "Plan", {"B2":2000,"B3":10,"B4":700,"B5":60})
_sheet(ROOT/"council_discount/council_deal.xlsx", "Deal Inputs", {"B2":1000,"B3":0,"B4":480,"B5":40})
_sheet(ROOT/"benchmark/deal.xlsx", "Deal Inputs", {"B2":1000,"B3":10,"B4":12,"B5":480,"B6":40})
print("scenarios built")
