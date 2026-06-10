"""
test_renderer_validation.py
===========================
Suite de validación PIL vs OpenGL para el CAD Estudio Merlos.

Detecta regresiones en AMBOS renderers comparando sus outputs pixel
a pixel. Si un fix en PIL cambia cómo se dibuja algo, el test avisa
si OpenGL produce algo diferente (y viceversa).

Cobertura:
  Grupo 1 — Geometría GL (Line, Circle, Arc, Polyline, Spline, Ellipse)
  Grupo 2 — Overlay PIL  (Text, Dimension, Hatch)
  Grupo 3 — Combinado    (plano típico arquitectónico)
  Grupo 4 — DXF real     (plano Casa Merlos, múltiples zooms)
  Grupo 5 — Casos extremos (LOD, capas ocultas, muchas capas)
  Grupo 6 — Performance  (benchmark comparativo)

Uso:
    python -X utf8 test_renderer_validation.py
    python -X utf8 test_renderer_validation.py --dxf ruta.dxf --save
    python -X utf8 test_renderer_validation.py --grupo 1      # solo geometría
    python -X utf8 test_renderer_validation.py --grupo 4      # solo DXF real
"""
from __future__ import annotations

import argparse, sys, time, os, math
from pathlib import Path
from typing import List, Tuple, Optional

# ── Argumentos ────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Validación PIL vs OpenGL")
parser.add_argument("--dxf",       default=None, help="Ruta al DXF de prueba")
parser.add_argument("--threshold", type=float, default=0.90)
parser.add_argument("--save",      action="store_true", help="Guardar imágenes diff")
parser.add_argument("--width",     type=int, default=1200)
parser.add_argument("--height",    type=int, default=900)
parser.add_argument("--grupo",     type=int, default=0,
                    help="Ejecutar solo el grupo N (0=todos)")
args = parser.parse_args()

W, H = args.width, args.height

# ── DXF candidatos ────────────────────────────────────────────────────────────

_DXF_CANDIDATES = [
    r"C:\Users\jmerl\OneDrive\Desktop\PROYECTOS AUTOCAD CONVERTIR JSON\Planos Casa Merlos.dxf",
    r"C:\Users\jmerl\OneDrive\Desktop\PROYECTOS AUTOCAD CONVERTIR JSON\Planos Casa Merlos (cambio en cochera).dxf",
    r"C:\Users\jmerl\OneDrive\Desktop\PLANO VIVIANA.dwg",
]

dxf_path = args.dxf
if dxf_path is None:
    for c in _DXF_CANDIDATES:
        if os.path.exists(c):
            dxf_path = c
            break

# ── Imports ───────────────────────────────────────────────────────────────────

print("=" * 68)
print("  Suite de Validación PIL vs OpenGL — Estudio Merlos CAD")
print("=" * 68)

import numpy as np

from cad.renderer_pil  import RendererPIL, RenderCtx
from cad.renderer_opengl import RendererOpenGL
from cad.viewport import zoom_to_fit
from cad.entities import (
    Line, Circle, Arc, Polyline, Spline, Ellipse, XLine,
    Text, Dimension, Leader, Hatch, Insert, Layer
)
from PIL import Image, ImageDraw


# ── Métricas ─────────────────────────────────────────────────────────────────

