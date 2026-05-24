"""
engine.py — Estudio Merlos CAD · Visor v1
==========================================
Comandos y alias compatibles con AutoCAD para cero curva de aprendizaje.

Render de alto rendimiento:
  • Capa "st" (estática)  → grid + entidades  (zoom/pan/cambio)
  • Capa "dy" (dinámica)  → cursor/preview/snap (cada movimiento)
  • Throttle 8 ms en _on_move · Índice espacial snap O(1) · Culling viewport

Dependencias: customtkinter, ezdxf (pip install ezdxf)
"""
from __future__ import annotations

import copy
import math
import os
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

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
_SNAP_CELL = 5.0


# ═══════════════════════════════════════════════════════════════════
# ENTIDADES
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Entity:
    layer: str = "A-MURO"
    color: str = "bylayer"
    selected: bool = field(default=False, repr=False)

    def snap_points(self) -> list:
        return []

    def bbox_pts(self) -> list:
        """Puntos para cálculo de bounding box (por defecto = snap_points)."""
        return self.snap_points()


@dataclass
class Line(Entity):
    x1: float = 0.0; y1: float = 0.0
    x2: float = 0.0; y2: float = 0.0

    def snap_points(self):
        return [
            (self.x1, self.y1, "end"),
            (self.x2, self.y2, "end"),
            ((self.x1+self.x2)/2, (self.y1+self.y2)/2, "mid"),
        ]

    def length(self):
        return math.hypot(self.x2-self.x1, self.y2-self.y1)

    def info(self):
        return f"LÍNEA  L={self.length():.3f} m  capa={self.layer}"

    def translated(self, dx, dy):
        return Line(x1=self.x1+dx, y1=self.y1+dy,
                    x2=self.x2+dx, y2=self.y2+dy, layer=self.layer)

    def rotated(self, cx, cy, deg):
        x1,y1 = _rotate_point(self.x1,self.y1,cx,cy,deg)
        x2,y2 = _rotate_point(self.x2,self.y2,cx,cy,deg)
        return Line(x1=x1,y1=y1,x2=x2,y2=y2,layer=self.layer)

    def scaled(self, cx, cy, f):
        return Line(x1=cx+(self.x1-cx)*f, y1=cy+(self.y1-cy)*f,
                    x2=cx+(self.x2-cx)*f, y2=cy+(self.y2-cy)*f, layer=self.layer)

    def mirrored(self, ax, ay, bx, by):
        x1,y1 = _mirror_point(self.x1,self.y1,ax,ay,bx,by)
        x2,y2 = _mirror_point(self.x2,self.y2,ax,ay,bx,by)
        return Line(x1=x1,y1=y1,x2=x2,y2=y2,layer=self.layer)


@dataclass
class Polyline(Entity):
    points: list = field(default_factory=list)
    closed: bool = False

    def snap_points(self):
        pts = [(x, y, "end") for x, y in self.points]
        n = len(self.points)
        for i in range(n-1):
            pts.append(((self.points[i][0]+self.points[i+1][0])/2,
                        (self.points[i][1]+self.points[i+1][1])/2, "mid"))
        if self.closed and n > 1:
            pts.append(((self.points[-1][0]+self.points[0][0])/2,
                        (self.points[-1][1]+self.points[0][1])/2, "mid"))
        # Centroide de polígono cerrado (mismo símbolo "cen" que círculos)
        if self.closed and n >= 3:
            cx = sum(p[0] for p in self.points) / n
            cy = sum(p[1] for p in self.points) / n
            pts.append((cx, cy, "cen"))
        return pts

    def perimeter(self):
        pts = self.points + ([self.points[0]] if self.closed and self.points else [])
        return sum(math.hypot(pts[i+1][0]-pts[i][0], pts[i+1][1]-pts[i][1])
                   for i in range(len(pts)-1))

    def area(self):
        if not self.closed or len(self.points) < 3:
            return 0.0
        n = len(self.points)
        return abs(sum(self.points[i][0]*self.points[(i+1)%n][1]
                       - self.points[(i+1)%n][0]*self.points[i][1]
                       for i in range(n))) / 2

    def info(self):
        extra = f"  A={self.area():.3f} m²" if self.closed else ""
        return f"POLILÍNEA  {len(self.points)} vért  P={self.perimeter():.3f} m{extra}  capa={self.layer}"

    def translated(self, dx, dy):
        return Polyline(points=[(x+dx, y+dy) for x,y in self.points],
                        closed=self.closed, layer=self.layer)

    def rotated(self, cx, cy, deg):
        return Polyline(points=[_rotate_point(x,y,cx,cy,deg) for x,y in self.points],
                        closed=self.closed, layer=self.layer)

    def scaled(self, cx, cy, f):
        return Polyline(points=[(cx+(x-cx)*f, cy+(y-cy)*f) for x,y in self.points],
                        closed=self.closed, layer=self.layer)

    def mirrored(self, ax, ay, bx, by):
        return Polyline(points=[_mirror_point(x,y,ax,ay,bx,by) for x,y in self.points],
                        closed=self.closed, layer=self.layer)


@dataclass
class Circle(Entity):
    cx: float = 0.0
    cy: float = 0.0
    radius: float = 1.0
    layer: str = "A-MURO"

    def snap_points(self):
        r = self.radius
        return [
            (self.cx,   self.cy,   "cen"),
            (self.cx+r, self.cy,   "qua"),
            (self.cx-r, self.cy,   "qua"),
            (self.cx,   self.cy+r, "qua"),
            (self.cx,   self.cy-r, "qua"),
        ]

    def info(self):
        return (f"CÍRCULO  r={self.radius:.3f} m  "
                f"A={math.pi*self.radius**2:.3f} m²  "
                f"P={2*math.pi*self.radius:.3f} m  capa={self.layer}")

    def translated(self, dx, dy):
        return Circle(cx=self.cx+dx, cy=self.cy+dy,
                      radius=self.radius, layer=self.layer)

    def rotated(self, cx, cy, deg):
        nx, ny = _rotate_point(self.cx, self.cy, cx, cy, deg)
        return Circle(cx=nx, cy=ny, radius=self.radius, layer=self.layer)

    def scaled(self, cx, cy, f):
        return Circle(cx=cx+(self.cx-cx)*f, cy=cy+(self.cy-cy)*f,
                      radius=self.radius*abs(f), layer=self.layer)

    def mirrored(self, ax, ay, bx, by):
        nx, ny = _mirror_point(self.cx, self.cy, ax, ay, bx, by)
        return Circle(cx=nx, cy=ny, radius=self.radius, layer=self.layer)


@dataclass
class Text(Entity):
    x: float = 0.0; y: float = 0.0
    content: str = ""
    height: float = 0.20
    angle: float = 0.0          # grados, CCW desde eje X (MIRRTEXT=0 por defecto)
    layer: str = "A-TEXTO"

    def snap_points(self):
        return [(self.x, self.y, "end")]

    def info(self):
        ang_str = f"  ang={self.angle:.1f}°" if self.angle else ""
        return f'TEXTO  "{self.content}"  h={self.height} m{ang_str}  capa={self.layer}'

    def translated(self, dx, dy):
        return Text(x=self.x+dx, y=self.y+dy, content=self.content,
                    height=self.height, angle=self.angle, layer=self.layer)

    def rotated(self, cx, cy, deg):
        nx, ny = _rotate_point(self.x, self.y, cx, cy, deg)
        return Text(x=nx, y=ny, content=self.content,
                    height=self.height, angle=(self.angle + deg) % 360, layer=self.layer)

    def scaled(self, cx, cy, f):
        return Text(x=cx+(self.x-cx)*f, y=cy+(self.y-cy)*f,
                    content=self.content, height=self.height*abs(f),
                    angle=self.angle, layer=self.layer)

    def mirrored(self, ax, ay, bx, by):
        nx, ny = _mirror_point(self.x, self.y, ax, ay, bx, by)
        # Reflejar el ángulo respecto al eje de espejo (MIRRTEXT=0 → texto legible)
        axis_ang = math.degrees(math.atan2(by-ay, bx-ax))
        new_angle = (2 * axis_ang - self.angle) % 360
        return Text(x=nx, y=ny, content=self.content,
                    height=self.height, angle=new_angle, layer=self.layer)


@dataclass
class Arc(Entity):
    """Arco de círculo definido por centro + radio + ángulos inicial/final."""
    cx: float = 0.0
    cy: float = 0.0
    radius: float = 1.0
    start_ang: float = 0.0    # grados, CCW desde este (eje X+), coords mundo
    end_ang: float = 180.0
    ccw: bool = True           # dirección: True=CCW, False=CW
    layer: str = "A-MURO"

    def snap_points(self):
        sa = math.radians(self.start_ang)
        ea = math.radians(self.end_ang)
        span = ((self.end_ang - self.start_ang) % 360 if self.ccw
                else (self.start_ang - self.end_ang) % 360)
        ma = math.radians(self.start_ang + (span/2 if self.ccw else -span/2))
        r = self.radius
        return [
            (self.cx + r*math.cos(sa), self.cy + r*math.sin(sa), "end"),
            (self.cx + r*math.cos(ea), self.cy + r*math.sin(ea), "end"),
            (self.cx + r*math.cos(ma), self.cy + r*math.sin(ma), "mid"),
            (self.cx, self.cy, "cen"),
        ]

    def info(self):
        span = ((self.end_ang - self.start_ang) % 360 if self.ccw
                else (self.start_ang - self.end_ang) % 360)
        L = math.radians(span) * self.radius
        return (f"ARCO  r={self.radius:.3f}m  span={span:.1f}deg  "
                f"L={L:.3f}m  capa={self.layer}")

    def translated(self, dx, dy):
        return Arc(cx=self.cx+dx, cy=self.cy+dy, radius=self.radius,
                   start_ang=self.start_ang, end_ang=self.end_ang,
                   ccw=self.ccw, layer=self.layer)

    def rotated(self, cx, cy, deg):
        ncx, ncy = _rotate_point(self.cx, self.cy, cx, cy, deg)
        return Arc(cx=ncx, cy=ncy, radius=self.radius,
                   start_ang=(self.start_ang+deg)%360,
                   end_ang=(self.end_ang+deg)%360,
                   ccw=self.ccw, layer=self.layer)

    def scaled(self, cx, cy, f):
        return Arc(cx=cx+(self.cx-cx)*f, cy=cy+(self.cy-cy)*f,
                   radius=self.radius*abs(f),
                   start_ang=self.start_ang, end_ang=self.end_ang,
                   ccw=self.ccw if f > 0 else not self.ccw, layer=self.layer)

    def mirrored(self, ax, ay, bx, by):
        ncx, ncy = _mirror_point(self.cx, self.cy, ax, ay, bx, by)
        axis_ang = math.degrees(math.atan2(by-ay, bx-ax))
        ns = (2*axis_ang - self.start_ang) % 360
        ne = (2*axis_ang - self.end_ang) % 360
        # al reflejar, los ángulos inicial/final se invierten y la dirección también
        return Arc(cx=ncx, cy=ncy, radius=self.radius,
                   start_ang=ne, end_ang=ns,
                   ccw=not self.ccw, layer=self.layer)

    def bbox_pts(self) -> list:
        """Bounding box real: incluye cruces de eje (E/N/W/S) dentro del arco."""
        pts = list(self.snap_points())
        r = self.radius
        span = ((self.end_ang - self.start_ang) % 360 if self.ccw
                else (self.start_ang - self.end_ang) % 360)
        for ang in (0.0, 90.0, 180.0, 270.0):
            if self.ccw:
                rel = (ang - self.start_ang) % 360
            else:
                rel = (self.start_ang - ang) % 360
            if rel <= span:
                pts.append((self.cx + r * math.cos(math.radians(ang)),
                             self.cy + r * math.sin(math.radians(ang)),
                             "qua"))
        return pts


# ═══════════════════════════════════════════════════════════════════
# CAPAS
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Layer:
    name: str
    color: str = "#FFFFFF"
    linewidth: int = 1
    visible: bool = True
    locked: bool = False
    linetype: str = "CONTINUOUS"


DEFAULT_LAYERS: dict[str, Layer] = {
    "0":          Layer("0",          "#FFFFFF", 1, linetype="CONTINUOUS"),
    "A-MURO":     Layer("A-MURO",     "#FFFFFF", 3, linetype="CONTINUOUS"),
    "A-TEXTO":    Layer("A-TEXTO",    "#FFFF00", 1, linetype="CONTINUOUS"),
    "A-COTA":     Layer("A-COTA",     "#00FFFF", 1, linetype="CONTINUOUS"),
    "A-PUERTA":   Layer("A-PUERTA",   "#00FF00", 2, linetype="CONTINUOUS"),
    "A-VENTANA":  Layer("A-VENTANA",  "#0080FF", 1, linetype="CONTINUOUS"),
    "A-EJE":      Layer("A-EJE",      "#FF0000", 1, linetype="CENTER"),
    "A-LOTE":     Layer("A-LOTE",     "#FF00FF", 4, linetype="CONTINUOUS"),
    "A-RETIRO":   Layer("A-RETIRO",   "#FFFF00", 1, linetype="DASHED"),
    "A-MUEBLE":   Layer("A-MUEBLE",   "#808080", 1, linetype="CONTINUOUS"),
}

# Patrones de dash para tk.Canvas: (relleno, vacío, …) en píxeles
# Scaled at render time relative to linewidth
_LINETYPES: dict[str, tuple] = {
    "CONTINUOUS": (),
    "DASHED":     (12, 6),
    "DOTTED":     (2,  6),
    "CENTER":     (24, 6, 4, 6),
    "DASHDOT":    (12, 6, 2, 6),
}
_LT_CYCLE = ["CONTINUOUS", "DASHED", "DOTTED", "CENTER", "DASHDOT"]
_LT_ABBR  = {
    "CONTINUOUS": "CONT",
    "DASHED":     "DASH",
    "DOTTED":     "DOT·",
    "CENTER":     "CTR",
    "DASHDOT":    "D·T",
}

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
    "X": "explode",       "EXPLODE": "explode",
    # Vista / Zoom
    "ZE": "zoom_e",       "ZA": "zoom_a",
    "Z": "zoom_cmd",      "ZOOM": "zoom_cmd",
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
    # Guardar / Abrir
    "SAVE": "save",       "GUARDAR": "save",     "QSAVE": "save",
    "SAVEAS": "saveas",   "GUARDARCOMO": "saveas",
    "OPEN": "open",       "ABRIR": "open",
    "NEW": "new_dwg",     "NUEVO": "new_dwg",
    # Export
    "DXF": "dxf",         "PNG": "png",
    # Config
    "GRID": "toggle_grid",   "GRILLA": "toggle_grid",
    "SNAP": "toggle_snap",
    "ORTHO": "toggle_ortho", "ORTO": "toggle_ortho",
    "OSNAP": "toggle_snap",
    # Ayuda
    "?": "help",          "HELP": "help",        "AYUDA": "help",
}


# ─── Descripciones para preview semántico de la barra ────────────
# Keyed by action value (el valor de _CMD_ALIASES), no por alias
_CMD_DESCRIPTIONS: dict[str, str] = {
    "line":          "LINE — dibuja segmentos; clic = punto, Enter = finalizar",
    "polyline":      "PLINE — polilínea continua; Enter = cerrar figura",
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
    "trim":          "TRIM — recorta en intersecciones; clic = segmento a cortar",
    "extend":        "EXTEND — extiende hasta borde; clic = entidad a extender",
    "explode":       "EXPLODE — descompone polilínea en segmentos individuales",
    "fillet":        "FILLET — empalme circular entre dos entidades",
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
}


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
    """Intersección de dos segmentos. Retorna (x, y) o None."""
    dx1, dy1 = x2 - x1, y2 - y1
    dx2, dy2 = x4 - x3, y4 - y3
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-12:
        return None
    t = ((x3 - x1) * dy2 - (y3 - y1) * dx2) / denom
    u = ((x3 - x1) * dy1 - (y3 - y1) * dx1) / denom
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (x1 + t * dx1, y1 + t * dy1)
    return None


