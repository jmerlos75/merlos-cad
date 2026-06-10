"""
cad/layout.py
=============
Espacio papel (Paper Space) — datos y renderizador PIL.

Exports:
    PAPER_SIZES_MM   — dict nombre → (w_mm, h_mm) en portrait
    paper_size()     — devuelve (w, h) en mm según orientación
    ViewportDef      — dataclass de un viewport en el papel
    LayoutSheet      — dataclass de una lámina completa
    default_viewport()
    layout_to_dict() / layout_from_dict()
    render_layout_pil()  — render PIL completo del paper space
"""
from __future__ import annotations

import math
import datetime
import os
from dataclasses import dataclass, field
from typing import List

# ── Tamaños de papel en mm (portrait) ────────────────────────────────────────
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A4":        (210.0,  297.0),
    "A3":        (297.0,  420.0),
    "A2":        (420.0,  594.0),
    "A1":        (594.0,  841.0),
    "A0":        (841.0, 1189.0),
    "Carta":     (216.0,  279.0),
    "Tabloide":  (279.0,  432.0),
}

# Título block height y márgenes (en mm)
TB_HEIGHT_MM  = 30.0    # altura del cuadro de títulos
MARGIN_MM     = 15.0    # margen de papel al viewport

# ── Escalas métricas completas (ampliación + reducción) ─────────────────────
# Cada entrada: (etiqueta, xp_factor, ltscale_equiv)
# xp_factor  = factor para ZOOM XP  (1.0 = 1:1)
# ltscale    = valor LTSCALE equivalente
METRIC_SCALES: list[tuple] = [
    # ── Ampliación (mecánica/SolidWorks) ──────────────────────────────
    ("100:1",   100.0,       0.01),
    ("50:1",     50.0,       0.02),
    ("32:1",     32.0,       0.03125),
    ("20:1",     20.0,       0.05),
    ("16:1",     16.0,       0.0625),
    ("10:1",     10.0,       0.1),
    ("8:1",       8.0,       0.125),
    ("5:1",       5.0,       0.2),
    ("4:1",       4.0,       0.25),
    ("3:1",       3.0,       1/3),
    ("2.5:1",     2.5,       0.4),
    ("2:1",       2.0,       0.5),
    ("1.5:1",     1.5,       1/1.5),
    ("1.25:1",    1.25,      0.8),
    # ── Natural ───────────────────────────────────────────────────────
    ("1:1",       1.0,       1.0),
    # ── Reducción ─────────────────────────────────────────────────────
    ("1:2",       1/2,       2),
    ("1:5",       1/5,       5),
    ("1:10",      1/10,      10),
    ("1:15",      1/15,      15),
    ("1:20",      1/20,      20),
    ("1:25",      1/25,      25),
    ("1:50",      1/50,      50),
    ("1:75",      1/75,      75),
    ("1:100",     1/100,     100),
    ("1:125",     1/125,     125),
    ("1:150",     1/150,     150),
    ("1:200",     1/200,     200),
    ("1:250",     1/250,     250),
    ("1:500",     1/500,     500),
    ("1:1000",    1/1000,    1000),
    ("1:1500",    1/1500,    1500),
    ("1:2000",    1/2000,    2000),
    ("1:2500",    1/2500,    2500),
    ("1:5000",    1/5000,    5000),
    ("1:5500",    1/5500,    5500),
    ("1:6000",    1/6000,    6000),
    ("1:6500",    1/6500,    6500),
    ("1:7000",    1/7000,    7000),
    ("1:7500",    1/7500,    7500),
    ("1:8000",    1/8000,    8000),
    ("1:8500",    1/8500,    8500),
    ("1:9000",    1/9000,    9000),
    ("1:9500",    1/9500,    9500),
    ("1:10000",   1/10000,   10000),
]

