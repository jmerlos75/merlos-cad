"""
sle/core/encoder.py
===================
Codifica un SpatialGraph en vectores numéricos para comparación y
futura entrada a modelos de ML.

Genera dos tipos de representaciones:
1. `encode_graph(g)` → vector plano (numpy array) con features globales + topolología
2. `encode_for_gnn(g)` → dict con matrices de nodos/aristas listas para PyTorch Geometric
   (solo disponible si torch_geometric está instalado; de lo contrario retorna dicts puros)

La codificación es estable: dos grafos con la misma topología y
proporciones similares producen vectores cercanos en coseno.

Sin dependencias obligatorias — numpy es la única dependencia opcional.
"""
from __future__ import annotations

import math
from typing import Any

from sle.core.spatial_graph import SpatialGraph, NodoEspacial


# ─── Constantes de codificación ─────────────────────────────────────

# Tipos de recinto → índice one-hot (11 tipos)
TIPOS_IDX: dict[str, int] = {
    "sala": 0,
    "comedor": 1,
    "sala_comedor": 2,
    "cocina": 3,
    "dormitorio_principal": 4,
    "dormitorio": 5,
    "bano": 6,
    "pasillo": 7,
    "cochera": 8,
    "lavanderia": 9,
    "estudio": 10,
    "cuarto_servicio": 11,
    "otro": 12,
}
N_TIPOS = len(TIPOS_IDX)

# Zonas → índice
ZONAS_IDX: dict[str, int] = {
    "publica": 0,
    "privada": 1,
    "servicio": 2,
    "circulacion": 3,
    "otro": 4,
}
N_ZONAS = len(ZONAS_IDX)

# Tipos de arista
ARISTAS_IDX: dict[str, int] = {
    "adyacente": 0,
    "puerta": 1,
    "exterior": 2,
}

# Dimensión total del vector de grafo (features globales)
# = distribución de tipos (N_TIPOS) + distribución de zonas (N_ZONAS)
#   + métricas escalares (8)
DIM_VECTOR = N_TIPOS + N_ZONAS + 8


# ─── Codificación de grafo completo ─────────────────────────────────

