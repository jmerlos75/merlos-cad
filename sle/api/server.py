"""
sle/api/server.py
=================
API HTTP del Spatial Learning Engine para la app de escritorio.

Expone los servicios del SLE como endpoints REST usando FastAPI.
La app de escritorio (customtkinter) se comunica con este servidor
para guardar proyectos, buscar similares, analizar plantas, etc.

Inicio rápido:
    cd "C:\\Users\\jmerl\\OneDrive\\Documentos\\Estudio Merlos AI"
    python -m sle.api.server

O desde código:
    from sle.api.server import app
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8766)

Endpoints:
  GET  /health                → estado del servidor
  GET  /estadisticas          → estadísticas de la memoria
  POST /proyectos             → guardar proyecto aprobado
  GET  /proyectos             → listar proyectos (paginado)
  GET  /proyectos/{id}        → obtener proyecto por id
  POST /similares             → buscar proyectos similares
  POST /analizar              → analizar un plan (topología + normativa)
  POST /contexto-rag          → obtener contexto RAG para un prompt
  POST /correcciones          → registrar corrección del arquitecto
  GET  /correcciones/patrones → patrones de corrección más frecuentes
  POST /entrenar              → lanzar entrenamiento del modelo
  GET  /modelo/estado         → estado del modelo de similitud

Requiere: fastapi, uvicorn
    pip install fastapi uvicorn
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── Lazy import de FastAPI ──────────────────────────────────────────

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
    _FASTAPI_OK = True
except ImportError:
    _FASTAPI_OK = False
    FastAPI = object
    BaseModel = object

from sle.core.memory import Memoria
from sle.core.spatial_graph import SpatialGraph
from sle.core.rag_context import obtener_contexto_rag
from sle.core.validator import validar_plan
from sle.core.encoder import encode_graph
from sle.learning.loss_functions import loss_total
from sle.learning.model import SimilitudModel
from sle.integration.correction_capture import CapturaCorrecciones


# ─── Modelos Pydantic ────────────────────────────────────────────────

if _FASTAPI_OK:
    class PlanRequest(BaseModel):
        plan: dict
        prompt_original: str = ""
        score: int = 0
        aprobado: bool = True

    class SimilarRequest(BaseModel):
        prompt: str
        n: int = Field(default=3, ge=1, le=10)
        umbral: float = Field(default=0.5, ge=0.0, le=1.0)

    class AnalizarRequest(BaseModel):
        plan: dict

    class ContextoRAGRequest(BaseModel):
        prompt: str
        n: int = Field(default=2, ge=1, le=5)

    class CorreccionRequest(BaseModel):
        plan_generado: dict
        plan_corregido: dict
        prompt_original: str
        score_inicial: int = 0
        score_final: int | None = None
        nota: str = ""

    class EntrenarRequest(BaseModel):
        n_epochs: int = Field(default=10, ge=1, le=100)
        max_pares: int = Field(default=50, ge=5, le=500)


# ─── Factory de la app ────────────────────────────────────────────────

def crear_app(db_path: str | Path | None = None) -> Any:
    """
    Crea y configura la aplicación FastAPI del SLE.
    db_path: ruta a la base de datos SQLite. Por defecto usa sle/data/correcciones.db
    """
    if not _FASTAPI_OK:
        raise ImportError(
            "FastAPI y uvicorn son necesarios para el servidor SLE.\n"
            "Instálalos con: pip install fastapi uvicorn"
        )

    memoria  = Memoria(db_path=db_path) if db_path else Memoria()
    captura  = CapturaCorrecciones(memoria=memoria)
    modelo_path = Path(__file__).parent.parent / "data" / "modelo_similitud.json"
    modelo   = SimilitudModel.cargar(modelo_path) if modelo_path.exists() else SimilitudModel()

    app = FastAPI(
        title="Spatial Learning Engine — Estudio Merlos AI",
        description="API del motor de razonamiento espacial arquitectónico",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Endpoints ────────────────────────────────────────────────────

    @app.get("/health")
    def health():
        stats = memoria.estadisticas()
        return {
            "ok": True,
            "version": "0.1.0",
            "proyectos": stats["n_proyectos"],
            "modelo_entrenado": modelo.entrenado,
        }

    @app.get("/estadisticas")
    def estadisticas():
        return memoria.estadisticas()

    @app.post("/proyectos")
    def guardar_proyecto(req: PlanRequest):
        try:
            pid = memoria.guardar_proyecto(
                req.plan,
                prompt_original=req.prompt_original,
                score=req.score,
                aprobado=req.aprobado,
            )
            return {"proyecto_id": pid}
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/proyectos")
    def listar_proyectos(limite: int = 50):
        return {"proyectos": memoria.listar_proyectos(limite=limite)}

    @app.get("/proyectos/{proyecto_id}")
    def obtener_proyecto(proyecto_id: int):
        p = memoria.obtener_proyecto(proyecto_id)
        if p is None:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        return p

    @app.post("/similares")
    def buscar_similares(req: SimilarRequest):
        similares = memoria.buscar_proyectos_similares(
            req.prompt, n=req.n, umbral=req.umbral
        )
        return {"similares": similares, "n_encontrados": len(similares)}

    @app.post("/analizar")
    def analizar_plan(req: AnalizarRequest):
        try:
            g = SpatialGraph.from_json(req.plan)
            analisis = g.analizar()
            topo_val = g.validate_topology()
            normativa = validar_plan(req.plan)
            loss = loss_total(g)
            return {
                "analisis": analisis,
                "topologia": topo_val,
                "normativa": normativa,
                "loss": loss,
                "encoding_dim": len(encode_graph(g)),
            }
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.post("/contexto-rag")
    def contexto_rag(req: ContextoRAGRequest):
        ctx = obtener_contexto_rag(req.prompt, memoria=memoria, n=req.n)
        return {"contexto": ctx, "tiene_contexto": bool(ctx)}

    @app.post("/correcciones")
    def registrar_correccion(req: CorreccionRequest):
        try:
            resultado = captura.registrar(
                plan_generado=req.plan_generado,
                plan_corregido=req.plan_corregido,
                prompt_original=req.prompt_original,
                score_inicial=req.score_inicial,
                score_final=req.score_final,
                nota_general=req.nota,
            )
            return resultado
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/correcciones/patrones")
    def patrones_correccion(n: int = 10):
        return {"patrones": captura.top_patrones_correccion(n)}

    @app.post("/entrenar")
    def entrenar(req: EntrenarRequest):
        from sle.learning.trainer import Trainer
        trainer = Trainer(memoria=memoria, modelo=modelo)
        try:
            metricas = trainer.entrenar(
                n_epochs=req.n_epochs,
                max_pares=req.max_pares,
            )
            trainer.guardar_modelo(modelo_path)
            return {"ok": True, "metricas": metricas}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/modelo/estado")
    def estado_modelo():
        return {
            "entrenado": modelo.entrenado,
            "version": modelo.version,
            "metricas": modelo.metricas,
        }

    return app


# ─── Entry point ─────────────────────────────────────────────────────

# Instancia global (para uso con uvicorn sle.api.server:app)
app = None
try:
    app = crear_app()
except ImportError as _e:
    logger.warning(f"No se pudo crear la app FastAPI: {_e}")


def main():
    """Lanza el servidor con uvicorn."""
    try:
        import uvicorn
    except ImportError:
        print("ERROR: uvicorn no está instalado.")
        print("  pip install fastapi uvicorn")
        return

    global app
    if app is None:
        app = crear_app()

    host = os.environ.get("SLE_HOST", "127.0.0.1")
    port = int(os.environ.get("SLE_PORT", "8766"))

    print(f"Iniciando SLE API en http://{host}:{port}")
    print(f"Documentación: http://{host}:{port}/docs")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
