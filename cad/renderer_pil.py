"""
cad/renderer_pil.py
===================
Renderizador CAD basado en Pillow (PIL).

Convierte la lista de entidades CAD en una PIL Image RGB lista para
convertir a Tkinter PhotoImage.  Cero dependencia de Tkinter o de engine.

Exporta:
    RenderCtx   — contexto inmutable de render (dataclass)
    RendererPIL — clase con caché de fuentes; método principal: render()

Uso típico desde engine.py:
    from cad.renderer_pil import RendererPIL, RenderCtx

    # En __init__:
    self._renderer = RendererPIL()

    # En _redraw_static:
    ctx = RenderCtx(W=W, H=H, scale=self.scale, offset_x=self.offset_x,
                    offset_y=self.offset_y, entities=candidates,
                    layers=self.layers, entity_index=self._entity_index,
                    grid_on=self.grid_on)
    img = self._renderer.render(ctx)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# PIL imports — el módulo no falla si Pillow no está instalado,
# pero RendererPIL.available() devuelve False.
try:
    from PIL import Image as _PILImage
    from PIL import ImageDraw as _PILDraw
    from PIL import ImageFont as _PILFont
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

from cad.entities import (
    Line, Polyline, Circle, Arc, Text, Insert, Hatch, Spline,
    Ellipse, XLine, Leader, ImageRef, Dimension,
    _LINETYPES, _LOD_TEXT_PX, _LOD_DOT_SCALE, _SNAP_CELL as _ENTITIES_SNAP_CELL,
)
import cad.viewport as _vp
from cad.renderer_base import BaseRenderer, RenderResult


# ── Paleta (valores por defecto, sobreescribibles) ───────────────────────────

CV_BG       = "#0A0A0A"
CV_GRID     = "#1A1A1A"
CV_GRID_MAJ = "#282828"
CV_AXIS     = "#333333"
CV_SELECT   = "#FFD700"

GRID_MAJOR  = 1.0
GRID_MINOR  = 0.25


# ── Contexto de render ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RenderCtx:
    """Estado inmutable que el engine pasa al renderer en cada frame."""
    W:            int
    H:            int
    scale:        float
    offset_x:     float
    offset_y:     float
    entities:     list      = field(default_factory=list, hash=False, compare=False)
    layers:       dict      = field(default_factory=dict, hash=False, compare=False)
    block_defs:   dict      = field(default_factory=dict, hash=False, compare=False)
    entity_index: dict      = field(default_factory=dict, hash=False, compare=False)
    entity_cell:  float     = 5.0   # tamaño de celda del índice (adaptativo)
    grid_on:      bool      = True
    # paleta — sobreescribir si el engine usa colores distintos
    bg_color:     str       = CV_BG
    grid_color:   str       = CV_GRID
    grid_maj_color: str     = CV_GRID_MAJ
    axis_color:   str       = CV_AXIS
    select_color: str       = CV_SELECT
    # tamaño de grilla configurable (en unidades modelo — metros por defecto)
    grid_major:   float     = GRID_MAJOR   # espaciado líneas mayores  (default 1.0 m)
    grid_minor:   float     = GRID_MINOR   # espaciado líneas menores  (default 0.25 m)
    cancel_ev:    object    = None   # threading.Event — si se activa, abortar render
    config:       dict      = field(default_factory=dict, hash=False, compare=False)  # settings.json
    sel_version:  int       = 0     # incrementa cuando cambia la selección → invalida VBO cache
    is_panning:   bool      = False  # True durante 300ms tras cualquier cambio de viewport
    # Cola de texto de cotas tesselladas en GL: [(wx,wy,txt,height,angle), …]
    # El GL renderer escribe aquí durante _tess_dim_gl; _render_text_gl lo consume.
    _dim_text_queue: list   = field(default_factory=list, hash=False, compare=False)


# ── Helpers puros ─────────────────────────────────────────────────────────────

def _h2rgb(hex_color: str) -> tuple:
    """Convierte '#RRGGBB' a (R, G, B) para PIL."""
    h = hex_color.lstrip('#')
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return (255, 255, 255)


def _entity_aabb(e, block_defs: dict | None = None) -> tuple:
    """Bounding-box de la entidad en coordenadas mundo (x0,y0,x1,y1)."""
    if isinstance(e, Insert):
        if block_defs is not None:
            return e._world_bbox(block_defs)
        return e.x, e.y, e.x, e.y   # fallback sin block_defs
    if isinstance(e, Text):
        return e.x, e.y, e.x, e.y
    pts = e.bbox_pts()
    if not pts:
        return 0.0, 0.0, 0.0, 0.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _in_viewport(e, vx0: float, vy0: float, vx1: float, vy1: float,
                 block_defs: dict | None = None) -> bool:
    """True si la entidad toca el área mundo dada."""
    ex0, ey0, ex1, ey1 = _entity_aabb(e, block_defs)
    return not (ex1 < vx0 or ex0 > vx1 or ey1 < vy0 or ey0 > vy1)


def build_layer_cache(layers: dict, scale: float,
                      ltscale: float = 1.0) -> dict:
    """
    Construye un dict {nombre: (visible, locked, color, linewidth, dash_px)}
    para acceso O(1) por entidad durante el render.

    Usada tanto por RendererPIL como por el fallback canvas de engine.py,
    garantizando una única fuente de verdad para la resolución de capas.

    dash_px: tupla de píxeles proporcional al zoom y al LTSCALE global.
    ltscale: factor global de escala de linetypes (AutoCAD LTSCALE).
    """
    ltf = max(0.5, min(scale / 40.0, 8.0)) * max(0.01, float(ltscale))
    cache: dict = {}
    for name, lyr in layers.items():
        raw_dash = _LINETYPES.get(lyr.linetype, ())
        dash_px  = tuple(max(1, int(v * ltf)) for v in raw_dash) if raw_dash else ()
        cache[name] = (lyr.visible, lyr.locked, lyr.color, lyr.linewidth, dash_px)
    return cache


def resolve_entity_props(e, lyr_props: tuple,
                         select_color: str = CV_SELECT,
                         scale: float = 1.0,
                         ltscale: float = 1.0) -> tuple:
    """
    Devuelve (color, linewidth, dash, skip) para una entidad dado su lyr_props.

    skip=True  → no dibujar (capa invisible).
    Aplica overrides de selección (#FFD700 + lw+1) y bloqueo (#555555).
    Respeta overrides de color, linetype y linewidth por entidad.
    """
    vis, locked, lyr_col, lyr_lw, dash = lyr_props
    if not vis:
        return "", 1, (), True

    # Color override por entidad
    col = (e.color if (e.color and e.color.lower() not in ("bylayer", ""))
           else lyr_col)

    # Linewidth override por entidad (0 = bylayer)
    ent_lw = getattr(e, "linewidth", 0) or 0
    lw = ent_lw if ent_lw > 0 else lyr_lw

    # Linetype override por entidad (aplica ltscale igual que build_layer_cache)
    ent_lt = getattr(e, "linetype", "bylayer") or "bylayer"
    if ent_lt.upper() not in ("BYLAYER", ""):
        ltf = max(0.5, min(scale / 40.0, 8.0)) * max(0.01, float(ltscale))
        raw_dash = _LINETYPES.get(ent_lt.upper(), ())
        dash = tuple(max(1, int(v * ltf)) for v in raw_dash) if raw_dash else ()

    if e.selected:
        col = select_color
        lw  = max(lw + 1, 2)
    elif locked:
        col = "#555555"
    return col, lw, dash, False


_QUERY_MAX_CELLS = 200 * 200   # 40 000 celdas máx antes de caer en scan lineal

def _query_viewport(entity_index: dict, snap_cell: float,
                    vx0: float, vy0: float,
                    vx1: float, vy1: float) -> list:
    """Entidades que tocan el viewport usando el índice espacial.
    La clave None contiene entidades grandes que se incluyen siempre.
    Si el viewport abarca demasiadas celdas (muy zoom-out) devuelve
    todas las entidades directamente (scan lineal — más rápido que
    iterar millones de celdas vacías)."""
    cx0 = int(math.floor(vx0 / snap_cell))
    cy0 = int(math.floor(vy0 / snap_cell))
    cx1 = int(math.floor(vx1 / snap_cell))
    cy1 = int(math.floor(vy1 / snap_cell))
    seen: set = set()
    # Entidades grandes (None-key): filtrar por AABB antes de incluir.
    # Sin este cull, entidades que abarcan > 40×40 celdas (paredes largas,
    # hatches de terreno) se devuelven en TODOS los frames → 8000+ candidatos
    # aunque el viewport esté en la esquina opuesta del dibujo.
    result: list = []
    large_list = entity_index.get(None, [])
    large_aabb = entity_index.get("__large_aabb__")
    for i, e in enumerate(large_list):
        if large_aabb is not None and i < len(large_aabb):
            ex0, ey0, ex1, ey1 = large_aabb[i]
            if ex1 < vx0 or ex0 > vx1 or ey1 < vy0 or ey0 > vy1:
                continue
        result.append(e)
    seen.update(id(e) for e in result)

    nx = cx1 - cx0 + 1
    ny = cy1 - cy0 + 1

    # Fast-path: devolver __flat__ cuando la query cubre casi todo el índice.
    # Se activa si supera 40k celdas (índice grande) O si cubre ≥ 85% de las
    # celdas reales del índice (índice pequeño pero world completo visible).
    # Ambas condiciones indican "zoom-out total" → devolver todas las entidades.
    _total_cells = entity_index.get("__total_cells__", 0)
    _full_scan   = (nx * ny > _QUERY_MAX_CELLS or
                    (_total_cells > 0 and nx * ny >= _total_cells * 0.85))
    if _full_scan:
        # O(1): devuelve referencia directa a la lista plana pre-calculada.
        flat = entity_index.get("__flat__")
        if flat is not None:
            return flat
        # Fallback (índice sin __flat__: iterar dict)
        for key, ents in entity_index.items():
            if key is None or key == "__flat__" or key == "__aabb__":
                continue
            for e in ents:
                eid = id(e)
                if eid not in seen:
                    seen.add(eid); result.append(e)
        return result

    for cx in range(cx0, cx1 + 1):
        for cy in range(cy0, cy1 + 1):
            for ent in entity_index.get((cx, cy), []):
                eid = id(ent)
                if eid not in seen:
                    seen.add(eid)
                    result.append(ent)
    return result


# ── Clase principal ───────────────────────────────────────────────────────────

class RendererPIL(BaseRenderer):
    """
    Renderizador PIL para el visor CAD.

    El único estado mutable es el caché de fuentes; todo lo demás
    llega a través de RenderCtx en cada llamada a render().
    """

    # Tamaño de celda del índice espacial — única fuente de verdad: cad.entities
    _SNAP_CELL: float = _ENTITIES_SNAP_CELL

    def __init__(self):
        self._font_cache: dict[int, Any] = {}
        # Block tile cache: (block_name, scale_rounded) → PIL Image RGBA
        # Evita re-renderizar el mismo bloque N veces por frame.
        self._block_cache: dict[tuple, Any] = {}

        # ── Cache de fondo (grid + ejes) ──────────────────────────────
        # La cuadrícula y ejes son costosos de recalcular cada frame.
        # Se reutiliza cuando scale/offset/tamaño/colores no cambian.
        # Clave: (scale, offset_x, offset_y, W, H, grid_on, bg_color,
        #         grid_color, grid_maj_color, axis_color)
        self._bg_cache_img: Any = None
        self._bg_cache_key: tuple | None = None

    # ── API pública ──────────────────────────────────────────────────

    @staticmethod
    def available() -> bool:
        """True si Pillow está instalado y es usable."""
        return _PIL_OK

    # ── Mapeo nombre de fuente → archivo TTF (mismo que dxf_export) ──────
    _FONT_MAP: dict = {
        "Arial":           "arial.ttf",
        "Courier New":     "cour.ttf",
        "Consolas":        "consola.ttf",
        "Segoe UI":        "segoeui.ttf",
        "Times New Roman": "times.ttf",
        "Calibri":         "calibri.ttf",
        "Helvetica":       "arial.ttf",
    }

    def render(self, ctx: RenderCtx) -> "RenderResult":
        """
        Renderiza fondo + grid + ejes + entidades.
        Retorna RenderResult con .image (PIL Image RGB) lista para PhotoImage.
        Lanza ImportError si Pillow no está disponible.
        """
        if not _PIL_OK:
            raise ImportError("Pillow no está instalado (pip install pillow)")

        # Leer fuente configurada en textstyles → usada en _get_font
        # Garantiza que el visor usa la misma fuente que el DXF exportado.
        try:
            _tx = ctx.config.get("textstyles", {})
            _active = _tx.get("active", "")
            _tx_data = _tx.get("styles", {}).get(_active, {})
            _font_name = _tx_data.get("font", "")
            self._configured_font = self._FONT_MAP.get(_font_name, None)
        except Exception:
            self._configured_font = None

        # ── Fondo: imagen nueva + grid + ejes ──────────────────────────────
        img  = _PILImage.new("RGB", (ctx.W, ctx.H), _h2rgb(ctx.bg_color))
        draw = _PILDraw.Draw(img)

        # 1. Grid — solo se dibuja en zoom medio-alto (scale >= 10)
        #    En zoom bajo: grid sería sub-pixel, se omite (LOD)
        # 1. Grid
        if ctx.grid_on:
            sc_g = ctx.scale
            if sc_g >= 10:
                # Zoom medio-alto: el grid tiene varias líneas → vale cachear.
                # Clave: TODOS los parámetros que afectan el aspecto del grid.
                _bg_key = (
                    sc_g, ctx.offset_x, ctx.offset_y,
                    ctx.W, ctx.H,
                    ctx.bg_color, ctx.grid_color, ctx.grid_maj_color,
                )
                if self._bg_cache_img is not None and self._bg_cache_key == _bg_key:
                    # Cache hit: pegar imagen de grid precalculada sobre el fondo
                    img.paste(self._bg_cache_img)
                else:
                    # Cache miss: dibujar grid en imagen separada y guardar
                    _grid_img  = _PILImage.new("RGB", (ctx.W, ctx.H),
                                               _h2rgb(ctx.bg_color))
                    _grid_draw = _PILDraw.Draw(_grid_img)
                    self._draw_grid(_grid_draw, ctx)
                    self._bg_cache_img = _grid_img
                    self._bg_cache_key = _bg_key
                    img.paste(_grid_img)
            else:
                # Zoom bajo (scale < 10): grid no se dibuja o es muy disperso,
                # no vale la pena cachear
                self._draw_grid(draw, ctx)

        # 2. Ejes
        ax = _h2rgb(ctx.axis_color)
        _ax_x, _ax_y = _vp.w2s(ctx.scale, ctx.offset_x, ctx.offset_y, 0.0, 0.0)
        draw.line([(0, int(_ax_y)), (ctx.W, int(_ax_y))], fill=ax, width=1)
        draw.line([(int(_ax_x), 0), (int(_ax_x), ctx.H)], fill=ax, width=1)

        # 3. Entidades con culling de viewport
        self._draw_entities(draw, img, ctx)

        return RenderResult(image=img, backend='pil')

    # ── Grid ─────────────────────────────────────────────────────────

    def _draw_grid(self, draw, ctx: RenderCtx):
        sc    = ctx.scale
        gmin  = ctx.grid_minor   # metros — espaciado menor (configurable)
        gmaj  = ctx.grid_major   # metros — espaciado mayor (configurable)

        # 3 niveles de zoom idénticos en PIL y OpenGL
        if sc >= 160:
            step_f, step_m = gmin,       gmaj
        elif sc >= 40:
            step_f, step_m = gmaj,       gmaj * 5
        elif sc >= 10:
            step_f, step_m = gmaj * 5,   gmaj * 10
        else:
            return

        wx0, wy0 = _vp.s2w(sc, ctx.offset_x, ctx.offset_y, 0, ctx.H)
        wx1, wy1 = _vp.s2w(sc, ctx.offset_x, ctx.offset_y, ctx.W, 0)
        grid_col = _h2rgb(ctx.grid_color)
        grid_maj = _h2rgb(ctx.grid_maj_color)

        for step, col in [(step_f, grid_col), (step_m, grid_maj)]:
            n_cols = int((wx1 - wx0) / step + 2)
            n_rows = int((wy1 - wy0) / step + 2)
            if n_cols * n_rows > 4000:
                continue
            x = math.floor(wx0 / step) * step
            while x <= wx1 + step:
                sx = int(ctx.offset_x + x * sc)
                draw.line([(sx, 0), (sx, ctx.H)], fill=col, width=1)
                x += step
            y = math.floor(wy0 / step) * step
            while y <= wy1 + step:
                sy = int(ctx.offset_y - y * sc)
                draw.line([(0, sy), (ctx.W, sy)], fill=col, width=1)
                y += step

    # ── Entidades ────────────────────────────────────────────────────

    def _draw_entities(self, draw, img, ctx: RenderCtx):
        """Itera entidades visibles (con culling) y las dibuja."""
        import time as _time
        _t0_total = _time.perf_counter()
        _slow_log = []   # [(ms, tipo, info)]

        vx0, vy0, vx1, vy1 = _vp.viewport_world(
            ctx.scale, ctx.offset_x, ctx.offset_y, ctx.W, ctx.H)
        margin = max(5.0, 50.0 / ctx.scale)
        vx0 -= margin; vy0 -= margin
        vx1 += margin; vy1 += margin

        if ctx.entity_index:
            candidates = _query_viewport(
                ctx.entity_index, ctx.entity_cell, vx0, vy0, vx1, vy1)
            # Si los candidatos son la lista plana, usar AABB precalculada por índice
            aabb_cache = (ctx.entity_index.get("__aabb__")
                          if candidates is ctx.entity_index.get("__flat__")
                          else None)
            # Para candidatos filtrados: lookup O(1) por id en vez de recalcular
            aabb_by_id = ctx.entity_index.get("__aabb_by_id__")
        else:
            candidates = ctx.entities
            aabb_cache = None
            aabb_by_id = None

        # Caché de capas — fuente compartida con el path canvas de engine
        _ltscale = float(ctx.config.get("ltscale", 1.0))
        lyr_cache = build_layer_cache(ctx.layers, ctx.scale, ltscale=_ltscale)
        lyr_def   = (True, False, "#FFFFFF", 1, ())

        sc = ctx.scale
        _lod_skip_px = 0.4   # entidad < 0.4 px → no dibujar (LOD agresivo)
        use_lod = sc < 1.0

        _cancel_ev = ctx.cancel_ev   # None o threading.Event

        if aabb_cache is not None:
            # ── Path rápido: AABB precalculada, sin llamar _entity_aabb() ──────
            for i, e in enumerate(candidates):
                # Chequear cancelación cada 50 entidades (evita overhead continuo)
                if _cancel_ev is not None and i % 50 == 0 and _cancel_ev.is_set():
                    return
                ex0, ey0, ex1, ey1 = aabb_cache[i]
                if ex1 < vx0 or ex0 > vx1 or ey1 < vy0 or ey0 > vy1:
                    continue
                if use_lod:
                    span_px = max((ex1 - ex0) * sc, (ey1 - ey0) * sc)
                    if span_px < _lod_skip_px:
                        continue
                if isinstance(e, Text) and e.height * sc < _LOD_TEXT_PX:
                    continue
                col, lw, dash, skip = resolve_entity_props(
                    e, lyr_cache.get(e.layer, lyr_def), ctx.select_color,
                    scale=ctx.scale, ltscale=_ltscale)
                if skip:
                    continue
                _te = _time.perf_counter()
                self._draw_entity(draw, img, e, col, lw, dash, ctx)
                _dt = (_time.perf_counter() - _te) * 1000
                if _dt > 30:
                    _slow_log.append((_dt, type(e).__name__,
                        getattr(e, 'pattern', '') or getattr(e, 'layer', ''),
                        len(getattr(e, 'boundary', getattr(e, 'points', [])))))
        else:
            # ── Path normal: AABB desde dict O(1) o cálculo on-the-fly ──────
            for _i, e in enumerate(candidates):
                if _cancel_ev is not None and _i % 50 == 0 and _cancel_ev.is_set():
                    return
                # Preferir lookup O(1) en vez de recalcular desde vértices
                if aabb_by_id is not None:
                    cached = aabb_by_id.get(id(e))
                    ex0, ey0, ex1, ey1 = cached if cached is not None else _entity_aabb(e, ctx.block_defs)
                else:
                    ex0, ey0, ex1, ey1 = _entity_aabb(e, ctx.block_defs)
                if ex1 < vx0 or ex0 > vx1 or ey1 < vy0 or ey0 > vy1:
                    continue
                if use_lod:
                    span_px = max((ex1 - ex0) * sc, (ey1 - ey0) * sc)
                    if span_px < _lod_skip_px:
                        continue
                if isinstance(e, Text) and e.height * sc < _LOD_TEXT_PX:
                    continue
                col, lw, dash, skip = resolve_entity_props(
                    e, lyr_cache.get(e.layer, lyr_def), ctx.select_color,
                    scale=ctx.scale, ltscale=_ltscale)
                if skip:
                    continue
                _te = _time.perf_counter()
                self._draw_entity(draw, img, e, col, lw, dash, ctx)
                _dt = (_time.perf_counter() - _te) * 1000
                if _dt > 30:
                    _slow_log.append((_dt, type(e).__name__,
                        getattr(e, 'pattern', '') or getattr(e, 'layer', ''),
                        len(getattr(e, 'boundary', getattr(e, 'points', [])))))

        _total_ms = (_time.perf_counter() - _t0_total) * 1000
        if _total_ms > 200 or _slow_log:
            print(f"[RENDER] total={_total_ms:.0f}ms  scale={ctx.scale:.4f}  "
                  f"candidatos={len(list(candidates)) if not isinstance(candidates, list) else len(candidates)}")
            for ms, tp, info, npts in sorted(_slow_log, reverse=True)[:10]:
                print(f"  !! {ms:6.0f}ms  {tp:12s}  pat/layer={info!r:20s}  pts={npts}")

    def _draw_entity(self, draw, img, e, col: str, lw: int,
                     dash: tuple, ctx: RenderCtx):
        """Dibuja una entidad CAD en el PIL ImageDraw."""
        lw  = max(1, int(lw))
        sc  = ctx.scale
        ox  = ctx.offset_x
        oy  = ctx.offset_y

        if isinstance(e, Line):
            sx1, sy1 = _vp.w2s(sc, ox, oy, e.x1, e.y1)
            sx2, sy2 = _vp.w2s(sc, ox, oy, e.x2, e.y2)
            if dash:
                self._dashed_line(draw, [(sx1, sy1), (sx2, sy2)], col, lw, dash)
            else:
                draw.line([(sx1, sy1), (sx2, sy2)], fill=col, width=lw)

        elif isinstance(e, Polyline):
            npts = len(e.points)
            if npts < 2:
                return
            # w2s_many usa numpy solo si hay suficientes puntos para amortizar
            # el overhead de np.asarray(). Para polylines típicas (4-12 pts),
            # el loop Python es igual o más rápido.
            # _dashed_line necesita lista de tuplas; draw.line acepta ambos formatos.
            # w2s_many (numpy) es más rápido para polylines grandes no-dashed.
            if dash:
                # Siempre usar tuplas para _dashed_line
                pts = [_vp.w2s(sc, ox, oy, px, py) for px, py in e.points]
                pts_draw = pts + [pts[0]] if (e.closed and npts > 2) else pts
                self._dashed_line(draw, pts_draw, col, lw, dash)
            elif npts >= 20:
                # numpy batch solo para draw.line (acepta lista plana)
                flat = _vp.w2s_many(sc, ox, oy, e.points)
                if e.closed and npts > 2:
                    flat = flat + flat[:2]
                draw.line(flat, fill=col, width=lw)
            else:
                pts = [_vp.w2s(sc, ox, oy, px, py) for px, py in e.points]
                pts_draw = pts + [pts[0]] if (e.closed and npts > 2) else pts
                draw.line(pts_draw, fill=col, width=lw)

        elif isinstance(e, Spline):
            if len(e.points) < 2:
                return
            ipts = e.interp(n_seg=20)
            # Splines siempre tienen 20*n segmentos interpolados → vale numpy
            flat = _vp.w2s_many(sc, ox, oy, ipts)
            if len(ipts) >= 2:
                if dash:
                    self._dashed_line(draw, flat, col, lw, dash)
                else:
                    draw.line(flat, fill=col, width=lw)

        elif isinstance(e, Circle):
            scx, scy = _vp.w2s(sc, ox, oy, e.cx, e.cy)
            sr = e.radius * sc
            if sr > 30_000:
                sr = 30_000   # cap igual que Arc
            bbox = [scx - sr, scy - sr, scx + sr, scy + sr]
            draw.ellipse(bbox, outline=col, fill=None, width=lw)
            if sc >= _LOD_DOT_SCALE:
                draw.ellipse([scx-2, scy-2, scx+2, scy+2], fill=col, outline=None)

        elif isinstance(e, Arc):
            scx, scy = _vp.w2s(sc, ox, oy, e.cx, e.cy)
            sr   = e.radius * sc
            # Cap: PIL arc es O(radius) en tiempo. Radio > 30k px = visible
            # como casi recta pero puede tomar 50-200ms en PIL.
            if sr > 30_000:
                sr = 30_000
            bbox = [scx - sr, scy - sr, scx + sr, scy + sr]
            # PIL ángulos CW desde East; AutoCAD CCW desde East.
            # Conversión: pil_start = -end_ang, pil_end = -start_ang (CCW)
            if e.ccw:
                pil_start = (-e.end_ang) % 360
                pil_end   = (-e.start_ang) % 360
            else:
                pil_start = (-e.start_ang) % 360
                pil_end   = (-e.end_ang) % 360
            draw.arc(bbox, start=pil_start, end=pil_end, fill=col, width=lw)

        elif isinstance(e, Text):
            sx, sy = _vp.w2s(sc, ox, oy, e.x, e.y)
            px = max(8, int(e.height * sc))
            if px < _LOD_TEXT_PX:
                return
            font = self._get_font(px)
            _vl = getattr(e, 'valign', 0)
            # Pillow vertical anchors: a=ascender(baseline) m=middle t=top
            v_anchor = "t" if _vl == 3 else ("m" if _vl == 2 else "a")
            draw_y = sy if v_anchor != "a" else sy - px
            is_multi = '\n' in e.content
            if abs(e.angle) < 1.0:
                if e.halign == 2 and is_multi:
                    # MTEXT TR multilínea: cuadro anclado a la derecha, contenido
                    # left-justified dentro del cuadro.
                    # Calcular borde izquierdo = sx - ancho_línea_más_larga
                    _line_widths = [
                        (font.getbbox(ln)[2] - font.getbbox(ln)[0]
                         if hasattr(font, 'getbbox') else len(ln) * px // 2)
                        for ln in e.content.split('\n') if ln
                    ]
                    _box_left = sx - (max(_line_widths) if _line_widths else 0)
                    draw.multiline_text((_box_left, draw_y), e.content,
                                        fill=col, font=font,
                                        anchor="l" + v_anchor,
                                        align="left",
                                        spacing=int(px * 0.4))
                else:
                    h_anchor = ("l", "m", "r")[min(e.halign, 2)]
                    draw.text((sx, draw_y), e.content, fill=col,
                              font=font, anchor=h_anchor + v_anchor)
            else:
                self._draw_rotated_text(img, e.content, sx, sy, px,
                                        e.angle, col, font,
                                        halign=e.halign, valign=_vl)

        elif isinstance(e, Dimension):
            self._draw_dim_pil(draw, img, e, col, lw, ctx)

        elif isinstance(e, Hatch):
            self._draw_hatch(draw, img, e, col, lw, ctx)

        elif isinstance(e, Insert):
            self._draw_insert(draw, img, e, col, lw, ctx)

        elif isinstance(e, Ellipse):
            bpts = e._pts_on_boundary(72)
            if len(bpts) >= 2:
                flat = _vp.w2s_many(sc, ox, oy, bpts)
                flat = flat + flat[:2]   # cerrar la elipse
                if dash:
                    self._dashed_line(draw, flat, col, lw, dash)
                else:
                    draw.line(flat, fill=col, width=lw)

        elif isinstance(e, XLine):
            import math as _math
            dx = e.x2 - e.x1
            dy = e.y2 - e.y1
            d = _math.hypot(dx, dy)
            if d < 1e-10:
                return
            ux, uy = dx / d, dy / d
            # Find canvas size via ctx
            W = getattr(ctx, 'width',  5000)
            H = getattr(ctx, 'height', 5000)
            # Project to screen space to get extent
            margin = max(W, H) + 500
            sx1, sy1 = _vp.w2s(sc, ox, oy, e.x1, e.y1)
            # Find t_min/t_max so line exits [-margin, W+margin] x [-margin, H+margin]
            t_values = []
            if abs(ux * sc) > 1e-6:
                for bnd in (-margin - sx1, W + margin - sx1):
                    t_values.append(bnd / (ux * sc))
            if abs(uy * sc) > 1e-6:
                for bnd in (-margin - sy1, H + margin - sy1):
                    # screen y goes down, world y goes up
                    t_values.append(bnd / (-uy * sc))
            if not t_values:
                return
            t_min = min(t_values)
            t_max = max(t_values)
            # Draw in world coords
            wx_start = e.x1 + ux * t_min
            wy_start = e.y1 + uy * t_min
            wx_end   = e.x1 + ux * t_max
            wy_end   = e.y1 + uy * t_max
            sxs, sys_ = _vp.w2s(sc, ox, oy, wx_start, wy_start)
            sxe, sye  = _vp.w2s(sc, ox, oy, wx_end,   wy_end)
            # Always use dashed for xlines
            self._dashed_line(draw, [(sxs, sys_), (sxe, sye)], col, lw, dash or (8, 4))

        elif isinstance(e, Leader):
            import math as _math
            if len(e.points) < 2:
                return
            flat = _vp.w2s_many(sc, ox, oy, e.points)
            # Reconstruir lista de tuplas solo para los 2 primeros puntos (flecha)
            spts_pairs = list(zip(flat[0::2], flat[1::2]))
            # Draw polyline con flat list (más rápido)
            draw.line(flat, fill=col, width=lw)
            # Draw filled arrowhead at points[0] pointing from points[1]
            tip = spts_pairs[0]
            base_pt = spts_pairs[1]
            arrow_px = max(6, int(e.arrow_size * sc))
            adx = tip[0] - base_pt[0]
            ady = tip[1] - base_pt[1]
            alen = _math.hypot(adx, ady)
            if alen > 1e-3:
                uax, uay = adx / alen, ady / alen
                bx = tip[0] - uax * arrow_px
                by = tip[1] - uay * arrow_px
                perp_x = -uay * arrow_px * 0.35
                perp_y =  uax * arrow_px * 0.35
                try:
                    draw.polygon([
                        (tip[0], tip[1]),
                        (bx + perp_x, by + perp_y),
                        (bx - perp_x, by - perp_y),
                    ], fill=col, outline=None)
                except Exception:
                    pass
            # Draw text at last point if any
            if e.text:
                tx, ty = spts_pairs[-1]
                px_h = max(8, int(0.10 * sc))
                if px_h >= _LOD_TEXT_PX:
                    try:
                        font = self._get_font(px_h)
                        draw.text((tx + 4, ty - px_h), e.text, fill=col,
                                  font=font, anchor="la")
                    except Exception:
                        pass

        elif isinstance(e, ImageRef):
            self._draw_image_ref(img, e, ctx)

    # ── ImageRef ─────────────────────────────────────────────────────

    def _draw_image_ref(self, img, e: "ImageRef", ctx):
        """Renderiza una imagen raster en el canvas PIL."""
        if not _PIL_OK:
            return
        sc = ctx.scale
        ox = ctx.offset_x
        oy = ctx.offset_y

        # Tamaño en píxeles
        pw = max(1, int(abs(e.width)  * sc))
        ph = max(1, int(abs(e.height) * sc))
        if pw < 2 or ph < 2:
            return

        # Posición en pantalla de la esquina inferior-izquierda (mundo → screen)
        sx, sy = _vp.w2s(sc, ox, oy, e.x, e.y)
        # En screen Y crece hacia abajo, pero en mundo Y puede crecer hacia arriba
        # La esquina superior izquierda en pantalla es (sx, sy - ph)
        paste_x = int(sx)
        paste_y = int(sy) - ph

        try:
            src = _PILImage.open(e.path).convert("RGBA")
            src = src.resize((pw, ph), _PILImage.LANCZOS)
            if abs(e.angle) > 0.1:
                # Rotar expandiendo el canvas para no perder píxeles
                src = src.rotate(-e.angle, expand=True, resample=_PILImage.BICUBIC)
                # Ajustar posición al nuevo bounding box
                paste_x -= (src.width  - pw) // 2
                paste_y -= (src.height - ph) // 2
            # Convertir a RGB para pegar en imagen RGB de fondo
            bg = _PILImage.new("RGBA", img.size, (0, 0, 0, 0))
            bg.paste(src, (paste_x, paste_y))
            img.paste(bg.convert("RGB"), mask=bg.split()[3])
        except Exception:
            # Fallback: rectángulo con X (archivo no encontrado o error de carga)
            draw = _PILDraw.Draw(img)
            ex0, ey0 = int(sx), int(sy) - ph
            ex1, ey1 = int(sx) + pw, int(sy)
            draw.rectangle([ex0, ey0, ex1, ey1], outline=(100, 100, 200), width=1)
            draw.line([ex0, ey0, ex1, ey1], fill=(100, 100, 200), width=1)
            draw.line([ex0, ey1, ex1, ey0], fill=(100, 100, 200), width=1)

    # ── Hatch ────────────────────────────────────────────────────────

    # ── Tabla de patrones: nombre → lista de (ángulo_base, offsets_adicionales)
    # Cada entrada: (ángulo_base_deg, doble_línea)
    # ángulo_base se usa solo si el hatch no trae ángulo propio.
    # doble_línea=True → dibuja también a ang+90 (cuadrícula).
    _HATCH_PATTERNS: dict = {
        # ── Manejado aparte ──────────────────────────────────────────────────
        "SOLID":    None,
        # ── ANSI (achurados de ingeniería) ───────────────────────────────────
        "ANSI31":   (45.0,  False),   # acero / metal general
        "ANSI32":   (45.0,  False),
        "ANSI33":   (45.0,  False),
        "ANSI34":   (45.0,  False),
        "ANSI35":   (45.0,  False),
        "ANSI36":   (45.0,  False),
        "ANSI37":   (45.0,  False),
        "ANSI38":   (45.0,  False),
        # ── ISO ─────────────────────────────────────────────────────────────
        "ISO02W100": (0.0,  False),
        "ISO03W100": (0.0,  False),
        "ISO04W100": (0.0,  False),
        "ISO05W100": (0.0,  False),
        "ISO06W100": (0.0,  False),
        "ISO07W100": (45.0, False),
        "ISO08W100": (45.0, False),
        "ISO09W100": (0.0,  True),
        "ISO10W100": (0.0,  True),
        "ISO11W100": (45.0, True),
        "ISO12W100": (45.0, True),
        "ISO13W100": (0.0,  False),
        "ISO14W100": (0.0,  False),
        "ISO15W100": (0.0,  False),
        # ── Líneas simples ───────────────────────────────────────────────────
        "LINES":    (0.0,   False),
        "LINE":     (0.0,   False),
        "DASH":     (45.0,  False),
        # ── Cuadrículas ──────────────────────────────────────────────────────
        "CROSS":    (0.0,   True),
        "NET":      (0.0,   True),
        "NET3":     (60.0,  True),
        "SQUARE":   (45.0,  True),
        "HEX":      (0.0,   True),
        "HONEY":    (0.0,   True),
        "ESCHER":   (60.0,  True),
        "STARS":    (0.0,   True),
        "TRANS":    (0.0,   True),
        # ── Materiales ───────────────────────────────────────────────────────
        "EARTH":    (45.0,  False),   # tierra / relleno
        "GRASS":    (90.0,  False),
        "GRAVEL":   (45.0,  False),
        "SAND":     (45.0,  False),
        "MUDST":    (0.0,   False),   # barro
        "SWAMP":    (0.0,   False),
        "CORK":     (45.0,  False),
        "FLEX":     (45.0,  False),
        "INSUL":    (0.0,   False),   # aislamiento
        "SACNCR":   (45.0,  False),   # concreto
        "CONC":     (45.0,  False),
        "CONCRETE": (45.0,  False),
        "BRICK":    (0.0,   False),
        "BRSTONE":  (0.0,   False),
        "STONE":    (45.0,  False),
        "DOLMIT":   (0.0,   False),
        "TRIANG":   (60.0,  False),
        "ZIGZAG":   (45.0,  False),
        "HOUND":    (0.0,   False),
        # ── Madera / acabados ─────────────────────────────────────────────────
        "WOOD":     (0.0,   False),
        "PLYWOOD":  (45.0,  False),
        "STEEL":    (45.0,  False),
        "SKIRTING": (45.0,  False),   # moldura / rodapié
        "CORK2":    (45.0,  False),
        # ── AR (arquitectura) ─────────────────────────────────────────────────
        "AR-B816":  (0.0,   False),
        "AR-B816C": (0.0,   False),
        "AR-B88":   (0.0,   False),
        "AR-BRELM": (0.0,   False),
        "AR-BRPAT": (0.0,   False),
        "AR-BRSTD": (0.0,   False),
        "AR-CONC":  (45.0,  False),
        "AR-HBONE": (45.0,  False),
        "AR-PARQ1": (45.0,  False),
        "AR-RROOF": (0.0,   False),
        "AR-RSHKE": (0.0,   False),
        "AR-SAND":  (45.0,  False),
        # ── Varios ───────────────────────────────────────────────────────────
        "DOTS":     (0.0,   False),
        "ANGLE":    (45.0,  False),
        "BOX":      (0.0,   True),
        "BRASS":    (45.0,  False),
        "CLAY":     (0.0,   False),
        "GOST_GLASS": (45.0, True),
        "GOST_WOOD":  (0.0,  False),
        "GOST_IRON":  (45.0, False),
        "ALUMINUM":   (45.0, False),
    }

    # Tamaño máximo del tile de hatch en píxeles por lado.
    # Polígonos más grandes se recortan al viewport para evitar OOM.
    _HATCH_MAX_TILE = 3000

    def _draw_hatch(self, draw, img, e: Hatch, col: str, lw: int, ctx):
        """Renderiza un Hatch (relleno/sombreado) sobre el PIL ImageDraw."""
        if len(e.boundary) < 3:
            return
        sc = ctx.scale
        ox = ctx.offset_x
        oy = ctx.offset_y
        W, H = img.size

        pts_px  = [_vp.w2s(sc, ox, oy, x, y) for x, y in e.boundary]
        pts_int = [(int(p[0]), int(p[1])) for p in pts_px]
        col_rgb = _h2rgb(col)

        xs = [p[0] for p in pts_px]
        ys = [p[1] for p in pts_px]
        x0_px = int(min(xs)); x1_px = int(max(xs))
        y0_px = int(min(ys)); y1_px = int(max(ys))

        if x1_px - x0_px < 1 or y1_px - y0_px < 1:
            return

        # ── SOLID / GRADIENTE ──────────────────────────────────────────────────
        if e.pattern == "SOLID" or getattr(e, "is_gradient", False):
            try:
                fill_col = col_rgb
                if getattr(e, "is_gradient", False):
                    gc = getattr(e, "gradient_color", "bylayer")
                    if gc not in ("bylayer", "byblock") and gc.startswith("#"):
                        fill_col = _h2rgb(gc)
                draw.polygon(pts_int, fill=fill_col, outline=None)
            except Exception:
                pass
            return

        # ── Patrón de líneas con clipping correcto ─────────────────────────────
        #
        # CLAVE: el tile usa como origen el bounding box del propio polígono,
        # no el viewport. Así pts_loc siempre tiene coordenadas locales válidas
        # incluso cuando el polígono se extiende fuera de pantalla.
        #
        # Si el tile resultante es demasiado grande (polígono enorme muy zoomado)
        # se recorta al viewport para no consumir memoria.
        try:
            # ── Origen del tile = intersección del bbox con el viewport ──────────
            # SIEMPRE clamp al viewport: evita allocar tiles de hasta 36 MB
            # (ej. hatch 3000×3000 RGBA) para hatches grandes con zoom alejado.
            # PIL dibuja polígonos con vértices fuera del tile correctamente
            # (los clipa al borde de la imagen), así que el resultado visual
            # es idéntico al tile completo, con una fracción de la memoria.
            tile_x0 = max(x0_px - 2, 0);  tile_y0 = max(y0_px - 2, 0)
            tile_x1 = min(x1_px + 2, W);  tile_y1 = min(y1_px + 2, H)
            tw = tile_x1 - tile_x0
            th = tile_y1 - tile_y0
            if tw <= 0 or th <= 0:
                return   # hatch completamente fuera del viewport

            # ── Coordenadas locales relativas al origen del tile ─────────────
            # TODOS los vértices se refieren al mismo origen → PIL dibuja
            # correctamente aunque algunos queden fuera del tile.
            pts_loc = [(int(p[0]) - tile_x0, int(p[1]) - tile_y0) for p in pts_px]

            # ── Máscara: blanco dentro del polígono ──────────────────────────
            poly_mask = _PILImage.new("L", (tw, th), 0)
            _PILDraw.Draw(poly_mask).polygon(pts_loc, fill=255)

            # ── Tile de líneas ───────────────────────────────────────────────
            line_tile = _PILImage.new("RGBA", (tw, th), (0, 0, 0, 0))
            ldraw     = _PILDraw.Draw(line_tile)

            lcx, lcy = tw / 2, th / 2
            line_col = col_rgb + (255,)

            # ── Construir lista de (ang_deg, spacing_px) a dibujar ───────────
            # Prioridad: pattern_lines del DXF > tabla de patrones > fallback
            plines = getattr(e, "pattern_lines", [])
            if plines:
                # Datos reales del DXF: ángulo de familia + rotación de entidad
                families = [
                    (pl_ang + e.angle, max(3, min(200, int(pl_sp * sc))))
                    for pl_ang, pl_sp in plines
                    if pl_sp * sc >= 1          # descartar familias demasiado densas
                ]
                if not families:               # si todas eran demasiado densas, 1 genérica
                    families = [(e.angle if e.angle != 0.0 else 45.0, 8)]
            else:
                # Fallback: tabla de patrones conocidos
                pat_info = self._HATCH_PATTERNS.get(e.pattern.upper())
                raw_sp   = e.scale * sc
                spacing  = max(2, min(200, int(raw_sp))) if raw_sp > 0 else 8
                if pat_info is not None:
                    base_ang, is_cross = pat_info
                    ang = e.angle if e.angle != 0.0 else base_ang
                    families = [(ang, spacing), (ang + 90, spacing)] if is_cross else [(ang, spacing)]
                else:
                    ang      = e.angle if e.angle != 0.0 else 45.0
                    families = [(ang, spacing)]

            for ang_deg, spacing in families:
                diag  = int(math.hypot(tw, th)) + spacing * 2
                a_rad = math.radians(ang_deg)
                ca  = math.cos(a_rad);  sa  = math.sin(a_rad)
                pca = math.cos(a_rad + math.pi / 2)
                psa = math.sin(a_rad + math.pi / 2)
                n   = diag // spacing + 2
                for i in range(-n, n + 1):
                    d  = i * spacing
                    mx = lcx + pca * d;  my = lcy + psa * d
                    ldraw.line([(mx - ca * diag, my - sa * diag),
                                (mx + ca * diag, my + sa * diag)],
                               fill=line_col, width=1)

            # ── Aplicar máscara y recortar al viewport antes de pegar ─────────
            result = _PILImage.new("RGBA", (tw, th), (0, 0, 0, 0))
            result.paste(line_tile, (0, 0), poly_mask)

            # Intersección del tile con el viewport (evita pegar fuera del canvas)
            paste_x = tile_x0;  paste_y = tile_y0
            crop_x0 = max(0, -paste_x);  crop_y0 = max(0, -paste_y)
            crop_x1 = tw - max(0, paste_x + tw - W)
            crop_y1 = th - max(0, paste_y + th - H)
            if crop_x1 > crop_x0 and crop_y1 > crop_y0:
                region = result.crop((crop_x0, crop_y0, crop_x1, crop_y1))
                img.paste(region,
                          (paste_x + crop_x0, paste_y + crop_y0),
                          region)

        except Exception:
            try:
                draw.polygon(pts_int, outline=col_rgb, fill=None)
            except Exception:
                pass

    # ── Dimension: renderizado PIL procedural ────────────────────────

    def _draw_dim_pil(self, draw, img, e: Dimension, col: str, lw: int, ctx):
        """Renderiza Dimension en PIL: líneas + flechas + texto.
        Corre en hilo de fondo — no usa canvas, todo en píxeles."""
        sc = ctx.scale;  ox = ctx.offset_x;  oy = ctx.offset_y
        col_rgb = _h2rgb(col)

        def w2s(wx, wy):
            sx, sy = _vp.w2s(sc, ox, oy, wx, wy)
            return (int(sx), int(sy))

        # WYSIWYG puro: todo escala proporcional con el zoom, igual que la geometría.
        # Lee el DIMSTYLE del config. Si no existe, usa defaults.
        dimstyle_name = e.style  # ej. "Arq-50"
        dimstyles = ctx.config.get("dimstyles", {}).get("styles", {})
        if dimstyle_name in dimstyles:
            ds = dimstyles[dimstyle_name]
            _DIMTXT      = ds.get("text_height", 0.20)
            _DIMASZ      = ds.get("arrow_size",  0.06)
            _EXT_OFFSET  = ds.get("ext_offset",  0.02)
            _EXT_OVER    = ds.get("ext_overshoot", ds.get("ext_beyond", 0.03))
            _DIMGAP      = ds.get("text_offset",  0.05)   # DIMGAP — gap texto↔línea
            _DIMLFAC     = float(ds.get("scale_factor", 1.0))  # DIMLFAC — factor escala
            _DIMDEC      = int(ds.get("precision", 2))         # DIMDEC  — decimales
            _DIMPOST     = ds.get("suffix", "")                # DIMPOST — sufijo
            _DIMBLK      = ds.get("arrow_type", "closed_filled")  # DIMBLK — tipo flecha
            _line_color  = ds.get("line_color", "bylayer")
            _text_color  = ds.get("text_color", "bylayer")
            _TXT_POS     = ds.get("text_position", "above")   # above / center
            _TXT_ALIGN   = ds.get("text_align", "aligned")    # aligned / horizontal
            _TXT_STYLE   = ds.get("text_style", "")           # archivo TTF para cotas
        else:
            _DIMTXT      = 0.20
            _DIMASZ      = 0.06
            _EXT_OFFSET  = 0.02
            _EXT_OVER    = 0.03
            _DIMGAP      = 0.05
            _DIMLFAC     = 1.0
            _DIMDEC      = 2
            _DIMPOST     = ""
            _DIMBLK      = "closed_filled"
            _line_color  = "bylayer"
            _text_color  = "bylayer"
            _TXT_POS     = "above"
            _TXT_ALIGN   = "aligned"
            _TXT_STYLE   = ""

        def _resolve_color(color_val: str, fallback_rgb: tuple) -> tuple:
            """Convierte color del dimstyle a RGB. bylayer → usa color de capa."""
            if not color_val or color_val.lower() in ("bylayer", "byblock", ""):
                return fallback_rgb
            if color_val.startswith("#") and len(color_val) == 7:
                try:
                    h = color_val.lstrip("#")
                    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                except Exception:
                    pass
            return fallback_rgb

        # col_rgb = color de la capa (fallback)
        # col_line_rgb = color para líneas y flechas de la cota
        # col_text_rgb = color para el número/texto de la cota
        col_line_rgb = _resolve_color(_line_color, col_rgb)
        col_text_rgb = _resolve_color(_text_color, col_rgb)

        TPIX    = max(1, int(_DIMTXT     * sc))
        ARR     = max(1, int(_DIMASZ     * sc))
        GAP     = max(1, int(_EXT_OFFSET * sc))
        OVER    = max(1, int(_EXT_OVER   * sc))
        TXTGAP  = max(1, int(_DIMGAP     * sc))   # gap texto↔línea de cota

        # Sistema de unidades activo (metric / imperial)
        _UNITS = ctx.config.get("units", "metric")
        _IMP_PREC = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16, 5: 32}.get(_DIMDEC, 8)

        # ── Función de texto respetando precision, scale_factor y suffix ──
        def _dim_text(dim_entity) -> str:
            """Genera el texto de la cota aplicando los parámetros del dimstyle."""
            if dim_entity.text_override:
                return dim_entity.text_override
            v = dim_entity.measurement() * _DIMLFAC   # aplica factor de escala
            if dim_entity.dim_type == "ANG":
                return f"{v:.1f}°"                     # ángulo: siempre 1 decimal
            # Imperial: usar format_dim_value para pies-pulgadas
            if _UNITS == "imperial" and dim_entity.dim_type not in ("ANG",):
                try:
                    from cad.layout import format_dim_value as _fdv
                    imp = _fdv(v, "imperial", _IMP_PREC)
                    if imp is not None:
                        if dim_entity.dim_type == "D":
                            return f"Ø{imp}"
                        if dim_entity.dim_type == "R":
                            return f"R{imp}"
                        return imp
                except Exception:
                    pass
            if dim_entity.dim_type == "D":
                return f"Ø{v:.{_DIMDEC}f}{_DIMPOST}"
            if dim_entity.dim_type == "R":
                return f"R{v:.{_DIMDEC}f}{_DIMPOST}"
            if dim_entity.dim_type == "ORD":
                x1_, y1_ = dim_entity.p1;  x2_, y2_ = dim_entity.p2
                eje = "X" if abs(y2_-y1_) >= abs(x2_-x1_) else "Y"
                return f"{eje} {v:.{_DIMDEC}f}{_DIMPOST}"
            return f"{v:.{_DIMDEC}f}{_DIMPOST}"

        # ── Función de flecha según DIMBLK ────────────────────────────────
        def _draw_arrow(draw_, tip, base, size, c_rgb):
            """Dibuja la flecha según el tipo configurado en DIMBLK."""
            tx, ty = tip;  bx, by = base
            dx, dy = bx - tx, by - ty
            d = math.hypot(dx, dy)
            if d < 1e-6:
                return
            ux, uy = dx / d, dy / d
            nx, ny = -uy, ux

            if _DIMBLK == "none":
                return  # sin flecha

            elif _DIMBLK == "dot":
                # Punto relleno en el extremo
                r = max(1, size // 2)
                draw_.ellipse([tx - r, ty - r, tx + r, ty + r], fill=c_rgb)

            elif _DIMBLK == "architectural":
                # Tick diagonal estilo arquitectónico (como ARCHTICK de AutoCAD)
                # Una línea diagonal de 45° centrada en el punto
                half = max(1, size)
                draw_.line([(int(tx - ux*half - nx*half),
                              int(ty - uy*half - ny*half)),
                             (int(tx + ux*half + nx*half),
                              int(ty + uy*half + ny*half))],
                           fill=c_rgb, width=max(1, lw + 1))

            else:
                # closed_filled (default): triángulo relleno
                p1_ = (int(tx + ux*size + nx*size*0.35),
                       int(ty + uy*size + ny*size*0.35))
                p2_ = (int(tx + ux*size - nx*size*0.35),
                       int(ty + uy*size - ny*size*0.35))
                draw_.polygon([tip, p1_, p2_], fill=c_rgb)

        x1, y1 = e.p1;  x2, y2 = e.p2;  px, py = e.pos

        # _draw_arrow y _dim_text ya definidos arriba con DIMBLK, DIMDEC, DIMLFAC, DIMPOST
        # Alias local para compatibilidad con el código de render que usa _arrow:
        def _arrow(draw, tip, base, size, c_rgb):
            _draw_arrow(draw, tip, base, size, c_rgb)

        def _ext_line(draw, sx0, sy0, ex0, ey0, c_rgb, lw):
            """Línea de extensión con gap y prolongación."""
            dx, dy = ex0 - sx0, ey0 - sy0
            d = math.hypot(dx, dy)
            if d < GAP + 1:
                return
            gx = sx0 + dx / d * GAP;  gy = sy0 + dy / d * GAP
            ox_ = ex0 + dx / d * OVER; oy_ = ey0 + dy / d * OVER
            draw.line([(int(gx), int(gy)), (int(ox_), int(oy_))],
                      fill=c_rgb, width=lw)

        _skip_ext = getattr(e, 'no_ext', False)

        if e.dim_type in ("H", "V", "A"):
            # ── Calcular puntos de la línea de cota (d1, d2) ─────────
            if e.dim_type == "H":
                d1 = (x1, py);  d2 = (x2, py)
                _e1s, _e1e = (x1,y1), (x1,py)
                _e2s, _e2e = (x2,y2), (x2,py)
            elif e.dim_type == "V":
                d1 = (px, y1);  d2 = (px, y2)
                _e1s, _e1e = (x1,y1), (px,y1)
                _e2s, _e2e = (x2,y2), (px,y2)
            elif e.rot_angle is not None:
                # ROTADA — cota lineal a ángulo arbitrario
                angle_rad = math.radians(e.rot_angle)
                ux_, uy_ = math.cos(angle_rad), math.sin(angle_rad)
                nx_, ny_ = -uy_, ux_
                dim_n = px * nx_ + py * ny_
                p1_n = x1 * nx_ + y1 * ny_
                off1 = dim_n - p1_n
                d1 = (x1 + nx_ * off1, y1 + ny_ * off1)
                p2_n = x2 * nx_ + y2 * ny_
                off2 = dim_n - p2_n
                d2 = (x2 + nx_ * off2, y2 + ny_ * off2)
                _e1s, _e1e = (x1,y1), d1
                _e2s, _e2e = (x2,y2), d2
            else:  # A — alineada (sin rotación)
                dlen = math.hypot(x2-x1, y2-y1)
                if dlen < 1e-9: return
                ux_, uy_ = (x2-x1)/dlen, (y2-y1)/dlen
                nx_, ny_ = -uy_, ux_
                off = (px-x1)*nx_ + (py-y1)*ny_
                d1 = (x1+nx_*off, y1+ny_*off)
                d2 = (x2+nx_*off, y2+ny_*off)
                _e1s, _e1e = (x1,y1), d1
                _e2s, _e2e = (x2,y2), d2

            # ── Dibujar extensiones (solo si no están suprimidas) ────
            if not _skip_ext:
                _ext_line(draw, *w2s(*_e1s), *w2s(*_e1e), col_line_rgb, lw)
                _ext_line(draw, *w2s(*_e2s), *w2s(*_e2e), col_line_rgb, lw)

            sd1 = w2s(*d1);  sd2 = w2s(*d2)
            draw.line([sd1, sd2], fill=col_line_rgb, width=lw)
            _arrow(draw, sd1, sd2, ARR, col_line_rgb)
            _arrow(draw, sd2, sd1, ARR, col_line_rgb)

            # ── Texto (usa precision, scale_factor, suffix del dimstyle) ──
            txt = _dim_text(e)
            fnt = self._get_dim_font(TPIX, _TXT_STYLE)
            dxs = sd2[0] - sd1[0];  dys = sd2[1] - sd1[1]
            ds  = math.hypot(dxs, dys)
            if ds < 1e-6:
                return
            uxs = dxs / ds;  uys = dys / ds
            nxs = -uys;       nys = uxs

            approx_tw = len(txt) * TPIX * 0.60
            fits_inside = ds > approx_tw + 2 * ARR + 6

            if fits_inside:
                cx_ = (sd1[0] + sd2[0]) / 2
                cy_ = (sd1[1] + sd2[1]) / 2
                # text_position: above=texto sobre línea, center=texto en la línea
                gap = 0 if _TXT_POS == "center" else TXTGAP + TPIX // 2
                tmx = int(cx_ + nxs * gap)
                tmy = int(cy_ + nys * gap)
            else:
                gap = ARR + TXTGAP + TPIX // 2
                tmx = int(sd2[0] + uxs * gap)
                tmy = int(sd2[1] + uys * gap)

            if e.text_pos and fits_inside:
                tmx_dxf, tmy_dxf = w2s(*e.text_pos)
                side = (nxs*(tmx_dxf - (sd1[0]+sd2[0])/2) +
                        nys*(tmy_dxf - (sd1[1]+sd2[1])/2))
                sign = 1 if side >= 0 else -1
                cx_ = (sd1[0] + sd2[0]) / 2
                cy_ = (sd1[1] + sd2[1]) / 2
                tmx = int(cx_ + nxs * sign * gap)
                tmy = int(cy_ + nys * sign * gap)

            # text_align: aligned=texto inclinado con la cota (tipo A), horizontal=siempre recto
            if e.dim_type == "A" and _TXT_ALIGN == "aligned":
                angle_deg = math.degrees(math.atan2(dys, dxs))
                self._draw_text_rotated(img, tmx, tmy, txt, fnt, col_text_rgb, angle_deg)
            else:
                draw.text((tmx, tmy), txt, fill=col_text_rgb, font=fnt, anchor="mm")

        elif e.dim_type in ("R", "D"):
            sc1 = w2s(x1, y1);  sc2 = w2s(x2, y2)
            if e.dim_type == "D":
                opx, opy = x1-(x2-x1), y1-(y2-y1)
                sop = w2s(opx, opy)
                draw.line([sop, sc2], fill=col_line_rgb, width=lw)
                _arrow(draw, sc2, sop, ARR, col_line_rgb)
                _arrow(draw, sop, sc2, ARR, col_line_rgb)
            else:
                draw.line([sc1, sc2], fill=col_line_rgb, width=lw)
                _arrow(draw, sc2, sc1, ARR, col_line_rgb)
            tp = e.text_pos
            tmx = w2s(*tp)[0] if tp else (sc1[0]+sc2[0])//2
            tmy = w2s(*tp)[1] if tp else (sc1[1]+sc2[1])//2 - ARR - TXTGAP
            fnt = self._get_font(TPIX)
            draw.text((tmx, tmy), _dim_text(e), fill=col_text_rgb, font=fnt, anchor="mm")

        elif e.dim_type == "ANG":
            cx_w, cy_w = x1, y1
            scx, scy = w2s(cx_w, cy_w)
            r_w = math.hypot(x2-cx_w, y2-cy_w)
            r_px = r_w * sc
            if r_px < 3:
                return
            a1 = math.degrees(math.atan2(-(y2-cy_w), x2-cx_w))
            a2 = math.degrees(math.atan2(-(py-cy_w), px-cx_w))
            ext = (a2-a1) % 360
            if ext > 180: ext -= 360
            bbox = [scx-r_px, scy-r_px, scx+r_px, scy+r_px]
            draw.arc(bbox, start=a1, end=a1+ext, fill=col_line_rgb, width=lw)
            amid = math.radians(a1 + ext/2)
            tmx = int(scx + math.cos(amid) * (r_px + TXTGAP + TPIX//2))
            tmy = int(scy - math.sin(amid) * (r_px + TXTGAP + TPIX//2))
            fnt = self._get_font(TPIX)
            draw.text((tmx, tmy), _dim_text(e), fill=col_text_rgb, font=fnt, anchor="mm")

    # ── Insert: render con block tile cache ──────────────────────────

    def _draw_insert(self, draw, img, ins: Insert, col: str, lw: int, ctx):
        """Renderiza una instancia de bloque.

        Estrategia: pre-renderizar el BlockDef a una imagen RGBA en espacio
        de bloque (escala 1:1 mundo), luego transformar (escala + rotación)
        y pegar en la imagen principal. El caché evita repetir el pre-render
        para todas las instancias del mismo bloque en el mismo frame.
        """
        bdef = ctx.block_defs.get(ins.block_name)
        if bdef is None or not bdef.entities:
            # Sin definición: marcar con cruz pequeña
            sx, sy = _vp.w2s(ctx.scale, ctx.offset_x, ctx.offset_y, ins.x, ins.y)
            r = 4
            draw.line([(sx-r, sy), (sx+r, sy)], fill=col, width=1)
            draw.line([(sx, sy-r), (sx, sy+r)], fill=col, width=1)
            return

        sc = ctx.scale

        # ── Bucket scale: cuantizar a potencia de 2 más cercana ──────────────
        # Sin cuantización, el cache_key usa el scale exacto. Cada paso de
        # wheel cambia el scale ~40%, lo que antes limpiaba todo el caché.
        # Con bucket scale: scales 460-920 comparten el bucket 512 → el tile
        # se reutiliza durante toda una "octava" de zoom sin re-render.
        # Únicamente se re-renderiza al cruzar una potencia de 2.
        qsc = 2.0 ** round(math.log2(max(sc, 1e-6)))

        # ── Cache key incluye ángulo cuantizado ──────────────────────────────
        # La rotación de un tile grande (expand=True) es O(N²) en píxeles y
        # puede tardar 300ms+ en tiles 2048×2048. Al incluir el ángulo en la
        # clave y guardar el tile YA ROTADO, el costo se paga una sola vez.
        # Cuantizar a 1° es suficiente — diferencias sub-grado son invisibles.
        _ang_key = round(ins.angle) % 360 if abs(ins.angle) > 0.1 else 0
        cache_key = (ins.block_name,
                     round(qsc * ins.scale_x, 4),
                     round(qsc * ins.scale_y, 4),
                     _ang_key)

        if cache_key not in self._block_cache:
            # Renderizar el bloque sin rotación
            raw = self._render_blockdef(
                bdef, ins.scale_x, ins.scale_y, qsc, col,
                viewport_w=ctx.W, viewport_h=ctx.H)

            # Aplicar rotación y guardar tile ya rotado en caché
            if raw is not None and abs(ins.angle) > 0.1:
                try:
                    raw = raw.rotate(ins.angle, expand=True,
                                     resample=_PILImage.Resampling.BILINEAR)
                except Exception:
                    pass

            self._block_cache[cache_key] = raw
            # Limitar tamaño de caché (200 tiles × ~0.5 MB ≈ 100 MB máx)
            if len(self._block_cache) > 200:
                for k in list(self._block_cache)[:50]:
                    del self._block_cache[k]

        tile = self._block_cache[cache_key]
        if tile is None:
            return

        # Redimensionar de bucket scale a scale real (BILINEAR, muy rápido)
        if qsc != sc:
            ratio = sc / qsc
            new_w = max(1, round(tile.width  * ratio))
            new_h = max(1, round(tile.height * ratio))
            if abs(new_w - tile.width) > 1 or abs(new_h - tile.height) > 1:
                try:
                    tile = tile.resize((new_w, new_h),
                                       _PILImage.Resampling.BILINEAR)
                except Exception:
                    pass

        # Calcular posición en pantalla del punto de inserción
        sx, sy = _vp.w2s(sc, ctx.offset_x, ctx.offset_y, ins.x, ins.y)

        # Posición correcta del tile:
        # El tile fue renderizado desde bx0..bx1, by0..by1 (espacio de bloque).
        # El INSERT coloca base_point del bloque en (ins.x, ins.y).
        # → La esquina izq del tile (= bx0 en bloque) está en pantalla:
        #     px = sx + (bx0 - base_point_x) * scale_x * sc  - 1 (margen del tile)
        # → La esquina sup del tile (= by1 en bloque) está en pantalla:
        #     py = sy - (by1 - base_point_y) * scale_y * sc  - 1
        bx0, by0, bx1, by1 = bdef.bbox()
        bpx = bdef.base_point[0]
        bpy = bdef.base_point[1]
        px = int(sx + (bx0 - bpx) * ins.scale_x * sc) - 1
        py = int(sy - (by1 - bpy) * ins.scale_y * sc) - 1

        try:
            img.paste(tile, (px, py), tile)
        except Exception:
            pass

    def _render_blockdef(self, bdef, sx: float, sy: float,
                         sc: float, default_col: str,
                         viewport_w: int = 1920, viewport_h: int = 1080):
        """Pre-renderiza un BlockDef a imagen RGBA (espacio de bloque).
        Retorna None si el bloque es invisible a esta escala."""
        # Calcular bbox del bloque en píxeles
        bx0, by0, bx1, by1 = bdef.bbox()
        asx, asy = abs(sx), abs(sy)   # escala puede ser negativa (bloque espejado)
        w_px = max(1, int((bx1 - bx0) * asx * sc))
        h_px = max(1, int((by1 - by0) * asy * sc))

        # LOD: si el bloque cabe en < 3×3 px no vale la pena renderizar
        if w_px < 3 or h_px < 3:
            return None
        # Cap de tamaño para bloques enormes.
        # Cap de tamaño: limitar el tile al mínimo necesario.
        # rotate(expand=True) es O(N²) → tile 2048px = 305ms, 256px = 5ms.
        # paste() con mask alfa también es O(N²) → tile 1132px = 40ms.
        # Un bloque típico de mueble ocupa 20-80px en pantalla; 256px
        # de tile es suficiente para cualquier zoom razonable.
        # Cap máximo = mínimo entre 512 y la mitad del lado menor del viewport.
        _vp_min   = min(viewport_w, viewport_h)
        _tile_cap = min(512, max(64, _vp_min // 2))
        w_px = min(w_px, _tile_cap)
        h_px = min(h_px, _tile_cap)

        tile = _PILImage.new("RGBA", (w_px + 2, h_px + 2), (0, 0, 0, 0))
        tdraw = _PILDraw.Draw(tile)

        # Offset: mapear bx0→px1, by1→py1 (Y invertido)
        off_x = -bx0 * asx * sc + 1
        off_y = h_px + by0 * asy * sc + 1   # Y invertido

        import math as _mb

        for ent in bdef.entities:
            # FIX #2: Resolver color por entidad.
            # BYLAYER/BYBLOCK/vacío → hereda del INSERT (default_col)
            # Color explícito (#RRGGBB) → usa el color propio de la entidad
            _ent_col = getattr(ent, "color", "bylayer") or "bylayer"
            if (_ent_col.lower() not in ("bylayer", "byblock", "")
                    and _ent_col.startswith("#")):
                ecol = _ent_col   # color explícito de la entidad
            else:
                ecol = default_col   # hereda del INSERT

            _ecol_rgb = _h2rgb(ecol) if isinstance(ecol, str) else ecol

            try:
                if isinstance(ent, Line):
                    x1s = int(off_x + ent.x1 * asx * sc)
                    y1s = int(off_y - ent.y1 * asy * sc)
                    x2s = int(off_x + ent.x2 * asx * sc)
                    y2s = int(off_y - ent.y2 * asy * sc)
                    tdraw.line([(x1s, y1s), (x2s, y2s)], fill=_ecol_rgb, width=1)

                elif isinstance(ent, Polyline) and len(ent.points) >= 2:
                    pts = [(int(off_x + px_ * asx * sc),
                            int(off_y - py_ * asy * sc))
                           for px_, py_ in ent.points]
                    if ent.closed and len(pts) > 2:
                        pts.append(pts[0])
                    tdraw.line(pts, fill=_ecol_rgb, width=1)

                # FIX #3a: Spline dentro de bloque
                elif isinstance(ent, Spline) and len(ent.points) >= 2:
                    spts = ent.interp(n_seg=16)
                    if len(spts) >= 2:
                        spts_px = [(int(off_x + px_ * asx * sc),
                                    int(off_y - py_ * asy * sc))
                                   for px_, py_ in spts]
                        if ent.closed and len(spts_px) > 2:
                            spts_px.append(spts_px[0])
                        tdraw.line(spts_px, fill=_ecol_rgb, width=1)

                elif isinstance(ent, Circle):
                    cx_ = int(off_x + ent.cx * asx * sc)
                    cy_ = int(off_y - ent.cy * asy * sc)
                    r_  = max(1, int(ent.radius * asx * sc))
                    tdraw.ellipse([cx_-r_, cy_-r_, cx_+r_, cy_+r_],
                                  outline=_ecol_rgb, fill=None)

                elif isinstance(ent, Arc):
                    cx_ = int(off_x + ent.cx * asx * sc)
                    cy_ = int(off_y - ent.cy * asy * sc)
                    r_  = max(1, int(ent.radius * asx * sc))
                    psa = (-ent.end_ang) % 360
                    pea = (-ent.start_ang) % 360
                    tdraw.arc([cx_-r_, cy_-r_, cx_+r_, cy_+r_],
                              start=psa, end=pea, fill=_ecol_rgb, width=1)

                # FIX #3b: Ellipse dentro de bloque
                elif isinstance(ent, Ellipse):
                    epts = ent._pts_on_boundary(36)
                    if len(epts) >= 2:
                        epts_px = [(int(off_x + px_ * asx * sc),
                                    int(off_y - py_ * asy * sc))
                                   for px_, py_ in epts]
                        epts_px.append(epts_px[0])   # cerrar
                        tdraw.line(epts_px, fill=_ecol_rgb, width=1)

                elif isinstance(ent, Text):
                    tx_ = int(off_x + ent.x * asx * sc)
                    ty_ = int(off_y - ent.y * asy * sc)
                    px_ = max(8, int(ent.height * asx * sc))
                    if px_ >= _LOD_TEXT_PX:
                        try:
                            font_ = self._get_font(px_)
                            tdraw.text((tx_, ty_ - px_), ent.content,
                                       fill=_ecol_rgb, font=font_, anchor="la")
                        except Exception:
                            pass

                # FIX #7: Hatch dentro de bloque — solid + patrón con líneas diagonales
                elif isinstance(ent, Hatch) and len(ent.boundary) >= 3:
                    hpts = [(int(off_x + px_ * asx * sc),
                             int(off_y - py_ * asy * sc))
                            for px_, py_ in ent.boundary]
                    if ent.pattern == "SOLID" or getattr(ent, "is_gradient", False):
                        try:
                            fill_col = _ecol_rgb
                            if getattr(ent, "is_gradient", False):
                                gc = getattr(ent, "gradient_color", "bylayer")
                                if gc not in ("bylayer","byblock") and gc.startswith("#"):
                                    fill_col = _h2rgb(gc)
                            tdraw.polygon(hpts, fill=fill_col, outline=None)
                        except Exception:
                            pass
                    else:
                        # Hatch con patrón: contorno + líneas diagonales clipeadas
                        # El tile ya está cacheado → se calcula solo una vez
                        try:
                            tdraw.polygon(hpts, outline=_ecol_rgb, fill=None)
                            # Líneas diagonales al ángulo del hatch (aprox ANSI31)
                            ang_r = _mb.radians(ent.angle if ent.angle else 45.0)
                            sp    = max(3, int(ent.scale * asx * sc * 1.5))
                            xs_h  = [p[0] for p in hpts]
                            ys_h  = [p[1] for p in hpts]
                            bx0h, bx1h = min(xs_h), max(xs_h)
                            by0h, by1h = min(ys_h), max(ys_h)
                            diag  = int(_mb.hypot(bx1h-bx0h, by1h-by0h)) + sp
                            # ── Safety cap: limitar líneas de hatch ───────────
                            # diag/sp = mitad de líneas; si son demasiadas
                            # (scale minúsculo → tile enorme), ampliar sp.
                            # Sin este cap: scale=0.01 en bloque grande
                            # puede generar 3000+ líneas → 500ms de render.
                            _max_hatch_lines = 150
                            _n_lines_est = max(1, int(2 * diag / max(2, sp)))
                            if _n_lines_est > _max_hatch_lines:
                                sp = max(sp, int(2 * diag / _max_hatch_lines))
                            cx_h  = (bx0h + bx1h) // 2
                            cy_h  = (by0h + by1h) // 2
                            cos_a = _mb.cos(ang_r); sin_a = _mb.sin(ang_r)
                            # Crear mask de la región y superponerla
                            _hmask = _PILImage.new("L", tile.size, 0)
                            _PILDraw.Draw(_hmask).polygon(hpts, fill=200)
                            _htmp  = _PILImage.new("RGBA", tile.size, (0,0,0,0))
                            _htd   = _PILDraw.Draw(_htmp)
                            for off in range(-diag, diag + diag, max(2, sp)):
                                lx0 = int(cx_h + off*(-sin_a) - cos_a*diag)
                                ly0 = int(cy_h + off*  cos_a  + sin_a*diag)
                                lx1 = int(cx_h + off*(-sin_a) + cos_a*diag)
                                ly1 = int(cy_h + off*  cos_a  - sin_a*diag)
                                _htd.line([(lx0,ly0),(lx1,ly1)],
                                          fill=_ecol_rgb + (180,), width=1)
                            _htmp.putalpha(_hmask)
                            tile.paste(_htmp, mask=_htmp.split()[3])
                        except Exception:
                            try:
                                tdraw.polygon(hpts, outline=_ecol_rgb, fill=None)
                            except Exception:
                                pass

            except Exception:
                pass
        return tile

    # ── Texto rotado ─────────────────────────────────────────────────

    @staticmethod
    def _draw_rotated_text(img, txt: str, sx: float, sy: float,
                            px: int, angle: float, col, font,
                            halign: int = 0, valign: int = 0):
        """Renderiza texto rotado pegando una imagen RGBA temporal."""
        try:
            bbox_t = (font.getbbox(txt) if hasattr(font, "getbbox")
                      else (0, 0, px * len(txt) // 2, px))
            tw = max(4, bbox_t[2] - bbox_t[0] + 4)
            th = max(4, bbox_t[3] - bbox_t[1] + 4)
            tmp = _PILImage.new("RGBA", (tw, th), (0, 0, 0, 0))
            _PILDraw.Draw(tmp).text((0, 0), txt, fill=col, font=font)
            tmp = tmp.rotate(-angle, expand=True,
                             resample=_PILImage.Resampling.BICUBIC)
            rw, rh = tmp.size
            paste_x = int(sx) - (rw // 2 if halign == 1 else rw if halign == 2 else 0)
            # valign: 3=top → ancla superior, 2=mid → centrado, 0=baseline → bottom
            paste_y = (int(sy)           if valign == 3 else
                       int(sy - rh // 2) if valign == 2 else
                       int(sy - rh))
            img.paste(tmp, (paste_x, paste_y), tmp)
        except Exception:
            _PILDraw.Draw(img).text((int(sx), int(sy - px)), txt,
                                    fill=col, font=font)

    # ── Línea con guiones ────────────────────────────────────────────

    @staticmethod
    def _dashed_line(draw, pts: list, col, lw: int, dash: tuple):
        """Dibuja una polilínea con patrón de guiones en PIL."""
        if not dash or len(pts) < 2:
            draw.line(pts, fill=col, width=lw)
            return
        on     = True
        d_idx  = 0
        seg_rem = dash[0]
        prev   = pts[0]
        for cur in pts[1:]:
            dx = cur[0] - prev[0]
            dy = cur[1] - prev[1]
            seg_len = math.hypot(dx, dy)
            if seg_len < 0.5:
                prev = cur
                continue
            ux, uy = dx / seg_len, dy / seg_len
            pos = 0.0
            while pos < seg_len:
                step = min(seg_rem, seg_len - pos)
                x0 = prev[0] + ux * pos
                y0 = prev[1] + uy * pos
                x1 = prev[0] + ux * (pos + step)
                y1 = prev[1] + uy * (pos + step)
                if on:
                    draw.line([(x0, y0), (x1, y1)], fill=col, width=lw)
                pos     += step
                seg_rem -= step
                if seg_rem <= 0.01:
                    on     = not on
                    d_idx  = (d_idx + 1) % len(dash)
                    seg_rem = dash[d_idx]
            prev = cur

    # ── Fuente cacheada ──────────────────────────────────────────────

    def _get_font(self, px: int):
        """Devuelve un PIL ImageFont cacheado al tamaño solicitado.

        Prioridad de fuente:
        1. Fuente configurada en textstyles (settings.json) — mismo que DXF exportado
        2. cour.ttf / arial.ttf — fallbacks clásicos
        3. PIL default font — último recurso
        """
        configured = getattr(self, "_configured_font", None)
        cache_key  = (px, configured or "")
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        font = None
        # Candidatos: fuente configurada primero, luego fallbacks
        candidates = []
        if configured:
            candidates.append(configured)
        candidates.extend(["arial.ttf", "arialbd.ttf", "calibri.ttf",
                            "segoeui.ttf", "cour.ttf", "DejaVuSans.ttf"])
        for name in candidates:
            try:
                font = _PILFont.truetype(name, max(6, px))
                break
            except Exception:
                pass
        if font is None:
            font = _PILFont.load_default()

        self._font_cache[cache_key] = font
        return font

    def _get_dim_font(self, px: int, style_name: str = ""):
        """Fuente para cotas: usa text_style del dimstyle si está definido,
        si no cae al textstyle global y luego a los fallbacks."""
        cache_key = (px, "dim:" + (style_name or ""))
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]
        font = None
        candidates = []
        if style_name:
            candidates.append(style_name)
        global_font = getattr(self, "_configured_font", None)
        if global_font:
            candidates.append(global_font)
        candidates.extend(["arial.ttf", "arialbd.ttf", "calibri.ttf",
                            "segoeui.ttf", "cour.ttf", "DejaVuSans.ttf"])
        for name in candidates:
            try:
                font = _PILFont.truetype(name, max(6, px))
                break
            except Exception:
                pass
        if font is None:
            font = _PILFont.load_default()
        self._font_cache[cache_key] = font
        return font

    def _draw_text_rotated(self, img, cx: int, cy: int,
                           txt: str, fnt, fill_rgb: tuple, angle_deg: float):
        """Dibuja texto rotado por angle_deg grados, centrado en (cx, cy).
        Usado para text_align=aligned en cotas alineadas."""
        try:
            from PIL import Image as _PIImg, ImageDraw as _PIDr
            try:
                bb = fnt.getbbox(txt)
                tw = bb[2] - bb[0] + 8
                th = bb[3] - bb[1] + 8
            except Exception:
                tw = len(txt) * 8 + 8
                th = 20
            tmp = _PIImg.new('RGBA', (tw, th), (0, 0, 0, 0))
            td  = _PIDr.Draw(tmp)
            td.text((tw // 2, th // 2), txt,
                    fill=fill_rgb + (255,), font=fnt, anchor="mm")
            tmp = tmp.rotate(-angle_deg, expand=True,
                             resample=_PIImg.BICUBIC)
            px_ = cx - tmp.width  // 2
            py_ = cy - tmp.height // 2
            img.paste(tmp, (px_, py_), mask=tmp.split()[3])
        except Exception:
            # Fallback: texto horizontal si falla la rotación
            from PIL import ImageDraw as _PIDr2
            _PIDr2.Draw(img).text((cx, cy), txt,
                                   fill=fill_rgb, font=fnt, anchor="mm")
