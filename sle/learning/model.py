"""
sle/learning/model.py
=====================
Modelos de ML para el Spatial Learning Engine.

Fase actual (Fase 1): SimilitudModel — comparación de grafos por
vector de embedding, sin GNN. Funciona sin PyTorch.

Fase futura (Fase 2): SpatialGNN — Graph Neural Network con
torch_geometric para aprender representaciones topológicas complejas.

La clase base `SLEModel` define la interfaz común para ambas fases.

Dependencias opcionales:
- Phase 1: ninguna (usa encoder.py puro Python)
- Phase 2: torch >= 2.0, torch_geometric (NO requeridos en Fase 1)
"""
from __future__ import annotations

import json
import math
import pickle
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from sle.core.encoder import (
    DIM_VECTOR,
    encode_graph,
    encode_for_gnn,
    cosine_similarity,
)
from sle.core.spatial_graph import SpatialGraph


# ─── Interfaz común ──────────────────────────────────────────────────

class SLEModel(ABC):
    """Interfaz base para todos los modelos del SLE."""

    @abstractmethod
    def predecir_similitud(self, g1: SpatialGraph, g2: SpatialGraph) -> float:
        """Retorna similitud [0, 1] entre dos grafos."""
        ...

    @abstractmethod
    def guardar(self, path: Path | str) -> None:
        """Serializa el modelo a disco."""
        ...

    @classmethod
    @abstractmethod
    def cargar(cls, path: Path | str) -> "SLEModel":
        """Carga el modelo desde disco."""
        ...


# ─── Fase 1: Similitud por coseno ────────────────────────────────────

class SimilitudModel(SLEModel):
    """
    Modelo de similitud de grafos basado en vectores de encoding.

    No tiene parámetros entrenables en Fase 1 — usa pesos fijos del encoder.
    En Fase 2 se pueden aprender pesos de importancia por tipo de feature.

    Guarda/carga en JSON simple.
    """

    def __init__(self):
        # Pesos por dimensión del vector de encoding
        # (1.0 = sin modificar, se ajustan con el trainer)
        self.pesos: list[float] = [1.0] * DIM_VECTOR
        self.version: str = "1.0"
        self.entrenado: bool = False
        self.metricas: dict = {}

    def predecir_similitud(self, g1: SpatialGraph, g2: SpatialGraph) -> float:
        """Similitud coseno ponderada entre los vectores de los dos grafos."""
        v1 = self._codificar(g1)
        v2 = self._codificar(g2)
        return cosine_similarity(v1, v2)

    def _codificar(self, g: SpatialGraph) -> list[float]:
        """Aplica pesos al vector de encoding."""
        v = encode_graph(g)
        return [x * p for x, p in zip(v, self.pesos)]

    def ranking(
        self,
        query: SpatialGraph,
        candidatos: list[SpatialGraph],
        n: int = 5,
    ) -> list[tuple[int, float]]:
        """
        Rankea candidatos por similitud con query.
        Retorna lista de (índice, similitud) ordenada de mayor a menor.
        """
        scores = [(i, self.predecir_similitud(query, c)) for i, c in enumerate(candidatos)]
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:n]

    def guardar(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.version,
            "pesos": self.pesos,
            "entrenado": self.entrenado,
            "metricas": self.metricas,
        }
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def cargar(cls, path: Path | str) -> "SimilitudModel":
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        m = cls()
        m.pesos     = data.get("pesos", m.pesos)
        m.version   = data.get("version", m.version)
        m.entrenado = data.get("entrenado", False)
        m.metricas  = data.get("metricas", {})
        return m


# ─── Fase 2 (stub): SpatialGNN ───────────────────────────────────────

