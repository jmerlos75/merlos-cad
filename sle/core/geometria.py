"""
sle/core/geometria.py
=====================
Motor de geometría 2D para extracción de recintos con ángulos múltiplos de 45°.

Soporta los 8 ángulos del Arq. Merlos:
  0° (E)  45° (NE)  90° (N)  135° (NO)
  180° (O)  225° (SO)  270° (S)  315° (SE)

Algoritmo:
  1. Lanzar 8 rayos → identificar QUÉ muro golpea cada rayo
  2. Para cada par de rayos consecutivos que golpeen MUROS DISTINTOS
     → vértice real = intersección infinita de esos dos muros
  3. Pares que golpeen el MISMO muro → no hay vértice (pared recta)
  Esto da el polígono exacto con los vértices correctos.
"""
from __future__ import annotations

import math
from typing import Optional

# ── Constantes ───────────────────────────────────────────────────────────

# 8 ángulos en grados (0 = Este, 90 = Norte, sentido antihorario)
ANGULOS_8: list[int] = [0, 45, 90, 135, 180, 225, 270, 315]

# Vectores unitarios precalculados
_DIRS_8: list[tuple[float, float]] = [
    (math.cos(math.radians(a)), math.sin(math.radians(a)))
    for a in ANGULOS_8
]

EPS = 1e-9


# ════════════════════════════════════════════════════════════════════════
# PRIMITIVAS GEOMÉTRICAS
# ════════════════════════════════════════════════════════════════════════

def rayo_segmento_interseccion(
    orig:    tuple[float, float],
    dir_rad: float,
    seg_a:   tuple[float, float],
    seg_b:   tuple[float, float],
) -> Optional[float]:
    """
    Intersección rayo → segmento.

    Rayo    : P + t·r  (r = (cos θ, sin θ), t > 0)
    Segmento: A + u·(B-A)  con u ∈ [0, 1]

    Cramer:
        D  = ry·sx − rx·sy          (s = B − A)
        t  = (dy·sx − dx·sy) / D    (d = A − P)
        u  = (rx·dy − ry·dx) / D

    Retorna t si la intersección existe, None en caso contrario.
    """
    rx = math.cos(dir_rad)
    ry = math.sin(dir_rad)
    sx = seg_b[0] - seg_a[0]
    sy = seg_b[1] - seg_a[1]

    D = ry * sx - rx * sy
    if abs(D) < EPS:
        return None             # rayo paralelo al segmento

    dx = seg_a[0] - orig[0]
    dy = seg_a[1] - orig[1]

    t = (dy * sx - dx * sy) / D
    u = (rx * dy - ry * dx) / D

    if t > EPS and -EPS <= u <= 1.0 + EPS:
        return t
    return None


def interseccion_lineas_infinitas(
    ln1: dict, ln2: dict,
) -> Optional[tuple[float, float]]:
    """
    Intersección de dos líneas extendidas al infinito.
    ln1, ln2 : dicts con x1,y1,x2,y2

    Fórmula determinante:
        |x1 y1 1|         |x3 y3 1|
        |x2 y2 1|   y     |x4 y4 1|
        ...
    Retorna (x, y) o None si son paralelas.
    """
    x1, y1 = ln1["x1"], ln1["y1"]
    x2, y2 = ln1["x2"], ln1["y2"]
    x3, y3 = ln2["x1"], ln2["y1"]
    x4, y4 = ln2["x2"], ln2["y2"]

    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < EPS:
        return None             # paralelas o coincidentes

    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    x = x1 + t * (x2 - x1)
    y = y1 + t * (y2 - y1)
    return (x, y)


def area_poligono(vertices: list[tuple[float, float]]) -> float:
    """Área por fórmula de Gauss (shoelace). Siempre positiva."""
    n = len(vertices)
    if n < 3:
        return 0.0
    total = sum(
        vertices[i][0] * vertices[(i + 1) % n][1]
        - vertices[(i + 1) % n][0] * vertices[i][1]
        for i in range(n)
    )
    return abs(total) / 2.0


def bbox_poligono(vertices: list[tuple[float, float]]) -> dict:
    """Bounding box axis-aligned."""
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    return {
        "x_min": min(xs), "y_min": min(ys),
        "x_max": max(xs), "y_max": max(ys),
        "cx": (min(xs) + max(xs)) / 2,
        "cy": (min(ys) + max(ys)) / 2,
    }


