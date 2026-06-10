# -*- coding: utf-8 -*-
"""
benchmark_renderer.py
=====================
Mide el performance del RendererPIL antes y despues de optimizaciones.
Ejecutar:
    python -X utf8 benchmark_renderer.py

Genera:
  - Tiempos por frame (ms)
  - Promedio, mediana, min, max
  - Estimacion de FPS
  - Desglose por tipo de entidad
"""
from __future__ import annotations

import sys
import os
import time
import statistics

sys.path.insert(0, os.path.dirname(__file__))

from cad.renderer_pil import RendererPIL, RenderCtx, _entity_aabb
from cad.entities import Line, Polyline, Circle, Arc, Text, Dimension, Layer
from cad.viewport import zoom_to_fit

# ── Config ───────────────────────────────────────────────────────────────────

RUNS   = 10
WARMUP = 2
W      = 1600
H      = 900

DXF_PATHS = [
    r"C:\Users\jmerl\OneDrive\Desktop\PROYECTOS AUTOCAD CONVERTIR JSON\Planos Casa Merlos.dxf",
    r"C:\Users\jmerl\OneDrive\Desktop\PROYECTOS AUTOCAD CONVERTIR JSON\Planos Casa Merlos (cambio en cochera).dxf",
    r"C:\Users\jmerl\OneDrive\Desktop\PROYECTOS AUTOCAD CONVERTIR JSON\PLANO VIVIANA.dxf",
]

# ── DXF loader ────────────────────────────────────────────────────────────────

def load_dxf():
    from cad.dxf_import import importar_dxf
    for path in DXF_PATHS:
        if os.path.exists(path):
            try:
                result = importar_dxf(path, escala=1000.0)
                print(f"  DXF cargado: {len(result.entities):,} entidades")
                print(f"  Archivo: {os.path.basename(path)}")
                return result.entities, result.layers, getattr(result, 'block_defs', {})
            except Exception as e:
                print(f"  Error en {os.path.basename(path)}: {e}")
    return None, None, None

# ── Escena sintetica ──────────────────────────────────────────────────────────

def build_synthetic(n_lines=800, n_poly=200, n_circ=150, n_arcs=80, n_text=100):
    import random
    rng = random.Random(42)
    ents = []
    lyr = {"0": Layer("0", "#FFFFFF", 1)}

    for _ in range(n_lines):
        x1 = rng.uniform(-20, 20); y1 = rng.uniform(-20, 20)
        ents.append(Line(x1=x1, y1=y1, x2=x1+rng.uniform(-5,5), y2=y1+rng.uniform(-5,5)))

    for _ in range(n_poly):
        cx = rng.uniform(-15, 15); cy = rng.uniform(-15, 15)
        n = rng.randint(4, 12)
        pts = [(cx+rng.uniform(-3,3), cy+rng.uniform(-3,3)) for _ in range(n)]
        ents.append(Polyline(points=pts, closed=rng.random() > 0.7))

    for _ in range(n_circ):
        ents.append(Circle(cx=rng.uniform(-20,20), cy=rng.uniform(-20,20), radius=rng.uniform(0.2,3.0)))

    for _ in range(n_arcs):
        ents.append(Arc(cx=rng.uniform(-20,20), cy=rng.uniform(-20,20),
                        radius=rng.uniform(0.2,2.0),
                        start_ang=rng.uniform(0,360), end_ang=rng.uniform(0,360), ccw=True))

    for i in range(n_text):
        ents.append(Text(x=rng.uniform(-20,20), y=rng.uniform(-20,20),
                         content=f"TEXTO {i}", height=0.25))

    return ents, lyr, {}

# ── Indice espacial ───────────────────────────────────────────────────────────

