"""Report downloads (Wave 1 / M6): any table as CSV (UTF-8 with a BOM, so Excel shows Hebrew) or Excel (.xlsx,
openpyxl). Endpoints take ``?format=csv|xlsx`` and pass their rows and columns here; amounts in agorot are written
in shekels (columns ending in ``_agorot`` become ``… ₪``)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from fastapi.responses import Response

FORMAT = "^(csv|xlsx)$"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _label(key: str) -> str:
    if key.endswith("_agorot"):
        return key.removesuffix("_agorot").replace("_", " ") + " ₪"
    return key.replace("_", " ")


def _value(key: str, v):
    if v is None:
        return ""
    if key.endswith("_agorot") and isinstance(v, int | float | Decimal):
        return round(float(v) / 100, 2)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, list | tuple):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {x}" for k, x in v.items())
    return v


def table(rows: Iterable[dict], columns: Sequence[str], *, name: str, fmt: str, sheet: str = "Report") -> Response:
    """The rows as a download. ``columns``: the keys, in order (others are left out)."""
    rows = list(rows)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([_label(c) for c in columns])
        for r in rows:
            w.writerow([_value(c, r.get(c)) for c in columns])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    if fmt == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = sheet[:31]
        ws.append([_label(c) for c in columns])
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for r in rows:
            ws.append([_value(c, r.get(c)) for c in columns])
        for i, c in enumerate(columns, start=1):
            if c.endswith("_agorot"):
                for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                    for cell in row:
                        cell.number_format = "#,##0.00"
            width = max([len(_label(c))] + [len(str(_value(c, r.get(c)))) for r in rows[:200]])
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(60, width + 2)
        ws.freeze_panes = "A2"
        buf = io.BytesIO()
        wb.save(buf)
        return Response(buf.getvalue(), media_type=XLSX,
                        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    raise HTTPException(400, "format is csv or xlsx")


def stamp(d: date | None = None) -> str:
    return (d or date.today()).isoformat()