def encode_graph(g: SpatialGraph) -> list[float]:
    """
    Codifica un SpatialGraph en un vector de floats de dimensión fija.

    Dimensión = N_TIPOS + N_ZONAS + 8 = 13 + 5 + 8 = 26

    Features incluidas:
    - Distribución normalizada de tipos de recinto (13D)
    - Distribución normalizada de zonas (5D)
    - Métricas escalares normalizadas (8D):
        0: n_recintos / 15
        1: n_dormitorios / 5
        2: n_banos / 4
        3: n_puertas_interiores / n_recintos
        4: n_accesos_exteriores / 3
        5: área construida / área_grid
        6: n_recintos_aislados / n_recintos
        7: tiene_pasillo (0/1)
    """
    n = len(g.nodos)
    if n == 0:
        return [0.0] * DIM_VECTOR

    a = g.analizar()
    tipos = a["tipos"]
    zonas = a["zonas"]

    # 1) Distribución de tipos (normalizada por n)
    tipo_vec = [0.0] * N_TIPOS
    for t, cnt in tipos.items():
        idx = TIPOS_IDX.get(t, TIPOS_IDX["otro"])
        tipo_vec[idx] += cnt / n

    # 2) Distribución de zonas (normalizada por n)
    zona_vec = [0.0] * N_ZONAS
    for z, cnt in zonas.items():
        idx = ZONAS_IDX.get(z, ZONAS_IDX["otro"])
        zona_vec[idx] += cnt / n

    # 3) Métricas escalares
    n_dorm = a["n_dormitorios"]
    n_ban = a["n_banos"]
    n_pint = a["n_puertas_interiores"]
    n_pext = a["n_accesos_exteriores"]
    n_aisl = len(a["recintos_aislados"])
    area_c = a["area_construida_m2"]
    area_g = a["area_grid_m2"] or 1.0
    tiene_pasillo = int(bool(tipos.get("pasillo")))

    escalares = [
        min(n / 15.0, 1.0),
        min(n_dorm / 5.0, 1.0),
        min(n_ban / 4.0, 1.0),
        min(n_pint / max(n, 1), 1.0),
        min(n_pext / 3.0, 1.0),
        min(area_c / area_g, 1.0),
        n_aisl / max(n, 1),
        float(tiene_pasillo),
    ]

    return tipo_vec + zona_vec + escalares


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Similitud coseno entre dos vectores de codificación."""
    dot = sum(a * b for a, b in zip(v1, v2))
    m1 = math.sqrt(sum(a * a for a in v1))
    m2 = math.sqrt(sum(b * b for b in v2))
    if m1 == 0 or m2 == 0:
        return 0.0
    return dot / (m1 * m2)


# ─── Codificación por nodo (para GNN) ───────────────────────────────

def encode_node(nodo: NodoEspacial, ancho_grid: float, alto_grid: float) -> list[float]:
    """
    Codifica un NodoEspacial en un vector de features por nodo.

    Dimensión = N_TIPOS + N_ZONAS + 6 = 13 + 5 + 6 = 24

    Features por nodo:
    - One-hot del tipo (13D)
    - One-hot de la zona (5D)
    - Posición normalizada: col/ancho_grid, fila/alto_grid (2D)
    - Dimensiones normalizadas: ancho/ancho_grid, alto/alto_grid (2D)
    - Área normalizada: area_m2 / (ancho_grid * alto_grid) (1D)
    - Relación aspecto: min(ancho,alto)/max(ancho,alto) (1D)
    """
    tipo_oh = [0.0] * N_TIPOS
    tipo_oh[TIPOS_IDX.get(nodo.tipo, TIPOS_IDX["otro"])] = 1.0

    zona_oh = [0.0] * N_ZONAS
    zona_oh[ZONAS_IDX.get(nodo.zona, ZONAS_IDX["otro"])] = 1.0

    ag = ancho_grid or 1.0
    hg = alto_grid or 1.0
    area_grid = ag * hg or 1.0

    ancho_n = nodo.ancho or 0
    alto_n  = nodo.alto  or 0

    pos = [nodo.col / ag, nodo.fila / hg]
    dims = [ancho_n / ag, alto_n / hg]
    area_n = (ancho_n * alto_n) / area_grid
    aspecto = min(ancho_n, alto_n) / max(ancho_n, alto_n, 1)

    return tipo_oh + zona_oh + pos + dims + [area_n, aspecto]


# ─── Representación para GNN (torch-free) ───────────────────────────

def encode_for_gnn(g: SpatialGraph) -> dict[str, Any]:
    """
    Construye matrices de features para un Graph Neural Network.

    Retorna un dict con:
    - "node_features": lista de vectores (1 por nodo)
    - "edge_index":    lista de pares [i, j] (índices de nodos)
    - "edge_features": lista de vectores de arista
    - "node_names":    lista de nombres en orden
    - "global_features": vector global del grafo

    Si torch y torch_geometric están disponibles, los valores son tensores.
    De lo contrario son listas Python puras.
    """
    nombres = sorted(g.nodos.keys())
    idx_map = {n: i for i, n in enumerate(nombres)}

    ag = g.ancho_m or 1.0
    hg = g.alto_m  or 1.0

    # Matrices de nodos
    node_features = [encode_node(g.nodos[n], ag, hg) for n in nombres]

    # Matrices de aristas
    edge_index: list[list[int]] = []
    edge_features: list[list[float]] = []

    for ar in g.aristas:
        if ar.tipo == "exterior":
            continue  # aristas al exterior no entran al GNN
        if ar.nodo_b is None or ar.nodo_a not in idx_map or ar.nodo_b not in idx_map:
            continue
        i = idx_map[ar.nodo_a]
        j = idx_map[ar.nodo_b]
        tipo_idx = ARISTAS_IDX.get(ar.tipo, 0)
        feat = [
            float(tipo_idx),
            ar.ancho / (ag or 1.0),  # ancho normalizado
        ]
        # Aristas bidireccionales
        edge_index.append([i, j])
        edge_features.append(feat)
        edge_index.append([j, i])
        edge_features.append(feat)

    global_features = encode_graph(g)

    result: dict[str, Any] = {
        "node_features": node_features,
        "edge_index": edge_index,
        "edge_features": edge_features,
        "node_names": nombres,
        "global_features": global_features,
        "n_nodes": len(nombres),
        "n_edges": len(edge_index) // 2,
    }

    # Intentar convertir a tensores si torch disponible
    try:
        import torch
        result["node_features"]  = torch.tensor(node_features, dtype=torch.float)
        result["edge_features"]  = torch.tensor(edge_features, dtype=torch.float) if edge_features else torch.zeros((0, 2))
        result["edge_index"]     = torch.tensor(edge_index, dtype=torch.long).t().contiguous() if edge_index else torch.zeros((2, 0), dtype=torch.long)
        result["global_features"] = torch.tensor([global_features], dtype=torch.float)
    except ImportError:
        pass  # sin torch: retorna listas puras

    return result


# ─── Smoke test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    plan = {
        "grid": {"ancho_m": 10, "alto_m": 8},
        "recintos": [
            {"nombre": "SALA",       "fila": 5, "col": 0, "ancho": 5, "alto": 3},
            {"nombre": "COCINA",     "fila": 5, "col": 5, "ancho": 5, "alto": 3},
            {"nombre": "DORMITORIO", "fila": 0, "col": 0, "ancho": 5, "alto": 5},
            {"nombre": "BANO",       "fila": 0, "col": 5, "ancho": 5, "alto": 5},
        ],
        "puertas": [
            {"tipo": "exterior",       "recinto": "SALA",  "lado": "sur",  "ancho": 1.1},
            {"tipo": "entre_recintos", "recinto1": "SALA", "recinto2": "COCINA", "ancho": 1.0},
        ],
    }
    from sle.core.spatial_graph import SpatialGraph
    g = SpatialGraph.from_json(plan)
    vec = encode_graph(g)
    print(f"Vector global dim={len(vec)}: {[round(x, 3) for x in vec]}")

    gnn = encode_for_gnn(g)
    print(f"GNN: {gnn['n_nodes']} nodos, {gnn['n_edges']} aristas")
    print(f"Node feature dim: {len(gnn['node_features'][0]) if gnn['node_features'] else 0}")

    # Similitud coseno entre dos variaciones del mismo plan
    plan2 = {**plan, "recintos": plan["recintos"][:3]}  # sin baño
    g2 = SpatialGraph.from_json(plan2)
    v2 = encode_graph(g2)
    sim = cosine_similarity(vec, v2)
    print(f"Similitud coseno plan vs plan-sin-bano: {sim:.3f}")
