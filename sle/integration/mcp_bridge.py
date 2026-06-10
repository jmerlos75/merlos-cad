"""
sle/integration/mcp_bridge.py
==============================
Bridge entre el SLE y el MCP de AutoCAD existente.

Responsabilidades:
1. Leer el estado actual del dibujo AutoCAD (recintos, dimensiones)
2. Convertirlo al formato JSON estándar del Engine
3. Enviar sugerencias del SLE de vuelta al MCP (llamadas a herramientas)
4. Capturar correcciones cuando el arquitecto modifica el DWG

IMPORTANTE: No importa nada del MCP directamente — usa solo el protocolo
de mensajes JSON para no crear acoplamiento duro. El bridge es agnóstico
a la versión del MCP.

El bridge opera en dos modos:
- Modo simulación: trabaja con dicts Python en memoria (para tests)
- Modo MCP real: hace llamadas HTTP/JSON al endpoint del MCP server

La detección del modo es automática (si MCP_URL está en el env).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sle.core.spatial_graph import SpatialGraph
from sle.core.memory import Memoria
from sle.core.rag_context import obtener_contexto_rag
from sle.integration.correction_capture import CapturaCorrecciones, detectar_correcciones

logger = logging.getLogger(__name__)


# ─── Constantes ──────────────────────────────────────────────────────

MCP_URL_ENV = "SLE_MCP_URL"      # env var para endpoint del MCP
MCP_URL_DEFAULT = "http://localhost:8765"

# Capas AutoCAD → tipos de recinto
CAPA_A_TIPO: dict[str, str] = {
    "A-SALA":        "sala",
    "A-COMEDOR":     "comedor",
    "A-SALA-COM":    "sala_comedor",
    "A-COCINA":      "cocina",
    "A-DORM-PRINC":  "dormitorio_principal",
    "A-DORM":        "dormitorio",
    "A-BANO":        "bano",
    "A-PASILLO":     "pasillo",
    "A-COCHERA":     "cochera",
    "A-LAVANDERIA":  "lavanderia",
    "A-ESTUDIO":     "estudio",
    "A-SERVICIO":    "cuarto_servicio",
}


# ─── Estructuras de datos ─────────────────────────────────────────────

@dataclass
class EstadoDWG:
    """Estado actual del dibujo AutoCAD importado al SLE."""
    proyecto_nombre: str = "sin_nombre"
    plan: dict | None = None
    grafo: SpatialGraph | None = None
    timestamp: str = ""
    fuente: str = "simulacion"


# ─── Convertidores de formato ─────────────────────────────────────────

def autocad_entidades_a_plan(entidades: list[dict]) -> dict:
    """
    Convierte entidades de AutoCAD (polilíneas/sólidos) al plan JSON del Engine.

    Las entidades tienen el formato que retorna el MCP de AutoCAD:
    [
      {
        "tipo": "LWPOLYLINE",
        "capa": "A-SALA",
        "nombre": "SALA",
        "bbox": {"x1": 0, "y1": 0, "x2": 5000, "y2": 3000},   # en mm
      },
      ...
    ]

    Convierte mm → metros y mm → celdas de 1m.
    """
    recintos = []
    grid_xmax, grid_ymax = 0.0, 0.0

    for ent in entidades:
        capa = ent.get("capa", "")
        tipo = CAPA_A_TIPO.get(capa, "otro")
        nombre = (ent.get("nombre") or ent.get("etiqueta") or capa).upper().replace(" ", "_")

        bbox = ent.get("bbox", {})
        x1 = float(bbox.get("x1", 0)) / 1000  # mm → m
        y1 = float(bbox.get("y1", 0)) / 1000
        x2 = float(bbox.get("x2", 0)) / 1000
        y2 = float(bbox.get("y2", 0)) / 1000

        col   = round(x1)
        fila  = round(y1)
        ancho = max(1, round(x2 - x1))
        alto  = max(1, round(y2 - y1))

        grid_xmax = max(grid_xmax, x2)
        grid_ymax = max(grid_ymax, y2)

        recintos.append({
            "nombre": nombre,
            "fila": fila,
            "col": col,
            "ancho": ancho,
            "alto": alto,
            "_tipo_detectado": tipo,
        })

    return {
        "grid": {"ancho_m": round(grid_xmax), "alto_m": round(grid_ymax)},
        "recintos": recintos,
        "puertas": [],
    }


def plan_a_herramientas_mcp(plan: dict) -> list[dict]:
    """
    Convierte un plan JSON del Engine en llamadas de herramienta del MCP AutoCAD.

    Retorna lista de llamadas [{"herramienta": str, "argumentos": dict}, ...].
    Estas llamadas se envían al MCP para que dibuje la planta en AutoCAD.
    """
    llamadas = []

    grid = plan.get("grid", {})
    ancho = grid.get("ancho_m", 0)
    alto  = grid.get("alto_m", 0)

    for rec in plan.get("recintos", []):
        x1 = rec["col"]   * 1000  # m → mm
        y1 = rec["fila"]  * 1000
        x2 = (rec["col"]  + rec["ancho"]) * 1000
        y2 = (rec["fila"] + rec["alto"])  * 1000

        llamadas.append({
            "herramienta": "dibujar_recinto",
            "argumentos": {
                "nombre": rec["nombre"],
                "x1": x1, "y1": y1,
                "x2": x2, "y2": y2,
                "espesor_muro": 150,  # mm — muro bloque/concreto
            },
        })

    for p in plan.get("puertas", []):
        if p.get("tipo") == "exterior":
            llamadas.append({
                "herramienta": "colocar_puerta_exterior",
                "argumentos": {
                    "recinto": p["recinto"],
                    "lado": p.get("lado", "sur"),
                    "ancho": int(p.get("ancho", 1.1) * 1000),
                },
            })
        elif p.get("tipo") == "entre_recintos":
            llamadas.append({
                "herramienta": "colocar_puerta_interior",
                "argumentos": {
                    "recinto1": p["recinto1"],
                    "recinto2": p["recinto2"],
                    "ancho": int(p.get("ancho", 1.0) * 1000),
                },
            })

    return llamadas


# ─── Bridge principal ─────────────────────────────────────────────────

class MCPBridge:
    """
    Bridge entre SLE y MCP AutoCAD.

    En modo simulación (sin MCP real), opera sobre dicts en memoria.
    En modo real, hace llamadas HTTP al MCP server.
    """

    def __init__(
        self,
        memoria: Memoria | None = None,
        mcp_url: str | None = None,
        modo_simulacion: bool | None = None,
    ):
        self.memoria = memoria or Memoria()
        self.mcp_url = mcp_url or os.environ.get(MCP_URL_ENV, MCP_URL_DEFAULT)
        self.captura = CapturaCorrecciones(memoria=self.memoria)

        if modo_simulacion is None:
            # Auto-detectar: simulación si no hay MCP real disponible
            self._simulacion = not self._verificar_mcp()
        else:
            self._simulacion = modo_simulacion

        self._estado: EstadoDWG = EstadoDWG()
        logger.info(
            f"MCPBridge iniciado en modo {'simulación' if self._simulacion else 'MCP real'}"
        )

    def _verificar_mcp(self) -> bool:
        """Retorna True si el MCP server responde."""
        try:
            import urllib.request
            with urllib.request.urlopen(f"{self.mcp_url}/health", timeout=1) as r:
                return r.status == 200
        except Exception:
            return False

    # ── Leer estado del DWG ──────────────────────────────────────────

    def leer_estado_dwg(self, nombre_proyecto: str = "actual") -> EstadoDWG:
        """
        Lee el estado actual del dibujo en AutoCAD y lo importa al SLE.
        En modo simulación, retorna el estado en memoria.
        """
        if self._simulacion:
            if self._estado.plan:
                return self._estado
            # Estado de demo
            self._estado = EstadoDWG(
                proyecto_nombre=nombre_proyecto,
                plan={"grid": {"ancho_m": 0, "alto_m": 0}, "recintos": [], "puertas": []},
                fuente="simulacion",
            )
            return self._estado

        # Modo real: llamar al MCP
        try:
            respuesta = self._llamar_mcp("leer_entidades_planta", {})
            entidades = respuesta.get("entidades", [])
            plan = autocad_entidades_a_plan(entidades)
            grafo = SpatialGraph.from_json(plan)
            self._estado = EstadoDWG(
                proyecto_nombre=nombre_proyecto,
                plan=plan,
                grafo=grafo,
                fuente="autocad",
            )
            return self._estado
        except Exception as e:
            logger.error(f"Error al leer estado DWG: {e}")
            return self._estado

    # ── Enviar plan al DWG ───────────────────────────────────────────

    def enviar_plan_a_autocad(self, plan: dict) -> dict:
        """
        Envía un plan del SLE a AutoCAD para dibujarlo.
        Retorna {"ok": bool, "n_llamadas": int, "errores": list}.
        """
        llamadas = plan_a_herramientas_mcp(plan)

        if self._simulacion:
            logger.info(f"[SIM] Enviar {len(llamadas)} herramientas al MCP")
            self._estado.plan = plan
            self._estado.grafo = SpatialGraph.from_json(plan)
            return {"ok": True, "n_llamadas": len(llamadas), "errores": [], "modo": "simulacion"}

        errores = []
        for llamada in llamadas:
            try:
                self._llamar_mcp(llamada["herramienta"], llamada["argumentos"])
            except Exception as e:
                errores.append(str(e))

        return {
            "ok": len(errores) == 0,
            "n_llamadas": len(llamadas),
            "errores": errores,
        }

    # ── Capturar corrección ──────────────────────────────────────────

    def capturar_correccion_dwg(
        self,
        plan_nuevo: dict,
        prompt: str,
        score: int | None = None,
    ) -> dict:
        """
        Detecta diferencias entre el estado anterior y el nuevo,
        las registra como correcciones en la Memoria.
        """
        plan_viejo = self._estado.plan or {"recintos": [], "puertas": []}
        resultado = self.captura.registrar(
            plan_generado=plan_viejo,
            plan_corregido=plan_nuevo,
            prompt_original=prompt,
            score_inicial=score or 75,
        )
        # Actualizar estado
        self._estado.plan  = plan_nuevo
        self._estado.grafo = SpatialGraph.from_json(plan_nuevo)
        return resultado

    # ── Contexto RAG ─────────────────────────────────────────────────

    def obtener_contexto_para_prompt(self, prompt: str, n: int = 2) -> str:
        """
        Retorna el contexto RAG para inyectar al prompt del arquitecto.
        """
        return obtener_contexto_rag(prompt, memoria=self.memoria, n=n)

    # ── Llamada HTTP al MCP ──────────────────────────────────────────

    def _llamar_mcp(self, herramienta: str, argumentos: dict) -> dict:
        """Hace una llamada HTTP POST al MCP server."""
        import urllib.request

        payload = json.dumps({
            "tool": herramienta,
            "arguments": argumentos,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.mcp_url}/call",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))


# ─── Smoke test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db = tmp.name

    mem = Memoria(db_path=db)
    bridge = MCPBridge(memoria=mem, modo_simulacion=True)

    # Test: enviar plan
    plan = {
        "grid": {"ancho_m": 10, "alto_m": 8},
        "recintos": [
            {"nombre": "SALA",       "fila": 5, "col": 0, "ancho": 5, "alto": 3},
            {"nombre": "COCINA",     "fila": 5, "col": 5, "ancho": 5, "alto": 3},
            {"nombre": "DORMITORIO", "fila": 0, "col": 0, "ancho": 5, "alto": 5},
            {"nombre": "BANO",       "fila": 0, "col": 5, "ancho": 5, "alto": 5},
        ],
        "puertas": [{"tipo": "exterior", "recinto": "SALA", "lado": "sur", "ancho": 1.1}],
    }

    res = bridge.enviar_plan_a_autocad(plan)
    print(f"Envío plan: {res}")

    estado = bridge.leer_estado_dwg("proyecto_demo")
    print(f"Estado DWG: recintos={len(estado.plan['recintos'])}, fuente={estado.fuente}")

    ctx = bridge.obtener_contexto_para_prompt("casa 1 dormitorio 80m²")
    print(f"Contexto RAG ({len(ctx)} chars)")

    # Test: convertir entidades AutoCAD
    entidades = [
        {"capa": "A-SALA", "nombre": "SALA", "bbox": {"x1": 0, "y1": 5000, "x2": 5000, "y2": 8000}},
        {"capa": "A-COCINA", "nombre": "COCINA", "bbox": {"x1": 5000, "y1": 5000, "x2": 10000, "y2": 8000}},
    ]
    plan_conv = autocad_entidades_a_plan(entidades)
    print(f"\nConvertido AutoCAD→Plan: {plan_conv}")