class SpatialGNN(SLEModel):
    """
    STUB — Graph Neural Network para Fase 2.

    Requiere: torch >= 2.0, torch_geometric
    Actualmente lanza NotImplementedError si torch no está disponible.

    Arquitectura propuesta:
    - GATConv x 3 capas (Graph Attention Network)
    - Readout: mean pooling global
    - MLP de proyección a embedding 64D
    - Loss: contrastiva (pares positivos/negativos de plantas)

    Para activar en Fase 2:
        pip install torch torch_geometric
    """

    def __init__(self, hidden_dim: int = 64, n_layers: int = 3):
        self.hidden_dim = hidden_dim
        self.n_layers = n_layers
        self._modelo = None
        self._intentar_inicializar()

    def _intentar_inicializar(self):
        try:
            import torch
            import torch.nn as nn
            try:
                from torch_geometric.nn import GATConv, global_mean_pool
                self._modelo = self._construir_gnn()
            except ImportError:
                pass  # torch_geometric no disponible
        except ImportError:
            pass  # torch no disponible

    def _construir_gnn(self):
        """Construye el GNN con PyTorch Geometric."""
        import torch
        import torch.nn as nn
        from torch_geometric.nn import GATConv, global_mean_pool

        from sle.core.encoder import DIM_VECTOR

        # Dimensiones de entrada: features de nodo (24D definido en encoder)
        DIM_NODO = 24  # N_TIPOS(13) + N_ZONAS(5) + 6 escalares

        class GraphModel(nn.Module):
            def __init__(self, in_channels, hidden, out_channels, n_layers):
                super().__init__()
                self.convs = nn.ModuleList()
                self.convs.append(GATConv(in_channels, hidden, heads=4, concat=False))
                for _ in range(n_layers - 2):
                    self.convs.append(GATConv(hidden, hidden, heads=4, concat=False))
                self.convs.append(GATConv(hidden, out_channels, heads=1, concat=False))
                self.bn = nn.ModuleList([nn.BatchNorm1d(hidden) for _ in range(n_layers - 1)])
                self.relu = nn.ReLU()

            def forward(self, x, edge_index, batch=None):
                for i, conv in enumerate(self.convs[:-1]):
                    x = self.relu(self.bn[i](conv(x, edge_index)))
                x = self.convs[-1](x, edge_index)
                if batch is not None:
                    from torch_geometric.nn import global_mean_pool
                    x = global_mean_pool(x, batch)
                return x

        return GraphModel(DIM_NODO, self.hidden_dim, self.hidden_dim, self.n_layers)

    def predecir_similitud(self, g1: SpatialGraph, g2: SpatialGraph) -> float:
        if self._modelo is None:
            # Fallback a similitud coseno simple
            v1 = encode_graph(g1)
            v2 = encode_graph(g2)
            return cosine_similarity(v1, v2)

        import torch
        d1 = encode_for_gnn(g1)
        d2 = encode_for_gnn(g2)

        with torch.no_grad():
            e1 = self._modelo(d1["node_features"], d1["edge_index"])
            e2 = self._modelo(d2["node_features"], d2["edge_index"])
            e1 = e1.mean(dim=0)
            e2 = e2.mean(dim=0)
            cos = torch.nn.functional.cosine_similarity(e1.unsqueeze(0), e2.unsqueeze(0))
            return float(cos.item())

    def guardar(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if self._modelo is not None:
            import torch
            torch.save(self._modelo.state_dict(), str(p) + ".pt")
        # Guarda config siempre
        cfg = {"hidden_dim": self.hidden_dim, "n_layers": self.n_layers}
        (p.parent / (p.stem + "_config.json")).write_text(
            json.dumps(cfg, indent=2), encoding="utf-8"
        )

    @classmethod
    def cargar(cls, path: Path | str) -> "SpatialGNN":
        p = Path(path)
        cfg_path = p.parent / (p.stem + "_config.json")
        hidden, n_layers = 64, 3
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            hidden   = cfg.get("hidden_dim", 64)
            n_layers = cfg.get("n_layers", 3)
        m = cls(hidden_dim=hidden, n_layers=n_layers)
        pt_path = Path(str(path) + ".pt")
        if pt_path.exists() and m._modelo is not None:
            import torch
            m._modelo.load_state_dict(torch.load(str(pt_path), weights_only=True))
        return m


# ─── Factory ─────────────────────────────────────────────────────────

def crear_modelo(tipo: str = "similitud") -> SLEModel:
    """
    Factory de modelos.
    tipo: 'similitud' (Fase 1, sin dependencias) | 'gnn' (Fase 2, requiere torch)
    """
    if tipo == "gnn":
        return SpatialGNN()
    return SimilitudModel()


# ─── Smoke test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    from sle.core.spatial_graph import SpatialGraph

    plan_a = {
        "grid": {"ancho_m": 10, "alto_m": 8},
        "recintos": [
            {"nombre": "SALA",        "fila": 5, "col": 0, "ancho": 5, "alto": 3},
            {"nombre": "COCINA",      "fila": 5, "col": 5, "ancho": 5, "alto": 3},
            {"nombre": "DORMITORIO",  "fila": 0, "col": 0, "ancho": 5, "alto": 5},
            {"nombre": "BANO",        "fila": 0, "col": 5, "ancho": 5, "alto": 5},
        ],
        "puertas": [{"tipo": "exterior", "recinto": "SALA", "lado": "sur", "ancho": 1.1}],
    }
    plan_b = {**plan_a}  # idéntico → similitud 1.0
    plan_c = {
        "grid": {"ancho_m": 20, "alto_m": 15},
        "recintos": [
            {"nombre": "OFICINA",   "fila": 0, "col": 0, "ancho": 10, "alto": 5},
            {"nombre": "SALA_CONF", "fila": 0, "col": 10, "ancho": 10, "alto": 5},
            {"nombre": "BANO",      "fila": 5, "col": 0, "ancho": 5, "alto": 5},
        ],
        "puertas": [{"tipo": "exterior", "recinto": "OFICINA", "lado": "norte", "ancho": 1.1}],
    }

    ga = SpatialGraph.from_json(plan_a)
    gb = SpatialGraph.from_json(plan_b)
    gc = SpatialGraph.from_json(plan_c)

    modelo = crear_modelo("similitud")
    print(f"A vs B (idénticos): {modelo.predecir_similitud(ga, gb):.3f}")
    print(f"A vs C (diferentes): {modelo.predecir_similitud(ga, gc):.3f}")

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        ruta = tmp.name
    modelo.guardar(ruta)
    m2 = SimilitudModel.cargar(ruta)
    print(f"Modelo cargado, pesos[0]={m2.pesos[0]}")
