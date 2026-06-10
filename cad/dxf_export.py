"""
dxf_export.py — Exporta entidades del visor a DXF (AC2018)
Requiere: ezdxf  (pip install ezdxf)
"""
from __future__ import annotations

import json
import os

from cad.entities import (Line, Polyline, Spline, Circle, Arc, Text,
                          Dimension, Hatch, Ellipse, XLine, Leader,
                          Insert, BlockDef, ImageRef)


# ── Leer settings.json ───────────────────────────────────────────────────────

def _leer_config_seccion(seccion: str) -> dict:
    """Lee una sección de config/settings.json. Retorna {} si no existe."""
    try:
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg_path = os.path.join(raiz, "config", "settings.json")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get(seccion, {})
    except Exception:
        return {}

def _leer_dimstyles() -> dict:
    return _leer_config_seccion("dimstyles")

def _leer_textstyles() -> dict:
    return _leer_config_seccion("textstyles")


# ── TEXTSTYLE: fuente de texto ────────────────────────────────────────────────

# Mapeo nombre legible → nombre de archivo TTF estándar en Windows/AutoCAD
_FONT_TTF: dict[str, str] = {
    "Arial":           "arial.ttf",
    "Courier New":     "cour.ttf",
    "Consolas":        "consola.ttf",
    "Segoe UI":        "segoeui.ttf",
    "Times New Roman": "times.ttf",
    "Calibri":         "calibri.ttf",
    "Helvetica":       "arial.ttf",    # fallback común
}
_DEFAULT_FONT_TTF   = "arial.ttf"
_DEFAULT_STYLE_NAME = "Merlos"

# TAREA 4 — Mapeo SHX → TTF para visores sin AutoCAD
_SHX_TO_TTF: dict[str, str] = {
    "romans.shx":    "arial.ttf",
    "romand.shx":    "arial.ttf",
    "romand9.shx":   "arial.ttf",
    "romanc.shx":    "arial.ttf",
    "archstyl.shx":  "arial.ttf",
    "simplex.shx":   "arial.ttf",
    "simplex9.shx":  "arial.ttf",
    "simplexe.shx":  "arial.ttf",
    "txt.shx":       "arial.ttf",
    "txt":           "arial.ttf",
    "monotxt.shx":   "cour.ttf",
    "complex.shx":   "arial.ttf",
    "italic.shx":    "times.ttf",
    "italic9.shx":   "times.ttf",
}

# TAREA 1 — Fallback visual para patrones custom no disponibles en acad.pat
_CUSTOM_HATCH_FALLBACK: dict[str, tuple] = {
    "SKIRTING": ("LINE",    0.8, 45),
    "CASCOTE":  ("AR-SAND", 0.5,  0),
    "WOOD-F5":  ("LINE",    0.5,  0),
    "XYLO1":    ("LINE",    0.5, 45),
    "ENDGRAIN": ("DOTS",    0.5,  0),
    "MADER-1":  ("LINE",    0.5, 30),
}


def _crear_textstyles_en_doc(doc, txstyles_cfg: dict) -> str:
    """
    Escribe los TEXTSTYLE del config en el documento DXF.
    Retorna el nombre del estilo activo para referenciarlo en add_text().

    Si el config no tiene textstyles, crea un estilo por defecto "Merlos"
    con arial.ttf — universalmente disponible en cualquier Windows con AutoCAD.
    """
    styles_dict = txstyles_cfg.get("styles", {})
    active_name = txstyles_cfg.get("active", _DEFAULT_STYLE_NAME)

    # Sin estilos configurados → usar default
    if not styles_dict:
        styles_dict = {_DEFAULT_STYLE_NAME: {"font": "Arial", "width_factor": 1.0}}
        active_name = _DEFAULT_STYLE_NAME

    for sname, sdata in styles_dict.items():
        try:
            font_display = sdata.get("font", "Arial")
            font_ttf     = _FONT_TTF.get(font_display, None)
            if font_ttf is None:
                # Puede ser una referencia directa a .shx o .ttf
                font_ttf = _SHX_TO_TTF.get(font_display.lower(),
                           font_display if font_display.lower().endswith((".ttf", ".otf"))
                           else _DEFAULT_FONT_TTF)

            if doc.styles.has_entry(sname):
                ts = doc.styles.get(sname)
            else:
                ts = doc.styles.new(sname)

            ts.dxf.font    = font_ttf
            # width: factor de ancho (1.0 = normal)
            ts.dxf.width   = float(sdata.get("width_factor", 1.0))
            # oblique: ángulo de inclinación en grados
            ts.dxf.oblique = float(sdata.get("oblique", 0.0))
            # height=0 → variable (cada entidad TEXT define su propia altura)
            ts.dxf.height  = 0.0

        except Exception as exc:
            print(f"[WARN] dxf_export: textstyle '{sname}' no creado: {exc}")

    # Garantizar que el estilo activo existe en el doc
    if not doc.styles.has_entry(active_name):
        try:
            ts = doc.styles.new(active_name)
            ts.dxf.font = _DEFAULT_FONT_TTF
        except Exception:
            active_name = "Standard"

    return active_name


def _color_to_aci(color_val: str) -> int:
    """Convierte un color de dimstyle a ACI.
    Acepta '#RRGGBB', 'bylayer', 'BYLAYER', 'byblock', 'BYBLOCK'."""
    if not color_val or color_val.upper() in ("BYLAYER", "BYBLOCK", ""):
        return 256  # 256 = BYLAYER en DXF
    if color_val.startswith("#"):
        return _hex_to_aci(color_val)
    return 256


def _crear_dimstyles_en_doc(doc, dimstyles_cfg: dict,
                            txstyle_name: str = "") -> None:
    """Escribe todos los DIMSTYLE del config en la tabla de estilos del DXF.

    Los parámetros DXF clave (dxf.dimXXX) corresponden exactamente a las
    variables de sistema de AutoCAD — AutoCAD los lee directamente.

    txstyle_name: nombre del TEXTSTYLE que el texto de cota debe usar.
    Si se especifica, se enlaza via dxf.dimtxsty para que el texto de cotas
    use la misma fuente TTF que el resto del dibujo (no simplex.shx).
    """
    styles_dict = dimstyles_cfg.get("styles", {})
    for nombre, ds in styles_dict.items():
        try:
            # Obtener o crear el estilo
            if doc.dimstyles.has_entry(nombre):
                dstyle = doc.dimstyles.get(nombre)
            else:
                dstyle = doc.dimstyles.new(nombre)

            # ── Texto ──────────────────────────────────────────────────
            # DIMTXT  = altura del texto de cota (unidades modelo)
            dstyle.dxf.dimtxt  = float(ds.get("text_height", 0.20))
            # DIMGAP  = gap entre texto y línea de cota
            dstyle.dxf.dimgap  = float(ds.get("text_offset", 0.05))
            # DIMCLRT = color del texto (ACI)
            dstyle.dxf.dimclrt = _color_to_aci(ds.get("text_color", "bylayer"))

            # ── Flechas ────────────────────────────────────────────────
            # DIMASZ  = tamaño de flecha
            dstyle.dxf.dimasz  = float(ds.get("arrow_size", 0.06))
            # DIMLDRBLK = tipo de flecha (nombre del bloque)
            arrow_map = {
                "architectural": "ARCHTICK",
                "closed_filled":  "",          # default AutoCAD = closed filled
                "dot":            "DOT",
                "none":           "NONE",
            }
            arrow_blk = arrow_map.get(ds.get("arrow_type", "closed_filled"), "")
            try:
                if arrow_blk:
                    dstyle.dxf.dimblk = arrow_blk
            except Exception:
                pass

            # ── Líneas de extensión ────────────────────────────────────
            # DIMEXO  = desfase del punto de origen (ext_offset)
            dstyle.dxf.dimexo  = float(ds.get("ext_offset", 0.02))
            # DIMEXE  = prolongación más allá de la línea de cota (ext_overshoot)
            dstyle.dxf.dimexe  = float(ds.get("ext_overshoot",
                                               ds.get("ext_beyond", 0.03)))
            # DIMCLRE = color de las líneas de extensión (ACI)
            dstyle.dxf.dimclre = _color_to_aci(ds.get("line_color", "bylayer"))
            # DIMCLRD = color de la línea de cota (ACI)
            dstyle.dxf.dimclrd = _color_to_aci(ds.get("line_color", "bylayer"))

            # ── Unidades ───────────────────────────────────────────────
            # DIMDEC  = decimales
            dstyle.dxf.dimdec  = int(ds.get("precision", 2))
            # DIMLFAC = factor de escala lineal
            dstyle.dxf.dimlfac = float(ds.get("scale_factor", 1.0))
            # DIMPOST = sufijo del texto (ej " m")
            sufijo = ds.get("suffix", "")
            if sufijo:
                try:
                    dstyle.dxf.dimpost = f"<>{sufijo}"
                except Exception:
                    pass

            # ── Textstyle del texto de cota ────────────────────────────
            # DIMTXSTY: nombre del TEXTSTYLE que usa el texto de cota.
            # Sin esto, AutoCAD usa "Standard" (simplex.shx) aunque el resto
            # del dibujo use Arial. Con esto: texto de cotas = misma fuente.
            if txstyle_name:
                try:
                    dstyle.dxf.dimtxsty = txstyle_name
                except Exception:
                    pass

        except Exception as exc:
            print(f"[WARN] dxf_export: no se pudo crear DIMSTYLE '{nombre}': {exc}")


