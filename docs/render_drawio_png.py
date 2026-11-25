# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render docs/architecture.drawio to an editable docs/architecture.drawio.png.

The PNG carries the diagram the way draw.io writes it (a ``tEXt`` chunk
``mxfile`` with the URL-encoded XML), so draw.io / diagrams.net opens the
PNG as the diagram again. The picture itself is drawn here with Qt from the
same geometry: a simplified renderer for the shapes this diagram uses
(rounded boxes, the storage cylinder, orthogonal edges, simple HTML labels).
Saving the diagram from draw.io later replaces it with draw.io's own render.

    python docs/render_drawio_png.py            (needs PySide6)
"""
from __future__ import annotations

import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QPainter,
                           QPainterPath, QPen, QPolygonF, QTextDocument)

HERE = Path(__file__).resolve().parent
SRC = HERE / "architecture.drawio"
OUT = HERE / "architecture.drawio.png"
SCALE = 2.0
MARGIN = 20


def style_of(cell) -> dict:
    out = {}
    for part in (cell.get("style") or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
        elif part:
            out[part] = "1"
    return out


def geometry(cell) -> QRectF:
    g = cell.find("mxGeometry")
    return QRectF(float(g.get("x", 0)), float(g.get("y", 0)),
                  float(g.get("width", 0)), float(g.get("height", 0)))


def color(value: str | None, default: str) -> QColor:
    if not value or value == "none":
        return QColor(0, 0, 0, 0) if value == "none" else QColor(default)
    return QColor(value)


def html_label(p: QPainter, html: str, rect: QRectF, st: dict,
               background: QColor | None = None) -> None:
    doc = QTextDocument()
    font = QFont()
    font.setFamilies(["Segoe UI", "DejaVu Sans", "Arial", "Helvetica"])
    font.setPixelSize(int(st.get("fontSize", 12)))
    doc.setDefaultFont(font)
    align = st.get("align", "center")
    css = f"color:{st.get('fontColor', '#1a1a1a')};"
    if st.get("fontStyle") == "1":
        css += "font-weight:bold;"
    doc.setHtml(f'<div align="{align}" style="{css}">{html}</div>')
    doc.setDocumentMargin(2)
    doc.setTextWidth(rect.width() if rect.width() > 0 else -1)
    if rect.width() <= 0:
        doc.adjustSize()
    size = doc.size()
    left = rect.left() + float(st.get("spacingLeft", 0))
    if rect.width() <= 0:                         # a point: centre on it
        x, y = rect.left() - size.width() / 2, rect.top() - size.height() / 2
    else:
        x = left if align == "left" else rect.left()
        if st.get("verticalAlign") == "top":
            y = rect.top() + float(st.get("spacingTop", 0))
        else:
            y = rect.center().y() - size.height() / 2
        if align == "left":
            doc.setTextWidth(rect.width() - float(st.get("spacingLeft", 0)))
    if background is not None:
        p.fillRect(QRectF(x, y, size.width(), size.height()), background)
    p.save()
    p.translate(x, y)
    doc.drawContents(p)
    p.restore()


def draw_vertex(p: QPainter, cell, st: dict, r: QRectF) -> None:
    pen = QPen(color(st.get("strokeColor"), "#000000"), float(st.get("strokeWidth", 1)))
    if st.get("dashed") == "1":
        pen.setDashPattern([float(v) / pen.widthF() for v in
                            st.get("dashPattern", "6 4").split()])
    fill = color(st.get("fillColor"), "#ffffff")
    if "text" in st:
        html_label(p, cell.get("value") or "", r, st)
        return
    p.setPen(pen)
    p.setBrush(fill)
    if st.get("shape") == "cylinder3":
        size = float(st.get("size", 15))
        path = QPainterPath()
        path.moveTo(r.left(), r.top() + size / 2)
        path.lineTo(r.left(), r.bottom() - size / 2)
        path.arcTo(QRectF(r.left(), r.bottom() - size, r.width(), size), 180, 180)
        path.lineTo(r.right(), r.top() + size / 2)
        path.arcTo(QRectF(r.left(), r.top(), r.width(), size), 0, 180)
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(r.left(), r.top(), r.width(), size))
        label = QRectF(r.left(), r.top() + size, r.width(), r.height() - size * 1.5)
        html_label(p, cell.get("value") or "", label, st)
        return
    radius = min(r.width(), r.height()) * float(st.get("arcSize", 10)) / 100
    p.drawRoundedRect(r, radius, radius)
    html_label(p, cell.get("value") or "", r, st)


def port(r: QRectF, st: dict, prefix: str, other: QRectF) -> QPointF:
    if f"{prefix}X" in st:
        return QPointF(r.left() + r.width() * float(st[f"{prefix}X"]),
                       r.top() + r.height() * float(st[f"{prefix}Y"]))
    # Facing side, centred: enough for the boxes side by side here.
    if other.center().x() > r.center().x():
        return QPointF(r.right(), r.center().y())
    return QPointF(r.left(), r.center().y())


def arrow(p: QPainter, tip: QPointF, frm: QPointF, pen: QPen) -> None:
    d = tip - frm
    length = (d.x() ** 2 + d.y() ** 2) ** 0.5 or 1
    ux, uy = d.x() / length, d.y() / length
    s = 4 + pen.widthF() * 3
    base = QPointF(tip.x() - ux * s * 1.6, tip.y() - uy * s * 1.6)
    poly = QPolygonF([tip, QPointF(base.x() - uy * s, base.y() + ux * s),
                      QPointF(base.x() + uy * s, base.y() - ux * s)])
    p.setBrush(pen.color())
    p.setPen(Qt.NoPen)
    p.drawPolygon(poly)


def draw_edge(p: QPainter, cell, st: dict, cells: dict) -> None:
    src, dst = cells[cell.get("source")], cells[cell.get("target")]
    rs, rd = geometry(src), geometry(dst)
    a = port(rs, st, "exit", rd)
    b = port(rd, st, "entry", rs)
    if abs(a.y() - b.y()) < 0.5:
        pts = [a, b]
    else:                                         # orthogonal: H, V, H
        mx = (a.x() + b.x()) / 2
        pts = [a, QPointF(mx, a.y()), QPointF(mx, b.y()), b]
    pen = QPen(color(st.get("strokeColor"), "#333333"), float(st.get("strokeWidth", 1)))
    pen.setCapStyle(Qt.FlatCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    path = QPainterPath(pts[0])
    for q in pts[1:]:
        path.lineTo(q)
    p.drawPath(path)
    if st.get("endArrow", "classic") != "none":
        arrow(p, pts[-1], pts[-2], pen)
    if st.get("startArrow", "none") != "none":
        arrow(p, pts[0], pts[1], pen)
    if cell.get("value"):
        if len(pts) == 2:
            mid = (pts[0] + pts[1]) / 2
        else:                                     # on the vertical leg
            mid = (pts[1] + pts[2]) / 2
        bg = st.get("labelBackgroundColor")
        html_label(p, cell.get("value"), QRectF(mid.x(), mid.y(), 0, 0), st,
                   color(bg, "#ffffff") if bg else None)


def png_with_text(png: bytes, key: str, text: str) -> bytes:
    """Insert a tEXt chunk before IEND."""
    data = key.encode("latin-1") + b"\0" + text.encode("latin-1")
    chunk = (struct.pack(">I", len(data)) + b"tEXt" + data
             + struct.pack(">I", zlib.crc32(b"tEXt" + data) & 0xFFFFFFFF))
    iend = png.rindex(b"IEND") - 4
    return png[:iend] + chunk + png[iend:]


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    xml = SRC.read_text("utf-8")
    root = ET.fromstring(xml).find(".//root")
    cells = {c.get("id"): c for c in root.iter("mxCell")}
    vertices = [c for c in cells.values() if c.get("vertex") == "1"]
    edges = [c for c in cells.values() if c.get("edge") == "1"]
    bounds = QRectF()
    for c in vertices:
        bounds = bounds.united(geometry(c))
    bounds.adjust(-MARGIN, -MARGIN, MARGIN, MARGIN)
    img = QImage(int(bounds.width() * SCALE), int(bounds.height() * SCALE),
                 QImage.Format_ARGB32)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    p.scale(SCALE, SCALE)
    p.translate(-bounds.left(), -bounds.top())
    # Document order: containers first, their contents above them.
    for c in vertices:
        draw_vertex(p, c, style_of(c), geometry(c))
    for c in edges:
        draw_edge(p, c, style_of(c), cells)
    p.end()
    tmp = OUT.with_suffix(".tmp.png")
    img.save(str(tmp), "PNG")
    # draw.io reads this back (Editor.extractGraphModelFromPng).
    encoded = quote(xml, safe="-_.!~*'()")
    OUT.write_bytes(png_with_text(tmp.read_bytes(), "mxfile", encoded))
    tmp.unlink()
    print(f"wrote {OUT} ({img.width()}x{img.height()})")


if __name__ == "__main__":
    main()
