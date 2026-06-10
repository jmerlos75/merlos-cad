"""
Spatial Learning Engine — Estudio Merlos AI
============================================
Motor de razonamiento espacial arquitectónico con memoria persistente.

Fase 1 (completa):
  - Memoria + Grafo espacial + RAG context
  - Encoder de grafos a vectores
  - Validador normativa CR
  - Loss functions arquitectónicas
  - Trainer (ajuste de pesos sin PyTorch)
  - Parser de datasets (SLE JSON, HouseGAN, CSV)
  - Integration bridge con MCP AutoCAD
  - Captura automática de correcciones del arquitecto
  - API REST FastAPI para la app de escritorio

Fase 2 (stubs): GNN con torch_geometric
"""

__version__ = "0.1.0"

from sle.core.memory import Memoria
from sle.core.spatial_graph import SpatialGraph
from sle.core.rag_context import obtener_contexto_rag
from sle.core.encoder import encode_graph, cosine_similarity
from sle.core.validator import validar_plan
from sle.core.reglas_merlos import (
    analizar_reglas_merlos,
    texto_analisis_merlos,
    guia_orientacion_para_prompt,
    clasificar_banos,
)
from sle.core.programa_arquitectonico import (
    generar_programa,
    texto_programa,
    texto_programa_para_prompt,
    ProgramaArquitectonico,
    PerfilCliente,
)

__all__ = [
    # Core
    "Memoria",
    "SpatialGraph",
    "obtener_contexto_rag",
    "encode_graph",
    "cosine_similarity",
    "validar_plan",
    # Reglas Merlos
    "analizar_reglas_merlos",
    "texto_analisis_merlos",
    "guia_orientacion_para_prompt",
    "clasificar_banos",
    # Programa arquitectónico
    "generar_programa",
    "texto_programa",
    "texto_programa_para_prompt",
    "ProgramaArquitectonico",
    "PerfilCliente",
]