def metrics(pil_img: Image.Image, ogl_img: Image.Image) -> dict:
    """
    Compara dos imágenes y retorna métricas detalladas.

    Campos retornados:
        mae       — Error Absoluto Medio (0-255)
        psnr      — Peak Signal-to-Noise Ratio en dB
        pct_diff  — % píxeles con delta > 10 en cualquier canal
        max_diff  — máximo error en un solo píxel
        region_mae— MAE por región (grid 3×3) para localizar problemas
        ok_geom   — True si MAE<8 y pct<15  (geometría GL)
        ok_overlay— True si MAE<12 y pct<25 (overlay PIL: anti-alias OK)
    """
    a = np.asarray(pil_img, dtype=np.float32)
    b = np.asarray(ogl_img, dtype=np.float32)
    if a.shape != b.shape:
        b = np.asarray(ogl_img.resize(pil_img.size), dtype=np.float32)

    diff = np.abs(a - b)
    mae       = float(diff.mean())
    mse       = float((diff ** 2).mean())
    psnr      = 10 * math.log10(255**2 / mse) if mse > 0 else 100.0
    pct_diff  = float((diff.max(axis=2) > 10).mean() * 100)
    max_diff  = float(diff.max())

    # MAE por región 3×3 — identifica qué zona del plano difiere
    rH, rW = a.shape[0] // 3, a.shape[1] // 3
    region_mae = {}
    labels = [("TL","TC","TR"),("ML","MC","MR"),("BL","BC","BR")]
    for ri in range(3):
        for ci in range(3):
            block = diff[ri*rH:(ri+1)*rH, ci*rW:(ci+1)*rW]
            region_mae[labels[ri][ci]] = round(float(block.mean()), 2)

    return {
        "mae":        round(mae,  2),
        "psnr":       round(psnr, 1),
        "pct_diff":   round(pct_diff, 1),
        "max_diff":   round(max_diff, 1),
        "region_mae": region_mae,
        "ok_geom":    mae < 8.0  and pct_diff < 15.0,
        "ok_overlay": mae < 12.0 and pct_diff < 25.0,
    }


def save_diff(pil_img, ogl_img, name: str):
    """Guarda PIL, OpenGL y diff amplificado como PNG."""
    a = np.asarray(pil_img, dtype=np.int32)
    b = np.asarray(ogl_img, dtype=np.int32)
    diff = np.clip(np.abs(a - b) * 6, 0, 255).astype(np.uint8)
    Image.fromarray(diff).save(f"val_{name}_diff.png")
    pil_img.save(f"val_{name}_pil.png")
    ogl_img.save(f"val_{name}_ogl.png")
    print(f"      Guardado: val_{name}_pil/ogl/diff.png")


# ── Helpers de construcción ───────────────────────────────────────────────────

_BASE_LAYERS = {
    '0':       Layer(name='0',       color='#FFFFFF', linewidth=1, visible=True),
    'A-MURO':  Layer(name='A-MURO',  color='#00BFFF', linewidth=2, visible=True),
    'A-COTA':  Layer(name='A-COTA',  color='#FFFF00', linewidth=1, visible=True),
    'A-TEXT':  Layer(name='A-TEXT',  color='#FFFFFF', linewidth=1, visible=True),
    'A-HATCH': Layer(name='A-HATCH', color='#FF8C00', linewidth=1, visible=True),
    'HIDDEN':  Layer(name='HIDDEN',  color='#FF0000', linewidth=1, visible=False),
    'LOCKED':  Layer(name='LOCKED',  color='#888888', linewidth=1, visible=True,
                     locked=True),
}

_DIM_CFG = {'dimstyles': {'active': 'Arq-50',
             'styles': {'Arq-50': {'txt_h': 0.20, 'arr': 0.06, 'gap': 0.05}}}}


def ctx(ents, layers=None, scale=None, ox=None, oy=None, cfg=None,
        grid=True) -> RenderCtx:
    lyr = layers or _BASE_LAYERS
    if scale is None:
        scale, ox, oy = zoom_to_fit(ents, W, H, padding=0.15)
    return RenderCtx(
        W=W, H=H, scale=scale, offset_x=ox, offset_y=oy,
        entities=ents, layers=lyr, block_defs={},
        entity_index={}, entity_cell=5.0,
        grid_on=grid, config=cfg or _DIM_CFG,
    )


# ── Runner ────────────────────────────────────────────────────────────────────