def _intersect_seg_circle(x1, y1, x2, y2, cx, cy, r):
    """Puntos de intersección segmento–círculo (lista 0..2 pts)."""
    dx, dy = x2 - x1, y2 - y1
    fx, fy = x1 - cx, y1 - cy
    a = dx * dx + dy * dy
    if a < 1e-16:
        return []
    b = 2 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - r * r
    disc = b * b - 4 * a * c
    if disc < 0:
        return []
    sq = math.sqrt(disc)
    pts = []
    for t in ((-b - sq) / (2 * a), (-b + sq) / (2 * a)):
        if 0.0 <= t <= 1.0:
            pts.append((x1 + t * dx, y1 + t * dy))
    return pts


def _pt_on_arc(pt: tuple, arc) -> bool:
    """True si el punto (ya en la circunferencia) cae dentro del span del arco."""
    ang = math.degrees(math.atan2(pt[1] - arc.cy, pt[0] - arc.cx)) % 360
    span = ((arc.end_ang - arc.start_ang) % 360 if arc.ccw
            else (arc.start_ang - arc.end_ang) % 360)
    rel  = ((ang - arc.start_ang) % 360 if arc.ccw
            else (arc.start_ang - ang) % 360)
    return rel <= span


def _cell(v: float) -> int:
    """Celda de snap para coordenada v (usa floor para soportar negativos)."""
    return int(math.floor(v / _SNAP_CELL))