def ordenar_ccw(vertices: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Ordena vértices en sentido antihorario respecto a su centroide."""
    if not vertices:
        return []
    cx = sum(v[0] for v in vertices) / len(vertices)
    cy = sum(v[1] for v in vertices) / len(vertices)
    return sorted(vertices, key=lambda v: math.atan2(v[1] - cy, v[0] - cx))


def angulo_segmento_deg(ln: dict) -> float:
    """Ángulo del segmento en [0°, 180°)."""
    dx = ln["x2"] - ln["x1"]
    dy = ln["y2"] - ln["y1"]
    return math.degrees(math.atan2(dy, dx)) % 180.0


def misma_linea_infinita(ln1: dict, ln2: dict, tol_ang: float = 2.0, tol_dist: float = None) -> bool:
    """
    Devuelve True si ln1 y ln2 son segmentos de la misma línea infinita.
    Compara ángulo y distancia perpendicular desde el origen.
    tol_dist: tolerancia en unidades del dibujo (se ajusta externamente).
    """
    if tol_dist is None:
        tol_dist = 0.5

    a1 = angulo_segmento_deg(ln1)
    a2 = angulo_segmento_deg(ln2)
    diff_ang = abs(a1 - a2) % 180.0
    if diff_ang > tol_ang and abs(diff_ang - 180.0) > tol_ang:
        return False

    # Distancia perpendicular desde (0,0) a cada línea
    def dist_perp(ln):
        dx = ln["x2"] - ln["x1"]
        dy = ln["y2"] - ln["y1"]
        L = math.hypot(dx, dy)
        if L < EPS:
            return 0.0
        # |A×(B-A)| / |B-A|  donde A=origen
        return abs(ln["x1"] * dy - ln["y1"] * dx) / L

    return abs(dist_perp(ln1) - dist_perp(ln2)) < tol_dist


# ════════════════════════════════════════════════════════════════════════
# EXTRACCIÓN DE POLÍGONO
# ════════════════════════════════════════════════════════════════════════

def _muro_mas_cercano(
    orig:     tuple[float, float],
    dir_rad:  float,
    lineas:   list[dict],
    rango:    float,
    min_dist: float,
) -> tuple[float, int]:
    """Rayo en dir_rad → (distancia, índice del muro). -1 si no hay."""
    t_min    = rango
    wall_idx = -1
    for j, ln in enumerate(lineas):
        t = rayo_segmento_interseccion(
            orig, dir_rad,
            (ln["x1"], ln["y1"]),
            (ln["x2"], ln["y2"]),
        )
        if t is not None and min_dist < t < t_min:
            t_min    = t
            wall_idx = j
    return t_min, wall_idx


def poligono_desde_paredes(
    px:       float,
    py:       float,
    lineas:   list[dict],
    rango:    float = 30.0,
    angulos:  list[int] = ANGULOS_8,
    min_dist: float = 0.05,
    tol_dist: float = 0.5,
) -> Optional[dict]:
    """
    Detecta el polígono real de un recinto arquitectónico.

    Algoritmo:
      1. Lanza 8 rayos y registra qué muro golpea cada uno.
      2. Para pares consecutivos con muros DISTINTOS:
         vértice = intersección infinita de esos dos muros.
      3. Para pares con el MISMO muro (o muros colineales):
         no hay esquina → ignorar.

    Args:
        px, py    : posición del texto del recinto (unidades del dibujo)
        lineas    : lista de dicts {x1,y1,x2,y2} con segmentos de muro
        rango     : distancia máxima de búsqueda
        angulos   : ángulos en grados (default: 8 dirs × 45°)
        min_dist  : distancia mínima (evita auto-impacto)
        tol_dist  : tolerancia para detectar muros colineales (misma línea)

    Returns:
        dict con vertices, area, bbox, rayos, n_impactos, n_vertices
        o None si < 3 rayos impactaron.
    """
    orig  = (px, py)
    dirs  = [
        (math.cos(math.radians(a)), math.sin(math.radians(a)))
        for a in angulos
    ]

    # ── Paso 1: lanzar rayos ────────────────────────────────────
    impactos: list[dict] = []
    for angulo_deg, (rx, ry) in zip(angulos, dirs):
        dir_rad = math.radians(angulo_deg)
        t, widx = _muro_mas_cercano(orig, dir_rad, lineas, rango, min_dist)
        if widx >= 0:
            impactos.append({
                "ang":   angulo_deg,
                "t":     t,
                "wall":  widx,
                "punto": (px + rx * t, py + ry * t),
            })

    if len(impactos) < 3:
        return None

    # ── Paso 2: construir vértices reales ───────────────────────
    # Vértice entre imp[i] y imp[i+1] solo si golpean muros distintos
    # (incluyendo el par último→primero para cerrar el polígono).
    vertices: list[tuple[float, float]] = []
    n = len(impactos)

    for i in range(n):
        curr  = impactos[i]
        nxt   = impactos[(i + 1) % n]

        ln_curr = lineas[curr["wall"]]
        ln_nxt  = lineas[nxt["wall"]]

        # Mismo segmento o muros colineales → no hay esquina
        if curr["wall"] == nxt["wall"] or misma_linea_infinita(
                ln_curr, ln_nxt, tol_dist=tol_dist):
            continue

        # Esquina = intersección infinita de los dos muros
        corner = interseccion_lineas_infinitas(ln_curr, ln_nxt)
        if corner is None:
            # Muros paralelos (p.ej. caja sin salida) → usar punto de impacto
            corner = curr["punto"]

        # Filtro de distancia: la esquina no debe estar lejos del texto
        dist = math.hypot(corner[0] - px, corner[1] - py)
        if dist < rango * 2:
            vertices.append(corner)

    if len(vertices) < 3:
        # Fallback: usar los puntos de impacto directos
        vertices = [imp["punto"] for imp in impactos]
        if len(vertices) < 3:
            return None

    # ── Paso 3: ordenar CCW y calcular métricas ─────────────────
    vertices = ordenar_ccw(vertices)
    area     = area_poligono(vertices)
    bbox     = bbox_poligono(vertices)
    bbox["area"] = area

    return {
        "vertices":   vertices,
        "area":       area,
        "bbox":       bbox,
        "rayos":      {imp["ang"]: round(imp["t"], 4) for imp in impactos},
        "n_impactos": len(impactos),
        "n_vertices": len(vertices),
    }


def poligono_a_recinto(
    nombre:   str,
    resultado: dict,
    origen_x:  float,
    origen_y:  float,
    escala:    float,
) -> dict:
    """
    Convierte resultado de poligono_desde_paredes() al formato de recinto SLE.
    Mantiene compatibilidad con el grid existente (fila/col/ancho/alto).
    Agrega polígono real en metros para uso futuro.
    """
    bbox = resultado["bbox"]

    # Vértices en metros relativos al origen del dibujo
    vertices_m = [
        (round((v[0] - origen_x) / escala, 3),
         round((v[1] - origen_y) / escala, 3))
        for v in resultado["vertices"]
    ]

    ancho_m = round((bbox["x_max"] - bbox["x_min"]) / escala, 2)
    alto_m  = round((bbox["y_max"] - bbox["y_min"]) / escala, 2)
    area_m2 = round(resultado["area"] / (escala ** 2), 2)

    col  = round((bbox["x_min"] - origen_x) / escala, 2)
    fila = round((bbox["y_min"] - origen_y) / escala, 2)

    return {
        "nombre":   nombre,
        "fila":     fila,
        "col":      col,
        "ancho":    ancho_m,
        "alto":     alto_m,
        "area_m2":  area_m2,
        "poligono": vertices_m,
        "n_rayos":  resultado["n_impactos"],
        "n_lados":  resultado["n_vertices"],
        "_bbox_raw": bbox,
    }


# ════════════════════════════════════════════════════════════════════════
# SMOKE TESTS
# ════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    def ok(cond, msg):
        estado = "[OK]" if cond else "[!!]"
        print(f"  {estado}  {msg}")
        if not cond:
            raise AssertionError(msg)

    print()
    print("=" * 62)
    print("  geometria.py — Smoke Tests")
    print("=" * 62)

    # ── Test 1: Recinto rectangular 4×3m ────────────────────────
    print("\n[ 1 ] Rectangular 4x3m — texto en centro (2, 1.5)")
    L1 = [
        {"x1": 0, "y1": 0, "x2": 4, "y2": 0},   # sur   (horizontal)
        {"x1": 0, "y1": 3, "x2": 4, "y2": 3},   # norte (horizontal)
        {"x1": 0, "y1": 0, "x2": 0, "y2": 3},   # oeste (vertical)
        {"x1": 4, "y1": 0, "x2": 4, "y2": 3},   # este  (vertical)
    ]
    r1 = poligono_desde_paredes(2.0, 1.5, L1, rango=10.0, tol_dist=0.1)
    ok(r1 is not None, "Recinto detectado")
    ok(abs(r1["area"] - 12.0) < 0.1, f"Area={r1['area']:.2f} (esperado 12.0)")
    ok(r1["n_vertices"] == 4, f"Vertices={r1['n_vertices']} (esperado 4)")
    print(f"  Vertices: {[(round(v[0],2), round(v[1],2)) for v in r1['vertices']]}")
    print(f"  Area    : {r1['area']:.2f}  Lados: {r1['n_vertices']}")

    # ── Test 2: Recinto con corte diagonal 45° ───────────────────
    print("\n[ 2 ] Cuadrado 4x4 con esquina NE cortada a 45°")
    # Vertices reales: (0,0) (4,0) (4,2) (2,4) (0,4)
    # Area = 16 - 0.5*2*2 = 14
    L2 = [
        {"x1": 0, "y1": 0, "x2": 4, "y2": 0},   # sur
        {"x1": 4, "y1": 0, "x2": 4, "y2": 2},   # este (parcial)
        {"x1": 4, "y1": 2, "x2": 2, "y2": 4},   # diagonal 135°
        {"x1": 2, "y1": 4, "x2": 0, "y2": 4},   # norte (parcial)
        {"x1": 0, "y1": 4, "x2": 0, "y2": 0},   # oeste
    ]
    r2 = poligono_desde_paredes(1.5, 1.5, L2, rango=10.0, tol_dist=0.1)
    ok(r2 is not None, "Recinto detectado")
    ok(abs(r2["area"] - 14.0) < 0.5, f"Area={r2['area']:.2f} (esperado ~14.0)")
    ok(r2["n_vertices"] == 5, f"Vertices={r2['n_vertices']} (esperado 5)")
    print(f"  Vertices: {[(round(v[0],2), round(v[1],2)) for v in r2['vertices']]}")
    print(f"  Area    : {r2['area']:.2f}  Lados: {r2['n_vertices']}")

    # ── Test 3: Recinto octogonal (dos diagonales) ───────────────
    print("\n[ 3 ] Recinto octogonal — todas las esquinas a 45°")
    # Octágono regular aprox: 3x3 con las 4 esquinas cortadas 1m
    # (0,1) (1,0) (3,0) (4,1) (4,3) (3,4) (1,4) (0,3)
    L3 = [
        {"x1": 1, "y1": 0, "x2": 3, "y2": 0},   # sur
        {"x1": 3, "y1": 0, "x2": 4, "y2": 1},   # SE diagonal
        {"x1": 4, "y1": 1, "x2": 4, "y2": 3},   # este
        {"x1": 4, "y1": 3, "x2": 3, "y2": 4},   # NE diagonal
        {"x1": 3, "y1": 4, "x2": 1, "y2": 4},   # norte
        {"x1": 1, "y1": 4, "x2": 0, "y2": 3},   # NO diagonal
        {"x1": 0, "y1": 3, "x2": 0, "y2": 1},   # oeste
        {"x1": 0, "y1": 1, "x2": 1, "y2": 0},   # SO diagonal
    ]
    # Area octágono: 4x4 - 4*(0.5*1*1) = 16 - 2 = 14
    r3 = poligono_desde_paredes(2.0, 2.0, L3, rango=10.0, tol_dist=0.1)
    ok(r3 is not None, "Recinto detectado")
    ok(abs(r3["area"] - 14.0) < 0.5, f"Area={r3['area']:.2f} (esperado ~14.0)")
    ok(r3["n_vertices"] == 8, f"Vertices={r3['n_vertices']} (esperado 8)")
    print(f"  Vertices: {[(round(v[0],2), round(v[1],2)) for v in r3['vertices']]}")
    print(f"  Area    : {r3['area']:.2f}  Lados: {r3['n_vertices']}")

    # ── Test 4: Función de intersección ─────────────────────────
    print("\n[ 4 ] rayo_segmento_interseccion — casos básicos")
    import math as _math
    t = rayo_segmento_interseccion((0,0), 0.0, (5,-1), (5,1))
    ok(t is not None and abs(t - 5.0) < 0.001, f"Rayo E vs x=5: t={t}")

    t2 = rayo_segmento_interseccion((0,0), _math.radians(90), (-1,4), (1,4))
    ok(t2 is not None and abs(t2 - 4.0) < 0.001, f"Rayo N vs y=4: t={t2}")

    t3 = rayo_segmento_interseccion((0,0), _math.radians(45), (0,4), (4,0))
    esperado = _math.sqrt(8)
    ok(t3 is not None and abs(t3 - esperado) < 0.01,
       f"Rayo NE vs diagonal: t={round(t3,4)} (esperado {round(esperado,4)})")

    # ── Test 5: poligono_a_recinto ───────────────────────────────
    print("\n[ 5 ] poligono_a_recinto — conversion a formato SLE")
    rec = poligono_a_recinto("SALA", r1, 0.0, 0.0, escala=1.0)
    ok(rec["nombre"] == "SALA", "Nombre correcto")
    ok(abs(rec["area_m2"] - 12.0) < 0.1, f"Area m2={rec['area_m2']}")
    ok(rec["n_lados"] == 4, f"Lados={rec['n_lados']}")
    ok(len(rec["poligono"]) == 4, "Poligono tiene 4 vertices")
    print(f"  {rec['nombre']}: {rec['ancho']}x{rec['alto']}m  area={rec['area_m2']}m²  lados={rec['n_lados']}")

    print()
    print("=" * 62)
    print("  Todos los tests OK")
    print("=" * 62)
    print()