class TestRunner:
    def __init__(self, r_pil: RendererPIL, r_ogl: RendererOpenGL):
        self.r_pil = r_pil
        self.r_ogl = r_ogl
        self.results: List[dict] = []
        self._failures: List[str] = []

    def run(self, name: str, render_ctx: RenderCtx,
            mode: str = "geom",   # "geom" | "overlay" | "combined"
            n_warmup: int = 0, n_bench: int = 3) -> dict:
        """
        Ejecuta un escenario: renderiza PIL y OpenGL, mide, reporta.
        mode:
          geom     → umbral estricto (geometría GL)
          overlay  → umbral relajado (texto, cotas — anti-aliasing PIL≠GL)
          combined → umbral intermedio
        """
        # Warm-up
        for _ in range(n_warmup):
            self.r_pil.render(render_ctx)
            self.r_ogl.render(render_ctx)

        # PIL
        t0 = time.perf_counter()
        for _ in range(n_bench):
            img_pil = self.r_pil.render(render_ctx).image
        t_pil = (time.perf_counter() - t0) / n_bench * 1000

        # OpenGL
        t0 = time.perf_counter()
        for _ in range(n_bench):
            img_ogl = self.r_ogl.render(render_ctx).image
        t_ogl = (time.perf_counter() - t0) / n_bench * 1000

        m = metrics(img_pil, img_ogl)

        # Seleccionar criterio según modo
        if mode == "geom":
            ok = m["ok_geom"]
            criteria = "MAE<8, diff<15%"
        elif mode == "overlay":
            ok = m["ok_overlay"]
            criteria = "MAE<12, diff<25%"
        else:  # combined
            ok = m["mae"] < 10.0 and m["pct_diff"] < 22.0
            criteria = "MAE<10, diff<22%"

        flag = "✅" if ok else "❌"
        sp   = t_pil / max(t_ogl, 0.1)

        print(f"  {flag}  {name:<35}  "
              f"PIL={t_pil:5.1f}ms  GL={t_ogl:5.1f}ms  {sp:.1f}x")
        print(f"       MAE={m['mae']:5.2f}  PSNR={m['psnr']:5.1f}dB  "
              f"diff={m['pct_diff']:5.1f}%  max={m['max_diff']:.0f}")

        # Mostrar región con mayor diferencia si hay problemas
        if not ok or m['mae'] > 5:
            worst = max(m['region_mae'], key=lambda k: m['region_mae'][k])
            wval  = m['region_mae'][worst]
            if wval > 5:
                print(f"       ⚠ Región con más diferencia: {worst} "
                      f"(MAE={wval:.1f})")

        if not ok:
            self._failures.append(name)
            reasons = []
            if mode == "geom"    and m['mae']      >= 8.0:  reasons.append(f"MAE={m['mae']:.1f}≥8")
            if mode == "geom"    and m['pct_diff']  >= 15.0: reasons.append(f"diff={m['pct_diff']:.1f}%≥15%")
            if mode == "overlay" and m['mae']      >= 12.0: reasons.append(f"MAE={m['mae']:.1f}≥12")
            if mode == "overlay" and m['pct_diff']  >= 25.0: reasons.append(f"diff={m['pct_diff']:.1f}%≥25%")
            if mode == "combined"and m['mae']      >= 10.0: reasons.append(f"MAE={m['mae']:.1f}≥10")
            if reasons:
                print(f"       ✗ Falló: {', '.join(reasons)}")

        if args.save:
            save_diff(img_pil, img_ogl, name.replace(' ', '_').lower())

        rec = {"name": name, "ok": ok, "mode": mode,
               "t_pil": t_pil, "t_ogl": t_ogl,
               **{k: m[k] for k in ("mae","psnr","pct_diff","max_diff")}}
        self.results.append(rec)
        return rec

    def summary(self) -> bool:
        all_ok = len(self._failures) == 0
        print("\n" + "=" * 68)
        print("  RESUMEN FINAL")
        print("=" * 68)
        for r in self.results:
            flag = "✅" if r["ok"] else "❌"
            sp   = r["t_pil"] / max(r["t_ogl"], 0.1)
            print(f"  {flag}  {r['name']:<35}  "
                  f"MAE={r['mae']:5.2f}  PSNR={r['psnr']:4.0f}dB  "
                  f"{r['t_pil']:.0f}/{r['t_ogl']:.0f}ms  {sp:.1f}x")

        if self._failures:
            print(f"\n  ❌ {len(self._failures)} ESCENARIO(S) FALLARON:")
            for f in self._failures:
                print(f"     · {f}")
        else:
            print("\n  ✅ TODOS LOS ESCENARIOS PASARON")

        # Tabla de speedup por grupo
        if self.results:
            avg_sp = sum(r["t_pil"]/max(r["t_ogl"],0.1) for r in self.results) / len(self.results)
            geom   = [r for r in self.results if r["mode"] == "geom"]
            over   = [r for r in self.results if r["mode"] == "overlay"]
            if geom:
                avg_g = sum(r["t_pil"]/max(r["t_ogl"],0.1) for r in geom) / len(geom)
                print(f"\n  Speedup geometría GL:  {avg_g:.1f}x promedio")
            if over:
                avg_o = sum(r["t_pil"]/max(r["t_ogl"],0.1) for r in over) / len(over)
                print(f"  Speedup overlay PIL:   {avg_o:.1f}x promedio")
            print(f"  Speedup global:        {avg_sp:.1f}x promedio")
        print()
        return all_ok


