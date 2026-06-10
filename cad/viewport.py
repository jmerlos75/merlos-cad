"""
cad/viewport.py
===============
Transformaciones mundo ↔ pantalla y consultas de viewport.

Funciones puras: no dependen de Tkinter, UI ni estado global.
El engine las usa como thin-wrappers delegando self.scale / self.offset_*.

Exporta:
    w2s(scale, ox, oy, wx, wy)  → (sx, sy)
    s2w(scale, ox, oy, sx, sy)  → (wx, wy)
    w2s_many(scale, ox, oy, points) → lista plana [sx0,sy0,sx1,sy1,...]
    viewport_world(scale, ox, oy, W, H) → (vx0, vy0, vx1, vy1)
    zoom_to_fit(entities, W, H, padding) → (scale, ox, oy)
    zoom_window(sx0, sy0, sx1, sy1, scale, ox, oy, W, H) → (scale, ox, oy)
"""
from __future__ import annotations

import math

try:
    import numpy as _np
    _NUMPY = True
except ImportError:
    _NUMPY = False

# Límites globales de zoom — única fuente de verdad para engine + viewport
# SCALE_MIN muy bajo para soportar planos en mm (un edificio de 20 000 mm
# necesita scale ≈ 0.075 px/mm para caber en 1500 px).
SCALE_MIN: float = 0.0001
SCALE_MAX: float = 20000.0


# ── Transforms ───────────────────────────────────────────────────────────────

def w2s(scale: float, ox: float, oy: float,
        wx: float, wy: float) -> tuple[float, float]:
    """Mundo → pantalla.  Y se invierte (mundo Y-arriba, pantalla Y-abajo)."""
    return ox + wx * scale, oy - wy * scale


def s2w(scale: float, ox: float, oy: float,
        sx: float, sy: float) -> tuple[float, float]:
    """Pantalla → mundo."""
    return (sx - ox) / scale, (oy - sy) / scale


def w2s_many(scale: float, ox: float, oy: float,
             points: list) -> list[float]:
    """Transforma lista de (wx, wy) a coords pantalla como lista plana.
    Usa numpy si está disponible; fallback a loop Python."""
    if _NUMPY and len(points) >= 4:
        a  = _np.asarray(points, dtype=_np.float64)
        sx = ox + a[:, 0] * scale
        sy = oy - a[:, 1] * scale
        flat = _np.empty(len(points) * 2, dtype=_np.float64)
        flat[0::2] = sx
        flat[1::2] = sy
        return flat.tolist()
    flat = []
    for wx, wy in points:
        flat.append(ox + wx * scale)
        flat.append(oy - wy * scale)
    return flat


def viewport_world(scale: float, ox: float, oy: float,
                   W: int, H: int) -> tuple[float, float, float, float]:
    """Coordenadas mundo de las esquinas del viewport (vx0, vy0, vx1, vy1)."""
    x0, y0 = s2w(scale, ox, oy, 0, H)
    x1, y1 = s2w(scale, ox, oy, W, 0)
    return x0, y0, x1, y1


# ── Zoom helpers ─────────────────────────────────────────────────────────────

def zoom_to_fit(entities: list, W: int, H: int,
                padding: float = 0.10) -> tuple[float, float, float]:
    """Calcula (scale, offset_x, offset_y) para encuadrar todas las entidades.
    padding = fracción del viewport a dejar de margen (0.10 = 10 %).
    Retorna la vista actual si no hay entidades.

    Usa bounding-box por PERCENTIL (10%–90%) para ignorar entidades "fantasma"
    y coordenadas extremas/inválidas que los DXF suelen incluir.
    Filtra NaN e Inf antes de calcular para evitar scale=NaN.
    """
    import math as _m

    if not entities:
        return 40.0, W / 2, H / 2

    xs, ys = [], []
    # Muestra hasta 20 000 puntos para no bloquear en archivos enormes
    step = max(1, len(entities) // 20_000)
    for e in entities[::step]:
        for pt in (e.bbox_pts() or e.snap_points()):
            x, y = pt[0], pt[1]
            # Descartar NaN, Inf y coordenadas absurdamente grandes (>1e9)
            if _m.isfinite(x) and _m.isfinite(y) and abs(x) < 1e9 and abs(y) < 1e9:
                xs.append(x); ys.append(y)
    if not xs:
        return 40.0, W / 2, H / 2

    # ── Percentil robusto 10%–90% (más agresivo que el 5%–95% anterior) ─
    xs.sort(); ys.sort()
    n = len(xs)
    if n >= 10:
        lo = max(0,   int(n * 0.10))
        hi = min(n-1, int(n * 0.90))
        min_x, max_x = xs[lo], xs[hi]
        min_y, max_y = ys[lo], ys[hi]
    else:
        min_x, max_x = xs[0], xs[-1]
        min_y, max_y = ys[0], ys[-1]

    # Fallback si el percentil colapsa a un punto → usar rango completo
    if max_x - min_x < 1e-6:
        min_x, max_x = xs[0], xs[-1]
    if max_y - min_y < 1e-6:
        min_y, max_y = ys[0], ys[-1]

    ew = max_x - min_x or 1.0
    eh = max_y - min_y or 1.0

    # Escala para que el contenido ocupe (1-2*padding) del viewport
    usable_w = W * (1 - 2*padding)
    usable_h = H * (1 - 2*padding)
    scale = min(usable_w / ew, usable_h / eh)

    # Sanity check: si scale es inválido, devolver vista default
    if not _m.isfinite(scale) or scale <= 0:
        return 40.0, W / 2, H / 2

    scale = max(SCALE_MIN, min(scale, SCALE_MAX))

    # Centrar
    cx_w = (min_x + max_x) / 2
    cy_w = (min_y + max_y) / 2
    ox = W / 2 - cx_w * scale
    oy = H / 2 + cy_w * scale

    # Sanity check final: offsets deben ser finitos
    if not (_m.isfinite(ox) and _m.isfinite(oy)):
        return 40.0, W / 2, H / 2

    return scale, ox, oy


def zoom_window(sx0: float, sy0: float, sx1: float, sy1: float,
                scale: float, ox: float, oy: float,
                W: int, H: int) -> tuple[float, float, float]:
    """Calcula (scale, offset_x, offset_y) para encuadrar una ventana en pantalla."""
    wx0, wy0 = s2w(scale, ox, oy, sx0, sy0)
    wx1, wy1 = s2w(scale, ox, oy, sx1, sy1)
    min_x, max_x = min(wx0, wx1), max(wx0, wx1)
    min_y, max_y = min(wy0, wy1), max(wy0, wy1)
    ew = max_x - min_x or 1.0
    eh = max_y - min_y or 1.0
    new_scale = min(W / ew, H / eh) * 0.9
    new_scale  = max(SCALE_MIN, min(new_scale, SCALE_MAX))
    cx_w = (min_x + max_x) / 2
    cy_w = (min_y + max_y) / 2
    new_ox = W / 2 - cx_w * new_scale
    new_oy = H / 2 + cy_w * new_scale
    return new_scale, new_ox, new_oy