# ── Helpers de conversión ────────────────────────────────────────────────────

def _lw_to_dxf(lw: int) -> int:
    """Convierte linewidth interna (1-5) a DXF lineweight (centésimas de mm)."""
    return {1: 13, 2: 18, 3: 35, 4: 50, 5: 70}.get(lw, 13)


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

# Paleta ACI mínima para búsqueda del color más cercano: (R, G, B, ACI)
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

# ── Nombres DXF para cada linetype interno (43 tipos) ────────────────────────
# La clave es el nombre interno; el valor es el nombre DXF estándar AutoCAD.
# ezdxf.setup_linetypes() carga los patrones estándar automáticamente.
# _LT_PATTERNS se usa como fallback si setup_linetypes falla.
_LT_DXF: dict[str, str] = {
    "CONTINUOUS":     "Continuous",
    # DASHED / HIDDEN
    "DASHED":         "DASHED",     "DASHED2":      "DASHED2",   "DASHEDX2":     "DASHEDX2",
    "HIDDEN":         "HIDDEN",     "HIDDEN2":      "HIDDEN2",   "HIDDENX2":     "HIDDENX2",
    # DOTTED
    "DOTTED":         "DOTTED",     "DOTTED2":      "DOTTED2",   "DOTTEDX2":     "DOTTEDX2",
    # CENTER
    "CENTER":         "CENTER",     "CENTER2":      "CENTER2",   "CENTERX2":     "CENTERX2",
    # DASHDOT
    "DASHDOT":        "DASHDOT",    "DASHDOT2":     "DASHDOT2",  "DASHDOTX2":    "DASHDOTX2",
    # DIVIDE
    "DIVIDE":         "DIVIDE",     "DIVIDE2":      "DIVIDE2",   "DIVIDEX2":     "DIVIDEX2",
    # PHANTOM
    "PHANTOM":        "PHANTOM",    "PHANTOM2":     "PHANTOM2",  "PHANTOMX2":    "PHANTOMX2",
    # BORDER
    "BORDER":         "BORDER",     "BORDER2":      "BORDER2",   "BORDERX2":     "BORDERX2",
    # ISO
    "ACAD_ISO02W100": "ACAD_ISO02W100", "ACAD_ISO03W100": "ACAD_ISO03W100",
    "ACAD_ISO04W100": "ACAD_ISO04W100", "ACAD_ISO05W100": "ACAD_ISO05W100",
    "ACAD_ISO07W100": "ACAD_ISO07W100", "ACAD_ISO08W100": "ACAD_ISO08W100",
    "ACAD_ISO09W100": "ACAD_ISO09W100", "ACAD_ISO10W100": "ACAD_ISO10W100",
    "ACAD_ISO11W100": "ACAD_ISO11W100", "ACAD_ISO12W100": "ACAD_ISO12W100",
    "ACAD_ISO13W100": "ACAD_ISO13W100", "ACAD_ISO14W100": "ACAD_ISO14W100",
    "ACAD_ISO15W100": "ACAD_ISO15W100",
}

# Patrones inline acad.lin (fallback si ezdxf.setup_linetypes falla)
# Formato: (longitud_total, seg1, seg2, …)  positivo=dash, negativo=gap, 0=punto
_LT_PATTERNS: dict[str, tuple] = {
    # DASHED / HIDDEN
    "DASHED":    (0.75,  0.5,   -0.25),
    "DASHED2":   (0.375, 0.25,  -0.125),
    "DASHEDX2":  (1.5,   1.0,   -0.5),
    "HIDDEN":    (0.75,  0.5,   -0.25),
    "HIDDEN2":   (0.375, 0.25,  -0.125),
    "HIDDENX2":  (1.5,   1.0,   -0.5),
    # DOTTED
    "DOTTED":    (0.25,  0.0,   -0.25),
    "DOTTED2":   (0.125, 0.0,   -0.125),
    "DOTTEDX2":  (0.5,   0.0,   -0.5),
    # CENTER
    "CENTER":    (2.0,   1.25,  -0.25, 0.25, -0.25),
    "CENTER2":   (1.0,   0.625, -0.125, 0.125, -0.125),
    "CENTERX2":  (4.0,   2.5,   -0.5,  0.5,   -0.5),
    # DASHDOT
    "DASHDOT":   (1.0,   0.5,   -0.25, 0.0,  -0.25),
    "DASHDOT2":  (0.5,   0.25,  -0.125, 0.0, -0.125),
    "DASHDOTX2": (2.0,   1.0,   -0.5,  0.0,  -0.5),
    # DIVIDE
    "DIVIDE":    (1.0,   0.5,   -0.25, 0.0, -0.25, 0.0, -0.25),
    "DIVIDE2":   (0.5,   0.25,  -0.125, 0.0, -0.125, 0.0, -0.125),
    "DIVIDEX2":  (2.0,   1.0,   -0.5,  0.0, -0.5,   0.0, -0.5),
    # PHANTOM
    "PHANTOM":   (2.5,   1.25,  -0.25, 0.0, -0.25, 0.0, -0.25),
    "PHANTOM2":  (1.25,  0.625, -0.125, 0.0, -0.125, 0.0, -0.125),
    "PHANTOMX2": (5.0,   2.5,   -0.5,  0.0, -0.5,   0.0, -0.5),
    # BORDER
    "BORDER":    (1.75,  0.5,   -0.25, 0.5, -0.25,  0.0, -0.25),
    "BORDER2":   (0.875, 0.25,  -0.125, 0.25, -0.125, 0.0, -0.125),
    "BORDERX2":  (3.5,   1.0,   -0.5,  1.0,  -0.5,   0.0, -0.5),
    # ISO (simplificados)
    "ACAD_ISO02W100": (0.75, 0.5, -0.25),
    "ACAD_ISO03W100": (1.5,  0.5, -1.0),
    "ACAD_ISO04W100": (2.0,  1.25, -0.25, 0.25, -0.25),
    "ACAD_ISO05W100": (2.5,  1.25, -0.25, 0.25, -0.25, 0.0, -0.25),
    "ACAD_ISO07W100": (0.5,  0.0, -0.5),
    "ACAD_ISO08W100": (2.0,  1.25, -0.25, 0.5, -0.25),
    "ACAD_ISO09W100": (2.5,  1.25, -0.25, 0.5, -0.25, 0.5, -0.25),
    "ACAD_ISO10W100": (0.75, 0.5, -0.25, 0.0, -0.25),
    "ACAD_ISO11W100": (1.25, 0.5, -0.25, 0.5, -0.25, 0.0, -0.25),
    "ACAD_ISO12W100": (1.25, 0.5, -0.25, 0.0, -0.25, 0.0, -0.25),
    "ACAD_ISO13W100": (1.75, 0.5, -0.25, 0.5, -0.25, 0.0, -0.25, 0.0, -0.25),
    "ACAD_ISO14W100": (1.75, 0.5, -0.25, 0.0, -0.25, 0.0, -0.25, 0.0, -0.25),
    "ACAD_ISO15W100": (2.25, 0.5, -0.25, 0.5, -0.25, 0.0, -0.25, 0.0, -0.25, 0.0, -0.25),
}