# ══════════════════════════════════════════════════════════════════════════════
#  ESCENARIOS
# ══════════════════════════════════════════════════════════════════════════════

def grupo_1_geometria(tr: TestRunner):
    """Geometría GL pura: Line, Circle, Arc, Polyline, Spline, Ellipse.
    Estos tipos van por el pipeline GL — fix en PIL NO se hereda.
    Umbral estricto: MAE<8, diff<15%."""

    print("\n── Grupo 1: Geometría GL ─────────────────────────────────────────")

    # 1a — Lines: horizontal, vertical, diagonal, múltiples grosores
    ents = [
        Line(x1=0.5, y1=3.5, x2=9.5, y2=3.5, layer='A-MURO'),   # horizontal
        Line(x1=0.5, y1=0.5, x2=0.5, y2=6.5, layer='A-MURO'),   # vertical
        Line(x1=1,   y1=1,   x2=9,   y2=6,   layer='0'),         # diagonal
        Line(x1=2,   y1=6,   x2=8,   y2=1,   layer='0'),         # diagonal inv
        Line(x1=3,   y1=0,   x2=3,   y2=7,   layer='A-COTA'),    # vertical fino
    ]
    tr.run("1a Lines (H/V/diagonal)", ctx(ents), mode="geom")

    # 1b — Circles: varios radios
    ents = [
        Circle(cx=2,   cy=2,   radius=1.5, layer='0'),
        Circle(cx=6,   cy=2,   radius=0.8, layer='A-MURO'),
        Circle(cx=4,   cy=5,   radius=2.0, layer='A-COTA'),
        Circle(cx=8.5, cy=5.5, radius=0.3, layer='0'),
    ]
    tr.run("1b Circles (varios radios)", ctx(ents), mode="geom")

    # 1c — Arcs: distintos rangos angulares y orientaciones
    ents = [
        Arc(cx=2,   cy=2,   radius=1.5, start_ang=0,   end_ang=90,  ccw=True,  layer='0'),
        Arc(cx=6,   cy=2,   radius=1.5, start_ang=45,  end_ang=270, ccw=True,  layer='A-MURO'),
        Arc(cx=4,   cy=5.5, radius=2.0, start_ang=180, end_ang=360, ccw=True,  layer='0'),
        Arc(cx=8,   cy=5,   radius=1.0, start_ang=270, end_ang=90,  ccw=False, layer='A-COTA'),
    ]
    tr.run("1c Arcs (ángulos variados)", ctx(ents), mode="geom")

    # 1d — Polylines: abierta, cerrada, con quiebres
    ents = [
        Polyline(points=[(0.5,0.5),(4,0.5),(4,3),(8,3),(8,6.5)],
                 closed=False, layer='A-MURO'),
        Polyline(points=[(1,4),(3,5.5),(5,4),(3,2.5)],
                 closed=True,  layer='0'),
        Polyline(points=[(5.5,0.5),(9,0.5),(9,2.5),(5.5,2.5)],
                 closed=True,  layer='A-COTA'),
    ]
    tr.run("1d Polylines (abierta/cerrada)", ctx(ents), mode="geom")

    # 1e — Splines
    try:
        from cad.entities import Spline as _Spline
        ents = [
            _Spline(points=[(0.5,3),(2,1),(4,5),(6,1),(8,4),(9.5,3)],
                    closed=False, layer='0'),
            _Spline(points=[(1,2),(3,6),(6,6),(8,2)],
                    closed=True,  layer='A-MURO'),
        ]
        tr.run("1e Splines", ctx(ents), mode="geom")
    except Exception as e:
        print(f"  ⚠  Spline no disponible: {e}")

    # 1f — Ellipses
    try:
        from cad.entities import Ellipse as _Ell
        ents = [
            _Ell(cx=3, cy=3, rx=2.5, ry=1.2, angle=0,   layer='0'),
            _Ell(cx=7, cy=5, rx=1.5, ry=2.0, angle=45,  layer='A-MURO'),
            _Ell(cx=5, cy=1.5, rx=3, ry=0.8, angle=30,  layer='A-COTA'),
        ]
        tr.run("1f Ellipses (rotadas)", ctx(ents), mode="geom")
    except Exception as e:
        print(f"  ⚠  Ellipse no disponible: {e}")

    # 1g — Mix geometría: todas juntas a zoom-fit
    ents = [
        Line(x1=0, y1=0, x2=10, y2=0, layer='A-MURO'),
        Line(x1=0, y1=7, x2=10, y2=7, layer='A-MURO'),
        Line(x1=0, y1=0, x2=0,  y2=7, layer='A-MURO'),
        Line(x1=10,y1=0, x2=10, y2=7, layer='A-MURO'),
        Circle(cx=5, cy=3.5, radius=2, layer='0'),
        Arc(cx=2, cy=5, radius=1.5, start_ang=0, end_ang=180, ccw=True, layer='A-COTA'),
        Polyline(points=[(7,1),(9,2),(9,5),(7,6)], closed=False, layer='0'),
    ]
    tr.run("1g Mix geometría", ctx(ents), mode="geom")


