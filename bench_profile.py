"""
bench_profile.py — Desglose de tiempo por componente del renderer OpenGL.
Mide cache miss vs cache hit para tessellate y overlay PIL.
Uso: python bench_profile.py "ruta/al/archivo.dxf"
"""
import sys, time, math
from pathlib import Path

DXF = sys.argv[1] if len(sys.argv) > 1 else None
W, H = 1920, 1080
N_FRAMES = 20

from cad.renderer_pil import RendererPIL, RenderCtx
from cad.renderer_opengl import RendererOpenGL
from cad.viewport import zoom_to_fit
from cad.dxf_import import importar_dxf

print(f"\n{'='*60}")
print(f"  BENCHMARK DESGLOSE — OpenGL renderer")
print(f"{'='*60}")

result     = importar_dxf(DXF)
entities   = result.entities
layers     = result.layers
block_defs = result.block_defs
print(f"\n  DXF : {Path(DXF).name}")
print(f"  Ents: {len(entities)}   Layers: {len(layers)}")

from cad.entities import (Line, Circle, Arc, Polyline, Spline, Ellipse,
                           XLine, Text, Dimension, Hatch, Insert, Leader)
counts = {}
for e in entities:
    t = type(e).__name__
    counts[t] = counts.get(t, 0) + 1
for t, n in sorted(counts.items(), key=lambda x: -x[1]):
    print(f"    {t:<16} {n}")

# Warm-up
ogl = RendererOpenGL()
pil = RendererPIL()
from cad.entities import Layer as _Layer
_dl = {'0': _Layer(name='0', color='#FFFFFF', linewidth=1, visible=True)}
ogl.render(RenderCtx(W=100, H=100, scale=10, offset_x=50, offset_y=50, layers=_dl))
print("\n  GL context: OK\n")

import PIL.Image as _PI
import cad.viewport as _vp
from cad.renderer_pil import _query_viewport

zoom_levels = [
    ("zoom-fit  ", 0.05),
    ("zoom-work ", 0.45),
]

for zoom_label, pad in zoom_levels:
    sc, ox, oy = zoom_to_fit(entities, W, H, padding=pad)

    def mk_ctx():
        return RenderCtx(
            entities=entities, layers=layers, block_defs=block_defs,
            W=W, H=H, scale=sc, offset_x=ox, offset_y=oy,
        )

    ctx = mk_ctx()

    # PIL baseline
    t0 = time.perf_counter()
    for _ in range(N_FRAMES):
        pil.render(mk_ctx())
    pil_ms = (time.perf_counter() - t0) / N_FRAMES * 1000

    # OpenGL total (avg 20 frames — mezcla de miss+hit)
    t0 = time.perf_counter()
    for _ in range(N_FRAMES):
        ogl.render(ctx)
    ogl_avg_ms = (time.perf_counter() - t0) / N_FRAMES * 1000

    # Tessellate cache MISS — forzar recalculo
    ogl._vbo_cache_key = None
    t0 = time.perf_counter()
    ogl.render(ctx)
    tess_miss_ms = (time.perf_counter() - t0) * 1000

    # Tessellate cache HIT — misma key
    t0 = time.perf_counter()
    for _ in range(N_FRAMES):
        ogl.render(ctx)
    tess_hit_ms = (time.perf_counter() - t0) / N_FRAMES * 1000

    # Overlay cache MISS
    ogl._overlay_key = None
    img_blank = _PI.new("RGB", (W, H), (10,10,10))
    t0 = time.perf_counter()
    ogl._render_text_dim_overlay(img_blank.copy(), ctx)
    overlay_miss_ms = (time.perf_counter() - t0) * 1000

    # Overlay cache HIT
    t0 = time.perf_counter()
    for _ in range(N_FRAMES):
        ogl._render_text_dim_overlay(img_blank.copy(), ctx)
    overlay_hit_ms = (time.perf_counter() - t0) / N_FRAMES * 1000

    # Stats
    vx0, vy0, vx1, vy1 = _vp.viewport_world(sc, ox, oy, W, H)
    cands = _query_viewport(ctx.entity_index, ctx.entity_cell, vx0, vy0, vx1, vy1)
    if not cands: cands = entities
    n_cands = len(cands)
    n_overlay = sum(1 for e in cands
                    if isinstance(e, (Text, Dimension, Hatch, Insert, Leader)))

    print(f"  {zoom_label}  sc={sc:.4f}  cands={n_cands}/{len(entities)}")
    print(f"    PIL total            : {pil_ms:7.1f} ms")
    print(f"    OpenGL avg (20 fr)   : {ogl_avg_ms:7.1f} ms  ({pil_ms/max(ogl_avg_ms,0.1):.1f}x)")
    print(f"    ├ tessellate MISS    : {tess_miss_ms:7.1f} ms  (1er zoom / edicion)")
    print(f"    ├ tessellate HIT     : {tess_hit_ms:7.1f} ms  (pan continuo)")
    print(f"    ├ overlay MISS       : {overlay_miss_ms:7.1f} ms  ({n_overlay} ents)")
    print(f"    └ overlay HIT (cache): {overlay_hit_ms:7.1f} ms  (viewport igual)")
    print()

ogl.cleanup()
print("Done.")