# ── Escalas imperiales ────────────────────────────────────────────────────────
IMPERIAL_SCALES: list[tuple] = [
    # ── Ampliación (mecánica/SolidWorks) ──────────────────────────────
    ("100:1",   100.0,       0.01),
    ("50:1",     50.0,       0.02),
    ("32:1",     32.0,       0.03125),
    ("20:1",     20.0,       0.05),
    ("16:1",     16.0,       0.0625),
    ("10:1",     10.0,       0.1),
    ("8:1",       8.0,       0.125),
    ("5:1",       5.0,       0.2),
    ("4:1",       4.0,       0.25),
    ("3:1",       3.0,       1/3),
    ("2.5:1",     2.5,       0.4),
    ("2:1",       2.0,       0.5),
    ("1.5:1",     1.5,       1/1.5),
    ("1.25:1",    1.25,      0.8),
    # ── Natural ───────────────────────────────────────────────────────
    ('1:1',       1.0,       1.0),
    # ── Arquitectónicas (pulgadas = pies) ─────────────────────────────
    ('6"=1\'',    0.5,       2),
    ('3"=1\'',    0.25,      4),
    ('1½"=1\'',   1/8,       8),
    ('1"=1\'',    1/12,      12),
    ('¾"=1\'',    1/16,      16),
    ('½"=1\'',    1/24,      24),
    ('3/8"=1\'',  1/32,      32),
    ('¼"=1\'',    1/48,      48),
    ('3/16"=1\'', 1/64,      64),
    ('1/8"=1\'',  1/96,      96),
    ('3/32"=1\'', 1/128,     128),
    # ── Ingeniería civil (1" = X pies) ────────────────────────────────
    ("1\"=10'",   1/120,     120),
    ("1\"=20'",   1/240,     240),
    ("1\"=30'",   1/360,     360),
    ("1\"=40'",   1/480,     480),
    ("1\"=50'",   1/600,     600),
    ("1\"=60'",   1/720,     720),
    ("1\"=100'",  1/1200,    1200),
    ("1\"=200'",  1/2400,    2400),
]

def get_scales(units: str = "metric") -> list[tuple]:
    """Devuelve la lista de escalas según el sistema de unidades activo."""
    return IMPERIAL_SCALES if units == "imperial" else METRIC_SCALES


