"""
cad/dxf_import.py
=================
Importa entidades DXF al visor CAD usando ezdxf.

Formatos soportados:
    • DXF (R12 … 2018) — lectura directa con ezdxf

    ⚠ DWG NO está soportado directamente por ezdxf.
      Para importar DWG primero convierta a DXF usando dwg_converter.py:

        from cad.dwg_converter import dwg_a_dxf
        ruta_dxf = dwg_a_dxf("plano.dwg")
        resultado = importar_dxf(ruta_dxf)

      El método _importar_dxf() de engine.py ya hace esto automáticamente.

Entidades importadas:
    LINE, LWPOLYLINE, POLYLINE (2-D), CIRCLE, ARC, TEXT, MTEXT
    Bloques (INSERT) → descompuestos con virtual_entities() (maneja rotación+escala)

Retorna:
    ImportResult(entities, layers, warnings, stats)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from cad.entities import Layer, Line, Polyline, Circle, Arc, Text, Hatch, BlockDef, Insert, Dimension, XLine, AttDef, Attrib


# ── Límites de seguridad ─────────────────────────────────────────────────────
#
# MAX_ENTITIES: sin límite práctico — el renderizado usa viewport culling
# (solo se dibujan las entidades visibles en pantalla, no todas).
# El cuello de botella real es la RAM, no el renderizador.
# Estimado: ~500 bytes/entidad → 1 M entidades ≈ 500 MB RAM.
# Para archivos de planta/sección típicos (10k–200k entidades) no hay problema.

MAX_ENTITIES  = 2_000_000   # límite de seguridad extremo (RAM ~1 GB)
MAX_INS_DEPTH = 8            # máx nivel de anidamiento de INSERT

# Si un bloque tiene más de N entidades primitivas es "complejo" y sus
# instancias se mantienen como Insert (no se explotan).  Si tiene pocas
# entidades puede convenir expandir (ej. bloques de símbolo simple).
# Pon en 0 para NUNCA explotar (siempre instancing).
BLOCK_EXPAND_THRESHOLD = 0   # 0 = instancing real siempre

# Tipos de entidades DXF que NO son geometría y se deben ignorar siempre
# ATTRIB se maneja aparte (se convierte a Text)
_SKIP_TYPES = frozenset({
    # Fix #3: XLINE movido a handler propio (se importa como XLine interna)
    # Fix #7: ATTDEF movido a handler propio (se importa como Text)
    "SEQEND", "VIEWPORT",
    "ACAD_PROXY_ENTITY", "UNKNOWN", "BODY", "REGION", "3DSOLID",
    "RAY", "OLE2FRAME", "IMAGE", "UNDERLAY",
    "MESH", "SURFACE", "PLANESURFACE", "EXTRUDEDSURFACE",
    "LOFTEDSURFACE", "REVOLVEDSURFACE", "SWEPTSURFACE",
})


# ── Paleta ACI → hex (colores estándar AutoCAD) ─────────────────────────────
_ACI_TO_HEX: dict[int, str] = {
    0:  "#FFFFFF",   # BYBLOCK
    1:  "#FF0000",   # rojo
    2:  "#FFFF00",   # amarillo
    3:  "#00FF00",   # verde
    4:  "#00FFFF",   # cian
    5:  "#0000FF",   # azul
    6:  "#FF00FF",   # magenta
    7:  "#FFFFFF",   # blanco/negro
    8:  "#808080",   # gris oscuro
    9:  "#C0C0C0",   # gris claro
    30: "#FF8000",   # naranja
    40: "#FFBF00",
    50: "#BFFF00",
    60: "#80FF00",
    70: "#00FF40",
    80: "#00FFBF",
    90: "#00BFFF",
    100:"#0080FF",
    110:"#4000FF",
    120:"#8000FF",
    130:"#BF00FF",
    140:"#FF00BF",
    150:"#FF0080",
    200:"#8B5CF6",   # violeta
    250:"#888888",
    256:"#FFFFFF",   # BYLAYER → blanco por defecto
}

def _aci_hex(aci: int) -> str:
    """Convierte AutoCAD Color Index (0-256) a hex RGB.
    Usa la tabla completa de 256 colores de ezdxf como fuente primaria."""
    try:
        import ezdxf.colors as _ec
        rgb = _ec.aci2rgb(aci)
        return f"#{rgb.r:02X}{rgb.g:02X}{rgb.b:02X}"
    except Exception:
        return _ACI_TO_HEX.get(aci, "#FFFFFF")

def _true_color_hex(rgb: tuple) -> str:
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    return f"#{r:02X}{g:02X}{b:02X}"

def _entity_color(e) -> str:
    """Lee el color explícito de una entidad DXF.
    Devuelve color hex si la entidad tiene color propio (no BYLAYER/BYBLOCK),
    o "bylayer" si debe heredar de la capa."""
    # True color (24-bit) tiene prioridad sobre ACI
    try:
        tc = e.dxf.true_color
        if tc is not None:
            r = (tc >> 16) & 0xFF
            g = (tc >>  8) & 0xFF
            b =  tc        & 0xFF
            return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        pass
    try:
        aci = int(e.dxf.color)
        # 256 = BYLAYER, 0 = BYBLOCK (hereda del bloque padre → tratamos como bylayer)
        if aci not in (0, 256):
            return _aci_hex(aci)
    except Exception:
        pass
    return "bylayer"

def _fix_autocad_codes(s: str) -> str:
    """Convierte códigos especiales de AutoCAD a caracteres Unicode."""
    s = re.sub(r'%%[dD]', '°', s)
    s = re.sub(r'%%[cC]', 'Ø', s)
    s = re.sub(r'%%[pP]', '±', s)
    # %%% → % (tres signos de porcentaje = un literal %)
    s = s.replace('%%%', '%')
    # %%o (overline) y %%u (underline) → eliminar código, conservar texto
    s = re.sub(r'%%[oOuU]', '', s)
    return s


def _text_height(h_raw: float, escala: float) -> float:
    """Calcula la altura de texto en unidades mundo.

    Cuando escala < 0.01 (conversión mm→m) los textos de AutoCAD diseñados
    para paper space quedan sub-milimétricos (0.0002m) e invisibles en el
    visor. En ese caso se preserva h_raw en las unidades de dibujo originales
    (que en el visor actuarán como metros) para mantener la visibilidad.

    Ejemplos para escala=0.001 (mm→m):
      h_raw=0.20mm → h=0.0002m (invisible) → usar 0.20 como "metros" ✓
      h_raw=125mm  → h=0.125m  (visible)   → usar 0.125m ✓
    """
    h = abs(h_raw * escala)
    if escala < 0.01 and h < 0.005:
        # Restaurar a unidades originales para que sea visible en el visor
        h = abs(h_raw)
    return max(h, 0.005)   # mínimo 5mm world-space


def _strip_mtext(raw: str) -> str:
    """Elimina los códigos de formato de MTEXT y devuelve texto limpio.

    Cubre los códigos más frecuentes de AutoCAD MText:
      \\P → salto de párrafo
      \\pxql; \\pi; \\pd; → párrafo con sangría/alineación
      \\A0; \\A1; \\A2; → alineación vertical
      \\H...; \\W...; \\Q...; \\T...; → altura/ancho/oblicuo/tracking
      \\C...; \\c...; → color
      \\f...; \\F...; → fuente
      \\S...^...; → fracción/exponente
      { }  → grupos de formato
    """
    s = raw
    # Fracción  \S num^denom;  (antes de quitar otros códigos)
    s = re.sub(r'\\S([^;^]*)[\^/]([^;]*);', r'\1/\2', s)
    # Codes con punto y coma: \Xarg;  (letra + cualquier argumento + ;)
    # Esto cubre \pxql; \H0.1x; \A1; \C5; \f...;  etc.
    s = re.sub(r'\\[A-Za-z][^;]*;', '', s)
    # Salto de párrafo \P (mayúscula = párrafo nuevo)
    s = s.replace('\\P', '\n')
    # Espacio duro y otros escapes simples
    s = s.replace('\\~', ' ').replace('\\n', '\n')
    # Códigos sin argumento que quedaron: \X
    s = re.sub(r'\\[A-Za-z]', '', s)
    # Llaves de agrupación de formato
    s = re.sub(r'[{}]', '', s)
    # Limpiar líneas vacías múltiples
    s = re.sub(r'\n{3,}', '\n\n', s)
    s = _fix_autocad_codes(s)
    return s.strip()


# ── Resultado ────────────────────────────────────────────────────────────────

@dataclass
class ImportResult:
    entities:   list      = field(default_factory=list)
    layers:     dict      = field(default_factory=dict)
    block_defs: dict      = field(default_factory=dict)   # name → BlockDef
    warnings:   list[str] = field(default_factory=list)
    stats:      dict      = field(default_factory=dict)


# ── Mapa de tipos de línea DXF → interno ─────────────────────────────────────
#
# AutoCAD incluye cientos de variantes de nombres de linetype.
# Los mapeamos a los 5 tipos que soporta el visor.
# La comparación se hace en MAYÚSCULAS sin espacios.

_LT_MAP: dict[str, str] = {}

def _build_lt_map():
    """Construye _LT_MAP con todos los alias conocidos de AutoCAD.

    Estrategia: los 43 tipos internos se mapean a sí mismos (mapeo exacto).
    Los nombres genéricos/aliases de AutoCAD van al tipo más cercano.
    """
    # ── Mapeos exactos (nombre DXF = nombre interno) ───────────────────────
    _exact = [
        "CONTINUOUS",
        "DASHED",    "DASHED2",    "DASHEDX2",
        "HIDDEN",    "HIDDEN2",    "HIDDENX2",
        "DOTTED",    "DOTTED2",    "DOTTEDX2",
        "CENTER",    "CENTER2",    "CENTERX2",
        "DASHDOT",   "DASHDOT2",   "DASHDOTX2",
        "DIVIDE",    "DIVIDE2",    "DIVIDEX2",
        "PHANTOM",   "PHANTOM2",   "PHANTOMX2",
        "BORDER",    "BORDER2",    "BORDERX2",
        "ACAD_ISO02W100", "ACAD_ISO03W100", "ACAD_ISO04W100",
        "ACAD_ISO05W100", "ACAD_ISO07W100", "ACAD_ISO08W100",
        "ACAD_ISO09W100", "ACAD_ISO10W100", "ACAD_ISO11W100",
        "ACAD_ISO12W100", "ACAD_ISO13W100", "ACAD_ISO14W100",
        "ACAD_ISO15W100",
    ]
    for n in _exact:
        _LT_MAP[n.upper()] = n

    # ── Aliases genéricos → tipo más cercano ───────────────────────────────
    continuous = ["BYLAYER", "BYBLOCK", "SOLID"]
    dashed     = ["DASH", "DASHED_", "SHORT_DASH", "LONG_DASH", "MEDIUM_DASH",
                  "ISO02W100", "ISO03W100", "ISODASH"]
    hidden2    = ["HIDDEN_"]
    dotted     = ["DOT", "DOT2", "DOTX2", "DOTS", "DOTTED_",
                  "ISO07W100"]
    center_map = ["CHAIN", "CHAIN2", "CHAINX2", "CENTER_",
                  "ISO04W100", "ISO05W100",
                  "CENTERLINE", "CHAIN_LINE"]
    dashdot    = ["FENCELINE1", "FENCELINE2", "TRACKS", "BATTING", "ZIGZAG",
                  "ISO10W100", "ISO12W100"]
    phantom_m  = ["ISO08W100", "ISO09W100"]
    divide_m   = ["ISO11W100", "ISO13W100", "ISO14W100", "ISO15W100"]

    for n in continuous: _LT_MAP[n.upper()] = "CONTINUOUS"
    for n in dashed:     _LT_MAP[n.upper()] = "DASHED"
    for n in hidden2:    _LT_MAP[n.upper()] = "HIDDEN"
    for n in dotted:     _LT_MAP[n.upper()] = "DOTTED"
    for n in center_map: _LT_MAP[n.upper()] = "CENTER"
    for n in dashdot:    _LT_MAP[n.upper()] = "DASHDOT"
    for n in phantom_m:  _LT_MAP[n.upper()] = "PHANTOM"
    for n in divide_m:   _LT_MAP[n.upper()] = "DIVIDE"

_build_lt_map()

def _dxf_linetype_to_internal(dxf_lt: str) -> str:
    """Convierte un nombre de linetype DXF al tipo interno del visor."""
    key = (dxf_lt or "").strip().upper().replace(" ", "_").replace("-", "_")
    # Búsqueda directa
    if key in _LT_MAP:
        return _LT_MAP[key]
    # Búsqueda por prefijo (ej. "DASHED_WIDE" → "DASHED")
    for prefix, internal in (
        ("HIDDEN",   "DASHED"),
        ("DASH",     "DASHED"),
        ("DASHED",   "DASHED"),
        ("DOT",      "DOTTED"),
        ("CENTER",   "CENTER"),
        ("CHAIN",    "CENTER"),
        ("PHANTOM",  "DASHDOT"),
        ("DASHDOT",  "DASHDOT"),
        ("DIVIDE",   "DASHED"),
        ("ISO",      "DASHED"),
        ("ACAD_ISO", "DASHED"),
    ):
        if key.startswith(prefix):
            return internal
    return "CONTINUOUS"


# ── Procesador de geometría primitiva ────────────────────────────────────────
#
# Convierte una entidad DXF primitiva (ya en espacio destino — puede ser
# espacio modelo o espacio de bloque) a la entidad interna del visor.
# Devuelve Entity | list[Entity] | None.
# Para HATCH con múltiples regiones outer devuelve lista; el resto devuelve Entity o None.

def _append_prim(e, escala: float, target: list) -> int:
    """Convierte e, añade resultado(s) a target. Retorna nº de entidades añadidas."""
    ent = _primitiva_dxf(e, escala)
    if ent is None:
        return 0
    if isinstance(ent, list):
        target.extend(ent)
        return len(ent)
    target.append(ent)
    return 1


def _primitiva_dxf(e, escala: float):
    """Convierte una entidad DXF primitiva al tipo interno. Retorna Entity | list[Entity] | None."""
    t = e.dxftype()
    if t in _SKIP_TYPES:
        return None
    lyr = e.dxf.layer if e.dxf.hasattr("layer") else "0"
    col = _entity_color(e)   # "bylayer" o hex si tiene color explícito

    if t == "LINE":
        x1, y1 = e.dxf.start.x * escala, e.dxf.start.y * escala
        x2, y2 = e.dxf.end.x   * escala, e.dxf.end.y   * escala
        if math.hypot(x2-x1, y2-y1) < 1e-9:
            return None
        return Line(x1=x1, y1=y1, x2=x2, y2=y2, layer=lyr, color=col)

    if t == "LWPOLYLINE":
        try:
            pts = [(p[0]*escala, p[1]*escala) for p in e.get_points()]
        except Exception:
            return None
        if len(pts) < 2:
            return None
        return Polyline(points=pts, closed=e.closed, layer=lyr, color=col)

    if t == "POLYLINE":
        try:
            pts = [(v.dxf.location.x*escala, v.dxf.location.y*escala)
                   for v in e.vertices]
            if len(pts) < 2:
                return None
            return Polyline(points=pts, closed=bool(e.dxf.flags & 1),
                            layer=lyr, color=col)
        except Exception:
            return None

    if t == "CIRCLE":
        r = abs(e.dxf.radius * escala)
        if r < 1e-9:
            return None
        return Circle(cx=e.dxf.center.x*escala, cy=e.dxf.center.y*escala,
                      radius=r, layer=lyr, color=col)

    if t == "ARC":
        r = abs(e.dxf.radius * escala)
        if r < 1e-9:
            return None
        ext_z = e.dxf.get("extrusion", (0.0, 0.0, 1.0))[2]
        return Arc(cx=e.dxf.center.x*escala, cy=e.dxf.center.y*escala,
                   radius=r, start_ang=e.dxf.start_angle,
                   end_ang=e.dxf.end_angle, ccw=(ext_z >= 0),
                   layer=lyr, color=col)

    if t == "TEXT":
        txt = e.dxf.get("text", "").strip()
        if not txt:
            return None
        h = _text_height(e.dxf.get("height", 0.20), escala)
        halign = int(e.dxf.get("halign", 0))
        halign = halign if halign in (0, 1, 2) else (1 if halign in (3, 4, 5) else 0)
        return Text(x=e.dxf.insert.x*escala, y=e.dxf.insert.y*escala,
                    content=_fix_autocad_codes(txt).upper(),
                    height=h,
                    angle=e.dxf.get("rotation", 0.0),
                    halign=halign,
                    layer=lyr, color=col)

    if t == "MTEXT":
        try:
            # API según versión de ezdxf:
            # ezdxf >= 1.x → plain_text()
            # ezdxf 0.x   → plain_mtext()
            # Fallback     → e.text (texto con códigos, limpiado por _strip_mtext)
            raw = ""
            for method in ("plain_text", "plain_mtext"):
                fn = getattr(e, method, None)
                if callable(fn):
                    try:
                        raw = fn()
                        break
                    except Exception:
                        pass
            if not raw:
                raw = getattr(e, "text", "") or ""
            txt = _strip_mtext(raw).strip()
        except Exception:
            return None
        if not txt:
            return None
        h = _text_height(e.dxf.get("char_height", 0.20), escala)
        # MText: el ángulo puede estar como "rotation" (escalar) O como
        # "text_direction" (vector XY). QLEADER y rutinas LISP usan el vector.
        # Cuando solo existe text_direction, "rotation" devuelve 0 por defecto.
        ang = 0.0
        try:
            if e.dxf.hasattr("rotation"):
                ang = float(e.dxf.rotation)
            elif e.dxf.hasattr("text_direction"):
                _td = e.dxf.text_direction
                ang = math.degrees(math.atan2(float(_td.y), float(_td.x)))
        except Exception:
            pass
        # attachment_point 1-9: row/col → halign + valign
        # 1=TL 2=TC 3=TR  4=ML 5=MC 6=MR  7=BL 8=BC 9=BR
        _ap    = int(e.dxf.get("attachment_point", 1))
        halign = {1:0,4:0,7:0, 2:1,5:1,8:1, 3:2,6:2,9:2}.get(_ap, 0)
        valign = {1:3,2:3,3:3, 4:2,5:2,6:2, 7:0,8:0,9:0}.get(_ap, 0)
        # Ancho real del cuadro MTEXT (group 41). 0 = sin restricción de ancho.
        mtext_w = float(e.dxf.get("width", 0.0)) * escala
        # Preservar el string raw con códigos de formato para round-trip sin pérdidas.
        # Solo se guarda si tiene códigos reales (negrita, itálica, tamaños mixtos…).
        _raw_mtext = ""
        try:
            _raw_check = getattr(e, "text", "") or ""
            if _raw_check and any(c in _raw_check
                                  for c in ('{', '\\f', '\\b', '\\i',
                                            '\\H', '\\C', '\\W', '\\A')):
                _raw_mtext = _raw_check
        except Exception:
            pass
        return Text(x=e.dxf.insert.x*escala, y=e.dxf.insert.y*escala,
                    content=txt.upper(), height=h,
                    angle=ang, halign=halign, valign=valign,
                    mtext_width=mtext_w,
                    layer=lyr, color=col,
                    raw_mtext=_raw_mtext)

    if t == "ATTRIB":
        # Atributo de bloque con valor visible — tratar como TEXT
        try:
            txt = e.dxf.get("text", "").strip()
            if not txt:
                return None
            h = _text_height(e.dxf.get("height", 0.20), escala)
            halign = int(e.dxf.get("halign", 0))
            halign = halign if halign in (0, 1, 2) else (1 if halign in (3, 4, 5) else 0)
            return Text(x=e.dxf.insert.x*escala, y=e.dxf.insert.y*escala,
                        content=_fix_autocad_codes(txt).upper(),
                        height=h,
                        angle=e.dxf.get("rotation", 0.0),
                        halign=halign, layer=lyr)
        except Exception:
            return None

    if t == "HATCH":
        try:
            # ── 1. Detectar SOLID ───────────────────────────────────────────
            # solid_fill flag (group 70) = 1 → relleno sólido
            # pattern_name "SOLID" también lo indica
            solid_fill = False
            raw_name   = ""
            try:
                solid_fill = bool(e.dxf.solid_fill)
            except Exception:
                try:
                    solid_fill = bool(int(e.dxf.get("solid_fill", 0)))
                except Exception:
                    pass
            try:
                raw_name = str(e.dxf.pattern_name).strip().upper()
            except Exception:
                try:
                    raw_name = str(e.dxf.get("pattern_name", "")).strip().upper()
                except Exception:
                    raw_name = ""
            if raw_name in ("SOLID", ""):
                solid_fill = True

            # ── 2. Nombre de patrón ─────────────────────────────────────────
            if solid_fill:
                pattern = "SOLID"
            else:
                # Solo renombrar alias puros; mantener nombre real para todo lo demás
                _PAT_ALIAS = {"LINE": "LINES", "NET": "CROSS", "NET3": "CROSS"}
                pattern = _PAT_ALIAS.get(raw_name, raw_name)

            # ── 3. Escala y ángulo — leer directamente del objeto DXF ────────
            # ezdxf: HATCH group 41 = pattern_scale, group 52 = pattern_angle
            dxf_scale = 1.0
            angle     = 0.0
            try:
                dxf_scale = float(e.dxf.pattern_scale)
            except Exception:
                try:
                    dxf_scale = float(e.dxf.get("pattern_scale", 1.0))
                except Exception:
                    dxf_scale = 1.0
            try:
                angle = float(e.dxf.pattern_angle)
            except Exception:
                try:
                    angle = float(e.dxf.get("pattern_angle", 0.0))
                except Exception:
                    angle = 0.0

            # Escala en unidades mundo (misma escala que el boundary)
            hatch_scale = max(1e-9, dxf_scale * escala)

            # ── 4. Extraer contorno ──────────────────────────────────────────
            # Función auxiliar: convierte un edge-path o polyline-path a lista pts
            def _path_to_pts(bp) -> list:
                pts_out = []
                # Polyline boundary path — tiene atributo .vertices
                if hasattr(bp, "vertices"):
                    for v in bp.vertices:
                        try:
                            pts_out.append((float(v[0]) * escala, float(v[1]) * escala))
                        except Exception:
                            pass
                # Edge boundary path — tiene atributo .edges
                elif hasattr(bp, "edges"):
                    edges = list(bp.edges)
                    for ei, edge in enumerate(edges):
                        try:
                            if hasattr(edge, "center") and hasattr(edge, "radius"):
                                # ARC edge → tesela
                                cx_a = float(edge.center[0])
                                cy_a = float(edge.center[1])
                                r_a  = float(edge.radius)
                                sa_r = math.radians(float(getattr(edge, "start_angle", 0)))
                                ea_r = math.radians(float(getattr(edge, "end_angle", 360)))
                                if ea_r <= sa_r:
                                    ea_r += 2 * math.pi
                                arc_len = abs(ea_r - sa_r) * max(r_a, 0.001)
                                steps = max(6, min(48, int(arc_len * escala * 40)))
                                for k in range(steps + 1):
                                    a = sa_r + (ea_r - sa_r) * k / steps
                                    pts_out.append(
                                        ((cx_a + r_a * math.cos(a)) * escala,
                                         (cy_a + r_a * math.sin(a)) * escala))
                            elif hasattr(edge, "major_axis"):
                                # ELLIPSE edge → tesela con ecuación paramétrica
                                cx_e = float(edge.center[0])
                                cy_e = float(edge.center[1])
                                mx   = float(edge.major_axis[0])
                                my   = float(edge.major_axis[1])
                                ratio = float(edge.ratio)     # minor/major
                                major_len   = math.hypot(mx, my)
                                major_angle = math.atan2(my, mx)
                                minor_len   = major_len * ratio
                                sp = float(getattr(edge, "start_param",
                                           math.radians(getattr(edge, "start_angle", 0))))
                                ep = float(getattr(edge, "end_param",
                                           math.radians(getattr(edge, "end_angle", 360))))
                                if ep <= sp:
                                    ep += 2 * math.pi
                                steps = max(8, min(64, int(abs(ep - sp) * major_len * escala * 20)))
                                for k in range(steps + 1):
                                    t  = sp + (ep - sp) * k / steps
                                    lx = major_len * math.cos(t)
                                    ly = minor_len  * math.sin(t)
                                    rx = cx_e + lx * math.cos(major_angle) - ly * math.sin(major_angle)
                                    ry = cy_e + lx * math.sin(major_angle) + ly * math.cos(major_angle)
                                    pts_out.append((rx * escala, ry * escala))
                            elif hasattr(edge, "start"):
                                # LINE edge — añadir start; añadir end en el último edge
                                # para cerrar el polígono cuando hay pocos edges
                                pts_out.append((float(edge.start[0]) * escala,
                                                float(edge.start[1]) * escala))
                                if ei == len(edges) - 1 and hasattr(edge, "end"):
                                    pts_out.append((float(edge.end[0]) * escala,
                                                    float(edge.end[1]) * escala))
                            elif hasattr(edge, "control_points"):
                                # SPLINE edge → puntos de control
                                for cp in edge.control_points:
                                    pts_out.append((float(cp[0]) * escala,
                                                    float(cp[1]) * escala))
                        except Exception:
                            pass
                return pts_out

            pts   = []
            holes: list = []

            # ── Estrategia: recoger TODOS los paths y clasificar por área/contención
            # AutoCAD no distingue outer/inner con path_type_flags en este DXF
            # (todos los paths aparecen como EXTERNAL=1 u OUTERMOST=16).
            # Enfoque geométrico: outer = path de mayor área, holes = paths cuyo
            # centroide está dentro del outer.
            _all_cands: list = []
            _accumulated: list = []

            # Iterar e.paths directamente — _path_to_pts maneja PolylinePath
            # (.vertices) y EdgePath (.edges) correctamente.
            # rendering_paths() se descarta: retorna vértices vacíos para
            # EdgePath (arcos/líneas), causando que 366 de 434 holes se pierdan.
            for bp in e.paths:
                try:
                    _bp_pts = _path_to_pts(bp)
                    if len(_bp_pts) >= 3:
                        _all_cands.append(_bp_pts)
                    _accumulated.extend(_bp_pts)
                except Exception:
                    pass

            # Fallback: si ningún path dio >= 3 puntos, usar todos acumulados
            if not _all_cands and len(_accumulated) >= 3:
                _all_cands.append(_accumulated)

            if _all_cands:
                def _area2(_pp: list) -> float:
                    _n = len(_pp)
                    _a = 0.0
                    for _i in range(_n):
                        _j = (_i + 1) % _n
                        _a += _pp[_i][0] * _pp[_j][1] - _pp[_j][0] * _pp[_i][1]
                    return abs(_a)

                def _in_poly(_px, _py, _poly):
                    _inside = False; _n = len(_poly); _j = _n - 1
                    for _i in range(_n):
                        _xi, _yi = _poly[_i]; _xj, _yj = _poly[_j]
                        if ((_yi > _py) != (_yj > _py)) and \
                                _px < (_xj - _xi) * (_py - _yi) / (_yj - _yi) + _xi:
                            _inside = not _inside
                        _j = _i
                    return _inside

                # Ordenar por área descendente — el mayor será el outer principal
                _all_cands.sort(key=_area2, reverse=True)

                # Clasificar cada candidato: outer (región propia) vs hole (dentro de un outer)
                # Un hatch con N outer paths separados genera N entidades Hatch independientes.
                _outer_paths: list = []    # [(pts, [holes_pts, ...])]
                for _cand in _all_cands:
                    _ccx = sum(_p[0] for _p in _cand) / len(_cand)
                    _ccy = sum(_p[1] for _p in _cand) / len(_cand)
                    _assigned = False
                    for _op_idx, (_op_pts, _op_holes) in enumerate(_outer_paths):
                        if _in_poly(_ccx, _ccy, _op_pts):
                            _op_holes.append(_cand)   # es hueco del outer existente
                            _assigned = True
                            break
                    if not _assigned:
                        _outer_paths.append((_cand, []))   # nueva región outer

                # El primer outer (mayor área) es el boundary principal
                if not _outer_paths:
                    return None
                pts   = _outer_paths[0][0]
                holes = _outer_paths[0][1]

            if len(pts) < 3:
                return None

            # ── 5. Leer familias de líneas del patrón (máx 2, sin dashes) ────
            # Solo ángulo + espaciado perpendicular. Costo único en import.
            # offset[1] = componente perpendicular (espaciado entre líneas).
            pattern_lines = []
            if not solid_fill:
                try:
                    for pl in e.pattern.lines:
                        perp = abs(float(pl.offset[1]))
                        if perp < 1e-12:
                            continue
                        pattern_lines.append((
                            float(pl.angle),        # ángulo de la familia
                            perp * hatch_scale,     # espaciado en unidades mundo
                        ))
                        if len(pattern_lines) >= 2:   # cap: máx 2 familias
                            break
                except Exception:
                    pattern_lines = []

            # ── 6. Detectar gradiente ────────────────────────────────────────
            # Los gradientes reales usan pattern=SOLID con datos de gradiente
            # (LINEAR, CYLINDER, SPHERICAL, etc.). Los renderizamos como sólido
            # con el color primario del gradiente.
            #
            # IMPORTANTE: Muchos hatches SOLID tienen has_gradient_data=True pero
            # con kind=GradientType.NONE (valor 0) y name=''. Son rellenos sólidos
            # normales — no tienen gradiente real. Sin esta verificación, esos hatches
            # se marcan is_gradient=True y _tess_hatch_gl los rechaza a PIL con
            # ValueError("gradient"), aunque son simples triangle fans de 3-23 vértices.
            is_gradient    = False
            gradient_color = col
            try:
                if hasattr(e, "has_gradient_data") and e.has_gradient_data:
                    grad = e.gradient
                    if grad is not None:
                        # Verificar que sea un gradiente REAL:
                        # kind != NONE (0) o name no vacío
                        try:
                            from ezdxf.entities.gradient import GradientType as _GT
                            _real = (grad.kind is not None and
                                     grad.kind != _GT.NONE and
                                     bool(getattr(grad, 'name', '')))
                        except Exception:
                            # Fallback si ezdxf cambia la API: confiar en name
                            _real = bool(getattr(grad, 'name', ''))
                        if _real:
                            is_gradient = True
                            c1 = getattr(grad, "color1", None)
                            if isinstance(c1, (list, tuple)) and len(c1) >= 3:
                                gradient_color = (f"#{int(c1[0]):02X}"
                                                  f"{int(c1[1]):02X}"
                                                  f"{int(c1[2]):02X}")
            except Exception:
                pass

            def _mk_hatch(bnd, hls):
                return Hatch(boundary=bnd, pattern=pattern,
                             angle=angle, scale=hatch_scale,
                             dxf_scale=dxf_scale,
                             layer=lyr, color=col,
                             is_gradient=is_gradient,
                             gradient_color=gradient_color,
                             pattern_lines=pattern_lines,
                             holes=hls)

            if len(_outer_paths) == 1:
                return _mk_hatch(pts, holes)
            # Múltiples regiones outer → una entidad Hatch por región
            return [_mk_hatch(op, oh) for op, oh in _outer_paths if len(op) >= 3]
        except Exception:
            return None

    # ── WIPEOUT — polígono de enmascaramiento ────────────────────────────────
    # Dibuja un polígono relleno blanco para "borrar" lo que hay debajo.
    if t == "WIPEOUT":
        try:
            boundary = e.boundary
            if boundary is not None:
                pts = [(float(v.x) * escala, float(v.y) * escala) for v in boundary]
                if len(pts) >= 3:
                    return Hatch(
                        boundary=pts,
                        pattern="SOLID",
                        color="#FFFFFF",
                        layer=lyr or "WIPEOUT",
                        holes=[],
                        scale=1.0,
                        angle=0.0,
                    )
        except Exception:
            pass
        return None

    # ── Fix #2: SOLID (relleno de 4 puntos, NO 3D solid) ────────────────────
    # AcDbSolid = polígono relleno de 3-4 vértices. Se convierte a Hatch SOLID.
    # Orden de vértices AutoCAD: p1,p2,p4,p3 (patrón en Z, no secuencial).
    if t == "SOLID":
        try:
            p1 = e.dxf.vtx0; p2 = e.dxf.vtx1
            p3 = e.dxf.vtx2; p4 = e.dxf.vtx3
            pts = [
                (p1.x * escala, p1.y * escala),
                (p2.x * escala, p2.y * escala),
                (p4.x * escala, p4.y * escala),
                (p3.x * escala, p3.y * escala),
            ]
            # Triangular si p3==p4
            if abs(p3.x - p4.x) < 1e-9 and abs(p3.y - p4.y) < 1e-9:
                pts = pts[:3]
            if len(pts) < 3:
                return None
            return Hatch(boundary=pts, pattern="SOLID",
                         angle=0.0, scale=1.0, dxf_scale=1.0,
                         layer=lyr, color=col)
        except Exception:
            return None

    # ── Fix #3: XLINE (línea de construcción infinita) ──────────────────────
    # Se importa como XLine interna usando el punto base y vector dirección.
    if t == "XLINE":
        try:
            p  = e.dxf.start
            uv = e.dxf.unit_vector
            return XLine(
                x1=p.x  * escala, y1=p.y  * escala,
                x2=(p.x + uv.x) * escala, y2=(p.y + uv.y) * escala,
                layer=lyr, color=col)
        except Exception:
            return None

    # ── Fix #4: 3DPOLYLINE (polilínea 3D → proyectar a XY) ──────────────────
    if t == "3DPOLYLINE":
        try:
            pts = [(v.dxf.location.x * escala, v.dxf.location.y * escala)
                   for v in e.vertices]
            if len(pts) < 2:
                return None
            return Polyline(points=pts,
                            closed=bool(getattr(e.dxf, "flags", 0) & 1),
                            layer=lyr, color=col)
        except Exception:
            return None

    # ── Fix #5: MLINE (muro de doble línea) → expandir a LINE reales ──────────
    # virtual_entities() descompone el MLINE en sus líneas paralelas con esquinas
    # mitradas correctas, sin modificar el documento ezdxf.
    # Las entidades resultantes se importan como Line internas normales.
    if t == "MLINE":
        try:
            lines = []
            for ve in e.virtual_entities():
                if ve.dxftype() == "LINE":
                    s  = ve.dxf.start
                    ex = ve.dxf.end
                    lines.append(Line(
                        x1=float(s.x)  * escala, y1=float(s.y)  * escala,
                        x2=float(ex.x) * escala, y2=float(ex.y) * escala,
                        layer=lyr, color=col))
            if lines:
                return lines          # _append_prim acepta listas
            # Fallback: si virtual_entities no da nada, eje central
            pts = [(v.location.x * escala, v.location.y * escala)
                   for v in e.vertices]
            return Polyline(points=pts, closed=False, layer=lyr, color=col) if len(pts) >= 2 else None
        except Exception:
            return None

    # ── SPLINE → Spline (flattening via ezdxf — respeta B-spline real) ──────
    if t == "SPLINE":
        try:
            from cad.entities import Spline as _Spl
            # flattening(distance) devuelve Vec3 — proyectamos a XY
            _flat_pts = list(e.flattening(0.5))   # desviación máx 0.5 unidades
            pts = [(float(p[0]) * escala, float(p[1]) * escala) for p in _flat_pts]
            if len(pts) < 2:
                return None
            return _Spl(points=pts, closed=bool(e.closed), layer=lyr, color=col)
        except Exception:
            return None

    # ── ELLIPSE → Ellipse (elipses completas) o Polyline (arcos de elipse) ──
    if t == "ELLIPSE":
        try:
            from cad.entities import Ellipse as _Ell
            cen = e.dxf.center
            maj = e.dxf.major_axis
            ratio = float(e.dxf.ratio)
            rx = math.hypot(maj.x, maj.y) * escala
            ry = rx * abs(ratio)
            angle_deg = math.degrees(math.atan2(maj.y, maj.x))
            sp = float(e.dxf.start_param)
            ep = float(e.dxf.end_param)
            delta = abs(ep - sp)
            is_full = abs(delta - 2 * math.pi) < 0.01 or delta < 0.01
            if is_full and rx > 1e-6:
                return _Ell(cx=cen.x * escala, cy=cen.y * escala,
                            rx=rx, ry=ry, angle=angle_deg,
                            layer=lyr, color=col)
            # Arco de elipse → Polyline via flattening
            _flat_pts = list(e.flattening(0.5))
            pts = [(float(p[0]) * escala, float(p[1]) * escala) for p in _flat_pts]
            if len(pts) < 2:
                return None
            return Polyline(points=pts, closed=False, layer=lyr, color=col)
        except Exception:
            return None

    # ── Fix #6: POINT (punto de referencia) → círculo mínimo visible ─────────
    if t == "POINT":
        try:
            cx = e.dxf.location.x * escala
            cy = e.dxf.location.y * escala
            # Radio = 1mm en unidades mundo (visible pero no intrusivo)
            r  = max(0.001 * escala, 1e-6)
            return Circle(cx=cx, cy=cy, radius=r, layer=lyr, color=col)
        except Exception:
            return None

    # ── Fix #7: LEADER (QLEADER clásico) → Leader interno ───────────────────
    # AcDbLeader creado con QLEADER o rutinas LISP. Tiene lista de vértices
    # que forman la polilínea + flecha en el primer vértice.
    # No tiene handler propio → caía silenciosamente. 263 en Casa Merlos.
    if t == "LEADER":
        try:
            from cad.entities import Leader as _Ldr
            _verts = list(e.vertices)
            _pts = [(float(_v[0]) * escala, float(_v[1]) * escala) for _v in _verts]
            if len(_pts) >= 2:
                return _Ldr(points=_pts, text="", layer=lyr, color=col)
        except Exception:
            return None

    # ── Fix #8: ATTDEF (definición de atributo) → Text con valor por defecto ─
    if t == "ATTDEF":
        try:
            content = str(e.dxf.get("text", "") or
                          e.dxf.get("tag",  "") or "").strip()
            if not content:
                return None
            h   = abs(e.dxf.get("height", 0.20) * escala)
            ang = float(e.dxf.get("rotation", 0.0))
            ins = e.dxf.insert
            return Text(x=ins.x * escala, y=ins.y * escala,
                        content=content, height=max(h, 1e-6),
                        angle=ang, layer=lyr, color=col)
        except Exception:
            return None

    return None   # tipo no soportado


# ── Parseo de BlockDef ────────────────────────────────────────────────────────

def _parsear_blockdef(blk, escala: float, _depth: int = 0) -> BlockDef:
    """Convierte una definición de bloque DXF a BlockDef interna.

    Itera las entidades raw del bloque.  Para sub-INSERTs llama
    INSERT.virtual_entities() — ese método SÍ aplica la transformación
    (posición + escala + rotación) y devuelve primitivas en el espacio
    del bloque padre.  Así se expanden correctamente todos los niveles
    de anidamiento sin perder geometría.

    Las entidades quedan en coordenadas del espacio de bloque (×escala),
    listas para que el renderer aplique el transform del INSERT externo.
    """
    MAX_BDEF_DEPTH = 6   # evita recursión infinita en bloques circulares

    bp = blk.base_point if hasattr(blk, "base_point") else (0.0, 0.0, 0.0)
    bdef = BlockDef(
        name=blk.name,
        base_point=(bp[0] * escala, bp[1] * escala),
    )

    if _depth > MAX_BDEF_DEPTH:
        return bdef

    for e in blk:           # iterar entidades raw del bloque (NO virtual_entities)
        t = e.dxftype()
        if t in _SKIP_TYPES:
            continue

        # ── Sub-INSERT: expandir con su propio transform ──────────────
        if t == "INSERT":
            if _depth >= MAX_BDEF_DEPTH:
                continue
            try:
                # INSERT.virtual_entities() aplica posición+escala+rotación
                # y devuelve primitivas en coordenadas del bloque padre.
                for sub_e in e.virtual_entities():
                    sub_t = sub_e.dxftype()
                    if sub_t in _SKIP_TYPES or sub_t == "INSERT":
                        continue
                    if sub_t == "DIMENSION":
                        # Ignorar en bloque — se extrae como Dimension semántica
                        # top-level en _procesar_entidad (INSERT processing)
                        continue
                    _append_prim(sub_e, escala, bdef.entities)
            except Exception:
                pass
            continue

        # ── DIMENSION dentro de bloque: ignorar aquí, se extrae como
        #    entidad semántica top-level en _procesar_entidad ───────────
        if t == "DIMENSION":
            continue

        # ── ATTDEF: definición de atributo → AttDef en bdef.attdefs ──
        if t == "ATTDEF":
            try:
                tag     = str(e.dxf.get("tag",    "")).strip()
                prompt  = str(e.dxf.get("prompt", "")).strip()
                default = str(e.dxf.get("text",   "") or tag).strip()
                h       = abs(e.dxf.get("height", 0.20) * escala)
                ang     = float(e.dxf.get("rotation", 0.0))
                lyr     = e.dxf.layer if e.dxf.hasattr("layer") else "0"
                ins_pt  = e.dxf.insert
                if tag:
                    bdef.attdefs.append(AttDef(
                        tag=tag, prompt=prompt, default=default,
                        x=ins_pt.x * escala, y=ins_pt.y * escala,
                        height=max(h, 1e-6), angle=ang, layer=lyr))
            except Exception:
                pass
            # No crear Text desde ATTDEF — el valor real viene de ATTRIB por instancia
            continue

        # ── ATTRIB dentro de bloque → convertir a texto ───────────────
        if t == "ATTRIB":
            try:
                txt = e.dxf.get("text", "").strip()
                if txt:
                    h = abs(e.dxf.get("height", 0.20) * escala)
                    lyr = e.dxf.layer if e.dxf.hasattr("layer") else "0"
                    bdef.entities.append(Text(
                        x=e.dxf.insert.x * escala,
                        y=e.dxf.insert.y * escala,
                        content=txt.upper(),
                        height=max(h, 0.01),
                        angle=e.dxf.get("rotation", 0.0),
                        layer=lyr))
            except Exception:
                pass
            continue

        # ── Primitiva normal ──────────────────────────────────────────
        _append_prim(e, escala, bdef.entities)

    return bdef


# ── Helpers para tipos complejos ─────────────────────────────────────────────

def _pt(v, escala: float) -> tuple:
    """Convierte un punto ezdxf (Vec3) a (x, y) escalado."""
    return (float(v.x) * escala, float(v.y) * escala)


def _importar_dim(e, result: ImportResult, conteo: dict, escala: float) -> None:
    """Wrapper — delega a _importar_dim_to usando result.entities."""
    _importar_dim_to(e, result.entities, conteo, escala)


def _importar_dim_to(e, entities: list, conteo: dict, escala: float) -> None:
    """Importa DIMENSION como entidad Dimension semántica (editable).

    Mapeo de dimtype DXF → dim_type interno:
      0 (Linear/Rotado) → H / V / A  según ángulo de rotación
      1 (Aligned)       → A
      2 (Angular 2L)    → ANG
      3 (Diameter)      → D
      4 (Radius)        → R
      5 (Angular 3pt)   → ANG
      6 (Ordinate)      → ORD

    Fallback: si faltan defpoints críticos, explota a geometría primitiva.
    """
    lyr = e.dxf.layer if e.dxf.hasattr("layer") else "0"
    # dimtype tiene bits de flags: usar solo bits 0-3 para el tipo base
    raw_type  = e.dxf.get("dimtype", 0)
    base_type = raw_type & 0x0F

    # Texto override ("" o "<>" = automático, cualquier otro = fijo)
    txt_raw = e.dxf.get("text", "") or ""
    text_override = "" if txt_raw in ("", "<>", "< >") else txt_raw

    # Estilo de cota
    style = e.dxf.get("dimstyle", "") or "Arq-50"

    # Posición del texto (text_midpoint = group 11)
    try:
        tp = e.dxf.text_midpoint
        text_pos = _pt(tp, escala)
    except Exception:
        text_pos = None

    # Detectar si AutoCAD suprime las ext lines (DIMCONTINUE, etc.)
    # Early-exit: para en cuanto encuentra 2 LINE — evita materializar todos los objs.
    _no_ext = False
    try:
        _n_lines = 0
        for _v in e.virtual_entities():
            if _v.dxftype() == "LINE":
                _n_lines += 1
                if _n_lines > 1:
                    break
        _no_ext = (_n_lines <= 1)
    except Exception:
        pass

    try:
        # ── LINEAR ROTADO (0) y ALINEADO (1) ─────────────────────
        if base_type in (0, 1):
            p1  = _pt(e.dxf.defpoint2, escala)   # origen extensión 1
            p2  = _pt(e.dxf.defpoint3, escala)   # origen extensión 2
            pos = _pt(e.dxf.defpoint,  escala)   # posición línea de cota

            rot_angle = None  # None = sin rotación especial
            if base_type == 1:
                dim_type = "A"
            else:
                # Tipo 0 — determinar H / V / A según ángulo de rotación
                angle = e.dxf.get("angle", 0.0) % 360.0
                norm = angle % 180.0
                if norm < 1.0 or norm > 179.0:
                    dim_type = "H"
                elif 89.0 < norm < 91.0:
                    dim_type = "V"
                else:
                    # Cota rotada (ej. 45°) — guardar ángulo real
                    dim_type = "A"
                    rot_angle = angle
            entities.append(Dimension(
                p1=p1, p2=p2, pos=pos,
                dim_type=dim_type,
                text_override=text_override,
                style=style, layer=lyr,
                text_pos=text_pos,
                rot_angle=rot_angle,
                no_ext=_no_ext))
            conteo["DIMENSION"] = conteo.get("DIMENSION", 0) + 1
            return

        # ── ANGULAR 2 LÍNEAS (2) ──────────────────────────────────
        if base_type == 2:
            # defpoint3 = vértice (intersección de las dos líneas)
            # defpoint2 = punto en brazo 1
            # defpoint4 = punto en brazo 2
            # defpoint  = posición del arco de cota
            center = _pt(e.dxf.defpoint3, escala)
            arm1   = _pt(e.dxf.defpoint2, escala)
            arm2   = _pt(e.dxf.defpoint4, escala)
            entities.append(Dimension(
                p1=center, p2=arm1, pos=arm2,
                dim_type="ANG",
                text_override=text_override,
                style=style, layer=lyr,
                text_pos=text_pos))
            conteo["DIMENSION"] = conteo.get("DIMENSION", 0) + 1
            return

        # ── DIÁMETRO (3) ──────────────────────────────────────────
        if base_type == 3:
            # defpoint  = centro del círculo
            # defpoint5 = punto en la circunferencia (flecha)
            center = _pt(e.dxf.defpoint,  escala)
            arrow  = _pt(e.dxf.defpoint5, escala)
            entities.append(Dimension(
                p1=center, p2=arrow, pos=arrow,
                dim_type="D",
                text_override=text_override,
                style=style, layer=lyr,
                text_pos=text_pos))
            conteo["DIMENSION"] = conteo.get("DIMENSION", 0) + 1
            return

        # ── RADIO (4) ─────────────────────────────────────────────
        if base_type == 4:
            # defpoint  = centro del círculo/arco
            # defpoint5 = punto en la circunferencia (donde apunta la flecha)
            center = _pt(e.dxf.defpoint,  escala)
            arrow  = _pt(e.dxf.defpoint5, escala)
            entities.append(Dimension(
                p1=center, p2=arrow, pos=arrow,
                dim_type="R",
                text_override=text_override,
                style=style, layer=lyr,
                text_pos=text_pos))
            conteo["DIMENSION"] = conteo.get("DIMENSION", 0) + 1
            return

        # ── ANGULAR 3 PUNTOS (5) ──────────────────────────────────
        if base_type == 5:
            # defpoint  = vértice (centro del ángulo)
            # defpoint2 = punto en brazo 1
            # defpoint3 = punto en brazo 2
            center = _pt(e.dxf.defpoint,  escala)
            arm1   = _pt(e.dxf.defpoint2, escala)
            arm2   = _pt(e.dxf.defpoint3, escala)
            entities.append(Dimension(
                p1=center, p2=arm1, pos=arm2,
                dim_type="ANG",
                text_override=text_override,
                style=style, layer=lyr,
                text_pos=text_pos))
            conteo["DIMENSION"] = conteo.get("DIMENSION", 0) + 1
            return

        # ── ORDENADA (6) ─────────────────────────────────────────
        if base_type == 6:
            # defpoint2 = punto medido
            # defpoint3 = extremo del líder
            p1  = _pt(e.dxf.defpoint2, escala)
            p2  = _pt(e.dxf.defpoint3, escala)
            pos = _pt(e.dxf.defpoint,  escala)
            entities.append(Dimension(
                p1=p1, p2=p2, pos=pos,
                dim_type="ORD",
                text_override=text_override,
                style=style, layer=lyr,
                text_pos=text_pos))
            conteo["DIMENSION"] = conteo.get("DIMENSION", 0) + 1
            return

    except Exception:
        pass

    # ── FALLBACK: explotar a geometría primitiva ──────────────────
    def _add_sub(source):
        added = 0
        for sub_e in source:
            sub_t = sub_e.dxftype()
            if sub_t in _SKIP_TYPES or sub_t == "INSERT":
                continue
            _n = _append_prim(sub_e, escala, entities)
            if _n:
                conteo[sub_t] = conteo.get(sub_t, 0) + _n
                added += _n
        return added

    try:
        if _add_sub(list(e.virtual_entities())) > 0:
            return
    except Exception:
        pass
    try:
        blk = e.get_geometry_block()
        if blk is not None and _add_sub(blk) > 0:
            return
    except Exception:
        pass
    # Último recurso: texto + línea mínima
    try:
        dim_txt = e.dxf.get("text", None) or f"{e.dxf.actual_measurement * escala:.2f}"
        tp = e.dxf.text_midpoint
        tx, ty = float(tp.x) * escala, float(tp.y) * escala
        h = abs(e.dxf.get("dimtxt", 2.5) * escala)
        entities.append(Text(
            x=tx, y=ty, content=str(dim_txt).upper(),
            height=max(h, 0.01), angle=0.0, layer=lyr))
        conteo["DIM_TEXT"] = conteo.get("DIM_TEXT", 0) + 1
    except Exception:
        pass


def _importar_attribs(e, ins_entity, result: ImportResult, conteo: dict, escala: float) -> None:
    """Extrae ATTRIBs de un INSERT.

    Crea un Text en WCS para display (comportamiento existente) Y un Attrib
    estructurado en ins_entity.attribs para que EATTEDIT pueda editar el valor
    y actualizar el Text de display sin reconstruir nada.

    La posición del ATTRIB (group code 10) ya está en WCS — el transform del
    INSERT fue aplicado por AutoCAD al guardar.
    """
    try:
        # e.attribs es una propiedad/lista en ezdxf moderno, NO un método.
        # Llamar e.attribs() lanza "'list' object is not callable" → se tragaba silenciosamente.
        attribs = list(e.attribs)
    except Exception:
        return
    if not attribs:
        return

    for attrib in attribs:
        try:
            txt = attrib.dxf.get("text", "").strip()
            tag = str(attrib.dxf.get("tag", "")).strip()
            if not txt:
                continue
            # Bit 1 de flags = invisible → no mostrar
            if attrib.dxf.get("flags", 0) & 1:
                continue
            # La posición del ATTRIB YA está en WCS (DXF estándar)
            wx  = float(attrib.dxf.insert.x) * escala
            wy  = float(attrib.dxf.insert.y) * escala
            h   = abs(attrib.dxf.get("height", 0.20) * escala)
            ang = float(attrib.dxf.get("rotation", 0.0))
            lyr = attrib.dxf.layer if attrib.dxf.hasattr("layer") else "0"
            # Text de display (rendering existente sin cambios)
            txt_ent = Text(x=wx, y=wy, content=txt.upper(),
                           height=max(h, 0.01), angle=ang, layer=lyr)
            result.entities.append(txt_ent)
            # Attrib estructurado — linkea el Text para que EATTEDIT lo actualice
            if ins_entity is not None:
                att = Attrib(tag=tag, value=txt, x=wx, y=wy,
                             height=max(h, 0.01), angle=ang, layer=lyr)
                att._text_ref = txt_ent
                ins_entity.attribs.append(att)
            conteo["ATTRIB"] = conteo.get("ATTRIB", 0) + 1
        except Exception:
            continue


def _importar_mleader(e, result: ImportResult, conteo: dict, escala: float) -> None:
    """Extrae texto y línea guía de MLEADER (cotas de nota / anotaciones con flecha)."""
    try:
        txt = None
        loc_x, loc_y = None, None
        h = 2.5   # altura por defecto en unidades DXF

        # ── Intentar via context_data (ezdxf >= 0.17) ────────────
        try:
            ctx = e.context_data
            if ctx is not None:
                if hasattr(ctx, "mtext") and ctx.mtext is not None:
                    raw = (ctx.mtext.default_content
                           if hasattr(ctx.mtext, "default_content") else "")
                    txt = _strip_mtext(raw).strip() or None
                    if hasattr(ctx.mtext, "insert") and ctx.mtext.insert:
                        loc_x = float(ctx.mtext.insert.x)
                        loc_y = float(ctx.mtext.insert.y)
                    if hasattr(ctx.mtext, "char_height") and ctx.mtext.char_height:
                        h = float(ctx.mtext.char_height)
                # Línea del líder (de la punta a la rotura del codo)
                if hasattr(ctx, "leaders"):
                    for leader in (ctx.leaders or []):
                        try:
                            pts_l = []
                            for lv in (leader.lines or []):
                                for pt in (lv.vertices or []):
                                    pts_l.append((float(pt.x)*escala, float(pt.y)*escala))
                            if len(pts_l) >= 2:
                                for i in range(len(pts_l) - 1):
                                    result.entities.append(Line(
                                        x1=pts_l[i][0], y1=pts_l[i][1],
                                        x2=pts_l[i+1][0], y2=pts_l[i+1][1],
                                        layer=e.dxf.layer if e.dxf.hasattr("layer") else "0"))
                        except Exception:
                            pass
        except Exception:
            pass

        # ── Fallback 1: atributos DXF directos ───────────────────
        if not txt:
            for attr in ("mtext_string", "text"):
                try:
                    raw = e.dxf.get(attr, "")
                    if raw:
                        txt = _strip_mtext(raw).strip() or None
                        if txt:
                            break
                except Exception:
                    pass

        if not txt:
            return

        # ── Posición del texto ────────────────────────────────────
        if loc_x is None:
            for attr in ("insert", "text_location", "dogleg_vector"):
                try:
                    v = getattr(e.dxf, attr, None) or e.dxf.get(attr, None)
                    if v is not None:
                        loc_x = float(v[0])
                        loc_y = float(v[1])
                        break
                except Exception:
                    pass
        if loc_x is None:
            return

        h_scaled = abs(e.dxf.get("char_height", h) * escala)
        _ent_layer = e.dxf.layer if e.dxf.hasattr("layer") else "0"
        entities.append(Text(
            x=loc_x * escala, y=loc_y * escala,
            content=txt.upper(),
            height=max(h_scaled, 0.01),
            angle=0.0, layer=_ent_layer))
        conteo["MLEADER"] = conteo.get("MLEADER", 0) + 1
    except Exception:
        pass


# ── Extractor semántico de DIMENSION desde bloques raw ───────────────────────

def _extraer_dims_bloque(
    blk_name: str,
    doc,
    ix: float, iy: float,       # INSERT insertion point (ya × escala)
    sx: float, sy: float,       # INSERT scale factors
    ang: float,                 # INSERT rotation degrees
    escala: float,
    result: ImportResult,
    conteo: dict,
    _depth: int = 0,
    _blks_sin_dims: set | None = None,  # caché de bloques sin DIMENSION
) -> None:
    """Lee DIMENSION desde las entidades RAW del bloque (sin explotar),
    aplica el transform del INSERT manualmente y crea Dimension semánticas.

    Pipeline correcto:
      raw block entity (DIMENSION) → leer defpoints → aplicar transform →
      crear Dimension interna → ignorar geometría gráfica DXF

    No usa virtual_entities() — ese método ya devuelve geometría explotada
    y pierde la semántica de la cota.
    """
    if _depth > 4:
        return
    # Saltar bloques que ya sabemos no tienen DIMENSION
    if _blks_sin_dims is not None and blk_name in _blks_sin_dims:
        return
    try:
        blk = doc.blocks.get(blk_name)
    except Exception:
        return
    if blk is None:
        return

    # Verificar rápido si el bloque tiene alguna DIMENSION (primera pasada)
    tipos = {e.dxftype() for e in blk}
    if "DIMENSION" not in tipos and "INSERT" not in tipos:
        if _blks_sin_dims is not None:
            _blks_sin_dims.add(blk_name)
        return

    # Base point del bloque (offset de origen del bloque, en DXF units)
    try:
        bp = blk.base_point
        bpx, bpy = float(bp[0]), float(bp[1])
    except Exception:
        bpx, bpy = 0.0, 0.0

    cos_a = math.cos(math.radians(ang))
    sin_a = math.sin(math.radians(ang))

    def _transform(local_x: float, local_y: float) -> tuple:
        """Transforma un punto del espacio de bloque a coordenadas mundo."""
        dx = local_x - bpx
        dy = local_y - bpy
        # Escala del bloque
        dxs = dx * sx
        dys = dy * sy
        # Rotación del INSERT
        dxr = dxs * cos_a - dys * sin_a
        dyr = dxs * sin_a + dys * cos_a
        # Traslación (ix/iy ya están en coords mundo = DXF × escala)
        return (dxr * escala + ix, dyr * escala + iy)

    for raw_e in blk:
        t = raw_e.dxftype()

        if t == "DIMENSION":
            # ── Leer solo semántica, ignorar geometría gráfica DXF ───
            try:
                lyr = raw_e.dxf.layer if raw_e.dxf.hasattr("layer") else "0"
                raw_type  = raw_e.dxf.get("dimtype", 0)
                base_type = raw_type & 0x0F

                txt_raw = raw_e.dxf.get("text", "") or ""
                text_override = "" if txt_raw in ("", "<>", "< >") else txt_raw
                style = raw_e.dxf.get("dimstyle", "") or "Arq-50"

                try:
                    tp = raw_e.dxf.text_midpoint
                    text_pos = _transform(float(tp.x), float(tp.y))
                except Exception:
                    text_pos = None

                def _tp(v):
                    return _transform(float(v.x), float(v.y))

                if base_type in (0, 1):
                    p1  = _tp(raw_e.dxf.defpoint2)
                    p2  = _tp(raw_e.dxf.defpoint3)
                    pos = _tp(raw_e.dxf.defpoint)
                    if base_type == 1:
                        dim_type = "A"
                    else:
                        a = raw_e.dxf.get("angle", 0.0) % 180.0
                        dim_type = "H" if (a < 1.0 or a > 179.0) else ("V" if 89.0 < a < 91.0 else "A")

                elif base_type == 2:
                    p1  = _tp(raw_e.dxf.defpoint3)   # vértice/centro
                    p2  = _tp(raw_e.dxf.defpoint2)   # brazo 1
                    pos = _tp(raw_e.dxf.defpoint4)   # brazo 2
                    dim_type = "ANG"

                elif base_type == 3:
                    p1  = _tp(raw_e.dxf.defpoint)
                    p2  = _tp(raw_e.dxf.defpoint5)
                    pos = p2;  dim_type = "D"

                elif base_type == 4:
                    p1  = _tp(raw_e.dxf.defpoint)
                    p2  = _tp(raw_e.dxf.defpoint5)
                    pos = p2;  dim_type = "R"

                elif base_type == 5:
                    p1  = _tp(raw_e.dxf.defpoint)
                    p2  = _tp(raw_e.dxf.defpoint2)
                    pos = _tp(raw_e.dxf.defpoint3)
                    dim_type = "ANG"

                elif base_type == 6:
                    p1  = _tp(raw_e.dxf.defpoint2)
                    p2  = _tp(raw_e.dxf.defpoint3)
                    pos = _tp(raw_e.dxf.defpoint)
                    dim_type = "ORD"

                else:
                    continue

                # Cap: no importar más de 5000 cotas (evita explosión por bloques repetidos)
                if conteo.get("DIMENSION", 0) < 5000:
                    result.entities.append(Dimension(
                        p1=p1, p2=p2, pos=pos,
                        dim_type=dim_type,
                        text_override=text_override,
                        style=style, layer=lyr,
                        text_pos=text_pos))
                    conteo["DIMENSION"] = conteo.get("DIMENSION", 0) + 1

            except Exception:
                pass  # defpoint faltante → ignorar esta cota

        elif t == "INSERT" and _depth < 4:
            # Bloque anidado → aplicar transforms en cadena
            try:
                sub_name = raw_e.dxf.name
                sub_ix_l = float(raw_e.dxf.insert.x)
                sub_iy_l = float(raw_e.dxf.insert.y)
                sub_ix, sub_iy = _transform(sub_ix_l, sub_iy_l)
                sub_sx   = float(raw_e.dxf.get("xscale", 1.0)) * sx
                sub_sy   = float(raw_e.dxf.get("yscale", 1.0)) * sy
                sub_ang  = ang + float(raw_e.dxf.get("rotation", 0.0))
                _extraer_dims_bloque(
                    sub_name, doc,
                    sub_ix, sub_iy, sub_sx, sub_sy, sub_ang,
                    escala, result, conteo, _depth + 1,
                    _blks_sin_dims=_blks_sin_dims)
            except Exception:
                pass


# ── Procesador de entidades del modelspace ────────────────────────────────────

def _procesar_entidad(
    e,
    doc,
    result: ImportResult,
    conteo: dict,
    escala: float,
    depth: int = 0,
    _blks_sin_dims: set | None = None,   # caché compartido entre todos los INSERTs
) -> None:
    """Convierte una entidad DXF del modelspace al formato del visor.

    Los INSERT se convierten a entidades Insert (block instancing real).
    Las entidades primitivas se convierten directamente.
    """
    if len(result.entities) >= MAX_ENTITIES:
        return

    t = e.dxftype()

    # ── DIMENSION — expandir geometría del bloque anónimo ────────────
    if t == "DIMENSION":
        _importar_dim(e, result, conteo, escala)
        return

    # ── MLEADER — extraer texto y línea de referencia ─────────────────
    if t == "MLEADER":
        _importar_mleader(e, result, conteo, escala)
        return

    # ── INSERT — block instancing ──────────────────────────────────
    if t == "INSERT":
        blk_name = e.dxf.name
        if blk_name.startswith(("*Model_Space", "*Paper_Space")):
            return
        if depth >= MAX_INS_DEPTH:
            return

        lyr_name = e.dxf.layer if e.dxf.hasattr("layer") else "0"

        # Parsear BlockDef si aún no la tenemos
        if blk_name not in result.block_defs:
            try:
                blk = doc.blocks.get(blk_name)
                if blk is not None:
                    result.block_defs[blk_name] = _parsear_blockdef(blk, escala)
            except Exception as exc:
                result.warnings.append(f"BlockDef '{blk_name}': {exc}")

        # Crear entidad Insert
        ins_entity = None
        try:
            ix  = e.dxf.insert.x * escala
            iy  = e.dxf.insert.y * escala
            sx  = float(e.dxf.get("xscale", 1.0))
            sy  = float(e.dxf.get("yscale", 1.0))
            ang = float(e.dxf.get("rotation", 0.0))
            bdef = result.block_defs.get(blk_name)
            if bdef is None or len(bdef.entities) > 0:
                ins_entity = Insert(block_name=blk_name,
                                    x=ix, y=iy,
                                    scale_x=sx, scale_y=sy,
                                    angle=ang, layer=lyr_name)
                result.entities.append(ins_entity)
                conteo["INSERT"] = conteo.get("INSERT", 0) + 1
        except Exception as exc:
            result.warnings.append(f"INSERT '{blk_name}' ignorado: {exc}")

        # ATTRIBs: texto WCS + Attrib estructurado en ins_entity
        try:
            _importar_attribs(e, ins_entity, result, conteo, escala)
        except Exception:
            pass

        # Extraer DIMENSION semánticas: leer raw block, aplicar transform INSERT
        # NO usar virtual_entities() — ya devuelve geometría explotada
        if _blks_sin_dims is not None:
            try:
                _extraer_dims_bloque(blk_name, doc,
                                     ix, iy, sx, sy, ang, escala,
                                     result, conteo,
                                     _blks_sin_dims=_blks_sin_dims)
            except Exception:
                pass
        return

    # ── Primitivas ─────────────────────────────────────────────────
    _n = _append_prim(e, escala, result.entities)
    if _n:
        conteo[t] = conteo.get(t, 0) + _n


# ── Importador principal ─────────────────────────────────────────────────────

def leer_capas_dxf(ruta: str) -> list[dict]:
    """
    Lee solo las capas de un DXF sin procesar entidades (muy rápido).
    Retorna lista de dicts: {name, color, visible, frozen, entity_count}.
    Usado para mostrar el diálogo de selección antes de importar.
    """
    import ezdxf
    from ezdxf import recover
    try:
        import io
        with open(ruta, "rb") as _f:
            _data = _f.read()
        doc, _ = recover.read(io.BytesIO(_data))
    except Exception:
        return []

    msp = doc.modelspace()
    # Contar entidades por capa
    conteo: dict[str, int] = {}
    for e in msp:
        lyr = e.dxf.layer if e.dxf.hasattr("layer") else "0"
        conteo[lyr] = conteo.get(lyr, 0) + 1

    capas = []
    for lyr in doc.layers:
        nombre = lyr.dxf.name
        col = "#FFFFFF"
        if lyr.dxf.hasattr("true_color"):
            r = (lyr.dxf.true_color >> 16) & 0xFF
            g = (lyr.dxf.true_color >>  8) & 0xFF
            b =  lyr.dxf.true_color        & 0xFF
            col = _true_color_hex((r, g, b))
        elif lyr.dxf.hasattr("color"):
            col = _aci_hex(abs(lyr.dxf.color))
        capas.append({
            "name":    nombre,
            "color":   col,
            "visible": not lyr.is_off,
            "frozen":  getattr(lyr, "is_frozen", False),
            "count":   conteo.get(nombre, 0),
        })
    # Ordenar: primero las visibles con entidades, luego por nombre
    capas.sort(key=lambda c: (not c["visible"] or c["frozen"], -c["count"], c["name"]))
    return capas


# Versión del esquema de caché. Incrementar cuando cambie la estructura
# de las clases de entidades (Line, Polyline, etc.) para invalidar caches viejos.
_CACHE_VERSION = 11  # raw_mtext en Text para round-trip MTEXT sin pérdida de formato

def _dxf_cache_path(ruta: str, escala: float) -> str:
    """Calcula la ruta al archivo de caché para este DXF + escala."""
    import os, hashlib
    try:
        st = os.stat(ruta)
        # Clave: ruta + tamaño + fecha modificación + escala + versión esquema
        key = f"{ruta}|{st.st_size}|{st.st_mtime}|{escala}|{_CACHE_VERSION}"
        h   = hashlib.md5(key.encode("utf-8", errors="replace")).hexdigest()[:12]
        cache_dir = os.path.join(os.path.expanduser("~"), ".estudio_merlos_ai", "dxf_cache")
        os.makedirs(cache_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(ruta))[0]
        return os.path.join(cache_dir, f"{base}_{h}.cadcache")
    except Exception:
        return ""

def _load_dxf_cache(ruta: str, escala: float):
    """Intenta cargar el ImportResult desde caché. Retorna None si no existe o inválido."""
    import pickle, os
    path = _dxf_cache_path(ruta, escala)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            payload = pickle.load(f)
        if not isinstance(payload, dict) or payload.get("v") != _CACHE_VERSION:
            return None
        result = payload["result"]
        if not isinstance(result, ImportResult):
            return None
        return result
    except Exception:
        return None

def _save_dxf_cache(ruta: str, escala: float, result: ImportResult) -> None:
    """Guarda el ImportResult en caché (silencia cualquier error)."""
    import pickle
    path = _dxf_cache_path(ruta, escala)
    if not path:
        return
    try:
        payload = {"v": _CACHE_VERSION, "result": result}
        with open(path, "wb") as f:
            pickle.dump(payload, f, protocol=5)
    except Exception:
        pass  # fallo de escritura no es fatal


def importar_dxf(
    ruta: str,
    escala: float = 1.0,
    agregar_a_layers: dict | None = None,
    capas_filtro: set | None = None,
) -> ImportResult:
    """
    Lee un archivo DXF y convierte sus entidades al formato del visor.
    Usa caché en disco: la segunda apertura del mismo archivo es ~35× más rápida.

    Args:
        ruta            : ruta al archivo .dxf
        escala          : factor de escala global (1.0 = sin cambio)
                          Ejemplo: 0.001 si el DXF está en mm y el visor en m
        agregar_a_layers: dict de capas existentes para no duplicar
        capas_filtro    : si se especifica, solo importa entidades de esas capas

    Returns:
        ImportResult con listas de entidades y capas nuevas
    """
    import ezdxf
    from ezdxf import recover

    if ruta.lower().endswith(".dwg"):
        raise ValueError(
            "ezdxf no puede leer DWG directamente.\n"
            "Convierta primero con:\n"
            "  from cad.dwg_converter import dwg_a_dxf\n"
            "  ruta_dxf = dwg_a_dxf('plano.dwg')\n"
            "O use _importar_dxf() del visor, que lo hace automáticamente."
        )

    # ── Caché de disco ────────────────────────────────────────────
    # Solo si no hay filtro de capas (el resultado filtrado no es reutilizable).
    # La clave de caché incluye tamaño + mtime del archivo → se invalida
    # automáticamente cuando el DXF se modifica en AutoCAD.
    if capas_filtro is None and agregar_a_layers is None:
        cached = _load_dxf_cache(ruta, escala)
        if cached is not None:
            return cached

    result = ImportResult()

    # ── Leer archivo ──────────────────────────────────────────────
    # Estrategia de dos pasos:
    # 1. Intentar ezdxf.readfile() — 2× más rápido que recover.read() porque
    #    no ejecuta el análisis de errores (la mayoría de DXFs están íntegros).
    # 2. Si falla (archivo corrupto, permiso denegado, OneDrive lock), leer a
    #    memoria y usar recover.read() con corrección de errores.
    try:
        doc = ezdxf.readfile(ruta)
    except ezdxf.DXFStructureError:
        # Archivo estructuralmente inválido → intentar recover
        try:
            import io as _io
            with open(ruta, "rb") as _f:
                _raw = _f.read()
            doc, auditor = recover.read(_io.BytesIO(_raw))
            if auditor.has_errors:
                result.warnings.append(
                    f"Archivo con {len(auditor.errors)} error(es) corregidos.")
        except ezdxf.DXFStructureError as e2:
            raise ValueError(f"No se pudo leer el archivo DXF: {e2}") from e2
        except OSError as e2:
            raise ValueError(f"No se pudo abrir el archivo: {e2}") from e2
    except OSError:
        # Permiso denegado (OneDrive sync, AutoCAD abierto) → leer a memoria primero
        try:
            import io as _io
            with open(ruta, "rb") as _f:
                _raw = _f.read()
            doc, auditor = recover.read(_io.BytesIO(_raw))
            if auditor.has_errors:
                result.warnings.append(
                    f"Archivo con {len(auditor.errors)} error(es) corregidos.")
        except Exception as e2:
            raise ValueError(f"No se pudo abrir el archivo: {e2}") from e2

    msp = doc.modelspace()

    # ── TAREA 3 — Capturar DimStyles si settings.json aún no los tiene ──────
    try:
        from cad.dxf_export import actualizar_dimstyles_desde_doc
        actualizar_dimstyles_desde_doc(doc)
    except Exception:
        pass  # nunca bloquear la importación

    # ── Importar capas ────────────────────────────────────────────
    existing = set((agregar_a_layers or {}).keys())
    nuevas_layers: dict[str, object] = {}

    # Conjunto de capas visibles en el DXF original (para filtrar entidades)
    _capas_visibles_dxf: set[str] = set()

    for lyr in doc.layers:
        nombre = lyr.dxf.name
        # Una capa DXF es visible si no está apagada NI congelada
        era_visible = not lyr.is_off and not getattr(lyr, "is_frozen", False)
        if era_visible:
            _capas_visibles_dxf.add(nombre)

        if nombre in existing:
            continue

        # ── Color de capa ────────────────────────────────────────────
        col = "#FFFFFF"
        try:
            # True color (24-bit) tiene prioridad
            if lyr.dxf.hasattr("true_color"):
                tc = lyr.dxf.true_color
                col = _true_color_hex(((tc >> 16) & 0xFF,
                                       (tc >>  8) & 0xFF,
                                        tc        & 0xFF))
            else:
                # ACI — ezdxf: lyr.color devuelve ACI positivo ya corregido
                # (las capas apagadas guardan ACI negativo, ezdxf lo expone abs)
                try:
                    aci = int(lyr.color)          # API de alto nivel de ezdxf
                except Exception:
                    aci = abs(int(lyr.dxf.color)) # fallback a DXF raw
                if aci not in (0, 256):            # 0=BYBLOCK, 256=BYLAYER→blanco
                    col = _aci_hex(aci)
        except Exception:
            col = "#FFFFFF"

        # ── Grosor de línea ──────────────────────────────────────────
        lw_px = 1
        try:
            lw_raw = lyr.dxf.lineweight   # centésimas de mm (e.g. 25 = 0.25 mm)
            lw_px  = 1 if lw_raw <= 0 else max(1, round(lw_raw / 35))
        except Exception:
            lw_px = 1

        # ── Tipo de línea ────────────────────────────────────────────
        lt = _dxf_linetype_to_internal(
            lyr.dxf.get("linetype", "Continuous") or "Continuous")

        nuevas_layers[nombre] = Layer(
            name=nombre, color=col, linewidth=lw_px,
            visible=True,   # siempre visible — las apagadas en AutoCAD igual se muestran
            locked=False,
            linetype=lt,
        )

    capas_apagadas = set()
    for lyr in doc.layers:
        if not (not lyr.is_off and not getattr(lyr, "is_frozen", False)):
            capas_apagadas.add(lyr.dxf.name)
    if capas_apagadas:
        result.warnings.append(
            f"Nota: {len(capas_apagadas)} capa(s) estaban apagadas/congeladas "
            f"en AutoCAD y se importaron visibles.")

    result.layers = nuevas_layers

    # ── Procesar entidades del modelspace ─────────────────────────
    conteo: dict[str, int] = {}
    truncado = False
    omitidas_capa = 0
    # Caché de bloques sin DIMENSION — evita re-escanear el mismo bloque
    # en cada INSERT que lo referencia (puede haber cientos de INSERTs iguales)
    _blks_sin_dims: set = set()

    for e in msp:
        if len(result.entities) >= MAX_ENTITIES:
            truncado = True
            break
        lyr_e = e.dxf.layer if e.dxf.hasattr("layer") else "0"
        # Saltar si el usuario eligió importar solo ciertas capas
        if capas_filtro is not None and lyr_e not in capas_filtro:
            omitidas_capa += 1
            continue
        # NO filtramos por visibilidad original del DXF — ya marcamos todas
        # las capas visible=True al importar, y el usuario las controla
        # desde el panel de capas del visor.
        try:
            _procesar_entidad(e, doc, result, conteo, escala,
                              depth=0, _blks_sin_dims=_blks_sin_dims)
        except Exception as ex:
            result.warnings.append(f"Entidad ignorada ({e.dxftype()}): {ex}")

    if omitidas_capa > 0:
        result.warnings.append(
            f"Se omitieron {omitidas_capa} entidades en capas apagadas/congeladas.")

    total_ents = sum(conteo.values())
    if truncado:
        result.warnings.append(
            f"⚠ Importación truncada en {MAX_ENTITIES:,} entidades. "
            "Archivo excepcionalmente grande — considere filtrar por capas.")
    elif total_ents > 500_000:
        result.warnings.append(
            f"ℹ Archivo muy grande ({total_ents:,} entidades). "
            "El renderizado usa culling — solo se dibujan las visibles en pantalla.")
    elif total_ents > 100_000:
        result.warnings.append(
            f"ℹ Archivo grande ({total_ents:,} entidades). "
            "Use el panel de capas para apagar las que no necesite.")

    # ── Deduplicar textos solapados ───────────────────────────────
    # En muchos DXF de AutoCAD existe simultáneamente un TEXT/MTEXT en el
    # modelspace Y el ATTRIB exportado del mismo bloque, ambos con el mismo
    # contenido y casi la misma posición → aparecen duplicados en el visor.
    #
    # Umbral adaptativo: 2× la altura del texto propio.
    # Funciona independientemente de si el DXF usa mm o metros:
    #   - texto de 250 mm de alto (cartel) → umbral 500 mm
    #   - texto de 0.25 m de alto          → umbral 0.50 m
    # Dos textos idénticos dentro de ese radio se consideran duplicados.
    # Algoritmo O(n) por contenido: agrupa por string exacto, solo compara
    # candidatos con el mismo texto. Con 2 500 textos: O(n²)=3.1M → O(n)≈2 500.
    from cad.entities import Text as _Text
    from collections import defaultdict as _dd
    _seen_by_content: dict = _dd(list)  # content → [(x, y, thresh)]
    _keep: list = []
    for ent in result.entities:
        if not isinstance(ent, _Text):
            _keep.append(ent)
            continue
        content = (ent.content or "").strip()
        thresh  = max(ent.height * 2.0, 1e-3)
        candidates = _seen_by_content[content]
        duplicate = any(
            abs(ent.x - sx) < max(thresh, st) and abs(ent.y - sy) < max(thresh, st)
            for sx, sy, st in candidates
        )
        if not duplicate:
            candidates.append((ent.x, ent.y, thresh))
            _keep.append(ent)
    _removed = len(result.entities) - len(_keep)
    if _removed > 0:
        result.entities = _keep
        result.warnings.append(
            f"ℹ {_removed} texto(s) duplicado(s) eliminado(s) (TEXT+ATTRIB en misma posición).")

    # ── Estadísticas ──────────────────────────────────────────────
    result.stats = {
        "total":       sum(conteo.values()),
        "por_tipo":    dict(conteo),
        "capas_nuevas": len(nuevas_layers),
        "truncado":    truncado,
    }

    # ── Guardar caché ─────────────────────────────────────────────
    # Solo si fue un import completo (sin filtros de capa).
    # La escritura se hace en el hilo secundario, no bloquea la UI.
    if capas_filtro is None and agregar_a_layers is None:
        _save_dxf_cache(ruta, escala, result)

    return result