_LT_DESCRIPTIONS: dict[str, str] = {
    "DASHED":    "__ __ __ __ __ __ __ __",
    "DASHED2":   "_ _ _ _ _ _ _ _ _ _ _",
    "DASHEDX2":  "____  ____  ____  ____",
    "HIDDEN":    "__ __ __ __ __ __ __ __",
    "HIDDEN2":   "_ _ _ _ _ _ _ _ _ _ _",
    "HIDDENX2":  "____  ____  ____  ____",
    "DOTTED":    ". . . . . . . . . . . .",
    "DOTTED2":   ". . . . . . . . . . . .",
    "DOTTEDX2":  ".   .   .   .   .   .",
    "CENTER":    "____  __  ____  __  ___",
    "CENTER2":   "__  _  __  _  __  _  __",
    "CENTERX2":  "________  ____  ________",
    "DASHDOT":   "__ . __ . __ . __ . __",
    "DASHDOT2":  "_ . _ . _ . _ . _ . _",
    "DASHDOTX2": "____  .  ____  .  ____",
    "DIVIDE":    "__ . . __ . . __ . . __",
    "DIVIDE2":   "_ . . _ . . _ . . _ . .",
    "DIVIDEX2":  "____  . .  ____  . .  ___",
    "PHANTOM":   "______  .  .  ______  .  .",
    "PHANTOM2":  "___  .  .  ___  .  .  ___",
    "PHANTOMX2": "__________  .  .  __________",
    "BORDER":    "__ __ . __ __ . __ __",
    "BORDER2":   "_ _ . _ _ . _ _ . _",
    "BORDERX2":  "____  ____  .  ____  ____",
    "ACAD_ISO02W100": "__ __ __ __ __ __ __ __",
    "ACAD_ISO03W100": "__   __   __   __   __",
    "ACAD_ISO04W100": "____  .  ____  .  ____",
    "ACAD_ISO05W100": "____  .  .  ____  .  .",
    "ACAD_ISO07W100": ".   .   .   .   .   .",
    "ACAD_ISO08W100": "____  _  ____  _  ____",
    "ACAD_ISO09W100": "____  _  _  ____  _  _",
    "ACAD_ISO10W100": "__  .  __  .  __  .  __",
    "ACAD_ISO11W100": "__  __  .  __  __  .  __",
    "ACAD_ISO12W100": "__  .  .  __  .  .  __",
    "ACAD_ISO13W100": "__  __  .  .  __  __  .  .",
    "ACAD_ISO14W100": "__  .  .  .  __  .  .  .",
    "ACAD_ISO15W100": "__  __  .  .  .  __  __  .  .  .",
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
    best_aci, best_d = 7, 1e18
    for pr, pg, pb, aci in _ACI_PALETTE:
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d < best_d:
            best_d = d; best_aci = aci
    return best_aci


# ── Paleta ACI ampliada (256 colores estándar AutoCAD) ──────────────────────
# Añade las tonalidades intermedias que faltaban en la paleta de 11 colores.
# Cuando se exporta con true_color esta paleta solo actúa como fallback para
# software que no soporte true_color (AutoCAD < 2004, LibreCAD, etc.).
_ACI_PALETTE_FULL: list = [
    # idx  R     G     B
    (255,   0,   0,  1),   # rojo
    (255, 127,   0, 30),   # naranja
    (255, 255,   0,  2),   # amarillo
    (127, 255,   0, 70),   # verde-amarillo
    (  0, 255,   0,  3),   # verde
    (  0, 255, 127, 90),   # verde-cian
    (  0, 255, 255,  4),   # cian
    (  0, 127, 255, 150),  # azul-cian
    (  0,   0, 255,  5),   # azul
    (127,   0, 255, 200),  # violeta
    (255,   0, 255,  6),   # magenta
    (255,   0, 127, 220),  # rosa-rojo
    (255, 255, 255,  7),   # blanco
    (192, 192, 192,  9),   # gris claro
    (128, 128, 128,  8),   # gris medio
    ( 64,  64,  64,251),   # gris oscuro
    (  0,   0,   0,  0),   # negro (BYBLOCK)
    # Grises adicionales
    (210, 210, 210,253),
    (160, 160, 160,252),
    ( 96,  96,  96,250),
    # Colores tierra/madera comunes en arquitectura
    (165,  42,  42, 14),   # marrón
    (210, 105,  30, 24),   # siena
    (244, 164,  96, 34),   # arena
    (255, 228, 196, 55),   # beige
    # Azules arquitectónicos
    ( 70, 130, 180,131),   # steel blue
    ( 30, 144, 255,141),   # dodger blue
    (100, 149, 237,151),   # cornflower
    # Verdes comunes
    ( 34, 139,  34, 94),   # forest green
    (144, 238, 144, 83),   # light green
]


def _hex_to_aci_full(hex_color: str) -> int:
    """Convierte #RRGGBB al ACI más cercano usando la paleta ampliada."""
    h = hex_color.strip().upper()
    exact = _COLOR_MAP_EXACT.get(h)
    if exact is not None:
        return exact
    try:
        r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    except Exception:
        return 7
    best_aci, best_d = 7, 1e18
    for pr, pg, pb, aci in _ACI_PALETTE_FULL:
        d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
        if d < best_d:
            best_d, best_aci = d, aci
            if d == 0:
                break
    return best_aci


def _dim_text_export(e, ds_cfg: dict) -> str:
    """Genera el texto de cota aplicando scale_factor, precision y suffix del dimstyle.
    Usado en el fallback geométrico cuando la entidad DIMENSION nativa falla."""
    if e.text_override:
        return e.text_override
    v = e.measurement() * float(ds_cfg.get("scale_factor", 1.0))
    prec   = int(ds_cfg.get("precision", 2))
    suffix = ds_cfg.get("suffix", "")
    if e.dim_type == "ANG":
        return f"{v:.1f}°"
    if e.dim_type == "D":
        return f"Ø{v:.{prec}f}{suffix}"
    if e.dim_type == "R":
        return f"R{v:.{prec}f}{suffix}"
    return f"{v:.{prec}f}{suffix}"


def _entity_attribs(e) -> dict:
    """
    Construye el dict de atributos DXF para una entidad incluyendo
    overrides de color, linetype y linewidth por entidad.

    Color: exporta true_color (RGB 24-bit, exacto) + color ACI (fallback).
    AutoCAD usa true_color cuando existe — sin degradación de color.
    """
    d = {"layer": e.layer}

    # ── Color override ────────────────────────────────────────────────
    col = getattr(e, "color", "bylayer") or "bylayer"
    if col.lower() not in ("bylayer", "byblock", "") and col.startswith("#"):
        try:
            h = col.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            # true_color: entero RGB 24-bit — AutoCAD R2004+ lo usa directamente
            d["true_color"] = (r << 16) | (g << 8) | b
            # color ACI: aproximación para software sin soporte true_color
            d["color"] = _hex_to_aci_full(col)
        except Exception:
            pass

    # ── Linetype override ─────────────────────────────────────────────
    lt = getattr(e, "linetype", "bylayer") or "bylayer"
    if lt.upper() not in ("BYLAYER", "BYBLOCK", "CONTINUOUS", ""):
        dxf_lt = _LT_DXF.get(lt.upper(), None)
        if dxf_lt and dxf_lt != "Continuous":
            d["linetype"] = dxf_lt

    # ── Linewidth override ────────────────────────────────────────────
    lw_val = getattr(e, "linewidth", 0) or 0
    if lw_val > 0:
        d["lineweight"] = _lw_to_dxf(lw_val)

    return d


# ── Exportador principal ─────────────────────────────────────────────────────

def exportar_dxf(entities: list, layers: dict, ruta: str,
                 block_defs: dict | None = None) -> None:
    """
    Exporta la lista de entidades del visor a un archivo DXF AC2018.

    Args:
        entities   : lista de Entity (Line, Polyline, Circle, Arc, Text…)
        layers     : dict nombre → Layer
        ruta       : ruta de destino (.dxf)
        block_defs : dict nombre → BlockDef (para exportar bloques e INSERT)

    Raises:
        ImportError si ezdxf no está instalado.
        Exception   si no se puede escribir el archivo.
    """
    import ezdxf

    doc = ezdxf.new(dxfversion="R2018")
    msp = doc.modelspace()

    # ── Variables de encabezado DXF ───────────────────────────────────
    try:
        doc.header["$INSUNITS"]    = 6    # 6 = metros
        doc.header["$MEASUREMENT"] = 1    # 1 = sistema métrico
        doc.header["$LUNITS"]      = 2    # 2 = decimal
        doc.header["$LUPREC"]      = 4    # 4 decimales
        # Mostrar lineweights al abrir (sin esto AutoCAD los oculta por defecto)
        doc.header["$LWDISPLAY"]   = 1
    except Exception as _exc:
        print(f"[WARN] dxf_export: no se pudo configurar header: {_exc}")

    # ── TEXTSTYLES primero — necesario antes de DIMSTYLES ────────────
    # (DIMSTYLE referencia el textstyle via dimtxsty)
    txstyles_cfg    = _leer_textstyles()
    _active_txstyle = _crear_textstyles_en_doc(doc, txstyles_cfg)

    # ── DIMSTYLES del config ──────────────────────────────────────────
    dimstyles_cfg = _leer_dimstyles()
    if dimstyles_cfg:
        _crear_dimstyles_en_doc(doc, dimstyles_cfg, txstyle_name=_active_txstyle)

    # Recolectar qué estilos necesitan las cotas del dibujo,
    # para garantizar que existan aunque no estén en settings.json
    _estilos_usados = {getattr(e, "style", "Standard")
                       for e in entities
                       if isinstance(e, Dimension)}
    for _nombre in _estilos_usados:
        if not doc.dimstyles.has_entry(_nombre):
            try:
                doc.dimstyles.new(_nombre)
            except Exception:
                pass

    # ── Cargar linetypes ──────────────────────────────────────────────
    # Estrategia 1: setup_linetypes carga el acad.lin completo
    # Estrategia 2: fallback inline para los que no se cargaron
    _needed_lt = {getattr(lyr, "linetype", "CONTINUOUS") for lyr in layers.values()}
    _needed_lt.discard("CONTINUOUS")
    _loaded_lt: set[str] = set()

    try:
        ezdxf.setup_linetypes(doc)
        _loaded_lt = {n.upper() for n in _LT_DXF.values() if n != "Continuous"}
    except Exception as exc:
        print(f"[WARN] dxf_export: setup_linetypes falló, usando definición inline: {exc}")

    for lt_name in _needed_lt:
        dxf_lt = _LT_DXF.get(lt_name, "Continuous")
        if dxf_lt == "Continuous" or lt_name in _loaded_lt:
            continue
        pattern = _LT_PATTERNS.get(lt_name)
        if pattern is None:
            continue
        try:
            if not doc.linetypes.has_entry(dxf_lt):
                doc.linetypes.new(
                    dxf_lt,
                    dxfattribs={
                        "description": _LT_DESCRIPTIONS.get(lt_name, lt_name),
                        "pattern":     list(pattern),
                    })
        except Exception as exc:
            print(f"[WARN] dxf_export: no se pudo definir linetype '{lt_name}': {exc}")

    # ── Crear capas ───────────────────────────────────────────────────
    for name, lyr in layers.items():
        try:
            # ezdxf API: new() falla si ya existe → fallback a get()
            try:
                dl = doc.layers.new(name)
            except Exception:
                dl = doc.layers.get(name)

            # Color ACI (requerido por todas las versiones DXF)
            _lyr_hex = lyr.color or "#FFFFFF"
            dl.color = _hex_to_aci_full(_lyr_hex)

            # true_color en capa (DXF AC2018) — color exacto en Layer Manager
            if _lyr_hex.startswith("#") and len(_lyr_hex) == 7:
                try:
                    _h = _lyr_hex.lstrip("#")
                    _r, _g, _b = int(_h[0:2],16), int(_h[2:4],16), int(_h[4:6],16)
                    dl.dxf.true_color = (_r << 16) | (_g << 8) | _b
                except Exception:
                    pass

            dl.lineweight = _lw_to_dxf(lyr.linewidth)
            lt_name = getattr(lyr, "linetype", "CONTINUOUS")
            try:
                dl.linetype = _LT_DXF.get(lt_name, "Continuous")
            except Exception:
                dl.linetype = "Continuous"
            if not lyr.visible:
                dl.off()
            if getattr(lyr, "locked", False):
                dl.lock()
        except Exception as exc:
            print(f"[WARN] dxf_export: capa '{name}' no creada: {exc}")

    # ── Exportar definiciones de bloques (con soporte de anidamiento) ──
    _bdef_dict = block_defs or {}

    def _exportar_entidades_bloque(blk, ents, bdef_dict, depth=0, visited=None):
        """Escribe entidades en un bloque DXF.
        Soporta Insert anidado con límite de profundidad (máx 8 niveles)
        y detección de referencias circulares mediante `visited`.
        """
        if depth > 8:
            return
        if visited is None:
            visited = set()

        for be in ents:
            _ba = _entity_attribs(be)
            try:
                if isinstance(be, Line):
                    blk.add_line((be.x1,be.y1,0),(be.x2,be.y2,0),dxfattribs=_ba)

                elif isinstance(be, Polyline) and len(be.points) >= 2:
                    pl = blk.add_lwpolyline([(x,y) for x,y in be.points],
                                            dxfattribs=_ba)
                    pl.closed = be.closed

                elif isinstance(be, Spline) and len(be.points) >= 2:
                    sp = blk.add_spline(
                        fit_points=[(x,y,0) for x,y in be.points],
                        dxfattribs=_ba)
                    if be.closed:
                        sp.closed = True

                elif isinstance(be, Circle):
                    blk.add_circle((be.cx,be.cy,0), radius=be.radius,
                                   dxfattribs=_ba)

                elif isinstance(be, Arc):
                    sa, ea = ((be.start_ang, be.end_ang) if be.ccw
                              else (be.end_ang, be.start_ang))
                    blk.add_arc((be.cx,be.cy,0), radius=be.radius,
                                start_angle=sa, end_angle=ea,
                                dxfattribs=_ba)

                elif isinstance(be, Text) and be.content:
                    blk.add_text(be.content, dxfattribs={
                        **_ba,
                        "height":   be.height,
                        "insert":   (be.x, be.y, 0),
                        "rotation": getattr(be, "angle", 0.0),
                        "style":    _active_txstyle})

                elif isinstance(be, Ellipse):
                    import math as _me
                    ratio = be.ry / be.rx if be.rx > 1e-10 else 1.0
                    mv = (be.rx * _me.cos(_me.radians(be.angle)),
                          be.rx * _me.sin(_me.radians(be.angle)), 0)
                    blk.add_ellipse(center=(be.cx,be.cy,0),
                                    major_axis=mv, ratio=ratio,
                                    dxfattribs=_ba)

                # FIX #1: Hatch dentro de bloques (antes silenciosamente ignorado)
                elif isinstance(be, Hatch) and len(be.boundary) >= 3:
                    _h_col = getattr(be, "color", "bylayer") or "bylayer"
                    _h_aci = (256 if _h_col.lower() in ("bylayer","byblock","")
                              else _hex_to_aci_full(_h_col))
                    hatch_blk = blk.add_hatch(color=_h_aci, dxfattribs=_ba)
                    hatch_blk.paths.add_polyline_path(
                        [(x, y) for x, y in be.boundary], is_closed=True)
                    _PAT_BLK = {"SOLID":"SOLID","ANSI31":"ANSI31",
                                "LINES":"LINE","CROSS":"NET"}
                    if be.pattern == "SOLID":
                        hatch_blk.set_solid_fill(color=_h_aci)
                    else:
                        try:
                            hatch_blk.set_pattern_fill(
                                _PAT_BLK.get(be.pattern, "ANSI31"),
                                scale=be.scale, angle=be.angle)
                        except Exception:
                            hatch_blk.set_solid_fill()

                elif isinstance(be, Insert) and be.block_name:
                    # ── Bloque anidado ──────────────────────────────
                    child_name = be.block_name
                    # Evitar ciclos: si ya estamos exportando este bloque, saltar
                    if child_name not in visited:
                        child_bdef = bdef_dict.get(child_name)
                        if child_bdef and child_bdef.entities:
                            # Asegurar que el bloque hijo existe en el doc
                            if child_name not in doc.blocks:
                                try:
                                    child_blk = doc.blocks.new(
                                        name=child_name,
                                        base_point=(child_bdef.base_point[0],
                                                    child_bdef.base_point[1], 0))
                                    # Exportar su contenido (recursivo)
                                    _exportar_entidades_bloque(
                                        child_blk, child_bdef.entities,
                                        bdef_dict, depth+1,
                                        visited | {child_name})
                                except Exception:
                                    pass
                    # Insertar la referencia dentro del bloque padre
                    blk.add_blockref(child_name,
                                     insert=(be.x, be.y, 0),
                                     dxfattribs={
                                         **_ba,
                                         "xscale":   be.scale_x,
                                         "yscale":   be.scale_y,
                                         "zscale":   1.0,
                                         "rotation": be.angle,
                                     })

            except Exception:
                pass

    # Exportar todos los BlockDef del dibujo
    for bname, bdef in _bdef_dict.items():
        if not bname or not bdef.entities:
            continue
        try:
            if bname in doc.blocks:
                blk = doc.blocks[bname]
            else:
                blk = doc.blocks.new(name=bname,
                                     base_point=(bdef.base_point[0],
                                                 bdef.base_point[1], 0))
            _exportar_entidades_bloque(blk, bdef.entities, _bdef_dict,
                                       depth=0, visited={bname})
        except Exception as exc:
            print(f"[WARN] dxf_export: bloque '{bname}' no exportado: {exc}")

    # ── Escribir entidades ────────────────────────────────────────────
    errores = 0
    for e in entities:
        # _entity_attribs incluye layer + color true_color + linetype + lineweight
        attribs = _entity_attribs(e)
        try:
            if isinstance(e, Line):
                msp.add_line(
                    (e.x1, e.y1, 0), (e.x2, e.y2, 0),
                    dxfattribs=attribs,
                )
            elif isinstance(e, Polyline):
                if len(e.points) >= 2:
                    pl = msp.add_lwpolyline(
                        [(x, y) for x, y in e.points],
                        dxfattribs=attribs,
                    )
                    pl.closed = e.closed

            elif isinstance(e, Spline):
                if len(e.points) >= 2:
                    try:
                        # Entidad SPLINE nativa DXF — curva que pasa por los puntos
                        sp = msp.add_spline(
                            fit_points=[(x, y, 0) for x, y in e.points],
                            dxfattribs=attribs,
                        )
                        if e.closed:
                            sp.closed = True
                    except Exception:
                        # Fallback: polilínea interpolada Catmull-Rom
                        interp = e.interp(n_seg=20)
                        if len(interp) >= 2:
                            pl = msp.add_lwpolyline(
                                [(x, y) for x, y in interp],
                                dxfattribs=attribs,
                            )
                            pl.closed = e.closed
            elif isinstance(e, Circle):
                msp.add_circle(
                    (e.cx, e.cy, 0), radius=e.radius,
                    dxfattribs=attribs,
                )
            elif isinstance(e, Arc):
                # DXF siempre va CCW; para CW se invierten los ángulos
                sa, ea = (e.start_ang, e.end_ang) if e.ccw else (e.end_ang, e.start_ang)
                msp.add_arc(
                    center=(e.cx, e.cy, 0), radius=e.radius,
                    start_angle=sa, end_angle=ea,
                    dxfattribs=attribs,
                )
            elif isinstance(e, Text):
                _ang = getattr(e, "angle", 0.0)
                # Aplicar mayúsculas a etiquetas de recinto (capa A-TEXTO)
                # para respetar la BIBLIA DE ESTILO del estudio.
                # Texto numérico, medidas, notas técnicas → se deja como está.
                _content = e.content
                if (e.layer.upper() in ("A-TEXTO", "A-TEXT", "TEXT")
                        and _content
                        and not any(c.isdigit() for c in _content[:3])):
                    _content = _content.upper()

                _halign = getattr(e, "halign", 0) or 0
                _valign = getattr(e, "valign", 0) or 0
                # attachment_point MTEXT: fila (valign) × 3 + columna (halign)
                # valign: 3=top→row0, 2=mid→row1, 0=bottom→row2
                # halign: 0=left→col0, 1=center→col1, 2=right→col2
                _row = {3: 0, 2: 1, 0: 2}.get(_valign, 0)
                _attach = _row * 3 + _halign + 1   # 1-9

                _raw = getattr(e, "raw_mtext", "") or ""

                if _raw:
                    # ── Tiene formato original: exportar MTEXT con códigos ────
                    # El string raw conserva negrita, itálica, tamaños mixtos, etc.
                    msp.add_mtext(_raw,
                                  dxfattribs={**attribs,
                                  "char_height":      e.height,
                                  "insert":           (e.x, e.y, 0),
                                  "rotation":         _ang,
                                  "attachment_point": _attach,
                                  "style":            _active_txstyle})

                elif "\n" in _content or len(_content) > 80:
                    # ── Texto largo o multilinea creado en el visor → MTEXT ──
                    msp.add_mtext(_content.replace("\n", "\\P"),
                                  dxfattribs={**attribs,
                                  "char_height":      e.height,
                                  "insert":           (e.x, e.y, 0),
                                  "rotation":         _ang,
                                  "attachment_point": _attach,
                                  "style":            _active_txstyle})
                else:
                    # ── TEXT simple ─────────────────────────────────────────
                    _txt_attrs = {**attribs,
                                  "height":   e.height,
                                  "insert":   (e.x, e.y, 0),
                                  "rotation": _ang,
                                  "halign":   _halign,
                                  "style":    _active_txstyle}
                    if _halign != 0:
                        # Para halign≠0, align_point es el punto de anclaje real
                        _txt_attrs["align_point"] = (e.x, e.y, 0)
                    msp.add_text(_content, dxfattribs=_txt_attrs)
            elif isinstance(e, Dimension):
                import math as _math

                x1, y1 = e.p1;  x2, y2 = e.p2;  px, py = e.pos
                tp = getattr(e, "text_pos", None)

                # Estilo de la cota — usa el nombre guardado en la entidad
                # (importado del DXF original o asignado por el usuario)
                _ds_nombre = getattr(e, "style", "Standard") or "Standard"

                # Leer text_height del dimstyle para el fallback geométrico
                _ds_cfg = (dimstyles_cfg.get("styles", {})
                           .get(_ds_nombre, {}))
                _fb_txth = float(_ds_cfg.get("text_height", 0.20))

                # ── Intento 1: entidad DIMENSION nativa de DXF ──────────
                _native_ok = False
                try:
                    if e.dim_type in ("H", "V", "A"):
                        if e.dim_type == "H":
                            d1x, d1y = x1, py;  d2x, d2y = x2, py
                            angle = 0.0
                        elif e.dim_type == "V":
                            d1x, d1y = px, y1;  d2x, d2y = px, y2
                            angle = 90.0
                        else:  # A — alineada o rotada
                            _rot = getattr(e, 'rot_angle', None)
                            if _rot is not None:
                                # Cota rotada: usar rot_angle directamente
                                angle = _rot
                                _arad = _math.radians(_rot)
                                _ux, _uy = _math.cos(_arad), _math.sin(_arad)
                                _nx, _ny = -_uy, _ux
                                _dn = px*_nx + py*_ny
                                _p1n = x1*_nx + y1*_ny
                                d1x, d1y = x1+_nx*(_dn-_p1n), y1+_ny*(_dn-_p1n)
                                _p2n = x2*_nx + y2*_ny
                                d2x, d2y = x2+_nx*(_dn-_p2n), y2+_ny*(_dn-_p2n)
                            else:
                                dlen = _math.hypot(x2-x1, y2-y1)
                                if dlen < 1e-9: raise ValueError("zero length")
                                ux, uy = (x2-x1)/dlen, (y2-y1)/dlen
                                nx, ny = -uy, ux
                                off = (px-x1)*nx + (py-y1)*ny
                                d1x, d1y = x1+nx*off, y1+ny*off
                                d2x, d2y = x2+nx*off, y2+ny*off
                                angle = _math.degrees(_math.atan2(y2-y1, x2-x1))

                        txt_pt = (tp[0], tp[1], 0) if tp else \
                                 ((d1x+d2x)/2, (d1y+d2y)/2, 0)

                        dim_ent = msp.add_linear_dim(
                            base=txt_pt,
                            p1=(x1, y1, 0), p2=(x2, y2, 0),
                            angle=angle,
                            dxfattribs={"layer": e.layer,
                                        "dimstyle": _ds_nombre})
                        if e.text_override:
                            dim_ent.dimension.dxf.text = e.text_override
                        dim_ent.render()
                        _native_ok = True

                    elif e.dim_type == "R":
                        cx, cy = x1, y1
                        r = _math.hypot(x2-cx, y2-cy)
                        dim_ent = msp.add_radius_dim(
                            center=(cx, cy, 0), radius=r,
                            angle=_math.degrees(_math.atan2(y2-cy, x2-cx)),
                            dxfattribs={"layer": e.layer,
                                        "dimstyle": _ds_nombre})
                        if e.text_override:
                            dim_ent.dimension.dxf.text = e.text_override
                        dim_ent.render()
                        _native_ok = True

                    elif e.dim_type == "D":
                        cx, cy = x1, y1
                        r = _math.hypot(x2-cx, y2-cy)
                        dim_ent = msp.add_diameter_dim(
                            center=(cx, cy, 0), radius=r,
                            angle=_math.degrees(_math.atan2(y2-cy, x2-cx)),
                            dxfattribs={"layer": e.layer,
                                        "dimstyle": _ds_nombre})
                        if e.text_override:
                            dim_ent.dimension.dxf.text = e.text_override
                        dim_ent.render()
                        _native_ok = True

                    elif e.dim_type == "ANG":
                        cx_a, cy_a = x1, y1
                        dim_ent = msp.add_angular_dim_2l(
                            center=(cx_a, cy_a, 0),
                            p1=(x2, y2, 0), p2=(px, py, 0),
                            distance=_math.hypot(x2-cx_a, y2-cy_a) * 1.2,
                            dxfattribs={"layer": e.layer,
                                        "dimstyle": _ds_nombre})
                        if e.text_override:
                            dim_ent.dimension.dxf.text = e.text_override
                        dim_ent.render()
                        _native_ok = True

                    elif e.dim_type == "ORD":
                        # Cota ordenada nativa — ezdxf distingue X e Y
                        _dx_o = abs(x2 - x1);  _dy_o = abs(y2 - y1)
                        _is_x = _dy_o >= _dx_o   # líder vertical → mide X
                        _ord_fn = (msp.add_ordinate_x_dim if _is_x
                                   else msp.add_ordinate_y_dim)
                        dim_ent = _ord_fn(
                            feature_location=(x1, y1, 0),
                            leader_endpoint=(x2, y2, 0),
                            dxfattribs={"layer": e.layer,
                                        "dimstyle": _ds_nombre})
                        if e.text_override:
                            dim_ent.dimension.dxf.text = e.text_override
                        dim_ent.render()
                        _native_ok = True

                except Exception:
                    _native_ok = False

                # ── Fallback geométrico si la entidad nativa falló ───────
                # Usa _fb_txth del dimstyle y _dim_text_export (con scale/prec/suffix)
                if not _native_ok:
                    _fb_txt = _dim_text_export(e, _ds_cfg)
                    if e.dim_type in ("H", "V", "A"):
                        if e.dim_type == "H":
                            d1x, d1y = x1, py;  d2x, d2y = x2, py
                        elif e.dim_type == "V":
                            d1x, d1y = px, y1;  d2x, d2y = px, y2
                        else:
                            _rot = getattr(e, 'rot_angle', None)
                            if _rot is not None:
                                _arad = _math.radians(_rot)
                                _ux, _uy = _math.cos(_arad), _math.sin(_arad)
                                _nx, _ny = -_uy, _ux
                                _dn = px*_nx + py*_ny
                                _p1n = x1*_nx + y1*_ny
                                d1x, d1y = x1+_nx*(_dn-_p1n), y1+_ny*(_dn-_p1n)
                                _p2n = x2*_nx + y2*_ny
                                d2x, d2y = x2+_nx*(_dn-_p2n), y2+_ny*(_dn-_p2n)
                            else:
                                dlen = _math.hypot(x2-x1, y2-y1)
                                if dlen < 1e-9:
                                    continue
                                ux, uy = (x2-x1)/dlen, (y2-y1)/dlen
                                nx, ny = -uy, ux
                                off = (px-x1)*nx + (py-y1)*ny
                                d1x, d1y = x1+nx*off, y1+ny*off
                                d2x, d2y = x2+nx*off, y2+ny*off
                        msp.add_line((x1, y1, 0), (d1x, d1y, 0), dxfattribs=attribs)
                        msp.add_line((x2, y2, 0), (d2x, d2y, 0), dxfattribs=attribs)
                        msp.add_line((d1x, d1y, 0), (d2x, d2y, 0), dxfattribs=attribs)
                        tmx = tp[0] if tp else (d1x+d2x)/2
                        tmy = tp[1] if tp else (d1y+d2y)/2
                        ang = _math.degrees(_math.atan2(d2y-d1y, d2x-d1x))
                        msp.add_text(_fb_txt, dxfattribs={**attribs,
                            "height": _fb_txth, "style": _active_txstyle,
                            "insert": (tmx, tmy, 0), "rotation": ang})

                    elif e.dim_type in ("R", "D"):
                        msp.add_line((x1, y1, 0), (x2, y2, 0), dxfattribs=attribs)
                        _ins_rd = (tp[0], tp[1], 0) if tp else (x2, y2, 0)
                        msp.add_text(_fb_txt, dxfattribs={**attribs,
                            "height": _fb_txth, "style": _active_txstyle,
                            "insert": _ins_rd})

                    elif e.dim_type == "ANG":
                        msp.add_line((x1, y1, 0), (x2, y2, 0), dxfattribs=attribs)
                        msp.add_line((x1, y1, 0), (px, py, 0), dxfattribs=attribs)
                        _ins_ang = (tp[0], tp[1], 0) if tp else \
                                   ((x1+x2+px)/3, (y1+y2+py)/3, 0)
                        msp.add_text(_fb_txt, dxfattribs={**attribs,
                            "height": _fb_txth, "style": _active_txstyle,
                            "insert": _ins_ang})

                    elif e.dim_type == "ARC":
                        cx_a, cy_a = px, py
                        r_a = _math.hypot(x1-cx_a, y1-cy_a)
                        if r_a > 1e-9:
                            a1_d = _math.degrees(_math.atan2(y1-cy_a, x1-cx_a))
                            a2_d = _math.degrees(_math.atan2(y2-cy_a, x2-cx_a))
                            _mid_ang = (_math.atan2(y1-cy_a, x1-cx_a) +
                                        _math.atan2(y2-cy_a, x2-cx_a)) / 2
                            r_dim = r_a * 1.10
                            msp.add_arc(center=(cx_a, cy_a, 0), radius=r_dim,
                                        start_angle=a1_d, end_angle=a2_d,
                                        dxfattribs=attribs)
                            _ef = r_dim * 1.05 / r_a
                            msp.add_line((x1, y1, 0),
                                (cx_a+(x1-cx_a)*_ef, cy_a+(y1-cy_a)*_ef, 0),
                                dxfattribs=attribs)
                            msp.add_line((x2, y2, 0),
                                (cx_a+(x2-cx_a)*_ef, cy_a+(y2-cy_a)*_ef, 0),
                                dxfattribs=attribs)
                            msp.add_text(f"⌒{_fb_txt}", dxfattribs={**attribs,
                                "height": _fb_txth, "style": _active_txstyle,
                                "insert": (tp[0], tp[1], 0) if tp else
                                          (cx_a + r_dim*1.15*_math.cos(_mid_ang),
                                           cy_a + r_dim*1.15*_math.sin(_mid_ang), 0)})

                    elif e.dim_type == "ORD":
                        msp.add_line((x1, y1, 0), (x2, y2, 0), dxfattribs=attribs)
                        _ins_ord = (tp[0], tp[1], 0) if tp else (x2, y2, 0)
                        msp.add_text(_fb_txt, dxfattribs={**attribs,
                            "height": _fb_txth, "style": _active_txstyle,
                            "insert": _ins_ord})
            elif isinstance(e, Ellipse):
                import math as _m2
                # Try DXF ELLIPSE entity first, fallback to polyline approximation
                try:
                    ratio = e.ry / e.rx if e.rx > 1e-10 else 1.0
                    major_vec = (e.rx * _m2.cos(_m2.radians(e.angle)),
                                 e.rx * _m2.sin(_m2.radians(e.angle)), 0)
                    msp.add_ellipse(
                        center=(e.cx, e.cy, 0),
                        major_axis=major_vec,
                        ratio=ratio,
                        dxfattribs=attribs,
                    )
                except Exception:
                    # Fallback: polyline approximation
                    bpts = e._pts_on_boundary(72)
                    if len(bpts) >= 2:
                        pl = msp.add_lwpolyline(
                            [(x, y) for x, y in bpts],
                            dxfattribs=attribs,
                        )
                        pl.closed = True

            elif isinstance(e, XLine):
                try:
                    import math as _m3
                    dx = e.x2 - e.x1
                    dy = e.y2 - e.y1
                    d = _m3.hypot(dx, dy)
                    if d > 1e-10:
                        ux, uy = dx / d, dy / d
                        msp.add_xline(
                            start=(e.x1, e.y1, 0),
                            unit_vector=(ux, uy, 0),
                            dxfattribs=attribs,
                        )
                except Exception:
                    # Fallback: segmento muy largo normalizado
                    # Se normaliza el vector para que *1_000_000 siempre alcance
                    # el borde del viewport sin importar la distancia entre los puntos
                    try:
                        import math as _m3b
                        _dx = e.x2 - e.x1;  _dy = e.y2 - e.y1
                        _d  = _m3b.hypot(_dx, _dy)
                        if _d > 1e-10:
                            _ux, _uy = _dx / _d, _dy / _d
                            _BIG = 1_000_000   # 1 Mm — cubre cualquier plano real
                            msp.add_line(
                                (e.x1 - _ux * _BIG, e.y1 - _uy * _BIG, 0),
                                (e.x1 + _ux * _BIG, e.y1 + _uy * _BIG, 0),
                                dxfattribs=attribs,
                            )
                    except Exception:
                        pass

            elif isinstance(e, Leader):
                if len(e.points) >= 2:
                    try:
                        leader_ent = msp.add_leader(
                            vertices=[(x, y, 0) for x, y in e.points],
                            dxfattribs=attribs,
                        )
                    except Exception:
                        # Fallback: polyline + text
                        pts2d = [(x, y) for x, y in e.points]
                        msp.add_lwpolyline(pts2d, dxfattribs=attribs)
                        if e.text:
                            lx, ly = e.points[-1]
                            # Usar text_height del leader si existe, si no 0.20
                            _ld_h = getattr(e, "text_height",
                                            getattr(e, "height", 0.20)) or 0.20
                            msp.add_text(e.text, dxfattribs={
                                **attribs,
                                "height": _ld_h,
                                "insert": (lx + _ld_h * 0.25, ly, 0),
                                "style":  _active_txstyle,
                            })

            elif isinstance(e, Insert):
                # Bloque insertado — referencia a BlockDef con transform
                if e.block_name:
                    try:
                        blk_ref = msp.add_blockref(
                            e.block_name,
                            insert=(e.x, e.y, 0),
                            dxfattribs={
                                **attribs,
                                "xscale":   e.scale_x,
                                "yscale":   e.scale_y,
                                "zscale":   1.0,
                                "rotation": e.angle,
                            }
                        )
                        # TAREA 2 — exportar atributos (ejes EJE A/B/C/1/2/3...)
                        for att in getattr(e, "attribs", []) or []:
                            try:
                                blk_ref.add_attrib(
                                    tag=att.tag,
                                    text=att.value,
                                    insert=(att.x, att.y, 0),
                                    dxfattribs={
                                        "layer":    att.layer,
                                        "height":   att.height,
                                        "rotation": att.angle,
                                    }
                                )
                            except Exception:
                                pass
                    except Exception as exc:
                        # Fallback: si el bloque no existe en el doc, marcar con cruz
                        msp.add_line((e.x-0.1, e.y, 0), (e.x+0.1, e.y, 0),
                                     dxfattribs=attribs)
                        msp.add_line((e.x, e.y-0.1, 0), (e.x, e.y+0.1, 0),
                                     dxfattribs=attribs)
                        print(f"[WARN] dxf_export: INSERT '{e.block_name}' sin def: {exc}")

            elif isinstance(e, ImageRef):
                # Imagen raster → WIPEOUT/IMAGE en DXF (si ezdxf lo soporta)
                # Fallback: rectángulo que representa el marco de la imagen
                try:
                    import math as _math
                    corners = e._corners()  # lista de 4 puntos con rotación
                    pts = [(x, y) for x, y in corners]
                    pts.append(pts[0])  # cerrar
                    pl = msp.add_lwpolyline(pts, dxfattribs={**attribs,
                                            "linetype": "DASHED"
                                            if _LT_DXF.get("DASHED") else "Continuous"})
                    pl.closed = True
                    # Texto con nombre del archivo en el centro
                    import os as _os
                    cx_ = sum(p[0] for p in corners) / 4
                    cy_ = sum(p[1] for p in corners) / 4
                    msp.add_text(_os.path.basename(e.path) or "IMG",
                                 dxfattribs={**attribs,
                                 "height": min(e.width, e.height) * 0.10,
                                 "insert": (cx_, cy_, 0),
                                 "style":  _active_txstyle})
                except Exception:
                    pass

            elif isinstance(e, Hatch):
                if len(e.boundary) >= 3:
                    # ── Color de la entidad ───────────────────────────────────
                    _hcol = getattr(e, "color", "bylayer") or "bylayer"
                    _haci = (256 if _hcol.lower() in ("bylayer","byblock","")
                             else _hex_to_aci_full(_hcol))
                    hatch_ent = msp.add_hatch(color=_haci, dxfattribs=attribs)
                    if _hcol.startswith("#"):
                        try:
                            _hh = _hcol.lstrip("#")
                            _hr,_hg,_hb = int(_hh[0:2],16),int(_hh[2:4],16),int(_hh[4:6],16)
                            hatch_ent.dxf.true_color = (_hr<<16)|(_hg<<8)|_hb
                        except Exception:
                            pass

                    # ── Boundary + Holes ──────────────────────────────────────
                    # flags=1 (EXTERNAL) = contorno exterior.
                    hatch_ent.paths.add_polyline_path(
                        [(x, y) for x, y in e.boundary], is_closed=True, flags=1)
                    # flags=0 (no EXTERNAL) = contorno interior / hueco.
                    # e.holes tiene default_factory=list → siempre iterable.
                    for _hole_pts in e.holes:
                        if len(_hole_pts) >= 3:
                            hatch_ent.paths.add_polyline_path(
                                [(x, y) for x, y in _hole_pts],
                                is_closed=True, flags=0)

                    # ── Patrón/relleno ────────────────────────────────────────
                    _is_grad = getattr(e, "is_gradient", False)

                    if _is_grad:
                        # FIX #2a: Gradiente → exportar con color primario del gradiente
                        # ezdxf soporta set_gradient() pero requiere parámetros complejos.
                        # Usamos set_solid_fill con el color real del gradiente.
                        _gc = getattr(e, "gradient_color", None) or _hcol or "#FFFFFF"
                        _gaci = (_haci if _gc in ("bylayer","byblock","")
                                 else _hex_to_aci_full(_gc))
                        try:
                            hatch_ent.set_solid_fill(color=_gaci)
                            # Intentar gradiente nativo si ezdxf lo soporta
                            if _gc.startswith("#"):
                                _gh = _gc.lstrip("#")
                                _gr = int(_gh[0:2],16)
                                _gg = int(_gh[2:4],16)
                                _gb = int(_gh[4:6],16)
                                try:
                                    hatch_ent.set_gradient(
                                        color1=(_gr, _gg, _gb),
                                        color2=(255, 255, 255),
                                        rotation=0.0,
                                        centered=1.0,
                                        name="LINEAR")
                                except Exception:
                                    pass  # gradiente nativo no disponible → sólido con color
                        except Exception:
                            hatch_ent.set_solid_fill()

                    elif e.pattern == "SOLID":
                        # FIX #2b: Sólido normal — usar color correcto
                        try:
                            hatch_ent.set_solid_fill(color=_haci)
                        except Exception:
                            pass

                    else:
                        # FIX #1: Pasar nombre de patrón directamente.
                        # Solo mapear aliases internos del visor → nombre DXF correcto.
                        # Todo lo demás (AR-CONC, EARTH, BRICK, GRASS…) pasa sin cambio.
                        _VISOR_TO_DXF = {
                            "LINES": "LINE",    # alias interno → nombre en acad.pat
                            "CROSS": "NET",     # alias interno → nombre en acad.pat
                        }
                        _pat_dxf = _VISOR_TO_DXF.get(e.pattern, e.pattern)
                        # Usar dxf_scale (escala original del DXF, sin conversión de unidades).
                        # e.scale tiene el espaciado en unidades mundo (útil para renderer
                        # pero incorrecto aquí — el DXF pattern_scale es un multiplicador sin unidades).
                        _exp_scale = (e.dxf_scale if getattr(e, "dxf_scale", 0.0) > 0.0
                                      else e.scale)
                        try:
                            hatch_ent.set_pattern_fill(
                                _pat_dxf, scale=_exp_scale, angle=e.angle)
                        except Exception:
                            # TAREA 1 — fallback inteligente para patrones custom
                            _fb = _CUSTOM_HATCH_FALLBACK.get(_pat_dxf.upper())
                            if _fb:
                                try:
                                    hatch_ent.set_pattern_fill(
                                        _fb[0], scale=_fb[1], angle=_fb[2])
                                except Exception:
                                    hatch_ent.set_solid_fill()
                            else:
                                try:
                                    hatch_ent.set_pattern_fill(
                                        "ANSI31", scale=e.scale, angle=e.angle)
                                except Exception:
                                    hatch_ent.set_solid_fill()

        except Exception as exc:
            errores += 1
            if errores <= 5:   # evitar flood en consola
                print(f"[WARN] dxf_export: entidad {type(e).__name__} ignorada: {exc}")

    if errores > 0:
        print(f"[WARN] dxf_export: {errores} entidades no exportadas.")

    doc.saveas(ruta)


# ── TAREA 3 — Extraer DimStyles de un doc ezdxf y guardar en settings.json ──

def actualizar_dimstyles_desde_doc(ezdxf_doc, settings_path: str | None = None) -> dict:
    """
    Lee los DIMSTYLE de un documento ezdxf ya abierto y los guarda en
    config/settings.json sección 'dimstyles'. Retorna el dict de estilos.

    Llamar desde dxf_import.py después de leer el doc, solo si la sección
    'dimstyles' está vacía en settings.json (para no sobreescribir ajustes manuales).
    """
    if settings_path is None:
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        settings_path = os.path.join(raiz, "config", "settings.json")

    dimstyles: dict = {}
    try:
        for ds in ezdxf_doc.dimstyles:
            nombre = ds.dxf.get("name", "Standard")
            # Traducir nombres DXF raw → nombres semánticos que usa _crear_dimstyles_en_doc
            raw_post = ds.dxf.get("dimpost", "") or ""
            suffix = raw_post.replace("<>", "").strip()   # "m" o "" si no hay sufijo
            dimstyles[nombre] = {
                "text_height":   ds.dxf.get("dimtxt",   0.25),
                "text_offset":   ds.dxf.get("dimgap",   0.05),
                "arrow_size":    ds.dxf.get("dimasz",   0.18),
                "ext_offset":    ds.dxf.get("dimexo",   0.0625),
                "ext_overshoot": ds.dxf.get("dimexe",   0.18),
                "precision":     ds.dxf.get("dimdec",   2),
                "scale_factor":  ds.dxf.get("dimscale", 1.0),
                "suffix":        suffix,
            }
    except Exception as exc:
        print(f"[WARN] dxf_export: no se pudieron leer dimstyles: {exc}")
        return {}

    if not dimstyles:
        return {}

    try:
        cfg: dict = {}
        if os.path.exists(settings_path):
            with open(settings_path, encoding="utf-8") as f:
                cfg = json.load(f)
        # Solo actualizar si la sección no tiene estilos (no sobreescribir manual)
        existing = cfg.get("dimstyles", {}).get("styles", {})
        if not existing:
            cfg.setdefault("dimstyles", {})["styles"] = dimstyles
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            print(f"[INFO] dxf_export: {len(dimstyles)} dimstyles guardados en settings.json")
    except Exception as exc:
        print(f"[WARN] dxf_export: no se pudo guardar dimstyles: {exc}")

    return dimstyles