def format_dim_value(meters: float, units: str,
                     precision_denom: int = 8,
                     suffix: str = "") -> str | None:
    """Formatea un valor de cota según el sistema de unidades.

    Métrico  → None  (el renderer usa su formato decimal normal)
    Imperial → pie-pulgadas fraccionarias: 4'-9⅛"

    precision_denom: 2=½  4=¼  8=⅛  16=1/16  32=1/32
    """
    if units != "imperial":
        return None

    from math import gcd as _gcd
    total_inches = meters / 0.0254
    feet = int(total_inches // 12)
    rem_in = total_inches - feet * 12
    whole_in = int(rem_in)
    frac = rem_in - whole_in

    num = round(frac * precision_denom)
    if num >= precision_denom:
        whole_in += 1
        num = 0
    if whole_in >= 12:
        feet += 1
        whole_in -= 12

    if num == 0:
        frac_str = ""
    else:
        g = _gcd(int(num), precision_denom)
        frac_str = f" {num//g}/{precision_denom//g}"

    if feet == 0:
        return f'{whole_in}{frac_str}"'
    if whole_in == 0 and not frac_str:
        return f"{feet}'-0\""
    return f"{feet}'-{whole_in}{frac_str}\""


def xp_to_model_scale(xp_expr: str, base_scale: float = 40.0) -> float | None:
    """Convierte una expresión XP a escala de modelo.

    Formatos aceptados:
      "1/100xp"  → 0.01  → model_scale = 0.01 * 1000 px/m (aprox)
      "2xp"      → 2.0
      "0.02xp"   → 0.02
      "1/4\"=1'" → arquitectónico imperial

    Devuelve el xp_factor (float) o None si el formato es inválido.
    """
    import re as _re
    s = xp_expr.strip().lower().rstrip("xp").strip()
    # Formato fraccionario  "1/100"  o "num/den"
    m = _re.match(r'^([\d.]+)\s*/\s*([\d.]+)$', s)
    if m:
        try:
            return float(m.group(1)) / float(m.group(2))
        except ZeroDivisionError:
            return None
    # Decimal simple "0.02"
    try:
        return float(s)
    except ValueError:
        return None


# ── VIEWPORT_SCALES — alias de compatibilidad (usa lista métrica estándar) ──
# Solo incluye escalas de reducción usadas en page setup de arquitectura
VIEWPORT_SCALES: list[tuple] = [
    (lbl, int(round(1 / xp))) for lbl, xp, _ in METRIC_SCALES
    if 0 < xp <= 1.0 and xp >= 1/10000
]


def paper_size(name: str, orientation: str) -> tuple[float, float]:
    """Devuelve (ancho_mm, alto_mm) según orientación.
    orientation: 'H' = Landscape, 'V' = Portrait.
    """
    w, h = PAPER_SIZES_MM.get(name, (420.0, 594.0))
    if orientation == "H":
        return (h, w)          # landscape: más ancho que alto
    return (w, h)


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ViewportDef:
    """Un viewport en espacio papel.
    Coordenadas (x, y) en mm desde la esquina inferior-izquierda del papel.
    """
    x:           float = 20.0    # mm desde borde izquierdo
    y:           float = 48.0    # mm desde borde inferior (encima del title block)
    width:       float = 250.0   # mm de ancho
    height:      float = 180.0   # mm de alto
    scale_denom: int   = 100     # denominador N en 1:N (1:100 → 100)
    view_cx:     float = 0.0     # centro modelo visible (unidades modelo)
    view_cy:     float = 0.0
    active:      bool  = True
    # Nombre opcional para identificar el viewport
    label:       str   = ""


@dataclass
class LayoutSheet:
    """Una lámina de papel espacio (paper space)."""
    name:        str   = "Lámina 1"
    paper:       str   = "A2"
    orientation: str   = "H"           # H=Landscape, V=Portrait
    viewports:   list  = field(default_factory=list)   # list[ViewportDef]

    # Vista guardada del layout canvas al salir (para restaurar al volver)
    view_scale:  float = 0.0            # 0 = recalcular al entrar
    view_ox:     float = 0.0
    view_oy:     float = 0.0

    # Cuadro de título
    tb_proyecto: str   = ""
    tb_desc:     str   = ""             # descripción / contenido de la lámina
    tb_firma:    str   = "Estudio Merlos"
    tb_escala:   str   = "1:100"
    tb_numero:   str   = "01"
    tb_fecha:    str   = ""             # "" = auto (fecha del sistema)


# ── Serialización ─────────────────────────────────────────────────────────────

def vp_to_dict(vp: ViewportDef) -> dict:
    return {
        "x": vp.x, "y": vp.y, "width": vp.width, "height": vp.height,
        "scale_denom": vp.scale_denom,
        "view_cx": vp.view_cx, "view_cy": vp.view_cy,
        "active": vp.active, "label": vp.label,
    }


def vp_from_dict(d: dict) -> ViewportDef:
    return ViewportDef(
        x=float(d.get("x", 20.0)),
        y=float(d.get("y", 48.0)),
        width=float(d.get("width", 250.0)),
        height=float(d.get("height", 180.0)),
        scale_denom=int(d.get("scale_denom", 100)),
        view_cx=float(d.get("view_cx", 0.0)),
        view_cy=float(d.get("view_cy", 0.0)),
        active=bool(d.get("active", True)),
        label=str(d.get("label", "")),
    )


def layout_to_dict(lay: LayoutSheet) -> dict:
    return {
        "name": lay.name, "paper": lay.paper, "orientation": lay.orientation,
        "viewports": [vp_to_dict(vp) for vp in lay.viewports],
        "view_scale": lay.view_scale,
        "view_ox": lay.view_ox,
        "view_oy": lay.view_oy,
        "tb_proyecto": lay.tb_proyecto,
        "tb_desc": lay.tb_desc,
        "tb_firma": lay.tb_firma,
        "tb_escala": lay.tb_escala,
        "tb_numero": lay.tb_numero,
        "tb_fecha": lay.tb_fecha,
    }


def layout_from_dict(d: dict) -> LayoutSheet:
    lay = LayoutSheet(
        name=d.get("name", "Lámina 1"),
        paper=d.get("paper", "A2"),
        orientation=d.get("orientation", "H"),
        view_scale=float(d.get("view_scale", 0.0)),
        view_ox=float(d.get("view_ox", 0.0)),
        view_oy=float(d.get("view_oy", 0.0)),
        tb_proyecto=d.get("tb_proyecto", ""),
        tb_desc=d.get("tb_desc", ""),
        tb_firma=d.get("tb_firma", "Estudio Merlos"),
        tb_escala=d.get("tb_escala", "1:100"),
        tb_numero=d.get("tb_numero", "01"),
        tb_fecha=d.get("tb_fecha", ""),
    )
    lay.viewports = [vp_from_dict(vd) for vd in d.get("viewports", [])]
    return lay


# ── Helpers ───────────────────────────────────────────────────────────────────

def default_viewport(paper: str, orientation: str,
                     scale_denom: int = 100) -> ViewportDef:
    """Genera un ViewportDef que llena el área útil del papel."""
    pw, ph = paper_size(paper, orientation)
    return ViewportDef(
        x      = MARGIN_MM,
        y      = TB_HEIGHT_MM + MARGIN_MM,
        width  = pw - 2 * MARGIN_MM,
        height = ph - TB_HEIGHT_MM - 2 * MARGIN_MM,
        scale_denom = scale_denom,
    )


def auto_fit_viewport(vp: ViewportDef, entities: list) -> ViewportDef:
    """Ajusta view_cx/view_cy del viewport al centroide del modelo."""
    if not entities:
        return vp
    xs, ys = [], []
    for e in entities:
        try:
            pts = e.bbox_pts()
            for p in pts:
                xs.append(p[0]); ys.append(p[1])
        except Exception:
            pass
    if not xs:
        return vp
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    return ViewportDef(
        x=vp.x, y=vp.y, width=vp.width, height=vp.height,
        scale_denom=vp.scale_denom,
        view_cx=cx, view_cy=cy,
        active=vp.active, label=vp.label,
    )


# ── Render PIL ────────────────────────────────────────────────────────────────

def _try_font(name: str, size: int):
    """Carga fuente TTF por nombre; devuelve None si no existe."""
    try:
        from PIL import ImageFont as _F
        return _F.truetype(name, size)
    except Exception:
        return None


def _load_fonts(base_px: int):
    """Devuelve (font_regular, font_bold, font_small) para el title block."""
    candidates_regular = [
        "arial.ttf", "Arial.ttf",
        "DejaVuSans.ttf", "LiberationSans-Regular.ttf",
        "Helvetica.ttf",
    ]
    candidates_bold = [
        "arialbd.ttf", "Arial Bold.ttf",
        "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
    ]
    reg  = None
    bold = None
    for name in candidates_regular:
        reg = _try_font(name, base_px)
        if reg:
            break
    for name in candidates_bold:
        bold = _try_font(name, base_px)
        if bold:
            break
    # Si nada encontrado se usa el default de PIL
    return reg, bold


def _draw_title_block(draw, layout: LayoutSheet,
                      psx0: float, psy0: float,
                      psx1: float, psy1: float,
                      lay_scale: float):
    """
    Dibuja el cuadro de títulos en la franja inferior completa del papel.

    Diseño (vista horizontal):
    ┌────────────────┬──────────────────────────────┬──────────┬──────────┐
    │ ESTUDIO MERLOS │  [proyecto]                  │ Lámina   │  [fecha] │
    │ [firma]        │  [descripción]               │ [número] │  [escala]│
    └────────────────┴──────────────────────────────┴──────────┴──────────┘
    """
    tb_h  = TB_HEIGHT_MM * lay_scale
    if tb_h < 5 or (psx1 - psx0) < 10:
        return

    tb_x0 = psx0
    tb_y0 = psy1 - tb_h         # top of title block
    tb_x1 = psx1
    tb_y1 = psy1                 # bottom = bottom of paper

    # Fondo y borde exterior
    draw.rectangle([tb_x0, tb_y0, tb_x1, tb_y1],
                   fill="#F8F8F0", outline="#000000", width=max(1, int(lay_scale * 0.3)))

    # Línea horizontal central (divide en 2 filas)
    mid_y = (tb_y0 + tb_y1) / 2
    draw.line([tb_x0, mid_y, tb_x1, mid_y], fill="#000000", width=1)

    # Columnas (proporciones relativas al ancho total)
    total_w = tb_x1 - tb_x0
    col1 = tb_x0 + total_w * 0.18   # fin col estudio
    col2 = tb_x0 + total_w * 0.68   # fin col proyecto
    col3 = tb_x0 + total_w * 0.82   # fin col lámina

    for cx in (col1, col2, col3):
        draw.line([cx, tb_y0, cx, tb_y1], fill="#000000", width=1)

    # Fuentes
    base_px = max(8, min(14, int(tb_h * 0.18)))
    f_reg, f_bold = _load_fonts(base_px)

    pad = max(3, int(tb_h * 0.06))
    fecha = layout.tb_fecha or datetime.datetime.now().strftime("%d/%m/%Y")

    def _t(x, y, text, bold=False):
        f = f_bold if bold else f_reg
        draw.text((x + pad, y + pad), text, fill="#000000", font=f)

    # Fila 1 (superior)
    _t(tb_x0, tb_y0, layout.tb_firma, bold=True)
    _t(col1,  tb_y0, layout.tb_proyecto, bold=True)
    _t(col2,  tb_y0, f"Lám. {layout.tb_numero}", bold=True)
    _t(col3,  tb_y0, fecha)

    # Fila 2 (inferior)
    _t(tb_x0, mid_y, "Arq. Joseph Merlos")
    _t(col1,  mid_y, layout.tb_desc)
    _t(col2,  mid_y, f"Escala {layout.tb_escala}")
    _t(col3,  mid_y, "Estudio Merlos AI")


def _lighten(hex_color: str, delta: int) -> str:
    """Aclara un color hex en `delta` unidades (igual que _cv_grid en engine)."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
        return f"#{min(255,r+delta):02X}{min(255,g+delta):02X}{min(255,b+delta):02X}"
    except Exception:
        return hex_color


def render_layout_pil(
    layout:      LayoutSheet,
    W:           int,
    H:           int,
    cv_bg:       str,
    lay_scale:   float,   # px por mm (zoom del paper space)
    offset_x:    float,   # screen x del origen del papel (esquina izq)
    offset_y:    float,   # screen y de la base del papel (Y aumenta hacia ABAJO)
    entities:    list,
    layers:      dict,
    block_defs:  dict,
    entity_index: dict,
    entity_cell: float,
    renderer,             # instancia de BaseRenderer (RendererPIL o RendererOpenGL)
    config:      dict = None,  # settings.json — necesario para dimstyles en cotas
    axis_color:  str  = "#CCCCCC",  # color de ejes del usuario
) -> object:              # devuelve PIL.Image o None
    """
    Renderiza una lámina de paper space como imagen PIL.

    Sistema de coordenadas del paper space:
        sx = offset_x + wx * lay_scale        (wx en mm desde borde izquierdo)
        sy = offset_y - wy * lay_scale        (wy en mm desde borde inferior)
        → origen del papel = esquina inferior-izquierda → (offset_x, offset_y)
        → esquina superior-derecha → (offset_x + pw*s,  offset_y - ph*s)

    El fondo fuera del papel usa cv_bg (igual que el espacio modelo).
    """
    try:
        from PIL import Image as _Img, ImageDraw as _Draw
    except ImportError:
        return None

    from cad.renderer_pil import RenderCtx

    pw_mm, ph_mm = paper_size(layout.paper, layout.orientation)

    # ── Crear imagen base con color del usuario (= mismo que modelo) ──────
    img  = _Img.new("RGB", (W, H), cv_bg)
    draw = _Draw(img)

    # ── Rectángulo del papel ──────────────────────────────────────────────
    # Coordenadas en screen
    psx0 = offset_x                          # left
    psy1 = offset_y                          # bottom (mayor y en screen)
    psx1 = offset_x + pw_mm * lay_scale      # right
    psy0 = offset_y - ph_mm * lay_scale      # top   (menor y en screen)

    paper_visible = (psx1 > 0 and psx0 < W and psy1 > 0 and psy0 < H)

    if paper_visible:
        # Sombra del papel
        sh = max(3, int(lay_scale * 1.5))
        draw.rectangle(
            [int(psx0 + sh), int(psy0 + sh), int(psx1 + sh), int(psy1 + sh)],
            fill="#3A3A3A",
        )
        # Papel blanco
        draw.rectangle(
            [int(psx0), int(psy0), int(psx1), int(psy1)],
            fill="#FFFFFF",
        )
        # Borde del papel
        lw = max(1, int(lay_scale * 0.2))
        draw.rectangle(
            [int(psx0), int(psy0), int(psx1), int(psy1)],
            outline="#888888", width=lw,
        )

    # ── Viewports ─────────────────────────────────────────────────────────
    for vp in layout.viewports:
        if not vp.active:
            continue

        # Posición del viewport en screen
        #   wx=vp.x, wy=vp.y+vp.height  → esquina superior-izquierda del vp
        vsx0 = offset_x + vp.x * lay_scale
        vsy0 = offset_y - (vp.y + vp.height) * lay_scale    # top (menor y screen)
        vsx1 = vsx0 + vp.width * lay_scale
        vsy1 = offset_y - vp.y * lay_scale                   # bottom (mayor y screen)

        # Clip al canvas
        cx0  = max(0, int(vsx0));  cy0  = max(0, int(vsy0))
        cx1  = min(W, int(vsx1));  cy1  = min(H, int(vsy1))
        if cx1 <= cx0 or cy1 <= cy0:
            continue

        sub_w = cx1 - cx0
        sub_h = cy1 - cy0

        # ── Escala del modelo dentro del viewport ──────────────────────
        # 1:N → 1mm papel = N mm realidad = N/1000 m modelo
        # → 1 modelo-metro = 1000/N mm papel = 1000/N * lay_scale px
        model_scale = 1000.0 * lay_scale / max(1, vp.scale_denom)

        # ── Transformada de modelo a screen ───────────────────────────
        # Centro del viewport en screen
        vp_csx = offset_x + (vp.x + vp.width  / 2.0) * lay_scale
        vp_csy = offset_y - (vp.y + vp.height / 2.0) * lay_scale

        # Screen donde cae el origen del modelo
        full_model_ox = vp_csx - vp.view_cx * model_scale
        full_model_oy = vp_csy + vp.view_cy * model_scale

        # Ajustar al origen del sub-raster
        sub_ox = full_model_ox - cx0
        sub_oy = full_model_oy - cy0

        # ── Render del modelo en sub-imagen ───────────────────────────
        # bg_color = cv_bg del usuario (oscuro en pantalla, blanco en PDF porque
        # _exportar_pdf_layouts pasa cv_bg="#FFFFFF" explícitamente)
        _cfg     = config or {}
        _grid_c  = "#EEEEEE" if cv_bg == "#FFFFFF" else _lighten(cv_bg, 18)
        _grid_m  = "#DDDDDD" if cv_bg == "#FFFFFF" else _lighten(cv_bg, 32)
        ctx = RenderCtx(
            W=sub_w, H=sub_h,
            scale=model_scale,
            offset_x=sub_ox,
            offset_y=sub_oy,
            entities=entities,
            layers=layers,
            block_defs=block_defs,
            entity_index=entity_index,
            entity_cell=entity_cell,
            grid_on=False,        # sin grilla en paper space
            bg_color=cv_bg,       # usa color del usuario (blanco en PDF, oscuro en pantalla)
            grid_color=_grid_c,
            grid_maj_color=_grid_m,
            axis_color=axis_color,   # color de ejes del usuario
            select_color=_cfg.get("select_color", "#FFD700"),
            config=_cfg,
        )

        try:
            vp_img = renderer.render(ctx).image
        except Exception as exc:
            print(f"[WARN] layout viewport render: {exc}")
            vp_img = None

        if vp_img is not None:
            img.paste(vp_img, (cx0, cy0))
        else:
            # Fallback: rectángulo blanco con texto de error
            draw.rectangle([cx0, cy0, cx1, cy1], fill="#FFFFFF", outline="#FF0000", width=1)

        # Borde del viewport (línea delgada negra, como en AutoCAD)
        bw = max(1, int(lay_scale * 0.15))
        draw.rectangle([int(vsx0), int(vsy0), int(vsx1), int(vsy1)],
                       outline="#333333", width=bw)

    # ── Cuadro de títulos ─────────────────────────────────────────────────
    if paper_visible:
        _draw_title_block(draw, layout, psx0, psy0, psx1, psy1, lay_scale)

    # ── Nombre del layout (label arriba del papel) ────────────────────────
    if paper_visible and lay_scale > 0.8:
        try:
            label_x = max(0, int(psx0))
            label_y = max(0, int(psy0) - max(16, int(lay_scale * 5)))
            if label_y >= 0:
                draw.text((label_x + 4, label_y + 2),
                          layout.name, fill="#AAAAAA")
        except Exception:
            pass

    return img
