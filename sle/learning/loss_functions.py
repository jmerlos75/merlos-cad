"""
sle/learning/loss_functions.py
==============================
Funciones de pérdida arquitectónicas para entrenar el SLE.

Diseñadas para que el modelo aprenda a valorar lo mismo que el arquitecto:
1. `loss_topologica`   — penaliza distribuciones de tipos alejadas al ideal
2. `loss_normativa`    — penaliza violaciones de la normativa CR (INVU/CFIA)
3. `loss_similitud`    — contrastiva: planes del arquitecto deben ser similares entre sí
4. `loss_correccion`   — amplifica la penalización en zonas que el arquitecto corrigió
5. `loss_total`        — combinación ponderada de las anteriores

Todas las funciones operan sobre vectores Python puros (sin PyTorch).
Si PyTorch está disponible, también se exponen como nn.Module para uso en trainer.

Retornan float (0 = perfecto, mayor = peor).
"""
from __future__ import annotations

import math
from typing import Any

from sle.core.spatial_graph import SpatialGraph
from sle.core.encoder import encode_graph, cosine_similarity, DIM_VECTOR


# ─── Loss topológica ─────────────────────────────────────────────────

# Proporciones ideales por tipo (del manual de diseño de JM)
PROPORCIONES_IDEALES: dict[str, float] = {
    "sala":                 0.15,
    "comedor":              0.08,
    "sala_comedor":         0.18,
    "cocina":               0.10,
    "dormitorio_principal": 0.12,
    "dormitorio":           0.10,
    "bano":                 0.06,
    "pasillo":              0.06,
    "cochera":              0.08,
    "lavanderia":           0.04,
    "estudio":              0.05,
    "cuarto_servicio":      0.04,
    "otro":                 0.00,
}

def loss_topologica(g: SpatialGraph) -> float:
    """
    Penaliza distribuciones de tipos de recintos alejadas de la proporción ideal.
    Retorna un valor entre 0 (ideal) y 1 (muy diferente al ideal).
    """
    if not g.nodos:
        return 1.0

    n = len(g.nodos)
    tipos = g.recintos_por_tipo()
    error = 0.0

    for tipo, ideal in PROPORCIONES_IDEALES.items():
        real = len(tipos.get(tipo, [])) / n
        error += abs(real - ideal)

    # Normalizar: el máximo error posible es 2.0 (todo tipo en cero + 1.0)
    return min(error / 2.0, 1.0)


# ─── Loss normativa CR ───────────────────────────────────────────────

# Áreas mínimas según INVU/CFIA
AREAS_MINIMAS: dict[str, float] = {
    "dormitorio_principal": 9.0,
    "dormitorio":           7.5,
    "cocina":               5.0,
    "sala":                 10.0,
    "sala_comedor":         10.0,
    "bano":                 2.5,
    "pasillo":              0.0,  # solo ancho mínimo
}

CIRCULACION_MINIMA = 1.20  # m
CIELO_MINIMO       = 2.40  # m


def loss_normativa(g: SpatialGraph) -> float:
    """
    Penaliza violaciones de la normativa CR.
    Retorna [0, 1] — 0 = sin violaciones, 1 = todo viola norma.
    Actualmente evalúa: áreas mínimas y pasillo mínimo.
    """
    if not g.nodos:
        return 1.0

    violaciones = 0
    total_checks = 0

    for nombre, nodo in g.nodos.items():
        area_min = AREAS_MINIMAS.get(nodo.tipo)
        if area_min is not None and area_min > 0:
            total_checks += 1
            if nodo.area_m2 < area_min:
                violaciones += 1

        # Pasillo: ancho mínimo 1.20m
        if nodo.tipo == "pasillo":
            total_checks += 1
            ancho_real = min(nodo.ancho, nodo.alto)  # menor dimensión = ancho
            if ancho_real < CIRCULACION_MINIMA:
                violaciones += 1

    if total_checks == 0:
        return 0.0

    return violaciones / total_checks


# ─── Loss similitud (contrastiva) ────────────────────────────────────

def loss_similitud(
    v_pred: list[float],
    v_pos: list[float],
    v_neg: list[float],
    margen: float = 0.2,
) -> float:
    """
    Loss contrastiva:
    - v_pred: embedding del plan generado
    - v_pos:  embedding de un plan aprobado por el arquitecto (debe estar cerca)
    - v_neg:  embedding de un plan rechazado o muy diferente (debe estar lejos)
    - margen: distancia mínima que debe haber entre pred-pos y pred-neg

    Loss = max(0, sim(pred, neg) - sim(pred, pos) + margen)
    """
    sim_pos = cosine_similarity(v_pred, v_pos)
    sim_neg = cosine_similarity(v_pred, v_neg)
    return max(0.0, sim_neg - sim_pos + margen)


