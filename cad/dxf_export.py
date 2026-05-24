"""
dxf_export.py — Exporta entidades del visor a DXF (AC2018)
Requiere: ezdxf  (pip install ezdxf)
"""
from __future__ import annotations


# ── Mapa exacto de colores hex → ACI (AutoCAD Color Index) ──────────────────
_COLOR_MAP_EXACT: dict[str, int] = {
    "#FFFFFF": 7,   # blanco
    "#FFFF00": 2,   # amarillo
    "#00FFFF": 4,   # cian
    "#00FF00": 3,   # verde
    "#0080FF": 5,   # azul
    "#FF0000": 1,   # rojo
    "#FF00FF": 6,   # magenta
    "#808080": 8,   # gris oscuro
    "#C0C0C0": 9,   # gris claro
    "#000000": 0,   # negro (BYBLOCK)
}

# Paleta ACI mínima: (R, G, B, ACI)
_ACI_PALETTE = [
    (255, 0,   0,   1),   # rojo
    (255, 255, 0,   2),   # amarillo
    (0,   255, 0,   3),   # verde
    (0,   255, 255, 4),   # cian
    (0,   0,   255, 5),   # azul
    (255, 0,   255, 6),   # magenta
    (255, 255, 255, 7),   # blanco
    (128, 128, 128, 8),   # gris
    (192, 192, 192, 9),   # gris claro
    (255, 128, 0,   30),  # naranja
    (128, 0,   255, 200), # violeta
]

# Nombres DXF para cada linetype interno
_LT_DXF: dict[str, str] = {
    "CONTINUOUS": "Continuous",
    "DASHED":     "DASHED",
    "DOTTED":     "DOTTED",
    "CENTER":     "CENTER",
    "DASHDOT":    "DASHDOT",
}

# Patrones inline (acad.lin format): [total_len, seg1, seg2, ...]
# positivo = dash, negativo = gap, cero = punto
_LT_PATTERNS: dict[str, tuple] = {
    "DASHED":  (0.75,  0.5,   -0.25),
    "DOTTED":  (0.25,  0.0,   -0.25),
    "CENTER":  (2.0,   1.25,  -0.25, 0.25, -0.25),
    "DASHDOT": (1.0,   0.5,   -0.25, 0.0,  -0.25),
}
_LT_DESCRIPTIONS: dict[str, str] = {
    "DASHED":  "__ __ __ __ __ __ __ __",
    "DOTTED":  ". . . . . . . . . . . .",
    "CENTER":  "____  __  ____  __  ___",
    "DASHDOT": "__ . __ . __ . __ . __",
}


def _hex_to_aci(hex_color: str) -> int:
    """Convierte color hex (#RRGGBB) al ACI más cercano."""
    h = hex_color.strip().upper()
    exact = _COLOR_MAP_EXACT.get(h)
    if exact is not None:
        return exact
    try:
        r = int(h[1:3], 16)
        g = int(h[3:5], 16)
        b = int(h[5:7], 16)
    except Exception:
        return 7  # blanco como fallback
    best_aci = 7
    best_d = 1e18
    for pr, pg, pb, aci in _ACI_PALETTE:
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d < best_d:
            best_d = d; best_aci = aci
    return best_aci


def exportar_dxf(entities: list, layers: dict, ruta: str) -> None:
    import ezdxf

    doc = ezdxf.new(dxfversion="R2018")
    msp = doc.modelspace()

    # ── Cargar linetypes ─────────────────────────────────────────────
    # Estrategia: intentar setup_linetypes (carga acad.lin completo),
    # si falla definir los patrones inline uno por uno.
    _needed_lt = {getattr(lyr, "linetype", "CONTINUOUS") for lyr in layers.values()}
    _needed_lt.discard("CONTINUOUS")  # Continuous siempre existe

    _loaded_lt: set[str] = set()

    # Intento 1: setup_linetypes carga todos los linetypes estándar
    try:
        ezdxf.setup_linetypes(doc)
        _loaded_lt = {n.upper() for n in _LT_DXF.values() if n != "Continuous"}
    except Exception:
        pass

    # Intento 2: definir inline los que no se cargaron
    for lt_name in _needed_lt:
        dxf_lt = _LT_DXF.get(lt_name, "Continuous")
        if dxf_lt == "Continuous":
            continue
        if lt_name in _loaded_lt:
            continue
        pattern = _LT_PATTERNS.get(lt_name)
        if pattern is None:
            continue
        try:
            if not doc.linetypes.contains(dxf_lt):
                doc.linetypes.new(
                    dxf_lt,
                    dxfattribs={
                        "description": _LT_DESCRIPTIONS.get(lt_name, lt_name),
                        "pattern": list(pattern),
                    })
        except Exception:
            pass

    # ── Crear capas ──────────────────────────────────────────────────
    for name, lyr in layers.items():
        try:
            dl = doc.layers.get(name)
            if dl is None:
                dl = doc.layers.add(name)
            dl.color = _hex_to_aci(lyr.color)
            dl.lineweight = _lw_to_dxf(lyr.linewidth)
            # Linetype
            lt_name = getattr(lyr, "linetype", "CONTINUOUS")
            dxf_lt = _LT_DXF.get(lt_name, "Continuous")
            try:
                dl.linetype = dxf_lt
            except Exception:
                dl.linetype = "Continuous"
            # Visibilidad / bloqueo
            if not lyr.visible:
                dl.off()
            if getattr(lyr, "locked", False):
                dl.lock()
        except Exception:
            pass

    # ── Escribir entidades ───────────────────────────────────────────
    from cad.engine import Line, Polyline, Circle, Arc, Text

    for e in entities:
        attribs = {"layer": e.layer}
        try:
            if isinstance(e, Line):
                msp.add_line(
                    (e.x1, e.y1, 0), (e.x2, e.y2, 0), dxfattribs=attribs
                )
            elif isinstance(e, Polyline):
                if len(e.points) >= 2:
                    pl = msp.add_lwpolyline(
                        [(x, y) for x, y in e.points],
                        dxfattribs=attribs,
                    )
                    pl.closed = e.closed
            elif isinstance(e, Circle):
                msp.add_circle(
                    (e.cx, e.cy, 0), radius=e.radius, dxfattribs=attribs
                )
            elif isinstance(e, Arc):
                # DXF siempre va CCW; para CW se invierten los ángulos
                if e.ccw:
                    msp.add_arc(
                        center=(e.cx, e.cy, 0), radius=e.radius,
                        start_angle=e.start_ang, end_angle=e.end_ang,
                        dxfattribs=attribs)
                else:
                    msp.add_arc(
                        center=(e.cx, e.cy, 0), radius=e.radius,
                        start_angle=e.end_ang, end_angle=e.start_ang,
                        dxfattribs=attribs)
            elif isinstance(e, Text):
                msp.add_text(
                    e.content,
                    dxfattribs={
                        **attribs,
                        "height": e.height,
                        "insert": (e.x, e.y),
                    },
                )
        except Exception:
            pass

    doc.saveas(ruta)


def _lw_to_dxf(lw: int) -> int:
    """Convierte linewidth (1-5) a DXF lineweight (centésimas de mm)."""
    mapping = {1: 13, 2: 18, 3: 35, 4: 50, 5: 70}
    return mapping.get(lw, 13)
