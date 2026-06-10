# -*- coding: utf-8 -*-
"""
benchmark_opengl.py
===================
Mide el performance del pipeline OpenGL con entidades sintéticas.
NO requiere ventana ni contexto GL — mide solo la CPU (tessellation + culling),
que es el cuello real a 600k entidades.

Ejecutar:
    python -X utf8 benchmark_opengl.py

Métricas:
  - Tessellation time (background thread work) en 3 escenarios:
      A) Full-scan   : todas las entidades visibles (zoom-out total)
      B) Viewport ×3 : 5k visible → tessella 45k (zoom-in en 600k)
      C) Cache hit   : 0ms (pan / zoom estable) — teórico
  - Tiempo del index query (viewport culling)
  - Proyección de FPS con overlay ~10ms
"""
from __future__ import annotations

import sys
import os
import time
import math
import random
import statistics
import threading

sys.path.insert(0, os.path.dirname(__file__))

# ── Configuración ─────────────────────────────────────────────────────────────
CANVAS_W   = 1920
CANVAS_H   = 1080
RUNS       = 3       # repeticiones por escenario (tessellation es lenta, pocas pasan)
RNG_SEED   = 42

# Proporciones realistas de un plano arquitectónico grande
ENTITY_MIX = {
    "Line"      : 0.62,
    "Polyline"  : 0.12,
    "Circle"    : 0.06,
    "Arc"       : 0.08,
    "Hatch"     : 0.05,
    "Text"      : 0.04,
    "Dimension" : 0.02,
    "Insert"    : 0.01,
}

# ── Generación de entidades sintéticas ────────────────────────────────────────

