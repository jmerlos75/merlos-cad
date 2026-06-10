"""
cad/entities.py
===============
Kernel de entidades CAD: dataclasses, capa, constantes y geometría pura.

No depende de Tkinter, PIL ni ningún framework de UI.
Puede importarse desde cualquier módulo del sistema (SLE, IA, DXF, tests).

Exporta:
    Entity, Line, Polyline, Circle, Text, Arc, Dimension, Hatch
    BlockDef, Insert, Ellipse, XLine, Leader
    Layer, DEFAULT_LAYERS
    _LINETYPES, _LT_CYCLE, _LT_ABBR
    _SNAP_CELL, _LOD_TEXT_PX, _LOD_DOT_SCALE
    _cell, _rotate_point, _mirror_point
    _dist_pt_seg, _angle_in_arc
    _seg_intersect, _intersect_seg_circle, _pt_on_arc
    _circumcircle
    _offset_line, _offset_circle, _offset_polyline
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ── Constantes ───────────────────────────────────────────────────────────────

_SNAP_CELL    = 5.0   # tamaño de celda del índice espacial (unidades mundo)
_LOD_TEXT_PX  = 5     # skip texto cuando alto en px < este valor
_LOD_DOT_SCALE = 10   # skip puntito de Circle cuando scale < este valor


# ── Geometría pura ────────────────────────────────────────────────────────────

def _cell(v: float) -> int:
    """Índice de celda del spatial index para la coordenada v."""
    return int(math.floor(v / _SNAP_CELL))


def _rotate_point(x: float, y: float,
                  cx: float, cy: float, deg: float) -> tuple[float, float]:
    """Rota (x, y) alrededor de (cx, cy) en grados CCW."""
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    dx, dy = x - cx, y - cy
    return cx + dx*c - dy*s, cy + dx*s + dy*c


def _mirror_point(x: float, y: float,
                  ax: float, ay: float,
                  bx: float, by: float) -> tuple[float, float]:
    """Refleja (x, y) en la recta (ax, ay) → (bx, by)."""
    dx, dy = bx - ax, by - ay
    d2 = dx*dx + dy*dy
    if d2 < 1e-18:
        return x, y
    t = ((x - ax)*dx + (y - ay)*dy) / d2
    fx, fy = ax + t*dx, ay + t*dy
    return 2*fx - x, 2*fy - y


def _dist_pt_seg(px: float, py: float,
                 ax: float, ay: float,
                 bx: float, by: float) -> float:
    """Distancia de punto (px, py) al segmento (ax,ay)–(bx,by)."""
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax)*dx + (py - ay)*dy) / (dx*dx + dy*dy)))
    return math.hypot(px - (ax + t*dx), py - (ay + t*dy))


def _angle_in_arc(ang: float, start: float, end: float, ccw: bool) -> bool:
    """True si ang (grados, 0–360) cae dentro del arco start→end."""
    ang %= 360; start %= 360; end %= 360
    if ccw:
        if start <= end:
            return start <= ang <= end
        return ang >= start or ang <= end
    else:
        if start >= end:
            return end <= ang <= start
        return ang <= start or ang >= end


def _seg_intersect(x1: float, y1: float, x2: float, y2: float,
                   x3: float, y3: float, x4: float, y4: float,
                   t_range: tuple = (0.0, 1.0),
                   u_range: tuple = (0.0, 1.0)) -> Optional[tuple]:
    """Intersección paramétrica de dos segmentos (o rectas si se amplía el rango).
    Retorna (ix, iy, t, u) o None si son paralelos o fuera de rango."""
    d1x, d1y = x2 - x1, y2 - y1
    d2x, d2y = x4 - x3, y4 - y3
    cross = d1x*d2y - d1y*d2x
    if abs(cross) < 1e-10:
        return None
    t = ((x3 - x1)*d2y - (y3 - y1)*d2x) / cross
    u = ((x3 - x1)*d1y - (y3 - y1)*d1x) / cross
    if t_range[0] <= t <= t_range[1] and u_range[0] <= u <= u_range[1]:
        return x1 + t*d1x, y1 + t*d1y, t, u
    return None


def _intersect_seg_circle(x1: float, y1: float, x2: float, y2: float,
                          cx: float, cy: float, r: float) -> list:
    """Puntos de intersección segmento–círculo (lista 0..2 puntos)."""
    dx, dy = x2 - x1, y2 - y1
    fx, fy = x1 - cx, y1 - cy
    a = dx*dx + dy*dy
    if a < 1e-16:
        return []
    b = 2*(fx*dx + fy*dy)
    c = fx*fx + fy*fy - r*r
    disc = b*b - 4*a*c
    if disc < 0:
        return []
    sq = math.sqrt(disc)
    pts = []
    for t in ((-b - sq) / (2*a), (-b + sq) / (2*a)):
        if 0.0 <= t <= 1.0:
            pts.append((x1 + t*dx, y1 + t*dy))
    return pts


def _pt_on_arc(pt: tuple, arc: "Arc") -> bool:
    """True si el punto (ya en la circunferencia) cae dentro del span del arco."""
    ang = math.degrees(math.atan2(pt[1] - arc.cy, pt[0] - arc.cx)) % 360
    span = ((arc.end_ang - arc.start_ang) % 360 if arc.ccw
            else (arc.start_ang - arc.end_ang) % 360)
    rel  = ((ang - arc.start_ang) % 360 if arc.ccw
            else (arc.start_ang - ang) % 360)
    return rel <= span


def _circumcircle(p1: tuple, p2: tuple,
                  p3: tuple) -> Optional[tuple[float, float, float]]:
    """Circunscrito de 3 puntos. Retorna (cx, cy, r) o None si son colineales."""
    ax, ay = p1; bx, by = p2; cx, cy = p3
    D = 2*(ax*(by - cy) + bx*(cy - ay) + cx*(ay - by))
    if abs(D) < 1e-10:
        return None
    a2, b2, c2 = ax*ax + ay*ay, bx*bx + by*by, cx*cx + cy*cy
    ux = (a2*(by - cy) + b2*(cy - ay) + c2*(ay - by)) / D
    uy = (a2*(cx - bx) + b2*(ax - cx) + c2*(bx - ax)) / D
    return ux, uy, math.hypot(ax - ux, ay - uy)


# ── Entidades ─────────────────────────────────────────────────────────────────

@dataclass
class Entity:
    layer:     str  = "A-MURO"
    color:     str  = "bylayer"
    linetype:  str  = "bylayer"   # "bylayer" | "CONT" | "DASH" | "CENTER" | "DOT"
    linewidth: int  = 0            # 0 = bylayer; 1-5 override
    selected:  bool = field(default=False, repr=False)

    def snap_points(self) -> list:
        return []

    def bbox_pts(self) -> list:
        """Puntos para bounding box (por defecto = snap_points)."""
        return self.snap_points()


@dataclass
class Line(Entity):
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0

    def snap_points(self) -> list:
        return [
            (self.x1, self.y1, "end"),
            (self.x2, self.y2, "end"),
            ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2, "mid"),
        ]

    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    def info(self) -> str:
        ang = math.degrees(math.atan2(self.y2 - self.y1, self.x2 - self.x1)) % 360
        return (f"LÍNEA\n"
                f"  inicio ({self.x1:.3f}, {self.y1:.3f})\n"
                f"  fin    ({self.x2:.3f}, {self.y2:.3f})\n"
                f"  largo  {self.length():.3f} m  ángulo {ang:.1f}°\n"
                f"  capa   {self.layer}")

    def translated(self, dx: float, dy: float) -> "Line":
        return Line(x1=self.x1+dx, y1=self.y1+dy,
                    x2=self.x2+dx, y2=self.y2+dy, layer=self.layer)

    def rotated(self, cx: float, cy: float, deg: float) -> "Line":
        x1, y1 = _rotate_point(self.x1, self.y1, cx, cy, deg)
        x2, y2 = _rotate_point(self.x2, self.y2, cx, cy, deg)
        return Line(x1=x1, y1=y1, x2=x2, y2=y2, layer=self.layer)

    def scaled(self, cx: float, cy: float, f: float) -> "Line":
        return Line(x1=cx+(self.x1-cx)*f, y1=cy+(self.y1-cy)*f,
                    x2=cx+(self.x2-cx)*f, y2=cy+(self.y2-cy)*f, layer=self.layer)

    def mirrored(self, ax: float, ay: float,
                 bx: float, by: float) -> "Line":
        x1, y1 = _mirror_point(self.x1, self.y1, ax, ay, bx, by)
        x2, y2 = _mirror_point(self.x2, self.y2, ax, ay, bx, by)
        return Line(x1=x1, y1=y1, x2=x2, y2=y2, layer=self.layer)


@dataclass
class Polyline(Entity):
    points: list = field(default_factory=list)
    closed: bool = False

    def snap_points(self) -> list:
        pts = [(x, y, "end") for x, y in self.points]
        n   = len(self.points)
        for i in range(n - 1):
            pts.append(((self.points[i][0] + self.points[i+1][0]) / 2,
                        (self.points[i][1] + self.points[i+1][1]) / 2, "mid"))
        if self.closed and n > 1:
            pts.append(((self.points[-1][0] + self.points[0][0]) / 2,
                        (self.points[-1][1] + self.points[0][1]) / 2, "mid"))
        if self.closed and n >= 3:
            cx = sum(p[0] for p in self.points) / n
            cy = sum(p[1] for p in self.points) / n
            pts.append((cx, cy, "cen"))
        return pts

    def perimeter(self) -> float:
        pts = self.points + ([self.points[0]] if self.closed and self.points else [])
        return sum(math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])
                   for i in range(len(pts) - 1))

    def area(self) -> float:
        if not self.closed or len(self.points) < 3:
            return 0.0
        n = len(self.points)
        return abs(sum(self.points[i][0] * self.points[(i+1) % n][1]
                       - self.points[(i+1) % n][0] * self.points[i][1]
                       for i in range(n))) / 2

    def info(self) -> str:
        cerrada = "cerrada" if self.closed else "abierta"
        area_txt = f"\n  área   {self.area():.3f} m²" if self.closed else ""
        pts_txt = "  ".join(f"({p[0]:.2f},{p[1]:.2f})" for p in self.points[:4])
        mas = f"  +{len(self.points)-4} más" if len(self.points) > 4 else ""
        return (f"POLILÍNEA  {cerrada}  {len(self.points)} vértices\n"
                f"  perímetro {self.perimeter():.3f} m{area_txt}\n"
                f"  verts  {pts_txt}{mas}\n"
                f"  capa   {self.layer}")

    def translated(self, dx: float, dy: float) -> "Polyline":
        return Polyline(points=[(x+dx, y+dy) for x, y in self.points],
                        closed=self.closed, layer=self.layer)

    def rotated(self, cx: float, cy: float, deg: float) -> "Polyline":
        return Polyline(
            points=[_rotate_point(x, y, cx, cy, deg) for x, y in self.points],
            closed=self.closed, layer=self.layer)

    def scaled(self, cx: float, cy: float, f: float) -> "Polyline":
        return Polyline(
            points=[(cx+(x-cx)*f, cy+(y-cy)*f) for x, y in self.points],
            closed=self.closed, layer=self.layer)

    def mirrored(self, ax: float, ay: float,
                 bx: float, by: float) -> "Polyline":
        return Polyline(
            points=[_mirror_point(x, y, ax, ay, bx, by) for x, y in self.points],
            closed=self.closed, layer=self.layer)


@dataclass
class Spline(Entity):
    """Curva Catmull-Rom que pasa por todos los puntos de control."""
    points: list = field(default_factory=list)
    closed: bool = False

    # ── interpolación Catmull-Rom ──────────────────────────────────────
    def interp(self, n_seg: int = 20) -> list:
        """Devuelve lista de puntos interpolados para renderizar."""
        pts = self.points
        n   = len(pts)
        if n < 2:
            return list(pts)
        if n == 2:
            # Línea recta entre dos puntos
            steps = n_seg
            return [(pts[0][0] + (pts[1][0]-pts[0][0])*k/steps,
                     pts[0][1] + (pts[1][1]-pts[0][1])*k/steps)
                    for k in range(steps+1)]
        # Cadena extendida con fantasmas en extremos
        if self.closed:
            chain = [pts[-1]] + list(pts) + [pts[0], pts[1]]
        else:
            chain = [pts[0]] + list(pts) + [pts[-1]]
        result = []
        segs = len(chain) - 3
        for i in range(segs):
            p0, p1, p2, p3 = chain[i], chain[i+1], chain[i+2], chain[i+3]
            end = n_seg if i < segs-1 else n_seg+1
            for k in range(end):
                t  = k / n_seg
                t2 = t * t
                t3 = t2 * t
                x = 0.5*((2*p1[0]) + (-p0[0]+p2[0])*t
                         + (2*p0[0]-5*p1[0]+4*p2[0]-p3[0])*t2
                         + (-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3)
                y = 0.5*((2*p1[1]) + (-p0[1]+p2[1])*t
                         + (2*p0[1]-5*p1[1]+4*p2[1]-p3[1])*t2
                         + (-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)
                result.append((x, y))
        return result

    def snap_points(self) -> list:
        pts = [(x, y, "end") for x, y in self.points]
        # Midpoints entre puntos de control consecutivos
        for i in range(len(self.points)-1):
            mx = (self.points[i][0]+self.points[i+1][0])/2
            my = (self.points[i][1]+self.points[i+1][1])/2
            pts.append((mx, my, "mid"))
        return pts

    def bbox_pts(self) -> list:
        return [(x, y, "end") for x, y in self.points]

    def info(self) -> str:
        cerrada = "cerrada" if self.closed else "abierta"
        pts_txt = "  ".join(f"({p[0]:.2f},{p[1]:.2f})" for p in self.points[:4])
        mas = f"  +{len(self.points)-4} más" if len(self.points) > 4 else ""
        return (f"SPLINE  {cerrada}  {len(self.points)} pts control\n"
                f"  {pts_txt}{mas}\n"
                f"  capa   {self.layer}")

    def translated(self, dx: float, dy: float) -> "Spline":
        return Spline(points=[(x+dx, y+dy) for x, y in self.points],
                      closed=self.closed, layer=self.layer, color=self.color,
                      linetype=self.linetype, linewidth=self.linewidth)

    def rotated(self, cx: float, cy: float, deg: float) -> "Spline":
        return Spline(
            points=[_rotate_point(x, y, cx, cy, deg) for x, y in self.points],
            closed=self.closed, layer=self.layer, color=self.color,
            linetype=self.linetype, linewidth=self.linewidth)

    def scaled(self, cx: float, cy: float, f: float) -> "Spline":
        return Spline(
            points=[(cx+(x-cx)*f, cy+(y-cy)*f) for x, y in self.points],
            closed=self.closed, layer=self.layer, color=self.color,
            linetype=self.linetype, linewidth=self.linewidth)

    def mirrored(self, ax: float, ay: float,
                 bx: float, by: float) -> "Spline":
        return Spline(
            points=[_mirror_point(x, y, ax, ay, bx, by) for x, y in self.points],
            closed=self.closed, layer=self.layer, color=self.color,
            linetype=self.linetype, linewidth=self.linewidth)


@dataclass
class Circle(Entity):
    cx:     float = 0.0
    cy:     float = 0.0
    radius: float = 1.0

    def snap_points(self) -> list:
        r = self.radius
        return [
            (self.cx,   self.cy,   "cen"),
            (self.cx+r, self.cy,   "qua"),
            (self.cx-r, self.cy,   "qua"),
            (self.cx,   self.cy+r, "qua"),
            (self.cx,   self.cy-r, "qua"),
        ]

    def info(self) -> str:
        return (f"CÍRCULO\n"
                f"  centro ({self.cx:.3f}, {self.cy:.3f})\n"
                f"  radio  {self.radius:.3f} m\n"
                f"  área   {math.pi*self.radius**2:.3f} m²  "
                f"perímetro {2*math.pi*self.radius:.3f} m\n"
                f"  capa   {self.layer}")

    def translated(self, dx: float, dy: float) -> "Circle":
        return Circle(cx=self.cx+dx, cy=self.cy+dy,
                      radius=self.radius, layer=self.layer)

    def rotated(self, cx: float, cy: float, deg: float) -> "Circle":
        nx, ny = _rotate_point(self.cx, self.cy, cx, cy, deg)
        return Circle(cx=nx, cy=ny, radius=self.radius, layer=self.layer)

    def scaled(self, cx: float, cy: float, f: float) -> "Circle":
        return Circle(cx=cx+(self.cx-cx)*f, cy=cy+(self.cy-cy)*f,
                      radius=self.radius*abs(f), layer=self.layer)

    def mirrored(self, ax: float, ay: float,
                 bx: float, by: float) -> "Circle":
        nx, ny = _mirror_point(self.cx, self.cy, ax, ay, bx, by)
        return Circle(cx=nx, cy=ny, radius=self.radius, layer=self.layer)


@dataclass
class Text(Entity):
    x:       float = 0.0
    y:       float = 0.0
    content: str   = ""
    height:  float = 0.20
    angle:       float = 0.0   # grados CCW desde eje X
    halign:      int   = 0     # 0=left  1=center  2=right
    valign:      int   = 0     # 0=baseline/bottom  2=middle  3=top
    mtext_width: float = 0.0   # ancho del cuadro MTEXT en unidades mundo (0=auto)
    layer:       str   = "A-TEXTO"
    raw_mtext:   str   = ""    # string MTEXT original con códigos de formato
                               # ({\fArial|b1;TEXTO}) — preservado para round-trip
                               # vacío en texto creado dentro del visor

    def snap_points(self) -> list:
        return [(self.x, self.y, "end")]

    def info(self) -> str:
        ang_str = f"\n  ángulo {self.angle:.1f}°" if self.angle else ""
        return (f'TEXTO\n'
                f'  contenido "{self.content}"\n'
                f'  pos    ({self.x:.3f}, {self.y:.3f})\n'
                f'  alto   {self.height:.4f} m{ang_str}\n'
                f'  capa   {self.layer}')

    def _kw(self) -> dict:
        """Campos comunes para todas las transformaciones."""
        return dict(content=self.content, height=self.height,
                    halign=self.halign, valign=self.valign,
                    mtext_width=self.mtext_width,
                    layer=self.layer, color=self.color,
                    linetype=self.linetype, linewidth=self.linewidth,
                    raw_mtext=self.raw_mtext)

    def translated(self, dx: float, dy: float) -> "Text":
        return Text(x=self.x+dx, y=self.y+dy, angle=self.angle, **self._kw())

    def rotated(self, cx: float, cy: float, deg: float) -> "Text":
        nx, ny = _rotate_point(self.x, self.y, cx, cy, deg)
        return Text(x=nx, y=ny, angle=(self.angle + deg) % 360, **self._kw())

    def scaled(self, cx: float, cy: float, f: float) -> "Text":
        kw = self._kw()
        kw["height"] = self.height * abs(f)
        kw["mtext_width"] = self.mtext_width * abs(f)
        return Text(x=cx+(self.x-cx)*f, y=cy+(self.y-cy)*f,
                    angle=self.angle, **kw)

    def mirrored(self, ax: float, ay: float,
                 bx: float, by: float) -> "Text":
        nx, ny = _mirror_point(self.x, self.y, ax, ay, bx, by)
        axis_ang = math.degrees(math.atan2(by - ay, bx - ax))
        new_angle = (2*axis_ang - self.angle) % 360
        return Text(x=nx, y=ny, angle=new_angle, **self._kw())


@dataclass
class Arc(Entity):
    """Arco de círculo: centro + radio + ángulos inicial/final."""
    cx:        float = 0.0
    cy:        float = 0.0
    radius:    float = 1.0
    start_ang: float = 0.0    # grados, CCW desde eje X+
    end_ang:   float = 180.0
    ccw:       bool  = True
    layer:     str   = "A-MURO"

    def snap_points(self) -> list:
        sa = math.radians(self.start_ang)
        ea = math.radians(self.end_ang)
        span = ((self.end_ang - self.start_ang) % 360 if self.ccw
                else (self.start_ang - self.end_ang) % 360)
        ma = math.radians(self.start_ang + (span/2 if self.ccw else -span/2))
        r  = self.radius
        return [
            (self.cx + r*math.cos(sa), self.cy + r*math.sin(sa), "end"),
            (self.cx + r*math.cos(ea), self.cy + r*math.sin(ea), "end"),
            (self.cx + r*math.cos(ma), self.cy + r*math.sin(ma), "mid"),
            (self.cx, self.cy, "cen"),
        ]

    def bbox_pts(self) -> list:
        """Bounding box real: incluye cruces de eje dentro del arco."""
        pts  = list(self.snap_points())
        r    = self.radius
        span = ((self.end_ang - self.start_ang) % 360 if self.ccw
                else (self.start_ang - self.end_ang) % 360)
        for ang in (0.0, 90.0, 180.0, 270.0):
            rel = ((ang - self.start_ang) % 360 if self.ccw
                   else (self.start_ang - ang) % 360)
            if rel <= span:
                pts.append((self.cx + r*math.cos(math.radians(ang)),
                             self.cy + r*math.sin(math.radians(ang)), "qua"))
        return pts

    def info(self) -> str:
        span = ((self.end_ang - self.start_ang) % 360 if self.ccw
                else (self.start_ang - self.end_ang) % 360)
        L = math.radians(span) * self.radius
        return (f"ARCO\n"
                f"  centro ({self.cx:.3f}, {self.cy:.3f})\n"
                f"  radio  {self.radius:.3f} m\n"
                f"  ángulos {self.start_ang:.1f}° → {self.end_ang:.1f}°  "
                f"span {span:.1f}°\n"
                f"  largo  {L:.3f} m\n"
                f"  capa   {self.layer}")

    def translated(self, dx: float, dy: float) -> "Arc":
        return Arc(cx=self.cx+dx, cy=self.cy+dy, radius=self.radius,
                   start_ang=self.start_ang, end_ang=self.end_ang,
                   ccw=self.ccw, layer=self.layer)

    def rotated(self, cx: float, cy: float, deg: float) -> "Arc":
        ncx, ncy = _rotate_point(self.cx, self.cy, cx, cy, deg)
        return Arc(cx=ncx, cy=ncy, radius=self.radius,
                   start_ang=(self.start_ang + deg) % 360,
                   end_ang=(self.end_ang + deg) % 360,
                   ccw=self.ccw, layer=self.layer)

    def scaled(self, cx: float, cy: float, f: float) -> "Arc":
        return Arc(cx=cx+(self.cx-cx)*f, cy=cy+(self.cy-cy)*f,
                   radius=self.radius*abs(f),
                   start_ang=self.start_ang, end_ang=self.end_ang,
                   ccw=self.ccw if f > 0 else not self.ccw, layer=self.layer)

    def mirrored(self, ax: float, ay: float,
                 bx: float, by: float) -> "Arc":
        ncx, ncy = _mirror_point(self.cx, self.cy, ax, ay, bx, by)
        axis_ang = math.degrees(math.atan2(by - ay, bx - ax))
        ns = (2*axis_ang - self.start_ang) % 360
        ne = (2*axis_ang - self.end_ang) % 360
        return Arc(cx=ncx, cy=ncy, radius=self.radius,
                   start_ang=ne, end_ang=ns,
                   ccw=not self.ccw, layer=self.layer)


@dataclass
class Dimension(Entity):
    """
    Cota lineal (H/V/A), radio (R), diámetro (D), angular (ANG),
    longitud de arco (ARC) u ordenada (ORD).

    p1, p2     : puntos de extensión (origen de las líneas de extensión)
    pos        : punto en la línea de cota (define offset)
    text_pos   : posición explícita del texto (None = calculada automáticamente)
    dim_type   : "H" | "V" | "A" | "R" | "D" | "ANG" | "ARC" | "ORD"
                  ARC → p1=inicio arco, p2=fin arco, pos=centro arco
                  ORD → p1=punto medido, p2=extremo del líder
    text_override: "" → medición automática; string → texto fijo
    style      : nombre del DIMSTYLE
    """
    p1:            tuple = field(default_factory=lambda: (0.0, 0.0))
    p2:            tuple = field(default_factory=lambda: (1.0, 0.0))
    pos:           tuple = field(default_factory=lambda: (0.5, -0.5))
    dim_type:      str   = "H"
    text_override: str   = ""
    style:         str   = "Arq-50"
    layer:         str   = "A-COTA"
    text_pos:      object = None   # tuple (x,y) o None → auto
    rot_angle:     object = None   # float (grados) o None → sin rotación
                                   # Para cotas DXF tipo 0 con ángulo ≠ 0°/90°
    no_ext:        bool   = False  # True → no dibujar líneas de extensión
                                   # (ej: cotas DIMCONTINUE/BASELINE importadas)

    # ── valor medido ──────────────────────────────────────────────
    def measurement(self) -> float:
        """Valor numérico de la cota según tipo."""
        x1, y1 = self.p1;  x2, y2 = self.p2
        if self.dim_type == "H":
            return abs(x2 - x1)
        if self.dim_type == "V":
            return abs(y2 - y1)
        if self.dim_type == "A":
            if self.rot_angle is not None:
                # Cota rotada: medir proyección sobre la dirección de rotación
                angle_rad = math.radians(self.rot_angle)
                ux, uy = math.cos(angle_rad), math.sin(angle_rad)
                return abs((x2 - x1) * ux + (y2 - y1) * uy)
            return math.hypot(x2 - x1, y2 - y1)
        if self.dim_type == "R":
            return math.hypot(x2 - x1, y2 - y1)
        if self.dim_type == "D":
            return math.hypot(x2 - x1, y2 - y1) * 2.0   # diámetro = 2×radio
        if self.dim_type == "ANG":
            # p1=centro, p2=extremo1, pos=extremo2
            px, py = self.pos
            a1 = math.degrees(math.atan2(y1 - px, x1 - py))
            a2 = math.degrees(math.atan2(y2 - px, x2 - py))
            return abs((a2 - a1 + 360) % 360)
        if self.dim_type == "ARC":
            # p1=inicio arco, p2=fin arco, pos=centro
            cx, cy = self.pos
            r = math.hypot(x1 - cx, y1 - cy)
            if r < 1e-9:
                return 0.0
            a1 = math.atan2(y1 - cy, x1 - cx)
            a2 = math.atan2(y2 - cy, x2 - cx)
            da = (a2 - a1) % (2 * math.pi)
            if da < 1e-9:
                da = 2 * math.pi
            return r * da
        if self.dim_type == "ORD":
            # p1=punto medido, p2=extremo líder
            # Si el líder es más vertical → mide X; más horizontal → mide Y
            dx = abs(x2 - x1);  dy = abs(y2 - y1)
            return x1 if dy >= dx else y1
        return 0.0

    def text(self) -> str:
        """Texto visible: override si existe, si no medición formateada."""
        if self.text_override:
            return self.text_override
        v = self.measurement()
        if self.dim_type == "ANG":
            return f"{v:.1f}°"
        if self.dim_type == "D":
            return f"Ø{v:.2f}"
        if self.dim_type == "R":
            return f"R{v:.2f}"
        if self.dim_type == "ORD":
            # etiqueta con eje
            x1, y1 = self.p1;  x2, y2 = self.p2
            dx = abs(x2 - x1);  dy = abs(y2 - y1)
            eje = "X" if dy >= dx else "Y"
            return f"{eje} {v:.2f}"
        return f"{v:.2f}"

    # ── snap / bbox ───────────────────────────────────────────────
    def snap_points(self) -> list:
        px, py = self.pos
        mx = (self.p1[0] + self.p2[0]) / 2
        my = (self.p1[1] + self.p2[1]) / 2
        pts = [
            (self.p1[0], self.p1[1], "end"),
            (self.p2[0], self.p2[1], "end"),
            (px, py, "mid"),
            (mx, my, "cen"),
        ]
        if self.text_pos is not None:
            pts.append((self.text_pos[0], self.text_pos[1], "end"))
        return pts

    def bbox_pts(self) -> list:
        """Puntos del bounding box real de la cota — incluye la línea de cota,
        las líneas de extensión y el texto. Necesario para selección correcta."""
        x1, y1 = self.p1
        x2, y2 = self.p2
        px, py = self.pos
        pts = [self.p1, self.p2, self.pos]

        if self.dim_type == "H":
            # Línea de cota: (x1,py)—(x2,py); extensiones verticales
            pts += [(x1, py), (x2, py)]
        elif self.dim_type == "V":
            # Línea de cota: (px,y1)—(px,y2); extensiones horizontales
            pts += [(px, y1), (px, y2)]
        elif self.dim_type == "A":
            if self.rot_angle is not None:
                angle_rad = math.radians(self.rot_angle)
                ux, uy = math.cos(angle_rad), math.sin(angle_rad)
                nx_v, ny_v = -uy, ux
                off1 = (px * nx_v + py * ny_v) - (x1 * nx_v + y1 * ny_v)
                off2 = (px * nx_v + py * ny_v) - (x2 * nx_v + y2 * ny_v)
                pts += [(x1 + nx_v*off1, y1 + ny_v*off1),
                        (x2 + nx_v*off2, y2 + ny_v*off2)]
            else:
                dlen = math.hypot(x2-x1, y2-y1)
                if dlen > 1e-9:
                    ux, uy = (x2-x1)/dlen, (y2-y1)/dlen
                    nx_v, ny_v = -uy, ux
                    off = (px-x1)*nx_v + (py-y1)*ny_v
                    pts += [(x1+nx_v*off, y1+ny_v*off),
                            (x2+nx_v*off, y2+ny_v*off)]
        elif self.dim_type in ("R", "D"):
            pts += [(px, py)]   # pos = punto en el radio
        elif self.dim_type == "ANG":
            pts += [self.pos]
        elif self.dim_type == "ARC":
            cx, cy = px, py
            r = math.hypot(x1-cx, y1-cy)
            if r > 1e-9:
                a1 = math.atan2(y1-cy, x1-cx)
                a2 = math.atan2(y2-cy, x2-cx)
                amid = (a1+a2)/2
                pts += [(cx+r*math.cos(a1), cy+r*math.sin(a1)),
                        (cx+r*math.cos(a2), cy+r*math.sin(a2)),
                        (cx+r*1.3*math.cos(amid), cy+r*1.3*math.sin(amid))]

        if self.text_pos is not None:
            pts.append(self.text_pos)

        return [(p[0], p[1]) for p in pts]

    def info(self) -> str:
        return (f"COTA  tipo={self.dim_type}\n"
                f"  valor  {self.text()}\n"
                f"  p1     ({self.p1[0]:.3f}, {self.p1[1]:.3f})\n"
                f"  p2     ({self.p2[0]:.3f}, {self.p2[1]:.3f})\n"
                f"  estilo {self.style}  capa {self.layer}")

    # ── transformaciones ─────────────────────────────────────────
    def _tp(self) -> object:
        """text_pos transformada o None."""
        return self.text_pos

    def translated(self, dx: float, dy: float) -> "Dimension":
        tp = (self.text_pos[0]+dx, self.text_pos[1]+dy) if self.text_pos else None
        return Dimension(
            p1=(self.p1[0]+dx, self.p1[1]+dy),
            p2=(self.p2[0]+dx, self.p2[1]+dy),
            pos=(self.pos[0]+dx, self.pos[1]+dy),
            dim_type=self.dim_type, text_override=self.text_override,
            style=self.style, layer=self.layer, text_pos=tp,
            rot_angle=self.rot_angle, no_ext=self.no_ext)

    def rotated(self, cx: float, cy: float, deg: float) -> "Dimension":
        np1 = _rotate_point(*self.p1, cx, cy, deg)
        np2 = _rotate_point(*self.p2, cx, cy, deg)
        npo = _rotate_point(*self.pos, cx, cy, deg)
        tp  = _rotate_point(*self.text_pos, cx, cy, deg) if self.text_pos else None
        # Ajustar rot_angle si existe
        ra = (self.rot_angle + deg) % 360.0 if self.rot_angle is not None else None
        return Dimension(p1=np1, p2=np2, pos=npo,
                         dim_type=self.dim_type, text_override=self.text_override,
                         style=self.style, layer=self.layer, text_pos=tp,
                         rot_angle=ra, no_ext=self.no_ext)

    def scaled(self, cx: float, cy: float, f: float) -> "Dimension":
        def _sc(pt):
            return (cx + (pt[0]-cx)*f, cy + (pt[1]-cy)*f)
        tp = _sc(self.text_pos) if self.text_pos else None
        return Dimension(p1=_sc(self.p1), p2=_sc(self.p2), pos=_sc(self.pos),
                         dim_type=self.dim_type, text_override=self.text_override,
                         style=self.style, layer=self.layer, text_pos=tp,
                         rot_angle=self.rot_angle, no_ext=self.no_ext)

    def mirrored(self, ax: float, ay: float, bx: float, by: float) -> "Dimension":
        np1 = _mirror_point(*self.p1, ax, ay, bx, by)
        np2 = _mirror_point(*self.p2, ax, ay, bx, by)
        npo = _mirror_point(*self.pos, ax, ay, bx, by)
        tp  = _mirror_point(*self.text_pos, ax, ay, bx, by) if self.text_pos else None
        # Espejo invierte el ángulo respecto al eje de espejo
        ra = None
        if self.rot_angle is not None:
            # Calcular ángulo del eje de espejo
            mirror_ang = math.degrees(math.atan2(by - ay, bx - ax))
            ra = (2 * mirror_ang - self.rot_angle) % 360.0
        return Dimension(p1=np1, p2=np2, pos=npo,
                         dim_type=self.dim_type, text_override=self.text_override,
                         style=self.style, layer=self.layer, text_pos=tp,
                         rot_angle=ra, no_ext=self.no_ext)


# ── Hatch / Relleno ──────────────────────────────────────────────────────────

@dataclass
class Hatch(Entity):
    """
    Sombreado/relleno de un área delimitada por puntos.

    boundary : lista de (x, y) que forman el polígono cerrado
    pattern  : "SOLID" | "ANSI31" | "LINES" | "CROSS"
    angle    : ángulo de las líneas (grados, ignorado para SOLID)
    scale    : espaciado entre líneas en unidades mundo
    """
    boundary:       list  = field(default_factory=list)
    pattern:        str   = "ANSI31"
    angle:          float = 45.0
    scale:          float = 0.5      # espaciado en unidades mundo (para renderer)
    dxf_scale:      float = 0.0     # pattern_scale original del DXF (0 = no disponible → usar scale)
    layer:          str   = "A-MURO"
    is_gradient:    bool  = False      # True → relleno gradiente DXF
    gradient_color: str   = "bylayer"  # color primario del gradiente
    # Familias de líneas reales leídas del DXF (máx 2).
    # Cada entrada: (angle_deg, spacing_m)
    #   angle_deg  — ángulo absoluto de la familia (grados, sin rotación de entidad)
    #   spacing_m  — separación perpendicular en unidades mundo
    # Lista vacía → usar tabla _HATCH_PATTERNS como fallback.
    pattern_lines:  list  = field(default_factory=list)
    # Huecos interiores (loops 1+). Cada entrada es una lista de (x, y).
    # El renderer usa bridge-earcut para restar estos huecos del relleno sólido.
    holes:          list  = field(default_factory=list)

    def snap_points(self) -> list:
        pts = [(x, y, "end") for x, y in self.boundary]
        if len(self.boundary) >= 3:
            cx = sum(p[0] for p in self.boundary) / len(self.boundary)
            cy = sum(p[1] for p in self.boundary) / len(self.boundary)
            pts.append((cx, cy, "cen"))
        return pts

    def info(self) -> str:
        n = len(self.boundary)
        if n >= 3:
            xs = [p[0] for p in self.boundary]; ys = [p[1] for p in self.boundary]
            w = max(xs)-min(xs); h = max(ys)-min(ys)
            bbox_txt = f"\n  bbox   {w:.3f} × {h:.3f} m"
        else:
            bbox_txt = ""
        tipo = f"GRADIENTE ({self.gradient_color})" if self.is_gradient else self.pattern
        return (f"HATCH\n"
                f"  patrón {tipo}  ángulo {self.angle:.0f}°  "
                f"escala {self.scale:.4f}{bbox_txt}\n"
                f"  verts  {n}\n"
                f"  capa   {self.layer}")

    # ── transformaciones ─────────────────────────────────────────
    def _extra(self) -> dict:
        """Campos extra para propagar en transformaciones."""
        return dict(is_gradient=self.is_gradient,
                    gradient_color=self.gradient_color,
                    pattern_lines=self.pattern_lines,
                    dxf_scale=self.dxf_scale,
                    holes=self.holes)

    def translated(self, dx: float, dy: float) -> "Hatch":
        return Hatch(
            boundary=[(x+dx, y+dy) for x, y in self.boundary],
            pattern=self.pattern, angle=self.angle,
            scale=self.scale, layer=self.layer,
            holes=[[(x+dx, y+dy) for x, y in h] for h in self.holes],
            **{k: v for k, v in self._extra().items() if k != 'holes'})

    def rotated(self, cx: float, cy: float, deg: float) -> "Hatch":
        return Hatch(
            boundary=[_rotate_point(x, y, cx, cy, deg) for x, y in self.boundary],
            pattern=self.pattern, angle=self.angle + deg,
            scale=self.scale, layer=self.layer,
            holes=[[_rotate_point(x, y, cx, cy, deg) for x, y in h] for h in self.holes],
            **{k: v for k, v in self._extra().items() if k != 'holes'})

    def scaled(self, cx: float, cy: float, f: float) -> "Hatch":
        af = abs(f)
        scaled_plines = [(a, s * af) for a, s in self.pattern_lines]
        return Hatch(
            boundary=[(cx + (x-cx)*f, cy + (y-cy)*f) for x, y in self.boundary],
            pattern=self.pattern, angle=self.angle,
            scale=self.scale * af, layer=self.layer,
            is_gradient=self.is_gradient, gradient_color=self.gradient_color,
            pattern_lines=scaled_plines,
            holes=[[(cx + (x-cx)*f, cy + (y-cy)*f) for x, y in h] for h in self.holes])

    def mirrored(self, ax: float, ay: float, bx: float, by: float) -> "Hatch":
        return Hatch(
            boundary=[_mirror_point(x, y, ax, ay, bx, by) for x, y in self.boundary],
            pattern=self.pattern, angle=self.angle,
            scale=self.scale, layer=self.layer,
            holes=[[_mirror_point(x, y, ax, ay, bx, by) for x, y in h] for h in self.holes],
            **{k: v for k, v in self._extra().items() if k != 'holes'})


# ── Block Instancing ─────────────────────────────────────────────────────────
#
# Arquitectura: BlockDef almacena la geometría UNA vez.
#               Insert referencia esa definición con una transformación.
#
# Esto reduce 400 árboles de 80k LINE → 400 Insert + 1 BlockDef con 200 LINE.
# El renderer aplica la transformación en tiempo de dibujo (o desde caché).

@dataclass
class AttDef(Entity):
    """Definición de atributo dentro de un bloque (ATTDEF en DXF).

    Vive en BlockDef.attdefs — no en engine.entities.
    Describe el esquema del atributo: tag, prompt y valor por defecto.
    """
    tag:     str   = ""      # identificador, ej. "E"
    prompt:  str   = ""      # texto de ayuda, ej. "EJE"
    default: str   = ""      # valor por defecto
    x:       float = 0.0
    y:       float = 0.0
    height:  float = 0.20
    angle:   float = 0.0
    layer:   str   = "A-TEXTO"

    def snap_points(self): return [(self.x, self.y)]
    def bbox_pts(self):    return [(self.x, self.y)]

    def translated(self, dx, dy):
        return AttDef(tag=self.tag, prompt=self.prompt, default=self.default,
                      x=self.x+dx, y=self.y+dy, height=self.height,
                      angle=self.angle, layer=self.layer,
                      color=self.color, linetype=self.linetype, linewidth=self.linewidth)

    def rotated(self, cx, cy, deg):
        nx, ny = _rotate_point(self.x, self.y, cx, cy, deg)
        return AttDef(tag=self.tag, prompt=self.prompt, default=self.default,
                      x=nx, y=ny, height=self.height, angle=(self.angle+deg)%360,
                      layer=self.layer, color=self.color, linetype=self.linetype, linewidth=self.linewidth)

    def scaled(self, cx, cy, f):
        return AttDef(tag=self.tag, prompt=self.prompt, default=self.default,
                      x=cx+(self.x-cx)*f, y=cy+(self.y-cy)*f,
                      height=self.height*abs(f), angle=self.angle,
                      layer=self.layer, color=self.color, linetype=self.linetype, linewidth=self.linewidth)

    def mirrored(self, ax, ay, bx, by):
        nx, ny = _mirror_point(self.x, self.y, ax, ay, bx, by)
        return AttDef(tag=self.tag, prompt=self.prompt, default=self.default,
                      x=nx, y=ny, height=self.height, angle=self.angle,
                      layer=self.layer, color=self.color, linetype=self.linetype, linewidth=self.linewidth)


@dataclass
class Attrib(Entity):
    """Valor de atributo en una inserción concreta (ATTRIB en DXF).

    Vive en Insert.attribs — no en engine.entities directamente.
    Contiene el valor editable por EATTEDIT y una referencia al Text
    entity de display para actualizarlo sin reconstruir nada.
    """
    tag:    str   = ""       # debe coincidir con AttDef.tag
    value:  str   = ""       # valor actual, editable via EATTEDIT
    x:      float = 0.0      # posición WCS (ya transformada por AutoCAD)
    y:      float = 0.0
    height: float = 0.20
    angle:  float = 0.0
    layer:  str   = "A-TEXTO"

    # Referencia al Text entity de display — no serializada
    _text_ref: object = field(default=None, init=False, repr=False, compare=False)

    def snap_points(self): return [(self.x, self.y)]
    def bbox_pts(self):    return [(self.x, self.y)]

    def translated(self, dx, dy):
        return Attrib(tag=self.tag, value=self.value,
                      x=self.x+dx, y=self.y+dy, height=self.height,
                      angle=self.angle, layer=self.layer,
                      color=self.color, linetype=self.linetype, linewidth=self.linewidth)

    def rotated(self, cx, cy, deg):
        nx, ny = _rotate_point(self.x, self.y, cx, cy, deg)
        return Attrib(tag=self.tag, value=self.value,
                      x=nx, y=ny, height=self.height, angle=(self.angle+deg)%360,
                      layer=self.layer, color=self.color, linetype=self.linetype, linewidth=self.linewidth)

    def scaled(self, cx, cy, f):
        return Attrib(tag=self.tag, value=self.value,
                      x=cx+(self.x-cx)*f, y=cy+(self.y-cy)*f,
                      height=self.height*abs(f), angle=self.angle,
                      layer=self.layer, color=self.color, linetype=self.linetype, linewidth=self.linewidth)

    def mirrored(self, ax, ay, bx, by):
        nx, ny = _mirror_point(self.x, self.y, ax, ay, bx, by)
        return Attrib(tag=self.tag, value=self.value,
                      x=nx, y=ny, height=self.height, angle=self.angle,
                      layer=self.layer, color=self.color, linetype=self.linetype, linewidth=self.linewidth)


@dataclass
class BlockDef:
    """Definición de bloque: geometría primitiva referenciada por Insert.

    Nunca vive en engine.entities — vive en engine.block_defs[name].
    Las entidades dentro del bloque usan coordenadas relativas al base_point.
    """
    name:       str  = ""
    base_point: tuple = field(default_factory=lambda: (0.0, 0.0))
    entities:   list  = field(default_factory=list)   # lista de Entity primitivas
    attdefs:    list  = field(default_factory=list)    # lista de AttDef del bloque

    # Caché bbox — no serializada, no comparada
    _bbox_cache: object = field(default=None, init=False, repr=False, compare=False)

    def bbox(self) -> tuple[float, float, float, float]:
        """Bounding box en espacio de bloque (sin aplicar transform). Cacheada."""
        if self._bbox_cache is not None:
            return self._bbox_cache
        xs, ys = [], []
        for e in self.entities:
            for pt in (e.bbox_pts() or []):
                xs.append(pt[0]); ys.append(pt[1])
        result = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
        self._bbox_cache = result
        return result

    def invalidate_bbox(self):
        """Invalidar caché bbox (llamar si se modifican entities)."""
        self._bbox_cache = None


@dataclass
class Insert(Entity):
    """Instancia de bloque — una referencia a BlockDef con transform.

    Vive en engine.entities como cualquier otra entidad.
    El renderer busca block_defs[block_name] para dibujar.
    """
    block_name: str   = ""
    x:          float = 0.0
    y:          float = 0.0
    scale_x:    float = 1.0
    scale_y:    float = 1.0
    angle:      float = 0.0   # grados CCW
    layer:      str   = "0"
    attribs:    list  = field(default_factory=list)   # lista de Attrib de esta instancia

    # ── Transform: coordenada bloque → mundo ──────────────────────────
    def transform_pt(self, bx: float, by: float) -> tuple[float, float]:
        """Aplica scale + rotate + translate al punto del espacio de bloque."""
        bx *= self.scale_x
        by *= self.scale_y
        if self.angle:
            bx, by = _rotate_point(bx, by, 0, 0, self.angle)
        return self.x + bx, self.y + by

    def _world_bbox(self, block_defs: dict) -> tuple:
        """Bbox en espacio mundo. Requiere acceso a block_defs."""
        bdef = block_defs.get(self.block_name)
        if bdef is None:
            return self.x, self.y, self.x, self.y
        bx0, by0, bx1, by1 = bdef.bbox()
        corners = [(bx0, by0), (bx1, by0), (bx0, by1), (bx1, by1)]
        pts = [self.transform_pt(cx - bdef.base_point[0],
                                 cy - bdef.base_point[1])
               for cx, cy in corners]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    # ── Entity interface ──────────────────────────────────────────────
    def snap_points(self) -> list:
        return [(self.x, self.y, "ins")]   # solo punto de inserción por defecto

    def bbox_pts(self) -> list:
        return [(self.x, self.y, "ins")]   # engine llama _world_bbox separado

    def info(self) -> str:
        return (f"BLOQUE  {self.block_name}\n"
                f"  pos    ({self.x:.3f}, {self.y:.3f})\n"
                f"  escala ({self.scale_x:.4f}, {self.scale_y:.4f})\n"
                f"  ángulo {self.angle:.2f}°\n"
                f"  capa   {self.layer}")

    def translated(self, dx: float, dy: float) -> "Insert":
        return Insert(block_name=self.block_name,
                      x=self.x+dx, y=self.y+dy,
                      scale_x=self.scale_x, scale_y=self.scale_y,
                      angle=self.angle, layer=self.layer)

    def rotated(self, cx: float, cy: float, deg: float) -> "Insert":
        nx, ny = _rotate_point(self.x, self.y, cx, cy, deg)
        return Insert(block_name=self.block_name,
                      x=nx, y=ny,
                      scale_x=self.scale_x, scale_y=self.scale_y,
                      angle=self.angle + deg, layer=self.layer)

    def scaled(self, cx: float, cy: float, f: float) -> "Insert":
        nx = cx + (self.x - cx) * f
        ny = cy + (self.y - cy) * f
        return Insert(block_name=self.block_name,
                      x=nx, y=ny,
                      scale_x=self.scale_x * abs(f),
                      scale_y=self.scale_y * abs(f),
                      angle=self.angle, layer=self.layer)

    def mirrored(self, ax: float, ay: float,
                 bx: float, by: float) -> "Insert":
        nx, ny = _mirror_point(self.x, self.y, ax, ay, bx, by)
        return Insert(block_name=self.block_name,
                      x=nx, y=ny,
                      scale_x=self.scale_x, scale_y=self.scale_y,
                      angle=self.angle, layer=self.layer)


# ── Nuevas entidades ─────────────────────────────────────────────────────────

@dataclass
class Ellipse(Entity):
    """Elipse rotada: centro + semiejes + ángulo CCW."""
    cx:    float = 0.0
    cy:    float = 0.0
    rx:    float = 1.0   # semi-eje mayor
    ry:    float = 0.5   # semi-eje menor
    angle: float = 0.0   # rotación CCW grados
    layer: str   = "A-MURO"

    def _pts_on_boundary(self, n: int = 72) -> list:
        """Genera n puntos sobre la frontera de la elipse."""
        a = math.radians(self.angle)
        ca, sa = math.cos(a), math.sin(a)
        pts = []
        for i in range(n):
            t = 2 * math.pi * i / n
            ct, st = math.cos(t), math.sin(t)
            px = self.cx + self.rx * ct * ca - self.ry * st * sa
            py = self.cy + self.rx * ct * sa + self.ry * st * ca
            pts.append((px, py))
        return pts

    def snap_points(self) -> list:
        a = math.radians(self.angle)
        ca, sa = math.cos(a), math.sin(a)
        # center + 4 quadrant extremes
        pts = [(self.cx, self.cy, "cen")]
        for ct, st in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
            px = self.cx + self.rx * ct * ca - self.ry * st * sa
            py = self.cy + self.rx * ct * sa + self.ry * st * ca
            pts.append((px, py, "qua"))
        return pts

    def bbox_pts(self) -> list:
        bpts = self._pts_on_boundary(36)
        return [(x, y, "mid") for x, y in bpts]

    def info(self) -> str:
        return (f"ELIPSE\n"
                f"  centro ({self.cx:.3f}, {self.cy:.3f})\n"
                f"  rx={self.rx:.3f}  ry={self.ry:.3f}  ángulo {self.angle:.1f}°\n"
                f"  capa   {self.layer}")

    def translated(self, dx: float, dy: float) -> "Ellipse":
        return Ellipse(cx=self.cx+dx, cy=self.cy+dy,
                       rx=self.rx, ry=self.ry, angle=self.angle, layer=self.layer)

    def rotated(self, cx: float, cy: float, deg: float) -> "Ellipse":
        ncx, ncy = _rotate_point(self.cx, self.cy, cx, cy, deg)
        return Ellipse(cx=ncx, cy=ncy, rx=self.rx, ry=self.ry,
                       angle=(self.angle + deg) % 360, layer=self.layer)

    def scaled(self, cx: float, cy: float, f: float) -> "Ellipse":
        af = abs(f)
        return Ellipse(cx=cx+(self.cx-cx)*f, cy=cy+(self.cy-cy)*f,
                       rx=self.rx*af, ry=self.ry*af,
                       angle=self.angle, layer=self.layer)

    def mirrored(self, ax: float, ay: float, bx: float, by: float) -> "Ellipse":
        ncx, ncy = _mirror_point(self.cx, self.cy, ax, ay, bx, by)
        axis_ang = math.degrees(math.atan2(by - ay, bx - ax))
        return Ellipse(cx=ncx, cy=ncy, rx=self.rx, ry=self.ry,
                       angle=(2 * axis_ang - self.angle) % 360, layer=self.layer)


@dataclass
class XLine(Entity):
    """Línea de construcción infinita (define dirección por 2 puntos)."""
    x1:    float = 0.0
    y1:    float = 0.0
    x2:    float = 1.0
    y2:    float = 0.0
    layer: str   = "A-REFEREN"

    def snap_points(self) -> list:
        mx = (self.x1 + self.x2) / 2
        my = (self.y1 + self.y2) / 2
        return [
            (self.x1, self.y1, "end"),
            (mx, my, "mid"),
        ]

    def bbox_pts(self) -> list:
        return [(self.x1, self.y1, "end")]

    def info(self) -> str:
        dx = self.x2 - self.x1
        dy = self.y2 - self.y1
        ang = math.degrees(math.atan2(dy, dx))
        return (f"XLINE\n"
                f"  ref    ({self.x1:.3f}, {self.y1:.3f})\n"
                f"  ángulo {ang:.2f}°\n"
                f"  capa   {self.layer}")

    def translated(self, dx: float, dy: float) -> "XLine":
        return XLine(x1=self.x1+dx, y1=self.y1+dy,
                     x2=self.x2+dx, y2=self.y2+dy, layer=self.layer)

    def rotated(self, cx: float, cy: float, deg: float) -> "XLine":
        nx1, ny1 = _rotate_point(self.x1, self.y1, cx, cy, deg)
        nx2, ny2 = _rotate_point(self.x2, self.y2, cx, cy, deg)
        return XLine(x1=nx1, y1=ny1, x2=nx2, y2=ny2, layer=self.layer)

    def scaled(self, cx: float, cy: float, f: float) -> "XLine":
        return XLine(x1=cx+(self.x1-cx)*f, y1=cy+(self.y1-cy)*f,
                     x2=cx+(self.x2-cx)*f, y2=cy+(self.y2-cy)*f,
                     layer=self.layer)

    def mirrored(self, ax: float, ay: float, bx: float, by: float) -> "XLine":
        nx1, ny1 = _mirror_point(self.x1, self.y1, ax, ay, bx, by)
        nx2, ny2 = _mirror_point(self.x2, self.y2, ax, ay, bx, by)
        return XLine(x1=nx1, y1=ny1, x2=nx2, y2=ny2, layer=self.layer)


@dataclass
class Leader(Entity):
    """Anotación con flecha (leader): polilínea + texto en extremo final."""
    points:     list  = field(default_factory=list)  # [(x,y), ...] ≥2 puntos
    text:       str   = ""
    arrow_size: float = 0.05   # metros
    layer:      str   = "A-COTA"

    def snap_points(self) -> list:
        pts = [(x, y, "end") for x, y in self.points]
        for i in range(len(self.points) - 1):
            mx = (self.points[i][0] + self.points[i+1][0]) / 2
            my = (self.points[i][1] + self.points[i+1][1]) / 2
            pts.append((mx, my, "mid"))
        return pts

    def bbox_pts(self) -> list:
        return [(x, y, "end") for x, y in self.points]

    def info(self) -> str:
        n = len(self.points)
        txt = f'  texto  "{self.text}"\n' if self.text else ""
        return (f"LEADER\n"
                f"  puntos {n}\n"
                f"{txt}"
                f"  capa   {self.layer}")

    def translated(self, dx: float, dy: float) -> "Leader":
        return Leader(points=[(x+dx, y+dy) for x, y in self.points],
                      text=self.text, arrow_size=self.arrow_size, layer=self.layer)

    def rotated(self, cx: float, cy: float, deg: float) -> "Leader":
        return Leader(points=[_rotate_point(x, y, cx, cy, deg) for x, y in self.points],
                      text=self.text, arrow_size=self.arrow_size, layer=self.layer)

    def scaled(self, cx: float, cy: float, f: float) -> "Leader":
        af = abs(f)
        return Leader(points=[(cx+(x-cx)*f, cy+(y-cy)*f) for x, y in self.points],
                      text=self.text, arrow_size=self.arrow_size*af, layer=self.layer)

    def mirrored(self, ax: float, ay: float, bx: float, by: float) -> "Leader":
        return Leader(points=[_mirror_point(x, y, ax, ay, bx, by) for x, y in self.points],
                      text=self.text, arrow_size=self.arrow_size, layer=self.layer)


# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class ImageRef(Entity):
    """Imagen raster insertada en el dibujo (PNG, JPG, BMP…)."""
    path:   str   = ""     # ruta del archivo de imagen
    x:      float = 0.0    # esquina inferior-izquierda (mundo)
    y:      float = 0.0
    width:  float = 1.0    # ancho en unidades mundo
    height: float = 1.0    # alto  en unidades mundo
    angle:  float = 0.0    # rotación en grados

    def _corners(self):
        """Cuatro esquinas [BL, BR, TR, TL] con rotación aplicada."""
        cx = self.x + self.width  / 2
        cy = self.y + self.height / 2
        hw = self.width  / 2
        hh = self.height / 2
        cos_a = math.cos(math.radians(self.angle))
        sin_a = math.sin(math.radians(self.angle))
        return [
            (cx + (-hw)*cos_a - (-hh)*sin_a, cy + (-hw)*sin_a + (-hh)*cos_a),
            (cx + ( hw)*cos_a - (-hh)*sin_a, cy + ( hw)*sin_a + (-hh)*cos_a),
            (cx + ( hw)*cos_a - ( hh)*sin_a, cy + ( hw)*sin_a + ( hh)*cos_a),
            (cx + (-hw)*cos_a - ( hh)*sin_a, cy + (-hw)*sin_a + ( hh)*cos_a),
        ]

    def snap_points(self):
        corners = self._corners()
        pts = [(x, y, "end") for x, y in corners]
        pts.append(((corners[0][0]+corners[2][0])/2,
                    (corners[0][1]+corners[2][1])/2, "cen"))
        return pts

    def bbox_pts(self):
        return [(x, y) for x, y in self._corners()]

    def translated(self, dx: float, dy: float) -> "ImageRef":
        return ImageRef(path=self.path, x=self.x+dx, y=self.y+dy,
                        width=self.width, height=self.height, angle=self.angle,
                        layer=self.layer, color=self.color)

    def rotated(self, cx: float, cy: float, deg: float) -> "ImageRef":
        ncx, ncy = _rotate_point(self.x + self.width/2, self.y + self.height/2, cx, cy, deg)
        return ImageRef(path=self.path,
                        x=ncx - self.width/2, y=ncy - self.height/2,
                        width=self.width, height=self.height,
                        angle=(self.angle + deg) % 360,
                        layer=self.layer, color=self.color)

    def scaled(self, cx: float, cy: float, f: float) -> "ImageRef":
        af = abs(f)
        nx = cx + (self.x - cx) * f
        ny = cy + (self.y - cy) * f
        return ImageRef(path=self.path, x=nx, y=ny,
                        width=self.width*af, height=self.height*af,
                        angle=self.angle, layer=self.layer, color=self.color)

    def mirrored(self, ax: float, ay: float, bx: float, by: float) -> "ImageRef":
        nx, ny = _mirror_point(self.x, self.y, ax, ay, bx, by)
        return ImageRef(path=self.path, x=nx, y=ny,
                        width=self.width, height=self.height, angle=self.angle,
                        layer=self.layer, color=self.color)


# ── Operaciones de offset ─────────────────────────────────────────────────────

def _offset_line(e: Line, dist: float,
                 side_wx: float, side_wy: float) -> Optional[Line]:
    """Genera una Line paralela a dist en el lado indicado."""
    dx, dy = e.x2 - e.x1, e.y2 - e.y1
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return None
    nx, ny = -dy/L, dx/L
    mid_x = (e.x1 + e.x2) / 2
    mid_y = (e.y1 + e.y2) / 2
    if (side_wx - mid_x)*nx + (side_wy - mid_y)*ny < 0:
        nx, ny = -nx, -ny
    return Line(x1=e.x1+nx*dist, y1=e.y1+ny*dist,
                x2=e.x2+nx*dist, y2=e.y2+ny*dist, layer=e.layer)


def _offset_circle(e: Circle, dist: float,
                   side_wx: float, side_wy: float) -> Optional[Circle]:
    """Genera un Circle concéntrico más grande o más pequeño."""
    d_click = math.hypot(side_wx - e.cx, side_wy - e.cy)
    new_r = e.radius + dist if d_click >= e.radius else e.radius - dist
    if new_r <= 0:
        return None
    return Circle(cx=e.cx, cy=e.cy, radius=new_r, layer=e.layer)


def _offset_polyline(e: Polyline, dist: float,
                     side_wx: float, side_wy: float) -> Optional[Polyline]:
    """Genera una Polyline paralela con juntas en mitra."""
    pts = e.points
    if len(pts) < 2:
        return None
    normals = []
    for i in range(len(pts) - 1):
        dx = pts[i+1][0] - pts[i][0]
        dy = pts[i+1][1] - pts[i][1]
        L  = math.hypot(dx, dy)
        normals.append((-dy/L, dx/L) if L >= 1e-9 else (0.0, 0.0))

    # Usar el segmento MÁS CERCANO al cursor para detectar el lado.
    # Usando solo el primer segmento (como antes) falla en polilíneas en L, U, etc.
    # porque el cursor puede estar lejos del segmento 0 y el producto punto da mal.
    best_seg = 0
    best_seg_d = math.inf
    for i in range(len(pts) - 1):
        mid_x = (pts[i][0] + pts[i+1][0]) * 0.5
        mid_y = (pts[i][1] + pts[i+1][1]) * 0.5
        d_seg = math.hypot(side_wx - mid_x, side_wy - mid_y)
        if d_seg < best_seg_d:
            best_seg_d = d_seg
            best_seg = i

    nx0, ny0 = normals[best_seg]
    mx0 = (pts[best_seg][0] + pts[best_seg + 1][0]) * 0.5
    my0 = (pts[best_seg][1] + pts[best_seg + 1][1]) * 0.5
    if (side_wx - mx0) * nx0 + (side_wy - my0) * ny0 < 0:
        normals = [(-nx, -ny) for nx, ny in normals]
    new_pts = []
    for i in range(len(pts)):
        if i == 0:
            nx, ny = normals[0]
            new_pts.append((pts[0][0]+nx*dist, pts[0][1]+ny*dist))
        elif i == len(pts) - 1:
            nx, ny = normals[-1]
            new_pts.append((pts[-1][0]+nx*dist, pts[-1][1]+ny*dist))
        else:
            n_prev = normals[i-1]; n_next = normals[i]
            p1x = pts[i-1][0]+n_prev[0]*dist; p1y = pts[i-1][1]+n_prev[1]*dist
            p2x = pts[i][0]+n_prev[0]*dist;   p2y = pts[i][1]+n_prev[1]*dist
            p3x = pts[i][0]+n_next[0]*dist;   p3y = pts[i][1]+n_next[1]*dist
            p4x = pts[i+1][0]+n_next[0]*dist; p4y = pts[i+1][1]+n_next[1]*dist
            res = _seg_intersect(p1x, p1y, p2x, p2y, p3x, p3y, p4x, p4y,
                                 t_range=(-10.0, 10.0), u_range=(-10.0, 10.0))
            if res:
                new_pts.append((res[0], res[1]))
            else:
                new_pts.append(((p2x+p3x)/2, (p2y+p3y)/2))
    return Polyline(points=new_pts, closed=e.closed, layer=e.layer)


# ── Capas ─────────────────────────────────────────────────────────────────────

@dataclass
class Layer:
    name:      str  = "0"
    color:     str  = "#FFFFFF"
    linewidth: int  = 1
    visible:   bool = True
    locked:    bool = False
    linetype:  str  = "CONTINUOUS"


DEFAULT_LAYERS: dict[str, Layer] = {
    "0":        Layer("0",        "#FFFFFF", 1),
    "A-MURO":   Layer("A-MURO",   "#FFFFFF", 3),
    "A-TEXTO":  Layer("A-TEXTO",  "#FFFF00", 1),
    "A-COTA":   Layer("A-COTA",   "#00FFFF", 1),
    "A-PUERTA": Layer("A-PUERTA", "#00FF00", 2),
    "A-VENTANA":Layer("A-VENTANA","#0080FF", 1),
    "A-EJE":    Layer("A-EJE",    "#FF0000", 1, linetype="CENTER"),
    "A-LOTE":   Layer("A-LOTE",   "#FF00FF", 4),
    "A-RETIRO": Layer("A-RETIRO", "#FFFF00", 1, linetype="DASHED"),
    "A-MUEBLE": Layer("A-MUEBLE", "#808080", 1),
}

# ── Patrones de linetype en píxeles (draw, gap, draw, gap …) ──────────────────
# Los valores se multiplican por ltf en build_layer_cache (escala + LTSCALE).
# Convención de nombres = estándar AutoCAD / ISO.
_LINETYPES: dict[str, tuple] = {
    # ── Continua ──────────────────────────────────────────────────────────────
    "CONTINUOUS":        (),

    # ── Familia DASHED / HIDDEN ───────────────────────────────────────────────
    "DASHED":            (12,  6),
    "DASHED2":           ( 6,  3),
    "DASHEDX2":          (24, 12),
    "HIDDEN":            (12,  6),           # alias DXF = DASHED
    "HIDDEN2":           ( 6,  3),
    "HIDDENX2":          (24, 12),

    # ── Familia DOTTED ────────────────────────────────────────────────────────
    "DOTTED":            (2,   6),
    "DOTTED2":           (2,   3),
    "DOTTEDX2":          (2,  12),

    # ── Familia CENTER ────────────────────────────────────────────────────────
    "CENTER":            (24,  6, 4,  6),
    "CENTER2":           (12,  3, 2,  3),
    "CENTERX2":          (48, 12, 8, 12),

    # ── Familia DASHDOT ───────────────────────────────────────────────────────
    "DASHDOT":           (12,  6, 2,  6),
    "DASHDOT2":          ( 6,  3, 2,  3),
    "DASHDOTX2":         (24, 12, 2, 12),

    # ── Familia DIVIDE (guión + 2 puntos) ────────────────────────────────────
    "DIVIDE":            (12,  6, 2,  6, 2,  6),
    "DIVIDE2":           ( 6,  3, 2,  3, 2,  3),
    "DIVIDEX2":          (24, 12, 2, 12, 2, 12),

    # ── Familia PHANTOM (guión largo + 2 puntos) ─────────────────────────────
    "PHANTOM":           (24,  6, 2,  6, 2,  6),
    "PHANTOM2":          (12,  3, 2,  3, 2,  3),
    "PHANTOMX2":         (48, 12, 2, 12, 2, 12),

    # ── Familia BORDER (guión + punto + guión + 2 puntos) ────────────────────
    "BORDER":            (12,  6, 12,  6, 2,  6),
    "BORDER2":           ( 6,  3,  6,  3, 2,  3),
    "BORDERX2":          (24, 12, 24, 12, 2, 12),

    # ── ISO (ingeniería/mecánica) ─────────────────────────────────────────────
    "ACAD_ISO02W100":    (12,  6),
    "ACAD_ISO03W100":    (12, 18),
    "ACAD_ISO04W100":    (24,  6, 4,  6),
    "ACAD_ISO05W100":    (24,  6, 4,  6, 4,  6),
    "ACAD_ISO07W100":    ( 2, 12),
    "ACAD_ISO08W100":    (24,  6, 8,  6),
    "ACAD_ISO09W100":    (24,  6, 8,  6, 8,  6),
    "ACAD_ISO10W100":    ( 8,  6, 2,  6),
    "ACAD_ISO11W100":    ( 8,  6, 8,  6, 2,  6),
    "ACAD_ISO12W100":    ( 8,  6, 2,  6, 2,  6),
    "ACAD_ISO13W100":    ( 8,  6, 8,  6, 2,  6, 2,  6),
    "ACAD_ISO14W100":    ( 8,  6, 2,  6, 2,  6, 2,  6),
    "ACAD_ISO15W100":    ( 8,  6, 8,  6, 2,  6, 2,  6, 2,  6),
}

# Ciclo rápido al hacer toggle de linetype (las más usadas en arquitectura)
_LT_CYCLE = [
    "CONTINUOUS", "DASHED", "HIDDEN", "DOTTED", "CENTER",
    "DASHDOT", "PHANTOM", "DIVIDE", "BORDER",
    "DASHED2", "CENTER2", "DASHEDX2", "CENTERX2",
    "ACAD_ISO02W100", "ACAD_ISO04W100", "ACAD_ISO07W100",
]

_LT_ABBR: dict[str, str] = {
    "CONTINUOUS":     "CONT",
    "DASHED":         "DASH",   "DASHED2":       "DSH2",  "DASHEDX2":      "DSHx",
    "HIDDEN":         "HIDN",   "HIDDEN2":        "HDN2",  "HIDDENX2":       "HDNx",
    "DOTTED":         "DOT·",   "DOTTED2":        "DOT2",  "DOTTEDX2":       "DOTx",
    "CENTER":         "CTR",    "CENTER2":        "CTR2",  "CENTERX2":       "CTRx",
    "DASHDOT":        "D·T",    "DASHDOT2":       "D·T2",  "DASHDOTX2":      "D·Tx",
    "DIVIDE":         "DIV",    "DIVIDE2":        "DIV2",  "DIVIDEX2":       "DIVx",
    "PHANTOM":        "PHT",    "PHANTOM2":       "PHT2",  "PHANTOMX2":      "PHEx",
    "BORDER":         "BDR",    "BORDER2":        "BDR2",  "BORDERX2":       "BDRx",
    "ACAD_ISO02W100": "IS02",   "ACAD_ISO03W100": "IS03",  "ACAD_ISO04W100": "IS04",
    "ACAD_ISO05W100": "IS05",   "ACAD_ISO07W100": "IS07",  "ACAD_ISO08W100": "IS08",
    "ACAD_ISO09W100": "IS09",   "ACAD_ISO10W100": "IS10",  "ACAD_ISO11W100": "IS11",
    "ACAD_ISO12W100": "IS12",   "ACAD_ISO13W100": "IS13",  "ACAD_ISO14W100": "IS14",
    "ACAD_ISO15W100": "IS15",
}


# ── Biblioteca de bloques predefinidos ───────────────────────────────────────
# Cada bloque se define en coordenadas locales (base_point = origen).
# Unidades: metros.  Convención: apertura de puertas hacia +Y, marco en X.

def _make_puerta_simple(ancho: float = 1.00) -> BlockDef:
    """Puerta simple: marco + arco de abatimiento 90°.
    Base point = esquina bisagra (0, 0). Ancho configurable."""
    ents = [
        # Marco — línea de vano en el muro
        Line(x1=0.0, y1=0.0, x2=ancho, y2=0.0, layer="A-PUERTA"),
        # Hoja de la puerta (cierra hacia +Y)
        Line(x1=0.0, y1=0.0, x2=0.0,   y2=ancho, layer="A-PUERTA"),
        # Arco de abatimiento 90° (radio = ancho, centro en bisagra)
        Arc(cx=0.0, cy=0.0, radius=ancho,
            start_ang=0.0, end_ang=90.0, layer="A-PUERTA"),
    ]
    bd = BlockDef()
    bd.name       = f"PUERTA-{int(ancho*100):03d}"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


def _make_puerta_doble(ancho: float = 1.60) -> BlockDef:
    """Puerta doble: dos hojas que abren simétricamente hacia +Y."""
    mitad = ancho / 2
    ents = [
        Line(x1=0.0,  y1=0.0, x2=ancho, y2=0.0,  layer="A-PUERTA"),
        # Hoja izquierda
        Line(x1=0.0,  y1=0.0, x2=0.0,   y2=mitad, layer="A-PUERTA"),
        Arc(cx=0.0,  cy=0.0, radius=mitad,
            start_ang=0.0, end_ang=90.0, layer="A-PUERTA"),
        # Hoja derecha
        Line(x1=ancho, y1=0.0, x2=ancho, y2=mitad, layer="A-PUERTA"),
        Arc(cx=ancho, cy=0.0, radius=mitad,
            start_ang=90.0, end_ang=180.0, layer="A-PUERTA"),
    ]
    bd = BlockDef()
    bd.name       = f"PUERTA-DBL-{int(ancho*100):03d}"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


def _make_puerta_corrediza(ancho: float = 1.00) -> BlockDef:
    """Puerta corrediza: hoja desplazada + rieles."""
    ents = [
        # Vano
        Line(x1=0.0, y1=0.0, x2=ancho, y2=0.0, layer="A-PUERTA"),
        # Hoja abierta (desplazada lateralmente)
        Line(x1=ancho, y1=0.0, x2=ancho*2, y2=0.0, layer="A-PUERTA"),
        # Riel (línea de guía)
        Line(x1=0.0, y1=0.05, x2=ancho*2, y2=0.05, layer="A-PUERTA"),
    ]
    bd = BlockDef()
    bd.name       = f"PUERTA-CORR-{int(ancho*100):03d}"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


def _make_ventana(ancho: float = 0.90, espesor_muro: float = 0.15) -> BlockDef:
    """Ventana: 3 líneas paralelas dentro del espesor del muro.
    Base point = esquina inferior izquierda del vano."""
    e = espesor_muro
    ents = [
        # Cara exterior
        Line(x1=0.0, y1=0.0,   x2=ancho, y2=0.0,   layer="A-VENTANA"),
        # Línea central (vidrio)
        Line(x1=0.0, y1=e/2,   x2=ancho, y2=e/2,   layer="A-VENTANA"),
        # Cara interior
        Line(x1=0.0, y1=e,     x2=ancho, y2=e,     layer="A-VENTANA"),
        # Jambas
        Line(x1=0.0, y1=0.0,   x2=0.0,   y2=e,     layer="A-VENTANA"),
        Line(x1=ancho, y1=0.0, x2=ancho,  y2=e,     layer="A-VENTANA"),
    ]
    bd = BlockDef()
    bd.name       = f"VENTANA-{int(ancho*100):03d}"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


def _make_wc() -> BlockDef:
    """Inodoro esquemático. Base point = centro de la base (pegado al muro)."""
    ents = [
        # Tanque (rectángulo 0.35 × 0.17)
        Polyline(points=[(-0.175, 0.0), (0.175, 0.0),
                         (0.175, 0.17), (-0.175, 0.17)],
                 closed=True, layer="A-SANITARIO"),
        # Asiento (elipse aproximada con polilínea 12 pts)
        *[Line(
            x1=0.22*math.cos(math.radians(i*30)),
            y1=0.30 + 0.28*math.sin(math.radians(i*30)),
            x2=0.22*math.cos(math.radians((i+1)*30)),
            y2=0.30 + 0.28*math.sin(math.radians((i+1)*30)),
            layer="A-SANITARIO") for i in range(12)],
    ]
    bd = BlockDef()
    bd.name       = "WC"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


def _make_lavatorio() -> BlockDef:
    """Lavatorio/lavamanos. Base point = centro de la base (pegado al muro)."""
    ents = [
        # Contorno rectangular redondeado (polilínea)
        Polyline(points=[(-0.25, 0.0), (0.25, 0.0),
                         (0.25, 0.45), (-0.25, 0.45)],
                 closed=True, layer="A-SANITARIO"),
        # Tazón interior
        Polyline(points=[(-0.17, 0.06), (0.17, 0.06),
                         (0.17, 0.37), (-0.17, 0.37)],
                 closed=True, layer="A-SANITARIO"),
        # Desagüe (círculo pequeño)
        Circle(cx=0.0, cy=0.215, radius=0.03, layer="A-SANITARIO"),
    ]
    bd = BlockDef()
    bd.name       = "LAVATORIO"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


def _make_ducha() -> BlockDef:
    """Ducha / plato de ducha 0.90×0.90. Base point = esquina."""
    ents = [
        Polyline(points=[(0.0, 0.0), (0.90, 0.0),
                         (0.90, 0.90), (0.0, 0.90)],
                 closed=True, layer="A-SANITARIO"),
        # Cruz de desagüe
        Line(x1=0.45-0.10, y1=0.45, x2=0.45+0.10, y2=0.45, layer="A-SANITARIO"),
        Line(x1=0.45, y1=0.45-0.10, x2=0.45, y2=0.45+0.10, layer="A-SANITARIO"),
        Circle(cx=0.45, cy=0.45, radius=0.05, layer="A-SANITARIO"),
    ]
    bd = BlockDef()
    bd.name       = "DUCHA"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


def _make_lavaplatos() -> BlockDef:
    """Fregadero doble. Base point = esquina izquierda pegada al muro."""
    ents = [
        # Contorno total 1.20 × 0.60
        Polyline(points=[(0.0, 0.0), (1.20, 0.0),
                         (1.20, 0.60), (0.0, 0.60)],
                 closed=True, layer="A-COCINA"),
        # Pileta izquierda
        Polyline(points=[(0.05, 0.05), (0.55, 0.05),
                         (0.55, 0.52), (0.05, 0.52)],
                 closed=True, layer="A-COCINA"),
        # Pileta derecha
        Polyline(points=[(0.62, 0.05), (1.15, 0.05),
                         (1.15, 0.52), (0.62, 0.52)],
                 closed=True, layer="A-COCINA"),
        # Desagüe izq
        Circle(cx=0.30, cy=0.285, radius=0.04, layer="A-COCINA"),
        # Desagüe der
        Circle(cx=0.885, cy=0.285, radius=0.04, layer="A-COCINA"),
    ]
    bd = BlockDef()
    bd.name       = "LAVAPLATOS"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


def _make_estufa() -> BlockDef:
    """Cocina/estufa 4 hornillas. Base point = esquina izquierda."""
    ents = [
        # Contorno 0.60 × 0.60
        Polyline(points=[(0.0, 0.0), (0.60, 0.0),
                         (0.60, 0.60), (0.0, 0.60)],
                 closed=True, layer="A-COCINA"),
        # 4 hornillas
        Circle(cx=0.15, cy=0.15, radius=0.09, layer="A-COCINA"),
        Circle(cx=0.45, cy=0.15, radius=0.09, layer="A-COCINA"),
        Circle(cx=0.15, cy=0.45, radius=0.09, layer="A-COCINA"),
        Circle(cx=0.45, cy=0.45, radius=0.09, layer="A-COCINA"),
    ]
    bd = BlockDef()
    bd.name       = "ESTUFA"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


def _make_cama_sencilla() -> BlockDef:
    """Cama sencilla 0.90×1.90. Base point = esquina."""
    ents = [
        Polyline(points=[(0.0, 0.0), (0.90, 0.0),
                         (0.90, 1.90), (0.0, 1.90)],
                 closed=True, layer="A-MOBILIARIO"),
        # Cabecera
        Line(x1=0.0, y1=1.65, x2=0.90, y2=1.65, layer="A-MOBILIARIO"),
        # Almohada
        Polyline(points=[(0.10, 1.68), (0.80, 1.68),
                         (0.80, 1.85), (0.10, 1.85)],
                 closed=True, layer="A-MOBILIARIO"),
    ]
    bd = BlockDef()
    bd.name       = "CAMA-SENCILLA"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


def _make_cama_doble() -> BlockDef:
    """Cama doble 1.40×1.90. Base point = esquina."""
    ents = [
        Polyline(points=[(0.0, 0.0), (1.40, 0.0),
                         (1.40, 1.90), (0.0, 1.90)],
                 closed=True, layer="A-MOBILIARIO"),
        Line(x1=0.0,  y1=1.65, x2=1.40, y2=1.65, layer="A-MOBILIARIO"),
        # Almohada izq
        Polyline(points=[(0.08, 1.68), (0.62, 1.68),
                         (0.62, 1.85), (0.08, 1.85)],
                 closed=True, layer="A-MOBILIARIO"),
        # Almohada der
        Polyline(points=[(0.78, 1.68), (1.32, 1.68),
                         (1.32, 1.85), (0.78, 1.85)],
                 closed=True, layer="A-MOBILIARIO"),
    ]
    bd = BlockDef()
    bd.name       = "CAMA-DOBLE"
    bd.base_point = (0.0, 0.0)
    bd.entities   = ents
    return bd


# ── Catálogo público ─────────────────────────────────────────────────────────
# Lista de (nombre_display, emoji, BlockDef) agrupados por categoría.
BLOCK_LIBRARY: list[tuple[str, str, str, BlockDef]] = [
    # (categoria, nombre_display, emoji, BlockDef)
    ("Puertas",    "Puerta 1.00m",    "🚪", _make_puerta_simple(1.00)),
    ("Puertas",    "Puerta 0.90m",    "🚪", _make_puerta_simple(0.90)),
    ("Puertas",    "Puerta 1.10m",    "🚪", _make_puerta_simple(1.10)),
    ("Puertas",    "Puerta Doble",    "🚪", _make_puerta_doble(1.60)),
    ("Puertas",    "Corrediza 1.00m", "🚪", _make_puerta_corrediza(1.00)),
    ("Ventanas",   "Ventana 0.90m",   "🪟", _make_ventana(0.90)),
    ("Ventanas",   "Ventana 1.20m",   "🪟", _make_ventana(1.20)),
    ("Ventanas",   "Ventana 1.50m",   "🪟", _make_ventana(1.50)),
    ("Ventanas",   "Ventana 0.60m",   "🪟", _make_ventana(0.60)),
    ("Sanitarios", "Inodoro",         "🚽", _make_wc()),
    ("Sanitarios", "Lavatorio",       "🪠", _make_lavatorio()),
    ("Sanitarios", "Ducha",           "🚿", _make_ducha()),
    ("Cocina",     "Lavaplatos",      "🍽", _make_lavaplatos()),
    ("Cocina",     "Estufa",          "🍳", _make_estufa()),
    ("Mobiliario", "Cama Sencilla",   "🛏", _make_cama_sencilla()),
    ("Mobiliario", "Cama Doble",      "🛏", _make_cama_doble()),
]