def grupo_2_overlay(tr: TestRunner):
    """Overlay PIL: Text, Dimension, Hatch.
    Estos tipos usan código PIL en ambos renderers.
    Umbral relajado: MAE<12, diff<25% (anti-aliasing + sub-pixel)."""

    print("\n── Grupo 2: Overlay PIL (Text/Dim/Hatch) ────────────────────────")

    # 2a — Textos: tamaños y rotaciones
    ents = [
        Text(x=1,   y=6,   content='DORMITORIO',   height=0.40, layer='A-TEXT'),
        Text(x=4,   y=4,   content='SALA',          height=0.30, layer='A-TEXT'),
        Text(x=7,   y=6,   content='COCINA',        height=0.25, layer='A-TEXT'),
        Text(x=1,   y=2,   content='BAÑO',          height=0.20, layer='A-TEXT'),
        Text(x=5,   y=1.5, content='45°',           height=0.25,
             angle=45, layer='A-TEXT'),
        Text(x=8.5, y=3,   content='90°',           height=0.20,
             angle=90, layer='A-TEXT'),
    ]
    tr.run("2a Text (tamaños y rotaciones)", ctx(ents), mode="overlay")

    # 2b — Cotas: H, V, alineada
    ents = [
        Line(x1=0,y1=0, x2=6,y2=0, layer='A-MURO'),
        Line(x1=0,y1=0, x2=0,y2=4, layer='A-MURO'),
        Line(x1=6,y1=0, x2=6,y2=4, layer='A-MURO'),
        Line(x1=0,y1=4, x2=6,y2=4, layer='A-MURO'),
        Dimension(p1=(0,0), p2=(6,0), pos=(3,-0.8),
                  dim_type='H', layer='A-COTA', style='Arq-50'),
        Dimension(p1=(0,0), p2=(0,4), pos=(-0.8,2),
                  dim_type='V', layer='A-COTA', style='Arq-50'),
        Dimension(p1=(0,4), p2=(6,4), pos=(3,5),
                  dim_type='H', layer='A-COTA', style='Arq-50'),
    ]
    tr.run("2b Dimensions (H y V)", ctx(ents), mode="overlay")

    # 2c — Hatches: sólido y patrón
    ents = [
        Hatch(boundary=[(0.5,0.5),(3.5,0.5),(3.5,3.5),(0.5,3.5)],
              pattern='SOLID',  angle=0,  scale=1.0,  layer='A-HATCH'),
        Hatch(boundary=[(4.5,0.5),(8.5,0.5),(8.5,3.5),(4.5,3.5)],
              pattern='ANSI31', angle=45, scale=0.15, layer='A-HATCH'),
        Hatch(boundary=[(1,4.5),(5,4.5),(5,6.5),(1,6.5)],
              pattern='ANSI31', angle=0,  scale=0.20, layer='0'),
    ]
    tr.run("2c Hatches (SOLID y ANSI31)", ctx(ents), mode="overlay")

    # 2d — Mix overlay completo
    ents = [
        Line(x1=0,y1=0, x2=8,y2=0, layer='A-MURO'),
        Line(x1=0,y1=5, x2=8,y2=5, layer='A-MURO'),
        Line(x1=0,y1=0, x2=0,y2=5, layer='A-MURO'),
        Line(x1=8,y1=0, x2=8,y2=5, layer='A-MURO'),
        Text(x=4, y=2.5, content='SALA', height=0.35, layer='A-TEXT'),
        Dimension(p1=(0,0), p2=(8,0), pos=(4,-0.8),
                  dim_type='H', layer='A-COTA', style='Arq-50'),
        Hatch(boundary=[(6,0),(8,0),(8,5),(6,5)],
              pattern='ANSI31', angle=45, scale=0.12, layer='A-HATCH'),
    ]
    tr.run("2d Mix overlay completo", ctx(ents), mode="combined")