# ═══════════════════════════════════════════════════════════════════
# TOOLTIP LIGERO
# ═══════════════════════════════════════════════════════════════════

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
                pass
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
        self._pan_start: Optional[tuple] = None
        self._zoom_prev: list[tuple] = []   # historial de vistas para ZOOM P

        # Entidades y capas
        self.entities: list[Entity] = []
        self.layers: dict[str, Layer] = {
            k: Layer(**{f.name: getattr(v, f.name)
                        for f in v.__dataclass_fields__.values()})
            for k, v in DEFAULT_LAYERS.items()
        }
        self.active_layer = "A-MURO"

        # Herramienta activa y dibujo
        self.tool       = "select"
        self.draw_pts:  list[tuple] = []
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

        # Throttle de mouse
        self._move_pending = False
        self._mouse_sx = 0
        self._mouse_sy = 0

        # Índice espacial de snap
        self._snap_index: dict = {}

        # Último comando (para SPACE = repetir)
        self._last_cmd_name = ""

        # Operación en curso: move/copy/dist/zoom_window
        self._op_mode   = ""         # "" | "move_base" | "move_dest" | "copy_dest"
                                     #    | "rotate_base" | "rotate_angle"
                                     #    | "scale_base"  | "scale_factor"
                                     #    | "mirror_p1"   | "mirror_p2"
                                     #    | "offset_sel"
                                     #    | "trim_cut"    | "trim_obj"
                                     #    | "extend_bnd"  | "extend_obj"
                                     #    | "matchprop_src" | "matchprop_dst"
                                     #    | "dist_p1" | "dist_p2"
                                     #    | "zoom_w1" | "zoom_w2"
                                     #    | "laymcur"
        self._op_sel:   list = []    # entidades seleccionadas para operaciones
        self._op_pts:   list = []    # puntos acumulados para operación
        self._op_info   = ""         # texto de información para status
        self._op_data:  dict = {}    # datos auxiliares (base, eje, dist…)

        # Selección por arrastre
        self._drag_start_s: Optional[tuple] = None   # px de inicio del drag
        self._is_dragging   = False

        # Historial de comandos
        self._cmd_history: list[str] = []
        self._cmd_hist_idx = -1
        self._echo_after_id = None
        self._current_ruta: str = ""    # última ruta de guardado (para Save rápido)

        self._build_ui()
        self._center_view()
        self._push_undo()
        self._redraw()

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

    def run(self):
        self.root.mainloop()

    # ─── Construcción UI ───────────────────────────────────────────
    # ─── Menú de barra nativo ────────────────────────────────────

    def _build_menubar(self):
        """Menú de barra estándar Tk — acceso a todos los comandos."""
        def _ea(a): return lambda: self._ejecutar_accion(a, "")
        def _st(t): return lambda: self._set_tool(t)

        mb = tk.Menu(self.root, tearoff=0)
        self.root.config(menu=mb)

        # ── Archivo ──────────────────────────────────────────────
        fm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Archivo", menu=fm)
        fm.add_command(label="Nuevo                 Ctrl+N", command=self._new_dwg)
        fm.add_command(label="Abrir…                Ctrl+O", command=self._abrir_json)
        fm.add_separator()
        fm.add_command(label="Guardar               Ctrl+S", command=self._guardar_json)
        fm.add_command(label="Guardar como…",                command=self._guardar_json_como)
        fm.add_separator()
        fm.add_command(label="Exportar DXF…",               command=self._exportar_dxf)
        fm.add_command(label="Exportar PNG…",               command=self._exportar_png)
        fm.add_separator()
        fm.add_command(label="Salir",                        command=self.root.quit)

        # ── Editar ───────────────────────────────────────────────
        em = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Editar", menu=em)
        em.add_command(label="Deshacer              Ctrl+Z", command=self._undo)
        em.add_command(label="Rehacer               Ctrl+Y", command=self._redo)
        em.add_separator()
        em.add_command(label="Seleccionar todo      Ctrl+A", command=self._select_all)
        em.add_command(label="Borrar selección      E",      command=_ea("erase"))
        em.add_separator()
        for lbl, accion in [
            ("Mover                M",  "move"),
            ("Copiar               CO", "copy"),
            ("Rotar                RO", "rotate"),
            ("Escalar              SC", "scale"),
            ("Espejo               MI", "mirror"),
            ("Paralela             O",  "offset"),
        ]:
            em.add_command(label=lbl, command=_ea(accion))
        em.add_separator()
        for lbl, accion in [
            ("Recortar             TR", "trim"),
            ("Extender             EX", "extend"),
            ("Explotar             X",  "explode"),
            ("Copiar propiedades   MA", "matchprop"),
        ]:
            em.add_command(label=lbl, command=_ea(accion))

        # ── Dibujar ──────────────────────────────────────────────
        dm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Dibujar", menu=dm)
        for lbl, tool in [
            ("Seleccionar          S",   "select"),
            ("Línea                L",   "line"),
            ("Polilínea            PL",  "polyline"),
            ("Rectángulo           REC", "rect"),
            ("Círculo              C",   "circle"),
            ("Arco                 A",   "arc"),
            ("Texto                T",   "text"),
        ]:
            dm.add_command(label=lbl, command=_st(tool))

        # ── Ver ──────────────────────────────────────────────────
        vm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Ver", menu=vm)
        vm.add_command(label="Zoom extensión        ZE",      command=self._zoom_extents)
        vm.add_command(label="Zoom anterior         ZP",
                       command=lambda: self._ejecutar_accion("zoom", "P"))
        vm.add_separator()
        vm.add_command(label="SNAP on/off           F3",
                       command=lambda: self._toggle("snap"))
        vm.add_command(label="GRID on/off           F7",
                       command=lambda: self._toggle("grid"))
        vm.add_command(label="ORTHO on/off          F8",
                       command=lambda: self._toggle("ortho"))
        vm.add_separator()
        vm.add_command(label="Configuración  ⚙",             command=self._open_config)

        # ── Capas ────────────────────────────────────────────────
        lm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Capas", menu=lm)
        lm.add_command(label="Aislar capa activa    LAYISO",  command=self._layiso)
        lm.add_command(label="Mostrar todas         LAYON",   command=self._layon)
        lm.add_separator()
        lm.add_command(label="Nueva capa…",                   command=self._nueva_capa)

        # ── Medir ────────────────────────────────────────────────
        mm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Medir", menu=mm)
        mm.add_command(label="Distancia             DI",      command=_ea("dist"))
        mm.add_command(label="Listar entidad        LI",      command=_ea("list_ent"))
        mm.add_command(label="Área                  AREA",    command=_ea("area_cmd"))

        # ── IA ───────────────────────────────────────────────────
        iam = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="🤖 IA", menu=iam)
        iam.add_command(label="Abrir asistente       /",      command=self._focus_ia_input)
        iam.add_separator()
        iam.add_command(label="Configurar proveedor  ⚙",      command=self._open_config)

        # ── Ayuda ────────────────────────────────────────────────
        hm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Ayuda", menu=hm)
        hm.add_command(label="Atajos de teclado     F1",      command=self._mostrar_ayuda)

    def _build_ui(self):
        self._tool_btns: dict[str, ctk.CTkButton] = {}
        self._build_menubar()   # menú nativo antes de cualquier widget

        # ── Barra de archivo + undo/redo (top, ancho completo) ────
        topbar = ctk.CTkFrame(self.root, fg_color="#111827", height=32, corner_radius=0)
        topbar.pack(side="top", fill="x")   # PRIMERO → abarca todo el ancho
        topbar.pack_propagate(False)

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
        _tb_btn(topbar, "  Nuevo  ",      self._new_dwg,          width=70)
        _tb_btn(topbar, "  Abrir  ",      self._abrir_json,       width=70)
        _tb_btn(topbar, "  Guardar  ",    self._guardar_json,     fg=UI_SUCC, hover="#15803D", width=80)
        _tb_btn(topbar, "  Guardar…  ",   self._guardar_json_como, width=90)
        _sep(topbar)
        _tb_btn(topbar, "  DXF  ",        self._exportar_dxf,     width=60)
        _tb_btn(topbar, "  PNG  ",        self._exportar_png,     width=60)
        _sep(topbar)

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

        # ── Toolbar izquierda ──────────────────────────────────────
        toolbar = ctk.CTkFrame(self.root, fg_color=UI_PAN, width=60, corner_radius=0)
        toolbar.pack(side="left", fill="y")
        toolbar.pack_propagate(False)
        ctk.CTkLabel(toolbar, text="EM\nCAD",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=UI_ACC).pack(pady=(12, 10))

        # ── Herramientas de dibujo ─────────────────────────────────
        _TOOLS = [
            # (icono, tooltip-completo, tool-name, font-size)
            ("↖",  "Seleccionar  [S]",    "select",   16),
            ("—",  "Línea  [L]",          "line",     18),
            ("⌐",  "Polilínea  [PL]",     "polyline", 15),
            ("□",  "Rectángulo  [REC]",   "rect",     15),
            ("○",  "Círculo  [C]",        "circle",   15),
            ("∩",  "Arco  [A]",           "arc",      16),
            ("T",  "Texto  [T]",          "text",     16),
        ]
        for icon, tip, tname, fsz in _TOOLS:
            b = ctk.CTkButton(
                toolbar, text=icon, width=44, height=38,
                fg_color=UI_ACC if tname == "select" else UI_CARD,
                hover_color=UI_ACC, corner_radius=8,
                font=ctk.CTkFont(size=fsz),
                command=lambda t=tname: self._set_tool(t),
            )
            b.pack(pady=2)
            self._tool_btns[tname] = b
            _Tooltip(b, tip)

        ctk.CTkFrame(toolbar, height=1, fg_color=UI_BORD).pack(fill="x", padx=6, pady=6)

        # ── Operaciones de edición ─────────────────────────────────
        ctk.CTkLabel(toolbar, text="EDIT",
                     font=ctk.CTkFont(size=8, weight="bold"),
                     text_color=UI_TEXT2).pack(pady=(0, 1))

        _EDIT_OPS = [
            ("E",   "#DC2626", "erase",     "Borrar selección  [E]"),
            ("M",   "#7C3AED", "move",      "Mover  [M]"),
            ("CO",  "#7C3AED", "copy",      "Copiar  [CO]"),
            ("RO",  "#7C3AED", "rotate",    "Rotar  [RO]"),
            ("SC",  "#7C3AED", "scale",     "Escalar  [SC]"),
            ("MI",  "#7C3AED", "mirror",    "Espejo  [MI]"),
            ("OF",  "#7C3AED", "offset",    "Paralela  [O]"),
            ("TR",  "#0F766E", "trim",      "Recortar  [TR]"),
            ("EX",  "#0F766E", "extend",    "Extender  [EX]"),
            ("X",   "#0F766E", "explode",   "Explotar PL  [X]"),
            ("MA",  "#0F766E", "matchprop", "Copiar props  [MA]"),
        ]
        self._op_btns: dict[str, ctk.CTkButton] = {}
        for lbl, hov, accion, tip in _EDIT_OPS:
            b = ctk.CTkButton(
                toolbar, text=lbl, width=44, height=30,
                fg_color=UI_CARD, hover_color=hov,
                corner_radius=8,
                font=ctk.CTkFont(size=11, weight="bold"),
                command=lambda a=accion: self._ejecutar_accion(a, ""),
            )
            b.pack(pady=2)
            self._op_btns[accion] = b
            _Tooltip(b, tip)

        ctk.CTkFrame(toolbar, height=1, fg_color=UI_BORD).pack(fill="x", padx=6, pady=6)

        # ── Vista ──────────────────────────────────────────────────
        _ze = ctk.CTkButton(toolbar, text="⊡", width=40, height=34,
                            fg_color=UI_CARD, hover_color=UI_ACC, corner_radius=8,
                            font=ctk.CTkFont(size=15),
                            command=self._zoom_extents)
        _ze.pack(pady=2)
        _Tooltip(_ze, "Zoom extensión  [ZE]")

        ctk.CTkFrame(toolbar, height=1, fg_color=UI_BORD).pack(fill="x", padx=6, pady=6)

        # ── Medición ───────────────────────────────────────────────
        ctk.CTkLabel(toolbar, text="MEDIR",
                     font=ctk.CTkFont(size=8, weight="bold"),
                     text_color=UI_TEXT2).pack(pady=(0, 1))

        _MEDIR = [
            ("DI",   "dist",     "Distancia  [DI]",          "#0F766E"),
            ("LI",   "list_ent", "Listar entidad  [LI]",     "#0F766E"),
            ("AREA", "area_cmd", "Área cerrada  [AREA]",     "#0F766E"),
        ]
        for lbl, accion, tip, hov in _MEDIR:
            b = ctk.CTkButton(
                toolbar, text=lbl, width=44, height=28,
                fg_color=UI_CARD, hover_color=hov,
                corner_radius=8,
                font=ctk.CTkFont(size=9, weight="bold"),
                command=lambda a=accion: self._ejecutar_accion(a, ""),
            )
            b.pack(pady=2)
            _Tooltip(b, tip)

        # Panel derecho
        right = ctk.CTkFrame(self.root, fg_color=UI_PAN, width=220, corner_radius=0)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        # ── Encabezado CAPAS + acciones rápidas ───────────────────
        _lhdr = ctk.CTkFrame(right, fg_color="transparent")
        _lhdr.pack(fill="x", padx=10, pady=(12, 2))
        ctk.CTkLabel(_lhdr, text="CAPAS",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=UI_TEXT2).pack(side="left")

        # LAYON — mostrar todas las capas
        _btn_layon = ctk.CTkButton(
            _lhdr, text="ALL", width=36, height=18,
            fg_color=UI_CARD, hover_color=UI_ACC,
            text_color=UI_TEXT2, corner_radius=3,
            font=ctk.CTkFont(family="Courier New", size=8, weight="bold"),
            command=self._layon)
        _btn_layon.pack(side="right", padx=(2, 0))
        _Tooltip(_btn_layon, "Mostrar todas  [LAYON]")

        # LAYISO — aislar capa activa
        _btn_layiso = ctk.CTkButton(
            _lhdr, text="ISO", width=36, height=18,
            fg_color=UI_CARD, hover_color="#7C3AED",
            text_color=UI_TEXT2, corner_radius=3,
            font=ctk.CTkFont(family="Courier New", size=8, weight="bold"),
            command=self._layiso)
        _btn_layiso.pack(side="right", padx=2)
        _Tooltip(_btn_layiso, "Aislar capa activa  [LAYISO]")

        # ── Filtro de búsqueda de capas ────────────────────────────
        self._layer_filter_var = tk.StringVar()
        self._layer_filter_var.trace_add("write", lambda *_: self._build_layer_panel())
        self._layer_filter_entry = ctk.CTkEntry(
            right, textvariable=self._layer_filter_var,
            placeholder_text="Buscar capa…", height=22,
            font=ctk.CTkFont(size=10))
        self._layer_filter_entry.pack(fill="x", padx=10, pady=(0, 4))

        self._layer_frame = ctk.CTkScrollableFrame(right, fg_color="transparent", height=260)
        self._layer_frame.pack(fill="x", padx=6)
        self._build_layer_panel()

        ctk.CTkFrame(right, height=1, fg_color=UI_BORD).pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(right, text="PROPIEDADES",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=UI_TEXT2).pack(anchor="w", padx=10)
        self._lbl_prop = ctk.CTkLabel(
            right, text="—\nNinguna entidad\nseleccionada",
            font=ctk.CTkFont(family="Courier New", size=10),
            text_color=UI_TEXT2, justify="left", anchor="nw", wraplength=200)
        self._lbl_prop.pack(anchor="w", padx=10, pady=(4, 0))

        # Selector de capa directo para la entidad seleccionada
        ctk.CTkLabel(right, text="Capa de la selección:",
                     font=ctk.CTkFont(size=9), text_color=UI_TEXT2).pack(
                         anchor="w", padx=10, pady=(4, 0))
        self._prop_layer_var = tk.StringVar(value="—")
        self._prop_layer_om = ctk.CTkOptionMenu(
            right, values=["—"],
            variable=self._prop_layer_var,
            width=200, height=24, font=ctk.CTkFont(size=10),
            command=self._prop_cambiar_capa)
        self._prop_layer_om.pack(padx=10, pady=(2, 0), fill="x")

        ctk.CTkButton(right, text="→ Mover a capa activa", height=26,
                      fg_color=UI_CARD, hover_color=UI_ACC,
                      font=ctk.CTkFont(size=10), corner_radius=6,
                      command=self._cambiar_capa_sel).pack(padx=10, pady=(4, 0), fill="x")

        ctk.CTkFrame(right, height=1, fg_color=UI_BORD).pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(right, text="Capa activa:",
                     font=ctk.CTkFont(size=10), text_color=UI_TEXT2).pack(anchor="w", padx=10)
        self._om_capa = ctk.CTkOptionMenu(
            right, values=list(self.layers.keys()),
            width=200, height=28, font=ctk.CTkFont(size=11),
            command=self._activar_capa)
        self._om_capa.set(self.active_layer)
        self._om_capa.pack(padx=10, pady=4)

        ctk.CTkFrame(right, height=1, fg_color=UI_BORD).pack(fill="x", padx=8, pady=6)
        ctk.CTkLabel(right,
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

        # Área central
        center = ctk.CTkFrame(self.root, fg_color="transparent", corner_radius=0)
        center.pack(side="left", fill="both", expand=True)
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(center, bg=CV_BG, cursor="none",
                                highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        # Barra de estado
        sb = ctk.CTkFrame(center, fg_color=UI_PAN, height=24, corner_radius=0)
        sb.grid(row=1, column=0, sticky="ew")
        # Empaquetar _lbl_tool primero (right) para que nunca sea tapado
        self._lbl_tool = ctk.CTkLabel(sb, text="SELECCIONAR [S]",
                                      font=ctk.CTkFont(family="Courier New", size=10),
                                      text_color=UI_ACC)
        self._lbl_tool.pack(side="right", padx=10)
        self._lbl_coords = ctk.CTkLabel(sb, text="X: 0.000  Y: 0.000",
                                        font=ctk.CTkFont(family="Courier New", size=10),
                                        text_color=UI_TEXT2, anchor="w")
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
        self._lbl_op = ctk.CTkLabel(sb, text="",
                                    font=ctk.CTkFont(family="Courier New", size=10),
                                    text_color=UI_WARN, wraplength=0)
        self._lbl_op.pack(side="left", padx=6, fill="x", expand=True)

        # ── Línea de comando expandible con historial ─────────────────
        self._cmd_expanded = False
        cmd_outer = ctk.CTkFrame(center, fg_color="#0D1117", corner_radius=0)
        cmd_outer.grid(row=2, column=0, sticky="ew")

        # Panel de historial (oculto por defecto, se muestra con ▲)
        self._hist_frame = tk.Frame(cmd_outer, bg="#080C10", height=110)
        self._hist_txt = tk.Text(
            self._hist_frame,
            bg="#080C10", fg=CV_CMD_FG,
            font=("Courier New", 10), relief="flat", bd=0,
            state="disabled", wrap="word", height=7,
            selectbackground="#1E293B", exportselection=False)
        self._hist_txt.pack(fill="both", expand=True, padx=6, pady=(4, 2))
        self._hist_txt.tag_configure("cad",  foreground=CV_CMD_FG)
        self._hist_txt.tag_configure("ai",   foreground="#38BDF8")
        self._hist_txt.tag_configure("resp", foreground=UI_WARN)
        self._hist_txt.tag_configure("err",  foreground=UI_ERR)
        self._hist_txt.tag_configure("sys",  foreground=UI_TEXT2)

        # Línea de input (siempre visible) — se empaqueta primero para quedar al fondo
        cf = ctk.CTkFrame(cmd_outer, fg_color="#0D1117", height=32, corner_radius=0)
        cf.pack(side="bottom", fill="x")

        # Hint semántico — justo encima del input, se actualiza al tipear
        self._cmd_hint_lbl = tk.Label(
            cmd_outer,
            text="", anchor="w",
            bg="#0D1117", fg="#475569",
            font=("Courier New", 9),
            padx=12, pady=1)
        self._cmd_hint_lbl.pack(side="bottom", fill="x")

        # Botón toggle historial
        self._btn_hist = ctk.CTkButton(
            cf, text="▲", width=26, height=24,
            fg_color="transparent", hover_color=UI_PAN,
            font=ctk.CTkFont(size=10), corner_radius=4,
            command=self._toggle_history)
        self._btn_hist.pack(side="right", padx=(0, 6), pady=4)

        # Prompt label — cambia color según modo (CAD verde / IA azul)
        self._cmd_prompt_lbl = ctk.CTkLabel(
            cf, text=">",
            font=ctk.CTkFont(family="Courier New", size=12, weight="bold"),
            text_color=CV_CMD_FG)
        self._cmd_prompt_lbl.pack(side="left", padx=(8, 2))

        self._cmd_var = tk.StringVar()
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
        # Estado de autocompletado
        self._ac_prefix: str = ""
        self._ac_matches: list = []
        self._ac_idx: int = 0

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
        cv.bind("<Delete>",           lambda e: self._erase())
        cv.bind("<BackSpace>",        lambda e: self._on_canvas_backspace())
        cv.bind("<space>",            lambda e: self._repeat_last())
        cv.bind("<Return>",           lambda e: self._on_canvas_return())
        cv.bind("<KP_Enter>",         lambda e: self._on_canvas_return())
        cv.bind("<Escape>",           lambda e: self._cancelar())
        cv.bind("<Tab>",              lambda e: self._on_canvas_tab() or "break")
        cv.bind("<Key>",              self._on_canvas_key)

        # Teclas de herramienta directas en canvas
        for key, tool in [("l","line"),("L","line"),("p","polyline"),("P","polyline"),
                          ("r","rect"),("R","rect"),("c","circle"),("C","circle"),
                          ("a","arc"),("A","arc"),
                          ("t","text"),("T","text"),("s","select"),("S","select")]:
            cv.bind(f"<{key}>", lambda e, t=tool: self._set_tool(t))
        cv.bind("<c>", lambda e: (self._cerrar_pline()
                                  if self.tool == "polyline" and self.draw_pts
                                  else self._set_tool("circle")))
        cv.bind("<C>", lambda e: (self._cerrar_pline()
                                  if self.tool == "polyline" and self.draw_pts
                                  else self._set_tool("circle")))
        cv.bind("<u>", lambda e: (self._undo_punto()
                                  if self.draw_pts else self._undo()))
        cv.bind("<U>", lambda e: self._undo())

    def _build_layer_panel(self):
        for w in self._layer_frame.winfo_children():
            w.destroy()

        # Filtro de búsqueda
        filtro = ""
        try:
            filtro = self._layer_filter_var.get().strip().upper()
        except Exception:
            pass

        for name, lyr in list(self.layers.items()):
            if filtro and filtro not in name.upper():
                continue
            is_active = name == self.active_layer

            # ── Tarjeta de capa: fondo sutilmente diferenciado ─────
            card = ctk.CTkFrame(
                self._layer_frame,
                fg_color=UI_CARD if is_active else "transparent",
                corner_radius=4)
            card.pack(fill="x", pady=1, padx=2)

            # ── Fila 1: [vis] [lock] [color] [nombre] ─────────────
            row1 = ctk.CTkFrame(card, fg_color="transparent")
            row1.pack(fill="x")

            # Visible checkbox
            vis_var = tk.BooleanVar(value=lyr.visible)
            def _tv(v=vis_var, l=lyr):
                l.visible = v.get(); self._redraw_static()
            ctk.CTkCheckBox(row1, variable=vis_var, text="", width=20,
                            checkbox_width=14, checkbox_height=14,
                            command=_tv).pack(side="left")

            # Lock toggle — "■" bloqueado / "□" libre
            lock_lbl = ctk.CTkLabel(
                row1, text="■" if lyr.locked else "□",
                font=ctk.CTkFont(size=11),
                text_color="#FF8C00" if lyr.locked else UI_TEXT2,
                cursor="hand2", width=16)
            lock_lbl.pack(side="left", padx=1)
            def _toggle_lock(l=lyr, lb=lock_lbl):
                l.locked = not l.locked
                lb.configure(
                    text="■" if l.locked else "□",
                    text_color="#FF8C00" if l.locked else UI_TEXT2)
                self._redraw_static()
            lock_lbl.bind("<Button-1>", lambda _e, fn=_toggle_lock: fn())

            # Color dot
            dot = tk.Canvas(row1, width=14, height=14, bg=lyr.color,
                            highlightthickness=1, highlightbackground=UI_BORD,
                            cursor="hand2")
            dot.pack(side="left", padx=2)
            def _pick_color(l=lyr, d=dot):
                from tkinter import colorchooser as _cc
                res = _cc.askcolor(color=l.color,
                                   title=f"Color — {l.name}",
                                   parent=self.root)
                if res and res[1]:
                    l.color = res[1].upper()
                    d.configure(bg=l.color)
                    self._redraw_static()
            dot.bind("<Button-1>", lambda _e, fn=_pick_color: fn())

            # Nombre capa
            lbl = ctk.CTkLabel(
                row1, text=name,
                font=ctk.CTkFont(size=10, weight="bold" if is_active else "normal"),
                text_color=UI_TEXT if is_active else UI_TEXT2,
                anchor="w", cursor="hand2", width=70)
            lbl.pack(side="left", padx=2)
            lbl.bind("<Button-1>", lambda _e, n=name: self._activar_capa(n))

            # Botón eliminar (no para "0" ni capa activa)
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
                    self._om_capa.configure(values=list(self.layers.keys()))
                    self._build_layer_panel()
                ctk.CTkButton(row1, text="×", width=18, height=18,
                              fg_color=UI_CARD, hover_color=UI_ERR,
                              text_color=UI_TEXT2, corner_radius=3,
                              font=ctk.CTkFont(size=10),
                              command=_del).pack(side="right", padx=2)

            # ── Fila 2: [linetype] [lw 1..5] ──────────────────────
            row2 = ctk.CTkFrame(card, fg_color="transparent")
            row2.pack(fill="x", pady=(0, 2))

            # Linetype cycle button
            lt_abbr = _LT_ABBR.get(lyr.linetype, lyr.linetype[:4])
            lt_btn = ctk.CTkButton(
                row2, text=lt_abbr, width=38, height=16,
                fg_color="#2A3A4A", hover_color="#3A4A5A",
                corner_radius=3, font=ctk.CTkFont(size=8))
            lt_btn.pack(side="left", padx=(20, 2))
            def _cycle_lt(l=lyr, btn=lt_btn):
                idx = _LT_CYCLE.index(l.linetype) if l.linetype in _LT_CYCLE else 0
                l.linetype = _LT_CYCLE[(idx + 1) % len(_LT_CYCLE)]
                btn.configure(text=_LT_ABBR.get(l.linetype, l.linetype[:4]))
                self._redraw_static()
            lt_btn.configure(command=_cycle_lt)

            # Botones de grosor 1-5
            lw_frame = ctk.CTkFrame(row2, fg_color="transparent")
            lw_frame.pack(side="left", padx=2)
            for lw_val in range(1, 6):
                def _set_lw(v, l=lyr, lf=lw_frame):
                    l.linewidth = v
                    for child in lf.winfo_children():
                        try:
                            child.configure(
                                fg_color=UI_ACC if int(child.cget("text")) == v
                                else UI_CARD)
                        except Exception:
                            pass
                    self._redraw_static()
                b = ctk.CTkButton(
                    lw_frame, text=str(lw_val), width=18, height=16,
                    fg_color=UI_ACC if lyr.linewidth == lw_val else UI_CARD,
                    hover_color=UI_ACC, corner_radius=3,
                    font=ctk.CTkFont(size=8),
                    command=lambda v=lw_val, fn=_set_lw: fn(v))
                b.pack(side="left", padx=1)

        # Botón nueva capa
        ctk.CTkFrame(self._layer_frame, height=1,
                     fg_color=UI_BORD).pack(fill="x", pady=4)
        ctk.CTkButton(self._layer_frame, text="+ Nueva capa", height=26,
                      fg_color=UI_CARD, hover_color=UI_ACC,
                      font=ctk.CTkFont(size=10), corner_radius=6,
                      command=self._nueva_capa).pack(fill="x", padx=4)
        self._update_prop_layer_om()

    def _activar_capa(self, name: str):
        self.active_layer = name
        self._om_capa.set(name)
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
        self._om_capa.configure(values=list(self.layers.keys()))
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
        """Actualiza el dropdown de capa según la selección actual."""
        if not hasattr(self, "_prop_layer_om"):
            return   # widget no creado aún (llamada desde __init__)
        layer_vals = list(self.layers.keys())
        self._prop_layer_om.configure(values=layer_vals)
        sel = [e for e in self.entities if e.selected]
        if len(sel) == 1:
            self._prop_layer_var.set(sel[0].layer)
        elif len(sel) > 1:
            capas = {e.layer for e in sel}
            self._prop_layer_var.set(
                next(iter(capas)) if len(capas) == 1 else "(varios)")
        else:
            self._prop_layer_var.set("—")

    # ─── Transformadas mundo↔pantalla ─────────────────────────────
    def w2s(self, wx, wy):
        return (self.offset_x + wx * self.scale,
                self.offset_y - wy * self.scale)

    def s2w(self, sx, sy):
        return ((sx - self.offset_x) / self.scale,
                (self.offset_y - sy) / self.scale)

    def _center_view(self):
        W = self.root.winfo_width()  or 1200
        H = self.root.winfo_height() or 900
        self.offset_x = (W - 52 - 220) / 2
        self.offset_y = (H - 80) / 2

    def _viewport_world(self):
        W = self.canvas.winfo_width()  or 800
        H = self.canvas.winfo_height() or 600
        x0, y0 = self.s2w(0, H)
        x1, y1 = self.s2w(W, 0)
        return x0, y0, x1, y1

    # ─── Zoom & Pan ───────────────────────────────────────────────
    def _save_zoom(self):
        """Guarda el estado de zoom actual para ZOOM P (máx 10 niveles)."""
        self._zoom_prev.append((self.scale, self.offset_x, self.offset_y))
        if len(self._zoom_prev) > 10:
            self._zoom_prev.pop(0)

    def _on_wheel(self, event):
        self._save_zoom()
        factor = 1.25 if event.delta > 0 else 1/1.25
        wx, wy = self.s2w(event.x, event.y)
        self.scale = max(0.5, min(self.scale * factor, 20000))
        self.offset_x = event.x - wx * self.scale
        self.offset_y = event.y + wy * self.scale
        self._redraw()

    def _on_pan_start(self, event):
        self._pan_start = (event.x, event.y, self.offset_x, self.offset_y)

    def _on_pan(self, event):
        if self._pan_start:
            dx = event.x - self._pan_start[0]
            dy = event.y - self._pan_start[1]
            self.offset_x = self._pan_start[2] + dx
            self.offset_y = self._pan_start[3] + dy
            self._redraw()

    def _on_pan_end(self, event):
        self._pan_start = None

    def _on_resize(self, event):
        self._redraw()

    def _zoom_extents(self, *_):
        self._save_zoom()
        pts = []
        for e in self.entities:
            lyr = self.layers.get(e.layer)
            if lyr and not lyr.visible:
                continue          # ignorar entidades en capas ocultas
            pts.extend((x, y) for x, y, *_ in e.bbox_pts())
        if not pts:
            self._center_view(); self._redraw(); return
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        W = self.canvas.winfo_width()  or 800
        H = self.canvas.winfo_height() or 600
        margin = 60
        sx = max(max(xs)-min(xs), 0.001)
        sy = max(max(ys)-min(ys), 0.001)
        self.scale = min((W-margin*2)/sx, (H-margin*2)/sy) * 0.85
        cx = (min(xs)+max(xs))/2; cy = (min(ys)+max(ys))/2
        self.offset_x = W/2 - cx*self.scale
        self.offset_y = H/2 + cy*self.scale
        self._redraw()

    def _zoom_window(self, sx0, sy0, sx1, sy1):
        self._save_zoom()
        wx0, wy0 = self.s2w(sx0, sy0)
        wx1, wy1 = self.s2w(sx1, sy1)
        W = self.canvas.winfo_width()  or 800
        H = self.canvas.winfo_height() or 600
        span_x = max(abs(wx1-wx0), 0.001)
        span_y = max(abs(wy1-wy0), 0.001)
        self.scale = min(W/span_x, H/span_y) * 0.90
        cx = (wx0+wx1)/2; cy = (wy0+wy1)/2
        self.offset_x = W/2 - cx*self.scale
        self.offset_y = H/2 + cy*self.scale
        self._redraw()

    # ─── Índice espacial de snap ──────────────────────────────────
    def _rebuild_snap_index(self):
        idx: dict = {}
        seen: set = set()
        for e in self.entities:
            for wp in e.snap_points():
                dedup = (round(wp[0], 4), round(wp[1], 4), wp[2])
                if dedup in seen:
                    continue
                seen.add(dedup)
                key = (_cell(wp[0]), _cell(wp[1]))
                idx.setdefault(key, []).append(wp)
        self._snap_index = idx

    def _find_snap(self, sx, sy):
        if not self.snap_on:
            return None
        wx, wy = self.s2w(sx, sy)
        rw = SNAP_PX / self.scale          # radio en coords mundo
        best_d = float(SNAP_PX)
        best   = None

        # ── Snaps estáticos (indexados): END MID CEN QUA ────────────
        static_ok = {t for t in ("end", "mid", "cen", "qua")
                     if self._snap_types.get(t, True)}
        if static_ok and self._snap_index:
            cr = max(2, int(rw / _SNAP_CELL) + 2)   # +2 margen extra
            cx_c, cy_c = _cell(wx), _cell(wy)
            for icx in range(cx_c - cr, cx_c + cr + 1):
                for icy in range(cy_c - cr, cy_c + cr + 1):
                    for wp in self._snap_index.get((icx, icy), []):
                        if wp[2] not in static_ok:
                            continue
                        esx, esy = self.w2s(wp[0], wp[1])
                        d = math.hypot(esx - sx, esy - sy)
                        if d < best_d:
                            best_d = d; best = wp

        # ── Snaps dinámicos: INT PER TAN NEA ────────────────────────
        need_dyn = any(self._snap_types.get(t) for t in ("int", "per", "tan", "nea"))
        if need_dyn:
            search_r = rw * 3
            nearby = [e for e in self.entities
                      if self._entity_near(e, wx, wy, search_r)]

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

            if self._snap_types.get("nea"):
                for pt in self._snap_nea(wx, wy, nearby, rw):
                    esx, esy = self.w2s(*pt)
                    d = math.hypot(esx - sx, esy - sy)
                    if d < best_d:
                        best_d = d; best = (pt[0], pt[1], "nea")

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

    def _snap_int(self, wx, wy, nearby, rw):
        """Puntos de intersección entre pares de entidades cercanas."""
        segs    = []
        circles = []
        for e in nearby:
            segs.extend(self._segs_of(e))
            if isinstance(e, (Circle, Arc)):
                circles.append(e)
        pts = []
        # segmento–segmento
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
        Si no hay punto previo (modo selección) usa el cursor como fallback."""
        if self.draw_pts:
            fx, fy = self.draw_pts[-1]   # from-point = origen de la línea en curso
        else:
            fx, fy = wx, wy              # fallback: igual a NEA (sin dibujo activo)

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

    # ─── Movimiento throttled ─────────────────────────────────────
    def _on_move(self, event):
        self._mouse_sx = event.x
        self._mouse_sy = event.y
        if not self._move_pending:
            self._move_pending = True
            self.root.after(8, self._flush_move)

    def _flush_move(self):
        self._move_pending = False
        sx, sy = self._mouse_sx, self._mouse_sy
        snap = self._find_snap(sx, sy)
        if snap:
            self.mouse_w = (snap[0], snap[1]); self.snap_pt = snap
        else:
            wx, wy = self.s2w(sx, sy)
            wx, wy = self._apply_ortho(wx, wy)
            self.mouse_w = (wx, wy); self.snap_pt = None
        # F1: actualizar entidad bajo el cursor
        self._hover_ent = self._find_hover_entity(sx, sy)
        x, y = self.mouse_w
        self._lbl_coords.configure(text=f"X: {x:10.3f}    Y: {y:10.3f}")
        self._redraw_dynamic()

    # ─── Clics: soporta clic puntual + arrastre para selección ────
    def _on_btn_down(self, event):
        self.canvas.focus_set()
        self._drag_start_s = (event.x, event.y)
        self._is_dragging  = False

    def _on_drag(self, event):
        # El arrastre sólo aplica a la herramienta SELECT y sin operación activa
        if self._drag_start_s is None:
            return
        if self.tool != "select" or self._op_mode:
            return
        dx = abs(event.x - self._drag_start_s[0])
        dy = abs(event.y - self._drag_start_s[1])
        if dx > 6 or dy > 6:   # umbral generoso para no interferir con clics
            self._is_dragging = True
            self._redraw_dynamic()   # dibuja rectángulo de selección

    def _on_btn_up(self, event):
        if self._drag_start_s is None:
            return
        start = self._drag_start_s
        self._drag_start_s = None
        was_drag = self._is_dragging
        self._is_dragging = False

        wx, wy = self.mouse_w

        # ── Operación en curso (MOVE/COPY/DIST/ZOOM_W) ────────────
        if self._op_mode:
            self._op_pts.append((wx, wy))
            self._handle_op()
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
        if self.tool in ("line", "polyline", "rect"):
            self.draw_pts.append((wx, wy))
            if self.tool == "line" and len(self.draw_pts) == 2:
                self._commit_line()
            elif self.tool == "rect" and len(self.draw_pts) == 2:
                self._commit_rect()
        elif self.tool == "circle":
            self.draw_pts.append((wx, wy))
            if len(self.draw_pts) == 1:
                self._lbl_op.configure(text="Radio o segundo punto:")
            elif len(self.draw_pts) == 2:
                self._commit_circle()
        elif self.tool == "arc":
            self.draw_pts.append((wx, wy))
            if len(self.draw_pts) == 1:
                self._lbl_op.configure(text="ARCO — Punto en el arco:")
            elif len(self.draw_pts) == 2:
                self._lbl_op.configure(text="ARCO — Punto final:")
            elif len(self.draw_pts) == 3:
                self._commit_arc()
        elif self.tool == "text":
            self._pedir_texto(wx, wy)

    def _on_dblclick(self, event):
        # Primero: editar texto con doble clic
        if self._try_edit_text(event.x, event.y):
            return
        if self.tool == "polyline" and len(self.draw_pts) >= 2:
            self._fin_pline()

    def _try_edit_text(self, sx, sy) -> bool:
        """Doble clic en entidad Texto → edita contenido. Retorna True si editó."""
        HIT = 24 / self.scale
        wx, wy = self.s2w(sx, sy)
        for i, e in enumerate(self.entities):
            if not isinstance(e, Text) or math.hypot(wx-e.x, wy-e.y) >= HIT:
                continue
            lyr = self.layers.get(e.layer)
            if lyr and lyr.locked:
                self._echo(f"!! Capa '{e.layer}' bloqueada — no se puede editar")
                return True   # consumir el evento pero no editar
            dlg = ctk.CTkInputDialog(
                text=f'Editar texto  (actual: "{e.content}"):',
                title="TEXTO — editar")
            nuevo = dlg.get_input()
            if nuevo is not None and nuevo.strip():
                self._push_undo()
                self.entities[i] = Text(x=e.x, y=e.y,
                                        content=nuevo.strip().upper(),
                                        height=e.height, layer=e.layer)
                self._redraw_static()
            return True
        return False

    def _on_rclick(self, event):
        if self._op_mode:
            self._cancelar(); return
        if self.tool == "polyline" and len(self.draw_pts) >= 2:
            self._fin_pline(); return
        # Modo idle → menú contextual
        self._show_context_menu(event)

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
            m.add_command(label="  Borrar          E",
                          command=lambda: self._ejecutar_accion("erase", ""))
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

        m.add_separator()
        m.add_command(label="  Zoom extensión  ZE",
                      command=self._zoom_extents)
        m.add_command(label="  🤖 Asistente IA  /",
                      command=self._focus_ia_input)
        m.add_separator()
        m.add_command(label="  Cancelar  Esc",
                      command=self._cancelar)

        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    # ─── Operaciones (MOVE / COPY / DIST / ZOOM_W) ───────────────
    def _handle_op(self):
        m = self._op_mode
        pts = self._op_pts

        if m in ("move_base", "copy_base") and len(pts) == 1:
            self._op_mode = "move_dest" if m == "move_base" else "copy_dest"
            self._lbl_op.configure(text="Punto destino:")
            self._dyn_clear()
            self.canvas.focus_set()
            self._redraw_dynamic()

        elif m in ("move_dest", "copy_dest") and len(pts) == 2:
            bx, by = pts[0]; dx2, dy2 = pts[1]
            dx, dy = dx2-bx, dy2-by
            self._push_undo()
            sel_ids = {id(e) for e in self._op_sel}
            if m == "move_dest":
                for i, e in enumerate(self.entities):
                    if id(e) in sel_ids:
                        self.entities[i] = e.translated(dx, dy)
                # MOVE termina tras un destino (igual que AutoCAD)
                self._op_mode = ""; self._op_sel = []; self._op_pts = []
                self._lbl_op.configure(text="")
                self._highlight_op_btn(None)
                self._dyn_clear()
            else:
                # COPY: multi-destino — mantiene el modo, base se convierte
                # en el destino anterior para poder hacer offsets sucesivos
                for e in self._op_sel:
                    self.entities.append(e.translated(dx, dy))
                n_copies = self._op_data.get("n_copies", 0) + 1
                self._op_data["n_copies"] = n_copies
                self._op_pts = [pts[1]]   # nuevo "base" = último destino
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

        elif m == "zoom_w1" and len(pts) == 1:
            self._op_mode = "zoom_w2"
            self._lbl_op.configure(text="Segunda esquina:")

        elif m == "zoom_w2" and len(pts) == 2:
            (wx0,wy0),(wx1,wy1) = pts
            W = self.canvas.winfo_width()  or 800
            H = self.canvas.winfo_height() or 600
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
                self._apply_mirror(ax, ay, bx, by)
            else:
                self._echo("!! Los dos puntos del eje son iguales")
                self._op_pts = []

        # ── OFFSET ────────────────────────────────────────────────────
        # ── TRIM ──────────────────────────────────────────────────────
        elif m == "trim_cut" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line, Polyline))
            if res:
                _, cutter = res
                self._op_data["cutter"] = cutter
                cutter.selected = True
                self._op_pts = []
                self._op_mode = "trim_obj"
                kind = "polilínea" if isinstance(cutter, Polyline) else "línea"
                self._lbl_op.configure(text=f"TR — {kind} borde — Clic en segmento a recortar:")
                self._redraw_static()
            else:
                self._echo("TR: clic en línea o polilínea como borde de corte")
                self._op_pts = []

        elif m == "trim_obj" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line, Polyline))
            if res:
                idx, target = res
                cutter = self._op_data.get("cutter")
                if isinstance(target, Line):
                    self._trim_line_with_any(idx, target, wx, wy, cutter)
                elif isinstance(target, Polyline):
                    self._trim_polyline_with(idx, target, wx, wy, cutter)
            else:
                self._echo("TR: clic sobre la línea o polilínea a recortar")
            self._op_pts = []   # stays in trim_obj

        # ── EXTEND ────────────────────────────────────────────────────
        elif m == "extend_bnd" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line, Polyline))
            if res:
                _, bnd = res
                self._op_data["boundary"] = bnd
                bnd.selected = True
                self._op_pts = []
                self._op_mode = "extend_obj"
                kind = "polilínea" if isinstance(bnd, Polyline) else "línea"
                self._lbl_op.configure(text=f"EX — {kind} límite — Clic en extremo a extender:")
                self._redraw_static()
            else:
                self._echo("EX: clic en línea o polilínea como límite")
                self._op_pts = []

        elif m == "extend_obj" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy, (Line,))
            if res:
                idx, line = res
                bnd = self._op_data.get("boundary")
                self._extend_line_to_any(idx, line, wx, wy, bnd)
            else:
                self._echo("EX: clic sobre la línea a extender")
            self._op_pts = []   # stays in extend_obj

        # ── MATCHPROP ─────────────────────────────────────────────────
        elif m == "matchprop_src" and len(pts) == 1:
            wx, wy = pts[0]
            res = self._pick_entity_at(wx, wy)
            if res:
                _, src = res
                self._op_data["src_layer"] = src.layer
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
                self._redraw_static()
            else:
                self._echo("MA: clic en entidad destino (ESC=fin)")
            self._op_pts = []   # stays in matchprop_dst

        elif m == "offset_sel" and len(pts) == 1:
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
                if isinstance(best, Line):
                    new_e = _offset_line(best, d, wx, wy)
                elif isinstance(best, Circle):
                    new_e = _offset_circle(best, d, wx, wy)
                else:
                    new_e = _offset_polyline(best, d, wx, wy)
                if new_e:
                    self._push_undo()
                    self.entities.append(new_e)
                    self._rebuild_snap_index(); self._redraw()
                else:
                    self._echo("OFFSET: radio resultante inválido")
            else:
                self._echo("OFFSET: clic sobre línea o círculo")
            self._op_pts = []   # continúa en modo offset_sel

    # ─── Aplicar transformaciones ─────────────────────────────────
    def _apply_rotate(self, deg: float):
        bx, by = self._op_data.get("base", (0.0, 0.0))
        sel_ids = {id(e) for e in self._op_sel}
        self._push_undo()
        for i, e in enumerate(self.entities):
            if id(e) in sel_ids:
                self.entities[i] = e.rotated(bx, by, deg)
        self._finish_op()

    def _apply_scale(self, f: float):
        if f < 0.001:
            self._echo("!! Factor demasiado pequeño"); return
        bx, by = self._op_data.get("base", (0.0, 0.0))
        sel_ids = {id(e) for e in self._op_sel}
        self._push_undo()
        for i, e in enumerate(self.entities):
            if id(e) in sel_ids:
                self.entities[i] = e.scaled(bx, by, f)
        self._finish_op()

    def _apply_mirror(self, ax, ay, bx, by):
        sel_ids = {id(e) for e in self._op_sel}
        self._push_undo()
        for i, e in enumerate(self.entities):
            if id(e) in sel_ids:
                self.entities[i] = e.mirrored(ax, ay, bx, by)
        self._finish_op()

    def _finish_op(self):
        self._op_mode = ""; self._op_sel = []; self._op_pts = []; self._op_data = {}
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

    # ─── TRIM ─────────────────────────────────────────────────────
    @staticmethod
    def _cutter_segments(cutter) -> list:
        """Devuelve los segmentos (x1,y1,x2,y2) del cutter (Line o Polyline)."""
        if isinstance(cutter, Line):
            return [(cutter.x1, cutter.y1, cutter.x2, cutter.y2)]
        if isinstance(cutter, Polyline) and len(cutter.points) >= 2:
            pts = cutter.points
            segs = [(pts[i][0],pts[i][1],pts[i+1][0],pts[i+1][1])
                    for i in range(len(pts)-1)]
            if cutter.closed and len(pts) > 2:
                segs.append((pts[-1][0],pts[-1][1],pts[0][0],pts[0][1]))
            return segs
        return []

    def _trim_line_with_any(self, line_idx, line, wx_click, wy_click, cutter):
        """Trim line contra cualquier cutter (Line o Polyline)."""
        dx, dy = line.x2-line.x1, line.y2-line.y1
        L2 = dx*dx + dy*dy
        t_click = ((wx_click-line.x1)*dx+(wy_click-line.y1)*dy) / max(L2, 1e-10)
        # Encontrar todas las intersecciones
        hits = []
        for sx1,sy1,sx2,sy2 in self._cutter_segments(cutter):
            res = _seg_intersect(line.x1, line.y1, line.x2, line.y2,
                                 sx1,sy1,sx2,sy2)
            if res:
                hits.append(res)
        if not hits:
            self._echo("TR: sin intersección con el borde"); return
        # Elegir la más cercana al clic
        ix, iy, t, _ = min(hits, key=lambda h: abs(h[2] - t_click))
        self._push_undo()
        if t_click < t:
            self.entities[line_idx] = Line(x1=ix, y1=iy,
                                           x2=line.x2, y2=line.y2, layer=line.layer)
        else:
            self.entities[line_idx] = Line(x1=line.x1, y1=line.y1,
                                           x2=ix, y2=iy, layer=line.layer)
        self._rebuild_snap_index(); self._redraw()

    def _trim_polyline_with(self, poly_idx, poly, wx_click, wy_click, cutter):
        """Trim de un segmento de Polyline contra un cutter."""
        pts = poly.points
        if len(pts) < 2:
            return
        # Encontrar el segmento del polyline más cercano al clic
        best_seg = None; best_dist = 1e18; best_si = 0
        for si in range(len(pts)-1):
            d = _dist_pt_seg(wx_click, wy_click,
                             pts[si][0],pts[si][1],pts[si+1][0],pts[si+1][1])
            if d < best_dist:
                best_dist = d; best_si = si
        ax,ay = pts[best_si]; bx,by = pts[best_si+1]
        # Intersectar ese segmento con el cutter
        hits = []
        for sx1,sy1,sx2,sy2 in self._cutter_segments(cutter):
            res = _seg_intersect(ax,ay,bx,by, sx1,sy1,sx2,sy2)
            if res:
                hits.append(res)
        if not hits:
            self._echo("TR: sin intersección"); return
        dx, dy = bx-ax, by-ay
        L2 = dx*dx+dy*dy
        t_click = ((wx_click-ax)*dx+(wy_click-ay)*dy)/max(L2,1e-9)
        ix, iy, t, _ = min(hits, key=lambda h: abs(h[2]-t_click))
        self._push_undo()
        new_pts = list(pts)
        if t_click < t:   # eliminar lado inicio del segmento → reemplazar pts[si]
            new_pts[best_si] = (ix, iy)
        else:             # eliminar lado final → reemplazar pts[si+1]
            new_pts[best_si+1] = (ix, iy)
        self.entities[poly_idx] = Polyline(points=new_pts, closed=poly.closed,
                                           layer=poly.layer)
        self._rebuild_snap_index(); self._redraw()

    # ─── EXTEND ───────────────────────────────────────────────────
    def _extend_line_to_any(self, line_idx, line, wx_click, wy_click, boundary):
        """Extend line hasta el borde (Line o Polyline)."""
        hits = []
        for sx1,sy1,sx2,sy2 in self._cutter_segments(boundary):
            res = _seg_intersect(line.x1, line.y1, line.x2, line.y2,
                                 sx1,sy1,sx2,sy2,
                                 t_range=(-1e9,1e9), u_range=(-1e9,1e9))
            if res:
                hits.append(res)
        if not hits:
            self._echo("EX: no hay intersección con el límite"); return
        d_start = math.hypot(wx_click-line.x1, wy_click-line.y1)
        d_end   = math.hypot(wx_click-line.x2, wy_click-line.y2)
        # Elegir la intersección más cercana al extremo que se va a extender
        if d_start < d_end:
            ix, iy, _, _ = min(hits, key=lambda h: math.hypot(h[0]-line.x1, h[1]-line.y1))
            self._push_undo()
            self.entities[line_idx] = Line(x1=ix, y1=iy,
                                           x2=line.x2, y2=line.y2, layer=line.layer)
        else:
            ix, iy, _, _ = min(hits, key=lambda h: math.hypot(h[0]-line.x2, h[1]-line.y2))
            self._push_undo()
            self.entities[line_idx] = Line(x1=line.x1, y1=line.y1,
                                           x2=ix, y2=iy, layer=line.layer)
        self._rebuild_snap_index(); self._redraw()

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
        self._add_entity(Line(x1=p1[0], y1=p1[1], x2=p2[0], y2=p2[1],
                              layer=self.active_layer))
        self.draw_pts.clear()

    def _commit_rect(self):
        (x1,y1),(x2,y2) = self.draw_pts
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

    def _fin_pline(self, *_):
        if len(self.draw_pts) >= 2:
            self._add_entity(Polyline(points=list(self.draw_pts),
                                      closed=False, layer=self.active_layer))
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
        # Paso 2 — altura (vacío = 0.20)
        dlg2 = ctk.CTkInputDialog(
            text="Altura del texto [0.20 m]:", title="TEXTO — Altura")
        h_str = dlg2.get_input()
        try:
            h = float(h_str) if h_str and h_str.strip() else 0.20
            if h <= 0:
                h = 0.20
        except ValueError:
            h = 0.20
        self._add_entity(Text(x=wx, y=wy, content=txt.strip().upper(),
                              height=h, layer=self.active_layer))
        self.draw_pts.clear()

    # ─── Selección ────────────────────────────────────────────────
    def _select_at(self, sx, sy):
        HIT = 8 / self.scale
        wx, wy = self.s2w(sx, sy)
        for e in self.entities:
            e.selected = False
        best = None; best_d = HIT
        for e in self.entities:
            lyr = self.layers.get(e.layer)
            if lyr and lyr.locked:
                continue
            d = self._dist_entity(wx, wy, e)
            if d < best_d:
                best_d = d; best = e
        if best:
            best.selected = True
            self._lbl_prop.configure(text=best.info())
        else:
            self._lbl_prop.configure(text="—\nNinguna entidad\nseleccionada")
        self._update_prop_layer_om()
        self._redraw_static()

    def _window_select(self, sx0, sy0, sx1, sy1):
        """Izq→der = ventana (solo dentro) / Der→izq = cruce (toca el rect)."""
        wx0, wy0 = self.s2w(sx0, sy0)
        wx1, wy1 = self.s2w(sx1, sy1)
        is_cross = sx1 < sx0   # Der→izq = cruce

        xmin, xmax = min(wx0,wx1), max(wx0,wx1)
        ymin, ymax = min(wy0,wy1), max(wy0,wy1)

        for e in self.entities:
            e.selected = False

        sel = []
        for e in self.entities:
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
            self._lbl_prop.configure(text=f"{len(sel)} entidad(es) sel.")
        else:
            self._lbl_prop.configure(text="—\nNinguna entidad\nseleccionada")
        self._update_prop_layer_om()
        self._redraw_static()

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
        self._redraw_static()

    def _erase(self):
        sel = [e for e in self.entities if e.selected]
        if sel:
            self._push_undo()
            for e in sel:
                self.entities.remove(e)
            self._lbl_prop.configure(text="—\nNinguna entidad\nseleccionada")
            self._rebuild_snap_index(); self._redraw_static()

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
            return math.hypot(wx-e.x, wy-e.y)
        return 1e9

    # ─── F1: Hover highlight ──────────────────────────────────────
    def _find_hover_entity(self, sx, sy):
        """Devuelve la entidad más cercana al cursor (dentro de HOVER_PX px) o None."""
        wx, wy = self.s2w(sx, sy)
        hw = HOVER_PX / self.scale      # radio en coords mundo
        best = None; best_d = hw
        for e in self.entities:
            lyr = self.layers.get(e.layer)
            if lyr and (not lyr.visible or lyr.locked):
                continue
            if e.selected:              # ya resaltadas por selección
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
        return {
            "entities": [self._copy_entity(e) for e in self.entities],
            "layers":   copy.deepcopy(self.layers),   # layers sí se mutan in-place
            "active_layer": self.active_layer,
        }

    def _restore_snapshot(self, snap: dict):
        """Restaura un snapshot completo."""
        self.entities     = [self._copy_entity(e) for e in snap["entities"]]
        self.layers       = copy.deepcopy(snap["layers"])
        self.active_layer = snap.get("active_layer", self.active_layer)
        # Sincronizar UI de capas
        self._om_capa.configure(values=list(self.layers.keys()))
        self._om_capa.set(self.active_layer)
        self._build_layer_panel()

    def _push_undo(self):
        self._undo_stack.append(self._snapshot())
        self._redo_stack.clear()
        if len(self._undo_stack) > 60:
            self._undo_stack.pop(0)
        self._update_undo_btns()

    def _undo(self, *_):
        if len(self._undo_stack) > 1:
            self._redo_stack.append(self._snapshot())
            self._undo_stack.pop()
            self._restore_snapshot(self._undo_stack[-1])
            self._lbl_prop.configure(text="—\nNinguna entidad\nseleccionada")
            self._rebuild_snap_index(); self._redraw()
        self._update_undo_btns()

    def _redo(self, *_):
        if self._redo_stack:
            self._undo_stack.append(self._snapshot())
            self._restore_snapshot(self._redo_stack.pop())
            self._rebuild_snap_index(); self._redraw()
        self._update_undo_btns()

    def _add_entity(self, e: Entity):
        lyr = self.layers.get(e.layer)
        if lyr and lyr.locked:
            self._echo(f"!! Capa '{e.layer}' está bloqueada — desbloquee antes de dibujar")
            return
        self._push_undo()
        self.entities.append(e)
        for wp in e.snap_points():
            key = (_cell(wp[0]), _cell(wp[1]))
            self._snap_index.setdefault(key, []).append(wp)
        self._redraw()

    # ─── Render: doble capa ───────────────────────────────────────
    def _redraw(self, *_):
        self._redraw_static()
        self._redraw_dynamic()

    def _redraw_static(self, *_):
        cv = self.canvas
        cv.delete("st")
        W = cv.winfo_width(); H = cv.winfo_height()
        if W < 10 or H < 10:
            return
        if self.grid_on:
            self._draw_grid(W, H)
        self._draw_axes(W, H)
        self._draw_entities(W, H)
        self._draw_hud(W, H)
        self._update_flags()

    def _redraw_dynamic(self, *_):
        cv = self.canvas
        cv.delete("dy")
        W = cv.winfo_width(); H = cv.winfo_height()
        if W < 10 or H < 10:
            return
        # F1: hover highlight — entidad bajo el cursor (antes del preview)
        if self._hover_ent is not None:
            self._render_entity(cv, self._hover_ent, "#4A9EFF", 2, "dy")
        self._draw_preview()
        self._draw_dyn_input()      # DYN: panel flotante de entrada
        self._draw_op_preview()
        self._draw_sel_rect()
        self._draw_snap_indicator()
        self._draw_cursor(W, H)

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
        for step, col in [(step_f, CV_GRID), (step_m, CV_GRID_MAJ)]:
            x = math.floor(wx0/step)*step
            while x <= wx1+step:
                sx, _ = self.w2s(x, 0)
                cv.create_line(sx, 0, sx, H, fill=col, width=1, tags="st")
                x += step
            y = math.floor(wy0/step)*step
            while y <= wy1+step:
                _, sy = self.w2s(0, y)
                cv.create_line(0, sy, W, sy, fill=col, width=1, tags="st")
                y += step

    def _draw_axes(self, W, H):
        cv = self.canvas
        ox, oy = self.w2s(0, 0)
        cv.create_line(0, oy, W, oy, fill=CV_AXIS, width=1, tags="st")
        cv.create_line(ox, 0, ox, H, fill=CV_AXIS, width=1, tags="st")

    # ─── Entidades con culling ────────────────────────────────────
    def _entity_aabb(self, e):
        """Bounding box real de la entidad en coordenadas mundo."""
        if isinstance(e, Text):
            return e.x, e.y, e.x, e.y   # punto único
        pts = e.bbox_pts()               # Arc usa bbox_pts() con cruces de eje
        if not pts:
            return 0, 0, 0, 0
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def _in_viewport(self, e, vx0, vy0, vx1, vy1):
        ex0,ey0,ex1,ey1 = self._entity_aabb(e)
        return not (ex1<vx0 or ex0>vx1 or ey1<vy0 or ey0>vy1)

    def _draw_entities(self, W, H):
        cv = self.canvas
        vx0,vy0,vx1,vy1 = self._viewport_world()
        m = max(5.0, 50/self.scale)
        vx0-=m; vy0-=m; vx1+=m; vy1+=m

        for e in self.entities:
            lyr = self.layers.get(e.layer)
            if lyr and not lyr.visible:
                continue
            if not self._in_viewport(e, vx0, vy0, vx1, vy1):
                continue
            # Color: entidad individual > capa (bylayer es el default)
            if e.color and e.color.lower() != "bylayer":
                col = e.color
            else:
                col = lyr.color if lyr else "#FFFFFF"
            lw   = lyr.linewidth if lyr else 1
            raw_dash = _LINETYPES.get(lyr.linetype if lyr else "CONTINUOUS", ())
            # LTSCALE: escalar patrón de dash según zoom actual
            # factor en px/unidad-mundo, normalizado a escala base 40
            if raw_dash:
                ltf = max(0.5, min(self.scale / 40.0, 8.0))
                dash = tuple(max(1, int(v * ltf)) for v in raw_dash)
            else:
                dash = ()
            if e.selected:
                col = CV_SELECT; lw = max(lw+1, 2)
            # Capas bloqueadas: color tenue pero visibles
            if lyr and lyr.locked and not e.selected:
                col = "#555555"
            self._render_entity(cv, e, col, lw, "st", dash=dash)

    def _render_entity(self, cv, e, col, lw, tag, dash=()):
        dk = {"dash": dash} if dash else {}
        if isinstance(e, Line):
            sx1,sy1 = self.w2s(e.x1, e.y1)
            sx2,sy2 = self.w2s(e.x2, e.y2)
            cv.create_line(sx1,sy1,sx2,sy2, fill=col, width=lw, tags=tag, **dk)

        elif isinstance(e, Polyline):
            if len(e.points) < 2:
                return
            flat = []
            for wx,wy in e.points:
                sx,sy = self.w2s(wx, wy); flat.extend([sx, sy])
            if e.closed:
                cv.create_polygon(*flat, fill="", outline=col,
                                  width=lw, joinstyle=tk.ROUND, tags=tag, **dk)
            else:
                cv.create_line(*flat, fill=col, width=lw,
                               joinstyle=tk.ROUND, tags=tag, **dk)

        elif isinstance(e, Circle):
            sx0,sy0 = self.w2s(e.cx-e.radius, e.cy+e.radius)
            sx1,sy1 = self.w2s(e.cx+e.radius, e.cy-e.radius)
            cv.create_oval(sx0,sy0,sx1,sy1, outline=col, fill="", width=lw,
                           tags=tag, **dk)
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
            sx,sy = self.w2s(e.x, e.y)
            px = max(8, int(e.height*self.scale*0.72))
            cv.create_text(sx,sy, text=e.content, fill=col,
                           font=("Courier New", px), anchor="sw",
                           angle=e.angle, tags=tag)

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

    def _draw_op_preview(self):
        """Preview de entidades fantasma durante operaciones."""
        cv = self.canvas
        mx, my = self.mouse_w

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

    def _draw_sel_rect(self):
        """Rectángulo de selección — sólo para herramienta SELECT."""
        cv = self.canvas
        if self.tool != "select":  # nunca mostrar durante dibujo
            return
        if not self._is_dragging or self._drag_start_s is None:
            return
        x0,y0 = self._drag_start_s
        x1,y1 = self._mouse_sx, self._mouse_sy
        if x1 < x0:   # Cruce — verde punteado
            col = CV_CRS_BOX; dash = (4,3); fill = ""
        else:          # Ventana — azul sólido
            col = CV_WIN_BOX; dash = (); fill = ""
        cv.create_rectangle(x0,y0,x1,y1, outline=col,
                            fill=fill, width=1, dash=dash, tags="dy")
        # indicador de modo
        label = "CRUCE" if x1<x0 else "VENTANA"
        cv.create_text(x1+6,y1+6, text=label, fill=col,
                       font=("Courier New", 8), anchor="nw", tags="dy")

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

    def _draw_cursor(self, W, H):
        cv = self.canvas
        sx, sy = self.w2s(*self.mouse_w)
        L = 12

        if self.snap_pt:
            col = _SNAP_COLORS.get(self.snap_pt[2], "white")
            # Aura: círculo delgado en radio SNAP_PX centrado en snap
            r = SNAP_PX
            cv.create_oval(sx-r, sy-r, sx+r, sy+r,
                           outline=col, width=1, tags="dy")
        else:
            col = "white"

        cv.create_line(sx-L, sy,   sx+L, sy,   fill=col, width=1, tags="dy")
        cv.create_line(sx,   sy-L, sx,   sy+L, fill=col, width=1, tags="dy")

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
        lines = [
            self._arch_scale_label(),
            f"{len(self.entities)} entidades",
        ]
        if self.draw_pts:
            lines.append(f"Pts: {len(self.draw_pts)}")
        for i, txt in enumerate(reversed(lines)):
            cv.create_text(W-10, H-10-i*14, text=txt, fill=UI_BORD,
                           font=("Courier New", 8), anchor="se", tags="st")

    def _update_flags(self):
        # Botones de modo: verde relleno = activo / gris = inactivo
        def _flag(btn, active):
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
        self._lbl_tool.configure(text=names.get(self.tool, self.tool.upper()))

    def _update_undo_btns(self):
        """Actualiza el estado visual de los botones Deshacer/Rehacer."""
        if not hasattr(self, "_btn_undo"):
            return
        can_undo = len(self._undo_stack) > 1
        can_redo = bool(self._redo_stack)
        self._btn_undo.configure(fg_color=UI_ACC   if can_undo else UI_CARD,
                                 text_color=UI_TEXT if can_undo else UI_TEXT2)
        self._btn_redo.configure(fg_color="#7C3AED" if can_redo else UI_CARD,
                                 text_color=UI_TEXT  if can_redo else UI_TEXT2)

    def _highlight_op_btn(self, accion: str | None):
        """Resalta el botón de operación activa con ámbar; limpia los demás."""
        for a, b in self._op_btns.items():
            b.configure(fg_color="#D97706" if a == accion else UI_CARD)

    # ─── Control de herramienta ───────────────────────────────────
    def _set_tool(self, tool: str):
        self._dyn_clear()
        self.draw_pts.clear()
        self._op_mode = ""; self._op_pts = []; self._op_sel = []; self._op_data = {}
        self._lbl_op.configure(text="")
        self.tool = tool
        for t, b in self._tool_btns.items():
            b.configure(fg_color=UI_ACC if t == tool else UI_CARD)
        self._highlight_op_btn(None)
        self._redraw_dynamic()

    def _cancelar(self, *_):
        # Esc con DYN activo: primero limpia el buffer, segundo Esc cancela la herramienta
        if self.dyn_on and (self._dyn_buf or any(v is not None for v in self._dyn_locked)):
            self._dyn_clear()
            self._redraw_dynamic()
            return
        if self._op_mode:
            for e in self.entities:
                e.selected = False
            self._op_mode = ""; self._op_pts = []; self._op_sel = []; self._op_data = {}
            self._lbl_op.configure(text="")
            self._highlight_op_btn(None)
            self._redraw_static(); return
        if self.draw_pts:
            self._dyn_clear()
            self.draw_pts.clear()
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
                 "move_dest", "copy_dest", "mirror_p2", "offset_dist")

    def _dyn_active(self) -> bool:
        if not self.dyn_on:
            return False
        if self._op_mode:
            return self._op_mode in self._DYN_OPS
        return (
            (self.tool in ("line", "polyline", "arc") and len(self.draw_pts) >= 1)
            or (self.tool == "circle"  and len(self.draw_pts) == 1)
            or (self.tool == "rect"    and len(self.draw_pts) == 1)
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
        else:
            if self._op_mode == "rotate_angle":  return ["ang"]
            if self._op_mode == "scale_factor":  return ["factor"]
            if self._op_mode == "offset_dist":   return ["dist"]
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
            if val is None or val <= 0:
                self._dyn_clear(); return
            self._dyn_clear()
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
        if (self.dyn_on and self._dyn_active()
                and (self._dyn_buf or any(v is not None for v in self._dyn_locked))):
            self._dyn_execute()
            return "break"
        if self.tool == "polyline":
            self._fin_pline()
        else:
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
        if not (self.dyn_on and self._dyn_active()):
            return
        if event.state & 0x4:
            return
        c = event.char
        if c and (c.isdigit() or c in ".-"):
            if c == "-" and self._dyn_buf:
                return
            self._dyn_buf += c
            self._redraw_dynamic()
            return "break"

    def _on_root_key(self, event):
        """Backup DYN: captura teclado desde root si DYN activo y foco fuera del cmd."""
        if not (self.dyn_on and self._dyn_active()):
            return
        focused = self.root.focus_get()
        if focused is self._cmd_entry:
            return
        if event.state & 0x4:
            return
        k = event.keysym; c = event.char
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

        # Hint contextual al pie
        if self.tool == "circle" and not op:
            hint = "Tab → ⌀" if self._dyn_circ_mode == "r" else "Tab → r"
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
        self._cmd_var.set("")

        # ── Modo IA: prefijo / ────────────────────────────────────
        if raw.startswith("/"):
            ia_prompt = raw[1:].strip()
            if ia_prompt:
                self._add_to_history(f"/ {ia_prompt}", "ai")
                self._ejecutar_ia(ia_prompt)
            return

        # Loguear al historial
        self._add_to_history(f"> {raw}", "cad")

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
        # ── Herramientas de dibujo ─────────────────────────────────
        if accion in ("line","polyline","rect","circle","text","arc"):
            self._set_tool(accion)

        # ── Selección y edición ────────────────────────────────────
        elif accion == "select":
            self._set_tool("select")

        elif accion == "erase":
            sel = [e for e in self.entities if e.selected]
            if sel:
                self._erase()
            else:
                self._echo("Seleccione entidades y presione E (o DEL)")

        elif accion == "move":
            sel = [e for e in self.entities if e.selected]
            if not sel:
                self._echo("!! Seleccione entidades primero, luego M")
                return
            self._dyn_clear()
            self._op_mode = "move_base"; self._op_sel = sel
            self._op_pts  = []
            self._lbl_op.configure(text="MOVE — Punto base:")
            self._highlight_op_btn("move")
            self.canvas.focus_set()

        elif accion == "copy":
            sel = [e for e in self.entities if e.selected]
            if not sel:
                self._echo("!! Seleccione entidades primero, luego CO")
                return
            self._dyn_clear()
            self._op_mode = "copy_base"; self._op_sel = sel
            self._op_pts  = []; self._op_data = {"n_copies": 0}
            self._lbl_op.configure(text="COPY — Punto base:")
            self._highlight_op_btn("copy")
            self.canvas.focus_set()

        elif accion == "rotate":
            sel = [e for e in self.entities if e.selected]
            if not sel:
                self._echo("!! Seleccione entidades primero, luego RO"); return
            self._dyn_clear()
            self._op_mode = "rotate_base"; self._op_sel = sel; self._op_pts = []
            self._op_data = {}
            self._lbl_op.configure(text="ROTATE — Punto base:")
            self._highlight_op_btn("rotate")
            self.canvas.focus_set()

        elif accion == "scale":
            sel = [e for e in self.entities if e.selected]
            if not sel:
                self._echo("!! Seleccione entidades primero, luego SC"); return
            self._dyn_clear()
            self._op_mode = "scale_base"; self._op_sel = sel; self._op_pts = []
            self._op_data = {}
            self._lbl_op.configure(text="SCALE — Punto base:")
            self._highlight_op_btn("scale")
            self.canvas.focus_set()

        elif accion == "mirror":
            sel = [e for e in self.entities if e.selected]
            if not sel:
                self._echo("!! Seleccione entidades primero, luego MI"); return
            self._dyn_clear()
            self._op_mode = "mirror_p1"; self._op_sel = sel; self._op_pts = []
            self._op_data = {}
            self._lbl_op.configure(text="MIRROR — Primer punto del eje:")
            self._highlight_op_btn("mirror")
            self.canvas.focus_set()

        elif accion == "offset":
            self._op_data = {}
            self._op_mode = "offset_dist"
            self._op_pts = []
            self._lbl_op.configure(text="OFFSET — Escribe distancia + Enter:")
            self._highlight_op_btn("offset")
            self.canvas.focus_set()

        elif accion == "trim":
            self._op_mode = "trim_cut"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="TR — Clic en borde de corte (línea):")
            self._highlight_op_btn("trim")

        elif accion == "extend":
            self._op_mode = "extend_bnd"; self._op_pts = []; self._op_data = {}
            self._lbl_op.configure(text="EX — Clic en límite de extensión (línea):")
            self._highlight_op_btn("extend")

        elif accion == "explode":
            self._explode()

        elif accion == "matchprop":
            self._op_mode = "matchprop_src"; self._op_pts = []
            self._op_data = {"undo_pushed": False}   # undo único para todo el comando
            self._lbl_op.configure(text="MA — Clic en entidad fuente:")
            self._highlight_op_btn("matchprop")

        # ── Vista ─────────────────────────────────────────────────
        elif accion in ("zoom_e","zoom_a"):
            self._zoom_extents()
        elif accion == "zoom_cmd":
            self._cmd_zoom(args)
        elif accion == "regen":
            self._redraw()
        elif accion == "pan_cmd":
            self._echo("PAN: use rueda del mouse (botón central)")

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
            # Aislar capa activa: ocultar todas las demás
            for n, l in self.layers.items():
                l.visible = (n == self.active_layer)
            self._build_layer_panel(); self._redraw_static()
            self._echo(f"LAYISO — solo visible: {self.active_layer}")

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
                info = "\n".join(e.info() for e in sel[:5])
                if len(sel) > 5:
                    info += f"\n... +{len(sel)-5} más"
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
            x, y = self.mouse_w
            self._echo(f"X={x:.4f}  Y={y:.4f}")

        elif accion == "area_cmd":
            sel = [e for e in self.entities if e.selected
                   and isinstance(e, (Polyline, Circle))]
            if sel:
                for e in sel:
                    if isinstance(e, Polyline) and e.closed:
                        self._echo(f"ÁREA polilínea = {e.area():.4f} m²")
                    elif isinstance(e, Circle):
                        self._echo(f"ÁREA círculo = {math.pi*e.radius**2:.4f} m²")
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

    def _cmd_zoom(self, rest: str):
        sub = rest.strip().upper()
        if not sub or sub in ("E", "EXTENTS"):
            self._zoom_extents()
        elif sub in ("A", "ALL"):
            self._zoom_extents()
        elif sub in ("W", "WINDOW"):
            self._op_mode = "zoom_w1"; self._op_pts = []
            self._lbl_op.configure(text="ZOOM W — Primera esquina:")
        elif sub in ("P", "PREVIOUS"):
            if self._zoom_prev:
                sc, ox, oy = self._zoom_prev.pop()
                self.scale = sc; self.offset_x = ox; self.offset_y = oy
                self._redraw()
                self._echo(f"ZOOM P — vista anterior restaurada")
            else:
                self._echo("ZOOM P — no hay vista anterior")
        else:
            try:
                factor = float(sub.rstrip("Xx"))
                self.scale = factor * 40.0
                self._redraw()
            except ValueError:
                self._echo(f"ZOOM: opciones E A W P  o número de escala")

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

    def _echo(self, msg: str):
        """Muestra mensaje en la etiqueta de operación (barra de estado).
        NO toca _cmd_var para no destruir lo que el usuario esté escribiendo."""
        self._lbl_op.configure(text=msg, text_color=UI_WARN)
        # Cancelar timeout anterior si existe
        if hasattr(self, "_echo_after_id") and self._echo_after_id:
            try:
                self.root.after_cancel(self._echo_after_id)
            except Exception:
                pass
        def _clear():
            # Solo limpiar si el mensaje actual sigue siendo el mismo
            if self._lbl_op.cget("text") == msg:
                self._lbl_op.configure(text="", text_color=UI_WARN)
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
            self._hist_frame.pack(side="top", fill="x")
            self._btn_hist.configure(text="▼")
            self._hist_txt.see("end")
        else:
            self._hist_frame.pack_forget()
            self._btn_hist.configure(text="▲")

    def _add_to_history(self, text: str, tag: str = "cad"):
        """Añade una línea al panel de historial con su color de tag."""
        self._hist_txt.configure(state="normal")
        self._hist_txt.insert("end", text + "\n", tag)
        # mantener máximo 200 líneas
        lines = int(self._hist_txt.index("end-1c").split(".")[0])
        if lines > 200:
            self._hist_txt.delete("1.0", "10.0")
        self._hist_txt.see("end")
        self._hist_txt.configure(state="disabled")

    def _on_cmd_change(self, *_):
        """Cambia el color del prompt según el modo CAD vs IA y actualiza el hint semántico."""
        raw = self._cmd_var.get()
        txt = raw.strip().upper()

        # ── Color del prompt ─────────────────────────────────────────
        if raw.startswith("/"):
            self._cmd_prompt_lbl.configure(text_color="#38BDF8")   # azul IA
        else:
            self._cmd_prompt_lbl.configure(text_color=CV_CMD_FG)   # verde CAD

        # ── Hint semántico ───────────────────────────────────────────
        hint = ""
        if raw.startswith("/"):
            hint = "🤖  IA activa — escribe tu consulta en lenguaje natural"
        elif txt:
            # 1) Coincidencia exacta en aliases
            action = _CMD_ALIASES.get(txt)
            if action:
                hint = _CMD_DESCRIPTIONS.get(action, "")
            else:
                # 2) Prefijo: buscar aliases que comiencen con el texto
                matches = [k for k in _CMD_ALIASES if k.startswith(txt)]
                if matches:
                    key = min(matches, key=len)
                    action = _CMD_ALIASES.get(key)
                    if action:
                        desc = _CMD_DESCRIPTIONS.get(action, "")
                        hint = (f"→ {key}   {desc}" if key != txt else desc)
        else:
            # Input vacío: mostrar contexto del tool activo
            _TOOL_HINT = {
                "line":     "LINE activo — clic = punto siguiente",
                "polyline": "PLINE activo — clic = punto · Enter = cerrar",
                "circle":   "CIRCLE activo — clic = centro",
                "arc":      "ARC activo — clic = primer punto",
                "text":     "TEXT activo — clic = posición",
                "move":     "MOVE — selecciona entidades a mover",
                "copy":     "COPY — selecciona entidades a copiar",
                "rotate":   "ROTATE — selecciona entidades",
                "scale":    "SCALE — selecciona entidades",
                "mirror":   "MIRROR — primer punto del eje",
                "offset":   "OFFSET — escribe distancia numérica",
                "trim":     "TRIM — clic en segmento a recortar",
                "extend":   "EXTEND — clic en entidad a extender",
                "dist":     "DISTANCE — primer punto",
                "area_cmd": "AREA — clic en puntos del polígono · Enter = calcular",
            }
            hint = _TOOL_HINT.get(self.tool, "")

        self._cmd_hint_lbl.configure(text=hint)

    # ─── Motor IA ─────────────────────────────────────────────────
    def _ejecutar_ia(self, prompt: str):
        """Lanza el comando IA en un hilo separado para no bloquear la UI."""
        import threading
        self._lbl_op.configure(text="IA procesando...")
        self._cmd_prompt_lbl.configure(text_color="#38BDF8")

        def _run():
            resultado = self._llamar_ia(prompt)
            self.root.after(0, lambda: self._ia_respuesta(resultado))

        threading.Thread(target=_run, daemon=True).start()

    # ─── Configuración (snaps + IA) ───────────────────────────────

    def _cfg_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "config", "settings.json")

    def _leer_config_ia(self) -> dict:
        try:
            import json
            with open(self._cfg_path(), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _guardar_config_ia(self, provider: str, api_key: str,
                           model: str, max_tokens: int = 500):
        import json
        cfg = self._leer_config_ia()
        cfg["provider"]   = provider
        cfg["model"]      = model
        cfg[f"{provider}_api_key"] = api_key
        cfg["max_tokens"] = max_tokens
        path = self._cfg_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

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

    def _open_config(self):
        """Panel ⚙ Configuración — Snaps + Asistente IA."""
        if hasattr(self, "_cfg_win") and self._cfg_win.winfo_exists():
            self._cfg_win.lift(); return

        win = ctk.CTkToplevel(self.root)
        win.title("⚙  Configuración")
        win.geometry("460x600")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        self._cfg_win = win

        _SMALL = ctk.CTkFont(size=10)
        _HEAD  = ctk.CTkFont(size=12, weight="bold")
        pad = {"padx": 16, "pady": (14, 4)}

        # ══ SNAPS ══════════════════════════════════════════════════
        ctk.CTkLabel(win, text="SNAPS", font=_HEAD,
                     text_color=UI_TEXT2).pack(anchor="w", **pad)

        snap_card = ctk.CTkFrame(win, fg_color=UI_CARD, corner_radius=8)
        snap_card.pack(fill="x", padx=16, pady=(0, 10))

        # Toggle global
        sg_var = tk.BooleanVar(value=self.snap_on)
        def _global_snap():
            self.snap_on = sg_var.get(); self._update_flags()
        ctk.CTkSwitch(snap_card, text="SNAP global activado",
                      variable=sg_var, command=_global_snap,
                      font=_SMALL).pack(anchor="w", padx=14, pady=(10, 6))

        ctk.CTkFrame(snap_card, height=1, fg_color=UI_BORD).pack(fill="x", padx=10)

        # Grid 4×2 de checkboxes
        _SNAP_INFO = [
            ("end", "END",  "Extremos de líneas/arcos"),
            ("mid", "MID",  "Punto medio de segmentos"),
            ("cen", "CEN",  "Centro de círculo/arco/polígono"),
            ("qua", "QUA",  "Cuadrantes 0° 90° 180° 270°"),
            ("int", "INT",  "Intersección entre entidades"),
            ("gri", "GRI",  "Intersección de grilla"),
            ("per", "PER",  "Pie perpendicular a entidad"),
            ("tan", "TAN",  "Tangente desde punto externo"),
            ("nea", "NEA",  "Punto más cercano en entidad"),
        ]
        grid = ctk.CTkFrame(snap_card, fg_color="transparent")
        grid.pack(fill="x", padx=10, pady=(6, 10))
        for i, (key, abbr, desc) in enumerate(_SNAP_INFO):
            row, col = divmod(i, 2)
            var = tk.BooleanVar(value=self._snap_types.get(key, True))
            def _on_chk(k=key, v=var):
                self._snap_types[k] = v.get()
            ctk.CTkCheckBox(
                grid, text=f"{abbr}  {desc}",
                variable=var, command=_on_chk,
                font=_SMALL, checkbox_width=15, checkbox_height=15,
            ).grid(row=row, column=col, sticky="w", padx=10, pady=3)

        # ══ ASISTENTE IA ═══════════════════════════════════════════
        ctk.CTkLabel(win, text="ASISTENTE IA", font=_HEAD,
                     text_color=UI_TEXT2).pack(anchor="w", padx=16, pady=(6, 4))

        ia_card = ctk.CTkFrame(win, fg_color=UI_CARD, corner_radius=8)
        ia_card.pack(fill="x", padx=16, pady=(0, 12))

        cfg = self._leer_config_ia()

        _MODELS: dict[str, list] = {
            "anthropic": ["claude-opus-4-5", "claude-sonnet-4-5",
                          "claude-3-5-haiku-20241022"],
            "openai":    ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
            "gemini":    ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
        }

        def _lbl(parent, text):
            ctk.CTkLabel(parent, text=text, font=_SMALL,
                         text_color=UI_TEXT2, anchor="w", width=90).pack(
                side="left", padx=(14, 4))

        # Proveedor
        row_p = ctk.CTkFrame(ia_card, fg_color="transparent")
        row_p.pack(fill="x", pady=(12, 4))
        _lbl(row_p, "Proveedor:")
        prov_var = tk.StringVar(value=cfg.get("provider", "anthropic"))
        prov_om  = ctk.CTkOptionMenu(
            row_p, values=["anthropic", "openai", "gemini"],
            variable=prov_var, width=180, height=26, font=_SMALL)
        prov_om.pack(side="left")

        # Modelo
        row_m = ctk.CTkFrame(ia_card, fg_color="transparent")
        row_m.pack(fill="x", pady=4)
        _lbl(row_m, "Modelo:")
        model_var = tk.StringVar(
            value=cfg.get("model", _MODELS["anthropic"][0]))
        model_om = ctk.CTkOptionMenu(
            row_m, values=_MODELS.get(prov_var.get(), []),
            variable=model_var, width=240, height=26, font=_SMALL)
        model_om.pack(side="left")

        # Actualizar modelo al cambiar proveedor
        def _on_prov(p):
            ms = _MODELS.get(p, [])
            model_om.configure(values=ms)
            if ms: model_var.set(ms[0])
            key_var.set(cfg.get(f"{p}_api_key", ""))
        prov_om.configure(command=_on_prov)

        # API Key
        row_k = ctk.CTkFrame(ia_card, fg_color="transparent")
        row_k.pack(fill="x", pady=4)
        _lbl(row_k, "API Key:")
        key_var = tk.StringVar(
            value=cfg.get(f"{prov_var.get()}_api_key", ""))
        ctk.CTkEntry(
            row_k, textvariable=key_var, show="•",
            width=240, height=26, font=_SMALL,
            placeholder_text="sk-…  /  sk-ant-…  /  AIza…"
        ).pack(side="left")

        # Max tokens
        row_t = ctk.CTkFrame(ia_card, fg_color="transparent")
        row_t.pack(fill="x", pady=4)
        _lbl(row_t, "Max tokens:")
        tok_var = tk.StringVar(value=str(cfg.get("max_tokens", 500)))
        ctk.CTkEntry(
            row_t, textvariable=tok_var, width=80, height=26, font=_SMALL
        ).pack(side="left")

        # Status + botones
        status_lbl = ctk.CTkLabel(ia_card, text="",
                                   font=ctk.CTkFont(size=9),
                                   text_color=UI_TEXT2)
        status_lbl.pack(pady=(6, 2))

        def _test():
            status_lbl.configure(text="Probando…", text_color=UI_TEXT2)
            win.update()
            ok = self._test_ia_connection(
                prov_var.get(), key_var.get().strip(), model_var.get())
            if ok:
                status_lbl.configure(text=f"✓  {ok}", text_color=UI_SUCC)
            else:
                status_lbl.configure(
                    text="✗  Error — verifica la API Key", text_color=UI_ERR)

        def _save():
            try:
                mt = int(tok_var.get())
            except ValueError:
                mt = 500
            self._guardar_config_ia(
                prov_var.get(), key_var.get().strip(), model_var.get(), mt)
            status_lbl.configure(text="✓  Guardado", text_color=UI_SUCC)

        row_b = ctk.CTkFrame(ia_card, fg_color="transparent")
        row_b.pack(fill="x", padx=14, pady=(4, 14))
        ctk.CTkButton(row_b, text="Probar conexión", width=130, height=28,
                      fg_color=UI_CARD, hover_color=UI_ACC,
                      font=_SMALL, command=_test).pack(side="left", padx=(0, 8))
        ctk.CTkButton(row_b, text="Guardar", width=100, height=28,
                      fg_color=UI_SUCC, hover_color="#15803D",
                      font=_SMALL, command=_save).pack(side="left")

    def _llamar_ia(self, prompt: str) -> dict:
        """Llama a la IA configurada. Retorna {text, entities, cmds}."""
        # Construir resumen del dibujo
        tipos = {}
        for e in self.entities:
            t = type(e).__name__
            tipos[t] = tipos.get(t, 0) + 1
        resumen_ents = "  ".join(f"{v} {k}" for k, v in tipos.items()) or "dibujo vacío"
        capas_activas = [n for n, l in self.layers.items() if l.visible]

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
        )

        ctx_dibujo = (
            f"Estado actual del dibujo:\n"
            f"  Entidades: {resumen_ents}\n"
            f"  Capa activa: {self.active_layer}\n"
            f"  Capas visibles: {', '.join(capas_activas)}\n"
            f"  Escala visual: 1:{max(1, int(1/self.scale*1000))}"
        )

        # ── Leer configuración del proveedor ────────────────────────
        cfg      = self._leer_config_ia()
        provider = cfg.get("provider", "anthropic")
        model    = cfg.get("model", "claude-opus-4-5")
        api_key  = (cfg.get(f"{provider}_api_key")
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

    def _ia_respuesta(self, resultado: dict):
        """Procesa y muestra la respuesta IA, crea entidades si las hay."""
        self._lbl_op.configure(text="")
        self._cmd_prompt_lbl.configure(text_color=CV_CMD_FG)

        texto    = resultado.get("text", "")
        entities = resultado.get("entities", [])
        cmds     = resultado.get("cmds", [])

        # Mostrar texto (primera línea en la barra, todo en historial)
        primera = texto.split("\n")[0][:110] if texto else "OK"
        self._echo(f"IA: {primera}")
        self._add_to_history(f"IA: {texto}", "resp")

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

        # Ejecutar comandos sugeridos
        for cmd in cmds:
            accion = _CMD_ALIASES.get(cmd.strip().upper())
            if accion:
                self._ejecutar_accion(accion, "")

        if entities or cmds:
            self._redraw()

    def _mostrar_ayuda(self):
        txt = (
            "COMANDOS — Estudio Merlos CAD  (compatibles AutoCAD)\n"
            "═══════════════════════════════════════════════════\n"
            "DIBUJO\n"
            "  L / LINE         Linea\n"
            "  PL / PLINE       Polilinea  (C=cerrar, U=deshacer pt, Enter=fin)\n"
            "  REC / RECTANG    Rectangulo\n"
            "  C / CIRCLE       Circulo  (centro -> radio)\n"
            "  A / ARC          Arco  (inicio -> punto en arco -> final)\n"
            "  T / TEXT / DT    Texto\n"
            "\nEDICION\n"
            "  E / ERASE        Borrar seleccion  (o tecla Delete)\n"
            "  M / MOVE         Mover  (sel. primero -> base -> destino)\n"
            "  CO / COPY        Copiar  (multi-destino, ESC=fin)\n"
            "  RO / ROTATE      Rotar  (sel. -> base -> angulo o clic)\n"
            "  SC / SCALE       Escalar  (sel. -> base -> factor o clic)\n"
            "  MI / MIRROR      Espejo  (sel. -> eje p1 -> eje p2)\n"
            "  O / OFFSET       Paralela  (dist -> clic entidad + lado)\n"
            "  TR / TRIM        Recortar  (clic borde -> clic a recortar)\n"
            "  EX / EXTEND      Extender  (clic limite -> clic extremo)\n"
            "  X / EXPLODE      Explotar polilinea en lineas\n"
            "  MA / MATCHPROP   Igualar capa  (clic fuente -> clic destinos, ESC=fin)\n"
            "  U / UNDO         Deshacer  (Ctrl+Z) — incluye cambios de capas\n"
            "  REDO             Rehacer  (Ctrl+Y)\n"
            "  Ctrl+A           Seleccionar todo (respeta capas bloqueadas)\n"
            "  Doble clic       Editar texto\n"
            "\nSELECCION\n"
            "  Clic             Seleccion puntual\n"
            "  Arrastrar izq->der   Ventana (azul, solo dentro)\n"
            "  Arrastrar der->izq   Cruce   (verde, toca el rect)\n"
            "\nVISTA\n"
            "  ZE / Z E         Zoom Extents (solo capas visibles)\n"
            "  Z W              Zoom Ventana\n"
            "  Z P              Zoom Anterior\n"
            "  Z numero         Zoom escala\n"
            "  Rueda            Zoom centrado en cursor\n"
            "  Boton central    Pan\n"
            "  RE / REGEN       Regenerar\n"
            "\nCOORDENADAS EN LINEA DE COMANDO\n"
            "  1.5,3.0          Absoluta\n"
            "  @1,0             Relativa\n"
            "  @2<45            Polar relativa (dist<angulo)\n"
            "\nCAPAS\n"
            "  LA nombre        Activar capa  (ej: LA A-MURO)\n"
            "  LAYISO           Aislar capa activa (oculta las demas)\n"
            "  LAYON            Mostrar todas las capas\n"
            "  LAYOFF nombre    Apagar capa\n"
            "  LAYLOCK nombre   Bloquear capa\n"
            "  LAYUNLOCK nombre Desbloquear capa\n"
            "  LAYMCUR          Clic en entidad -> su capa queda activa\n"
            "\nMEDICION\n"
            "  DI / DIST        Distancia entre dos puntos\n"
            "  LI / LIST        Info de entidad seleccionada\n"
            "  AREA             Area de polilinea o circulo\n"
            "\nCONFIGURACION\n"
            "  SPACE            Repetir ultimo comando\n"
            "  F1 / ?           Ayuda\n"
            "  F3 / F9          Toggle OSNAP\n"
            "  F7               Toggle GRILLA\n"
            "  F8               Toggle ORTO\n"
            "\nEXPORTAR / ARCHIVO\n"
            "  DXF              Exportar DXF\n"
            "  PNG              Captura PNG\n"
            "  Ctrl+S / SAVE    Guardar JSON\n"
            "  Ctrl+O / OPEN    Abrir JSON\n"
            "  Ctrl+N / NEW     Nuevo dibujo\n"
            "\nASISTENTE IA\n"
            "  /texto           Consultar IA  (ej: /dibuja un cuarto de 4x3)"
        )
        messagebox.showinfo("Ayuda — Comandos CAD", txt, parent=self.root)

    # ─── Nuevo dibujo ─────────────────────────────────────────────
    def _new_dwg(self):
        if self.entities:
            if not messagebox.askyesno(
                    "Nuevo", "¿Crear nuevo dibujo?\nSe perderán cambios no guardados.",
                    parent=self.root):
                return
        self._push_undo()
        self.entities.clear()
        # Resetear capas a los valores por defecto
        self.layers = {
            k: Layer(**{f.name: getattr(v, f.name)
                        for f in v.__dataclass_fields__.values()})
            for k, v in DEFAULT_LAYERS.items()
        }
        self.active_layer = "A-MURO"
        self._om_capa.configure(values=list(self.layers.keys()))
        self._om_capa.set(self.active_layer)
        self._build_layer_panel()
        self._current_ruta = ""
        self.root.title("Estudio Merlos CAD  v1")
        if hasattr(self, "_lbl_file"):
            self._lbl_file.configure(text="Sin guardar")
        self._rebuild_snap_index()
        self._redraw()
        self._echo("Nuevo dibujo")

    # ─── Exportar / Guardar ───────────────────────────────────────
    def _exportar_dxf(self):
        ruta = filedialog.asksaveasfilename(
            parent=self.root, title="Exportar DXF",
            defaultextension=".dxf",
            filetypes=[("DXF", "*.dxf"), ("Todos", "*.*")])
        if not ruta:
            return
        try:
            from cad.dxf_export import exportar_dxf
            exportar_dxf(self.entities, self.layers, ruta)
            self._echo(f"DXF: {os.path.basename(ruta)}")
        except ImportError:
            messagebox.showwarning("ezdxf", "pip install ezdxf", parent=self.root)
        except Exception as ex:
            messagebox.showerror("Error DXF", str(ex), parent=self.root)

    def _exportar_png(self):
        ruta = filedialog.asksaveasfilename(
            parent=self.root, title="Exportar PNG",
            defaultextension=".png", filetypes=[("PNG", "*.png")])
        if not ruta:
            return
        try:
            from PIL import ImageGrab
            x = self.canvas.winfo_rootx(); y = self.canvas.winfo_rooty()
            w = self.canvas.winfo_width();  h = self.canvas.winfo_height()
            ImageGrab.grab((x, y, x+w, y+h)).save(ruta)
            self._echo(f"PNG: {os.path.basename(ruta)}")
        except Exception as ex:
            messagebox.showerror("Error PNG", str(ex), parent=self.root)

    def _guardar_json(self, *_):
        """Ctrl+S — guarda en la ruta actual; si no existe pide nombre."""
        if self._current_ruta:
            self._escribir_json(self._current_ruta)
        else:
            self._guardar_json_como()

    def _guardar_json_como(self, *_):
        """SAVEAS — siempre pide nombre."""
        import json as _j
        ruta = filedialog.asksaveasfilename(
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
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self._current_ruta = ruta
        nombre = os.path.basename(ruta)
        self.root.title(f"Estudio Merlos CAD — {nombre}")
        if hasattr(self, "_lbl_file"):
            self._lbl_file.configure(text=nombre)
        self._echo(f"Guardado: {nombre}")

    def _abrir_json(self):
        import json
        ruta = filedialog.askopenfilename(
            parent=self.root, title="Abrir plano",
            filetypes=[("JSON plano", "*.json"), ("Todos", "*.*")])
        if not ruta:
            return
        try:
            with open(ruta, encoding="utf-8") as f:
                data = json.load(f)
            self._push_undo()
            self.entities.clear()
            # Restaurar capas
            if "layers" in data:
                self.layers.clear()
                for n, ld in data["layers"].items():
                    self.layers[n] = Layer(
                        name=n,
                        color=ld.get("color", "#FFFFFF"),
                        linewidth=ld.get("linewidth", 1),
                        visible=ld.get("visible", True),
                        locked=ld.get("locked", False),
                        linetype=ld.get("linetype", "CONTINUOUS"),
                    )
            if "active_layer" in data and data["active_layer"] in self.layers:
                self.active_layer = data["active_layer"]
                self._om_capa.set(self.active_layer)
            for ed in data.get("entities", []):
                t = ed.get("tipo")
                if t == "line":
                    self.entities.append(Line(
                        x1=ed["x1"],y1=ed["y1"],x2=ed["x2"],y2=ed["y2"],
                        layer=ed.get("layer","A-MURO")))
                elif t == "polyline":
                    self.entities.append(Polyline(
                        points=ed["points"],closed=ed.get("closed",False),
                        layer=ed.get("layer","A-MURO")))
                elif t == "circle":
                    self.entities.append(Circle(
                        cx=ed["cx"],cy=ed["cy"],radius=ed["radius"],
                        layer=ed.get("layer","A-MURO")))
                elif t == "arc":
                    self.entities.append(Arc(
                        cx=ed["cx"],cy=ed["cy"],radius=ed["radius"],
                        start_ang=ed.get("start_ang",0.0),
                        end_ang=ed.get("end_ang",180.0),
                        ccw=ed.get("ccw",True),
                        layer=ed.get("layer","A-MURO")))
                elif t == "text":
                    self.entities.append(Text(
                        x=ed["x"],y=ed["y"],content=ed["content"],
                        height=ed.get("height",0.20),
                        angle=ed.get("angle", 0.0),
                        layer=ed.get("layer","A-TEXTO")))
            self._rebuild_snap_index()
            self._om_capa.configure(values=list(self.layers.keys()))
            self._build_layer_panel()
            self._zoom_extents()
            self._current_ruta = ruta
            nombre = os.path.basename(ruta)
            self.root.title(f"Estudio Merlos CAD — {nombre}")
            if hasattr(self, "_lbl_file"):
                self._lbl_file.configure(text=nombre)
        except Exception as ex:
            messagebox.showerror("Error", str(ex), parent=self.root)


# ═══════════════════════════════════════════════════════════════════
# UTILIDADES GEOMÉTRICAS
# ═══════════════════════════════════════════════════════════════════

def _dist_pt_seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx-ax, by-ay
    if dx == dy == 0:
        return math.hypot(px-ax, py-ay)
    t = max(0.0, min(1.0, ((px-ax)*dx+(py-ay)*dy)/(dx*dx+dy*dy)))
    return math.hypot(px-(ax+t*dx), py-(ay+t*dy))


# ═══════════════════════════════════════════════════════════════════
# GEOMETRÍA — usados por rotated/scaled/mirrored de las entidades
# ═══════════════════════════════════════════════════════════════════

def _rotate_point(x: float, y: float, cx: float, cy: float, deg: float):
    """Rota (x,y) alrededor de (cx,cy) en grados CCW."""
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    dx, dy = x-cx, y-cy
    return cx + dx*c - dy*s, cy + dx*s + dy*c


def _mirror_point(x: float, y: float, ax: float, ay: float, bx: float, by: float):
    """Refleja (x,y) en la recta (ax,ay)→(bx,by)."""
    dx, dy = bx-ax, by-ay
    d2 = dx*dx + dy*dy
    if d2 < 1e-18:
        return x, y
    t = ((x-ax)*dx + (y-ay)*dy) / d2
    fx, fy = ax+t*dx, ay+t*dy
    return 2*fx - x, 2*fy - y


def _offset_line(e: "Line", dist: float, side_wx: float, side_wy: float):
    """Genera una Line paralela a dist en el lado indicado por (side_wx, side_wy)."""
    dx, dy = e.x2-e.x1, e.y2-e.y1
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return None
    nx, ny = -dy/L, dx/L     # normal hacia la izquierda
    mid_x = (e.x1+e.x2)/2; mid_y = (e.y1+e.y2)/2
    if (side_wx-mid_x)*nx + (side_wy-mid_y)*ny < 0:
        nx, ny = -nx, -ny     # flip hacia el lado correcto
    return Line(x1=e.x1+nx*dist, y1=e.y1+ny*dist,
                x2=e.x2+nx*dist, y2=e.y2+ny*dist, layer=e.layer)


def _seg_intersect(x1, y1, x2, y2, x3, y3, x4, y4,
                   t_range=(0.0, 1.0), u_range=(0.0, 1.0)):
    """Intersección paramétrica entre dos segmentos (o rectas si se amplía el rango).
    Retorna (ix, iy, t, u) o None si son paralelas o fuera de rango."""
    d1x, d1y = x2-x1, y2-y1
    d2x, d2y = x4-x3, y4-y3
    cross = d1x*d2y - d1y*d2x
    if abs(cross) < 1e-10:
        return None
    t = ((x3-x1)*d2y - (y3-y1)*d2x) / cross
    u = ((x3-x1)*d1y - (y3-y1)*d1x) / cross
    if t_range[0] <= t <= t_range[1] and u_range[0] <= u <= u_range[1]:
        return x1+t*d1x, y1+t*d1y, t, u
    return None


def _circumcircle(p1, p2, p3):
    """Circumscrito de 3 puntos. Retorna (cx, cy, r) o None si son colineales."""
    ax, ay = p1; bx, by = p2; cx, cy = p3
    D = 2*(ax*(by-cy) + bx*(cy-ay) + cx*(ay-by))
    if abs(D) < 1e-10:
        return None
    a2, b2, c2 = ax*ax+ay*ay, bx*bx+by*by, cx*cx+cy*cy
    ux = (a2*(by-cy) + b2*(cy-ay) + c2*(ay-by)) / D
    uy = (a2*(cx-bx) + b2*(ax-cx) + c2*(bx-ax)) / D
    return ux, uy, math.hypot(ax-ux, ay-uy)


def _angle_in_arc(ang: float, start: float, end: float, ccw: bool) -> bool:
    """True si ang (en grados, 0-360) cae dentro del arco."""
    ang %= 360; start %= 360; end %= 360
    if ccw:
        if start <= end:
            return start <= ang <= end
        return ang >= start or ang <= end
    else:
        if start >= end:
            return end <= ang <= start
        return ang <= start or ang >= end


def _offset_circle(e: "Circle", dist: float, side_wx: float, side_wy: float):
    """Genera un Circle concéntrico más grande o más pequeño según el lado."""
    d_click = math.hypot(side_wx-e.cx, side_wy-e.cy)
    new_r = e.radius + dist if d_click >= e.radius else e.radius - dist
    if new_r <= 0:
        return None
    return Circle(cx=e.cx, cy=e.cy, radius=new_r, layer=e.layer)


def _offset_polyline(e: "Polyline", dist: float,
                     side_wx: float, side_wy: float) -> "Optional[Polyline]":
    """Genera una Polyline paralela con juntas en mitra.
    Para cada vértice interior calcula la intersección de los dos segmentos
    adyacentes ya desplazados; en los extremos sólo desplaza perpendicularmente.
    """
    pts = e.points
    if len(pts) < 2:
        return None

    # 1. Calcular la normal de cada segmento
    normals = []
    for i in range(len(pts) - 1):
        dx = pts[i+1][0] - pts[i][0]
        dy = pts[i+1][1] - pts[i][1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            normals.append((0.0, 0.0))
        else:
            normals.append((-dy/L, dx/L))   # normal izquierda (CCW)

    # 2. Determinar el lado usando el primer segmento
    nx0, ny0 = normals[0]
    mx0 = (pts[0][0]+pts[1][0])/2; my0 = (pts[0][1]+pts[1][1])/2
    if (side_wx-mx0)*nx0 + (side_wy-my0)*ny0 < 0:
        normals = [(-nx, -ny) for nx, ny in normals]

    # 3. Calcular los vértices desplazados
    new_pts = []
    n_segs = len(normals)
    for i in range(len(pts)):
        if i == 0:                          # extremo inicial
            nx, ny = normals[0]
            new_pts.append((pts[0][0]+nx*dist, pts[0][1]+ny*dist))
        elif i == len(pts)-1:              # extremo final
            nx, ny = normals[-1]
            new_pts.append((pts[-1][0]+nx*dist, pts[-1][1]+ny*dist))
        else:                               # vértice interior — mitra
            n_prev = normals[i-1]; n_next = normals[i]
            # Intersección de las dos rectas desplazadas
            # seg_prev: desde pts[i-1]+n_prev*dist hacia pts[i]+n_prev*dist
            # seg_next: desde pts[i]+n_next*dist hacia pts[i+1]+n_next*dist
            p1x = pts[i-1][0]+n_prev[0]*dist; p1y = pts[i-1][1]+n_prev[1]*dist
            p2x = pts[i][0]+n_prev[0]*dist;   p2y = pts[i][1]+n_prev[1]*dist
            p3x = pts[i][0]+n_next[0]*dist;   p3y = pts[i][1]+n_next[1]*dist
            p4x = pts[i+1][0]+n_next[0]*dist; p4y = pts[i+1][1]+n_next[1]*dist
            res = _seg_intersect(p1x,p1y,p2x,p2y,p3x,p3y,p4x,p4y,
                                 t_range=(-10.0,10.0), u_range=(-10.0,10.0))
            if res:
                new_pts.append((res[0], res[1]))
            else:   # paralelos — usa punto medio simple
                mx = (p2x+p3x)/2; my = (p2y+p3y)/2
                new_pts.append((mx, my))

    return Polyline(points=new_pts, closed=e.closed, layer=e.layer)
