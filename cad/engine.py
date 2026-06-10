"""
engine.py — Estudio Merlos CAD · Visor v1
==========================================
Comandos y alias compatibles con AutoCAD para cero curva de aprendizaje.

Render de alto rendimiento:
  • Capa "st" (estática)  → grid + entidades  (zoom/pan/cambio)
  • Capa "dy" (dinámica)  → cursor/preview/snap (cada movimiento)
  • Throttle 33 ms en _on_move (~30 fps) · Índice espacial snap O(1)
  • R-Tree grid hash para culling viewport O(k) · LOD texto/detalles

Dependencias: customtkinter, ezdxf (pip install ezdxf)
"""
from __future__ import annotations

import copy
import math
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

import customtkinter as ctk

# Reducir el polling DPI de CTk de 100ms → 2000ms.
# check_dpi_scaling llama wm_state() en Windows, que puede bloquear 500ms.
# A 100ms se dispara 10×/s → freezes continuos en el main thread.
try:
    from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker as _CtkST
    _CtkST.update_loop_interval = 2000
except Exception:
    pass
from cad.i18n import (t, set_language, LANGUAGES, current_lang,
                      TOOL_PROMPT_KEYS, OP_PROMPT_KEYS)

# ── Módulos desacoplados ──────────────────────────────────────────────────────
from cad.entities import (
    Entity, Line, Polyline, Spline, Circle, Text, Arc, Dimension, Hatch, BlockDef, Insert,
    Ellipse, XLine, Leader, ImageRef,
    Layer, DEFAULT_LAYERS, _LINETYPES, _LT_CYCLE, _LT_ABBR, BLOCK_LIBRARY,
    _SNAP_CELL, _LOD_TEXT_PX, _LOD_DOT_SCALE,
    _cell, _rotate_point, _mirror_point,
    _dist_pt_seg, _angle_in_arc,
    _seg_intersect, _intersect_seg_circle, _pt_on_arc, _circumcircle,
    _offset_line, _offset_circle, _offset_polyline,
)
import cad.viewport as _vp
from cad.viewport import SCALE_MIN, SCALE_MAX

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

try:
    from PIL import ImageTk as _PILImageTk
    _PIL_IMAGEtk_OK = True
except ImportError:
    _PILImageTk = None          # type: ignore
    _PIL_IMAGEtk_OK = False

from cad.renderer_pil import RendererPIL, RenderCtx, build_layer_cache, resolve_entity_props
from cad.renderer_base import select_renderer
from cad.layout import (
    LayoutSheet, ViewportDef, PAPER_SIZES_MM,
    paper_size, default_viewport, auto_fit_viewport,
    layout_to_dict, layout_from_dict, render_layout_pil,
    VIEWPORT_SCALES, TB_HEIGHT_MM,
    get_scales, METRIC_SCALES, IMPERIAL_SCALES,
)

# ─── Paleta UI ────────────────────────────────────────────────────
UI_BG    = "#0F172A"
UI_PAN   = "#1E293B"
UI_CARD  = "#334155"
UI_TEXT  = "#F8FAFC"
UI_TEXT2 = "#94A3B8"
UI_BORD  = "#475569"
UI_ACC   = "#2563EB"
UI_SUCC  = "#16A34A"
UI_WARN  = "#EAB308"
UI_ERR   = "#DC2626"

# ─── Paleta canvas ────────────────────────────────────────────────
CV_BG        = "#0A0A0A"
CV_GRID      = "#1A1A1A"
CV_GRID_MAJ  = "#282828"
CV_AXIS      = "#333333"
CV_SNAP_END  = "#FFD700"
CV_SNAP_MID  = "#00FFFF"
CV_SNAP_CEN  = "#FF00FF"
CV_SNAP_QUA  = "#FF8000"
CV_PREVIEW   = "#2563EB"
CV_SELECT    = "#FFD700"
CV_WIN_BOX   = "#0044CC"     # ventana selección izq→der
CV_CRS_BOX   = "#007744"     # cruce selección der→izq
CV_CMD_FG    = "#00FF41"
CV_GHOST     = "#445566"     # entidades "fantasma" en MOVE/COPY preview

# Color por tipo de snap (compartido entre cursor y marcador)
_SNAP_COLORS: dict[str, str] = {
    "end": CV_SNAP_END,   # amarillo
    "mid": CV_SNAP_MID,   # cian
    "cen": CV_SNAP_CEN,   # magenta
    "qua": CV_SNAP_QUA,   # naranja
    "int": "#F59E0B",     # ámbar
    "per": "#38BDF8",     # celeste
    "tan": "#A78BFA",     # violeta
    "nea": "#FB923C",     # naranja claro
    "gri": "#6EE7B7",     # verde menta
}

SNAP_PX    = 18
HOVER_PX   = 10        # píxeles máximos para hover-highlight de entidad
GRID_MAJOR = 1.0
GRID_MINOR = 0.25
# _SNAP_CELL, _LOD_TEXT_PX, _LOD_DOT_SCALE → importadas de cad.entities
# FPS — throttle del mouse move
_MOVE_MS      = 33   # ~30 fps  (era 8 ms = 125 fps)


# Entity, Line, Polyline, Circle, Text, Arc, Layer, DEFAULT_LAYERS,
# _LINETYPES, _LT_CYCLE, _LT_ABBR → importadas de cad.entities (línea 29)

# ─── Alias completos compatibles con AutoCAD ─────────────────────
# Mapeados a nombre interno de herramienta o acción
_CMD_ALIASES: dict[str, str] = {
    # Herramientas de dibujo
    "L": "line",          "LINE": "line",        "LINEA": "line",
    "PL": "polyline",     "PLINE": "polyline",   "POLILINEA": "polyline",
    "REC": "rect",        "RECTANG": "rect",     "RECTANGLE": "rect",
    "C": "circle",        "CIRCLE": "circle",    "CIRCULO": "circle",
    "A": "arc",           "ARC": "arc",          "ARCO": "arc",
    "T": "text",          "TEXT": "text",        "TEXTO": "text",
    "DT": "text",         "DTEXT": "text",       "MT": "text",
    "SPL": "spline",      "SPLINE": "spline",    "CURVA": "spline",
    # Selección / Edición básica
    "S": "select",        "SE": "select",
    "E": "erase",         "ERASE": "erase",      "BORRAR": "erase",
    "DEL": "erase",       "DELETE": "erase",
    "M": "move",          "MOVE": "move",        "MOVER": "move",
    "CO": "copy",         "CP": "copy",          "COPY": "copy",
    "COPIAR": "copy",
    "RO": "rotate",       "ROTATE": "rotate",    "ROTAR": "rotate",
    "SC": "scale",        "SCALE": "scale",      "ESCALA": "scale",
    "MI": "mirror",       "MIRROR": "mirror",    "ESPEJO": "mirror",
    "O": "offset",        "OFFSET": "offset",
    "TR": "trim",         "TRIM": "trim",        "RECORTAR": "trim",
    "EX": "extend",       "EXTEND": "extend",    "EXTENDER": "extend",
    "F": "fillet",        "FI": "fillet",        "FILLET": "fillet",   "EMPALME": "fillet",
    "X": "explode",       "EXPLODE": "explode",
    # Vista / Zoom
    "ZE": "zoom_e",       "ZA": "zoom_a",
    "Z": "zoom_cmd",      "ZOOM": "zoom_cmd",
    "LTSCALE": "ltscale", "LTS": "ltscale",
    "R": "regen",         "RE": "regen",
    "REGEN": "regen",     "REDRAW": "regen",
    "PAN": "pan_cmd",
    # Undo / Redo
    "U": "undo",          "UNDO": "undo",        "DESHACER": "undo",
    "REDO": "redo",       "REHACER": "redo",
    # Capas / Propiedades
    "LA": "layer_cmd",    "LAYER": "layer_cmd",  "CAPA": "layer_cmd",
    "LAYISO": "layer_iso", "LAYOFF": "layer_off", "LAYON": "layer_on",
    "LAYLOCK": "layer_lock", "LAYULK": "layer_unlock",
    "LAYUNLOCK": "layer_unlock", "LAYUL": "layer_unlock",
    "LAYMCUR": "layer_mcur", "LCUR": "layer_mcur",
    "PR": "properties",   "MO": "properties",    "CH": "properties",
    "PROPERTIES": "properties",
    "MA": "matchprop",    "MATCHPROP": "matchprop",
    # Medición / Información
    "DI": "dist",         "DIST": "dist",        "DISTANCE": "dist",
    "DISTANCIA": "dist",
    "LI": "list_ent",     "LS": "list_ent",      "LIST": "list_ent",
    "LISTA": "list_ent",
    "ID": "id_point",     "AREA": "area_cmd",
    # Cotas
    "DH": "dim_h",        "DIMH": "dim_h",       "DIMHOR": "dim_h",
    "DV": "dim_v",        "DIMV": "dim_v",       "DIMVER": "dim_v",
    "DA": "dim_a",        "DIMA": "dim_a",       "DIMAL": "dim_a",
    "DAN": "dim_ang",     "DIMANG": "dim_ang",
    "DR": "dim_r",        "DIMR": "dim_r",       "DIMRAD": "dim_r",
    "DCO": "dim_co",      "DIMCO": "dim_co",     "DIMCONT": "dim_co",
    "DBA": "dim_ba",      "DIMBA": "dim_ba",     "DIMBASE": "dim_ba",
    "DSP": "dim_sp",      "DIMSP": "dim_sp",     "DIMSPACE": "dim_sp",
    "DD":  "dim_d",       "DIMD": "dim_d",       "DIMDIA": "dim_d",   "DIMDIAMETER": "dim_d",
    "DAR": "dim_arc_len", "DIMARC": "dim_arc_len",
    "DOR": "dim_ord",     "DIMORD": "dim_ord",   "DIMORDINATE": "dim_ord",
    # Hatch / Relleno
    "BH": "hatch",          "HATCH": "hatch",      "BHATCH": "hatch",
    "RELLENO": "hatch",
    # Bloques
    "I": "insert",          "INSERT": "insert",    "INSERTAR": "insert",
    "IMG": "image_cmd",     "IMAGE": "image_cmd",  "IMAGEN": "image_cmd",
    "BLOCK": "block_cmd",   "BLOQUE": "block_cmd", "B": "block_cmd",
    "WBLOCK": "block_cmd",
    "EATTEDIT": "eattedit", "EA": "eattedit",      "ATTEDIT": "eattedit",
    # Guardar / Abrir
    "SAVE": "save",       "GUARDAR": "save",     "QSAVE": "save",
    "SAVEAS": "saveas",   "GUARDARCOMO": "saveas",
    "OPEN": "open",       "ABRIR": "open",
    "NEW": "new_dwg",     "NUEVO": "new_dwg",
    # Export
    "DXF": "dxf",         "PNG": "png",        "PDF": "pdf",
    # Layout / Paper Space
    "LAY":    "layout_new",  "LAYOUT":  "layout_new",  "LAMINA": "layout_new",
    "MODELO": "layout_model","MODEL":   "layout_model",
    "MSPACE": "layout_model","MS":      "layout_model",
    "PSPACE": "layout_ps",   "PS":      "layout_ps",
    "PAGESETUP": "layout_setup",
    # Config
    "GRID": "toggle_grid",   "GRILLA": "toggle_grid",
    "SNAP": "toggle_snap",
    "ORTHO": "toggle_ortho", "ORTO": "toggle_ortho",
    "OSNAP": "toggle_snap",
    # Ayuda
    "?": "help",          "HELP": "help",        "AYUDA": "help",
    # Nuevas herramientas y operaciones
    "EL": "ellipse",      "ELLIPSE": "ellipse",  "ELIPSE": "ellipse",
    "POL": "polygon",     "POLYGON": "polygon",  "POLIGONO": "polygon",
    "XL": "xline",        "XLINE": "xline",
    "AR": "array",        "ARRAY": "array",      "ARREGLO": "array",
    "CHA": "chamfer",     "CHAMFER": "chamfer",  "CHAFLAN": "chamfer",
    "AL": "align_cmd",    "ALIGN": "align_cmd",  "ALINEAR": "align_cmd",
    "BR": "break_cmd",    "BREAK": "break_cmd",  "PARTIR": "break_cmd",
    "MEA": "measure",     "MEASURE": "measure",  "MEDIR": "measure",
    "LD": "leader_cmd",   "LEADER": "leader_cmd","LIDER": "leader_cmd",
    "CL": "cloud",        "CLOUD": "cloud",      "NUBE": "cloud",
    "LC": "layer_mcur",
    # Scroll / Zoom
    "SS": "scrollspeed", "SCROLLSPEED": "scrollspeed", "VELSCROLL": "scrollspeed",
    # SLE — Spatial Learning Engine
    "SLECORR": "slecorr", "SLC": "slecorr", "APRENDER": "slecorr",
    # Íconos de menú
    "MENUICONS": "menu_icons_toggle", "MIC": "menu_icons_toggle",
}


# ─── Descripciones para preview semántico de la barra ────────────
# Keyed by action value (el valor de _CMD_ALIASES), no por alias
_CMD_DESCRIPTIONS: dict[str, str] = {
    "line":          "LINE — dibuja segmentos; clic = punto, Enter = finalizar",
    "polyline":      "PLINE — polilínea continua; Enter = cerrar figura",
    "spline":        "SPLINE — curva Catmull-Rom; clic = pts control, Enter = finalizar",
    "rect":          "RECTANG — rectángulo por dos esquinas opuestas",
    "circle":        "CIRCLE — círculo por centro y radio",
    "arc":           "ARC — arco por tres puntos",
    "text":          "TEXT — inserta texto; clic = posición de inserción",
    "select":        "SELECT — selecciona entidades con clic o ventana",
    "erase":         "ERASE — elimina entidades seleccionadas",
    "move":          "MOVE — mueve; punto base → punto destino",
    "copy":          "COPY — copia; punto base → punto destino",
    "rotate":        "ROTATE — rota entidades; punto base → ángulo",
    "scale":         "SCALE — escala; punto base → factor numérico",
    "mirror":        "MIRROR — espeja respecto a eje de dos puntos",
    "offset":        "OFFSET — copia paralela; escribe distancia → clic lado",
    "trim":          "TRIM — 1 clic en el segmento a eliminar (auto-detecta bordes)",
    "extend":        "EXTEND — 1 clic cerca del extremo a extender (auto-detecta límite)",
    "explode":       "EXPLODE — descompone polilínea en segmentos individuales",
    "fillet":        "FILLET [F] — empalme entre 2 líneas · R=radio (0=esquina viva)",
    "chamfer":       "CHAMFER — chaflán entre dos líneas",
    "dist":          "DISTANCE — mide distancia entre dos puntos",
    "list_ent":      "LIST — muestra propiedades de entidades seleccionadas",
    "id_point":      "ID — informa coordenadas del punto indicado",
    "area_cmd":      "AREA — calcula área de polígono definido por puntos",
    "undo":          "UNDO — deshace la última acción  [Ctrl+Z]",
    "redo":          "REDO — rehace la acción deshecha  [Ctrl+Y]",
    "zoom_e":        "ZOOM EXTENTS — ajusta vista a todas las entidades",
    "zoom_a":        "ZOOM ALL — vista completa del dibujo",
    "zoom_cmd":      "ZOOM — Z E extents · Z W ventana · rueda = acercar/alejar",
    "regen":         "REGEN — regenera la visualización del dibujo",
    "pan_cmd":       "PAN — desplaza vista; clic y arrastra (o botón central)",
    "layer_cmd":     "LAYER — abre el gestor de capas",
    "layer_iso":     "LAYISO — aísla la capa de la entidad que selecciones",
    "layer_off":     "LAYOFF — apaga la capa de la entidad seleccionada",
    "layer_on":      "LAYON — hace visibles todas las capas apagadas",
    "layer_lock":    "LAYLOCK — bloquea la capa de la entidad seleccionada",
    "layer_unlock":  "LAYULK — desbloquea la capa de la entidad seleccionada",
    "layer_mcur":    "LAYMCUR — convierte en activa la capa de la entidad",
    "properties":    "PROPERTIES — edita propiedades de la entidad seleccionada",
    "matchprop":     "MATCHPROP — copia propiedades de una entidad a otras",
    "save":          "SAVE — guarda el archivo actual  [Ctrl+S]",
    "saveas":        "SAVEAS — guarda con nuevo nombre o formato",
    "open":          "OPEN — abre un archivo DWG / DXF",
    "new_dwg":       "NEW — nuevo dibujo en blanco",
    "dxf":           "DXF — exporta el dibujo en formato DXF",
    "png":           "PNG — exporta imagen PNG de la vista actual",
    "toggle_grid":   "GRID — activa/desactiva grilla de referencia  [F7]",
    "toggle_snap":   "SNAP — activa/desactiva snap de objeto  [F3]",
    "toggle_ortho":  "ORTHO — activa/desactiva modo ortogonal  [F8]",
    "help":          "HELP — muestra lista de comandos disponibles",
    # Layout
    "ltscale":       "LTSCALE / LTS — escala global de tipos de línea (DASHED, CENTER…)",
    "layout_new":    "LAY — nueva lámina de papel (paper space)",
    "layout_model":  "MS / MODELO — volver al espacio modelo",
    "layout_ps":     "PS / PSPACE — activar espacio papel",
    "layout_setup":  "PAGESETUP — configurar lámina activa (papel, escala)",
    # Cotas
    "hatch":   "HATCH/BH — sombreado; elige patrón → clic puntos → Enter para cerrar",
    "insert":  "INSERT/I — insertar bloque; elige de la biblioteca → clic para colocar",
    "block_cmd": "BLOCK/B — crear bloque desde selección → nombre → base point",
    "dim_h":   "DIMH — cota horizontal; clic P1, P2, posición línea cota",
    "dim_v":   "DIMV — cota vertical; clic P1, P2, posición línea cota",
    "dim_a":   "DIMA — cota alineada; clic P1, P2, posición línea cota",
    "dim_ang": "DIMANG — cota angular; clic centro, primer eje, segundo eje",
    "dim_co":      "DIMCO — cota continua; encadena desde la última (ESC=fin)",
    "dim_ba":      "DIMBA — cota base; varias cotas desde el mismo origen (ESC=fin)",
    "dim_sp":      "DIMSP — espaciado uniforme entre cotas paralelas seleccionadas",
    "dim_d":       "DD — cota diámetro (Ø); clic en círculo o en centro+borde",
    "dim_arc_len": "DAR — longitud de arco; clic en arco, luego posición texto",
    "dim_ord":     "DOR — cota ordenada X/Y; clic en punto medido, luego extremo líder",
    "dim_r":   "DIMR — radio/diámetro; clic en círculo o clic centro+punto",
    "scrollspeed": "SCROLLSPEED — velocidad del scroll (1 lento … 10 rápido)  ej: SS 7",
    "slecorr": "SLECORR — registra correcciones de la planta en el SLE para aprendizaje",
    # Herramientas avanzadas (antes sin descripción)
    "ellipse":    "ELLIPSE — elipse por centro y semiejes",
    "polygon":    "POLYGON — polígono regular; número de lados → centro → radio",
    "xline":      "XLINE — línea de construcción infinita (raycast)",
    "array":      "ARRAY — arreglo [R]ectangular o [P]olar de entidades",
    "align_cmd":  "ALIGN — alinea objetos con pares de puntos fuente/destino",
    "break_cmd":  "BREAK — parte una entidad en dos segmentos",
    "measure":    "MEASURE — mide distancia acumulada entre puntos  (Esc = total)",
    "leader_cmd": "LEADER — flecha anotativa con texto  [LD]",
    "cloud":      "CLOUD — nube de revisión sobre área  [CL]",
    "image_cmd":  "IMAGE — inserta imagen rasterizada (PNG/JPG/BMP)  [IMG]",
    "eattedit":   "EATTEDIT — edita atributos de texto en bloque insertado  [EA]",
    "pdf":        "PDF — exporta lámina activa a archivo PDF",
    "laymcur":    "LAYMCUR — clic en entidad para convertir su capa en activa  [LC]",
    "menu_icons_toggle": "MENUICONS — activa/desactiva íconos en los menús desplegables",
}


# ═══════════════════════════════════════════════════════════════════
# PROMPTS PASO A PASO → traducciones en cad/i18n.py
# Lookup: TOOL_PROMPT_KEYS[(tool, n)] → clave i18n → t(clave) → texto traducido
#         OP_PROMPT_KEYS[op_mode]      → clave i18n → t(clave) → texto traducido
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# HELPERS GEOMÉTRICOS (snap dinámico)
# ═══════════════════════════════════════════════════════════════════

def _perp_foot_seg(px: float, py: float,
                   x1: float, y1: float, x2: float, y2: float):
    """Pie de perpendicular desde P al segmento AB (t ∈ [0,1]). None si infinito."""
    dx, dy = x2 - x1, y2 - y1
    dlen2 = dx * dx + dy * dy
    if dlen2 < 1e-16:
        return None
    t = ((px - x1) * dx + (py - y1) * dy) / dlen2
    t = max(0.0, min(1.0, t))
    return (x1 + t * dx, y1 + t * dy)


def _intersect_seg_seg(x1, y1, x2, y2, x3, y3, x4, y4):
    """Wrapper de _seg_intersect que devuelve solo (x, y) o None."""
    res = _seg_intersect(x1, y1, x2, y2, x3, y3, x4, y4)
    return (res[0], res[1]) if res else None
# _intersect_seg_circle, _pt_on_arc, _cell → importadas de cad.entities


# ═══════════════════════════════════════════════════════════════════
# TOOLTIP LIGERO
# ═══════════════════════════════════════════════════════════════════

def _is_light(hex_color: str) -> bool:
    """True si el color es claro (para elegir texto negro o blanco)."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
        return (r*299 + g*587 + b*114) / 1000 > 128
    except Exception:
        return False


class _NullWidget:
    """Stub silencioso para widgets que se reemplazaron en la UI."""
    def configure(self, **_): pass
    def pack(self, **_): pass


class _Tooltip:
    """Tooltip flotante para widgets CTk/Tk.  delay en ms."""

    def __init__(self, widget, text: str, delay: int = 500):
        self._w     = widget
        self._text  = text
        self._delay = delay
        self._job   = None
        self._tw    = None
        widget.bind("<Enter>",  self._enter, add="+")
        widget.bind("<Leave>",  self._leave, add="+")
        widget.bind("<Button>", self._leave, add="+")

    def _enter(self, _e=None):
        self._cancel()
        self._job = self._w.after(self._delay, self._show)

    def _leave(self, _e=None):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._job:
            self._w.after_cancel(self._job)
            self._job = None

    def _show(self):
        x = self._w.winfo_rootx() + self._w.winfo_width() + 6
        y = self._w.winfo_rooty() + max(0, (self._w.winfo_height() - 20) // 2)
        self._tw = tk.Toplevel(self._w)
        self._tw.wm_overrideredirect(True)
        self._tw.wm_geometry(f"+{x}+{y}")
        self._tw.wm_attributes("-topmost", True)
        tk.Label(
            self._tw, text=self._text, justify="left",
            background="#1E293B", foreground="#CBD5E1",
            relief="flat", font=("Courier New", 9),
            padx=7, pady=4,
        ).pack()

    def _hide(self):
        if self._tw:
            try:
                self._tw.destroy()
            except Exception:
                pass  # widget Tkinter ya destruido — ignorar
            self._tw = None


# ═══════════════════════════════════════════════════════════════════
# VENTANA PRINCIPAL
# ═══════════════════════════════════════════════════════════════════

class CADWindow:
    """Visor CAD con comandos compatibles con AutoCAD."""

    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title("Estudio Merlos CAD  v1")
        self.root.geometry("1440x900")
        self.root.minsize(1000, 640)
        self.root.configure(fg_color=UI_BG)

        # Canvas transform
        self.scale    = 40.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        # PIL render cache
        self._pil_photo      = None   # ImageTk.PhotoImage (mantener referencia)
        self._canvas_img_id  = None   # ID del item de imagen en el canvas
        # Renderer activo: PIL (default) u OpenGL según config['rendering']['backend']
        # select_renderer() lee el config y hace fallback a PIL si OpenGL no disponible
        self._renderer       = select_renderer(self._leer_config_ia())
        # Cuando el tessellator OpenGL termina, disparar un redraw para que
        # las entidades nuevas aparezcan sin esperar un evento de usuario.
        if hasattr(self._renderer, '_tess_done_cb'):
            self._renderer._tess_done_cb = lambda: self.root.after(0, self._redraw_static)

        # ── Render asíncrono PIL ──────────────────────────────────────
        # El render pesado corre en un hilo de fondo; solo paste() toca Tk.
        self._render_thread: Optional[threading.Thread] = None
        self._render_pending: bool = False   # hay un render scheduled/corriendo
        self._render_ctx_pending: object = None   # ctx para el próximo render
        self._render_cancel: threading.Event = threading.Event()  # señal de cancelación
        self._render_gen: int = 0            # generación del render activo
        self._render_t0:  float = 0.0        # timestamp inicio del render actual
        self._render_ms:  float = 0.0        # ms del último render completado
        self._fps_times:  list  = []         # timestamps de renders completados (rolling)
        self._sel_version: int  = 0          # incrementa al cambiar selección → invalida VBO cache
        self._config_cache: Optional[dict] = None   # cache de settings.json
        self._config_mtime: float          = 0.0    # mtime del archivo al leerlo

        self._pan_start:       Optional[tuple] = None
        self._pan_prev_ox:     float = 0.0
        self._pan_prev_oy:     float = 0.0
        self._pan_rendered_ox: float = 0.0
        self._pan_rendered_oy: float = 0.0
        self._pan_pending:     bool  = False
        self._zoom_prev: list[tuple] = []   # historial de vistas para ZOOM P

        # Zoom-preview: snapshot de la última imagen PIL renderizada
        self._pil_img_cache:   object = None   # PIL Image del último render completo
        self._pil_cache_scale: float  = 0.0    # scale del render que produjo el cache
        self._pil_cache_ox:    float  = 0.0    # offset_x del render que produjo el cache
        self._pil_cache_oy:    float  = 0.0    # offset_y del render que produjo el cache

        # Entidades y capas
        self.entities: list[Entity] = []
        self.layers: dict[str, Layer] = {
            k: Layer(**{f.name: getattr(v, f.name)
                        for f in v.__dataclass_fields__.values()})
            for k, v in DEFAULT_LAYERS.items()
        }
        self.active_layer = "0"

        # ── Velocidad de scroll ───────────────────────────────────────
        # Rango 1-10; default 5 → factor base 1.40× por notch de rueda.
        # Fórmula: base = 1.0 + scroll_speed * 0.08
        self._scroll_speed: int = int(
            self._leer_config_ia().get("scroll_speed", 5))
        self._scroll_speed = max(1, min(10, self._scroll_speed))

        # ── SLE — Spatial Learning Engine ────────────────────────────
        self._sle_disponible  = False
        self._sle_memoria     = None
        self._sle_captura     = None
        self._sle_prompt_cache = ""   # último prompt de app.py (vía baseline file)
        try:
            from sle.core.memory import Memoria as _SLEMem
            from sle.integration.correction_capture import CapturaCorrecciones as _CC
            self._sle_memoria    = _SLEMem()
            self._sle_captura    = _CC(self._sle_memoria)
            self._sle_disponible = True
        except Exception:
            pass

        # Herramienta activa y dibujo
        self.tool       = "select"
        self.draw_pts:  list[tuple] = []
        self.selected:  list        = []
        self.mouse_w:   tuple = (0.0, 0.0)
        self.snap_pt:   Optional[tuple] = None
        self._hover_ent = None          # F1: entidad bajo el cursor
        self.ortho      = False
        self.snap_on    = True
        self.grid_on    = True

        # Dynamic Input (DYN) — captura de teclado en canvas durante dibujo
        self._dyn_buf:    str  = ""           # buffer del campo activo
        self._dyn_field:  int  = 0            # índice del campo activo
        self._dyn_locked: list = [None, None, None]  # hasta 3 valores bloqueados
        self._dyn_circ_mode: str = "r"        # "r" = radio · "d" = diámetro
        self.dyn_on:      bool = True          # activo por defecto  (F12 = toggle)

        # Tipos de snap habilitados (END/MID/CEN/QUA estáticos; INT/PER/TAN/NEA dinámicos)
        self._snap_types: dict[str, bool] = {
            "end": True, "mid": True, "cen": True, "qua": True,
            "int": True, "per": False, "tan": False, "nea": False, "gri": False,
        }

        # Undo / Redo
        self._undo_stack: list[list] = []
        self._redo_stack: list[list] = []

        # Ring buffer para panel 📊 Perf (últimos 60 frames)
        self._perf_ring: list = []   # lista de dicts con métricas por frame
        self._perf_ring_max = 60

        # Último resultado de export DXF verificado (para Health Monitor)
        # None = nunca exportado; [] = sin errores; [str,...] = errores encontrados
        self._last_roundtrip_result = None

        # Contador de CTkProgressBar activos (para Health Monitor, evita gc.get_objects)
        self._active_pbar_count: int = 0

        # ── Freeze Detector ──────────────────────────────────────────────
        import time as _t_fd, threading as _thr_fd
        self._watchdog_active   = False
        self._watchdog_ack_time = _t_fd.perf_counter()
        self._freeze_reported   = False
        self._native_dialog_open = False   # True mientras un filedialog nativo bloquea el hilo
        self._freeze_events: list = []
        self._freeze_lock       = _thr_fd.Lock()
        self._last_freeze_op    = ''
        self._refresh_freeze_panel = lambda: None   # set al construir el panel
        _fd_dir = os.path.dirname(os.path.abspath(__file__))
        self._freeze_log_path   = os.path.normpath(
            os.path.join(_fd_dir, '..', 'freeze_log.jsonl'))
        try:
            import json as _j_fd
            with open(self._freeze_log_path, 'r', encoding='utf-8') as _ff:
                for _ln in _ff:
                    _ln = _ln.strip()
                    if _ln:
                        self._freeze_events.append(_j_fd.loads(_ln))
            self._freeze_events = self._freeze_events[-50:]
        except Exception:
            pass

        # Throttle de mouse
        self._move_pending = False
        self._mouse_sx = 0
        self._mouse_sy = 0

        # Índice espacial de snap
        self._snap_index: dict = {}

        # Diccionario de definiciones de bloques (block instancing)
        self.block_defs: dict[str, BlockDef] = {}

        # Índice espacial de entidades (viewport culling O(k))
        self._entity_index: dict = {}
        self._entity_cell: float = _SNAP_CELL   # tamaño de celda adaptativo
        self._indexing: bool = False             # True mientras se construye en background

        # Último comando (para SPACE = repetir)
        self._last_cmd_name = ""
        self._ctx_popup_open = False  # True mientras el menú flotante está visible

        # Operación en curso — backing store para la propiedad _op_mode
        # (ver @property _op_mode más abajo; NUNCA asignar _op_mode_val directo)
        self._op_mode_val = ""       # "" | "move_sel"  | "move_base"  | "move_dest"
                                     #    | "copy_sel"  | "copy_base"  | "copy_dest"
                                     #    | "rotate_sel"| "rotate_base"| "rotate_angle"
                                     #    | "scale_sel" | "scale_base" | "scale_factor"
                                     #    | "mirror_sel"| "mirror_p1"  | "mirror_p2"
                                     #    | "offset_dist" | "offset_sel" | "offset_side"
                                     #    | "trim_obj"  | "extend_obj"
                                     #    | "fillet_p1" | "fillet_p2"
                                     #    | "matchprop_src" | "matchprop_dst"
                                     #    | "dist_p1"   | "dist_p2"
                                     #    | "zoom_w1"   | "zoom_w2"
                                     #    | "laymcur"
                                     #    | "dim_lp1"   | "dim_lp2"   | "dim_lpos"
                                     #    | "dim_r_obj" | "dim_r_pt"
                                     #    | "dim_arc_obj"| "dim_arc_pos"
                                     #    | "dim_ord_p1"| "dim_ord_p2"
                                     #    | "dim_ang_cen"| "dim_ang_p1"| "dim_ang_p2"
                                     #    | "dim_chain_next" | "dim_sp_pick"
                                     #    | "hatch_pts" | "insert_place" | "block_name"
        self._op_sel:   list = []    # entidades seleccionadas para operaciones
        self._op_pts:   list = []    # puntos acumulados para operación
        self._op_info   = ""         # texto de información para status
        self._op_data:  dict = {}    # datos auxiliares (base, eje, dist…)

        # ── Buffer de teclado en canvas (estilo AutoCAD) ───────────────
        # El usuario escribe abreviaturas directamente sobre el canvas;
        # se acumulan en _kbd_buf y se ejecutan con clic derecho o Enter.
        self._kbd_buf:  str  = ""    # texto acumulado (ej. "TR", "PL")
        self._kbd_buf_visible: bool = False   # mostrar overlay

        # Selección por arrastre
        self._drag_start_s: Optional[tuple] = None   # px de inicio del drag
        self._is_dragging   = False

        # ── Grips ──────────────────────────────────────────────────
        # Lista de puntos de agarre de entidades seleccionadas
        self._grips: list = []      # [{"wx","wy","eidx","gid"}, …]
        self._hot_grip: Optional[dict] = None   # grip activo (rojo)
        self._hover_grip: Optional[dict] = None # grip bajo el cursor (naranja, pre-clic)

        # Variables de estado para INSERT/BLOCK
        self._blk_selected  = tk.StringVar(value="")
        self._blk_scale_var = tk.StringVar(value="1.0")
        self._blk_angle_var = tk.StringVar(value="0")

        # Biblioteca externa de bloques (carpeta DWG/DXF)
        _cfg0 = self._leer_config_ia()
        self._block_lib_path: str = _cfg0.get("block_lib_path", "")
        # Caché: { ruta_archivo: BlockDef } — se llena bajo demanda
        self._block_lib_cache: dict[str, "BlockDef"] = {}
        # Índice escaneado: [ (categoria, nombre, ruta_abs) ]
        self._block_lib_index: list = []
        if self._block_lib_path and os.path.isdir(self._block_lib_path):
            self.root.after(800, self._escanear_biblioteca)
        self._grip_drag_mode: bool = False      # True durante arrastre de grip
        self._grip_just_activated: bool = False # True justo tras activar grip (1er clic)

        # Leer settings.json UNA SOLA VEZ (evita 3 lecturas de disco en __init__)
        _cfg = self._leer_config_ia()

        # Historial de comandos
        self._cmd_history: list[str] = list(_cfg.get("cmd_nav_history", []))
        self._hist_log:    list[dict] = list(_cfg.get("cmd_hist_log",   []))
        self._cmd_hist_idx = -1
        self._hist_save_pending = False   # debounce de guardado
        self._ia_streaming      = False   # True mientras la IA está generando
        self._echo_after_id = None
        self._current_ruta: str = ""    # última ruta de guardado (para Save rápido)
        self._dirty: bool = False       # True = hay cambios sin guardar

        # ── Auto-recuperación ante cierres inesperados ────────────────
        # Guarda un .recovery cada _AUTOSAVE_MS ms mientras haya entidades.
        # Al arrancar, si el archivo existe, ofrece restaurar.
        _ar_mins = int(_cfg.get("autosave_min", 3))
        _ar_on   = bool(_cfg.get("autosave_enabled", True))
        self._AUTOSAVE_MS  = _ar_mins * 60 * 1000
        self._autosave_enabled = _ar_on
        self._autosave_job = None             # ID del after() pendiente

        # Barra de comandos flotante — estado (se sobreescribe con config guardado)
        _cfg_cmd = _cfg.get("cmd_bar", {})
        self._cmd_floating: bool   = False   # se restaura con after() si estaba flotando
        self._cmd_float_frame      = None
        self._cmd_float_w: int     = int(_cfg_cmd.get("w", 620))
        self._cmd_float_h: int     = int(_cfg_cmd.get("h", 56))
        self._cmd_float_x: int     = int(_cfg_cmd.get("x", 0))   # 0 = centrar al abrir
        self._cmd_float_y: int     = int(_cfg_cmd.get("y", 0))
        self._cmd_was_floating: bool  = bool(_cfg_cmd.get("floating", False))
        self._cmd_was_expanded: bool  = bool(_cfg_cmd.get("expanded", False))
        self._drag_cmd_x0: int     = 0
        self._drag_cmd_y0: int     = 0
        self._drag_cmd_fx: int     = 0
        self._drag_cmd_fy: int     = 0
        self._resize_cmd_x0: int   = 0   # resize derecho
        self._resize_cmd_w0: int   = 0
        self._resize_top_y0: int   = 0   # resize superior
        self._resize_top_h0: int   = 0

        # CURSORSIZE — brazos del cursor (0-100, equivale a CURSORSIZE de AutoCAD)
        self.cursor_size: int = int(_cfg.get("cursor_size", 30))

        # ── Colores personalizables (Preferencias → Colores) ──────────────
        # cv_bg: fondo del canvas (default #1C1C1E — más claro que el negro puro
        #        para que los hatches sean visibles)
        self.cv_bg:      str = _cfg.get("cv_bg",      "#1C1C1E")
        self.cmd_bar_bg: str = _cfg.get("cmd_bar_bg", "#0D1117")
        # Colores de ejes, selección y grilla (configurables)
        self.axis_color:   str   = _cfg.get("axis_color",   CV_AXIS)
        self.select_color: str   = _cfg.get("select_color", CV_SELECT)
        self.grid_major:   float = float(_cfg.get("grid_major", 1.0))
        self.grid_minor:   float = float(_cfg.get("grid_minor", 0.25))
        # Colores del cursor: idle = sin comando activo; active = comando recibido
        self.cursor_color_idle:   str = _cfg.get("cursor_color_idle",   "#FFFFFF")
        self.cursor_color_active: str = _cfg.get("cursor_color_active", "#22D3EE")

        # Idioma — carga desde settings.json (aplica globalmente vía i18n)
        set_language(_cfg.get("language", "es"))

        # Parámetros persistentes entre usos del mismo comando
        self.fillet_radius:     float = 0.0   # R=0 = esquina viva (más útil para muros)
        self._offset_last_dist: float = 0.15  # default = espesor gypsum 0.15 m
        self.rclick_as_enter:   bool  = bool(_cfg.get("rclick_as_enter", True))
        # Nuevas herramientas
        self.chamfer_d1:        float = 0.10
        self.chamfer_d2:        float = 0.10
        self._polygon_sides:    int   = 6
        self._text_last_height:    float  = 0.20   # altura por defecto para TEXT
        self._measure_total:       float  = 0.0
        self._measure_last_pt:     object = None
        self._inline_editor_frame: object = None  # widget del editor inline de texto
        self.ghost_panels:      bool  = bool(_cfg.get("ghost_panels", False))
        # Comandos personalizados en el menú contextual (lista de accion-names)
        self._ctx_menu_cmds: list[str] = _cfg.get("context_menu_cmds", [])

        # Ghost panel state (place-overlay approach)
        self._ghost_overlays: list = []       # lista de dicts de estado por panel

        # ── Layout / Paper Space ─────────────────────────────────────────────
        # Lista de láminas (LayoutSheet).  -1 = espacio modelo activo.
        self.layouts: list = []                  # list[LayoutSheet]
        self.active_layout_idx: int = -1         # -1 = modelo
        self._saved_model_view: dict = {}        # scale/ox/oy guardados al entrar a layout
        self._layout_tab_frame = None            # referencia al frame de tabs

        self._build_ui()
        self._center_view()
        self._cargar_terreno_proyecto()   # pre-carga lote desde proyecto_activo
        self._push_undo()
        self._redraw()
        self._start_watchdog()

        # Bindings globales
        r = self.root
        r.bind("<Control-z>",       lambda e: self._undo())
        r.bind("<Control-Z>",       lambda e: self._undo())
        r.bind("<Control-y>",       lambda e: self._redo())
        r.bind("<Control-Y>",       lambda e: self._redo())
        r.bind("<Control-s>",       lambda e: self._guardar_json())
        r.bind("<Control-S>",       lambda e: self._guardar_json())
        r.bind("<Control-o>",       lambda e: self._abrir_json())
        r.bind("<Control-O>",       lambda e: self._abrir_json())
        r.bind("<Control-n>",       lambda e: self._new_dwg())
        r.bind("<Control-N>",       lambda e: self._new_dwg())
        r.bind("<Control-a>",       lambda e: self._select_all())
        r.bind("<Control-A>",       lambda e: self._select_all())
        r.bind("<F1>",              lambda e: self._mostrar_ayuda())
        r.bind("<F3>",              lambda e: self._toggle("snap"))
        r.bind("<F7>",              lambda e: self._toggle("grid"))
        r.bind("<F8>",              lambda e: self._toggle("ortho"))
        r.bind("<F9>",              lambda e: self._toggle("snap"))
        r.bind("<F12>",             lambda e: self._toggle_dyn())
        r.bind("<Escape>",          lambda e: self._cancelar())
        # DYN backup: captura dígitos desde cualquier widget cuando DYN está activo
        # (el canvas puede perder el foco en algunos entornos CTk)
        r.bind("<Key>",             self._on_root_key, add="+")
        # Al recuperar foco (ej. despertar de suspensión) limpiar cursor fantasma.
        # Se registra tanto en root como en canvas porque el evento llega a uno
        # u otro según el estado del OS al despertar.
        r.bind("<FocusIn>",         self._on_focus_restore, add="+")

        # Dar foco al canvas al arrancar para que los comandos de teclado
        # funcionen sin necesidad de hacer clic previo en el área modelo.
        self.root.after(200, self.canvas.focus_set)

        # Interceptar cierre de ventana (X del OS) con diálogo de guardado
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── Auto-recuperación ─────────────────────────────────────────
        # Verificar si quedó un archivo de recuperación de una sesión anterior
        self.root.after(400, self._check_recovery_file)
        # Arrancar el ciclo de guardado automático (si está habilitado en config)
        if self._autosave_enabled:
            self._autosave_job = self.root.after(self._AUTOSAVE_MS, self._autosave_tick)

    def _on_canvas_leave(self, event=None):
        """Mouse sale del canvas: borrar cursor fantasma inmediatamente."""
        try:
            self.canvas.delete("dy_c")
        except Exception:
            pass
        self._crs_ids = {}
        self.snap_pt = None

    def _on_focus_restore(self, event=None):
        """Limpia ítems de cursor obsoletos al recuperar foco (sleep/wake/alt-tab).
        Invalida el cache PIL para forzar un render completo — tras sleep/wake el
        back-buffer del SO puede haber descartado la imagen anterior.
        """
        try:
            self.canvas.delete("dy_c")
        except Exception:
            pass
        self._crs_ids = {}
        self.snap_pt = None
        self._pil_img_cache = None   # fuerza render limpio post-sleep/wake
        self._redraw_dynamic()
        self._redraw_static()        # re-renderiza fondo completo

    def _on_canvas_unmap(self):
        """Ventana minimizada: invalida el cache PIL para que al restaurar
        se haga un render completo (el SO puede haber descartado el back-buffer).
        """
        self._pil_img_cache = None

    def run(self):
        self.root.mainloop()

    # ─── Construcción UI ───────────────────────────────────────────
    # ─── Menú de barra nativo ────────────────────────────────────

    def _hub_lanzar(self, modulo: str):
        """Lanza un módulo del Hub Central como proceso independiente."""
        import sys
        import subprocess

        base = os.path.dirname(os.path.dirname(__file__))
        scripts = {
            "terreno":  (os.path.join(base, "terreno_app.py"),   []),
            "programa": (os.path.join(base, "dwg_to_sle.py"),    ["--programa"]),
            "diseno":   (os.path.join(base, "app.py"),           []),
            "dwg":      (os.path.join(base, "dwg_to_sle.py"),    []),
        }
        if modulo not in scripts:
            return
        script, args = scripts[modulo]
        if not os.path.exists(script):
            messagebox.showerror("Hub Central",
                                 f"No se encontró el módulo:\n{script}")
            return
        try:
            subprocess.Popen(
                [sys.executable, script] + args,
                cwd=base,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            messagebox.showerror("Hub Central", f"Error al lanzar módulo:\n{exc}")

    def _build_menubar(self):
        """Menú de barra completo — frame CTk personalizado + tk.Menu para desplegables.

        En Windows el fondo de la barra nativa (tk.Menu como menubar) lo controla
        el SO y no acepta colores; por eso usamos un CTkFrame como barra y abrimos
        cada menú con tk_popup() al hacer clic en los botones.
        """
        # ── Íconos Lucide-style para los menús (siempre activos) ─────────
        try:
            from cad.menu_icons import build_icons as _build_icons
            self._menu_icons = _build_icons()
        except Exception as _e:
            print(f"[menubar] íconos no disponibles: {_e}")
            self._menu_icons = {}

        def _ic(name):
            """Devuelve el PhotoImage del ícono o el blank si no existe."""
            return self._menu_icons.get(name) or self._menu_icons.get("blank")

        def _ea(a): return lambda: self._ejecutar_accion(a, "")
        def _st(t_): return lambda: self._set_tool(t_)
        def _cfg(tab): return lambda: self._open_config(tab)
        def _cmd(menu, label, accel=None, command=None, icon=None):
            kw: dict = {}
            if icon is not None:
                kw["image"]    = icon
                kw["compound"] = "left"
            if accel:
                menu.add_command(label=label, accelerator=accel,
                                 command=command, **kw)
            else:
                menu.add_command(label=label, command=command, **kw)

        # ── Paleta oscura integrada al tema del programa ──────────────
        _MB_BG   = "#1E293B"   # UI_PAN  — fondo barra y menús
        _MB_FG   = "#CBD5E1"   # texto base
        _MB_ABG  = "#2563EB"   # hover activo (azul acento)
        _MB_AFG  = "#FFFFFF"
        _MB_HBG  = "#334155"   # hover suave botones de la barra
        _MB_FONT = ("Segoe UI", 9)
        _BAR_H   = 32

        def _mk():
            """Crea un tk.Menu con el tema oscuro — padre root para que tk_popup funcione."""
            return tk.Menu(
                self.root, tearoff=0,
                bg=_MB_BG, fg=_MB_FG,
                activebackground=_MB_ABG, activeforeground=_MB_AFG,
                borderwidth=1, relief="solid",
                font=_MB_FONT,
            )

        # ── Frame que actúa como barra de menú ────────────────────────
        mb_bar = ctk.CTkFrame(
            self.root, fg_color=_MB_BG, corner_radius=0,
            height=_BAR_H, border_width=0,
        )
        mb_bar.pack(side="top", fill="x")
        mb_bar.pack_propagate(False)
        self._mb_bar  = mb_bar
        self._mb_btns: dict[str, ctk.CTkButton] = {}   # clave → botón (para i18n)

        _BTN_FONT = ctk.CTkFont("Segoe UI", 13)

        def _cascade(key: str, label: str, menu: tk.Menu):
            """Crea un botón en la barra que despliega el menú al hacer clic."""
            def _post(m=menu):
                try:
                    x = btn.winfo_rootx()
                    y = btn.winfo_rooty() + btn.winfo_height()
                    m.tk_popup(x, y)
                except Exception:
                    pass
            btn = ctk.CTkButton(
                mb_bar, text=label,
                fg_color="transparent", hover_color=_MB_HBG,
                text_color=_MB_FG, font=_BTN_FONT,
                height=_BAR_H - 2, width=0,
                corner_radius=3,
                command=_post,
            )
            btn.pack(side="left", padx=1, pady=1)
            self._mb_btns[key] = btn

        # ── Separador vertical decorativo ─────────────────────────────
        def _vsep():
            tk.Frame(mb_bar, bg="#2D3F55", width=1).pack(
                side="left", fill="y", pady=4, padx=2)

        # ══════════════════════════════════════════════════════════
        # ARCHIVO
        # ══════════════════════════════════════════════════════════
        fm = _mk()
        _cmd(fm, "Nuevo",               "Ctrl+N",   command=self._new_dwg,               icon=_ic("nuevo"))
        _cmd(fm, "Abrir…",              "Ctrl+O",   command=self._abrir_json,            icon=_ic("abrir"))
        _cmd(fm, "Importar DXF/DWG…",               command=self._importar_dxf,          icon=_ic("importar"))
        fm.add_separator()
        _cmd(fm, "Guardar",             "Ctrl+S",   command=self._guardar_json,          icon=_ic("guardar"))
        _cmd(fm, "Guardar como…",                   command=self._guardar_json_como,     icon=_ic("guardar_as"))
        fm.add_separator()
        _cmd(fm, "Exportar DXF…",                   command=self._exportar_dxf,          icon=_ic("exportar_dxf"))
        _cmd(fm, "Exportar DXF verificado…",        command=self._exportar_dxf_verificado, icon=_ic("exportar_dxf"))
        _cmd(fm, "Exportar PNG…",                   command=self._exportar_png,          icon=_ic("exportar_png"))
        _cmd(fm, "Exportar PDF (Layout)…",          command=self._exportar_pdf,          icon=_ic("exportar_pdf"))
        fm.add_separator()
        _cmd(fm, "Nueva lámina",        "LAY",      command=self._new_layout,            icon=_ic("lamina"))
        _cmd(fm, "Espacio modelo",      "MS",       command=self._switch_to_model,       icon=_ic("modelo"))
        _cmd(fm, "Espacio papel",       "PS",       command=_ea("layout_ps"),            icon=_ic("papel"))
        _cmd(fm, "Config. de página…",  "PAGESETUP",command=_ea("layout_setup"),         icon=_ic("pag_setup"))
        fm.add_separator()
        _cmd(fm, "Salir",                           command=self._on_close,              icon=_ic("salir"))
        _cascade("file", t("menu_file"), fm)

        # ══════════════════════════════════════════════════════════
        # DIBUJAR
        # ══════════════════════════════════════════════════════════
        dm = _mk()
        for lbl, accel, tool, ico in [
            ("Línea",            "L",    "line",     "linea"),
            ("Polilínea",        "PL",   "polyline", "polilinea"),
            ("Spline",           "SPL",  "spline",   "spline"),
            ("Rectángulo",       "REC",  "rect",     "rectangulo"),
            ("Círculo",          "C",    "circle",   "circulo"),
            ("Arco",             "A",    "arc",      "arco"),
            ("Elipse",           "EL",   "ellipse",  "elipse"),
            ("Polígono",         "POL",  "polygon",  "poligono"),
        ]:
            _cmd(dm, lbl, accel, command=_st(tool), icon=_ic(ico))
        dm.add_separator()
        for lbl, accel, tool, ico in [
            ("Texto",            "T",    "text",   "texto"),
            ("Líder",            "LD",   "leader", "lider"),
            ("Nube de revisión", "CL",   "cloud",  "nube"),
            ("Línea infinita",   "XL",   "xline",  "xline"),
        ]:
            _cmd(dm, lbl, accel, command=_st(tool), icon=_ic(ico))
        dm.add_separator()
        _cmd(dm, "Definir bloque",    "B",   command=_ea("block_cmd"), icon=_ic("bloque_def"))
        _cmd(dm, "Insertar bloque",   "I",   command=_ea("insert"),    icon=_ic("bloque_ins"))
        _cmd(dm, "Insertar imagen",   "IMG", command=_ea("image_cmd"), icon=_ic("imagen"))
        _cmd(dm, "Editar atributos…", "EA",  command=_ea("eattedit"),  icon=_ic("atributos"))
        dm.add_separator()
        _cmd(dm, "Relleno / Hatch",   "BH",  command=_ea("hatch"),    icon=_ic("hatch"))
        _cascade("draw", t("menu_draw"), dm)

        # ══════════════════════════════════════════════════════════
        # MODIFICAR
        # ══════════════════════════════════════════════════════════
        em = _mk()
        _cmd(em, "Deshacer",         "Ctrl+Z", command=self._undo,        icon=_ic("deshacer"))
        _cmd(em, "Rehacer",          "Ctrl+Y", command=self._redo,        icon=_ic("rehacer"))
        em.add_separator()
        _cmd(em, "Seleccionar todo", "Ctrl+A", command=self._select_all,  icon=_ic("sel_todo"))
        _cmd(em, "Borrar",           "E",      command=_ea("erase"),      icon=_ic("borrar"))
        em.add_separator()
        for lbl, accel, accion, ico in [
            ("Mover",              "M",  "move",      "mover"),
            ("Copiar",             "CO", "copy",      "copiar"),
            ("Rotar",              "RO", "rotate",    "rotar"),
            ("Escalar",            "SC", "scale",     "escalar"),
            ("Espejo",             "MI", "mirror",    "espejo"),
            ("Desplazar paralelo", "O",  "offset",    "offset"),
            ("Alinear",            "AL", "align_cmd", "alinear"),
        ]:
            _cmd(em, lbl, accel, command=_ea(accion), icon=_ic(ico))
        em.add_separator()
        _cmd(em, "Arreglo (Array)", "AR", command=_ea("array"), icon=_ic("array"))
        em.add_separator()
        for lbl, accel, accion, ico in [
            ("Recortar", "TR",  "trim",      "recortar"),
            ("Extender", "EX",  "extend",    "extender"),
            ("Empalme",  "F",   "fillet",    "fillet"),
            ("Chaflán",  "CHA", "chamfer",   "chamfer"),
            ("Partir",   "BR",  "break_cmd", "partir"),
            ("Explotar", "X",   "explode",   "explotar"),
        ]:
            _cmd(em, lbl, accel, command=_ea(accion), icon=_ic(ico))
        em.add_separator()
        _cmd(em, "Propiedades",        "PR", command=_ea("properties"), icon=_ic("propiedades"))
        _cmd(em, "Copiar propiedades", "MA", command=_ea("matchprop"),  icon=_ic("matchprop"))
        _cascade("edit", t("menu_edit"), em)

        _vsep()

        # ══════════════════════════════════════════════════════════
        # COTAS
        # ══════════════════════════════════════════════════════════
        cotm = _mk()
        for lbl, accel, accion, ico in [
            ("Horizontal",       "DH",  "dim_h",       "dim_h"),
            ("Vertical",         "DV",  "dim_v",       "dim_v"),
            ("Alineada",         "DA",  "dim_a",       "dim_a"),
            ("Angular",          "DAN", "dim_ang",     "dim_ang"),
            ("Radio",            "DR",  "dim_r",       "dim_r"),
            ("Diámetro",         "DD",  "dim_d",       "dim_d"),
            ("Longitud de arco", "DAR", "dim_arc_len", "dim_arc"),
        ]:
            _cmd(cotm, lbl, accel, command=_ea(accion), icon=_ic(ico))
        cotm.add_separator()
        for lbl, accel, accion, ico in [
            ("Continua",      "DCO", "dim_co",  "dim_co"),
            ("En línea base", "DBA", "dim_ba",  "dim_ba"),
            ("Espaciado",     "DSP", "dim_sp",  "dim_sp"),
            ("Ordenada",      "DOR", "dim_ord", "dim_ord"),
        ]:
            _cmd(cotm, lbl, accel, command=_ea(accion), icon=_ic(ico))
        cotm.add_separator()
        _cmd(cotm, "⚙  Estilo de cotas…", command=_cfg("📐 Cotas"), icon=_ic("dim_cfg"))
        _cascade("cotas", "Cotas", cotm)

        # ══════════════════════════════════════════════════════════
        # CAPAS
        # ══════════════════════════════════════════════════════════
        lym = _mk()
        _cmd(lym, "Gestor de capas",       "LA",      command=_ea("layer_cmd"),    icon=_ic("capas"))
        _cmd(lym, "Nueva capa…",                      command=self._nueva_capa,    icon=_ic("nueva_capa"))
        lym.add_separator()
        _cmd(lym, "Aislar capa",           "LAYISO",  command=self._layiso,        icon=_ic("layiso"))
        _cmd(lym, "Apagar capa",           "LAYOFF",  command=_ea("layer_off"),    icon=_ic("layoff"))
        _cmd(lym, "Encender todas",        "LAYON",   command=self._layon,         icon=_ic("layon"))
        _cmd(lym, "Bloquear capa",         "LAYLOCK", command=_ea("layer_lock"),   icon=_ic("laylock"))
        _cmd(lym, "Desbloquear capa",      "LAYULK",  command=_ea("layer_unlock"), icon=_ic("layulk"))
        lym.add_separator()
        _cmd(lym, "Capa actual desde obj", "LC",      command=_ea("laymcur"),      icon=_ic("laymcur"))
        _cascade("layers", t("menu_layers"), lym)

        # ══════════════════════════════════════════════════════════
        # MEDIR
        # ══════════════════════════════════════════════════════════
        mm = _mk()
        _cmd(mm, "Distancia",       "DI",   command=_ea("dist"),      icon=_ic("dist"))
        _cmd(mm, "Medir segmentos", "MEA",  command=_ea("measure"),   icon=_ic("measure"))
        _cmd(mm, "Área",            "AREA", command=_ea("area_cmd"),  icon=_ic("area"))
        _cmd(mm, "ID Punto",        "ID",   command=_ea("id_point"),  icon=_ic("id_pt"))
        mm.add_separator()
        _cmd(mm, "Listar entidad",  "LI",   command=_ea("list_ent"),  icon=_ic("list_ent"))
        _cascade("measure", t("menu_measure"), mm)

        # ══════════════════════════════════════════════════════════
        # VER
        # ══════════════════════════════════════════════════════════
        vm = _mk()
        _cmd(vm, "Zoom extensión",       "ZE",  command=self._zoom_extents,                            icon=_ic("zoom_e"))
        _cmd(vm, "Zoom todo",            "ZA",  command=_ea("zoom_a"),                                 icon=_ic("zoom_a"))
        _cmd(vm, "Zoom ventana",         "Z W", command=lambda: self._ejecutar_accion("zoom_cmd","W"), icon=_ic("zoom_w"))
        _cmd(vm, "Zoom anterior",        "ZP",  command=lambda: self._ejecutar_accion("zoom_cmd","P"), icon=_ic("zoom_p"))
        _cmd(vm, "Escala viewport XP…",  "Z",   command=lambda: self._ejecutar_accion("zoom_cmd",""), icon=_ic("zoom_xp"))
        _cmd(vm, "Regenerar",            "RE",  command=_ea("regen"),                                  icon=_ic("regen"))
        vm.add_separator()
        _cmd(vm, "Encuadrar",            "PAN", command=_ea("pan_cmd"),       icon=_ic("pan"))
        _cmd(vm, "Velocidad scroll…",    "SS",  command=_ea("scrollspeed"),   icon=_ic("scroll"))
        vm.add_separator()
        _cmd(vm, "SNAP on/off",  "F3",  command=lambda: self._toggle("snap"),  icon=_ic("snap"))
        _cmd(vm, "GRID on/off",  "F7",  command=lambda: self._toggle("grid"),  icon=_ic("grid"))
        _cmd(vm, "ORTHO on/off", "F8",  command=lambda: self._toggle("ortho"), icon=_ic("ortho"))
        _cmd(vm, "DYN on/off",   "F12", command=self._toggle_dyn,             icon=_ic("dyn"))
        vm.add_separator()
        _cmd(vm, "Escala tipos de línea…", "LTS", command=self._ltscale_dialog,        icon=_ic("lts"))
        vm.add_separator()
        _cmd(vm, "Preferencias de color…",        command=self._preferencias_colores,  icon=_ic("colores"))
        _cascade("view", t("menu_view"), vm)

        _vsep()

        # ══════════════════════════════════════════════════════════
        # CONFIGURACIÓN  (por temas → abre tab específico)
        # ══════════════════════════════════════════════════════════
        cfgm = _mk()
        _cmd(cfgm, "General  (cursor, unidades…)", command=_cfg("🖱 General"), icon=_ic("cfg_general"))
        _cmd(cfgm, "Cotas  (estilo, escala…)",      command=_cfg("📐 Cotas"),  icon=_ic("cfg_cotas"))
        _cmd(cfgm, "Texto  (fuente, altura…)",       command=_cfg("🖊 Texto"),  icon=_ic("cfg_texto"))
        _cmd(cfgm, "Visual  (colores, fondo…)",      command=_cfg("🎨 Visual"), icon=_ic("cfg_visual"))
        _cmd(cfgm, "IA  (modelo, API key…)",         command=_cfg("🤖 IA"),    icon=_ic("cfg_ia"))
        cfgm.add_separator()
        _cmd(cfgm, "Personalizar menú contextual…",  command=self._abrir_editor_menu_contextual, icon=_ic("menu_ctx"))
        _cascade("config", "⚙ Config", cfgm)

        # ══════════════════════════════════════════════════════════
        # IA
        # ══════════════════════════════════════════════════════════
        iam = _mk()
        _cmd(iam, "Abrir asistente",         "/",   command=self._focus_ia_input, icon=_ic("ia_chat"))
        iam.add_separator()
        _cmd(iam, "Registrar corrección SLE","SLC", command=_ea("slecorr"),       icon=_ic("sle"))
        iam.add_separator()
        _cmd(iam, "Configurar IA…",                 command=_cfg("🤖 IA"),        icon=_ic("cfg_ia"))
        _cascade("ia", "🤖 IA", iam)

        # ══════════════════════════════════════════════════════════
        # HUB CENTRAL
        # ══════════════════════════════════════════════════════════
        hcm = _mk()
        _cmd(hcm, "Terreno del Lote",       command=lambda: self._hub_lanzar("terreno"), icon=_ic("terreno"))
        _cmd(hcm, "Programa Arquitectónico",command=lambda: self._hub_lanzar("programa"),icon=_ic("programa"))
        _cmd(hcm, "Diseño IA",              command=lambda: self._hub_lanzar("diseno"),  icon=_ic("diseno"))
        hcm.add_separator()
        _cmd(hcm, "DWG Extractor",          command=lambda: self._hub_lanzar("dwg"),     icon=_ic("dwg_ext"))
        _cascade("hub", "⬡ Hub", hcm)

        _vsep()

        # ══════════════════════════════════════════════════════════
        # AYUDA
        # ══════════════════════════════════════════════════════════
        hm = _mk()
        _cmd(hm, "Atajos de teclado", "F1", command=self._mostrar_ayuda, icon=_ic("atajos"))
        _cascade("help", "Ayuda", hm)

    def _rebuild_menubar(self):
        """Destruye y reconstruye la barra de menú con la configuración actual.

        Usado al cambiar configuración de íconos en vivo sin reiniciar.
        Al reconstruir, el nuevo frame se reposiciona antes del topbar porque
        pack(side='top') lo dejaría debajo de todos los widgets ya empacados.
        """
        if hasattr(self, "_mb_bar"):
            try:
                self._mb_bar.destroy()
            except Exception:
                pass
        self._build_menubar()
        # Reposicionar: la nueva barra queda al final del orden pack → moverla
        # antes del topbar para que vuelva a la fila superior correcta.
        if hasattr(self, "_topbar_frame"):
            try:
                self._mb_bar.pack_configure(before=self._topbar_frame)
            except Exception:
                pass

    # ── Infraestructura Interface 2 (íconos en barras y paneles) ─────────

    def _ctk_ico(self, name: str, size: int = 18):
        """Retorna (y cachea) un CTkImage para `name`. None si no existe."""
        key = f"{name}_{size}"
        if key in self._ctk_icon_cache:
            return self._ctk_icon_cache[key]
        pil = self._pil_icons.get(name)
        if pil is None:
            return None
        img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
        self._ctk_icon_cache[key] = img
        return img

    def _reg_btn(self, btn, icon_key: str, label: str, size: int = 18):
        """Registra un botón en el sistema de íconos para `_apply_icon_mode()`."""
        self._iconable_btns.append((btn, icon_key, label, size))

    def _apply_icon_mode(self, icon_on=None):
        """Reconfigura todos los botones registrados según modo icono."""
        if icon_on is None:
            icon_on = bool(self._leer_config_ia().get("menu_icons", False))
        for btn, icon_key, label, size in self._iconable_btns:
            try:
                if icon_on:
                    img = self._ctk_ico(icon_key, size)
                    if img:
                        btn.configure(image=img, compound="left", text=f" {label}")
                    else:
                        btn.configure(image="", compound="left", text=label)
                else:
                    btn.configure(image="", compound="left", text=label)
            except Exception:
                pass

    def _toggle_menu_icons(self):
        """Alterna íconos de interfaz on/off y persiste el setting."""
        cfg = self._leer_config_ia()
        nuevo_valor = not cfg.get("menu_icons", False)
        self._save_cfg_key("menu_icons", nuevo_valor)
        self._apply_icon_mode(nuevo_valor)
        estado = "activados" if nuevo_valor else "desactivados"
        self._echo(f"Íconos de interfaz {estado}")

    def _apply_language(self):
        """Aplica el idioma activo a todos los elementos traducibles de la UI en vivo."""
        # ── Botones de la barra de menú personalizada ─────────────────
        if hasattr(self, "_mb_btns"):
            _lbl_map = {
                "file":    t("menu_file"),
                "draw":    t("menu_draw"),
                "edit":    t("menu_edit"),
                "layers":  t("menu_layers"),
                "measure": t("menu_measure"),
                "view":    t("menu_view"),
            }
            for key, lbl in _lbl_map.items():
                btn = self._mb_btns.get(key)
                if btn:
                    try: btn.configure(text=lbl)
                    except Exception: pass
        # ── Toggles de status bar ─────────────────────────────────────
        if hasattr(self, "_btn_snap"):
            self._btn_snap.configure(text=t("btn_snap"))
            self._btn_grid.configure(text=t("btn_grid"))
            self._btn_ortho.configure(text=t("btn_ortho"))
        # ── Prompt de la barra de comando ─────────────────────────────
        self._update_prompt()

    def _build_ui(self):
        self._tool_btns: dict[str, ctk.CTkButton] = {}
        # ── Infraestructura de íconos (Interface 2) ───────────────────
        self._iconable_btns: list  = []   # (btn, icon_key, label, size)
        self._ctk_icon_cache: dict = {}   # CTkImage refs — anti-GC
        self._pil_icons: dict      = {}   # PIL Images base
        try:
            from cad.menu_icons import build_icons_pil as _bip
            self._pil_icons = _bip()
        except Exception as _bip_e:
            print(f"[build_ui] íconos PIL no disponibles: {_bip_e}")
        self._build_menubar()   # menú nativo antes de cualquier widget

        # ── Barra de archivo + undo/redo (top, ancho completo) ────
        topbar = ctk.CTkFrame(self.root, fg_color="#111827", height=32, corner_radius=0)
        topbar.pack(side="top", fill="x")   # PRIMERO → abarca todo el ancho
        topbar.pack_propagate(False)
        self._topbar_frame = topbar   # referencia para reordenar menubar al reconstruir

        _tbf = ctk.CTkFont(size=10)
        _tbf_b = ctk.CTkFont(size=10, weight="bold")

        def _tb_btn(parent, text, cmd, fg=UI_CARD, hover=UI_ACC, width=None):
            kw = {"width": width} if width else {}
            b = ctk.CTkButton(parent, text=text, command=cmd,
                              height=22, fg_color=fg, hover_color=hover,
                              corner_radius=4, font=_tbf, **kw)
            b.pack(side="left", padx=2, pady=4)
            return b

        def _sep(parent):
            ctk.CTkFrame(parent, width=1, height=20,
                         fg_color=UI_BORD).pack(side="left", padx=6, pady=6)

        # Grupo archivo
        _b_nuevo    = _tb_btn(topbar, "  Nuevo  ",      self._new_dwg,          width=70)
        _b_abrir    = _tb_btn(topbar, "  Abrir  ",      self._abrir_json,       width=70)
        _b_guardar  = _tb_btn(topbar, "  Guardar  ",    self._guardar_json,     fg=UI_SUCC, hover="#15803D", width=80)
        _b_guarda2  = _tb_btn(topbar, "  Guardar…  ",   self._guardar_json_como, width=90)
        _sep(topbar)
        _b_dxf_up   = _tb_btn(topbar, "⬆ DXF",          self._importar_dxf,     fg="#1D4ED8", hover="#1E40AF", width=68)
        _b_dxf_dn   = _tb_btn(topbar, "⬇ DXF",          self._exportar_dxf,     width=68)
        _b_png_dn   = _tb_btn(topbar, "⬇ PNG",          self._exportar_png,     width=68)
        _b_pdf_dn   = _tb_btn(topbar, "⬇ PDF",          self._exportar_pdf,     width=68, fg="#7C3AED", hover="#6D28D9")
        _sep(topbar)
        self._reg_btn(_b_nuevo,   "nuevo",       "Nuevo",    14)
        self._reg_btn(_b_abrir,   "abrir",       "Abrir",    14)
        self._reg_btn(_b_guardar, "guardar",     "Guardar",  14)
        self._reg_btn(_b_guarda2, "guardar_as",  "Guardar…", 14)
        self._reg_btn(_b_dxf_up,  "importar",    "DXF↑",     14)
        self._reg_btn(_b_dxf_dn,  "exportar_dxf","DXF↓",    14)
        self._reg_btn(_b_png_dn,  "exportar_png","PNG↓",     14)
        self._reg_btn(_b_pdf_dn,  "exportar_pdf","PDF↓",     14)

        # Grupo Undo / Redo (guardamos ref para actualizar estado)
        self._btn_undo = ctk.CTkButton(
            topbar, text="↩ Deshacer", command=self._undo,
            height=22, width=100, fg_color=UI_ACC, hover_color="#1D4ED8",
            corner_radius=4, font=_tbf_b)
        self._btn_undo.pack(side="left", padx=2, pady=4)
        self._btn_redo = ctk.CTkButton(
            topbar, text="↪ Rehacer", command=self._redo,
            height=22, width=100, fg_color=UI_CARD, hover_color="#1D4ED8",
            corner_radius=4, font=_tbf_b)
        self._btn_redo.pack(side="left", padx=2, pady=4)
        self._reg_btn(self._btn_undo, "deshacer", "Deshacer", 14)
        self._reg_btn(self._btn_redo, "rehacer",  "Rehacer",  14)

        # Etiqueta de archivo abierto (derecha)
        self._lbl_file = ctk.CTkLabel(
            topbar, text="Sin guardar",
            font=ctk.CTkFont(family="Courier New", size=9),
            text_color=UI_TEXT2)
        self._lbl_file.pack(side="right", padx=12)

        # Botón configuración
        _cfg_btn = ctk.CTkButton(
            topbar, text="⚙", width=28, height=22,
            fg_color=UI_CARD, hover_color="#374151",
            corner_radius=4, font=ctk.CTkFont(size=14),
            command=self._open_config)
        _cfg_btn.pack(side="right", padx=(0, 4), pady=4)
        _Tooltip(_cfg_btn, "Configuración  [snaps + IA]")

        # Botón Asistente IA — visible y accesible desde la topbar
        _ia_btn = ctk.CTkButton(
            topbar, text="🤖 IA", width=56, height=22,
            fg_color="#1E3A5F", hover_color="#2563EB",
            text_color="#93C5FD", corner_radius=4, font=_tbf_b,
            command=self._focus_ia_input)
        _ia_btn.pack(side="right", padx=(0, 2), pady=4)
        _Tooltip(_ia_btn, "Asistente IA  [/consulta + Enter]")

        # ── Toolbar izquierda + Panel derecho ─────────────────────
        self._tool_btns: dict[str, ctk.CTkButton] = {}
        self._op_btns:   dict[str, ctk.CTkButton] = {}

        if not self.ghost_panels:
            # ── Barra horizontal superior (Dibujo + Cotas) ───────────
            top_toolbar = ctk.CTkFrame(self.root, fg_color=UI_PAN, height=52, corner_radius=0)
            top_toolbar.pack(side="top", fill="x")
            top_toolbar.pack_propagate(False)
            self._populate_top_toolbar(top_toolbar)

            # ── Panel izquierdo con tabs (Editar | Modificar) ────────
            toolbar = ctk.CTkFrame(self.root, fg_color=UI_PAN, width=72, corner_radius=0)
            toolbar.pack(side="left", fill="y")
            toolbar.pack_propagate(False)
            self._populate_toolbar(toolbar)

            # ── Panel derecho ────────────────────────────────────────
            right = ctk.CTkFrame(self.root, fg_color=UI_PAN, width=220, corner_radius=0)
            right.pack(side="right", fill="y")
            right.pack_propagate(False)
            self._populate_right(right)
        # Si ghost_panels=True los paneles se crean en _create_ghost_panels
        # (se llama con after() una vez que el canvas tenga geometría real)

        # ── Área central (canvas) ─────────────────────────────────
        # En modo ghost el canvas ocupa TODO el ancho (los paneles flotan encima)
        center = ctk.CTkFrame(self.root, fg_color="transparent", corner_radius=0)
        if self.ghost_panels:
            center.pack(fill="both", expand=True)
        else:
            center.pack(side="left", fill="both", expand=True)
        self._center_frame = center   # referencia para ghost_reposition
        center.rowconfigure(0, weight=1)
        center.rowconfigure(1, minsize=28)   # tab strip layouts
        center.rowconfigure(2, minsize=26)   # status bar (SNAP/GRID/ORTHO)
        # row 3 = barra de comandos (dock)
        center.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(center, bg=self.cv_bg, cursor="none",
                                highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        # ── Tab strip de layouts (entre canvas y status bar) ──────────
        self._layout_tab_frame = tk.Frame(center, bg="#1A1A2E", height=28)
        self._layout_tab_frame.grid(row=1, column=0, sticky="ew")
        self._layout_tab_frame.pack_propagate(False)
        self._build_layout_tabs()

        # Barra de estado
        sb = ctk.CTkFrame(center, fg_color=UI_PAN, height=24, corner_radius=0)
        sb.grid(row=2, column=0, sticky="ew")
        # Empaquetar _lbl_tool primero (right) para que nunca sea tapado
        self._lbl_tool = ctk.CTkLabel(sb, text="SELECCIONAR [S]",
                                      font=ctk.CTkFont(family="Courier New", size=10),
                                      text_color=UI_ACC)
        self._lbl_tool.pack(side="right", padx=10)
        self._lbl_coords = tk.Label(sb, text="X: 0.000  Y: 0.000",
                                    font=("Courier New", 10),
                                    fg=UI_TEXT2, bg=UI_PAN, anchor="w")
        self._lbl_coords.pack(side="left", padx=10)
        # Toggles de modo: SNAP · GRID · ORTHO — clickeables
        _flag_font = ctk.CTkFont(family="Courier New", size=9, weight="bold")
        self._btn_snap = ctk.CTkButton(
            sb, text="SNAP", width=42, height=18,
            fg_color=UI_SUCC, hover_color="#15803D",
            text_color=UI_BG, font=_flag_font, corner_radius=3,
            command=lambda: self._toggle("snap"))
        self._btn_snap.pack(side="left", padx=(6, 2), pady=3)
        self._btn_grid = ctk.CTkButton(
            sb, text="GRID", width=38, height=18,
            fg_color=UI_SUCC, hover_color="#15803D",
            text_color=UI_BG, font=_flag_font, corner_radius=3,
            command=lambda: self._toggle("grid"))
        self._btn_grid.pack(side="left", padx=2, pady=3)
        self._btn_ortho = ctk.CTkButton(
            sb, text="ORTHO", width=48, height=18,
            fg_color=UI_CARD, hover_color="#15803D",
            text_color=UI_TEXT2, font=_flag_font, corner_radius=3,
            command=lambda: self._toggle("ortho"))
        self._btn_ortho.pack(side="left", padx=(2, 6), pady=3)

        # Badge LTSCALE — clickeable, abre prompt inline
        _lts_init = str(self._leer_config_ia().get("ltscale", 1.0))
        self._btn_lts = ctk.CTkButton(
            sb, text=f"LTS:{_lts_init}", height=18,
            fg_color=UI_CARD, hover_color="#1D4ED8",
            text_color=UI_TEXT2, font=_flag_font, corner_radius=3,
            command=self._ltscale_dialog)
        self._btn_lts.pack(side="left", padx=(0, 6), pady=3)
        self._reg_btn(self._btn_snap,  "snap",  "SNAP",            12)
        self._reg_btn(self._btn_grid,  "grid",  "GRID",            12)
        self._reg_btn(self._btn_ortho, "ortho", "ORTHO",           12)
        self._reg_btn(self._btn_lts,   "lts",   f"LTS:{_lts_init}", 12)

        self._lbl_op = tk.Label(sb, text="",
                                font=("Courier New", 10),
                                fg=UI_WARN, bg=UI_BG,
                                anchor="w")
        self._lbl_op.pack(side="left", padx=6, fill="x", expand=True)

        # ── Línea de comando expandible con historial ─────────────────
        # Restaurar estado expandido de la sesión anterior
        self._cmd_expanded = getattr(self, "_cmd_was_expanded", False)
        self._ac_prefix: str  = ""
        self._ac_matches: list = []
        self._ac_idx: int     = 0
        self._cmd_var         = tk.StringVar()
        self._dock_cmd_bar()

        # Canvas bindings
        cv = self.canvas
        cv.bind("<Motion>",           self._on_move)
        cv.bind("<Button-1>",         self._on_btn_down)
        cv.bind("<B1-Motion>",        self._on_drag)
        cv.bind("<ButtonRelease-1>",  self._on_btn_up)
        cv.bind("<Double-Button-1>",  self._on_dblclick)
        cv.bind("<Button-3>",         self._on_rclick)
        cv.bind("<Button-2>",         self._on_pan_start)
        cv.bind("<B2-Motion>",        self._on_pan)
        cv.bind("<ButtonRelease-2>",  self._on_pan_end)
        cv.bind("<MouseWheel>",       self._on_wheel)
        cv.bind("<Configure>",        self._on_resize)
        cv.bind("<Map>",              lambda e: self._on_focus_restore())
        cv.bind("<Unmap>",            lambda e: self._on_canvas_unmap())
        cv.bind("<Leave>",            self._on_canvas_leave)
        cv.bind("<FocusOut>",         self._on_canvas_leave, add="+")  # limpia cursor cuando diálogo CTk roba el foco
        cv.bind("<FocusIn>",          lambda e: self._on_focus_restore(), add="+")
        cv.bind("<Delete>",           lambda e: self._erase())
        cv.bind("<BackSpace>",        lambda e: self._on_canvas_backspace())
        cv.bind("<space>",            lambda e: self._repeat_last())
        cv.bind("<Return>",           lambda e: self._on_canvas_return())
        cv.bind("<KP_Enter>",         lambda e: self._on_canvas_return())
        cv.bind("<Escape>",           lambda e: self._cancelar())
        cv.bind("<Tab>",              lambda e: self._on_canvas_tab() or "break")
        cv.bind("<Key>",              self._on_canvas_key)
        # Nota: los atajos de letra individuales fueron reemplazados por el
        # buffer de teclado (_kbd_buf). El usuario escribe la abreviatura
        # y confirma con clic derecho o Enter. Backspace borra el último char.
        cv.bind("<u>", lambda e: (self._undo_punto()
                                  if self.draw_pts else self._undo()))
        cv.bind("<U>", lambda e: self._undo())

        # ── Aplicar modo icono inicial según setting ─────────────────
        self._apply_icon_mode()

        # ── Ghost panels: crear Toplevels flotantes cuando esté listo ──
        if self.ghost_panels:
            self.root.after(500, self._create_ghost_panels)

        # ── Restaurar barra de comandos flotante si estaba así al cerrar ──
        if self._cmd_was_floating:
            self.root.after(650, self._float_cmd_bar)

    # ══════════════════════════════════════════════════════════════════
    # BARRA DE COMANDOS — dock / flotante
    # ══════════════════════════════════════════════════════════════════

    def _build_cmd_bar(self, outer):
        """Puebla `outer` con todos los widgets de la barra de comandos."""
        # ── Historial ────────────────────────────────────────────────
        # IMPORTANTE: crear _hist_frame pero NO empaquetarlo aún.
        # cf y hint_lbl deben empaquetarse PRIMERO para que siempre tengan
        # espacio garantizado (tkinter asigna prioridad en orden de pack).
        # Si se empaqueta _hist_frame antes con expand=True, el Text interior
        # (~140px natural) acapara todo el espacio y empuja cf/hint fuera del área visible.
        _hist_bg = self._cmd_bg_dark()
        self._hist_frame = tk.Frame(outer, bg=_hist_bg)
        self._hist_txt = tk.Text(
            self._hist_frame,
            bg=_hist_bg, fg=CV_CMD_FG,
            font=("Courier New", 10), relief="flat", bd=0,
            state="disabled", wrap="word", height=10,
            selectbackground="#1E293B", exportselection=False)
        self._hist_txt.pack(fill="both", expand=True, padx=6, pady=(4, 2))
        self._hist_txt.tag_configure("cad",  foreground=CV_CMD_FG)
        self._hist_txt.tag_configure("ai",   foreground="#38BDF8")
        self._hist_txt.tag_configure("resp", foreground=UI_WARN)
        self._hist_txt.tag_configure("err",  foreground=UI_ERR)
        self._hist_txt.tag_configure("sys",  foreground=UI_TEXT2)
        self._hist_txt.tag_configure("res",  foreground="#7DD3FC")   # resultados de comandos

        # ── Input row — se empaqueta ANTES del historial para garantizar visibilidad ──
        cf = ctk.CTkFrame(outer, fg_color=self.cmd_bar_bg, height=32, corner_radius=0)
        cf.pack(side="bottom", fill="x")

        # Hint semántico
        self._cmd_hint_lbl = tk.Label(
            outer, text="", anchor="w",
            bg=self.cmd_bar_bg, fg="#475569",
            font=("Courier New", 9), padx=12, pady=1)
        self._cmd_hint_lbl.pack(side="bottom", fill="x")

        # ── Historial: empaquetar DESPUÉS del input row ───────────────
        # Al ser el último en la lista de pack, fill+expand llena el espacio
        # que queda ARRIBA de cf y hint, nunca los desplaza hacia afuera.
        if self._cmd_expanded:
            self._hist_frame.pack(fill="both", expand=True)

        # Botón float/dock  ⊟ = flotar · ⊞ = dockear
        _float_sym = "⊟" if not self._cmd_floating else "⊞"
        self._btn_float = ctk.CTkButton(
            cf, text=_float_sym, width=26, height=24,
            fg_color="transparent", hover_color=UI_PAN,
            font=ctk.CTkFont(size=12), corner_radius=4,
            command=self._toggle_cmd_float)
        self._btn_float.pack(side="right", padx=(0, 2), pady=4)

        # Botón toggle historial
        self._btn_hist = ctk.CTkButton(
            cf, text="▲", width=26, height=24,
            fg_color="transparent", hover_color=UI_PAN,
            font=ctk.CTkFont(size=10), corner_radius=4,
            command=self._toggle_history)
        self._btn_hist.pack(side="right", padx=(0, 2), pady=4)

        # Badge CAD / IA
        def _toggle_ia_mode():
            raw = self._cmd_var.get()
            if raw.startswith("/"):
                self._cmd_var.set("")
                self._cmd_entry.focus_set()
            else:
                self._cmd_var.set("/")
                self._cmd_entry.focus_set()
                self._cmd_entry.icursor("end")

        self._cmd_prompt_lbl = ctk.CTkButton(
            cf, text="CAD",
            font=ctk.CTkFont(family="Courier New", size=10, weight="bold"),
            text_color=UI_BG, fg_color=CV_CMD_FG,
            hover_color="#1a7a40",
            corner_radius=4, width=40, height=22,
            command=_toggle_ia_mode)
        self._cmd_prompt_lbl.pack(side="left", padx=(8, 6), pady=5)

        self._cmd_entry = tk.Entry(
            cf, textvariable=self._cmd_var,
            bg="#0D1117", fg=CV_CMD_FG,
            insertbackground=CV_CMD_FG,
            font=("Courier New", 12), relief="flat", bd=0)
        self._cmd_entry.pack(side="left", fill="x", expand=True, padx=4)
        self._cmd_entry.bind("<Return>", lambda e: self._ejecutar_comando())
        self._cmd_entry.bind("<Up>",     lambda e: self._historial_up())
        self._cmd_entry.bind("<Down>",   lambda e: self._historial_down())
        self._cmd_entry.bind("<Tab>",    lambda e: self._cmd_autocomplete() or "break")
        self._cmd_var.trace_add("write", self._on_cmd_change)

        # Restaurar log visual del historial guardado
        self._restore_hist_log()

    def _dock_cmd_bar(self):
        """Crea la barra de comandos en modo dock (row=3 del grid central)."""
        if self._cmd_float_frame and self._cmd_float_frame.winfo_exists():
            self._cmd_float_frame.destroy()
            self._cmd_float_frame = None
        self._cmd_floating = False

        outer = tk.Frame(self._center_frame, bg="#0D1117")
        outer.grid(row=3, column=0, sticky="ew")
        self._cmd_dock_outer = outer
        self._build_cmd_bar(outer)

    def _float_cmd_bar(self):
        """Saca la barra del dock y la convierte en overlay arrastrable."""
        if hasattr(self, "_cmd_dock_outer") and self._cmd_dock_outer.winfo_exists():
            self._cmd_dock_outer.grid_remove()
            self._cmd_dock_outer.destroy()

        self._cmd_floating = True
        # Sin update_idletasks() — forzar layout bloqueaba ~500ms.
        # Si la ventana aún no tiene geometría calculada (arranque), usar reqwidth.
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        if rw <= 10:
            rw = self.root.winfo_reqwidth() or 1440
        if rh <= 10:
            rh = self.root.winfo_reqheight() or 900
        fw = self._cmd_float_w
        fh = self._cmd_float_h

        # Si no hay posición guardada, centrar abajo
        if self._cmd_float_x == 0 and self._cmd_float_y == 0:
            self._cmd_float_x = max(0, (rw - fw) // 2)
            self._cmd_float_y = max(0, rh - fh - 40)

        fx, fy = self._cmd_float_x, self._cmd_float_y

        wrap = tk.Frame(self.root, bg="#0D1117",
                        highlightbackground="#334155", highlightthickness=1)
        wrap.place(x=fx, y=fy, width=fw, height=fh)
        self._cmd_float_frame = wrap

        # ── Resize-top handle (3 px, cursor size_ns) ─────────────────
        rtop = tk.Frame(wrap, bg="#475569", height=3, cursor="size_ns")
        rtop.pack(fill="x", side="top")

        def _rtop_start(ev):
            self._resize_top_y0 = ev.y_root
            self._resize_top_h0 = wrap.winfo_height()
            self._drag_cmd_fy   = wrap.winfo_y()

        def _rtop_move(ev):
            dy   = ev.y_root - self._resize_top_y0
            minh = 56
            if self._cmd_expanded:
                minh = 162
            new_h = max(minh, self._resize_top_h0 - dy)
            new_y = self._drag_cmd_fy + (self._resize_top_h0 - new_h)
            self._cmd_float_h = new_h
            self._cmd_float_y = new_y
            wrap.place(y=new_y, height=new_h)

        def _rtop_release(_ev):
            self._cmd_save_float_state()

        rtop.bind("<ButtonPress-1>",   _rtop_start)
        rtop.bind("<B1-Motion>",       _rtop_move)
        rtop.bind("<ButtonRelease-1>", _rtop_release)

        # ── Drag handle (franja azul oscuro, cursor fleur) ────────────
        handle = tk.Frame(wrap, bg="#1E293B", height=12, cursor="fleur")
        handle.pack(fill="x", side="top")

        def _drag_start(ev):
            self._drag_cmd_x0 = ev.x_root
            self._drag_cmd_y0 = ev.y_root
            self._drag_cmd_fx = wrap.winfo_x()
            self._drag_cmd_fy = wrap.winfo_y()

        def _drag_move(ev):
            nx = self._drag_cmd_fx + (ev.x_root - self._drag_cmd_x0)
            ny = self._drag_cmd_fy + (ev.y_root - self._drag_cmd_y0)
            wrap.place(x=nx, y=ny)
            self._cmd_float_x = nx
            self._cmd_float_y = ny

        def _drag_release(_ev):
            self._cmd_save_float_state()

        handle.bind("<ButtonPress-1>",   _drag_start)
        handle.bind("<B1-Motion>",       _drag_move)
        handle.bind("<ButtonRelease-1>", _drag_release)
        handle.bind("<Double-Button-1>", lambda _: self._toggle_cmd_float())

        # ── Resize-right handle (6 px, cursor size_we) ───────────────
        resize = tk.Frame(wrap, bg="#334155", width=6, cursor="size_we")
        resize.pack(fill="y", side="right")

        def _resize_start(ev):
            self._resize_cmd_x0 = ev.x_root
            self._resize_cmd_w0 = wrap.winfo_width()

        def _resize_move(ev):
            new_w = max(320, self._resize_cmd_w0 + (ev.x_root - self._resize_cmd_x0))
            self._cmd_float_w = new_w
            wrap.place(width=new_w)

        def _resize_release(_ev):
            self._cmd_save_float_state()

        resize.bind("<ButtonPress-1>",   _resize_start)
        resize.bind("<B1-Motion>",       _resize_move)
        resize.bind("<ButtonRelease-1>", _resize_release)

        # ── Contenido ─────────────────────────────────────────────────
        content = tk.Frame(wrap, bg="#0D1117")
        content.pack(fill="both", expand=True)
        self._build_cmd_bar(content)

    def _cmd_float_sync_height(self):
        """Aplica la altura guardada (o mínimo válido) al overlay flotante."""
        if not self._cmd_floating or not self._cmd_float_frame:
            return
        minh = 162 if self._cmd_expanded else 56
        fh = max(minh, self._cmd_float_h)
        self._cmd_float_h = fh
        self._cmd_float_frame.place(
            x=self._cmd_float_x, y=self._cmd_float_y,
            width=self._cmd_float_w, height=fh)

    def _cmd_save_float_state(self):
        """Persiste posición, tamaño y estado flotante en settings.json."""
        import json as _json
        cfg = self._leer_config_ia()
        cfg["cmd_bar"] = {
            "floating": self._cmd_floating,
            "expanded": self._cmd_expanded,
            "x": self._cmd_float_x,
            "y": self._cmd_float_y,
            "w": self._cmd_float_w,
            "h": self._cmd_float_h,
        }
        path = self._cfg_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(cfg, f, indent=2)

    def _toggle_cmd_float(self):
        """Alterna entre modo dock y flotante."""
        if self._cmd_floating:
            self._dock_cmd_bar()
        else:
            self._float_cmd_bar()
        self._cmd_save_float_state()

    # ══════════════════════════════════════════════════════════════════
    # GHOST PANELS — paneles flotantes con alpha animado
    # ══════════════════════════════════════════════════════════════════

    def _populate_top_toolbar(self, parent):
        """Barra horizontal superior: texto corto legible, igual estilo al panel izquierdo."""
        _FONT_BTN = ctk.CTkFont(size=11, weight="bold")
        _FONT_GRP = ctk.CTkFont(size=8,  weight="bold")

        _TOOL_ICON = {
            "select":   "select",
            "line":     "linea",
            "polyline": "polilinea",
            "spline":   "spline",
            "rect":     "rectangulo",
            "circle":   "circulo",
            "arc":      "arco",
            "text":     "texto",
            "ellipse":  "elipse",
            "polygon":  "poligono",
            "xline":    "xline",
            "cloud":    "nube",
        }
        _OP_ICON = {
            "dim_h":        "dim_h",
            "dim_v":        "dim_v",
            "dim_a":        "dim_a",
            "dim_ang":      "dim_ang",
            "dim_r":        "dim_r",
            "dim_co":       "dim_co",
            "dim_ba":       "dim_ba",
            "dim_sp":       "dim_sp",
            "dim_d":        "dim_d",
            "dim_arc_len":  "dim_arc",
            "dim_ord":      "dim_ord",
            "leader_cmd":   "lider",
            "hatch":        "hatch",
            "insert":       "bloque_ins",
            "block_cmd":    "bloque_def",
            "image_cmd":    "imagen",
        }

        # ── helpers ───────────────────────────────────────────────────
        def _group(text):
            """Contenedor vertical: label de grupo + fila de botones."""
            col = ctk.CTkFrame(parent, fg_color="transparent")
            col.pack(side="left", padx=(6, 2), pady=0)
            ctk.CTkLabel(col, text=text, font=_FONT_GRP,
                         text_color=UI_TEXT2, height=13).pack()
            row = ctk.CTkFrame(col, fg_color="transparent")
            row.pack()
            return row

        def _sep():
            ctk.CTkFrame(parent, width=1, fg_color=UI_BORD).pack(
                side="left", fill="y", padx=4, pady=8)

        def _tool_btn(row, label, tip, tname):
            b = ctk.CTkButton(
                row, text=label, width=42, height=36,
                fg_color=UI_ACC if tname == self.tool else UI_CARD,
                hover_color=UI_ACC, corner_radius=6,
                font=_FONT_BTN,
                command=lambda t=tname: self._set_tool(t),
            )
            b.pack(side="left", padx=2, pady=2)
            self._tool_btns[tname] = b
            _Tooltip(b, tip)
            self._reg_btn(b, _TOOL_ICON.get(tname, tname), label, 18)

        def _op_btn(row, label, tip, accion, color="#0369A1"):
            b = ctk.CTkButton(
                row, text=label, width=42, height=36,
                fg_color=UI_CARD, hover_color=color,
                corner_radius=6, font=_FONT_BTN,
                command=lambda a=accion: self._ejecutar_accion(a, ""),
            )
            b.pack(side="left", padx=2, pady=2)
            self._op_btns[accion] = b
            _Tooltip(b, tip)
            self._reg_btn(b, _OP_ICON.get(accion, accion), label, 18)

        # ── DIBUJAR ───────────────────────────────────────────────────
        r1 = _group("─── DIBUJAR")
        _tool_btn(r1, "SEL",  "Seleccionar  [S]",    "select")
        _tool_btn(r1, "L",    "Línea  [L]",           "line")
        _tool_btn(r1, "PL",   "Polilínea  [PL]",      "polyline")
        _tool_btn(r1, "SPL",  "Spline  [SPL]",         "spline")
        _tool_btn(r1, "REC",  "Rectángulo  [REC]",    "rect")
        _tool_btn(r1, "C",    "Círculo  [C]",         "circle")
        _tool_btn(r1, "A",    "Arco  [A]",            "arc")
        _tool_btn(r1, "T",    "Texto  [T]",           "text")
        _tool_btn(r1, "EL",   "Elipse  [EL]",         "ellipse")
        _tool_btn(r1, "POL",  "Polígono regular  [POL]", "polygon")

        _sep()

        # ── CONSTRUIR ─────────────────────────────────────────────────
        r_const = _group("─── CONSTRUIR")
        _tool_btn(r_const, "XL", "Línea de construcción  [XL]", "xline")

        _sep()

        # ── COTAS ─────────────────────────────────────────────────────
        r2 = _group("─── COTAS")
        _op_btn(r2, "DH",  "Cota horizontal  [DH]",  "dim_h")
        _op_btn(r2, "DV",  "Cota vertical  [DV]",    "dim_v")
        _op_btn(r2, "DA",  "Cota alineada  [DA]",    "dim_a")
        _op_btn(r2, "ANG", "Cota angular  [DAN]",    "dim_ang")
        _op_btn(r2, "R",   "Radio/Diámetro  [DR]",   "dim_r")
        _op_btn(r2, "DCO", "Cota continua  [DCO]",   "dim_co")
        _op_btn(r2, "DBA", "Cota base  [DBA]",       "dim_ba")
        _op_btn(r2, "DSP", "Espaciar cotas  [DSP]",  "dim_sp")
        _op_btn(r2, "DD",  "Diámetro Ø  [DD]",       "dim_d")
        _op_btn(r2, "DAR", "Long. arco  [DAR]",      "dim_arc_len")
        _op_btn(r2, "DOR", "Ordenada X/Y  [DOR]",    "dim_ord")

        _sep()

        # ── ANOTACIÓN ─────────────────────────────────────────────────
        r_ann = _group("─── ANOTACIÓN")
        _op_btn(r_ann, "LD", "Leader/Anotación con flecha  [LD]", "leader_cmd", "#0369A1")

        _sep()

        # ── RELLENO ───────────────────────────────────────────────────
        r3 = _group("─── RELLENO")
        _op_btn(r3, "H",    "Hatch/Relleno  [H]",     "hatch", "#0F766E")
        _tool_btn(r3, "CLOUD", "Nube de revisión  [CL]", "cloud")

        _sep()

        # ── BLOQUES ───────────────────────────────────────────────────
        r4 = _group("─── BLOQUES")
        _op_btn(r4, "INS", "Insertar bloque  [I]",       "insert",    "#7C3AED")
        _op_btn(r4, "BLK", "Crear bloque  [B]",          "block_cmd", "#6D28D9")
        _op_btn(r4, "IMG", "Insertar imagen  [IMG]",     "image_cmd", "#0F766E")

        _sep()

        # ── ZOOM (anclado a la derecha) ───────────────────────────────
        ze_col = ctk.CTkFrame(parent, fg_color="transparent")
        ze_col.pack(side="right", padx=8, pady=0)
        ctk.CTkLabel(ze_col, text="VISTA", font=_FONT_GRP,
                     text_color=UI_TEXT2, height=13).pack()
        ze_row = ctk.CTkFrame(ze_col, fg_color="transparent")
        ze_row.pack()
        ze = ctk.CTkButton(ze_row, text="ZE", width=42, height=36,
                           fg_color=UI_CARD, hover_color=UI_ACC,
                           corner_radius=6, font=_FONT_BTN,
                           command=self._zoom_extents)
        ze.pack(side="left", padx=2, pady=2)
        _Tooltip(ze, "Zoom extensión  [ZE]")
        self._reg_btn(ze, "zoom_e", "ZE", 18)

    def _populate_toolbar(self, parent):
        """Panel izquierdo con dos tabs: EDITAR y MODIFICAR+MEDIR."""
        _SMALL = ctk.CTkFont(size=9, weight="bold")
        _EDIT_ICON = {
            "erase":    "borrar",
            "move":     "mover",
            "copy":     "copiar",
            "array":    "array",
            "rotate":   "rotar",
            "scale":    "escalar",
            "mirror":   "espejo",
            "offset":   "offset",
            "eattedit": "atributos",
        }
        _MOD_ICON = {
            "trim":      "recortar",
            "extend":    "extender",
            "fillet":    "fillet",
            "chamfer":   "chamfer",
            "align_cmd": "alinear",
            "break_cmd": "partir",
            "explode":   "explotar",
            "matchprop": "matchprop",
        }
        _MEDIR_ICON = {
            "dist":     "dist",
            "id_point": "id_pt",
            "list_ent": "list_ent",
            "area_cmd": "area",
            "measure":  "measure",
            "laymcur":  "laymcur",
        }

        ctk.CTkLabel(parent, text="EM\nCAD",
                     font=ctk.CTkFont(size=9, weight="bold"),
                     text_color=UI_ACC).pack(pady=(8, 4))

        tabs = ctk.CTkTabview(parent, width=68, height=0,
                              fg_color=UI_PAN,
                              segmented_button_fg_color=UI_CARD,
                              segmented_button_selected_color=UI_ACC,
                              segmented_button_selected_hover_color="#1D4ED8",
                              segmented_button_unselected_color=UI_CARD,
                              segmented_button_unselected_hover_color=UI_BORD,
                              text_color=UI_TEXT,
                              anchor="nw",
                              corner_radius=6)
        tabs.pack(fill="both", expand=True, padx=2)
        tabs.add("✂")
        tabs.add("⟳")

        # ── Tab 1: EDITAR ─────────────────────────────────────────────
        t1 = tabs.tab("✂")

        _EDIT_OPS = [
            ("E",   "#DC2626", "erase",     "Borrar selección  [E]"),
            ("M",   "#7C3AED", "move",      "Mover  [M]"),
            ("CO",  "#7C3AED", "copy",      "Copiar  [CO]"),
            ("AR",  "#7C3AED", "array",     "Array rect/polar  [AR]"),
            ("RO",  "#7C3AED", "rotate",    "Rotar  [RO]"),
            ("SC",  "#7C3AED", "scale",     "Escalar  [SC]"),
            ("MI",  "#7C3AED", "mirror",    "Espejo  [MI]"),
            ("OF",  "#7C3AED", "offset",    "Paralela  [O]"),
            ("EA",  "#0369A1", "eattedit",  "Editar atributos bloque  [EA]"),
        ]
        for lbl, hov, accion, tip in _EDIT_OPS:
            b = ctk.CTkButton(
                t1, text=lbl, width=52, height=30,
                fg_color=UI_CARD, hover_color=hov,
                corner_radius=6,
                font=ctk.CTkFont(size=11, weight="bold"),
                command=lambda a=accion: self._ejecutar_accion(a, ""),
            )
            b.pack(pady=2)
            self._op_btns[accion] = b
            _Tooltip(b, tip)
            self._reg_btn(b, _EDIT_ICON.get(accion, accion), lbl, 14)

        # ── Tab 2: MODIFICAR + MEDIR ──────────────────────────────────
        t2 = tabs.tab("⟳")

        ctk.CTkLabel(t2, text="MODIF",
                     font=ctk.CTkFont(size=8, weight="bold"),
                     text_color=UI_TEXT2).pack(pady=(2, 0))

        _MOD_OPS = [
            ("TR",  "#0F766E", "trim",      "Recortar  [TR]"),
            ("EX",  "#0F766E", "extend",    "Extender  [EX]"),
            ("FI",  "#0F766E", "fillet",    "Fillet/Empalme  [F]"),
            ("CHA", "#0F766E", "chamfer",   "Chamfer/Achaflanado  [CHA]"),
            ("AL",  "#0F766E", "align_cmd", "Alinear  [AL]"),
            ("BR",  "#0F766E", "break_cmd", "Break/Partir  [BR]"),
            ("X",   "#0F766E", "explode",   "Explotar PL  [X]"),
            ("MA",  "#0F766E", "matchprop", "Copiar props  [MA]"),
        ]
        for lbl, hov, accion, tip in _MOD_OPS:
            b = ctk.CTkButton(
                t2, text=lbl, width=52, height=30,
                fg_color=UI_CARD, hover_color=hov,
                corner_radius=6,
                font=ctk.CTkFont(size=11, weight="bold"),
                command=lambda a=accion: self._ejecutar_accion(a, ""),
            )
            b.pack(pady=2)
            self._op_btns[accion] = b
            _Tooltip(b, tip)
            self._reg_btn(b, _MOD_ICON.get(accion, accion), lbl, 14)

        ctk.CTkFrame(t2, height=1, fg_color=UI_BORD).pack(fill="x", padx=4, pady=4)
        ctk.CTkLabel(t2, text="MEDIR",
                     font=ctk.CTkFont(size=8, weight="bold"),
                     text_color=UI_TEXT2).pack(pady=(0, 0))

        for lbl, accion, tip, hov in [
            ("DI",   "dist",     "Distancia  [DI]",           "#0F766E"),
            ("ID",   "id_point", "ID Punto — coordenadas  [ID]", "#0F766E"),
            ("LI",   "list_ent", "Listar entidad  [LI]",      "#0F766E"),
            ("AREA", "area_cmd", "Área cerrada  [AREA]",      "#0F766E"),
            ("MEA",  "measure",  "Medir segmentos  [MEA]",    "#0F766E"),
            ("LC",   "laymcur",  "Capa activa  [LC]",         "#0F766E"),
        ]:
            b = ctk.CTkButton(
                t2, text=lbl, width=52, height=30,
                fg_color=UI_CARD, hover_color=hov, corner_radius=6,
                font=ctk.CTkFont(size=11, weight="bold"),
                command=lambda a=accion: self._ejecutar_accion(a, ""),
            )
            b.pack(pady=2)
            _Tooltip(b, tip)
            self._reg_btn(b, _MEDIR_ICON.get(accion, accion), lbl, 14)

    def _populate_right(self, parent):
        """Construye el contenido del panel derecho (capas + props) en `parent`."""
        _FONT_LHD = ctk.CTkFont(family="Courier New", size=8, weight="bold")

        # ── Estado de colapso ──────────────────────────────────────────
        self._layer_panel_expanded  = False   # empieza colapsado
        self._layer_needs_rebuild   = False   # flag: layers cambiaron mientras colapsado

        # ── Fila 1: título + toggle + ISO + ALL ───────────────────────
        _lhdr = ctk.CTkFrame(parent, fg_color="transparent")
        _lhdr.pack(fill="x", padx=10, pady=(12, 2))
        ctk.CTkLabel(_lhdr, text="CAPAS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=UI_TEXT2).pack(side="left")

        _btn_layon = ctk.CTkButton(
            _lhdr, text="ALL", width=36, height=18,
            fg_color=UI_CARD, hover_color=UI_ACC, text_color=UI_TEXT2,
            corner_radius=3, font=_FONT_LHD, command=self._layon)
        _btn_layon.pack(side="right", padx=(2, 0))
        _Tooltip(_btn_layon, "Mostrar todas  [LAYON]")

        _btn_layiso = ctk.CTkButton(
            _lhdr, text="ISO", width=36, height=18,
            fg_color=UI_CARD, hover_color="#7C3AED", text_color=UI_TEXT2,
            corner_radius=3, font=_FONT_LHD, command=self._layiso)
        _btn_layiso.pack(side="right", padx=2)
        _Tooltip(_btn_layiso, "Aislar capa activa  [LAYISO]")

        # Botón toggle collapse: muestra "▶ N capas" / "▼ N capas"
        self._layer_toggle_btn = ctk.CTkButton(
            _lhdr, text="▶  0 capas", width=90, height=18,
            fg_color="transparent", hover_color=UI_CARD,
            text_color=UI_TEXT2, anchor="w",
            font=ctk.CTkFont(size=10))
        self._layer_toggle_btn.pack(side="left", padx=(6, 0))

        # ── Body colapsable (fila VIS/LCK + filtro + scrollframe) ─────
        self._layer_body = ctk.CTkFrame(parent, fg_color="transparent")
        # NO se hace .pack() aquí → empieza oculto

        # Función toggle
        def _toggle_panel():
            if not self._layer_panel_expanded:
                # EXPANDIR
                self._layer_panel_expanded = True
                self._layer_body.pack(fill="x", before=self._layer_body_sentinel)
                n = len(self.layers)
                self._layer_toggle_btn.configure(
                    text=f"▼  {n} capa{'s' if n != 1 else ''}")
                # Construir cards si no existen o si los layers cambiaron
                if self._layer_needs_rebuild or not self._layer_card_refs:
                    self._layer_needs_rebuild = False
                    self._build_layer_panel()
            else:
                # COLAPSAR
                self._layer_panel_expanded = False
                self._layer_body.pack_forget()
                n = len(self.layers)
                self._layer_toggle_btn.configure(
                    text=f"▶  {n} capa{'s' if n != 1 else ''}")

        self._layer_toggle_btn.configure(command=_toggle_panel)

        # Fila 2: VIS / LCK  (dentro del body colapsable)
        _lhdr2 = ctk.CTkFrame(self._layer_body, fg_color="transparent")
        _lhdr2.pack(fill="x", padx=10, pady=(0, 2))

        def _toggle_all_vis():
            alguna_apagada = any(not l.visible for l in self.layers.values())
            nuevo_estado = alguna_apagada
            for l in self.layers.values():
                l.visible = nuevo_estado
            self.root.after(10,  self._build_layer_panel)
            self.root.after(50,  self._redraw_static)

        def _toggle_all_lock():
            alguna_libre = any(not l.locked for l in self.layers.values())
            nuevo_estado = alguna_libre
            for l in self.layers.values():
                l.locked = nuevo_estado
            self.root.after(10, self._build_layer_panel)
            self.root.after(30, self._redraw_static)

        _btn_vis = ctk.CTkButton(
            _lhdr2, text="👁 VIS", width=58, height=18,
            fg_color=UI_CARD, hover_color=UI_SUCC, text_color=UI_TEXT2,
            corner_radius=3, font=_FONT_LHD, command=_toggle_all_vis)
        _btn_vis.pack(side="left", padx=(0, 3))
        _Tooltip(_btn_vis, "Encender/Apagar todas las capas")

        _btn_lck = ctk.CTkButton(
            _lhdr2, text="🔒 LCK", width=58, height=18,
            fg_color=UI_CARD, hover_color="#FF8C00", text_color=UI_TEXT2,
            corner_radius=3, font=_FONT_LHD, command=_toggle_all_lock)
        _btn_lck.pack(side="left", padx=(0, 3))
        _Tooltip(_btn_lck, "Bloquear/Desbloquear todas las capas")

        # Filtro de búsqueda (dentro del body)
        self._layer_filter_var = tk.StringVar()
        self._layer_filter_debounce_id = None

        def _on_filter_change(*_):
            if self._layer_filter_debounce_id is not None:
                try:
                    self.root.after_cancel(self._layer_filter_debounce_id)
                except Exception:
                    pass
            self._layer_filter_debounce_id = self.root.after(
                300, self._filter_layer_panel)

        self._layer_filter_var.trace_add("write", _on_filter_change)
        self._layer_filter_entry = ctk.CTkEntry(
            self._layer_body, textvariable=self._layer_filter_var,
            placeholder_text="Buscar capa…", height=22, font=ctk.CTkFont(size=10))
        self._layer_filter_entry.pack(fill="x", padx=10, pady=(0, 4))

        def _on_filter_focus(*_):
            if self._kbd_buf:
                self._kbd_buf = ""
                self._redraw_dynamic()
        self._layer_filter_entry.bind("<FocusIn>", _on_filter_focus)

        # ScrollFrame de cards — tk nativo (CTkScrollableFrame tiene bug recursivo en
        # su scrollbar: set() → _draw() → update_idletasks() → set() → loop ~500ms)
        _lf_outer = tk.Frame(self._layer_body, bg=UI_PAN)
        _lf_outer.pack(fill="x", padx=6)

        self._layer_canvas = tk.Canvas(
            _lf_outer, bg=UI_PAN, highlightthickness=0, height=260, bd=0)
        _lf_vsb = tk.Scrollbar(_lf_outer, orient="vertical",
                               command=self._layer_canvas.yview)
        self._layer_canvas.configure(yscrollcommand=_lf_vsb.set)
        _lf_vsb.pack(side="right", fill="y")
        self._layer_canvas.pack(side="left", fill="both", expand=True)

        self._layer_frame = tk.Frame(self._layer_canvas, bg=UI_PAN)
        _lf_win = self._layer_canvas.create_window(
            (0, 0), window=self._layer_frame, anchor="nw")

        def _lf_frame_cfg(event):
            # Suprimir durante operaciones masivas (pack_forget×200, destroy×200)
            # para evitar 200 scrollregion recalcs que causan freeze ~500ms
            if getattr(self, '_layer_suppress_configure', False):
                return
            self._layer_canvas.configure(
                scrollregion=self._layer_canvas.bbox("all"))
        self._layer_frame.bind("<Configure>", _lf_frame_cfg)
        self._lf_frame_cfg = _lf_frame_cfg   # guardada para rebind si es necesario

        def _lf_canvas_cfg(event):
            self._layer_canvas.itemconfig(_lf_win, width=event.width)
        self._layer_canvas.bind("<Configure>", _lf_canvas_cfg)

        def _lf_scroll(event):
            self._layer_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        # bind_all cuando el mouse entra, unbind cuando sale — no interfiere con zoom canvas
        self._layer_canvas.bind(
            "<Enter>", lambda _e: self._layer_canvas.bind_all("<MouseWheel>", _lf_scroll))
        self._layer_canvas.bind(
            "<Leave>", lambda _e: self._layer_canvas.unbind_all("<MouseWheel>"))

        # Sentinel: frame invisible que sirve de ancla para insertar el body
        self._layer_body_sentinel = ctk.CTkFrame(parent, fg_color="transparent", height=0)
        self._layer_body_sentinel.pack(fill="x")

        # Inicializar refs vacíos (sin cards)
        self._layer_card_refs = {}
        self._build_layer_panel()   # construye solo con las capas iniciales (pocas)

        ctk.CTkFrame(parent, height=1, fg_color=UI_BORD).pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(parent, text="PROPIEDADES",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=UI_TEXT2).pack(anchor="w", padx=10)

        # ── Panel dinámico de propiedades — tk nativo (mismo motivo que _layer_frame)
        _pf_outer = tk.Frame(parent, bg=UI_PAN)
        _pf_outer.pack(fill="x", padx=4, pady=(2, 4))
        self._prop_canvas = tk.Canvas(
            _pf_outer, bg=UI_PAN, highlightthickness=0, height=300, bd=0)
        _pf_vsb = tk.Scrollbar(_pf_outer, orient="vertical",
                               command=self._prop_canvas.yview)
        self._prop_canvas.configure(yscrollcommand=_pf_vsb.set)
        _pf_vsb.pack(side="right", fill="y")
        self._prop_canvas.pack(side="left", fill="both", expand=True)
        self._prop_frame = tk.Frame(self._prop_canvas, bg=UI_PAN)
        _pf_win = self._prop_canvas.create_window(
            (0, 0), window=self._prop_frame, anchor="nw")

        def _pf_frame_cfg(event):
            if getattr(self, '_prop_suppress_configure', False):
                return
            self._prop_canvas.configure(
                scrollregion=self._prop_canvas.bbox("all"))
        self._prop_frame.bind("<Configure>", _pf_frame_cfg)

        def _pf_canvas_cfg(event):
            self._prop_canvas.itemconfig(_pf_win, width=event.width)
        self._prop_canvas.bind("<Configure>", _pf_canvas_cfg)

        def _pf_scroll(event):
            self._prop_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self._prop_canvas.bind(
            "<Enter>", lambda _e: self._prop_canvas.bind_all("<MouseWheel>", _pf_scroll))
        self._prop_canvas.bind(
            "<Leave>", lambda _e: self._prop_canvas.unbind_all("<MouseWheel>"))
        # stub para compatibilidad con código antiguo que llama _lbl_prop.configure
        self._lbl_prop = _NullWidget()
        self._prop_layer_var = tk.StringVar(value="—")
        self._rebuild_prop_panel([])

        ctk.CTkFrame(parent, height=1, fg_color=UI_BORD).pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(parent, text="Capa activa:",
                     font=ctk.CTkFont(size=10), text_color=UI_TEXT2).pack(anchor="w", padx=10)
        # tk.OptionMenu nativo — CTkOptionMenu.set() llamaba _draw()→update_idletasks()
        # que desencadenaba cascada de CTkScrollbar en cada cambio de capa activa
        self._om_capa_var = tk.StringVar(value=self.active_layer)
        self._om_capa = tk.OptionMenu(
            parent, self._om_capa_var, *list(self.layers.keys()),
            command=self._activar_capa)
        self._om_capa.configure(
            bg=UI_CARD, fg=UI_TEXT, activebackground=UI_ACC,
            activeforeground=UI_TEXT, font=("Arial", 11),
            highlightthickness=0, bd=0, relief="flat")
        self._om_capa["menu"].configure(
            bg=UI_CARD, fg=UI_TEXT, activebackground=UI_ACC, font=("Arial", 11))
        self._om_capa.pack(padx=10, pady=4)

        # ── Panel de Bloques ───────────────────────────────────────────────────
        ctk.CTkFrame(parent, height=1, fg_color=UI_BORD).pack(fill="x", padx=8, pady=6)
        _blk_hdr = ctk.CTkFrame(parent, fg_color="transparent")
        _blk_hdr.pack(fill="x", padx=10, pady=(4, 2))
        ctk.CTkLabel(_blk_hdr, text="BLOQUES",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=UI_TEXT2).pack(side="left")
        _btn_blk_ins = ctk.CTkButton(
            _blk_hdr, text="📦 Insertar  [I]", width=120, height=26,
            fg_color="#7C3AED", hover_color="#6D28D9", text_color="white",
            corner_radius=6, font=ctk.CTkFont(size=10, weight="bold"),
            command=lambda: self._ejecutar_accion("insert", ""))
        _btn_blk_ins.pack(side="right", padx=(4, 0))
        _Tooltip(_btn_blk_ins, "Abrir biblioteca de bloques e insertar  [I]")

        # Indicador de biblioteca externa
        self._lbl_lib_info = ctk.CTkLabel(
            parent, text=self._lib_info_text(),
            font=ctk.CTkFont(size=9), text_color=UI_TEXT2, anchor="w")
        self._lbl_lib_info.pack(fill="x", padx=12, pady=(0, 2))

        # Botón configurar carpeta de biblioteca
        _btn_lib = ctk.CTkButton(
            parent, text="📁 Carpeta de biblioteca…",
            width=200, height=22,
            fg_color=UI_CARD, hover_color="#7C3AED", text_color=UI_TEXT2,
            corner_radius=6, font=ctk.CTkFont(size=9),
            command=self._configurar_biblioteca)
        _btn_lib.pack(fill="x", padx=10, pady=(0, 2))
        _Tooltip(_btn_lib, "Seleccionar carpeta con archivos DWG/DXF como biblioteca de bloques")

        _btn_blk_def = ctk.CTkButton(
            parent, text="＋ Crear bloque desde selección  [B]",
            width=200, height=22,
            fg_color=UI_CARD, hover_color="#6D28D9", text_color=UI_TEXT2,
            corner_radius=6, font=ctk.CTkFont(size=9),
            command=lambda: self._ejecutar_accion("block_cmd", ""))
        _btn_blk_def.pack(fill="x", padx=10, pady=(0, 4))
        _Tooltip(_btn_blk_def, "Crear bloque a partir de entidades seleccionadas  [B]")

        ctk.CTkFrame(parent, height=1, fg_color=UI_BORD).pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(parent,
            text="Atajos rápidos:\n"
                 "L  PL  REC  C  A  T  S\n"
                 "E  M  CO  RO  SC  MI\n"
                 "O  TR  EX  X  MA\n"
                 "ZE  U  REDO  DI  LI  AREA\n"
                 "Tab = autocompletar cmd\n"
                 "SPACE = repetir último\n"
                 "Ctrl+A = selec. todo\n"
                 "Ctrl+S = guardar rápido\n"
                 "Doble clic = editar texto\n"
                 "Clic derecho = cancelar\n"
                 "F1=Ayuda  F3=Snap\n"
                 "F7=Grid   F8=Ortho\n"
                 "/texto = Asistente IA",
            font=ctk.CTkFont(size=9), text_color=UI_BORD,
            justify="left").pack(anchor="w", padx=10)

    # ── Ciclo de vida de los ghost panels ────────────────────────────

    # ══════════════════════════════════════════════════════════════════
    # GHOST PANELS — slide desde el borde, dentro de la misma ventana
    # Canvas 100% visible cuando ocultos. Paneles se deslizan al hover.
    # Izquierdo: x sale de -60 → 0. Derecho: x sale de cw → cw-220.
    # ══════════════════════════════════════════════════════════════════

    def _create_ghost_panels(self):
        """Crea paneles que se deslizan desde los bordes al acercar el mouse."""
        cf = self._center_frame
        self._ghost_overlays = []

        def _make(side: str, width: int, populate_fn):
            # tk.Frame nativo — CTkFrame llamaba _update_dimensions_event→_draw()
            # en cada panel.place() del slide (cada 20ms), bloqueando el GL worker.
            panel = tk.Frame(cf, bg=UI_PAN, width=width)
            panel.pack_propagate(False)

            _gp_canvas = tk.Canvas(panel, bg=UI_PAN, highlightthickness=0, bd=0)
            _gp_vsb = tk.Scrollbar(panel, orient="vertical",
                                   command=_gp_canvas.yview)
            _gp_canvas.configure(yscrollcommand=_gp_vsb.set)
            _gp_vsb.pack(side="right", fill="y")
            _gp_canvas.pack(side="left", fill="both", expand=True)

            scroll = tk.Frame(_gp_canvas, bg=UI_PAN)
            _gp_win = _gp_canvas.create_window((0, 0), window=scroll, anchor="nw")

            def _gp_frame_cfg(event, c=_gp_canvas):
                c.configure(scrollregion=c.bbox("all"))
            scroll.bind("<Configure>", _gp_frame_cfg)

            def _gp_canvas_cfg(event, c=_gp_canvas, w=_gp_win):
                c.itemconfig(w, width=event.width)
            _gp_canvas.bind("<Configure>", _gp_canvas_cfg)

            # NO usar bind_all aquí — el ghost panel cubre el canvas de dibujo y
            # bind_all interceptaría el zoom (rueda del mouse) del renderer GL.
            # El panel tiene barra de scroll propia y los sub-paneles (_layer_canvas,
            # _prop_canvas) manejan su propio bind_all solo cuando el mouse está sobre ellos.

            populate_fn(scroll)

            # Posición inicial: fuera de pantalla
            if side == "left":
                x_hidden, x_shown = -width, 0
                panel.place(x=x_hidden, y=0, width=width, height=1)
            else:
                x_hidden, x_shown = width, 0   # offset desde relx=1.0
                panel.place(relx=1.0, x=x_hidden, y=0, width=width, height=1)

            self._ghost_overlays.append({
                "panel": panel, "side": side, "width": width,
                "x_cur":    float(x_hidden),
                "x_hidden": float(x_hidden),
                "x_shown":  float(x_shown),
            })

        _make("left",  72,  self._populate_toolbar)
        _make("right", 220, self._populate_right)

        self._ghost_slide_poll()

    def _ghost_slide_poll(self):
        """Poll a 20 ms: desliza paneles según posición del mouse."""
        if not self._ghost_overlays:
            return   # paneles destruidos → detener el loop
        try:
            mx  = self.root.winfo_pointerx() - self._center_frame.winfo_rootx()
            ch  = self.canvas.winfo_height()
            cw  = self._center_frame.winfo_width()
        except tk.TclError:
            self.root.after(20, self._ghost_slide_poll)
            return

        TRIGGER = 24   # px desde el borde que activan el slide

        for ov in self._ghost_overlays:
            panel   = ov["panel"]
            side    = ov["side"]
            width   = ov["width"]

            near = (side == "left"  and mx < TRIGGER) or \
                   (side == "right" and mx > cw - TRIGGER)
            # Una vez mostrado, mantener hasta salir completamente del panel
            if not near:
                if side == "left":
                    near = (ov["x_cur"] > ov["x_hidden"] + 2
                            and mx < width)
                else:
                    near = (ov["x_cur"] < ov["x_hidden"] - 2
                            and mx > cw - width)

            target = ov["x_shown"] if near else ov["x_hidden"]
            diff   = target - ov["x_cur"]

            if abs(diff) > 0.4:
                ov["x_cur"] += diff * 0.30
            else:
                ov["x_cur"] = target

            xi = int(ov["x_cur"])
            if side == "left":
                panel.place(x=xi, y=0, width=width, height=ch)
            else:
                panel.place(relx=1.0, x=xi, y=0, width=width, height=ch)

        self.root.after(20, self._ghost_slide_poll)

    def _ghost_destroy(self):
        """Elimina los paneles ghost en runtime (toggle OFF)."""
        for ov in self._ghost_overlays:
            try:
                ov["panel"].place_forget()
                ov["panel"].destroy()
            except Exception:
                pass  # widget Tkinter ya destruido — ignorar
        self._ghost_overlays = []

    # Máximo de capas en el panel sin filtro. Con 500 el rebuild tarda ~400ms
    # pero ocurre solo al cargar el DXF (una vez). El buscador usa debounce.
    _LAYER_PANEL_MAX = 500

    def _make_layer_card(self, name: str, lyr) -> "tk.Frame":
        """Crea y retorna el Frame nativo de una capa (sin packear).
        Usa widgets tk nativos — CTkButton/CTkFrame/CTkCheckBox llaman _draw()
        en __init__, generando freezes de ~500ms con 200 capas × 10 widgets."""
        is_active = name == self.active_layer
        # Fondo "transparente" = UI_PAN (color del panel de capas)
        card_bg   = UI_CARD if is_active else UI_PAN
        _fn_norm  = ("Arial", 9)
        _fn_bold  = ("Arial", 9, "bold")
        _fn_small = ("Arial", 8)

        card = tk.Frame(self._layer_frame, bg=card_bg,
                        highlightthickness=1 if is_active else 0,
                        highlightbackground=UI_ACC if is_active else card_bg)

        # ── Fila 1: [vis] [lock] [color] [nombre] ─────────────────
        row1 = tk.Frame(card, bg=card_bg)
        row1.pack(fill="x")

        vis_var = tk.BooleanVar(value=lyr.visible)
        def _tv(v=vis_var, l=lyr):
            l.visible = v.get(); self._redraw_static()
        tk.Checkbutton(row1, variable=vis_var, text="", width=1,
                       bg=card_bg, fg=UI_TEXT2, selectcolor=UI_ACC,
                       activebackground=card_bg, bd=0,
                       command=_tv).pack(side="left")

        lock_lbl = tk.Label(
            row1, text="■" if lyr.locked else "□",
            font=("Arial", 11),
            fg="#FF8C00" if lyr.locked else UI_TEXT2,
            bg=card_bg, cursor="hand2", width=2)
        lock_lbl.pack(side="left", padx=1)
        def _toggle_lock(l=lyr, lb=lock_lbl):
            l.locked = not l.locked
            lb.configure(
                text="■" if l.locked else "□",
                fg="#FF8C00" if l.locked else UI_TEXT2)
            self._redraw_static()
        lock_lbl.bind("<Button-1>", lambda _e, fn=_toggle_lock: fn())

        dot = tk.Canvas(row1, width=14, height=14, bg=lyr.color,
                        highlightthickness=1, highlightbackground=UI_BORD,
                        cursor="hand2")
        dot.pack(side="left", padx=2)
        def _pick_color(l=lyr, d=dot):
            from tkinter import colorchooser as _cc
            res = _cc.askcolor(color=l.color, title=f"Color — {l.name}",
                               parent=self.root)
            if res and res[1]:
                l.color = res[1].upper()
                d.configure(bg=l.color)
                _rnd_pc = getattr(self, '_renderer', None)
                if _rnd_pc is not None:
                    getattr(_rnd_pc, '_block_geom_cache', {}).clear()
                self._redraw_static()
        dot.bind("<Button-1>", lambda _e, fn=_pick_color: fn())

        lbl = tk.Label(
            row1, text=name,
            font=_fn_bold if is_active else _fn_norm,
            fg=UI_TEXT if is_active else UI_TEXT2,
            bg=card_bg, anchor="w", cursor="hand2", width=9)
        lbl.pack(side="left", padx=2)
        lbl.bind("<Button-1>", lambda _e, n=name: self._activar_capa(n))

        if name != "0" and not is_active:
            def _del(n=name):
                if any(e.layer == n for e in self.entities):
                    self._echo(f"!! Capa '{n}' tiene entidades — muévalas primero")
                    return
                if not messagebox.askyesno(
                        "Eliminar capa",
                        f"¿Eliminar la capa  '{n}'?\n\n"
                        "Esta acción no se puede deshacer.",
                        parent=self.root):
                    return
                self._push_undo()
                del self.layers[n]
                self._refresh_om_capa(list(self.layers.keys()))
                self._build_layer_panel()
            tk.Button(row1, text="×", width=2,
                      bg=UI_CARD, activebackground=UI_ERR,
                      fg=UI_TEXT2, font=_fn_small,
                      bd=0, relief="flat",
                      command=_del).pack(side="right", padx=2)

        # ── Fila 2: [linetype] [lw 1..5] ──────────────────────────
        row2 = tk.Frame(card, bg=card_bg)
        row2.pack(fill="x", pady=(0, 2))

        lt_abbr = _LT_ABBR.get(lyr.linetype, lyr.linetype[:4])
        lt_btn = tk.Button(
            row2, text=lt_abbr, width=5,
            bg="#2A3A4A", activebackground="#3A4A5A",
            fg=UI_TEXT2, font=_fn_small,
            bd=0, relief="flat")
        lt_btn.pack(side="left", padx=(20, 2))
        def _cycle_lt(l=lyr, btn=lt_btn):
            idx = _LT_CYCLE.index(l.linetype) if l.linetype in _LT_CYCLE else 0
            l.linetype = _LT_CYCLE[(idx + 1) % len(_LT_CYCLE)]
            btn.configure(text=_LT_ABBR.get(l.linetype, l.linetype[:4]))
            self._redraw_static()
        lt_btn.configure(command=_cycle_lt)

        lw_frame = tk.Frame(row2, bg=card_bg)
        lw_frame.pack(side="left", padx=2)
        for lw_val in range(1, 6):
            def _set_lw(v, l=lyr, lf=lw_frame):
                l.linewidth = v
                for child in lf.winfo_children():
                    try:
                        child.configure(
                            bg=UI_ACC if int(child.cget("text")) == v
                            else UI_CARD)
                    except Exception:
                        pass
                self._redraw_static()
            b = tk.Button(
                lw_frame, text=str(lw_val), width=2,
                bg=UI_ACC if lyr.linewidth == lw_val else UI_CARD,
                activebackground=UI_ACC,
                fg=UI_TEXT, font=_fn_small,
                bd=0, relief="flat",
                command=lambda v=lw_val, fn=_set_lw: fn(v))
            b.pack(side="left", padx=1)

        return card

    # ── Panel de Bloques ──────────────────────────────────────────────────────

    def _build_block_panel(self, parent):
        """Construye la lista scrollable de bloques de la biblioteca."""
        _FONT_S = ctk.CTkFont(size=9)
        _FONT_B = ctk.CTkFont(size=9, weight="bold")

        # Filtro de categoría
        cats = list(dict.fromkeys(cat for cat, *_ in BLOCK_LIBRARY))
        cats_all = ["Todos"] + cats

        self._blk_cat_var   = tk.StringVar(value="Todos")
        self._blk_selected  = tk.StringVar(value="")   # nombre del bloque activo
        self._blk_frame_ref = None

        cat_row = tk.Frame(parent, bg=UI_PAN)
        cat_row.pack(fill="x", padx=10, pady=(0, 3))
        tk.Label(cat_row, text="Cat:", font=("Arial", 9),
                 fg=UI_TEXT2, bg=UI_PAN, width=4).pack(side="left")
        cat_om = tk.OptionMenu(cat_row, self._blk_cat_var, *cats_all,
                               command=lambda _: self._refresh_block_list())
        cat_om.configure(bg=UI_CARD, fg=UI_TEXT, activebackground=UI_ACC,
                         activeforeground=UI_TEXT, font=("Arial", 9),
                         highlightthickness=0, bd=0, relief="flat")
        cat_om["menu"].configure(bg=UI_CARD, fg=UI_TEXT,
                                 activebackground=UI_ACC, font=("Arial", 9))
        cat_om.pack(side="left", padx=2)

        # Frame scrollable de bloques — tk nativo (mismo motivo que _layer_frame)
        _bl_outer = tk.Frame(parent, bg=UI_PAN)
        _bl_outer.pack(fill="x", padx=6, pady=(0, 4))
        _bl_canvas = tk.Canvas(_bl_outer, bg=UI_PAN, highlightthickness=0,
                               height=160, bd=0)
        _bl_vsb = tk.Scrollbar(_bl_outer, orient="vertical",
                               command=_bl_canvas.yview)
        _bl_canvas.configure(yscrollcommand=_bl_vsb.set)
        _bl_vsb.pack(side="right", fill="y")
        _bl_canvas.pack(side="left", fill="both", expand=True)
        self._blk_list_frame = tk.Frame(_bl_canvas, bg=UI_PAN)
        _bl_win = _bl_canvas.create_window(
            (0, 0), window=self._blk_list_frame, anchor="nw")

        def _bl_frame_cfg(event):
            _bl_canvas.configure(scrollregion=_bl_canvas.bbox("all"))
        self._blk_list_frame.bind("<Configure>", _bl_frame_cfg)

        def _bl_canvas_cfg(event):
            _bl_canvas.itemconfig(_bl_win, width=event.width)
        _bl_canvas.bind("<Configure>", _bl_canvas_cfg)
        self._blk_btn_map: dict[str, tk.Button] = {}

        # Opciones de transformación (escala / ángulo)
        opts_row = ctk.CTkFrame(parent, fg_color="transparent")
        opts_row.pack(fill="x", padx=10, pady=(0, 2))

        ctk.CTkLabel(opts_row, text="Esc:", font=_FONT_S,
                     text_color=UI_TEXT2, width=28).pack(side="left")
        self._blk_scale_var = tk.StringVar(value="1.0")
        ctk.CTkEntry(opts_row, textvariable=self._blk_scale_var,
                     width=48, height=22, font=_FONT_S).pack(side="left", padx=2)

        ctk.CTkLabel(opts_row, text="Ang°:", font=_FONT_S,
                     text_color=UI_TEXT2, width=36).pack(side="left")
        self._blk_angle_var = tk.StringVar(value="0")
        ctk.CTkEntry(opts_row, textvariable=self._blk_angle_var,
                     width=48, height=22, font=_FONT_S).pack(side="left", padx=2)

        self._refresh_block_list()

    def _refresh_block_list(self):
        """Reconstruye los botones de la lista de bloques según la categoría activa."""
        cat_filter = self._blk_cat_var.get()
        frame = self._blk_list_frame
        for w in frame.winfo_children():
            w.destroy()
        self._blk_btn_map.clear()

        selected = self._blk_selected.get()

        for cat, nombre, emoji, bdef in BLOCK_LIBRARY:
            if cat_filter != "Todos" and cat != cat_filter:
                continue
            is_sel = (bdef.name == selected)
            bg = "#7C3AED" if is_sel else UI_CARD
            btn = tk.Button(
                frame,
                text=f"{emoji} {nombre}",
                anchor="w",
                bg=bg, activebackground="#6D28D9",
                fg="white" if is_sel else UI_TEXT,
                font=("Arial", 9),
                bd=0, relief="flat",
                command=lambda n=bdef.name: self._select_block(n),
            )
            btn.pack(fill="x", pady=1, padx=2)
            self._blk_btn_map[bdef.name] = btn

    def _select_block(self, block_name: str):
        """Marca un bloque como activo en el panel y activa la herramienta INSERT."""
        self._blk_selected.set(block_name)
        self._refresh_block_list()
        self._ejecutar_accion("insert", block_name)

    def _get_selected_block_def(self) -> "BlockDef | None":
        """Retorna el BlockDef seleccionado en el panel (biblioteca o bloque propio)."""
        name = self._blk_selected.get()
        if not name:
            return None
        # Buscar primero en bloques propios del dibujo, luego en biblioteca
        if name in self.block_defs:
            return self.block_defs[name]
        for _, _, _, bdef in BLOCK_LIBRARY:
            if bdef.name == name:
                return bdef
        return None

    # ── Comando INSERT ────────────────────────────────────────────────────────

    def _cmd_insert(self, arg: str = ""):
        """Activa la herramienta INSERT. Si arg es nombre de bloque, lo preselecciona."""
        # Si se especificó bloque por argumento, buscarlo
        if arg:
            arg_up = arg.strip().upper()
            # Buscar en bloques del dibujo
            match = next((n for n in self.block_defs if n.upper() == arg_up), None)
            if not match:
                # Buscar en biblioteca
                match = next((bd.name for _, _, _, bd in BLOCK_LIBRARY
                               if bd.name.upper() == arg_up), None)
            if match:
                self._blk_selected.set(match)

        bdef = self._get_selected_block_def()
        if bdef is None:
            # Ningún bloque seleccionado → mostrar dialog de selección
            self._abrir_selector_bloque()
            return

        # Registrar el bloque en block_defs del dibujo si viene de la biblioteca
        if bdef.name not in self.block_defs:
            self.block_defs[bdef.name] = bdef

        self._op_mode = "insert_place"
        self._op_data = {"block_name": bdef.name}
        try:
            sc = float(self._blk_scale_var.get())
        except Exception:
            sc = 1.0
        try:
            ang = float(self._blk_angle_var.get())
        except Exception:
            ang = 0.0
        self._op_data["scale"] = sc
        self._op_data["angle"] = ang
        self._echo(f"INSERT '{bdef.name}': clic para colocar  (ESC=cancelar, R=rotar 90°)")

    # ── Biblioteca externa de bloques ─────────────────────────────────────────

    def _lib_info_text(self) -> str:
        """Texto informativo para el panel: carpeta y cantidad de bloques."""
        if not self._block_lib_path:
            return "Sin biblioteca configurada"
        n = len(self._block_lib_index)
        nombre = os.path.basename(self._block_lib_path) or self._block_lib_path
        return f"📂 {nombre}  ({n} bloques)" if n else f"📂 {nombre}  (escaneando…)"

    def _escanear_biblioteca(self):
        """Escanea la carpeta de biblioteca y construye el índice en hilo secundario."""
        if not self._block_lib_path or not os.path.isdir(self._block_lib_path):
            return

        def _worker():
            index = []
            base = self._block_lib_path
            for root_dir, dirs, files in os.walk(base):
                dirs.sort()
                # Categoría = nombre de subcarpeta relativa; raíz = "General"
                rel = os.path.relpath(root_dir, base)
                cat = "General" if rel == "." else rel.replace(os.sep, " / ")
                for fname in sorted(files):
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in (".dxf", ".dwg"):
                        nombre = os.path.splitext(fname)[0]
                        ruta   = os.path.join(root_dir, fname)
                        index.append((cat, nombre, ruta))
            self.root.after(0, lambda: self._aplicar_indice_biblioteca(index))

        import threading as _th
        _th.Thread(target=_worker, daemon=True).start()

    def _aplicar_indice_biblioteca(self, index: list):
        """Aplica el índice escaneado en el hilo principal."""
        self._block_lib_index = index
        if hasattr(self, '_lbl_lib_info'):
            self._lbl_lib_info.configure(text=self._lib_info_text())

    def _configurar_biblioteca(self):
        """Diálogo para elegir la carpeta de biblioteca."""
        from tkinter import filedialog
        carpeta = self._open_native_dialog(filedialog.askdirectory,
            title="Seleccionar carpeta de biblioteca de bloques",
            initialdir=self._block_lib_path or os.path.expanduser("~"),
            parent=self.root)
        if not carpeta:
            return
        self._block_lib_path  = carpeta
        self._block_lib_cache = {}
        self._block_lib_index = []
        # Guardar en settings.json
        import json
        cfg = self._leer_config_ia()
        cfg["block_lib_path"] = carpeta
        path = self._cfg_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        if hasattr(self, '_lbl_lib_info'):
            self._lbl_lib_info.configure(text=self._lib_info_text())
        self._echo(f"Biblioteca: {carpeta}  — escaneando…")
        self._escanear_biblioteca()

    def _cargar_bloque_desde_archivo(self, ruta: str) -> "BlockDef | None":
        """Carga un DXF/DWG y retorna su BlockDef. Usa caché en memoria."""
        if ruta in self._block_lib_cache:
            return self._block_lib_cache[ruta]
        try:
            ext = os.path.splitext(ruta)[1].lower()
            ruta_dxf = ruta
            if ext == ".dwg":
                from cad.dwg_converter import dwg_a_dxf
                ruta_dxf = dwg_a_dxf(ruta)   # convierte a DXF temporal
            from cad.dxf_import import importar_dxf
            res = importar_dxf(ruta_dxf, encoding="utf-8")
            nombre = os.path.splitext(os.path.basename(ruta))[0]
            # Si el DXF ya tiene bloques definidos, usar el primero
            if res.block_defs:
                bdef = next(iter(res.block_defs.values()))
                bdef.name = nombre
            else:
                # Todas las entidades del DXF → un bloque
                from cad.entities import BlockDef as _BD
                bdef = _BD(name=nombre, base_point=(0.0, 0.0),
                           entities=res.entities)
            self._block_lib_cache[ruta] = bdef
            return bdef
        except Exception as ex:
            self._echo(f"!! No se pudo cargar '{os.path.basename(ruta)}': {ex}")
            return None

    def _abrir_selector_bloque(self):
        """Diálogo flotante: biblioteca de bloques + opciones de escala/ángulo."""
        try:
            self._abrir_selector_bloque_impl()
        except Exception as ex:
            import traceback
            self._echo(f"!! Error abriendo biblioteca: {ex}")
            traceback.print_exc()

    def _abrir_selector_bloque_impl(self):
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Insertar bloque")
        dlg.resizable(True, True)
        dlg.attributes("-topmost", True)
        w, h = 380, 580
        rx = self.root.winfo_x() + (self.root.winfo_width()  - w) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{rx}+{ry}")
        dlg.minsize(320, 400)

        _F   = ctk.CTkFont(size=11, weight="bold")
        _FS  = ctk.CTkFont(size=10)
        _FSS = ctk.CTkFont(size=9)

        ctk.CTkLabel(dlg, text="Biblioteca de bloques",
                     font=_F, text_color=UI_TEXT).pack(pady=(12, 4))

        # ── Opciones de transformación ─────────────────────────────────────
        opts = ctk.CTkFrame(dlg, fg_color=UI_CARD, corner_radius=8)
        opts.pack(fill="x", padx=14, pady=(0, 6))
        opts.columnconfigure((1, 3), weight=1)
        ctk.CTkLabel(opts, text="Escala:", font=_FSS,
                     text_color=UI_TEXT2).grid(row=0, column=0, padx=(10,4), pady=6, sticky="e")
        _sc_var = tk.StringVar(value=self._blk_scale_var.get())
        ctk.CTkEntry(opts, textvariable=_sc_var, width=60, height=24,
                     font=_FSS).grid(row=0, column=1, padx=4, pady=6, sticky="w")
        ctk.CTkLabel(opts, text="Ángulo°:", font=_FSS,
                     text_color=UI_TEXT2).grid(row=0, column=2, padx=(10,4), pady=6, sticky="e")
        _ang_var = tk.StringVar(value=self._blk_angle_var.get())
        ctk.CTkEntry(opts, textvariable=_ang_var, width=60, height=24,
                     font=_FSS).grid(row=0, column=3, padx=(4,10), pady=6, sticky="w")

        # ── Barra de búsqueda + filtro de categoría ────────────────────────
        search_row = ctk.CTkFrame(dlg, fg_color="transparent")
        search_row.pack(fill="x", padx=14, pady=(0, 4))
        _buscar_var = tk.StringVar()
        ctk.CTkEntry(search_row, textvariable=_buscar_var, placeholder_text="🔍 Buscar…",
                     height=26, font=_FSS).pack(side="left", fill="x", expand=True, padx=(0, 4))

        # Categorías: Mi dibujo + biblioteca interna + biblioteca externa
        _cats_ext  = list(dict.fromkeys(cat for cat, _, _ in self._block_lib_index))
        _cats_int  = list(dict.fromkeys(cat for cat, *_ in BLOCK_LIBRARY))
        _todas_cats = (["Todos", "Mi dibujo"] + _cats_ext + _cats_int) if _cats_ext else \
                      ["Todos", "Mi dibujo"] + _cats_int
        cat_var = tk.StringVar(value="Todos")
        cat_om  = ctk.CTkOptionMenu(search_row, values=_todas_cats,
                                    variable=cat_var, width=130, height=26, font=_FSS,
                                    command=lambda v: _rebuild(v, _buscar_var.get()))
        cat_om.pack(side="left")
        _buscar_var.trace_add("write", lambda *_: _rebuild(cat_var.get(), _buscar_var.get()))

        sf_ref = [None]

        def _rebuild(cat_filter: str, buscar: str):
            if sf_ref[0] is None:
                return
            for child in sf_ref[0].winfo_children():
                child.destroy()
            buscar = buscar.strip().lower()

            def _btn(texto, cmd):
                if buscar and buscar not in texto.lower():
                    return
                ctk.CTkButton(sf_ref[0], text=texto, anchor="w",
                              width=330, height=28, fg_color=UI_CARD,
                              hover_color="#7C3AED", font=_FS,
                              command=cmd).pack(fill="x", pady=1, padx=2)

            def _header(txt):
                ctk.CTkLabel(sf_ref[0], text=txt, font=_FSS,
                             text_color=UI_TEXT2).pack(anchor="w", padx=4, pady=(6,1))

            # ── Bloques del dibujo activo ─────────────────────────────────
            propios = [n for n in self.block_defs
                       if not any(bd.name == n for _, _, _, bd in BLOCK_LIBRARY)]
            if propios and cat_filter in ("Todos", "Mi dibujo"):
                _header("── Mi dibujo ──")
                for nm in propios:
                    _btn(f"⊡  {nm}", lambda n=nm: _insertar_nombre(n))

            # ── Biblioteca externa (DWG/DXF de carpeta) ───────────────────
            if self._block_lib_index and cat_filter != "Mi dibujo":
                last_cat = None
                for cat, nombre, ruta in self._block_lib_index:
                    if cat_filter not in ("Todos",) and cat != cat_filter:
                        continue
                    if cat != last_cat:
                        _header(f"── {cat} ──")
                        last_cat = cat
                    ext = os.path.splitext(ruta)[1].upper().lstrip(".")
                    ico = "🧱" if ext == "DWG" else "📄"
                    _btn(f"{ico}  {nombre}  [{ext}]",
                         lambda r=ruta, n=nombre: _insertar_archivo(r, n))

            # ── Biblioteca interna hardcoded ───────────────────────────────
            if cat_filter not in ("Mi dibujo",) and not self._block_lib_index:
                last_cat = None
                for cat, nombre, emoji, bdef in BLOCK_LIBRARY:
                    if cat_filter not in ("Todos",) and cat != cat_filter:
                        continue
                    if cat != last_cat:
                        _header(f"── {cat} ──")
                        last_cat = cat
                    _btn(f"{emoji}  {nombre}", lambda n=bdef.name: _insertar_nombre(n))

            if not sf_ref[0].winfo_children():
                ctk.CTkLabel(sf_ref[0], text="Sin resultados",
                             font=_FSS, text_color=UI_TEXT2).pack(pady=20)

        def _sync_opts():
            self._blk_scale_var.set(_sc_var.get())
            self._blk_angle_var.set(_ang_var.get())

        def _insertar_nombre(block_name: str):
            _sync_opts()
            self._blk_selected.set(block_name)
            dlg.destroy()
            self._cmd_insert(block_name)

        def _insertar_archivo(ruta: str, nombre: str):
            _sync_opts()
            dlg.destroy()
            # Cargar el bloque en hilo secundario para no congelar UI
            self._echo(f"Cargando '{nombre}'…")
            import threading as _th
            def _worker():
                bdef = self._cargar_bloque_desde_archivo(ruta)
                if bdef is None:
                    return
                self.block_defs[bdef.name] = bdef
                self.root.after(0, lambda: (
                    self._blk_selected.set(bdef.name),
                    self._cmd_insert(bdef.name),
                ))
            _th.Thread(target=_worker, daemon=True).start()

        sf = ctk.CTkScrollableFrame(dlg, fg_color="transparent")
        sf.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        sf_ref[0] = sf
        _rebuild("Todos", "")

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkButton(btn_row, text="📁 Cambiar carpeta", width=140, height=28,
                      fg_color=UI_CARD, hover_color="#7C3AED", font=_FSS,
                      command=lambda: (dlg.destroy(), self._configurar_biblioteca())
                      ).pack(side="left")
        ctk.CTkButton(btn_row, text="Cancelar", width=100, height=28,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_FS,
                      command=dlg.destroy).pack(side="right")

    def _commit_insert(self, wx: float, wy: float):
        """Coloca el Insert en el canvas en la posición dada."""
        d = self._op_data
        ins = Insert(
            block_name = d["block_name"],
            x          = wx,
            y          = wy,
            scale_x    = d.get("scale", 1.0),
            scale_y    = d.get("scale", 1.0),
            angle      = d.get("angle", 0.0),
            layer      = self.active_layer,
        )
        self._push_undo()
        self.entities.append(ins)
        self._rebuild_snap_index()
        self._redraw()
        self._echo(f"INSERT '{d['block_name']}' colocado  (clic=otro, ESC=fin)")
        # Mantener el modo activo para colocar múltiples instancias

    def _commit_image(self):
        """Crea una entidad ImageRef con los datos acumulados en _op_data."""
        import os as _os
        x    = self._op_data.get("img_x", 0.0)
        y    = self._op_data.get("img_y", 0.0)
        w    = self._op_data.get("img_w", 1.0)
        h    = self._op_data.get("img_h", w)
        path = self._op_data.get("img_path", "")
        ent  = ImageRef(path=path, x=x, y=y, width=w, height=h,
                        angle=0.0, layer=self.active_layer)
        self._push_undo()
        self._add_entity(ent)
        self._echo(f"IMG — {_os.path.basename(path)}  {w:.3f}×{h:.3f} m  @ ({x:.3f},{y:.3f})")
        self._finish_op()

    def _draw_insert_preview(self, cv, sx: float, sy: float):
        """Preview del bloque bajo el cursor durante INSERT."""
        d    = getattr(self, '_op_data', {})
        name = d.get("block_name")
        if not name:
            return
        bdef = self.block_defs.get(name)
        if not bdef:
            return
        sc  = d.get("scale", 1.0)
        ang = d.get("angle", 0.0)
        # Transform temporal: crear Insert en posición de cursor y dibujar sus líneas
        tmp = Insert(block_name=name, x=self.mouse_w[0], y=self.mouse_w[1],
                     scale_x=sc, scale_y=sc, angle=ang, layer=self.active_layer)
        for e in bdef.entities:
            try:
                if isinstance(e, Line):
                    wx1, wy1 = tmp.transform_pt(e.x1, e.y1)
                    wx2, wy2 = tmp.transform_pt(e.x2, e.y2)
                    sx1, sy1 = self.w2s(wx1, wy1)
                    sx2, sy2 = self.w2s(wx2, wy2)
                    cv.create_line(sx1, sy1, sx2, sy2,
                                   fill="#A78BFA", width=1, dash=(4, 3), tags="dy")
                elif isinstance(e, Arc):
                    # Simplificar: arco como polilínea de 16 segmentos
                    steps = 16
                    pts = []
                    for i in range(steps + 1):
                        a = math.radians(e.start_angle +
                                         (e.end_angle - e.start_angle) * i / steps)
                        bx = e.cx + e.radius * math.cos(a)
                        by = e.cy + e.radius * math.sin(a)
                        wx_, wy_ = tmp.transform_pt(bx, by)
                        pts.extend(self.w2s(wx_, wy_))
                    if len(pts) >= 4:
                        cv.create_line(*pts, fill="#A78BFA", width=1,
                                       dash=(4, 3), tags="dy")
                elif isinstance(e, Circle):
                    wcx, wcy = tmp.transform_pt(e.cx, e.cy)
                    scx, scy = self.w2s(wcx, wcy)
                    r_px = e.radius * sc * self.scale
                    cv.create_oval(scx - r_px, scy - r_px,
                                   scx + r_px, scy + r_px,
                                   outline="#A78BFA", width=1, dash=(4, 3), tags="dy")
                elif isinstance(e, Polyline):
                    pts = []
                    for px, py in e.points:
                        wx_, wy_ = tmp.transform_pt(px, py)
                        pts.extend(self.w2s(wx_, wy_))
                    if len(pts) >= 4:
                        cv.create_line(*pts, fill="#A78BFA", width=1,
                                       dash=(4, 3), tags="dy")
                        if e.closed and len(pts) >= 6:
                            cv.create_line(pts[-2], pts[-1], pts[0], pts[1],
                                           fill="#A78BFA", width=1,
                                           dash=(4, 3), tags="dy")
            except Exception:
                pass
        # Punto de inserción
        cv.create_rectangle(sx - 4, sy - 4, sx + 4, sy + 4,
                             outline="#A78BFA", fill="", width=1, tags="dy")
        # ── Etiqueta flotante: ángulo y escala ───────────────────
        cv.create_text(sx + 18, sy - 20,
                       text=f"∠ {ang:.0f}°   ×{sc:.2f}",
                       fill="#A78BFA",
                       font=("Courier New", 9, "bold"),
                       anchor="nw", tags="dy")
        cv.create_text(sx + 18, sy - 8,
                       text="R = rotar 90°",
                       fill="#64748B",
                       font=("Courier New", 8),
                       anchor="nw", tags="dy")

    # ── Comando BLOCK (crear desde selección) ─────────────────────────────────

    def _cmd_block(self, arg: str = ""):
        """Crea un bloque a partir de las entidades seleccionadas."""
        if not self.selected:
            self._echo("BLOCK: seleccione entidades primero, luego ejecute BLOCK")
            return
        self._op_mode = "block_name"
        self._echo("BLOCK: escriba el nombre del bloque y presione Enter")

    def _commit_block_from_selection(self, name: str):
        """Convierte las entidades seleccionadas en un BlockDef + Insert."""
        if not name.strip():
            self._echo("!! BLOCK: nombre vacío — cancelado")
            self._op_mode = None
            return
        name = name.strip().upper()
        if name in self.block_defs:
            self._echo(f"!! BLOCK: '{name}' ya existe — use otro nombre")
            return

        # Calcular centroide como base_point
        xs, ys = [], []
        for e in self.selected:
            for pt in (e.bbox_pts() or []):
                xs.append(pt[0]); ys.append(pt[1])
        if not xs:
            self._echo("!! BLOCK: no se pudo calcular base point")
            self._op_mode = None
            return
        bx = sum(xs) / len(xs)
        by = sum(ys) / len(ys)

        # Convertir entidades a coordenadas locales
        import copy as _copy
        local_ents = []
        for e in self.selected:
            le = _copy.deepcopy(e)
            le = le.translated(-bx, -by)
            local_ents.append(le)

        bdef = BlockDef()
        bdef.name       = name
        bdef.base_point = (bx, by)
        bdef.entities   = local_ents
        self.block_defs[name] = bdef

        # Reemplazar entidades originales con un Insert
        self._push_undo()
        for e in self.selected:
            self.entities.remove(e)
        ins = Insert(block_name=name, x=bx, y=by,
                     scale_x=1.0, scale_y=1.0, angle=0.0,
                     layer=self.active_layer)
        self.entities.append(ins)
        self.selected = [ins]

        self._blk_selected.set(name)

        self._op_mode = None
        self._rebuild_snap_index()
        self._redraw()
        self._echo(f"BLOCK '{name}' creado — {len(local_ents)} entidades")

    # ── EATTEDIT ──────────────────────────────────────────────────────────────

    def _cmd_eattedit(self, arg: str = ""):
        """EATTEDIT — editar atributos de un bloque.  Clic sobre un Insert."""
        # Si hay exactamente un Insert seleccionado, abrir el diálogo directamente
        sel_inserts = [e for e in self.selected
                       if isinstance(e, Insert) and getattr(e, 'attribs', None)]
        if sel_inserts:
            self._open_eattedit_dialog(sel_inserts[0])
            return
        self._op_mode = "eattedit_pick"
        self._lbl_op.configure(text="EATTEDIT — Clic en bloque con atributos para editar:")
        self._update_prompt()
        self.canvas.focus_set()
        self._echo("EATTEDIT: haga clic en un bloque con atributos")

    def _open_eattedit_dialog(self, ins: "Insert"):
        """Abre el Enhanced Attribute Editor para un Insert."""
        import tkinter as tk

        bdef = self.block_defs.get(ins.block_name)
        attribs = getattr(ins, 'attribs', [])

        # Si el bloque no tiene attribs estructurados, informar
        if not attribs:
            # Intentar mostrar los AttDefs del bloque como referencia
            attdefs = getattr(bdef, 'attdefs', []) if bdef else []
            if not attdefs:
                self._echo(f"EATTEDIT: el bloque '{ins.block_name}' no tiene atributos definidos")
                return

        win = tk.Toplevel(self.root)
        win.title("Enhanced Attribute Editor")
        win.configure(bg="#1E293B")
        win.resizable(False, False)
        win.wm_attributes("-topmost", True)

        UI_BG    = "#1E293B"
        UI_CARD  = "#334155"
        UI_TEXT  = "#F8FAFC"
        UI_ACC   = "#2563EB"
        UI_DIM   = "#94A3B8"
        UI_ENTRY = "#0F172A"

        # ── Encabezado ────────────────────────────────────────────────
        hdr = tk.Frame(win, bg=UI_BG, pady=6, padx=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Block:", bg=UI_BG, fg=UI_DIM,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Label(hdr, text=f"  {ins.block_name}", bg=UI_BG, fg=UI_TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        # ── Tabla de atributos ────────────────────────────────────────
        tbl = tk.Frame(win, bg=UI_CARD, padx=2, pady=2)
        tbl.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        # Cabecera de columnas
        for col, label, w in [("tag", "Tag", 8), ("prompt", "Prompt", 14), ("value", "Value", 22)]:
            tk.Label(tbl, text=label, bg=UI_ACC, fg=UI_TEXT,
                     font=("Segoe UI", 8, "bold"), width=w,
                     anchor="w", padx=6).grid(row=0, column={"tag":0,"prompt":1,"value":2}[col],
                                               sticky="ew", padx=1, pady=1)

        # Filas de atributos
        entry_vars = []   # [(attrib, StringVar)]

        # Construir mapa tag→attdef para el prompt
        attdef_map = {}
        if bdef:
            for ad in getattr(bdef, 'attdefs', []):
                attdef_map[ad.tag] = ad

        for row_idx, att in enumerate(attribs, start=1):
            ad  = attdef_map.get(att.tag)
            prompt_txt = ad.prompt if ad else att.tag

            tk.Label(tbl, text=att.tag, bg=UI_CARD, fg=UI_TEXT,
                     font=("Segoe UI", 9), width=8,
                     anchor="w", padx=6).grid(row=row_idx, column=0, sticky="ew", padx=1, pady=1)
            tk.Label(tbl, text=prompt_txt, bg=UI_CARD, fg=UI_DIM,
                     font=("Segoe UI", 9), width=14,
                     anchor="w", padx=6).grid(row=row_idx, column=1, sticky="ew", padx=1, pady=1)

            var = tk.StringVar(value=att.value)
            ent = tk.Entry(tbl, textvariable=var, bg=UI_ENTRY, fg=UI_TEXT,
                           insertbackground=UI_TEXT, relief="flat",
                           font=("Segoe UI", 9), width=22)
            ent.grid(row=row_idx, column=2, sticky="ew", padx=2, pady=2)
            entry_vars.append((att, var))

        tbl.columnconfigure(2, weight=1)

        # ── Botones ───────────────────────────────────────────────────
        def _apply():
            changed = False
            for att, var in entry_vars:
                new_val = var.get().strip()
                if new_val != att.value:
                    att.value = new_val
                    # Actualizar el Text entity de display si existe
                    txt = getattr(att, '_text_ref', None)
                    if txt is not None and txt in self.entities:
                        txt.content = new_val.upper()
                    changed = True
            if changed:
                self._push_undo()
                self._rebuild_snap_index()
                self._redraw_static()
                self._echo(f"EATTEDIT: atributos de '{ins.block_name}' actualizados")

        def _ok():
            _apply()
            win.destroy()

        btn_bar = tk.Frame(win, bg=UI_BG, pady=8)
        btn_bar.pack(fill="x", padx=12)
        for label, cmd, col in [("Apply", _apply, UI_ACC),
                                 ("OK",    _ok,    "#16A34A"),
                                 ("Cancel",win.destroy,"#475569")]:
            tk.Button(btn_bar, text=label, command=cmd,
                      bg=col, fg=UI_TEXT, activebackground=col,
                      relief="flat", font=("Segoe UI", 9, "bold"),
                      padx=14, pady=4, cursor="hand2").pack(side="left", padx=4)

        # ── Centrar sobre el canvas ───────────────────────────────────
        win.update_idletasks()
        cx = self.root.winfo_x() + self.root.winfo_width()  // 2
        cy = self.root.winfo_y() + self.root.winfo_height() // 2
        w, h = win.winfo_width(), win.winfo_height()
        win.geometry(f"+{cx - w//2}+{cy - h//2}")

        # Foco en primer Entry
        if entry_vars:
            entry_vars[0][0]   # attrib ref
            tbl.grid_slaves(row=1, column=2)[0].focus_set()

        win.grab_set()
        self.root.wait_window(win)

    def _build_layer_panel(self):
        """Reconstruye el panel en batches de 15: los primeros aparecen
        casi instantáneamente, el resto en background sin freezear la UI."""
        # Si el panel está colapsado: marcar para rebuild al expandir, no crear widgets
        if not getattr(self, '_layer_panel_expanded', True):
            self._layer_needs_rebuild = True
            # Actualizar solo el contador del botón toggle (costo ~0)
            n = len(self.layers)
            try:
                self._layer_toggle_btn.configure(
                    text=f"▶  {n} capa{'s' if n != 1 else ''}")
            except Exception:
                pass
            return

        # Cancelar build anterior si todavía está en curso
        if getattr(self, '_layer_build_id', None):
            try:
                self.root.after_cancel(self._layer_build_id)
            except Exception:
                pass
            self._layer_build_id = None

        # Suprimir <Configure> durante destroy masivo:
        # 200 destroy() sin supresión → 200 scrollregion recalcs → freeze ~500ms
        self._layer_suppress_configure = True
        try:
            for w in self._layer_frame.winfo_children():
                w.destroy()
        finally:
            self._layer_suppress_configure = False
        self._layer_card_refs = {}

        todas_ord = sorted(
            self.layers.items(),
            key=lambda kv: (kv[0] != self.active_layer, kv[0].upper())
        )
        total = len(todas_ord)

        # Encabezado-contador (tk nativo — CTkLabel._draw() bloqueaba en cada rebuild)
        self._layer_total_lbl = tk.Label(
            self._layer_frame,
            text=f"{total} capa{'s' if total != 1 else ''}",
            font=("Arial", 9), fg=UI_TEXT2, bg=UI_PAN, anchor="w")
        self._layer_total_lbl.pack(anchor="w", padx=6, pady=(2, 4))

        # Footer pre-creado (siempre al final, reordenado por _filter)
        self._layer_new_sep = tk.Frame(self._layer_frame, height=1, bg=UI_BORD)
        self._layer_new_btn = tk.Button(
            self._layer_frame, text="+ Nueva capa",
            bg=UI_CARD, activebackground=UI_ACC,
            fg=UI_TEXT, font=("Arial", 10),
            bd=0, relief="flat",
            command=self._nueva_capa)

        _BATCH = 15   # widgets por tick (≈8 ms / batch → UI fluida)
        idx = [0]
        # Snapshot de la lista para que no cambie entre batches
        _todo = todas_ord

        def _next_batch():
            self._layer_build_id = None
            start = idx[0]
            end   = min(start + _BATCH, total)
            for i in range(start, end):
                name, lyr = _todo[i]
                self._layer_card_refs[name] = self._make_layer_card(name, lyr)
            idx[0] = end
            # Actualizar layout con lo que hay hasta ahora
            self._filter_layer_panel()
            if end < total:
                # Más batches pendientes — ceder al event loop y continuar
                self._layer_build_id = self.root.after(0, _next_batch)
            else:
                # Solo reconstruir panel de propiedades si hay selección activa
                # (evita crear CTkOptionMenu innecesarios al cargar un DXF sin sel)
                if any(getattr(e, 'selected', False) for e in self.entities):
                    self._update_prop_layer_om()

        # Primer batch: ejecutar en el siguiente tick (libera el frame actual)
        self._layer_build_id = self.root.after(0, _next_batch)

    def _filter_layer_panel(self):
        """Filtra el panel de capas con pack/pack_forget sin destruir widgets.
        ~100× más rápido que _build_layer_panel() — llamar desde el buscador."""
        refs = getattr(self, '_layer_card_refs', None)
        if not refs:
            return   # panel aún no construido

        filtro = ""
        try:
            filtro = self._layer_filter_var.get().strip().upper()
        except Exception:
            pass

        total = len(self.layers)
        _canvas = getattr(self, '_layer_canvas', None)

        # Suprimir <Configure> durante los pack/pack_forget masivos:
        # 200 pack_forget() sin supresión → 200 scrollregion recalcs → freeze ~500ms
        self._layer_suppress_configure = True
        try:
            # 1. Quitar TODAS las cards y el footer del layout
            for card in refs.values():
                card.pack_forget()
            sep = getattr(self, '_layer_new_sep', None)
            btn = getattr(self, '_layer_new_btn', None)
            if sep: sep.pack_forget()
            if btn: btn.pack_forget()

            # 2. Re-poner las cards que coinciden (en orden de inserción = sorted)
            visibles = 0
            for name, card in refs.items():
                if not filtro or filtro in name.upper():
                    card.pack(fill="x", pady=1, padx=2)
                    visibles += 1

            # 3. Re-poner footer al final
            if sep: sep.pack(fill="x", pady=4)
            if btn: btn.pack(fill="x", padx=4)
        finally:
            self._layer_suppress_configure = False
            # Una sola actualización del scrollregion al final
            if _canvas:
                _canvas.configure(scrollregion=_canvas.bbox("all"))

        # 4. Actualizar contadores (interior del scroll + botón toggle del header)
        lbl = getattr(self, '_layer_total_lbl', None)
        if lbl:
            lbl.configure(
                text=(f"{visibles} de {total} capas" if filtro
                      else f"{total} capa{'s' if total != 1 else ''}"))
        try:
            self._layer_toggle_btn.configure(
                text=f"▼  {total} capa{'s' if total != 1 else ''}")
        except Exception:
            pass


    def _refresh_om_capa(self, values: list):
        """Actualiza las opciones del dropdown de capa activa (tk.OptionMenu nativo)."""
        menu = self._om_capa["menu"]
        menu.delete(0, "end")
        for v in values:
            menu.add_command(label=v,
                             command=lambda val=v: (
                                 self._om_capa_var.set(val),
                                 self._activar_capa(val)))

    def _activar_capa(self, name: str):
        old = self.active_layer
        self.active_layer = name
        self._om_capa_var.set(name)
        # Reconstruir solo las 2 tarjetas afectadas (vieja activa → inactiva, nueva → activa).
        # Evita destruir y recrear las 200 tarjetas por un solo clic de capa.
        refs = getattr(self, '_layer_card_refs', {})
        if refs and name in refs:
            for n in (old, name):
                lyr = self.layers.get(n)
                if lyr and n in refs:
                    refs[n].destroy()
                    refs[n] = self._make_layer_card(n, lyr)
            self._filter_layer_panel()
        else:
            self._build_layer_panel()

    def _layiso(self):
        """Aisla la capa activa (delega a _ejecutar_accion)."""
        self._ejecutar_accion("layer_iso", "")

    def _layon(self):
        """Muestra todas las capas (delega a _ejecutar_accion)."""
        self._ejecutar_accion("layer_on", "")

    def _focus_ia_input(self):
        """Enfoca el campo de comando con '/' para iniciar consulta IA."""
        self._cmd_var.set("/")
        self._cmd_entry.focus_set()
        self._cmd_entry.icursor("end")
        self._echo("Asistente IA — escribe tu consulta después del  /  y presiona Enter")

    def _nueva_capa(self):
        dlg = ctk.CTkInputDialog(text="Nombre de la nueva capa:", title="Nueva capa")
        nombre = dlg.get_input()
        if not nombre:
            return
        nombre = nombre.strip().upper()
        if not nombre:
            return
        if nombre in self.layers:
            self._echo(f"!! Capa '{nombre}' ya existe"); return
        # Color picker
        from tkinter.colorchooser import askcolor
        resultado = askcolor(color="#FFFFFF", title=f"Color de capa '{nombre}'")
        color = "#FFFFFF"
        if resultado and resultado[1]:
            color = resultado[1].upper()
        self._push_undo()
        self.layers[nombre] = Layer(nombre, color, 1)
        self._refresh_om_capa(list(self.layers.keys()))
        self._activar_capa(nombre)
        self._echo(f"Capa '{nombre}' creada — color {color}")

    def _cambiar_capa_sel(self):
        sel = [i for i, e in enumerate(self.entities) if e.selected]
        if not sel:
            self._echo("Seleccione entidades primero"); return
        self._push_undo()
        for i in sel:
            self.entities[i].layer = self.active_layer
        self._redraw_static()
        self._echo(f"Capa → {self.active_layer}")
        self._update_prop_layer_om()

    def _prop_cambiar_capa(self, nueva_capa: str):
        """Mueve la selección a la capa elegida en el dropdown de propiedades."""
        if nueva_capa == "—" or nueva_capa not in self.layers:
            return
        sel = [i for i, e in enumerate(self.entities) if e.selected]
        if not sel:
            return
        self._push_undo()
        for i in sel:
            self.entities[i].layer = nueva_capa
        self._redraw_static()
        self._echo(f"Capa → {nueva_capa}")

    def _update_prop_layer_om(self):
        """Actualiza el panel de propiedades según la selección actual."""
        sel = [e for e in self.entities if e.selected]
        self._rebuild_prop_panel(sel)

    # ─── Panel de propiedades dinámico ───────────────────────────────────

    def _rebuild_prop_panel(self, sel=None):
        """Reconstruye el formulario de propiedades según la selección actual."""
        if not hasattr(self, "_prop_frame"):
            return
        if sel is None:
            sel = [e for e in self.entities if e.selected]

        pf = self._prop_frame
        BG  = UI_PAN   # fondo del panel (mismo que _prop_frame)
        BG2 = UI_CARD  # fondo de filas de campo

        # Ocultar widgets anteriores con pack_forget (instantáneo, sin cascade de destroy).
        # El destroy real se difiere a after(0) para no bloquear el hilo principal 500ms.
        # Causa: destroy() × N widgets con resolución de geometría = freeze en _apply_grip_move.
        _old_children = list(pf.winfo_children())
        self._prop_suppress_configure = True
        try:
            for w in _old_children:
                try: w.pack_forget()
                except Exception: pass
        finally:
            self._prop_suppress_configure = False

        if _old_children:
            def _deferred_destroy(_wlist=_old_children):
                for w in _wlist:
                    try: w.destroy()
                    except Exception: pass
            self.root.after(0, _deferred_destroy)

        _F  = ("Arial", 10)
        _FB = ("Arial", 10, "bold")
        _FS = ("Courier New", 9)
        _FL = ("Arial", 9)

        def _sep():
            tk.Frame(pf, height=1, bg=UI_BORD).pack(fill="x", padx=4, pady=3)

        def _lbl(txt):
            tk.Label(pf, text=txt, font=_FL, fg=UI_TEXT2,
                     bg=BG, anchor="w").pack(fill="x", padx=6, pady=(2, 0))

        def _field_row(label, sv, readonly=False):
            r = tk.Frame(pf, bg=BG2)
            r.pack(fill="x", padx=4, pady=1)
            tk.Label(r, text=label, font=_FL, fg=UI_TEXT2,
                     bg=BG2, width=10, anchor="e").pack(side="left", padx=(0, 4))
            st = "disabled" if readonly else "normal"
            ent = tk.Entry(r, textvariable=sv, font=_FS, state=st,
                           bg=UI_BG, fg=UI_TEXT, insertbackground=UI_TEXT,
                           disabledbackground=UI_CARD, disabledforeground=UI_TEXT2,
                           relief="flat", bd=1,
                           highlightthickness=1, highlightbackground=UI_BORD)
            ent.pack(side="left", fill="x", expand=True)

        def _option_menu(parent, var, values, command=None, **pack_kw):
            """Selector lazy: muestra valor actual + botón ▼ que abre un popup solo al hacer click.
            Evita los N add_command() de tk.OptionMenu que bloquean el hilo principal al crearse."""
            f = tk.Frame(parent, bg=UI_CARD, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=UI_BORD)
            f.pack(**pack_kw)
            lbl = tk.Label(f, textvariable=var, font=_F, fg=UI_TEXT, bg=UI_CARD,
                           anchor="w")
            lbl.pack(side="left", fill="x", expand=True, padx=(4, 0))

            def _open_popup():
                m = tk.Menu(f, tearoff=0, bg=UI_CARD, fg=UI_TEXT,
                            activebackground=UI_ACC, activeforeground=UI_TEXT, font=_F)
                for v in values:
                    def _pick(val=v):
                        var.set(val)
                        if command:
                            command(val)
                    m.add_command(label=v, command=_pick)
                try:
                    m.tk_popup(arr.winfo_rootx(),
                               arr.winfo_rooty() + arr.winfo_height())
                finally:
                    m.grab_release()

            arr = tk.Button(f, text="▼", font=("Arial", 8), bg=UI_CARD, fg=UI_TEXT2,
                            bd=0, relief="flat", command=_open_popup, padx=4, pady=0)
            arr.pack(side="right")
            return f

        # ── Sin selección ─────────────────────────────────────────
        if not sel:
            tk.Label(pf, text="—  Ninguna entidad",
                     font=_FS, fg=UI_TEXT2, bg=BG).pack(
                         padx=6, pady=6, anchor="w")
            return

        # ── Múltiples entidades ───────────────────────────────────
        if len(sel) > 1:
            tk.Label(pf, text=f"{len(sel)} entidades sel.",
                     font=_FB, fg=UI_TEXT, bg=BG).pack(
                         anchor="w", padx=6, pady=(4, 2))
            _lbl("Capa:")
            lv = tk.StringVar()
            capas = {e.layer for e in sel}
            lv.set(next(iter(capas)) if len(capas) == 1 else "(varios)")
            _option_menu(pf, lv, list(self.layers.keys()),
                         command=self._prop_cambiar_capa,
                         fill="x", padx=6, pady=(0, 2))
            tk.Button(pf, text="→ Mover a capa activa",
                      bg=UI_CARD, activebackground=UI_ACC,
                      fg=UI_TEXT, font=_F, bd=0, relief="flat",
                      command=self._cambiar_capa_sel
                      ).pack(fill="x", padx=6, pady=(0, 4))
            return

        # ── Una sola entidad ──────────────────────────────────────
        e   = sel[0]
        idx = next((i for i, ent in enumerate(self.entities) if ent is e), None)

        etype_lbl = {
            "Line": "LÍNEA", "Polyline": "POLILÍNEA", "Circle": "CÍRCULO",
            "Arc": "ARCO", "Text": "TEXTO", "Dimension": "COTA",
            "Hatch": "HATCH/RELLENO",
        }.get(type(e).__name__, type(e).__name__.upper())

        tk.Label(pf, text=etype_lbl, font=_FB,
                 fg=UI_TEXT, bg=BG).pack(anchor="w", padx=6, pady=(4, 2))

        # ── Capa ──────────────────────────────────────────────────
        _lbl("Capa:")
        lv = tk.StringVar(value=e.layer)
        _option_menu(pf, lv, list(self.layers.keys()),
                     command=self._prop_cambiar_capa,
                     fill="x", padx=6, pady=(0, 2))

        # ── Color ─────────────────────────────────────────────────
        col_hex = e.color if (e.color and e.color.startswith("#")) else "#FFFFFF"
        col_var = tk.StringVar(value=col_hex)
        _lbl("Color:")
        _cf = tk.Frame(pf, bg=BG)
        _cf.pack(fill="x", padx=6, pady=(0, 2))

        def _pick_color(_col_var=col_var, _sw=None):
            from tkinter import colorchooser
            c = colorchooser.askcolor(color=_col_var.get(), title="Color")[1]
            if c:
                _col_var.set(c.upper())
                if _sw:
                    _sw.configure(bg=c.upper(), text=c.upper(),
                                  fg="#000000" if _is_light(c) else "#FFFFFF")

        swatch = tk.Button(
            _cf, text=col_hex, width=14,
            bg=col_hex, fg="#000000" if _is_light(col_hex) else "#FFFFFF",
            font=("Courier New", 8), bd=0, relief="flat",
            command=lambda: _pick_color(col_var, swatch))
        swatch.pack(side="left")

        # ── Linetype override ─────────────────────────────────────
        _lbl("Tipo de línea:")
        _lt_options = ["bylayer"] + list(_LINETYPES.keys())
        lt_val = getattr(e, "linetype", "bylayer") or "bylayer"
        lt_var = tk.StringVar(value=lt_val if lt_val in _lt_options else "bylayer")
        _option_menu(pf, lt_var, _lt_options, fill="x", padx=6, pady=(0, 2))

        # ── Grosor override ───────────────────────────────────────
        _lbl("Grosor (0=capa):")
        lw_val = str(getattr(e, "linewidth", 0) or 0)
        lw_var = tk.StringVar(value=lw_val)
        _option_menu(pf, lw_var, ["0","1","2","3","4","5"],
                     anchor="w", padx=6, pady=(0, 2))

        _sep()

        # ── Campos específicos por tipo ───────────────────────────
        spec: dict[str, tk.StringVar] = {}

        if isinstance(e, Line):
            for nm, val in [("x1", e.x1), ("y1", e.y1),
                            ("x2", e.x2), ("y2", e.y2)]:
                sv = tk.StringVar(value=f"{val:.4f}")
                _field_row(nm, sv)
                spec[nm] = sv

        elif isinstance(e, Circle):
            for nm, val in [("cx", e.cx), ("cy", e.cy), ("r", e.radius)]:
                sv = tk.StringVar(value=f"{val:.4f}")
                _field_row(nm, sv)
                spec[nm] = sv

        elif isinstance(e, Arc):
            for nm, val in [("cx", e.cx), ("cy", e.cy), ("r", e.radius),
                            ("a_ini", e.start_ang), ("a_fin", e.end_ang)]:
                sv = tk.StringVar(value=f"{val:.4f}")
                _field_row(nm, sv)
                spec[nm] = sv
            sv_ccw = tk.StringVar(value="CCW" if e.ccw else "CW")
            _field_row("dir", sv_ccw, readonly=True)

        elif isinstance(e, Polyline):
            sv_n = tk.StringVar(value=str(len(e.points)))
            _field_row("verts", sv_n, readonly=True)
            _lbl("Cerrada:")
            cl_var = tk.BooleanVar(value=e.closed)
            tk.Checkbutton(pf, text="", variable=cl_var,
                           bg=BG, fg=UI_TEXT2, selectcolor=UI_ACC,
                           activebackground=BG, bd=0).pack(anchor="w", padx=6)
            spec["closed"] = cl_var

        elif isinstance(e, Spline):
            sv_n = tk.StringVar(value=str(len(e.points)))
            _field_row("pts ctrl", sv_n, readonly=True)
            _lbl("Cerrada:")
            cl_var = tk.BooleanVar(value=e.closed)
            tk.Checkbutton(pf, text="", variable=cl_var,
                           bg=BG, fg=UI_TEXT2, selectcolor=UI_ACC,
                           activebackground=BG, bd=0).pack(anchor="w", padx=6)
            spec["closed"] = cl_var

        elif isinstance(e, Text):
            sv_txt  = tk.StringVar(value=e.content)
            sv_h    = tk.StringVar(value=f"{e.height:.4f}")
            sv_ang  = tk.StringVar(value=f"{getattr(e,'angle',0.0):.2f}")
            sv_x    = tk.StringVar(value=f"{e.x:.4f}")
            sv_y    = tk.StringVar(value=f"{e.y:.4f}")
            _field_row("texto",   sv_txt)
            _field_row("alto",    sv_h)
            _field_row("ángulo",  sv_ang)
            _field_row("X",       sv_x)
            _field_row("Y",       sv_y)
            # Alineación horizontal
            _lbl("Alin. horiz.:")
            _HA_MAP = {"Izquierda": 0, "Centro": 1, "Derecha": 2}
            _HA_INV = {0: "Izquierda", 1: "Centro", 2: "Derecha"}
            _ha_cur = getattr(e, "halign", 0)
            sv_ha = tk.StringVar(value=_HA_INV.get(_ha_cur, "Izquierda"))
            _option_menu(pf, sv_ha, list(_HA_MAP.keys()),
                         fill="x", padx=6, pady=(0, 2))
            # Alineación vertical
            _lbl("Alin. vert.:")
            _VA_MAP = {"Baseline": 0, "Medio": 2, "Top": 3}
            _VA_INV = {0: "Baseline", 2: "Medio", 3: "Top"}
            _va_cur = getattr(e, "valign", 0)
            sv_va = tk.StringVar(value=_VA_INV.get(_va_cur, "Baseline"))
            _option_menu(pf, sv_va, list(_VA_MAP.keys()),
                         fill="x", padx=6, pady=(0, 2))
            spec.update({"texto": sv_txt, "alto": sv_h, "angulo": sv_ang,
                         "x": sv_x, "y": sv_y,
                         "ha": sv_ha, "ha_map": _HA_MAP,
                         "va": sv_va, "va_map": _VA_MAP})

        elif isinstance(e, Dimension):
            DIM_TYPES = ["H", "V", "A", "R", "D", "ANG", "ARC", "ORD"]
            sv_tipo = tk.StringVar(value=e.dim_type)
            _lbl("Tipo:")
            _option_menu(pf, sv_tipo, DIM_TYPES, fill="x", padx=6, pady=(0, 2))
            spec["tipo"] = sv_tipo

            med_val = e.measurement()
            med_str = f"{med_val:.2f}"
            if e.dim_type == "ANG":   med_str = f"{med_val:.1f}°"
            elif e.dim_type == "D":   med_str = f"Ø{med_val:.2f}"
            elif e.dim_type == "R":   med_str = f"R{med_val:.2f}"
            _lbl("Valor medido:")
            tk.Label(pf, text=med_str, font=_FS,
                     fg=UI_ACC, bg=BG).pack(anchor="w", padx=6, pady=(2, 0))

            _lbl("Estilo cota:")
            styles_list = list(self._leer_config_ia().get("dimstyles", {}).get("styles", {}).keys())
            sv_st = tk.StringVar(value=e.style)
            _option_menu(pf, sv_st, styles_list if styles_list else ["Arq-50"],
                         fill="x", padx=6, pady=(0, 2))
            spec["estilo"] = sv_st

            sv_ovr = tk.StringVar(value=e.text_override)
            _field_row("texto custom", sv_ovr)
            spec["ovr"] = sv_ovr

            _tp_cur = getattr(e, "text_pos", None)
            sv_tp = tk.StringVar(
                value=f"{_tp_cur[0]:.4f},{_tp_cur[1]:.4f}" if _tp_cur else "")
            _field_row("txt xy", sv_tp)
            _lbl("  (vacío = auto)")
            spec["text_pos"] = sv_tp

        elif isinstance(e, Hatch):
            PATTERNS = ["SOLID", "ANSI31", "LINES", "CROSS"]
            sv_pat   = tk.StringVar(value=e.pattern)
            sv_ang   = tk.StringVar(value=f"{e.angle:.1f}")
            sv_sc    = tk.StringVar(value=f"{e.scale:.4f}")
            sv_verts = tk.StringVar(value=str(len(e.boundary)))
            _lbl("Patrón:")
            _option_menu(pf, sv_pat, PATTERNS, fill="x", padx=6, pady=(0, 2))
            _field_row("ángulo", sv_ang)
            _field_row("escala", sv_sc)
            _field_row("verts",  sv_verts, readonly=True)
            spec.update({"pat": sv_pat, "ang": sv_ang, "sc": sv_sc})

        _sep()

        # ── Botón Aplicar ─────────────────────────────────────────
        def _aplicar():
            if idx is None or idx >= len(self.entities):
                return
            try:
                new_layer = lv.get() if lv.get() in self.layers else e.layer
                _cv = col_var.get()
                new_col = _cv if (_cv.startswith("#") or _cv.lower() == "bylayer") else e.color
                new_lt    = lt_var.get()
                new_lw    = int(lw_var.get())
                kw = dict(layer=new_layer, color=new_col, linetype=new_lt, linewidth=new_lw)

                if isinstance(e, Line):
                    new_e = Line(
                        x1=float(spec["x1"].get()), y1=float(spec["y1"].get()),
                        x2=float(spec["x2"].get()), y2=float(spec["y2"].get()), **kw)
                elif isinstance(e, Circle):
                    new_e = Circle(
                        cx=float(spec["cx"].get()), cy=float(spec["cy"].get()),
                        radius=abs(float(spec["r"].get())), **kw)
                elif isinstance(e, Arc):
                    new_e = Arc(
                        cx=float(spec["cx"].get()), cy=float(spec["cy"].get()),
                        radius=abs(float(spec["r"].get())),
                        start_ang=float(spec["a_ini"].get()),
                        end_ang=float(spec["a_fin"].get()),
                        ccw=e.ccw, **kw)
                elif isinstance(e, Polyline):
                    closed_val = spec["closed"].get() if isinstance(
                        spec.get("closed"), tk.BooleanVar) else e.closed
                    new_e = Polyline(points=e.points, closed=closed_val, **kw)
                elif isinstance(e, Spline):
                    closed_val = spec["closed"].get() if isinstance(
                        spec.get("closed"), tk.BooleanVar) else e.closed
                    new_e = Spline(points=e.points, closed=closed_val, **kw)
                elif isinstance(e, Text):
                    _ha_map = spec.get("ha_map", {"Izquierda": 0, "Centro": 1, "Derecha": 2})
                    _va_map = spec.get("va_map", {"Baseline": 0, "Medio": 2, "Top": 3})
                    _new_ha = _ha_map.get(spec["ha"].get(), getattr(e, "halign", 0)) if "ha" in spec else getattr(e, "halign", 0)
                    _new_va = _va_map.get(spec["va"].get(), getattr(e, "valign", 0)) if "va" in spec else getattr(e, "valign", 0)
                    _new_x  = float(spec["x"].get()) if "x" in spec else e.x
                    _new_y  = float(spec["y"].get()) if "y" in spec else e.y
                    new_e = Text(
                        x=_new_x, y=_new_y,
                        content=spec["texto"].get().strip().upper() or e.content,
                        height=abs(float(spec["alto"].get())),
                        angle=float(spec["angulo"].get()),
                        halign=_new_ha, valign=_new_va,
                        mtext_width=getattr(e, "mtext_width", 0.0), **kw)
                elif isinstance(e, Dimension):
                    _tp_str = spec.get("text_pos", tk.StringVar(value="")).get().strip()
                    _tp_new = None
                    if _tp_str:
                        try:
                            _tpx, _tpy = [float(v) for v in _tp_str.split(",")]
                            _tp_new = (_tpx, _tpy)
                        except Exception:
                            _tp_new = getattr(e, "text_pos", None)
                    new_tipo   = spec.get("tipo",   tk.StringVar(value=e.dim_type)).get()
                    new_estilo = spec.get("estilo",  tk.StringVar(value=e.style)).get()
                    new_e = Dimension(
                        p1=e.p1, p2=e.p2, pos=e.pos,
                        dim_type=new_tipo,
                        text_override=spec["ovr"].get(),
                        style=new_estilo, layer=new_layer,
                        text_pos=_tp_new)
                elif isinstance(e, Hatch):
                    new_e = Hatch(
                        boundary=e.boundary,
                        pattern=spec["pat"].get() if "pat" in spec else e.pattern,
                        angle=float(spec["ang"].get()) if "ang" in spec else e.angle,
                        scale=max(0.001, float(spec["sc"].get())) if "sc" in spec else e.scale,
                        layer=new_layer)
                else:
                    self._echo("!! Tipo sin soporte de edición"); return

                self._push_undo()
                new_e.selected = True
                self.entities[idx] = new_e
                self._rebuild_snap_index()
                self._grips = []; self._hot_grip = None; self._hover_grip = None
                self._rebuild_prop_panel([new_e])
                self._redraw()
                self._echo("Propiedades aplicadas ✓")
            except ValueError as ex:
                self._echo(f"!! Valor inválido: {ex}")

        tk.Button(pf, text="✓  Aplicar",
                  bg=UI_SUCC, activebackground="#15803D",
                  fg=UI_BG, font=_FB, bd=0, relief="flat",
                  command=_aplicar).pack(fill="x", padx=6, pady=(2, 6))

    # ─── Transformadas mundo↔pantalla (thin wrappers → cad.viewport) ────
    def w2s(self, wx, wy):
        return _vp.w2s(self.scale, self.offset_x, self.offset_y, wx, wy)

    def s2w(self, sx, sy):
        return _vp.s2w(self.scale, self.offset_x, self.offset_y, sx, sy)

    def w2s_array(self, points):
        return _vp.w2s_many(self.scale, self.offset_x, self.offset_y, points)

    def _center_view(self):
        W = self.root.winfo_width()  or 1200
        H = self.root.winfo_height() or 900
        self.offset_x = (W - 52 - 220) / 2
        self.offset_y = (H - 80) / 2

    def _cargar_terreno_proyecto(self) -> None:
        """
        Pre-carga el perímetro del lote y la zona de retiros al abrir el CAD Visor.

        Lee terreno_json_path desde settings.json. Si el archivo existe y tiene
        el campo 'poligono', agrega:
          • A-LOTE   — polilínea cerrada exacta del terreno
          • A-RETIRO — rectángulo bbox-inset con retiros mínimos (referencia aproximada)

        No hace nada si ya hay entidades (dibujo existente cargado).
        """
        if self.entities:          # dibujo existente → no sobreescribir
            return
        import json as _j
        cfg = self._leer_config_ia()
        tp  = cfg.get("terreno_json_path", "")
        if not tp or not os.path.exists(tp):
            return
        try:
            data = _j.load(open(tp, encoding="utf-8"))
        except Exception:
            return

        pol = data.get("poligono", [])
        if len(pol) < 3:
            return

        pts = [(float(p[0]), float(p[1])) for p in pol]

        # ── Asegurar capas ────────────────────────────────────────────
        from cad.entities import DEFAULT_LAYERS as _DL
        for lyr_name in ("A-LOTE", "A-RETIRO"):
            if lyr_name not in self.layers and lyr_name in _DL:
                self.layers[lyr_name] = _DL[lyr_name]

        # ── Perímetro del lote ────────────────────────────────────────
        self.entities.append(Polyline(points=pts, closed=True, layer="A-LOTE"))

        # ── Zona edificable (bbox inset por retiros mínimos) ──────────
        retiros = data.get("retiros", {})
        if retiros:
            rf = float(retiros.get("frontal",     0))
            rp = float(retiros.get("posterior",   0))
            rl = float(retiros.get("lateral_izq", 0))
            rr = float(retiros.get("lateral_der", 0))
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x0 = min(xs) + min(rl, rr)
            x1 = max(xs) - min(rl, rr)
            y0 = min(ys) + rp
            y1 = max(ys) - rf
            if x1 > x0 and y1 > y0:
                ret_pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                self.entities.append(
                    Polyline(points=ret_pts, closed=True, layer="A-RETIRO")
                )

        # ── Zoom para encuadrar el lote ───────────────────────────────
        # winfo devuelve 1 (no 0) antes del primer draw → usar max() en vez de 'or'
        W = max(self.canvas.winfo_width(),  800)
        H = max(self.canvas.winfo_height(), 600)
        self.scale, self.offset_x, self.offset_y = _vp.zoom_to_fit(
            self.entities, W, H, padding=0.12
        )

    def _viewport_world(self):
        W = self.canvas.winfo_width()  or 800
        H = self.canvas.winfo_height() or 600
        return _vp.viewport_world(self.scale, self.offset_x, self.offset_y, W, H)

    # ─── Zoom & Pan ───────────────────────────────────────────────
    def _save_zoom(self):
        """Guarda el estado de zoom actual para ZOOM P (máx 10 niveles)."""
        self._zoom_prev.append((self.scale, self.offset_x, self.offset_y))
        if len(self._zoom_prev) > 10:
            self._zoom_prev.pop(0)

    # ID del after() pendiente de render diferido post-scroll
    _scroll_render_id: object = None

    def _zoom_preview(self, W: int, H: int):
        """Feedback visual inmediato durante scroll: escala la imagen PIL del
        último render usando NEAREST (< 5 ms). No lanza hilo, no bloquea UI.

        Estrategia geométrica:
        - El render anterior cubre la vista con scale=S0, offsets=(ox0, oy0).
        - Ahora el viewport es scale=S1, offsets=(ox1, oy1).
        - Cualquier punto mundo (wx, wy) proyectaba a:
              px0 = ox0 + wx * S0  (columna en la imagen antigua)
        - Ahora proyecta a:
              px1 = ox1 + wx * S1  (columna deseada en pantalla)
        - Entonces: px0 = ox0 + (px1 - ox1) / S1 * S0
                         = ox0 + (px1 - ox1) * ratio   con ratio = S0/S1
        - El preview es un crop + resize de la imagen antigua para cubrir (W, H).
        """
        if (self._pil_img_cache is None
                or not RendererPIL.available()
                or self._canvas_img_id is None):
            return
        try:
            from PIL import Image as _PILImg
            img0    = self._pil_img_cache
            S0, ox0, oy0 = self._pil_cache_scale, self._pil_cache_ox, self._pil_cache_oy
            S1, ox1, oy1 = self.scale, self.offset_x, self.offset_y
            if S0 <= 0 or S1 <= 0:
                return
            ratio = S0 / S1   # < 1 = zoom in; > 1 = zoom out

            # Aplicar preview tanto para zoom-IN como zoom-OUT.
            #
            # Zoom-IN  (ratio < 1): crop + stretch de la imagen vieja — sin bordes.
            # Zoom-OUT (ratio > 1): imagen vieja se achica, bordes de fondo visibles.
            #   La alternativa (dejar imagen vieja quieta) causaba un efecto visual
            #   de "snap back": la imagen vieja (más acercada) hacía parecer que el
            #   dibujo AVANZÓ al hacer scroll-out, y luego saltaba al render correcto.
            #   Los bordes de fondo (~120ms) son mucho menos confusos que ese efecto.
            #
            # Límite: ratio > 4 → imagen resultante < 25% → demasiado pequeña,
            # mejor dejar imagen vieja que mostrar 75% de fondo negro.
            if ratio > 4.0:
                return

            iw0, ih0 = img0.width, img0.height

            # Región de la imagen antigua que mapea a la pantalla actual
            # px_old = ox0 + (px_new - ox1) * ratio
            left  = int(ox0 + (0   - ox1) * ratio)
            top   = int(oy0 + (0   - oy1) * ratio)
            right = int(ox0 + (W   - ox1) * ratio)
            bot   = int(oy0 + (H   - oy1) * ratio)

            # Clamp al dominio de la imagen antigua
            cl = max(0, left);  ct = max(0, top)
            cr = min(iw0, right); cb = min(ih0, bot)

            if cr <= cl or cb <= ct:
                # Sin solapamiento (zoom extremo) — skip, el render completo lo resolverá
                return

            # Crear imagen de destino del color de fondo
            bg = self._hex2rgb3(self.cv_bg) if hasattr(self, 'cv_bg') else (28, 28, 30)
            preview = _PILImg.new("RGB", (W, H), bg)

            # Crop de la parte válida de la imagen antigua
            crop = img0.crop((cl, ct, cr, cb))

            # Calcular dónde pega el crop en la imagen de destino
            dx = int((cl - left) / ratio)
            dy = int((ct - top)  / ratio)
            new_cw = max(1, int((cr - cl) / ratio))
            new_ch = max(1, int((cb - ct) / ratio))

            crop_resized = crop.resize((new_cw, new_ch), _PILImg.NEAREST)
            preview.paste(crop_resized, (dx, dy))

            # Mostrar el preview — siempre PhotoImage nuevo (paste() bloquea 500ms
            # bajo contención de GIL con el tessellator, igual que en _apply_render).
            M = self._PIL_MARGIN
            _new_photo = _PILImageTk.PhotoImage(preview)
            _old_photo = self._pil_photo
            self._pil_photo = _new_photo
            if self._canvas_img_id is None:
                self.canvas.delete("st")
                self._canvas_img_id = self.canvas.create_image(
                    -M, -M, anchor="nw",
                    image=self._pil_photo, tags=("st", "st_pil"))
            else:
                self.canvas.itemconfigure(self._canvas_img_id, image=self._pil_photo)
                self.canvas.coords(self._canvas_img_id, -M, -M)
            del _old_photo
        except Exception as err:
            # Preview fallido es inocuo — el render completo lo corregirá
            print(f"[zoom_preview] {err}")

    def _on_wheel(self, event):
        self._save_zoom()

        # ── Fix race condition: invalidar renders en vuelo ─────────────────
        # Un hilo que empezó ANTES de este scroll puede terminar DESPUÉS y
        # llamar _apply_render con escala obsoleta, sobreescribiendo el preview
        # correcto → efecto visual de "zoom va y vuelve".
        # Incrementar _render_gen aquí hace que _apply_render() descarte
        # cualquier resultado de generaciones anteriores.
        self._render_gen += 1

        # ── Factor normalizado por magnitud del delta y velocidad configurada ─
        # event.delta = ±120 por notch en mouse estándar.
        # Trackpads y mice de alta resolución envían delta=±40 o ±24 en
        # múltiples eventos por notch → sin normalizar, el zoom se multiplica
        # N veces en lugar de una sola.
        # base = 1.0 + scroll_speed * 0.08  →  speed 5 = 1.40× (default AutoCAD-like)
        zoom_base = 1.0 + self._scroll_speed * 0.08   # 1=1.08 … 5=1.40 … 10=1.80
        steps  = abs(event.delta) / 120.0              # 1.0 = un notch estándar
        factor = (zoom_base ** steps) if event.delta > 0 else (1.0 / (zoom_base ** steps))

        wx, wy = self.s2w(event.x, event.y)
        self.scale = max(SCALE_MIN, min(self.scale * factor, SCALE_MAX))
        self.offset_x = event.x - wx * self.scale
        self.offset_y = event.y + wy * self.scale

        W = self.canvas.winfo_width() or 800
        H = self.canvas.winfo_height() or 600

        # ── Preview instantáneo: escalar imagen PIL del último render ──────
        # < 5 ms (NEAREST). Da retroalimentación visual antes del render completo.
        self._zoom_preview(W, H)

        # ── HUD y dynamic layer ────────────────────────────────────────────
        self.canvas.delete("st_hud")
        self._draw_hud(W, H)
        self._redraw_dynamic()

        # ── Render completo diferido: 120 ms tras el último tick del scroll ─
        if self._scroll_render_id is not None:
            self.root.after_cancel(self._scroll_render_id)
        self._scroll_render_id = self.root.after(120, self._scroll_render_commit)

    def _scroll_render_commit(self):
        """Render PIL completo una vez que el scroll se detuvo."""
        self._scroll_render_id = None
        self._redraw_static()

    def _on_pan_start(self, event):
        self._pan_start = (event.x, event.y, self.offset_x, self.offset_y)
        self._pan_prev_ox = self.offset_x
        self._pan_prev_oy = self.offset_y
        self._pan_rendered_ox = self.offset_x
        self._pan_rendered_oy = self.offset_y

    def _on_pan(self, event):
        if not self._pan_start:
            return
        dx = event.x - self._pan_start[0]
        dy = event.y - self._pan_start[1]
        self.offset_x = self._pan_start[2] + dx
        self.offset_y = self._pan_start[3] + dy
        # Throttle: programar flush solo si no hay uno pendiente
        if not self._pan_pending:
            self._pan_pending = True
            self.root.after(self._move_ms, self._flush_pan)

    def _flush_pan(self):
        self._pan_pending = False
        incr_dx = self.offset_x - self._pan_rendered_ox
        incr_dy = self.offset_y - self._pan_rendered_oy
        if abs(incr_dx) < 0.3 and abs(incr_dy) < 0.3:
            return
        self._pan_rendered_ox = self.offset_x
        self._pan_rendered_oy = self.offset_y

        cv = self.canvas
        # Dimensiones cacheadas en _redraw_static — evita 2 Tcl round-trips por frame.
        # El canvas no cambia de tamaño durante un arrastre, así que el caché es exacto.
        W = getattr(self, '_cv_w', 0) or cv.winfo_width()  or 800
        H = getattr(self, '_cv_h', 0) or cv.winfo_height() or 600

        if RendererPIL.available() and self._canvas_img_id is not None:
            # Pan rápido: mover la imagen existente (O(1), sin re-render)
            # Deja bordes negros pero es inmediato; se rellena al soltar.
            cv.move(self._canvas_img_id, incr_dx, incr_dy)
            cv.delete("st_hud")
            self._draw_hud(W, H)
            self._redraw_dynamic()
        else:
            self._redraw_pan(incr_dx, incr_dy)

    def _on_pan_end(self, event):
        self._pan_start = None
        self._pan_pending = False
        self._pan_rendered_ox = self.offset_x
        self._pan_rendered_oy = self.offset_y
        # Re-render completo al soltar el pan (rellena bordes negros)
        self._redraw_static()

    def _on_resize(self, event):
        # Debounce: espera 150 ms de silencio antes de lanzar el render.
        # Evita acumular hilos PIL durante resize continuo del borde de ventana.
        if hasattr(self, '_resize_after_id'):
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(150, self._redraw)

    def _zoom_extents(self, *_):
        self._save_zoom()
        # ZE usa TODAS las entidades sin importar visibilidad de capa,
        # para que el usuario siempre pueda ubicar el dibujo aunque las
        # capas estén apagadas.
        W = self.canvas.winfo_width()  or 800
        H = self.canvas.winfo_height() or 600
        sc, ox, oy = _vp.zoom_to_fit(self.entities, W, H, padding=0.10)
        ratio = int(round(1.0 / sc)) if sc > 0 else 0
        print(f"[ZE] {len(self.entities)} ent · scale={sc:.5f} (1:{ratio}) · ox={ox:.0f} oy={oy:.0f} · cell={self._entity_cell:.1f}")
        self.scale, self.offset_x, self.offset_y = sc, ox, oy
        self._redraw()

    def _zoom_window(self, sx0, sy0, sx1, sy1):
        self._save_zoom()
        W = self.canvas.winfo_width()  or 800
        H = self.canvas.winfo_height() or 600
        self.scale, self.offset_x, self.offset_y = _vp.zoom_window(
            sx0, sy0, sx1, sy1,
            self.scale, self.offset_x, self.offset_y, W, H)
        self._redraw()

    # ─── Índice espacial de snap ──────────────────────────────────
    _SNAP_MAX_PTS   = 500_000   # límite de puntos en el snap index (evitar freeze)
    _SYNC_IDX_LIMIT = 300       # entidades — por encima se usa hilo secundario

    def _rebuild_snap_index(self):
        """Rebuilds snap + entity indices.

        ≤ _SYNC_IDX_LIMIT entidades → síncrono (rápido, sin overhead de hilo).
        >  _SYNC_IDX_LIMIT entidades → delega a _rebuild_indices_bg() para no
           bloquear el main thread durante operaciones en archivos grandes.
        Los 22 call-sites del código se benefician automáticamente.
        """
        if len(self.entities) > self._SYNC_IDX_LIMIT:
            self._rebuild_indices_bg()          # hilo secundario, ya existía
        else:
            snap_idx, ent_idx, cell = self._compute_indices(list(self.entities))
            self._snap_index   = snap_idx
            self._entity_cell  = cell
            self._entity_index = ent_idx

    # ─── Cálculo de índices (puede correr en hilo secundario) ────────
    def _compute_indices(self, entities_snapshot: list):
        """Computa snap_index + entity_index a partir de un snapshot de entidades.
        Devuelve (snap_idx, entity_idx, cell).  Seguro para ejecutar en hilo."""
        # ── Snap index ────────────────────────────────────────────────
        snap_idx: dict = {}
        seen: set = set()
        total = 0
        for e in entities_snapshot:
            if total >= self._SNAP_MAX_PTS:
                break
            for wp in e.snap_points():
                dedup = (round(wp[0], 4), round(wp[1], 4), wp[2])
                if dedup in seen:
                    continue
                seen.add(dedup)
                key = (_cell(wp[0]), _cell(wp[1]))
                snap_idx.setdefault(key, []).append(wp)
                total += 1

        # ── Entity index (adaptive cell) ──────────────────────────────
        cell = _SNAP_CELL
        if entities_snapshot:
            xs, ys = [], []
            step = max(1, len(entities_snapshot) // 500)
            for e in entities_snapshot[::step]:
                x0, y0, x1, y1 = self._entity_aabb(e)
                xs += [x0, x1]; ys += [y0, y1]
            if xs:
                ext = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
                cell = max(_SNAP_CELL, ext / 400.0)

        ent_idx: dict  = {}
        large: list = []
        _MAX_CELLS = 40 * 40
        for e in entities_snapshot:
            x0, y0, x1, y1 = self._entity_aabb(e)
            cx0 = int(math.floor(x0 / cell))
            cy0 = int(math.floor(y0 / cell))
            cx1 = int(math.floor(x1 / cell))
            cy1 = int(math.floor(y1 / cell))
            if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) > _MAX_CELLS:
                large.append(e)
                continue
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    ent_idx.setdefault((cx, cy), []).append(e)
        ent_idx[None] = large
        flat = list(entities_snapshot)
        ent_idx["__flat__"]       = flat
        aabb_list = [self._entity_aabb(e) for e in flat]
        ent_idx["__aabb__"]       = aabb_list
        # AABB de entidades grandes para cull en _query_viewport
        ent_idx["__large_aabb__"] = [self._entity_aabb(e) for e in large]
        # AABB por id para lookup O(1) en renderer (evita recalcular desde vértices)
        ent_idx["__aabb_by_id__"] = {id(e): aabb for e, aabb in zip(flat, aabb_list)}
        # Número de celdas reales — usado en _query_viewport para el fast-path
        # cuando la query cubre ≥ 85% del grid (zoom-out total).
        ent_idx["__total_cells__"] = sum(1 for k in ent_idx if isinstance(k, tuple))

        return snap_idx, ent_idx, cell

    def _rebuild_indices_bg(self, on_done=None):
        """Para importaciones grandes: calcula índices en hilo secundario.
        La UI sigue respondiendo (snap deshabilitado hasta terminar).
        on_done() se llama en el hilo principal cuando los índices están listos."""
        snapshot = list(self.entities)   # copia antes de lanzar el hilo

        def _worker():
            snap_idx, ent_idx, cell = self._compute_indices(snapshot)
            # Volver al hilo principal para asignar los resultados
            def _apply():
                self._snap_index   = snap_idx
                self._entity_cell  = cell
                self._entity_index = ent_idx
                self._indexing     = False
                if on_done:
                    on_done()
            self.root.after(0, _apply)

        # Índice temporal mínimo: muestra todas las entidades, sin culling optimizado.
        # _entity_aabb se computa en el hilo para no bloquear la UI aquí.
        flat_tmp = list(snapshot)
        self._snap_index   = {}
        self._entity_index = {None: flat_tmp, "__flat__": flat_tmp, "__aabb__": None}
        self._entity_cell  = _SNAP_CELL
        self._indexing     = True
        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    # ─── Compatibilidad: alias para código que llama directo al índice ──
    def _rebuild_entity_index(self):
        """Alias — el índice ahora se construye dentro de _compute_indices."""
        snap_idx, ent_idx, cell = self._compute_indices(list(self.entities))
        self._snap_index   = snap_idx
        self._entity_cell  = cell
        self._entity_index = ent_idx

    _QUERY_MAX_CELLS = 200 * 200   # 40 000 celdas máx antes de scan lineal

    def _query_viewport(self, vx0: float, vy0: float,
                        vx1: float, vy1: float) -> list:
        """Entidades que tocan el viewport. Sin iterar las fuera de pantalla.
        Usa la celda adaptativa calculada en _rebuild_entity_index.
        Si el viewport abarca demasiadas celdas (zoom muy lejano) hace
        scan lineal del índice completo — más rápido que iterar celdas vacías."""
        cell = self._entity_cell
        cx0 = int(math.floor(vx0 / cell))
        cy0 = int(math.floor(vy0 / cell))
        cx1 = int(math.floor(vx1 / cell))
        cy1 = int(math.floor(vy1 / cell))
        seen: set = set()
        # Bucket global: entidades grandes (siempre incluidas)
        result: list = list(self._entity_index.get(None, []))
        seen.update(id(e) for e in result)

        nx = cx1 - cx0 + 1
        ny = cy1 - cy0 + 1
        if nx * ny > self._QUERY_MAX_CELLS:
            # Scan lineal: usar lista plana precalculada (O(1) vs O(cells))
            flat = self._entity_index.get("__flat__")
            if flat is not None:
                return flat   # ya incluye todas las entidades
            # Fallback (no debería ocurrir)
            for key, ents in self._entity_index.items():
                if key is None or key == "__flat__":
                    continue
                for e in ents:
                    eid = id(e)
                    if eid not in seen:
                        seen.add(eid); result.append(e)
            return result

        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                for e in self._entity_index.get((cx, cy), []):
                    eid = id(e)
                    if eid not in seen:
                        seen.add(eid)
                        result.append(e)
        return result

    # Número máximo de celdas del grid a recorrer en cada búsqueda de snap.
    # Limitar esto es CRÍTICO para dibujos grandes/con mucho zoom out:
    # sin límite, cr puede llegar a miles → O(n²) en cada movimiento del mouse.
    _SNAP_CR_MAX = 20

    def _find_snap(self, sx, sy):
        if not self.snap_on:
            return None
        wx, wy = self.s2w(sx, sy)
        rw = SNAP_PX / self.scale          # radio en coords mundo

        # ── Protección LOD: si el radio de snap cubre demasiadas celdas
        # (dibujo muy grande o muy zoom out) desactivar snap temporalmente.
        # El usuario verá el cursor libre y podrá acercar el zoom para snappear.
        cr_raw = int(rw / _SNAP_CELL) + 2
        _cr_max = self._snap_cr_max   # adaptativo según nº entidades
        if cr_raw > _cr_max:
            return None   # demasiado zoom out → sin snap (cursor libre)

        best_d = float(SNAP_PX)
        best   = None

        # ── Snaps estáticos (indexados): END MID CEN QUA ────────────
        static_ok = {t for t in ("end", "mid", "cen", "qua")
                     if self._snap_types.get(t, True)}
        if static_ok and self._snap_index:
            cr = max(2, min(cr_raw, _cr_max))
            cx_c, cy_c = _cell(wx), _cell(wy)
            # best_d² en world-coords para poda de celdas sin raíz cuadrada
            _sc = self.scale
            best_dw_sq = (best_d / _sc) ** 2
            for icx in range(cx_c - cr, cx_c + cr + 1):
                # Poda X: distancia mínima de la franja de celdas al cursor
                _cdx = max(0.0, icx * _SNAP_CELL - wx,
                           wx - (icx + 1) * _SNAP_CELL)
                if _cdx * _cdx >= best_dw_sq:
                    continue
                for icy in range(cy_c - cr, cy_c + cr + 1):
                    # Poda XY: distancia mínima de esta celda al cursor
                    _cdy = max(0.0, icy * _SNAP_CELL - wy,
                               wy - (icy + 1) * _SNAP_CELL)
                    if _cdx * _cdx + _cdy * _cdy >= best_dw_sq:
                        continue
                    for wp in self._snap_index.get((icx, icy), []):
                        if wp[2] not in static_ok:
                            continue
                        # Pre-filtro en world-coords (evita w2s innecesario)
                        _dwx = wp[0] - wx; _dwy = wp[1] - wy
                        if _dwx * _dwx + _dwy * _dwy >= best_dw_sq:
                            continue
                        esx, esy = self.w2s(wp[0], wp[1])
                        d = math.hypot(esx - sx, esy - sy)
                        if d < best_d:
                            best_d = d; best = wp
                            best_dw_sq = (best_d / _sc) ** 2

        # ── Snaps dinámicos: INT PER TAN NEA ────────────────────────
        need_dyn = any(self._snap_types.get(t) for t in ("int", "per", "tan", "nea"))
        if need_dyn:
            search_r = rw * 3
            # Usar índice espacial para candidatos cercanos en O(k)
            if self._entity_index:
                # IMPORTANTE: usar _entity_cell (tamaño real del índice), NO _SNAP_CELL
                # _entity_index se construye con _entity_cell (adaptativo según el dibujo).
                # Usar _SNAP_CELL aquí producía keys que no coincidían → nearby vacío.
                _ecell = self._entity_cell
                cr_dyn = max(1, min(int(search_r / _ecell) + 1, _cr_max))
                cx_d   = int(math.floor(wx / _ecell))
                cy_d   = int(math.floor(wy / _ecell))
                _seen_dyn: set = set()
                nearby = []
                _sr_sq = search_r * search_r
                for icx in range(cx_d - cr_dyn, cx_d + cr_dyn + 1):
                    _cdx2 = max(0.0, icx * _ecell - wx,
                                wx - (icx + 1) * _ecell)
                    if _cdx2 * _cdx2 >= _sr_sq:
                        continue
                    for icy in range(cy_d - cr_dyn, cy_d + cr_dyn + 1):
                        _cdy2 = max(0.0, icy * _ecell - wy,
                                    wy - (icy + 1) * _ecell)
                        if _cdx2 * _cdx2 + _cdy2 * _cdy2 >= _sr_sq:
                            continue
                        for e in self._entity_index.get((icx, icy), []):
                            eid = id(e)
                            if eid not in _seen_dyn:
                                _seen_dyn.add(eid)
                                nearby.append(e)
            else:
                nearby = [e for e in self.entities
                          if self._entity_near(e, wx, wy, search_r)]

            # NEA es el snap de MENOR prioridad: solo gana si ningún otro snap
            # geométrico (INT, PER, TAN) encontró nada dentro del aperture.
            # Se evalúa primero para establecer un candidato inicial, pero
            # INT/PER/TAN lo sobreescriben aunque estén más lejos del cursor.
            _nea_best: tuple | None = None
            _nea_best_d: float = float(SNAP_PX)  # dentro del aperture

            if self._snap_types.get("nea"):
                for pt in self._snap_nea(wx, wy, nearby, rw):
                    esx, esy = self.w2s(*pt)
                    d = math.hypot(esx - sx, esy - sy)
                    if d < _nea_best_d:
                        _nea_best_d = d; _nea_best = (pt[0], pt[1], "nea")

            if self._snap_types.get("int"):
                for pt in self._snap_int(wx, wy, nearby, rw):
                    esx, esy = self.w2s(*pt)
                    d = math.hypot(esx - sx, esy - sy)
                    if d < best_d:
                        best_d = d; best = (pt[0], pt[1], "int")

            if self._snap_types.get("per"):
                for pt in self._snap_per(wx, wy, nearby):
                    esx, esy = self.w2s(*pt)
                    d = math.hypot(esx - sx, esy - sy)
                    if d < best_d:
                        best_d = d; best = (pt[0], pt[1], "per")

            if self._snap_types.get("tan") and self.draw_pts:
                px_d, py_d = self.draw_pts[-1]
                for pt in self._snap_tan(px_d, py_d, nearby):
                    esx, esy = self.w2s(*pt)
                    d = math.hypot(esx - sx, esy - sy)
                    if d < best_d:
                        best_d = d; best = (pt[0], pt[1], "tan")

            # NEA solo entra si ningún snap geométrico ganó al best previo a dyn
            if _nea_best and best is None:
                best = _nea_best
                best_d = _nea_best_d

        # ── Snap de grilla (GRI) ─────────────────────────────────────
        if self._snap_types.get("gri"):
            # Determinar paso de grilla activo (igual que _draw_grid)
            if self.scale >= 160:
                gstep = GRID_MINOR
            elif self.scale >= 40:
                gstep = GRID_MAJOR
            elif self.scale >= 10:
                gstep = GRID_MAJOR * 5
            else:
                gstep = GRID_MAJOR * 10
            gx = round(wx / gstep) * gstep
            gy = round(wy / gstep) * gstep
            esx, esy = self.w2s(gx, gy)
            d = math.hypot(esx - sx, esy - sy)
            if d < best_d:
                best_d = d; best = (gx, gy, "gri")

        return best

    # ── Helpers de snap dinámico ─────────────────────────────────

    @staticmethod
    def _entity_near(e, wx, wy, r):
        """True si la bbox de la entidad se superpone con el radio r."""
        pts = e.bbox_pts()
        if not pts:
            return False
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        return (min(xs) - r <= wx <= max(xs) + r and
                min(ys) - r <= wy <= max(ys) + r)

    @staticmethod
    def _segs_of(e):
        """Devuelve segmentos (x1,y1,x2,y2) de una entidad Line/Polyline."""
        if isinstance(e, Line):
            return [(e.x1, e.y1, e.x2, e.y2)]
        if isinstance(e, Polyline) and len(e.points) >= 2:
            pts = e.points
            segs = [(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])
                    for i in range(len(pts) - 1)]
            if e.closed and len(pts) > 2:
                segs.append((pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]))
            return segs
        return []

    # Límite de segmentos para snap INT — evita O(N²) con planos densos.
    # 60 segs → 1 770 comparaciones → <1ms.  Sin límite → 500 segs → 125 000
    # comparaciones → ~500ms de freeze visible en cada movimiento del mouse.
    _SNAP_INT_MAX_SEGS = 60

    def _snap_int(self, wx, wy, nearby, rw):
        """Puntos de intersección entre pares de entidades cercanas.

        Cap de segmentos: se conservan los _SNAP_INT_MAX_SEGS más cercanos al
        cursor (por midpoint).  Garantiza O(k²) con k pequeño incluso en
        planos con miles de polylines densas (ej. DXF arquitectónico 67k ents).
        """
        segs    = []
        circles = []
        for e in nearby:
            segs.extend(self._segs_of(e))
            if isinstance(e, (Circle, Arc)):
                circles.append(e)

        # Aplicar cap: quedar con los segmentos cuyo midpoint está más cerca del cursor
        _cap = self._SNAP_INT_MAX_SEGS
        if len(segs) > _cap:
            segs.sort(key=lambda s: (((s[0]+s[2])*0.5 - wx)**2 +
                                      ((s[1]+s[3])*0.5 - wy)**2))
            segs = segs[:_cap]

        pts = []
        # segmento–segmento  O(k²), k ≤ _cap
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                p = _intersect_seg_seg(*segs[i], *segs[j])
                if p and math.hypot(p[0] - wx, p[1] - wy) <= rw:
                    pts.append(p)
        # segmento–círculo
        for seg in segs:
            for circ in circles:
                for p in _intersect_seg_circle(*seg, circ.cx, circ.cy, circ.radius):
                    if math.hypot(p[0] - wx, p[1] - wy) <= rw:
                        pts.append(p)
        return pts

    def _snap_per(self, wx, wy, nearby):
        """Pie perpendicular desde el ÚLTIMO PUNTO DIBUJADO hacia entidades cercanas.
        Requiere un punto previo activo — sin él PER no tiene significado geométrico."""
        if not self.draw_pts:
            return []   # sin punto de origen no hay perpendicular real
        fx, fy = self.draw_pts[-1]   # from-point = origen de la línea en curso

        pts = []
        for e in nearby:
            if isinstance(e, (Circle, Arc)):
                d = math.hypot(fx - e.cx, fy - e.cy)
                if d < 1e-9:
                    continue
                # Pie de perpendicular = punto en la circunferencia en dirección from_pt→center
                pt = (e.cx + e.radius * (fx - e.cx) / d,
                      e.cy + e.radius * (fy - e.cy) / d)
                if isinstance(e, Arc) and not _pt_on_arc(pt, e):
                    continue   # el pie cae fuera del span del arco
                pts.append(pt)
            else:
                for seg in self._segs_of(e):
                    p = _perp_foot_seg(fx, fy, *seg)
                    if p:
                        pts.append(p)
        return pts

    @staticmethod
    def _snap_tan(from_x, from_y, nearby):
        """Puntos tangentes desde from_pt a círculos/arcos cercanos.
        Fórmula: θ = atan2(from_y-cy, from_x-cx) ± acos(r/d)."""
        pts = []
        for e in nearby:
            if not isinstance(e, (Circle, Arc)):
                continue
            d = math.hypot(from_x - e.cx, from_y - e.cy)
            if d <= e.radius + 1e-9:
                continue   # punto dentro del círculo, sin tangente real
            # Dirección centro → from_pt
            ang_cp = math.atan2(from_y - e.cy, from_x - e.cx)
            # Ángulo en el centro entre la línea centro-from_pt y la línea centro-T
            half = math.acos(max(-1.0, min(1.0, e.radius / d)))
            for sign in (+1, -1):
                ta = ang_cp + sign * half
                pt = (e.cx + e.radius * math.cos(ta),
                      e.cy + e.radius * math.sin(ta))
                if isinstance(e, Arc) and not _pt_on_arc(pt, e):
                    continue   # tangente fuera del span del arco
                pts.append(pt)
        return pts

    def _snap_nea(self, wx, wy, nearby, rw):
        """Punto más cercano sobre cualquier entidad (dentro del radio de snap)."""
        pts = []
        for e in nearby:
            if isinstance(e, (Circle, Arc)):
                d = math.hypot(wx - e.cx, wy - e.cy)
                if d < 1e-9:
                    continue
                pt = (e.cx + e.radius * (wx - e.cx) / d,
                      e.cy + e.radius * (wy - e.cy) / d)
                if isinstance(e, Arc) and not _pt_on_arc(pt, e):
                    continue   # punto más cercano fuera del span
                if math.hypot(pt[0] - wx, pt[1] - wy) <= rw:
                    pts.append(pt)
            else:
                for seg in self._segs_of(e):
                    p = _perp_foot_seg(wx, wy, *seg)
                    if p and math.hypot(p[0] - wx, p[1] - wy) <= rw:
                        pts.append(p)
        return pts

    def _apply_ortho(self, wx, wy):
        if not self.ortho or not self.draw_pts:
            return wx, wy
        lx, ly = self.draw_pts[-1]
        dx = wx-lx; dy = wy-ly
        return (wx, ly) if abs(dx) >= abs(dy) else (lx, wy)

    # ─── Throttle adaptativo ─────────────────────────────────────
    @property
    def _move_ms(self) -> int:
        """Intervalo de refresco dinámico según cantidad de entidades.
        El cursor visual siempre es inmediato (fast path).
        Este valor solo afecta al snap + hover (slow path throttled)."""
        n = len(self.entities)
        if n > 500_000: return 40   # 25 fps snap — archivo masivo
        if n > 200_000: return 25   # 40 fps snap
        if n > 80_000:  return 16   # 60 fps snap
        if n > 40_000:  return 12
        if n > 15_000:  return 8
        if n > 3_000:   return 6
        return 5   # ~200 fps para dibujos pequeños

    @property
    def _snap_cr_max(self) -> int:
        """Limita cuántas celdas busca el snap — adaptativo al tamaño del archivo."""
        n = len(self.entities)
        if n > 500_000: return 5    # radio mínimo: solo snap muy cercano
        if n > 200_000: return 8
        if n > 80_000:  return 12
        return self._SNAP_CR_MAX    # valor base 20

    # ─── Movimiento del mouse: cursor inmediato + snap throttled ─────
    def _on_move(self, event):
        self._mouse_sx = event.x
        self._mouse_sy = event.y
        if self._pan_start:          # durante pan no calcular snap/hover
            return

        # ── Fast path: cursor inmediato solo en estado verdaderamente idle ──
        # Solo cuando no hay grips, selección, ni herramienta activa;
        # en cualquier otro caso el redraw completo es caro y se throttlea.
        _cursor_idle = (
            not self._op_mode
            and not self.draw_pts
            and self.tool == "select"
            and not self._is_dragging
            and self._hot_grip is None
            and not self._grips
            and not self.snap_pt
            and not self._kbd_buf   # si hay buffer de teclado → redraw completo
        )
        if _cursor_idle:
            wx, wy = self.s2w(event.x, event.y)
            wx, wy = self._apply_ortho(wx, wy)
            self.mouse_w = (wx, wy)
            # Actualizar cursor SIN delete+create: solo mover los items persistentes.
            # Evita saturar Tk con cientos de operaciones por segundo.
            self._move_cursor_items(event.x, event.y)
        else:
            # Slow path pero siempre actualizar mouse_w con posición raw+ortho.
            # Esto evita que _on_btn_up use coordenadas obsoletas cuando el
            # usuario hace clic más rápido que el intervalo de _flush_move.
            # _flush_move luego sobreescribe con la posición con snap/ortho.
            _raw_wx, _raw_wy = self.s2w(event.x, event.y)
            _raw_wx, _raw_wy = self._apply_ortho(_raw_wx, _raw_wy)
            self.mouse_w = (_raw_wx, _raw_wy)

        # ── Slow path: snap + hover, throttled ────────────────────────
        if not self._move_pending:
            self._move_pending = True
            self.root.after(self._move_ms, self._flush_move)

    def _move_cursor_items(self, sx: float, sy: float):
        """Fast path: mueve los items del cursor usando coords() — sin delete/create."""
        # Si hay buffer de teclado → el panel flotante necesita redraw completo
        if self._kbd_buf:
            self._redraw_dynamic()
            return
        ids = getattr(self, '_crs_ids', None)
        if not ids:
            # Primera vez o items destruidos → usar redraw completo y crear items
            self._redraw_dynamic()
            return
        cv = self.canvas
        # Usar cache de dimensiones — evita 2 Tcl calls por mouse move.
        W = getattr(self, '_cv_w', 0) or cv.winfo_width()
        H = getattr(self, '_cv_h', 0) or cv.winfo_height()
        pct   = max(5, min(100, self.cursor_size)) / 100.0
        arm_h = max(12, int(W * pct))
        arm_v = max(12, int(H * pct))
        G = 5
        try:
            cv.coords(ids['hl'], sx - arm_h, sy, sx - G, sy)
            cv.coords(ids['hr'], sx + G,     sy, sx + arm_h, sy)
            cv.coords(ids['vt'], sx, sy - arm_v, sx, sy - G)
            cv.coords(ids['vb'], sx, sy + G,     sx, sy + arm_v)
            # Color del cursor — solo llamar itemconfig si cambió desde el último frame.
            # Evita 4 Tcl round-trips en el caso común (cursor idle inmóvil).
            _cmd_active = bool(self._op_mode or self.draw_pts or self.tool != "select")
            _col = self.cursor_color_active if _cmd_active else self.cursor_color_idle
            if _col != getattr(self, '_crs_last_col', None):
                self._crs_last_col = _col
                for _k in ('hl', 'hr', 'vt', 'vb'):
                    if _k in ids:
                        try: cv.itemconfig(ids[_k], fill=_col, state='normal')
                        except Exception: pass
            x, y = self.mouse_w
            tx = sx + 20
            cy0, cy1 = sy - 26, sy - 14
            if self._kbd_buf:
                # Panel kbd activo — ocultar textos sueltos de coordenadas
                for k in ('tx', 'ty'):
                    if k in ids:
                        try: cv.itemconfig(ids[k], state='hidden')
                        except Exception: pass
            else:
                cv.coords(ids['tx'], tx, cy0)
                cv.coords(ids['ty'], tx, cy1)
                cv.itemconfig(ids['tx'], text=f"X  {x:8.3f}", state='normal')
                cv.itemconfig(ids['ty'], text=f"Y  {y:8.3f}", state='normal')
        except Exception:
            # Item destruido (resize/redraw) → borrar dy_c y forzar recreación limpia.
            # IMPORTANTE: primero delete para evitar cursor doble en pantalla.
            try:
                self.canvas.delete("dy_c")
            except Exception:
                pass
            self._crs_ids = {}
            self._redraw_dynamic()

    def _flush_move(self):
        self._move_pending = False
        # Si pan activo o popup abierto → solo actualizar coordenadas, sin snap/hover
        if self._pan_start or self._ctx_popup_open:
            return
        sx, sy = self._mouse_sx, self._mouse_sy

        # ── Skip snap si el ratón no se ha movido desde el último flush ──
        _lssx = getattr(self, '_last_snap_sx', -999)
        _lssy = getattr(self, '_last_snap_sy', -999)
        _pos_changed = abs(sx - _lssx) > 0.5 or abs(sy - _lssy) > 0.5
        self._last_snap_sx = sx
        self._last_snap_sy = sy

        if _pos_changed:
            # Snap activo SOLO cuando hay un comando en curso:
            #   • Herramienta de dibujo activa  (line, circle, arc…)
            #   • Operación en progreso         (MOVE, COPY, ROTATE, DIM…)
            # En modo "select" puro sin operación, el snap no aporta y consume CPU.
            _snap_needed = (self.tool != "select") or bool(self._op_mode) or (self._hot_grip is not None)
            snap = self._find_snap(sx, sy) if _snap_needed else None
            if snap:
                self.mouse_w = (snap[0], snap[1]); self.snap_pt = snap
            else:
                wx, wy = self.s2w(sx, sy)
                wx, wy = self._apply_ortho(wx, wy)
                self.mouse_w = (wx, wy); self.snap_pt = None
        # Hover LOD: desactivar si hay muchas entidades y no hay zoom suficiente.
        # Escala mínima requerida sube con la cantidad de entidades.
        n = len(self.entities)
        if   n > 200_000: _hover_min_scale = 50.0
        elif n >  50_000: _hover_min_scale = 20.0
        elif n >  10_000: _hover_min_scale = 10.0
        else:             _hover_min_scale = 0.0
        _hover_lod = self.scale < _hover_min_scale
        prev_hover = self._hover_ent
        self._hover_ent = None if _hover_lod else self._find_hover_entity(sx, sy)
        x, y = self.mouse_w
        self._lbl_coords.configure(text=f"X: {x:10.3f}    Y: {y:10.3f}")
        # ── Info de entidad bajo el cursor ──────────────────────────
        if self._hover_ent is not prev_hover:
            self._update_entity_hint(self._hover_ent)

        # ── Hover grip: resaltar grip bajo el cursor antes de clicar ──
        prev_hg = self._hover_grip
        if self._grips and self._hot_grip is None and not self._op_mode:
            GRIP_HIT_PX = 12   # zona un poco generosa para hover
            best_hg = None; best_hgd = GRIP_HIT_PX
            for g in self._grips:
                sgx, sgy = self.w2s(g["wx"], g["wy"])
                d = math.hypot(sx - sgx, sy - sgy)
                if d < best_hgd:
                    best_hgd = d; best_hg = g
            self._hover_grip = best_hg
        else:
            self._hover_grip = None

        self._redraw_dynamic()

    # ─── Clics: soporta clic puntual + arrastre para selección ────
    def _on_btn_down(self, event):
        # ── PAN mode: botón izquierdo actúa como botón central ────────
        if self._op_mode == "pan_mode":
            self._on_pan_start(event)
            return
        # Cerrar editor inline (Toplevel) si el clic fue fuera de él
        _ief = getattr(self, "_inline_editor_frame", None)
        if _ief is not None:
            try:
                fx = _ief.winfo_rootx();  fy = _ief.winfo_rooty()
                fw = _ief.winfo_width();  fh = _ief.winfo_height()
                rx = event.x_root;        ry = event.y_root
                if not (fx <= rx <= fx + fw and fy <= ry <= fy + fh):
                    _ief.destroy()
                    self._inline_editor_frame = None
                    self._redraw()
            except Exception:
                self._inline_editor_frame = None
        self.canvas.focus_set()
        self._drag_start_s = (event.x, event.y)
        self._is_dragging  = False
        self._grip_drag_mode = False   # se activa si el usuario arrastra con grip
        self._grip_just_activated = False  # True solo si acabamos de activar el grip ahora

        # Activar grip en mousedown (para que el arrastre funcione)
        if self.tool == "select" and not self._op_mode and self._grips:
            if self._hot_grip is None:
                # Prioridad 1: grip que ya tenía hover (naranja) → activar directo
                if self._hover_grip is not None:
                    self._hot_grip = self._hover_grip
                    self._hover_grip = None
                    self._grip_just_activated = True
                    self._redraw_dynamic()
                else:
                    # Fallback: buscar grip bajo el cursor (sin hover previo)
                    GRIP_HIT_PX = 10
                    best = None; best_d = GRIP_HIT_PX
                    for g in self._grips:
                        sgx, sgy = self.w2s(g["wx"], g["wy"])
                        d = math.hypot(event.x - sgx, event.y - sgy)
                        if d < best_d:
                            best_d = d; best = g
                    if best:
                        self._hot_grip = best
                        self._grip_just_activated = True   # marcar para btn_up
                        self._redraw_dynamic()

    def _on_drag(self, event):
        # ── PAN mode ──────────────────────────────────────────────────
        if self._op_mode == "pan_mode":
            self._on_pan(event)
            return
        # El arrastre aplica a SELECT idle y también en move_sel/copy_sel
        if self._drag_start_s is None:
            return
        # Actualizar posición actual del mouse — <B1-Motion> NO dispara <Motion>,
        # así que _mouse_sx/sy quedarían congelados sin esta línea y el rectángulo
        # de selección no se vería actualizado mientras se arrastra.
        self._mouse_sx = event.x
        self._mouse_sy = event.y

        # ── Grip drag ────────────────────────────────────────────────
        if self._hot_grip is not None:
            dx = abs(event.x - self._drag_start_s[0])
            dy = abs(event.y - self._drag_start_s[1])
            if dx > 3 or dy > 3:
                self._grip_drag_mode = True
                self._is_dragging    = True
                self._redraw_dynamic()   # muestra preview del grip
            # Snap activo durante grip drag: programar _flush_move igual que _on_move.
            # Sin esto snap_pt nunca se actualiza y el grip no se adhiere a endpoints.
            if not self._move_pending:
                self._move_pending = True
                self.root.after(self._move_ms, self._flush_move)
            return

        _sel_phases = ("erase_sel", "move_sel", "copy_sel", "rotate_sel", "scale_sel", "mirror_sel", "array_sel", "align_sel")
        if self._op_mode not in _sel_phases:
            if self.tool != "select" or self._op_mode:
                return
        dx = abs(event.x - self._drag_start_s[0])
        dy = abs(event.y - self._drag_start_s[1])
        if dx > 6 or dy > 6:   # umbral generoso para no interferir con clics
            self._is_dragging = True
            self._redraw_dynamic()   # dibuja rectángulo de selección

    def _on_btn_up(self, event):
        # ── PAN mode: terminar arrastre pero mantener el modo activo ──
        if self._op_mode == "pan_mode":
            self._on_pan_end(event)
            return
        if self._drag_start_s is None:
            return
        start = self._drag_start_s
        self._drag_start_s = None
        was_drag = self._is_dragging
        self._is_dragging = False

        # Calcular posición mundo exacta en el momento del click.
        # NO usar self.mouse_w directamente: puede estar desactualizado si el
        # usuario hace clic más rápido que el intervalo de _flush_move.
        # Preferir snap_pt si el click cae dentro del radio de snap.
        # SIEMPRE guardar la posición cruda (sin snap) para comandos que
        # necesitan detectar el lado del click (ej: OFFSET).
        self._click_raw_w = self.s2w(event.x, event.y)
        if self.snap_pt:
            sp_sx, sp_sy = self.w2s(self.snap_pt[0], self.snap_pt[1])
            if math.hypot(event.x - sp_sx, event.y - sp_sy) <= SNAP_PX * 2:
                wx, wy = self.snap_pt[0], self.snap_pt[1]
            else:
                wx, wy = self.s2w(event.x, event.y)
                wx, wy = self._apply_ortho(wx, wy)
        else:
            wx, wy = self.s2w(event.x, event.y)
            wx, wy = self._apply_ortho(wx, wy)

        # ── Grip drag — confirmar en mouseup ─────────────────────
        if self._hot_grip is not None:
            if self._grip_drag_mode:
                # Drag: soltar en destino → confirmar movimiento
                gx, gy = self.snap_pt[:2] if self.snap_pt else (wx, wy)
                self._grip_drag_mode = False
                self._is_dragging = False
                self._apply_grip_move(gx, gy)
            elif getattr(self, '_grip_just_activated', False):
                # 1er clic: acaba de activarse el grip → queda rojo, esperar 2do clic
                self._grip_just_activated = False
                self._is_dragging = False
            else:
                # 2do clic (clic-clic): el grip ya estaba rojo → aplicar movimiento
                gx, gy = self.snap_pt[:2] if self.snap_pt else (wx, wy)
                self._grip_just_activated = False
                self._apply_grip_move(gx, gy)
            return

        # ── Operación en curso ────────────────────────────────────
        _SEL_PHASE_LABELS = {
            "erase_sel":  "ERASE",
            "move_sel":   "MOVE",
            "copy_sel":   "COPY",
            "rotate_sel": "ROTATE",
            "scale_sel":  "SCALE",
            "mirror_sel": "MIRROR",
            "array_sel":  "ARRAY",
        }
        if self._op_mode:
            # *_sel: el clic actúa como selección acumulativa de entidades
            if self._op_mode in _SEL_PHASE_LABELS:
                if was_drag:
                    self._window_select(start[0], start[1], event.x, event.y, add=True)
                else:
                    self._select_at(event.x, event.y, add=True)
                sel = [e for e in self.entities if e.selected]
                n = len(sel)
                lbl = _SEL_PHASE_LABELS[self._op_mode]
                self._lbl_op.configure(
                    text=f"{lbl} — {n} sel. — Enter/☞ para confirmar, clic para más:")
                self.canvas.focus_set()
                self._redraw_dynamic()
                return
            self._op_pts.append((wx, wy))
            try:
                self._handle_op()
            except Exception as _exc:
                import traceback
                _msg = f"!! ERROR en operación: {_exc}"
                print(traceback.format_exc())
                self._echo(_msg)
                self._op_mode = ""; self._op_pts = []; self._op_data = {}
                self._lbl_op.configure(text="")
            # Redibujar inmediatamente para mostrar panel DYN en nueva fase
            self.canvas.focus_set()
            self._redraw_dynamic()
            return

        # ── Herramientas de dibujo ─────────────────────────────────
        # Para dibujo siempre registramos el punto (ignoramos si hubo drag)
        if self.tool != "select":
            self._click_draw(wx, wy)
            # Mantener foco en canvas para que DYN capture el teclado
            self.canvas.focus_set()
            return

        # ── Selección ─────────────────────────────────────────────
        if was_drag:
            self._window_select(start[0], start[1], event.x, event.y)
        else:
            self._select_at(event.x, event.y)

    def _click_draw(self, wx, wy):
        self._dyn_clear()   # resetear DYN para el próximo segmento
        if self.tool in ("line", "polyline", "spline", "rect"):
            self.draw_pts.append((wx, wy))
            self._update_prompt()
            if self.tool == "line" and len(self.draw_pts) == 2:
                self._commit_line()
            elif self.tool == "rect" and len(self.draw_pts) == 2:
                self._commit_rect()
        elif self.tool == "circle":
            self.draw_pts.append((wx, wy))
            self._update_prompt()
            if len(self.draw_pts) == 2:
                self._commit_circle()
        elif self.tool == "arc":
            self.draw_pts.append((wx, wy))
            self._update_prompt()
            if len(self.draw_pts) == 3:
                self._commit_arc()
        elif self.tool == "text":
            self._pedir_texto(wx, wy)
        elif self.tool == "ellipse":
            self.draw_pts.append((wx, wy))
            self._update_prompt()
            if len(self.draw_pts) == 3:
                self._commit_ellipse()
        elif self.tool == "polygon":
            self.draw_pts.append((wx, wy))
            self._update_prompt()
            if len(self.draw_pts) == 2:
                self._commit_polygon()
        elif self.tool == "xline":
            self.draw_pts.append((wx, wy))
            self._update_prompt()
            if len(self.draw_pts) == 2:
                self._commit_xline()
        elif self.tool in ("cloud", "leader"):
            self.draw_pts.append((wx, wy))
            self._update_prompt()

    def _on_dblclick(self, event):
        # Primero: EATTEDIT si el doble clic es sobre un Insert con atributos
        wx, wy = self.s2w(event.x, event.y)
        hit_r = 20.0 / max(self.scale, 1e-6)
        # Usar índice espacial para evitar O(N) scan completo en dibujos grandes.
        _eidx = self._entity_index
        _ents_to_check = (
            self._query_viewport(wx - hit_r, wy - hit_r, wx + hit_r, wy + hit_r)
            if _eidx else self.entities
        )
        for e in _ents_to_check:
            if isinstance(e, Insert) and getattr(e, 'attribs', None):
                if self._dist_entity(wx, wy, e) <= hit_r:
                    self._open_eattedit_dialog(e)
                    return
        # Segundo: editar texto con doble clic
        if self._try_edit_text(event.x, event.y):
            return
        if self.tool == "polyline" and len(self.draw_pts) >= 2:
            self._fin_pline()
        elif self.tool == "spline" and len(self.draw_pts) >= 2:
            self._fin_spline()
        elif self.tool == "cloud" and len(self.draw_pts) >= 3:
            self._commit_cloud()
        elif self.tool == "leader" and len(self.draw_pts) >= 2:
            self._commit_leader()

    def _try_edit_text(self, sx, sy) -> bool:
        """Doble clic en entidad Texto → abre editor inline. True si encontró texto."""
        # Si ya hay un editor abierto, cerrarlo primero
        if getattr(self, "_inline_editor_frame", None):
            try:
                self._inline_editor_frame.destroy()
            except Exception:
                pass
            self._inline_editor_frame = None

        HIT = 24 / self.scale
        wx, wy = self.s2w(sx, sy)
        # Pre-filtrar con índice espacial para evitar O(N) scan en dibujos grandes.
        # La búsqueda retorna un subconjunto pequeño del viewport; si no hay índice
        # recae en self.entities (comportamiento anterior sin regresión).
        _eidx = self._entity_index
        if _eidx:
            _cands = self._query_viewport(wx - HIT, wy - HIT, wx + HIT, wy + HIT)
        else:
            _cands = self.entities
        for e in _cands:
            if not isinstance(e, Text):
                continue
            x0, y0, x1, y1 = self._text_world_bbox(e)
            margin = HIT
            if not (x0 - margin <= wx <= x1 + margin and
                    y0 - margin <= wy <= y1 + margin):
                continue
            lyr = self.layers.get(e.layer)
            if lyr and lyr.locked:
                self._echo(f"!! Capa '{e.layer}' bloqueada — no se puede editar")
                return True
            # Lookup del índice sólo al encontrar el hit (O(1) en la práctica:
            # ocurre ≤1 vez por doble-clic, normalmente 0).
            try:
                i = self.entities.index(e)
            except ValueError:
                continue
            self._open_inline_text_editor(i, e)
            return True
        return False

    def _open_inline_text_editor(self, i: int, e):
        """Editor de texto flotante (Toplevel) anclado al texto en pantalla.

        Usa tk.Toplevel con overrideredirect → siempre encima de los paneles.
        Fuente proporcional al zoom. Ancho = mtext_width real o estimado.
        Alto crece automáticamente. Clamped para no salirse de la pantalla.
        Enter / Ctrl+Enter = guardar.  Escape = cancelar.
        """
        BG  = "#0C1117"
        FG  = "#FCD34D"
        BRD = "#FCD34D"    # color del borde del Toplevel

        # ── Fuente proporcional al zoom ──────────────────────────────────
        font_px = max(9, min(60, int(e.height * self.scale * 0.82)))
        font    = ("Arial", font_px)
        line_h  = int(font_px * 1.55)          # altura de línea en px

        # ── Ancho del editor ─────────────────────────────────────────────
        mw = getattr(e, "mtext_width", 0.0) or 0.0
        if mw > 0:
            w_px = max(100, int(mw * self.scale))
        else:
            lines_ = e.content.split("\n") if "\n" in e.content else [e.content]
            max_ch = max((len(l) for l in lines_), default=10)
            w_px   = max(100, int(max_ch * font_px * 0.62 + 20))

        is_multi = "\n" in e.content or mw > 0
        n_lines  = max(1, e.content.count("\n") + 1)
        h_px     = n_lines * line_h + 14      # alto inicial

        # ── Posición absoluta en pantalla ────────────────────────────────
        sx, sy  = self.w2s(e.x, e.y)          # coords canvas-local
        abs_x   = self.canvas.winfo_rootx() + int(sx)
        abs_y   = self.canvas.winfo_rooty() + int(sy)

        # Ajustar esquina superior-izquierda según halign/valign
        ha = getattr(e, "halign", 0)
        va = getattr(e, "valign", 0)
        if ha == 2:   win_x = abs_x - w_px       # right-anchored
        elif ha == 1: win_x = abs_x - w_px // 2  # center
        else:         win_x = abs_x              # left-anchored

        if va == 3:   win_y = abs_y              # top-anchored
        elif va == 2: win_y = abs_y - h_px // 2  # middle
        else:         win_y = abs_y - h_px       # bottom-anchored

        # Clamp: no salirse de la pantalla
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_x = max(10, min(win_x, sw - w_px  - 10))
        win_y = max(10, min(win_y, sh - h_px  - 40))

        # ── Crear Toplevel ───────────────────────────────────────────────
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.wm_attributes("-topmost", True)
        win.configure(bg=BRD)                  # borde amarillo de 2 px
        self._inline_editor_frame = win        # referencia para cerrar desde _on_btn_down

        inner = tk.Frame(win, bg=BG)
        inner.pack(fill="both", expand=True, padx=2, pady=2)

        common = dict(font=font, bg=BG, fg=FG,
                      insertbackground="white",
                      selectbackground="#1E40AF",
                      selectforeground="white",
                      relief="flat", bd=0)

        # ── Callbacks compartidos ────────────────────────────────────────
        def _commit(content: str):
            content = content.strip()
            if content:
                self._push_undo()
                self.entities[i] = Text(
                    x=e.x, y=e.y,
                    content=content.upper(),
                    height=e.height,  angle=e.angle,
                    halign=getattr(e, "halign",      0),
                    valign=getattr(e, "valign",      0),
                    mtext_width=getattr(e, "mtext_width", 0.0),
                    layer=e.layer,    color=e.color,
                    linetype=getattr(e, "linetype",  "bylayer"),
                    linewidth=getattr(e, "linewidth", 0))
                self._rebuild_snap_index()
            _close()

        def _close():
            try:
                win.destroy()
            except Exception:
                pass
            self._inline_editor_frame = None
            self._redraw()

        # ── Widget de edición ────────────────────────────────────────────
        if is_multi:
            editor = tk.Text(inner, **common,
                             height=max(2, n_lines + 1),
                             width=1,          # ancho controlado por geometry()
                             wrap="word", undo=True)
            editor.insert("1.0", e.content)
            editor.tag_add("sel", "1.0", "end-1c")
            editor.pack(padx=4, pady=4, fill="both", expand=True)

            def _on_key(*_):
                txt  = editor.get("1.0", "end-1c")
                rows = max(2, txt.count("\n") + 2)
                editor.configure(height=rows)
                new_h = rows * line_h + 14
                new_h = min(new_h, sh - win_y - 10)
                win.geometry(f"{w_px}x{new_h}+{win_x}+{win_y}")
            editor.bind("<KeyRelease>", _on_key)

            editor.bind("<Control-Return>",
                        lambda ev: _commit(editor.get("1.0", "end-1c")) or "break")
            editor.bind("<Escape>", lambda ev: _close())
            widget = editor

        else:
            editor = tk.Entry(inner, **common, width=1)
            editor.insert(0, e.content)
            editor.select_range(0, "end")
            editor.pack(padx=4, pady=4, fill="x")

            editor.bind("<Return>",   lambda ev: _commit(editor.get()))
            editor.bind("<KP_Enter>", lambda ev: _commit(editor.get()))
            editor.bind("<Escape>",   lambda ev: _close())
            widget = editor

        # ── Posicionar y mostrar ─────────────────────────────────────────
        win.geometry(f"{w_px}x{h_px}+{win_x}+{win_y}")
        widget.focus_set()
        self._echo("TEXTO — Enter / Ctrl+Enter = guardar  |  Esc = cancelar")

    def _confirm_sel_to_base(self):
        """Confirma selección en *_sel y pasa a la fase de punto base."""
        sel = [e for e in self.entities if e.selected]
        if not sel:
            self._echo("!! Seleccione al menos una entidad"); return
        self._op_sel = sel
        self._op_pts = []
        m = self._op_mode
        if m == "copy_sel":
            self._op_data = {"n_copies": 0}
            self._op_mode = "copy_base"
            for e in self.entities:
                e.selected = False
            self._lbl_op.configure(text="COPY — Punto base:")
        elif m == "rotate_sel":
            self._op_data = {}
            self._op_mode = "rotate_base"
            self._lbl_op.configure(text="ROTATE — Punto base:")
            self._highlight_op_btn("rotate")
        elif m == "scale_sel":
            self._op_data = {}
            self._op_mode = "scale_base"
            self._lbl_op.configure(text="SCALE — Punto base:")
            self._highlight_op_btn("scale")
        elif m == "mirror_sel":
            self._op_data = {}
            self._op_mode = "mirror_p1"
            self._lbl_op.configure(text="MIRROR — Primer punto del eje:")
            self._highlight_op_btn("mirror")
        elif m == "array_sel":
            self._op_data = {}
            self._op_mode = "array_type"
            self._lbl_op.configure(
                text=f"ARRAY — {len(sel)} objetos  →  [R] Rectangular   [P] Polar   Enter = R:")
            self._highlight_op_btn("array")
        elif m == "align_sel":
            self._op_data = {}
            self._op_mode = "align_sp1"
            self._lbl_op.configure(text="ALIGN — Punto fuente 1:")
            self._highlight_op_btn(None)
        else:  # move_sel
            self._op_mode = "move_base"
            self._lbl_op.configure(text="MOVE — Punto base:")
        self._update_prompt()
        self._redraw_dynamic()

    def _on_rclick(self, event):
        """Clic derecho: ejecuta buffer de teclado si hay texto acumulado,
        o actúa como Enter / menú contextual según config."""
        # ── Prioridad 0: buffer de teclado acumulado → ejecutar ──────
        if self._kbd_buf:
            self._ejecutar_kbd_buf(); return

        if self.rclick_as_enter:
            # ── Modo Enter ───────────────────────────────────────────
            # Prioridad 1: DYN activo con datos → ejecutar DYN
            if (self.dyn_on and self._dyn_active()
                    and (self._dyn_buf or any(v is not None for v in self._dyn_locked))):
                self._dyn_execute(); return
            # Prioridad 2a: erase_sel → clic derecho = confirmar y borrar
            if self._op_mode == "erase_sel":
                self._erase(); self._finish_op(); return
            # Prioridad 2: *_sel → confirmar selección y pasar a operación
            if self._op_mode in ("move_sel", "copy_sel",
                                 "rotate_sel", "scale_sel", "mirror_sel",
                                 "array_sel", "align_sel"):
                self._confirm_sel_to_base(); return
            # Prioridad 3: cualquier herramienta/operación activa → salir al select
            if self._salir_herramienta(): return
            # Prioridad 4: idle → repetir último comando
            if self._last_cmd_name:
                self._ejecutar_accion(self._last_cmd_name, "")
            return
        # ── Modo menú contextual ─────────────────────────────────────
        if self._op_mode == "erase_sel":
            self._erase(); self._finish_op(); return
        if self._op_mode in ("move_sel", "copy_sel",
                             "rotate_sel", "scale_sel", "mirror_sel",
                             "array_sel", "align_sel"):
            self._confirm_sel_to_base(); return
        # Herramienta o operación activa → salir (no mostrar menú)
        if self._salir_herramienta(): return
        self._show_context_menu(event)

    # Catálogo completo de comandos disponibles para el menú contextual
    _CTX_CATALOG: list[tuple[str, str, str]] = [
        # (accion, label_menu, categoria)
        # ── Dibujo ──────────────────────────────────────────────────
        ("line",       "Línea            L",    "Dibujo"),
        ("polyline",   "Polilínea        PL",   "Dibujo"),
        ("spline",     "Spline           SPL",  "Dibujo"),
        ("rect",       "Rectángulo       REC",  "Dibujo"),
        ("circle",     "Círculo          C",    "Dibujo"),
        ("arc",        "Arco             A",    "Dibujo"),
        ("ellipse",    "Elipse           EL",   "Dibujo"),
        ("polygon",    "Polígono         POL",  "Dibujo"),
        ("text",       "Texto            T",    "Dibujo"),
        ("leader",     "Líder            LD",   "Dibujo"),
        ("cloud",      "Nube revisión    CL",   "Dibujo"),
        ("xline",      "Línea infinita   XL",   "Dibujo"),
        ("hatch",      "Hatch            BH",   "Dibujo"),
        ("block_cmd",  "Definir bloque   B",    "Dibujo"),
        ("insert",     "Insertar bloque  I",    "Dibujo"),
        ("image_cmd",  "Insertar imagen  IMG",  "Dibujo"),
        ("eattedit",   "Editar atributos EA",   "Dibujo"),
        # ── Edición ─────────────────────────────────────────────────
        ("move",       "Mover            M",    "Edición"),
        ("copy",       "Copiar           CO",   "Edición"),
        ("rotate",     "Rotar            RO",   "Edición"),
        ("scale",      "Escalar          SC",   "Edición"),
        ("mirror",     "Espejo           MI",   "Edición"),
        ("array",      "Array            AR",   "Edición"),
        ("align_cmd",  "Alinear          AL",   "Edición"),
        ("erase",      "Borrar           E",    "Edición"),
        # ── Modificación ────────────────────────────────────────────
        ("offset",     "Paralela         O",    "Modificación"),
        ("trim",       "Recortar         TR",   "Modificación"),
        ("extend",     "Extender         EX",   "Modificación"),
        ("fillet",     "Empalme          F",    "Modificación"),
        ("chamfer",    "Chaflán          CHA",  "Modificación"),
        ("break_cmd",  "Partir           BR",   "Modificación"),
        ("explode",    "Explotar         X",    "Modificación"),
        ("matchprop",  "Copiar props     MA",   "Modificación"),
        ("properties", "Propiedades      PR",   "Modificación"),
        # ── Cotas ───────────────────────────────────────────────────
        ("dim_h",      "Horizontal       DH",   "Cotas"),
        ("dim_v",      "Vertical         DV",   "Cotas"),
        ("dim_a",      "Alineada         DA",   "Cotas"),
        ("dim_ang",    "Angular          DAN",  "Cotas"),
        ("dim_r",      "Radio            DR",   "Cotas"),
        ("dim_d",      "Diámetro         DD",   "Cotas"),
        ("dim_arc_len","Longitud arco    DAR",  "Cotas"),
        ("dim_co",     "Continua         DCO",  "Cotas"),
        ("dim_ba",     "Línea base       DBA",  "Cotas"),
        ("dim_sp",     "Espaciado        DSP",  "Cotas"),
        ("dim_ord",    "Ordenada         DOR",  "Cotas"),
        # ── Capas ───────────────────────────────────────────────────
        ("layer_cmd",    "Gestor capas     LA",     "Capas"),
        ("layer_iso",    "Aislar capa      LAYISO", "Capas"),
        ("layer_off",    "Apagar capa      LAYOFF", "Capas"),
        ("layer_on",     "Encender todas   LAYON",  "Capas"),
        ("layer_lock",   "Bloquear capa    LAYLOCK","Capas"),
        ("layer_unlock", "Desbloquear      LAYULK", "Capas"),
        ("laymcur",      "Capa de objeto   LC",     "Capas"),
        # ── Medir ───────────────────────────────────────────────────
        ("dist",       "Distancia        DI",   "Medir"),
        ("measure",    "Medir segmentos  MEA",  "Medir"),
        ("area_cmd",   "Área             AREA", "Medir"),
        ("id_point",   "ID Punto         ID",   "Medir"),
        ("list_ent",   "Listar           LI",   "Medir"),
        # ── Vista ───────────────────────────────────────────────────
        ("zoom_e",   "Zoom extensión   ZE",  "Vista"),
        ("zoom_a",      "Zoom todo        ZA",  "Vista"),
        ("scrollspeed", "Vel. scroll      SS",  "Vista"),
        ("pan_cmd",  "Encuadrar        PAN", "Vista"),
        ("regen",    "Regenerar        RE",  "Vista"),
        # ── SLE — Aprendizaje ────────────────────────────────────────
        ("slecorr",  "Aprendizaje SLE  SLC", "SLE"),
    ]

    def _show_context_menu(self, event):
        """Menú contextual en clic derecho (idle)."""
        sel = [e for e in self.entities if e.selected]

        MBG   = "#1E293B"   # fondo
        MFG   = "#F1F5F9"   # texto
        MABG  = "#2563EB"   # fondo activo
        MAFG  = "#FFFFFF"   # texto activo
        MFONT = ("Segoe UI", 10)

        def _mk(parent):
            return tk.Menu(parent, tearoff=0,
                           bg=MBG, fg=MFG,
                           activebackground=MABG, activeforeground=MAFG,
                           font=MFONT, bd=0, relief="flat")

        m = _mk(self.root)

        if sel:
            # ── Selección activa ──────────────────────────────────
            m.add_command(label=f"  {len(sel)} entidad(es) seleccionada(s)",
                          state="disabled", font=("Segoe UI", 9))
            m.add_separator()
            for lbl, accion in [
                ("Mover           M",  "move"),
                ("Copiar          CO", "copy"),
                ("Rotar           RO", "rotate"),
                ("Escalar         SC", "scale"),
                ("Espejo          MI", "mirror"),
            ]:
                m.add_command(label=f"  {lbl}",
                              command=lambda a=accion: self._ejecutar_accion(a, ""))
            m.add_separator()
            # EATTEDIT solo cuando hay bloques con atributos en la selección
            from cad.entities import Insert as _Insert
            _sel_inserts = [e for e in sel if isinstance(e, _Insert)
                            and getattr(e, 'attribs', None)]
            if _sel_inserts:
                m.add_command(label="  Editar atributos  EA",
                              command=lambda: self._ejecutar_accion("eattedit", ""))
                m.add_separator()
            m.add_command(label="  Borrar          E",
                          command=lambda: self._ejecutar_accion("erase", ""))
            m.add_separator()
            m.add_command(label="  Propiedades     PR",
                          command=lambda: self._ejecutar_accion("properties", ""))
            m.add_command(label="  Listar          LI",
                          command=lambda: self._ejecutar_accion("list_ent", ""))
            m.add_command(label="  Área            AREA",
                          command=lambda: self._ejecutar_accion("area_cmd", ""))
            m.add_separator()
            m.add_command(label="  → Mover a capa activa",
                          command=self._cambiar_capa_sel)
        else:
            # ── Sin selección — herramientas de dibujo ────────────
            m.add_command(label="  Sin selección",
                          state="disabled", font=("Segoe UI", 9))
            m.add_separator()
            for lbl, tname in [
                ("Seleccionar     S",   "select"),
                ("Línea           L",   "line"),
                ("Polilínea       PL",  "polyline"),
                ("Rectángulo      REC", "rect"),
                ("Círculo         C",   "circle"),
                ("Arco            A",   "arc"),
                ("Texto           T",   "text"),
            ]:
                m.add_command(label=f"  {lbl}",
                              command=lambda t=tname: self._set_tool(t))

        m.add_separator()

        # Sub-menú edición rápida (siempre disponible)
        edit_sub = _mk(m)
        for lbl, accion in [
            ("Paralela        O",  "offset"),
            ("Recortar        TR", "trim"),
            ("Extender        EX", "extend"),
            ("Explotar        X",  "explode"),
            ("Copiar props    MA", "matchprop"),
        ]:
            edit_sub.add_command(label=f"  {lbl}",
                                 command=lambda a=accion: self._ejecutar_accion(a, ""))
        m.add_cascade(label="  Editar…", menu=edit_sub)

        # ── Favoritos personalizados ──────────────────────────────────
        if self._ctx_menu_cmds:
            m.add_separator()
            _catalog_map = {a: lbl for a, lbl, _ in self._CTX_CATALOG}
            m.add_command(label="  ★ Favoritos", state="disabled",
                          font=("Segoe UI", 9))
            for accion in self._ctx_menu_cmds:
                lbl = _catalog_map.get(accion, accion)
                _TOOL_NAMES = frozenset({
                    "line", "polyline", "spline", "rect", "circle", "arc",
                    "ellipse", "polygon", "text", "leader", "cloud", "xline",
                    "select",
                })
                if accion in _TOOL_NAMES:
                    m.add_command(label=f"  {lbl}",
                                  command=lambda t=accion: self._set_tool(t))
                else:
                    m.add_command(label=f"  {lbl}",
                                  command=lambda a=accion: self._ejecutar_accion(a, ""))

        # ── Sub-menú Capas (siempre disponible) ──────────────────────
        lay_sub = _mk(m)
        for lbl, accion in [
            ("Aislar capa     LAYISO",  "layer_iso"),
            ("Encender todas  LAYON",   "layer_on"),
            ("Bloquear capa   LAYLOCK", "layer_lock"),
            ("Desbloquear     LAYULK",  "layer_unlock"),
        ]:
            lay_sub.add_command(label=f"  {lbl}",
                                command=lambda a=accion: self._ejecutar_accion(a, ""))
        m.add_cascade(label="  Capas…", menu=lay_sub)

        m.add_separator()
        m.add_command(label="  Zoom extensión  ZE",
                      command=self._zoom_extents)
        m.add_command(label="  Zoom todo       ZA",
                      command=lambda: self._ejecutar_accion("zoom_a", ""))
        m.add_command(label="  Encuadrar       PAN",
                      command=lambda: self._ejecutar_accion("pan_cmd", ""))
        m.add_command(label=f"  Vel. scroll      SS  [{self._scroll_speed}]",
                      command=lambda: self._ejecutar_accion("scrollspeed", ""))
        m.add_separator()
        m.add_command(label="  🤖 Asistente IA  /",
                      command=self._focus_ia_input)
        if self._sle_disponible:
            m.add_command(label="  🧠 Registrar corrección  SLC",
                          command=self._registrar_correccion_sle)
        m.add_separator()
        m.add_command(label="  Cancelar  Esc",
                      command=self._cancelar)

        try:
            self._ctx_popup_open = True
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
            self._ctx_popup_open = False

    # ─── Operaciones (MOVE / COPY / DIST / ZOOM_W) ───────────────
    def _handle_op(self):
        m = self._op_mode
        pts = self._op_pts

        # ── EATTEDIT: pick de Insert ──────────────────────────────────
        if m == "eattedit_pick" and len(pts) == 1:
            wx, wy = pts[0]
            # Buscar el Insert más cercano al clic
            best_ins = None
            best_d   = 1e9
            for e in self.entities:
                if not isinstance(e, Insert):
                    continue
                d = self._dist_entity(wx, wy, e)
                if d < best_d:
                    best_d = d
                    best_ins = e
            self._op_mode = ""
            self._op_pts  = []
            self._lbl_op.configure(text="")
            if best_ins is None or best_d > 20.0 / max(self.scale, 1e-6):
                self._echo("EATTEDIT: no se encontró ningún bloque en ese punto")
                return
            if not getattr(best_ins, 'attribs', None):
                self._echo(f"EATTEDIT: '{best_ins.block_name}' no tiene atributos editables")
                return
            self._open_eattedit_dialog(best_ins)
            return

        # ── POLYGON sides: clic mientras se pedían los lados ──────────
        if m == "polygon_sides" and len(pts) == 1:
            # Confirmar lados desde el buf DYN (o mantener actual)
            try:
                n = int(float(self._dyn_buf)) if self._dyn_buf else self._polygon_sides
                if n >= 3:
                    self._polygon_sides = int(n)
            except Exception:
                pass
            self._dyn_clear()
            self._op_mode = ""
            # Usar el clic como primer punto del polígono (centro)
            self.draw_pts.append(pts[0])
            self._op_pts = []
            self._lbl_op.configure(
                text=f"POLÍGONO {self._polygon_sides} lados — clic en vértice:")
            self._update_prompt()
            self._redraw_dynamic()
            return

        # ── ARRAY POLAR: clic en centro → pedir cantidad ──────────────
        if m == "array_pol_ctr" and len(pts) == 1:
            cx, cy = pts[0]
            self._op_data["pol_ctr"] = (cx, cy)
            self._op_pts = []
            self._op_mode = "array_pol_n"
            self._dyn_clear()
            self._lbl_op.configure(
                text=f"ARRAY POLAR — ⊙({cx:.3f},{cy:.3f}) — N de items (Enter=6):")
            self._update_prompt()
            self._redraw_dynamic()
            return

        if m in ("move_base", "copy_base") and len(pts) == 1:
            self._op_mode = "move_dest" if m == "move_base" else "copy_dest"
            self._update_prompt()
            self._dyn_clear()
            self.canvas.focus_set()
            self._redraw_dynamic()

        elif m in ("move_dest", "copy_dest") and len(pts) == 2:
            bx, by = pts[0]; dx2, dy2 = pts[1]
            dx, dy = dx2-bx, dy2-by
            sel_ids = {id(e) for e in self._op_sel}
            if m == "move_dest":
                # Diff: solo las entidades seleccionadas cambian de posición
                mod_pairs = [(i, e) for i, e in enumerate(self.entities)
                             if id(e) in sel_ids]
                self._push_undo_diff(modified=mod_pairs)
                for i, e in mod_pairs:
                    self.entities[i] = e.translated(dx, dy)
                # MOVE termina tras un destino (igual que AutoCAD)
                self._op_mode = ""; self._op_sel = []; self._op_pts = []; self._op_data = {}
                self._grips = []; self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False
                self._lbl_op.configure(text="")
                self._highlight_op_btn(None)
                self._dyn_clear()
            else:
                # COPY: multi-destino — se agregan N entidades al final
                n_add = len(self._op_sel)
                self._push_undo_diff(added_count=n_add)
                for e in self._op_sel:
                    self.entities.append(e.translated(dx, dy))
                n_copies = self._op_data.get("n_copies", 0) + 1
                self._op_data["n_copies"] = n_copies
                self._op_pts = [pts[0]]   # punto base fijo (igual que AutoCAD)
                self._lbl_op.configure(
                    text=f"COPY ({n_copies}) — Siguiente destino (ESC=fin):")
                self._dyn_clear()          # limpiar para siguiente destino
            self._rebuild_snap_index(); self._redraw()

        elif m == "dist_p1" and len(pts) == 1:
            self._op_mode = "dist_p2"
            self._lbl_op.configure(text="Segundo punto:")

        elif m == "dist_p2" and len(pts) == 2:
            (x1,y1),(x2,y2) = pts
            dist = math.hypot(x2-x1, y2-y1)
            ang  = math.degrees(math.atan2(y2-y1, x2-x1))
            msg  = f"DIST = {dist:.4f} m   Δx={x2-x1:.4f}  Δy={y2-y1:.4f}   Ang={ang:.2f}°"
            self._echo(msg)
            self._lbl_op.configure(text=msg)
            self._op_mode = ""; self._op_pts = []
            self._redraw_static()

        elif m == "laymcur" and len(pts) == 1:
            wx_c, wy_c = pts[0]
            res = self._pick_entity_at(wx_c, wy_c)
            if res:
                _, ent = res
                self._activar_capa(ent.layer)
                self._echo(f"LAYMCUR — capa activa: {ent.layer}")
            else:
                self._echo("LAYMCUR — ninguna entidad bajo el cursor")
            self._op_mode = ""; self._op_pts = []
            self._lbl_op.configure(text="")

        elif m == "layiso_pick" and len(pts) == 1:
            wx_c, wy_c = pts[0]
            res = self._pick_entity_at(wx_c, wy_c)
            if res:
                _, ent = res
                capa_iso = ent.layer
                for n, l in self.layers.items():
                    l.visible = (n == capa_iso)
                self._build_layer_panel(); self._redraw_static()
                self._echo(f"LAYISO — solo visible: {capa_iso}")
            else:
                self._echo("LAYISO — ninguna entidad bajo el cursor")
            self._op_mode = ""; self._op_pts = []
            self._lbl_op.configure(text="")

        elif m == "id_pick" and len(pts) == 1:
            wx_c, wy_c = pts[0]
            # Preferir snap si está activo
            if self.snap_pt:
                wx_c, wy_c = self.snap_pt[0], self.snap_pt[1]
            self._echo(f"ID  X={wx_c:.4f}  Y={wy_c:.4f}")
            self._op_mode = ""; self._op_pts = []
            self._lbl_op.configure(text="")

        elif m == "zoom_w1" and len(pts) == 1:
            self._op_mode = "zoom_w2"
            self._lbl_op.configure(text="ZOOM W — Segunda esquina:")
            self._redraw_dynamic()

        elif m == "zoom_w2" and len(pts) == 2:
            (wx0,wy0),(wx1,wy1) = pts
            W = max(self.canvas.winfo_width(),  800)
            H = max(self.canvas.winfo_height(), 600)
            sx0, sy0 = self.w2s(wx0, wy0)
            sx1, sy1 = self.w2s(wx1, wy1)
            self._zoom_window(sx0, sy0, sx1, sy1)
            self._op_mode = ""; self._op_pts = []
            self._lbl_op.configure(text="")

        elif m == "circle_r" and len(pts) == 2:
            self.draw_pts = list(pts)
            self._commit_circle()
            self._op_mode = ""; self._op_pts = []

        # ── ROTATE ────────────────────────────────────────────────────
        elif m == "rotate_base" and len(pts) == 1:
            self._op_data["base"] = pts[0]
            self._op_pts = []
            self._op_mode = "rotate_angle"
            self._lbl_op.configure(text="ROTATE — Escribe ángulo ↵ o clic:")
            self._dyn_clear()
            self.canvas.focus_set()
            self._redraw_dynamic()

        elif m == "rotate_angle" and len(pts) == 1:
            bx, by = self._op_data["base"]
            px, py = pts[0]
            ang = math.degrees(math.atan2(py-by, px-bx))
            self._apply_rotate(ang)

        # ── SCALE ─────────────────────────────────────────────────────
        elif m == "scale_base" and len(pts) == 1:
            self._op_data["base"] = pts[0]
            self._op_pts = []
            self._op_mode = "scale_factor"
            self._lbl_op.configure(text="SCALE — Escribe factor ↵ o clic:")
            self._dyn_clear()
            self.canvas.focus_set()
            self._redraw_dynamic()

        elif m == "scale_factor" and len(pts) == 1:
            bx, by = self._op_data["base"]
            px, py = pts[0]
            f = max(0.001, math.hypot(px-bx, py-by))
            self._apply_scale(f)

        # ── MIRROR ────────────────────────────────────────────────────
        elif m == "mirror_p1" and len(pts) == 1:
            self._op_data["p1"] = pts[0]
            self._op_pts = []
            self._op_mode = "mirror_p2"
            self._lbl_op.configure(text="MIRROR — Escribe dist+ángulo ↵ o clic:")
            self._dyn_clear()
            self.canvas.focus_set()
            self._redraw_dynamic()

        elif m == "mirror_p2" and len(pts) == 1:
            ax, ay = self._op_data["p1"]
            bx, by = pts[0]
            if abs(bx-ax) > 1e-9 or abs(by-ay) > 1e-9:
                # Guardar eje y preguntar si se borran los originales
                self._op_data["axis"] = (ax, ay, bx, by)
                self._op_mode = "mirror_keep"
                self._op_pts = []
                self._lbl_op.configure(
                    text="MIRROR — ¿Borrar originales?  [S/Y] Sí   [N] No   Enter = No:")
                self._update_prompt()
                self._redraw_dynamic()
            else:
                self._echo("!! Los dos puntos del eje son iguales")
                self._op_pts = []

        # ── OFFSET ────────────────────────────────────────────────────
        # ── TRIM  (QuickTrim: 1 clic, bordes auto) ────────────────────
        elif m == "trim_obj" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line, Polyline, Circle, Arc))
            if res:
                idx, target = res
                if isinstance(target, Line):
                    self._quick_trim_line(idx, target, wx, wy)
                elif isinstance(target, Polyline):
                    self._quick_trim_poly(idx, target, wx, wy)
                elif isinstance(target, Circle):
                    self._quick_trim_circle(idx, target, wx, wy)
                elif isinstance(target, Arc):
                    self._quick_trim_arc(idx, target, wx, wy)
                self._op_data["n_ops"] = self._op_data.get("n_ops", 0) + 1
            else:
                self._echo("TR: clic sobre la línea, polilínea, círculo o arco a recortar")
            self._op_pts = []   # queda en trim_obj para múltiples recortes

        # ── EXTEND (QuickExtend: 1 clic cerca del extremo) ────────────
        elif m == "extend_obj" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line,))
            if res:
                idx, line = res
                self._quick_extend_line(idx, line, wx, wy)
                self._op_data["n_ops"] = self._op_data.get("n_ops", 0) + 1
            else:
                self._echo("EX: clic cerca del extremo de la línea a extender")
            self._op_pts = []   # queda en extend_obj para múltiples extensiones

        # ── FILLET ────────────────────────────────────────────────────
        elif m == "fillet_p1" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line,))
            if res:
                self._op_data["fillet_idx1"] = res[0]
                self._op_data["fillet_l1"]   = res[1]
                self._op_mode = "fillet_p2"; self._op_pts = []
                self._lbl_op.configure(
                    text=f"FILLET  R={self.fillet_radius:.3f} — Clic en 2ª línea:")
                self._update_prompt()
            else:
                self._echo("FILLET: clic en una línea"); self._op_pts = []

        elif m == "fillet_p2" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line,))
            if res:
                idx2, line2 = res
                idx1 = self._op_data.get("fillet_idx1")
                line1 = self._op_data.get("fillet_l1")
                if idx1 is not None and idx1 != idx2:
                    self._do_fillet(idx1, line1, idx2, line2)
                else:
                    self._echo("FILLET: seleccione una línea diferente")
            else:
                self._echo("FILLET: clic en una línea")
            # Volver a fillet_p1 para múltiples fillets consecutivos
            self._op_mode = "fillet_p1"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(
                text=f"FILLET  R={self.fillet_radius:.3f} — Clic en 1ª línea:")
            self._update_prompt()

        # ── CHAMFER ───────────────────────────────────────────────────
        elif m == "chamfer_p1" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line,))
            if res:
                self._op_data["chamfer_idx1"] = res[0]
                self._op_data["chamfer_l1"]   = res[1]
                self._op_mode = "chamfer_p2"; self._op_pts = []
                self._lbl_op.configure(
                    text=f"CHAMFER  D={self.chamfer_d1:.3f} — Clic en 2ª línea:")
                self._update_prompt()
            else:
                self._echo("CHAMFER: clic en una línea"); self._op_pts = []

        elif m == "chamfer_p2" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line,))
            if res:
                idx2, line2 = res
                idx1 = self._op_data.get("chamfer_idx1")
                line1 = self._op_data.get("chamfer_l1")
                if idx1 is not None and idx1 != idx2:
                    self._do_chamfer(idx1, line1, idx2, line2)
                else:
                    self._echo("CHAMFER: seleccione una línea diferente")
            else:
                self._echo("CHAMFER: clic en una línea")
            self._op_mode = "chamfer_p1"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(
                text=f"CHAMFER  D={self.chamfer_d1:.3f} — Clic en 1ª línea:")
            self._update_prompt()

        # ── BREAK ─────────────────────────────────────────────────────
        elif m == "break_p1" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line, Polyline, Arc, Circle))
            if res:
                idx, ent = res
                self._op_data["break_idx"] = idx
                self._op_data["break_ent"] = ent
                self._op_data["break_p1"]  = (wx, wy)
                self._op_mode = "break_p2"
                self._op_pts  = []
                self._lbl_op.configure(
                    text="BREAK — 2º punto de ruptura  (Enter o @ = mismo punto):")
                self._update_prompt()
                self._redraw_dynamic()
            else:
                self._echo("BREAK: clic en línea, arco o polilínea")
                self._op_pts = []

        elif m == "break_p2" and len(pts) == 1:
            wx2, wy2 = pts[0]
            idx = self._op_data["break_idx"]
            ent = self._op_data["break_ent"]
            p1  = self._op_data["break_p1"]
            self._do_break_two(idx, ent, p1, (wx2, wy2))
            self._op_mode = "break_p1"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="BREAK — Clic en entidad en el 1er punto:")
            self._update_prompt()

        # ── ALIGN ─────────────────────────────────────────────────────
        elif m == "align_sp1" and len(pts) == 1:
            self._op_data["sp1"] = pts[0]
            self._op_mode = "align_sp2"; self._op_pts = []
            self._lbl_op.configure(text="ALIGN — Punto fuente 2:")
            self._update_prompt()

        elif m == "align_sp2" and len(pts) == 1:
            self._op_data["sp2"] = pts[0]
            self._op_mode = "align_dp1"; self._op_pts = []
            self._lbl_op.configure(text="ALIGN — Punto destino 1:")
            self._update_prompt()

        elif m == "align_dp1" and len(pts) == 1:
            self._op_data["dp1"] = pts[0]
            self._op_mode = "align_dp2"; self._op_pts = []
            self._lbl_op.configure(text="ALIGN — Punto destino 2:")
            self._update_prompt()

        elif m == "align_dp2" and len(pts) == 1:
            self._op_data["dp2"] = pts[0]
            self._do_align()
            self._op_mode = ""; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="")

        # ── MEASURE ───────────────────────────────────────────────────
        elif m == "measure_p1" and len(pts) == 1:
            self._measure_last_pt = pts[0]
            self._measure_total = 0.0
            self._op_data["n_segs"] = 0
            self._op_mode = "measure_next"; self._op_pts = []
            self._lbl_op.configure(text="MEASURE — Siguiente punto (ESC=total):")
            self._update_prompt()

        elif m == "measure_next" and len(pts) == 1:
            wx, wy = pts[0]
            lx, ly = self._measure_last_pt
            d = math.hypot(wx - lx, wy - ly)
            self._measure_total += d
            self._op_data["n_segs"] = self._op_data.get("n_segs", 0) + 1
            self._echo(f"MEASURE  segmento={d:.4f} m   total={self._measure_total:.4f} m")
            self._measure_last_pt = (wx, wy)
            self._op_pts = []   # stays in measure_next

        # ── MATCHPROP ─────────────────────────────────────────────────
        elif m == "matchprop_src" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy)
            if res:
                _, src = res
                self._op_data["src_layer"] = src.layer
                self._op_data["src_color"] = src.color
                self._op_pts = []
                self._op_mode = "matchprop_dst"
                self._lbl_op.configure(
                    text=f"MA  capa={src.layer} — Clic en destinos (ESC=fin):")
            else:
                self._echo("MA: clic en entidad fuente")
                self._op_pts = []

        elif m == "matchprop_dst" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy)
            if res:
                idx, _ = res
                # Un solo undo para todo el comando matchprop
                if not self._op_data.get("undo_pushed"):
                    self._push_undo()
                    self._op_data["undo_pushed"] = True
                self.entities[idx].layer = self._op_data["src_layer"]
                self.entities[idx].color = self._op_data["src_color"]
                self._op_data["n_dst"] = self._op_data.get("n_dst", 0) + 1
                self._redraw_static()
            else:
                self._echo("MA: clic en entidad destino (ESC=fin)")
            self._op_pts = []   # stays in matchprop_dst

        # ── COTAS LINEALES (H / V / A) ────────────────────────────────
        elif m == "dim_lp1" and len(pts) == 1:
            self._op_mode = "dim_lp2"
            dt = self._op_data.get("dim_type", "H")
            self._lbl_op.configure(text=f"DIM-{dt} — Segundo punto de extensión:")
            self._redraw_dynamic()

        elif m == "dim_lp2" and len(pts) == 2:
            self._op_mode = "dim_lpos"
            dt = self._op_data.get("dim_type", "H")
            self._lbl_op.configure(text=f"DIM-{dt} — Posición de la línea de cota:")
            self._redraw_dynamic()

        elif m == "dim_lpos" and len(pts) == 3:
            self._commit_dim()

        # ── COTA RADIO / DIÁMETRO ─────────────────────────────────────
        elif m == "dim_r_obj" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Circle,))
            dt  = self._op_data.get("dim_type", "R")
            if res:
                _, circ = res
                ang = math.atan2(wy - circ.cy, wx - circ.cx)
                rpx = circ.cx + circ.radius * math.cos(ang)
                rpy = circ.cy + circ.radius * math.sin(ang)
                self._op_data["p1"] = (circ.cx, circ.cy)
                self._op_data["p2"] = (rpx, rpy)
                self._op_pts = [(circ.cx, circ.cy), (rpx, rpy), (rpx, rpy)]
                self._commit_dim()
            else:
                self._op_data["p1"] = pts[0]
                self._op_mode = "dim_r_pt"
                self._op_pts = []
                lbl = "DIMD — Punto en la circunferencia:" if dt == "D" else "DIMR — Punto en la circunferencia:"
                self._lbl_op.configure(text=lbl)
                self._redraw_dynamic()

        elif m == "dim_r_pt" and len(pts) == 1:
            p1 = self._op_data.get("p1", (0.0, 0.0))
            self._op_pts = [p1, pts[0], pts[0]]
            self._commit_dim()

        # ── COTA LONGITUD DE ARCO (DAR) ───────────────────────────────
        elif m == "dim_arc_obj" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Arc,))
            if res:
                _, arc = res
                sa = math.radians(arc.start_ang)
                ea = math.radians(arc.end_ang)
                p1 = (arc.cx + arc.radius * math.cos(sa),
                      arc.cy + arc.radius * math.sin(sa))
                p2 = (arc.cx + arc.radius * math.cos(ea),
                      arc.cy + arc.radius * math.sin(ea))
                self._op_data["p1"]    = p1
                self._op_data["p2"]    = p2
                self._op_data["cen"]   = (arc.cx, arc.cy)
                self._op_data["arc_r"] = arc.radius
                self._op_mode = "dim_arc_pos"
                self._op_pts  = []
                self._lbl_op.configure(text="DAR — Posición del texto:")
                self._redraw_dynamic()
            else:
                self._echo("DAR: clic directamente sobre un arco")

        elif m == "dim_arc_pos" and len(pts) == 1:
            p1  = self._op_data["p1"]
            p2  = self._op_data["p2"]
            cen = self._op_data["cen"]
            lyr = "A-COTA" if "A-COTA" in self.layers else self.active_layer
            dim = Dimension(p1=p1, p2=p2, pos=cen, dim_type="ARC",
                            text_pos=pts[0], layer=lyr)
            self._push_undo(); self._add_entity(dim)
            self._echo(f"DAR: {dim.text()}")
            self._finish_op()

        # ── COTA ORDENADA (DOR) ───────────────────────────────────────
        elif m == "dim_ord_p1" and len(pts) == 1:
            self._op_data["p1"] = pts[0]
            self._op_mode = "dim_ord_p2"
            self._op_pts  = []
            self._lbl_op.configure(text="DOR — Extremo del líder:")
            self._redraw_dynamic()

        elif m == "dim_ord_p2" and len(pts) == 1:
            p1  = self._op_data["p1"]
            p2  = pts[0]
            lyr = "A-COTA" if "A-COTA" in self.layers else self.active_layer
            # pos = midpoint del líder (para grips)
            pos = ((p1[0]+p2[0])/2, (p1[1]+p2[1])/2)
            dim = Dimension(p1=p1, p2=p2, pos=pos, dim_type="ORD", layer=lyr)
            self._push_undo(); self._add_entity(dim)
            self._echo(f"DOR: {dim.text()}")
            self._finish_op()

        # ── COTA ANGULAR ──────────────────────────────────────────────
        elif m == "dim_ang_cen" and len(pts) == 1:
            self._op_data["cen"] = pts[0]
            self._op_pts = []
            self._op_mode = "dim_ang_p1"
            self._lbl_op.configure(text="DIMANG — Primer punto del eje:")
            self._redraw_dynamic()

        elif m == "dim_ang_p1" and len(pts) == 1:
            self._op_mode = "dim_ang_p2"
            self._lbl_op.configure(text="DIMANG — Segundo punto del eje:")
            self._redraw_dynamic()

        elif m == "dim_ang_p2" and len(pts) == 2:
            cen = self._op_data.get("cen", (0.0, 0.0))
            self._op_pts = [cen, pts[0], pts[1]]
            # p1=cen, p2=arm1, pos=arm2 — reordenar para Dimension
            self._op_data["ang_cen"] = cen
            self._commit_dim()

        # ── COTA CONTINUA / BASE ──────────────────────────────────────
        elif m == "dim_chain_next" and len(pts) == 1:
            self._commit_dim_chain(pts[0])

        # ── ESPACIADO DE COTAS — selección de objetivos ───────────────
        elif m == "dim_sp_pick" and len(pts) == 1:
            wx, wy = pts[0]
            from cad.entities import Dimension as _Dim
            HIT = 14 / self.scale
            best = None; best_d = HIT
            for e in self.entities:
                if not isinstance(e, _Dim):
                    continue
                if e.dim_type not in ("H", "V", "A"):
                    continue
                d = self._dist_entity(wx, wy, e)
                if d < best_d:
                    best_d = d; best = e
            if best is None:
                self._echo("DSP: clic sobre una cota lineal (H/V/A)")
            else:
                base = self._op_data.get("base")
                if base is None:
                    self._op_data["base"] = best
                    self._op_data["targets"] = []
                    self._echo("DSP: base seleccionada. Ahora clic en cotas a alinear (Enter=aplicar, ESC=cancelar)")
                elif best is base:
                    self._echo("DSP: esa ya es la base")
                else:
                    if best.dim_type != base.dim_type:
                        self._echo("!! DSP: las cotas deben ser del mismo tipo")
                    elif best in self._op_data["targets"]:
                        self._echo("DSP: ya estaba en la lista")
                    else:
                        self._op_data["targets"].append(best)
                        n = len(self._op_data["targets"])
                        self._echo(f"DSP: {n} cota(s) a alinear (Enter=aplicar)")
            self._op_pts = []

        # ── HATCH — acumulación de puntos ─────────────────────────────
        elif m == "insert_place":
            # Clic coloca el bloque; el modo se mantiene para múltiples copias
            self._commit_insert(wx, wy)

        elif m == "image_place" and len(pts) == 1:
            wx, wy = pts[0]
            self._op_data["img_x"] = wx
            self._op_data["img_y"] = wy
            self._op_mode = "image_width"
            self._op_pts  = []
            asp  = self._op_data.get("img_asp", 1.0)
            self._lbl_op.configure(
                text=f"IMG — Ancho en metros (alto auto = W/{asp:.2f}) → escribe y Enter:")
            self._update_prompt()
            self._dyn_clear()
            self.canvas.focus_set()
            self._redraw_dynamic()

        elif m == "hatch_pts":
            # Puntos se acumulan; Enter cierra y crea el hatch
            self._lbl_op.configure(
                text=f"HATCH — Punto {len(pts)} (Enter=cerrar | ESC=cancelar):")
            self._redraw_dynamic()

        elif m == "offset_sel" and len(pts) == 1:
            # Fase 1 — seleccionar entidad.  NO crear offset todavía.
            wx, wy = pts[0]
            d = self._op_data.get("offset_dist", 0.5)
            HIT = 12 / self.scale
            best = None; best_d = HIT
            for e in self.entities:
                lyr = self.layers.get(e.layer)
                if lyr and lyr.locked:
                    continue
                de = self._dist_entity(wx, wy, e)
                if de < best_d and isinstance(e, (Line, Circle, Polyline)):
                    best_d = de; best = e
            if best:
                # Entidad encontrada → pasar a fase 2 (elegir lado con preview)
                self._op_data["offset_entity"] = best
                self._op_mode = "offset_side"
                self._op_pts  = []
                self._lbl_op.configure(
                    text=f"OFFSET  d={d:.3f} — Mueva el cursor al lado deseado y haga clic:")
                self._redraw_dynamic()
            else:
                self._echo("OFFSET — clic sobre línea, polilínea o círculo")
                self._op_pts = []   # reintentar

        elif m == "offset_side" and len(pts) == 1:
            # Fase 2 — el cursor indica el lado; el clic lo confirma.
            # Usar posición cruda (sin snap) para detectar lado correctamente.
            side_x, side_y = getattr(self, "_click_raw_w",
                                     self.mouse_w)   # fallback: mouse actual
            d    = self._op_data.get("offset_dist", 0.5)
            best = self._op_data.get("offset_entity")
            if best:
                if isinstance(best, Line):
                    new_e = _offset_line(best, d, side_x, side_y)
                elif isinstance(best, Circle):
                    new_e = _offset_circle(best, d, side_x, side_y)
                else:
                    new_e = _offset_polyline(best, d, side_x, side_y)
                if new_e:
                    self._push_undo()
                    self.entities.append(new_e)
                    self._rebuild_snap_index(); self._redraw()
                else:
                    self._echo("OFFSET — radio resultante inválido (círculo demasiado pequeño)")
            # Volver a offset_sel para offset múltiple con la misma distancia
            self._op_data.pop("offset_entity", None)
            self._op_mode = "offset_sel"
            self._op_pts  = []
            self._lbl_op.configure(
                text=f"OFFSET  d={d:.3f} — Clic en otra entidad  (ESC = salir):")
            self._redraw_dynamic()

    # ─── Aplicar transformaciones ─────────────────────────────────
    def _apply_rotate(self, deg: float):
        bx, by = self._op_data.get("base", (0.0, 0.0))
        sel_ids = {id(e) for e in self._op_sel}
        mod_pairs = [(i, e) for i, e in enumerate(self.entities) if id(e) in sel_ids]
        self._push_undo_diff(modified=mod_pairs)
        for i, e in mod_pairs:
            self.entities[i] = e.rotated(bx, by, deg)
        self._finish_op()

    def _apply_scale(self, f: float):
        if f < 0.001:
            self._echo("!! Factor demasiado pequeño"); return
        bx, by = self._op_data.get("base", (0.0, 0.0))
        sel_ids = {id(e) for e in self._op_sel}
        mod_pairs = [(i, e) for i, e in enumerate(self.entities) if id(e) in sel_ids]
        self._push_undo_diff(modified=mod_pairs)
        for i, e in mod_pairs:
            self.entities[i] = e.scaled(bx, by, f)
        self._finish_op()

    def _apply_mirror(self, ax, ay, bx, by, keep_source: bool = False):
        """Aplica mirror al eje (ax,ay)→(bx,by).

        keep_source=False (default AutoCAD): reemplaza originales con espejo.
        keep_source=True:  mantiene originales y agrega el espejo como entidad nueva.
        """
        sel_ids   = {id(e) for e in self._op_sel}
        mod_pairs = [(i, e) for i, e in enumerate(self.entities) if id(e) in sel_ids]
        if keep_source:
            # Agrega copias especulares al final; originales intactos
            mirrored = [e.mirrored(ax, ay, bx, by) for _, e in mod_pairs]
            self._push_undo_diff(added_count=len(mirrored))
            self.entities.extend(mirrored)
        else:
            # Reemplaza originales con sus versiones especulares
            self._push_undo_diff(modified=mod_pairs)
            for i, e in mod_pairs:
                self.entities[i] = e.mirrored(ax, ay, bx, by)
        self._finish_op()

    def _finish_op(self):
        self._op_mode = ""; self._op_sel = []; self._op_pts = []; self._op_data = {}
        self._grips = []; self._hot_grip = None; self._hover_grip = None
        self._grip_drag_mode = False
        for e in self.entities:
            e.selected = False
        self._lbl_op.configure(text="")
        self._highlight_op_btn(None)
        self._rebuild_snap_index(); self._redraw()

    def _commit_dim(self):
        """Crea una entidad Dimension con los puntos acumulados y reinicia el modo."""
        dt   = self._op_data.get("dim_type", "H")
        pts  = self._op_pts

        if dt in ("H", "V", "A"):
            if len(pts) < 3:
                self._echo("!! Cota: faltan puntos"); return
            p1, p2, pos = pts[0], pts[1], pts[2]
        elif dt in ("R", "D"):
            if len(pts) < 2:
                self._echo(f"!! Cota {dt}: faltan puntos"); return
            p1  = self._op_data.get("p1", pts[0])
            p2  = self._op_data.get("p2", pts[1])
            pos = p2
        elif dt == "ANG":
            if len(pts) < 3:
                self._echo("!! Cota ANG: faltan puntos"); return
            p1  = pts[0]   # centro
            p2  = pts[1]   # extremo brazo 1
            pos = pts[2]   # extremo brazo 2
        else:
            return

        # Preferir capa A-COTA si existe, si no usar la activa
        lyr = "A-COTA" if "A-COTA" in self.layers else self.active_layer
        # Verificar si la capa está bloqueada antes de intentar agregar
        lyr_obj = self.layers.get(lyr)
        if lyr_obj and lyr_obj.locked:
            self._echo(f"!! Capa '{lyr}' bloqueada — desbloquee para cotar")
            self._op_mode = ""; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="")
            return
        dim = Dimension(p1=p1, p2=p2, pos=pos, dim_type=dt, layer=lyr)
        self._push_undo()
        self._add_entity(dim)
        val = dim.text()
        self._echo(f"✓ COTA-{dt} {val}  (capa {lyr}  —  {len(self.entities)} ent.)")

        # Limpiar modo → queda listo para otra cota del mismo tipo (como AutoCAD)
        self._op_mode = ""; self._op_pts = []; self._op_data = {}
        self._lbl_op.configure(text="")
        self._highlight_op_btn(None)
        self._rebuild_snap_index(); self._redraw()

    # ════════════════════════════════════════════════════════════════
    # COTA CONTINUA / BASE  (DCO / DBA)  +  ESPACIADO  (DSP)
    # ════════════════════════════════════════════════════════════════

    def _last_linear_dim(self):
        """Devuelve la última Dimension lineal (H/V/A) agregada, o None."""
        from cad.entities import Dimension as _Dim
        for e in reversed(self.entities):
            if isinstance(e, _Dim) and e.dim_type in ("H", "V", "A"):
                return e
        return None

    def _dim_baseline_spacing(self) -> float:
        """Distancia entre líneas de cota base (DIMSTYLE baseline_spacing, o 3.75×altura)."""
        try:
            cfg = self._leer_config_ia()
            ds  = cfg.get("dimstyles", {})
            name = ds.get("active", "Arq-50")
            style = ds.get("styles", {}).get(name, {})
            # Usa baseline_spacing directo si el usuario lo configuró
            bs = style.get("baseline_spacing", 0.0)
            if bs and float(bs) > 0:
                return float(bs)
            h = float(style.get("text_height", 0.20))
            return max(0.10, h * 3.75)
        except Exception:
            return 0.40

    def _iniciar_dim_chain(self, modo: str):
        """Inicia DCO (continue) o DBA (baseline) tomando la última cota lineal como base."""
        base = self._last_linear_dim()
        if base is None:
            self._echo(f"!! {'DCO' if modo == 'continue' else 'DBA'}: dibuje primero una cota lineal (DH/DV/DA)")
            return
        accion = "dim_co" if modo == "continue" else "dim_ba"
        prefix = "DCO" if modo == "continue" else "DBA"
        self._op_data = {
            "dim_type": base.dim_type,
            "base":     base,
            "modo":     modo,
        }
        self._op_mode = "dim_chain_next"; self._op_pts = []
        self._lbl_op.configure(
            text=f"{prefix}-{base.dim_type} — Siguiente punto (ESC=fin):")
        self._highlight_op_btn(accion)
        self._last_cmd_name = accion
        self._update_prompt()
        self.canvas.focus_set()

    def _commit_dim_chain(self, p_click: tuple):
        """Crea una cota continua o base derivada de self._op_data['base']."""
        base  = self._op_data.get("base")
        modo  = self._op_data.get("modo", "continue")
        if base is None:
            self._finish_op()
            return

        dt = base.dim_type
        wx, wy = p_click

        if modo == "continue":
            # Mismo origen = p2 de la anterior; misma línea de cota
            p1 = base.p2
            p2 = (wx, wy)
            pos = self._proyectar_pos_misma_linea(base, p2)
        else:  # baseline
            # Mismo origen p1 que la base; pos desplazada perpendicular
            p1 = base.p1
            p2 = (wx, wy)
            pos = self._desplazar_pos_baseline(base, p2)

        # Tipo H/V: si el punto cae detrás del origen, igual la cota es válida
        # (la entidad Dimension toma el valor absoluto del componente).
        lyr = "A-COTA" if "A-COTA" in self.layers else self.active_layer
        from cad.entities import Dimension as _Dim
        nueva = _Dim(p1=p1, p2=p2, pos=pos, dim_type=dt,
                     style=base.style, layer=lyr)

        self._push_undo()
        self._add_entity(nueva)
        self._echo(f"COTA-{dt} ({'continua' if modo == 'continue' else 'base'}): {nueva.text()}")

        # Encadenar: la nueva es la nueva base
        self._op_data["base"] = nueva
        self._op_pts = []
        self._redraw_static()

    def _proyectar_pos_misma_linea(self, base, p2: tuple) -> tuple:
        """Calcula 'pos' para que la nueva cota quede sobre la misma línea de cota que `base`."""
        dt = base.dim_type
        bx1, by1 = base.p1; bx2, by2 = base.p2
        px,  py  = base.pos
        x2,  y2  = p2
        if dt == "H":
            mid_x = (base.p2[0] + x2) / 2.0
            return (mid_x, py)
        if dt == "V":
            mid_y = (base.p2[1] + y2) / 2.0
            return (px, mid_y)
        # Alineada (A): proyectar al offset perpendicular de la base
        import math as _m
        dx, dy = bx2 - bx1, by2 - by1
        L = _m.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L         # dirección
        nx, ny = -uy, ux                # normal
        # offset perpendicular de la base
        off = (px - bx1) * nx + (py - by1) * ny
        # punto medio del nuevo tramo (p2_base → p2_nuevo) sobre eje paralelo
        mx = (bx2 + x2) / 2.0
        my = (by2 + y2) / 2.0
        # proyectar (mx, my) sobre línea de cota = misma offset normal
        # tomar proyección sobre dirección u y sumar offset normal
        t = (mx - bx1) * ux + (my - by1) * uy
        bx_on = bx1 + ux * t
        by_on = by1 + uy * t
        return (bx_on + nx * off, by_on + ny * off)

    def _desplazar_pos_baseline(self, base, p2: tuple) -> tuple:
        """Calcula 'pos' para cota base: misma dirección, offset perpendicular adicional."""
        import math as _m
        dt = base.dim_type
        sp = self._dim_baseline_spacing()
        bx1, by1 = base.p1; bx2, by2 = base.p2
        px,  py  = base.pos
        x2,  y2  = p2

        if dt == "H":
            # Desplazar hacia el mismo lado donde está la línea de cota actual
            sign = 1.0 if py >= by1 else -1.0
            new_y = py + sign * sp
            mid_x = (bx1 + x2) / 2.0
            return (mid_x, new_y)
        if dt == "V":
            sign = 1.0 if px >= bx1 else -1.0
            new_x = px + sign * sp
            mid_y = (by1 + y2) / 2.0
            return (new_x, mid_y)
        # Alineada
        dx, dy = bx2 - bx1, by2 - by1
        L = _m.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        nx, ny = -uy, ux
        off = (px - bx1) * nx + (py - by1) * ny
        sign = 1.0 if off >= 0 else -1.0
        new_off = off + sign * sp
        mx = (bx1 + x2) / 2.0
        my = (by1 + y2) / 2.0
        t = (mx - bx1) * ux + (my - by1) * uy
        bx_on = bx1 + ux * t
        by_on = by1 + uy * t
        return (bx_on + nx * new_off, by_on + ny * new_off)

    # ── DSP — espaciado uniforme entre cotas paralelas ───────────────

    def _iniciar_dim_spacing(self):
        """Activa el modo DSP. Si ya hay selección, la usa como objetivos."""
        from cad.entities import Dimension as _Dim
        sel_dims = [e for e in self.entities
                    if isinstance(e, _Dim)
                    and e.dim_type in ("H", "V", "A")
                    and getattr(e, "selected", False)]

        # Si hay 2+ cotas seleccionadas, pedir espaciado directamente
        if len(sel_dims) >= 2:
            base = sel_dims[0]
            targets = sel_dims[1:]
            if any(d.dim_type != base.dim_type for d in targets):
                self._echo("!! DSP: todas las cotas deben ser del mismo tipo")
                return
            self._op_data = {"base": base, "targets": targets}
            self._pedir_dim_spacing_y_aplicar()
            return

        # Si no, modo interactivo: clic base + clic objetivos
        self._op_data = {"base": None, "targets": []}
        self._op_mode = "dim_sp_pick"; self._op_pts = []
        self._lbl_op.configure(
            text="DSP — Clic en la cota BASE (luego objetivos, Enter=aplicar):")
        self._highlight_op_btn("dim_sp")
        self._last_cmd_name = "dim_sp"
        self._update_prompt()
        self.canvas.focus_set()

    def _confirmar_dim_spacing(self):
        """Llamado al presionar Enter en modo dim_sp_pick."""
        base    = self._op_data.get("base")
        targets = self._op_data.get("targets", [])
        if base is None or not targets:
            self._echo("!! DSP: seleccione base + al menos 1 objetivo")
            return
        self._pedir_dim_spacing_y_aplicar()

    def _pedir_dim_spacing_y_aplicar(self):
        """Diálogo de espaciado (auto / valor) y aplicar."""
        import tkinter as _tk
        default = f"{self._dim_baseline_spacing():.2f}"
        from tkinter import simpledialog
        val = simpledialog.askstring(
            "DSP — Espaciado entre cotas",
            f"Distancia perpendicular entre cotas (m).\n"
            f"Vacío = auto ({default}):",
            parent=self.root)
        if val is None:
            self._finish_op(); return
        val = val.strip()
        if not val:
            sp = self._dim_baseline_spacing()
        else:
            try:
                sp = float(val.replace(",", "."))
                if sp <= 0:
                    raise ValueError
            except ValueError:
                self._echo("!! DSP: valor inválido")
                self._finish_op(); return

        self._aplicar_dim_spacing(sp)
        self._finish_op()

    def _aplicar_dim_spacing(self, spacing: float):
        """Reposiciona el `pos` de cada cota objetivo paralela a la base, a múltiplos del espaciado."""
        import math as _m
        base    = self._op_data.get("base")
        targets = list(self._op_data.get("targets", []))
        if base is None or not targets:
            return
        dt = base.dim_type

        # Calcular offset perpendicular base→cota para ordenar
        def _offset_perp(d):
            bx1, by1 = base.p1; bx2, by2 = base.p2
            px,  py  = d.pos
            if dt == "H":
                return py - by1
            if dt == "V":
                return px - bx1
            # A
            dx, dy = bx2 - bx1, by2 - by1
            L = _m.hypot(dx, dy) or 1.0
            nx, ny = -dy/L, dx/L
            return (px - bx1) * nx + (py - by1) * ny

        base_off = _offset_perp(base)
        # Signo de cada objetivo respecto a la base
        with_sign = [(d, _offset_perp(d) - base_off) for d in targets]
        # Ordenar por |offset relativo| ascendente para que el más cercano sea el #1
        with_sign.sort(key=lambda t: abs(t[1]))

        # Reasignar offset = base_off + sign * spacing * i
        self._push_undo()
        idx_entities = {id(e): i for i, e in enumerate(self.entities)}
        for i, (d, rel) in enumerate(with_sign, start=1):
            sign = 1.0 if rel >= 0 else -1.0
            new_off = base_off + sign * spacing * i
            ent_idx = idx_entities.get(id(d))
            if ent_idx is None:
                continue
            nueva_pos = self._pos_con_offset(base, d, new_off)
            d2 = Dimension(p1=d.p1, p2=d.p2, pos=nueva_pos,
                           dim_type=d.dim_type,
                           text_override=d.text_override,
                           style=d.style, layer=d.layer)
            self.entities[ent_idx] = d2

        self._echo(f"DSP: {len(with_sign)} cota(s) espaciada(s) a {spacing:.2f}")
        self._rebuild_snap_index(); self._redraw()

    def _pos_con_offset(self, base, dim_obj, new_offset: float) -> tuple:
        """Devuelve el nuevo `pos` para `dim_obj` colocado a `new_offset` perpendicular respecto a `base`."""
        import math as _m
        dt = base.dim_type
        bx1, by1 = base.p1; bx2, by2 = base.p2
        if dt == "H":
            mid_x = (dim_obj.p1[0] + dim_obj.p2[0]) / 2.0
            return (mid_x, by1 + new_offset)
        if dt == "V":
            mid_y = (dim_obj.p1[1] + dim_obj.p2[1]) / 2.0
            return (bx1 + new_offset, mid_y)
        # Alineada
        dx, dy = bx2 - bx1, by2 - by1
        L = _m.hypot(dx, dy) or 1.0
        ux, uy = dx/L, dy/L
        nx, ny = -uy, ux
        mx = (dim_obj.p1[0] + dim_obj.p2[0]) / 2.0
        my = (dim_obj.p1[1] + dim_obj.p2[1]) / 2.0
        t = (mx - bx1) * ux + (my - by1) * uy
        bx_on = bx1 + ux * t
        by_on = by1 + uy * t
        return (bx_on + nx * new_offset, by_on + ny * new_offset)

    def _ejecutar_hatch_dialog(self):
        """
        Muestra un pequeño diálogo para elegir patrón, ángulo y escala del hatch,
        luego entra en el modo hatch_pts para que el usuario haga clic en los puntos.
        """
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Hatch / Relleno")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.attributes("-topmost", True)

        PATTERNS = ["SOLID", "ANSI31", "LINES", "CROSS"]
        pat_var   = tk.StringVar(value="ANSI31")
        angle_var = tk.StringVar(value="45")
        scale_var = tk.StringVar(value="0.5")

        pad = dict(padx=10, pady=4)

        ctk.CTkLabel(dlg, text="Patrón:").grid(row=0, column=0, sticky="w", **pad)
        ctk.CTkOptionMenu(dlg, values=PATTERNS, variable=pat_var,
                          width=130).grid(row=0, column=1, **pad)

        ctk.CTkLabel(dlg, text="Ángulo (°):").grid(row=1, column=0, sticky="w", **pad)
        ctk.CTkEntry(dlg, textvariable=angle_var, width=80).grid(row=1, column=1, sticky="w", **pad)

        ctk.CTkLabel(dlg, text="Escala:").grid(row=2, column=0, sticky="w", **pad)
        ctk.CTkEntry(dlg, textvariable=scale_var, width=80).grid(row=2, column=1, sticky="w", **pad)

        def _aceptar():
            try:
                ang = float(angle_var.get())
            except ValueError:
                ang = 45.0
            try:
                sc = max(0.001, float(scale_var.get()))
            except ValueError:
                sc = 0.5
            pat = pat_var.get()
            dlg.destroy()
            # Iniciar modo de captura de puntos
            self._op_mode = "hatch_pts"
            self._op_pts  = []
            self._op_sel  = []
            self._op_data = {
                "hatch_pattern": pat,
                "hatch_angle":   ang,
                "hatch_scale":   sc,
            }
            self._lbl_op.configure(
                text=f"HATCH {pat} — Clic en vértices del borde (Enter=cerrar | ESC=cancelar):")
            self._highlight_op_btn("hatch")
            self._last_cmd_name = "hatch"
            self._update_prompt()
            self.canvas.focus_set()

        def _cancelar_dlg():
            dlg.destroy()

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.grid(row=3, column=0, columnspan=2, pady=8)
        ctk.CTkButton(btn_row, text="✓ Aceptar", width=100,
                      command=_aceptar).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="✕ Cancelar", width=100, fg_color=UI_ERR,
                      command=_cancelar_dlg).pack(side="left", padx=4)

        dlg.bind("<Return>",   lambda _: _aceptar())
        dlg.bind("<Escape>",   lambda _: _cancelar_dlg())

        # Centrar sobre el visor
        self.root.update_idletasks()
        rx = self.root.winfo_x() + self.root.winfo_width()  // 2 - 160
        ry = self.root.winfo_y() + self.root.winfo_height() // 2 - 100
        dlg.geometry(f"320x175+{rx}+{ry}")

    def _commit_hatch(self):
        """Crea un Hatch con los puntos acumulados en _op_pts."""
        pts = self._op_pts
        if len(pts) < 3:
            self._echo("!! HATCH: se necesitan al menos 3 puntos"); return
        pattern = self._op_data.get("hatch_pattern", "ANSI31")
        angle   = self._op_data.get("hatch_angle",   45.0)
        scale   = self._op_data.get("hatch_scale",    0.5)
        lyr     = self.active_layer
        h = Hatch(boundary=list(pts), pattern=pattern,
                  angle=angle, scale=scale, layer=lyr)
        self._push_undo()
        self._add_entity(h)
        self._echo(f"HATCH {pattern} — {len(pts)} vértices en capa {lyr}")
        self._op_mode = ""; self._op_pts = []; self._op_data = {}
        self._lbl_op.configure(text="")
        self._highlight_op_btn(None)
        self._rebuild_snap_index(); self._redraw()

    # ─── Pick entity en posición mundo ───────────────────────────
    def _pick_entity_at(self, wx, wy, types=None):
        """(index, entity) del objeto más cercano o None."""
        HIT = 12 / self.scale
        best_i = best_e = None; best_d = HIT
        for i, e in enumerate(self.entities):
            if types and not isinstance(e, types):
                continue
            lyr = self.layers.get(e.layer)
            if lyr and lyr.locked:
                continue
            d = self._dist_entity(wx, wy, e)
            if d < best_d:
                best_d = d; best_i = i; best_e = e
        return (best_i, best_e) if best_i is not None else None

    # ─── TRIM / EXTEND / FILLET helpers ───────────────────────────

    def _all_segs(self, exclude_idx: int) -> list:
        """Todos los segmentos de entidades excepto exclude_idx.
        Incluye XLine convertidas a segmentos muy largos (bordes de corte infinitos)."""
        segs = []
        for i, e in enumerate(self.entities):
            if i == exclude_idx:
                continue
            if isinstance(e, Line):
                segs.append((e.x1, e.y1, e.x2, e.y2))
            elif isinstance(e, XLine):
                _dx = e.x2 - e.x1; _dy = e.y2 - e.y1
                _d  = math.hypot(_dx, _dy)
                if _d < 1e-10: continue
                _ux, _uy = _dx / _d, _dy / _d
                _BIG = 1e7
                segs.append((e.x1 - _ux*_BIG, e.y1 - _uy*_BIG,
                              e.x1 + _ux*_BIG, e.y1 + _uy*_BIG))
            elif isinstance(e, Arc):
                # Arco como borde de corte: aproximar con segmentos
                _span = (e.end_ang - e.start_ang) % 360
                if _span < 1e-6: _span = 360
                _n = max(32, int(_span / 3))
                _sa = math.radians(e.start_ang); _ea = _sa + math.radians(_span)
                _angs = [_sa + (_ea - _sa)*k/_n for k in range(_n + 1)]
                _pa = [(e.cx + e.radius*math.cos(a), e.cy + e.radius*math.sin(a)) for a in _angs]
                for k in range(len(_pa) - 1):
                    segs.append((_pa[k][0], _pa[k][1], _pa[k+1][0], _pa[k+1][1]))
            elif isinstance(e, Polyline) and len(e.points) >= 2:
                pts = e.points
                for k in range(len(pts) - 1):
                    segs.append((pts[k][0], pts[k][1], pts[k+1][0], pts[k+1][1]))
                if e.closed and len(pts) > 2:
                    segs.append((pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]))
        return segs

    def _all_circ_edges(self, exclude_idx: int) -> list:
        """Retorna lista de (cx,cy,r,entity) de Circle/Arc como bordes de corte."""
        edges = []
        for i, e in enumerate(self.entities):
            if i == exclude_idx: continue
            if isinstance(e, (Circle, Arc)):
                edges.append((e.cx, e.cy, e.radius, e))
        return edges

    def _quick_trim_line(self, idx: int, line: "Line", wx: float, wy: float):
        """QuickTrim: recorta una Line usando TODOS los demás como bordes."""
        dx, dy = line.x2 - line.x1, line.y2 - line.y1
        L2 = dx*dx + dy*dy
        if L2 < 1e-16:
            return
        t_click = ((wx - line.x1)*dx + (wy - line.y1)*dy) / L2
        # Recolectar todas las intersecciones internas (0 < t < 1)
        t_hits = []
        for sx1, sy1, sx2, sy2 in self._all_segs(idx):
            res = _seg_intersect(line.x1, line.y1, line.x2, line.y2,
                                 sx1, sy1, sx2, sy2,
                                 t_range=(1e-6, 1-1e-6), u_range=(-1e9, 1e9))
            if res:
                t_hits.append(round(res[2], 8))
        # Bordes de corte Circle/Arc (intersección exacta)
        for cx, cy, r, ce in self._all_circ_edges(idx):
            for px, py in _intersect_seg_circle(line.x1, line.y1, line.x2, line.y2, cx, cy, r):
                if isinstance(ce, Arc) and not _pt_on_arc((px, py), ce):
                    continue
                t = ((px - line.x1)*dx + (py - line.y1)*dy) / L2
                if 1e-6 < t < 1-1e-6:
                    t_hits.append(round(t, 8))
        if not t_hits:
            self._echo("TR: sin intersecciones sobre esta línea"); return
        t_hits = sorted(set(t_hits))
        left  = max((t for t in t_hits if t < t_click), default=None)
        right = min((t for t in t_hits if t > t_click), default=None)
        def pt(t): return (line.x1 + dx*t, line.y1 + dy*t)
        self._push_undo()
        if left is None and right is None:
            return
        elif left is None:
            rx, ry = pt(right)
            self.entities[idx] = Line(x1=rx, y1=ry, x2=line.x2, y2=line.y2,
                                      layer=line.layer, color=line.color)
        elif right is None:
            lx, ly = pt(left)
            self.entities[idx] = Line(x1=line.x1, y1=line.y1, x2=lx, y2=ly,
                                      layer=line.layer, color=line.color)
        else:
            # Partir: conservar ambos extremos, eliminar segmento medio
            lx, ly = pt(left); rx, ry = pt(right)
            self.entities[idx] = Line(x1=line.x1, y1=line.y1, x2=lx, y2=ly,
                                      layer=line.layer, color=line.color)
            self.entities.append(Line(x1=rx, y1=ry, x2=line.x2, y2=line.y2,
                                      layer=line.layer, color=line.color))
        self._rebuild_snap_index(); self._redraw()

    def _quick_trim_poly(self, idx: int, poly: "Polyline", wx: float, wy: float):
        """QuickTrim en el segmento de Polyline más cercano al clic."""
        pts = poly.points
        if len(pts) < 2:
            return
        best_si = 0; best_d = 1e18
        for si in range(len(pts) - 1):
            d = _dist_pt_seg(wx, wy, pts[si][0], pts[si][1],
                             pts[si+1][0], pts[si+1][1])
            if d < best_d:
                best_d = d; best_si = si
        ax, ay = pts[best_si]; bx, by = pts[best_si+1]
        dx, dy = bx-ax, by-ay
        L2 = dx*dx + dy*dy
        if L2 < 1e-16:
            return
        t_click = ((wx-ax)*dx + (wy-ay)*dy) / L2
        t_hits = []
        for sx1, sy1, sx2, sy2 in self._all_segs(idx):
            res = _seg_intersect(ax, ay, bx, by, sx1, sy1, sx2, sy2,
                                 t_range=(1e-6, 1-1e-6), u_range=(-1e9, 1e9))
            if res:
                t_hits.append(round(res[2], 8))
        # Bordes de corte Circle/Arc (intersección exacta)
        for cx, cy, r, ce in self._all_circ_edges(idx):
            for px, py in _intersect_seg_circle(ax, ay, bx, by, cx, cy, r):
                if isinstance(ce, Arc) and not _pt_on_arc((px, py), ce):
                    continue
                t = ((px - ax)*dx + (py - ay)*dy) / L2
                if 1e-6 < t < 1-1e-6:
                    t_hits.append(round(t, 8))
        if not t_hits:
            self._echo("TR: sin intersecciones en este segmento"); return
        t_hits = sorted(set(t_hits))
        left  = max((t for t in t_hits if t < t_click), default=None)
        right = min((t for t in t_hits if t > t_click), default=None)
        def seg_pt(t): return (ax + dx*t, ay + dy*t)
        self._push_undo()
        new_pts = list(pts)
        if left is None and right is not None:
            new_pts[best_si] = seg_pt(right)
            self.entities[idx] = Polyline(points=new_pts, closed=False,
                                          layer=poly.layer, color=poly.color)
        elif right is None and left is not None:
            new_pts[best_si+1] = seg_pt(left)
            self.entities[idx] = Polyline(points=new_pts, closed=False,
                                          layer=poly.layer, color=poly.color)
        elif left is not None and right is not None:
            lx, ly = seg_pt(left); rx, ry = seg_pt(right)
            new_pts[best_si+1] = (lx, ly)
            self.entities[idx] = Polyline(points=new_pts, closed=False,
                                          layer=poly.layer, color=poly.color)
            tail = [(rx, ry)] + list(pts[best_si+1:])
            if len(tail) >= 2:
                self.entities.append(Polyline(points=tail, layer=poly.layer,
                                              color=poly.color))
        self._rebuild_snap_index(); self._redraw()

    # ── Helpers para TRIM de Circle/Arc ──────────────────────────────────────

    def _circle_intersection_angles(self, idx: int, cx: float, cy: float, r: float,
                                    arc_entity=None) -> list:
        """Ángulos (grados, 0-360) donde otras entidades intersectan el círculo/arco."""
        ang_hits = set()
        for sx1, sy1, sx2, sy2 in self._all_segs(idx):
            for px, py in _intersect_seg_circle(sx1, sy1, sx2, sy2, cx, cy, r):
                ang = math.degrees(math.atan2(py - cy, px - cx)) % 360
                ang_hits.add(round(ang, 6))
        return sorted(ang_hits)

    def _quick_trim_circle(self, idx: int, circ: "Circle", wx: float, wy: float):
        """QuickTrim: convierte el Círculo en Arco eliminando el segmento clicado.
        Requiere al menos 2 bordes de corte que crucen el círculo."""
        ang_hits = self._circle_intersection_angles(idx, circ.cx, circ.cy, circ.radius)
        if len(ang_hits) < 2:
            self._echo("TR: se necesitan al menos 2 bordes de corte en el círculo"); return

        # Ángulo del punto clicado proyectado sobre la circunferencia
        click_ang = math.degrees(math.atan2(wy - circ.cy, wx - circ.cx)) % 360

        # Encontrar qué arco CCW entre intersecciones consecutivas contiene el clic
        n = len(ang_hits)
        cut_sa = ang_hits[-1]; cut_ea = ang_hits[0]   # gap que cruza 0°
        for k in range(n):
            a0 = ang_hits[k]
            a1 = ang_hits[(k + 1) % n]
            # Span CCW de a0 a a1
            span = (a1 - a0) % 360
            click_in = (click_ang - a0) % 360
            if 0 < click_in < span:
                cut_sa = a0; cut_ea = a1; break

        # El arco CONSERVADO va de cut_ea a cut_sa (CCW = el resto del círculo)
        self._push_undo()
        self.entities[idx] = Arc(
            cx=circ.cx, cy=circ.cy, radius=circ.radius,
            start_ang=cut_ea, end_ang=cut_sa,
            ccw=True, layer=circ.layer, color=circ.color)
        self._rebuild_snap_index(); self._redraw()

    def _quick_trim_arc(self, idx: int, arc: "Arc", wx: float, wy: float):
        """QuickTrim: recorta un Arco usando todas las demás entidades como bordes."""
        ang_hits = self._circle_intersection_angles(idx, arc.cx, arc.cy, arc.radius)
        if not ang_hits:
            self._echo("TR: sin intersecciones sobre este arco"); return

        # Normalizar span del arco (CCW)
        sa  = arc.start_ang % 360
        ea  = arc.end_ang   % 360
        span = (ea - sa) % 360
        if span < 1e-6: span = 360

        def ang_to_t(ang):
            """Posición paramétrica t ∈ [0,1] dentro del span del arco."""
            return ((ang - sa) % 360) / span

        click_ang = math.degrees(math.atan2(wy - arc.cy, wx - arc.cx)) % 360
        t_click   = ang_to_t(click_ang)

        # Solo los hits dentro del arco (0 < t < 1)
        t_hits = sorted({round(ang_to_t(a), 8) for a in ang_hits
                         if 1e-6 < ang_to_t(a) < 1 - 1e-6})
        if not t_hits:
            self._echo("TR: sin intersecciones dentro del arco"); return

        left  = max((t for t in t_hits if t < t_click), default=None)
        right = min((t for t in t_hits if t > t_click), default=None)

        def t_to_ang(t):
            return (sa + t * span) % 360

        self._push_undo()
        if left is None and right is None:
            return
        elif left is None:
            self.entities[idx] = Arc(cx=arc.cx, cy=arc.cy, radius=arc.radius,
                                     start_ang=t_to_ang(right), end_ang=arc.end_ang,
                                     ccw=arc.ccw, layer=arc.layer, color=arc.color)
        elif right is None:
            self.entities[idx] = Arc(cx=arc.cx, cy=arc.cy, radius=arc.radius,
                                     start_ang=arc.start_ang, end_ang=t_to_ang(left),
                                     ccw=arc.ccw, layer=arc.layer, color=arc.color)
        else:
            ang_l = t_to_ang(left); ang_r = t_to_ang(right)
            self.entities[idx] = Arc(cx=arc.cx, cy=arc.cy, radius=arc.radius,
                                     start_ang=arc.start_ang, end_ang=ang_l,
                                     ccw=arc.ccw, layer=arc.layer, color=arc.color)
            self.entities.append(Arc(cx=arc.cx, cy=arc.cy, radius=arc.radius,
                                     start_ang=ang_r, end_ang=arc.end_ang,
                                     ccw=arc.ccw, layer=arc.layer, color=arc.color))
        self._rebuild_snap_index(); self._redraw()

    def _quick_extend_line(self, idx: int, line: "Line", wx: float, wy: float):
        """QuickExtend: extiende el extremo más cercano al clic hacia el límite más próximo."""
        d_s = math.hypot(wx - line.x1, wy - line.y1)
        d_e = math.hypot(wx - line.x2, wy - line.y2)
        ext_start = d_s < d_e
        best_ix = best_iy = None; best_dist = 1e18
        for sx1, sy1, sx2, sy2 in self._all_segs(idx):
            res = _seg_intersect(line.x1, line.y1, line.x2, line.y2,
                                 sx1, sy1, sx2, sy2,
                                 t_range=(-1e9, 1e9), u_range=(-1e9, 1e9))
            if res:
                ix, iy, t, _ = res
                if ext_start and t < -1e-6:
                    d = math.hypot(ix - line.x1, iy - line.y1)
                    if d < best_dist:
                        best_dist = d; best_ix = ix; best_iy = iy
                elif not ext_start and t > 1+1e-6:
                    d = math.hypot(ix - line.x2, iy - line.y2)
                    if d < best_dist:
                        best_dist = d; best_ix = ix; best_iy = iy
        if best_ix is None:
            self._echo("EX: no se encontró límite para extender"); return
        self._push_undo()
        if ext_start:
            self.entities[idx] = Line(x1=best_ix, y1=best_iy,
                                      x2=line.x2, y2=line.y2,
                                      layer=line.layer, color=line.color)
        else:
            self.entities[idx] = Line(x1=line.x1, y1=line.y1,
                                      x2=best_ix, y2=best_iy,
                                      layer=line.layer, color=line.color)
        self._rebuild_snap_index(); self._redraw()

    def _do_fillet(self, idx1: int, line1: "Line",
                   idx2: int, line2: "Line"):
        """Ejecuta FILLET entre dos líneas con self.fillet_radius."""
        r = self.fillet_radius
        res = _seg_intersect(line1.x1, line1.y1, line1.x2, line1.y2,
                             line2.x1, line2.y1, line2.x2, line2.y2,
                             t_range=(-1e9, 1e9), u_range=(-1e9, 1e9))
        if not res:
            self._echo("FILLET: las líneas son paralelas"); return
        ix, iy, _, _ = res
        # Extremo más cercano a la intersección en cada línea
        d1s = math.hypot(line1.x1-ix, line1.y1-iy)
        d1e = math.hypot(line1.x2-ix, line1.y2-iy)
        d2s = math.hypot(line2.x1-ix, line2.y1-iy)
        d2e = math.hypot(line2.x2-ix, line2.y2-iy)

        if r < 1e-6:
            # R=0: mover el extremo más cercano de cada línea a (ix, iy)
            self._push_undo()
            if d1s <= d1e:
                self.entities[idx1] = Line(x1=ix, y1=iy,
                                           x2=line1.x2, y2=line1.y2,
                                           layer=line1.layer, color=line1.color)
            else:
                self.entities[idx1] = Line(x1=line1.x1, y1=line1.y1,
                                           x2=ix, y2=iy,
                                           layer=line1.layer, color=line1.color)
            if d2s <= d2e:
                self.entities[idx2] = Line(x1=ix, y1=iy,
                                           x2=line2.x2, y2=line2.y2,
                                           layer=line2.layer, color=line2.color)
            else:
                self.entities[idx2] = Line(x1=line2.x1, y1=line2.y1,
                                           x2=ix, y2=iy,
                                           layer=line2.layer, color=line2.color)
            self._rebuild_snap_index(); self._redraw()
            self._echo(f"FILLET R=0 — esquina en ({ix:.3f}, {iy:.3f})")
            return

        # R>0: calcular arco de empalme
        # Vectores unitarios que SALEN de la intersección hacia los extremos lejanos
        fx1 = line1.x1 if d1s > d1e else line1.x2
        fy1 = line1.y1 if d1s > d1e else line1.y2
        fx2 = line2.x1 if d2s > d2e else line2.x2
        fy2 = line2.y1 if d2s > d2e else line2.y2
        len1 = math.hypot(fx1-ix, fy1-iy)
        len2 = math.hypot(fx2-ix, fy2-iy)
        if len1 < 1e-10 or len2 < 1e-10:
            return
        ux1, uy1 = (fx1-ix)/len1, (fy1-iy)/len1
        ux2, uy2 = (fx2-ix)/len2, (fy2-iy)/len2
        # Bisectriz
        bx, by = ux1+ux2, uy1+uy2
        b_len = math.hypot(bx, by)
        if b_len < 1e-10:
            self._echo("FILLET: ángulo de 180° no soportado"); return
        bx /= b_len; by /= b_len
        cos_a = max(-1.0, min(1.0, ux1*ux2 + uy1*uy2))
        sin_half = math.sqrt((1.0 - cos_a) / 2.0)
        if sin_half < 1e-10:
            self._echo("FILLET: ángulo demasiado pequeño"); return
        cos_half = math.sqrt((1.0 + cos_a) / 2.0)
        tan_dist = r * cos_half / sin_half
        dist_cen = r / sin_half
        # Centro del arco y puntos tangentes
        cx_arc = ix + bx * dist_cen
        cy_arc = iy + by * dist_cen
        tx1 = ix + ux1 * tan_dist; ty1 = iy + uy1 * tan_dist
        tx2 = ix + ux2 * tan_dist; ty2 = iy + uy2 * tan_dist
        ang1 = math.degrees(math.atan2(ty1-cy_arc, tx1-cx_arc)) % 360
        ang2 = math.degrees(math.atan2(ty2-cy_arc, tx2-cx_arc)) % 360
        ccw  = (ux1*uy2 - uy1*ux2) > 0
        self._push_undo()
        # Recortar/extender cada línea al punto tangente
        if d1s <= d1e:
            self.entities[idx1] = Line(x1=tx1, y1=ty1, x2=line1.x2, y2=line1.y2,
                                       layer=line1.layer, color=line1.color)
        else:
            self.entities[idx1] = Line(x1=line1.x1, y1=line1.y1, x2=tx1, y2=ty1,
                                       layer=line1.layer, color=line1.color)
        if d2s <= d2e:
            self.entities[idx2] = Line(x1=tx2, y1=ty2, x2=line2.x2, y2=line2.y2,
                                       layer=line2.layer, color=line2.color)
        else:
            self.entities[idx2] = Line(x1=line2.x1, y1=line2.y1, x2=tx2, y2=ty2,
                                       layer=line2.layer, color=line2.color)
        s_ang = ang1 if ccw else ang2
        e_ang = ang2 if ccw else ang1
        self.entities.append(Arc(cx=cx_arc, cy=cy_arc, radius=r,
                                 start_ang=s_ang, end_ang=e_ang,
                                 ccw=ccw, layer=line1.layer))
        self._rebuild_snap_index(); self._redraw()
        self._echo(f"FILLET R={r:.3f} — arco insertado")

    # ─── CHAMFER ─────────────────────────────────────────────────
    def _do_chamfer(self, idx1: int, line1: "Line",
                    idx2: int, line2: "Line"):
        """Ejecuta CHAMFER entre dos líneas con self.chamfer_d1/d2."""
        d1 = self.chamfer_d1
        d2 = self.chamfer_d2
        res = _seg_intersect(line1.x1, line1.y1, line1.x2, line1.y2,
                             line2.x1, line2.y1, line2.x2, line2.y2,
                             t_range=(-1e9, 1e9), u_range=(-1e9, 1e9))
        if not res:
            self._echo("CHAMFER: las líneas son paralelas"); return
        ix, iy, _, _ = res
        # Distances from intersection to each endpoint
        d1s = math.hypot(line1.x1-ix, line1.y1-iy)
        d1e = math.hypot(line1.x2-ix, line1.y2-iy)
        d2s = math.hypot(line2.x1-ix, line2.y1-iy)
        d2e = math.hypot(line2.x2-ix, line2.y2-iy)
        # Vectors pointing away from intersection
        fx1 = line1.x1 if d1s > d1e else line1.x2
        fy1 = line1.y1 if d1s > d1e else line1.y2
        fx2 = line2.x1 if d2s > d2e else line2.x2
        fy2 = line2.y1 if d2s > d2e else line2.y2
        len1 = math.hypot(fx1-ix, fy1-iy)
        len2 = math.hypot(fx2-ix, fy2-iy)
        if len1 < 1e-10 or len2 < 1e-10:
            return
        ux1, uy1 = (fx1-ix)/len1, (fy1-iy)/len1
        ux2, uy2 = (fx2-ix)/len2, (fy2-iy)/len2
        tx1 = ix + ux1 * d1;  ty1 = iy + uy1 * d1
        tx2 = ix + ux2 * d2;  ty2 = iy + uy2 * d2
        self._push_undo()
        if d1s <= d1e:
            self.entities[idx1] = Line(x1=tx1, y1=ty1, x2=line1.x2, y2=line1.y2,
                                       layer=line1.layer, color=line1.color)
        else:
            self.entities[idx1] = Line(x1=line1.x1, y1=line1.y1, x2=tx1, y2=ty1,
                                       layer=line1.layer, color=line1.color)
        if d2s <= d2e:
            self.entities[idx2] = Line(x1=tx2, y1=ty2, x2=line2.x2, y2=line2.y2,
                                       layer=line2.layer, color=line2.color)
        else:
            self.entities[idx2] = Line(x1=line2.x1, y1=line2.y1, x2=tx2, y2=ty2,
                                       layer=line2.layer, color=line2.color)
        self.entities.append(Line(x1=tx1, y1=ty1, x2=tx2, y2=ty2,
                                  layer=line1.layer))
        self._rebuild_snap_index(); self._redraw()
        self._echo(f"CHAMFER D={d1:.3f}/{d2:.3f} — chaflán insertado")

    # ─── BREAK ───────────────────────────────────────────────────
    def _do_break_two(self, idx: int, ent, p1: tuple, p2: tuple):
        """Parte una entidad entre dos puntos — elimina el segmento entre p1 y p2."""
        ax, ay = p1; bx, by = p2
        # Si los dos puntos son prácticamente iguales → break at point
        if math.hypot(bx - ax, by - ay) < 1e-6:
            self._do_break(idx, ent, ax, ay)
            return
        if isinstance(ent, Line):
            lx1, ly1, lx2, ly2 = ent.x1, ent.y1, ent.x2, ent.y2
            L = math.hypot(lx2 - lx1, ly2 - ly1)
            if L < 1e-9:
                return
            def _t(px, py):
                return ((px - lx1) * (lx2 - lx1) + (py - ly1) * (ly2 - ly1)) / (L * L)
            t1 = max(0.0, min(1.0, _t(ax, ay)))
            t2 = max(0.0, min(1.0, _t(bx, by)))
            if t1 > t2:
                t1, t2 = t2, t1
            segs = []
            if t1 > 1e-6:
                segs.append(Line(x1=lx1, y1=ly1,
                                 x2=lx1 + t1*(lx2-lx1), y2=ly1 + t1*(ly2-ly1),
                                 layer=ent.layer))
            if t2 < 1.0 - 1e-6:
                segs.append(Line(x1=lx1 + t2*(lx2-lx1), y1=ly1 + t2*(ly2-ly1),
                                 x2=lx2, y2=ly2,
                                 layer=ent.layer))
            self._push_undo_diff(
                removed=[(idx, ent)],
                added_count=len(segs))
            del self.entities[idx]
            self.entities.extend(segs)
            self._rebuild_snap_index(); self._redraw()
            self._echo(f"BREAK  2 puntos — {len(segs)} segmentos resultantes")
        else:
            # Fallback: break at p1 for unsupported types
            self._do_break(idx, ent, ax, ay)

    def _do_break(self, idx: int, ent, wx: float, wy: float):
        """Parte una entidad en el punto clickeado."""
        if isinstance(ent, Circle):
            self._echo("BREAK: no aplica a círculos"); return
        self._push_undo()
        if isinstance(ent, Line):
            # Find nearest point on line
            dx = ent.x2 - ent.x1;  dy = ent.y2 - ent.y1
            d = dx*dx + dy*dy
            if d < 1e-12:
                return
            t = max(0.0, min(1.0, ((wx-ent.x1)*dx + (wy-ent.y1)*dy) / d))
            bpx = ent.x1 + t*dx;  bpy = ent.y1 + t*dy
            new_l1 = Line(x1=ent.x1, y1=ent.y1, x2=bpx, y2=bpy,
                          layer=ent.layer, color=ent.color)
            new_l2 = Line(x1=bpx, y1=bpy, x2=ent.x2, y2=ent.y2,
                          layer=ent.layer, color=ent.color)
            self.entities[idx] = new_l1
            self.entities.insert(idx+1, new_l2)
        elif isinstance(ent, Polyline):
            # Find nearest vertex
            if len(ent.points) < 2:
                return
            nearest = min(range(len(ent.points)),
                          key=lambda i: math.hypot(ent.points[i][0]-wx,
                                                   ent.points[i][1]-wy))
            if nearest == 0 or nearest == len(ent.points) - 1:
                self._echo("BREAK: no se puede partir en extremo"); return
            pts1 = ent.points[:nearest+1]
            pts2 = ent.points[nearest:]
            self.entities[idx] = Polyline(points=pts1, closed=False, layer=ent.layer)
            self.entities.insert(idx+1, Polyline(points=pts2, closed=False, layer=ent.layer))
        self._rebuild_snap_index(); self._redraw()
        self._echo("BREAK — entidad partida")

    # ─── ALIGN ───────────────────────────────────────────────────
    def _do_align(self):
        """Alinea entidades seleccionadas usando 2 pares de puntos.
        Solo aplica traslación + rotación (sin escalado), igual que AutoCAD por defecto.
        """
        sel  = self._op_sel
        sp1  = self._op_data.get("sp1")
        sp2  = self._op_data.get("sp2")
        dp1  = self._op_data.get("dp1")
        dp2  = self._op_data.get("dp2")
        if not (sp1 and sp2 and dp1 and dp2 and sel):
            self._echo("ALIGN: datos insuficientes"); return
        # Translation: move so sp1 → dp1
        tx = dp1[0] - sp1[0]
        ty = dp1[1] - sp1[1]
        # Rotation: align direction (sp2-sp1) → (dp2-dp1)
        src_ang = math.atan2(sp2[1]-sp1[1], sp2[0]-sp1[0])
        dst_ang = math.atan2(dp2[1]-dp1[1], dp2[0]-dp1[0])
        rot_deg = math.degrees(dst_ang - src_ang)
        # Pivot = dp1 (= translated sp1)
        px, py = dp1
        self._push_undo()
        for i, e in enumerate(self.entities):
            if e not in sel:
                continue
            e2 = e.translated(tx, ty)
            if abs(rot_deg) > 1e-6:
                e2 = e2.rotated(px, py, rot_deg)
            self.entities[i] = e2
        self._rebuild_snap_index(); self._redraw()
        self._echo(f"ALIGN — traslado=({tx:.3f},{ty:.3f})  rot={rot_deg:.2f}°")

    # ─── ARRAY DIALOG ────────────────────────────────────────────
    def _array_dialog(self, sel: list, default_tab: str = "Rectangular"):
        """Diálogo modal para array rectangular o polar."""
        import tkinter as _tk
        top = ctk.CTkToplevel(self)
        top.title("ARRAY — Arreglo")
        top.geometry("340x440")
        top.resizable(False, False)
        top.grab_set()

        tabs = ctk.CTkTabview(top, width=320, height=360,
                              fg_color=UI_CARD,
                              segmented_button_fg_color=UI_BORD,
                              segmented_button_selected_color=UI_ACC)
        tabs.pack(padx=10, pady=10)
        tabs.add("Rectangular")
        tabs.add("Polar")
        tabs.set(default_tab)

        # ── Rectangular tab ───────────────────────────────────────
        tr = tabs.tab("Rectangular")
        _flds_r = {}
        for lbl, key, defv in [
            ("Filas:", "rows", "3"),
            ("Columnas:", "cols", "3"),
            ("Espaciado fila (m):", "row_sp", "1.0"),
            ("Espaciado col (m):", "col_sp", "1.0"),
        ]:
            ctk.CTkLabel(tr, text=lbl, anchor="w").pack(fill="x", padx=8, pady=(6,0))
            e = ctk.CTkEntry(tr); e.insert(0, defv); e.pack(fill="x", padx=8)
            _flds_r[key] = e

        # ── Polar tab ─────────────────────────────────────────────
        tp = tabs.tab("Polar")
        _flds_p = {}
        for lbl, key, defv in [
            ("Cantidad (total copias):", "count", "6"),
            ("Ángulo total (°):", "angle", "360"),
        ]:
            ctk.CTkLabel(tp, text=lbl, anchor="w").pack(fill="x", padx=8, pady=(6,0))
            e = ctk.CTkEntry(tp); e.insert(0, defv); e.pack(fill="x", padx=8)
            _flds_p[key] = e

        ctk.CTkLabel(tp, text="Centro X (m):", anchor="w").pack(fill="x", padx=8, pady=(6,0))
        e_cx = ctk.CTkEntry(tp); e_cx.insert(0, "0.0"); e_cx.pack(fill="x", padx=8)
        ctk.CTkLabel(tp, text="Centro Y (m):", anchor="w").pack(fill="x", padx=8, pady=(6,0))
        e_cy = ctk.CTkEntry(tp); e_cy.insert(0, "0.0"); e_cy.pack(fill="x", padx=8)

        def _confirm():
            tab = tabs.get()
            try:
                if tab == "Rectangular":
                    rows    = max(1, int(_flds_r["rows"].get()))
                    cols    = max(1, int(_flds_r["cols"].get()))
                    row_sp  = float(_flds_r["row_sp"].get())
                    col_sp  = float(_flds_r["col_sp"].get())
                    self._push_undo()
                    new_ents = []
                    for i in range(rows):
                        for j in range(cols):
                            if i == 0 and j == 0:
                                continue
                            for e in sel:
                                new_ents.append(
                                    e.translated(col_sp * j, row_sp * i))
                    for ne in new_ents:
                        self.entities.append(ne)
                    self._rebuild_snap_index(); self._redraw()
                    self._echo(f"ARRAY  {rows}×{cols} = {rows*cols} copias totales")
                else:
                    count  = max(2, int(_flds_p["count"].get()))
                    total_ang = float(_flds_p["angle"].get())
                    acx    = float(e_cx.get())
                    acy    = float(e_cy.get())
                    step_ang = total_ang / count
                    self._push_undo()
                    for k in range(1, count):
                        for e in sel:
                            self.entities.append(
                                e.rotated(acx, acy, step_ang * k))
                    self._rebuild_snap_index(); self._redraw()
                    self._echo(f"ARRAY POLAR  {count} copias  ángulo={total_ang}°")
            except Exception as exc:
                self._echo(f"!! ARRAY error: {exc}")
            top.destroy()

        ctk.CTkButton(top, text="Aceptar", fg_color=UI_ACC,
                      command=_confirm).pack(pady=8)
        ctk.CTkButton(top, text="Cancelar", fg_color=UI_CARD,
                      command=top.destroy).pack()

    # ─── EXPLODE ──────────────────────────────────────────────────
    def _explode(self):
        sel = [e for e in self.entities if e.selected and isinstance(e, Polyline)]
        if not sel:
            self._echo("EXPLODE: seleccione polilíneas primero"); return
        self._push_undo()
        for poly in sel:
            self.entities.remove(poly)
            pts = poly.points; n = len(pts)
            pairs = list(zip(pts, pts[1:]))
            if poly.closed and n > 2:
                pairs.append((pts[-1], pts[0]))
            for p1, p2 in pairs:
                self.entities.append(Line(x1=p1[0], y1=p1[1],
                                          x2=p2[0], y2=p2[1], layer=poly.layer))
        self._rebuild_snap_index(); self._redraw()

    # ─── Herramientas de dibujo ───────────────────────────────────
    def _commit_line(self):
        p1, p2 = self.draw_pts
        if math.hypot(p2[0]-p1[0], p2[1]-p1[1]) < 1e-6:
            self._echo("!! Longitud cero — línea descartada")
            self.draw_pts.clear(); return
        self._add_entity(Line(x1=p1[0], y1=p1[1], x2=p2[0], y2=p2[1],
                              layer=self.active_layer))
        self.draw_pts.clear()

    def _commit_rect(self):
        (x1,y1),(x2,y2) = self.draw_pts
        if abs(x2-x1) < 1e-6 or abs(y2-y1) < 1e-6:
            self._echo("!! Rectángulo degenerado — descartado")
            self.draw_pts.clear(); return
        self._add_entity(Polyline(points=[(x1,y1),(x2,y1),(x2,y2),(x1,y2)],
                                  closed=True, layer=self.active_layer))
        self.draw_pts.clear()

    def _commit_circle(self):
        (cx,cy),(px,py) = self.draw_pts[:2]
        r = math.hypot(px-cx, py-cy)
        if r > 0.001:
            self._add_entity(Circle(cx=cx, cy=cy, radius=r,
                                    layer=self.active_layer))
        self.draw_pts.clear()
        self._lbl_op.configure(text="")

    def _commit_arc(self):
        p1, pmid, p2 = self.draw_pts[:3]
        res = _circumcircle(p1, pmid, p2)
        if res is None:
            self._echo("!! Puntos colineales — arco imposible")
            self.draw_pts.clear(); self._lbl_op.configure(text=""); return
        cx, cy, r = res
        sa = math.degrees(math.atan2(p1[1]-cy,   p1[0]-cx))   % 360
        ea = math.degrees(math.atan2(p2[1]-cy,   p2[0]-cx))   % 360
        # winding del triángulo determina dirección CCW/CW
        cross = ((pmid[0]-p1[0])*(p2[1]-p1[1])
               - (pmid[1]-p1[1])*(p2[0]-p1[0]))
        ccw = cross > 0
        if r > 0.001:
            self._add_entity(Arc(cx=cx, cy=cy, radius=r,
                                 start_ang=sa, end_ang=ea, ccw=ccw,
                                 layer=self.active_layer))
        self.draw_pts.clear(); self._lbl_op.configure(text="")

    def _commit_ellipse(self):
        """Commit ellipse from 3 points: center, major-axis end, minor-radius point."""
        cpt, p_major, p_minor = self.draw_pts[:3]
        cx, cy = cpt
        rx = math.hypot(p_major[0]-cx, p_major[1]-cy)
        if rx < 1e-6:
            self._echo("!! Elipse degenerada — descartada")
            self.draw_pts.clear(); return
        angle = math.degrees(math.atan2(p_major[1]-cy, p_major[0]-cx))
        # ry = perpendicular distance from center to minor-axis click
        # (project p_minor onto the perpendicular to major axis)
        dx_maj = p_major[0] - cx
        dy_maj = p_major[1] - cy
        l = math.hypot(dx_maj, dy_maj)
        if l < 1e-10:
            self.draw_pts.clear(); return
        ux, uy = dx_maj / l, dy_maj / l   # unit along major axis
        px, py = p_minor[0]-cx, p_minor[1]-cy
        ry = abs(-px*uy + py*ux)           # perpendicular component
        if ry < 1e-6:
            ry = rx * 0.5
        self._add_entity(Ellipse(cx=cx, cy=cy, rx=rx, ry=ry,
                                 angle=angle, layer=self.active_layer))
        self.draw_pts.clear(); self._lbl_op.configure(text="")

    def _commit_polygon(self):
        """Commit regular polygon from center + vertex point."""
        cpt, vpt = self.draw_pts[:2]
        cx, cy = cpt
        r = math.hypot(vpt[0]-cx, vpt[1]-cy)
        if r < 1e-6:
            self._echo("!! Polígono degenerado — descartado")
            self.draw_pts.clear(); return
        n = max(3, min(72, self._polygon_sides))
        start_ang = math.atan2(vpt[1]-cy, vpt[0]-cx)
        pts = []
        for i in range(n):
            a = start_ang + 2 * math.pi * i / n
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        self._add_entity(Polyline(points=pts, closed=True, layer=self.active_layer))
        self.draw_pts.clear(); self._lbl_op.configure(text="")

    def _commit_xline(self):
        """Commit xline from 2 points."""
        p1, p2 = self.draw_pts[:2]
        if math.hypot(p2[0]-p1[0], p2[1]-p1[1]) < 1e-6:
            self._echo("!! Dirección nula — xline descartada")
            self.draw_pts.clear(); return
        self._add_entity(XLine(x1=p1[0], y1=p1[1], x2=p2[0], y2=p2[1],
                               layer=self.active_layer))
        self.draw_pts.clear(); self._lbl_op.configure(text="")

    def _commit_cloud(self):
        """Commit revision cloud as closed polyline with arc bumps."""
        boundary = list(self.draw_pts)
        if len(boundary) < 3:
            self.draw_pts.clear(); return
        # Close the boundary loop
        boundary.append(boundary[0])
        BUMP_HEIGHT = 0.05  # meters
        expanded = []
        for i in range(len(boundary) - 1):
            ax, ay = boundary[i]
            bx, by = boundary[i+1]
            # Edge midpoint for bump
            mx = (ax + bx) / 2
            my = (ay + by) / 2
            # Perpendicular direction (left side = outward for CCW polygon)
            dx, dy = bx - ax, by - ay
            d = math.hypot(dx, dy)
            if d < 1e-10:
                expanded.append((ax, ay))
                continue
            nx, ny = -dy / d, dx / d   # perpendicular pointing outward
            expanded.append((ax, ay))
            # lerp(A,B,0.25)
            expanded.append((ax + dx*0.25, ay + dy*0.25))
            # bump top
            expanded.append((mx + nx*BUMP_HEIGHT, my + ny*BUMP_HEIGHT))
            # lerp(A,B,0.75)
            expanded.append((ax + dx*0.75, ay + dy*0.75))
        self._add_entity(Polyline(points=expanded, closed=True,
                                  layer=self.active_layer))
        self.draw_pts.clear(); self._lbl_op.configure(text="")

    def _commit_leader(self):
        """Commit leader from draw_pts. Ask for text if desired."""
        if len(self.draw_pts) < 2:
            self.draw_pts.clear(); return
        pts = list(self.draw_pts)
        # Ask for text
        try:
            dlg = ctk.CTkInputDialog(
                text="Texto del leader  (Enter vacío = sin texto):",
                title="LEADER — texto")
            txt = dlg.get_input()
        except Exception:
            txt = ""
        if txt is None:
            txt = ""
        self._add_entity(Leader(points=pts, text=txt.strip(),
                                arrow_size=0.05, layer=self.active_layer))
        self.draw_pts.clear(); self._lbl_op.configure(text="")

    def _fin_pline(self, *_):
        if len(self.draw_pts) >= 2:
            self._add_entity(Polyline(points=list(self.draw_pts),
                                      closed=False, layer=self.active_layer))
        self.draw_pts.clear()

    def _fin_spline(self, *_):
        if len(self.draw_pts) >= 2:
            self._add_entity(Spline(points=list(self.draw_pts),
                                    closed=False, layer=self.active_layer))
        self.draw_pts.clear()

    def _cerrar_spline(self, *_):
        if self.tool == "spline" and len(self.draw_pts) >= 3:
            self._add_entity(Spline(points=list(self.draw_pts),
                                    closed=True, layer=self.active_layer))
            self.draw_pts.clear()

    def _cerrar_pline(self, *_):
        if self.tool == "polyline" and len(self.draw_pts) >= 3:
            self._add_entity(Polyline(points=list(self.draw_pts),
                                      closed=True, layer=self.active_layer))
            self.draw_pts.clear()

    def _undo_punto(self, *_):
        if self.draw_pts:
            self.draw_pts.pop()
        self._redraw_dynamic()

    def _pedir_texto(self, wx, wy):
        # Paso 1 — contenido
        dlg = ctk.CTkInputDialog(
            text=f"Texto en ({wx:.2f}, {wy:.2f}):", title="TEXTO")
        txt = dlg.get_input()
        if not txt or not txt.strip():
            self.draw_pts.clear()
            return
        # Paso 2 — altura (vacío = usar última altura)
        dlg2 = ctk.CTkInputDialog(
            text=f"Altura del texto [{self._text_last_height:.2f} m]:",
            title="TEXTO — Altura")
        h_str = dlg2.get_input()
        try:
            h = float(h_str) if h_str and h_str.strip() else self._text_last_height
            if h <= 0:
                h = self._text_last_height
        except ValueError:
            h = self._text_last_height
        self._text_last_height = h   # recordar para la próxima vez
        self._add_entity(Text(x=wx, y=wy, content=txt.strip().upper(),
                              height=h, layer=self.active_layer))
        self.draw_pts.clear()

    # ─── Selección ────────────────────────────────────────────────
    def _select_at(self, sx, sy, add: bool = False):
        """Selecciona la entidad más cercana al punto de pantalla (sx, sy).

        add=True  → acumula sobre la selección actual (modo *_sel de comandos).
        add=False → reemplaza la selección (clic normal en idle).
        Si se hace clic sobre una entidad ya seleccionada con add=True, la deselecciona
        (toggle), igual que AutoCAD.
        """
        # Guardar si había selección previa — si no cambia nada, no redibujamos PIL
        _had_sel = any(e.selected for e in self.entities)

        HIT = 8 / self.scale
        wx, wy = self.s2w(sx, sy)

        if not add:
            for e in self.entities:
                e.selected = False

        best = None; best_d = HIT
        # Usar índice espacial: en vez de _dist_entity() para cada entidad del dibujo,
        # se consultan solo las celdas que tocan el radio HIT (decenas vs miles).
        _eidx = self._entity_index
        _cands = (self._query_viewport(wx - HIT, wy - HIT, wx + HIT, wy + HIT)
                  if _eidx else self.entities)
        for e in _cands:
            lyr = self.layers.get(e.layer)
            if lyr and lyr.locked:
                continue
            d = self._dist_entity(wx, wy, e)
            if d < best_d:
                best_d = d; best = e

        if best:
            if add and best.selected:
                best.selected = False   # toggle: clic en ya-seleccionada la quita
            else:
                best.selected = True
            self._sel_version += 1
            # Si add=False, hay exactamente 0 o 1 entidades seleccionadas ahora
            # (deselección masiva + best.selected=True) → evitar O(N) sum().
            if not add:
                n_sel = 1 if best.selected else 0
            else:
                n_sel = sum(1 for e in self.entities if e.selected)
            if n_sel == 1 and best.selected:
                info = best.info()
                self._lbl_prop.configure(text=info)
                self._add_to_history("── entidad seleccionada ──", "sys")
                for line in info.splitlines():
                    if line.strip():
                        self._add_to_history("  " + line, "cad")
            else:
                self._lbl_prop.configure(text=f"{n_sel} entidad(es) sel.")
        else:
            if not add:
                self._lbl_prop.configure(text="—\nNinguna entidad\nseleccionada")
        self._update_prop_layer_om()
        # Grips en cualquier selección idle (sin comando, herramienta select)
        if not self._op_mode and self.tool == "select":
            self._compute_grips()
        else:
            self._grips = []; self._hot_grip = None; self._hover_grip = None
        # Render PIL solo si la selección cambió visualmente.
        if _had_sel or best is not None:
            self._redraw_static()
        else:
            self._redraw_dynamic()

    def _window_select(self, sx0, sy0, sx1, sy1, add: bool = False):
        """Izq→der = ventana (solo dentro) / Der→izq = cruce (toca el rect).

        add=True  → acumula sobre la selección actual (modo *_sel de comandos).
        add=False → reemplaza la selección (drag normal en idle).
        """
        # Guardar selección previa para render condicional
        _had_sel = any(e.selected for e in self.entities)

        wx0, wy0 = self.s2w(sx0, sy0)
        wx1, wy1 = self.s2w(sx1, sy1)
        is_cross = sx1 < sx0   # Der→izq = cruce

        xmin, xmax = min(wx0,wx1), max(wx0,wx1)
        ymin, ymax = min(wy0,wy1), max(wy0,wy1)

        if not add:
            for e in self.entities:
                e.selected = False

        sel = []
        # Pre-filtrar con índice espacial: la ventana de selección es exactamente
        # el viewport de búsqueda → _query_viewport descarta entidades fuera del rect.
        _eidx = self._entity_index
        _cands = (self._query_viewport(xmin, ymin, xmax, ymax)
                  if _eidx else self.entities)
        for e in _cands:
            lyr = self.layers.get(e.layer)
            if lyr and lyr.locked:
                continue
            ex0, ey0, ex1, ey1 = self._entity_aabb(e)
            if is_cross:
                # Cruce: el AABB toca el rectángulo
                if not (ex1 < xmin or ex0 > xmax or ey1 < ymin or ey0 > ymax):
                    sel.append(e)
            else:
                # Ventana: completamente dentro
                if ex0 >= xmin and ex1 <= xmax and ey0 >= ymin and ey1 <= ymax:
                    sel.append(e)

        for e in sel:
            e.selected = True
        if sel:
            self._sel_version += 1
        n_sel = sum(1 for e in self.entities if e.selected)
        if n_sel:
            self._lbl_prop.configure(text=f"{n_sel} entidad(es) sel.")
        else:
            self._lbl_prop.configure(text="—\nNinguna entidad\nseleccionada")
        self._update_prop_layer_om()
        # Grips en selección idle (sin comando activo)
        if not self._op_mode and self.tool == "select":
            self._compute_grips()
        else:
            self._grips = []; self._hot_grip = None; self._hover_grip = None
        # Render PIL solo si hay cambio visual (selección nueva o se limpió una previa)
        if _had_sel or sel:
            self._redraw_static()
        else:
            self._redraw_dynamic()

    def _select_all(self):
        n = 0
        for e in self.entities:
            lyr = self.layers.get(e.layer)
            if lyr and lyr.locked:
                e.selected = False
                continue
            e.selected = True
            n += 1
        self._lbl_prop.configure(text=f"Todo: {n} entidades seleccionadas")
        self._update_prop_layer_om()
        self._grips = []; self._hot_grip = None; self._hover_grip = None
        self._redraw_static()

    # ─── Grips ────────────────────────────────────────────────────

    def _compute_grips(self):
        """Recalcula la lista de grips a partir de las entidades seleccionadas."""
        self._grips = []
        self._hot_grip = None
        self._hover_grip = None
        for i, e in enumerate(self.entities):
            if not e.selected:
                continue
            lyr = self.layers.get(e.layer)
            if lyr and lyr.locked:
                continue
            for wx, wy, gid in self._grip_pts(e):
                self._grips.append({"wx": wx, "wy": wy, "eidx": i, "gid": gid})

    def _grip_pts(self, e):
        """Genera (wx, wy, grip_id) para cada punto de agarre de la entidad."""
        if isinstance(e, Line):
            yield e.x1, e.y1, "p1"
            yield e.x2, e.y2, "p2"
            yield (e.x1 + e.x2) / 2, (e.y1 + e.y2) / 2, "mid"

        elif isinstance(e, Polyline):
            pts = e.points
            n = len(pts)
            for vi, (vx, vy) in enumerate(pts):
                yield vx, vy, f"v{vi}"
            segs = n if e.closed else n - 1
            for mi in range(segs):
                j = (mi + 1) % n
                yield (pts[mi][0] + pts[j][0]) / 2, (pts[mi][1] + pts[j][1]) / 2, f"m{mi}"

        elif isinstance(e, Circle):
            yield e.cx, e.cy, "cen"
            yield e.cx + e.radius, e.cy, "q0"
            yield e.cx, e.cy + e.radius, "q1"
            yield e.cx - e.radius, e.cy, "q2"
            yield e.cx, e.cy - e.radius, "q3"

        elif isinstance(e, Arc):
            yield e.cx, e.cy, "cen"
            sa = math.radians(e.start_ang)
            ea = math.radians(e.end_ang)
            yield e.cx + e.radius * math.cos(sa), e.cy + e.radius * math.sin(sa), "start"
            yield e.cx + e.radius * math.cos(ea), e.cy + e.radius * math.sin(ea), "end"
            # midpoint del arco
            ma = math.radians((e.start_ang + e.end_ang) / 2)
            yield e.cx + e.radius * math.cos(ma), e.cy + e.radius * math.sin(ma), "mid"

        elif isinstance(e, Text):
            yield e.x, e.y, "ins"
            # Grip adicional en el centro del bbox → más fácil arrastrar
            x0, y0, x1, y1 = self._text_world_bbox(e)
            cx = (x0 + x1) * 0.5
            cy = (y0 + y1) * 0.5
            if abs(cx - e.x) > e.height * 0.3 or abs(cy - e.y) > e.height * 0.3:
                yield cx, cy, "cen"

        elif isinstance(e, Dimension):
            yield e.p1[0],  e.p1[1],  "p1"
            yield e.p2[0],  e.p2[1],  "p2"
            yield e.pos[0], e.pos[1], "pos"
            # Grip del texto: usa text_pos si está fijado, si no el centro calculado
            if getattr(e, "text_pos", None) is not None:
                yield e.text_pos[0], e.text_pos[1], "txt"
            else:
                tx, ty = self._dim_text_center(e)
                yield tx, ty, "txt"

        elif isinstance(e, Hatch):
            for vi, (vx, vy) in enumerate(e.boundary):
                yield vx, vy, f"v{vi}"

    def _check_grip_click(self, sx: int, sy: int) -> bool:
        """
        Procesa un clic sobre los grips.
        Si había un hot grip → confirma el movimiento.
        Si el clic cae sobre un grip frío → lo activa.
        Devuelve True si el clic fue consumido por los grips.
        """
        GRIP_HIT_PX = 10

        # ── Fase 2: ya hay un grip caliente → confirmar movimiento ──
        if self._hot_grip is not None:
            wx, wy = self.snap_pt[:2] if self.snap_pt else self.mouse_w
            self._apply_grip_move(wx, wy)
            return True

        # ── Fase 1: buscar grip frío bajo el cursor ──────────────────
        best = None
        best_d = GRIP_HIT_PX
        for g in self._grips:
            sgx, sgy = self.w2s(g["wx"], g["wy"])
            d = math.hypot(sx - sgx, sy - sgy)
            if d < best_d:
                best_d = d
                best = g

        if best:
            self._hot_grip = best
            self._redraw_dynamic()
            return True

        return False

    def _apply_grip_move(self, new_wx: float, new_wy: float):
        """Aplica el movimiento del grip activo y reinicia el estado."""
        g = self._hot_grip
        if g is None:
            return
        i   = g["eidx"]
        gid = g["gid"]

        if i >= len(self.entities):
            self._hot_grip = None
            self._hover_grip = None
            return

        e = self.entities[i]
        self._push_undo_diff(modified=[(i, e)])
        new_e = self._grip_modified_entity(e, gid, new_wx, new_wy)
        if new_e is not None:
            new_e.selected = True
            self.entities[i] = new_e

        self._hot_grip = None
        self._hover_grip = None

        # ── Overlay inmediato: dibuja nueva posición en canvas ANTES de que
        #    GL reteselle (3-4 s en archivos grandes).  Se borra en _apply_render
        #    cuando la imagen OpenGL con la geometría nueva llega.
        if new_e is not None and hasattr(self._renderer, '_tess_pending'):
            cv_ = self.canvas
            cv_.delete("st_grip_post")
            _col = getattr(self, 'select_color', '#00BFFF')
            self._render_entity(cv_, new_e, _col, 1, "st_grip_post")
            self._grip_post_active = True

        self._rebuild_snap_index()
        self._compute_grips()
        self._rebuild_prop_panel([self.entities[i]] if i < len(self.entities) else [])
        self._redraw()

    def _grip_modified_entity(self, e, gid: str, nx: float, ny: float):
        """Devuelve una copia de *e* con el grip *gid* movido a (nx, ny)."""
        if isinstance(e, Line):
            kw = dict(layer=e.layer, color=e.color, linewidth=e.linewidth)
            if gid == "p1":
                return Line(x1=nx, y1=ny, x2=e.x2, y2=e.y2, **kw)
            if gid == "p2":
                return Line(x1=e.x1, y1=e.y1, x2=nx, y2=ny, **kw)
            if gid == "mid":
                dx = nx - (e.x1 + e.x2) / 2
                dy = ny - (e.y1 + e.y2) / 2
                return e.translated(dx, dy)

        elif isinstance(e, Polyline):
            pts = list(e.points)
            kw  = dict(closed=e.closed, layer=e.layer,
                       color=e.color, linewidth=e.linewidth)
            if gid.startswith("v"):
                vi = int(gid[1:])
                if 0 <= vi < len(pts):
                    pts[vi] = (nx, ny)
                    return Polyline(points=pts, **kw)
            elif gid.startswith("m"):
                mi = int(gid[1:])
                n  = len(pts)
                j  = (mi + 1) % n
                mx_ = (pts[mi][0] + pts[j][0]) / 2
                my_ = (pts[mi][1] + pts[j][1]) / 2
                return e.translated(nx - mx_, ny - my_)

        elif isinstance(e, Circle):
            kw = dict(layer=e.layer, color=e.color, linewidth=e.linewidth)
            if gid == "cen":
                return Circle(cx=nx, cy=ny, radius=e.radius, **kw)
            if gid.startswith("q"):
                new_r = max(1e-6, math.hypot(nx - e.cx, ny - e.cy))
                return Circle(cx=e.cx, cy=e.cy, radius=new_r, **kw)

        elif isinstance(e, Arc):
            kw = dict(layer=e.layer, color=e.color, linewidth=e.linewidth)
            if gid == "cen":
                return Arc(cx=nx, cy=ny, radius=e.radius,
                           start_ang=e.start_ang, end_ang=e.end_ang,
                           ccw=e.ccw, **kw)
            if gid in ("start", "end", "mid"):
                new_ang = math.degrees(math.atan2(ny - e.cy, nx - e.cx))
                if gid == "start":
                    return Arc(cx=e.cx, cy=e.cy, radius=e.radius,
                               start_ang=new_ang, end_ang=e.end_ang,
                               ccw=e.ccw, **kw)
                if gid == "end":
                    return Arc(cx=e.cx, cy=e.cy, radius=e.radius,
                               start_ang=e.start_ang, end_ang=new_ang,
                               ccw=e.ccw, **kw)
                if gid == "mid":   # cambia radio
                    new_r = max(1e-6, math.hypot(nx - e.cx, ny - e.cy))
                    return Arc(cx=e.cx, cy=e.cy, radius=new_r,
                               start_ang=e.start_ang, end_ang=e.end_ang,
                               ccw=e.ccw, **kw)

        elif isinstance(e, Text):
            if gid == "ins":
                return Text(x=nx, y=ny, angle=e.angle, **e._kw())
            if gid == "cen":
                # Arrastrar el centro del bbox → mover punto de inserción equivalente
                x0, y0, x1, y1 = self._text_world_bbox(e)
                dx = nx - (x0 + x1) * 0.5
                dy = ny - (y0 + y1) * 0.5
                return Text(x=e.x + dx, y=e.y + dy, angle=e.angle, **e._kw())

        elif isinstance(e, Dimension):
            kw = dict(dim_type=e.dim_type, text_override=e.text_override,
                      style=e.style, layer=e.layer,
                      text_pos=getattr(e, "text_pos", None))
            if gid == "p1":
                return Dimension(p1=(nx, ny), p2=e.p2, pos=e.pos, **kw)
            if gid == "p2":
                return Dimension(p1=e.p1, p2=(nx, ny), pos=e.pos, **kw)
            if gid == "pos":
                return Dimension(p1=e.p1, p2=e.p2, pos=(nx, ny), **kw)
            if gid == "txt":
                # Fijar posición del texto en la posición arrastrada
                return Dimension(p1=e.p1, p2=e.p2, pos=e.pos,
                                 dim_type=e.dim_type, text_override=e.text_override,
                                 style=e.style, layer=e.layer, text_pos=(nx, ny))

        elif isinstance(e, Hatch):
            if gid.startswith("v"):
                vi = int(gid[1:])
                pts = list(e.boundary)
                if 0 <= vi < len(pts):
                    pts[vi] = (nx, ny)
                    return Hatch(boundary=pts, pattern=e.pattern,
                                 angle=e.angle, scale=e.scale, layer=e.layer)

        return None

    def _draw_grips(self):
        """Dibuja cuadrados de grip en la capa dinámica."""
        if not self._grips:
            return
        cv  = self.canvas
        GPX = 6   # mitad del tamaño del cuadrado en px
        for g in self._grips:
            sx, sy = self.w2s(g["wx"], g["wy"])
            if g is self._hot_grip:
                fill = "#FF4040"   # rojo — grip activo (clic)
                sz   = GPX + 1     # ligeramente más grande cuando activo
            elif g is self._hover_grip:
                fill = "#FF8C00"   # naranja — grip bajo el cursor (hover)
                sz   = GPX
            else:
                fill = "#3B82F6"   # azul — grip frío
                sz   = GPX
            cv.create_rectangle(sx - sz, sy - sz, sx + sz, sy + sz,
                                fill=fill, outline="white", width=1, tags="dy")

    def _draw_grip_preview(self):
        """Preview fantasma de la entidad mientras un grip está caliente."""
        if self._hot_grip is None:
            return
        g   = self._hot_grip
        i   = g["eidx"]
        gid = g["gid"]
        if i >= len(self.entities):
            return
        e = self.entities[i]
        mx, my = self.snap_pt[:2] if self.snap_pt else self.mouse_w
        prev = self._grip_modified_entity(e, gid, mx, my)
        if prev:
            self._render_entity(self.canvas, prev, UI_WARN, 1, "dy")
        # línea guía del grip al cursor
        sgx, sgy = self.w2s(g["wx"], g["wy"])
        smx, smy = self.w2s(mx, my)
        self.canvas.create_line(sgx, sgy, smx, smy,
                                fill=UI_WARN, width=1, dash=(4, 3), tags="dy")

    def _erase(self):
        sel = [e for e in self.entities if e.selected]
        if sel:
            sel_ids = {id(e) for e in sel}
            # Diff: guarda solo las entidades eliminadas con sus índices originales
            rem_pairs = [(i, e) for i, e in enumerate(self.entities)
                         if id(e) in sel_ids]
            self._push_undo_diff(removed=rem_pairs)
            self.entities = [e for e in self.entities if id(e) not in sel_ids]
            self._grips = []; self._hot_grip = None; self._hover_grip = None
            self._lbl_prop.configure(text="—\nNinguna entidad\nseleccionada")
            self._rebuild_snap_index(); self._redraw_static()

    @staticmethod
    def _text_world_bbox(e):
        """Bounding box mundo de un Text con halign y valign correctos.
        Devuelve (x0, y0, x1, y1) en coordenadas mundo.

        Usa las métricas reales del font atlas GL (advance por glifo) cuando
        están disponibles; si no, cae a la estimación h*0.6*chars.
        El cálculo de v_base replica exactamente al renderer OpenGL.
        """
        lines  = e.content.split('\n') if '\n' in e.content else [e.content]
        n_ln   = max(1, len(lines))
        h      = e.height
        lh     = h * 1.4   # line height en unidades mundo

        ha = getattr(e, 'halign', 0)
        va = getattr(e, 'valign', 0)

        # ── Ancho ────────────────────────────────────────────────────
        mw = getattr(e, 'mtext_width', 0.0) or 0.0
        if mw > 0:
            # mtext_width DXF disponible: usarlo directamente
            line_widths = [mw] * n_ln
        else:
            # Intentar usar métricas reales del atlas GL
            try:
                from cad.renderer_opengl import _atlas_glyph_uvs as _guv, \
                                                _atlas_base_px   as _bpx, \
                                                _atlas_sdf_pad   as _spad
            except ImportError:
                _guv, _bpx, _spad = {}, 16, 0
            if _guv:
                sf_w = h / float(_bpx)
                _fb  = _guv.get('?') or (0, 0, 0, 0, _bpx * 0.6, _bpx)
                # adv_px en glyph_uvs[4] es el avance REAL del glifo (sin pad SDF)
                line_widths = [
                    sum((_guv.get(ch) or _fb)[4] * sf_w for ch in ln) if ln else 0.0
                    for ln in lines
                ]
            else:
                # Atlas todavía no inicializado → estimación clásica
                max_ch = max((len(l) for l in lines), default=1)
                line_widths = [h * 0.6 * max_ch] * n_ln

        max_w = max(line_widths) if line_widths else h

        # ── Horizontal ───────────────────────────────────────────────
        if ha == 1:    x0, x1 = e.x - max_w * 0.5, e.x + max_w * 0.5
        elif ha == 2:  x0, x1 = e.x - max_w,        e.x
        else:          x0, x1 = e.x,                 e.x + max_w

        # ── Vertical — mismo v_base que usa el renderer GL ───────────
        if va == 3:    v_base = -h
        elif va == 2:  v_base = (n_ln * lh) * 0.5 - h
        else:          v_base = 0.0   # va == 0: baseline en e.y

        # Altura real del glifo desde el atlas (ph_w = h_px * sf_w).
        # Si el atlas está disponible calculamos el máximo real; si no, ~0.75h.
        try:
            from cad.renderer_opengl import _atlas_glyph_uvs as _guv2, \
                                            _atlas_base_px   as _bpx2
        except ImportError:
            _guv2, _bpx2 = {}, 16
        if _guv2:
            sf_w2 = h / float(_bpx2)
            all_chars = [ch for ln in lines for ch in ln if ln]
            if all_chars:
                _fb2 = _guv2.get('?') or (0, 0, 0, 0, _bpx2 * 0.6, _bpx2)
                max_ph = max((_guv2.get(ch) or _fb2)[5] * sf_w2 for ch in all_chars)
            else:
                max_ph = h * 0.75
        else:
            max_ph = h * 0.75

        # El renderer dibuja cada glifo entre yb = e.y+y_off y yt = e.y+y_off+ph_w
        # línea 0: y_off = v_base   → tope = e.y + v_base + max_ph
        # última:  y_off = v_base - (n_ln-1)*lh → yb = e.y + v_base - (n_ln-1)*lh
        y_top    = e.y + v_base + max_ph
        y_bottom = e.y + v_base - (n_ln - 1) * lh

        # Margen mínimo para facilitar el clic (5 % de h)
        margin = h * 0.05
        y1 = y_top    + margin
        y0 = y_bottom - margin

        # Para valign=3 el punto de inserción ES el tope del frame → incluirlo
        if va == 3:
            y1 = max(y1, e.y)

        return x0, y0, x1, y1

    def _dist_entity(self, wx, wy, e):
        if isinstance(e, Line):
            return _dist_pt_seg(wx, wy, e.x1, e.y1, e.x2, e.y2)
        if isinstance(e, Polyline):
            pts = e.points
            segs = list(zip(pts, pts[1:]))
            if e.closed and len(pts) > 1:
                segs.append((pts[-1], pts[0]))
            return min((_dist_pt_seg(wx, wy, a[0], a[1], b[0], b[1])
                        for a, b in segs), default=1e9)
        if isinstance(e, Circle):
            return abs(math.hypot(wx-e.cx, wy-e.cy) - e.radius)
        if isinstance(e, Arc):
            d_c = math.hypot(wx-e.cx, wy-e.cy)
            ang = math.degrees(math.atan2(wy-e.cy, wx-e.cx)) % 360
            if _angle_in_arc(ang, e.start_ang, e.end_ang, e.ccw):
                return abs(d_c - e.radius)
            sa = math.radians(e.start_ang); ea = math.radians(e.end_ang)
            return min(math.hypot(wx-(e.cx+e.radius*math.cos(sa)),
                                  wy-(e.cy+e.radius*math.sin(sa))),
                       math.hypot(wx-(e.cx+e.radius*math.cos(ea)),
                                  wy-(e.cy+e.radius*math.sin(ea))))
        if isinstance(e, Text):
            # Distancia al bounding box con halign/valign correctos.
            x0, y0, x1, y1 = self._text_world_bbox(e)
            dx = max(0.0, x0 - wx, wx - x1)
            dy = max(0.0, y0 - wy, wy - y1)
            return math.hypot(dx, dy)
        if isinstance(e, Dimension):
            # Distancia a los segmentos visibles reales: línea de cota + extensiones + texto
            x1, y1 = e.p1;  x2, y2 = e.p2;  px, py = e.pos
            segs = []
            if e.dim_type == "H":
                d1x, d1y, d2x, d2y = x1, py, x2, py
                segs = [(x1,y1,d1x,d1y), (x2,y2,d2x,d2y), (d1x,d1y,d2x,d2y)]
            elif e.dim_type == "V":
                d1x, d1y, d2x, d2y = px, y1, px, y2
                segs = [(x1,y1,d1x,d1y), (x2,y2,d2x,d2y), (d1x,d1y,d2x,d2y)]
            elif e.dim_type == "A":
                if getattr(e, 'rot_angle', None) is not None:
                    ar = math.radians(e.rot_angle)
                    ux, uy = math.cos(ar), math.sin(ar)
                    nxv, nyv = -uy, ux
                    base = px*nxv + py*nyv
                    off1 = base - (x1*nxv + y1*nyv)
                    off2 = base - (x2*nxv + y2*nyv)
                    d1x, d1y = x1+nxv*off1, y1+nyv*off1
                    d2x, d2y = x2+nxv*off2, y2+nyv*off2
                else:
                    dlen = math.hypot(x2-x1, y2-y1)
                    if dlen > 1e-9:
                        ux, uy = (x2-x1)/dlen, (y2-y1)/dlen
                        nxv, nyv = -uy, ux
                        off = (px-x1)*nxv + (py-y1)*nyv
                        d1x, d1y = x1+nxv*off, y1+nyv*off
                        d2x, d2y = x2+nxv*off, y2+nyv*off
                    else:
                        d1x, d1y, d2x, d2y = x1, y1, x2, y2
                segs = [(x1,y1,d1x,d1y), (x2,y2,d2x,d2y), (d1x,d1y,d2x,d2y)]
            else:
                # R, D, ANG, ARC, ORD: distancia a los 3 puntos definitorios
                return min(math.hypot(wx-x1, wy-y1),
                           math.hypot(wx-x2, wy-y2),
                           math.hypot(wx-px, wy-py))
            # Distancia al segmento más cercano
            best = min(_dist_pt_seg(wx, wy, ax, ay, bx, by)
                       for ax, ay, bx, by in segs)
            # También distancia al texto
            tp = getattr(e, 'text_pos', None)
            tx, ty = tp if tp else self._dim_text_center(e)
            best = min(best, math.hypot(wx-tx, wy-ty))
            return best
        if isinstance(e, Hatch):
            if len(e.boundary) < 2:
                return 1e9
            pts = e.boundary
            segs = list(zip(pts, pts[1:])) + [(pts[-1], pts[0])]
            return min((_dist_pt_seg(wx, wy, a[0], a[1], b[0], b[1])
                        for a, b in segs), default=1e9)
        if isinstance(e, Insert):
            # Distancia al punto de inserción; también revisar posición de cada attrib
            # (el texto del EJE está en WCS en att.x/att.y, no en e.x/e.y siempre)
            d = math.hypot(wx - e.x, wy - e.y)
            for att in getattr(e, 'attribs', []):
                ax = getattr(att, 'x', e.x)
                ay = getattr(att, 'y', e.y)
                d = min(d, math.hypot(wx - ax, wy - ay))
            return d
        return 1e9

    # ─── F1: Hover highlight ──────────────────────────────────────
    def _find_hover_entity(self, sx, sy):
        """Devuelve la entidad más cercana al cursor (dentro de HOVER_PX px) o None.
        Usa el índice espacial para búsqueda O(k) en vez de O(n)."""
        wx, wy = self.s2w(sx, sy)
        hw = HOVER_PX / self.scale      # radio en coords mundo

        # Mismo límite que snap: si el zoom out es extremo, skip hover
        cell = self._entity_cell
        cr_raw = int(hw / cell) + 1
        if cr_raw > self._SNAP_CR_MAX * 2:
            return None

        # Radio de hover en coordenadas mundo (usado también en el loop de candidatos)
        bbox_lo_x = wx - hw; bbox_hi_x = wx + hw
        bbox_lo_y = wy - hw; bbox_hi_y = wy + hw

        # Candidatos via índice espacial (usa celda adaptativa)
        if self._entity_index:
            cell = self._entity_cell
            cr = max(1, min(int(hw / cell) + 2, self._SNAP_CR_MAX * 2))
            cx_c = int(math.floor(wx / cell))
            cy_c = int(math.floor(wy / cell))
            seen: set = set()
            # Bucket global (None key): prefiltar por AABB para no checar distancia
            # a entidades que ni siquiera tocan el área de búsqueda.
            candidates = []
            for e in self._entity_index.get(None, []):
                ex0, ey0, ex1, ey1 = self._entity_aabb(e)
                if ex1 >= bbox_lo_x and ex0 <= bbox_hi_x and ey1 >= bbox_lo_y and ey0 <= bbox_hi_y:
                    candidates.append(e)
                    seen.add(id(e))
            for icx in range(cx_c - cr, cx_c + cr + 1):
                for icy in range(cy_c - cr, cy_c + cr + 1):
                    for e in self._entity_index.get((icx, icy), []):
                        eid = id(e)
                        if eid not in seen:
                            seen.add(eid)
                            candidates.append(e)
        else:
            candidates = self.entities

        best = None; best_d = hw
        # Lookup O(1) de AABB para early-exit antes de calcular distancia por vértices.
        # Elimina el O(k×v) de polilíneas grandes: si la AABB no toca el radio de hover,
        # ni siquiera miramos sus vértices.
        aabb_by_id = (self._entity_index.get("__aabb_by_id__")
                      if self._entity_index else None)
        for e in candidates:
            lyr = self.layers.get(e.layer)
            if lyr and (not lyr.visible or lyr.locked):
                continue
            if e.selected:              # ya resaltadas por selección
                continue
            # Early-exit por AABB (O(1)) antes del cálculo por vértices (O(v))
            if aabb_by_id is not None:
                aabb = aabb_by_id.get(id(e))
                if aabb is not None:
                    ex0, ey0, ex1, ey1 = aabb
                    if (ex1 < bbox_lo_x or ex0 > bbox_hi_x or
                            ey1 < bbox_lo_y or ey0 > bbox_hi_y):
                        continue
            d_world = self._dist_entity(wx, wy, e)
            if d_world < best_d:
                best_d = d_world; best = e
        return best

    # ─── Undo / Redo ──────────────────────────────────────────────
    @staticmethod
    def _copy_entity(e: "Entity") -> "Entity":
        """Copia superficial de entidad (rápido). Polyline.points se copia explícitamente."""
        new_e = copy.copy(e)
        if isinstance(e, Polyline):
            new_e.points = list(e.points)   # list de tuples inmutables → ok con shallow
        return new_e

    def _snapshot(self) -> dict:
        """Captura estado completo. Usa copy.copy por entidad (×10 más rápido que deepcopy)."""
        self._last_freeze_op = '_snapshot'
        return {
            "entities": [self._copy_entity(e) for e in self.entities],
            "layers":   copy.deepcopy(self.layers),   # layers sí se mutan in-place
            "active_layer": self.active_layer,
        }

    def _restore_snapshot(self, snap: dict):
        """Restaura un snapshot (completo o diferencial)."""
        if snap.get('_kind') == 'diff':
            # ── Restauración diferencial: invierte exactamente lo que se hizo ──
            # 1) Eliminar entidades que fueron agregadas al final
            added = snap['added_count']
            if added > 0:
                del self.entities[-added:]
            # 2) Re-insertar entidades que fueron eliminadas (en orden ascendente)
            for i, e in snap['removed']:
                idx = min(i, len(self.entities))
                self.entities.insert(idx, self._copy_entity(e))
            # 3) Restaurar entidades que fueron modificadas
            for i, old_e in snap['modified']:
                if 0 <= i < len(self.entities):
                    self.entities[i] = self._copy_entity(old_e)
        else:
            # ── Snapshot completo ──────────────────────────────────────────
            self.entities = [self._copy_entity(e) for e in snap["entities"]]

        self.layers       = copy.deepcopy(snap["layers"])
        self.active_layer = snap.get("active_layer", self.active_layer)
        # Sincronizar UI de capas
        self._refresh_om_capa(list(self.layers.keys()))
        self._om_capa_var.set(self.active_layer)
        self._build_layer_panel()

    def _invalidate_gl_vbo(self):
        """Invalida el VBO cache de OpenGL para forzar rebuild en el próximo frame.
        Necesario cuando las entidades cambian de posición/forma sin cambiar en cantidad
        (MOVE, ROTATE, SCALE, MIRROR, OFFSET, FILLET, etc.) porque el cache key
        (id + len) no detecta modificaciones in-place."""
        r = getattr(self, "_renderer", None)
        if r is not None:
            if hasattr(r, "_vbo_cache"):
                r._vbo_cache     = {}
                r._vbo_cache_key = None

    def _push_undo(self):
        """Snapshot completo — fallback para operaciones complejas.
        Usa _push_undo_diff() cuando sea posible (mucho menos RAM)."""
        self._last_freeze_op = '_push_undo'
        self._invalidate_gl_vbo()   # entidades van a cambiar → VBO viejo inválido
        snap = self._snapshot()
        snap['_kind'] = 'full'
        self._undo_stack_push(snap)
        self._dirty = True

    def _push_undo_diff(self, modified=None, removed=None, added_count=0):
        """Snapshot diferencial — almacena SOLO las entidades que cambian.
        Con 100k entidades y MOVE de 10: 10 copias en vez de 100k (10000× menos).

        modified:     lista de (índice, entidad_vieja) — antes de modificar
        removed:      lista de (índice, entidad) — antes de eliminar
        added_count:  cuántas entidades se agregarán al final de self.entities
        """
        # Invalidar VBO solo cuando hay modificaciones/eliminaciones in-place.
        # Esas operaciones cambian geometría existente sin alterar len(entities),
        # por lo que el cache key no las detectaría → hay que forzar miss.
        # Adiciones puras (added_count > 0, modified=None, removed=None):
        # len(entities) crece → el cache key ya genera un miss y activa el
        # path incremental del tessellator (tessella solo las entidades nuevas).
        if modified or removed:
            self._invalidate_gl_vbo()
        snap = {
            '_kind':       'diff',
            'n_before':    len(self.entities),
            'modified':    [(i, self._copy_entity(e)) for i, e in (modified or [])],
            'removed':     sorted(((i, self._copy_entity(e)) for i, e in (removed or [])),
                                  key=lambda x: x[0]),
            'added_count': added_count,
            'layers':      copy.deepcopy(self.layers),
            'active_layer': self.active_layer,
        }
        self._undo_stack_push(snap)
        self._dirty = True

    def _undo_stack_push(self, snap):
        self._undo_stack.append(snap)
        self._redo_stack.clear()
        if len(self._undo_stack) > 30:   # 30 → suficiente sin consumir GB
            self._undo_stack.pop(0)
        self._update_undo_btns()

    def _undo(self, *_):
        if len(self._undo_stack) > 1:
            # Cancelar operación en curso para evitar puntos huérfanos
            self.draw_pts.clear()
            self._op_mode = ""; self._op_pts = []; self._op_data = {}
            self._grips = []; self._hot_grip = None; self._hover_grip = None
            self._redo_stack.append(self._snapshot())
            self._undo_stack.pop()
            self._restore_snapshot(self._undo_stack[-1])
            self._lbl_prop.configure(text="—\nNinguna entidad\nseleccionada")
            self._rebuild_snap_index(); self._redraw()
        self._update_undo_btns()

    def _redo(self, *_):
        if self._redo_stack:
            # Cancelar operación en curso (mismo motivo que _undo)
            self.draw_pts.clear()
            self._op_mode = ""; self._op_pts = []; self._op_data = {}
            self._grips = []; self._hot_grip = None; self._hover_grip = None
            self._undo_stack.append(self._snapshot())
            self._restore_snapshot(self._redo_stack.pop())
            self._lbl_prop.configure(text="—\nNinguna entidad\nseleccionada")
            self._rebuild_snap_index(); self._redraw()
        self._update_undo_btns()

    def _add_entity(self, e: Entity):
        lyr = self.layers.get(e.layer)
        if lyr and lyr.locked:
            self._echo(f"!! Capa '{e.layer}' está bloqueada — desbloquee antes de dibujar")
            return
        self._push_undo_diff(added_count=1)
        self.entities.append(e)
        # ── snap index incremental ─────────────────────────────────
        for wp in e.snap_points():
            key = (_cell(wp[0]), _cell(wp[1]))
            self._snap_index.setdefault(key, []).append(wp)
        # ── entity spatial index incremental (usa celda adaptativa) ──
        cell = self._entity_cell
        x0, y0, x1, y1 = self._entity_aabb(e)
        cx0 = int(math.floor(x0 / cell))
        cy0 = int(math.floor(y0 / cell))
        cx1 = int(math.floor(x1 / cell))
        cy1 = int(math.floor(y1 / cell))
        _MAX_CELLS = 40 * 40
        if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) > _MAX_CELLS:
            self._entity_index.setdefault(None, []).append(e)
        else:
            for cx in range(cx0, cx1 + 1):
                for cy in range(cy0, cy1 + 1):
                    self._entity_index.setdefault((cx, cy), []).append(e)
        self._redraw()

    # ─── Render: doble capa ───────────────────────────────────────
    def _redraw(self, *_):
        self._redraw_static()
        self._redraw_dynamic()

    # ── PIL: delegar a RendererPIL ────────────────────────────────────

    def _render_pil_full(self, W: int, H: int):
        """Construye un RenderCtx y delega al RendererPIL desacoplado."""
        self._last_freeze_op = '_render_pil_full'
        ctx = self._build_render_ctx(W, H)
        return self._renderer.render(ctx).image

    # ── PIL: render principal ─────────────────────────────────────

    def _redraw_static(self, *_):
        """Dispara un render asíncrono: PIL corre en hilo de fondo,
        paste() se aplica en el hilo principal cuando termina.

        Usa un contador de generación (_render_gen) para que solo el render
        más reciente aplique su resultado: los renders obsoletos descartan
        su imagen sin llamar paste(), eliminando la cadena de bloqueos.
        """
        cv = self.canvas
        W  = cv.winfo_width();  H = cv.winfo_height()
        if W < 10 or H < 10:
            return
        self._cv_w = W; self._cv_h = H   # cache para _redraw_dynamic_inner

        # ── Layout / Paper Space ───────────────────────────────────────
        if getattr(self, "active_layout_idx", -1) >= 0:
            if RendererPIL.available():
                self._redraw_static_layout(W, H)
            return

        if not RendererPIL.available():
            cv.delete("st"); self._canvas_img_id = None
            if self.grid_on: self._draw_grid(W, H)
            self._draw_axes(W, H); self._draw_entities(W, H)
            self._draw_hud(W, H); self._update_flags()
            return

        # Cancelar render anterior y crear nuevo Event para este render
        # (debe hacerse ANTES de construir ctx porque RenderCtx es frozen)
        self._render_cancel.set()                     # señal al hilo anterior
        cancel_ev = threading.Event()
        self._render_cancel = cancel_ev

        # Incrementar generación — cualquier render previo ya es obsoleto
        self._render_gen += 1
        gen = self._render_gen
        import time as _t; self._render_t0 = _t.perf_counter()

        self._render_pending = True

        # Construir ctx en el hilo principal (accede a self.*); cancel_ev incluido
        ctx = self._build_render_ctx(W, H, cancel_ev=cancel_ev)

        def _bg_render():
            try:
                img = self._renderer.render(ctx).image
                # Pre-convertir a RGBA en hilo de fondo para minimizar trabajo en main thread.
                # ImageTk.PhotoImage(RGB) hace convert('RGBA') internamente en el main thread
                # bloqueando ~20-40ms. Con RGBA pre-convertido aquí, el main thread solo
                # ejecuta el Tcl PyImagingPhoto (memcpy nivel C, ~1ms).
                if img is not None:
                    img = img.convert('RGBA')
                    img.load()   # fuerza el buffer C a estar en memoria antes del traspaso
            except Exception as err:
                img = None
                print(f"[WARN] PIL render falló: {err}")
            # Solo aplicar si no fuimos cancelados y seguimos siendo el más reciente
            if not cancel_ev.is_set() and gen == self._render_gen:
                self.root.after(0, lambda: self._apply_render(img, W, H, gen))

        self._render_thread = threading.Thread(target=_bg_render, daemon=True)
        self._render_thread.start()

    # Margen extra alrededor del render PIL.
    # 0 = imagen exactamente del tamaño del canvas (más rápido).
    # Valores pequeños (~80) dan algo de buffer sin costar demasiado.
    _PIL_MARGIN: int = 0

    def _build_render_ctx(self, W: int, H: int, cancel_ev=None):
        """Construye RenderCtx en el hilo principal (único que puede acceder a self.*)."""
        from cad.renderer_pil import RenderCtx
        import time as _t_ctx
        M = self._PIL_MARGIN
        cfg = self._leer_config_ia()

        # Detectar cambio de viewport (pan/zoom) para flag is_panning.
        # Se compara la firma (scale, offset) redondeada; si cambia → anotar tiempo.
        # is_panning=True durante 300ms tras el último cambio de viewport.
        _now_ctx = _t_ctx.perf_counter()
        _vp_sig = (round(self.scale, 5),
                   round(self.offset_x, 2),
                   round(self.offset_y, 2))
        if _vp_sig != getattr(self, '_last_vp_sig', None):
            self._last_vp_sig      = _vp_sig
            self._last_vp_change_t = _now_ctx
        _is_panning = (_now_ctx - getattr(self, '_last_vp_change_t', 0.0)) < 0.30

        # sel_version: se incrementa directamente en _select_at / _window_select
        # — ya no se recalcula con sum() O(N) en cada frame.
        return RenderCtx(
            W=W + 2 * M, H=H + 2 * M,
            scale=self.scale,
            offset_x=self.offset_x + M,
            offset_y=self.offset_y + M,
            entities=self.entities,
            layers=self.layers,
            block_defs=self.block_defs,
            entity_index=self._entity_index,
            entity_cell=self._entity_cell,
            grid_on=self.grid_on,
            bg_color=self.cv_bg,
            grid_color=self._cv_grid(),
            grid_maj_color=self._cv_grid_maj(),
            axis_color=self.axis_color,
            select_color=self.select_color,
            grid_major=self.grid_major,
            grid_minor=self.grid_minor,
            cancel_ev=cancel_ev,
            config=cfg,
            sel_version=self._sel_version,
            is_panning=_is_panning,
        )

    def _apply_render(self, img, W: int, H: int, gen: int = -1):
        """Aplica el resultado PIL en el hilo principal.
        `gen` es la generación del render: si ya no es la actual, se descarta.
        """
        # Guard: si llegó un render más reciente mientras este corría, ignorar.
        # NO limpiar _render_pending aquí — el gen más reciente lo hará al completar.
        # Limpiar prematuramente dejaría _render_pending=False mientras un render
        # más nuevo sigue corriendo (comportamiento "stuck" silencioso).
        cur_gen = getattr(self, '_render_gen', 0)
        if gen >= 0 and gen != cur_gen:
            return

        self._render_pending = False
        import time as _t
        now = _t.perf_counter()
        self._render_ms = (now - self._render_t0) * 1000
        self._fps_times.append(now)
        self._fps_times = [t for t in self._fps_times if now - t < 2.0]  # ventana 2s
        cv = self.canvas
        M  = self._PIL_MARGIN   # imagen es (W+2M)×(H+2M), colocada en (-M,-M)

        # ── Indicador de tessellation en curso ───────────────────────────────
        if (getattr(self._renderer, '_tess_pending', False)
                and not getattr(self, '_tess_poll_active', False)):
            self._tess_poll_active = True
            self.root.after(150, self._poll_tess_progress)

        # Mostrar badge del renderer activo en esquina inferior derecha.
        # Usar tk.Label nativo — CTkLabel.configure() tarda ~500ms por round-trips internos.
        _rname = getattr(self._renderer, 'name', lambda: 'PIL')()
        if not hasattr(self, '_lbl_renderer'):
            self._lbl_renderer = tk.Label(
                cv, text="", font=("Segoe UI", 10, "bold"),
                fg="#22D3EE", bg="#0F172A",
                padx=6, pady=2, relief="flat")
        _badge = "⬡ OpenGL" if "OpenGL" in _rname else ""
        if _badge:
            if self._lbl_renderer.cget("text") != _badge:
                self._lbl_renderer.configure(text=_badge)
            self._lbl_renderer.place(relx=1.0, rely=0.0, anchor="ne", x=-8, y=8)
        else:
            self._lbl_renderer.place_forget()

        if img is not None:
            try:
                # Siempre crear PhotoImage nuevo en lugar de paste().
                # paste() toma 500-1000ms bajo contención de GIL con el tessellator;
                # PhotoImage() + itemconfigure toma ~10-50ms sin competir con numpy.
                new_photo = _PILImageTk.PhotoImage(img)
                _old_photo = self._pil_photo   # mantener referencia hasta después del configure
                self._pil_photo = new_photo
                if self._canvas_img_id is None:
                    cv.delete("st")
                    self._canvas_img_id = cv.create_image(
                        -M, -M, anchor="nw",
                        image=self._pil_photo, tags=("st", "st_pil"))
                else:
                    cv.itemconfigure(self._canvas_img_id, image=self._pil_photo)
                    cv.coords(self._canvas_img_id, -M, -M)
                    cv.delete("st_hud")
                del _old_photo   # liberar PhotoImage anterior
                # Guardar snapshot para zoom-preview durante scroll
                self._pil_img_cache   = img
                self._pil_cache_scale = self.scale
                self._pil_cache_ox    = self.offset_x
                self._pil_cache_oy    = self.offset_y
            except Exception as err:
                print(f"[WARN] apply_render falló: {err}")
        # ── Limpiar overlay post-grip cuando GL ya tiene la geometría nueva ──
        # _tess_pending pasa a False exactamente cuando el resultado se sube a VRAM
        # y el render actual ya usa esa geometría → es seguro borrar el overlay.
        if getattr(self, '_grip_post_active', False):
            if not getattr(self._renderer, '_tess_pending', False):
                cv.delete("st_grip_post")
                self._grip_post_active = False

        self._draw_hud(W, H)
        self._draw_dims_overlay(W, H)   # PIL no renderiza Dimension → overlay en canvas

        # ── Capturar métricas al ring buffer ─────────────────────────────
        try:
            import sys as _sys
            stats = getattr(self._renderer, '_perf_stats', {})
            fps_list = self._fps_times
            fps = len(fps_list) / 2.0 if len(fps_list) > 1 else 0.0
            undo_kb = sum(_sys.getsizeof(s) for s in self._undo_stack) // 1024
            snap = {
                "gl_ms":      stats.get("gl_ms", 0.0),
                "frame_ms":   stats.get("frame_ms", 0.0),
                "draw_ms":    stats.get("draw_ms", 0.0),
                "text_ms":    stats.get("text_ms", 0.0),
                "pbo_ms":     stats.get("pbo_ms", 0.0),
                "overlay_ms": stats.get("overlay_ms", 0.0),
                "total_ms":   stats.get("total_ms", self._render_ms),
                "vram_kb":    stats.get("vram_kb", 0),
                "cache_hit":  stats.get("cache_hit", False),
                "n_ents":     stats.get("n_ents", len(self.entities)),
                "undo_kb":    undo_kb,
                "fps":        fps,
            }
            self._perf_ring.append(snap)
            if len(self._perf_ring) > self._perf_ring_max:
                self._perf_ring.pop(0)
        except Exception:
            pass

        # Panel de capas: actualizar contador siempre; construir cards solo si expandido
        if getattr(self, '_layer_panel_pending', False):
            self._layer_panel_pending = False
            n = len(self.layers)
            txt = f"{'▼' if getattr(self,'_layer_panel_expanded',False) else '▶'}  {n} capa{'s' if n!=1 else ''}"
            try:
                self._layer_toggle_btn.configure(text=txt)
            except Exception:
                pass
            if getattr(self, '_layer_panel_expanded', False):
                # Panel abierto: reconstruir con las nuevas capas
                self.root.after(500, self._build_layer_panel)
            else:
                # Panel colapsado: marcar para rebuild al expandir (cero costo ahora)
                self._layer_needs_rebuild = True

        self._update_flags()
        self._redraw_dynamic()

        # Si hay una barra de importación pendiente, cerrarla ahora que
        # la imagen ya está en pantalla (evita que desaparezca con canvas negro)
        _ilw = getattr(self, '_import_loading_win', None)
        if _ilw is not None:
            self._import_loading_win = None
            try:
                _ilw.destroy()
            except Exception:
                pass

    def _redraw_pan(self, dx: float, dy: float):
        """Pan: re-renderiza imagen PIL en la nueva posición (rápido ~10 ms)."""
        self._last_freeze_op = '_redraw_pan'
        cv = self.canvas
        # Usar dimensiones cacheadas por _redraw_static para evitar 2 Tcl round-trips
        # por cada evento de pan (hasta 60 veces/segundo durante arrastre).
        W = getattr(self, '_cv_w', 0) or cv.winfo_width()
        H = getattr(self, '_cv_h', 0) or cv.winfo_height()
        if W < 10 or H < 10:
            return

        if RendererPIL.available() and self._canvas_img_id is not None:
            try:
                # Mover dims inmediatamente con el pan (antes del re-render PIL)
                # para que no queden "atrás" visualmente durante el redraw
                cv.move("st_dim", dx, dy)
                M   = self._PIL_MARGIN
                img = self._render_pil_full(W, H)
                self._pil_photo = _PILImageTk.PhotoImage(img)
                cv.itemconfig(self._canvas_img_id, image=self._pil_photo)
                cv.coords(self._canvas_img_id, -M, -M)
                cv.delete("st_hud")
                self._draw_hud(W, H)
                self._draw_dims_overlay(W, H)   # redibuja en posición correcta
            except Exception:
                # Fallback completo
                self._redraw_static()
        else:
            # Sin PIL: mover items del canvas (comportamiento anterior)
            cv.move("st_ent", dx, dy)
            cv.delete("st_grid"); cv.delete("st_axes"); cv.delete("st_hud")
            if self.grid_on:
                self._draw_grid(W, H)
            self._draw_axes(W, H)
            self._draw_hud(W, H)

        self._update_flags()
        self._redraw_dynamic()

    def _redraw_dynamic(self, *_):
        # Guard de reentrancia: si ya estamos dibujando, ignorar llamada anidada.
        # Previene acumulación de canvas items cuando _draw_grips/_draw_op_preview
        # disparan un segundo _redraw_dynamic antes de que el primero termine.
        if getattr(self, '_dynamic_drawing', False):
            return
        self._dynamic_drawing = True
        try:
            self._redraw_dynamic_inner()
        finally:
            self._dynamic_drawing = False

    def _redraw_dynamic_inner(self, *_):
        cv = self.canvas
        cv.delete("dy")
        # tag_raise("dy_c") aquí era inútil: acabamos de borrar todos los "dy",
        # no hay nada encima del crosshair en este momento.
        # Usar dimensiones cacheadas de _redraw_static para evitar winfo_*
        # bajo contención de GIL (bloquea 500ms durante tessellation).
        W = getattr(self, '_cv_w', 0) or cv.winfo_width()
        H = getattr(self, '_cv_h', 0) or cv.winfo_height()
        if W < 10 or H < 10:
            return

        # ── Elementos "pesados" solo cuando hacen falta ───────────────
        _full = (
            self._hover_ent is not None
            or self._op_mode
            or self.draw_pts
            or self.tool != "select"
            or self._is_dragging
            or self._hot_grip is not None
            or bool(self._grips)
        )
        if _full:
            if self._hover_ent is not None:
                self._render_entity(cv, self._hover_ent, "#4A9EFF", 2, "dy")
            self._draw_preview()
            self._draw_dyn_input()      # DYN: panel flotante de entrada
            self._draw_op_preview()
            self._draw_sel_rect()
            self._draw_zoom_window_rect()   # ZOOM W: preview rectángulo dorado
            self._draw_grips()          # Grips: cuadrados azules/rojos
            self._draw_grip_preview()   # Grips: fantasma de entidad modificada
            # Panel de confirmación especial (mirror keep/delete)
            if self._op_mode == "mirror_keep":
                self._draw_mirror_keep_panel(cv)
            # Paneles informativos de operaciones multi-clic
            if self._op_mode in ("trim_obj", "extend_obj"):
                self._draw_trim_extend_panel(cv)
            if self._op_mode == "hatch_pts":
                self._draw_hatch_panel(cv)
            if self._op_mode in ("matchprop_src", "matchprop_dst"):
                self._draw_matchprop_panel(cv)
            if self._op_mode in ("measure_p1", "measure_next"):
                self._draw_measure_panel(cv)
            if self._op_mode == "dist_p2":
                self._draw_dist_ruler(cv)
            if self._op_mode in ("align_sp1", "align_sp2", "align_dp1", "align_dp2"):
                self._draw_align_panel(cv)
            if self._op_mode in (
                "dim_lp1", "dim_lp2", "dim_lpos",
                "dim_ang_cen", "dim_ang_p1", "dim_ang_p2",
                "dim_r_obj", "dim_r_pt",
            ):
                self._draw_dim_step_panel(cv)
            if self._op_mode == "zoom_opts":
                self._draw_zoom_opts_panel(cv)
            if self._op_mode == "array_type":
                self._draw_array_type_panel(cv)
            if self._op_mode == "array_pol_ctr":
                self._draw_array_pol_ctr_panel(cv)
            if self._op_mode in ("image_place", "image_width"):
                self._draw_image_preview()

        # ── Overlay buffer de teclado ─────────────────────────────────
        if self._kbd_buf:
            self._draw_kbd_buf_overlay(cv)

        if self.snap_pt:
            self._draw_snap_indicator()
        self._draw_cursor(W, H)
        # Subir crosshair encima de overlays "dy" si existen.
        # Usar _crs_ids (dict local, O(1)) en vez de find_withtag (scan O(n) del canvas).
        if getattr(self, '_crs_ids', None):
            cv.tag_raise("dy_c")

    # ─── Grid ─────────────────────────────────────────────────────
    def _draw_grid(self, W, H):
        cv = self.canvas
        if self.scale >= 160:
            step_f, step_m = GRID_MINOR, GRID_MAJOR
        elif self.scale >= 40:
            step_f, step_m = GRID_MAJOR, GRID_MAJOR*5
        elif self.scale >= 10:
            step_f, step_m = GRID_MAJOR*5, GRID_MAJOR*10
        else:
            return
        wx0, wy0 = self.s2w(0, H); wx1, wy1 = self.s2w(W, 0)
        for step, col in [(step_f, self._cv_grid()), (step_m, self._cv_grid_maj())]:
            # Limitar líneas de grid para no saturar el canvas
            n_cols = int((wx1 - wx0) / step) + 2
            n_rows = int((wy1 - wy0) / step) + 2
            if n_cols * n_rows > 4000:
                continue   # demasiadas líneas — saltar este nivel de grid
            x = math.floor(wx0/step)*step
            while x <= wx1+step:
                sx, _ = self.w2s(x, 0)
                cv.create_line(sx, 0, sx, H, fill=col, width=1, tags=("st", "st_grid"))
                x += step
            y = math.floor(wy0/step)*step
            while y <= wy1+step:
                _, sy = self.w2s(0, y)
                cv.create_line(0, sy, W, sy, fill=col, width=1, tags=("st", "st_grid"))
                y += step

    def _draw_axes(self, W, H):
        cv = self.canvas
        ox, oy = self.w2s(0, 0)
        cv.create_line(0, oy, W, oy, fill=CV_AXIS, width=1, tags=("st", "st_axes"))
        cv.create_line(ox, 0, ox, H, fill=CV_AXIS, width=1, tags=("st", "st_axes"))

    # ─── Entidades con culling ────────────────────────────────────
    def _entity_aabb(self, e):
        """Bounding box real de la entidad en coordenadas mundo."""
        if isinstance(e, ImageRef):
            corners = e._corners()
            xs = [c[0] for c in corners]; ys = [c[1] for c in corners]
            return min(xs), min(ys), max(xs), max(ys)
        if isinstance(e, Insert):
            return e._world_bbox(self.block_defs)
        if isinstance(e, Text):
            return self._text_world_bbox(e)
        if isinstance(e, Dimension):
            # bbox_pts() ya calcula la geometría real (línea de cota + extensiones + texto)
            pts = e.bbox_pts()
            if not pts:
                return 0, 0, 0, 0
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            # Margen extra = aprox. altura del texto de la cota para capturar el texto
            _dim_style = getattr(self, '_dim_styles', {}).get(
                getattr(e, 'style', ''), {})
            _txt_h = float(_dim_style.get('DIMTXT', 0.15) if _dim_style else 0.15)
            m = max(_txt_h * 1.5, 0.10)
            return min(xs)-m, min(ys)-m, max(xs)+m, max(ys)+m
        pts = e.bbox_pts()               # Arc usa bbox_pts() con cruces de eje
        if not pts:
            return 0, 0, 0, 0
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def _in_viewport(self, e, vx0, vy0, vx1, vy1):
        ex0,ey0,ex1,ey1 = self._entity_aabb(e)
        return not (ex1<vx0 or ex0>vx1 or ey1<vy0 or ey0>vy1)

    def _draw_dims_overlay(self, W: int, H: int):
        """Fallback canvas overlay para Dimension — solo cuando PIL no está disponible.
        Con PIL activo, el renderer PIL ya dibuja Dimension en el hilo de fondo.
        El overlay canvas se usa únicamente para Dimension seleccionadas/hover
        (para mostrar grips y highlight sin esperar el próximo render PIL).
        """
        # Con PIL disponible: el renderer PIL ya maneja Dimension → no duplicar
        if RendererPIL.available():
            cv = self.canvas
            cv.delete("st_dim")
            return
        # Sin PIL: fallback canvas
        from cad.entities import Dimension as _Dim
        cv = self.canvas
        cv.delete("st_dim")
        vx0, vy0, vx1, vy1 = self._viewport_world()
        m = max(5.0, 50 / self.scale)
        vx0 -= m; vy0 -= m; vx1 += m; vy1 += m
        _lyr_cache   = build_layer_cache(self.layers, self.scale)
        _lyr_default = (True, False, "#FFFFFF", 1, ())
        _tag         = ("st", "st_dim")
        for e in self.entities:
            if not isinstance(e, _Dim):
                continue
            if not self._in_viewport(e, vx0, vy0, vx1, vy1):
                continue
            col, lw, dash, skip = resolve_entity_props(
                e, _lyr_cache.get(e.layer, _lyr_default), CV_SELECT,
                scale=self.scale)
            if skip:
                continue
            self._render_entity(cv, e, col, lw, _tag, dash=dash)

    def _draw_entities(self, W, H):
        cv = self.canvas
        vx0,vy0,vx1,vy1 = self._viewport_world()
        m = max(5.0, 50/self.scale)
        vx0-=m; vy0-=m; vx1+=m; vy1+=m

        # Usar índice espacial si está disponible; fallback a lista completa
        candidates = (self._query_viewport(vx0, vy0, vx1, vy1)
                      if self._entity_index else self.entities)

        # Caché de capas — misma función que usa RendererPIL (fuente única)
        _lyr_cache   = build_layer_cache(self.layers, self.scale)
        _lyr_default = (True, False, "#FFFFFF", 1, ())
        _tag_ent     = ("st", "st_ent")

        for e in candidates:
            # Filtro fino viewport
            if not self._in_viewport(e, vx0, vy0, vx1, vy1):
                continue
            # LOD: omitir texto ilegible en zoom lejano
            if isinstance(e, Text) and e.height * self.scale < _LOD_TEXT_PX:
                continue
            col, lw, dash, skip = resolve_entity_props(
                e, _lyr_cache.get(e.layer, _lyr_default), CV_SELECT,
                scale=self.scale)
            if skip:
                continue
            self._render_entity(cv, e, col, lw, _tag_ent, dash=dash)

    def _render_entity(self, cv, e, col, lw, tag, dash=()):
        dk = {"dash": dash} if dash else {}
        if isinstance(e, Line):
            sx1,sy1 = self.w2s(e.x1, e.y1)
            sx2,sy2 = self.w2s(e.x2, e.y2)
            cv.create_line(sx1,sy1,sx2,sy2, fill=col, width=lw, tags=tag, **dk)

        elif isinstance(e, Polyline):
            if len(e.points) < 2:
                return
            flat = self.w2s_array(e.points)
            if e.closed:
                cv.create_polygon(*flat, fill="", outline=col,
                                  width=lw, joinstyle=tk.ROUND, tags=tag, **dk)
            else:
                cv.create_line(*flat, fill=col, width=lw,
                               joinstyle=tk.ROUND, tags=tag, **dk)

        elif isinstance(e, Spline):
            if len(e.points) < 2:
                return
            ipts = e.interp(n_seg=16)
            flat = self.w2s_array(ipts)
            if len(flat) >= 4:
                cv.create_line(*flat, fill=col, width=lw,
                               smooth=False, joinstyle=tk.ROUND, tags=tag, **dk)

        elif isinstance(e, Circle):
            sx0,sy0 = self.w2s(e.cx-e.radius, e.cy+e.radius)
            sx1,sy1 = self.w2s(e.cx+e.radius, e.cy-e.radius)
            cv.create_oval(sx0,sy0,sx1,sy1, outline=col, fill="", width=lw,
                           tags=tag, **dk)
            # LOD: puntito central solo cuando hay suficiente zoom
            if self.scale >= _LOD_DOT_SCALE:
                scx,scy = self.w2s(e.cx, e.cy)
                cv.create_oval(scx-2,scy-2,scx+2,scy+2, fill=col, outline="", tags=tag)

        elif isinstance(e, Arc):
            scx,scy = self.w2s(e.cx, e.cy)
            sr = e.radius * self.scale
            if e.ccw:
                extent = (e.end_ang - e.start_ang) % 360 or 360
            else:
                extent = -(((e.start_ang - e.end_ang) % 360) or 360)
            cv.create_arc(scx-sr, scy-sr, scx+sr, scy+sr,
                          start=e.start_ang, extent=extent,
                          outline=col, fill="", width=lw,
                          style=tk.ARC, tags=tag, **dk)

        elif isinstance(e, Text):
            # Dibujar bbox en lugar de re-renderizar el texto sobre el PIL.
            # Re-renderizar create_text encima del PIL crea doble-texto desplazado.
            ex0, ey0, ex1, ey1 = self._entity_aabb(e)
            sx0, sy_top = self.w2s(ex0, ey1)   # esquina sup-izq en pantalla
            sx1, sy_bot = self.w2s(ex1, ey0)   # esquina inf-der en pantalla
            cv.create_rectangle(sx0, sy_top, sx1, sy_bot,
                                outline=col, fill="", width=lw, tags=tag)

        elif isinstance(e, Dimension):
            self._render_dim(cv, e, col, lw, tag)

        elif isinstance(e, Hatch):
            self._render_hatch(cv, e, col, lw, tag)

        elif isinstance(e, Ellipse):
            bpts = e._pts_on_boundary(72)
            if len(bpts) >= 2:
                flat = self.w2s_array(bpts + [bpts[0]])
                if len(flat) >= 4:
                    if dash:
                        cv.create_line(*flat, fill=col, width=lw, tags=tag, dash=dash)
                    else:
                        cv.create_line(*flat, fill=col, width=lw, tags=tag)

        elif isinstance(e, XLine):
            sx1, sy1 = self.w2s(e.x1, e.y1)
            dx = e.x2 - e.x1;  dy = e.y2 - e.y1
            d = math.hypot(dx, dy)
            if d < 1e-10:
                return
            W = self.canvas.winfo_width(); H = self.canvas.winfo_height()
            sc = self.scale
            ux, uy = dx / d, dy / d
            # Compute t extent in screen space
            t_vals = []
            if abs(ux * sc) > 1e-6:
                for bnd in (-200 - sx1, W + 200 - sx1):
                    t_vals.append(bnd / (ux * sc))
            if abs(uy * sc) > 1e-6:
                for bnd in (-200 - sy1, H + 200 - sy1):
                    t_vals.append(bnd / (-uy * sc))
            if not t_vals:
                return
            t_min = min(t_vals); t_max = max(t_vals)
            wx_s = e.x1 + ux * t_min;  wy_s = e.y1 + uy * t_min
            wx_e = e.x1 + ux * t_max;  wy_e = e.y1 + uy * t_max
            sxs, sys_ = self.w2s(wx_s, wy_s)
            sxe, sye  = self.w2s(wx_e, wy_e)
            cv.create_line(sxs, sys_, sxe, sye, fill=col, width=lw,
                           dash=dash or (8, 4), tags=tag)

        elif isinstance(e, Leader):
            if len(e.points) < 2:
                return
            flat = self.w2s_array(e.points)
            if len(flat) >= 4:
                cv.create_line(*flat, fill=col, width=lw, tags=tag)
            # Arrowhead
            tip = self.w2s(*e.points[0])
            base_s = self.w2s(*e.points[1])
            arrow_px = max(6, int(e.arrow_size * self.scale))
            adx = tip[0] - base_s[0];  ady = tip[1] - base_s[1]
            alen = math.hypot(adx, ady)
            if alen > 1e-3:
                uax, uay = adx / alen, ady / alen
                bx = tip[0] - uax * arrow_px;  by = tip[1] - uay * arrow_px
                perpx = -uay * arrow_px * 0.35;  perpy = uax * arrow_px * 0.35
                cv.create_polygon(
                    tip[0], tip[1],
                    bx + perpx, by + perpy,
                    bx - perpx, by - perpy,
                    fill=col, outline="", tags=tag)
            # Text
            if e.text:
                tx, ty = self.w2s(*e.points[-1])
                cv.create_text(tx + 4, ty, text=e.text,
                               fill=col, font=("Courier New", 9),
                               anchor="w", tags=tag)

    def _draw_dim_arrow(self, cv, tip_x, tip_y, from_x, from_y, size_px, col, tag):
        """Flecha sólida en tip_x,tip_y apuntando desde from_x,from_y."""
        dx, dy = tip_x - from_x, tip_y - from_y
        dlen = math.hypot(dx, dy)
        if dlen < 1e-6:
            return
        ux, uy = dx / dlen, dy / dlen
        bx = tip_x - ux * size_px
        by = tip_y - uy * size_px
        perpx = -uy * size_px * 0.35
        perpy =  ux * size_px * 0.35
        cv.create_polygon(
            tip_x, tip_y,
            bx + perpx, by + perpy,
            bx - perpx, by - perpy,
            fill=col, outline="", tags=tag,
        )

    def _dim_text_center(self, e: "Dimension") -> tuple:
        """Calcula el centro del texto de una cota (sin convertir a pantalla)."""
        x1, y1 = e.p1;  x2, y2 = e.p2;  px, py = e.pos
        if e.dim_type == "H":
            return ((x1 + x2) / 2, py)
        if e.dim_type == "V":
            return (px, (y1 + y2) / 2)
        if e.dim_type in ("A", "R", "D"):
            return ((x1 + x2) / 2, (y1 + y2) / 2)
        if e.dim_type == "ARC":
            cx, cy = px, py
            r = math.hypot(x1 - cx, y1 - cy)
            if r < 1e-9:
                return (cx, cy)
            a1 = math.atan2(y1 - cy, x1 - cx)
            a2 = math.atan2(y2 - cy, x2 - cx)
            amid = (a1 + a2) / 2
            return (cx + r * 1.3 * math.cos(amid), cy + r * 1.3 * math.sin(amid))
        if e.dim_type == "ORD":
            return (x2, y2)
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    def _render_dim(self, cv, e: "Dimension", col: str, lw: int, tag):
        """Dibuja una cota (H/V/A/R/D/ANG/ARC/ORD) en el canvas."""
        ARR_PX = 8
        GAP    = 2    # px entre punto de extensión y la línea de extensión
        OVER   = 4    # px de prolongación más allá del eje de cota
        TSIZE  = 9    # font size fijo para texto

        x1, y1 = e.p1
        x2, y2 = e.p2
        px, py = e.pos

        # Helper: posición del texto en pantalla (usa text_pos si está fijado)
        def _text_screen():
            tp = getattr(e, "text_pos", None)
            if tp is not None:
                return self.w2s(*tp)
            wx, wy = self._dim_text_center(e)
            return self.w2s(wx, wy)

        if e.dim_type in ("H", "V", "A"):
            # Calcular extremos de la línea de cota
            if e.dim_type == "H":
                d1x, d1y = x1, py
                d2x, d2y = x2, py
                ex1s, ex1e = (x1, y1), (x1, py)
                ex2s, ex2e = (x2, y2), (x2, py)
            elif e.dim_type == "V":
                d1x, d1y = px, y1
                d2x, d2y = px, y2
                ex1s, ex1e = (x1, y1), (px, y1)
                ex2s, ex2e = (x2, y2), (px, y2)
            elif getattr(e, 'rot_angle', None) is not None:
                # Cota rotada — línea de cota a rot_angle, extensiones perpendiculares
                angle_rad = math.radians(e.rot_angle)
                ux, uy = math.cos(angle_rad), math.sin(angle_rad)
                nx, ny = -uy, ux
                dim_n = px * nx + py * ny
                p1_n = x1 * nx + y1 * ny
                off1 = dim_n - p1_n
                d1x, d1y = x1 + nx * off1, y1 + ny * off1
                p2_n = x2 * nx + y2 * ny
                off2 = dim_n - p2_n
                d2x, d2y = x2 + nx * off2, y2 + ny * off2
                ex1s, ex1e = (x1, y1), (d1x, d1y)
                ex2s, ex2e = (x2, y2), (d2x, d2y)
            else:  # A — alineada (sin rotación)
                dlen_w = math.hypot(x2 - x1, y2 - y1)
                if dlen_w < 1e-9:
                    return
                ux, uy = (x2 - x1) / dlen_w, (y2 - y1) / dlen_w
                nx, ny = -uy, ux
                off = (px - x1) * nx + (py - y1) * ny
                d1x, d1y = x1 + nx * off, y1 + ny * off
                d2x, d2y = x2 + nx * off, y2 + ny * off
                ex1s, ex1e = (x1, y1), (d1x, d1y)
                ex2s, ex2e = (x2, y2), (d2x, d2y)

            # Convertir a pantalla
            sd1x, sd1y = self.w2s(d1x, d1y)
            sd2x, sd2y = self.w2s(d2x, d2y)
            se1sx, se1sy = self.w2s(*ex1s)
            se1ex, se1ey = self.w2s(*ex1e)
            se2sx, se2sy = self.w2s(*ex2s)
            se2ex, se2ey = self.w2s(*ex2e)

            def _ext(sx, sy, ex, ey):
                dx, dy = ex - sx, ey - sy
                plen = math.hypot(dx, dy)
                if plen < GAP + 1:
                    return
                gx = sx + dx / plen * GAP
                gy = sy + dy / plen * GAP
                ox = ex + dx / plen * OVER
                oy = ey + dy / plen * OVER
                cv.create_line(gx, gy, ox, oy, fill=col, width=lw, tags=tag)

            if not getattr(e, 'no_ext', False):
                _ext(se1sx, se1sy, se1ex, se1ey)
                _ext(se2sx, se2sy, se2ex, se2ey)

            # Línea de cota
            cv.create_line(sd1x, sd1y, sd2x, sd2y, fill=col, width=lw, tags=tag)

            # Flechas
            self._draw_dim_arrow(cv, sd1x, sd1y, sd2x, sd2y, ARR_PX, col, tag)
            self._draw_dim_arrow(cv, sd2x, sd2y, sd1x, sd1y, ARR_PX, col, tag)

            # Texto (posición fijada por grip o calculada)
            tmx, tmy = _text_screen()
            ang_deg = math.degrees(math.atan2(sd2y - sd1y, sd2x - sd1x))
            if not (-90 <= ang_deg <= 90):
                ang_deg += 180
            # Si no hay text_pos fijado → desplazar perpendicularmente para no tapar la línea
            tp = getattr(e, "text_pos", None)
            if tp is None:
                perp_ang = math.radians(ang_deg + 90)
                tmx += math.cos(perp_ang) * 8
                tmy += math.sin(perp_ang) * 8
            cv.create_text(tmx, tmy, text=e.text(), fill=col,
                           font=("Courier New", TSIZE), angle=-ang_deg,
                           anchor="center", tags=tag)

        elif e.dim_type == "R":
            scx, scy = self.w2s(x1, y1)
            srx, sry = self.w2s(x2, y2)
            cv.create_line(scx, scy, srx, sry, fill=col, width=lw, tags=tag)
            self._draw_dim_arrow(cv, srx, sry, scx, scy, ARR_PX, col, tag)
            tmx, tmy = _text_screen()
            cv.create_text(tmx - 6, tmy - 8, text=e.text(), fill=col,
                           font=("Courier New", TSIZE), anchor="e", tags=tag)

        elif e.dim_type == "D":
            # Diámetro: línea que pasa por el centro (p1), terminando en p2 y su opuesto
            scx, scy = self.w2s(x1, y1)
            srx, sry = self.w2s(x2, y2)
            # Opuesto = p1 reflejado respecto a p2... no, p1=centro, p2=punto en borde
            # La línea de cota va desde el borde opuesto hasta p2, pasando por centro
            dx_w = x2 - x1;  dy_w = y2 - y1
            r_w  = math.hypot(dx_w, dy_w)
            if r_w < 1e-9:
                return
            opx, opy = x1 - dx_w, y1 - dy_w   # punto opuesto en el diámetro
            sopx, sopy = self.w2s(opx, opy)
            cv.create_line(sopx, sopy, srx, sry, fill=col, width=lw, tags=tag)
            self._draw_dim_arrow(cv, srx,  sry,  sopx, sopy, ARR_PX, col, tag)
            self._draw_dim_arrow(cv, sopx, sopy, srx,  sry,  ARR_PX, col, tag)
            tmx, tmy = _text_screen()
            cv.create_text(tmx, tmy - 8, text=e.text(), fill=col,
                           font=("Courier New", TSIZE), anchor="center", tags=tag)

        elif e.dim_type == "ANG":
            # p1 = centro, p2 = extremo brazo1, pos = extremo brazo2
            cx_w, cy_w = x1, y1
            scx, scy = self.w2s(cx_w, cy_w)
            sp2x, sp2y = self.w2s(x2, y2)
            spox, spoy = self.w2s(px, py)
            # Brazos guía
            cv.create_line(scx, scy, sp2x, sp2y, fill=col, width=lw,
                           dash=(4, 3), tags=tag)
            cv.create_line(scx, scy, spox, spoy, fill=col, width=lw,
                           dash=(4, 3), tags=tag)
            # Arco
            r_w  = math.hypot(x2 - cx_w, y2 - cy_w)
            r_px = r_w * self.scale
            if r_px > 2:
                a1 = math.degrees(math.atan2(-(y2 - cy_w), x2 - cx_w))
                a2 = math.degrees(math.atan2(-(py - cy_w), px - cx_w))
                ext = (a2 - a1) % 360
                if ext > 180:
                    ext -= 360
                cv.create_arc(scx - r_px, scy - r_px, scx + r_px, scy + r_px,
                              start=a1, extent=ext, outline=col, fill="",
                              width=lw, style=tk.ARC, tags=tag)
                amid = math.radians(a1 + ext / 2)
                tmx, tmy = _text_screen()
                if getattr(e, "text_pos", None) is None:
                    tmx = scx + r_px * math.cos(amid) * 1.2
                    tmy = scy - r_px * math.sin(amid) * 1.2
                cv.create_text(tmx, tmy, text=e.text(), fill=col,
                               font=("Courier New", TSIZE), anchor="center",
                               tags=tag)

        elif e.dim_type == "ARC":
            # Cota longitud de arco: p1=inicio, p2=fin, pos=centro del arco
            cx_w, cy_w = px, py
            r_w = math.hypot(x1 - cx_w, y1 - cy_w)
            if r_w < 1e-9:
                return
            r_px = r_w * self.scale
            scx, scy = self.w2s(cx_w, cy_w)

            a1_r = math.atan2(y1 - cy_w, x1 - cx_w)
            a2_r = math.atan2(y2 - cy_w, x2 - cx_w)
            da   = (a2_r - a1_r) % (2 * math.pi)

            a1_deg = math.degrees(math.atan2(-(y1 - cy_w), x1 - cx_w))
            ext    = math.degrees(da) if da > 0 else 360.0

            # Arco de cota (radio ligeramente mayor que el arco real)
            r_off = min(0.15 * r_w, max(self.scale * 8, 0.05 * r_w))
            r_dim = (r_w + r_off) * self.scale
            if r_dim > 2:
                cv.create_arc(scx - r_dim, scy - r_dim, scx + r_dim, scy + r_dim,
                              start=a1_deg, extent=ext,
                              outline=col, fill="", width=lw,
                              style=tk.ARC, tags=tag)

            # Líneas de extensión desde inicio y fin del arco al arco de cota
            r_dim_w = r_w + r_off
            ext1_end = (cx_w + r_dim_w * math.cos(a1_r),
                        cy_w + r_dim_w * math.sin(a1_r))
            ext2_end = (cx_w + r_dim_w * math.cos(a2_r),
                        cy_w + r_dim_w * math.sin(a2_r))
            cv.create_line(*self.w2s(x1, y1), *self.w2s(*ext1_end),
                           fill=col, width=lw, tags=tag)
            cv.create_line(*self.w2s(x2, y2), *self.w2s(*ext2_end),
                           fill=col, width=lw, tags=tag)

            # Flechas en los extremos del arco de cota
            a1_end_s = self.w2s(*ext1_end)
            a2_end_s = self.w2s(*ext2_end)
            tang1 = math.degrees(a1_r) + 90
            tang2 = math.degrees(a2_r) - 90
            # Simplificado: usar draw_dim_arrow desde punto extendido hacia tangente
            tg1r = math.radians(tang1)
            tg2r = math.radians(tang2)
            n1x = a1_end_s[0] + math.cos(tg1r) * ARR_PX
            n1y = a1_end_s[1] - math.sin(tg1r) * ARR_PX
            n2x = a2_end_s[0] + math.cos(tg2r) * ARR_PX
            n2y = a2_end_s[1] - math.sin(tg2r) * ARR_PX
            self._draw_dim_arrow(cv, *a1_end_s, n1x, n1y, ARR_PX, col, tag)
            self._draw_dim_arrow(cv, *a2_end_s, n2x, n2y, ARR_PX, col, tag)

            # Texto con símbolo de arco ⌒
            tmx, tmy = _text_screen()
            amid_r = a1_r + da / 2
            if getattr(e, "text_pos", None) is None:
                tmx = scx + r_dim * math.cos(amid_r) * 1.15
                tmy = scy - r_dim * math.sin(amid_r) * 1.15
            cv.create_text(tmx, tmy, text=f"⌒ {e.text()}", fill=col,
                           font=("Courier New", TSIZE), anchor="center", tags=tag)

        elif e.dim_type == "ORD":
            # Ordenada: p1=punto medido, p2=extremo del líder
            sp1x, sp1y = self.w2s(x1, y1)
            sp2x, sp2y = self.w2s(x2, y2)
            # Líder con codo ortogonal (si tiene desfase)
            dx_s = sp2x - sp1x;  dy_s = sp2y - sp1y
            if abs(dy_s) > abs(dx_s):  # líder vertical → mide X
                # Dibuja: p1 → codo horizontal → p2
                cv.create_line(sp1x, sp1y, sp2x, sp1y,
                               fill=col, width=lw, tags=tag)
                cv.create_line(sp2x, sp1y, sp2x, sp2y,
                               fill=col, width=lw, tags=tag)
            else:                       # líder horizontal → mide Y
                cv.create_line(sp1x, sp1y, sp1x, sp2y,
                               fill=col, width=lw, tags=tag)
                cv.create_line(sp1x, sp2y, sp2x, sp2y,
                               fill=col, width=lw, tags=tag)
            # Punto en la entidad
            cv.create_oval(sp1x - 3, sp1y - 3, sp1x + 3, sp1y + 3,
                           outline=col, fill=col, tags=tag)
            # Texto al final del líder
            tmx, tmy = _text_screen()
            if getattr(e, "text_pos", None) is None:
                tmx, tmy = sp2x + 5, sp2y
            cv.create_text(tmx, tmy, text=e.text(), fill=col,
                           font=("Courier New", TSIZE), anchor="w", tags=tag)

    # ─── Hatch render ─────────────────────────────────────────────
    def _render_hatch(self, cv, e: "Hatch", col: str, lw: int, tag):
        """Dibuja el relleno de un Hatch en el canvas."""
        if len(e.boundary) < 3:
            return
        # Convertir boundary a pantalla
        spts = [self.w2s(x, y) for x, y in e.boundary]
        flat = []
        for sx, sy in spts:
            flat.extend([sx, sy])

        if e.pattern == "SOLID":
            cv.create_polygon(*flat, fill=col, outline=col,
                              width=lw, tags=tag, stipple="")
            return

        # Para patrones de líneas: dibujar outline del polígono y
        # líneas interiores clipadas mediante scanlines
        cv.create_polygon(*flat, fill="", outline=col,
                          width=lw, tags=tag)

        # Espaciado en píxeles
        sp_px = max(3, e.scale * self.scale)

        # Ángulos según patrón
        angles_deg = []
        if e.pattern == "ANSI31":
            angles_deg = [e.angle]          # por defecto 45°
        elif e.pattern == "LINES":
            angles_deg = [e.angle]
        elif e.pattern == "CROSS":
            angles_deg = [e.angle, e.angle + 90.0]

        # Bounding box en pantalla
        xs = [p[0] for p in spts]
        ys = [p[1] for p in spts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        diag = math.hypot(x1 - x0, y1 - y0) + sp_px * 2

        # Para cada ángulo: generar líneas paralelas y cliparlas al polígono
        for ang_deg in angles_deg:
            ang_rad = math.radians(ang_deg)
            cos_a, sin_a = math.cos(ang_rad), math.sin(ang_rad)
            # Centro del bbox
            cx = (x0 + x1) / 2
            cy = (y0 + y1) / 2

            # Vector perpendicular a la dirección de las líneas
            px_vec = -sin_a
            py_vec =  cos_a

            # Generar líneas desde -diag/2 a +diag/2 en la dirección perpendicular
            n_lines = int(diag / sp_px) + 2
            for k in range(-n_lines, n_lines + 1):
                # Offset perpendicular
                off = k * sp_px
                # Punto en la línea (pasando por cx+off*pvec, cy+off*pvec)
                lx = cx + px_vec * off
                ly = cy + py_vec * off
                # Extremos de la línea de barrido (larga)
                half = diag
                lax = lx - cos_a * half;  lay = ly - sin_a * half
                lbx = lx + cos_a * half;  lby = ly + sin_a * half

                # Clipar al polígono (intersección de segmento con bordes)
                ts = []
                n = len(spts)
                for i in range(n):
                    ax, ay = spts[i]
                    bx, by = spts[(i + 1) % n]
                    # Resolver intersección paramétrica
                    dx_ab = bx - ax;  dy_ab = by - ay
                    dx_l  = lbx - lax; dy_l  = lby - lay
                    denom = dx_ab * dy_l - dy_ab * dx_l
                    if abs(denom) < 1e-6:
                        continue
                    t = ((lax - ax) * dy_ab - (lay - ay) * dx_ab) / denom
                    u = ((lax - ax) * dy_l  - (lay - ay) * dx_l)  / denom
                    if -1e-6 <= u <= 1.0 + 1e-6 and -1e-6 <= t <= 1.0 + 1e-6:
                        ts.append(t)
                ts.sort()
                # Dibujar segmentos pares (dentro del polígono)
                for i in range(0, len(ts) - 1, 2):
                    t_in  = ts[i]
                    t_out = ts[i + 1]
                    ix = lax + t_in  * (lbx - lax)
                    iy = lay + t_in  * (lby - lay)
                    ox = lax + t_out * (lbx - lax)
                    oy = lay + t_out * (lby - lay)
                    cv.create_line(ix, iy, ox, oy, fill=col, width=1, tags=tag)

    # ─── Capa dinámica ────────────────────────────────────────────
    def _draw_preview(self):
        cv = self.canvas
        if not self.draw_pts:
            return
        mx, my = self.w2s(*self.mouse_w)

        for wx,wy in self.draw_pts:
            sx,sy = self.w2s(wx, wy)
            cv.create_oval(sx-3,sy-3,sx+3,sy+3,
                           fill=UI_ACC, outline="white", width=1, tags="dy")

        if self.tool in ("line", "polyline"):
            sx0, sy0 = self.w2s(*self.draw_pts[-1])
            dyn_here = self.dyn_on and self._dyn_active()
            dyn_target = self._dyn_pt() if dyn_here else None

            if dyn_target:
                # Punto DYN calculado → línea sólida al destino exacto
                tmx, tmy = self.w2s(*dyn_target)
                cv.create_line(sx0, sy0, tmx, tmy, fill=CV_PREVIEW, width=1, tags="dy")
                cv.create_oval(tmx-4, tmy-4, tmx+4, tmy+4,
                               fill=CV_PREVIEW, outline="white", width=1, tags="dy")
            else:
                # Preview normal hacia el cursor
                cv.create_line(sx0, sy0, mx, my, fill=CV_PREVIEW,
                               width=1, dash=(6,4), tags="dy")
                if not dyn_here:   # etiqueta solo cuando DYN está apagado
                    dx = self.mouse_w[0] - self.draw_pts[-1][0]
                    dy = self.mouse_w[1] - self.draw_pts[-1][1]
                    dist  = math.hypot(dx, dy)
                    angle = math.degrees(math.atan2(dy, dx))
                    cv.create_text(mx+12, my-12,
                                   text=f"{dist:.3f}m  {angle:.1f}°",
                                   fill=UI_TEXT2, font=("Courier New", 9),
                                   anchor="w", tags="dy")

        elif self.tool == "spline":
            # Puntos confirmados ya → dibujar curva hasta el cursor
            preview_pts = list(self.draw_pts) + [self.mouse_w]
            if len(preview_pts) >= 2:
                tmp_spl = Spline(points=preview_pts)
                ipts = tmp_spl.interp(n_seg=16)
                flat = self.w2s_array(ipts)
                if len(flat) >= 4:
                    cv.create_line(*flat, fill=CV_PREVIEW, width=1,
                                   joinstyle=tk.ROUND, dash=(6, 4), tags="dy")
            # Etiqueta en el cursor
            cv.create_text(mx+12, my-12,
                           text=f"{len(self.draw_pts)} pts — Enter/DblClic p/finalizar",
                           fill=UI_TEXT2, font=("Courier New", 9),
                           anchor="w", tags="dy")

        elif self.tool == "rect" and len(self.draw_pts) == 1:
            sx0, sy0 = self.w2s(*self.draw_pts[0])
            dyn_here = self.dyn_on and self._dyn_active()
            dyn_corner = self._dyn_rect_pt() if dyn_here else None

            if dyn_corner:
                # Rectángulo DYN con dimensiones exactas
                tmx, tmy = self.w2s(*dyn_corner)
                cv.create_rectangle(sx0, sy0, tmx, tmy, outline=CV_PREVIEW,
                                    fill="", width=1, tags="dy")
                wx1, wy1 = self.draw_pts[0]
                wx2, wy2 = dyn_corner
                cv.create_oval(tmx-4, tmy-4, tmx+4, tmy+4,
                               fill=CV_PREVIEW, outline="white", width=1, tags="dy")
            else:
                # Preview normal hacia el cursor
                cv.create_rectangle(sx0, sy0, mx, my, outline=CV_PREVIEW,
                                    fill="", width=1, dash=(6, 4), tags="dy")
                wx1, wy1 = self.draw_pts[0]
                wx2, wy2 = self.mouse_w
                if not dyn_here:
                    cv.create_text(mx+12, my-12,
                                   text=f"{abs(wx2-wx1):.2f}m × {abs(wy2-wy1):.2f}m",
                                   fill=UI_TEXT2, font=("Courier New", 9),
                                   anchor="w", tags="dy")

        elif self.tool == "arc" and len(self.draw_pts) >= 1:
            sx0, sy0 = self.w2s(*self.draw_pts[0])
            cv.create_oval(sx0-3,sy0-3,sx0+3,sy0+3,
                           fill=UI_ACC, outline="white", width=1, tags="dy")
            if len(self.draw_pts) == 1:
                # esperando punto en arco: muestra línea guía
                cv.create_line(sx0,sy0,mx,my, fill=CV_PREVIEW,
                               width=1, dash=(4,3), tags="dy")
                cv.create_text(mx+12, my-12, text="Punto en arco",
                               fill=UI_TEXT2, font=("Courier New",9),
                               anchor="w", tags="dy")
            elif len(self.draw_pts) == 2:
                # compute preview arc con cursor como punto final
                p1, pmid = self.draw_pts[0], self.draw_pts[1]
                p3 = self.mouse_w
                res = _circumcircle(p1, pmid, p3)
                sx1, sy1 = self.w2s(*pmid)
                cv.create_oval(sx1-3,sy1-3,sx1+3,sy1+3,
                               fill=UI_ACC, outline="white", width=1, tags="dy")
                if res:
                    pcx, pcy, pr = res
                    psa = math.degrees(math.atan2(p1[1]-pcy, p1[0]-pcx)) % 360
                    pea = math.degrees(math.atan2(p3[1]-pcy, p3[0]-pcx)) % 360
                    cross = ((pmid[0]-p1[0])*(p3[1]-p1[1])
                           - (pmid[1]-p1[1])*(p3[0]-p1[0]))
                    pccw = cross > 0
                    scx, scy = self.w2s(pcx, pcy)
                    sr = pr * self.scale
                    ext = (pea-psa)%360 or 360 if pccw else -(((psa-pea)%360) or 360)
                    cv.create_arc(scx-sr, scy-sr, scx+sr, scy+sr,
                                  start=psa, extent=ext,
                                  outline=CV_PREVIEW, fill="",
                                  width=1, style=tk.ARC,
                                  dash=(6,4), tags="dy")
                    cv.create_text(mx+12, my-12, text=f"r={pr:.3f}m",
                                   fill=UI_TEXT2, font=("Courier New",9),
                                   anchor="w", tags="dy")

        elif self.tool == "circle" and len(self.draw_pts) == 1:
            cx, cy = self.draw_pts[0]
            dyn_here = self.dyn_on and self._dyn_active()

            # Radio DYN (locked o buffer en progreso)
            dyn_r = None
            if dyn_here:
                dyn_r = self._dyn_locked[0]
                if dyn_r is None and self._dyn_buf:
                    try:
                        dyn_r = float(self._dyn_buf)
                    except ValueError:
                        pass

            r = dyn_r if dyn_r is not None else math.hypot(self.mouse_w[0]-cx, self.mouse_w[1]-cy)
            scx, scy = self.w2s(cx, cy)
            sx0, sy0 = self.w2s(cx-r, cy+r)
            sx1, sy1 = self.w2s(cx+r, cy-r)
            cv.create_oval(sx0, sy0, sx1, sy1, outline=CV_PREVIEW,
                           fill="", width=1, dash=(6,4), tags="dy")
            if not dyn_here or dyn_r is None:
                cv.create_line(scx, scy, mx, my, fill=CV_PREVIEW,
                               width=1, dash=(3,3), tags="dy")
            if not dyn_here:
                cv.create_text(mx+12, my-12, text=f"r={r:.3f}m",
                               fill=UI_TEXT2, font=("Courier New", 9),
                               anchor="w", tags="dy")

        elif self.tool == "ellipse":
            n_pts = len(self.draw_pts)
            if n_pts == 0:
                pass  # no points yet
            elif n_pts == 1:
                # Draw a simple circle preview
                cx, cy = self.draw_pts[0]
                r = math.hypot(self.mouse_w[0]-cx, self.mouse_w[1]-cy)
                scx, scy = self.w2s(cx, cy)
                sx0, sy0 = self.w2s(cx-r, cy+r); sx1, sy1 = self.w2s(cx+r, cy-r)
                cv.create_oval(sx0, sy0, sx1, sy1, outline=CV_PREVIEW,
                               fill="", width=1, dash=(6,4), tags="dy")
                cv.create_line(scx, scy, mx, my, fill=CV_PREVIEW,
                               width=1, dash=(3,3), tags="dy")
            elif n_pts == 2:
                # Draw major axis line
                cx, cy = self.draw_pts[0]
                p_maj = self.draw_pts[1]
                sx0, sy0 = self.w2s(cx, cy)
                sx1, sy1 = self.w2s(*p_maj)
                cv.create_line(sx0, sy0, sx1, sy1, fill=CV_PREVIEW,
                               width=1, tags="dy")
                # Draw ellipse preview with mouse as ry
                rx = math.hypot(p_maj[0]-cx, p_maj[1]-cy)
                angle = math.degrees(math.atan2(p_maj[1]-cy, p_maj[0]-cx))
                dx_maj = p_maj[0]-cx; dy_maj = p_maj[1]-cy
                l = math.hypot(dx_maj, dy_maj)
                if l > 1e-10:
                    ux, uy = dx_maj/l, dy_maj/l
                    px_m = self.mouse_w[0]-cx; py_m = self.mouse_w[1]-cy
                    ry = max(0.01, abs(-px_m*uy + py_m*ux))
                    tmp_e = Ellipse(cx=cx, cy=cy, rx=rx, ry=ry, angle=angle)
                    bpts = tmp_e._pts_on_boundary(72)
                    flat = self.w2s_array(bpts + [bpts[0]])
                    if len(flat) >= 4:
                        cv.create_line(*flat, fill=CV_PREVIEW, width=1,
                                       dash=(6,4), tags="dy")
                cv.create_text(mx+12, my-12,
                               text=f"ry={abs(-( self.mouse_w[0]-cx)*( -(p_maj[1]-cy)/max(1e-10,math.hypot(p_maj[0]-cx,p_maj[1]-cy))) + (self.mouse_w[1]-cy)*( (p_maj[0]-cx)/max(1e-10,math.hypot(p_maj[0]-cx,p_maj[1]-cy)))):.3f}m",
                               fill=UI_TEXT2, font=("Courier New", 9),
                               anchor="w", tags="dy")

        elif self.tool == "polygon":
            n = max(3, min(72, self._polygon_sides))
            if len(self.draw_pts) == 1:
                cx, cy = self.draw_pts[0]
                r = math.hypot(self.mouse_w[0]-cx, self.mouse_w[1]-cy)
                start_ang = math.atan2(self.mouse_w[1]-cy, self.mouse_w[0]-cx)
                pts = [(cx + r*math.cos(start_ang + 2*math.pi*i/n),
                        cy + r*math.sin(start_ang + 2*math.pi*i/n))
                       for i in range(n)]
                flat = self.w2s_array(pts + [pts[0]])
                if len(flat) >= 4:
                    cv.create_line(*flat, fill=CV_PREVIEW, width=1,
                                   dash=(6,4), tags="dy")
            cv.create_text(mx+12, my-12, text=f"POL n={n}",
                           fill=UI_TEXT2, font=("Courier New", 9),
                           anchor="w", tags="dy")

        elif self.tool == "xline":
            if len(self.draw_pts) == 1:
                # Draw infinite line preview
                p1 = self.draw_pts[0]
                dx = self.mouse_w[0] - p1[0];  dy = self.mouse_w[1] - p1[1]
                d = math.hypot(dx, dy)
                if d > 1e-10:
                    W = cv.winfo_width(); H = cv.winfo_height()
                    ux, uy = dx/d, dy/d
                    sx1, sy1 = self.w2s(*p1)
                    margin = max(W, H) + 200
                    t_vals = []
                    if abs(ux) > 1e-6:
                        for bnd in (-margin-sx1, W+margin-sx1):
                            t_vals.append(bnd/ux)
                    if abs(uy) > 1e-6:
                        for bnd in (-margin-sy1, H+margin-sy1):
                            t_vals.append(bnd/(-uy if abs(uy)>1e-6 else 1))
                    if t_vals:
                        t_min, t_max = min(t_vals), max(t_vals)
                        wxs = p1[0]+ux*t_min; wys = p1[1]+uy*t_min
                        wxe = p1[0]+ux*t_max; wye = p1[1]+uy*t_max
                        sxs, sys_ = self.w2s(wxs, wys)
                        sxe, sye  = self.w2s(wxe, wye)
                        cv.create_line(sxs, sys_, sxe, sye, fill=CV_PREVIEW,
                                       width=1, dash=(8,4), tags="dy")
            elif len(self.draw_pts) == 0:
                cv.create_text(mx+12, my-12, text="XLINE: punto de referencia",
                               fill=UI_TEXT2, font=("Courier New", 9),
                               anchor="w", tags="dy")

        elif self.tool == "cloud":
            if len(self.draw_pts) >= 1:
                preview_pts = list(self.draw_pts) + [self.mouse_w]
                flat = self.w2s_array(preview_pts)
                if len(flat) >= 4:
                    cv.create_line(*flat, fill=CV_PREVIEW, width=1,
                                   dash=(6,4), tags="dy")
            cv.create_text(mx+12, my-12,
                           text=f"CLOUD {len(self.draw_pts)} pts — Enter/DblClic p/cerrar",
                           fill=UI_TEXT2, font=("Courier New", 9),
                           anchor="w", tags="dy")

        elif self.tool == "leader":
            if len(self.draw_pts) >= 1:
                preview_pts = list(self.draw_pts) + [self.mouse_w]
                flat = self.w2s_array(preview_pts)
                if len(flat) >= 4:
                    cv.create_line(*flat, fill=CV_PREVIEW, width=1,
                                   dash=(6,4), tags="dy")
                # Draw arrowhead preview
                tip = self.w2s(*self.draw_pts[0])
                if len(self.draw_pts) >= 2:
                    base_s = self.w2s(*self.draw_pts[1])
                else:
                    base_s = (mx, my)
                adx = tip[0]-base_s[0]; ady = tip[1]-base_s[1]
                alen = math.hypot(adx, ady)
                if alen > 3:
                    uax, uay = adx/alen, ady/alen
                    arrow_px = 8
                    bx = tip[0] - uax*arrow_px; by = tip[1] - uay*arrow_px
                    px2 = -uay*arrow_px*0.35; py2 = uax*arrow_px*0.35
                    cv.create_polygon(tip[0], tip[1],
                                      bx+px2, by+py2, bx-px2, by-py2,
                                      fill=CV_PREVIEW, outline="", tags="dy")
            cv.create_text(mx+12, my-12,
                           text=f"LEADER {len(self.draw_pts)} pts — Enter p/finalizar",
                           fill=UI_TEXT2, font=("Courier New", 9),
                           anchor="w", tags="dy")

        # ── Info flotante: contador de puntos para tools multi-click ─────
        _n = len(self.draw_pts)
        _info = None
        if self.tool == "leader" and _n >= 2:
            _info = f"LÍDER  {_n} pts — Enter para terminar"
        elif self.tool == "cloud" and _n >= 3:
            _info = f"NUBE  {_n} pts — Enter para cerrar"
        elif self.tool in ("polyline", "spline") and _n >= 2:
            _info = f"{'POLILÍNEA' if self.tool == 'polyline' else 'SPLINE'}  {_n} pts — Enter para terminar"
        elif self.tool == "xline" and _n == 1:
            x1, y1 = self.draw_pts[0]
            x2, y2 = self.mouse_w
            ang = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 360
            _info = f"XLINE  ∠ {ang:.1f}°"
        if _info:
            cv = self.canvas
            ox, oy = self._mouse_sx + 16, self._mouse_sy + 16
            cv.create_text(ox, oy, text=_info,
                           fill="#94A3B8",
                           font=("Courier New", 9),
                           anchor="nw", tags="dy")

    def _draw_image_preview(self):
        """Preview de imagen durante image_place e image_width."""
        cv  = self.canvas
        m   = self._op_mode
        asp = self._op_data.get("img_asp", 1.0)

        if m == "image_place":
            # Rectángulo fantasma centrado en el cursor mostrando aspecto
            pt = (self.snap_pt[0], self.snap_pt[1]) if self.snap_pt else self.mouse_w
            wx, wy = pt
            w_m = self._op_data.get("img_w", 1.0)
            h_m = w_m / asp if asp > 1e-9 else w_m
            sx0, sy0 = self.w2s(wx,       wy)
            sx1, sy1 = self.w2s(wx + w_m, wy + h_m)
            cv.create_rectangle(sx0, sy1, sx1, sy0,
                                 outline="#A78BFA", dash=(4, 3), width=1, tags="dy")
            cv.create_line(sx0, sy1, sx1, sy0, fill="#A78BFA",
                           dash=(4, 3), width=1, tags="dy")
            cv.create_line(sx0, sy0, sx1, sy1, fill="#A78BFA",
                           dash=(4, 3), width=1, tags="dy")

        elif m == "image_width":
            x0 = self._op_data.get("img_x", 0.0)
            y0 = self._op_data.get("img_y", 0.0)
            try:
                w_m = float(self._dyn_buf) if self._dyn_buf else self._op_data.get("img_w", 1.0)
            except Exception:
                w_m = self._op_data.get("img_w", 1.0)
            h_m = w_m / asp if asp > 1e-9 else w_m
            sx0, sy0 = self.w2s(x0,        y0)
            sx1, sy1 = self.w2s(x0 + w_m,  y0 + h_m)
            cv.create_rectangle(sx0, sy1, sx1, sy0,
                                 outline="#A78BFA", width=1, tags="dy")
            cv.create_line(sx0, sy1, sx1, sy0, fill="#6D28D9",
                           dash=(4, 3), width=1, tags="dy")
            cv.create_line(sx0, sy0, sx1, sy1, fill="#6D28D9",
                           dash=(4, 3), width=1, tags="dy")
            # Dimensión W y H
            cx = (sx0 + sx1) / 2; cy = (sy0 + sy1) / 2
            cv.create_text(cx, cy, text=f"{w_m:.3f} × {h_m:.3f} m",
                           fill="#A78BFA", font=("Courier New", 9, "bold"),
                           anchor="center", tags="dy")

    def _draw_op_preview(self):
        """Preview de entidades fantasma durante operaciones."""
        cv = self.canvas
        mx, my = self.mouse_w

        # ── OFFSET fase 2: preview en tiempo real según lado del cursor ──
        if self._op_mode == "offset_side":
            best = self._op_data.get("offset_entity")
            d    = self._op_data.get("offset_dist", 0.5)
            if best:
                # Resaltar la entidad fuente en azul
                self._render_entity(cv, best, "#4A9EFF", 2, "dy")
                # Calcular y dibujar la entidad offset fantasma
                if isinstance(best, Line):
                    ghost = _offset_line(best, d, mx, my)
                elif isinstance(best, Circle):
                    ghost = _offset_circle(best, d, mx, my)
                elif isinstance(best, Polyline):
                    ghost = _offset_polyline(best, d, mx, my)
                else:
                    ghost = None
                if ghost:
                    self._render_entity(cv, ghost, CV_GHOST, 2, "dy")
                    # Etiqueta con la distancia sobre el cursor
                    smx, smy = self.w2s(mx, my)
                    cv.create_text(smx + 14, smy - 14,
                                   text=f"d = {d:.3f}",
                                   fill=CV_GHOST,
                                   font=("Courier New", 9, "bold"),
                                   anchor="w", tags="dy")
            return

        if self._op_mode in ("move_dest", "copy_dest"):
            if len(self._op_pts) < 1: return
            bx, by = self._op_pts[0]
            # Usar punto DYN exacto si está disponible
            dyn_here = self.dyn_on and self._dyn_active()
            dyn_target = self._dyn_pt() if dyn_here else None
            tx, ty = dyn_target if dyn_target else (mx, my)
            dx, dy = tx-bx, ty-by
            for e in self._op_sel:
                self._render_entity(cv, e.translated(dx, dy), CV_GHOST, 1, "dy")
            sbx, sby = self.w2s(bx, by); stx, sty = self.w2s(tx, ty)
            cv.create_line(sbx, sby, stx, sty, fill=CV_GHOST, width=1, dash=(4,3), tags="dy")
            if dyn_target:
                cv.create_oval(stx-4, sty-4, stx+4, sty+4,
                               fill=CV_GHOST, outline="white", width=1, tags="dy")
            if not dyn_here:
                cv.create_text(stx+12, sty-12, text=f"Δ {dx:.3f},{dy:.3f}",
                               fill=UI_WARN, font=("Courier New",9), anchor="w", tags="dy")

        elif self._op_mode == "rotate_angle" and "base" in self._op_data:
            bx, by = self._op_data["base"]
            # Ángulo DYN o desde cursor
            dyn_here = self.dyn_on and self._dyn_active()
            if dyn_here and self._dyn_locked[0] is not None:
                ang = self._dyn_locked[0]
            elif dyn_here and self._dyn_buf:
                try: ang = float(self._dyn_buf)
                except: ang = math.degrees(math.atan2(my-by, mx-bx))
            else:
                ang = math.degrees(math.atan2(my-by, mx-bx))
            for e in self._op_sel:
                self._render_entity(cv, e.rotated(bx, by, ang), CV_GHOST, 1, "dy")
            sbx, sby = self.w2s(bx, by); smx, smy = self.w2s(mx, my)
            cv.create_line(sbx, sby, smx, smy, fill=CV_GHOST, width=1, dash=(4,3), tags="dy")
            if not dyn_here:
                cv.create_text(smx+12, smy-12, text=f"∠ {ang:.1f}°",
                               fill=UI_WARN, font=("Courier New",9), anchor="w", tags="dy")

        elif self._op_mode == "scale_factor" and "base" in self._op_data:
            bx, by = self._op_data["base"]
            # Factor DYN o desde cursor
            dyn_here = self.dyn_on and self._dyn_active()
            if dyn_here and self._dyn_locked[0] is not None:
                f = max(0.001, self._dyn_locked[0])
            elif dyn_here and self._dyn_buf:
                try: f = max(0.001, float(self._dyn_buf))
                except: f = max(0.001, math.hypot(mx-bx, my-by))
            else:
                f = max(0.001, math.hypot(mx-bx, my-by))
            for e in self._op_sel:
                self._render_entity(cv, e.scaled(bx, by, f), CV_GHOST, 1, "dy")
            sbx, sby = self.w2s(bx, by); smx, smy = self.w2s(mx, my)
            cv.create_line(sbx, sby, smx, smy, fill=CV_GHOST, width=1, dash=(4,3), tags="dy")
            if not dyn_here:
                cv.create_text(smx+12, smy-12, text=f"× {f:.3f}",
                               fill=UI_WARN, font=("Courier New",9), anchor="w", tags="dy")

        # ── COTAS — preview vivo ──────────────────────────────────────
        elif self._op_mode == "dim_lp2" and len(self._op_pts) >= 1:
            smx, smy   = self.w2s(*self.mouse_w)
            ss1x, ss1y = self.w2s(*self._op_pts[0])
            cv.create_line(ss1x, ss1y, smx, smy, fill=UI_WARN,
                           width=1, dash=(4, 3), tags="dy")
            # Marcador en P1 ya capturado
            cv.create_rectangle(ss1x - 4, ss1y - 4, ss1x + 4, ss1y + 4,
                                fill="#F472B6", outline="", tags="dy")

        elif self._op_mode == "dim_lpos" and len(self._op_pts) >= 2:
            dt = self._op_data.get("dim_type", "H")
            _d = Dimension(p1=self._op_pts[0], p2=self._op_pts[1],
                           pos=self.mouse_w, dim_type=dt)
            self._render_dim(cv, _d, UI_WARN, 1, "dy")
            # Marcadores en P1 y P2 ya capturados
            for _wp in self._op_pts[:2]:
                _sx, _sy = self.w2s(*_wp)
                cv.create_rectangle(_sx - 4, _sy - 4, _sx + 4, _sy + 4,
                                    fill="#F472B6", outline="", tags="dy")

        elif self._op_mode == "dim_r_pt" and "p1" in self._op_data:
            dt = self._op_data.get("dim_type", "R")
            _d = Dimension(p1=self._op_data["p1"], p2=self.mouse_w,
                           pos=self.mouse_w, dim_type=dt)
            self._render_dim(cv, _d, UI_WARN, 1, "dy")

        elif self._op_mode == "dim_ang_p1" and "cen" in self._op_data:
            smx, smy = self.w2s(*self.mouse_w)
            scx, scy = self.w2s(*self._op_data["cen"])
            cv.create_line(scx, scy, smx, smy, fill=UI_WARN,
                           width=1, dash=(4, 3), tags="dy")
            # Marcador en centro
            cv.create_oval(scx - 5, scy - 5, scx + 5, scy + 5,
                           fill="", outline="#67E8F9", width=2, tags="dy")

        elif self._op_mode == "dim_arc_pos" and "p1" in self._op_data:
            p1  = self._op_data["p1"]
            p2  = self._op_data["p2"]
            cen = self._op_data["cen"]
            _d = Dimension(p1=p1, p2=p2, pos=cen, dim_type="ARC",
                           text_pos=self.mouse_w)
            self._render_dim(cv, _d, UI_WARN, 1, "dy")

        elif self._op_mode == "dim_ord_p2" and "p1" in self._op_data:
            p1 = self._op_data["p1"]
            _d = Dimension(p1=p1, p2=self.mouse_w,
                           pos=self.mouse_w, dim_type="ORD")
            self._render_dim(cv, _d, UI_WARN, 1, "dy")

        elif self._op_mode == "dim_chain_next" and "base" in self._op_data:
            base = self._op_data["base"]
            modo = self._op_data.get("modo", "continue")
            if modo == "continue":
                p1 = base.p2
                pos = self._proyectar_pos_misma_linea(base, self.mouse_w)
            else:
                p1 = base.p1
                pos = self._desplazar_pos_baseline(base, self.mouse_w)
            _d = Dimension(p1=p1, p2=self.mouse_w, pos=pos,
                           dim_type=base.dim_type, style=base.style)
            self._render_dim(cv, _d, UI_WARN, 1, "dy")

        elif self._op_mode == "dim_ang_p2" and "cen" in self._op_data and len(self._op_pts) >= 1:
            cen = self._op_data["cen"]
            sp1x, sp1y = self.w2s(*self._op_pts[0])
            scx, scy   = self.w2s(*cen)
            smx, smy   = self.w2s(*self.mouse_w)
            cv.create_line(scx, scy, sp1x, sp1y, fill=UI_WARN, width=1, tags="dy")
            cv.create_line(scx, scy, smx, smy, fill=UI_WARN,
                           width=1, dash=(4, 3), tags="dy")
            _d = Dimension(p1=cen, p2=self._op_pts[0],
                           pos=self.mouse_w, dim_type="ANG")
            self._render_dim(cv, _d, UI_WARN, 1, "dy")
            # Marcador en centro y en P1
            cv.create_oval(scx - 5, scy - 5, scx + 5, scy + 5,
                           fill="", outline="#67E8F9", width=2, tags="dy")
            cv.create_rectangle(sp1x - 4, sp1y - 4, sp1x + 4, sp1y + 4,
                                fill="#67E8F9", outline="", tags="dy")

        elif self._op_mode == "hatch_pts" and self._op_pts:
            # Preview del polígono de hatch en construcción
            spts_prev = [self.w2s(x, y) for x, y in self._op_pts]
            smx, smy = self.w2s(*self.mouse_w)
            # Dibujar segmentos ya definidos
            for i in range(len(spts_prev) - 1):
                cv.create_line(*spts_prev[i], *spts_prev[i+1],
                               fill=UI_WARN, width=1, tags="dy")
            # Línea dinámica desde el último punto al cursor
            cv.create_line(*spts_prev[-1], smx, smy,
                           fill=UI_WARN, width=1, dash=(4, 3), tags="dy")
            # Línea de cierre (cursor al primer punto)
            if len(spts_prev) >= 2:
                cv.create_line(smx, smy, *spts_prev[0],
                               fill=UI_WARN, width=1, dash=(2, 4), tags="dy")
            # Puntos
            for spx, spy in spts_prev:
                cv.create_oval(spx-3, spy-3, spx+3, spy+3,
                               fill=UI_WARN, outline="", tags="dy")

        elif self._op_mode == "insert_place":
            sx, sy = self.w2s(*self.mouse_w)
            self._draw_insert_preview(cv, sx, sy)

        elif self._op_mode == "mirror_p2" and "p1" in self._op_data:
            ax, ay = self._op_data["p1"]
            # Punto DYN o cursor
            dyn_here = self.dyn_on and self._dyn_active()
            dyn_target = self._dyn_pt() if dyn_here else None
            bx, by = dyn_target if dyn_target else (mx, my)
            sax, say = self.w2s(ax, ay); sbx, sby = self.w2s(bx, by)
            cv.create_line(sax, say, sbx, sby, fill=UI_WARN,
                           width=1, dash=(6,3), tags="dy")
            if abs(bx-ax) > 1e-9 or abs(by-ay) > 1e-9:
                for e in self._op_sel:
                    self._render_entity(cv, e.mirrored(ax, ay, bx, by), CV_GHOST, 1, "dy")

    def _draw_kbd_buf_overlay(self, cv):
        """Panel unificado junto al cursor: coordenadas + buffer de teclado + hint.

        Layout (de arriba a abajo):
          ┌──────────────────────────────────────┐
          │  X  62.022    Y   5.477              │  ← fila coords (dim)
          ├ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┤  ← separador
          │  ▶ REC▌                               │  ← fila comando (bright)
          │    → RECTÁNGULO                        │  ← fila hint (si resuelto)
          └──────────────────────────────────────┘

        Cuando este panel está visible, los textos X/Y persistentes del
        crosshair (dy_c) se ocultan para evitar duplicidad — ver _draw_cursor.
        """
        ox = self._mouse_sx      # posición del cursor en pantalla
        oy = self._mouse_sy
        x, y = self.mouse_w      # coordenadas mundo actuales

        # ── Resolver alias → nombre legible ───────────────────────────
        token  = self._kbd_buf.upper()
        accion = _CMD_ALIASES.get(token)
        _NAMES = {
            # ── Herramientas de dibujo ─────────────────────────────
            "line":         "LÍNEA",
            "polyline":     "POLILÍNEA",
            "spline":       "SPLINE",
            "rect":         "RECTÁNGULO",
            "circle":       "CÍRCULO",
            "arc":          "ARCO",
            "text":         "TEXTO",
            "ellipse":      "ELIPSE",
            "polygon":      "POLÍGONO",
            "xline":        "LÍNEA INF.",
            "cloud":        "NUBE REVISIÓN",
            "leader_cmd":   "LÍDER",
            # ── Edición básica ─────────────────────────────────────
            "select":       "SELECCIONAR",
            "erase":        "BORRAR",
            "move":         "MOVER",
            "copy":         "COPIAR",
            "rotate":       "ROTAR",
            "scale":        "ESCALAR",
            "mirror":       "ESPEJO",
            "offset":       "PARALELA",
            "trim":         "RECORTAR",
            "extend":       "EXTENDER",
            "fillet":       "EMPALME",
            "chamfer":      "CHAFLÁN",
            "array":        "ARRAY",
            "align_cmd":    "ALINEAR",
            "break_cmd":    "PARTIR",
            "explode":      "DESCOMPONER",
            # ── Relleno / Bloques / Insertar ──────────────────────
            "hatch":        "RELLENO",
            "insert":       "INSERTAR",
            "block_cmd":    "DEF. BLOQUE",
            "image_cmd":    "INSERTAR IMAGEN",
            # ── Capas ─────────────────────────────────────────────
            "layer_cmd":    "CAPAS",
            "layer_iso":    "AISLAR CAPA",
            "layer_off":    "APAGAR CAPA",
            "layer_on":     "ENCENDER CAPA",
            "layer_lock":   "BLOQUEAR CAPA",
            "layer_unlock": "DESBLOQUEAR CAPA",
            "layer_mcur":   "CAPA ACTUAL",
            "laymcur":      "CAPA ACTUAL",
            # ── Propiedades ───────────────────────────────────────
            "properties":   "PROPIEDADES",
            "matchprop":    "COPIAR PROPS",
            # ── Medición / Información ────────────────────────────
            "dist":         "DISTANCIA",
            "measure":      "MEDIR",
            "area_cmd":     "ÁREA",
            "list_ent":     "LISTAR ENT.",
            "id_point":     "ID PUNTO",
            # ── Cotas ─────────────────────────────────────────────
            "dim_h":        "COTA HORIZ.",
            "dim_v":        "COTA VERT.",
            "dim_a":        "COTA ALIN.",
            "dim_ang":      "COTA ANGULAR",
            "dim_r":        "COTA RADIO",
            "dim_d":        "COTA DIÁMETRO",
            "dim_arc_len":  "COTA ARC. LONG.",
            "dim_co":       "COTA CONTINUA",
            "dim_ba":       "COTA BASE",
            "dim_sp":       "COTA ESPACIADO",
            "dim_ord":      "COTA ORDENADA",
            # ── Vista ─────────────────────────────────────────────
            "zoom_cmd":     "ZOOM",
            "zoom_e":       "ZOOM EXTENSIÓN",
            "zoom_a":       "ZOOM TODO",
            "regen":        "REGENERAR",
            "pan_cmd":      "ENCUADRAR",
            # ── Láminas / Layout ──────────────────────────────────
            "layout_new":   "NUEVA LÁMINA",
            "layout_model": "ESPACIO MODELO",
            "layout_ps":    "ESPACIO PAPEL",
            "layout_setup": "CONFIG. PÁGINA",
            # ── Guardar / Abrir / Export ──────────────────────────
            "save":         "GUARDAR",
            "saveas":       "GUARDAR COMO",
            "open":         "ABRIR",
            "new_dwg":      "NUEVO PLANO",
            "dxf":          "EXPORTAR DXF",
            "png":          "EXPORTAR PNG",
            "pdf":          "EXPORTAR PDF",
            # ── Config / Toggles ──────────────────────────────────
            "toggle_grid":  "GRILLA ON/OFF",
            "toggle_snap":  "SNAP ON/OFF",
            "toggle_ortho": "ORTO ON/OFF",
            # ── Deshacer / Rehacer ────────────────────────────────
            "undo":         "DESHACER",
            "redo":         "REHACER",
            # ── Ayuda ─────────────────────────────────────────────
            "help":         "AYUDA",
        }
        hint = _NAMES.get(accion, accion.upper()) if accion else None

        # ── Métricas del panel ────────────────────────────────────────
        PW      = 196   # ancho del panel
        PAD_X   = 8     # padding horizontal interior
        PAD_Y   = 5     # padding vertical interior
        R_COORD = 15    # altura fila coordenadas
        R_CMD   = 18    # altura fila comando
        R_HINT  = 14    # altura fila hint
        GAP     = 3     # espacio entre filas

        ph = PAD_Y + R_COORD + GAP + 2 + GAP + R_CMD + PAD_Y
        if hint:
            ph += GAP + R_HINT

        # Posición: a la derecha del cursor, arriba
        px = ox + 22
        py = oy - ph - 6
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4:   px = ox - PW - 8
        if py < 4:             py = oy + 8

        # ── Fondo y borde del panel ───────────────────────────────────
        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill="#0D1117", outline="#334155", width=1, tags="dy")

        # ── Fila 1: coordenadas (color tenue) ─────────────────────────
        coord_y = py + PAD_Y
        cv.create_text(px + PAD_X, coord_y,
                       text=f"X {x:9.3f}     Y {y:9.3f}",
                       fill="#4B5563",
                       font=("Courier New", 9),
                       anchor="nw", tags="dy")

        # ── Separador fino ────────────────────────────────────────────
        sep_y = coord_y + R_COORD + GAP
        cv.create_line(px + 2, sep_y, px + PW - 2, sep_y,
                       fill="#1E293B", tags="dy")

        # ── Fila 2: comando con cursor de texto ───────────────────────
        cmd_y  = sep_y + GAP + 2
        bg_cmd = "#1E3A5F" if hint else "#131C2E"
        cv.create_rectangle(px + 1, cmd_y - 1,
                            px + PW - 1, cmd_y + R_CMD + 1,
                            fill=bg_cmd, outline="", tags="dy")
        cv.create_text(px + PAD_X, cmd_y + 1,
                       text="▶ " + self._kbd_buf + "▌",
                       fill="#60A5FA",
                       font=("Courier New", 11, "bold"),
                       anchor="nw", tags="dy")

        # ── Fila 3: nombre del comando resuelto ───────────────────────
        if hint:
            hint_y = cmd_y + R_CMD + GAP
            cv.create_text(px + PAD_X + 10, hint_y,
                           text=f"→  {hint}",
                           fill="#94A3B8",
                           font=("Courier New", 9),
                           anchor="nw", tags="dy")

    def _draw_mirror_keep_panel(self, cv):
        """Panel flotante de confirmación para MIRROR: ¿borrar originales? [S/N]."""
        ox = self._mouse_sx
        oy = self._mouse_sy
        PW = 244; PAD_X = 10; PAD_Y = 6
        R1 = 16; R2 = 14; GAP = 3
        ph = PAD_Y + R1 + GAP + R2 + PAD_Y
        px = ox + 22; py = oy - ph - 8
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = ox - PW - 8
        if py < 4:           py = oy + 8

        # Fondo naranja-oscuro para distinguirlo del panel DYN azul
        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill="#1C1208", outline="#F59E0B", width=1, tags="dy")

        # Fila 1: título
        cv.create_text(px + PAD_X, py + PAD_Y,
                       text="ESPEJO — ¿Borrar originales?",
                       fill="#F59E0B",
                       font=("Courier New", 10, "bold"),
                       anchor="nw", tags="dy")

        # Fila 2: opciones
        cv.create_text(px + PAD_X, py + PAD_Y + R1 + GAP,
                       text="[S / Y]  Sí — borrar       [N]  No — copiar        Enter = No",
                       fill="#94A3B8",
                       font=("Courier New", 8),
                       anchor="nw", tags="dy")

    def _draw_trim_extend_panel(self, cv):
        """Panel flotante para TRIM / EXTEND mientras están activos."""
        m      = self._op_mode
        is_trim = (m == "trim_obj")
        ox, oy  = self._mouse_sx, self._mouse_sy
        PW = 248; PAD_X = 10; PAD_Y = 6
        R1 = 16; R2 = 14; GAP = 3
        ph = PAD_Y + R1 + GAP + R2 + PAD_Y
        px = ox + 22; py = oy - ph - 8
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = ox - PW - 8
        if py < 4:            py = oy + 8

        n_ops  = self._op_data.get("n_ops", 0)
        accent = "#22C55E" if is_trim else "#F97316"   # verde TRIM / naranja EXTEND
        bg     = "#071209" if is_trim else "#120A04"

        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill=bg, outline=accent, width=1, tags="dy")
        title = (f"TRIM  ({n_ops} cortes)" if is_trim
                 else f"EXTEND  ({n_ops} extensiones)")
        cv.create_text(px + PAD_X, py + PAD_Y,
                       text=title, fill=accent,
                       font=("Courier New", 10, "bold"),
                       anchor="nw", tags="dy")
        action = ("clic en segmento → cortar        Enter / Esc = salir"
                  if is_trim else
                  "clic en extremo → extender        Enter / Esc = salir")
        cv.create_text(px + PAD_X, py + PAD_Y + R1 + GAP,
                       text=action, fill="#94A3B8",
                       font=("Courier New", 8),
                       anchor="nw", tags="dy")

    def _draw_hatch_panel(self, cv):
        """Panel flotante para HATCH durante la captura de vértices."""
        ox, oy = self._mouse_sx, self._mouse_sy
        pat    = self._op_data.get("hatch_pattern", "ANSI31")
        scale  = self._op_data.get("hatch_scale",   0.5)
        ang    = self._op_data.get("hatch_angle",   45.0)
        n_pts  = len(self._op_pts)

        PW = 228; PAD_X = 10; PAD_Y = 6
        R1 = 16; R2 = 14; R3 = 12; GAP = 3
        ph = PAD_Y + R1 + GAP + R2 + GAP + R3 + PAD_Y
        px = ox + 22; py = oy - ph - 8
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = ox - PW - 8
        if py < 4:            py = oy + 8

        accent = "#A78BFA"   # violeta HATCH
        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill="#0D0A1A", outline=accent, width=1, tags="dy")
        cv.create_text(px + PAD_X, py + PAD_Y,
                       text=f"HATCH  {pat}",
                       fill=accent, font=("Courier New", 10, "bold"),
                       anchor="nw", tags="dy")
        cv.create_text(px + PAD_X, py + PAD_Y + R1 + GAP,
                       text=f"escala {scale:.3f}   ∠ {ang:.0f}°",
                       fill="#94A3B8", font=("Courier New", 9),
                       anchor="nw", tags="dy")
        cv.create_text(px + PAD_X, py + PAD_Y + R1 + GAP + R2 + GAP,
                       text=f"vértices: {n_pts}    Enter = cerrar    Esc = cancelar",
                       fill="#64748B", font=("Courier New", 8),
                       anchor="nw", tags="dy")

    def _draw_matchprop_panel(self, cv):
        """Panel flotante para MATCHPROP — indica paso origen / destino."""
        m      = self._op_mode
        ox, oy = self._mouse_sx, self._mouse_sy
        PW = 240; PAD_X = 10; PAD_Y = 6
        R1 = 16; R2 = 14; GAP = 3
        ph = PAD_Y + R1 + GAP + R2 + PAD_Y
        px = ox + 22; py = oy - ph - 8
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = ox - PW - 8
        if py < 4:            py = oy + 8

        accent = "#38BDF8"   # celeste MATCHPROP
        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill="#060E14", outline=accent, width=1, tags="dy")

        if m == "matchprop_src":
            title  = "MATCHPROP — paso 1 / 2"
            detail = "clic en objeto ORIGEN para copiar propiedades"
        else:  # matchprop_dst
            src_layer = self._op_data.get("src_layer", "?")
            n_dst     = self._op_data.get("n_dst", 0)
            title  = f"MATCHPROP → capa: {src_layer}   ({n_dst} aplicados)"
            detail = "clic en objeto DESTINO        Enter / Esc = terminar"

        cv.create_text(px + PAD_X, py + PAD_Y,
                       text=title, fill=accent,
                       font=("Courier New", 10, "bold"),
                       anchor="nw", tags="dy")
        cv.create_text(px + PAD_X, py + PAD_Y + R1 + GAP,
                       text=detail, fill="#94A3B8",
                       font=("Courier New", 8),
                       anchor="nw", tags="dy")

    def _draw_measure_panel(self, cv):
        """Panel flotante para MEASURE — muestra distancia acumulada y segmento vivo."""
        m      = self._op_mode
        ox, oy = self._mouse_sx, self._mouse_sy

        # Segmento vivo: de último punto al cursor actual
        live_d = 0.0
        if m == "measure_next" and self._measure_last_pt is not None:
            pt = (self.snap_pt[0], self.snap_pt[1]) if self.snap_pt else self.mouse_w
            lx, ly = self._measure_last_pt
            live_d = math.hypot(pt[0] - lx, pt[1] - ly)

        total  = self._measure_total
        n_segs = self._op_data.get("n_segs", 0)

        # Layout: 3 filas cuando hay acumulado, 2 filas en el primer punto
        show_total = (m == "measure_next")
        PW = 228; PAD_X = 10; PAD_Y = 6
        R1 = 16; GAP = 3
        n_rows = 3 if show_total else 2
        ph = PAD_Y + n_rows * R1 + (n_rows - 1) * GAP + PAD_Y
        px = ox + 22; py = oy - ph - 8
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = ox - PW - 8
        if py < 4:            py = oy + 8

        accent = "#FCD34D"   # amarillo MEASURE
        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill="#12100A", outline=accent, width=1, tags="dy")
        # Fila título
        cv.create_text(px + PAD_X, py + PAD_Y,
                       text=f"MEASURE  ({n_segs} segmentos)",
                       fill=accent, font=("Courier New", 10, "bold"),
                       anchor="nw", tags="dy")
        y2 = py + PAD_Y + R1 + GAP
        if show_total:
            cv.create_text(px + PAD_X, y2,
                           text=f"segmento:  {live_d:.4f} m",
                           fill="#94A3B8", font=("Courier New", 9),
                           anchor="nw", tags="dy")
            cv.create_text(px + PAD_X, y2 + R1 + GAP,
                           text=f"total:     {total + live_d:.4f} m",
                           fill="#F0F9FF", font=("Courier New", 9, "bold"),
                           anchor="nw", tags="dy")
        else:
            cv.create_text(px + PAD_X, y2,
                           text="clic en 1er punto para iniciar medición",
                           fill="#64748B", font=("Courier New", 8),
                           anchor="nw", tags="dy")

    def _draw_dist_ruler(self, cv):
        """Regla en tiempo real para el comando DIST (dist_p2).

        Dibuja:
        • Línea punteada del punto-1 al cursor con marcas de tick cada metro
        • Etiqueta de distancia centrada sobre la línea
        • Panel flotante junto al cursor: dist, Δx, Δy, ángulo
        """
        if self._op_mode != "dist_p2":
            return
        if not self._op_pts:
            return

        p1x, p1y = self._op_pts[0]          # primer punto en coords mundo
        # Punto vivo: snap si disponible, si no cursor crudo
        pt = (self.snap_pt[0], self.snap_pt[1]) if self.snap_pt else self.mouse_w
        p2x, p2y = pt

        dist = math.hypot(p2x - p1x, p2y - p1y)
        if dist < 1e-9:
            return

        dx = p2x - p1x
        dy = p2y - p1y
        ang_deg = math.degrees(math.atan2(dy, dx))

        # ── Coordenadas pantalla ──────────────────────────────────────────
        sx1, sy1 = self.w2s(p1x, p1y)
        sx2, sy2 = self.w2s(p2x, p2y)

        ACCENT = "#FCD34D"      # amarillo regla
        DIM_C  = "#94A3B8"      # gris frio tick/etiqueta

        # ── Línea punteada principal ──────────────────────────────────────
        cv.create_line(sx1, sy1, sx2, sy2,
                       fill=ACCENT, width=1, dash=(6, 4), tags="dy")

        # ── Tick cada 1 m (solo si hay espacio entre ticks en pantalla) ───
        px_per_m = self.scale          # px por metro
        if px_per_m >= 12:             # solo cuando los ticks no se apiñan
            n_ticks = int(dist)        # número de metros completos
            ux = dx / dist; uy = dy / dist          # vector unitario
            # perpendicular (±8 px)
            perp_scale = 8.0 / (self.scale or 1.0)  # 8 px → unidades mundo
            px_w = -uy * perp_scale
            py_w =  ux * perp_scale
            for k in range(1, n_ticks + 1):
                tx = p1x + ux * k;  ty = p1y + uy * k
                tsx, tsy = self.w2s(tx, ty)
                t1x, t1y = self.w2s(tx + px_w, ty + py_w)
                t2x, t2y = self.w2s(tx - px_w, ty - py_w)
                cv.create_line(t1x, t1y, t2x, t2y,
                               fill=DIM_C, width=1, tags="dy")
                # etiqueta numérica del tick (cada 5 m o si muy espaciados)
                if px_per_m >= 60 or k % 5 == 0:
                    cv.create_text(tsx, tsy - 10,
                                   text=str(k),
                                   fill=DIM_C, font=("Courier New", 7),
                                   tags="dy")

        # ── Etiqueta de distancia centrada sobre la línea ─────────────────
        mx = (sx1 + sx2) / 2
        my = (sy1 + sy2) / 2
        # Offset perpendicular de 14 px hacia arriba de la línea
        line_ang_rad = math.atan2(sy2 - sy1, sx2 - sx1)
        off_x = -math.sin(line_ang_rad) * 14
        off_y =  math.cos(line_ang_rad) * 14

        lbl_dist = f"{dist:.3f} m"
        # Sombra oscura para legibilidad sobre cualquier fondo
        for ddx, ddy in ((-1, -1), (1, 1), (-1, 1), (1, -1)):
            cv.create_text(mx + off_x + ddx, my + off_y + ddy,
                           text=lbl_dist,
                           fill="#0A0A0A", font=("Courier New", 9, "bold"),
                           angle=-ang_deg % 360,
                           tags="dy")
        cv.create_text(mx + off_x, my + off_y,
                       text=lbl_dist,
                       fill=ACCENT, font=("Courier New", 9, "bold"),
                       angle=-ang_deg % 360,
                       tags="dy")

        # ── Marcadores de punto inicial (cuadrado) y cursor (cruz) ───────
        S = 5
        cv.create_rectangle(sx1 - S, sy1 - S, sx1 + S, sy1 + S,
                             outline=ACCENT, fill="", width=1, tags="dy")

        # ── Panel flotante junto al cursor ────────────────────────────────
        ox, oy = self._mouse_sx, self._mouse_sy
        PW = 210; PAD_X = 10; PAD_Y = 6
        R1 = 14; GAP = 2
        rows = [
            (f"dist:   {dist:.4f} m",   "#F0F9FF"),
            (f"Δx:     {dx:+.4f} m",    DIM_C),
            (f"Δy:     {dy:+.4f} m",    DIM_C),
            (f"ángulo: {ang_deg:.2f}°",  DIM_C),
        ]
        ph = PAD_Y + len(rows) * R1 + (len(rows) - 1) * GAP + PAD_Y
        px = ox + 22; py = oy - ph - 8
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = ox - PW - 8
        if py < 4:            py = oy + 8

        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill="#0C0C08", outline=ACCENT, width=1, tags="dy")
        for i, (txt, col) in enumerate(rows):
            ry = py + PAD_Y + i * (R1 + GAP)
            cv.create_text(px + PAD_X, ry,
                           text=txt, fill=col,
                           font=("Courier New", 9,
                                 "bold" if i == 0 else "normal"),
                           anchor="nw", tags="dy")

    def _draw_align_panel(self, cv):
        """Panel flotante para ALIGN — indicador de paso 1-4."""
        m      = self._op_mode
        ox, oy = self._mouse_sx, self._mouse_sy

        _STEPS = {
            "align_sp1": (1, "1er punto ORIGEN"),
            "align_sp2": (2, "2do punto ORIGEN"),
            "align_dp1": (3, "1er punto DESTINO"),
            "align_dp2": (4, "2do punto DESTINO"),
        }
        step, desc = _STEPS.get(m, (1, ""))

        PW = 224; PAD_X = 10; PAD_Y = 6
        R1 = 16; R2 = 14; R3 = 12; GAP = 3
        ph = PAD_Y + R1 + GAP + R2 + GAP + R3 + PAD_Y
        px = ox + 22; py = oy - ph - 8
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = ox - PW - 8
        if py < 4:            py = oy + 8

        accent = "#FB923C"   # naranja cálido ALIGN
        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill="#120900", outline=accent, width=1, tags="dy")

        # Título con número de paso
        cv.create_text(px + PAD_X, py + PAD_Y,
                       text=f"ALIGN — paso {step} / 4",
                       fill=accent, font=("Courier New", 10, "bold"),
                       anchor="nw", tags="dy")
        # Descripción del paso
        cv.create_text(px + PAD_X, py + PAD_Y + R1 + GAP,
                       text=desc,
                       fill="#F0F9FF", font=("Courier New", 9),
                       anchor="nw", tags="dy")
        # Barra de progreso con 4 puntos
        dot_y = py + PAD_Y + R1 + GAP + R2 + GAP + R3 // 2 - 1
        dot_spacing = (PW - PAD_X * 2) // 4
        for i in range(1, 5):
            dx = px + PAD_X + (i - 1) * dot_spacing + dot_spacing // 2
            col = accent if i <= step else "#1E293B"
            r = 5 if i == step else 3
            cv.create_oval(dx - r, dot_y - r, dx + r, dot_y + r,
                           fill=col, outline="", tags="dy")
        # Línea que conecta los puntos
        x0 = px + PAD_X + dot_spacing // 2
        x1 = px + PAD_X + 3 * dot_spacing + dot_spacing // 2
        cv.create_line(x0, dot_y, x1, dot_y,
                       fill="#1E293B", width=1, tags="dy")
        cv.tag_lower("dy")   # línea detrás de los puntos ya dibujados

    def _draw_dim_step_panel(self, cv):
        """Panel flotante DIM — indicador de paso y valor en tiempo real.

        Cubre modos lineales (H/V/A), angular, radio/diámetro.
        El panel se posiciona a la derecha y arriba del cursor, igual que
        los demás paneles informativos.
        """
        m  = self._op_mode
        ox = self._mouse_sx
        oy = self._mouse_sy
        pt = (self.snap_pt[0], self.snap_pt[1]) if self.snap_pt else self.mouse_w

        # ── color según tipo de cota ──────────────────────────────────
        accent = "#F472B6"   # rosa — cotas
        bg     = "#110810"
        n_steps = 3

        # ── DIM lineal (H / V / A) ────────────────────────────────────
        if m in ("dim_lp1", "dim_lp2", "dim_lpos"):
            dt   = self._op_data.get("dim_type", "H")
            name = {"H": "HORIZ.", "V": "VERT.", "A": "ALINEADA"}.get(dt, dt)
            _STEPS = {
                "dim_lp1":  (1, "①  Primer punto de extensión"),
                "dim_lp2":  (2, "②  Segundo punto de extensión"),
                "dim_lpos": (3, "③  Posición de la línea de cota"),
            }
            step, desc = _STEPS[m]
            value_txt = None
            if m == "dim_lp2" and self._op_pts:
                p1 = self._op_pts[0]
                if dt == "H":
                    d = abs(pt[0] - p1[0])
                elif dt == "V":
                    d = abs(pt[1] - p1[1])
                else:
                    d = math.hypot(pt[0] - p1[0], pt[1] - p1[1])
                value_txt = f"  dist: {d:.4f} m"
            elif m == "dim_lpos" and len(self._op_pts) >= 2:
                p1, p2 = self._op_pts[0], self._op_pts[1]
                if dt == "H":
                    d = abs(p2[0] - p1[0])
                elif dt == "V":
                    d = abs(p2[1] - p1[1])
                else:
                    d = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
                value_txt = f"  dist: {d:.4f} m"
            title = f"DIM {name}  ·  paso {step} / 3"

        # ── DIM angular ───────────────────────────────────────────────
        elif m in ("dim_ang_cen", "dim_ang_p1", "dim_ang_p2"):
            accent  = "#67E8F9"   # cian — angular
            bg      = "#060C10"
            _STEPS  = {
                "dim_ang_cen": (1, "①  Centro del ángulo"),
                "dim_ang_p1":  (2, "②  Primer punto del ángulo"),
                "dim_ang_p2":  (3, "③  Segundo punto del ángulo"),
            }
            step, desc = _STEPS[m]
            value_txt = None
            if m == "dim_ang_p2" and "cen" in self._op_data and self._op_pts:
                cen = self._op_data["cen"]
                p1  = self._op_pts[0]
                a1  = math.atan2(p1[1] - cen[1], p1[0] - cen[0])
                a2  = math.atan2(pt[1]  - cen[1], pt[0]  - cen[0])
                ang = abs(math.degrees(a2 - a1)) % 360
                if ang > 180:
                    ang = 360 - ang
                value_txt = f"  ángulo: {ang:.2f}°"
            elif m == "dim_ang_p1" and "cen" in self._op_data:
                cen = self._op_data["cen"]
                d   = math.hypot(pt[0] - cen[0], pt[1] - cen[1])
                value_txt = f"  d_cen: {d:.4f} m"
            title = f"DIM ANGULAR  ·  paso {step} / 3"

        # ── DIM radio / diámetro ──────────────────────────────────────
        elif m in ("dim_r_obj", "dim_r_pt"):
            accent  = "#A78BFA"   # violeta — radio/diámetro
            bg      = "#0A0710"
            dt      = self._op_data.get("dim_type", "R")
            n_steps = 2
            if m == "dim_r_obj":
                step = 1; desc = "①  Clic sobre el círculo o arco"
                value_txt = None
            else:
                step = 2; desc = "②  Punto en la circunferencia"
                p1 = self._op_data.get("p1")
                if p1:
                    r = math.hypot(pt[0] - p1[0], pt[1] - p1[1])
                    sym = "Ø" if dt == "D" else "R"
                    val = r * 2 if dt == "D" else r
                    value_txt = f"  {sym}: {val:.4f} m"
                else:
                    value_txt = None
            name  = "DIÁMETRO" if dt == "D" else "RADIO"
            title = f"DIM {name}  ·  paso {step} / 2"

        else:
            return   # modo no gestionado por este panel

        # ── Layout ────────────────────────────────────────────────────
        show_val = (value_txt is not None)
        PW    = 234;  PAD_X = 10;  PAD_Y = 6
        R1    = 15;   R2    = 14;  R3    = 13;  R_DOT = 14;  GAP   = 3
        ph    = PAD_Y + R1 + GAP + R2 + GAP + R_DOT + PAD_Y
        if show_val:
            ph += GAP + R3

        px = ox + 22;  py = oy - ph - 8
        CW = cv.winfo_width();  CH = cv.winfo_height()
        if px + PW > CW - 4:   px = ox - PW - 8
        if py < 4:              py = oy + 8

        # Fondo + borde
        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill=bg, outline=accent, width=1, tags="dy")

        # Fila 1: título / nombre comando
        cv.create_text(px + PAD_X, py + PAD_Y,
                       text=title,
                       fill=accent, font=("Courier New", 10, "bold"),
                       anchor="nw", tags="dy")

        # Fila 2: descripción del paso actual
        y2 = py + PAD_Y + R1 + GAP
        cv.create_text(px + PAD_X, y2,
                       text=desc,
                       fill="#F0F9FF", font=("Courier New", 9),
                       anchor="nw", tags="dy")

        # Fila 3: valor en tiempo real (si aplica)
        if show_val:
            y3 = y2 + R2 + GAP
            cv.create_text(px + PAD_X, y3,
                           text=value_txt,
                           fill="#FCD34D", font=("Courier New", 9, "bold"),
                           anchor="nw", tags="dy")

        # Barra de progreso (puntos conectados)
        dot_y = py + ph - PAD_Y - R_DOT // 2 + 2
        if n_steps > 1:
            dot_spacing = (PW - PAD_X * 2) // n_steps
            # Línea de fondo primero
            x0 = px + PAD_X + dot_spacing // 2
            x1 = px + PAD_X + (n_steps - 1) * dot_spacing + dot_spacing // 2
            cv.create_line(x0, dot_y, x1, dot_y,
                           fill="#1E293B", width=1, tags="dy")
            # Puntos encima
            for i in range(1, n_steps + 1):
                dx = px + PAD_X + (i - 1) * dot_spacing + dot_spacing // 2
                col   = accent if i <= step else "#1E293B"
                r_dot = 5      if i == step else 3
                cv.create_oval(dx - r_dot, dot_y - r_dot,
                               dx + r_dot, dot_y + r_dot,
                               fill=col, outline="", tags="dy")

    def _draw_array_type_panel(self, cv):
        """Panel de selección de tipo ARRAY antes de abrir el diálogo."""
        ox, oy = self._mouse_sx, self._mouse_sy
        PW = 230; PAD_X = 10; PAD_Y = 7
        GAP = 5
        R_TITLE = 14   # alto fila título
        R_BTNS  = 18   # alto fila botones
        ph = PAD_Y + R_TITLE + GAP + R_BTNS + PAD_Y

        # Posición: a la derecha y arriba del cursor
        px = ox + 22; py = oy - ph - 8
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = ox - PW - 12
        if py < 4:            py = oy + 14

        n_sel = len(self._op_sel)
        accent  = "#60A5FA"   # azul ARRAY
        bg      = "#060B14"
        dim     = "#475569"

        # Fondo + borde
        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill=bg, outline=accent, width=1, tags="dy")

        # ── Fila 1: título ────────────────────────────────────────────
        ty1 = py + PAD_Y
        cv.create_text(px + PAD_X, ty1,
                       text="ARRAY",
                       fill=accent, font=("Courier New", 10, "bold"),
                       anchor="nw", tags="dy")
        # Badge con nº de objetos (esquina derecha)
        badge_txt = f"{n_sel} obj."
        cv.create_text(px + PW - PAD_X, ty1,
                       text=badge_txt,
                       fill="#94A3B8", font=("Courier New", 9),
                       anchor="ne", tags="dy")

        # ── Separador ─────────────────────────────────────────────────
        sep_y = ty1 + R_TITLE + GAP // 2
        cv.create_line(px + PAD_X, sep_y, px + PW - PAD_X, sep_y,
                       fill=dim, width=1, tags="dy")

        # ── Fila 2: botones [R] y [P] ─────────────────────────────────
        ty2 = sep_y + GAP
        btn_w = (PW - PAD_X * 2 - GAP * 2) // 2
        # Botón [R] Rectangular
        bx_r = px + PAD_X
        cv.create_rectangle(bx_r, ty2, bx_r + btn_w, ty2 + R_BTNS,
                            fill="#0F2044", outline=accent, width=1, tags="dy")
        cv.create_text(bx_r + btn_w // 2, ty2 + R_BTNS // 2,
                       text="[R]  Rectangular",
                       fill=accent, font=("Courier New", 9, "bold"),
                       anchor="center", tags="dy")
        # Botón [P] Polar
        bx_p = bx_r + btn_w + GAP * 2
        cv.create_rectangle(bx_p, ty2, bx_p + btn_w, ty2 + R_BTNS,
                            fill="#0A1A1A", outline=dim, width=1, tags="dy")
        cv.create_text(bx_p + btn_w // 2, ty2 + R_BTNS // 2,
                       text="[P]  Polar",
                       fill="#94A3B8", font=("Courier New", 9),
                       anchor="center", tags="dy")
        # Hint Enter
        cv.create_text(px + PW - PAD_X, ty2 + R_BTNS // 2,
                       text="Enter=R",
                       fill=dim, font=("Courier New", 8),
                       anchor="e", tags="dy")

    def _draw_zoom_opts_panel(self, cv):
        """Panel flotante ZOOM — muestra opciones E/W/P/factor/XP."""
        ox, oy = self._mouse_sx, self._mouse_sy
        in_ps  = getattr(self, "active_layout_idx", -1) >= 0

        PW = 240; PAD_X = 10; PAD_Y = 7; GAP = 4
        R_TITLE = 14; R_OPT = 13; R_FACTOR = 16
        n_rows  = 4 + (1 if in_ps else 0)   # E A W P [XP]
        ph      = PAD_Y + R_TITLE + GAP + n_rows * (R_OPT + 2) + GAP + R_FACTOR + PAD_Y

        px = ox + 22; py = oy - ph - 8
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = ox - PW - 12
        if py < 4:            py = oy + 14

        accent = "#22D3EE"   # cian — color de zoom
        bg     = "#06101A"
        dim    = "#475569"
        hi     = "#F8FAFC"

        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill=bg, outline=accent, width=1, tags="dy")

        # Título
        ty = py + PAD_Y
        cv.create_text(px + PAD_X, ty, text="ZOOM",
                       fill=accent, font=("Courier New", 10, "bold"),
                       anchor="nw", tags="dy")
        if in_ps:
            cv.create_text(px + PW - PAD_X, ty, text="📐 paper",
                           fill="#94A3B8", font=("Courier New", 8),
                           anchor="ne", tags="dy")

        # Separador
        sep_y = ty + R_TITLE + GAP // 2
        cv.create_line(px + PAD_X, sep_y, px + PW - PAD_X, sep_y,
                       fill=dim, width=1, tags="dy")

        # Opciones
        opts = [("[E]  Extensión", "E"), ("[A]  Todo", "A"),
                ("[W]  Ventana",   "W"), ("[P]  Anterior", "P")]
        if in_ps:
            opts.append(("[XP]  Escala viewport  ej: 1/100xp", "XP"))

        oy2 = sep_y + GAP
        for label, key in opts:
            col_ = hi if key in ("E",) else "#94A3B8"
            cv.create_text(px + PAD_X, oy2, text=label,
                           fill=col_, font=("Courier New", 9),
                           anchor="nw", tags="dy")
            oy2 += R_OPT + 2

        # Campo de factor numérico
        oy2 += GAP // 2
        buf = self._kbd_buf or ""
        factor_txt = f"factor: {buf}▌" if buf else "factor: ___"
        cv.create_text(px + PAD_X, oy2, text=factor_txt,
                       fill=accent if buf else dim,
                       font=("Courier New", 9), anchor="nw", tags="dy")

    def _draw_array_pol_ctr_panel(self, cv):
        """Panel ARRAY POLAR — paso 1: clic en centro de rotación."""
        ox, oy = self._mouse_sx, self._mouse_sy
        PW = 230; PAD_X = 10; PAD_Y = 7; GAP = 4
        R1 = 14; R2 = 13
        ph = PAD_Y + R1 + GAP + R2 + PAD_Y
        px = ox + 22; py = oy - ph - 8
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = ox - PW - 12
        if py < 4:            py = oy + 14

        n_sel  = len(self._op_sel)
        accent = "#34D399"   # verde polar
        bg     = "#06100A"
        dim    = "#334155"

        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill=bg, outline=accent, width=1, tags="dy")
        # Título
        cv.create_text(px + PAD_X, py + PAD_Y,
                       text="ARRAY POLAR",
                       fill=accent, font=("Courier New", 10, "bold"),
                       anchor="nw", tags="dy")
        cv.create_text(px + PW - PAD_X, py + PAD_Y,
                       text=f"{n_sel} obj.",
                       fill="#94A3B8", font=("Courier New", 9),
                       anchor="ne", tags="dy")
        sep_y = py + PAD_Y + R1 + GAP // 2
        cv.create_line(px + PAD_X, sep_y, px + PW - PAD_X, sep_y,
                       fill=dim, width=1, tags="dy")
        # Instrucción
        cv.create_text(px + PAD_X, sep_y + GAP,
                       text="⊙  Clic para fijar el centro de rotación",
                       fill="#F0FDF4", font=("Courier New", 9),
                       anchor="nw", tags="dy")

    def _draw_sel_rect(self):
        """Rectángulo de selección estilo AutoCAD:
        Izq→Der = VENTANA  (azul, solo entidades completamente dentro)
        Der→Izq = CRUCE    (verde punteado, entidades que intersectan)
        """
        cv = self.canvas
        if self.tool != "select":  # nunca mostrar durante dibujo
            return
        if not self._is_dragging or self._drag_start_s is None:
            return
        x0, y0 = self._drag_start_s
        x1, y1 = self._mouse_sx, self._mouse_sy
        is_cross = x1 < x0
        if is_cross:   # Cruce — verde punteado con relleno suave
            col = CV_CRS_BOX; dash = (5, 3)
            fill_col = "#00774420"   # verde muy transparente (stipple)
            stip = "gray12"
        else:          # Ventana — azul sólido con relleno suave
            col = CV_WIN_BOX; dash = ()
            fill_col = "#0044CC"
            stip = "gray12"
        # Relleno suave
        cv.create_rectangle(x0, y0, x1, y1, outline="",
                            fill=col, stipple=stip, tags="dy")
        # Borde
        cv.create_rectangle(x0, y0, x1, y1, outline=col,
                            fill="", width=1, dash=dash, tags="dy")
        # Etiqueta en la esquina opuesta al origen
        label = "CRUCE" if is_cross else "VENTANA"
        lx = x1 + (6 if x1 > x0 else -6)
        ly = y1 + (6 if y1 > y0 else -16)
        anchor = "nw" if x1 > x0 else "ne"
        cv.create_text(lx, ly, text=label, fill=col,
                       font=("Courier New", 8, "bold"), anchor=anchor, tags="dy")

    def _draw_zoom_window_rect(self):
        """Preview del rectángulo de ZOOM W mientras el usuario elige la segunda esquina."""
        if self._op_mode != "zoom_w2":
            return
        if not self._op_pts:
            return
        cv = self.canvas
        wx0, wy0 = self._op_pts[0]
        sx0, sy0 = self.w2s(wx0, wy0)
        sx1, sy1 = self._mouse_sx, self._mouse_sy
        # Estilo AutoCAD Zoom Window: magenta / amarillo para diferenciar de la selección
        col  = "#FFD700"   # dorado
        dash = (6, 3)
        stip = "gray12"
        cv.create_rectangle(sx0, sy0, sx1, sy1, outline="",
                            fill=col, stipple=stip, tags="dy")
        cv.create_rectangle(sx0, sy0, sx1, sy1, outline=col,
                            fill="", width=1, dash=dash, tags="dy")
        # Etiqueta en la esquina móvil
        lx = sx1 + (6 if sx1 > sx0 else -6)
        ly = sy1 + (6 if sy1 > sy0 else -16)
        anchor = "nw" if sx1 > sx0 else "ne"
        cv.create_text(lx, ly, text="ZOOM W", fill=col,
                       font=("Courier New", 8, "bold"), anchor=anchor, tags="dy")

    def _draw_snap_indicator(self):
        cv = self.canvas
        if not self.snap_pt:
            return
        sx, sy = self.w2s(self.snap_pt[0], self.snap_pt[1])
        stype = self.snap_pt[2]
        r = 9   # marcador más grande para visibilidad

        # Cada tipo tiene color + forma de marcador diferente
        _CFG = {
            "end": (CV_SNAP_END, "square"),   # cuadrado amarillo
            "mid": (CV_SNAP_MID, "diamond"),  # rombo cian
            "cen": (CV_SNAP_CEN, "circle"),   # círculo magenta
            "qua": (CV_SNAP_QUA, "square"),   # cuadrado naranja (rotado 45)
            "int": ("#F59E0B",   "cross"),    # cruz naranja
            "per": ("#38BDF8",   "per"),      # ángulo recto celeste
            "tan": ("#A78BFA",   "circle"),   # círculo violeta
            "nea": ("#FB923C",   "square"),   # cuadrado naranja claro
            "gri": ("#6EE7B7",   "plus"),     # cruz ortogonal verde menta
        }
        col, shape = _CFG.get(stype, (CV_SNAP_END, "square"))

        if shape == "square":
            cv.create_rectangle(sx-r, sy-r, sx+r, sy+r,
                                outline=col, width=2, tags="dy")
        elif shape == "diamond":
            cv.create_polygon(sx, sy-r, sx+r, sy, sx, sy+r, sx-r, sy,
                              outline=col, fill="", width=2, tags="dy")
        elif shape == "circle":
            cv.create_oval(sx-r, sy-r, sx+r, sy+r,
                           outline=col, width=2, tags="dy")
        elif shape == "cross":
            cv.create_line(sx-r, sy-r, sx+r, sy+r, fill=col, width=2, tags="dy")
            cv.create_line(sx+r, sy-r, sx-r, sy+r, fill=col, width=2, tags="dy")
        elif shape == "per":
            h = r - 2
            cv.create_line(sx-h, sy+h, sx-h, sy-h, sx+h, sy-h,
                           fill=col, width=2, tags="dy")
        elif shape == "plus":
            cv.create_line(sx-r, sy,   sx+r, sy,   fill=col, width=2, tags="dy")
            cv.create_line(sx,   sy-r, sx,   sy+r, fill=col, width=2, tags="dy")

        cv.create_text(sx + r + 5, sy, text=stype.upper(), fill=col,
                       font=("Courier New", 8, "bold"), anchor="w", tags="dy")

    # ── Modos que requieren cuadrito selector (pickbox) ──────────────
    _PICKBOX_MODES = frozenset({
        "erase_sel",                     # selección antes de ERASE
        "move_sel", "copy_sel",          # selección de entidades antes de MOVE/COPY
        "rotate_sel", "scale_sel", "mirror_sel",  # ídem para ROTATE/SCALE/MIRROR
        "array_sel",                     # selección antes de ARRAY
        "array_pol_ctr",                 # array polar: pick centro de rotación
        "trim_obj", "extend_obj",
        "fillet_p1", "fillet_p2",
        "chamfer_p1", "chamfer_p2",      # chamfer pick
        "offset_sel",
        "matchprop_src", "matchprop_dst",
        "break_p1",                      # break: pick entity
        "laymcur",                       # set layer from entity
        "layiso_pick",                   # LAYISO: pick entity to isolate its layer
        "id_pick",                       # ID: pick point to show coordinates
        "align_sel",                     # ALIGN: selection phase
        "dim_r_obj",                     # dim radius: pick circle/arc
        "zoom_w1",                       # ZOOM W: primera esquina
        "zoom_w2",                       # ZOOM W: segunda esquina (con preview)
    })
    _PICKBOX_SIZE = 6   # semilado del cuadrito en píxeles (AutoCAD usa ~5–8 px)

    def _is_pickbox_mode(self) -> bool:
        """True cuando un comando está esperando que el usuario pickee una entidad."""
        return self._op_mode in self._PICKBOX_MODES

    def _draw_cursor(self, W, H):
        cv = self.canvas
        sx, sy = self.w2s(*self.mouse_w)

        # ── Color base: activo (herramienta/operación) vs inactivo ──────
        _cmd_active = bool(self._op_mode or self.draw_pts or self.tool != "select")
        _base_col   = self.cursor_color_active if _cmd_active else self.cursor_color_idle

        # ── Color final: snap sobreescribe el base ────────────────────
        if self.snap_pt:
            # Snap detectado: círculo del color del snap
            col = _SNAP_COLORS.get(self.snap_pt[2], _base_col)
            r = SNAP_PX
            cv.create_oval(sx-r, sy-r, sx+r, sy+r,
                           outline=col, fill="", width=1, tags="dy")
        else:
            col = _base_col
            # Comando activo pero sin snap cercano: círculo del color activo
            if self.tool != "select" or self._op_mode:
                r = SNAP_PX
                cv.create_oval(sx-r, sy-r, sx+r, sy+r,
                               outline=col, fill="", width=1, tags="dy")

        # ══ PICKBOX (cuadrito selector — igual a AutoCAD) ════════════
        if self._is_pickbox_mode():
            pb = self._PICKBOX_SIZE
            # Cuadrito outline blanco/amarillo según snap
            pick_col = col if self.snap_pt else "#C0C0C0"
            cv.create_rectangle(sx - pb, sy - pb, sx + pb, sy + pb,
                                outline=pick_col, fill="", width=1, tags="dy")

            # Coordenadas a la derecha del cuadrito (desplazadas arriba
            # para no superponerse con el borde inferior del pickbox)
            x, y = self.mouse_w
            tx = sx + pb + 12
            cv.create_text(tx, sy - 14,
                           text=f"X  {x:8.3f}",
                           fill=UI_TEXT2, font=("Courier New", 9),
                           anchor="w", tags="dy")
            cv.create_text(tx, sy - 2,
                           text=f"Y  {y:8.3f}",
                           fill=UI_TEXT2, font=("Courier New", 9),
                           anchor="w", tags="dy")
            # Ocultar el crosshair persistente (dy_c) mientras pickbox esté activo
            _crs_st = getattr(self, '_crs_item_state', {})
            for _k, _cid in getattr(self, '_crs_ids', {}).items():
                if _crs_st.get(_k, (None, None))[1] != 'hidden':
                    try: cv.itemconfig(_cid, state='hidden')
                    except Exception: pass
                    _crs_st[_k] = (_crs_st.get(_k, (col,))[0], 'hidden')
            self._crs_item_state = _crs_st
            return   # no dibuja brazos

        # ══ CROSSHAIR — items PERSISTENTES (tag "dy_c", no borrados por cv.delete("dy"))
        pct   = max(5, min(100, self.cursor_size)) / 100.0
        arm_h = max(12, int(W * pct))
        arm_v = max(12, int(H * pct))

        G = 5  # gap central
        ids = getattr(self, '_crs_ids', {})

        # Si no hay IDs registrados pero pueden existir items "dy_c" huérfanos
        # de un redraw anterior (Tkinter no lanza excepción al mover ID inválido),
        # los borramos ahora para evitar dos cruces simultáneos en pantalla.
        if not ids:
            try:
                cv.delete("dy_c")
            except Exception:
                pass
        # Cache de (color, state) por brazo — evita llamadas innecesarias a itemconfig
        # (cada itemconfigure genera un round-trip Tcl/Tk; a 60fps = 240/s).
        _crs_st = getattr(self, '_crs_item_state', {})

        def _line(key, x0, y0, x1, y1):
            if key in ids:
                try:
                    cv.coords(ids[key], x0, y0, x1, y1)
                    # Solo llamar itemconfig si el color o visibilidad cambió
                    _prev = _crs_st.get(key)
                    if _prev != (col, 'normal'):
                        cv.itemconfig(ids[key], fill=col, state='normal')
                        _crs_st[key] = (col, 'normal')
                    return
                except Exception:
                    pass
            ids[key] = cv.create_line(x0, y0, x1, y1,
                                      fill=col, width=1, tags="dy_c")
            _crs_st[key] = (col, 'normal')

        _line('hl', sx - arm_h, sy, sx - G,     sy)
        _line('hr', sx + G,     sy, sx + arm_h, sy)
        _line('vt', sx, sy - arm_v, sx, sy - G)
        _line('vb', sx, sy + G,     sx, sy + arm_v)
        self._crs_ids        = ids
        self._crs_item_state = _crs_st

        # ── Coordenadas flotantes ─────────────────────────────────────
        # Offset diagonal para no cruzar ningún brazo del crosshair.
        # • tx  = al menos 20 px a la derecha del centro (fuera del brazo H)
        # • cy0/cy1 ambas encima del brazo H (brazo en sy; texto por encima)
        # • Si DYN activo, el bloque va debajo del cursor (evita tapar panel)
        x, y = self.mouse_w
        dyn_visible = self.dyn_on and self._dyn_active()
        tx = max(sx + 20, sx + SNAP_PX + 8) if self.snap_pt else sx + 20
        if dyn_visible:
            # Bloque debajo — claro respecto al brazo H (está en sy)
            cy0, cy1 = sy + 16, sy + 28
        else:
            # Bloque arriba — ambas líneas claramente sobre el brazo H
            cy0, cy1 = sy - 26, sy - 14

        # Textos de coordenadas — también persistentes
        # Ocultos cuando el panel kbd o un panel flotante de comando está activo
        # (evita que los textos dy_c taponeen los paneles dy que quedan debajo).
        _PANEL_MODES = frozenset({
            "mirror_keep",
            "trim_obj", "extend_obj",
            "hatch_pts",
            "matchprop_src", "matchprop_dst",
            "measure_p1", "measure_next",
            "align_sp1", "align_sp2", "align_dp1", "align_dp2",
            "array_type",
            "array_pol_ctr", "array_pol_n",
            "image_place", "image_width",
            "zoom_opts",
        })
        _suppress_coords = self._kbd_buf or (self._op_mode in _PANEL_MODES)

        def _txt(key, px, py, text):
            if key in ids:
                try:
                    cv.coords(ids[key], px, py)
                    cv.itemconfig(ids[key], text=text, state='normal')
                    return
                except Exception:
                    pass
            ids[key] = cv.create_text(px, py, text=text,
                                      fill=UI_TEXT2, font=("Courier New", 9),
                                      anchor="w", tags="dy_c")
        if _suppress_coords:
            # Panel flotante o kbd activo — ocultar textos sueltos de coords
            for k in ('tx', 'ty'):
                if k in ids:
                    try: cv.itemconfig(ids[k], state='hidden')
                    except Exception: pass
        else:
            _txt('tx', tx, cy0, f"X  {x:8.3f}")
            _txt('ty', tx, cy1, f"Y  {y:8.3f}")
        self._crs_ids = ids

        # ── Ángulo y distancia flotantes ──────────────────────────────
        # Solo cuando se está dibujando Y el DYN no está activo
        # (si DYN activo, ya muestra ∠ y d en su panel — no duplicar)
        if self.draw_pts and not dyn_visible:
            px0, py0 = self.draw_pts[-1]
            dx, dy_w = x - px0, y - py0
            dist = math.hypot(dx, dy_w)
            ang = math.degrees(math.atan2(dy_w, dx)) % 360
            cv.create_text(tx, cy1 + 14,
                           text=f"∠ {ang:6.1f}°",
                           fill=CV_SNAP_MID,
                           font=("Courier New", 9, "bold"),
                           anchor="w", tags="dy")
            cv.create_text(tx, cy1 + 26,
                           text=f"d  {dist:7.3f} m",
                           fill=CV_SNAP_MID,
                           font=("Courier New", 9, "bold"),
                           anchor="w", tags="dy")

    # Escalas arquitectónicas estándar (denominador)
    _ARCH_SCALES = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000]

    def _arch_scale_label(self) -> str:
        """Devuelve la escala arquitectónica más cercana a la vista actual."""
        try:
            dpi = self.root.winfo_fpixels("1i")  # px/pulgada reales
        except Exception:
            dpi = 96.0
        # px/m → mm en pantalla por metro mundo → denominador para 1mm papel = 1m mundo
        # escala = (scale px/m) / (dpi px/in * 1in/25.4mm) → mm_pantalla/m
        # denominador k: scale / (dpi/25.4) = 1/k → k = dpi/(scale*25.4)
        if self.scale <= 0:
            return "1:—"
        raw = dpi / (self.scale * 25.4 / 1000)   # 1000 mm/m
        # snap al más cercano
        best = min(self._ARCH_SCALES, key=lambda s: abs(s - raw))
        exact = abs(best - raw) / raw < 0.05      # ±5 % → sin ~
        prefix = "" if exact else "~"
        return f"{prefix}1:{best}"

    def _draw_hud(self, W, H):
        cv = self.canvas
        # Ítems persistentes: se crean una vez y se actualizan con itemconfigure/coords.
        # Evita delete("st_hud") + create_text×N en cada frame (fuente de freeze).
        _HUD_SLOTS = 5  # siempre 5 slots fijos (slot 4 = draw_pts, oculto si vacío)
        fps_times = getattr(self, '_fps_times', [])
        render_ms = getattr(self, '_render_ms', 0.0)
        fps = len(fps_times) / 2.0 if len(fps_times) > 1 else 0.0
        lines = [
            self._arch_scale_label(),
            f"sc={self.scale:.1f}",
            f"{len(self.entities)} ent.",
            f"{render_ms:.0f}ms  {fps:.1f}fps",
            f"Pts: {len(self.draw_pts)}" if self.draw_pts else "",
        ]

        # Durante tessellation mantener ítems existentes sin tocar
        _hud = getattr(self, '_hud_items', None)
        if getattr(self._renderer, '_tess_pending', False) and _hud:
            return

        # Crear ítems persistentes la primera vez (o si el canvas fue reiniciado)
        if not _hud:
            self._hud_items = [
                cv.create_text(0, 0, text="", fill=UI_BORD,
                               font=("Courier New", 8), anchor="se",
                               tags=("st", "st_hud"))
                for _ in range(_HUD_SLOTS)
            ]
            self._hud_txt = [""] * _HUD_SLOTS
            _hud = self._hud_items

        _hud_txt = self._hud_txt
        # Actualizar posición y texto sin crear/destruir ítems Tcl
        for i, txt in enumerate(reversed(lines)):
            iid = _hud[i]
            cv.coords(iid, W - 10, H - 10 - i * 14)
            if _hud_txt[i] != txt:
                _hud_txt[i] = txt
                cv.itemconfigure(iid,
                                 text=txt,
                                 state="normal" if txt else "hidden")

    # ── Helpers de color derivado ──────────────────────────────────
    def _hex2rgb3(self, hex_color: str) -> tuple:
        """Convierte '#RRGGBB' a (r, g, b) int."""
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c*2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def _cmd_bg_dark(self) -> str:
        """Versión ligeramente más oscura de cmd_bar_bg para el historial."""
        r, g, b = self._hex2rgb3(self.cmd_bar_bg)
        return f"#{max(0,r-8):02X}{max(0,g-8):02X}{max(0,b-8):02X}"

    def _cv_grid(self) -> str:
        """Color de líneas de cuadrícula menor (bg + ~18 luminosidad)."""
        r, g, b = self._hex2rgb3(self.cv_bg)
        d = 18
        return f"#{min(255,r+d):02X}{min(255,g+d):02X}{min(255,b+d):02X}"

    def _cv_grid_maj(self) -> str:
        """Color de cuadrícula mayor (bg + ~32 luminosidad)."""
        r, g, b = self._hex2rgb3(self.cv_bg)
        d = 32
        return f"#{min(255,r+d):02X}{min(255,g+d):02X}{min(255,b+d):02X}"

    def _guardar_config_colores(self):
        """Persiste colores personalizables en settings.json."""
        cfg = self._leer_config_ia()
        cfg["cv_bg"]               = self.cv_bg
        cfg["cmd_bar_bg"]          = self.cmd_bar_bg
        cfg["cursor_color_idle"]   = self.cursor_color_idle
        cfg["cursor_color_active"] = self.cursor_color_active
        cfg["axis_color"]          = self.axis_color
        cfg["select_color"]        = self.select_color
        cfg["grid_major"]          = self.grid_major
        cfg["grid_minor"]          = self.grid_minor
        path = self._cfg_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            import json as _json
            _json.dump(cfg, f, indent=2)
        self._config_dirty = True

    def _aplicar_colores_live(self):
        """Aplica los colores nuevos al canvas y barra de comandos sin reiniciar."""
        # Canvas
        self.canvas.configure(bg=self.cv_bg)
        # Reconstruir barra de comandos
        if hasattr(self, "_cmd_dock_outer") and self._cmd_dock_outer.winfo_exists():
            for w in self._cmd_dock_outer.winfo_children():
                try:
                    w.destroy()
                except Exception:
                    pass
            self._build_cmd_bar(self._cmd_dock_outer)
        # Re-render (cursor color se aplica al próximo _draw_cursor)
        self._pil_img_cache = None
        self._redraw_static()
        # Forzar recreación del cursor para que cambie de color inmediatamente
        try:
            self.canvas.delete("dy_c")
        except Exception:
            pass
        self._crs_ids = {}
        self._redraw_dynamic()

    # ═══════════════════════════════════════════════════════════════════
    # LAYOUTS — PAPER SPACE
    # ═══════════════════════════════════════════════════════════════════

    # ── Tab strip ────────────────────────────────────────────────────────

    def _build_layout_tabs(self):
        """Reconstruye el strip de tabs Modelo / Lámina 1 / … / [+]."""
        tf = self._layout_tab_frame
        if tf is None:
            return
        for w in tf.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

        _BG   = "#1A1A2E"
        _ACT  = "#2563EB"    # tab activo
        _IDLE = "#2A2A3E"    # tab inactivo
        _HOVER= "#3A3A5E"
        _TX   = "#F8FAFC"
        _TX2  = "#94A3B8"
        _FNT  = ("Courier New", 9, "bold")
        _FNTS = ("Courier New", 8)

        # ── Tab "Modelo" ─────────────────────────────────────────────
        is_model = (self.active_layout_idx == -1)
        mod_bg   = _ACT if is_model else _IDLE
        mod_fg   = _TX  if is_model else _TX2

        btn_mod = tk.Button(
            tf, text="  Modelo  ",
            bg=mod_bg, fg=mod_fg, relief="flat",
            font=_FNT, bd=0, padx=6, pady=0,
            activebackground=_ACT, activeforeground=_TX,
            cursor="hand2",
            command=self._switch_to_model,
        )
        btn_mod.pack(side="left", fill="y", padx=(4, 1), pady=3)

        # Separador visual
        tk.Frame(tf, bg="#3A3A5E", width=1).pack(side="left", fill="y", pady=4)

        # ── Tabs de láminas ──────────────────────────────────────────
        for idx, lay in enumerate(self.layouts):
            is_act = (self.active_layout_idx == idx)
            t_bg   = _ACT if is_act else _IDLE
            t_fg   = _TX  if is_act else _TX2

            # Frame contenedor del tab
            tab_f = tk.Frame(tf, bg=t_bg)
            tab_f.pack(side="left", fill="y", padx=1, pady=3)

            # Nombre (clic = activar)
            name_lbl = tk.Label(
                tab_f, text=f"  {lay.name}  ",
                bg=t_bg, fg=t_fg, font=_FNT,
                cursor="hand2", padx=2,
            )
            name_lbl.pack(side="left", fill="y")
            name_lbl.bind("<Button-1>",
                          lambda e, i=idx: self._switch_to_layout(i))
            name_lbl.bind("<Double-Button-1>",
                          lambda e, i=idx: self._rename_layout_dialog(i))
            # Tooltip: doble clic para renombrar
            _Tooltip(name_lbl, f"Clic = activar  |  Doble clic = renombrar\n"
                                f"Papel: {lay.paper} {'Horizontal' if lay.orientation=='H' else 'Vertical'}")

            # Botón ⚙ (Page Setup)
            cfg_btn = tk.Label(
                tab_f, text="⚙", bg=t_bg, fg="#64748B",
                font=("Arial", 9), cursor="hand2", padx=1,
            )
            cfg_btn.pack(side="left", fill="y")
            cfg_btn.bind("<Button-1>",
                         lambda e, i=idx: self._page_setup_dialog(i))

            # Botón × (eliminar)
            del_btn = tk.Label(
                tab_f, text="×", bg=t_bg, fg="#64748B",
                font=("Arial", 10, "bold"), cursor="hand2", padx=3,
            )
            del_btn.pack(side="left", fill="y")
            del_btn.bind("<Button-1>",
                         lambda e, i=idx: self._delete_layout(i))

        # ── Botón "+" nueva lámina ───────────────────────────────────
        tk.Frame(tf, bg="#3A3A5E", width=1).pack(side="left", fill="y", pady=4)
        add_btn = tk.Button(
            tf, text="  ＋  ",
            bg=_IDLE, fg="#64748B", relief="flat",
            font=("Arial", 11), bd=0, padx=4, pady=0,
            activebackground=_HOVER, activeforeground=_TX,
            cursor="hand2",
            command=self._new_layout,
        )
        add_btn.pack(side="left", fill="y", padx=(1, 4), pady=3)
        _Tooltip(add_btn, "Nueva lámina")

        # ── Info del layout activo (extremo derecho) ─────────────────
        if self.active_layout_idx >= 0:
            lay = self.layouts[self.active_layout_idx]
            pw, ph = paper_size(lay.paper, lay.orientation)
            info = f"  {lay.paper} {'H' if lay.orientation=='H' else 'V'}  "
            tk.Label(tf, text=info, bg=_BG, fg="#475569",
                     font=_FNTS).pack(side="right", padx=8)

    # ── Creación y gestión ────────────────────────────────────────────

    def _new_layout(self, name: str = "", paper: str = "A2",
                    orientation: str = "H", scale_denom: int = 100):
        """Crea una nueva lámina y activa su tab."""
        if not name:
            n = len(self.layouts) + 1
            name = f"Lámina {n}"

        # Autodetectar escala inicial a partir de extents del modelo
        vp = default_viewport(paper, orientation, scale_denom)
        vp = auto_fit_viewport(vp, self.entities)

        # Ajustar escala automática si hay entidades
        if self.entities:
            best = self._auto_scale_for_viewport(vp, paper, orientation)
            vp = ViewportDef(
                x=vp.x, y=vp.y, width=vp.width, height=vp.height,
                scale_denom=best,
                view_cx=vp.view_cx, view_cy=vp.view_cy,
                active=True,
            )
            scale_denom = best

        cfg = self._leer_config_ia()
        lay = LayoutSheet(
            name=name, paper=paper, orientation=orientation,
            viewports=[vp],
            tb_proyecto=cfg.get("proyecto_nombre", ""),
            tb_firma=cfg.get("firma_pdf", "Estudio Merlos"),
            tb_escala=f"1:{scale_denom}",
            tb_numero=f"{len(self.layouts)+1:02d}",
        )
        self.layouts.append(lay)
        idx = len(self.layouts) - 1
        self._build_layout_tabs()
        self._switch_to_layout(idx)

    def _auto_scale_for_viewport(self, vp: ViewportDef,
                                  paper: str, orientation: str) -> int:
        """Elige el denominador de escala que mejor encaja el modelo en el vp."""
        # Bounding box del modelo
        xs, ys = [], []
        for e in self.entities:
            try:
                for p in e.bbox_pts():
                    xs.append(p[0]); ys.append(p[1])
            except Exception:
                pass
        if not xs:
            return 100

        model_w = max(xs) - min(xs)
        model_h = max(ys) - min(ys)
        if model_w < 1e-6 and model_h < 1e-6:
            return 100

        pw, ph = paper_size(paper, orientation)
        vp_w_mm = pw - 2 * 15.0
        vp_h_mm = ph - 30.0 - 2 * 15.0

        # scale_denom tal que todo el modelo quepa con 85% del viewport
        fill = 0.85
        denom_from_w = (model_w * 1000.0) / (vp_w_mm * fill) if model_w > 0 else 1
        denom_from_h = (model_h * 1000.0) / (vp_h_mm * fill) if model_h > 0 else 1
        needed = max(denom_from_w, denom_from_h)

        # Escoge el siguiente múltiplo estándar hacia arriba
        for _, d in VIEWPORT_SCALES:
            if d >= needed:
                return d
        return 1000

    def _delete_layout(self, idx: int):
        """Elimina una lámina tras confirmación."""
        if idx < 0 or idx >= len(self.layouts):
            return
        name = self.layouts[idx].name
        if not messagebox.askyesno("Eliminar lámina",
                                   f"¿Eliminar '{name}'?", parent=self.root):
            return
        self.layouts.pop(idx)
        # Ajustar índice activo
        if self.active_layout_idx == idx:
            self._switch_to_model()
        elif self.active_layout_idx > idx:
            self.active_layout_idx -= 1
        self._build_layout_tabs()

    def _rename_layout_dialog(self, idx: int):
        """Diálogo inline para renombrar una lámina."""
        if idx < 0 or idx >= len(self.layouts):
            return
        dlg = ctk.CTkInputDialog(
            text=f"Nuevo nombre para '{self.layouts[idx].name}':",
            title="Renombrar lámina")
        nuevo = dlg.get_input()
        if nuevo and nuevo.strip():
            self.layouts[idx].name = nuevo.strip()
            self._build_layout_tabs()

    # ── Cambio de espacio ─────────────────────────────────────────────

    def _switch_to_model(self):
        """Activa el espacio modelo."""
        # Guardar vista del layout activo antes de salir
        if self.active_layout_idx >= 0:
            lay = self.layouts[self.active_layout_idx]
            lay.view_scale = self.scale
            lay.view_ox    = self.offset_x
            lay.view_oy    = self.offset_y
        # Restaurar vista del modelo
        sv = self._saved_model_view
        if sv:
            self.scale    = sv.get("scale",    self.scale)
            self.offset_x = sv.get("offset_x", self.offset_x)
            self.offset_y = sv.get("offset_y", self.offset_y)
        self.active_layout_idx = -1
        self._build_layout_tabs()
        self._pil_img_cache = None
        self._redraw_static()
        self._echo("Espacio modelo")

    def _switch_to_layout(self, idx: int):
        """Activa una lámina de papel."""
        if idx < 0 or idx >= len(self.layouts):
            return

        # Guardar vista actual
        if self.active_layout_idx == -1:
            # venimos del modelo
            self._saved_model_view = {
                "scale":    self.scale,
                "offset_x": self.offset_x,
                "offset_y": self.offset_y,
            }
        elif self.active_layout_idx >= 0:
            # venimos de otro layout
            prev = self.layouts[self.active_layout_idx]
            prev.view_scale = self.scale
            prev.view_ox    = self.offset_x
            prev.view_oy    = self.offset_y

        self.active_layout_idx = idx
        lay = self.layouts[idx]

        # Restaurar o calcular vista del layout
        if lay.view_scale > 0:
            self.scale    = lay.view_scale
            self.offset_x = lay.view_ox
            self.offset_y = lay.view_oy
        else:
            self._fit_layout_view(lay)

        self._build_layout_tabs()
        self._pil_img_cache = None
        self._redraw_static()
        self._echo(f"Layout: {lay.name}  ({lay.paper})")

    def _fit_layout_view(self, lay: LayoutSheet):
        """Calcula scale/offset para encajar el papel en el canvas."""
        cv = self.canvas
        W  = cv.winfo_width()  or 800
        H  = cv.winfo_height() or 600
        pw_mm, ph_mm = paper_size(lay.paper, lay.orientation)

        # Escala: encajar con un 10% de margen
        margin_factor = 0.88
        sx = W * margin_factor / pw_mm
        sy = H * margin_factor / ph_mm
        self.scale = min(sx, sy)

        # Centrar el papel en el canvas.
        # En paper space: sx = offset_x + wx*scale,  sy = offset_y - wy*scale
        # El centro del papel (pw/2, ph/2) debe quedar en (W/2, H/2):
        #   W/2 = offset_x + pw/2 * scale  → offset_x = W/2 - pw/2 * scale
        #   H/2 = offset_y - ph/2 * scale  → offset_y = H/2 + ph/2 * scale
        self.offset_x = W / 2 - (pw_mm / 2) * self.scale
        self.offset_y = H / 2 + (ph_mm / 2) * self.scale

    # ── Render del layout ──────────────────────────────────────────────

    def _redraw_static_layout(self, W: int, H: int):
        """Render asíncrono del paper space (misma arquitectura que model)."""
        lay = self.layouts[self.active_layout_idx]

        # Captura de estado en hilo principal
        _lay_scale  = self.scale
        _offset_x   = self.offset_x
        _offset_y   = self.offset_y
        _entities   = list(self.entities)
        _layers     = dict(self.layers)
        _block_defs = dict(self.block_defs)
        _eidx       = dict(self._entity_index)
        _ecell      = self._entity_cell
        _cv_bg      = self.cv_bg          # ← sigue el color del modelo automáticamente
        _renderer   = self._renderer
        _lay_snap   = lay                  # referencia (inmutable para render)
        _cfg        = self._leer_config_ia()

        self._render_cancel.set()
        cancel_ev = threading.Event()
        self._render_cancel = cancel_ev
        self._render_gen += 1
        gen = self._render_gen
        self._render_pending = True

        def _bg_render():
            try:
                img = render_layout_pil(
                    layout      = _lay_snap,
                    W           = W,
                    H           = H,
                    cv_bg       = _cv_bg,
                    lay_scale   = _lay_scale,
                    offset_x    = _offset_x,
                    offset_y    = _offset_y,
                    entities    = _entities,
                    layers      = _layers,
                    block_defs  = _block_defs,
                    entity_index= _eidx,
                    entity_cell = _ecell,
                    renderer    = _renderer,
                    config      = _cfg,
                    axis_color  = self.axis_color,
                )
            except Exception as err:
                img = None
                print(f"[WARN] layout render falló: {err}")
            if not cancel_ev.is_set() and gen == self._render_gen:
                self.root.after(0, lambda: self._apply_render(img, W, H, gen))

        threading.Thread(target=_bg_render, daemon=True).start()

    # ── Viewport: fit al modelo ────────────────────────────────────────

    def _layout_viewport_fit(self, lay_idx: int, vp_idx: int = 0):
        """Centra y escala el viewport para encajar todos los entities."""
        if lay_idx < 0 or lay_idx >= len(self.layouts):
            return
        lay = self.layouts[lay_idx]
        if vp_idx >= len(lay.viewports):
            return
        vp  = lay.viewports[vp_idx]
        vp2 = auto_fit_viewport(vp, self.entities)
        best = self._auto_scale_for_viewport(vp2, lay.paper, lay.orientation)
        lay.viewports[vp_idx] = ViewportDef(
            x=vp2.x, y=vp2.y, width=vp2.width, height=vp2.height,
            scale_denom=best,
            view_cx=vp2.view_cx, view_cy=vp2.view_cy,
            active=vp2.active,
        )
        lay.tb_escala = f"1:{best}"
        if self.active_layout_idx == lay_idx:
            self._pil_img_cache = None
            self._redraw_static()

    # ── Page Setup dialog ─────────────────────────────────────────────

    def _page_setup_dialog(self, idx: int):
        """Diálogo de configuración de lámina: papel, orientación, escala y viewports."""
        if idx < 0 or idx >= len(self.layouts):
            return
        lay = self.layouts[idx]

        dlg = ctk.CTkToplevel(self.root)
        dlg.title(f"Configurar lámina — {lay.name}")
        dlg.resizable(True, True)
        dlg.attributes("-topmost", True)
        W_dlg, H_dlg = 540, 700
        rx = self.root.winfo_x() + max(0, (self.root.winfo_width()  - W_dlg) // 2)
        ry = self.root.winfo_y() + max(0, (self.root.winfo_height() - H_dlg) // 2)
        dlg.geometry(f"{W_dlg}x{H_dlg}+{rx}+{ry}")
        dlg.minsize(460, 560)
        self.root.after(80, dlg.grab_set)

        BG = "#1E293B"; CARD = "#334155"; TX = "#F8FAFC"; TX2 = "#94A3B8"
        ACC = "#2563EB"; RED = "#DC2626"
        FB = ctk.CTkFont(size=12, weight="bold")
        F  = ctk.CTkFont(size=11)
        FS = ctk.CTkFont(size=10)

        ctk.CTkLabel(dlg, text=f"📐  {lay.name}", font=FB,
                     text_color=TX).pack(pady=(14, 4))

        sc = ctk.CTkScrollableFrame(dlg, fg_color=BG)
        sc.pack(fill="both", expand=True, padx=10, pady=4)

        def _row(label, widget_fn, **kw):
            f = ctk.CTkFrame(sc, fg_color="transparent")
            f.pack(fill="x", pady=3)
            ctk.CTkLabel(f, text=label, font=FS, text_color=TX2,
                         width=120, anchor="w").pack(side="left")
            widget_fn(f, **kw)

        # ── Papel ─────────────────────────────────────────────────────
        v_paper   = tk.StringVar(value=lay.paper)
        v_orient  = tk.StringVar(value="Horizontal" if lay.orientation=="H" else "Vertical")
        v_scale   = tk.StringVar(value=lay.tb_escala)
        v_proj    = tk.StringVar(value=lay.tb_proyecto)
        v_desc    = tk.StringVar(value=lay.tb_desc)
        v_firma   = tk.StringVar(value=lay.tb_firma)
        v_num     = tk.StringVar(value=lay.tb_numero)
        v_fecha   = tk.StringVar(value=lay.tb_fecha)

        def _om(parent, var, values, **kw):
            ctk.CTkOptionMenu(parent, variable=var, values=values,
                              height=26, font=F).pack(side="left", fill="x", expand=True)

        def _entry(parent, var, **kw):
            ctk.CTkEntry(parent, textvariable=var, height=26, font=F
                         ).pack(side="left", fill="x", expand=True)

        _row("Papel",       _om, var=v_paper,
             values=list(PAPER_SIZES_MM.keys()))
        _row("Orientación", _om, var=v_orient,
             values=["Horizontal", "Vertical"])
        _row("Escala VP1",  _om, var=v_scale,
             values=[lbl for lbl, _, _ in get_scales(
                 self._leer_config_ia().get("units", "metric"))])

        ctk.CTkLabel(sc, text="── Cuadro de Títulos ──",
                     font=FS, text_color=TX2).pack(pady=(10, 2))

        _row("Proyecto",    _entry, var=v_proj)
        _row("Descripción", _entry, var=v_desc)
        _row("Firma",       _entry, var=v_firma)
        _row("Nº Lámina",   _entry, var=v_num)
        _row("Fecha",       _entry, var=v_fecha)

        # Vista previa en tiempo real
        lbl_preview = ctk.CTkLabel(sc, text="",
                                   font=ctk.CTkFont(size=9), text_color="#64748B")
        lbl_preview.pack(pady=(4, 0))

        def _update_preview(*_):
            orient_str = v_orient.get()
            o = "H" if orient_str == "Horizontal" else "V"
            pw, ph = paper_size(v_paper.get(), o)
            lbl_preview.configure(
                text=f"Papel: {pw:.0f} × {ph:.0f} mm  |  VP1: {v_scale.get()}"
            )
        v_paper.trace_add("write", _update_preview)
        v_orient.trace_add("write", _update_preview)
        v_scale.trace_add("write", _update_preview)
        _update_preview()

        # ── Viewports adicionales ──────────────────────────────────────
        ctk.CTkLabel(sc, text="── Viewports ──",
                     font=FS, text_color=TX2).pack(pady=(12, 2))

        # Copia de trabajo: lista de dicts con los datos de cada VP
        _vp_data: list[dict] = []
        for vp in lay.viewports:
            _vp_data.append({
                "scale_denom": tk.StringVar(value=f"1:{vp.scale_denom}"),
                "label":       tk.StringVar(value=vp.label or ""),
                "active":      tk.BooleanVar(value=vp.active),
                "_vp_ref":     vp,   # referencia al ViewportDef original
            })

        vp_list_frame = ctk.CTkFrame(sc, fg_color="transparent")
        vp_list_frame.pack(fill="x", pady=2)

        def _rebuild_vp_list():
            for w in vp_list_frame.winfo_children():
                w.destroy()
            for i, d in enumerate(_vp_data):
                row = ctk.CTkFrame(vp_list_frame, fg_color=CARD, corner_radius=6)
                row.pack(fill="x", pady=2, padx=2)

                # Número
                ctk.CTkLabel(row, text=f"VP{i+1}", font=FS, text_color=TX2,
                             width=32).pack(side="left", padx=(8, 4))

                # Label
                ctk.CTkEntry(row, textvariable=d["label"], height=24, font=FS,
                             placeholder_text="etiqueta",
                             width=100).pack(side="left", padx=4)

                # Escala
                ctk.CTkOptionMenu(row, variable=d["scale_denom"],
                                  values=[lbl for lbl, _, _ in get_scales(
                                      self._leer_config_ia().get("units","metric"))],
                                  height=24, font=FS,
                                  width=90).pack(side="left", padx=4)

                # Activo
                ctk.CTkSwitch(row, text="", variable=d["active"],
                              width=40, height=20).pack(side="left", padx=4)

                # Ajustar ⟲ (solo si hay entidades)
                ctk.CTkButton(row, text="⟲", width=28, height=24, font=FS,
                              fg_color="#1D4ED8", hover_color="#1E40AF",
                              command=lambda di=d: _fit_vp(di)
                              ).pack(side="left", padx=2)

                # Eliminar ✕ (solo si hay más de 1 viewport)
                if len(_vp_data) > 1:
                    ctk.CTkButton(row, text="✕", width=28, height=24, font=FS,
                                  fg_color=RED, hover_color="#B91C1C",
                                  command=lambda i2=i: _del_vp(i2)
                                  ).pack(side="left", padx=(2, 6))

        def _fit_vp(d: dict):
            """Ajusta view_cx/view_cy del VP al centroide del modelo."""
            if not self.entities:
                return
            xs = [pt[0] for e in self.entities for pt in (e.bbox_pts() or [])]
            ys = [pt[1] for e in self.entities for pt in (e.bbox_pts() or [])]
            if xs:
                d["_vp_ref"] = ViewportDef(
                    x=d["_vp_ref"].x, y=d["_vp_ref"].y,
                    width=d["_vp_ref"].width, height=d["_vp_ref"].height,
                    scale_denom=d["_vp_ref"].scale_denom,
                    view_cx=(min(xs)+max(xs))/2,
                    view_cy=(min(ys)+max(ys))/2,
                    active=d["_vp_ref"].active,
                    label=d["_vp_ref"].label,
                )

        def _del_vp(i: int):
            if len(_vp_data) <= 1:
                return
            _vp_data.pop(i)
            _rebuild_vp_list()

        def _add_vp():
            """Agrega un viewport posicionado automáticamente."""
            orient_str = v_orient.get()
            o = "H" if orient_str == "Horizontal" else "V"
            pw, ph = paper_size(v_paper.get(), o)
            n = len(_vp_data)
            # Divide el área útil en columnas iguales
            usable_w  = pw - 2 * MARGIN_MM
            usable_h  = ph - TB_HEIGHT_MM - 2 * MARGIN_MM
            col_w = usable_w / (n + 1)
            # Reposicionar todos los VPs existentes
            for i2, d2 in enumerate(_vp_data):
                ref = d2["_vp_ref"]
                d2["_vp_ref"] = ViewportDef(
                    x=MARGIN_MM + i2 * col_w, y=ref.y,
                    width=col_w - 2, height=usable_h,
                    scale_denom=ref.scale_denom,
                    view_cx=ref.view_cx, view_cy=ref.view_cy,
                    active=ref.active, label=ref.label,
                )
            # Nuevo VP en la última columna
            best = self._auto_scale_for_viewport(
                ViewportDef(x=0, y=MARGIN_MM+TB_HEIGHT_MM,
                            width=col_w-2, height=usable_h),
                v_paper.get(), o)
            new_vp = ViewportDef(
                x=MARGIN_MM + n * col_w,
                y=MARGIN_MM + TB_HEIGHT_MM,
                width=col_w - 2,
                height=usable_h,
                scale_denom=best,
                active=True,
                label=f"VP{n+1}",
            )
            new_vp = auto_fit_viewport(new_vp, self.entities)
            _vp_data.append({
                "scale_denom": tk.StringVar(value=f"1:{new_vp.scale_denom}"),
                "label":       tk.StringVar(value=new_vp.label),
                "active":      tk.BooleanVar(value=True),
                "_vp_ref":     new_vp,
            })
            _rebuild_vp_list()

        _rebuild_vp_list()

        ctk.CTkButton(sc, text="+ Agregar viewport", height=26, font=FS,
                      fg_color=CARD, hover_color="#475569",
                      command=_add_vp
                      ).pack(fill="x", padx=2, pady=(4, 8))

        def _aplicar():
            orient_str = v_orient.get()
            o = "H" if orient_str == "Horizontal" else "V"
            lay.paper       = v_paper.get()
            lay.orientation = o
            lay.tb_proyecto = v_proj.get()
            lay.tb_desc     = v_desc.get()
            lay.tb_firma    = v_firma.get()
            lay.tb_numero   = v_num.get()
            lay.tb_fecha    = v_fecha.get()

            # Escala del VP1 → actualiza tb_escala
            sc_str = v_scale.get()
            lay.tb_escala = sc_str
            try:
                denom_vp1 = int(sc_str.split(":")[1])
            except Exception:
                denom_vp1 = 100

            # Reconstruir viewports desde _vp_data
            new_vps: list[ViewportDef] = []
            for i2, d in enumerate(_vp_data):
                ref = d["_vp_ref"]
                try:
                    sc_d = int(d["scale_denom"].get().split(":")[1])
                except Exception:
                    sc_d = 100
                if i2 == 0:
                    # VP1: recalcular geometría para el nuevo papel
                    base = default_viewport(lay.paper, lay.orientation, denom_vp1)
                    base = auto_fit_viewport(base, self.entities)
                    # Si hay múltiples VPs, estrechar VP1
                    if len(_vp_data) > 1:
                        pw2, ph2 = paper_size(lay.paper, lay.orientation)
                        col_w2 = (pw2 - 2 * MARGIN_MM) / len(_vp_data)
                        base = ViewportDef(
                            x=MARGIN_MM, y=base.y,
                            width=col_w2 - 2, height=base.height,
                            scale_denom=denom_vp1,
                            view_cx=base.view_cx, view_cy=base.view_cy,
                            active=d["active"].get(),
                            label=d["label"].get(),
                        )
                    else:
                        base = ViewportDef(
                            x=base.x, y=base.y,
                            width=base.width, height=base.height,
                            scale_denom=denom_vp1,
                            view_cx=base.view_cx, view_cy=base.view_cy,
                            active=d["active"].get(),
                            label=d["label"].get(),
                        )
                    new_vps.append(base)
                else:
                    new_vps.append(ViewportDef(
                        x=ref.x, y=ref.y,
                        width=ref.width, height=ref.height,
                        scale_denom=sc_d,
                        view_cx=ref.view_cx, view_cy=ref.view_cy,
                        active=d["active"].get(),
                        label=d["label"].get(),
                    ))

            lay.viewports  = new_vps
            lay.view_scale = 0.0   # forzar recalc de vista al entrar
            self._build_layout_tabs()
            if self.active_layout_idx == idx:
                self._fit_layout_view(lay)
                self._pil_img_cache = None
                self._redraw_static()
            dlg.destroy()

        ctk.CTkButton(dlg, text="✓  Aplicar", height=30,
                      fg_color="#16A34A", hover_color="#15803D",
                      font=FB, command=_aplicar
                      ).pack(fill="x", padx=16, pady=(4, 2))
        ctk.CTkButton(dlg, text="Ajustar VP1 al modelo  ⟲",
                      height=26, fg_color=CARD, hover_color="#475569",
                      font=F,
                      command=lambda: (
                          self._layout_viewport_fit(idx),
                          _update_preview(),
                      )).pack(fill="x", padx=16, pady=(0, 10))

    def _preferencias_colores(self):
        """Diálogo de Preferencias → Colores (canvas + barra de comandos)."""
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("🎨  Preferencias — Colores")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        self.root.update_idletasks()
        px = self.root.winfo_x() + self.root.winfo_width()  // 2 - 220
        py = self.root.winfo_y() + self.root.winfo_height() // 2 - 200
        dlg.geometry(f"440x580+{px}+{py}")
        dlg.lift(); dlg.focus_force()
        self.root.after(80, dlg.grab_set)

        BG   = "#1E293B"; CARD = "#334155"; ACC = "#2563EB"
        BORD = "#475569"; TX   = "#F8FAFC"; TX2 = "#94A3B8"
        F14  = ctk.CTkFont(size=14, weight="bold")
        F12  = ctk.CTkFont(size=12)
        F11  = ctk.CTkFont(size=11)
        F10  = ctk.CTkFont(size=10)

        ctk.CTkLabel(dlg, text="🎨  Preferencias de Color",
                     font=F14, text_color=TX).pack(pady=(16, 4))
        ctk.CTkLabel(dlg, text="Los cambios se aplican en tiempo real.",
                     font=F10, text_color=TX2).pack(pady=(0, 8))

        _cv_var      = tk.StringVar(value=self.cv_bg)
        _cmd_var     = tk.StringVar(value=self.cmd_bar_bg)
        _cur_idle_var   = tk.StringVar(value=self.cursor_color_idle)
        _cur_active_var = tk.StringVar(value=self.cursor_color_active)

        _CANVAS_SWATCHES = [
            ("#0A0A0A", "Negro"),    ("#1C1C1E", "Gris oscuro"),
            ("#1E1E2E", "Noche"),    ("#2D2D2D", "Grafito"),
            ("#1A1A2E", "Marino"),   ("#0D1B2A", "Azul noche"),
            ("#2C2C2C", "Carbón"),   ("#3A3A3A", "Pizarra"),
        ]
        _CMD_SWATCHES = [
            ("#0D1117", "GitHub"),   ("#0F172A", "Slate"),
            ("#1E293B", "Navy"),     ("#1A1A2E", "Marino"),
            ("#111827", "Gray 9"),   ("#0A0A0A", "Negro"),
        ]

        def _preview(var):
            try:
                v = var.get().strip()
                if len(v) == 7 and v.startswith("#"):
                    if var is _cv_var:
                        self.cv_bg = v
                    elif var is _cmd_var:
                        self.cmd_bar_bg = v
                    elif var is _cur_idle_var:
                        self.cursor_color_idle = v
                    elif var is _cur_active_var:
                        self.cursor_color_active = v
                    self._aplicar_colores_live()
                    self._guardar_config_colores()   # auto-guardar en cada cambio
            except Exception:
                pass

        def _swatch_row(parent, var, swatches):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=(4, 0))
            for hex_c, tip in swatches:
                def _pick(h=hex_c, v=var):
                    v.set(h)
                    _preview(v)
                btn = tk.Button(row, bg=hex_c, width=3, height=1,
                                relief="solid", bd=1,
                                activebackground=hex_c,
                                cursor="hand2", command=_pick)
                btn.pack(side="left", padx=2)
                _Tooltip(btn, tip)

        def _refresh_swatch(var, sw):
            try:
                v = var.get().strip()
                if len(v) == 7 and v.startswith("#"):
                    sw.configure(bg=v)
            except Exception:
                pass

        def _hex_row(parent, var, label_txt):
            frm = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=8)
            frm.pack(fill="x", padx=12, pady=4)
            ctk.CTkLabel(frm, text=label_txt, font=F11,
                         text_color=TX, width=160, anchor="w").pack(
                side="left", padx=10, pady=8)
            ent = ctk.CTkEntry(frm, textvariable=var, width=110,
                               fg_color="#0F172A", border_color=BORD,
                               text_color=TX,
                               font=ctk.CTkFont(family="Courier New", size=12))
            ent.pack(side="left", padx=(0, 6), pady=6)
            swatch = tk.Frame(frm, width=30, height=24, bg=var.get(),
                              relief="solid", bd=1)
            swatch.pack(side="left", padx=4)
            var.trace_add("write", lambda *_: _refresh_swatch(var, swatch))

        # ── Sección canvas ────────────────────────────────────────
        sec1 = ctk.CTkFrame(dlg, fg_color=BG, corner_radius=10)
        sec1.pack(fill="x", padx=16, pady=(4, 6))
        ctk.CTkLabel(sec1, text="Fondo del Canvas",
                     font=F12, text_color=TX).pack(anchor="w", padx=12, pady=(8, 2))
        ctk.CTkLabel(sec1,
                     text="La cuadrícula se auto-deriva del color elegido.",
                     font=F10, text_color=TX2).pack(anchor="w", padx=12)
        _hex_row(sec1, _cv_var, "Color hex:")
        _swatch_row(sec1, _cv_var, _CANVAS_SWATCHES)
        ctk.CTkFrame(sec1, height=6, fg_color="transparent").pack()

        # ── Sección barra de comandos ─────────────────────────────
        sec2 = ctk.CTkFrame(dlg, fg_color=BG, corner_radius=10)
        sec2.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(sec2, text="Fondo de la Barra de Comandos",
                     font=F12, text_color=TX).pack(anchor="w", padx=12, pady=(8, 2))
        ctk.CTkLabel(sec2,
                     text="Color de fondo del historial y entry de comandos.",
                     font=F10, text_color=TX2).pack(anchor="w", padx=12)
        _hex_row(sec2, _cmd_var, "Color hex:")
        _swatch_row(sec2, _cmd_var, _CMD_SWATCHES)
        ctk.CTkFrame(sec2, height=6, fg_color="transparent").pack()

        # ── Sección colores del cursor ────────────────────────────
        _CUR_IDLE_SWATCHES = [
            ("#FFFFFF", "Blanco"),    ("#E2E8F0", "Gris claro"),
            ("#94A3B8", "Gris"),      ("#64748B", "Pizarra"),
            ("#CBD5E1", "Plata"),     ("#F1F5F9", "Casi blanco"),
        ]
        _CUR_ACTIVE_SWATCHES = [
            ("#22D3EE", "Cian"),      ("#4ADE80", "Verde"),
            ("#FBBF24", "Amarillo"),  ("#F87171", "Rojo"),
            ("#A78BFA", "Violeta"),   ("#60A5FA", "Azul"),
        ]
        sec_cur = ctk.CTkFrame(dlg, fg_color=BG, corner_radius=10)
        sec_cur.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(sec_cur, text="Color del Cursor",
                     font=F12, text_color=TX).pack(anchor="w", padx=12, pady=(8, 2))
        ctk.CTkLabel(sec_cur,
                     text="Inactivo = sin comando  |  Activo = comando recibido",
                     font=F10, text_color=TX2).pack(anchor="w", padx=12)
        _hex_row(sec_cur, _cur_idle_var,   "Inactivo (idle):")
        _swatch_row(sec_cur, _cur_idle_var,   _CUR_IDLE_SWATCHES)
        _hex_row(sec_cur, _cur_active_var, "Activo (comando):")
        _swatch_row(sec_cur, _cur_active_var, _CUR_ACTIVE_SWATCHES)
        ctk.CTkFrame(sec_cur, height=6, fg_color="transparent").pack()

        # ── Traza de los vars del cursor para preview en tiempo real ──
        _cur_idle_var.trace_add(  "write", lambda *_: _preview(_cur_idle_var))
        _cur_active_var.trace_add("write", lambda *_: _preview(_cur_active_var))

        # ── Sección: Ejes y Selección ─────────────────────────────
        sec_ax = ctk.CTkFrame(dlg, fg_color=CARD, corner_radius=10)
        sec_ax.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(sec_ax, text="Color de Ejes y Selección",
                     font=F12, text_color=TX).pack(anchor="w", padx=12, pady=(8, 2))
        ctk.CTkLabel(sec_ax, text="Ejes = líneas X/Y en el origen  ·  Selección = borde de entidad seleccionada",
                     font=F10, text_color=TX2).pack(anchor="w", padx=12)
        _axis_var   = tk.StringVar(value=self.axis_color)
        _sel_var    = tk.StringVar(value=self.select_color)
        _hex_row(sec_ax, _axis_var, "Ejes (X/Y):")
        _swatch_row(sec_ax, _axis_var, [
            ("#333333","Gris oscuro"), ("#444444","Gris"), ("#555555","Medio"),
            ("#1A3A1A","Verde oscuro"), ("#1A1A3A","Azul oscuro"), ("#3A1A1A","Rojo oscuro"),
        ])
        _hex_row(sec_ax, _sel_var, "Selección:")
        _swatch_row(sec_ax, _sel_var, [
            ("#FFD700","Dorado"), ("#00FFFF","Cian"), ("#FF6B6B","Coral"),
            ("#4ADE80","Verde"), ("#A78BFA","Violeta"), ("#60A5FA","Azul"),
        ])
        ctk.CTkFrame(sec_ax, height=6, fg_color="transparent").pack()
        _axis_var.trace_add("write", lambda *_: _preview(_axis_var))
        _sel_var.trace_add( "write", lambda *_: _preview(_sel_var))

        # ── Sección: Grilla ───────────────────────────────────────
        sec_gr = ctk.CTkFrame(dlg, fg_color=CARD, corner_radius=10)
        sec_gr.pack(fill="x", padx=16, pady=(0, 6))
        ctk.CTkLabel(sec_gr, text="Tamaño de Grilla",
                     font=F12, text_color=TX).pack(anchor="w", padx=12, pady=(8, 2))
        ctk.CTkLabel(sec_gr, text="Unidades en metros (ej: mayor=1.0, menor=0.25)",
                     font=F10, text_color=TX2).pack(anchor="w", padx=12)
        _gmaj_var = tk.StringVar(value=str(self.grid_major))
        _gmin_var = tk.StringVar(value=str(self.grid_minor))

        def _gr_row(label, var):
            r = ctk.CTkFrame(sec_gr, fg_color="transparent")
            r.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(r, text=label, font=F10, text_color=TX2,
                         width=120, anchor="w").pack(side="left")
            ctk.CTkEntry(r, textvariable=var, width=80, height=24,
                         font=F10).pack(side="left")

        _gr_row("Mayor (m):", _gmaj_var)
        _gr_row("Menor (m):", _gmin_var)
        ctk.CTkFrame(sec_gr, height=6, fg_color="transparent").pack()

        # ── Botones ───────────────────────────────────────────────
        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(4, 12))

        def _aplicar():
            try:
                def _ok(v): return len(v) == 7 and v.startswith("#")
                cv_v    = _cv_var.get().strip()
                cmd_v   = _cmd_var.get().strip()
                idle_v  = _cur_idle_var.get().strip()
                act_v   = _cur_active_var.get().strip()
                ax_v    = _axis_var.get().strip()
                sel_v   = _sel_var.get().strip()
                if _ok(cv_v):   self.cv_bg               = cv_v
                if _ok(cmd_v):  self.cmd_bar_bg           = cmd_v
                if _ok(idle_v): self.cursor_color_idle    = idle_v
                if _ok(act_v):  self.cursor_color_active  = act_v
                if _ok(ax_v):   self.axis_color           = ax_v
                if _ok(sel_v):  self.select_color         = sel_v
                try:
                    gmaj = float(_gmaj_var.get())
                    gmin = float(_gmin_var.get())
                    if gmaj > 0 and gmin > 0:
                        self.grid_major = gmaj
                        self.grid_minor = gmin
                except ValueError:
                    pass
                self._guardar_config_colores()
                self._aplicar_colores_live()
            except Exception as ex:
                self._echo(f"!! Color inválido: {ex}")

        def _restablecer():
            _cv_var.set("#1C1C1E")
            _cmd_var.set("#0D1117")
            _cur_idle_var.set("#FFFFFF")
            _cur_active_var.set("#22D3EE")
            _axis_var.set(CV_AXIS)
            _sel_var.set(CV_SELECT)
            _gmaj_var.set("1.0")
            _gmin_var.set("0.25")
            _aplicar()

        ctk.CTkButton(btn_row, text="Restablecer", width=100, height=30,
                      fg_color=CARD, hover_color=BORD,
                      border_width=1, border_color=BORD,
                      font=F11, command=_restablecer).pack(side="left", padx=(0, 4))
        ctk.CTkButton(btn_row, text="Cancelar", width=90, height=30,
                      fg_color=CARD, hover_color=BORD,
                      border_width=1, border_color=BORD,
                      font=F11, command=dlg.destroy).pack(side="right", padx=(4, 0))
        ctk.CTkButton(btn_row, text="Aplicar y Guardar", width=140, height=30,
                      fg_color=ACC, hover_color="#1D4ED8",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=_aplicar).pack(side="right", padx=(0, 4))

    def _update_flags(self):
        # Botones de modo: verde relleno = activo / gris = inactivo
        # Cachear estado — configure() dispara _draw() en CTk (~500ms), solo llamar si cambió
        def _flag(btn, active):
            if getattr(btn, '_fl_active', None) is active:
                return
            btn._fl_active = active
            btn.configure(
                fg_color=UI_SUCC if active else UI_CARD,
                text_color=UI_BG  if active else UI_TEXT2)
        _flag(self._btn_snap,  self.snap_on)
        _flag(self._btn_grid,  self.grid_on)
        _flag(self._btn_ortho, self.ortho)
        names = {
            "select":   "SELECCIONAR [S]",
            "line":     "LÍNEA [L]",
            "polyline": "POLILÍNEA [PL]  C=cerrar  Enter=fin",
            "rect":     "RECTÁNGULO [REC]",
            "circle":   "CÍRCULO [C]  → centro · radio",
            "arc":      "ARCO [A]  → inicio · punto en arco · final",
            "text":     "TEXTO [T]",
        }
        _tool_text = names.get(self.tool, self.tool.upper())
        if getattr(self._lbl_tool, '_fl_text', None) != _tool_text:
            self._lbl_tool._fl_text = _tool_text
            self._lbl_tool.configure(text=_tool_text)

    def _update_undo_btns(self):
        """Actualiza el estado visual de los botones Deshacer/Rehacer."""
        if not hasattr(self, "_btn_undo"):
            return
        can_undo = len(self._undo_stack) > 1
        can_redo = bool(self._redo_stack)
        if getattr(self._btn_undo, '_fl_active', None) is not can_undo:
            self._btn_undo._fl_active = can_undo
            self._btn_undo.configure(fg_color=UI_ACC   if can_undo else UI_CARD,
                                     text_color=UI_TEXT if can_undo else UI_TEXT2)
        if getattr(self._btn_redo, '_fl_active', None) is not can_redo:
            self._btn_redo._fl_active = can_redo
            self._btn_redo.configure(fg_color="#7C3AED" if can_redo else UI_CARD,
                                     text_color=UI_TEXT  if can_redo else UI_TEXT2)

    def _highlight_op_btn(self, accion: str | None):
        """Resalta el botón de operación activa con ámbar; limpia los demás."""
        prev = getattr(self, '_highlighted_op', None)
        if prev == accion:
            return
        if prev is not None:
            b = self._op_btns.get(prev)
            if b:
                try: b.configure(fg_color=UI_CARD)
                except Exception: pass
        if accion is not None:
            b = self._op_btns.get(accion)
            if b:
                try: b.configure(fg_color="#D97706")
                except Exception: pass
        self._highlighted_op = accion

    # ─── Propiedad _op_mode con auto-update ──────────────────────────
    #
    # DISEÑO PARA COMANDOS FUTUROS:
    #   Cualquier `self._op_mode = "nuevo_estado"` actualiza automáticamente
    #   el hint de la barra de comandos.  Para que el nuevo estado muestre
    #   un mensaje, basta con:
    #     1. Agregar la cadena en _STRINGS  (cad/i18n.py)
    #     2. Agregar la clave en OP_PROMPT_KEYS  (cad/i18n.py)
    #   No se necesita ningún otro cambio en engine.py.
    #
    @property
    def _op_mode(self) -> str:
        return self._op_mode_val

    @_op_mode.setter
    def _op_mode(self, value: str) -> None:
        self._op_mode_val = value
        # Actualizar hint solo cuando la UI ya está construida
        if hasattr(self, "_cmd_hint_lbl"):
            self._update_prompt()

    # ─── Control de herramienta ───────────────────────────────────
    def _update_prompt(self):
        """Actualiza el prompt paso a paso en la barra de comando (estilo AutoCAD)."""
        if self._op_mode:
            i18n_key = OP_PROMPT_KEYS.get(self._op_mode, "")
            txt = t(i18n_key) if i18n_key else ""
        else:
            n   = len(self.draw_pts)
            key = (self.tool, n)
            if key not in TOOL_PROMPT_KEYS:
                steps = [k[1] for k in TOOL_PROMPT_KEYS if k[0] == self.tool]
                best  = max((s for s in steps if s <= n), default=None)
                key   = (self.tool, best) if best is not None else None
            i18n_key = TOOL_PROMPT_KEYS.get(key, "") if key else ""
            txt = t(i18n_key) if i18n_key else ""

        # Solo actualizar hint si el usuario no está escribiendo
        if not self._cmd_var.get():
            self._cmd_hint_lbl.configure(
                text=txt,
                fg=("#94A3B8" if not txt else "#CBD5E1"),
            )

    def _salir_herramienta(self) -> bool:
        """
        Finaliza la herramienta o operación activa y regresa al modo select.
        Retorna True si había algo activo (hubo salida), False si ya estaba idle.

        Usado por Enter y clic derecho para implementar el ciclo:
          activo → salir → idle → repetir último → activo → …
        """
        # Herramienta de dibujo activa
        if self.tool != "select":
            # Polilínea/Spline con ≥2 puntos: confirmar antes de salir
            if self.tool == "polyline" and len(self.draw_pts) >= 2:
                self._fin_pline()
            elif self.tool == "spline" and len(self.draw_pts) >= 2:
                self._fin_spline()
            elif self.tool == "cloud" and len(self.draw_pts) >= 3:
                self._commit_cloud()
            elif self.tool == "leader" and len(self.draw_pts) >= 2:
                self._commit_leader()
            else:
                self._dyn_clear()
                self.draw_pts.clear()
            self._set_tool("select")
            return True
        # Operación de edición activa (move/copy/rotate/etc.)
        if self._op_mode:
            self._cancelar()
            return True
        return False  # idle — nada que salir

    def _set_tool(self, tool: str):
        self._dyn_clear()
        self.draw_pts.clear()
        self._op_mode = ""; self._op_pts = []; self._op_sel = []; self._op_data = {}
        self._grips = []; self._hot_grip = None; self._hover_grip = None
        self._grip_drag_mode = False
        self._lbl_op.configure(text="")
        self.tool = tool
        # Guardar último comando para poder repetirlo con Enter/clic derecho
        if tool in ("line", "polyline", "spline", "rect", "circle", "arc", "text",
                    "ellipse", "polygon", "xline", "cloud", "leader"):
            self._last_cmd_name = tool
        for t, b in self._tool_btns.items():
            _c = UI_ACC if t == tool else UI_CARD
            if getattr(b, '_fl_fg', None) != _c:
                b._fl_fg = _c
                b.configure(fg_color=_c)
        self._highlight_op_btn(None)
        # POLYGON: pedir número de lados ANTES del primer clic
        if tool == "polygon":
            self._op_mode = "polygon_sides"
            self._lbl_op.configure(
                text=f"POLÍGONO — Número de lados (actual: {self._polygon_sides})  "
                     f"→ escribe y Enter, o Enter para mantener:")
        elif tool == "text":
            self._lbl_op.configure(
                text=f"TEXTO — Altura actual: {self._text_last_height:.2f} m  "
                     f"→ clic para colocar:")
        self._update_prompt()
        self._redraw_dynamic()

    def _cancelar(self, *_):
        # ESC prioridad 0: limpiar buffer de teclado si hay texto
        if self._kbd_buf:
            self._kbd_buf = ""
            self._redraw_dynamic()
            return
        # Esc con DYN activo: primero limpia el buffer, segundo Esc cancela la herramienta
        if self.dyn_on and (self._dyn_buf or any(v is not None for v in self._dyn_locked)):
            self._dyn_clear()
            self._redraw_dynamic()
            return
        if self._hot_grip is not None or self._grip_drag_mode:
            self._hot_grip = None
            self._hover_grip = None
            self._grip_drag_mode = False
            self._redraw_dynamic()
            return
        if self._grips:
            # Escape con grips visibles → deseleccionar todo y quitar grips
            for e in self.entities:
                e.selected = False
            self._grips = []; self._hot_grip = None; self._hover_grip = None
            self._lbl_prop.configure(text="—\nNinguna entidad\nseleccionada")
            self._redraw_static()
            return
        if self._op_mode:
            # PAN mode: restaurar cursor antes de limpiar
            if self._op_mode == "pan_mode":
                self.canvas.configure(cursor="")
            # Special: measure shows total on ESC
            if self._op_mode in ("measure_p1", "measure_next") and self._measure_total > 0:
                self._echo(f"MEASURE  total acumulado = {self._measure_total:.4f} m")
                self._measure_total = 0.0; self._measure_last_pt = None
            for e in self.entities:
                e.selected = False
            self._op_mode = ""; self._op_pts = []; self._op_sel = []; self._op_data = {}
            self._grips = []; self._hot_grip = None; self._hover_grip = None
            # Limpiar selección de bloque activo al cancelar INSERT
            if hasattr(self, '_blk_selected'):
                self._blk_selected.set("")
            self._lbl_op.configure(text="")
            self._highlight_op_btn(None)
            self._update_prompt()
            self._redraw_static(); return
        if self.draw_pts:
            self._dyn_clear()
            self.draw_pts.clear()
            self._update_prompt()
            self._redraw_dynamic()
        else:
            self._set_tool("select")

    def _toggle(self, flag: str):
        if flag == "snap":   self.snap_on = not self.snap_on
        elif flag == "grid": self.grid_on = not self.grid_on
        elif flag == "ortho":self.ortho   = not self.ortho
        self._redraw()

    def _toggle_dyn(self, *_):
        self.dyn_on = not self.dyn_on
        self._dyn_clear()
        self._echo(f"DYN {'ON — escribí directo en el canvas' if self.dyn_on else 'OFF'}")
        self._redraw_dynamic()

    # ─── Dynamic Input (DYN) — todos los tools ────────────────────

    # Op-modes que DYN acepta (además de draw_pts tools)
    _DYN_OPS = ("rotate_angle", "scale_factor",
                 "move_dest", "copy_dest", "mirror_p2", "offset_dist",
                 "polygon_sides", "fillet_r", "chamfer_d",
                 "image_width",
                 "array_pol_n")

    def _dyn_active(self) -> bool:
        if not self.dyn_on:
            return False
        if self._op_mode:
            return self._op_mode in self._DYN_OPS
        return (
            (self.tool in ("line", "polyline", "arc") and len(self.draw_pts) >= 1)
            or (self.tool == "circle"  and len(self.draw_pts) == 1)
            or (self.tool == "rect"    and len(self.draw_pts) == 1)
            or (self.tool == "ellipse" and len(self.draw_pts) >= 1)
        )

    def _dyn_fields(self) -> list:
        """Campos activos para el contexto actual."""
        ortho = self.ortho
        if not self._op_mode:
            if self.tool in ("line", "polyline", "arc"):
                return ["dist"] if ortho else ["dist", "ang"]
            if self.tool == "circle":
                return ["r_d"]          # un solo campo, Tab cambia modo r↔d
            if self.tool == "rect":
                return ["width", "height"]
            if self.tool == "ellipse":
                return ["dist"] if ortho else ["dist", "ang"]
        else:
            if self._op_mode == "rotate_angle":  return ["ang"]
            if self._op_mode == "scale_factor":  return ["factor"]
            if self._op_mode == "offset_dist":   return ["dist"]
            if self._op_mode == "polygon_sides": return ["sides"]
            if self._op_mode == "fillet_r":      return ["r"]
            if self._op_mode == "chamfer_d":     return ["d"]
            if self._op_mode == "image_width":   return ["w"]
            if self._op_mode == "array_pol_n":   return ["n_pol"]
            if self._op_mode in ("move_dest", "copy_dest", "mirror_p2"):
                return ["dist"] if ortho else ["dist", "ang"]
        return []

    def _dyn_clear(self):
        self._dyn_buf    = ""
        self._dyn_field  = 0
        self._dyn_locked = [None, None, None]

    # ── Cálculo de punto destino (line-like) ──────────────────────
    def _dyn_from_pt(self):
        """Punto de partida para cálculo dist+ang según contexto."""
        if not self._op_mode:
            return self.draw_pts[-1] if self.draw_pts else None
        if self._op_mode in ("move_dest", "copy_dest"):
            return self._op_pts[0] if self._op_pts else None
        if self._op_mode == "mirror_p2":
            return self._op_data.get("p1")
        return None

    def _dyn_pt(self):
        """Punto destino (line / arc / move / copy / mirror). None si faltan datos."""
        from_pt = self._dyn_from_pt()
        if from_pt is None:
            return None
        dist = self._dyn_locked[0]
        if dist is None:
            if self._dyn_field == 0 and self._dyn_buf:
                try:    dist = float(self._dyn_buf)
                except: return None
            else:
                return None
        angle = self._dyn_locked[1]
        if angle is None and self._dyn_field == 1 and self._dyn_buf:
            try:    angle = float(self._dyn_buf)
            except: pass
        px, py = from_pt
        if angle is not None:
            ang_rad = math.radians(angle)
        else:
            mx, my = self.mouse_w
            dx, dy = mx - px, my - py
            if abs(dx) < 1e-9 and abs(dy) < 1e-9:
                return None
            ang_rad = math.atan2(dy, dx)
            if self.ortho:
                ang_rad = math.radians(round(math.degrees(ang_rad) / 90) * 90)
        return (px + dist * math.cos(ang_rad), py + dist * math.sin(ang_rad))

    def _dyn_rect_pt(self):
        """Segunda esquina del RECT desde el DYN (width, height)."""
        if not self.draw_pts:
            return None
        x1, y1 = self.draw_pts[0]
        w = self._dyn_locked[0]
        if w is None and self._dyn_field == 0 and self._dyn_buf:
            try:    w = float(self._dyn_buf)
            except: return None
        if w is None:
            return None
        h = self._dyn_locked[1]
        if h is None and self._dyn_field == 1 and self._dyn_buf:
            try:    h = float(self._dyn_buf)
            except: pass
        if h is None:
            h = self.mouse_w[1] - y1   # usar Y del mouse mientras no se escribe altura
        return (x1 + w, y1 + h)

    # ── Ejecución ─────────────────────────────────────────────────
    def _dyn_execute(self):
        # Commit buffer al campo activo
        if self._dyn_buf:
            try:
                self._dyn_locked[self._dyn_field] = float(self._dyn_buf)
            except ValueError:
                self._dyn_clear(); return

        # ── LINE / POLYLINE / ARC ──────────────────────────────────
        if not self._op_mode and self.tool in ("line", "polyline", "arc"):
            pt = self._dyn_pt()
            if pt is None: return
            self._dyn_clear()
            self.mouse_w = pt
            self._click_draw(*pt)
            self.canvas.focus_set()
            self._redraw_dynamic()

        # ── CIRCLE ────────────────────────────────────────────────
        elif not self._op_mode and self.tool == "circle" and self.draw_pts:
            raw = self._dyn_locked[0]
            if raw is None:
                try:    raw = float(self._dyn_buf) if self._dyn_buf else None
                except: raw = None
            if raw is None or raw <= 0:
                self._dyn_clear(); return
            r = raw / 2.0 if self._dyn_circ_mode == "d" else raw
            cx, cy = self.draw_pts[0]
            self._dyn_clear(); self._dyn_circ_mode = "r"
            self._add_entity(Circle(cx=cx, cy=cy, radius=r, layer=self.active_layer))
            self.draw_pts.clear(); self._lbl_op.configure(text="")
            self._redraw()

        # ── RECT ──────────────────────────────────────────────────
        elif not self._op_mode and self.tool == "rect" and self.draw_pts:
            pt2 = self._dyn_rect_pt()
            if pt2 is None: return
            self._dyn_clear()
            self.mouse_w = pt2
            self._click_draw(*pt2)
            self._redraw()

        # ── ROTATE ────────────────────────────────────────────────
        elif self._op_mode == "rotate_angle":
            val = self._dyn_locked[0]
            if val is None: return
            self._dyn_clear(); self._apply_rotate(val)

        # ── SCALE ─────────────────────────────────────────────────
        elif self._op_mode == "scale_factor":
            val = self._dyn_locked[0]
            if val is None: return
            self._dyn_clear(); self._apply_scale(val)

        # ── OFFSET: confirmar distancia → pasar a selección ───────
        elif self._op_mode == "offset_dist":
            val = self._dyn_locked[0]
            # Enter sin escribir nada → usar distancia guardada
            if val is None and not self._dyn_buf:
                val = self._offset_last_dist
            elif val is None:
                try:    val = float(self._dyn_buf) if self._dyn_buf else None
                except: val = None
            if val is None or val <= 0:
                self._dyn_clear(); return
            self._dyn_clear()
            self._offset_last_dist = val          # persistir para próximo uso
            self._op_data["offset_dist"] = val
            self._op_mode = "offset_sel"; self._op_pts = []
            self._lbl_op.configure(text=f"OFFSET  d={val:.3f} — Clic en entidad:")
            self.canvas.focus_set()
            self._redraw_dynamic()

        # ── MOVE / COPY dest ──────────────────────────────────────
        elif self._op_mode in ("move_dest", "copy_dest"):
            pt = self._dyn_pt()
            if pt is None: return
            self._dyn_clear()
            self.mouse_w = pt
            self._op_pts.append(pt)
            self._handle_op()
            self.canvas.focus_set()
            self._redraw_dynamic()

        # ── ELLIPSE paso 2/3 ─────────────────────────────────────
        elif not self._op_mode and self.tool == "ellipse" and self.draw_pts:
            pt = self._dyn_pt()
            if pt is None: return
            self._dyn_clear()
            self.mouse_w = pt
            self._click_draw(*pt)
            self.canvas.focus_set()
            self._redraw_dynamic()

        # ── FILLET radio (estado inicial fillet_r o durante fillet_p1/p2)
        elif self._op_mode in ("fillet_r", "fillet_p1", "fillet_p2"):
            val = self._dyn_locked[0]
            if val is None:
                try: val = float(self._dyn_buf) if self._dyn_buf else None
                except: val = None
            if val is not None and val >= 0:
                self.fillet_radius = val
                self._dyn_clear()
                self._op_mode = "fillet_p1"; self._op_pts = []; self._op_data = {}
                self._lbl_op.configure(
                    text=f"FILLET  R={self.fillet_radius:.3f} — Clic en 1ª línea:")
                self.canvas.focus_set()
                self._redraw_dynamic()
                self._update_prompt()

        # ── CHAMFER distancia (estado inicial chamfer_d o durante chamfer_p1/p2)
        elif self._op_mode in ("chamfer_d", "chamfer_p1", "chamfer_p2"):
            val = self._dyn_locked[0]
            if val is None:
                try: val = float(self._dyn_buf) if self._dyn_buf else None
                except: val = None
            if val is not None and val >= 0:
                self.chamfer_d1 = val
                self.chamfer_d2 = val
                self._dyn_clear()
                self._op_mode = "chamfer_p1"; self._op_pts = []; self._op_data = {}
                self._lbl_op.configure(
                    text=f"CHAMFER  D={self.chamfer_d1:.3f} — Clic en 1ª línea:")
                self.canvas.focus_set()
                self._redraw_dynamic()
                self._update_prompt()

        # ── POLYGON sides ─────────────────────────────────────────
        elif self._op_mode == "polygon_sides":
            try:
                n = int(float(self._dyn_buf)) if self._dyn_buf else self._polygon_sides
                if n >= 3:
                    self._polygon_sides = int(n)
            except Exception:
                pass
            self._dyn_clear()
            self._op_mode = ""
            self._lbl_op.configure(
                text=f"POLÍGONO {self._polygon_sides} lados — clic en centro:")
            self._echo(f"POLYGON: {self._polygon_sides} lados")
            self._update_prompt()
            self.canvas.focus_set()
            self._redraw_dynamic()

        # ── IMAGE width → confirmar y crear entidad ───────────────
        elif self._op_mode == "image_width":
            val = self._dyn_locked[0]
            if val is None:
                try:    val = float(self._dyn_buf) if self._dyn_buf else None
                except: val = None
            if val is None or val <= 0:
                self._dyn_clear(); return
            asp = self._op_data.get("img_asp", 1.0)
            self._op_data["img_w"] = val
            self._op_data["img_h"] = val / asp if asp > 1e-9 else val
            self._dyn_clear()
            self._commit_image()

        # ── ARRAY POLAR ejecutar ─────────────────────────────────
        elif self._op_mode == "array_pol_n":
            try:
                n = int(self._dyn_buf) if self._dyn_buf else 6
                n = max(2, n)
            except Exception:
                n = 6
            cx, cy = self._op_data.get("pol_ctr", (0.0, 0.0))
            step_ang = 360.0 / n
            sel = self._op_sel
            self._push_undo()
            for k in range(1, n):
                for e in sel:
                    self.entities.append(e.rotated(cx, cy, step_ang * k))
            self._rebuild_snap_index(); self._redraw()
            self._echo(f"ARRAY POLAR  {n} items  ∠paso={step_ang:.1f}°  ⊙({cx:.3f},{cy:.3f})")
            self._dyn_clear()
            self._finish_op()

        # ── MIRROR p2 ────────────────────────────────────────────
        elif self._op_mode == "mirror_p2":
            pt = self._dyn_pt()
            if pt is None: return
            self._dyn_clear()
            self.mouse_w = pt
            self._op_pts.append(pt)
            self._handle_op()
            self.canvas.focus_set()
            self._redraw_dynamic()

    # ─── Handlers de teclado ──────────────────────────────────────

    def _on_canvas_return(self):
        # ltscale_input → Enter confirma el valor escrito en el kbd buffer
        if self._op_mode == "ltscale_input":
            buf = self._kbd_buf.strip()
            self._kbd_buf = ""
            try:
                self._apply_ltscale(float(buf))
            except ValueError:
                self._echo("LTSCALE — valor inválido")
            self._op_mode = ""
            self._lbl_op.configure(text="")
            return "break"
        # Prioridad 0: mirror_keep → Enter = No (conservar originales, igual a AutoCAD)
        if self._op_mode == "mirror_keep":
            ax, ay, bx, by = self._op_data["axis"]
            self._apply_mirror(ax, ay, bx, by, keep_source=True)
            return "break"
        # zoom_opts → Enter ejecuta factor escrito o Extents por defecto
        if self._op_mode == "zoom_opts":
            buf = self._kbd_buf.strip()
            self._kbd_buf = ""
            self._finish_op()
            if buf:
                self._cmd_zoom(buf)
            else:
                self._zoom_extents()
            return "break"

        # Prioridad 0b: array_type → Enter = Rectangular por defecto
        if self._op_mode == "array_type":
            sel = self._op_sel[:]
            self._finish_op()
            self.after(0, lambda: self._array_dialog(sel, "Rectangular"))
            return "break"
        # BREAK p2: Enter = mismo punto que p1 (Break at Point)
        if self._op_mode == "break_p2":
            p1  = self._op_data.get("break_p1", (0, 0))
            idx = self._op_data.get("break_idx")
            ent = self._op_data.get("break_ent")
            if idx is not None and ent is not None:
                self._do_break(idx, ent, *p1)
            self._op_mode = "break_p1"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="BREAK — Clic en entidad en el 1er punto:")
            self._update_prompt()
            return "break"
        # Prioridad 1: DYN activo con datos → ejecutar DYN
        if (self.dyn_on and self._dyn_active()
                and (self._dyn_buf or any(v is not None for v in self._dyn_locked))):
            self._dyn_execute()
            return "break"
        # Prioridad 1b: hatch_pts → cerrar y confirmar polígono
        if self._op_mode == "hatch_pts":
            self._commit_hatch()
            return "break"
        # Prioridad 1b·2: DSP → confirmar selección y pedir espaciado
        if self._op_mode == "dim_sp_pick":
            self._confirmar_dim_spacing()
            return "break"
        # Prioridad 1b·3: DCO / DBA → Enter termina la cadena
        if self._op_mode == "dim_chain_next":
            self._finish_op()
            return "break"
        # Prioridad 1c: block_name → leer nombre del bloque desde _kbd_buf / cmd_entry
        if self._op_mode == "block_name":
            nombre = self._kbd_buf.strip() or self._cmd_entry.get().strip()
            self._kbd_buf = ""
            self._commit_block_from_selection(nombre)
            return "break"
        # Prioridad 2a: erase_sel → Enter confirma y borra selección actual
        if self._op_mode == "erase_sel":
            self._erase()
            self._finish_op()
            return "break"
        # Prioridad 2: *_sel → confirmar selección y pasar a operación
        if self._op_mode in ("move_sel", "copy_sel",
                             "rotate_sel", "scale_sel", "mirror_sel",
                             "array_sel", "align_sel"):
            self._confirm_sel_to_base()
            return "break"
        # Prioridad 3: herramienta/operación activa → salir al select
        if self._salir_herramienta():
            return "break"
        # Prioridad 4: idle → ejecutar barra de comandos (o repetir último)
        self._ejecutar_comando()

    def _on_canvas_backspace(self):
        if self.dyn_on and self._dyn_active() and self._dyn_buf:
            self._dyn_buf = self._dyn_buf[:-1]
            self._redraw_dynamic()
            return "break"
        self._erase()

    def _on_canvas_tab(self):
        if not (self.dyn_on and self._dyn_active()):
            return
        # CIRCLE: Tab cicla modo r ↔ d (no avanza campo)
        if not self._op_mode and self.tool == "circle":
            self._dyn_circ_mode = "d" if self._dyn_circ_mode == "r" else "r"
            self._dyn_buf = ""; self._dyn_locked[0] = None
            self._redraw_dynamic()
            return "break"
        # Resto: commit + avanzar campo
        if self._dyn_buf:
            try:    self._dyn_locked[self._dyn_field] = float(self._dyn_buf)
            except: pass
        self._dyn_buf = ""
        nf = len(self._dyn_fields())
        if nf > 1:
            self._dyn_field = (self._dyn_field + 1) % nf
        self._redraw_dynamic()
        return "break"

    def _on_canvas_key(self, event):
        """Captura teclado en canvas.

        Prioridad:
          1. Ctrl+algo → ignorar (atajos del sistema)
          2. DYN activo + dígito/signo → alimentar DYN
          3. Herramienta de dibujo activa / op_mode activa → ignorar letras
             (no queremos acumular en _kbd_buf mientras se dibuja)
          4. Modo idle (select, sin operación) → acumular en _kbd_buf
             Backspace borra el último caracter.
             Enter ejecuta el buffer (igual que clic derecho).
        """
        if event.state & 0x4:   # Ctrl+ → dejar pasar al sistema
            return

        c = event.char
        k = event.keysym

        # ── MIRROR keep/delete: S/Y = borrar, N/Enter = conservar ───────
        if self._op_mode == "mirror_keep":
            if c and c.upper() in ("S", "Y"):
                ax, ay, bx, by = self._op_data["axis"]
                self._apply_mirror(ax, ay, bx, by, keep_source=False)
            elif c and c.upper() == "N":
                ax, ay, bx, by = self._op_data["axis"]
                self._apply_mirror(ax, ay, bx, by, keep_source=True)
            # cualquier otra tecla también ignorada (el panel ya muestra las opciones)
            return "break"

        # ── ZOOM opciones: E/A/W/P o factor numérico ──────────────────
        if self._op_mode == "zoom_opts":
            if c and c.upper() in ("E", "A"):
                self._finish_op()
                self._zoom_extents()
            elif c and c.upper() == "W":
                self._finish_op()
                self._op_mode = "zoom_w1"; self._op_pts = []
                self._lbl_op.configure(text="ZOOM W — Primera esquina:")
                self.canvas.focus_set()
                self._redraw_dynamic()
            elif c and c.upper() == "P":
                self._finish_op()
                if self._zoom_prev:
                    sc, ox, oy = self._zoom_prev.pop()
                    self.scale = sc; self.offset_x = ox; self.offset_y = oy
                    self._redraw()
                    self._echo("ZOOM P — vista anterior restaurada")
                else:
                    self._echo("ZOOM P — no hay vista anterior")
            elif c and (c.isdigit() or c in (".", "/", "-")):
                # Inicio de factor numérico o XP — acumular en kbd_buf
                self._kbd_buf += c
                self._redraw_dynamic()
            elif k in ("BackSpace",):
                self._kbd_buf = self._kbd_buf[:-1]
                self._redraw_dynamic()
            elif c and c.upper() == "X":
                # "xp" suffix — acumular
                self._kbd_buf += c
                self._redraw_dynamic()
            return "break"

        # ── ARRAY tipo: R = Rectangular, P = Polar ────────────────────
        if self._op_mode == "array_type":
            if c and c.upper() == "R":
                sel = self._op_sel[:]
                self._finish_op()
                self.after(0, lambda: self._array_dialog(sel, "Rectangular"))
            elif c and c.upper() == "P":
                # Polar: flujo en canvas — pedir centro con clic
                sel = self._op_sel[:]
                self._op_mode = "array_pol_ctr"
                self._op_sel  = sel
                self._op_pts  = []
                self._op_data = {}
                self._dyn_clear()
                self._lbl_op.configure(
                    text=f"ARRAY POLAR — {len(sel)} obj. — ⊙ Clic en centro de rotación:")
                self._update_prompt()
                self._redraw_dynamic()
            # Cualquier otra tecla: ignorar (esperamos R/P/Enter)
            return "break"

        # ── INSERT: R = rotar 90°, teclas numéricas = ángulo exacto ──────
        if self._op_mode == "insert_place":
            if c and c.upper() == "R":
                ang = self._op_data.get("angle", 0.0)
                self._op_data["angle"] = (ang + 90.0) % 360.0
                try: self._blk_angle_var.set(f"{self._op_data['angle']:.0f}")
                except Exception: pass
                self._redraw_dynamic()
                return "break"

        # ── Prioridad 1: DYN numérico ─────────────────────────────────
        if self.dyn_on and self._dyn_active():
            if c and (c.isdigit() or c in ".-"):
                if c == "-" and self._dyn_buf:
                    return
                self._dyn_buf += c
                self._redraw_dynamic()
                return "break"
            return   # cualquier otra tecla con DYN activo: ignorar

        # ── Herramienta activa o operación en curso → no acumular ────
        if self.tool != "select" or self._op_mode:
            return

        # ── Buffer de teclado en canvas ───────────────────────────────
        if k == "BackSpace":
            if self._kbd_buf:
                self._kbd_buf = self._kbd_buf[:-1]
                self._redraw_dynamic()
            return "break"

        if k in ("Return", "KP_Enter"):
            # Enter = ejecutar buffer (si hay algo)
            if self._kbd_buf:
                self._ejecutar_kbd_buf()
                return "break"
            return  # dejar que _on_canvas_return maneje el idle

        # Solo letras y dígitos (sin modificadores especiales)
        if c and (c.isalpha() or c.isdigit()):
            self._kbd_buf += c.upper()
            self._redraw_dynamic()
            return "break"

    def _ejecutar_kbd_buf(self):
        """Ejecuta el comando acumulado en _kbd_buf y limpia el buffer."""
        raw = self._kbd_buf.strip()
        self._kbd_buf = ""
        self._redraw_dynamic()
        if not raw:
            return
        accion = _CMD_ALIASES.get(raw)
        if accion:
            self._last_cmd_name = accion
            self._add_to_history(f"> {raw}", "cad")
            self._ejecutar_accion(accion, "")
        else:
            self._echo(f"!! Comando desconocido: '{raw}'  (F1 para ayuda)")

    def _on_root_key(self, event):
        """Captura teclado desde root cuando el canvas no tiene foco.

        Dos roles:
          1. Backup DYN: alimenta el buffer numérico cuando DYN está activo.
          2. Robo de foco: redirige al canvas y procesa comandos de teclado
             (kbd_buf) aunque el usuario no haya hecho clic en el área modelo.
        """
        focused = self.root.focus_get()
        if focused is self._cmd_entry:
            return          # el cmd_entry tiene su propio binding
        if event.state & 0x4:
            return          # Ctrl+algo → dejar al sistema

        # Si el canvas YA tiene el foco, _on_canvas_key lo habrá procesado;
        # no duplicar.
        if focused is self.canvas:
            return

        # No robar foco a ningún campo de texto de la UI (buscador de capas,
        # cuadro de propiedades, etc.).  Comprobar la clase Tk del widget
        # enfocado — Entry cubre tk.Entry y el interior de CTkEntry.
        if focused is not None:
            try:
                if focused.winfo_class() in ("Entry", "Text", "TEntry", "TCombobox"):
                    return
            except Exception:
                pass

        k = event.keysym; c = event.char

        # ── DYN activo: manejar aquí (igual que antes) ────────────────
        if self.dyn_on and self._dyn_active():
            if k == "Tab":
                return self._on_canvas_tab()
            if k in ("Return", "KP_Enter"):
                if self._dyn_buf or any(v is not None for v in self._dyn_locked):
                    self._dyn_execute(); return "break"
            if k == "BackSpace" and self._dyn_buf:
                self._dyn_buf = self._dyn_buf[:-1]
                self._redraw_dynamic(); return "break"
            if c and (c.isdigit() or c in ".-"):
                if c == "-" and self._dyn_buf: return
                self._dyn_buf += c
                self._redraw_dynamic(); return "break"
            return

        # ── Sin DYN y canvas sin foco: robar foco y reenviar al canvas ──
        # Permite escribir comandos (E, M, L…) nada más abrir el programa,
        # sin necesidad de hacer clic previo en el área modelo.
        self.canvas.focus_set()
        return self._on_canvas_key(event)

    # ─── Panel visual DYN ─────────────────────────────────────────

    def _draw_dyn_input(self):
        if not self.dyn_on or not self._dyn_active():
            return
        cv  = self.canvas
        flds = self._dyn_fields()
        if not flds:
            return

        sx, sy = (self.w2s(self.snap_pt[0], self.snap_pt[1])
                  if self.snap_pt else self.w2s(*self.mouse_w))

        def _val(field_idx, fmt=":.3f", suffix=""):
            v = self._dyn_locked[field_idx]
            if self._dyn_field == field_idx:
                return (self._dyn_buf or "") + "▌"
            return (format(v, fmt.lstrip(':')) + suffix if v is not None else "·")

        rows = []   # (icon, value_str, active)
        op = self._op_mode

        # ── OFFSET dist ── (antes de la regla ["dist"] genérica) ───
        if op == "offset_dist":
            rows.append(("↔", (self._dyn_buf or "") + "▌", True))

        # ── ROTATE ────────────────────────────────────────────────
        elif flds == ["ang"]:
            rows.append(("∠", (self._dyn_buf or "") + "▌°", True))

        # ── POLYGON sides ─────────────────────────────────────────
        elif flds == ["sides"]:
            cur = self._dyn_buf or str(self._polygon_sides)
            rows.append(("n", cur + "▌", True))

        # ── FILLET radio ───────────────────────────────────────────
        elif flds == ["r"]:
            cur = self._dyn_buf or f"{self.fillet_radius:.3f}"
            rows.append(("r", cur + "▌", True))

        # ── CHAMFER distancia ──────────────────────────────────────
        elif flds == ["d"]:
            cur = self._dyn_buf or f"{self.chamfer_d1:.3f}"
            rows.append(("D=", cur + "▌", True))

        # ── IMAGE ancho (con alto calculado automáticamente) ───────
        elif flds == ["w"]:
            asp = self._op_data.get("img_asp", 1.0)
            try:
                cur_w = float(self._dyn_buf) if self._dyn_buf else self._op_data.get("img_w", 1.0)
                cur_h = cur_w / asp if asp > 1e-9 else cur_w
            except Exception:
                cur_w = 1.0; cur_h = 1.0
            rows.append(("W=", (self._dyn_buf or f"{cur_w:.3f}") + "▌", True))
            rows.append(("H=", f"{cur_h:.3f}", False))

        # ── ARRAY POLAR count ─────────────────────────────────────
        elif flds == ["n_pol"]:
            cx, cy = self._op_data.get("pol_ctr", (0.0, 0.0))
            cur_n = self._dyn_buf or "6"
            try: step = f"{360.0 / max(2, int(cur_n)):.1f}°"
            except: step = "—"
            rows.append(("N=", cur_n + "▌", True))
            rows.append(("∠=", f"360° ÷ {cur_n} = {step}", False))
            rows.append(("⊙", f"({cx:.3f}, {cy:.3f})", False))

        # ── SCALE ─────────────────────────────────────────────────
        elif flds == ["factor"]:
            rows.append(("×", (self._dyn_buf or "") + "▌", True))

        # ── CIRCLE r ↔ d ──────────────────────────────────────────
        elif flds == ["r_d"]:
            icon = "⌀" if self._dyn_circ_mode == "d" else "r"
            rows.append((icon, (self._dyn_buf or "") + "▌", True))

        # ── RECT ──────────────────────────────────────────────────
        elif flds == ["width", "height"]:
            v0 = self._dyn_locked[0]
            s0 = (self._dyn_buf + "▌") if self._dyn_field == 0 else (f"{v0:.3f}" if v0 is not None else "·")
            rows.append(("w", s0, self._dyn_field == 0))
            v1 = self._dyn_locked[1]
            s1 = (self._dyn_buf + "▌") if self._dyn_field == 1 else (f"{v1:.3f}" if v1 is not None else "·")
            rows.append(("h", s1, self._dyn_field == 1))

        # ── LINE / POLYLINE / ARC / MOVE / COPY / MIRROR ──────────
        elif flds in (["dist"], ["dist", "ang"]):
            v0 = self._dyn_locked[0]
            s0 = (self._dyn_buf + "▌") if self._dyn_field == 0 else (f"{v0:.3f}" if v0 is not None else "·")
            rows.append(("d", s0, self._dyn_field == 0))
            if "ang" in flds:
                v1 = self._dyn_locked[1]
                s1 = (self._dyn_buf + "▌") if self._dyn_field == 1 else (f"{v1:.1f}°" if v1 is not None else "·")
                rows.append(("∠", s1, self._dyn_field == 1))

        if not rows:
            return

        ROW_H = 22; PAD_X = 10; PAD_Y = 6; PW = 148
        ph = len(rows) * ROW_H + PAD_Y * 2
        px = sx + 24; py = sy - ph - 10
        CW = cv.winfo_width(); CH = cv.winfo_height()
        if px + PW > CW - 4: px = sx - PW - 8
        if py < 4:            py = sy + 10

        cv.create_rectangle(px, py, px + PW, py + ph,
                            fill="#0C111A", outline="#2563EB", width=1, tags="dy")

        for i, (icon, val, active) in enumerate(rows):
            ry = py + PAD_Y + i * ROW_H
            if active:
                cv.create_rectangle(px+1, ry-1, px+PW-1, ry+ROW_H-3,
                                    fill="#1E3A5F", outline="", tags="dy")
            col_ic = "#2563EB" if active else "#475569"
            col_v  = "#F0F9FF" if active else "#64748B"
            fnt    = ("Courier New", 10, "bold") if active else ("Courier New", 10)
            cv.create_text(px+PAD_X,    ry+ROW_H//2-2, text=icon, fill=col_ic,
                           font=("Courier New", 10, "bold"), anchor="w", tags="dy")
            cv.create_text(px+PAD_X+20, ry+ROW_H//2-2, text=val,  fill=col_v,
                           font=fnt, anchor="w", tags="dy")

        # Contador de copias (COPY multi-destino) — etiqueta encima del panel
        if op == "copy_dest":
            n_copies = self._op_data.get("n_copies", 0)
            lbl = f"▶  COPIA  {n_copies + 1}"
            cv.create_text(px + PW // 2, py - 4,
                           text=lbl, fill="#34D399",
                           font=("Courier New", 9, "bold"),
                           anchor="s", tags="dy")

        # Hint contextual al pie
        if self.tool == "circle" and not op:
            hint = "Tab → ⌀" if self._dyn_circ_mode == "r" else "Tab → r"
        elif op == "copy_dest":
            n_copies = self._op_data.get("n_copies", 0)
            hint = f"Esc = fin  ({n_copies} cop.)" if n_copies else "Enter → misma dirección"
        elif len(rows) > 1:
            hint = "Tab → sig. campo"
        elif op in ("rotate_angle", "scale_factor", "offset_dist"):
            hint = "Enter → aplicar"
        else:
            hint = "Enter → confirmar"
        cv.create_text(px+PW-PAD_X, py+ph-2, text=hint, fill="#334155",
                       font=("Courier New", 7), anchor="se", tags="dy")

    def _repeat_last(self, *_):
        """SPACE = repetir último comando (comportamiento AutoCAD)."""
        if self._last_cmd_name:
            self._ejecutar_accion(self._last_cmd_name, "")

    # ═══════════════════════════════════════════════════════════════
    # LÍNEA DE COMANDO — Parser compatible AutoCAD
    # ═══════════════════════════════════════════════════════════════

    def _ejecutar_comando(self):
        raw = self._cmd_var.get().strip()
        if not raw:
            self._repeat_last(); return
        self._cmd_history.insert(0, raw)
        if len(self._cmd_history) > 50:
            self._cmd_history.pop()
        self._cmd_hist_idx = -1
        # Persistir nav history con debounce compartido
        if not self._hist_save_pending:
            self._hist_save_pending = True
            self.root.after(2000, self._flush_hist_save)

        # ── Modo IA: prefijo / ────────────────────────────────────
        if raw.startswith("/"):
            ia_prompt = raw[1:].strip()
            if ia_prompt:
                # Marcar streaming ANTES de limpiar el campo para que
                # _on_cmd_change no colapse el historial
                self._ia_streaming = True
                self._cmd_var.set("")
                self._ia_streaming = False
                self._ejecutar_ia(ia_prompt)
            else:
                self._cmd_var.set("")
            return

        self._cmd_var.set("")

        # Loguear al historial
        self._add_to_history(f"> {raw}", "cad")

        # ── Si zoom_opts está activo, la barra actúa como sub-opción ──
        # (las letras E/A/W/P se interceptan aquí en vez de ir al lookup
        #  de aliases, donde "E" dispararía ERASE en lugar de EXTENTS)
        if self._op_mode == "zoom_opts":
            self._finish_op()
            self._cmd_zoom(raw.strip())
            return

        # ── Si scrollspeed_input está activo, el input es el nuevo valor ──
        if self._op_mode == "scrollspeed_input":
            self._finish_op()
            self._ejecutar_accion("scrollspeed", raw.strip())
            return

        # Entrada numérica para operaciones activas
        if self._op_mode in ("rotate_angle", "scale_factor"):
            try:
                val = float(raw)
                if self._op_mode == "rotate_angle":
                    self._apply_rotate(val)
                else:
                    self._apply_scale(val)
                return
            except ValueError:
                pass   # no era número, sigue parsing normal

        # Coordenada absoluta: X,Y
        if "," in raw and not raw.upper().startswith("ZOOM"):
            self._parse_coord(raw); return

        parts  = raw.split(None, 1)
        token  = parts[0].upper()
        rest   = parts[1].strip() if len(parts) > 1 else ""

        # ZOOM con sub-opciones: Z E / Z A / Z W / Z número
        if token in ("Z", "ZOOM"):
            self._cmd_zoom(rest); return

        # Lookup en tabla de alias
        accion = _CMD_ALIASES.get(token)
        if accion:
            self._last_cmd_name = accion
            self._ejecutar_accion(accion, rest)
        else:
            self._echo(f"!! Comando desconocido: '{raw}'  (F1 para ayuda)")

    def _ejecutar_accion(self, accion: str, args: str):
        # ── Guard: bloquear herramientas de dibujo/edición en paper space ──
        _BLOCKED_IN_LAYOUT = {
            "line","polyline","spline","rect","circle","text","arc",
            "ellipse","polygon","xline","cloud","leader","hatch","insert",
            "image","block_cmd",
            "erase","move","copy","rotate","scale","mirror","offset",
            "trim","extend","fillet","chamfer","break","align","array",
            "matchprop","explode",
        }
        if getattr(self, "active_layout_idx", -1) >= 0 and accion in _BLOCKED_IN_LAYOUT:
            self._echo("⚠  En paper space no se puede editar el modelo — use MS para volver al espacio modelo")
            return

        # Registrar como último comando repetible (permite repetir con clic derecho).
        # Se excluyen comandos que no tienen sentido repetir (select, zoom, vistas, etc.).
        _NO_REPEAT = {"select", "zoom_e", "zoom_a", "zoom_p", "zoom_w",
                      "undo", "redo", "new", "open", "save", "saveas"}
        if accion not in _NO_REPEAT:
            self._last_cmd_name = accion

        # ── Herramientas de dibujo ─────────────────────────────────
        if accion in ("line","polyline","spline","rect","circle","text","arc",
                      "ellipse","polygon","xline","cloud","leader"):
            self._set_tool(accion)

        # ── Selección y edición ────────────────────────────────────
        elif accion == "select":
            self._set_tool("select")

        elif accion == "erase":
            sel = [e for e in self.entities if e.selected]
            if sel:
                self._erase()
            else:
                # Sin selección → entrar en modo de selección (pickbox + ventana/cruce)
                self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False; self._grips = []
                self._op_mode = "erase_sel"; self._op_sel = []; self._op_pts = []
                self._lbl_op.configure(
                    text="ERASE — ☐ Clic o ventana en entidades a borrar (Enter = confirmar):")
                self._highlight_op_btn(None)
                self._update_prompt()
                self.canvas.focus_set()
                self._redraw_dynamic()

        elif accion == "move":
            sel = [e for e in self.entities if e.selected]
            if sel:
                # Ya hay selección → ir directo al punto base
                self._dyn_clear()
                self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False; self._grips = []
                self._op_mode = "move_base"; self._op_sel = sel
                self._op_pts  = []
                self._lbl_op.configure(text="MOVE — Punto base:")
                self._highlight_op_btn("move")
            else:
                # Sin selección → cuadrito para seleccionar entidades
                self._dyn_clear()
                self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False; self._grips = []
                self._op_mode = "move_sel"; self._op_sel = []; self._op_pts = []
                self._lbl_op.configure(
                    text="MOVE — ☐ Clic en entidades a mover (Enter para confirmar):")
                self._highlight_op_btn("move")
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "copy":
            sel = [e for e in self.entities if e.selected]
            if sel:
                self._dyn_clear()
                self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False; self._grips = []
                for e in self.entities:
                    e.selected = False
                self._op_mode = "copy_base"; self._op_sel = sel
                self._op_pts  = []; self._op_data = {"n_copies": 0}
                self._lbl_op.configure(text="COPY — Punto base:")
                self._highlight_op_btn("copy")
            else:
                self._dyn_clear()
                self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False; self._grips = []
                self._op_mode = "copy_sel"; self._op_sel = []; self._op_pts = []
                self._lbl_op.configure(
                    text="COPY — ☐ Clic en entidades a copiar (Enter para confirmar):")
                self._highlight_op_btn("copy")
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "rotate":
            sel = [e for e in self.entities if e.selected]
            self._dyn_clear()
            self._hot_grip = None; self._hover_grip = None
            self._grip_drag_mode = False; self._grips = []
            if sel:
                self._op_mode = "rotate_base"; self._op_sel = sel; self._op_pts = []
                self._op_data = {}
                self._lbl_op.configure(text="ROTATE — Punto base:")
                self._highlight_op_btn("rotate")
            else:
                self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False; self._grips = []
                self._op_mode = "rotate_sel"; self._op_sel = []; self._op_pts = []
                self._lbl_op.configure(
                    text="ROTATE — ☐ Clic en entidades a rotar (Enter para confirmar):")
                self._highlight_op_btn("rotate")
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "scale":
            sel = [e for e in self.entities if e.selected]
            self._dyn_clear()
            self._hot_grip = None; self._hover_grip = None
            self._grip_drag_mode = False; self._grips = []
            if sel:
                self._op_mode = "scale_base"; self._op_sel = sel; self._op_pts = []
                self._op_data = {}
                self._lbl_op.configure(text="SCALE — Punto base:")
                self._highlight_op_btn("scale")
            else:
                self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False; self._grips = []
                self._op_mode = "scale_sel"; self._op_sel = []; self._op_pts = []
                self._lbl_op.configure(
                    text="SCALE — ☐ Clic en entidades a escalar (Enter para confirmar):")
                self._highlight_op_btn("scale")
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "mirror":
            sel = [e for e in self.entities if e.selected]
            self._dyn_clear()
            self._hot_grip = None; self._hover_grip = None
            self._grip_drag_mode = False; self._grips = []
            if sel:
                self._op_mode = "mirror_p1"; self._op_sel = sel; self._op_pts = []
                self._op_data = {}
                self._lbl_op.configure(text="MIRROR — Primer punto del eje:")
                self._highlight_op_btn("mirror")
            else:
                self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False; self._grips = []
                self._op_mode = "mirror_sel"; self._op_sel = []; self._op_pts = []
                self._lbl_op.configure(
                    text="MIRROR — ☐ Clic en entidades a reflejar (Enter para confirmar):")
                self._highlight_op_btn("mirror")
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "offset":
            self._op_data = {"offset_dist": self._offset_last_dist}
            self._op_mode = "offset_dist"
            self._op_pts = []
            self._lbl_op.configure(
                text=f"OFFSET — distancia [{self._offset_last_dist:.3f}] — Enter=aceptar o escribe nuevo valor:")
            self._highlight_op_btn("offset")
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "trim":
            # QuickTrim: va directo a trim_obj (sin paso previo de selección de borde)
            self._op_mode = "trim_obj"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="TR — Clic en el segmento a recortar:")
            self._highlight_op_btn("trim")
            self._update_prompt()

        elif accion == "extend":
            # QuickExtend: va directo a extend_obj (sin selección de límite previa)
            self._op_mode = "extend_obj"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="EX — Clic cerca del extremo a extender:")
            self._highlight_op_btn("extend")
            self._update_prompt()

        elif accion == "fillet":
            self._op_mode = "fillet_r"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(
                text=f"FILLET — Radio actual: {self.fillet_radius:.3f} m  "
                     f"→ escribe nuevo valor y Enter, o Enter para mantener:")
            self._highlight_op_btn(None)
            self.canvas.focus_set()
            self._update_prompt()
            self._redraw_dynamic()

        elif accion == "explode":
            self._explode()

        # ── Array ─────────────────────────────────────────────────────
        elif accion == "array":
            sel = [e for e in self.entities if e.selected]
            self._hot_grip = None; self._hover_grip = None
            self._grip_drag_mode = False; self._grips = []
            if sel:
                # Ya hay selección → pasar directo al selector de tipo
                self._op_mode = "array_type"; self._op_sel = sel
                self._op_pts = []; self._op_data = {}
                self._lbl_op.configure(
                    text=f"ARRAY — {len(sel)} objetos  →  [R] Rectangular   [P] Polar   Enter = R:")
                self._highlight_op_btn("array")
            else:
                # Sin selección → cuadrito para seleccionar entidades
                self._op_mode = "array_sel"; self._op_sel = []; self._op_pts = []; self._op_data = {}
                self._lbl_op.configure(
                    text="ARRAY — ☐ Clic en entidades a arreglar (Enter para confirmar):")
                self._highlight_op_btn("array")
            self._update_prompt()
            self.canvas.focus_set()
            self._redraw_dynamic()

        # ── Chamfer ───────────────────────────────────────────────────
        elif accion == "chamfer":
            self._op_mode = "chamfer_d"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(
                text=f"CHAMFER — Distancia actual: {self.chamfer_d1:.3f} m  "
                     f"→ escribe nuevo valor y Enter, o Enter para mantener:")
            self._highlight_op_btn(None)
            self.canvas.focus_set()
            self._update_prompt()
            self._redraw_dynamic()

        # ── Break ─────────────────────────────────────────────────────
        elif accion == "break_cmd":
            self._op_mode = "break_p1"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="BREAK — Clic en entidad en el punto de ruptura:")
            self._highlight_op_btn(None)
            self.canvas.focus_set()
            self._update_prompt()

        # ── Align ─────────────────────────────────────────────────────
        elif accion == "align_cmd":
            sel = [e for e in self.entities if e.selected]
            if sel:
                self._op_mode = "align_sp1"; self._op_pts = []
                self._op_sel = sel; self._op_data = {}
                self._lbl_op.configure(text="ALIGN — Punto fuente 1:")
                self._highlight_op_btn(None)
                self.canvas.focus_set()
                self._update_prompt()
            else:
                # Sin selección → entrar en modo selección igual que MOVE/COPY
                self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False; self._grips = []
                self._op_mode = "align_sel"; self._op_sel = []; self._op_pts = []
                self._lbl_op.configure(
                    text="ALIGN — ☐ Clic en entidades a alinear (Enter para confirmar):")
                self._highlight_op_btn(None)
                self._update_prompt()
                self.canvas.focus_set()

        # ── Measure ───────────────────────────────────────────────────
        elif accion == "measure":
            self._measure_total = 0.0
            self._measure_last_pt = None
            self._op_mode = "measure_p1"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="MEASURE — Especifique primer punto:")
            self._highlight_op_btn(None)
            self.canvas.focus_set()
            self._update_prompt()

        # ── Leader (op mode) ──────────────────────────────────────────
        elif accion == "leader_cmd":
            self._set_tool("leader")

        # ── Layer Make Current ─────────────────────────────────────────
        elif accion == "laymcur":
            self._op_mode = "laymcur"; self._op_pts = []
            self._lbl_op.configure(text="LAYMCUR — Clic en entidad para hacer su capa activa:")
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "matchprop":
            self._op_mode = "matchprop_src"; self._op_pts = []
            self._op_data = {"undo_pushed": False}   # undo único para todo el comando
            self._lbl_op.configure(text="MA — Clic en entidad fuente:")
            self._highlight_op_btn("matchprop")

        # ── Bloques ───────────────────────────────────────────────
        elif accion == "insert":
            self._cmd_insert(str(args))

        elif accion == "block_cmd":
            self._cmd_block(str(args))

        elif accion == "eattedit":
            self._cmd_eattedit(str(args))

        # ── Cotas ─────────────────────────────────────────────────
        elif accion == "hatch":
            self._ejecutar_hatch_dialog()

        elif accion == "image_cmd":
            import tkinter.filedialog as _fd
            try:
                from PIL import Image as _PILImg
                _PIL_AVAIL = True
            except ImportError:
                _PIL_AVAIL = False
            path = self._open_native_dialog(_fd.askopenfilename,
                title="Insertar imagen",
                filetypes=[
                    ("Imágenes", "*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp"),
                    ("Todos los archivos", "*.*"),
                ],
            )
            if not path:
                return
            asp = 1.0
            if _PIL_AVAIL:
                try:
                    with _PILImg.open(path) as _im:
                        _w, _h = _im.size
                        asp = _w / _h if _h > 0 else 1.0
                except Exception:
                    pass
            self._op_mode = "image_place"
            self._op_pts = []
            self._op_data = {"img_path": path, "img_asp": asp, "img_w": 1.0}
            self._lbl_op.configure(
                text=f"IMG — Clic en punto de origen (esquina inferior-izquierda):")
            self._highlight_op_btn(None)
            self._last_cmd_name = "image_cmd"
            self._update_prompt()
            self.canvas.focus_set()

        elif accion in ("dim_h", "dim_v", "dim_a"):
            dt = {"dim_h": "H", "dim_v": "V", "dim_a": "A"}[accion]
            self._op_data = {"dim_type": dt}
            self._op_mode = "dim_lp1"; self._op_pts = []
            self._lbl_op.configure(text=f"DIM-{dt} — Primer punto de extensión:")
            self._highlight_op_btn(accion)
            self._last_cmd_name = accion
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "dim_ang":
            self._op_data = {"dim_type": "ANG"}
            self._op_mode = "dim_ang_cen"; self._op_pts = []
            self._lbl_op.configure(text="DIMANG — Centro del ángulo:")
            self._highlight_op_btn("dim_ang")
            self._last_cmd_name = "dim_ang"
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "dim_r":
            self._op_data = {"dim_type": "R"}
            self._op_mode = "dim_r_obj"; self._op_pts = []
            self._lbl_op.configure(text="DIMR — Clic en un círculo o en el centro:")
            self._highlight_op_btn("dim_r")
            self._last_cmd_name = "dim_r"
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "dim_co":
            self._iniciar_dim_chain(modo="continue")

        elif accion == "dim_ba":
            self._iniciar_dim_chain(modo="baseline")

        elif accion == "dim_sp":
            self._iniciar_dim_spacing()

        elif accion == "dim_d":
            self._op_data = {"dim_type": "D"}
            self._op_mode = "dim_r_obj"; self._op_pts = []
            self._lbl_op.configure(text="DIMD — Clic en el círculo o en el centro:")
            self._highlight_op_btn("dim_d")
            self._last_cmd_name = "dim_d"
            self._update_prompt(); self.canvas.focus_set()

        elif accion == "dim_arc_len":
            self._op_data = {"dim_type": "ARC"}
            self._op_mode = "dim_arc_obj"; self._op_pts = []
            self._lbl_op.configure(text="DAR — Clic sobre el arco:")
            self._highlight_op_btn("dim_arc_len")
            self._last_cmd_name = "dim_arc_len"
            self._update_prompt(); self.canvas.focus_set()

        elif accion == "dim_ord":
            self._op_data = {"dim_type": "ORD"}
            self._op_mode = "dim_ord_p1"; self._op_pts = []
            self._lbl_op.configure(text="DOR — Punto a medir (snap a vértice):")
            self._highlight_op_btn("dim_ord")
            self._last_cmd_name = "dim_ord"
            self._update_prompt(); self.canvas.focus_set()

        # ── Vista ─────────────────────────────────────────────────
        elif accion in ("zoom_e","zoom_a"):
            self._zoom_extents()
        elif accion == "zoom_cmd":
            self._cmd_zoom(args)
        elif accion == "ltscale":
            self._cmd_ltscale(args)
        elif accion == "regen":
            self._redraw()
        elif accion == "pan_cmd":
            self._op_mode = "pan_mode"
            self._op_pts  = []
            self._op_data = {}
            self.canvas.configure(cursor="fleur")
            self._lbl_op.configure(
                text="PAN — clic y arrastra para desplazar vista  ·  Esc para salir")
            self._update_prompt()
            self._redraw_dynamic()

        # ── Undo / Redo ───────────────────────────────────────────
        elif accion == "undo":
            self._undo()
        elif accion == "redo":
            self._redo()

        # ── Capas / Propiedades ───────────────────────────────────
        elif accion == "layer_cmd":
            if args:
                nombre = args.upper()
                if nombre in self.layers:
                    self._activar_capa(nombre)
                else:
                    self._echo(f"!! Capa '{nombre}' no existe")
            else:
                self._echo("Capas: " + "  ".join(self.layers.keys()))

        elif accion == "layer_iso":
            # LAYISO: clic en entidad → aislar su capa (como AutoCAD)
            self._op_mode = "layiso_pick"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(
                text="LAYISO — Clic en entidad cuya capa quieres aislar  ·  Esc para cancelar")
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "layer_on":
            # Activar todas las capas
            for l in self.layers.values():
                l.visible = True
            self._build_layer_panel(); self._redraw_static()
            self._echo("LAYON — todas las capas visibles")

        elif accion == "layer_off":
            # Apagar capa especificada (o activa si sin args)
            nombre = args.upper().strip() if args else self.active_layer
            if nombre in self.layers:
                if nombre == self.active_layer:
                    self._echo("!! No se puede apagar la capa activa"); return
                self.layers[nombre].visible = False
                self._build_layer_panel(); self._redraw_static()
                self._echo(f"LAYOFF — capa apagada: {nombre}")
            else:
                self._echo(f"!! Capa '{nombre}' no existe")

        elif accion == "layer_lock":
            nombre = args.upper().strip() if args else self.active_layer
            if nombre in self.layers:
                self.layers[nombre].locked = True
                # Des-seleccionar entidades de esa capa
                for e in self.entities:
                    if e.layer == nombre:
                        e.selected = False
                self._build_layer_panel(); self._redraw_static()
                self._echo(f"LAYLOCK — bloqueada: {nombre}")
            else:
                self._echo(f"!! Capa '{nombre}' no existe")

        elif accion == "layer_unlock":
            nombre = args.upper().strip() if args else self.active_layer
            if nombre in self.layers:
                self.layers[nombre].locked = False
                self._build_layer_panel(); self._redraw_static()
                self._echo(f"LAYUNLOCK — desbloqueada: {nombre}")
            else:
                self._echo(f"!! Capa '{nombre}' no existe")

        elif accion == "layer_mcur":
            self._op_mode = "laymcur"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="LAYMCUR — Clic en entidad para hacer su capa activa:")

        elif accion == "properties":
            sel = [e for e in self.entities if e.selected]
            if sel:
                info = "\n".join(e.info() for e in sel[:6])
                if len(sel) > 6:
                    info += f"\n... +{len(sel)-6} más"
                self._lbl_prop.configure(text=info)
            else:
                self._echo("Seleccione entidades primero")

        # ── Medición ─────────────────────────────────────────────
        elif accion == "dist":
            self._op_mode = "dist_p1"; self._op_pts = []
            self._lbl_op.configure(text="DIST — Primer punto:")

        elif accion == "list_ent":
            sel = [e for e in self.entities if e.selected]
            if sel:
                self._lbl_prop.configure(
                    text="\n".join(e.info() for e in sel[:6]))
            else:
                self._echo("Seleccione entidades primero (LI)")

        elif accion == "id_point":
            self._op_mode = "id_pick"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="ID — Clic en el punto a identificar:")
            self._update_prompt()
            self.canvas.focus_set()

        elif accion == "area_cmd":
            sel = [e for e in self.entities if e.selected
                   and isinstance(e, (Polyline, Circle))]
            if sel:
                mostrado = 0
                for e in sel:
                    if isinstance(e, Polyline) and e.closed:
                        self._echo(f"ÁREA polilínea = {e.area():.4f} m²")
                        mostrado += 1
                    elif isinstance(e, Polyline) and not e.closed:
                        self._echo("ÁREA: la polilínea debe estar cerrada")
                        mostrado += 1
                    elif isinstance(e, Circle):
                        self._echo(f"ÁREA círculo = {math.pi*e.radius**2:.4f} m²")
                        mostrado += 1
                if not mostrado:
                    self._echo("Seleccione polilínea cerrada o círculo")
            else:
                self._echo("Seleccione polilínea cerrada o círculo")

        # ── Guardar / Abrir ───────────────────────────────────────
        elif accion == "save":
            self._guardar_json()
        elif accion == "saveas":
            self._guardar_json_como()
        elif accion == "open":
            self._abrir_json()
        elif accion == "new_dwg":
            self._new_dwg()

        # ── Export ────────────────────────────────────────────────
        elif accion == "dxf":
            self._exportar_dxf()
        elif accion == "png":
            self._exportar_png()
        elif accion == "pdf":
            self._exportar_pdf()

        # ── Layout / Paper Space ──────────────────────────────────
        elif accion == "layout_new":
            self._new_layout()
        elif accion == "layout_model":
            self._switch_to_model()
        elif accion == "layout_ps":
            # PSPACE: activar primer layout o crear uno
            if self.layouts:
                self._switch_to_layout(0)
            else:
                self._new_layout()
        elif accion == "layout_setup":
            if self.active_layout_idx >= 0:
                self._page_setup_dialog(self.active_layout_idx)
            else:
                self._echo("Activa una lámina primero")

        # ── Config ───────────────────────────────────────────────
        elif accion == "toggle_grid":
            self._toggle("grid")
        elif accion == "toggle_snap":
            self._toggle("snap")
        elif accion == "toggle_ortho":
            self._toggle("ortho")

        # ── Ayuda ─────────────────────────────────────────────────
        elif accion == "help":
            self._mostrar_ayuda()

        # ── Scroll speed ──────────────────────────────────────────
        elif accion == "scrollspeed":
            if args.strip():
                try:
                    val = int(round(float(args.strip())))
                    val = max(1, min(10, val))
                    self._scroll_speed = val
                    self._save_cfg_key("scroll_speed", val)
                    base = 1.0 + val * 0.08
                    self._echo(
                        f"SCROLLSPEED = {val}  (base zoom ×{base:.2f}/notch)")
                except ValueError:
                    self._echo("!! SCROLLSPEED: valor inválido — use un número entre 1 y 10")
            else:
                # Sin argumento → modo interactivo
                self._op_mode = "scrollspeed_input"
                self._op_pts  = []; self._op_data = {}
                self._lbl_op.configure(
                    text=f"SS — Velocidad actual: {self._scroll_speed}  "
                         f"→ escribe 1 (lento) … 10 (rápido) y Enter:")
                self.canvas.focus_set()
                self._update_prompt()

        # ── SLE — Spatial Learning Engine ─────────────────────────
        elif accion == "slecorr":
            self._registrar_correccion_sle()

        # ── Íconos de menú ─────────────────────────────────────────
        elif accion == "menu_icons_toggle":
            self._toggle_menu_icons()

    # ─── SLE — Spatial Learning Engine ──────────────────────────────────

    def _ents_to_plan_json(self) -> "dict | None":
        """
        Lee entidades del canvas actual y construye un plan JSON para el SLE.
        Requiere textos en A-TEXTO (nombres de recintos) y líneas en A-PAREDES*.
        Retorna None si no hay textos de recintos en el dibujo.
        """
        from cad.entities import Text as _Txt, Line as _Ln, Polyline as _Pl

        CAPAS_PAREDES = {
            "a-paredes", "a-paredes 0", "a-paredes 1", "a-paredes 2",
            "a-paredes 3", "a-paredes 4", "a-wall", "a-muro", "a-paredes gypsum",
        }

        # 1. Textos de recintos (capa A-TEXTO)
        textos = []
        for e in self.entities:
            if isinstance(e, _Txt) and e.layer.lower() == "a-texto" and e.content.strip():
                textos.append({"nombre": e.content.strip().upper(), "x": e.x, "y": e.y})
        if not textos:
            return None

        # 2. Líneas de paredes (capas A-PAREDES*)
        lineas = []
        for e in self.entities:
            if e.layer.lower() not in CAPAS_PAREDES:
                continue
            if isinstance(e, _Ln):
                lineas.append({"x1": e.x1, "y1": e.y1, "x2": e.x2, "y2": e.y2})
            elif isinstance(e, _Pl):
                pts = e.points
                n   = len(pts)
                for i in range(n - 1):
                    lineas.append({"x1": pts[i][0], "y1": pts[i][1],
                                   "x2": pts[i+1][0], "y2": pts[i+1][1]})
                if e.closed and n > 2:
                    lineas.append({"x1": pts[-1][0], "y1": pts[-1][1],
                                   "x2": pts[0][0],  "y2": pts[0][1]})

        # 3. Motor de rayos (importar_dwg_merlos) con fallback a bbox manual
        recintos = None
        try:
            from sle.data.importar_dwg_merlos import (
                asignar_geometria_a_textos, detectar_escala_textos,
            )
            factor = detectar_escala_textos(textos)
            if factor != 1.0:
                for t in textos:
                    t["x"] *= factor; t["y"] *= factor
                for ln in lineas:
                    ln["x1"] *= factor; ln["y1"] *= factor
                    ln["x2"] *= factor; ln["y2"] *= factor
            recintos = asignar_geometria_a_textos(textos, lineas, escala=factor)
        except Exception:
            pass

        # Fallback: bbox de líneas cercanas (radio 8 m) por recinto
        if not recintos:
            recintos = []
            xs    = [t["x"] for t in textos]
            x_rng = (max(xs) - min(xs)) if len(xs) > 1 else 1.0
            factor = 0.001 if x_rng > 10_000 else (0.01 if x_rng > 500 else 1.0)
            if factor != 1.0:
                for t in textos:
                    t["x"] *= factor; t["y"] *= factor
                for ln in lineas:
                    ln["x1"] *= factor; ln["y1"] *= factor
                    ln["x2"] *= factor; ln["y2"] *= factor
            for t in textos:
                px, py = t["x"], t["y"]
                radio  = 8.0
                lc = [ln for ln in lineas
                      if (min(ln["x1"], ln["x2"]) - radio <= px <= max(ln["x1"], ln["x2"]) + radio and
                          min(ln["y1"], ln["y2"]) - radio <= py <= max(ln["y1"], ln["y2"]) + radio)]
                if lc:
                    lx = [ln["x1"] for ln in lc] + [ln["x2"] for ln in lc]
                    ly = [ln["y1"] for ln in lc] + [ln["y2"] for ln in lc]
                    x0, y0, x1, y1 = min(lx), min(ly), max(lx), max(ly)
                else:
                    x0, y0 = px - 1.5, py - 1.5
                    x1, y1 = px + 1.5, py + 1.5
                recintos.append({
                    "nombre": t["nombre"], "x": px, "y": py,
                    "ancho": max(0.5, round(x1 - x0, 2)),
                    "alto":  max(0.5, round(y1 - y0, 2)),
                    "bbox":  (x0, y0, x1, y1),
                })

        if not recintos:
            return None

        # 4. Construir plan JSON
        all_x0 = min(r["bbox"][0] for r in recintos)
        all_y0 = min(r["bbox"][1] for r in recintos)
        all_x1 = max(r["bbox"][2] for r in recintos)
        all_y1 = max(r["bbox"][3] for r in recintos)

        return {
            "grid": {
                "ancho_m": round(all_x1 - all_x0, 1),
                "alto_m":  round(all_y1 - all_y0, 1),
            },
            "recintos": [
                {
                    "nombre": r["nombre"],
                    "fila":   round(r["bbox"][1] - all_y0, 2),
                    "col":    round(r["bbox"][0] - all_x0, 2),
                    "ancho":  round(r["ancho"], 2),
                    "alto":   round(r["alto"],  2),
                }
                for r in recintos
            ],
            "puertas": [],
        }

    def _registrar_correccion_sle(self):
        """
        Flujo de captura de correcciones del SLE desde el CAD:

        1ª vez (sin baseline): toma instantánea del estado actual como punto de
                               partida y pide al usuario que edite + vuelva a correr.
        2ª vez en adelante:    compara estado actual con baseline, registra
                               correcciones en la Memoria del SLE y actualiza baseline.

        El archivo baseline también lo escribe app.py cuando genera un plan,
        para capturar correcciones hechas directamente sobre la planta de la IA.
        """
        import json, threading as _thr
        from pathlib import Path

        if not self._sle_disponible:
            self._echo("SLE no disponible (falta sle/)")
            return

        BASE_DIR     = Path(__file__).parent.parent
        baseline_path = BASE_DIR / "sle" / "data" / "baseline_plan.json"

        # ── Leer estado actual desde entidades del CAD ────────────────
        plan_actual = self._ents_to_plan_json()
        if not plan_actual or not plan_actual.get("recintos"):
            self._echo("SLE: no se encontraron recintos — agrega textos en A-TEXTO")
            return

        n_act = len(plan_actual["recintos"])

        # ── Sin baseline → guardar instantánea y pedir que se edite ──
        if not baseline_path.exists():
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                json.dumps({"prompt": "", "plan": plan_actual},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._echo(f"SLE: instantánea guardada ({n_act} recintos)")
            self._echo("SLE: edita la planta y ejecuta SLECORR de nuevo para registrar cambios")
            return

        # ── Cargar baseline ───────────────────────────────────────────
        try:
            raw   = json.loads(baseline_path.read_text(encoding="utf-8"))
            # El baseline puede ser {"prompt":..., "plan":...} o el plan directo
            if "plan" in raw:
                plan_base = raw["plan"]
                prompt    = raw.get("prompt", "") or "corrección manual en CAD"
            else:
                plan_base = raw
                prompt    = "corrección manual en CAD"
        except Exception as ex:
            self._echo(f"SLE: error leyendo baseline — {ex}")
            return

        n_base = len(plan_base.get("recintos", []))
        self._echo(f"SLE: comparando baseline ({n_base}) → actual ({n_act})…")

        # ── Registrar en background thread ────────────────────────────
        def _worker():
            try:
                resultado = self._sle_captura.registrar(
                    plan_generado  = plan_base,
                    plan_corregido = plan_actual,
                    prompt_original = prompt,
                )
                n_corr = resultado["n_correcciones"]
                pid    = resultado["proyecto_id"]

                if n_corr == 0:
                    self.root.after(0, self._echo,
                        f"SLE: sin cambios detectados — plan guardado (id={pid})")
                else:
                    self.root.after(0, self._echo,
                        f"SLE ✓ {n_corr} corrección(es) registradas → id={pid}")
                    for c in resultado["correcciones"][:4]:
                        self.root.after(0, self._echo, f"  · {c}")

                # Actualizar baseline al estado actual
                baseline_path.write_text(
                    json.dumps({"prompt": prompt, "plan": plan_actual},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as ex:
                self.root.after(0, self._echo, f"SLE: error al registrar — {ex}")

        _thr.Thread(target=_worker, daemon=True).start()

    def _cmd_zoom(self, rest: str):
        from cad.layout import xp_to_model_scale, get_scales
        sub = rest.strip()
        sub_up = sub.upper()

        if not sub:
            # Sin argumento → panel flotante interactivo
            self._op_mode  = "zoom_opts"
            self._op_pts   = []
            self._op_data  = {}
            self._lbl_op.configure(
                text="ZOOM — [E] Extensión  [W] Ventana  [P] Anterior  "
                     "o escriba factor (ej: 2  0.5  1/100xp):")
            self._redraw_dynamic()
            return

        if sub_up in ("E", "EXTENTS"):
            self._zoom_extents()
        elif sub_up in ("A", "ALL"):
            self._zoom_extents()
        elif sub_up in ("W", "WINDOW"):
            self._op_mode = "zoom_w1"; self._op_pts = []
            self._lbl_op.configure(text="ZOOM W — Primera esquina:")
            self.canvas.focus_set()
            self._redraw_dynamic()
        elif sub_up in ("P", "PREVIOUS"):
            if self._zoom_prev:
                sc, ox, oy = self._zoom_prev.pop()
                self.scale = sc; self.offset_x = ox; self.offset_y = oy
                self._redraw()
                self._echo("ZOOM P — vista anterior restaurada")
            else:
                self._echo("ZOOM P — no hay vista anterior")
        elif sub.lower().endswith("xp"):
            # ── ZOOM XP: escala viewport en paper space ─────────────
            xp_factor = xp_to_model_scale(sub)
            if xp_factor is None:
                self._echo("ZOOM XP — formato inválido. Ej: 1/100xp  0.02xp  2xp")
                return
            if getattr(self, "active_layout_idx", -1) < 0:
                self._echo("⚠  ZOOM XP solo aplica en paper space (lámina activa)")
                return
            # Calcular escala de modelo para que xp_factor sea el ratio modelo/papel
            # 1 mm papel = 1/xp_factor mm modelo → scale = xp_factor * px_per_mm
            # px_per_mm en pantalla ≈ self.scale / 1000 (scale está en px/m)
            # Para viewport: setear la escala real del viewport
            lay = self.layouts[self.active_layout_idx]
            if lay.viewports:
                try:
                    denom = int(round(1.0 / xp_factor))
                except (ZeroDivisionError, OverflowError):
                    denom = 1
                vp = lay.viewports[0]
                from cad.layout import ViewportDef, auto_fit_viewport
                lay.viewports[0] = ViewportDef(
                    x=vp.x, y=vp.y, width=vp.width, height=vp.height,
                    scale_denom=denom,
                    view_cx=vp.view_cx, view_cy=vp.view_cy,
                    active=vp.active, label=vp.label,
                )
                lay.tb_escala = f"1:{denom}" if xp_factor <= 1 else f"{int(round(xp_factor))}:1"
                self._pil_img_cache = None
                self._redraw_static()
                self._echo(f"ZOOM XP — Viewport 1 → escala {lay.tb_escala}")
            else:
                self._echo("ZOOM XP — sin viewport en la lámina activa")
        else:
            # Factor numérico simple (2x, 0.5, 2X)
            try:
                factor = float(sub.rstrip("xX"))
                if factor <= 0:
                    raise ValueError
                # Relativo al zoom actual
                W = self.canvas.winfo_width() or 800
                H = self.canvas.winfo_height() or 600
                cx_s = W / 2; cy_s = H / 2
                cx_w = (cx_s - self.offset_x) / self.scale
                cy_w = (cy_s - self.offset_y) / (-self.scale)
                self.scale *= factor
                self.offset_x = cx_s - cx_w * self.scale
                self.offset_y = cy_s + cy_w * self.scale
                self._redraw()
                self._echo(f"ZOOM ×{factor}")
            except ValueError:
                self._echo("ZOOM — opciones: E  A  W  P  · factor: 2  0.5  2x  · paper: 1/100xp  2xp")

    # ── LTSCALE ──────────────────────────────────────────────────────
    def _cmd_ltscale(self, rest: str):
        """Aplica LTSCALE desde la línea de comandos."""
        sub = rest.strip()
        if not sub:
            cur = self._leer_config_ia().get("ltscale", 1.0)
            self._echo(f"LTSCALE = {cur}  →  ingrese nuevo valor (Enter para mantener):")
            self._lbl_op.configure(text=f"LTSCALE actual: {cur}  — ingrese nuevo valor:")
            self._op_mode = "ltscale_input"
            return
        try:
            val = float(sub)
            if val <= 0:
                raise ValueError
            self._apply_ltscale(val)
        except ValueError:
            self._echo("LTSCALE — ingrese un número positivo. Ej: 50  1  0.5")

    def _apply_ltscale(self, val: float):
        """Guarda ltscale en config, actualiza badge y redibuja."""
        self._save_cfg_key("ltscale", val)
        self._pil_img_cache = None
        if hasattr(self, "_btn_lts"):
            self._btn_lts.configure(text=f"LTS:{val:g}")
        self._redraw_static()
        self._echo(f"LTSCALE = {val:g}")

    def _ltscale_dialog(self):
        """Diálogo rápido para cambiar LTSCALE desde el badge."""
        from cad.layout import get_scales
        cfg = self._leer_config_ia()
        cur = float(cfg.get("ltscale", 1.0))
        units = cfg.get("units", "metric")
        scales = get_scales(units)

        dlg = ctk.CTkToplevel(self.root)
        dlg.title("LTSCALE — Escala de tipos de línea")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        rx = self.root.winfo_x() + (self.root.winfo_width()  - 340) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - 480) // 2
        dlg.geometry(f"340x480+{rx}+{ry}")
        self.root.after(80, dlg.grab_set)

        BG = "#1E293B"; CARD = "#334155"; TX = "#F8FAFC"; TX2 = "#94A3B8"
        FB = ctk.CTkFont(size=12, weight="bold")
        FS = ctk.CTkFont(size=10)

        ctk.CTkLabel(dlg, text="LTSCALE", font=FB, text_color=TX
                     ).pack(pady=(14, 2))
        ctk.CTkLabel(dlg, text="Escala global de tipos de línea (DASHED, CENTER…)",
                     font=FS, text_color=TX2).pack(pady=(0, 8))

        # Campo manual
        v_val = tk.StringVar(value=str(cur))
        ent = ctk.CTkEntry(dlg, textvariable=v_val, width=120, height=30, font=FB)
        ent.pack(pady=4)

        # Lista de escalas estándar agrupadas
        sc_frame = ctk.CTkScrollableFrame(dlg, fg_color=BG, height=260)
        sc_frame.pack(fill="both", expand=True, padx=10, pady=4)

        # Ampliación
        ctk.CTkLabel(sc_frame, text="── Ampliación ──", font=FS, text_color=TX2
                     ).pack(anchor="w")
        amp_row = ctk.CTkFrame(sc_frame, fg_color="transparent")
        amp_row.pack(fill="x", pady=2)
        for lbl, xp, lts in scales:
            if xp > 1.0:
                ctk.CTkButton(amp_row, text=lbl, width=70, height=22, font=FS,
                              fg_color=CARD, hover_color="#1D4ED8",
                              command=lambda v=lts: (v_val.set(str(v)))).pack(
                                  side="left", padx=2, pady=1)

        # Reducción
        ctk.CTkLabel(sc_frame, text="── Reducción ──", font=FS, text_color=TX2
                     ).pack(anchor="w", pady=(6, 0))
        row = None
        for i, (lbl, xp, lts) in enumerate(s for s in scales if s[1] <= 1.0):
            if i % 4 == 0:
                row = ctk.CTkFrame(sc_frame, fg_color="transparent")
                row.pack(fill="x", pady=1)
            ctk.CTkButton(row, text=lbl, width=70, height=22, font=FS,
                          fg_color=CARD, hover_color="#1D4ED8",
                          command=lambda v=lts: (v_val.set(str(v)))).pack(
                              side="left", padx=2)

        def _aplicar():
            try:
                self._apply_ltscale(float(v_val.get()))
                dlg.destroy()
            except ValueError:
                pass

        ctk.CTkButton(dlg, text="✓ Aplicar", height=30,
                      fg_color="#16A34A", hover_color="#15803D",
                      font=FB, command=_aplicar
                      ).pack(fill="x", padx=16, pady=(4, 10))

    def _parse_coord(self, raw: str):
        """Acepta X,Y · @X,Y · @d<ángulo · d<ángulo"""
        raw = raw.strip()
        try:
            if raw.startswith("@") and "<" in raw:   # @d<ángulo (polar relativo)
                parts = raw[1:].split("<")
                dist  = float(parts[0])
                ang   = math.radians(float(parts[1]))
                if self.draw_pts:
                    lx, ly = self.draw_pts[-1]
                else:
                    lx, ly = self.mouse_w
                wx = lx + dist*math.cos(ang)
                wy = ly + dist*math.sin(ang)
            elif "<" in raw:                           # d<ángulo (polar absoluto)
                parts = raw.split("<")
                dist  = float(parts[0])
                ang   = math.radians(float(parts[1]))
                if self.draw_pts:
                    lx, ly = self.draw_pts[-1]
                else:
                    lx, ly = (0.0, 0.0)
                wx = lx + dist*math.cos(ang)
                wy = ly + dist*math.sin(ang)
            elif raw.startswith("@"):                   # @X,Y (relativo)
                parts = raw[1:].split(",")
                if self.draw_pts:
                    lx, ly = self.draw_pts[-1]
                else:
                    lx, ly = self.mouse_w
                wx = lx + float(parts[0])
                wy = ly + float(parts[1])
            else:                                       # X,Y (absoluto)
                parts = raw.split(",")
                wx, wy = float(parts[0]), float(parts[1])

            self.mouse_w = (wx, wy)
            self._click_draw(wx, wy)
            self._redraw_dynamic()
        except Exception:
            self._echo(f"!! Coordenada inválida: '{raw}'  (ej: 1.5,3.0  @1,0  @2<45)")

    def _on_click_world(self, wx, wy):
        self._click_draw(wx, wy)

    # ─── Hint de entidad bajo el cursor ──────────────────────────────
    _ETYPE_LABEL = {
        "Line":       "LÍNEA",
        "Polyline":   "POLILÍNEA",
        "Circle":     "CÍRCULO",
        "Arc":        "ARCO",
        "Text":       "TEXTO",
        "Dimension":  "COTA",
        "Hatch":      "HATCH",
        "Insert":     "BLOQUE",
    }

    def _update_entity_hint(self, ent):
        """Actualiza la línea de hint con la info de la entidad bajo el cursor.
        Si ent es None, limpia el hint."""
        if not hasattr(self, "_cmd_hint_lbl"):
            return
        if ent is None:
            try:
                self._cmd_hint_lbl.configure(text="")
            except Exception:
                pass
            return
        try:
            tipo = self._ETYPE_LABEL.get(type(ent).__name__, type(ent).__name__.upper())
            info = ent.info()
            # La primera línea ya incluye el tipo; mostramos todo pero en una línea
            # compacta: reemplazamos saltos por  ·
            compact = info.replace("\n", "  ·  ").replace("  ·    ·  ", "  ·  ")
            self._cmd_hint_lbl.configure(text=f"▶ {compact}")
        except Exception:
            pass

    def _poll_tess_progress(self):
        """Muestra el avance del tessellator en la barra de estado mientras corre.
        Llama _redraw_static() cada tick para que cuando el resultado esté listo
        el GL thread lo consuma y muestre las entidades nuevas.
        Se auto-cancela cuando _tess_pending se vuelve False.
        Llamado desde el hilo principal via root.after() — seguro para Tkinter."""
        rend = self._renderer
        if not getattr(rend, '_tess_pending', False):
            # Tessellation terminó (resultado ya consumido por un render anterior)
            cur = self._lbl_op.cget("text")
            if cur.startswith("⟳"):
                self._lbl_op.configure(text="", fg="#F8FAFC")
            self._tess_poll_active = False
            return
        prog = getattr(rend, '_tess_progress', '') or "⟳ Preparando geometría…"
        self._lbl_op.configure(text=prog, fg="#22D3EE")
        # Disparar render solo si el anterior ya terminó: evita crear N hilos PIL
        # simultáneos que compiten por GIL y suman 200-500ms de overhead cada uno.
        if not getattr(self, '_render_pending', False):
            self._redraw_static()
        # 400ms entre renders intermedios durante tessellation (era 150ms).
        # Libera el hilo principal para cursor y eventos ~87% del tiempo vs 67%.
        # El frame final sigue siendo inmediato vía _tess_done_cb.
        self.root.after(400, self._poll_tess_progress)

    def _echo(self, msg: str):
        """Muestra mensaje en la etiqueta de operación y lo registra en el historial.
        NO toca _cmd_var para no destruir lo que el usuario esté escribiendo."""
        self._lbl_op.configure(text=msg, fg=UI_WARN)

        # ── Registrar también en el panel de historial ─────────────────
        # Errores=rojo  |  Resultados numéricos=azul claro  |  Resto=gris sistema
        if hasattr(self, "_hist_txt"):
            _RESULT_PREFIXES = ("DIST", "ÁREA", "AREA", "ID ", "MEASURE",
                                "ARRAY", "BLOCK", "IMG ", "EATTEDIT:",
                                "INSERT", "Capa")
            if msg.startswith("!!"):
                _tag = "err"
            elif any(msg.startswith(p) for p in _RESULT_PREFIXES):
                _tag = "res"   # azul claro — resultados de comandos
            else:
                _tag = "sys"   # gris — mensajes de estado
            self._add_to_history(msg, _tag)

        # Cancelar timeout anterior si existe
        if hasattr(self, "_echo_after_id") and self._echo_after_id:
            try:
                self.root.after_cancel(self._echo_after_id)
            except Exception:
                pass  # after_id ya expiró o fue cancelado — ignorar
        def _clear():
            # Solo limpiar si el mensaje actual sigue siendo el mismo
            if self._lbl_op.cget("text") == msg:
                self._lbl_op.configure(text="", fg=UI_WARN)
            self._echo_after_id = None
        self._echo_after_id = self.root.after(4000, _clear)

    def _cmd_autocomplete(self):
        """Tab: autocompleta comandos. Cicla si hay múltiples coincidencias."""
        raw = self._cmd_var.get()
        prefix = raw.upper()
        if not prefix:
            return
        # Si el prefix cambió respecto a la sesión anterior, reiniciar lista
        if prefix != self._ac_prefix:
            self._ac_prefix = prefix
            self._ac_matches = sorted(
                k for k in _CMD_ALIASES if k.startswith(prefix) and len(k) > len(prefix)
            ) or sorted(k for k in _CMD_ALIASES if k.startswith(prefix))
            self._ac_idx = 0
        if not self._ac_matches:
            self._echo(f"? sin coincidencia: {prefix}")
            return
        choice = self._ac_matches[self._ac_idx % len(self._ac_matches)]
        self._ac_idx += 1
        self._cmd_var.set(choice)
        self._cmd_entry.icursor("end")
        # Mostrar opciones si hay más de una
        if len(self._ac_matches) > 1:
            self._echo("  ".join(self._ac_matches[:8]))

    def _historial_up(self):
        if self._cmd_history:
            self._cmd_hist_idx = min(self._cmd_hist_idx+1, len(self._cmd_history)-1)
            self._cmd_var.set(self._cmd_history[self._cmd_hist_idx])

    def _historial_down(self):
        if self._cmd_hist_idx > 0:
            self._cmd_hist_idx -= 1
            self._cmd_var.set(self._cmd_history[self._cmd_hist_idx])
        else:
            self._cmd_hist_idx = -1
            self._cmd_var.set("")

    # ─── Historial visual ─────────────────────────────────────────
    def _toggle_history(self):
        self._cmd_expanded = not self._cmd_expanded
        if self._cmd_expanded:
            # IMPORTANTE: usar fill="both" + expand=True (igual que _build_cmd_bar).
            # Sin expand=True el Text interior (height=10 ≈ 150px) toma todo el espacio
            # disponible y empuja cf (con botones CAD/IA) fuera del área visible.
            self._hist_frame.pack(fill="both", expand=True)
            self._btn_hist.configure(text="▼")
            self._hist_txt.see("end")
        else:
            self._hist_frame.pack_forget()
            self._btn_hist.configure(text="▲")
        self._cmd_float_sync_height()  # ajusta alto si está flotando
        self._cmd_save_float_state()   # persistir estado expandido/colapsado

    def _restore_hist_log(self):
        """Puebla _hist_txt con el log guardado en disco (llamado al construir la barra)."""
        if not self._hist_log:
            return
        self._hist_txt.configure(state="normal")
        self._hist_txt.delete("1.0", "end")
        # separador visual para distinguir sesión anterior
        self._hist_txt.insert("end", "── sesión anterior ──\n", "sys")
        for entry in self._hist_log:
            self._hist_txt.insert("end", entry["t"] + "\n", entry.get("g", "cad"))
        self._hist_txt.insert("end", "── sesión actual ────\n", "sys")
        self._hist_txt.see("end")
        self._hist_txt.configure(state="disabled")

    def _add_to_history(self, text: str, tag: str = "cad"):
        """Añade una línea al panel de historial con su color de tag."""
        self._hist_txt.configure(state="normal")
        self._hist_txt.insert("end", text + "\n", tag)
        # mantener máximo 200 líneas en pantalla
        lines = int(self._hist_txt.index("end-1c").split(".")[0])
        if lines > 200:
            self._hist_txt.delete("1.0", "10.0")
        self._hist_txt.see("end")
        self._hist_txt.configure(state="disabled")

        # Acumular en log persistente (máx 100 entradas)
        self._hist_log.append({"t": text, "g": tag})
        if len(self._hist_log) > 100:
            self._hist_log = self._hist_log[-100:]

        # Guardar con debounce (evita escribir JSON en cada carácter)
        if not self._hist_save_pending:
            self._hist_save_pending = True
            self.root.after(2000, self._flush_hist_save)

    def _flush_hist_save(self):
        """Escribe historial a disco (se llama 2 s después del último mensaje)."""
        self._hist_save_pending = False
        import json as _json
        cfg = self._leer_config_ia()
        cfg["cmd_hist_log"]    = self._hist_log
        cfg["cmd_nav_history"] = self._cmd_history[:50]
        path = self._cfg_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(cfg, f, indent=2)

    def _on_cmd_change(self, *_):
        """Cambia el color del prompt según el modo CAD vs IA y actualiza el hint semántico."""
        raw = self._cmd_var.get()
        txt = raw.strip().upper()

        # ── Badge CAD / IA ───────────────────────────────────────────
        if raw.startswith("/"):
            self._cmd_prompt_lbl.configure(
                text="IA", fg_color="#38BDF8", text_color=UI_BG)
            # Auto-expandir historial la primera vez que se tipea "/"
            if not self._cmd_expanded:
                self._toggle_history()
        else:
            self._cmd_prompt_lbl.configure(
                text="CAD", fg_color=CV_CMD_FG, text_color=UI_BG)
            # NO colapsar al volver a CAD — el usuario decide con ▲▼

        # ── Hint semántico / prompt paso a paso ─────────────────────
        hint = ""
        if raw.startswith("/"):
            hint = t("ia_hint")
        elif txt:
            # Mientras escribe: autocompletar comando
            action = _CMD_ALIASES.get(txt)
            if action:
                hint = _CMD_DESCRIPTIONS.get(action, "")
            else:
                matches = [k for k in _CMD_ALIASES if k.startswith(txt)]
                if matches:
                    key = min(matches, key=len)
                    action = _CMD_ALIASES.get(key)
                    if action:
                        desc = _CMD_DESCRIPTIONS.get(action, "")
                        hint = (f"-> {key}   {desc}" if key != txt else desc)
        else:
            # Input vacío: prompt AutoCAD del paso actual (traducido)
            if self._op_mode:
                i18n_key = OP_PROMPT_KEYS.get(self._op_mode, "")
                hint = t(i18n_key) if i18n_key else ""
            else:
                n   = len(self.draw_pts)
                key = (self.tool, n)
                if key not in TOOL_PROMPT_KEYS:
                    steps = [k[1] for k in TOOL_PROMPT_KEYS if k[0] == self.tool]
                    best  = max((s for s in steps if s <= n), default=None)
                    key   = (self.tool, best) if best is not None else None
                i18n_key = TOOL_PROMPT_KEYS.get(key, "") if key else ""
                hint = t(i18n_key) if i18n_key else ""

        self._cmd_hint_lbl.configure(
            text=hint,
            fg="#CBD5E1" if hint else "#475569",
        )

    # ─── Motor IA ─────────────────────────────────────────────────
    def _ejecutar_ia(self, prompt: str):
        """Lanza el comando IA con streaming en tiempo real."""
        import threading, re as _re

        # Asegurar que el historial esté visible para ver la respuesta
        if not self._cmd_expanded:
            self._toggle_history()

        self._add_to_history(f"/ {prompt}", "ai")
        self._lbl_op.configure(text="▋  generando...")
        self._cmd_prompt_lbl.configure(text="IA", fg_color="#38BDF8", text_color=UI_BG)

        # Insertar línea vacía con mark para actualizar en streaming
        self._hist_txt.configure(state="normal")
        self._hist_txt.insert("end", "\n", "resp")
        self._hist_txt.mark_set("ia_stream", "end-1c linestart")
        self._hist_txt.mark_gravity("ia_stream", "left")
        self._hist_txt.see("end")
        self._hist_txt.configure(state="disabled")

        buf: list[str] = []

        def _on_chunk(text: str):
            buf.append(text)
            self.root.after(0, _update_ui)

        def _update_ui():
            full    = "".join(buf)
            display = _re.sub(r"```json.*?```", "", full, flags=_re.DOTALL).strip()
            self._hist_txt.configure(state="normal")
            self._hist_txt.delete("ia_stream", "end")
            self._hist_txt.insert("end", display + " ▋", "resp")
            self._hist_txt.see("end")
            self._hist_txt.configure(state="disabled")
            preview = display.replace("\n", " ")[:80]
            self._lbl_op.configure(text=f"▋ {preview}")

        def _on_done():
            full   = "".join(buf)
            result = self._parsear_respuesta_ia(full)
            self.root.after(0, lambda: _finalize(result, full))

        def _finalize(result: dict, full_text: str):
            display = _re.sub(r"```json.*?```", "", full_text, flags=_re.DOTALL).strip()
            self._hist_txt.configure(state="normal")
            self._hist_txt.delete("ia_stream", "end")
            self._hist_txt.insert("end", display + "\n", "resp")
            self._hist_txt.see("end")
            self._hist_txt.configure(state="disabled")
            # Acumular en log persistente (sin la línea de streaming)
            self._hist_log.append({"t": f"IA: {display}", "g": "resp"})
            if len(self._hist_log) > 100:
                self._hist_log = self._hist_log[-100:]
            # Procesar entidades/cmds
            self._ia_respuesta(result, texto_ya_mostrado=True)

        threading.Thread(target=lambda: self._llamar_ia_stream(
            prompt, _on_chunk, _on_done), daemon=True).start()

    # ─── Configuración (snaps + IA) ───────────────────────────────

    def _cfg_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "config", "settings.json")

    def _leer_config_ia(self) -> dict:
        # Usar dirty-flag en vez de getmtime().
        # getmtime() sobre OneDrive puede bloquear 500ms si el archivo está
        # siendo sincronizado → freezes en _build_render_ctx cada 1-30s.
        # La config solo cambia cuando el usuario guarda desde la UI → marcamos
        # _config_dirty=True allí; aquí solo leemos si está dirty o no hay cache.
        if (getattr(self, '_config_cache', None) is not None and
                not getattr(self, '_config_dirty', True)):
            return self._config_cache
        try:
            import json
            with open(self._cfg_path(), encoding="utf-8") as f:
                self._config_cache = json.load(f)
                self._config_dirty = False
                return self._config_cache
        except Exception:
            self._config_dirty = False
            return getattr(self, '_config_cache', None) or {}

    def _abrir_editor_menu_contextual(self):
        """
        Diálogo para personalizar los comandos del menú contextual (★ Favoritos).
        Muestra checkboxes agrupados por categoría. Guarda en settings.json.
        """
        import json as _json

        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Personalizar menú contextual")
        dlg.resizable(True, True)

        # Centrar sobre la ventana principal y forzar al frente
        dlg.update_idletasks()
        w, h = 420, 620
        rx = self.root.winfo_x() + (self.root.winfo_width()  - w) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{rx}+{ry}")
        dlg.attributes("-topmost", True)
        dlg.lift()
        dlg.focus_force()
        # grab_set con pequeño delay para que la ventana esté visible primero
        dlg.after(100, dlg.grab_set)

        BG   = "#0F172A"
        CARD = "#1E293B"
        BORD = "#334155"
        ACC  = "#7C3AED"
        TXT  = "#F1F5F9"
        TXT2 = "#94A3B8"

        dlg.configure(fg_color=BG)

        # ── Header ────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(dlg, fg_color=CARD, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text="☰  Menú contextual — Favoritos",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=TXT).pack(anchor="w", padx=14, pady=10)
        ctk.CTkLabel(hdr,
                     text="Selecciona los comandos que aparecerán en la\n"
                          "sección ★ Favoritos del menú al hacer clic derecho.",
                     font=ctk.CTkFont(size=10), text_color=TXT2,
                     justify="left").pack(anchor="w", padx=14, pady=(0, 10))

        # ── Cuerpo scrollable ─────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(dlg, fg_color=BG,
                                        scrollbar_button_color=BORD,
                                        scrollbar_button_hover_color=ACC)
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # Construir checkboxes agrupados por categoría
        vars_: dict[str, tk.BooleanVar] = {}
        activos = set(self._ctx_menu_cmds)

        # Agrupar catálogo por categoría
        categorias: dict[str, list] = {}
        for accion, lbl, cat in self._CTX_CATALOG:
            categorias.setdefault(cat, []).append((accion, lbl))

        CAT_COLORS = {
            "Dibujo":       "#0EA5E9",   # azul cielo
            "Edición":      "#10B981",   # verde esmeralda
            "Modificación": "#F59E0B",   # ámbar
            "Cotas":        "#F472B6",   # rosa
            "Capas":        "#A78BFA",   # violeta
            "Medir":        "#34D399",   # verde menta
            "Vista":        "#8B5CF6",   # púrpura
        }

        for cat, cmds in categorias.items():
            # Encabezado de categoría
            cat_frm = ctk.CTkFrame(scroll, fg_color="transparent")
            cat_frm.pack(fill="x", padx=12, pady=(12, 2))
            ctk.CTkLabel(cat_frm, text=cat.upper(),
                         font=ctk.CTkFont(size=9, weight="bold"),
                         text_color=CAT_COLORS.get(cat, TXT2),
                         ).pack(anchor="w")
            ctk.CTkFrame(cat_frm, height=1, fg_color=BORD).pack(fill="x", pady=(2, 0))

            # Checkboxes de esta categoría
            for accion, lbl in cmds:
                var = tk.BooleanVar(value=(accion in activos))
                vars_[accion] = var
                ctk.CTkCheckBox(
                    scroll,
                    text=lbl.strip(),
                    variable=var,
                    font=ctk.CTkFont(family="Courier New", size=11),
                    text_color=TXT,
                    fg_color=ACC,
                    hover_color="#6D28D9",
                    border_color=BORD,
                    checkmark_color="#FFFFFF",
                    corner_radius=4,
                ).pack(anchor="w", padx=20, pady=2)

        # ── Botones ───────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(dlg, fg_color=CARD, corner_radius=0)
        btn_row.pack(fill="x", side="bottom")

        def _seleccionar_todos():
            for v in vars_.values(): v.set(True)

        def _limpiar():
            for v in vars_.values(): v.set(False)

        def _guardar():
            seleccionados = [a for a, v in vars_.items() if v.get()]
            # Mantener el orden del catálogo
            orden = [a for a, _, _ in self._CTX_CATALOG]
            self._ctx_menu_cmds = [a for a in orden if a in seleccionados]
            # Persistir en settings.json
            cfg = self._leer_config_ia()
            cfg["context_menu_cmds"] = self._ctx_menu_cmds
            path = self._cfg_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as _f:
                _json.dump(cfg, _f, indent=2)
            dlg.destroy()

        ctk.CTkButton(btn_row, text="Todos", width=70, height=30,
                      fg_color=CARD, hover_color=BORD,
                      border_width=1, border_color=BORD,
                      font=ctk.CTkFont(size=11),
                      command=_seleccionar_todos,
                      ).pack(side="left", padx=(10, 4), pady=8)
        ctk.CTkButton(btn_row, text="Limpiar", width=70, height=30,
                      fg_color=CARD, hover_color=BORD,
                      border_width=1, border_color=BORD,
                      font=ctk.CTkFont(size=11),
                      command=_limpiar,
                      ).pack(side="left", padx=(0, 4), pady=8)
        ctk.CTkButton(btn_row, text="Guardar", width=90, height=30,
                      fg_color=ACC, hover_color="#6D28D9",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=_guardar,
                      ).pack(side="right", padx=10, pady=8)
        ctk.CTkButton(btn_row, text="Cancelar", width=80, height=30,
                      fg_color=CARD, hover_color=BORD,
                      border_width=1, border_color=BORD,
                      font=ctk.CTkFont(size=11),
                      command=dlg.destroy,
                      ).pack(side="right", padx=(0, 4), pady=8)

    # ── Helpers para API keys (keyring → no quedan en disco legible) ──────────
    _KR_SERVICE = "estudio-merlos-ai"

    @staticmethod
    def _kr_set(provider: str, api_key: str) -> None:
        """Guarda api_key en el gestor de credenciales del SO (nunca en disco)."""
        try:
            import keyring as _kr
            _kr.set_password(CADWindow._KR_SERVICE, provider, api_key)
        except Exception:
            pass  # keyring no disponible: no guardar en settings.json

    @staticmethod
    def _kr_get(provider: str) -> str:
        """Lee api_key desde el gestor de credenciales del SO."""
        try:
            import keyring as _kr
            val = _kr.get_password(CADWindow._KR_SERVICE, provider)
            return val or ""
        except Exception:
            return ""

    def _guardar_config_ia(self, provider: str, api_key: str,
                           model: str, max_tokens: int = 500):
        import json
        cfg = self._leer_config_ia()
        cfg["provider"]   = provider
        cfg["model"]      = model
        # SEC-01: la API key se almacena en el gestor de credenciales del SO,
        # NUNCA en settings.json (evita exposición en OneDrive / control de versiones).
        self._kr_set(provider, api_key)
        # Limpiar cualquier key en texto plano que pudiera haber quedado de versiones anteriores
        for _p in ("anthropic", "openai", "gemini", "openrouter"):
            cfg.pop(f"{_p}_api_key", None)
        cfg["max_tokens"] = max_tokens
        path = self._cfg_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        self._config_dirty = True

    def _test_ia_connection(self, provider: str, api_key: str, model: str) -> str:
        """Retorna string de éxito o '' en fallo."""
        try:
            if provider == "anthropic":
                import anthropic
                client = anthropic.Anthropic(api_key=api_key or None)
                client.messages.create(
                    model=model, max_tokens=5,
                    messages=[{"role": "user", "content": "Hi"}])
                return f"Claude OK  ({model})"
            elif provider == "openai":
                from openai import OpenAI
                client = OpenAI(api_key=api_key or None)
                client.chat.completions.create(
                    model=model, max_tokens=5,
                    messages=[{"role": "user", "content": "Hi"}])
                return f"OpenAI OK  ({model})"
            elif provider == "gemini":
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                genai.GenerativeModel(model).generate_content("Hi")
                return f"Gemini OK  ({model})"
        except Exception:
            return ""
        return ""

    def _open_config(self, initial_tab: str = "🖱 General"):
        """Panel ⚙ Configuración — 5 tabs: General | Cotas | Texto | Visual | IA.
        initial_tab: nombre del tab a mostrar al abrir (ej. '📐 Cotas')."""
        # Reusar ventana existente solo si fue inicializada completamente.
        # _cfg_win_built=False indica que una apertura anterior falló a mitad
        # (ej: "No more menus") dejando una ventana en blanco — en ese caso
        # destruirla y recrear desde cero.
        _win_ok = (
            hasattr(self, "_cfg_win") and
            self._cfg_win is not None and
            self._cfg_win.winfo_exists() and
            getattr(self, "_cfg_win_built", False)
        )
        if _win_ok:
            self._cfg_win.deiconify()
            self._cfg_win.lift()
            try:
                if hasattr(self, "_cfg_tabs"):
                    self._cfg_tabs.set(initial_tab)
            except Exception:
                pass
            return
        # Limpiar ventana rota si existe
        if hasattr(self, "_cfg_win") and self._cfg_win is not None:
            try:
                self._cfg_win.destroy()
            except Exception:
                pass
            self._cfg_win = None

        self._cfg_win_built = False   # se pone True al final si todo va bien
        # tk.Toplevel en vez de CTkToplevel: evita el flash blanco.
        # CTkToplevel.__init__ llama _windows_set_titlebar_color→update() durante
        # su __init__, causando el flash antes de que podamos ocultarla.
        # tk.Toplevel no llama update() — el withdraw() es efectivo desde el inicio.
        win = tk.Toplevel(self.root)
        win.withdraw()   # ocultar durante construcción
        win.configure(bg=UI_BG)
        win.title("⚙  Configuración — Estudio Merlos CAD")
        win.geometry("560x640")
        win.resizable(False, True)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", win.withdraw)   # ocultar en vez de destruir
        self._cfg_win = win

        _SMALL = ctk.CTkFont(size=10)
        _HEAD  = ctk.CTkFont(size=11, weight="bold")
        _TINY  = ctk.CTkFont(size=9)

        def _scroll_tab(parent):
            """Reemplaza CTkScrollableFrame con tk nativo.
            CTkScrollableFrame provoca cascadas _draw→update_idletasks de ~500ms
            cada vez que el scrollbar actualiza. tk.Scrollbar nativo no tiene ese bug."""
            # Leer bg real del parent (CTkFrame del tab) para evitar flash al cambiar tab
            try:
                _fc = parent.cget("fg_color")
                _bg = (_fc[1] if isinstance(_fc, (list,tuple)) and len(_fc)>1
                       else (_fc if isinstance(_fc, str) and _fc not in ("transparent","")
                             else UI_PAN))
            except Exception:
                _bg = UI_PAN
            # yscrollincrement=20: cada "unit" = 20px  →  3 units × 20px = 60px/notch
            _cv = tk.Canvas(parent, bg=_bg, highlightthickness=0, bd=0,
                            yscrollincrement=20)
            _sb = tk.Scrollbar(parent, orient="vertical", command=_cv.yview)
            inner = tk.Frame(_cv, bg=_bg)
            _wid = _cv.create_window((0, 0), window=inner, anchor="nw")
            _cv.configure(yscrollcommand=_sb.set)
            inner.bind("<Configure>", lambda e: _cv.configure(scrollregion=_cv.bbox("all")))
            _cv.bind("<Configure>", lambda e: _cv.itemconfigure(_wid, width=e.width))
            def _wheel(e):
                # max(1,…) evita que deltas pequeños de touchpad se trunquen a 0
                # 3 unidades × 20px = ~60px por notch de rueda normal
                _d = e.delta
                if not _d:
                    return
                units = max(1, abs(_d) // 40) * 3
                _cv.yview_scroll(-units if _d > 0 else units, "units")
            _cv.bind("<MouseWheel>", _wheel)
            inner.bind("<MouseWheel>", _wheel)
            # _scroll_wheel expuesto para que el caller pueda propagarlo a hijos
            inner._scroll_wheel = _wheel
            _sb.pack(side="right", fill="y")
            _cv.pack(side="left", fill="both", expand=True)
            return inner

        def _om(parent, values, var=None, cmd=None, width=14):
            """tk.OptionMenu estilizado — elimina CTkOptionMenu._draw()→update_idletasks cascade."""
            _v = var if var is not None else tk.StringVar(value=values[0] if values else "")
            _w = tk.OptionMenu(parent, _v, *(values or [""]), command=cmd)
            _w.configure(bg=UI_CARD, fg=UI_TEXT, activebackground=UI_ACC,
                         activeforeground=UI_TEXT, font=_SMALL,
                         highlightthickness=0, bd=0, relief="flat", width=width)
            _w["menu"].configure(bg=UI_CARD, fg=UI_TEXT,
                                 activebackground=UI_ACC, font=_SMALL, bd=0)
            # configure(values=...) y configure(command=...) para compat con CTkOptionMenu
            _orig_cfg = _w.configure
            def _compat_cfg(values=None, command=None, **kw):
                if values is not None:
                    m = _w["menu"]; m.delete(0, "end")
                    for v in values: m.add_command(label=v, command=tk._setit(_v, v))
                if command is not None:
                    m = _w["menu"]
                    _items = [m.entrycget(i, "label") for i in range(m.index("end")+1)]
                    m.delete(0, "end")
                    for v in _items: m.add_command(label=v, command=tk._setit(_v, v, command))
                if kw: _orig_cfg(**kw)
            _w.configure = _compat_cfg
            return _w

        def _card(parent, pady=(0, 10)):
            f = tk.Frame(parent, bg=UI_CARD)
            f.pack(fill="x", padx=12, pady=pady)
            return f

        def _section(parent, text, pady=(14, 4)):
            try: _bg = parent.cget('bg')
            except Exception: _bg = UI_PAN
            tk.Label(parent, text=text, font=("Segoe UI", 11, "bold"),
                     fg=UI_TEXT2, bg=_bg, anchor="w").pack(anchor="w", padx=14, pady=pady)

        def _hint(parent, text):
            try: _bg = parent.cget('bg')
            except Exception: _bg = UI_PAN
            tk.Label(parent, text=text, font=("Segoe UI", 9),
                     fg=UI_BORD, bg=_bg, wraplength=480,
                     justify="left").pack(anchor="w", padx=14, pady=(0, 8))

        def _seg_btn(parent, values, variable, command, **_):
            """tk nativo en lugar de CTkSegmentedButton — elimina _draw() en __init__."""
            fr = tk.Frame(parent, bg=UI_CARD)
            _btns = {}
            def _sel(v):
                variable.set(v)
                for _v, _b in _btns.items():
                    _b.configure(
                        bg=UI_ACC if _v == v else UI_BORD,
                        fg=UI_TEXT if _v == v else UI_TEXT2)
                command(v)
            for v in values:
                _b = tk.Button(
                    fr, text=v, font=("Segoe UI", 10),
                    bg=UI_ACC if variable.get() == v else UI_BORD,
                    fg=UI_TEXT if variable.get() == v else UI_TEXT2,
                    relief="flat", bd=0, padx=6, pady=3,
                    activebackground=UI_ACC, activeforeground=UI_TEXT,
                    cursor="hand2",
                    command=lambda val=v: _sel(val))
                _b.pack(side="left", padx=1)
                _btns[v] = _b
            return fr

        # ── Tabs ──────────────────────────────────────────────────────
        tabs = ctk.CTkTabview(win,
                              fg_color=UI_BG,
                              segmented_button_fg_color=UI_CARD,
                              segmented_button_selected_color=UI_ACC,
                              segmented_button_selected_hover_color="#1D4ED8",
                              segmented_button_unselected_color=UI_CARD,
                              segmented_button_unselected_hover_color=UI_BORD,
                              text_color=UI_TEXT,
                              corner_radius=8)
        tabs.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._cfg_tabs = tabs   # guardado para saltar al tab correcto desde el menú

        for tab_name in ("🖱 General", "📐 Cotas", "🖊 Texto", "🎨 Visual", "🤖 IA", "📊 Perf"):
            tabs.add(tab_name)

        # Activar el tab solicitado (o General por defecto)
        try:
            tabs.set(initial_tab)
        except Exception:
            tabs.set("🖱 General")

        # ════════════════════════════════════════════════════════════
        # TAB 1 — GENERAL
        # ════════════════════════════════════════════════════════════
        t1 = _scroll_tab(tabs.tab("🖱 General"))

        # ── CURSOR ──────────────────────────────────────────────────
        _section(t1, "CURSOR")
        cur_card = _card(t1)
        cur_row = tk.Frame(cur_card, bg=UI_CARD)
        cur_row.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(cur_row, text="Tamaño", font=("Segoe UI", 10),
                 fg=UI_TEXT2, bg=UI_CARD, anchor="w").pack(side="left")
        cur_lbl = tk.Label(cur_row, text=f"{self.cursor_size}%",
                           font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                           width=4, anchor="e")
        cur_lbl.pack(side="right")
        def _on_cursor_size(v):
            self.cursor_size = int(float(v))
            cur_lbl.configure(text=f"{self.cursor_size}%")
            self._save_cfg_key("cursor_size", self.cursor_size)
        cur_sl = tk.Scale(cur_card, from_=5, to=100, orient="horizontal",
                          resolution=5, command=_on_cursor_size,
                          bg=UI_CARD, fg=UI_TEXT2, troughcolor=UI_BG,
                          highlightthickness=0, bd=0, sliderlength=14, showvalue=0)
        cur_sl.set(self.cursor_size)
        cur_sl.pack(fill="x", padx=14, pady=(0, 10))

        # ── UNIDADES ────────────────────────────────────────────────
        _section(t1, "UNIDADES")
        u_card = _card(t1)
        _cfg_u = self._leer_config_ia()
        u_val  = "Imperial (ft/in)" if _cfg_u.get("units") == "imperial" else "Métrico (m)"
        u_var  = tk.StringVar(value=u_val)
        def _on_units(val):
            self._save_cfg_key("units", "imperial" if "ft" in val else "metric")
        _seg_btn(u_card, values=["Métrico (m)", "Imperial (ft/in)"],
                 variable=u_var, command=_on_units,
        ).pack(fill="x", padx=14, pady=(10, 4))
        prec_row = tk.Frame(u_card, bg=UI_CARD)
        prec_row.pack(fill="x", padx=14, pady=(4, 10))
        tk.Label(prec_row, text="Precisión decimal:", font=("Segoe UI", 10),
                 fg=UI_TEXT2, bg=UI_CARD).pack(side="left")
        prec_var = tk.StringVar(value=str(_cfg_u.get("precision_decimal", 4)))
        _om(prec_row, ["0","1","2","3","4","6"], prec_var,
            lambda v: self._save_cfg_key("precision_decimal", int(v)),
            width=5).pack(side="left", padx=8)
        tk.Label(prec_row, text="Precisión ángulo:", font=("Segoe UI", 10),
                 fg=UI_TEXT2, bg=UI_CARD).pack(side="left", padx=(12, 0))
        ang_prec_var = tk.StringVar(value=str(_cfg_u.get("precision_angle", 2)))
        _om(prec_row, ["0","1","2","3"], ang_prec_var,
            lambda v: self._save_cfg_key("precision_angle", int(v)),
            width=4).pack(side="left", padx=8)

        # ── CLIC DERECHO ────────────────────────────────────────────
        _section(t1, "CLIC DERECHO")
        rc_card = _card(t1)
        rc_var = tk.BooleanVar(value=self.rclick_as_enter)
        def _on_rclick():
            self.rclick_as_enter = rc_var.get()
            self._save_cfg_key("rclick_as_enter", self.rclick_as_enter)
        tk.Checkbutton(rc_card, text="Clic derecho = Enter  (estilo AutoCAD)",
                       variable=rc_var, command=_on_rclick, font=("Segoe UI", 10),
                       fg=UI_TEXT, bg=UI_CARD, selectcolor=UI_ACC,
                       activebackground=UI_CARD, activeforeground=UI_TEXT,
                       bd=0, cursor="hand2",
                       ).pack(anchor="w", padx=14, pady=(10, 4))
        _hint(rc_card, "OFF → muestra menú contextual al hacer clic derecho")
        ctk.CTkButton(rc_card, text="☰  Personalizar menú contextual…",
                      height=28, corner_radius=6,
                      fg_color=UI_CARD, hover_color=UI_BORD,
                      border_width=1, border_color=UI_BORD, font=ctk.CTkFont(size=11),
                      command=self._abrir_editor_menu_contextual,
                      ).pack(anchor="w", padx=14, pady=(0, 10))

        # ── SNAPS ───────────────────────────────────────────────────
        _section(t1, "SNAPS")
        snap_card = _card(t1)
        sg_var = tk.BooleanVar(value=self.snap_on)
        def _global_snap():
            self.snap_on = sg_var.get(); self._update_flags()
        tk.Checkbutton(snap_card, text="SNAP global ON",
                       variable=sg_var, command=_global_snap,
                       font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                       selectcolor=UI_ACC, activebackground=UI_CARD,
                       activeforeground=UI_TEXT, bd=0, cursor="hand2",
                       ).pack(anchor="w", padx=14, pady=(10, 6))
        tk.Frame(snap_card, height=1, bg=UI_BORD).pack(fill="x", padx=10)
        _SNAP_INFO = [
            ("end","END","Extremos"),("mid","MID","Punto medio"),
            ("cen","CEN","Centro"),  ("qua","QUA","Cuadrantes"),
            ("int","INT","Intersección"),("gri","GRI","Grilla"),
            ("per","PER","Perpendicular"),("tan","TAN","Tangente"),
            ("nea","NEA","Más cercano"),
        ]
        sg = tk.Frame(snap_card, bg=UI_CARD)
        sg.pack(fill="x", padx=10, pady=(6, 10))
        for i, (key, abbr, desc) in enumerate(_SNAP_INFO):
            row, col = divmod(i, 3)
            var = tk.BooleanVar(value=self._snap_types.get(key, True))
            def _on_chk(k=key, v=var):
                self._snap_types[k] = v.get()
            tk.Checkbutton(sg, text=f"{abbr} {desc}", variable=var, command=_on_chk,
                           font=("Segoe UI", 9), fg=UI_TEXT, bg=UI_CARD,
                           selectcolor=UI_ACC, activebackground=UI_CARD,
                           activeforeground=UI_TEXT, bd=0, cursor="hand2",
                           ).grid(row=row, column=col, sticky="w", padx=8, pady=2)

        # ── BARRA DE COMANDOS ────────────────────────────────────────
        _section(t1, "BARRA DE COMANDOS")
        cmd_card = _card(t1)
        cmd_float_var = tk.BooleanVar(value=self._cmd_floating)
        def _on_cmd_float():
            self._toggle_cmd_float()
            cmd_float_var.set(self._cmd_floating)
        tk.Checkbutton(cmd_card, text="Barra flotante  (arrastrable)",
                       variable=cmd_float_var, command=_on_cmd_float,
                       font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                       selectcolor=UI_ACC, activebackground=UI_CARD,
                       activeforeground=UI_TEXT, bd=0, cursor="hand2",
                       ).pack(anchor="w", padx=14, pady=(10, 4))
        _hint(cmd_card, "Doble clic en la barra azul → vuelve a posición original")

        # ── AUTO-RECUPERACIÓN ───────────────────────────────────────
        _section(t1, "AUTO-RECUPERACIÓN")
        ar_card = _card(t1)

        # Leer intervalo guardado (minutos); default = 3
        _cfg_ar = self._leer_config_ia()
        _ar_min_saved = int(_cfg_ar.get("autosave_min", 3))

        ar_top = tk.Frame(ar_card, bg=UI_CARD)
        ar_top.pack(fill="x", padx=14, pady=(10, 4))

        ar_sw_var = tk.BooleanVar(value=_cfg_ar.get("autosave_enabled", True))
        ar_min_var = tk.IntVar(value=_ar_min_saved)

        def _on_ar_toggle():
            enabled = ar_sw_var.get()
            self._autosave_enabled = enabled
            self._save_cfg_key("autosave_enabled", enabled)
            if enabled:
                # Reiniciar el ciclo
                if self._autosave_job:
                    self.root.after_cancel(self._autosave_job)
                ms = int(ar_min_var.get()) * 60 * 1000
                self._AUTOSAVE_MS  = ms
                self._autosave_job = self.root.after(ms, self._autosave_tick)
                ar_slider_row.pack(fill="x", padx=14, pady=(0, 4))
                ar_hint_row.pack(anchor="w", padx=14, pady=(0, 8))
            else:
                if self._autosave_job:
                    self.root.after_cancel(self._autosave_job)
                    self._autosave_job = None
                ar_slider_row.pack_forget()
                ar_hint_row.pack_forget()

        ar_sw = tk.Checkbutton(
            ar_top, text="Guardar copia de recuperación automáticamente",
            variable=ar_sw_var, command=_on_ar_toggle,
            font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
            selectcolor=UI_ACC, activebackground=UI_CARD,
            activeforeground=UI_TEXT, bd=0, cursor="hand2",
        )
        ar_sw.pack(side="left")

        # Fila del slider de intervalo
        ar_slider_row = tk.Frame(ar_card, bg=UI_CARD)
        ar_slider_row.pack(fill="x", padx=14, pady=(0, 4))

        tk.Label(ar_slider_row, text="Intervalo:",
                 font=("Segoe UI", 10), fg=UI_TEXT2, bg=UI_CARD).pack(side="left")
        ar_lbl = tk.Label(ar_slider_row, text=f"{ar_min_var.get()} min",
                          font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                          width=6, anchor="e")
        ar_lbl.pack(side="right")

        def _on_ar_slider(v):
            mins = int(float(v))
            ar_min_var.set(mins)
            ar_lbl.configure(text=f"{mins} min")
            self._AUTOSAVE_MS = mins * 60 * 1000
            self._save_cfg_key("autosave_min", mins)
            # Reiniciar el ciclo con el nuevo intervalo
            if self._autosave_job:
                self.root.after_cancel(self._autosave_job)
            self._autosave_job = self.root.after(self._AUTOSAVE_MS, self._autosave_tick)

        ar_slider = tk.Scale(
            ar_slider_row, from_=1, to=30, orient="horizontal",
            resolution=1, command=_on_ar_slider,
            bg=UI_CARD, fg=UI_TEXT2, troughcolor=UI_BG,
            highlightthickness=0, bd=0, sliderlength=14, showvalue=0,
        )
        ar_slider.set(ar_min_var.get())
        ar_slider.pack(side="left", fill="x", expand=True, padx=(10, 10))

        # Fila de pista
        ar_hint_row = tk.Frame(ar_card, bg=UI_CARD)
        ar_hint_row.pack(anchor="w", padx=14, pady=(0, 8))
        tk.Label(
            ar_hint_row,
            text="El archivo recovery.json se elimina al cerrar correctamente.",
            font=("Segoe UI", 9), fg=UI_BORD, bg=UI_CARD,
        ).pack(anchor="w")

        # Ocultar controles si está desactivado
        if not ar_sw_var.get():
            ar_slider_row.pack_forget()
            ar_hint_row.pack_forget()

        # ── IDIOMA ──────────────────────────────────────────────────
        _section(t1, "IDIOMA")
        lang_card = _card(t1)
        _cfg_cur = self._leer_config_ia()
        _lang_var = tk.StringVar(value=LANGUAGES.get(_cfg_cur.get("language","es"), "Español"))
        def _on_lang(display_name):
            code = next((k for k, v in LANGUAGES.items() if v == display_name), "es")
            set_language(code)
            self._save_cfg_key("language", code)
            self._apply_language()
        _om(lang_card, list(LANGUAGES.values()), _lang_var,
            _on_lang, width=22).pack(anchor="w", padx=14, pady=(10, 10))

        # ════════════════════════════════════════════════════════════
        # TAB 2 — COTAS
        # ════════════════════════════════════════════════════════════
        t2 = _scroll_tab(tabs.tab("📐 Cotas"))

        cfg_c = self._leer_config_ia()
        dimstyles = cfg_c.get("dimstyles", {})
        all_styles = list(dimstyles.get("styles", {}).keys()) or ["Arq-50"]
        active_ds  = dimstyles.get("active", all_styles[0])

        # Ensure default dimstyle exists
        if "Arq-50" not in dimstyles.get("styles", {}):
            _default_ds = {
                "arrow_type": "architectural", "arrow_size": 0.15,
                "text_height": 0.20, "text_style": "Standard",
                "text_position": "above", "text_align": "with_dim",
                "ext_beyond": 0.20, "ext_offset": 0.05, "text_offset": 0.05,
                "units_format": "decimal", "precision": 2,
                "suffix": " m", "scale_factor": 1.0,
                "line_color": "BYLAYER", "text_color": "BYLAYER",
            }
            if "styles" not in dimstyles:
                dimstyles["styles"] = {}
            dimstyles["styles"]["Arq-50"] = _default_ds
            cfg_c["dimstyles"] = dimstyles
            all_styles = ["Arq-50"]
            active_ds  = "Arq-50"

        # ── Estilo activo ────────────────────────────────────────────
        _section(t2, "ESTILO ACTIVO")
        ds_hdr = _card(t2, pady=(0, 6))
        ds_hdr_row = tk.Frame(ds_hdr, bg=UI_CARD)
        ds_hdr_row.pack(fill="x", padx=14, pady=(10, 10))

        ds_var = tk.StringVar(value=active_ds)
        ds_om  = _om(ds_hdr_row, all_styles, ds_var, width=18)
        ds_om.pack(side="left")

        def _save_ds_active(name):
            c = self._leer_config_ia()
            c.setdefault("dimstyles", {})["active"] = name
            self._write_config(c)
            # _do_refresh_preview se define más abajo — se llama via after()
            t2.after(80, lambda: _do_refresh_preview())

        ds_om.configure(command=_save_ds_active)

        ctk.CTkButton(ds_hdr_row, text="+ Nuevo", width=70, height=28,
                      fg_color=UI_SUCC, hover_color="#15803D", font=_SMALL,
                      command=lambda: self._dim_new_style(ds_var, ds_om)
                      ).pack(side="left", padx=6)
        ctk.CTkButton(ds_hdr_row, text="⎘ Dupl.", width=70, height=28,
                      fg_color=UI_CARD, hover_color=UI_ACC, font=_SMALL,
                      command=lambda: self._dim_dup_style(ds_var, ds_om)
                      ).pack(side="left", padx=2)
        ctk.CTkButton(ds_hdr_row, text="🗑", width=40, height=28,
                      fg_color=UI_CARD, hover_color="#DC2626", font=_SMALL,
                      command=lambda: self._dim_del_style(ds_var, ds_om, all_styles)
                      ).pack(side="left", padx=2)

        def _get_ds():
            c = self._leer_config_ia()
            return c.get("dimstyles", {}).get("styles", {}).get(ds_var.get(), {})

        # Referencia mutable para debounce del preview
        _prev_pending = [False]
        _prev_photo   = [None]   # mantiene referencia del PhotoImage

        def _set_ds(key, val):
            c = self._leer_config_ia()
            c.setdefault("dimstyles", {}).setdefault("styles", {}).setdefault(
                ds_var.get(), {})
            c["dimstyles"]["styles"][ds_var.get()][key] = val
            self._write_config(c)
            self._pil_img_cache = None  # invalidar para que canvas redibuje con nuevo estilo
            # Actualizar preview + canvas con debounce 120 ms
            if not _prev_pending[0]:
                _prev_pending[0] = True
                t2.after(120, _do_refresh_preview)
                self.root.after(150, self._redraw_static)

        # ── Helpers para la UI de DIMSTYLE ────────────────────────────
        def _row2(parent, label, widget_fn):
            r = tk.Frame(parent, bg=UI_CARD)
            r.pack(fill="x", padx=14, pady=3)
            tk.Label(r, text=label, font=("Segoe UI", 10), fg=UI_TEXT2,
                     bg=UI_CARD, width=25, anchor="w").pack(side="left")
            widget_fn(r)

        # ── VISTA PREVIA ─────────────────────────────────────────────
        _section(t2, "VISTA PREVIA")
        prev_card = _card(t2)
        PW, PH = 330, 148     # píxeles del canvas de preview

        prev_lbl = tk.Label(prev_card, text="Cargando…",
                            bg="#111827", fg=UI_TEXT2,
                            font=("Segoe UI", 10))
        prev_lbl.pack(padx=10, pady=10)

        def _dim_preview_img(ds: dict) -> "PIL.Image.Image":
            """Genera imagen PIL de la cota con los parámetros del dimstyle."""
            try:
                from PIL import Image, ImageDraw, ImageFont as _IF
            except ImportError:
                return None

            img  = Image.new("RGB", (PW, PH), "#111827")
            draw = ImageDraw.Draw(img)

            # Geometría del preview (fija)
            PAD   = 38         # margen horizontal
            DIM_Y = PH // 2 + 8   # Y de la línea de cota
            x1p, x2p = PAD, PW - PAD   # extremos en px

            # Escala fija para representar ~1.50 m
            DIM_M = 1.50
            sc_p  = (x2p - x1p) / DIM_M   # px/m

            # Parámetros del dimstyle
            text_h  = float(ds.get("text_height", 0.20))
            arr_sz  = float(ds.get("arrow_size",  0.06))
            ext_off = float(ds.get("ext_offset",  0.02))
            ext_ov  = float(ds.get("ext_overshoot", ds.get("ext_beyond", 0.03)))
            prec    = int(ds.get("precision", 2))
            suffix  = ds.get("suffix", "")

            TPIX = max(8,  min(32, int(text_h  * sc_p)))
            ARR  = max(4,  min(18, int(arr_sz  * sc_p)))
            GAP  = max(2,  min(12, int(ext_off * sc_p)))
            OVER = max(1,  min(8,  int(ext_ov  * sc_p)))

            # Colores
            def _rc(cv, dflt):
                if not cv or cv.lower() in ("bylayer","byblock",""):
                    return dflt
                if cv.startswith("#") and len(cv) == 7:
                    try:
                        h = cv.lstrip("#")
                        return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16))
                    except Exception:
                        pass
                return dflt

            col_line = _rc(ds.get("line_color","bylayer"), (200, 210, 220))
            col_txt  = _rc(ds.get("text_color","bylayer"), (240, 245, 250))
            col_wall = (80, 100, 120)

            # "Muros" que se están acotando
            wall_top = DIM_Y - 38
            wall_bot = DIM_Y - 4
            draw.rectangle([x1p-2, wall_top, x1p+2, wall_bot], fill=col_wall)
            draw.rectangle([x2p-2, wall_top, x2p+2, wall_bot], fill=col_wall)
            # Etiquetas de los muros
            draw.text((x1p, wall_top - 6), "P1", fill=(100,120,140),
                      anchor="mm")
            draw.text((x2p, wall_top - 6), "P2", fill=(100,120,140),
                      anchor="mm")

            # Líneas de extensión
            def _ext(x):
                y0 = wall_bot + GAP
                y1 = DIM_Y   + OVER
                draw.line([(x, y0), (x, y1)], fill=col_line, width=1)
            _ext(x1p);  _ext(x2p)

            # Línea de cota
            draw.line([(x1p, DIM_Y), (x2p, DIM_Y)], fill=col_line, width=1)

            # Flechas (siempre triángulo relleno en el preview)
            def _arrow(tip_x, dir_x):
                bx = tip_x + dir_x * ARR
                p1 = (bx, DIM_Y - max(2, ARR//3))
                p2 = (bx, DIM_Y + max(2, ARR//3))
                draw.polygon([(tip_x, DIM_Y), p1, p2], fill=col_line)

            _arrow(x1p, +1);  _arrow(x2p, -1)

            # Texto de la cota
            txt = f"{DIM_M:.{prec}f}{suffix}"
            try:
                fnt = _IF.truetype("arial.ttf", TPIX)
            except Exception:
                try:
                    fnt = _IF.load_default(size=TPIX)
                except Exception:
                    fnt = _IF.load_default()

            cx = (x1p + x2p) // 2
            ty = DIM_Y - TPIX // 2 - 5
            draw.text((cx, ty), txt, fill=col_txt, font=fnt, anchor="mm")

            # Nombre del estilo (esquina inferior derecha)
            draw.text((PW - 8, PH - 6), ds_var.get(),
                      fill=(60, 80, 100), anchor="rb")

            return img

        def _do_refresh_preview():
            """Regenera la imagen de preview y la muestra en prev_lbl."""
            _prev_pending[0] = False
            try:
                img = _dim_preview_img(_get_ds())
                if img is None:
                    prev_lbl.configure(text="PIL no disponible", image="")
                    return
                from PIL.ImageTk import PhotoImage as _PILPhoto
                ph = _PILPhoto(img)
                _prev_photo[0] = ph   # mantener referencia
                prev_lbl.configure(image=ph, text="")
            except Exception as exc:
                prev_lbl.configure(text=f"Preview no disponible\n{str(exc)[:60]}", image="")

        # Mostrar preview inicial
        t2.after(50, _do_refresh_preview)

        # ── LÍNEAS ──────────────────────────────────────────────────
        _section(t2, "LÍNEAS")
        ln_card = _card(t2)

        ds = _get_ds()
        ext_overshoot_var = tk.StringVar(value=str(ds.get("ext_overshoot", ds.get("ext_beyond", 0.03))))
        ext_offset_var    = tk.StringVar(value=str(ds.get("ext_offset", 0.02)))
        baseline_sp_var   = tk.StringVar(value=str(ds.get("baseline_spacing", 0.0)))

        def _save_ext_overshoot(v):
            try:
                _set_ds("ext_overshoot", float(v))
            except ValueError:
                pass

        def _save_ext_offset(v):
            try:
                _set_ds("ext_offset", float(v))
            except ValueError:
                pass

        def _save_baseline_spacing(v):
            try:
                _set_ds("baseline_spacing", float(v))
            except ValueError:
                pass

        ext_overshoot_var.trace("w", lambda *args: _save_ext_overshoot(ext_overshoot_var.get()))
        ext_offset_var.trace("w",    lambda *args: _save_ext_offset(ext_offset_var.get()))
        baseline_sp_var.trace("w",   lambda *args: _save_baseline_spacing(baseline_sp_var.get()))

        _row2(ln_card, "Prolongación de línea (m):",
              lambda p: ctk.CTkEntry(p, textvariable=ext_overshoot_var, width=80, height=24,
                                     font=_SMALL).pack(side="left"))
        _row2(ln_card, "Desfase del punto (m):",
              lambda p: ctk.CTkEntry(p, textvariable=ext_offset_var, width=80, height=24,
                                     font=_SMALL).pack(side="left"))
        _row2(ln_card, "Espaciado base DCO (m):",
              lambda p: ctk.CTkEntry(p, textvariable=baseline_sp_var, width=80, height=24,
                                     font=_SMALL).pack(side="left"))
        tk.Frame(ln_card, height=6, bg=UI_CARD).pack()

        # ── FLECHAS ──────────────────────────────────────────────────
        _section(t2, "FLECHAS")
        ar_card = _card(t2)
        ds = _get_ds()
        arrow_var = tk.StringVar(value=ds.get("arrow_type", "architectural"))
        arrow_sz_var = tk.StringVar(value=str(ds.get("arrow_size", 0.06)))

        def _save_arrow_size(v):
            try:
                _set_ds("arrow_size", float(v))
            except ValueError:
                pass

        arrow_sz_var.trace("w", lambda *args: _save_arrow_size(arrow_sz_var.get()))

        _row2(ar_card, "Tipo:",
              lambda p: _om(p, ["architectural","closed_filled","dot","none"],
                  arrow_var, lambda v: _set_ds("arrow_type", v),
                  width=18).pack(side="left"))
        _row2(ar_card, "Tamaño (m):",
              lambda p: ctk.CTkEntry(p, textvariable=arrow_sz_var, width=80,
                                     height=24, font=_SMALL).pack(side="left"))
        tk.Frame(ar_card, height=6, bg=UI_CARD).pack()

        # ── TEXTO DE COTA ─────────────────────────────────────────────
        _section(t2, "TEXTO")
        tx_card = _card(t2)
        ds = _get_ds()
        txt_h_var     = tk.StringVar(value=str(ds.get("text_height", 0.20)))
        txt_off_var   = tk.StringVar(value=str(ds.get("text_offset", 0.05)))
        txt_pos_var   = tk.StringVar(value=ds.get("text_position", "above"))
        txt_aln_var   = tk.StringVar(value=ds.get("text_align", "aligned"))
        txt_style_var = tk.StringVar(value=ds.get("text_style", ""))

        def _save_text_height(v):
            try:
                _set_ds("text_height", float(v))
            except ValueError:
                pass

        def _save_text_offset(v):
            try:
                _set_ds("text_offset", float(v))
            except ValueError:
                pass

        txt_h_var.trace("w",     lambda *args: _save_text_height(txt_h_var.get()))
        txt_off_var.trace("w",   lambda *args: _save_text_offset(txt_off_var.get()))
        txt_style_var.trace("w", lambda *args: _set_ds("text_style", txt_style_var.get().strip()))

        _row2(tx_card, "Fuente (TTF):",
              lambda p: ctk.CTkEntry(p, textvariable=txt_style_var, width=140,
                                     height=24, font=_SMALL,
                                     placeholder_text="arial.ttf  /  cour.ttf"
                                     ).pack(side="left"))
        _row2(tx_card, "Altura (m):",
              lambda p: ctk.CTkEntry(p, textvariable=txt_h_var, width=80,
                                     height=24, font=_SMALL).pack(side="left"))
        _row2(tx_card, "Offset texto-línea (m):",
              lambda p: ctk.CTkEntry(p, textvariable=txt_off_var, width=80,
                                     height=24, font=_SMALL).pack(side="left"))
        _row2(tx_card, "Posición:",
              lambda p: _seg_btn(p,
                  values=["above", "center"],
                  variable=txt_pos_var,
                  command=lambda v: _set_ds("text_position", v)
              ).pack(side="left"))
        _row2(tx_card, "Alineación:",
              lambda p: _seg_btn(p,
                  values=["aligned", "horizontal"],
                  variable=txt_aln_var,
                  command=lambda v: _set_ds("text_align", v)
              ).pack(side="left"))
        tk.Frame(tx_card, height=6, bg=UI_CARD).pack()

        # ── UNIDADES DE COTA ──────────────────────────────────────────
        _section(t2, "UNIDADES DE COTA")
        uc_card = _card(t2)
        ds = _get_ds()
        uc_fmt_var  = tk.StringVar(value=ds.get("units_format","decimal"))
        uc_prec_var = tk.StringVar(value=str(ds.get("precision", 2)))
        uc_suf_var  = tk.StringVar(value=ds.get("suffix", " m"))
        uc_sc_var   = tk.StringVar(value=str(ds.get("scale_factor", 1.0)))

        def _save_suffix(v):
            _set_ds("suffix", v)

        def _save_scale_factor(v):
            try:
                _set_ds("scale_factor", float(v))
            except ValueError:
                pass

        uc_suf_var.trace("w", lambda *args: _save_suffix(uc_suf_var.get()))
        uc_sc_var.trace("w", lambda *args: _save_scale_factor(uc_sc_var.get()))

        _row2(uc_card, "Formato:",
              lambda p: _seg_btn(p,
                  values=["decimal","fractional"],
                  variable=uc_fmt_var,
                  command=lambda v: _set_ds("units_format", v)
              ).pack(side="left"))
        _row2(uc_card, "Precisión:",
              lambda p: _om(p, ["0","1","2","3","4"], uc_prec_var,
                  lambda v: _set_ds("precision", int(v)),
                  width=5).pack(side="left"))
        _row2(uc_card, "Sufijo:",
              lambda p: ctk.CTkEntry(p, textvariable=uc_suf_var, width=80,
                                     height=24, font=_SMALL).pack(side="left"))
        _row2(uc_card, "Factor escala:",
              lambda p: ctk.CTkEntry(p, textvariable=uc_sc_var, width=80,
                                     height=24, font=_SMALL).pack(side="left"))

        # ── COLORES ───────────────────────────────────────────────────
        _section(t2, "COLORES")
        cl_card = _card(t2)
        ds = _get_ds()

        def _color_swatch_row(parent, label, cfg_key, default="#F8FAFC"):
            """Fila con label + swatch de color clickeable."""
            saved = ds.get(cfg_key, default)
            # "bylayer" → mostrar blanco como representación visual
            hex_val = saved if (saved and saved.startswith("#")) else "#F8FAFC"
            col_var = tk.StringVar(value=hex_val)

            r = tk.Frame(parent, bg=UI_CARD)
            r.pack(fill="x", padx=14, pady=4)
            tk.Label(r, text=label, font=("Segoe UI", 10), fg=UI_TEXT2,
                     bg=UI_CARD, width=22, anchor="w").pack(side="left")

            # Swatch — botón de color
            swatch_ref = []

            def _pick(cv=col_var, ck=cfg_key, sr=swatch_ref):
                from tkinter import colorchooser as _cc
                result = _cc.askcolor(color=cv.get(), title=f"Color — {label}")[1]
                if result:
                    result = result.upper()
                    cv.set(result)
                    if sr:
                        sr[0].configure(
                            fg_color=result,
                            text=result,
                            text_color="#000000" if _is_light(result) else "#FFFFFF")
                    _set_ds(ck, result)   # también programa refresh de preview
                    self._redraw()

            # tk.Button nativo — CTkButton.__init__ → _draw() bloquea 500ms bajo GIL
            _sw_btn = tk.Button(
                r, text=hex_val, width=14, height=1,
                bg=hex_val,
                fg="#000000" if _is_light(hex_val) else "#FFFFFF",
                font=("Courier New", 9),
                relief="flat", bd=0,
                activebackground=hex_val, activeforeground="#000000",
                command=_pick)
            _sw_btn.pack(side="left", padx=6)
            # Wrapper configure(fg_color/text_color=...) para compatibilidad con _pick/_set_bylayer
            _sw_orig_cfg = _sw_btn.configure
            def _sw_cfg(fg_color=None, text_color=None, **kw):
                if fg_color is not None:
                    kw["bg"] = fg_color; kw["activebackground"] = fg_color
                if text_color is not None:
                    kw["fg"] = text_color
                if kw: _sw_orig_cfg(**kw)
            _sw_btn.configure = _sw_cfg
            sw = _sw_btn
            swatch_ref.append(sw)

            # Botón "BYLAYER"
            def _set_bylayer(cv=col_var, ck=cfg_key, sw_=sw):
                cv.set("#F8FAFC")
                sw_.configure(fg_color="#F8FAFC", text="BYLAYER", text_color="#000000")
                _set_ds(ck, "bylayer")
                self._redraw()

            _by_btn = tk.Button(r, text="BYLAYER", width=8, height=1,
                                bg=UI_CARD, fg=UI_TEXT, font=_SMALL,
                                relief="flat", bd=0,
                                activebackground=UI_ACC, activeforeground=UI_TEXT,
                                command=_set_bylayer)
            _by_btn.pack(side="left", padx=2)

        _color_swatch_row(cl_card, "Color de líneas:", "line_color")
        _color_swatch_row(cl_card, "Color de texto:",  "text_color")
        tk.Frame(cl_card, height=6, bg=UI_CARD).pack()

        # ── GROSOR DE LÍNEA ───────────────────────────────────────────
        _section(t2, "GROSOR DE LÍNEA")
        lw_card = _card(t2)
        ds = _get_ds()

        # Grosor en mm (valores estándar DXF/AutoCAD)
        _LW_OPTIONS = ["BYLAYER", "0.09", "0.13", "0.18", "0.25",
                       "0.35", "0.50", "0.70", "1.00"]
        _saved_lw = str(ds.get("lineweight", "BYLAYER"))
        lw_var = tk.StringVar(value=_saved_lw if _saved_lw in _LW_OPTIONS else "BYLAYER")

        lw_r = tk.Frame(lw_card, bg=UI_CARD)
        lw_r.pack(fill="x", padx=14, pady=6)
        tk.Label(lw_r, text="Grosor (mm):", font=("Segoe UI", 10),
                 fg=UI_TEXT2, bg=UI_CARD, width=20, anchor="w").pack(side="left")
        _om(lw_r, _LW_OPTIONS, lw_var,
            lambda v: _set_ds("lineweight", v),
            width=10).pack(side="left", padx=6)
        tk.Frame(lw_card, height=6, bg=UI_CARD).pack()

        # ── INFO ──────────────────────────────────────────────────────
        info_frame = tk.Frame(t2, bg=UI_BG)
        info_frame.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(info_frame,
                 text="💾 Los cambios se guardan automáticamente y se aplican al renderizar",
                 font=("Segoe UI", 9), fg=UI_TEXT2, bg=UI_BG,
                 wraplength=340, justify="left").pack(anchor="w")

        # ════════════════════════════════════════════════════════════
        # TAB 3 — TEXTO
        # ════════════════════════════════════════════════════════════
        t3 = _scroll_tab(tabs.tab("🖊 Texto"))

        _section(t3, "ESTILO ACTIVO")
        cfg_tx  = self._leer_config_ia()
        txstyles = cfg_tx.get("textstyles", {})
        tx_names  = list(txstyles.get("styles", {}).keys()) or ["Standard"]
        tx_active = txstyles.get("active", tx_names[0])
        if "Standard" not in txstyles.get("styles", {}):
            txstyles.setdefault("styles", {})["Standard"] = {
                "font":"Courier New","height":0.20,"bold":False,
                "italic":False,"width_factor":1.0,"oblique":0.0}

        tx_hdr_card = _card(t3, pady=(0,6))
        tx_hdr_row  = tk.Frame(tx_hdr_card, bg=UI_CARD)
        tx_hdr_row.pack(fill="x", padx=14, pady=(10,10))
        tx_style_var = tk.StringVar(value=tx_active)
        tx_om = _om(tx_hdr_row, tx_names, tx_style_var, width=18)
        tx_om.pack(side="left")
        ctk.CTkButton(tx_hdr_row, text="+ Nuevo", width=70, height=28,
                      fg_color=UI_SUCC, hover_color="#15803D", font=_SMALL,
                      command=lambda: self._tx_new_style(tx_style_var, tx_om)
                      ).pack(side="left", padx=6)

        def _get_tx():
            c = self._leer_config_ia()
            return c.get("textstyles",{}).get("styles",{}).get(tx_style_var.get(),{})

        def _set_tx(key, val):
            c = self._leer_config_ia()
            c.setdefault("textstyles",{}).setdefault("styles",{}).setdefault(
                tx_style_var.get(), {})
            c["textstyles"]["styles"][tx_style_var.get()][key] = val
            self._write_config(c)

        _section(t3, "PARÁMETROS")
        tx_card2 = _card(t3)
        tx_s = _get_tx()
        tx_font_var = tk.StringVar(value=tx_s.get("font","Courier New"))
        tx_h_var    = tk.StringVar(value=str(tx_s.get("height",0.20)))
        tx_wf_var   = tk.StringVar(value=str(tx_s.get("width_factor",1.0)))
        tx_ob_var   = tk.StringVar(value=str(tx_s.get("oblique",0.0)))

        def _row_tx(label, widget_fn):
            r = tk.Frame(tx_card2, bg=UI_CARD)
            r.pack(fill="x", padx=14, pady=3)
            tk.Label(r, text=label, font=("Segoe UI", 10), fg=UI_TEXT2,
                     bg=UI_CARD, width=24, anchor="w").pack(side="left")
            widget_fn(r)

        _row_tx("Fuente:", lambda p: _om(p,
            ["Courier New","Arial","Consolas","Segoe UI","Times New Roman"],
            tx_font_var, lambda v: _set_tx("font", v),
            width=20).pack(side="left"))
        _row_tx("Altura por defecto (m):", lambda p: ctk.CTkEntry(
            p, textvariable=tx_h_var, width=80, height=24,
            font=_SMALL).pack(side="left"))
        _row_tx("Factor ancho:", lambda p: ctk.CTkEntry(
            p, textvariable=tx_wf_var, width=80, height=24,
            font=_SMALL).pack(side="left"))
        _row_tx("Oblicuidad (°):", lambda p: ctk.CTkEntry(
            p, textvariable=tx_ob_var, width=80, height=24,
            font=_SMALL).pack(side="left"))
        tx_bold_var   = tk.BooleanVar(value=tx_s.get("bold",False))
        tx_italic_var = tk.BooleanVar(value=tx_s.get("italic",False))
        style_row = tk.Frame(tx_card2, bg=UI_CARD)
        style_row.pack(fill="x", padx=14, pady=(4,10))
        tk.Checkbutton(style_row, text="Negrita", variable=tx_bold_var,
                       command=lambda: _set_tx("bold", tx_bold_var.get()),
                       font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                       selectcolor=UI_ACC, activebackground=UI_CARD,
                       activeforeground=UI_TEXT, bd=0, cursor="hand2",
                       ).pack(side="left", padx=4)
        tk.Checkbutton(style_row, text="Cursiva", variable=tx_italic_var,
                       command=lambda: _set_tx("italic", tx_italic_var.get()),
                       font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                       selectcolor=UI_ACC, activebackground=UI_CARD,
                       activeforeground=UI_TEXT, bd=0, cursor="hand2",
                       ).pack(side="left", padx=4)

        def _apply_txstyle():
            try:
                _set_tx("height",       float(tx_h_var.get()))
                _set_tx("width_factor", float(tx_wf_var.get()))
                _set_tx("oblique",      float(tx_ob_var.get()))
                c = self._leer_config_ia()
                c.setdefault("textstyles",{})["active"] = tx_style_var.get()
                self._write_config(c)
            except ValueError:
                pass

        ctk.CTkButton(t3, text="✔  Aplicar estilo de texto", height=32,
                      fg_color=UI_SUCC, hover_color="#15803D", font=_SMALL,
                      command=_apply_txstyle,
                      ).pack(fill="x", padx=12, pady=(4,12))

        # ════════════════════════════════════════════════════════════
        # TAB 4 — VISUAL
        # ════════════════════════════════════════════════════════════
        t4 = _scroll_tab(tabs.tab("🎨 Visual"))

        cfg_v = self._leer_config_ia()

        _section(t4, "GRILLA")
        gr_card = _card(t4)
        gr_maj_var = tk.StringVar(value=str(cfg_v.get("grid_spacing_major", 1.0)))
        gr_min_var = tk.StringVar(value=str(cfg_v.get("grid_spacing_minor", 0.25)))
        gr_vis_var = tk.BooleanVar(value=self.grid_on)
        tk.Checkbutton(gr_card, text="Visible al iniciar", variable=gr_vis_var,
                       command=lambda: self._save_cfg_key("grid_on_startup", gr_vis_var.get()),
                       font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                       selectcolor=UI_ACC, activebackground=UI_CARD,
                       activeforeground=UI_TEXT, bd=0, cursor="hand2",
                       ).pack(anchor="w", padx=14, pady=(10,4))
        def _row_v(parent, label, widget_fn):
            r = tk.Frame(parent, bg=UI_CARD)
            r.pack(fill="x", padx=14, pady=3)
            tk.Label(r, text=label, font=("Segoe UI", 10), fg=UI_TEXT2,
                     bg=UI_CARD, width=25, anchor="w").pack(side="left")
            widget_fn(r)
        _row_v(gr_card, "Espaciado mayor (m):", lambda p: ctk.CTkEntry(
            p, textvariable=gr_maj_var, width=80, height=24, font=_SMALL).pack(side="left"))
        _row_v(gr_card, "Espaciado menor (m):", lambda p: ctk.CTkEntry(
            p, textvariable=gr_min_var, width=80, height=24, font=_SMALL).pack(side="left"))
        tk.Frame(gr_card, height=6, bg=UI_CARD).pack()

        _section(t4, "LINETYPES EN CANVAS")
        lt_card = _card(t4)
        lt_var = tk.BooleanVar(value=cfg_v.get("linetypes_in_canvas", False))
        tk.Checkbutton(lt_card, text="Renderizar DASHED/CENTER/DOTTED en canvas",
                       variable=lt_var,
                       command=lambda: self._save_cfg_key("linetypes_in_canvas", lt_var.get()),
                       font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                       selectcolor=UI_ACC, activebackground=UI_CARD,
                       activeforeground=UI_TEXT, bd=0, cursor="hand2",
                       ).pack(anchor="w", padx=14, pady=(10,4))
        lt_sc_var = tk.StringVar(value=str(cfg_v.get("ltscale", 1.0)))
        _row_v(lt_card, "Escala global (LTSCALE):", lambda p: ctk.CTkEntry(
            p, textvariable=lt_sc_var, width=80, height=24, font=_SMALL).pack(side="left"))
        _hint(lt_card, "Afecta solo la visualización en canvas; el DXF exportado usa su propio LTSCALE")

        # ── RENDERER (OpenGL / PIL) ───────────────────────────────────────
        _section(t4, "RENDERER")
        rd_card = _card(t4)
        _rendering_cfg  = cfg_v.get("rendering", {})
        _backend_actual = _rendering_cfg.get("backend", "auto")
        rd_var = tk.StringVar(value=_backend_actual)

        from cad.renderer_opengl import RendererOpenGL as _RGL
        _ogl_ok = _RGL().available()

        tk.Label(rd_card, text="Backend de renderizado",
                 font=("Segoe UI", 10), fg=UI_TEXT2, bg=UI_CARD,
                 ).pack(anchor="w", padx=14, pady=(10, 4))
        _rd_cmd = lambda v: (
            self._save_cfg_key("rendering",
                {"backend": v, "fallback_to_pil_on_error": True}),
            self._echo(f"Renderer cambiado a '{v}' — reinicie el visor para aplicar")
        )
        _seg_btn(rd_card, values=["auto", "opengl", "pil"],
                 variable=rd_var, command=_rd_cmd,
        ).pack(anchor="w", padx=14, pady=(0, 4))

        _ogl_status = "✅ OpenGL 3.3+ disponible" if _ogl_ok \
                      else "⚠️ OpenGL no disponible en este sistema"
        _hint(rd_card, f"{_ogl_status}\n"
                       "Auto (recomendado): usa OpenGL si está disponible, si no PIL\n"
                       "OpenGL: forzar GPU — más rápido en planos grandes\n"
                       "PIL: forzar CPU — compatible con todo hardware\n"
                       "El cambio se aplica al reiniciar el visor")

        # ── Antialiasing ──────────────────────────────────────────────────
        tk.Label(rd_card, text="Antialiasing de líneas",
                 font=("Segoe UI", 10), fg=UI_TEXT2, bg=UI_CARD,
                 ).pack(anchor="w", padx=14, pady=(10, 4))
        _hint(rd_card, "GL_LINE_SMOOTH + GL_BLEND activos — calidad óptima en NVIDIA\n"
                       "MSAA requiere contexto de ventana GL nativo (pendiente)")

        _section(t4, "TOOLBAR")
        tb_card = _card(t4)
        tb_pos_var = tk.StringVar(value=cfg_v.get("toolbar_position","left+top"))
        tk.Label(tb_card, text="Posición barra herramientas",
                 font=("Segoe UI", 10), fg=UI_TEXT2, bg=UI_CARD,
                 ).pack(anchor="w", padx=14, pady=(10,4))
        _seg_btn(tb_card, values=["left+top","top only","left only"],
                 variable=tb_pos_var,
                 command=lambda v: self._save_cfg_key("toolbar_position", v),
        ).pack(anchor="w", padx=14, pady=(0,10))
        _hint(tb_card, "Los cambios de posición se aplican al reiniciar el visor")

        # ── Íconos de interfaz ─────────────────────────────────────────────
        _section(t4, "INTERFAZ 2 — ÍCONOS")
        mi_card = _card(t4)
        _icons_var = tk.BooleanVar(
            value=bool(self._leer_config_ia().get("menu_icons", False)))

        def _on_menu_icons_toggle(val=None):
            self._toggle_menu_icons()
            _icons_var.set(bool(self._leer_config_ia().get("menu_icons", False)))

        _row_icons = tk.Frame(mi_card, bg=UI_CARD)
        _row_icons.pack(fill="x", padx=14, pady=(10, 8))
        tk.Label(_row_icons, text="Íconos en barras y paneles",
                 font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                 anchor="w").pack(side="left")
        ctk.CTkSwitch(
            _row_icons, text="",
            variable=_icons_var,
            command=_on_menu_icons_toggle,
            onvalue=True, offvalue=False,
            fg_color=UI_CARD,
            progress_color=UI_ACC,
            button_color=UI_TEXT,
        ).pack(side="right")
        _hint(mi_card, "Activa/desactiva íconos Lucide en barras y paneles (Interface 2).\n"
                       "Los menús desplegables SIEMPRE muestran íconos.\n"
                       "También: escribir MENUICONS o MIC en la barra de comandos.")

        def _apply_visual():
            try:
                self._save_cfg_key("grid_spacing_major", float(gr_maj_var.get()))
                self._save_cfg_key("grid_spacing_minor", float(gr_min_var.get()))
                self._save_cfg_key("ltscale",            float(lt_sc_var.get()))
            except ValueError:
                pass

        ctk.CTkButton(t4, text="✔  Aplicar configuración visual", height=32,
                      fg_color=UI_SUCC, hover_color="#15803D", font=_SMALL,
                      command=_apply_visual,
                      ).pack(fill="x", padx=12, pady=(4,12))

        # ════════════════════════════════════════════════════════════
        # TAB 5 — IA
        # ════════════════════════════════════════════════════════════
        t5 = _scroll_tab(tabs.tab("🤖 IA"))

        _section(t5, "PROVEEDOR")
        ia_card = _card(t5)
        cfg = self._leer_config_ia()
        _MODELS: dict[str, list] = {
            "anthropic": ["claude-opus-4-5","claude-sonnet-4-5","claude-3-5-haiku-20241022"],
            "openai":    ["gpt-4o","gpt-4o-mini","gpt-4-turbo"],
            "gemini":    ["gemini-2.0-flash","gemini-1.5-pro","gemini-1.5-flash"],
        }
        def _lbl_ia(parent, text):
            tk.Label(parent, text=text, font=("Segoe UI", 10),
                     fg=UI_TEXT2, bg=UI_CARD, anchor="w", width=12).pack(side="left", padx=(14,4))
        row_p = tk.Frame(ia_card, bg=UI_CARD)
        row_p.pack(fill="x", pady=(12,4))
        _lbl_ia(row_p, "Proveedor:")
        prov_var = tk.StringVar(value=cfg.get("provider","anthropic"))
        prov_om  = _om(row_p, ["anthropic","openai","gemini"], prov_var, width=16)
        prov_om.pack(side="left")
        row_m = tk.Frame(ia_card, bg=UI_CARD)
        row_m.pack(fill="x", pady=4)
        _lbl_ia(row_m, "Modelo:")
        model_var = tk.StringVar(value=cfg.get("model", _MODELS["anthropic"][0]))
        model_om  = _om(row_m, _MODELS.get(prov_var.get(),[]) or [""],
                        model_var, width=26)
        model_om.pack(side="left")
        def _on_prov(p):
            ms = _MODELS.get(p, [])
            model_om.configure(values=ms)
            if ms: model_var.set(ms[0])
            # SEC-01: leer desde keyring, no desde settings.json
            key_var.set(self._kr_get(p) or cfg.get(f"{p}_api_key", ""))
        prov_om.configure(command=_on_prov)   # ruteado a _compat_cfg → actualiza menu items
        row_k = tk.Frame(ia_card, bg=UI_CARD)
        row_k.pack(fill="x", pady=4)
        _lbl_ia(row_k, "API Key:")
        # SEC-01: leer desde keyring, no desde settings.json
        key_var = tk.StringVar(value=self._kr_get(prov_var.get()) or cfg.get(f"{prov_var.get()}_api_key", ""))
        ctk.CTkEntry(row_k, textvariable=key_var, show="•",
                     width=240, height=26, font=_SMALL,
                     placeholder_text="sk-…  /  sk-ant-…  /  AIza…").pack(side="left")
        row_t = tk.Frame(ia_card, bg=UI_CARD)
        row_t.pack(fill="x", pady=4)
        _lbl_ia(row_t, "Max tokens:")
        tok_var = tk.StringVar(value=str(cfg.get("max_tokens",500)))
        ctk.CTkEntry(row_t, textvariable=tok_var, width=80,
                     height=26, font=_SMALL).pack(side="left")

        _section(t5, "COMPORTAMIENTO")
        ia_beh_card = _card(t5)
        inj_proj_var = tk.BooleanVar(value=cfg.get("inject_project_context", True))
        inj_ents_var = tk.BooleanVar(value=cfg.get("inject_entities", True))
        tk.Checkbutton(ia_beh_card, text="Inyectar contexto del proyecto activo",
                       variable=inj_proj_var,
                       command=lambda: self._save_cfg_key("inject_project_context",
                                                           inj_proj_var.get()),
                       font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                       selectcolor=UI_ACC, activebackground=UI_CARD,
                       activeforeground=UI_TEXT, bd=0, cursor="hand2",
                       ).pack(anchor="w", padx=14, pady=(10,4))
        tk.Checkbutton(ia_beh_card, text="Incluir resumen de entidades en contexto",
                       variable=inj_ents_var,
                       command=lambda: self._save_cfg_key("inject_entities",
                                                           inj_ents_var.get()),
                       font=("Segoe UI", 10), fg=UI_TEXT, bg=UI_CARD,
                       selectcolor=UI_ACC, activebackground=UI_CARD,
                       activeforeground=UI_TEXT, bd=0, cursor="hand2",
                       ).pack(anchor="w", padx=14, pady=(4,10))

        status_lbl = tk.Label(ia_card, text="", font=("Segoe UI", 9),
                              fg=UI_TEXT2, bg=UI_CARD)
        status_lbl.pack(pady=(6,2))

        def _test():
            status_lbl.configure(text="Probando…", fg=UI_TEXT2)
            win.update()
            ok = self._test_ia_connection(prov_var.get(), key_var.get().strip(), model_var.get())
            if ok:
                status_lbl.configure(text=f"✓  {ok}", fg=UI_SUCC)
            else:
                status_lbl.configure(text="✗  Error — verifica la API Key", fg=UI_ERR)

        def _save_ia():
            try: mt = int(tok_var.get())
            except ValueError: mt = 500
            self._guardar_config_ia(prov_var.get(), key_var.get().strip(),
                                    model_var.get(), mt)
            status_lbl.configure(text="✓  Guardado", fg=UI_SUCC)

        row_b = tk.Frame(ia_card, bg=UI_CARD)
        row_b.pack(fill="x", padx=14, pady=(4,14))
        ctk.CTkButton(row_b, text="Probar conexión", width=130, height=28,
                      fg_color=UI_CARD, hover_color=UI_ACC,
                      font=_SMALL, command=_test).pack(side="left", padx=(0,8))
        ctk.CTkButton(row_b, text="Guardar IA", width=100, height=28,
                      fg_color=UI_SUCC, hover_color="#15803D",
                      font=_SMALL, command=_save_ia).pack(side="left")

        # ── Tab 📊 Perf ───────────────────────────────────────────────
        t6 = _scroll_tab(tabs.tab("📊 Perf"))
        self._build_perf_tab(t6, _HEAD, _SMALL, _TINY, _card, _section, win)

        # ════════════════════════════════════════════════════════════
        # BOTONES INFERIORES
        # ════════════════════════════════════════════════════════════
        btn_bar = tk.Frame(win, bg=UI_PAN, height=48)
        btn_bar.pack(side="bottom", fill="x")
        ctk.CTkButton(btn_bar, text="Cerrar", width=100, height=32,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_SMALL,
                      command=win.withdraw).pack(side="right", padx=12, pady=8)

        # Propagar <MouseWheel> a todos los widgets hijos de cada tab scroll.
        # En tkinter los eventos NO burbujean desde widgets hijos hacia el frame padre
        # a menos que el hijo no tenga binding propio. Checkbutton y Label propagan
        # solos, pero Scale y CTkEntry consumen el evento sin pasarlo arriba.
        # Al bindear en todos los hijos con add=True, el scroll del frame funciona
        # independientemente de sobre qué widget esté el cursor.
        def _propagate_scroll(widget, fn):
            widget.bind("<MouseWheel>", fn, add=True)
            for child in widget.winfo_children():
                _propagate_scroll(child, fn)

        for _inner in (t1, t2, t3, t4, t5, t6):
            _fn = getattr(_inner, '_scroll_wheel', None)
            if _fn:
                _propagate_scroll(_inner, _fn)

        # Marcar la ventana como completamente inicializada y mostrarla.
        # Si _open_config falla antes de llegar aquí, _cfg_win_built queda False
        # y la próxima apertura detecta la ventana rota y la destruye.
        self._cfg_win_built = True
        win.deiconify()   # mostrar ahora que todo está construido — sin flash

        # Colorear barra de título — dos niveles de soporte Windows:
        #   Win 11 build 22000+ : DWMWA_CAPTION_COLOR=35 → color exacto #1E293B
        #   Win 10 20H1+        : DWMWA_USE_IMMERSIVE_DARK_MODE=20/19 → barra oscura
        # Necesita after() para que el HWND esté completamente inicializado.
        def _apply_titlebar():
            try:
                import ctypes as _ct
                # Obtener HWND real de la ventana tk
                _hwnd = win.winfo_id()
                # Intentar GetParent (útil en algunas configs de Windows)
                _parent = _ct.windll.user32.GetParent(_hwnd)
                if _parent: _hwnd = _parent

                # ── Nivel 1: color exacto (Windows 11+) ──────────────
                _h = UI_PAN.lstrip('#')
                _r, _g, _b = int(_h[0:2],16), int(_h[2:4],16), int(_h[4:6],16)
                _bgr = _r | (_g << 8) | (_b << 16)   # DWM usa BGR
                _ct.windll.dwmapi.DwmSetWindowAttribute(
                    _hwnd, 35,
                    _ct.byref(_ct.c_int(_bgr)), _ct.sizeof(_ct.c_int))

                # ── Nivel 2: dark mode (Windows 10 20H1+) ────────────
                # Attr 20 = versión oficial, attr 19 = versión pre-release
                for _attr in (20, 19):
                    _ct.windll.dwmapi.DwmSetWindowAttribute(
                        _hwnd, _attr,
                        _ct.byref(_ct.c_int(1)), _ct.sizeof(_ct.c_int))
            except Exception:
                pass
        win.after(10, _apply_titlebar)

    def _build_perf_tab(self, parent, _HEAD, _SMALL, _TINY, _card, _section, win):
        """Construye el tab 📊 Perf con barras de color auto-actualizadas."""
        import sys as _sys

        # ── Umbrales (ms) ────────────────────────────────────────────
        THRESH = {
            "gl_ms":      (20,  80,  "GL geometry"),
            "frame_ms":   (15,  60,  "GL frame"),
            "draw_ms":    (5,   30,  "GL draw calls"),
            "text_ms":    (5,   30,  "GL text"),
            "pbo_ms":     (2,   15,  "PBO readback"),
            "overlay_ms": (30,  100, "PIL overlay"),
            "total_ms":   (50,  150, "Frame total"),
            "vram_kb":    (50_000, 200_000, "VRAM bufs"),
            "undo_kb":    (100_000, 300_000, "Undo stack"),
        }
        COLOR_OK   = "#16A34A"
        COLOR_WARN = "#EAB308"
        COLOR_CRIT = "#DC2626"
        COLOR_BAR  = "#1E293B"

        def _bar_color(val, warn, crit):
            if val < warn:  return COLOR_OK
            if val < crit:  return COLOR_WARN
            return COLOR_CRIT

        # ── Sección RENDER ────────────────────────────────────────────
        _section(parent, "RENDER  (promedio 60 frames)")
        render_card = _card(parent)

        bars = {}   # key → (tk.Canvas barra, CTkLabel valor, CTkLabel estado)

        def _make_row(parent_f, key, label):
            # tk nativo — parents son tk.Frame (via _card) por lo que
            # la redirección interna de CTkFrame ya no aplica.
            row = tk.Frame(parent_f, bg=UI_CARD)
            row.pack(fill="x", padx=12, pady=(4, 0))
            lbl_head = tk.Label(row, text=label, font=("Segoe UI", 10),
                                fg="#94A3B8", bg=UI_CARD, width=14, anchor="w")
            lbl_head.pack(side="left")
            bar = tk.Canvas(row, width=180, height=12, bg=COLOR_BAR,
                            highlightthickness=0, bd=0)
            bar.create_rectangle(0, 0, 0, 12, fill=COLOR_OK, outline="", tags="bar")
            bar.pack(side="left", padx=(6, 6))
            lbl_val = tk.Label(row, text="—", font=("Segoe UI", 10),
                               fg="#CBD5E1", bg=UI_CARD, width=8, anchor="w")
            lbl_val.pack(side="left")
            lbl_st = tk.Label(row, text="", font=("Segoe UI", 9),
                              fg=COLOR_OK, bg=UI_CARD, width=3)
            lbl_st.pack(side="left")
            bars[key] = (bar, lbl_val, lbl_st)

        _make_row(render_card, "gl_ms",      "GL geometry")
        _make_row(render_card, "frame_ms",   "  ↳ frame")
        _make_row(render_card, "draw_ms",    "  ↳ draw calls")
        _make_row(render_card, "text_ms",    "  ↳ text GPU")
        _make_row(render_card, "pbo_ms",     "  ↳ PBO read")
        _make_row(render_card, "overlay_ms", "PIL overlay")
        _make_row(render_card, "total_ms",   "Frame total")

        # FPS y entidades
        fps_row = tk.Frame(render_card, bg=UI_CARD)
        fps_row.pack(fill="x", padx=12, pady=(6, 8))
        lbl_fps  = tk.Label(fps_row, text="FPS: —", font=("Segoe UI", 10),
                            fg="#CBD5E1", bg=UI_CARD)
        lbl_fps.pack(side="left", padx=(0, 20))
        lbl_ents = tk.Label(fps_row, text="Entidades: —", font=("Segoe UI", 10),
                            fg="#CBD5E1", bg=UI_CARD)
        lbl_ents.pack(side="left")
        lbl_hit  = tk.Label(fps_row, text="Cache: —", font=("Segoe UI", 10),
                            fg="#CBD5E1", bg=UI_CARD)
        lbl_hit.pack(side="left", padx=(20, 0))

        # ── Sección MEMORIA ───────────────────────────────────────────
        _section(parent, "MEMORIA")
        mem_card = _card(parent)
        _make_row(mem_card, "vram_kb",  "VRAM buffers")
        _make_row(mem_card, "undo_kb",  "Undo stack")

        # ── Botones ───────────────────────────────────────────────────
        btn_row = tk.Frame(parent, bg=UI_BG)
        btn_row.pack(fill="x", padx=12, pady=(12, 4))

        lbl_bench = tk.Label(btn_row, text="", font=("Segoe UI", 9),
                             fg="#94A3B8", bg=UI_BG)
        lbl_bench.pack(side="right", padx=8)

        def _run_benchmark():
            lbl_bench.configure(text="Midiendo 20 frames…")
            win.update()
            results = []
            for _ in range(20):
                self._redraw_static()
                win.update()
                if self._perf_ring:
                    results.append(self._perf_ring[-1].copy())
            if results:
                avg_gl  = sum(r["gl_ms"]      for r in results) / len(results)
                avg_ov  = sum(r["overlay_ms"] for r in results) / len(results)
                avg_tot = sum(r["total_ms"]   for r in results) / len(results)
                max_tot = max(r["total_ms"]   for r in results)
                report  = (f"Benchmark 20 frames\n"
                           f"GL avg:      {avg_gl:.1f} ms\n"
                           f"Overlay avg: {avg_ov:.1f} ms\n"
                           f"Total avg:   {avg_tot:.1f} ms\n"
                           f"Total max:   {max_tot:.1f} ms\n"
                           f"FPS est:     {1000/avg_tot:.0f}\n"
                           f"Entidades:   {results[-1]['n_ents']}")
                lbl_bench.configure(text=f"avg {avg_tot:.0f}ms | max {max_tot:.0f}ms")
                win.clipboard_clear()
                win.clipboard_append(report)

        ctk.CTkButton(btn_row, text="▶ Benchmark 20 frames", width=160, height=28,
                      fg_color=UI_ACC, hover_color="#1D4ED8", font=_SMALL,
                      command=_run_benchmark).pack(side="left")

        # Botón diagnóstico cache miss (abre panel debajo)
        diag_var = tk.StringVar(value="🔍 Diagnosticar cache")
        _diag_active = [False]

        def _toggle_diag():
            new = not _diag_active[0]
            _diag_active[0] = new
            rend = self._renderer
            try:
                rend._cache_diag      = new
                if new:   # reiniciar contadores al activar
                    rend._diag_miss_log   = []
                    rend._diag_miss_count = 0
                    rend._diag_frame_count= 0
                    rend._diag_last_key   = ()
            except Exception: pass
            diag_var.set("⏹ Detener diagnóstico" if new else "🔍 Diagnosticar cache")
            diag_card.pack(fill="x", padx=0, pady=(4,0)) if new else diag_card.pack_forget()
            if new: _poll_diag()

        ctk.CTkButton(btn_row, textvariable=diag_var, width=170, height=28,
                      fg_color="#7C3AED", hover_color="#6D28D9", font=_SMALL,
                      command=_toggle_diag).pack(side="left", padx=8)

        # ── Card de diagnóstico (oculta hasta activar) ────────────────
        diag_card = _card(parent)
        diag_card.pack_forget()   # empieza oculta

        # cache_key tiene exactamente 6 campos (vx0/vy0/vx1/vy1 NO están en el tuple,
        # se verifican por separado vía _vp_valid en _render_entities).
        _LABELS_6 = ("id(entities)", "len(entities)", "lod",
                     "ltscale_key", "sel_version", "vp_mode")

        # Fila de stats (misses / frames / hit%)
        stats_row = tk.Frame(diag_card, bg=UI_CARD)
        stats_row.pack(fill="x", padx=12, pady=(8, 4))
        lbl_stats = tk.Label(stats_row, text="Misses: 0  |  Frames: 0  |  Hit: —",
                             font=("Segoe UI", 10), fg="#94A3B8", bg=UI_CARD)
        lbl_stats.pack(side="left")
        ctk.CTkButton(stats_row, text="🗑 Limpiar", width=80, height=22,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_TINY,
                      command=lambda: (
                          setattr(self._renderer, '_diag_miss_log',   []),
                          setattr(self._renderer, '_diag_miss_count',  0),
                          setattr(self._renderer, '_diag_frame_count', 0),
                      )).pack(side="right")

        # Tabla: 6 campos reales del cache key
        _section(diag_card, "CACHE KEY  (último frame)")
        key_card = tk.Frame(diag_card, bg="#0F172A")
        key_card.pack(fill="x", padx=12, pady=(0, 2))
        _key_rows = {}
        for lbl in _LABELS_6:
            row = tk.Frame(key_card, bg="#0F172A")
            row.pack(fill="x", padx=8, pady=1)
            tk.Label(row, text=f"{lbl}:", font=("Courier New", 9),
                     fg="#475569", bg="#0F172A", width=16, anchor="w").pack(side="left")
            lbl_val = tk.Label(row, text="—", font=("Courier New", 9),
                               fg="#CBD5E1", bg="#0F172A", anchor="w")
            lbl_val.pack(side="left", fill="x", expand=True)
            _key_rows[lbl] = lbl_val

        # Nota: vp_mode=0 → full-scan (zoom-out total)
        #       vp_mode=1 → tile ×3 (viewport culling activo)
        # Viewport bounds se verifican por separado (no en cache_key)
        tk.Label(key_card,
                 text="  vp_mode: 0=full-scan  1=tile×3  (viewport bounds = _vp_valid separado)",
                 font=("Courier New", 9), fg="#334155", bg="#0F172A", anchor="w").pack(
                     fill="x", padx=8, pady=(0, 4))

        # Log de últimos misses
        _section(diag_card, "ÚLTIMOS MISSES  (máx 20)")
        miss_box_frame = tk.Frame(diag_card, bg="#0F172A")
        miss_box_frame.pack(fill="x", padx=12, pady=(0, 10))
        import tkinter as _tk
        miss_box = _tk.Text(miss_box_frame, height=14,
                            bg="#0F172A", fg="#94A3B8",
                            insertbackground="#94A3B8",
                            font=("Courier New", 9),
                            relief="flat", padx=6, pady=4,
                            state="disabled", wrap="none")
        miss_box.pack(fill="x", padx=0)

        # Poller que actualiza la card mientras el diagnóstico está activo
        def _poll_diag():
            if not win.winfo_exists() or not _diag_active[0]:
                return
            rend = self._renderer
            _tess = getattr(rend, '_tess_pending', False)
            n_miss  = getattr(rend, '_diag_miss_count',  0)
            n_frame = getattr(rend, '_diag_frame_count', 0)
            hit_pct = f"{100*(n_frame-n_miss)//max(1,n_frame)}%" if n_frame else "—"
            hit_col = COLOR_OK if n_miss == 0 else (COLOR_WARN if n_miss < n_frame*0.2 else COLOR_CRIT)

            # Construir texto stats (con overlay si hay)
            _stat_txt = f"Misses: {n_miss}  |  Frames: {n_frame}  |  Hit: {hit_pct}"
            ov = getattr(rend, '_diag_overlay_counts', {})
            if ov:
                ov_pairs = sorted(((t, n) for t, n in ov.items() if isinstance(n, int)),
                                  key=lambda x: -x[1])
                _stat_txt += "  PIL overlay: " + "  ".join(f"{t}:{n}" for t, n in ov_pairs)
                dt = getattr(rend, '_diag_dim_types', {})
                if dt:
                    _stat_txt += " [" + " ".join(f"{k}:{v}" for k, v in sorted(dt.items())) + "]"
            # Cache lbl_stats — tk.Label.configure(fg=) sin overhead _draw()
            if (getattr(lbl_stats, '_fl_t', None) != _stat_txt or
                    getattr(lbl_stats, '_fl_c', None) != hit_col):
                lbl_stats._fl_t = _stat_txt; lbl_stats._fl_c = hit_col
                lbl_stats.configure(text=_stat_txt, fg=hit_col)

            # Cache _key_rows — solo actualizar si cambia texto o color
            last_key = getattr(rend, '_diag_last_key', ())
            prev_key = getattr(rend, '_diag_prev_key', ())
            for i, lbl in enumerate(_LABELS_6):
                _r = _key_rows[lbl]
                if i >= len(last_key):
                    if getattr(_r, '_fl_t', None) != "":
                        _r._fl_t = ""; _r.configure(text="esperando frame…", fg="#475569")
                    continue
                val = last_key[i]
                if lbl == "id(entities)":
                    txt = f"0x{val:x}" if isinstance(val, int) else str(val)
                elif lbl == "len(entities)":
                    txt = f"{val:,}" if isinstance(val, int) else str(val)
                elif lbl == "lod":
                    txt = f"{val}  (sc ~ {2**val:.1f}×)" if isinstance(val, int) else str(val)
                elif lbl == "vp_mode":
                    txt = "1 = tile×3 (culling)" if val == 1 else "0 = full-scan"
                else:
                    txt = str(val)
                changed = (i < len(prev_key) and prev_key[i] != val)
                col = COLOR_CRIT if changed else "#CBD5E1"
                if getattr(_r, '_fl_t', None) != txt or getattr(_r, '_fl_c', None) != col:
                    _r._fl_t = txt; _r._fl_c = col
                    _r.configure(text=txt, fg=col)

            # Textbox: canvas.insert bloquea bajo GIL contención — saltar durante tessellation
            if not _tess:
                miss_log = getattr(rend, '_diag_miss_log', [])
                miss_box.configure(state="normal")
                miss_box.delete("1.0", "end")
                if miss_log:
                    for ts, reason in miss_log[-20:]:
                        miss_box.insert("end", f"[{ts}]  {reason}\n")
                else:
                    miss_box.insert("end", "Sin misses recientes — cache 100% ✅\n")
                dfl = getattr(rend, '_dim_fail_log', {})
                if dfl:
                    miss_box.insert("end", "\nDIM TESS FAILURES:\n")
                    for k, cnt in sorted(dfl.items(), key=lambda x: -x[1])[:15]:
                        miss_box.insert("end", f"  ×{cnt:4d}  {k}\n")
                ifl = getattr(rend, '_ins_fail_log', {})
                if ifl:
                    miss_box.insert("end", "\nINSERT TESS FAILURES:\n")
                    for k, cnt in sorted(ifl.items(), key=lambda x: -x[1])[:15]:
                        miss_box.insert("end", f"  ×{cnt:4d}  {k}\n")
                otm = getattr(rend, '_diag_overlay_timing', {})
                if otm:
                    total_t = sum(otm.values())
                    miss_box.insert("end", f"\nOVERLAY TIMING (total {total_t:.1f}ms):\n")
                    for typ, ms in sorted(otm.items(), key=lambda x: -x[1]):
                        pct = ms / total_t * 100 if total_t > 0 else 0
                        miss_box.insert("end", f"  {typ:<12} {ms:6.1f}ms  {pct:4.0f}%\n")
                miss_box.configure(state="disabled")

            win.after(400, _poll_diag)   # actualizar 2.5× por segundo

        ctk.CTkButton(btn_row, text="📋 Copiar", width=80, height=28,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_SMALL,
                      command=lambda: (win.clipboard_clear(),
                                       win.clipboard_append(self._perf_report())
                                       )).pack(side="left", padx=8)

        # ── Sección GL ERRORS ──────────────────────────────────────────
        # IMPORTANTE: los widgets deben crearse ANTES de definir _poll_gl_errors
        # para que las cell variables de Python 3.14 estén inicializadas.
        _section(parent, "GL ERRORS")
        gl_err_card = _card(parent, pady=(0, 6))

        gl_err_hdr = tk.Frame(gl_err_card, bg=UI_CARD)
        gl_err_hdr.pack(fill="x", padx=12, pady=(6, 2))

        lbl_gl_err_count = tk.Label(gl_err_hdr,
                                    text="Sin errores GL  ✅",
                                    font=("Segoe UI", 10),
                                    fg=COLOR_OK, bg=UI_CARD, anchor="w")
        lbl_gl_err_count.pack(side="left")

        lbl_worker_status = tk.Label(gl_err_hdr,
                                     text="worker: activo",
                                     font=("Segoe UI", 9),
                                     fg="#475569", bg=UI_CARD, anchor="e")
        lbl_worker_status.pack(side="right", padx=(8, 0))

        ctk.CTkButton(gl_err_hdr, text="🗑 Limpiar", width=80, height=22,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_TINY,
                      command=lambda: (
                          setattr(self._renderer, '_gl_error_log', []),
                      )).pack(side="right")

        # Textbox de errores
        gl_err_frame = tk.Frame(gl_err_card, bg="#0F172A")
        gl_err_frame.pack(fill="x", padx=12, pady=(0, 8))
        import tkinter as _tk2
        gl_err_box = _tk2.Text(gl_err_frame, height=10,
                               bg="#0F172A", fg="#F87171",
                               insertbackground="#F87171",
                               font=("Courier New", 8),
                               relief="flat", padx=6, pady=4,
                               state="disabled", wrap="none")
        gl_err_sb = tk.Scrollbar(gl_err_frame, orient="vertical",
                                 command=gl_err_box.yview)
        gl_err_box.configure(yscrollcommand=gl_err_sb.set)
        gl_err_sb.pack(side="right", fill="y")
        gl_err_box.pack(side="left", fill="x", expand=True)

        # Insertar texto inicial
        gl_err_box.configure(state="normal")
        gl_err_box.insert("end", "Sin errores GL registrados\n")
        gl_err_box.configure(state="disabled")

        # Poller — definido DESPUÉS de los widgets para evitar NameError en Python 3.14
        def _poll_gl_errors():
            if not win.winfo_exists():
                return
            rend = self._renderer
            err_log = getattr(rend, '_gl_error_log', [])
            alive   = getattr(rend, '_gl_worker_alive', True)

            n = len(err_log)
            if n == 0 and alive:
                _txt = "Sin errores GL  ✅";        _col = COLOR_OK
            elif n == 0 and not alive:
                _txt = "⚠ GL worker detenido";     _col = COLOR_CRIT
            else:
                _txt = f"⚠ {n} error{'es' if n>1 else ''} GL"; _col = COLOR_CRIT

            if getattr(lbl_gl_err_count, '_fl_t', None) != _txt:
                lbl_gl_err_count._fl_t = _txt
                lbl_gl_err_count.configure(text=_txt, fg=_col)

            _ws = "worker: activo  🟢" if alive else "worker: MUERTO  🔴"
            _wc = "#475569" if alive else COLOR_CRIT
            if getattr(lbl_worker_status, '_fl_t', None) != _ws:
                lbl_worker_status._fl_t = _ws
                lbl_worker_status.configure(text=_ws, fg=_wc)

            _log_id = (id(err_log), len(err_log))
            if getattr(gl_err_box, '_fl_log_id', None) != _log_id:
                gl_err_box._fl_log_id = _log_id
                gl_err_box.configure(state="normal")
                gl_err_box.delete("1.0", "end")
                if err_log:
                    for ts, etype, msg, tb in err_log:
                        gl_err_box.insert("end",
                            f"[{ts}] {etype}: {msg}\n{tb}\n{'─'*60}\n")
                else:
                    gl_err_box.insert("end",
                        "Sin errores GL registrados\n" if alive
                        else "GL worker detenido — reiniciar la aplicación\n")
                gl_err_box.see("end")
                gl_err_box.configure(state="disabled")

            win.after(600, _poll_gl_errors)

        _poll_gl_errors()

        # ── Sección BENCHMARK DE CAPACIDAD ────────────────────────────
        _section(parent, "BENCHMARK DE CAPACIDAD")
        bench_card = _card(parent)

        # Fila selección de N
        n_row = tk.Frame(bench_card, bg=UI_CARD)
        n_row.pack(fill="x", padx=12, pady=(8, 4))
        tk.Label(n_row, text="Simular:", font=("Segoe UI", 10),
                 fg="#94A3B8", bg=UI_CARD).pack(side="left", padx=(0, 8))

        _bench_n = tk.IntVar(value=600_000)
        _PRESETS = [("18k", 18_000), ("100k", 100_000),
                    ("300k", 300_000), ("600k", 600_000)]
        _n_btns = {}
        def _sel_n(val):
            _bench_n.set(val)
            for v, b in _n_btns.items():
                b.configure(fg_color=UI_ACC if v == val else UI_CARD,
                             hover_color="#1D4ED8" if v == val else UI_BORD)
        for label, val in _PRESETS:
            b = ctk.CTkButton(n_row, text=label, width=54, height=26,
                              fg_color=UI_ACC if val == 600_000 else UI_CARD,
                              hover_color="#1D4ED8" if val == 600_000 else UI_BORD,
                              font=_SMALL,
                              command=lambda v=val: _sel_n(v))
            b.pack(side="left", padx=3)
            _n_btns[val] = b

        # Fila botón + progreso
        run_row = tk.Frame(bench_card, bg=UI_CARD)
        run_row.pack(fill="x", padx=12, pady=(4, 6))
        lbl_prog = tk.Label(run_row, text="", font=("Segoe UI", 9),
                            fg="#94A3B8", bg=UI_CARD)
        lbl_prog.pack(side="right", padx=4)

        # Tabla de resultados (empieza vacía)
        tbl_frame = tk.Frame(bench_card, bg="#0F172A")
        tbl_frame.pack(fill="x", padx=12, pady=(0, 4))

        # Cabecera de tabla
        hdr = tk.Frame(tbl_frame, bg="#0F172A")
        hdr.pack(fill="x", padx=8, pady=(6, 2))
        for col, w in [("Escenario", 160), ("Tessellation", 110), ("FPS est.", 80)]:
            tk.Label(hdr, text=col, font=("Courier New", 9),
                     fg="#475569", bg="#0F172A",
                     width=w//8, anchor="w").pack(side="left")

        sep_tbl = tk.Frame(tbl_frame, height=1, bg="#1E293B")
        sep_tbl.pack(fill="x", padx=8)

        # Filas de resultado (3 escenarios)
        _result_rows = {}
        for key, scenario in [("full",  "Full-scan (ZE)"),
                               ("tile",  "Tile ×3  (trabajo)"),
                               ("cache", "Cache hit (pan)")]:
            rw = tk.Frame(tbl_frame, bg="#0F172A")
            rw.pack(fill="x", padx=8, pady=1)
            lbl_sc  = tk.Label(rw, text=scenario, font=("Courier New", 9),
                               fg="#94A3B8", bg="#0F172A", width=20, anchor="w")
            lbl_sc.pack(side="left")
            lbl_ms  = tk.Label(rw, text="—", font=("Courier New", 9),
                               fg="#CBD5E1", bg="#0F172A", width=14, anchor="w")
            lbl_ms.pack(side="left")
            lbl_fps = tk.Label(rw, text="—", font=("Courier New", 9),
                               fg="#CBD5E1", bg="#0F172A", width=10, anchor="w")
            lbl_fps.pack(side="left")
            _result_rows[key] = (lbl_ms, lbl_fps)

        # Fila culling
        sep_tbl2 = tk.Frame(tbl_frame, height=1, bg="#1E293B")
        sep_tbl2.pack(fill="x", padx=8, pady=(4, 0))
        cull_row = tk.Frame(tbl_frame, bg="#0F172A")
        cull_row.pack(fill="x", padx=8, pady=(2, 6))
        lbl_cull = tk.Label(cull_row, text="Culling query: —",
                            font=("Courier New", 9), fg="#94A3B8", bg="#0F172A", anchor="w")
        lbl_cull.pack(side="left")

        # Botón copiar reporte de capacidad
        copy_row = tk.Frame(bench_card, bg=UI_CARD)
        copy_row.pack(fill="x", padx=12, pady=(0, 8))
        _bench_report = [""]   # mutable container

        def _copy_bench():
            if not _bench_report[0]:
                win.clipboard_clear()
                win.clipboard_append("(benchmark en progreso — espere a que termine)")
                return
            win.clipboard_clear()
            win.clipboard_append(_bench_report[0])
        btn_copy_report = ctk.CTkButton(copy_row, text="📋 Copiar reporte", width=140, height=26,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_SMALL,
                      command=_copy_bench)
        btn_copy_report.pack(side="left")

        # ── Lógica del benchmark ──────────────────────────────────────
        _bench_running = [False]

        _baseline_ms = [0.0]   # snapshot del frame time estable ANTES del benchmark

        def _run_capacity_bench():
            if _bench_running[0]:
                return
            # Capturar frame time estable antes de que el benchmark contamine _perf_stats
            _ps_now = getattr(self._renderer, '_perf_stats', {})
            _baseline_ms[0] = _ps_now.get('total_ms', 0.0)
            _bench_running[0] = True
            n_total = _bench_n.get()
            btn_bench.configure(state="disabled", text="⏳ Midiendo…")
            btn_copy_report.configure(state="disabled")
            for lbl_ms, lbl_fps in _result_rows.values():
                lbl_ms.configure(text="—",  fg="#CBD5E1")
                lbl_fps.configure(text="—", fg="#CBD5E1")
            lbl_cull.configure(text="Culling query: —")
            lbl_prog.configure(text="Iniciando…")
            _bench_report[0] = ""

            import threading as _thr
            import queue as _q
            import math as _m
            import statistics as _st

            result_q = _q.Queue()

            def _worker():
                import sys, os
                sys.path.insert(0, os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))))
                try:
                    from cad.renderer_opengl import RendererOpenGL
                    from cad.renderer_pil import (build_layer_cache,
                                                   _query_viewport, _entity_aabb)
                    from cad.entities import (Line, Polyline, Circle, Arc,
                                               Text, Dimension, Hatch, Layer)
                    import queue as _qq, random as _rng_mod, time as _t

                    rng = _rng_mod.Random(42)
                    WORLD = 200.0
                    MIX   = {"Line":0.62,"Polyline":0.12,"Circle":0.06,
                              "Arc":0.08,"Hatch":0.05,"Text":0.04,
                              "Dimension":0.02,"Insert":0.01}
                    N_LAY = 50
                    lay_names = [f"A-{i:02d}" for i in range(N_LAY)]
                    layers = {n: Layer(n,"#FFFFFF",1) for n in lay_names}
                    layers["0"] = Layer("0","#FFFFFF",1)
                    lay_cyc = lay_names * (n_total // N_LAY + 2)
                    li = [0]
                    def _lay():
                        l = lay_cyc[li[0] % len(lay_cyc)]; li[0] += 1; return l

                    result_q.put(("prog", f"Generando {n_total:,} entidades…"))
                    ents = []
                    counts = {t: max(1, int(n_total * f)) for t, f in MIX.items()}
                    counts["Line"] += n_total - sum(counts.values())
                    for _ in range(counts["Line"]):
                        x = rng.uniform(0,WORLD); y = rng.uniform(0,WORLD)
                        ents.append(Line(x1=x,y1=y,
                                         x2=x+rng.gauss(0,3),y2=y+rng.gauss(0,3),
                                         layer=_lay()))
                    for _ in range(counts["Polyline"]):
                        cx=rng.uniform(0,WORLD); cy=rng.uniform(0,WORLD)
                        pts=[(cx+rng.gauss(0,2),cy+rng.gauss(0,2))
                             for _ in range(rng.randint(4,12))]
                        ents.append(Polyline(points=pts,closed=rng.random()>0.5,
                                              layer=_lay()))
                    for _ in range(counts["Circle"]):
                        ents.append(Circle(cx=rng.uniform(0,WORLD),
                                            cy=rng.uniform(0,WORLD),
                                            radius=rng.uniform(0.1,2),layer=_lay()))
                    for _ in range(counts["Arc"]):
                        ents.append(Arc(cx=rng.uniform(0,WORLD),
                                         cy=rng.uniform(0,WORLD),
                                         radius=rng.uniform(0.1,2),
                                         start_ang=rng.uniform(0,360),
                                         end_ang=rng.uniform(0,360),
                                         ccw=True,layer=_lay()))
                    for _ in range(counts["Hatch"]):
                        cx=rng.uniform(0,WORLD); cy=rng.uniform(0,WORLD)
                        w=rng.uniform(1,6); h=rng.uniform(1,6)
                        ents.append(Hatch(boundary=[(cx,cy),(cx+w,cy),
                                                     (cx+w,cy+h),(cx,cy+h)],
                                           pattern="SOLID",layer=_lay()))
                    for i in range(counts["Text"]):
                        ents.append(Text(x=rng.uniform(0,WORLD),
                                          y=rng.uniform(0,WORLD),
                                          content=f"T{i}",height=0.20,
                                          layer=_lay()))
                    for _ in range(counts["Dimension"]):
                        x1=rng.uniform(0,WORLD); y1=rng.uniform(0,WORLD)
                        ents.append(Dimension(p1=(x1,y1),p2=(x1+rng.uniform(1,8),y1),
                                               pos=(x1,y1-1),dim_type="H",
                                               layer=_lay()))
                    rng.shuffle(ents)

                    result_q.put(("prog", f"Construyendo índice ({len(ents):,} ents)…"))
                    cell = 5.0
                    idx  = {}
                    aabbs = []
                    flat  = []
                    for e in ents:
                        try:
                            x0,y0,x1,y1 = _entity_aabb(e,{})
                            if not all(_m.isfinite(v) for v in (x0,y0,x1,y1)):
                                continue
                            aabbs.append((x0,y0,x1,y1)); flat.append(e)
                        except Exception: continue
                    for e,(x0,y0,x1,y1) in zip(flat,aabbs):
                        cx0=int(x0//cell); cy0=int(y0//cell)
                        cx1=int(x1//cell); cy1=int(y1//cell)
                        if (cx1-cx0+1)*(cy1-cy0+1)>500: continue
                        for cx in range(cx0,cx1+1):
                            for cy in range(cy0,cy1+1):
                                idx.setdefault((cx,cy),[]).append(e)
                    idx["__aabb__"]       = aabbs
                    idx["__flat__"]       = flat
                    idx["__total_cells__"] = sum(1 for k in idx if isinstance(k, tuple))

                    lyr_cache = build_layer_cache(layers, scale=1.0)

                    # Renderer mock
                    rend = object.__new__(RendererOpenGL)
                    rend._tess_result_q = _qq.Queue(maxsize=2)
                    rend._tess_progress = ""
                    rend._font_atlas    = None
                    rend._tess_done_cb  = None

                    import threading as _thr2
                    ev = _thr2.Event()

                    class _Ctx:
                        pass
                    ctx = _Ctx()
                    ctx.entities=ents; ctx.layers=layers; ctx.block_defs={}
                    ctx.entity_index=idx; ctx.entity_cell=cell
                    ctx.config={}; ctx.select_color="#FFD700"; ctx.sel_version=0
                    ctx.cancel_ev=ev; ctx.bg_color="#000"; ctx.dim_text_queue=[]

                    sc_full = 1920/WORLD
                    sc_work = 1920/(WORLD*0.10)
                    # Full-scan puede tardar 5-7s en 600k → 1 run basta (es estable).
                    # Tile es rápido (~90ms) → 2 runs para promedio más confiable.
                    RUNS_FULL = 1 if n_total >= 200_000 else 3
                    RUNS_TILE = 2 if n_total >= 200_000 else 3

                    # ─ Culling query ──────────────────────────────────
                    vw=1920/sc_work; vh=1080/sc_work
                    vx0e=-vw*1.5; vx1e=vw*1.5
                    vy0e=-vh*1.5; vy1e=vh*1.5
                    t0=_t.perf_counter()
                    for _ in range(20):
                        cands=_query_viewport(idx,cell,vx0e,vy0e,vx1e,vy1e)
                    cull_ms=(_t.perf_counter()-t0)*1000/20
                    n_vis=len(cands) if cands else 0
                    result_q.put(("cull", cull_ms, n_vis))

                    # ─ Full-scan ───────────────────────────────────────
                    result_q.put(("prog", "Full-scan (zoom total)…"))
                    ctx.scale=sc_full; ctx.offset_x=960; ctx.offset_y=540
                    ctx.W=1920; ctx.H=1080
                    times_full=[]
                    for i in range(RUNS_FULL):
                        t0=_t.perf_counter()
                        def _wk(r=i):
                            rend._tessellate_and_enqueue(
                                list(ents),{},"bench_f",sc_full,
                                lyr_cache,1.0,"#FFD700",ev,ctx,None)
                        th=_thr2.Thread(target=_wk,daemon=True); th.start()
                        th.join(timeout=120)
                        times_full.append((_t.perf_counter()-t0)*1000)
                        try: rend._tess_result_q.get_nowait()
                        except Exception: pass
                    avg_full=_st.mean(times_full)
                    fps_full=1000/max(1,avg_full+10)
                    result_q.put(("row","full",avg_full,fps_full))

                    # ─ Tile ×3 ─────────────────────────────────────────
                    result_q.put(("prog", "Tile ×3 (zoom trabajo)…"))
                    ctx.scale=sc_work; ctx.offset_x=960; ctx.offset_y=540
                    tile_cands=_query_viewport(idx,cell,vx0e,vy0e,vx1e,vy1e) or list(ents)
                    tb=(vx0e,vy0e,vx1e,vy1e)
                    times_tile=[]
                    for i in range(RUNS_TILE):
                        t0=_t.perf_counter()
                        def _wk2(r=i):
                            rend._tessellate_and_enqueue(
                                tile_cands,{},"bench_t",sc_work,
                                lyr_cache,1.0,"#FFD700",ev,ctx,tb)
                        th=_thr2.Thread(target=_wk2,daemon=True); th.start()
                        th.join(timeout=60)
                        times_tile.append((_t.perf_counter()-t0)*1000)
                        try: rend._tess_result_q.get_nowait()
                        except Exception: pass
                    avg_tile=_st.mean(times_tile)
                    fps_tile=1000/max(1,avg_tile+10)
                    result_q.put(("row","tile",avg_tile,fps_tile))
                    result_q.put(("done",
                                   n_total, avg_full, fps_full,
                                   avg_tile, fps_tile,
                                   cull_ms, n_vis))

                except Exception as exc:
                    result_q.put(("error", str(exc)))

            _thr.Thread(target=_worker, daemon=True).start()

            def _poll_results():
                if not win.winfo_exists():
                    return
                while not result_q.empty():
                    msg = result_q.get_nowait()
                    kind = msg[0]
                    if kind == "prog":
                        lbl_prog.configure(text=msg[1])
                    elif kind == "cull":
                        _, ms, nv = msg
                        lbl_cull.configure(
                            text=f"Culling query: {ms:.1f}ms  —  {nv:,} ents visibles")
                    elif kind == "row":
                        _, key, ms, fps = msg
                        lbl_ms, lbl_fps = _result_rows[key]
                        c_ms  = COLOR_OK if ms < 200 else (COLOR_WARN if ms < 2000 else COLOR_CRIT)
                        c_fps = COLOR_OK if fps > 30  else (COLOR_WARN if fps > 10  else COLOR_CRIT)
                        lbl_ms.configure(text=f"{ms:,.0f} ms", fg=c_ms)
                        lbl_fps.configure(text=f"{fps:.1f} fps", fg=c_fps)
                    elif kind == "done":
                        _, n, f_ms, f_fps, t_ms, t_fps, c_ms, n_vis = msg
                        # Cache hit: FPS real basado en perf_stats del renderer
                        _ch_ms = _baseline_ms[0]   # frame time capturado antes del benchmark
                        _ch_fps = round(1000 / _ch_ms) if _ch_ms > 1 else 60
                        lbl_ms2, lbl_fps2 = _result_rows["cache"]
                        _c_fps_col = COLOR_OK if _ch_fps >= 30 else (COLOR_WARN if _ch_fps >= 10 else COLOR_CRIT)
                        lbl_ms2.configure(text=f"~{_ch_ms:.0f} ms", fg=COLOR_OK)
                        lbl_fps2.configure(text=f"~{_ch_fps} fps", fg=_c_fps_col)
                        lbl_prog.configure(text="✅ Completado")
                        btn_bench.configure(state="normal", text="▶ Medir capacidad")
                        btn_copy_report.configure(state="normal")
                        _bench_running[0] = False
                        _bench_report[0] = (
                            f"BENCHMARK DE CAPACIDAD — {n:,} entidades\n"
                            f"{'─'*44}\n"
                            f"Full-scan (ZE)    : {f_ms:>8,.0f} ms  {f_fps:.1f} fps\n"
                            f"Tile ×3 (trabajo) : {t_ms:>8,.0f} ms  {t_fps:.1f} fps\n"
                            f"Cache hit (pan)   : {_ch_ms:>8.0f} ms  ~{_ch_fps} fps\n"
                            f"Culling query     : {c_ms:>7.1f} ms  {n_vis:,} ents vis\n"
                        )
                        return
                    elif kind == "error":
                        lbl_prog.configure(text=f"❌ {msg[1][:60]}")
                        btn_bench.configure(state="normal", text="▶ Medir capacidad")
                        _bench_running[0] = False
                        return
                win.after(200, _poll_results)

            win.after(200, _poll_results)

        btn_bench = ctk.CTkButton(run_row, text="▶ Medir capacidad",
                                   width=150, height=28,
                                   fg_color=UI_ACC, hover_color="#1D4ED8",
                                   font=_SMALL, command=_run_capacity_bench)
        btn_bench.pack(side="left")
        tk.Label(run_row, text="(corre en background — UI responde)",
                 font=("Segoe UI", 9), fg="#475569", bg=UI_CARD,
                 ).pack(side="left", padx=8)

        # ── Auto-refresh cada 1 segundo ───────────────────────────────
        _refresh_id = [None]

        def _refresh():
            if not win.winfo_exists():
                return
            ring = self._perf_ring
            if not ring:
                _refresh_id[0] = win.after(1000, _refresh)
                return

            # Promediar últimos N frames
            N    = min(60, len(ring))
            last = ring[-N:]

            def _avg(key):
                return sum(r.get(key, 0.0) for r in last) / N

            def _update_bar(key, val, unit="ms", scale=150):
                if key not in bars:
                    return
                bar, lbl_val, lbl_st = bars[key]
                warn, crit, _ = THRESH.get(key, (50, 150, ""))
                frac = min(1.0, val / crit) if crit > 0 else 0
                color = _bar_color(val, warn, crit)
                # bar es tk.Canvas — coords/itemconfig son nativos, sin overhead _draw()
                bar.coords("bar", 0, 0, int(frac * 180), 12)
                bar.itemconfig("bar", fill=color)
                _vtxt = f"{val:.0f} ms" if unit == "ms" else f"{val/1024:.0f} MB"
                if getattr(lbl_val, '_fl_val', None) != _vtxt:
                    lbl_val._fl_val = _vtxt
                    lbl_val.configure(text=_vtxt)
                icon = "✅" if val < warn else ("⚠️" if val < crit else "🔴")
                if getattr(lbl_st, '_fl_text', None) != icon:
                    lbl_st._fl_text = icon
                    lbl_st.configure(text=icon, fg=color)

            _update_bar("gl_ms",      _avg("gl_ms"))
            _update_bar("frame_ms",   _avg("frame_ms"))
            _update_bar("draw_ms",    _avg("draw_ms"))
            _update_bar("text_ms",    _avg("text_ms"))
            _update_bar("pbo_ms",     _avg("pbo_ms"))
            _update_bar("overlay_ms", _avg("overlay_ms"))
            _update_bar("total_ms",   _avg("total_ms"))
            _update_bar("vram_kb",    _avg("vram_kb"),  unit="kb")
            _update_bar("undo_kb",    _avg("undo_kb"),  unit="kb")

            fps_val  = _avg("fps")
            ents_val = int(last[-1]["n_ents"])
            hits     = sum(1 for r in last if r["cache_hit"])
            hit_pct  = hits * 100 // N

            fps_color = COLOR_OK if fps_val > 30 else (COLOR_WARN if fps_val > 15 else COLOR_CRIT)
            lbl_fps.configure(text=f"FPS: {fps_val:.0f}", fg=fps_color)
            lbl_ents.configure(text=f"Entidades: {ents_val:,}")
            hit_col = COLOR_OK if hit_pct > 80 else COLOR_WARN
            lbl_hit.configure(text=f"Cache hit: {hit_pct}%", fg=hit_col)

            _refresh_id[0] = win.after(1000, _refresh)

        _refresh()

        # ── Sección FREEZE DETECTOR ───────────────────────────────────────
        _section(parent, "FREEZE DETECTOR")
        fd_card = _card(parent)

        fd_hdr = tk.Frame(fd_card, bg=UI_CARD)
        fd_hdr.pack(fill="x", padx=12, pady=(6, 2))
        tk.Label(fd_hdr, text="● Activo — umbral 500ms",
                 font=("Segoe UI", 10), fg=COLOR_OK, bg=UI_CARD).pack(side="left")
        fd_cnt_lbl = tk.Label(fd_hdr, text="", font=("Segoe UI", 9),
                              fg="#94A3B8", bg=UI_CARD)
        fd_cnt_lbl.pack(side="right")

        fd_box_fr = tk.Frame(fd_card, bg="#0F172A")
        fd_box_fr.pack(fill="x", padx=12, pady=(2, 4))
        fd_box = tk.Text(fd_box_fr, height=12, font=_TINY,
                         bg="#0F172A", fg="#94A3B8",
                         wrap="none", state="disabled",
                         relief="flat", bd=0, highlightthickness=0,
                         insertbackground="#94A3B8")
        fd_box.pack(fill="x", padx=4, pady=4)

        def _fd_refresh():
            # Guard: el widget puede estar destruido si _cfg_win fue recreado
            # después de un fallo. Sin este check, cada freeze genera un
            # TclError que llena el event loop y congela el GL worker.
            try:
                if not fd_cnt_lbl.winfo_exists():
                    return
            except Exception:
                return
            with self._freeze_lock:
                evs = list(self._freeze_events)
            fd_cnt_lbl.configure(text=f"{len(evs)} eventos")
            fd_box.configure(state="normal")
            fd_box.delete("1.0", "end")
            if not evs:
                fd_box.insert("end", "Sin freezes detectados aún.\n")
            else:
                for ev in reversed(evs[-15:]):
                    stack_top = ev['stack'][-1] if ev['stack'] else '?'
                    tess = "⏳tess" if ev['tess_pending'] else "✓tess"
                    fd_box.insert("end",
                        f"[{ev['ts']}]  {ev['frozen_ms']}ms  "
                        f"ents={ev['ents']}  sc={ev['sc']}  {tess}  glq={ev['gl_queue']}\n"
                        f"  op: {ev['last_op'] or '?'}\n"
                        f"  → {stack_top}\n\n")
            fd_box.configure(state="disabled")

        self._refresh_freeze_panel = _fd_refresh
        _fd_refresh()

        def _fd_copy():
            with self._freeze_lock:
                evs = list(self._freeze_events)
            if not evs:
                win.clipboard_clear(); win.clipboard_append("Sin freezes registrados.")
                return
            lines = [f"FREEZE LOG — Estudio Merlos AI CAD\n"
                     f"{len(evs)} eventos  |  "
                     f"Archivo: {self._freeze_log_path}\n{'='*60}"]
            for ev in evs:
                lines.append(f"\n[{ev['ts']}]  FREEZE {ev['frozen_ms']}ms")
                lines.append(f"  ents={ev['ents']}  sc={ev['sc']}  "
                             f"tess_pending={ev['tess_pending']}  gl_queue={ev['gl_queue']}")
                lines.append(f"  last_op: {ev['last_op'] or '?'}")
                lines.append("  STACK:")
                for fr in ev['stack']:
                    lines.append(f"    {fr}")
            win.clipboard_clear()
            win.clipboard_append('\n'.join(lines))

        def _fd_clear():
            with self._freeze_lock:
                self._freeze_events.clear()
            try:
                open(self._freeze_log_path, 'w').close()
            except Exception:
                pass
            _fd_refresh()

        fd_btn = tk.Frame(fd_card, bg=UI_CARD)
        fd_btn.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkButton(fd_btn, text="📋 Copiar log completo", width=155, height=26,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_SMALL,
                      command=_fd_copy).pack(side="left", padx=(0, 6))
        ctk.CTkButton(fd_btn, text="🗑 Limpiar", width=80, height=26,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_SMALL,
                      command=_fd_clear).pack(side="left")

        # ════════════════════════════════════════════════════════════
        # 🏥 HEALTH MONITOR — detector unificado de problemas
        # ════════════════════════════════════════════════════════════
        _section(parent, "🏥 HEALTH MONITOR")
        hm_card = _card(parent, pady=(0, 6))

        # ── Header: badge global + botón Actualizar ───────────────
        hm_hdr = tk.Frame(hm_card, bg=UI_CARD)
        hm_hdr.pack(fill="x", padx=12, pady=(6, 2))

        lbl_hm_badge = tk.Label(hm_hdr, text="⏳ Iniciando…",
                                font=("Segoe UI", 10, "bold"),
                                fg="#94A3B8", bg=UI_CARD, anchor="w")
        lbl_hm_badge.pack(side="left")

        lbl_hm_ts = tk.Label(hm_hdr, text="",
                             font=("Segoe UI", 8),
                             fg="#475569", bg=UI_CARD, anchor="e")
        lbl_hm_ts.pack(side="right", padx=(8, 0))

        _hm_last = [None]   # [lista de checks] — actualizado por _hm_run

        def _hm_copy():
            import time as _t_cp
            data = _hm_last[0]
            if not data:
                return
            lines = [f"🏥 Health Monitor — {_t_cp.strftime('%Y-%m-%d %H:%M:%S')}",
                     "=" * 55]
            for icon, cat, msg, _ in data:
                lines.append(f"{icon}  {cat:<14}  {msg}")
            text = "\n".join(lines)
            try:
                win.clipboard_clear()
                win.clipboard_append(text)
            except Exception:
                pass

        ctk.CTkButton(hm_hdr, text="📋 Copiar", width=80, height=22,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_TINY,
                      command=_hm_copy).pack(side="right", padx=(0, 4))

        # ── Textbox ───────────────────────────────────────────────
        hm_frame = tk.Frame(hm_card, bg="#0A0F1E")
        hm_frame.pack(fill="x", padx=12, pady=(2, 8))
        import tkinter as _tk_hm
        hm_box = _tk_hm.Text(hm_frame, height=12,
                             bg="#0A0F1E", fg="#CBD5E1",
                             font=("Courier New", 9),
                             relief="flat", padx=8, pady=4,
                             state="disabled", wrap="none")
        hm_sb = tk.Scrollbar(hm_frame, orient="vertical", command=hm_box.yview)
        hm_box.configure(yscrollcommand=hm_sb.set)
        hm_sb.pack(side="right", fill="y")
        hm_box.pack(side="left", fill="x", expand=True)

        # Colores por severidad
        hm_box.tag_configure("ok",   foreground="#4ADE80")
        hm_box.tag_configure("warn", foreground="#FCD34D")
        hm_box.tag_configure("crit", foreground="#F87171")
        hm_box.tag_configure("gray", foreground="#475569")
        hm_box.tag_configure("head", foreground="#94A3B8",
                             font=("Courier New", 9, "bold"))
        hm_box.tag_configure("sep",  foreground="#1E293B")

        def _hm_run():
            """Ejecuta los checks y actualiza el display. Siempre seguro."""
            import sys as _sys, time as _t_hm

            rend     = self._renderer
            checks   = []   # [(icon, cat_14chr, msg, tag)]

            # ── 1. GL Worker ──────────────────────────────────────
            alive       = getattr(rend, '_gl_worker_alive', True)
            err_log     = getattr(rend, '_gl_error_log', [])
            n_timeout   = getattr(rend, '_gl_timeout_count', 0)
            n_renders   = getattr(rend, '_gl_render_count', 1) or 1
            timeout_pct = n_timeout / n_renders * 100
            if not alive:
                checks.append(("🔴", "GL Worker",
                                "Thread MUERTO — reiniciar aplicación", "crit"))
            elif err_log:
                checks.append(("🟡", "GL Worker",
                                f"{len(err_log)} error(es) — ver GL ERRORS", "warn"))
            elif n_timeout > 0:
                checks.append(("🟡", "GL Worker",
                                f"{n_timeout} timeout(s) ({timeout_pct:.1f}% renders)", "warn"))
            else:
                checks.append(("🟢", "GL Worker",
                                f"Activo — {n_renders} renders, 0 errores", "ok"))

            # ── 2. Render performance ─────────────────────────────
            ring = self._perf_ring
            if ring:
                N    = min(30, len(ring))
                last = ring[-N:]
                avg_tot = sum(r["total_ms"]   for r in last) / N
                avg_ov  = sum(r["overlay_ms"] for r in last) / N
                avg_gl  = sum(r["gl_ms"]      for r in last) / N
                hit_pct = sum(1 for r in last if r.get("cache_hit")) / N * 100
                fps     = 1000.0 / avg_tot if avg_tot > 0 else 0
                msg = (f"total={avg_tot:.0f}ms  gl={avg_gl:.0f}ms  "
                       f"overlay={avg_ov:.0f}ms  hit={hit_pct:.0f}%  ~{fps:.0f}fps")
                tag = "ok" if avg_tot < 80 else ("warn" if avg_tot < 200 else "crit")
                icon = "🟢" if tag == "ok" else ("🟡" if tag == "warn" else "🔴")
                checks.append((icon, "Render", msg, tag))
            else:
                checks.append(("⚪", "Render", "Sin datos aún", "gray"))

            # ── 3. Tessellator ────────────────────────────────────
            dim_fail  = getattr(rend, '_dim_fail_log', {})
            ins_fail  = getattr(rend, '_ins_fail_log', {})
            tess_pend = getattr(rend, '_tess_pending', False)
            n_df = sum(dim_fail.values())
            n_if = sum(ins_fail.values())
            msgs_t = []
            if n_df:  msgs_t.append(f"{n_df} cotas→PIL")
            if n_if:  msgs_t.append(f"{n_if} inserts→PIL")
            if tess_pend: msgs_t.append("tess_pending")
            if msgs_t:
                tag = "crit" if tess_pend else "warn"
                checks.append(("🔴" if tess_pend else "🟡", "Tessellator",
                                "  ".join(msgs_t), tag))
            else:
                checks.append(("🟢", "Tessellator", "Sin fallos GPU", "ok"))

            # ── 4. LOD (oscilación) ───────────────────────────────
            miss_log = getattr(rend, '_diag_miss_log', [])
            if miss_log:
                lod_m = sum(1 for _, r in miss_log[-20:] if "lod" in r.lower())
                if   lod_m >= 8: checks.append(("🔴","LOD", f"{lod_m}/20 misses — oscilación activa","crit"))
                elif lod_m >= 3: checks.append(("🟡","LOD", f"{lod_m}/20 misses tipo LOD","warn"))
                else:            checks.append(("🟢","LOD", f"Estable ({lod_m} misses LOD recientes)","ok"))
            else:
                checks.append(("🟢", "LOD", "Estable (diagnóstico no activo)", "ok"))

            # ── 5. Memoria ────────────────────────────────────────
            try:
                undo_mb = sum(_sys.getsizeof(s) for s in self._undo_stack) / 1_048_576
                vram_kb = self._perf_ring[-1].get("vram_kb", 0) if self._perf_ring else 0
                msg_m   = f"Undo={undo_mb:.0f}MB  VRAM={vram_kb/1024:.1f}MB  entidades={len(self.entities):,}"
                tag_m   = "crit" if undo_mb > 300 else ("warn" if undo_mb > 100 else "ok")
                icon_m  = "🔴" if tag_m == "crit" else ("🟡" if tag_m == "warn" else "🟢")
                checks.append((icon_m, "Memoria", msg_m, tag_m))
            except Exception:
                checks.append(("⚪", "Memoria", "No disponible", "gray"))

            # ── 6. Freezes recientes ──────────────────────────────
            evts = getattr(self, '_freeze_events', [])
            if not evts:
                checks.append(("🟢", "Freezes", "Sin eventos detectados", "ok"))
            else:
                mx = max(e["frozen_ms"] for e in evts)
                tag_f = "crit" if mx >= 1000 else "warn"
                checks.append(("🔴" if mx >= 1000 else "🟡", "Freezes",
                                f"{len(evts)} evento(s), máx {mx}ms", tag_f))

            # ── 7. CTkProgressBar residuales ─────────────────────
            # Usamos _active_pbar_count en vez de gc.get_objects() (que toma
            # 6-25ms con 18k-67k entidades y bloquea el hilo principal).
            n_pb = getattr(self, '_active_pbar_count', 0)
            if n_pb > 0:
                checks.append(("🟡", "CTk Widgets",
                               f"{n_pb} CTkProgressBar activo(s) residual(es)", "warn"))
            else:
                checks.append(("🟢", "CTk Widgets", "Sin ProgressBar residuales", "ok"))

            # ── 8. Snap INT con N entidades ───────────────────────
            n_ents   = len(self.entities)
            snap_int = getattr(self, '_snap_types', {}).get("int", False)
            if n_ents > 3000 and snap_int:
                checks.append(("🟡", "Snap INT",
                                f"{n_ents:,} ents + INT activo — posible O(N²)", "warn"))
            else:
                checks.append(("🟢", "Snap INT",
                                f"{n_ents:,} entidades — rendimiento OK", "ok"))

            # ── 9. DXF Export roundtrip ───────────────────────────
            rt = self._last_roundtrip_result
            if rt is None:
                checks.append(("⚪", "DXF Export",
                                "Sin export verificado aún", "gray"))
            elif rt:
                checks.append(("🔴", "DXF Export",
                                f"{len(rt)} discrepancia(s) en último export", "crit"))
            else:
                checks.append(("🟢", "DXF Export", "Último export OK", "ok"))

            # ── 10. GL Timeout rate ───────────────────────────────
            if n_renders > 10 and n_timeout > 0:
                checks.append(("🔴" if timeout_pct > 5 else "🟡", "GL Timeouts",
                                f"{n_timeout}/{n_renders} renders hicieron timeout ({timeout_pct:.1f}%)",
                                "crit" if timeout_pct > 5 else "warn"))
            elif n_renders > 10:
                checks.append(("🟢", "GL Timeouts",
                                f"0/{n_renders} timeouts", "ok"))

            # ── Actualizar badge global ───────────────────────────
            n_crit = sum(1 for *_, t in checks if t == "crit")
            n_warn = sum(1 for *_, t in checks if t == "warn")
            if n_crit:
                badge = f"🔴  {n_crit} crítico(s)"
                badge_col = COLOR_CRIT
            elif n_warn:
                badge = f"🟡  {n_warn} advertencia(s)"
                badge_col = COLOR_WARN
            else:
                badge = "🟢  Sistema OK"
                badge_col = COLOR_OK

            if getattr(lbl_hm_badge, '_fl_t', None) != badge:
                lbl_hm_badge._fl_t = badge
                lbl_hm_badge.configure(text=badge, fg=badge_col)

            ts_txt = _t_hm.strftime("%H:%M:%S")
            lbl_hm_ts.configure(text=f"actualizado {ts_txt}")

            # ── Guardar para botón Copiar ─────────────────────────
            _hm_last[0] = checks

            # ── Actualizar textbox ────────────────────────────────
            hm_box.configure(state="normal")
            hm_box.delete("1.0", "end")
            hm_box.insert("end", f"{'CHECK':<16}  {'ESTADO'}\n", "head")
            hm_box.insert("end", "─" * 55 + "\n", "sep")
            for icon, cat, msg, tag in checks:
                hm_box.insert("end", f"{icon}  {cat:<14}  ", "head")
                hm_box.insert("end", f"{msg}\n", tag)
            hm_box.configure(state="disabled")

        def _hm_poll():
            # Parar si la ventana fue destruida O está oculta (withdrawn).
            # win.withdraw() no destruye la ventana pero sí detiene winfo_ismapped().
            # Esto evita que el after(2000) corra indefinidamente en background.
            if not win.winfo_exists() or not win.winfo_ismapped():
                return
            try:
                _hm_run()
            except Exception:
                pass
            win.after(2000, _hm_poll)

        _hm_run()   # ejecución inmediata al abrir el tab
        win.after(2000, _hm_poll)

        win.protocol("WM_DELETE_WINDOW", lambda: (
            win.after_cancel(_refresh_id[0]) if _refresh_id[0] else None,
            win.destroy()
        ))

    # ── Freeze Detector ──────────────────────────────────────────────────

    def _start_watchdog(self):
        """Arranca el watchdog de freeze detection."""
        import time as _t
        self._watchdog_active   = True
        self._watchdog_ack_time = _t.perf_counter()
        self.root.after(200, self._heartbeat_ack)
        t = threading.Thread(target=self._watchdog_worker,
                             daemon=True, name="CAD-WatchdogThread")
        t.start()

    def _heartbeat_ack(self):
        """Confirmación en hilo principal — prueba que el event loop responde."""
        import time as _t
        self._watchdog_ack_time = _t.perf_counter()
        if self._watchdog_active:
            self.root.after(200, self._heartbeat_ack)

    def _watchdog_worker(self):
        """Hilo de fondo: detecta cuando el hilo principal no responde >500ms."""
        import time as _t
        THRESHOLD = 0.5    # segundos
        while self._watchdog_active:
            _t.sleep(0.1)
            # Suprimir detección mientras un diálogo nativo (filedialog) tiene el hilo:
            # el OS bloquea el event loop por diseño — no es un freeze real.
            if getattr(self, '_native_dialog_open', False):
                self._watchdog_ack_time = _t.perf_counter()  # reset timer
                self._freeze_reported   = False
                continue
            elapsed = _t.perf_counter() - self._watchdog_ack_time
            if elapsed > THRESHOLD and not self._freeze_reported:
                self._freeze_reported = True
                self._capture_freeze(elapsed * 1000)
            elif elapsed < 0.25:
                self._freeze_reported = False

    def _open_native_dialog(self, fn, **kw):
        """Wrapper para cualquier filedialog.askXxx — suprime el watchdog mientras
        el diálogo nativo del OS bloquea el hilo principal (no es un freeze real)."""
        import time as _t
        self._native_dialog_open = True
        try:
            return fn(**kw)
        finally:
            self._native_dialog_open = False
            self._watchdog_ack_time  = _t.perf_counter()   # reset timer al salir

    def _capture_freeze(self, duration_ms: float):
        """Captura stack trace + contexto durante el freeze. Corre en watchdog thread."""
        import sys, traceback, json
        from datetime import datetime
        # Stack del hilo principal
        frames = sys._current_frames()
        main_id = threading.main_thread().ident
        stack = []
        if main_id in frames:
            for fi in traceback.extract_stack(frames[main_id]):
                stack.append(f"{os.path.basename(fi.filename)}:{fi.lineno}  {fi.name}")
        stack = stack[-10:]   # últimos 10 frames
        # Estado del renderer
        rend  = getattr(self, '_renderer', None)
        gl_q  = 0
        try:
            gl_q = rend._gl_queue.qsize() if rend else 0
        except Exception:
            pass
        event = {
            'ts':           datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'frozen_ms':    int(duration_ms),
            'ents':         len(getattr(self, 'entities', [])),
            'sc':           round(float(getattr(self, 'scale', 0)), 2),
            'tess_pending': bool(getattr(rend, '_tess_pending', False)),
            'gl_queue':     gl_q,
            'last_op':      getattr(self, '_last_freeze_op', ''),
            'stack':        stack,
        }
        with self._freeze_lock:
            self._freeze_events.append(event)
            if len(self._freeze_events) > 50:
                self._freeze_events = self._freeze_events[-50:]
        try:
            with open(self._freeze_log_path, 'a', encoding='utf-8') as _f:
                _f.write(json.dumps(event, ensure_ascii=False) + '\n')
        except Exception:
            pass
        try:
            self.root.after(0, self._refresh_freeze_panel)
        except Exception:
            pass

    def _perf_report(self) -> str:
        """Genera un reporte de texto con el promedio del ring buffer."""
        ring = self._perf_ring
        if not ring:
            return "Sin datos de performance aún."
        N    = len(ring)
        last = ring[-min(60, N):]
        def _avg(k): return sum(r[k] for r in last) / len(last)
        import sys as _sys
        return (
            f"=== REPORTE PERFORMANCE — Estudio Merlos CAD ===\n"
            f"Frames medidos:  {len(last)}\n"
            f"GL avg:          {_avg('gl_ms'):.1f} ms\n"
            f"Overlay PIL avg: {_avg('overlay_ms'):.1f} ms\n"
            f"Frame total avg: {_avg('total_ms'):.1f} ms\n"
            f"VRAM buffers:    {_avg('vram_kb')/1024:.1f} MB\n"
            f"Undo stack:      {_avg('undo_kb')/1024:.1f} MB\n"
            f"FPS estimado:    {_avg('fps'):.0f}\n"
            f"Entidades:       {int(last[-1]['n_ents']):,}\n"
            f"Cache hit rate:  {sum(1 for r in last if r['cache_hit'])*100//len(last)}%\n"
        )

    def _save_cfg_key(self, key: str, value) -> None:
        """Guarda un único campo en settings.json inmediatamente."""
        import json as _json
        cfg = self._leer_config_ia()
        cfg[key] = value
        path = self._cfg_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(cfg, f, indent=2)
        self._config_dirty = True

    def _write_config(self, cfg: dict) -> None:
        """Escribe el dict completo de configuración a disco."""
        import json as _json
        path = self._cfg_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            _json.dump(cfg, f, indent=2)
        self._config_dirty = True

    def _dim_new_style(self, ds_var, ds_om) -> None:
        """Crea un nuevo DIMSTYLE copiando los valores por defecto."""
        dlg = ctk.CTkInputDialog(text="Nombre del nuevo estilo de cota:", title="Nuevo DIMSTYLE")
        name = dlg.get_input()
        if not name or not name.strip():
            return
        name = name.strip()
        cfg = self._leer_config_ia()
        styles = cfg.setdefault("dimstyles", {}).setdefault("styles", {})
        if name not in styles:
            styles[name] = {
                "arrow_type":"architectural","arrow_size":0.15,
                "text_height":0.20,"text_style":"Standard",
                "text_position":"above","text_align":"with_dim",
                "ext_beyond":0.20,"ext_offset":0.05,"text_offset":0.05,
                "units_format":"decimal","precision":2,
                "suffix":" m","scale_factor":1.0,
                "line_color":"BYLAYER","text_color":"BYLAYER",
            }
            cfg["dimstyles"]["active"] = name
            self._write_config(cfg)
            vals = list(styles.keys())
            ds_om.configure(values=vals)
            ds_var.set(name)

    def _dim_dup_style(self, ds_var, ds_om) -> None:
        """Duplica el DIMSTYLE activo con sufijo _copia."""
        cfg    = self._leer_config_ia()
        styles = cfg.setdefault("dimstyles",{}).setdefault("styles",{})
        src    = ds_var.get()
        name   = src + "_copia"
        i = 2
        while name in styles:
            name = f"{src}_copia{i}"; i += 1
        styles[name] = dict(styles.get(src, {}))
        cfg["dimstyles"]["active"] = name
        self._write_config(cfg)
        vals = list(styles.keys())
        ds_om.configure(values=vals)
        ds_var.set(name)

    def _dim_del_style(self, ds_var, ds_om, all_styles) -> None:
        """Elimina el DIMSTYLE activo (mínimo 1 debe quedar)."""
        cfg    = self._leer_config_ia()
        styles = cfg.setdefault("dimstyles",{}).setdefault("styles",{})
        name   = ds_var.get()
        if len(styles) <= 1:
            return
        styles.pop(name, None)
        remaining = list(styles.keys())
        cfg["dimstyles"]["active"] = remaining[0]
        self._write_config(cfg)
        ds_om.configure(values=remaining)
        ds_var.set(remaining[0])

    def _tx_new_style(self, tx_var, tx_om) -> None:
        """Crea un nuevo TEXTSTYLE."""
        dlg = ctk.CTkInputDialog(text="Nombre del nuevo estilo de texto:", title="Nuevo TextStyle")
        name = dlg.get_input()
        if not name or not name.strip():
            return
        name = name.strip()
        cfg = self._leer_config_ia()
        styles = cfg.setdefault("textstyles",{}).setdefault("styles",{})
        if name not in styles:
            styles[name] = {"font":"Courier New","height":0.20,
                            "bold":False,"italic":False,
                            "width_factor":1.0,"oblique":0.0}
            cfg["textstyles"]["active"] = name
            self._write_config(cfg)
            vals = list(styles.keys())
            tx_om.configure(values=vals)
            tx_var.set(name)

    def _construir_ctx_proyecto(self, cfg: dict) -> str:
        """Lee proyecto_activo de settings y construye un bloque de texto para el system prompt."""
        pa = cfg.get("proyecto_activo", {})
        if not pa:
            return ""
        lines = ["\n── PROYECTO ACTIVO ──────────────────────────────"]
        nombre = pa.get("nombre", "Sin nombre")
        lines.append(f"Nombre: {nombre}")

        t = pa.get("terreno")
        if not t:
            # Fallback para proyectos viejos: leer archivo JSON apuntado por terreno_json_path
            tp = cfg.get("terreno_json_path", "")
            if tp and os.path.exists(tp):
                try:
                    import json as _j
                    with open(tp, encoding="utf-8") as _f:
                        td = _j.load(_f)
                    t = {
                        "area_m2":       td.get("area_m2"),
                        "huella_max_m2": td.get("huella_max_m2"),
                        "retiros":       td.get("retiros", {}),
                        "norte_lote":    td.get("norte_lote") or cfg.get("norte_lote", "norte"),
                    }
                except Exception as exc:
                    print(f"[WARN] no se pudo leer terreno JSON para contexto IA: {exc}")
        if t:
            r = t.get("retiros", {})
            lines.append(
                f"Terreno: {t.get('area_m2','?')} m²  "
                f"| huella máx: {t.get('huella_max_m2','?')} m²  "
                f"| norte lote: {t.get('norte_lote','?')}"
            )
            lines.append(
                f"  Retiros — frontal: {r.get('frontal','?')} m  "
                f"posterior: {r.get('posterior','?')} m  "
                f"laterales: {r.get('lateral_izq','?')}/{r.get('lateral_der','?')} m"
            )

        p = pa.get("programa")
        if p:
            lines.append(
                f"Programa: {p.get('total_m2','?')} m²  "
                f"| {p.get('n_dormitorios','?')} dorm  "
                f"| perfil: {p.get('perfil','?')}  "
                f"| norte: {p.get('norte_lote','?')}"
            )
            for esp in p.get("espacios", []):
                estado = "✓" if esp.get("cumple", True) else "⚠"
                lines.append(f"  {estado} {esp['nombre']}: {esp['area_m2']} m²")
            if p.get("errores"):
                lines.append(f"  !! Errores normativa: {', '.join(p['errores'][:3])}")

        d = pa.get("diseno_paso0")
        if d:
            lines.append(f"Diseño Paso 0: score {d.get('score','?')}/100  | norte: {d.get('norte','?')}")
            for obs in d.get("observaciones", [])[:3]:
                lines.append(f"  → {obs}")

        lines.append("─────────────────────────────────────────────────")
        return "\n".join(lines)

    def _llamar_ia_stream(self, prompt: str, on_chunk, on_done):
        """Llama a la IA con streaming. on_chunk(str) por cada token, on_done() al terminar."""
        cfg      = self._leer_config_ia()
        provider = cfg.get("provider", "anthropic")
        model    = cfg.get("model", "claude-opus-4-5")
        api_key  = (self._kr_get(provider)
                    or cfg.get(f"{provider}_api_key")   # fallback: key antigua en texto plano
                    or os.environ.get(f"{provider.upper()}_API_KEY")
                    or os.environ.get("ANTHROPIC_API_KEY"))
        max_tok  = int(cfg.get("max_tokens", 500))

        tipos = {}
        for e in self.entities:
            tp = type(e).__name__
            tipos[tp] = tipos.get(tp, 0) + 1
        resumen_ents  = "  ".join(f"{v} {k}" for k, v in tipos.items()) or "dibujo vacío"
        capas_activas = [n for n, l in self.layers.items() if l.visible]
        ctx_proyecto  = self._construir_ctx_proyecto(cfg)
        ctx_sistema = (
            "Sos un asistente CAD integrado en 'Estudio Merlos CAD', una aplicación "
            "para arquitectos costarricenses. Respondés siempre en español, de forma "
            "concisa (máx 3 oraciones). Si la petición implica crear o modificar "
            "entidades, devolvés TAMBIÉN un bloque JSON al final con el formato:\n"
            "```json\n"
            "{\"entities\": [{\"type\":\"line\",\"x1\":0,\"y1\":0,\"x2\":5,\"y2\":0,"
            "\"layer\":\"A-MURO\"}, ...], \"cmds\": [\"ZE\"]}\n```\n"
            "Tipos válidos: line, polyline, circle, arc, text.\n"
            "Capas disponibles: " + ", ".join(self.layers.keys()) + ".\n"
            "Normativa CR: muro=0.15m, puerta ext=1.10m, int=1.00m, "
            "retiro frontal≥2m, posterior≥3m."
            + ctx_proyecto
        )
        ctx_dibujo = (
            f"Estado actual: entidades={resumen_ents}, "
            f"capa activa={self.active_layer}, "
            f"capas visibles={', '.join(capas_activas)}"
        )
        user_msg = f"{ctx_dibujo}\n\nPetición: {prompt}"

        try:
            if provider == "anthropic":
                import anthropic
                client = (anthropic.Anthropic(api_key=api_key)
                          if api_key else anthropic.Anthropic())
                with client.messages.stream(
                    model=model, max_tokens=max_tok,
                    system=ctx_sistema,
                    messages=[{"role": "user", "content": user_msg}]
                ) as stream:
                    for text in stream.text_stream:
                        on_chunk(text)

            elif provider == "openai":
                from openai import OpenAI
                client = OpenAI(api_key=api_key or None)
                stream = client.chat.completions.create(
                    model=model, max_tokens=max_tok,
                    messages=[{"role": "system", "content": ctx_sistema},
                               {"role": "user",   "content": user_msg}],
                    stream=True)
                for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        on_chunk(delta)

            elif provider == "gemini":
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                gm = genai.GenerativeModel(model, system_instruction=ctx_sistema)
                for chunk in gm.generate_content(user_msg, stream=True):
                    if chunk.text:
                        on_chunk(chunk.text)

            else:
                on_chunk(f"Proveedor desconocido: {provider}")

        except ImportError as ex:
            pkg = str(ex).split("'")[1] if "'" in str(ex) else str(ex)
            on_chunk(f"Paquete '{pkg}' no instalado — pip install {pkg}")
        except Exception as ex:
            on_chunk(f"Error API ({provider}): {ex}")

        on_done()

    def _llamar_ia(self, prompt: str) -> dict:
        """Llama a la IA configurada. Retorna {text, entities, cmds}."""
        # ── Leer configuración del proveedor ────────────────────────
        cfg      = self._leer_config_ia()

        # Construir resumen del dibujo
        tipos = {}
        for e in self.entities:
            t = type(e).__name__
            tipos[t] = tipos.get(t, 0) + 1
        resumen_ents = "  ".join(f"{v} {k}" for k, v in tipos.items()) or "dibujo vacío"
        capas_activas = [n for n, l in self.layers.items() if l.visible]
        ctx_proyecto  = self._construir_ctx_proyecto(cfg)

        ctx_sistema = (
            "Sos un asistente CAD integrado en 'Estudio Merlos CAD', una aplicación "
            "para arquitectos costarricenses. Respondés siempre en español, de forma "
            "concisa (máx 3 oraciones). Si la petición implica crear o modificar "
            "entidades, devolvés TAMBIÉN un bloque JSON al final con el formato:\n"
            "```json\n"
            "{\"entities\": [{\"type\":\"line\",\"x1\":0,\"y1\":0,\"x2\":5,\"y2\":0,\"layer\":\"A-MURO\"}, ...], "
            "\"cmds\": [\"ZE\"]}\n"
            "```\n"
            "Tipos válidos: line, polyline, circle, arc, text.\n"
            "Capas disponibles: " + ", ".join(self.layers.keys()) + ".\n"
            "Normativa CR activa: muro=0.15m, puerta ext=1.10m, int=1.00m, "
            "retiro frontal≥2m, posterior≥3m."
            + ctx_proyecto
        )

        ctx_dibujo = (
            f"Estado actual del dibujo:\n"
            f"  Entidades: {resumen_ents}\n"
            f"  Capa activa: {self.active_layer}\n"
            f"  Capas visibles: {', '.join(capas_activas)}\n"
            f"  Escala visual: 1:{max(1, int(1/self.scale*1000))}"
        )
        provider = cfg.get("provider", "anthropic")
        model    = cfg.get("model", "claude-opus-4-5")
        api_key  = (self._kr_get(provider)
                    or cfg.get(f"{provider}_api_key")   # fallback: key antigua en texto plano
                    or os.environ.get(f"{provider.upper()}_API_KEY")
                    or os.environ.get("ANTHROPIC_API_KEY"))  # legacy fallback
        max_tok  = int(cfg.get("max_tokens", 500))

        try:
            if provider == "anthropic":
                import anthropic
                client = (anthropic.Anthropic(api_key=api_key)
                          if api_key else anthropic.Anthropic())
                msg = client.messages.create(
                    model=model, max_tokens=max_tok,
                    system=ctx_sistema,
                    messages=[{"role": "user",
                               "content": f"{ctx_dibujo}\n\nPetición: {prompt}"}])
                return self._parsear_respuesta_ia(msg.content[0].text)

            elif provider == "openai":
                from openai import OpenAI
                client = OpenAI(api_key=api_key or None)
                resp = client.chat.completions.create(
                    model=model, max_tokens=max_tok,
                    messages=[
                        {"role": "system", "content": ctx_sistema},
                        {"role": "user",
                         "content": f"{ctx_dibujo}\n\nPetición: {prompt}"},
                    ])
                return self._parsear_respuesta_ia(
                    resp.choices[0].message.content)

            elif provider == "gemini":
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                m = genai.GenerativeModel(model, system_instruction=ctx_sistema)
                resp = m.generate_content(
                    f"{ctx_dibujo}\n\nPetición: {prompt}")
                return self._parsear_respuesta_ia(resp.text)

            else:
                return {"text": f"Proveedor desconocido: {provider}",
                        "entities": [], "cmds": []}

        except ImportError as ex:
            pkg = str(ex).split("'")[1] if "'" in str(ex) else str(ex)
            return {"text": f"Paquete '{pkg}' no instalado — pip install {pkg}",
                    "entities": [], "cmds": []}
        except Exception as ex:
            return {"text": f"Error API ({provider}): {ex}",
                    "entities": [], "cmds": []}

    def _parsear_respuesta_ia(self, texto: str) -> dict:
        """Extrae texto legible + bloque JSON de la respuesta."""
        import re, json
        entities, cmds = [], []
        # Busca bloque ```json ... ```
        m = re.search(r"```json\s*(\{.*?\})\s*```", texto, re.DOTALL)
        if not m:
            m = re.search(r"(\{[^{}]*\"entities\"[^{}]*\})", texto, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                entities = data.get("entities", [])
                cmds     = data.get("cmds", [])
            except Exception as _json_ex:
                self._echo(f"!! IA: JSON inválido ({type(_json_ex).__name__})")
            texto_limpio = texto[:m.start()].strip()
        else:
            texto_limpio = texto.strip()
        return {"text": texto_limpio, "entities": entities, "cmds": cmds}

    # SEC-02: comandos que la IA tiene permitido ejecutar.
    # Usa los nombres INTERNOS del sistema (valores de _CMD_ALIASES), no los alias.
    # Excluidos explícitamente: save, saveas, open, new_dwg, dxf, png
    # (efectos sobre sistema de archivos) y hub_lanzar / ejecutar_script.
    _IA_ALLOWED_CMDS: frozenset = frozenset({
        # Dibujo
        "line", "polyline", "rect", "circle", "arc", "text", "hatch",
        # Edición
        "erase", "move", "copy", "rotate", "scale", "mirror",
        "offset", "trim", "extend", "fillet", "explode",
        # Capas (solo visibilidad — no renombrar ni borrar)
        "layer_on", "layer_off", "layer_iso",
        # Vista
        "zoom_e", "zoom_a", "zoom_cmd", "regen",
        # Undo/Redo
        "undo", "redo",
        # Medición (solo lectura)
        "dist", "area_cmd", "list_ent",
    })

    def _ia_respuesta(self, resultado: dict, texto_ya_mostrado: bool = False):
        """Procesa la respuesta IA y crea entidades si las hay."""
        self._lbl_op.configure(text="")
        self._cmd_prompt_lbl.configure(text="CAD", fg_color=CV_CMD_FG, text_color=UI_BG)

        texto    = resultado.get("text", "")
        entities = resultado.get("entities", [])
        cmds     = resultado.get("cmds", [])

        # Texto ya mostrado en streaming — solo echo breve en la barra de estado
        if not texto_ya_mostrado:
            primera = texto.split("\n")[0][:110] if texto else "OK"
            self._echo(f"IA: {primera}")
            self._add_to_history(f"IA: {texto}", "resp")
        else:
            primera = texto.split("\n")[0][:110] if texto else "OK"
            self._echo(f"IA: {primera}")

        # Crear entidades enviadas por la IA
        if entities:
            self._push_undo()
            n = 0; n_err = 0
            for ed in entities:
                try:
                    t   = ed.get("type", "")
                    lyr = ed.get("layer", self.active_layer)
                    if lyr not in self.layers:
                        lyr = self.active_layer
                    if t == "line":
                        self.entities.append(Line(
                            x1=float(ed["x1"]), y1=float(ed["y1"]),
                            x2=float(ed["x2"]), y2=float(ed["y2"]), layer=lyr))
                        n += 1
                    elif t == "circle":
                        self.entities.append(Circle(
                            cx=float(ed["cx"]), cy=float(ed["cy"]),
                            radius=float(ed["radius"]), layer=lyr))
                        n += 1
                    elif t == "polyline":
                        pts = [(float(p[0]), float(p[1])) for p in ed.get("points", [])]
                        if len(pts) >= 2:
                            self.entities.append(Polyline(
                                points=pts, closed=ed.get("closed", False), layer=lyr))
                            n += 1
                    elif t == "text":
                        self.entities.append(Text(
                            x=float(ed["x"]), y=float(ed["y"]),
                            content=str(ed.get("content","")).upper(),
                            height=float(ed.get("height", 0.20)), layer=lyr))
                        n += 1
                except Exception as _ent_ex:
                    n_err += 1
                    self._add_to_history(
                        f"  !! entidad IA '{ed.get('type','?')}' ignorada: {_ent_ex}", "sys")
            if n:
                self._rebuild_snap_index()
                self._add_to_history(f"  → {n} entidades creadas", "sys")
            if n_err:
                self._echo(f"!! {n_err} entidad(es) IA con error — ver historial")

        # Ejecutar comandos sugeridos por la IA
        for cmd in cmds:
            accion = _CMD_ALIASES.get(cmd.strip().upper())
            if accion:
                self._ejecutar_accion(accion, "")

        if entities or cmds:
            self._redraw()

    def _mostrar_ayuda(self):
        """Ventana de ayuda con scroll — F1."""
        # Si ya está abierta, traer al frente
        if hasattr(self, "_ayuda_win") and self._ayuda_win.winfo_exists():
            self._ayuda_win.lift(); self._ayuda_win.focus_set(); return

        win = ctk.CTkToplevel(self.root)
        win.title("F1 — Atajos y Comandos  ·  Estudio Merlos CAD")
        win.geometry("720x680")
        win.resizable(True, True)
        win.attributes("-topmost", False)
        win.configure(fg_color=UI_BG)
        self._ayuda_win = win

        # ── Cabecera ──────────────────────────────────────────────
        hdr = ctk.CTkFrame(win, fg_color=UI_PAN, corner_radius=0, height=46)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr,
            text="⌨  Atajos de teclado  &  Comandos",
            font=ctk.CTkFont("Segoe UI", 15, "bold"),
            text_color=UI_TEXT,
        ).pack(side="left", padx=16, pady=10)
        ctk.CTkLabel(
            hdr,
            text="compatible AutoCAD",
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=UI_TEXT2,
        ).pack(side="left", pady=10)
        ctk.CTkButton(
            hdr, text="✕", width=32, height=28,
            fg_color="transparent", hover_color=UI_ERR,
            text_color=UI_TEXT2, font=ctk.CTkFont("Segoe UI", 12),
            command=win.destroy,
        ).pack(side="right", padx=8, pady=8)

        # ── Área scrollable ───────────────────────────────────────
        # Usamos tk.Text con scrollbar nativa (mejor rendimiento y select)
        frame = ctk.CTkFrame(win, fg_color=UI_BG, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=0, pady=0)

        sb = tk.Scrollbar(frame, orient="vertical", bg=UI_CARD,
                          troughcolor=UI_PAN, activebackground=UI_ACC,
                          width=12, bd=0, highlightthickness=0)
        sb.pack(side="right", fill="y")

        txt_widget = tk.Text(
            frame,
            wrap="word", state="normal",
            bg=UI_BG, fg=UI_TEXT,
            insertbackground=UI_ACC,
            selectbackground=UI_ACC, selectforeground="#FFFFFF",
            font=("Consolas", 10),
            relief="flat", bd=0,
            padx=20, pady=14,
            yscrollcommand=sb.set,
            cursor="arrow",
            highlightthickness=0,
        )
        txt_widget.pack(side="left", fill="both", expand=True)
        sb.config(command=txt_widget.yview)

        # ── Tags de estilo ────────────────────────────────────────
        txt_widget.tag_configure("title",
            font=("Segoe UI", 13, "bold"), foreground="#60A5FA",
            spacing1=10, spacing3=4)
        txt_widget.tag_configure("section",
            font=("Segoe UI", 10, "bold"), foreground="#34D399",
            spacing1=14, spacing3=2)
        txt_widget.tag_configure("key",
            font=("Consolas", 10, "bold"), foreground="#FCD34D")
        txt_widget.tag_configure("desc",
            font=("Consolas", 10), foreground="#CBD5E1")
        txt_widget.tag_configure("sep",
            font=("Consolas", 9), foreground="#334155")
        txt_widget.tag_configure("note",
            font=("Segoe UI", 9, "italic"), foreground="#94A3B8",
            spacing1=2)

        def _ins(text, tag="desc"):
            txt_widget.insert("end", text, tag)

        def _row(keys, desc):
            """Una fila con tecla destacada + descripción."""
            _ins(f"  {keys:<18}", "key")
            _ins(f"{desc}\n", "desc")

        def _sec(title):
            _ins(f"\n{title}\n", "section")
            _ins("  " + "─" * 62 + "\n", "sep")

        # ── Contenido ─────────────────────────────────────────────
        _ins("ESTUDIO MERLOS CAD  —  Referencia de comandos\n", "title")
        _ins("  Compatible con alias de AutoCAD · v1\n", "note")

        _sec("DIBUJO")
        _row("L / LINE",        "Línea  (Enter o ESC para terminar)")
        _row("PL / PLINE",      "Polilínea  (C=cerrar, U=deshacer, Enter=fin)")
        _row("SPL / SPLINE",    "Spline")
        _row("REC / RECTANG",   "Rectángulo  (2 esquinas)")
        _row("C / CIRCLE",      "Círculo  (centro → radio o DYN)")
        _row("A / ARC",         "Arco  (inicio → punto en arco → final)")
        _row("EL / ELLIPSE",    "Elipse")
        _row("POL / POLYGON",   "Polígono regular  (lados → radio)")
        _row("T / TEXT / DT",   "Texto  (clic → escribe → Enter)")
        _row("LD / LEADER",     "Líder con anotación")
        _row("CL / CLOUD",      "Nube de revisión")
        _row("XL / XLINE",      "Línea infinita")
        _row("BH / HATCH",      "Relleno / Hatch")
        _row("B / BLOCK",       "Definir bloque")
        _row("I / INSERT",      "Insertar bloque")
        _row("IMG",             "Insertar imagen")

        _sec("EDICIÓN — MODIFICAR")
        _row("E / ERASE",       "Borrar selección  (o tecla Delete)")
        _row("M / MOVE",        "Mover  (sel. → base → destino)")
        _row("CO / COPY",       "Copiar  (multi-destino, ESC=fin)")
        _row("RO / ROTATE",     "Rotar  (sel. → base → ángulo o clic)")
        _row("SC / SCALE",      "Escalar  (sel. → base → factor o clic)")
        _row("MI / MIRROR",     "Espejo  (sel. → eje p1 → eje p2)")
        _row("O / OFFSET",      "Paralela  (dist → entidad + lado)")
        _row("AL / ALIGN",      "Alinear  (sel. → par de puntos origen/destino)")
        _row("AR / ARRAY",      "Arreglo Rectangular o Polar")
        _ins("    ", "desc"); _ins("[R]", "key"); _ins(" Rectangular → filas / cols / separación\n", "desc")
        _ins("    ", "desc"); _ins("[P]", "key"); _ins(" Polar → clic centro → N= en DYN\n", "desc")
        _row("TR / TRIM",       "Recortar  (clic borde → clic lo que sobra)")
        _row("EX / EXTEND",     "Extender  (clic límite → clic extremo)")
        _row("F / FILLET",      "Empalme con radio")
        _row("CHA / CHAMFER",   "Chaflán")
        _row("BR / BREAK",      "Partir entidad en un punto")
        _row("X / EXPLODE",     "Explotar polilínea / bloque en líneas")
        _row("MA / MATCHPROP",  "Copiar propiedades de capa")
        _row("PR / PROPERTIES", "Panel de propiedades")

        _sec("SELECCIÓN")
        _row("Clic",            "Selección puntual")
        _row("Arrastrar →",     "Ventana (azul) — sólo entidades dentro")
        _row("Arrastrar ←",     "Cruce   (verde) — entidades que toca el rect")
        _row("Ctrl+A",          "Seleccionar todo  (respeta capas bloqueadas)")
        _row("Doble clic",      "Editar texto in-place")
        _row("S / SE / SELECT", "Activar herramienta selección  (salir de comando activo)")
        _ins("  En comandos *_sel: cada clic/drag acumula a la selección.\n", "note")

        _sec("COTAS")
        _row("DH",              "Horizontal")
        _row("DV",              "Vertical")
        _row("DA",              "Alineada")
        _row("DAN",             "Angular")
        _row("DR",              "Radio")
        _row("DD",              "Diámetro")
        _row("DAR",             "Longitud de arco")
        _row("DCO",             "Continua")
        _row("DBA",             "En línea base")
        _row("DSP",             "Espaciado")
        _row("DOR",             "Ordenada")

        _sec("CAPAS")
        _row("LA  nombre",      "Activar capa  (ej: LA A-MURO)")
        _row("LAYISO",          "Aislar capa activa  (oculta las demás)")
        _row("LAYON",           "Mostrar todas las capas")
        _row("LAYOFF  nombre",  "Apagar capa")
        _row("LAYLOCK nombre",  "Bloquear capa")
        _row("LAYULK  nombre",  "Desbloquear capa")
        _row("LAYMCUR / LC",    "Clic en entidad → su capa queda activa")
        _row("LA  (gestor)",    "Abrir gestor de capas")

        _sec("MEDICIÓN")
        _row("DI / DIST",       "Distancia entre dos puntos")
        _row("MEA / MEASURE",   "Medir segmentos de polilínea")
        _row("AREA",            "Área de polilínea o círculo")
        _row("ID",              "Coordenadas de un punto")
        _row("LI / LIST",       "Info de entidad seleccionada")

        _sec("VISTA")
        _row("ZE / Z E",        "Zoom Extents  (ajusta a todas las entidades visibles)")
        _row("ZA",              "Zoom All  (igual a ZE — alias alternativo)")
        _row("Z W",             "Zoom Ventana")
        _row("ZP / Z P",        "Zoom Anterior")
        _row("Z  número",       "Zoom a escala  (ej: Z 0.5)")
        _row("Rueda ratón",     "Zoom centrado en cursor")
        _row("PAN",             "Activa modo encuadre con clic izquierdo  (Esc para salir)")
        _row("Botón central",   "Pan directo  (sin activar modo)")
        _row("RE / REGEN",      "Regenerar pantalla")
        _row("SS / SCROLLSPEED","Velocidad de la rueda  (1=lento … 10=rápido, default 5)")
        _ins("    Uso:  SS 7   (aplica inmediatamente y se guarda en settings)\n", "note")
        _ins("    Velocidad 5 = factor ×1.40 por notch  (comportamiento estándar).\n", "note")
        _ins("    Velocidad 1 = ×1.08/notch  ·  Velocidad 10 = ×1.80/notch\n", "note")

        _sec("COORDENADAS EN LÍNEA DE COMANDO")
        _row("1.5,3.0",         "Absoluta")
        _row("@1,0",            "Relativa")
        _row("@2<45",           "Polar relativa  (dist < ángulo)")

        _sec("TECLAS DE FUNCIÓN")
        _row("F1  / ?",         "Esta ventana de ayuda")
        _row("F3  / F9",        "Toggle OSNAP (snap a objetos)")
        _row("F7",              "Toggle GRILLA")
        _row("F8",              "Toggle ORTO  (fuerza 0°/90°)")
        _row("F12",             "Toggle DYN  (entrada dinámica flotante)")
        _row("ESC",             "Cancelar operación / deseleccionar")
        _row("SPACE / Enter",   "Repetir último comando")
        _row("Delete",          "Borrar selección")
        _row("Ctrl+Z",          "Deshacer  (ilimitado)")
        _row("Ctrl+Y",          "Rehacer")
        _row("Ctrl+S",          "Guardar")
        _row("Ctrl+O",          "Abrir")
        _row("Ctrl+N",          "Nuevo dibujo")

        _sec("EXPORTAR / ARCHIVO")
        _row("DXF",             "Exportar DXF")
        _row("PNG",             "Captura PNG")
        _row("PDF",             "Exportar Layout PDF")
        _row("SAVE / Ctrl+S",   "Guardar JSON")
        _row("OPEN / Ctrl+O",   "Abrir JSON")
        _row("NEW  / Ctrl+N",   "Nuevo dibujo")
        _row("LAY",             "Nueva lámina  (Layout)")
        _row("MS",              "Volver a espacio modelo")

        _sec("ASISTENTE IA")
        _row("/texto",          "Consultar IA  (ej: /dibuja un cuarto de 4×3)")

        _sec("LÍNEA DE TIPO / PROPIEDADES GLOBALES")
        _row("LTSCALE / LTS",   "Escala global de tipos de línea  (ej: LTSCALE 50)")
        _ins("    Afecta DASHED, CENTER, HIDDEN, etc. en todo el dibujo.\n", "note")
        _row("EATTEDIT / EA",   "Editor de atributos de bloque (Enhanced Attribute Edit)")
        _ins("    Ejecuta EA → clic en un bloque con atributos para editarlos.\n", "note")

        _sec("VISTA AVANZADA")
        _row("PAN",             "Activa modo encuadre  (arrastrar con clic izquierdo)")
        _row("PAGESETUP",       "Configurar lámina activa  (papel, orientación, escala)")
        _row("PS / PSPACE",     "Activar espacio papel  (paper space)")
        _row("MS / MSPACE",     "Volver a espacio modelo desde paper space")
        _row("LAY / LAYOUT",    "Nueva lámina de presentación")

        _sec("CONFIGURACIÓN / APARIENCIA")
        _row("MIC / MENUICONS",  "Alternar Interface 2 — íconos Lucide en barras y paneles")
        _ins("    ON : botones muestran ícono + texto  ·  OFF : solo texto  (default OFF)\n", "note")
        _ins("    También disponible en  Configuración → Visual → INTERFAZ 2 — ÍCONOS\n", "note")
        _row("LTSCALE / LTS",    "Escala global de tipos de línea  (ej: LTSCALE 50)")
        _ins("    Duplicado aquí por conveniencia; ver sección LÍNEA DE TIPO más arriba.\n", "note")

        _sec("SLE — SPATIAL LEARNING ENGINE")
        _ins("  El SLE aprende tu estilo arquitectónico a partir de las\n", "note")
        _ins("  correcciones que haces sobre las plantas generadas por la IA.\n", "note")
        _ins("  Cuanto más lo uses, mejor contexto entrega al generar nuevas plantas.\n\n", "note")
        _row("SLECORR / SLC",   "Registrar correcciones en el SLE")
        _ins("    ┌ 1ª vez (sin baseline):\n", "note")
        _ins("    │   Toma una instantánea del estado actual del dibujo\n", "note")
        _ins("    │   como punto de partida para detectar cambios.\n", "note")
        _ins("    │   Edita la planta y vuelve a ejecutar el comando.\n", "note")
        _ins("    │\n", "note")
        _ins("    └ 2ª vez en adelante:\n", "note")
        _ins("        Compara el estado actual con la instantánea anterior,\n", "note")
        _ins("        detecta qué recintos se movieron / redimensionaron /\n", "note")
        _ins("        se eliminaron o añadieron, y los registra en la memoria\n", "note")
        _ins("        del SLE para alimentar el aprendizaje.\n\n", "note")
        _row("APRENDER",        "Alias de SLECORR")
        _ins("\n", "note")
        _ins("  FLUJO TÍPICO con app.py:\n", "note")
        _ins("    1. Generar planta en app.py → se dibuja en el CAD\n", "note")
        _ins("    2. Editar recintos directamente en el CAD\n", "note")
        _ins("    3. Ejecutar SLECORR → el SLE registra las diferencias\n", "note")
        _ins("    4. Las correcciones acumuladas mejoran el modelo de aprendizaje\n", "note")
        _ins("\n", "note")
        _ins("  FLUJO SIN app.py (planta dibujada manualmente):\n", "note")
        _ins("    1. Dibujar la planta con etiquetas en capa A-TEXTO\n", "note")
        _ins("    2. SLECORR → toma instantánea inicial\n", "note")
        _ins("    3. Hacer ajustes\n", "note")
        _ins("    4. SLECORR → registra los cambios\n", "note")
        _ins("\n", "note")
        _ins("  REQUISITO: los recintos deben tener etiquetas de texto\n", "note")
        _ins("  en la capa A-TEXTO y muros en A-PAREDES / A-PAREDES 0-4.\n", "note")
        _ins("\n", "desc")

        # Sólo lectura
        txt_widget.config(state="disabled")

        # ── Pie con botón cerrar ──────────────────────────────────
        foot = ctk.CTkFrame(win, fg_color=UI_PAN, corner_radius=0, height=40)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        ctk.CTkButton(
            foot, text="Cerrar",
            width=90, height=26,
            fg_color=UI_CARD, hover_color=UI_ACC,
            text_color=UI_TEXT, font=ctk.CTkFont("Segoe UI", 11),
            command=win.destroy,
        ).pack(side="right", padx=12, pady=7)
        ctk.CTkLabel(
            foot,
            text="↑↓ scroll  ·  arrastra el borde para redimensionar",
            font=ctk.CTkFont("Segoe UI", 9), text_color=UI_TEXT2,
        ).pack(side="left", padx=14, pady=7)

        win.focus_set()

    # ─── Nuevo dibujo ─────────────────────────────────────────────
    # ─── Auto-recuperación ────────────────────────────────────────────────
    def _recovery_path(self) -> str:
        """Ruta del archivo de recuperación — en la carpeta config del proyecto."""
        base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "recovery.json")

    def _autosave_tick(self):
        """Guarda copia de recuperación si hay entidades.
        Se reprograma automáticamente cada _AUTOSAVE_MS milisegundos.
        """
        # Serializar en hilo de fondo para no bloquear el UI ~500ms
        # (json.dump de 67k entidades tarda 500ms en el hilo principal).
        try:
            if self.entities and getattr(self, "_autosave_enabled", True) \
                    and not getattr(self, "_autosave_bg_running", False):
                import threading as _thr
                self._autosave_bg_running = True
                _thr.Thread(
                    target=self._escribir_recovery_bg,
                    daemon=True,
                    name="CAD-Autosave"
                ).start()
        except Exception as exc:
            print(f"[AUTOSAVE] dispatch error: {exc}")
            self._autosave_bg_running = False
        finally:
            if getattr(self, "_autosave_enabled", True):
                self._autosave_job = self.root.after(self._AUTOSAVE_MS, self._autosave_tick)

    def _escribir_recovery(self):
        """Guarda el dibujo en el archivo de recuperación sin tocar _current_ruta ni el título."""
        import json, datetime, copy as _copy
        path     = self._recovery_path()
        tmp      = path + ".tmp"
        prev_ruta  = self._current_ruta
        prev_title = self.root.title()
        prev_lbl   = (self._lbl_file.cget("text")
                      if hasattr(self, "_lbl_file") else None)
        try:
            self._escribir_json(tmp)
            # Inyectar metadatos de recuperación
            with open(tmp, encoding="utf-8") as f:
                data = json.load(f)
            data["_recovery"]    = True
            data["_saved_at"]    = datetime.datetime.now().isoformat(timespec="seconds")
            data["_source_file"] = prev_ruta
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)   # reemplazo atómico: nunca deja archivo corrupto
        except Exception as exc:
            print(f"[AUTOSAVE] no se pudo escribir recovery: {exc}")
            try: os.remove(tmp)
            except Exception: pass
        finally:
            # Revertir los side-effects de _escribir_json
            self._current_ruta = prev_ruta
            self._dirty = True          # el autosave no cuenta como guardado real
            self.root.title(prev_title)
            if hasattr(self, "_lbl_file") and prev_lbl is not None:
                self._lbl_file.configure(text=prev_lbl)

    def _escribir_recovery_bg(self):
        """Versión de _escribir_recovery para hilo de fondo (CAD-Autosave).

        Usa _escribir_json_silent=True para omitir llamadas Tkinter directas
        (root.title, configure) que no son thread-safe. Los side-effects sobre
        _current_ruta y _dirty se revierten vía root.after(0, ...) al terminar.
        """
        import json as _json, datetime as _dt
        path      = self._recovery_path()
        tmp       = path + ".tmp"
        prev_ruta  = self._current_ruta
        prev_dirty = self._dirty
        _ms = self._AUTOSAVE_MS
        try:
            self._escribir_json_silent = True   # suprimir Tkinter calls en _escribir_json
            self._escribir_json(tmp)
            with open(tmp, encoding="utf-8") as f:
                data = _json.load(f)
            data["_recovery"]    = True
            data["_saved_at"]    = _dt.datetime.now().isoformat(timespec="seconds")
            data["_source_file"] = prev_ruta
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as exc:
            print(f"[AUTOSAVE BG] error: {exc}")
            try: os.remove(tmp)
            except Exception: pass
        finally:
            self._escribir_json_silent = False
            self._autosave_bg_running  = False
            # Revertir side-effects y actualizar UI en el hilo principal
            def _ui_revert():
                self._current_ruta = prev_ruta
                self._dirty        = prev_dirty
                try:
                    n = os.path.basename(prev_ruta) if prev_ruta else "sin título"
                    suf = " *" if self._dirty else ""
                    self.root.title(f"Estudio Merlos CAD — {n}{suf}")
                except Exception:
                    pass
                self._echo(f"💾 Recovery guardado  ({_ms // 60000} min)")
            self.root.after(0, _ui_revert)

    def _delete_recovery(self):
        """Elimina el archivo de recuperación tras un cierre limpio."""
        try:
            p = self._recovery_path()
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass

    def _check_recovery_file(self):
        """Al arrancar: si existe un recovery, ofrece restaurarlo."""
        import json, datetime
        path = self._recovery_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not data.get("_recovery"):
                return
        except Exception:
            return   # archivo corrupto → ignorar

        saved_at   = data.get("_saved_at", "desconocido")
        src_file   = data.get("_source_file", "") or "Sin guardar"
        n_ents     = len(data.get("entities", []))

        # ── Diálogo de recuperación ───────────────────────────────────
        # tk.Toplevel en vez de CTkToplevel: evita _windows_set_titlebar_color
        # + update() que bloquea ~600ms en startup en Windows 11.
        dlg = tk.Toplevel(self.root)
        dlg.title("Recuperación automática")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()
        w, h = 420, 230
        rx = self.root.winfo_x() + (self.root.winfo_width()  - w) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{rx}+{ry}")
        dlg.configure(bg=UI_BG)

        hdr = ctk.CTkFrame(dlg, fg_color="#7C3AED", corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(
            hdr, text="🛟  Archivo de recuperación encontrado",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color="#FFFFFF",
        ).pack(anchor="w", padx=16, pady=11)

        body = ctk.CTkFrame(dlg, fg_color=UI_BG)
        body.pack(fill="both", expand=True, padx=18, pady=10)

        src_short = os.path.basename(src_file) if src_file != "Sin guardar" else "Sin guardar"
        for lbl, val in [
            ("📅  Guardado:",  saved_at.replace("T", "  ")),
            ("📄  Archivo:",   src_short),
            ("📐  Entidades:", str(n_ents)),
        ]:
            row = ctk.CTkFrame(body, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(row, text=lbl, width=110,
                         font=ctk.CTkFont("Segoe UI", 10, "bold"),
                         text_color=UI_TEXT2, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=val,
                         font=ctk.CTkFont("Segoe UI", 10),
                         text_color=UI_TEXT, anchor="w").pack(side="left")

        btn_row = ctk.CTkFrame(dlg, fg_color=UI_PAN, corner_radius=0)
        btn_row.pack(fill="x", side="bottom")

        def _restaurar():
            dlg.destroy()
            try:
                import tempfile, json as _j
                # Escribir los datos del recovery a un temp sin los metadatos _*
                clean = {k: v for k, v in data.items() if not k.startswith("_")}
                with tempfile.NamedTemporaryFile("w", suffix=".json",
                                                 delete=False, encoding="utf-8") as tf:
                    _j.dump(clean, tf, ensure_ascii=False)
                    tpath = tf.name
                # Cargar usando el lector estándar
                self._abrir_json_path(tpath)
                try: os.remove(tpath)
                except Exception: pass
                # Restaurar metadatos de sesión
                self._current_ruta = data.get("_source_file", "")
                self._dirty = True   # recovery = cambios sin guardar
                src = self._current_ruta
                titulo = (f"Estudio Merlos CAD — [RECUPERADO] {os.path.basename(src)}"
                          if src else "Estudio Merlos CAD  v1  [RECUPERADO]")
                self.root.title(titulo)
                if hasattr(self, "_lbl_file"):
                    self._lbl_file.configure(text="⚠ RECUPERADO")
                self._echo("✅ Dibujo restaurado desde archivo de recuperación")
                self._delete_recovery()
            except Exception as exc:
                self._echo(f"❌ Error al restaurar: {exc}")

        def _descartar():
            dlg.destroy()
            self._delete_recovery()

        ctk.CTkButton(
            btn_row, text="🛟  Restaurar",
            width=130, height=32,
            fg_color="#7C3AED", hover_color="#6D28D9",
            text_color="#FFFFFF",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            command=_restaurar,
        ).pack(side="left", padx=(12, 4), pady=10)

        ctk.CTkButton(
            btn_row, text="Descartar",
            width=100, height=32,
            fg_color=UI_CARD, hover_color=UI_ERR,
            text_color=UI_TEXT,
            font=ctk.CTkFont("Segoe UI", 12),
            command=_descartar,
        ).pack(side="right", padx=12, pady=10)

        dlg.wait_window()

    def _on_close(self):
        """Intercepta el cierre de ventana: ofrece Guardar / No guardar / Cancelar."""
        # Liberar recursos del renderer (importante para OpenGL: destruye contexto GL)
        try:
            self._renderer.cleanup()
        except Exception:
            pass

        # Si no hay cambios pendientes, cerrar directamente
        if not self._dirty and not self.entities:
            self._delete_recovery()
            self.root.quit()
            return

        # ── Diálogo personalizado (tema oscuro) ──────────────────────
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Cerrar — Estudio Merlos CAD")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.grab_set()

        # Centrar sobre la ventana principal
        dlg.update_idletasks()
        w, h = 380, 190
        rx = self.root.winfo_x() + (self.root.winfo_width()  - w) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{rx}+{ry}")
        dlg.configure(fg_color=UI_BG)

        # Icono + mensaje
        hdr = ctk.CTkFrame(dlg, fg_color=UI_PAN, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(
            hdr, text="💾  ¿Guardar cambios antes de salir?",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=UI_TEXT,
        ).pack(anchor="w", padx=16, pady=12)

        # Nombre del archivo actual
        nombre = (os.path.basename(self._current_ruta)
                  if self._current_ruta else "Sin guardar")
        ctk.CTkLabel(
            dlg,
            text=f"Archivo: {nombre}",
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=UI_TEXT2,
        ).pack(anchor="w", padx=18, pady=(8, 0))

        ctk.CTkLabel(
            dlg,
            text="Los cambios no guardados se perderán si elige «No guardar».",
            font=ctk.CTkFont("Segoe UI", 10),
            text_color=UI_TEXT2,
            wraplength=350,
        ).pack(anchor="w", padx=18, pady=(4, 14))

        # Botones
        btn_row = ctk.CTkFrame(dlg, fg_color=UI_PAN, corner_radius=0)
        btn_row.pack(fill="x", side="bottom")

        def _guardar_y_salir():
            dlg.destroy()
            self._guardar_json()
            # Solo cerrar si el guardado tuvo éxito (ruta asignada)
            if self._current_ruta:
                self._dirty = False
                self._delete_recovery()   # cierre limpio → borrar recovery
                self.root.quit()

        def _no_guardar():
            dlg.destroy()
            self._delete_recovery()       # cierre limpio → borrar recovery
            self.root.quit()

        def _cancelar():
            dlg.destroy()

        ctk.CTkButton(
            btn_row, text="Guardar",
            width=110, height=32,
            fg_color=UI_ACC, hover_color="#1D4ED8",
            text_color="#FFFFFF",
            font=ctk.CTkFont("Segoe UI", 12, "bold"),
            command=_guardar_y_salir,
        ).pack(side="left", padx=(12, 4), pady=10)

        ctk.CTkButton(
            btn_row, text="No guardar",
            width=110, height=32,
            fg_color=UI_ERR, hover_color="#B91C1C",
            text_color="#FFFFFF",
            font=ctk.CTkFont("Segoe UI", 12),
            command=_no_guardar,
        ).pack(side="left", padx=4, pady=10)

        ctk.CTkButton(
            btn_row, text="Cancelar",
            width=90, height=32,
            fg_color=UI_CARD, hover_color=UI_BORD,
            text_color=UI_TEXT,
            font=ctk.CTkFont("Segoe UI", 12),
            command=_cancelar,
        ).pack(side="right", padx=12, pady=10)

        dlg.wait_window()

    def _new_dwg(self):
        if self.entities:
            if not messagebox.askyesno(
                    "Nuevo", "¿Crear nuevo dibujo?\nSe perderán cambios no guardados.",
                    parent=self.root):
                return
        self._push_undo()
        self.entities.clear()
        # Limpiar selección y grips para que no queden flotando
        self._grips = []; self._hot_grip = None; self._hover_grip = None
        self._grip_drag_mode = False
        self._op_mode = ""; self._op_pts = []; self._op_sel = []; self._op_data = {}
        self.draw_pts.clear()
        self._crs_ids = {}      # forzar recreación del cursor
        # Resetear capas a los valores por defecto
        self.layers = {
            k: Layer(**{f.name: getattr(v, f.name)
                        for f in v.__dataclass_fields__.values()})
            for k, v in DEFAULT_LAYERS.items()
        }
        self.active_layer = "0"
        self._refresh_om_capa(list(self.layers.keys()))
        self._om_capa_var.set(self.active_layer)
        self._build_layer_panel()
        self._current_ruta = ""
        self._dirty = False
        self.root.title("Estudio Merlos CAD  v1")
        if hasattr(self, "_lbl_file"):
            self._lbl_file.configure(text="Sin guardar")
        self._rebuild_snap_index()
        # Invalidar VRAM GL para que no muestre el dibujo anterior durante la
        # tessellation vacía que sigue. Sin esto, el renderer dibuja el old VRAM
        # "por continuidad visual" y el dibujo anterior sigue visible con ents=0.
        _rend = getattr(self, '_renderer', None)
        if _rend is not None:
            _rend._vbo_cache_key = None
            _rend._vram_bufs_key = None
        self._redraw()
        self._echo("Nuevo dibujo")

    # ─── Importar DXF / DWG ──────────────────────────────────────
    def _dialogo_seleccion_capas(self, capas_info: list, total_ents: int) -> set | None:
        """
        Muestra una lista de capas del DXF con checkboxes para que el usuario
        elija cuáles importar.

        Returns:
            set de nombres de capas seleccionadas, o
            None si el usuario canceló.
            set vacío indica "importar todas" (botón Todas).
        """
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Seleccionar capas a importar")
        dlg.resizable(True, True)
        dlg.attributes("-topmost", True)

        # Centrar
        self.root.update_idletasks()
        px = self.root.winfo_x() + self.root.winfo_width()  // 2 - 280
        py = self.root.winfo_y() + self.root.winfo_height() // 2 - 280
        dlg.geometry(f"560x560+{px}+{py}")
        dlg.minsize(400, 300)

        _fnt  = ctk.CTkFont(size=11)
        _fntb = ctk.CTkFont(size=11, weight="bold")
        _fnts = ctk.CTkFont(size=10)

        # Encabezado
        hdr = ctk.CTkFrame(dlg, fg_color=UI_PAN, corner_radius=0)
        hdr.pack(fill="x")
        ctk.CTkLabel(hdr, text=f"Capas en el archivo  ({len(capas_info)} capas · {total_ents:,} entidades)",
                     font=_fntb, text_color=UI_TEXT).pack(side="left", padx=12, pady=8)

        # Barra de búsqueda
        search_var = tk.StringVar()
        search_bar = ctk.CTkFrame(dlg, fg_color="transparent")
        search_bar.pack(fill="x", padx=8, pady=(6, 2))
        ctk.CTkLabel(search_bar, text="Buscar:", font=_fnts,
                     text_color=UI_TEXT2).pack(side="left", padx=(0, 4))
        ctk.CTkEntry(search_bar, textvariable=search_var,
                     height=26, font=_fnts, width=200).pack(side="left")

        # Botones de selección rápida
        qbtn = ctk.CTkFrame(dlg, fg_color="transparent")
        qbtn.pack(fill="x", padx=8, pady=2)

        # Lista con scroll
        scroll = ctk.CTkScrollableFrame(dlg, fg_color=UI_PAN, corner_radius=6)
        scroll.pack(fill="both", expand=True, padx=8, pady=4)

        # Construir checkboxes
        check_vars: dict[str, tk.BooleanVar] = {}
        check_rows: list[tuple] = []   # (frame, capa_dict, checkbox)

        for c in capas_info:
            var = tk.BooleanVar(value=c["visible"] and not c["frozen"])
            check_vars[c["name"]] = var

            row = ctk.CTkFrame(scroll, fg_color="transparent", height=28)
            row.pack(fill="x", padx=2, pady=1)

            # Swatch de color
            sw = tk.Label(row, bg=c["color"] if c["color"] != "#000000" else "#333333",
                          width=2, relief="flat")
            sw.pack(side="left", padx=(4, 6), pady=4)

            # Checkbox con nombre
            estado = ""
            if c["frozen"]: estado = " [congelada]"
            elif not c["visible"]: estado = " [apagada]"
            lbl = f"{c['name']}{estado}  ({c['count']:,} ents)"
            cb = ctk.CTkCheckBox(row, text=lbl, variable=var,
                                 font=_fnts, height=22,
                                 text_color=UI_TEXT2 if (c["frozen"] or not c["visible"]) else UI_TEXT)
            cb.pack(side="left", fill="x", expand=True)
            check_rows.append((row, c, cb))

        def _filtrar(*_):
            q = search_var.get().lower()
            for row, c, cb in check_rows:
                match = q in c["name"].lower()
                if match:
                    row.pack(fill="x", padx=2, pady=1)
                else:
                    row.pack_forget()

        search_var.trace_add("write", _filtrar)

        def _sel_todas():
            for c in capas_info:
                check_vars[c["name"]].set(True)

        def _sel_ninguna():
            for c in capas_info:
                check_vars[c["name"]].set(False)

        def _sel_visibles():
            for c in capas_info:
                check_vars[c["name"]].set(c["visible"] and not c["frozen"])

        for txt, cmd in [("Todas", _sel_todas),
                          ("Ninguna", _sel_ninguna),
                          ("Solo visibles", _sel_visibles)]:
            ctk.CTkButton(qbtn, text=txt, height=24, width=90,
                          fg_color=UI_CARD, hover_color=UI_ACC,
                          font=_fnts, command=cmd).pack(side="left", padx=3)

        # Info de entidades seleccionadas
        info_var = tk.StringVar()
        info_lbl = ctk.CTkLabel(qbtn, textvariable=info_var, font=_fnts,
                                text_color=UI_TEXT2)
        info_lbl.pack(side="right", padx=8)

        def _actualizar_info(*_):
            n = sum(c["count"] for c in capas_info
                    if check_vars[c["name"]].get())
            info_var.set(f"{n:,} entidades seleccionadas")

        for v in check_vars.values():
            v.trace_add("write", _actualizar_info)
        _actualizar_info()

        # Botones OK / Cancelar
        result_holder = [None]   # None=cancelado, set=selección

        def _ok():
            sel = {name for name, var in check_vars.items() if var.get()}
            result_holder[0] = sel
            dlg.destroy()

        def _cancelar():
            result_holder[0] = None
            dlg.destroy()

        footer = ctk.CTkFrame(dlg, fg_color=UI_PAN, corner_radius=0)
        footer.pack(fill="x", side="bottom")
        ctk.CTkButton(footer, text="Cancelar", width=100,
                      fg_color=UI_CARD, hover_color=UI_BORD,
                      command=_cancelar).pack(side="right", padx=8, pady=8)
        ctk.CTkButton(footer, text="  Importar seleccionadas  ", width=180,
                      fg_color=UI_ACC, hover_color="#1D4ED8",
                      font=_fntb, command=_ok).pack(side="right", padx=4, pady=8)

        dlg.bind("<Return>", lambda _: _ok())
        dlg.bind("<Escape>", lambda _: _cancelar())
        self.root.after(100, dlg.grab_set)
        dlg.wait_window()

        return result_holder[0]

    def _importar_dxf(self):
        """
        Importa un archivo DXF o DWG al visor.
        Una sola barra de progreso. El diálogo de selección de capas es opcional.
        """
        ruta = self._open_native_dialog(filedialog.askopenfilename,
            parent=self.root,
            title="Importar DXF / DWG",
            filetypes=[
                ("Planos AutoCAD", "*.dxf *.dwg"),
                ("DXF", "*.dxf"),
                ("DWG", "*.dwg"),
                ("Todos", "*.*"),
            ],
        )
        if not ruta:
            return

        # ── Diálogo de opciones ───────────────────────────────────
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Opciones de importación")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)

        self.root.update_idletasks()
        px = self.root.winfo_x() + self.root.winfo_width()  // 2 - 210
        py = self.root.winfo_y() + self.root.winfo_height() // 2 - 180
        dlg.geometry(f"420x360+{px}+{py}")
        dlg.lift(); dlg.focus_force()

        _fnt   = ctk.CTkFont(size=12)
        _fnt_s = ctk.CTkFont(size=11)

        ctk.CTkLabel(dlg, text="Importar DXF / DWG",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(14, 4))
        ctk.CTkLabel(dlg, text=os.path.basename(ruta),
                     font=_fnt_s, text_color=UI_TEXT2).pack(pady=(0, 4))

        if ruta.lower().endswith(".dwg"):
            from cad.dwg_converter import herramientas_disponibles
            tools = herramientas_disponibles()
            _tool_txt = (f"✓ Conversor disponible: {tools[0]}" if tools
                         else "⚠ Sin conversor DWG — ver instrucciones al importar")
            _tool_col = "#16A34A" if tools else "#EAB308"
            ctk.CTkLabel(dlg, text=_tool_txt, font=_fnt_s,
                         text_color=_tool_col).pack(pady=(0, 6))
        else:
            ctk.CTkFrame(dlg, height=6, fg_color="transparent").pack()

        # Factor de escala
        frm_esc = ctk.CTkFrame(dlg, fg_color=UI_PAN, corner_radius=8)
        frm_esc.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(frm_esc, text="Factor de escala:", font=_fnt).pack(
            side="left", padx=10, pady=8)
        _escala_var = ctk.StringVar(value="1.0")
        _ESCALAS = [
            ("1.0  — sin cambio",  "1.0"),
            ("0.001 — mm → m",     "0.001"),
            ("0.01  — cm → m",     "0.01"),
            ("0.0254 — in → m",    "0.0254"),
            ("25.4  — in → mm",    "25.4"),
        ]
        ctk.CTkOptionMenu(
            frm_esc, values=[x[0] for x in _ESCALAS],
            variable=ctk.StringVar(value=_ESCALAS[0][0]),
            fg_color=UI_CARD, button_color=UI_ACC, width=220,
            command=lambda v: _escala_var.set(
                next(val for lbl, val in _ESCALAS if lbl == v))
        ).pack(side="left", padx=6, pady=6)

        # Modo: agregar vs reemplazar
        _modo_var = ctk.StringVar(value="agregar")
        frm_modo = ctk.CTkFrame(dlg, fg_color=UI_PAN, corner_radius=8)
        frm_modo.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(frm_modo, text="Modo:", font=_fnt).pack(
            side="left", padx=10, pady=8)
        ctk.CTkRadioButton(frm_modo, text="Agregar al dibujo actual",
                           variable=_modo_var, value="agregar",
                           font=_fnt_s).pack(side="left", padx=8)
        ctk.CTkRadioButton(frm_modo, text="Reemplazar todo",
                           variable=_modo_var, value="reemplazar",
                           font=_fnt_s).pack(side="left", padx=8)

        # Selección de capas (opcional)
        _sel_capas_var = tk.BooleanVar(value=False)
        frm_sel = ctk.CTkFrame(dlg, fg_color=UI_PAN, corner_radius=8)
        frm_sel.pack(fill="x", padx=20, pady=4)
        ctk.CTkCheckBox(frm_sel, text="Seleccionar capas a importar",
                        variable=_sel_capas_var, font=_fnt_s,
                        checkbox_width=16, checkbox_height=16,
                        ).pack(side="left", padx=10, pady=8)

        # ── ADIP Extract ──────────────────────────────────────────
        _adip_var = tk.BooleanVar(value=False)
        frm_adip = ctk.CTkFrame(dlg, fg_color=UI_PAN, corner_radius=8)
        frm_adip.pack(fill="x", padx=20, pady=4)
        _frm_adip_inner = ctk.CTkFrame(frm_adip, fg_color="transparent")
        _frm_adip_inner.pack(side="left", padx=10, pady=6, fill="x", expand=True)
        ctk.CTkCheckBox(_frm_adip_inner, text="🔍 Ejecutar ADIP Extract antes de abrir",
                        variable=_adip_var, font=_fnt_s,
                        checkbox_width=16, checkbox_height=16,
                        ).pack(anchor="w", pady=(2, 1))
        ctk.CTkLabel(_frm_adip_inner,
                     text="Extrae estilos, capas y bloques desde AutoCAD (requiere AutoCAD abierto)",
                     font=ctk.CTkFont(size=9), text_color=UI_TEXT2,
                     ).pack(anchor="w", padx=(22, 0))

        # Barra de progreso ADIP (oculta hasta que se active)
        frm_adip_prog = ctk.CTkFrame(dlg, fg_color="transparent")
        _adip_lbl_var = tk.StringVar(value="")

        _ok_pressed = [False]
        def _ok():
            if _adip_var.get():
                import threading as _thr, subprocess as _sp, sys as _sys2
                btn_ok.configure(state="disabled")
                btn_cancel.configure(state="disabled")
                ctk.CTkLabel(frm_adip_prog, textvariable=_adip_lbl_var,
                             font=_fnt_s, text_color=UI_TEXT2).pack(pady=(4, 2))
                _adip_pb = ttk.Progressbar(frm_adip_prog, length=360,
                                           mode="indeterminate")
                _adip_pb.pack(padx=20, pady=(0, 6))
                _adip_pb.start(15)
                # insertar ANTES de los botones para que quede entre el check y los btns
                frm_adip_prog.pack(fill="x", padx=20, pady=(0, 6),
                                   before=frm_btns)
                _adip_lbl_var.set("Ejecutando ADIP Extract…")
                dlg.geometry(f"420x420+{px}+{py}")   # expandir para la barra
                dlg.update_idletasks()

                def _run_adip():
                    try:
                        _adip_py = os.path.join(
                            os.path.dirname(os.path.dirname(
                                os.path.abspath(__file__))),
                            "adip_extract.py")
                        _sp.run([_sys2.executable, _adip_py],
                                capture_output=True, text=True, timeout=300)
                    except Exception:
                        pass
                    finally:
                        dlg.after(0, lambda: (_adip_pb.stop(),
                                              _set_adip_done()))

                def _set_adip_done():
                    _ok_pressed[0] = True
                    dlg.destroy()

                _thr.Thread(target=_run_adip, daemon=True).start()
            else:
                _ok_pressed[0] = True
                dlg.destroy()

        frm_btns = ctk.CTkFrame(dlg, fg_color="transparent")
        frm_btns.pack(pady=10)
        btn_cancel = ctk.CTkButton(frm_btns, text="Cancelar", width=100,
                      fg_color=UI_CARD, hover_color=UI_BORD,
                      command=dlg.destroy)
        btn_cancel.pack(side="left", padx=6)
        btn_ok = ctk.CTkButton(frm_btns, text="  Importar  ", width=110,
                      fg_color=UI_ACC, hover_color="#1D4ED8",
                      command=_ok)
        btn_ok.pack(side="left", padx=6)
        dlg.bind("<Return>", lambda _: _ok())

        self.root.after(100, dlg.grab_set)
        dlg.wait_window()

        if not _ok_pressed[0]:
            return

        escala    = float(_escala_var.get())
        modo      = _modo_var.get()
        sel_capas = _sel_capas_var.get()

        # ── Convertir DWG → DXF si es necesario ──────────────────
        ruta_para_ezdxf = ruta
        _dxf_temp = None
        if ruta.lower().endswith(".dwg"):
            from cad.dwg_converter import dwg_a_dxf, DwgConverterError
            self._echo("Convirtiendo DWG → DXF…")
            self.root.update_idletasks()
            try:
                ruta_para_ezdxf = dwg_a_dxf(ruta)
                _dxf_temp = ruta_para_ezdxf
            except DwgConverterError as ex:
                messagebox.showerror("No se puede importar DWG", str(ex),
                                     parent=self.root)
                return

        from cad.dxf_import import importar_dxf, leer_capas_dxf

        # Snapshot del canvas ANTES de lanzar hilos (necesario para pre-calcular zoom)
        _canvas_W = self.canvas.winfo_width()  or 800
        _canvas_H = self.canvas.winfo_height() or 600

        # ── Barra de progreso ÚNICA para toda la operación ────────
        self.root.update_idletasks()
        _pw, _ph = 340, 90
        _prx = self.root.winfo_x() + self.root.winfo_width()  // 2 - _pw // 2
        _pry = self.root.winfo_y() + self.root.winfo_height() // 2 - _ph // 2

        prog_win = ctk.CTkToplevel(self.root)
        prog_win.title("Importando")
        prog_win.resizable(False, False)
        prog_win.attributes("-topmost", True)
        prog_win.geometry(f"{_pw}x{_ph}+{_prx}+{_pry}")
        prog_win.protocol("WM_DELETE_WINDOW", lambda: None)  # no cerrable

        _lbl_paso_var = tk.StringVar(value="Importando archivo…")
        ctk.CTkLabel(prog_win, textvariable=_lbl_paso_var,
                     font=ctk.CTkFont(size=11),
                     text_color=UI_TEXT2).pack(pady=(14, 4))
        pbar = ttk.Progressbar(prog_win, length=300, mode="indeterminate")
        pbar.pack(padx=20, pady=(0, 14))
        pbar.start(20)
        self._active_pbar_count += 1
        prog_win.update()
        # Contenedor mutable: apunta a la barra activa en cada momento.
        # _esperar_capas puede reemplazarla con pbar2 sin romper _aplicar_resultado.
        _cur_pbar = [pbar]

        # ── Función que aplica el resultado al dibujo (hilo principal) ──
        def _aplicar_resultado(res):
            # Error: cerrar barra y mostrar mensaje
            if isinstance(res, Exception):
                try: _cur_pbar[0].stop(); _cur_pbar[0].destroy()
                except Exception: pass
                try: prog_win.destroy()
                except Exception: pass
                messagebox.showerror("Error al importar", str(res),
                                     parent=self.root)
                return

            # Barra sigue visible — actualizar etiqueta
            _lbl_paso_var.set("Preparando vista…")
            try: prog_win.update_idletasks()
            except Exception: pass

            # Estado limpio antes de cargar
            self._push_undo()
            self._grips = []; self._hot_grip = None; self._hover_grip = None
            self._grip_drag_mode = False
            self._op_mode = ""; self._op_pts = []; self._op_sel = []; self._op_data = {}
            self.draw_pts.clear(); self._crs_ids = {}
            self._hover_ent = None
            self._pil_img_cache = None

            if modo == "reemplazar":
                self.entities.clear()
                self.layers.clear()
                self.block_defs.clear()
                from cad import renderer_opengl as _rog
                _rog._insert_bbox_cache.clear()
                _rnd_dxf = getattr(self, '_renderer', None)
                if _rnd_dxf is not None:
                    getattr(_rnd_dxf, '_block_geom_cache', {}).clear()
                    getattr(_rnd_dxf, '_block_cache_layer_index', {}).clear()

            # Bloques
            self.block_defs.update(res.block_defs)

            # Capas del resultado
            for nombre, lyr_obj in res.layers.items():
                if nombre not in self.layers:
                    self.layers[nombre] = lyr_obj
            if modo == "reemplazar" and "0" not in self.layers:
                self.layers["0"] = Layer("0", "#FFFFFF")

            # Capas huérfanas — en bulk con set (sin loop item a item)
            capas_conocidas = set(self.layers)
            for ln in {e.layer for e in res.entities if e.layer not in capas_conocidas}:
                self.layers[ln] = Layer(name=ln, color="#FFFFFF")

            # Añadir entidades en bloque
            self.entities.extend(res.entities)

            self._refresh_om_capa(list(self.layers.keys()))
            self._layer_panel_pending = True   # panel diferido: se construye post-render

            # Viewport pre-calculado en el worker (no bloquea el hilo principal)
            _vp_pre = getattr(res, '_viewport', None)
            if _vp_pre:
                self._save_zoom()
                self.scale, self.offset_x, self.offset_y = _vp_pre
            else:
                # Fallback: calcular aquí (síncrono, solo para archivos pequeños)
                self._zoom_extents()

            # Destruir la barra activa inmediatamente — stop() sin destroy() deja
            # un _internal_loop pendiente que dispara _draw() ~500ms después.
            try: _cur_pbar[0].stop(); _cur_pbar[0].destroy()
            except Exception: pass
            self._active_pbar_count = max(0, self._active_pbar_count - 1)
            _lbl_paso_var.set("Renderizando…")
            try: prog_win.update_idletasks()
            except Exception: pass
            self._import_loading_win = prog_win

            self._redraw()

            n_ents   = len(self.entities)
            n_blocks = len(self.block_defs)
            st  = res.stats
            msg = (f"Importado: {n_ents:,} entidades"
                   f"  ({', '.join(f'{k}:{v}' for k, v in st.get('por_tipo', {}).items())})"
                   f"  · {st.get('capas_nuevas', 0)} capas  · {n_blocks} bloques")

            # Avisos — se muestran DESPUÉS del mensaje de stats con delay
            avisos = [w for w in res.warnings
                      if not w.startswith("Nota:") and not w.startswith("Se omitieron")]

            def _on_index_done():
                self._echo(msg)
                self._redraw_static()
                # GC diferido: recoger objetos del archivo anterior y arrays
                # temporales del tessellator en un solo golpe, 3s después de
                # cargar. Evita los freezes periódicos de 500-700ms que el GC
                # de Python causa solo cuando decide limpiar por su cuenta.
                import gc as _gc
                self.root.after(3000, _gc.collect)
                def _mostrar_aviso(idx):
                    if idx >= len(avisos):
                        return
                    extra = (f"  (+{len(avisos)-idx-1} más)"
                             if idx == 4 and len(avisos) > 5 else "")
                    self._echo(f"⚠ {avisos[idx]}{extra}")
                    if idx < min(4, len(avisos) - 1):
                        self.root.after(4500, lambda i=idx+1: _mostrar_aviso(i))
                if avisos:
                    self.root.after(4500, lambda: _mostrar_aviso(0))

            if n_ents > 20_000:
                # Diferir un frame para que el event loop respire antes de lanzar el hilo.
                # threading.Thread.start() puede bloquear 500ms bajo contención de GIL.
                self.root.after(0, lambda: self._rebuild_indices_bg(on_done=_on_index_done))
                self._echo(msg + "  — indexando…")
            else:
                self._rebuild_snap_index()
                _on_index_done()

        # ── Rama A: sin selección de capas → importar directamente ──
        if not sel_capas:
            _resultado_holder: list = [None]

            def _worker_directo():
                try:
                    layers_ex = self.layers if modo == "agregar" else None
                    res = importar_dxf(
                        ruta_para_ezdxf, escala=escala,
                        agregar_a_layers=layers_ex, capas_filtro=None)
                    # Pre-calcular viewport en este hilo (evita freeze en main thread)
                    if res.entities:
                        try:
                            sc, ox, oy = _vp.zoom_to_fit(
                                res.entities, _canvas_W, _canvas_H, padding=0.10)
                            res._viewport = (sc, ox, oy)
                        except Exception:
                            pass
                    _resultado_holder[0] = res
                except Exception as ex:
                    _resultado_holder[0] = ex
                finally:
                    if _dxf_temp and os.path.isfile(_dxf_temp):
                        try: os.remove(_dxf_temp)
                        except OSError: pass

            hilo = threading.Thread(target=_worker_directo, daemon=True)
            hilo.start()

            def _poll_directo():
                if hilo.is_alive():
                    self.root.after(120, _poll_directo)
                    return
                _aplicar_resultado(_resultado_holder[0])

            self.root.after(120, _poll_directo)

        # ── Rama B: con selección de capas → leer capas primero ──
        else:
            _lbl_paso_var.set("Leyendo capas del archivo…")
            _capas_holder: list = [None]

            def _worker_capas():
                try:
                    _capas_holder[0] = leer_capas_dxf(ruta_para_ezdxf)
                except Exception as ex:
                    _capas_holder[0] = ex

            hilo_capas = threading.Thread(target=_worker_capas, daemon=True)
            hilo_capas.start()

            def _esperar_capas():
                if hilo_capas.is_alive():
                    self.root.after(80, _esperar_capas)
                    return

                _capas_res = _capas_holder[0]
                capas_info = [] if isinstance(_capas_res, Exception) else (_capas_res or [])

                # Ocultar barra mientras está el diálogo de capas.
                # Destruir pbar inmediatamente — stop() sin destroy() deja un
                # _internal_loop pendiente que dispara ~500ms de freeze.
                pbar.stop()
                try: pbar.destroy()
                except Exception: pass
                self._active_pbar_count = max(0, self._active_pbar_count - 1)
                prog_win.withdraw()

                capas_filtro = None
                if capas_info:
                    total_ents = sum(c["count"] for c in capas_info)
                    capas_filtro = self._dialogo_seleccion_capas(capas_info, total_ents)
                    if capas_filtro is None:   # canceló
                        prog_win.destroy()
                        return

                # Reactivar con una barra nueva para la importación real
                prog_win.deiconify()
                _lbl_paso_var.set("Importando archivo…")
                pbar2 = ttk.Progressbar(prog_win, length=300, mode="indeterminate")
                pbar2.pack(padx=20, pady=(0, 14))
                pbar2.start(20)
                self._active_pbar_count += 1
                _cur_pbar[0] = pbar2   # _aplicar_resultado cerrará esta barra
                prog_win.update()

                _resultado_holder2: list = [None]

                def _worker_import():
                    try:
                        layers_ex = self.layers if modo == "agregar" else None
                        res2 = importar_dxf(
                            ruta_para_ezdxf, escala=escala,
                            agregar_a_layers=layers_ex,
                            capas_filtro=capas_filtro if capas_filtro else None)
                        # Pre-calcular viewport en este hilo
                        if res2.entities:
                            try:
                                sc, ox, oy = _vp.zoom_to_fit(
                                    res2.entities, _canvas_W, _canvas_H, padding=0.10)
                                res2._viewport = (sc, ox, oy)
                            except Exception:
                                pass
                        _resultado_holder2[0] = res2
                    except Exception as ex:
                        _resultado_holder2[0] = ex
                    finally:
                        if _dxf_temp and os.path.isfile(_dxf_temp):
                            try: os.remove(_dxf_temp)
                            except OSError: pass

                hilo_imp = threading.Thread(target=_worker_import, daemon=True)
                hilo_imp.start()

                def _poll_import():
                    if hilo_imp.is_alive():
                        self.root.after(120, _poll_import)
                        return
                    _aplicar_resultado(_resultado_holder2[0])

                self.root.after(120, _poll_import)

            self.root.after(80, _esperar_capas)

    # ─── Exportar / Guardar ───────────────────────────────────────
    def _exportar_dxf(self):
        ruta = self._open_native_dialog(filedialog.asksaveasfilename,
            parent=self.root, title="Exportar DXF",
            defaultextension=".dxf",
            filetypes=[("DXF", "*.dxf"), ("Todos", "*.*")])
        if not ruta:
            return

        # Snapshot antes del hilo — el usuario puede seguir editando
        _ents_snap  = list(self.entities)
        _lyrs_snap  = dict(self.layers)
        _blks_snap  = dict(getattr(self, "block_defs", {}))
        _nombre     = os.path.basename(ruta)

        # Indicador visual liviano (tk nativo, sin _internal_loop de CTk)
        _exp_win = tk.Toplevel(self.root)
        _exp_win.title("")
        _exp_win.resizable(False, False)
        _exp_win.attributes("-topmost", True)
        _exp_win.overrideredirect(True)
        _exp_win.configure(bg="#1E293B")
        _pw, _ph = 260, 44
        _prx = self.root.winfo_x() + self.root.winfo_width()  // 2 - _pw // 2
        _pry = self.root.winfo_y() + self.root.winfo_height() // 2 - _ph // 2
        _exp_win.geometry(f"{_pw}x{_ph}+{_prx}+{_pry}")
        tk.Label(_exp_win, text=f"Exportando {_nombre}…",
                 bg="#1E293B", fg="#94A3B8",
                 font=("Segoe UI", 10)).pack(expand=True)
        _exp_win.update()

        _result = [None]   # "ok" | "no_ezdxf" | Exception
        _fixes  = [[]]     # lista de fixes aplicados por pre_export_fix

        def _worker():
            try:
                from cad.dxf_export import exportar_dxf
                from cad.dxf_roundtrip import pre_export_fix
                # 1. Correcciones previas al export (rápido, sin I/O)
                applied = pre_export_fix(_ents_snap, _lyrs_snap)
                _fixes[0] = applied
                # 2. Export
                exportar_dxf(_ents_snap, _lyrs_snap, ruta, block_defs=_blks_snap)
                _result[0] = "ok"
            except ImportError:
                _result[0] = "no_ezdxf"
            except Exception as ex:
                _result[0] = ex

        _hilo = threading.Thread(target=_worker, daemon=True)
        _hilo.start()

        def _poll_export():
            if _hilo.is_alive():
                self.root.after(100, _poll_export)
                return
            try: _exp_win.destroy()
            except Exception: pass
            if _result[0] == "ok":
                self._echo(f"DXF: {_nombre}")
                for fx in _fixes[0]:
                    self._echo(f"  ↳ fix: {fx}")
            elif _result[0] == "no_ezdxf":
                messagebox.showwarning("ezdxf", "pip install ezdxf", parent=self.root)
            else:
                messagebox.showerror("Error DXF", str(_result[0]), parent=self.root)

        self.root.after(100, _poll_export)

    def _exportar_dxf_verificado(self):
        """
        Export DXF con roundtrip validation:
          1. pre_export_fix  — correcciones rápidas
          2. exportar_dxf    — genera DXF en temp
          3. validate_roundtrip — reimporta y compara vs original
          4. Muestra reporte de discrepancias antes de confirmar
        """
        ruta = self._open_native_dialog(filedialog.asksaveasfilename,
            parent=self.root, title="Exportar DXF verificado",
            defaultextension=".dxf",
            filetypes=[("DXF", "*.dxf"), ("Todos", "*.*")])
        if not ruta:
            return

        _ents_snap = list(self.entities)
        _lyrs_snap = dict(self.layers)
        _blks_snap = dict(getattr(self, "block_defs", {}))
        _nombre    = os.path.basename(ruta)

        # Indicador
        _exp_win = tk.Toplevel(self.root)
        _exp_win.title("")
        _exp_win.resizable(False, False)
        _exp_win.attributes("-topmost", True)
        _exp_win.overrideredirect(True)
        _exp_win.configure(bg="#1E293B")
        _pw, _ph = 320, 44
        _prx = self.root.winfo_x() + self.root.winfo_width()  // 2 - _pw // 2
        _pry = self.root.winfo_y() + self.root.winfo_height() // 2 - _ph // 2
        _exp_win.geometry(f"{_pw}x{_ph}+{_prx}+{_pry}")
        _lbl_v = tk.Label(_exp_win, text=f"Verificando {_nombre}…",
                          bg="#1E293B", fg="#94A3B8", font=("Segoe UI", 10))
        _lbl_v.pack(expand=True)
        _exp_win.update()

        _result = [None]   # (fixes, report) | Exception

        def _worker_v():
            try:
                from cad.dxf_roundtrip import export_verified
                fixes, report = export_verified(
                    _ents_snap, _lyrs_snap, _blks_snap, ruta, escala=1.0)
                _result[0] = (fixes, report)
            except Exception as ex:
                _result[0] = ex

        _hilo_v = threading.Thread(target=_worker_v, daemon=True)
        _hilo_v.start()

        def _poll_v():
            if _hilo_v.is_alive():
                self.root.after(150, _poll_v)
                return
            try: _exp_win.destroy()
            except Exception: pass

            res = _result[0]
            if isinstance(res, Exception):
                messagebox.showerror("Error DXF", str(res), parent=self.root)
                return

            fixes, report = res

            # Mostrar fixes aplicados
            for fx in fixes:
                self._echo(f"  ↳ fix: {fx}")

            # Mostrar reporte
            self._echo(f"DXF verificado: {_nombre}  — {report.summary()}")
            if report.issues:
                for line in report.lines()[1:]:   # skip header
                    self._echo(f"  {line}")

            # Guardar resultado para Health Monitor
            _errs = [str(i) for i in report.issues
                     if i.severity == "error" and not i.corrected]
            self._last_roundtrip_result = _errs

            # Si hay errores no corregidos → mostrar diálogo de advertencia
            if report.has_errors:
                detail = "\n".join(_errs)
                messagebox.showwarning(
                    "Discrepancias en export",
                    f"Se detectaron diferencias en el DXF exportado:\n\n{detail}\n\n"
                    "El archivo fue guardado de todas formas.",
                    parent=self.root)

        self.root.after(150, _poll_v)

    def _exportar_png(self):
        """
        Exporta el canvas a PNG usando el RendererPIL (no captura de pantalla).

        Ventajas sobre ImageGrab:
          • Funciona aunque la ventana esté detrás de otras o minimizada
          • Soporte HiDPI correcto (no depende de coordenadas de pantalla)
          • Resolución configurable × escala (1×, 2×, 3×, 4×)
          • La selección no aparece en la imagen exportada
        """
        ruta = self._open_native_dialog(filedialog.asksaveasfilename,
            parent=self.root, title="Exportar PNG",
            defaultextension=".png", filetypes=[("PNG", "*.png")])
        if not ruta:
            return

        # ── Diálogo de resolución ─────────────────────────────────
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Resolución PNG")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        self.root.update_idletasks()
        px = self.root.winfo_x() + self.root.winfo_width()  // 2 - 160
        py = self.root.winfo_y() + self.root.winfo_height() // 2 - 90
        dlg.geometry(f"320x200+{px}+{py}")

        _fnt = ctk.CTkFont(size=12)
        ctk.CTkLabel(dlg, text="Resolución de exportación",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(14, 6))

        _ESCALAS = [
            ("1×  — resolución pantalla",   1),
            ("2×  — alta calidad",          2),
            ("3×  — impresión (recomendado)", 3),
            ("4×  — ultra alta resolución", 4),
        ]
        _escala_var = ctk.IntVar(value=2)
        om = ctk.CTkOptionMenu(
            dlg, values=[x[0] for x in _ESCALAS],
            variable=ctk.StringVar(value=_ESCALAS[1][0]),
            fg_color="#334155", button_color="#2563EB", width=260, font=_fnt,
            command=lambda v: _escala_var.set(
                next(val for lbl, val in _ESCALAS if lbl == v)))
        om.pack(padx=20, pady=6)

        W_cv = self.canvas.winfo_width()  or 800
        H_cv = self.canvas.winfo_height() or 600
        _lbl_size = ctk.CTkLabel(dlg,
                                  text=f"Salida: {W_cv*2} × {H_cv*2} px",
                                  font=ctk.CTkFont(size=11), text_color="#94A3B8")
        _lbl_size.pack()

        def _on_scale(v):
            s = next(val for lbl, val in _ESCALAS if lbl == v)
            _escala_var.set(s)
            _lbl_size.configure(text=f"Salida: {W_cv*s} × {H_cv*s} px")
        om.configure(command=_on_scale)

        _ok = [False]
        def _confirmar():
            _ok[0] = True; dlg.destroy()

        frm = ctk.CTkFrame(dlg, fg_color="transparent")
        frm.pack(pady=10)
        ctk.CTkButton(frm, text="Cancelar", width=100,
                      fg_color="#334155", hover_color="#475569",
                      command=dlg.destroy).pack(side="left", padx=6)
        ctk.CTkButton(frm, text="Exportar", width=100,
                      fg_color="#2563EB", hover_color="#1D4ED8",
                      command=_confirmar).pack(side="left", padx=6)

        self.root.after(100, dlg.grab_set)
        dlg.wait_window()
        if not _ok[0]:
            return

        factor = _escala_var.get()

        try:
            # Ocultar selección en la imagen exportada
            sel_backup = [(e, e.selected) for e in self.entities if e.selected]
            for e, _ in sel_backup:
                e.selected = False

            W_out = W_cv * factor
            H_out = H_cv * factor

            # Ajustar offsets para el nuevo tamaño manteniendo el centro
            cx_w = (self.offset_x - W_cv / 2) / self.scale * (-1)
            cy_w = (self.offset_y - H_cv / 2) / self.scale

            scale_out  = self.scale * factor
            offset_x_out = W_out / 2 - cx_w * scale_out
            offset_y_out = H_out / 2 + cy_w * scale_out

            from cad.renderer_pil import RenderCtx
            ctx = RenderCtx(
                W=W_out, H=H_out,
                scale=scale_out,
                offset_x=offset_x_out,
                offset_y=offset_y_out,
                entities=self.entities,
                layers=self.layers,
                entity_index=self._entity_index,
                grid_on=False,          # sin grid en PNG exportado
                bg_color=CV_BG,
                grid_color=CV_GRID,
                grid_maj_color=CV_GRID_MAJ,
                axis_color=CV_AXIS,
                select_color=CV_SELECT,
            )
            img = self._renderer.render(ctx).image
            img.save(ruta, "PNG")
            self._echo(f"PNG {W_out}×{H_out}px → {os.path.basename(ruta)}")

        except Exception as ex:
            messagebox.showerror("Error PNG", str(ex), parent=self.root)
        finally:
            # Restaurar selección
            for e, was_sel in sel_backup:
                e.selected = was_sel

    # ═══════════════════════════════════════════════════════════════════════
    # PLOT / PDF  —  Diálogo estilo AutoCAD
    # ═══════════════════════════════════════════════════════════════════════

    def _exportar_pdf(self):
        """
        Si hay un layout activo: exporta TODAS las láminas (o la activa) como PDF.
        Si estamos en espacio modelo: abre el diálogo de plot clásico.
        """
        if getattr(self, "active_layout_idx", -1) >= 0 and self.layouts:
            self._exportar_pdf_layouts()
        else:
            self._plot_dialog()

    def _exportar_pdf_layouts(self):
        """Exporta todas las láminas del proyecto a un PDF multi-página."""
        try:
            from PIL import Image as _Img
        except ImportError:
            messagebox.showerror("Pillow requerido",
                "pip install Pillow", parent=self.root)
            return

        # ── Diálogo de opciones (plot style + DPI) ────────────────────────
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Exportar láminas — PDF")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        W_d, H_d = 320, 200
        rx = self.root.winfo_x() + (self.root.winfo_width()  - W_d) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - H_d) // 2
        dlg.geometry(f"{W_d}x{H_d}+{rx}+{ry}")
        self.root.after(80, dlg.grab_set)

        FB = ctk.CTkFont(size=11, weight="bold")
        FS = ctk.CTkFont(size=10)
        BG = "#1E293B"; CARD = "#334155"; TX = "#F8FAFC"; TX2 = "#94A3B8"

        ctk.CTkLabel(dlg, text="Opciones de impresión",
                     font=FB, text_color=TX).pack(pady=(12, 6))

        v_estilo = tk.StringVar(value="Color")
        v_dpi    = tk.IntVar(value=150)
        _ok      = [False]

        row1 = ctk.CTkFrame(dlg, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=3)
        ctk.CTkLabel(row1, text="Plot style:", font=FS, text_color=TX2,
                     width=90, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(row1, variable=v_estilo,
                          values=["Color", "Monocromático", "Escala grises"],
                          height=26, font=FS).pack(side="left", fill="x", expand=True)

        row2 = ctk.CTkFrame(dlg, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=3)
        ctk.CTkLabel(row2, text="Resolución:", font=FS, text_color=TX2,
                     width=90, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(row2, variable=v_dpi,
                          values=[72, 150, 300],
                          height=26, font=FS).pack(side="left", fill="x", expand=True)

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(10, 8))
        ctk.CTkButton(btn_row, text="Cancelar", width=90, height=28,
                      fg_color=CARD, hover_color="#475569", font=FS,
                      command=dlg.destroy).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="🖨 Exportar PDF", height=28,
                      fg_color="#16A34A", hover_color="#15803D", font=FB,
                      command=lambda: (setattr(_ok, 0, True), dlg.destroy())
                      ).pack(side="left", fill="x", expand=True)

        dlg.wait_window()
        if not _ok[0]:
            return

        # Pedir destino
        ruta = self._open_native_dialog(filedialog.asksaveasfilename,
            parent=self.root, title="Guardar PDF…",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")])
        if not ruta:
            return

        self._echo("Generando PDF…")
        self.root.update_idletasks()

        try:
            import copy as _copy
            DPI    = int(v_dpi.get())
            estilo = v_estilo.get()
            pages  = []

            for lay in self.layouts:
                pw_mm, ph_mm = paper_size(lay.paper, lay.orientation)
                W_px     = int(pw_mm / 25.4 * DPI)
                H_px     = int(ph_mm / 25.4 * DPI)
                lay_scale = DPI / 25.4

                offset_x = 0.0
                offset_y = H_px

                # ── Aplicar plot style a las capas ─────────────────────
                layers_plot = dict(self.layers)
                if estilo == "Monocromático":
                    layers_plot = {n: _copy.copy(l) for n, l in layers_plot.items()}
                    for l in layers_plot.values():
                        l.color = "#000000"
                elif estilo == "Escala grises":
                    layers_plot = {n: _copy.copy(l) for n, l in layers_plot.items()}
                    for l in layers_plot.values():
                        if l.color.startswith("#"):
                            r = int(l.color[1:3], 16)
                            g = int(l.color[3:5], 16)
                            b = int(l.color[5:7], 16)
                            gray = int(0.299*r + 0.587*g + 0.114*b)
                            l.color = f"#{gray:02X}{gray:02X}{gray:02X}"

                img = render_layout_pil(
                    layout       = lay,
                    W            = W_px,
                    H            = H_px,
                    cv_bg        = "#FFFFFF",
                    lay_scale    = lay_scale,
                    offset_x     = offset_x,
                    offset_y     = float(offset_y),
                    entities     = list(self.entities),
                    layers       = layers_plot,
                    block_defs   = dict(self.block_defs),
                    entity_index = dict(self._entity_index),
                    entity_cell  = self._entity_cell,
                    renderer     = self._renderer,
                    config       = self._leer_config_ia(),
                    axis_color   = "#CCCCCC",
                )
                if img is None:
                    img = _Img.new("RGB", (W_px, H_px), "#FFFFFF")
                pages.append(img.convert("RGB"))

            if not pages:
                self._echo("Sin láminas para exportar"); return

            pages[0].save(
                ruta, "PDF", resolution=DPI,
                save_all=True,
                append_images=pages[1:] if len(pages) > 1 else [],
            )
            nombre = os.path.basename(ruta)
            self._echo(f"PDF guardado: {nombre}  ({len(pages)} lámina(s) · {estilo} · {DPI} dpi)")
            try:
                os.startfile(ruta)
            except Exception:
                pass
        except Exception as exc:
            messagebox.showerror("Error al exportar PDF",
                                 str(exc), parent=self.root)

    def _plot_dialog(self):
        """
        Diálogo de plot completo, similar al de AutoCAD:
        Page Setup · Paper Size · Plot Area · Plot Scale · Plot Offset ·
        Plot Style (color/mono) · Capas · Orientación · Titleblock · Preview
        """
        import datetime

        cfg0 = self._leer_config_ia()
        _plot_cfg = cfg0.get("plot", {})

        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Plot — Exportar PDF")
        dlg.resizable(True, True)
        dlg.attributes("-topmost", True)
        W_dlg, H_dlg = 780, 740
        rx = self.root.winfo_x() + max(0, (self.root.winfo_width()  - W_dlg) // 2)
        ry = self.root.winfo_y() + max(0, (self.root.winfo_height() - H_dlg) // 2)
        dlg.geometry(f"{W_dlg}x{H_dlg}+{rx}+{ry}")
        dlg.minsize(700, 640)

        # ── Fuentes ──────────────────────────────────────────────────────────
        _FB  = ctk.CTkFont(size=11, weight="bold")
        _F   = ctk.CTkFont(size=11)
        _FS  = ctk.CTkFont(size=10)
        _FSS = ctk.CTkFont(size=9)

        # ── Variables de estado ───────────────────────────────────────────────
        _HOJAS = {
            "A4  (210×297)":  (210, 297),
            "A3  (297×420)":  (297, 420),
            "A2  (420×594)":  (420, 594),
            "A1  (594×841)":  (594, 841),
            "A0  (841×1189)": (841, 1189),
            "Carta (216×279)": (216, 279),
            "Tabloide (279×432)": (279, 432),
        }
        _ESCALAS_PLOT = ["Ajustar", "1:20", "1:25", "1:50", "1:75", "1:100",
                         "1:125", "1:150", "1:200", "1:250", "1:500",
                         "1:1000", "2:1", "5:1"]

        v_hoja      = tk.StringVar(value=_plot_cfg.get("hoja",    "A3  (297×420)"))
        v_orient    = tk.StringVar(value=_plot_cfg.get("orient",  "Horizontal"))
        v_escala    = tk.StringVar(value=_plot_cfg.get("escala",  "1:100"))
        v_fit       = tk.BooleanVar(value=_plot_cfg.get("fit",    False))
        v_center    = tk.BooleanVar(value=_plot_cfg.get("center", True))
        v_off_x     = tk.StringVar(value=str(_plot_cfg.get("off_x", "0.00")))
        v_off_y     = tk.StringVar(value=str(_plot_cfg.get("off_y", "0.00")))
        v_area      = tk.StringVar(value=_plot_cfg.get("area",    "Extents"))
        v_estilo    = tk.StringVar(value=_plot_cfg.get("estilo",  "Color"))
        v_dpi       = tk.IntVar(value=_plot_cfg.get("dpi",        150))
        v_tb        = tk.BooleanVar(value=_plot_cfg.get("tb",     True))
        v_norte     = tk.BooleanVar(value=_plot_cfg.get("norte",  True))
        v_escgraf   = tk.BooleanVar(value=_plot_cfg.get("escgraf",True))
        v_upside    = tk.BooleanVar(value=_plot_cfg.get("upside", False))
        v_proyecto  = tk.StringVar(value=_plot_cfg.get("proyecto", cfg0.get("proyecto_nombre", "Proyecto")))
        v_firma     = tk.StringVar(value=_plot_cfg.get("firma",    cfg0.get("firma_pdf", "Estudio Merlos")))
        v_escala_mm = tk.StringVar(value="1")    # mm en papel
        v_escala_u  = tk.StringVar(value="100")  # unidades en modelo

        # Capas: dict {nombre: BooleanVar}
        # Inicializa con estado real: visible=True y locked=False
        # (capas bloqueadas no deben aparecer en el plot igual que en A/B)
        _capa_vars: dict[str, tk.BooleanVar] = {
            n: tk.BooleanVar(value=lyr.visible and not lyr.locked)
            for n, lyr in self.layers.items()
        }

        # Ventana de selección (para Plot Area = Window)
        _window_pts: list = []   # [(wx0,wy0),(wx1,wy1)]

        def _save_cfg():
            cfg0["plot"] = {
                "hoja": v_hoja.get(), "orient": v_orient.get(),
                "escala": v_escala.get(), "fit": v_fit.get(),
                "center": v_center.get(), "off_x": v_off_x.get(),
                "off_y": v_off_y.get(), "area": v_area.get(),
                "estilo": v_estilo.get(), "dpi": v_dpi.get(),
                "tb": v_tb.get(), "norte": v_norte.get(),
                "escgraf": v_escgraf.get(), "upside": v_upside.get(),
                "proyecto": v_proyecto.get(), "firma": v_firma.get(),
            }
            cfg0["firma_pdf"] = v_firma.get()
            cfg0["proyecto_nombre"] = v_proyecto.get()
            import json as _j
            _p = self._cfg_path()
            os.makedirs(os.path.dirname(_p), exist_ok=True)
            with open(_p, "w", encoding="utf-8") as f:
                _j.dump(cfg0, f, indent=2)

        def _on_fit(*_):
            state = "disabled" if v_fit.get() else "normal"
            _om_escala.configure(state=state)
            _ent_mm.configure(state=state)
            _ent_u.configure(state=state)

        def _on_escala(val):
            if val == "Ajustar":
                v_fit.set(True); _on_fit()
                return
            v_fit.set(False); _on_fit()
            if ":" in val:
                parts = val.split(":")
                try:
                    num, den = float(parts[0]), float(parts[1])
                    v_escala_mm.set(str(int(num)))
                    v_escala_u.set(str(int(den)))
                except Exception:
                    pass

        def _on_mm_u(*_):
            # Actualizar label de escala resultante
            try:
                mm  = float(v_escala_mm.get())
                u   = float(v_escala_u.get())
                lbl_esc_res.configure(
                    text=f"= 1 : {u/mm:.0f}" if mm != 0 else "")
            except Exception:
                pass

        # ── Layout del diálogo ────────────────────────────────────────────────
        # Título
        hdr = ctk.CTkFrame(dlg, fg_color=UI_CARD, corner_radius=0)
        hdr.pack(fill="x", side="top")

        ctk.CTkLabel(hdr, text="Plot — Exportar a PDF",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=UI_TEXT).pack(side="left", padx=16, pady=10)

        # Footer anclado abajo PRIMERO (antes del cuerpo)
        foot = ctk.CTkFrame(dlg, fg_color=UI_CARD, corner_radius=0, height=110)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)

        # Separador
        ctk.CTkFrame(dlg, height=1, fg_color=UI_BORD).pack(fill="x", side="bottom")

        # ══ Columnas en el cuerpo ════════════════════════════════════════════
        cols = ctk.CTkFrame(dlg, fg_color="transparent")
        cols.pack(fill="both", expand=True, side="top")
        cols.columnconfigure(0, weight=1)
        cols.columnconfigure(2, weight=1)
        cols.rowconfigure(0, weight=1)

        left  = ctk.CTkFrame(cols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(4, 2), pady=4)

        ctk.CTkFrame(cols, width=1, fg_color=UI_BORD).grid(row=0, column=1, sticky="ns", pady=8)

        right = ctk.CTkFrame(cols, fg_color="transparent")
        right.grid(row=0, column=2, sticky="nsew", padx=(2, 4), pady=4)

        def _section(parent, title):
            f = ctk.CTkFrame(parent, fg_color=UI_PAN, corner_radius=6)
            f.pack(fill="x", padx=6, pady=(4, 2))
            ctk.CTkLabel(f, text=title, font=_FB, text_color="#60A5FA",
                         anchor="w").pack(fill="x", padx=10, pady=(6, 2))
            inner = ctk.CTkFrame(f, fg_color="transparent")
            inner.pack(fill="x", padx=8, pady=(0, 8))
            return inner

        def _field(parent, label, widget_fn, label_w=120):
            r = ctk.CTkFrame(parent, fg_color="transparent")
            r.pack(fill="x", pady=2)
            ctk.CTkLabel(r, text=label, font=_FS, text_color=UI_TEXT2,
                         width=label_w, anchor="e").pack(side="left", padx=(0, 6))
            w = widget_fn(r)
            if w: w.pack(side="left")
            return w

        def _om(parent, values, var, **kw):
            return ctk.CTkOptionMenu(parent, values=values, variable=var,
                                     width=kw.get("width", 190), height=26,
                                     font=_FS, **{k: v for k, v in kw.items()
                                                  if k not in ("width",)})

        # ══ COLUMNA IZQUIERDA ════════════════════════════════════════════════

        # ── Hoja y orientación ────────────────────────────────────────────────
        s1 = _section(left, "HOJA")
        _field(s1, "Tamaño de papel:", lambda p: _om(p, list(_HOJAS.keys()), v_hoja, width=220))
        _field(s1, "Orientación:",
               lambda p: ctk.CTkSegmentedButton(p, values=["Horizontal", "Vertical"],
                                                variable=v_orient, font=_FS, width=180))
        ctk.CTkCheckBox(s1, text="Plot upside-down", variable=v_upside,
                        font=_FSS).pack(anchor="w", padx=4, pady=(2, 0))

        # ── Plot Area ─────────────────────────────────────────────────────────
        s2 = _section(left, "ÁREA DE PLOT")
        _area_opts = ["Extents", "Display", "Window"]
        for opt in _area_opts:
            r = ctk.CTkFrame(s2, fg_color="transparent")
            r.pack(fill="x", pady=1)
            ctk.CTkRadioButton(r, text=opt, variable=v_area, value=opt,
                               font=_FS).pack(side="left", padx=4)
            if opt == "Window":
                ctk.CTkButton(r, text="Pick <", width=60, height=22,
                              fg_color=UI_CARD, hover_color=UI_ACC, font=_FSS,
                              command=lambda: _pick_window(dlg)
                              ).pack(side="left", padx=4)
                _lbl_win = ctk.CTkLabel(r, text="(no definida)", font=_FSS,
                                        text_color=UI_TEXT2)
                _lbl_win.pack(side="left")

        # ── Plot Scale ────────────────────────────────────────────────────────
        s3 = _section(left, "ESCALA DE PLOT")
        ctk.CTkCheckBox(s3, text="Fit to paper", variable=v_fit,
                        font=_FS, command=_on_fit).pack(anchor="w", padx=4, pady=(0, 4))
        sc_lbl_row = ctk.CTkFrame(s3, fg_color="transparent")
        sc_lbl_row.pack(fill="x", pady=2)
        ctk.CTkLabel(sc_lbl_row, text="Escala predefinida:", font=_FS,
                     text_color=UI_TEXT2, width=130, anchor="e").pack(side="left", padx=(0, 6))
        _om_escala = ctk.CTkOptionMenu(sc_lbl_row, values=_ESCALAS_PLOT,
                                       variable=v_escala, width=160, height=26,
                                       font=_FS, command=_on_escala)
        _om_escala.pack(side="left")
        sc_row = ctk.CTkFrame(s3, fg_color="transparent")
        sc_row.pack(fill="x", pady=2)
        ctk.CTkLabel(sc_row, text="", width=130).pack(side="left")
        _ent_mm = ctk.CTkEntry(sc_row, textvariable=v_escala_mm, width=44,
                               height=24, font=_FS)
        _ent_mm.pack(side="left")
        ctk.CTkLabel(sc_row, text=" mm =", font=_FS,
                     text_color=UI_TEXT2).pack(side="left")
        _ent_u = ctk.CTkEntry(sc_row, textvariable=v_escala_u, width=56,
                              height=24, font=_FS)
        _ent_u.pack(side="left", padx=(4, 0))
        ctk.CTkLabel(sc_row, text=" unidades", font=_FS,
                     text_color=UI_TEXT2).pack(side="left", padx=4)
        lbl_esc_res = ctk.CTkLabel(s3, text="= 1 : 100", font=_FSS,
                                   text_color="#60A5FA")
        lbl_esc_res.pack(anchor="e", padx=6)
        v_escala_mm.trace_add("write", _on_mm_u)
        v_escala_u.trace_add("write", _on_mm_u)

        # ── Plot Offset ───────────────────────────────────────────────────────
        s4 = _section(left, "OFFSET DE PLOT")
        ctk.CTkCheckBox(s4, text="Centrar el plot", variable=v_center,
                        font=_FS).pack(anchor="w", padx=4, pady=(0, 4))
        off_row = ctk.CTkFrame(s4, fg_color="transparent")
        off_row.pack(fill="x", pady=2)
        ctk.CTkLabel(off_row, text="X:", font=_FS,
                     text_color=UI_TEXT2, width=20).pack(side="left")
        ctk.CTkEntry(off_row, textvariable=v_off_x, width=60, height=24,
                     font=_FS).pack(side="left")
        ctk.CTkLabel(off_row, text=" mm    Y:", font=_FS,
                     text_color=UI_TEXT2).pack(side="left", padx=(4, 0))
        ctk.CTkEntry(off_row, textvariable=v_off_y, width=60, height=24,
                     font=_FS).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(off_row, text=" mm", font=_FS,
                     text_color=UI_TEXT2).pack(side="left")

        # ══ COLUMNA DERECHA ══════════════════════════════════════════════════

        # ── Plot Style ────────────────────────────────────────────────────────
        s5 = _section(right, "PLOT STYLE TABLE")
        for opt, tip in [("Color",       "Colores reales de capas"),
                         ("Monocromático", "Todo en negro"),
                         ("Escala grises", "Grises proporcionales")]:
            r = ctk.CTkFrame(s5, fg_color="transparent")
            r.pack(fill="x", pady=1)
            ctk.CTkRadioButton(r, text=opt, variable=v_estilo, value=opt,
                               font=_FS).pack(side="left", padx=4)
            ctk.CTkLabel(r, text=tip, font=_FSS,
                         text_color=UI_TEXT2).pack(side="left", padx=6)

        ctk.CTkLabel(s5, text="Resolución:", font=_FS,
                     text_color=UI_TEXT2).pack(anchor="w", padx=6, pady=(6, 0))
        _dpi_opts = {"72 DPI — borrador": 72, "150 DPI — estándar": 150,
                     "300 DPI — alta calidad": 300}
        _dpi_sv = tk.StringVar(value="150 DPI — estándar")
        ctk.CTkOptionMenu(s5, values=list(_dpi_opts.keys()),
                          variable=_dpi_sv, width=200, height=24, font=_FSS,
                          command=lambda v: v_dpi.set(_dpi_opts[v])
                          ).pack(anchor="w", padx=6, pady=(2, 0))

        # ── Capas ─────────────────────────────────────────────────────────────
        s6 = _section(right, "CAPAS A PLOTEAR")
        # Botones Todas / Ninguna
        btn_row = ctk.CTkFrame(s6, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(btn_row, text="Todas", width=70, height=22,
                      fg_color=UI_CARD, hover_color=UI_ACC, font=_FSS,
                      command=lambda: [v.set(True) for v in _capa_vars.values()]
                      ).pack(side="left", padx=2)
        ctk.CTkButton(btn_row, text="Ninguna", width=70, height=22,
                      fg_color=UI_CARD, hover_color="#7C3AED", font=_FSS,
                      command=lambda: [v.set(False) for v in _capa_vars.values()]
                      ).pack(side="left", padx=2)
        ctk.CTkButton(btn_row, text="Solo visibles", width=90, height=22,
                      fg_color=UI_CARD, hover_color=UI_ACC, font=_FSS,
                      command=lambda: [
                          _capa_vars[n].set(lyr.visible)
                          for n, lyr in self.layers.items()
                          if n in _capa_vars]
                      ).pack(side="left", padx=2)

        sf_capas = ctk.CTkScrollableFrame(s6, fg_color="transparent", height=130)
        sf_capas.pack(fill="x")
        for nombre, var in sorted(_capa_vars.items()):
            lyr = self.layers.get(nombre)
            row_c = ctk.CTkFrame(sf_capas, fg_color="transparent")
            row_c.pack(fill="x", pady=1)
            # Swatch de color
            col_hex = lyr.color if lyr and lyr.color.startswith("#") else "#FFFFFF"
            ctk.CTkFrame(row_c, width=12, height=12,
                         fg_color=col_hex, corner_radius=2
                         ).pack(side="left", padx=(2, 4))
            ctk.CTkCheckBox(row_c, text=nombre, variable=var,
                            font=_FSS, height=18,
                            checkbox_width=14, checkbox_height=14
                            ).pack(side="left")

        # ── Titleblock y anotaciones ──────────────────────────────────────────
        s7 = _section(right, "ANOTACIONES")
        ctk.CTkCheckBox(s7, text="Titleblock",    variable=v_tb,
                        font=_FS).pack(anchor="w", padx=4, pady=1)
        ctk.CTkCheckBox(s7, text="Norte",         variable=v_norte,
                        font=_FS).pack(anchor="w", padx=4, pady=1)
        ctk.CTkCheckBox(s7, text="Escala gráfica", variable=v_escgraf,
                        font=_FS).pack(anchor="w", padx=4, pady=1)

        pr = ctk.CTkFrame(s7, fg_color="transparent")
        pr.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(pr, text="Proyecto:", font=_FSS, text_color=UI_TEXT2,
                     width=60, anchor="e").pack(side="left", padx=(0, 4))
        ctk.CTkEntry(pr, textvariable=v_proyecto, width=200, height=24,
                     font=_FSS).pack(side="left")
        fr = ctk.CTkFrame(s7, fg_color="transparent")
        fr.pack(fill="x", pady=2)
        ctk.CTkLabel(fr, text="Firma:", font=_FSS, text_color=UI_TEXT2,
                     width=60, anchor="e").pack(side="left", padx=(0, 4))
        ctk.CTkEntry(fr, textvariable=v_firma, width=200, height=24,
                     font=_FSS).pack(side="left")

        # ── Botones de acción (se añaden al foot ya empacado arriba) ─────────
        # Preview thumbnail
        _preview_lbl = ctk.CTkLabel(foot, text="", width=120, height=80)
        _preview_lbl.pack(side="left", padx=10, pady=8)

        def _run_preview():
            try:
                img = self._plot_render(
                    hoja_mm=_HOJAS[v_hoja.get()],
                    orient=v_orient.get(),
                    escala_txt=v_escala.get(),
                    fit=v_fit.get(),
                    center=v_center.get(),
                    off_x=float(v_off_x.get() or 0),
                    off_y=float(v_off_y.get() or 0),
                    area=v_area.get(),
                    window_pts=_window_pts,
                    estilo=v_estilo.get(),
                    dpi=72,          # preview siempre a 72 DPI
                    capas_vis={n for n, v in _capa_vars.items() if v.get()},
                    tb=v_tb.get(), norte=v_norte.get(), escgraf=v_escgraf.get(),
                    upside=v_upside.get(),
                    proyecto=v_proyecto.get(), firma=v_firma.get(),
                    mm_papel=float(v_escala_mm.get() or 1),
                    u_modelo=float(v_escala_u.get() or 100),
                )
                # Escalar thumbnail
                thumb_w, thumb_h = 180, 120
                img_t = img.copy()
                img_t.thumbnail((thumb_w, thumb_h))
                ctk_img = ctk.CTkImage(light_image=img_t,
                                       size=(img_t.width, img_t.height))
                _preview_lbl.configure(image=ctk_img, text="")
                _preview_lbl._photo = ctk_img   # mantener referencia
            except Exception as ex:
                _preview_lbl.configure(text=f"Preview\nerror:\n{ex}",
                                       font=_FSS, text_color="red")

        def _do_plot():
            from tkinter import filedialog
            ruta = self._open_native_dialog(filedialog.asksaveasfilename,
                parent=dlg, title="Guardar PDF",
                defaultextension=".pdf",
                filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")])
            if not ruta:
                return
            _save_cfg()
            dlg.destroy()
            self._echo("Generando PDF…")
            self.root.update_idletasks()
            try:
                img = self._plot_render(
                    hoja_mm=_HOJAS[v_hoja.get()],
                    orient=v_orient.get(),
                    escala_txt=v_escala.get(),
                    fit=v_fit.get(),
                    center=v_center.get(),
                    off_x=float(v_off_x.get() or 0),
                    off_y=float(v_off_y.get() or 0),
                    area=v_area.get(),
                    window_pts=_window_pts,
                    estilo=v_estilo.get(),
                    dpi=v_dpi.get(),
                    capas_vis={n for n, v in _capa_vars.items() if v.get()},
                    tb=v_tb.get(), norte=v_norte.get(), escgraf=v_escgraf.get(),
                    upside=v_upside.get(),
                    proyecto=v_proyecto.get(), firma=v_firma.get(),
                    mm_papel=float(v_escala_mm.get() or 1),
                    u_modelo=float(v_escala_u.get() or 100),
                )
                img.save(ruta, "PDF", resolution=v_dpi.get())
                esc = v_escala.get()
                hoja = v_hoja.get().split("(")[0].strip()
                self._echo(f"PDF guardado: {os.path.basename(ruta)}  [{hoja} · {esc}]")
            except Exception as ex:
                import traceback; traceback.print_exc()
                messagebox.showerror("Error PDF", str(ex), parent=self.root)

        def _pick_window(parent_dlg):
            v_area.set("Window")
            self._echo("Seleccione esquina 1 de la ventana de plot…")
            parent_dlg.withdraw()
            self._plot_pick_window(
                callback=lambda pts: (
                    _window_pts.clear(),
                    _window_pts.extend(pts),
                    _lbl_win.configure(
                        text=f"({pts[0][0]:.1f},{pts[0][1]:.1f}) — ({pts[1][0]:.1f},{pts[1][1]:.1f})"
                        ) if len(pts) == 2 else None,
                    parent_dlg.deiconify()
                )
            )

        btn_frame = ctk.CTkFrame(foot, fg_color="transparent")
        btn_frame.pack(side="right", padx=10, pady=8)
        ctk.CTkButton(btn_frame, text="👁 Preview", width=110, height=36,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_F,
                      command=_run_preview).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="Cancelar", width=100, height=36,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_F,
                      command=dlg.destroy).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="🖨 Plot to PDF", width=130, height=36,
                      fg_color="#7C3AED", hover_color="#6D28D9",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=_do_plot).pack(side="left", padx=4)

        # Trigger preview automático al abrir
        dlg.after(300, _run_preview)
        self.root.after(100, dlg.grab_set)

    def _plot_pick_window(self, callback):
        """Permite al usuario hacer clic-arrastre en el canvas para definir el Window."""
        pts = []

        def _on_click(event):
            wx, wy = self.s2w(event.x, event.y)
            pts.append((wx, wy))
            if len(pts) == 1:
                self._echo("Seleccione esquina 2 de la ventana de plot…")
            elif len(pts) >= 2:
                self.canvas.unbind("<Button-1>", _cid1)
                self.canvas.unbind("<Button-3>", _cid3)
                callback(pts[:2])

        def _cancel(event):
            self.canvas.unbind("<Button-1>", _cid1)
            self.canvas.unbind("<Button-3>", _cid3)
            callback([])

        _cid1 = self.canvas.bind("<Button-1>", _on_click, add=True)
        _cid3 = self.canvas.bind("<Button-3>", _cancel, add=True)

    def _plot_render(self, *, hoja_mm, orient, escala_txt, fit, center,
                     off_x, off_y, area, window_pts, estilo, dpi,
                     capas_vis, tb, norte, escgraf, upside,
                     proyecto, firma, mm_papel=1.0, u_modelo=100.0):
        """
        Renderiza el plot a una PIL Image RGB lista para guardar como PDF.
        """
        import datetime
        from cad.renderer_pil import RendererPIL, RenderCtx
        from PIL import Image as _PI, ImageDraw as _PID, ImageFont as _PIF

        # ── Dimensiones de hoja ───────────────────────────────────────────────
        W_mm, H_mm = hoja_mm
        if orient == "Horizontal":
            W_mm, H_mm = max(W_mm, H_mm), min(W_mm, H_mm)
        else:
            W_mm, H_mm = min(W_mm, H_mm), max(W_mm, H_mm)

        TB_H_MM  = 25 if tb else 0
        MARG_MM  = 10
        W_px     = int(W_mm * dpi / 25.4)
        H_px     = int(H_mm * dpi / 25.4)
        tb_h_px  = int(TB_H_MM * dpi / 25.4)
        marg_px  = int(MARG_MM * dpi / 25.4)

        draw_W = W_px - 2 * marg_px
        draw_H = H_px - tb_h_px - 2 * marg_px

        # ── Entidades a plotear (filtro por capa) ─────────────────────────────
        ents_plot = [e for e in self.entities
                     if e.layer in capas_vis]

        # Área de plot
        if area == "Window" and len(window_pts) == 2:
            wx0 = min(window_pts[0][0], window_pts[1][0])
            wy0 = min(window_pts[0][1], window_pts[1][1])
            wx1 = max(window_pts[0][0], window_pts[1][0])
            wy1 = max(window_pts[0][1], window_pts[1][1])
            ents_plot = [e for e in ents_plot
                         if any(wx0 <= pt[0] <= wx1 and wy0 <= pt[1] <= wy1
                                for pt in (e.bbox_pts() or []))]
        elif area == "Display":
            vx0, vy0, vx1, vy1 = self._viewport_world()
            ents_plot = [e for e in ents_plot
                         if self._in_viewport(e, vx0, vy0, vx1, vy1)]

        # ── Calcular escala y offset ──────────────────────────────────────────
        from cad.viewport import zoom_to_fit as _ztf
        if fit or escala_txt == "Ajustar":
            sc, ox, oy = _ztf(ents_plot or self.entities,
                               draw_W, draw_H, padding=0.05)
        else:
            # mm_papel mm en papel = u_modelo unidades reales
            # 1 unidad real = (dpi/25.4) * (mm_papel/u_modelo) px
            sc = (dpi / 25.4) * (mm_papel / max(u_modelo, 0.001))
            # Centrar bbox del dibujo
            pts_all = [pt for e in (ents_plot or self.entities)
                       for pt in (e.bbox_pts() or [])]
            if pts_all:
                cx_w = (min(p[0] for p in pts_all) + max(p[0] for p in pts_all)) / 2
                cy_w = (min(p[1] for p in pts_all) + max(p[1] for p in pts_all)) / 2
            else:
                cx_w = cy_w = 0.0
            ox = marg_px + draw_W / 2 - cx_w * sc
            oy = marg_px + draw_H / 2 + cy_w * sc

        if center:
            # Ya centrado por defecto — off_x/off_y son adicionales
            pass
        ox += off_x * dpi / 25.4
        oy -= off_y * dpi / 25.4   # Y invertido

        # ── Capas para render: solo las visibles en el plot ───────────────────
        layers_plot = {n: lyr for n, lyr in self.layers.items()
                       if n in capas_vis}

        # ── Plot style: modificar colores ─────────────────────────────────────
        import copy as _copy
        if estilo == "Monocromático":
            layers_plot = {n: _copy.copy(lyr) for n, lyr in layers_plot.items()}
            for lyr in layers_plot.values():
                lyr.color = "#000000"
        elif estilo == "Escala grises":
            layers_plot = {n: _copy.copy(lyr) for n, lyr in layers_plot.items()}
            for lyr in layers_plot.values():
                if lyr.color.startswith("#"):
                    r = int(lyr.color[1:3], 16)
                    g = int(lyr.color[3:5], 16)
                    b = int(lyr.color[5:7], 16)
                    gray = int(0.299*r + 0.587*g + 0.114*b)
                    lyr.color = f"#{gray:02X}{gray:02X}{gray:02X}"

        # ── Render PIL ────────────────────────────────────────────────────────
        # Ocultar selección
        sel_bk = [(e, e.selected) for e in self.entities if e.selected]
        for e, _ in sel_bk:
            e.selected = False

        try:
            _plot_cfg = self._leer_config_ia()
            ctx = RenderCtx(
                W=draw_W, H=draw_H,
                scale=sc, offset_x=ox - marg_px, offset_y=oy - marg_px,
                entities=list(ents_plot),
                layers=layers_plot,
                block_defs=self.block_defs,
                entity_index=dict(self._entity_index),
                entity_cell=self._entity_cell,
                grid_on=False,
                bg_color="#FFFFFF",
                axis_color="#CCCCCC",      # ejes sutiles en PDF
                select_color="#000000",
                grid_major=self.grid_major,
                grid_minor=self.grid_minor,
                config=_plot_cfg,
            )
            img_draw = RendererPIL().render(ctx).image
        finally:
            for e, was in sel_bk:
                e.selected = was

        # ── Imagen completa ───────────────────────────────────────────────────
        bg = "#FFFFFF"
        img = _PI.new("RGB", (W_px, H_px), bg)
        img.paste(img_draw, (marg_px, marg_px))

        if upside:
            img = img.rotate(180)

        d = _PID.Draw(img)
        lw_m = max(2, int(dpi / 75))

        # Marco exterior
        d.rectangle([marg_px//2, marg_px//2,
                     W_px - marg_px//2, H_px - marg_px//2],
                    outline="#000000", width=lw_m)

        # ── Titleblock ────────────────────────────────────────────────────────
        if tb:
            y_tb = H_px - tb_h_px
            d.line([(0, y_tb), (W_px, y_tb)], fill="#000000", width=lw_m)

            try:
                fnt_big = _PIF.truetype("arial.ttf", int(dpi * 0.12))
                fnt_med = _PIF.truetype("arial.ttf", int(dpi * 0.08))
                fnt_sml = _PIF.truetype("arial.ttf", int(dpi * 0.06))
            except Exception:
                fnt_big = fnt_med = fnt_sml = _PIF.load_default()

            pad = int(dpi * 0.05)
            col_w = int(W_px * 0.35)
            x_col = W_px - col_w - pad
            d.line([(x_col, y_tb), (x_col, H_px)], fill="#000000", width=1)

            # Columna izquierda: nombre proyecto
            d.text((pad * 2, y_tb + pad),
                   proyecto.upper(), fill="#000000", font=fnt_big)

            # Columna derecha
            d.text((x_col + pad, y_tb + pad),
                   firma, fill="#000000", font=fnt_med)
            esc_label = (escala_txt if not fit else "Ajustada")
            d.text((x_col + pad, y_tb + pad + int(dpi * 0.10)),
                   f"Escala: {esc_label}", fill="#000000", font=fnt_sml)
            d.text((x_col + pad, y_tb + pad + int(dpi * 0.17)),
                   f"Fecha: {datetime.date.today().strftime('%d/%m/%Y')}",
                   fill="#000000", font=fnt_sml)

            # Segunda columna intermedia: dimensiones hoja + DPI
            x_mid = marg_px + int(draw_W * 0.55)
            d.line([(x_mid, y_tb), (x_mid, H_px)], fill="#CCCCCC", width=1)
            d.text((x_mid + pad, y_tb + pad),
                   f"{W_mm}×{H_mm} mm", fill="#555555", font=fnt_sml)
            d.text((x_mid + pad, y_tb + pad + int(dpi * 0.08)),
                   f"{dpi} DPI", fill="#555555", font=fnt_sml)

            # ── Escala gráfica ────────────────────────────────────────────────
            if escgraf and not fit:
                try:
                    seg_m  = max(0.001, u_modelo / mm_papel / 1000.0)
                    seg_px = int(sc * seg_m)
                    if seg_px > 5:
                        n_segs = 5
                        bar_H  = int(dpi * 0.04)
                        x_bar  = pad * 2
                        y_bar  = y_tb + int(tb_h_px * 0.6)
                        for i in range(n_segs):
                            fill = "#000000" if i % 2 == 0 else "#FFFFFF"
                            d.rectangle([x_bar + i*seg_px, y_bar,
                                         x_bar + (i+1)*seg_px, y_bar + bar_H],
                                        fill=fill, outline="#000000", width=1)
                        d.text((x_bar, y_bar - int(dpi * 0.05)),
                               "0", fill="#000000", font=fnt_sml)
                        d.text((x_bar + n_segs*seg_px + 2, y_bar - int(dpi * 0.05)),
                               f"{n_segs*seg_m:.0f}m", fill="#000000", font=fnt_sml)
                except Exception:
                    pass

        # ── Norte ─────────────────────────────────────────────────────────────
        if norte:
            n_r  = int(dpi * 0.15)
            n_cx = W_px - marg_px - n_r - int(dpi * 0.05)
            n_cy = marg_px + n_r + int(dpi * 0.05)
            d.ellipse([n_cx-n_r, n_cy-n_r, n_cx+n_r, n_cy+n_r],
                      outline="#000000", width=max(1, lw_m//2))
            d.polygon([(n_cx, n_cy - n_r + 2),
                       (n_cx - n_r//3, n_cy + n_r//2),
                       (n_cx, n_cy),
                       (n_cx + n_r//3, n_cy + n_r//2)],
                      fill="#000000")
            try:
                fnt_n = _PIF.truetype("arial.ttf", int(dpi * 0.09))
            except Exception:
                fnt_n = _PIF.load_default()
            d.text((n_cx - int(dpi * 0.03), n_cy - n_r - int(dpi * 0.1)),
                   "N", fill="#000000", font=fnt_n)

        return img

    def _exportar_pdf_rapido(self, *_):
        # Diálogo PDF simplificado (modelo → PDF sin layouts).
        # Para el flujo completo usar _plot_dialog().
        dlg = ctk.CTkToplevel(self.root)
        dlg.title("Exportar PDF — Rápido")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        w, h = 380, 520
        rx = self.root.winfo_x() + (self.root.winfo_width()  - w) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{rx}+{ry}")

        _F  = ctk.CTkFont(size=11)
        _FB = ctk.CTkFont(size=11, weight="bold")
        _FS = ctk.CTkFont(size=10)

        ctk.CTkLabel(dlg, text="Layout / PDF", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=UI_TEXT).pack(pady=(14, 4))

        def _card():
            f = ctk.CTkFrame(dlg, fg_color=UI_CARD, corner_radius=8)
            f.pack(fill="x", padx=16, pady=4)
            return f

        def _row(parent, label, widget_fn):
            r = ctk.CTkFrame(parent, fg_color="transparent")
            r.pack(fill="x", padx=10, pady=3)
            ctk.CTkLabel(r, text=label, font=_FS, text_color=UI_TEXT2,
                         width=110, anchor="e").pack(side="left", padx=(0, 8))
            w = widget_fn(r)
            w.pack(side="left", fill="x", expand=True)
            return w

        # Tamaño de hoja
        # Dimensiones en mm (ancho × alto) para orientación vertical
        _HOJAS = {
            "A4  (210×297 mm)":  (210, 297),
            "A3  (297×420 mm)":  (297, 420),
            "A2  (420×594 mm)":  (420, 594),
            "A1  (594×841 mm)":  (594, 841),
            "A0  (841×1189 mm)": (841, 1189),
        }
        _ESCALAS = ["1:50", "1:75", "1:100", "1:125", "1:150", "1:200",
                    "1:250", "1:500", "Ajustar"]

        c1 = _card()
        ctk.CTkLabel(c1, text="HOJA", font=_FB, text_color=UI_TEXT2
                     ).pack(anchor="w", padx=10, pady=(6, 2))
        hoja_var  = tk.StringVar(value="A3  (297×420 mm)")
        escala_var = tk.StringVar(value="1:100")
        orient_var = tk.StringVar(value="Horizontal")
        dpi_var    = tk.IntVar(value=150)

        _row(c1, "Tamaño:", lambda p: ctk.CTkOptionMenu(
            p, values=list(_HOJAS.keys()), variable=hoja_var, font=_FS, height=26))
        _row(c1, "Orientación:", lambda p: ctk.CTkOptionMenu(
            p, values=["Horizontal", "Vertical"], variable=orient_var, font=_FS, height=26))
        _row(c1, "Escala:", lambda p: ctk.CTkOptionMenu(
            p, values=_ESCALAS, variable=escala_var, font=_FS, height=26))
        _row(c1, "Resolución:", lambda p: ctk.CTkOptionMenu(
            p, values=["72 DPI (borrador)", "150 DPI (estándar)", "300 DPI (alta calidad)"],
            variable=tk.StringVar(value="150 DPI (estándar)"), font=_FS, height=26,
            command=lambda v: dpi_var.set(int(v.split()[0]))))

        # Titleblock
        c2 = _card()
        ctk.CTkLabel(c2, text="ROTULACIÓN", font=_FB, text_color=UI_TEXT2
                     ).pack(anchor="w", padx=10, pady=(6, 2))
        tb_var      = tk.BooleanVar(value=True)
        norte_var   = tk.BooleanVar(value=True)
        escgraf_var = tk.BooleanVar(value=True)

        cfg0 = self._leer_config_ia()
        proy_nombre = cfg0.get("proyecto_nombre", "")
        proy_var  = tk.StringVar(value=proy_nombre or "Proyecto")
        firma_var = tk.StringVar(value=cfg0.get("firma_pdf", "Estudio Merlos"))
        escala_txt = tk.StringVar(value="")   # se auto-llena al exportar

        ctk.CTkCheckBox(c2, text="Incluir titleblock", variable=tb_var,
                        font=_FS).pack(anchor="w", padx=10, pady=2)
        ctk.CTkCheckBox(c2, text="Incluir norte", variable=norte_var,
                        font=_FS).pack(anchor="w", padx=10, pady=2)
        ctk.CTkCheckBox(c2, text="Incluir escala gráfica", variable=escgraf_var,
                        font=_FS).pack(anchor="w", padx=10, pady=(2, 6))

        _row(c2, "Nombre proyecto:", lambda p: ctk.CTkEntry(
            p, textvariable=proy_var, height=24, font=_FS))
        _row(c2, "Firma / estudio:", lambda p: ctk.CTkEntry(
            p, textvariable=firma_var, height=24, font=_FS))

        _ok = [False]
        def _confirmar(): _ok[0] = True; dlg.destroy()

        btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_row.pack(pady=10)
        ctk.CTkButton(btn_row, text="Cancelar", width=110, height=32,
                      fg_color=UI_CARD, hover_color=UI_BORD, font=_F,
                      command=dlg.destroy).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="🖨 Exportar PDF", width=150, height=32,
                      fg_color="#7C3AED", hover_color="#6D28D9", font=_FB,
                      command=_confirmar).pack(side="left", padx=6)

        self.root.after(100, dlg.grab_set)
        dlg.wait_window()
        if not _ok[0]:
            return

        # Guardar firma en settings para próxima vez
        cfg0["firma_pdf"] = firma_var.get()
        import json as _json
        _path_cfg = self._cfg_path()
        os.makedirs(os.path.dirname(_path_cfg), exist_ok=True)
        with open(_path_cfg, "w", encoding="utf-8") as _f:
            _json.dump(cfg0, _f, indent=2)

        # ── Pedir ruta de guardado ─────────────────────────────────────────────
        ruta = self._open_native_dialog(filedialog.asksaveasfilename,
            parent=self.root, title="Guardar PDF",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")])
        if not ruta:
            return

        self._echo("Generando PDF…")
        self.root.update_idletasks()

        try:
            from cad.renderer_pil import RendererPIL, RenderCtx
            from PIL import Image as _PILImage, ImageDraw as _PILDraw, ImageFont as _PILFont

            # ── Calcular dimensiones en píxeles ───────────────────────────────
            dpi     = dpi_var.get()
            hoja_mm = _HOJAS[hoja_var.get()]
            if orient_var.get() == "Horizontal":
                hoja_mm = (hoja_mm[1], hoja_mm[0])   # rotar
            W_mm, H_mm = hoja_mm

            # Titleblock: franja inferior de 25mm
            TB_H_MM = 25 if tb_var.get() else 0
            MARGIN_MM = 10   # margen interior

            W_px = int(W_mm * dpi / 25.4)
            H_px = int(H_mm * dpi / 25.4)
            tb_h_px   = int(TB_H_MM * dpi / 25.4)
            margin_px = int(MARGIN_MM * dpi / 25.4)

            # Área de dibujo disponible
            draw_W = W_px - 2 * margin_px
            draw_H = H_px - tb_h_px - 2 * margin_px

            # ── Calcular escala ────────────────────────────────────────────────
            esc_txt = escala_var.get()
            if esc_txt == "Ajustar":
                # zoom to fit en el área disponible
                from cad.viewport import zoom_to_fit as _ztf
                sc, ox, oy = _ztf(
                    self.entities, draw_W, draw_H, padding=0.05)
                escala_num = 1000.0 / sc   # escala real (1:X)
                esc_txt = f"1:{int(round(escala_num))}"
            else:
                # 1:100 → sc = dpi / (100 * 25.4 / 1000) px/m
                factor_escala = int(esc_txt.split(":")[1])
                # 1 metro real = (dpi / 25.4 * 1000 / factor_escala) px
                sc = dpi / 25.4 * 1000.0 / factor_escala

            # Centrar el dibujo en el área disponible
            from cad.viewport import zoom_to_fit as _ztf2
            _, ox_fit, oy_fit = _ztf2(
                self.entities, draw_W, draw_H, padding=0.05)
            # Recalcular offsets con la escala elegida
            if len(self.entities) > 0:
                xs = [pt[0] for e in self.entities for pt in (e.bbox_pts() or [])]
                ys = [pt[1] for e in self.entities for pt in (e.bbox_pts() or [])]
                if xs:
                    cx_w = (min(xs) + max(xs)) / 2
                    cy_w = (min(ys) + max(ys)) / 2
                    ox = margin_px + draw_W / 2 - cx_w * sc
                    oy = margin_px + draw_H / 2 + cy_w * sc
                else:
                    ox = margin_px + draw_W / 2
                    oy = margin_px + draw_H / 2
            else:
                ox = margin_px + draw_W / 2
                oy = margin_px + draw_H / 2

            # ── Ocultar selección ──────────────────────────────────────────────
            sel_backup = [(e, e.selected) for e in self.entities if e.selected]
            for e, _ in sel_backup:
                e.selected = False

            # ── Render PIL ────────────────────────────────────────────────────
            ctx = RenderCtx(
                W=W_px, H=H_px - tb_h_px,
                scale=sc,
                offset_x=ox, offset_y=oy,
                entities=list(self.entities),
                layers=self.layers,
                block_defs=self.block_defs,
                entity_index={},
                entity_cell=1.0,
                grid_on=False,
                bg_color="#FFFFFF",
                select_color="#FFD700",
            )
            renderer = RendererPIL()
            img_draw = renderer.render(ctx).image

            # ── Crear imagen completa con titleblock ───────────────────────────
            img_full = _PILImage.new("RGB", (W_px, H_px), "#FFFFFF")
            img_full.paste(img_draw, (0, 0))

            draw = _PILDraw.Draw(img_full)

            # Marco exterior
            lw_marco = max(2, int(dpi / 75))
            draw.rectangle([margin_px//2, margin_px//2,
                            W_px - margin_px//2, H_px - margin_px//2],
                           outline="#000000", width=lw_marco)

            # Línea separadora titleblock
            if tb_var.get():
                y_tb = H_px - tb_h_px
                draw.line([(0, y_tb), (W_px, y_tb)], fill="#000000", width=lw_marco)

                # ── Titleblock ────────────────────────────────────────────────
                try:
                    fnt_big  = _PILFont.truetype("arial.ttf",  int(dpi * 0.12))
                    fnt_med  = _PILFont.truetype("arial.ttf",  int(dpi * 0.08))
                    fnt_sml  = _PILFont.truetype("arial.ttf",  int(dpi * 0.06))
                except Exception:
                    fnt_big = fnt_med = fnt_sml = _PILFont.load_default()

                pad = int(dpi * 0.05)
                # Columna derecha: firma / escala / fecha
                col_w = int(W_px * 0.35)
                x_col = W_px - col_w - pad

                # Líneas verticales del titleblock
                draw.line([(x_col, y_tb), (x_col, H_px)], fill="#000000", width=1)

                # Nombre del proyecto (izquierda)
                draw.text((pad * 2, y_tb + pad),
                          proy_var.get().upper(),
                          fill="#000000", font=fnt_big)

                # Firma (columna derecha, arriba)
                draw.text((x_col + pad, y_tb + pad),
                          firma_var.get(), fill="#000000", font=fnt_med)

                # Escala
                draw.text((x_col + pad, y_tb + pad + int(dpi * 0.1)),
                          f"Escala: {esc_txt}", fill="#000000", font=fnt_sml)

                # Fecha
                fecha = datetime.date.today().strftime("%d/%m/%Y")
                draw.text((x_col + pad, y_tb + pad + int(dpi * 0.17)),
                          f"Fecha: {fecha}", fill="#000000", font=fnt_sml)

                # ── Escala gráfica ────────────────────────────────────────────
                if escgraf_var.get() and esc_txt != "Ajustar":
                    try:
                        factor_eg = int(esc_txt.split(":")[1])
                        # Barra de 5 segmentos × 1m real cada uno
                        seg_m = 1.0
                        seg_px = int(sc * seg_m)
                        n_segs = 5
                        bar_W  = seg_px * n_segs
                        bar_H  = int(dpi * 0.04)
                        x_bar  = pad * 2
                        y_bar  = y_tb + tb_h_px - bar_H - pad * 2

                        for i in range(n_segs):
                            fill = "#000000" if i % 2 == 0 else "#FFFFFF"
                            draw.rectangle([x_bar + i*seg_px, y_bar,
                                            x_bar + (i+1)*seg_px, y_bar + bar_H],
                                           fill=fill, outline="#000000", width=1)
                        draw.text((x_bar, y_bar - int(dpi*0.05)),
                                  f"0", fill="#000000", font=fnt_sml)
                        draw.text((x_bar + bar_W - int(dpi*0.03), y_bar - int(dpi*0.05)),
                                  f"{n_segs}m", fill="#000000", font=fnt_sml)
                    except Exception:
                        pass

            # ── Norte (símbolo simple) ─────────────────────────────────────────
            if norte_var.get():
                n_r  = int(dpi * 0.15)
                n_cx = W_px - margin_px - n_r - int(dpi * 0.1)
                n_cy = margin_px + n_r + int(dpi * 0.1)
                draw.ellipse([n_cx - n_r, n_cy - n_r, n_cx + n_r, n_cy + n_r],
                             outline="#000000", width=max(1, lw_marco//2))
                # Flecha N
                draw.polygon([(n_cx, n_cy - n_r + 2),
                              (n_cx - n_r//3, n_cy + n_r//2),
                              (n_cx, n_cy),
                              (n_cx + n_r//3, n_cy + n_r//2)],
                             fill="#000000")
                try:
                    fnt_n = _PILFont.truetype("arial.ttf", int(dpi * 0.09))
                except Exception:
                    fnt_n = _PILFont.load_default()
                draw.text((n_cx - int(dpi*0.03), n_cy - n_r - int(dpi*0.1)),
                          "N", fill="#000000", font=fnt_n)

            # ── Guardar PDF ────────────────────────────────────────────────────
            img_full.save(ruta, "PDF", resolution=dpi)
            self._echo(f"PDF exportado: {os.path.basename(ruta)}  ({esc_txt}, {W_mm}×{H_mm}mm)")

        except Exception as ex:
            import traceback; traceback.print_exc()
            messagebox.showerror("Error PDF", str(ex), parent=self.root)
        finally:
            for e, was_sel in sel_backup if 'sel_backup' in dir() else []:
                e.selected = was_sel

    def _guardar_json(self, *_):
        """Ctrl+S — guarda en la ruta actual; si no existe pide nombre."""
        if self._current_ruta:
            self._escribir_json(self._current_ruta)
        else:
            self._guardar_json_como()

    def _guardar_json_como(self, *_):
        """SAVEAS — siempre pide nombre."""
        import json as _j
        ruta = self._open_native_dialog(filedialog.asksaveasfilename,
            parent=self.root, title="Guardar plano como…",
            defaultextension=".json",
            filetypes=[("JSON plano", "*.json")])
        if not ruta:
            return
        self._escribir_json(ruta)

    def _escribir_json(self, ruta: str):
        import json
        # Serializar capas
        layers_data = {}
        for n, l in self.layers.items():
            layers_data[n] = {
                "color": l.color, "linewidth": l.linewidth,
                "visible": l.visible, "locked": l.locked,
                "linetype": l.linetype,
            }
        data: dict = {
            "active_layer": self.active_layer,
            "layers": layers_data,
            "entities": [],
        }
        for e in self.entities:
            if isinstance(e, Line):
                data["entities"].append({"tipo":"line",
                    "x1":e.x1,"y1":e.y1,"x2":e.x2,"y2":e.y2,"layer":e.layer})
            elif isinstance(e, Polyline):
                data["entities"].append({"tipo":"polyline",
                    "points":e.points,"closed":e.closed,"layer":e.layer})
            elif isinstance(e, Circle):
                data["entities"].append({"tipo":"circle",
                    "cx":e.cx,"cy":e.cy,"radius":e.radius,"layer":e.layer})
            elif isinstance(e, Arc):
                data["entities"].append({"tipo":"arc",
                    "cx":e.cx,"cy":e.cy,"radius":e.radius,
                    "start_ang":e.start_ang,"end_ang":e.end_ang,
                    "ccw":e.ccw,"layer":e.layer})
            elif isinstance(e, Text):
                data["entities"].append({"tipo":"text",
                    "x":e.x,"y":e.y,"content":e.content,
                    "height":e.height,"angle":e.angle,"layer":e.layer})
            elif isinstance(e, Dimension):
                d = {"tipo":"dimension",
                     "p1":list(e.p1),"p2":list(e.p2),"pos":list(e.pos),
                     "dim_type":e.dim_type,"text_override":e.text_override,
                     "style":e.style,"layer":e.layer}
                if getattr(e, "text_pos", None) is not None:
                    d["text_pos"] = list(e.text_pos)
                if getattr(e, "rot_angle", None) is not None:
                    d["rot_angle"] = e.rot_angle
                if getattr(e, "no_ext", False):
                    d["no_ext"] = True
                data["entities"].append(d)
            elif isinstance(e, Hatch):
                data["entities"].append({"tipo":"hatch",
                    "boundary":list(e.boundary),"pattern":e.pattern,
                    "angle":e.angle,"scale":e.scale,"layer":e.layer,
                    "color":e.color})
            elif isinstance(e, Spline):
                data["entities"].append({"tipo":"spline",
                    "points":list(e.points),"closed":e.closed,"layer":e.layer})
            elif isinstance(e, Ellipse):
                data["entities"].append({"tipo":"ellipse",
                    "cx":e.cx,"cy":e.cy,"rx":e.rx,"ry":e.ry,
                    "angle":e.angle,"layer":e.layer})
            elif isinstance(e, XLine):
                data["entities"].append({"tipo":"xline",
                    "x1":e.x1,"y1":e.y1,"x2":e.x2,"y2":e.y2,"layer":e.layer})
            elif isinstance(e, Leader):
                data["entities"].append({"tipo":"leader",
                    "points":list(e.points),"text":e.text,
                    "arrow_size":e.arrow_size,"layer":e.layer})
        # ── Serializar layouts ────────────────────────────────────────
        data["layouts"] = [layout_to_dict(lay) for lay in self.layouts]

        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self._current_ruta = ruta
        self._dirty = False
        nombre = os.path.basename(ruta)
        if not getattr(self, "_escribir_json_silent", False):
            self.root.title(f"Estudio Merlos CAD — {nombre}")
            if hasattr(self, "_lbl_file"):
                self._lbl_file.configure(text=nombre)
            self._echo(f"Guardado: {nombre}")

    def _abrir_json(self):
        import json
        ruta = self._open_native_dialog(filedialog.askopenfilename,
            parent=self.root, title="Abrir plano",
            filetypes=[("JSON plano", "*.json"), ("Todos", "*.*")])
        if not ruta:
            return
        self._abrir_json_path(ruta)

    def _abrir_json_path(self, ruta: str):
        """Carga un archivo JSON en el dibujo actual (sin diálogo de archivo).
        Usado por Abrir, importación y restauración de recovery.

        El json.load() + loop de reconstrucción de entidades corren en un hilo
        secundario para no bloquear el hilo principal (~2 s en archivos grandes).
        """
        import json

        # ── Spinner de carga ──────────────────────────────────────────
        _spin_win = tk.Toplevel(self.root)
        _spin_win.title("")
        _spin_win.resizable(False, False)
        _spin_win.attributes("-topmost", True)
        _spin_win.overrideredirect(True)
        self.root.update_idletasks()
        pw = 260; ph = 64
        rx = self.root.winfo_x() + self.root.winfo_width()  // 2 - pw // 2
        ry = self.root.winfo_y() + self.root.winfo_height() // 2 - ph // 2
        _spin_win.geometry(f"{pw}x{ph}+{rx}+{ry}")
        _spin_win.configure(bg="#1A1A2E")
        tk.Label(_spin_win, text="⏳  Abriendo archivo…",
                 font=("Segoe UI", 13), fg="#A0A8C0", bg="#1A1A2E").pack(expand=True)
        # tk.Canvas indeterminate — sin CTkProgressBar._internal_loop
        _pbar = tk.Canvas(_spin_win, width=220, height=8, bg="#334155",
                          highlightthickness=0, bd=0)
        _pbar.create_rectangle(0, 0, 40, 8, fill="#2563EB", outline="", tags="fill")
        _pbar.pack(pady=(0, 10))
        _pbar_run = [True]
        def _tick(pos=[0]):
            if not _pbar_run[0]:
                return
            pos[0] = (pos[0] + 4) % 220
            x0 = pos[0]; x1 = min(x0 + 60, 220)
            _pbar.coords("fill", x0, 0, x1, 8)
            try: _spin_win.after(40, _tick)
            except Exception: pass
        _tick()
        try: _spin_win.update()
        except Exception: pass

        _resultado = [None]   # [resultado | excepción]
        _canvas_W = self.canvas.winfo_width()  or 800
        _canvas_H = self.canvas.winfo_height() or 600

        def _worker():
            """Corre en hilo secundario: I/O + reconstrucción de entidades."""
            try:
                with open(ruta, encoding="utf-8") as f:
                    data = json.load(f)

                # Capas
                new_layers = {}
                if "layers" in data:
                    for n, ld in data["layers"].items():
                        new_layers[n] = Layer(
                            name=n,
                            color=ld.get("color", "#FFFFFF"),
                            linewidth=ld.get("linewidth", 1),
                            visible=ld.get("visible", True),
                            locked=ld.get("locked", False),
                            linetype=ld.get("linetype", "CONTINUOUS"),
                        )

                # Entidades
                new_entities = []
                for ed in data.get("entities", []):
                    t = ed.get("tipo")
                    if t == "line":
                        new_entities.append(Line(
                            x1=ed["x1"],y1=ed["y1"],x2=ed["x2"],y2=ed["y2"],
                            layer=ed.get("layer","A-MURO")))
                    elif t == "polyline":
                        new_entities.append(Polyline(
                            points=ed["points"],closed=ed.get("closed",False),
                            layer=ed.get("layer","A-MURO")))
                    elif t == "circle":
                        new_entities.append(Circle(
                            cx=ed["cx"],cy=ed["cy"],radius=ed["radius"],
                            layer=ed.get("layer","A-MURO")))
                    elif t == "arc":
                        new_entities.append(Arc(
                            cx=ed["cx"],cy=ed["cy"],radius=ed["radius"],
                            start_ang=ed.get("start_ang",0.0),
                            end_ang=ed.get("end_ang",180.0),
                            ccw=ed.get("ccw",True),
                            layer=ed.get("layer","A-MURO")))
                    elif t == "text":
                        new_entities.append(Text(
                            x=ed["x"],y=ed["y"],content=ed["content"],
                            height=ed.get("height",0.20),
                            angle=ed.get("angle", 0.0),
                            layer=ed.get("layer","A-TEXTO")))
                    elif t == "dimension":
                        tp = ed.get("text_pos")
                        ra = ed.get("rot_angle")
                        new_entities.append(Dimension(
                            p1=tuple(ed["p1"]), p2=tuple(ed["p2"]),
                            pos=tuple(ed["pos"]),
                            dim_type=ed.get("dim_type","H"),
                            text_override=ed.get("text_override",""),
                            style=ed.get("style","Arq-50"),
                            layer=ed.get("layer","A-COTA"),
                            text_pos=tuple(tp) if tp else None,
                            rot_angle=float(ra) if ra is not None else None,
                            no_ext=bool(ed.get("no_ext", False))))
                    elif t == "hatch":
                        new_entities.append(Hatch(
                            boundary=[tuple(p) for p in ed["boundary"]],
                            pattern=ed.get("pattern","ANSI31"),
                            angle=ed.get("angle",45.0),
                            scale=ed.get("scale",1.0),
                            layer=ed.get("layer","A-HATCH"),
                            color=ed.get("color","bylayer")))
                    elif t == "spline":
                        new_entities.append(Spline(
                            points=[tuple(p) for p in ed["points"]],
                            closed=ed.get("closed",False),
                            layer=ed.get("layer","A-MURO")))
                    elif t == "ellipse":
                        new_entities.append(Ellipse(
                            cx=ed["cx"],cy=ed["cy"],
                            rx=ed.get("rx",1.0),ry=ed.get("ry",0.5),
                            angle=ed.get("angle",0.0),
                            layer=ed.get("layer","A-MURO")))
                    elif t == "xline":
                        new_entities.append(XLine(
                            x1=ed["x1"],y1=ed["y1"],
                            x2=ed["x2"],y2=ed["y2"],
                            layer=ed.get("layer","A-REFEREN")))
                    elif t == "leader":
                        new_entities.append(Leader(
                            points=[tuple(p) for p in ed["points"]],
                            text=ed.get("text",""),
                            arrow_size=ed.get("arrow_size",0.05),
                            layer=ed.get("layer","A-COTA")))

                # Viewport pre-calculado para no bloquear el hilo principal
                _vp_pre = None
                if new_entities:
                    try:
                        sc, ox, oy = _vp.zoom_to_fit(
                            new_entities, _canvas_W, _canvas_H, padding=0.10)
                        _vp_pre = (sc, ox, oy)
                    except Exception:
                        pass

                _resultado[0] = {
                    "layers":   new_layers,
                    "entities": new_entities,
                    "layouts":  data.get("layouts", []),
                    "active_layer": data.get("active_layer"),
                    "vp": _vp_pre,
                }
            except Exception as ex:
                _resultado[0] = ex

        _hilo = threading.Thread(target=_worker, daemon=True)
        _hilo.start()

        def _poll():
            if _hilo.is_alive():
                self.root.after(80, _poll)
                return
            # ── Hilo terminó: aplicar en el hilo principal ────────────
            _pbar_run[0] = False
            try: _pbar.destroy(); _spin_win.destroy()
            except Exception: pass

            res = _resultado[0]
            if isinstance(res, Exception):
                messagebox.showerror("Error", str(res), parent=self.root)
                return

            try:
                self._push_undo()
                self.entities.clear()
                self._grips = []; self._hot_grip = None; self._hover_grip = None
                self._grip_drag_mode = False
                self._op_mode = ""; self._op_pts = []; self._op_sel = []
                self._op_data = {}
                self.draw_pts.clear(); self._crs_ids = {}

                # Aplicar capas y entidades ya construidas
                self.layers.clear()
                self.layers.update(res["layers"])
                self.entities.extend(res["entities"])

                al = res.get("active_layer")
                if al and al in self.layers:
                    self.active_layer = al
                    self._om_capa_var.set(al)

                # Layouts
                self.layouts = [layout_from_dict(ld) for ld in res["layouts"]]
                self.active_layout_idx = -1
                self._saved_model_view = {}
                self._build_layout_tabs()

                self._rebuild_snap_index()
                self._refresh_om_capa(list(self.layers.keys()))
                self._layer_panel_pending = True   # panel diferido post-render

                # Viewport pre-calculado en el worker
                if res["vp"]:
                    self._save_zoom()
                    self.scale, self.offset_x, self.offset_y = res["vp"]
                    self._redraw()
                else:
                    self._zoom_extents()

                self._current_ruta = ruta
                self._dirty = False
                nombre = os.path.basename(ruta)
                self.root.title(f"Estudio Merlos CAD — {nombre}")
                if hasattr(self, "_lbl_file"):
                    self._lbl_file.configure(text=nombre)
            except Exception as ex:
                messagebox.showerror("Error al aplicar", str(ex), parent=self.root)

        self.root.after(80, _poll)