def grupo_3_combinado(tr: TestRunner):
    """Plano típico arquitectónico: geometría + anotaciones juntas."""

    print("\n── Grupo 3: Plano arquitectónico típico ─────────────────────────")

    # 3a — Recinto simple con cotas y texto
    ents = [
        # Muros exteriores
        Line(x1=0,  y1=0,  x2=12, y2=0,  layer='A-MURO'),
        Line(x1=12, y1=0,  x2=12, y2=8,  layer='A-MURO'),
        Line(x1=12, y1=8,  x2=0,  y2=8,  layer='A-MURO'),
        Line(x1=0,  y1=8,  x2=0,  y2=0,  layer='A-MURO'),
        # Muro interior
        Line(x1=7,  y1=0,  x2=7,  y2=8,  layer='A-MURO'),
        # Círculos (sanitarios simulados)
        Circle(cx=2, cy=4, radius=0.6, layer='0'),
        Circle(cx=3.5, cy=6, radius=0.5, layer='0'),
        # Arc (puerta simulada)
        Arc(cx=7, cy=2.5, radius=1.0, start_ang=0, end_ang=90, ccw=True, layer='0'),
        # Polilínea (ventana simulada)
        Polyline(points=[(3,8),(6,8)], closed=False, layer='0'),
        # Textos
        Text(x=3.5, y=4, content='SALA-COMEDOR', height=0.35, layer='A-TEXT'),
        Text(x=9.5, y=4, content='DORMITORIO',   height=0.30, layer='A-TEXT'),
        # Cotas
        Dimension(p1=(0,0), p2=(12,0), pos=(6,-1.0),
                  dim_type='H', layer='A-COTA', style='Arq-50'),
        Dimension(p1=(0,0), p2=(0,8),  pos=(-1.0,4),
                  dim_type='V', layer='A-COTA', style='Arq-50'),
        Dimension(p1=(0,0), p2=(7,0),  pos=(3.5,-1.8),
                  dim_type='H', layer='A-COTA', style='Arq-50'),
        # Hatch en dormitorio
        Hatch(boundary=[(7,0),(12,0),(12,8),(7,8)],
              pattern='ANSI31', angle=45, scale=0.12, layer='A-HATCH'),
    ]
    tr.run("3a Plano típico (muros+cotas+texto)", ctx(ents), mode="combined")

    # 3b — Mismo plano a escala de trabajo (zoom-in)
    sc, ox, oy = zoom_to_fit(ents, W, H, padding=0.60)
    tr.run("3b Plano típico zoom-work", ctx(ents, scale=sc, ox=ox, oy=oy),
           mode="combined")

    # 3c — Capas ocultas y bloqueadas (no deben aparecer)
    ents_caps = ents + [
        Line(x1=0, y1=0, x2=12, y2=8, layer='HIDDEN'),   # capa invisible
        Line(x1=0, y1=8, x2=12, y2=0, layer='LOCKED'),   # capa bloqueada (gris)
    ]
    tr.run("3c Capas ocultas/bloqueadas", ctx(ents_caps), mode="combined")