def build_entities(n_total: int, seed: int = RNG_SEED):
    """Genera n_total entidades con la misma proporción que un plano real."""
    from cad.entities import (Line, Polyline, Circle, Arc, Text,
                               Dimension, Hatch, Layer)
    rng = random.Random(seed)

    # Distribución en 50 capas (igual que un edificio completo)
    N_LAYERS = 50
    layer_names = [f"A-CAPA-{i:02d}" for i in range(N_LAYERS)]
    layers = {n: Layer(n, f"#{rng.randint(0,0xFFFFFF):06X}", 1) for n in layer_names}
    layers["0"] = Layer("0", "#FFFFFF", 1)

    ents = []
    # Distribuir en un área 200m × 200m (edificio grande)
    WORLD = 200.0

    counts = {t: max(1, int(n_total * f)) for t, f in ENTITY_MIX.items()}
    # Ajustar al total exacto con Lines
    total_so_far = sum(counts.values())
    counts["Line"] += n_total - total_so_far

    layer_cycle = layer_names * (n_total // N_LAYERS + 1)
    li = 0

    def _lay():
        nonlocal li
        l = layer_cycle[li % len(layer_cycle)]
        li += 1
        return l

    # Lines
    for _ in range(counts["Line"]):
        x1 = rng.uniform(0, WORLD); y1 = rng.uniform(0, WORLD)
        dx = rng.gauss(0, 3); dy = rng.gauss(0, 3)
        ents.append(Line(x1=x1, y1=y1, x2=x1+dx, y2=y1+dy, layer=_lay()))

    # Polylines (muros, contornos)
    for _ in range(counts["Polyline"]):
        cx = rng.uniform(0, WORLD); cy = rng.uniform(0, WORLD)
        n = rng.randint(4, 16)
        pts = [(cx + rng.gauss(0, 2), cy + rng.gauss(0, 2)) for _ in range(n)]
        ents.append(Polyline(points=pts, closed=rng.random() > 0.5, layer=_lay()))

    # Circles
    for _ in range(counts["Circle"]):
        ents.append(Circle(
            cx=rng.uniform(0, WORLD), cy=rng.uniform(0, WORLD),
            radius=rng.uniform(0.1, 2.0), layer=_lay()))

    # Arcs
    for _ in range(counts["Arc"]):
        sa = rng.uniform(0, 360); ea = rng.uniform(0, 360)
        ents.append(Arc(
            cx=rng.uniform(0, WORLD), cy=rng.uniform(0, WORLD),
            radius=rng.uniform(0.1, 2.0),
            start_ang=sa, end_ang=ea, ccw=True, layer=_lay()))

    # Hatches (rellenos sólidos convexos — los más comunes)
    for _ in range(counts["Hatch"]):
        cx = rng.uniform(0, WORLD); cy = rng.uniform(0, WORLD)
        w = rng.uniform(1, 6); h = rng.uniform(1, 6)
        boundary = [(cx, cy), (cx+w, cy), (cx+w, cy+h), (cx, cy+h)]
        ents.append(Hatch(boundary=boundary, pattern="SOLID", layer=_lay()))

    # Text
    for i in range(counts["Text"]):
        ents.append(Text(
            x=rng.uniform(0, WORLD), y=rng.uniform(0, WORLD),
            content=f"TEXTO {i}", height=rng.choice([0.15, 0.20, 0.25, 0.30]),
            layer=_lay()))

    # Dimensions (simplificadas)
    for _ in range(counts["Dimension"]):
        x1 = rng.uniform(0, WORLD); y1 = rng.uniform(0, WORLD)
        x2 = x1 + rng.uniform(1, 8)
        ents.append(Dimension(
            p1=(x1, y1), p2=(x2, y1), pos=(x1, y1-1),
            dim_type="H", layer=_lay()))

    rng.shuffle(ents)
    return ents, layers

# ── Índice espacial ────────────────────────────────────────────────────────────

def build_index(entities, cell_size=5.0):
    """Construye índice espacial (mismo algoritmo que el engine)."""
    from cad.renderer_pil import _entity_aabb
    idx = {}
    aabbs = []
    flat  = []
    for e in entities:
        try:
            x0, y0, x1, y1 = _entity_aabb(e, {})
            if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
                continue
            aabbs.append((x0, y0, x1, y1))
            flat.append(e)
        except Exception:
            continue

    for e, (x0, y0, x1, y1) in zip(flat, aabbs):
        cx0 = int(x0 // cell_size); cy0 = int(y0 // cell_size)
        cx1 = int(x1 // cell_size); cy1 = int(y1 // cell_size)
        cells = (cx1-cx0+1) * (cy1-cy0+1)
        if cells > 500:
            continue
        for cx in range(cx0, cx1+1):
            for cy in range(cy0, cy1+1):
                idx.setdefault((cx, cy), []).append(e)

    idx["__aabb__"]       = aabbs
    idx["__flat__"]       = flat
    idx["__total_cells__"] = sum(1 for k in idx if isinstance(k, tuple))
    return idx

# ── Ctx simulado (sin tkinter) ────────────────────────────────────────────────

class _FakeCtx:
    def __init__(self, entities, layers, scale, ox, oy, idx, cell):
        import threading
        self.entities     = entities
        self.layers       = layers
        self.block_defs   = {}
        self.entity_index = idx
        self.entity_cell  = cell
        self.scale        = scale
        self.offset_x     = ox
        self.offset_y     = oy
        self.W            = CANVAS_W
        self.H            = CANVAS_H
        self.config       = {}
        self.select_color = "#FFD700"
        self.sel_version  = 0
        self.cancel_ev    = threading.Event()
        self.bg_color     = "#0A0A0A"
        self.dim_text_queue = []

# ── Benchmark de tessellation ──────────────────────────────────────────────────

def run_tess_benchmark(label, renderer, ents_snap, lyr_cache, ctx, sc, tess_bounds=None):
    """Mide _tessellate_and_enqueue directamente (sin GL)."""
    import queue

    times = []
    for run_i in range(RUNS):
        result_q = queue.Queue()
        t0 = time.perf_counter()

        def _worker():
            renderer._tessellate_and_enqueue(
                ents_snap, {}, f"bench_{run_i}", sc,
                lyr_cache, 1.0, "#FFD700", ctx.cancel_ev, ctx, tess_bounds)
            result_q.put(time.perf_counter())

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=120)
        elapsed = (result_q.get_nowait() - t0) * 1000 if not result_q.empty() else 120000
        times.append(elapsed)
        renderer._tess_result_q.get_nowait() if not renderer._tess_result_q.empty() else None

    avg = statistics.mean(times)
    mn  = min(times)
    mx  = max(times)
    fps_overlay10 = 1000.0 / max(1, avg + 10)   # overlay ~10ms constante

    print(f"  {label}")
    print(f"    Entidades tesselladas : {len(ents_snap):>9,}")
    print(f"    Avg  : {avg:7.0f} ms  |  Min: {mn:.0f}ms  Max: {mx:.0f}ms")
    print(f"    FPS (estimado, overlay=10ms): {fps_overlay10:.1f}")
    return {"avg": avg, "min": mn, "max": mx, "n": len(ents_snap)}

# ── Benchmark de culling ───────────────────────────────────────────────────────

def run_culling_benchmark(label, idx, cell, vx0, vy0, vx1, vy1, n_total):
    from cad.renderer_pil import _query_viewport
    QRUNS = 100
    t0 = time.perf_counter()
    for _ in range(QRUNS):
        result = _query_viewport(idx, cell, vx0, vy0, vx1, vy1)
    elapsed_ms = (time.perf_counter() - t0) * 1000 / QRUNS
    n_found = len(result) if result else 0
    print(f"  {label}")
    print(f"    Viewport : ({vx0:.0f},{vy0:.0f}) → ({vx1:.0f},{vy1:.0f})")
    print(f"    Resultado: {n_found:,} de {n_total:,} entidades  ({100*n_found/max(1,n_total):.1f}%)")
    print(f"    Query time: {elapsed_ms:.2f} ms  (media de {QRUNS} queries)")
    return n_found

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    SEP = "=" * 68

    # ── Escenas a probar
    SCENARIOS = [
        ("18k  (DXF actual)",  18_000),
        ("100k (edificio med)", 100_000),
        ("300k (edificio grd)", 300_000),
        ("600k (meta final)",   600_000),
    ]

    print(f"\n{SEP}")
    print("  BENCHMARK OpenGL Tessellator — Estudio Merlos AI")
    print(f"  {time.strftime('%Y-%m-%d %H:%M')}    Python {sys.version.split()[0]}")
    print(SEP)

    from cad.renderer_pil import build_layer_cache, _query_viewport
    from cad.renderer_opengl import RendererOpenGL

    # Renderer mock — solo _tessellate_and_enqueue (sin GL calls)
    # Necesita los atributos que el método lee/escribe en self.
    renderer = object.__new__(RendererOpenGL)
    renderer._tess_result_q  = __import__('queue').Queue(maxsize=2)
    renderer._tess_progress  = ""
    renderer._font_atlas     = None   # el tessellator solo lo lee (None = sin atlas de texto)

    results = {}

    for scene_label, N in SCENARIOS:
        print(f"\n{'─'*68}")
        print(f"  Escena: {scene_label}  ({N:,} entidades)")
        print(f"{'─'*68}")

        print("  Generando entidades…")
        t0 = time.perf_counter()
        entities, layers, = build_entities(N)[:2]
        gen_ms = (time.perf_counter() - t0) * 1000
        print(f"  Generadas {len(entities):,} ents en {gen_ms:.0f}ms")

        print("  Construyendo índice espacial…")
        t0 = time.perf_counter()
        cell = 5.0
        idx = build_index(entities, cell)
        idx_ms = (time.perf_counter() - t0) * 1000
        n_cells = len([k for k in idx if isinstance(k, tuple)])
        print(f"  Índice: {n_cells:,} celdas en {idx_ms:.0f}ms")

        lyr_cache = build_layer_cache(layers, scale=1.0)

        # Viewport: 10% del mundo visible (escala work)
        WORLD = 200.0
        sc_work  = CANVAS_W / (WORLD * 0.10)   # ~10% del mundo visible
        sc_full  = CANVAS_W / WORLD             # todo el mundo visible

        ox_ctr   = CANVAS_W / 2
        oy_ctr   = CANVAS_H / 2

        ctx_full = _FakeCtx(entities, layers, sc_full, ox_ctr, oy_ctr, idx, cell)
        ctx_work = _FakeCtx(entities, layers, sc_work, ox_ctr, oy_ctr, idx, cell)

        # Viewport extendido ×3 (lo que usa el tessellator en modo tile)
        vw_work = CANVAS_W / sc_work;  vh_work = CANVAS_H / sc_work
        cx_w = (CANVAS_W/2 - ox_ctr) / sc_work
        cy_w = (oy_ctr - CANVAS_H/2) / sc_work
        vx0e = cx_w - vw_work * 1.5;  vx1e = cx_w + vw_work * 1.5
        vy0e = cy_w - vh_work * 1.5;  vy1e = cy_w + vh_work * 1.5

        print(f"\n  ── CULLING (viewport query) ──")
        n_vp_full = run_culling_benchmark(
            "Full zoom-out (sc_full, todas visibles)",
            idx, cell, 0, 0, WORLD, WORLD, N)
        n_vp_work = run_culling_benchmark(
            "Zoom trabajo  (sc_work, 10% del mundo)",
            idx, cell, vx0e, vy0e, vx1e, vy1e, N)

        print(f"\n  ── TESSELLATION ──")

        # A) Full-scan (zoom-out total — worst case)
        r_full = run_tess_benchmark(
            "A) Full-scan — zoom-out total (worst case)",
            renderer, list(entities), lyr_cache, ctx_full, sc_full)

        # B) Viewport ×3 (tile mode — zoom trabajo)
        vp3_cands = _query_viewport(idx, cell, vx0e, vy0e, vx1e, vy1e) or list(entities)
        tess_bounds = (vx0e, vy0e, vx1e, vy1e)
        r_tile = run_tess_benchmark(
            "B) Viewport ×3 — tile mode (zoom trabajo)",
            renderer, vp3_cands, lyr_cache, ctx_work, sc_work, tess_bounds)

        # C) Cache hit (teórico — 0ms de tessellation)
        print(f"  C) Cache hit — pan estable")
        print(f"    Tessellation    : 0 ms  (VRAM válida, solo actualiza uProjection)")
        print(f"    FPS (estimado)  : ~{1000/(19+10):.0f}  (GL=19ms + overlay=10ms)")

        results[scene_label] = {"full": r_full, "tile": r_tile, "n": N}

    # ── Resumen final ─────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  RESUMEN — Tessellation time (ms) por escenario")
    print(SEP)
    print(f"  {'Escena':25s}  {'N total':>10s}  {'Full-scan':>12s}  {'Tile ×3':>12s}  {'Cache hit':>12s}")
    print(f"  {'─'*65}")
    for lbl, r in results.items():
        full_ms = r['full']['avg']
        tile_ms = r['tile']['avg']
        print(f"  {lbl:25s}  {r['n']:>10,}  {full_ms:>10.0f}ms  {tile_ms:>10.0f}ms  {'0ms':>12s}")

    print(f"\n  Leyenda:")
    print(f"    Full-scan : primer render o zoom total (work en background — UI no congela)")
    print(f"    Tile ×3   : LOD change con viewport culling (work en background)")
    print(f"    Cache hit : pan/zoom estable (0ms tessellation, VRAM válida)")
    print(f"    FPS final = 1000 / (GL_ms + overlay_ms)  [tessellation no bloquea]")

    # Guardar reporte
    rpt_path = os.path.join(os.path.dirname(__file__), "benchmark_opengl_result.txt")
    with open(rpt_path, "w", encoding="utf-8") as f:
        f.write(f"BENCHMARK OpenGL Tessellator\n")
        f.write(f"Fecha : {time.strftime('%Y-%m-%d %H:%M')}\n\n")
        for lbl, r in results.items():
            f.write(f"{lbl}\n")
            f.write(f"  Full-scan : {r['full']['avg']:.0f}ms\n")
            f.write(f"  Tile ×3   : {r['tile']['avg']:.0f}ms\n\n")
    print(f"\n  Reporte guardado: {rpt_path}\n")
    print(SEP)


if __name__ == "__main__":
    main()