def build_index(entities):
    """Construye indice espacial con cell_size automatico."""
    aabbs = []
    flat  = []
    for e in entities:
        try:
            x0, y0, x1, y1 = _entity_aabb(e, {})
            w, h = x1 - x0, y1 - y0
            if w < 0 or h < 0 or w > 1e8 or h > 1e8:
                continue
            aabbs.append((x0, y0, x1, y1))
            flat.append(e)
        except Exception:
            pass

    if not aabbs:
        return {}, 5.0

    # Cell size = mediana del ancho de entidades * 5
    widths = sorted(a[2]-a[0] for a in aabbs if a[2]-a[0] > 0)
    if widths:
        med_w  = widths[len(widths)//2]
        cell   = max(0.1, med_w * 5)
    else:
        cell = 5.0

    idx = {}
    for e, (x0, y0, x1, y1) in zip(flat, aabbs):
        cx0 = int(x0 // cell); cy0 = int(y0 // cell)
        cx1 = int(x1 // cell); cy1 = int(y1 // cell)
        if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) > 5000:
            continue  # entidad enorme: solo en __flat__
        for cx in range(cx0, cx1+1):
            for cy in range(cy0, cy1+1):
                idx.setdefault((cx, cy), []).append(e)

    idx["__aabb__"] = aabbs
    idx["__flat__"] = flat
    return idx, cell

# ── Benchmark ────────────────────────────────────────────────────────────────

def run_benchmark(label, entities, layers, block_defs, idx, cell, scale, ox, oy):
    renderer = RendererPIL()
    ctx = RenderCtx(
        W=W, H=H,
        scale=scale, offset_x=ox, offset_y=oy,
        entities=entities,
        layers=layers,
        block_defs=block_defs,
        entity_index=idx,
        entity_cell=cell,
        grid_on=True,
        config={},
    )

    # Warmup
    for _ in range(WARMUP):
        renderer.render(ctx)

    # Medicion
    times = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        renderer.render(ctx)
        times.append((time.perf_counter() - t0) * 1000)

    avg = statistics.mean(times)
    med = statistics.median(times)
    mn  = min(times)
    mx  = max(times)
    fps = 1000.0 / avg

    SEP = "=" * 62
    print(f"\n{SEP}")
    print(f"  {label}")
    print(SEP)
    print(f"  Entidades : {len(entities):,}")
    print(f"  Viewport  : {W}x{H}  scale={scale:.2f}")
    print(f"  Index     : {len([k for k in idx if isinstance(k,tuple)]):,} celdas  cell={cell:.2f}")
    print(f"  {'─'*56}")
    print(f"  Promedio  : {avg:7.1f} ms   ->  {fps:5.1f} FPS")
    print(f"  Mediana   : {med:7.1f} ms")
    print(f"  Min       : {mn:7.1f} ms")
    print(f"  Max       : {mx:7.1f} ms")
    print(f"  {'─'*56}")
    type_counts = {}
    for e in entities:
        t = type(e).__name__
        type_counts[t] = type_counts.get(t, 0) + 1
    print("  Por tipo:")
    for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"    {t:20s}: {cnt:5,}")
    print(SEP)
    return {"avg": avg, "med": med, "min": mn, "max": mx, "fps": fps,
            "n": len(entities)}

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*62)
    print("  BENCHMARK RendererPIL - Estudio Merlos AI")
    print("="*62)

    print("\n  Cargando escena...")
    entities, layers, block_defs = load_dxf()
    scene_label = "Casa Merlos (DXF real)"

    if entities is None:
        print("  Sin DXF - usando escena sintetica")
        entities, layers, block_defs = build_synthetic()
        scene_label = f"Sintetica ({len(entities):,} entidades)"

    # Calcular zoom automatico para ver el plano completo
    scale, ox, oy = zoom_to_fit(entities, W, H)
    print(f"  Auto-zoom: scale={scale:.3f}  offset=({ox:.0f}, {oy:.0f})")

    print("  Construyendo indice espacial...")
    idx, cell = build_index(entities)
    n_cells = len([k for k in idx if isinstance(k, tuple)])
    print(f"  Indice: {n_cells:,} celdas  cell={cell:.3f}")

    # ── Zoom-to-fit (todo visible, LOD activo) ───────────────────────────
    r_fit  = run_benchmark(f"{scene_label} [zoom-fit sc={scale:.3f}]",
                           entities, layers, block_defs, idx, cell, scale, ox, oy)

    # ── Zoom de trabajo (scale=40, simula ver un cuarto) ────────────────
    # Centrar en el medio del plano
    cx_w = (ox - W/2) / (-scale) if scale > 0 else 0
    cy_w = (oy - H/2) / scale    if scale > 0 else 0
    sc_work = 40.0
    ox_w    = W/2 - cx_w * sc_work
    oy_w    = H/2 + cy_w * sc_work
    r_work = run_benchmark(f"{scene_label} [zoom-work sc=40]",
                           entities, layers, block_defs, idx, cell, sc_work, ox_w, oy_w)

    # ── Zoom detalle (scale=200, simula ver cotas) ────────────────────
    sc_det  = 200.0
    ox_d    = W/2 - cx_w * sc_det
    oy_d    = H/2 + cy_w * sc_det
    r_det  = run_benchmark(f"{scene_label} [zoom-detail sc=200]",
                           entities, layers, block_defs, idx, cell, sc_det, ox_d, oy_d)

    # ── Resumen ─────────────────────────────────────────────────────────
    print("\n  RESUMEN:")
    print(f"  {'Escenario':35s}  {'Avg':>7s}  {'FPS':>7s}")
    print(f"  {'─'*54}")
    for lbl, res in [("zoom-fit  (LOD activo)",  r_fit),
                     ("zoom-work (grid visible)", r_work),
                     ("zoom-detail (cotas)",      r_det)]:
        print(f"  {lbl:35s}  {res['avg']:6.1f}ms  {res['fps']:6.1f}")

    # Guardar baseline del zoom de trabajo (el más representativo)
    baseline = os.path.join(os.path.dirname(__file__), "benchmark_baseline.txt")
    with open(baseline, "w") as f:
        f.write(f"BENCHMARK\n")
        f.write(f"Fecha     : {time.strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"Escena    : {scene_label}\n")
        f.write(f"Entidades : {r_work['n']}\n")
        f.write(f"\n[zoom-fit sc={scale:.3f}]\n")
        f.write(f"  Avg={r_fit['avg']:.1f}ms  FPS={r_fit['fps']:.1f}\n")
        f.write(f"\n[zoom-work sc=40]\n")
        f.write(f"  Avg={r_work['avg']:.1f}ms  FPS={r_work['fps']:.1f}\n")
        f.write(f"\n[zoom-detail sc=200]\n")
        f.write(f"  Avg={r_det['avg']:.1f}ms  FPS={r_det['fps']:.1f}\n")
    print(f"\n  Guardado: {baseline}\n")


if __name__ == "__main__":
    main()