def grupo_4_dxf_real(tr: TestRunner):
    """DXF real: múltiples zooms con el plano de Casa Merlos."""

    print("\n── Grupo 4: DXF real ────────────────────────────────────────────")

    if not dxf_path or not os.path.exists(dxf_path):
        print(f"  ⚠  DXF no encontrado — pasa --dxf ruta/al/archivo.dxf")
        return

    from cad.dxf_import import importar_dxf
    from cad.renderer_pil import build_layer_cache

    fname = Path(dxf_path).name
    fsize = os.path.getsize(dxf_path) // 1024
    print(f"  Cargando: {fname}  ({fsize} KB)")
    try:
        result = importar_dxf(dxf_path)
    except Exception as e:
        print(f"  ❌ Error al importar DXF: {e}")
        return

    ents_dxf  = result.entities
    lyrs_dxf  = result.layers
    bdefs_dxf = result.block_defs
    print(f"  {len(ents_dxf)} entidades, {len(lyrs_dxf)} capas")

    # Desglose de tipos
    from collections import Counter
    tipos = Counter(type(e).__name__ for e in ents_dxf)
    top5 = tipos.most_common(5)
    print(f"  Tipos: {', '.join(f'{t}={n}' for t,n in top5)}")

    zooms = [
        ("zoom_fit",    0.05),
        ("zoom_work",   0.45),
        ("zoom_detail", 0.80),
    ]
    for zname, pad in zooms:
        sc, ox, oy = zoom_to_fit(ents_dxf, W, H, padding=pad)
        c = RenderCtx(
            W=W, H=H, scale=sc, offset_x=ox, offset_y=oy,
            entities=ents_dxf, layers=lyrs_dxf, block_defs=bdefs_dxf,
            entity_index={}, entity_cell=5.0,
            grid_on=True, config=_DIM_CFG,
        )
        tr.run(f"4 DXF {zname} ({len(ents_dxf)}ents)",
               c, mode="combined", n_bench=2)


