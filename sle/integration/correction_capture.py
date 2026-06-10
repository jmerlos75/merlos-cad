"""
sle/integration/correction_capture.py
======================================
Captura automática de correcciones del arquitecto.

Cuando el arquitecto aprueba/rechaza un plan o lo modifica manualmente,
este módulo detecta los cambios y los registra en la Memoria del SLE
para enriquecer el aprendizaje.

Uso principal:
  - Llamado desde la app de escritorio cuando el arquitecto modifica un plan
  - Llamado desde el MCP bridge cuando detecta cambios en AutoCAD
  - Puede usarse standalone para registrar correcciones manualmente

Tipos de corrección detectados:
  - 'movido':         recinto cambió de posición
  - 'redimensionado': recinto cambió de tamaño
  - 'eliminado':      recinto que estaba no está
  - 'añadido':        recinto nuevo que no estaba
  - 'puerta_añadida': nueva puerta/conexión
  - 'puerta_eliminada': puerta eliminada
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sle.core.memory import Memoria
from sle.core.spatial_graph import SpatialGraph

logger = logging.getLogger(__name__)


# ─── Estructuras ─────────────────────────────────────────────────────

@dataclass
class Correccion:
    """Representa un cambio detectado entre dos versiones de un plan."""
    tipo: str                       # 'movido' | 'redimensionado' | 'eliminado' | 'añadido' | ...
    recinto: str | None = None
    antes: dict | None = None
    despues: dict | None = None
    nota: str = ""

    def __str__(self) -> str:
        partes = [f"[{self.tipo}]"]
        if self.recinto:
            partes.append(self.recinto)
        if self.nota:
            partes.append(f"— {self.nota}")
        return " ".join(partes)


# ─── Detector de diferencias ─────────────────────────────────────────

def detectar_correcciones(
    plan_antes: dict,
    plan_despues: dict,
) -> list[Correccion]:
    """
    Compara dos versiones de un plan y devuelve la lista de correcciones.

    Los planes tienen formato:
    {
      "recintos": [{"nombre": ..., "fila": ..., "col": ..., "ancho": ..., "alto": ...}],
      "puertas":  [...]
    }
    """
    correcciones: list[Correccion] = []

    # Indexar recintos por nombre
    recintos_a: dict[str, dict] = {
        r["nombre"]: r for r in plan_antes.get("recintos", [])
    }
    recintos_b: dict[str, dict] = {
        r["nombre"]: r for r in plan_despues.get("recintos", [])
    }

    # Eliminados
    for nombre, rec_a in recintos_a.items():
        if nombre not in recintos_b:
            correcciones.append(Correccion(
                tipo="eliminado",
                recinto=nombre,
                antes=rec_a,
                nota=f"recinto eliminado del plan",
            ))

    # Añadidos
    for nombre, rec_b in recintos_b.items():
        if nombre not in recintos_a:
            correcciones.append(Correccion(
                tipo="añadido",
                recinto=nombre,
                despues=rec_b,
                nota=f"recinto nuevo",
            ))

    # Modificados (movidos / redimensionados)
    for nombre in recintos_a:
        if nombre not in recintos_b:
            continue
        ra = recintos_a[nombre]
        rb = recintos_b[nombre]

        pos_cambio = (ra.get("fila") != rb.get("fila") or ra.get("col") != rb.get("col"))
        dim_cambio = (ra.get("ancho") != rb.get("ancho") or ra.get("alto") != rb.get("alto"))

        if pos_cambio and dim_cambio:
            correcciones.append(Correccion(
                tipo="movido_y_redimensionado",
                recinto=nombre,
                antes=ra, despues=rb,
                nota=f"posición ({ra.get('fila')},{ra.get('col')})→({rb.get('fila')},{rb.get('col')}) "
                     f"tamaño {ra.get('ancho')}×{ra.get('alto')}→{rb.get('ancho')}×{rb.get('alto')}",
            ))
        elif pos_cambio:
            correcciones.append(Correccion(
                tipo="movido",
                recinto=nombre,
                antes=ra, despues=rb,
                nota=f"({ra.get('fila')},{ra.get('col')}) → ({rb.get('fila')},{rb.get('col')})",
            ))
        elif dim_cambio:
            correcciones.append(Correccion(
                tipo="redimensionado",
                recinto=nombre,
                antes=ra, despues=rb,
                nota=f"{ra.get('ancho')}×{ra.get('alto')}m → {rb.get('ancho')}×{rb.get('alto')}m",
            ))

    # Diferencias en puertas
    puertas_a = _normalizar_puertas(plan_antes.get("puertas", []))
    puertas_b = _normalizar_puertas(plan_despues.get("puertas", []))

    for k in puertas_a - puertas_b:
        correcciones.append(Correccion(
            tipo="puerta_eliminada",
            nota=f"conexión eliminada: {k}",
        ))
    for k in puertas_b - puertas_a:
        correcciones.append(Correccion(
            tipo="puerta_añadida",
            nota=f"conexión añadida: {k}",
        ))

    return correcciones


def _normalizar_puertas(puertas: list[dict]) -> set[str]:
    """Convierte lista de puertas en set de strings para comparación."""
    out = set()
    for p in puertas:
        if p.get("tipo") == "entre_recintos":
            par = tuple(sorted([p.get("recinto1", ""), p.get("recinto2", "")]))
            out.add(f"INT:{par[0]}↔{par[1]}")
        elif p.get("tipo") == "exterior":
            out.add(f"EXT:{p.get('recinto','')}:{p.get('lado','')}")
    return out


# ─── Capturador automático ───────────────────────────────────────────

class CapturaCorrecciones:
    """
    Registra correcciones en la Memoria del SLE.

    Flujo típico:
      1. El motor genera un plan (plan_generado)
      2. El arquitecto modifica el plan (plan_corregido)
      3. CapturaCorrecciones.registrar(...) guarda el plan nuevo y
         las diferencias detectadas en la base de datos

    La Memoria acumula estas señales para alimentar el entrenamiento.
    """

    def __init__(self, memoria: Memoria | None = None):
        self.memoria = memoria or Memoria()

    def registrar(
        self,
        plan_generado: dict,
        plan_corregido: dict,
        prompt_original: str,
        score_inicial: int = 0,
        score_final: int | None = None,
        nota_general: str = "",
    ) -> dict:
        """
        Registra un par (plan_generado, plan_corregido) en la Memoria.

        1. Guarda el plan corregido como proyecto aprobado
        2. Detecta y registra las correcciones individuales
        3. Retorna resumen de lo registrado
        """
        correcciones = detectar_correcciones(plan_generado, plan_corregido)

        # Si el arquitecto corrigió algo y no dio score, asumir que es bueno
        score = score_final if score_final is not None else (
            min(95, score_inicial + 10) if correcciones else score_inicial
        )

        # Guardar el plan corregido (aprobado)
        proj_id = self.memoria.guardar_proyecto(
            plan_corregido,
            prompt_original=prompt_original,
            score=score,
            aprobado=True,
        )

        # Guardar cada corrección individual
        ids_corr = []
        for c in correcciones:
            cid = self.memoria.guardar_correccion(
                proyecto_id=proj_id,
                tipo_cambio=c.tipo,
                recinto=c.recinto,
                json_antes=c.antes,
                json_despues=c.despues,
                nota=c.nota or nota_general,
            )
            ids_corr.append(cid)

        logger.info(
            f"Capturadas {len(correcciones)} correcciones → proyecto_id={proj_id}"
        )

        return {
            "proyecto_id": proj_id,
            "score": score,
            "n_correcciones": len(correcciones),
            "correcciones": [str(c) for c in correcciones],
            "ids_correcciones": ids_corr,
        }

    def registrar_aprobacion_directa(
        self,
        plan: dict,
        prompt: str,
        score: int = 90,
    ) -> int:
        """
        Registra un plan aprobado directamente (sin comparar con versión anterior).
        Útil cuando el arquitecto aprueba sin modificaciones.
        """
        proj_id = self.memoria.guardar_proyecto(
            plan,
            prompt_original=prompt,
            score=score,
            aprobado=True,
        )
        logger.info(f"Plan aprobado registrado → proyecto_id={proj_id}")
        return proj_id

    def top_patrones_correccion(self, n: int = 10) -> list[dict]:
        """
        Retorna las correcciones más frecuentes — útil para mejorar el generador.
        """
        return self.memoria.top_correcciones(n)


# ─── Smoke test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db = tmp.name

    mem = Memoria(db_path=db)
    captura = CapturaCorrecciones(memoria=mem)

    plan_original = {
        "grid": {"ancho_m": 10, "alto_m": 8},
        "recintos": [
            {"nombre": "SALA",       "fila": 5, "col": 0, "ancho": 4, "alto": 3},  # ancho=4, lo correcto es 5
            {"nombre": "COCINA",     "fila": 5, "col": 4, "ancho": 6, "alto": 3},
            {"nombre": "DORMITORIO", "fila": 0, "col": 0, "ancho": 5, "alto": 5},
            {"nombre": "BANO",       "fila": 0, "col": 5, "ancho": 5, "alto": 5},
        ],
        "puertas": [],
    }

    plan_corregido = {
        "grid": {"ancho_m": 10, "alto_m": 8},
        "recintos": [
            {"nombre": "SALA",       "fila": 5, "col": 0, "ancho": 5, "alto": 3},  # corregido
            {"nombre": "COCINA",     "fila": 5, "col": 5, "ancho": 5, "alto": 3},  # ajustado
            {"nombre": "DORMITORIO", "fila": 0, "col": 0, "ancho": 5, "alto": 5},
            {"nombre": "BANO",       "fila": 0, "col": 5, "ancho": 5, "alto": 5},
        ],
        "puertas": [{"tipo": "exterior", "recinto": "SALA", "lado": "sur", "ancho": 1.1}],
    }

    resultado = captura.registrar(
        plan_generado=plan_original,
        plan_corregido=plan_corregido,
        prompt_original="casa de 1 dormitorio 80m² sala-comedor integrado",
        score_inicial=75,
    )

    print(f"Resultado de captura:")
    for k, v in resultado.items():
        print(f"  {k}: {v}")

    print(f"\nTop patrones: {captura.top_patrones_correccion(5)}")