# ─── Loss de corrección ──────────────────────────────────────────────

def loss_correccion(
    g: SpatialGraph,
    correcciones: list[dict],
    peso: float = 2.0,
) -> float:
    """
    Amplifica la pérdida para los recintos que el arquitecto ha corregido
    frecuentemente. Cuantas más correcciones, más importante es ese recinto.

    correcciones: lista de dicts {"recinto": str, "tipo_cambio": str, "freq": int}
    peso: multiplicador extra por corrección frecuente

    Retorna pérdida adicional proporcional a las correcciones no atendidas.
    """
    if not correcciones or not g.nodos:
        return 0.0

    # Construir mapa recinto → frecuencia de corrección
    freq_map: dict[str, int] = {}
    for c in correcciones:
        r = c.get("recinto", "")
        if r:
            freq_map[r] = freq_map.get(r, 0) + c.get("freq", 1)

    # Penalizar recintos que siguen siendo problemáticos
    # (heurística: área fuera de rango correcto)
    penalizacion = 0.0
    total_freq = sum(freq_map.values()) or 1

    for nombre, nodo in g.nodos.items():
        if nombre not in freq_map:
            continue
        freq = freq_map[nombre]
        area_min = AREAS_MINIMAS.get(nodo.tipo, 0)
        if area_min > 0 and nodo.area_m2 < area_min:
            penalizacion += (freq / total_freq) * peso

    return min(penalizacion, 1.0)


# ─── Loss total ─────────────────────────────────────────────────────

PESOS_DEFAULT = {
    "topologica":  0.30,
    "normativa":   0.40,
    "similitud":   0.20,
    "correccion":  0.10,
}


def loss_total(
    g: SpatialGraph,
    v_pos: list[float] | None = None,
    v_neg: list[float] | None = None,
    correcciones: list[dict] | None = None,
    pesos: dict[str, float] | None = None,
) -> dict[str, float]:
    """
    Loss total ponderada.

    Retorna dict con:
    - 'total':      pérdida combinada [0, 1]
    - 'topologica': componente topológica
    - 'normativa':  componente normativa
    - 'similitud':  componente contrastiva (0 si no hay v_pos/v_neg)
    - 'correccion': componente de corrección (0 si no hay correcciones)
    """
    w = pesos or PESOS_DEFAULT

    lt = loss_topologica(g)
    ln = loss_normativa(g)

    ls = 0.0
    if v_pos and v_neg:
        v_pred = encode_graph(g)
        ls = loss_similitud(v_pred, v_pos, v_neg)

    lc = 0.0
    if correcciones:
        lc = loss_correccion(g, correcciones)

    total = (
        w.get("topologica", 0) * lt +
        w.get("normativa",  0) * ln +
        w.get("similitud",  0) * ls +
        w.get("correccion", 0) * lc
    )

    return {
        "total":      round(total, 4),
        "topologica": round(lt,    4),
        "normativa":  round(ln,    4),
        "similitud":  round(ls,    4),
        "correccion": round(lc,    4),
    }


# ─── Smoke test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    plan_bueno = {
        "grid": {"ancho_m": 10, "alto_m": 8},
        "recintos": [
            {"nombre": "SALA_COMEDOR", "fila": 5, "col": 0, "ancho": 10, "alto": 3},
            {"nombre": "COCINA",       "fila": 0, "col": 5, "ancho": 5,  "alto": 5},
            {"nombre": "DORMITORIO_PRINCIPAL", "fila": 0, "col": 0, "ancho": 5, "alto": 5},
        ],
        "puertas": [{"tipo": "exterior", "recinto": "SALA_COMEDOR", "lado": "sur", "ancho": 1.1}],
    }
    plan_malo = {
        "grid": {"ancho_m": 5, "alto_m": 4},
        "recintos": [
            {"nombre": "DORMITORIO_PRINCIPAL", "fila": 0, "col": 0, "ancho": 2, "alto": 2},  # muy pequeño
            {"nombre": "COCINA",               "fila": 0, "col": 2, "ancho": 1, "alto": 2},  # muy pequeño
        ],
        "puertas": [],  # sin acceso exterior
    }

    g_bueno = SpatialGraph.from_json(plan_bueno)
    g_malo  = SpatialGraph.from_json(plan_malo)

    print("=== Plan BUENO ===")
    lt_b = loss_total(g_bueno)
    for k, v in lt_b.items():
        print(f"  {k}: {v}")

    print("\n=== Plan MALO ===")
    lt_m = loss_total(g_malo)
    for k, v in lt_m.items():
        print(f"  {k}: {v}")

    print(f"\nLoss bueno={lt_b['total']:.4f}  malo={lt_m['total']:.4f}")
    assert lt_b["total"] < lt_m["total"], "El plan bueno debería tener menos loss"
    print("✓ Loss total correctamente ordenada")