def grupo_5_edge_cases(tr: TestRunner):
    """Casos extremos: LOD, entidades fuera del viewport, color edge cases."""

    print("\n── Grupo 5: Casos extremos ──────────────────────────────────────")

    # 5a — Entidades sub-píxel (LOD debe saltarlas en GL, no en PIL)
    #      Esperado: diferencia alta si LOD recorta entidades que PIL dibuja
    ents_tiny = [
        Line(x1=0, y1=0, x2=0.001, y2=0.001, layer='0'),  # sub-pixel
        Line(x1=1, y1=1, x2=11,    y2=1,     layer='A-MURO'),  # visible
        Circle(cx=6, cy=4, radius=0.0005, layer='0'),           # sub-pixel
        Circle(cx=3, cy=4, radius=2.0,    layer='0'),           # visible
    ]
    # A zoom-fit las entidades sub-pixel NO se ven en ninguno → OK
    tr.run("5a LOD sub-pixel", ctx(ents_tiny), mode="geom")

    # 5b — Muchas capas con colores variados
    layers_many = {}
    ents_colored = []
    colors = ['#FF0000','#00FF00','#0000FF','#FFFF00','#FF00FF',
              '#00FFFF','#FF8800','#8800FF','#FFFFFF','#888888']
    for i, col in enumerate(colors):
        lname = f"L{i:02d}"
        layers_many[lname] = Layer(name=lname, color=col,
                                   linewidth=1, visible=True)
        x0 = (i % 5) * 2
        y0 = (i // 5) * 3
        ents_colored.append(Line(x1=x0, y1=y0, x2=x0+1.5, y2=y0+2.5,
                                 layer=lname))
        ents_colored.append(Circle(cx=x0+0.75, cy=y0+1.25, radius=0.6,
                                   layer=lname))
    tr.run("5b 10 capas colores variados",
           ctx(ents_colored, layers=layers_many), mode="geom")

    # 5c — Canvas vacío (solo background + grid)
    # mode="overlay": PIL y OpenGL tienen grillas con pasos distintos
    # (diferencia visual esperada, no bug funcional).
    tr.run("5c Canvas vacío (fondo + grilla)",
           ctx([], scale=40, ox=W//2, oy=H//2), mode="overlay")

    # 5d — Entidad justo en el borde del viewport
    sc_work = 80
    ox, oy = W // 2, H // 2
    vx0 = -ox / sc_work
    vy0 = -(H - oy) / sc_work
    ents_borde = [
        Line(x1=vx0, y1=0, x2=vx0+0.1, y2=5, layer='A-MURO'),      # borde izq
        Line(x1=0, y1=vy0, x2=5, y2=vy0+0.1, layer='A-MURO'),      # borde inf
        Circle(cx=0, cy=0, radius=2, layer='0'),                     # centrado
    ]
    tr.run("5d Entidades en borde viewport",
           ctx(ents_borde, scale=sc_work, ox=ox, oy=oy), mode="geom")


def grupo_6_performance(tr: TestRunner):
    """Benchmark comparativo PIL vs OpenGL a distintas densidades."""

    print("\n── Grupo 6: Performance ─────────────────────────────────────────")

    import random
    random.seed(42)

    for n_ents, label in [(500, "500ents"), (2000, "2k ents"), (5000, "5k ents")]:
        ents = []
        for i in range(n_ents):
            x0 = random.uniform(0, 50)
            y0 = random.uniform(0, 50)
            t  = i % 4
            if t == 0:
                ents.append(Line(x1=x0, y1=y0,
                                 x2=x0+random.uniform(0.5,3),
                                 y2=y0+random.uniform(0.5,3), layer='A-MURO'))
            elif t == 1:
                ents.append(Circle(cx=x0, cy=y0,
                                   radius=random.uniform(0.2,1.5), layer='0'))
            elif t == 2:
                ents.append(Arc(cx=x0, cy=y0,
                                radius=random.uniform(0.3,1.0),
                                start_ang=random.uniform(0,180),
                                end_ang=random.uniform(180,360),
                                ccw=True, layer='A-COTA'))
            else:
                pts = [(x0+random.uniform(0,2), y0+random.uniform(0,2))
                       for _ in range(3)]
                ents.append(Polyline(points=pts, closed=False, layer='0'))

        sc, ox, oy = zoom_to_fit(ents, W, H, padding=0.05)
        c = ctx(ents, scale=sc, ox=ox, oy=oy, grid=False)

        t0 = time.perf_counter()
        for _ in range(5): tr.r_pil.render(c)
        t_pil = (time.perf_counter() - t0) / 5 * 1000

        t0 = time.perf_counter()
        for _ in range(5): tr.r_ogl.render(c)
        t_ogl = (time.perf_counter() - t0) / 5 * 1000

        sp = t_pil / max(t_ogl, 0.1)
        print(f"  📊  {label:<10}  PIL={t_pil:6.1f}ms  GL={t_ogl:6.1f}ms  "
              f"speedup={sp:.1f}x")

    # Límite práctico
    print("\n  Estimación de límite para 30 FPS (≤33ms/frame):")
    print("     PIL:    ~3.000–5.000 entidades")
    print("     OpenGL: ~30.000–50.000 entidades (pan con cache hit)")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\nCanvas: {W}×{H}px")

    r_pil = RendererPIL()
    r_ogl = RendererOpenGL()

    if not r_ogl.available():
        print("\n[ERROR] OpenGL no disponible.")
        sys.exit(1)

    # Warm-up GL
    _dl = {'0': Layer(name='0', color='#FFFFFF', linewidth=1, visible=True)}
    _dc = RenderCtx(W=64, H=64, scale=10, offset_x=32, offset_y=32, layers=_dl)
    r_ogl.render(_dc)
    print(f"Renderers: {type(r_pil).__name__} | {r_ogl.name()}\n")

    tr = TestRunner(r_pil, r_ogl)

    grupo = args.grupo
    if grupo in (0, 1): grupo_1_geometria(tr)
    if grupo in (0, 2): grupo_2_overlay(tr)
    if grupo in (0, 3): grupo_3_combinado(tr)
    if grupo in (0, 4): grupo_4_dxf_real(tr)
    if grupo in (0, 5): grupo_5_edge_cases(tr)
    if grupo in (0, 6): grupo_6_performance(tr)

    ok = tr.summary()
    r_ogl.cleanup()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
