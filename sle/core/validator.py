"""
sle/core/validator.py
=====================
STUB — el validador real ya existe en `modulos/diseno/validador.py`.

Este archivo es un thin wrapper que re-exporta el validador existente
para uso desde el SLE, evitando duplicación de lógica.

En el futuro, este módulo puede crecer para agregar validaciones
específicas del SLE (validación topológica avanzada, reglas aprendidas
de las correcciones del arquitecto, etc.).
"""
from __future__ import annotations

from modulos.diseno.grid import Grid
from modulos.diseno.validador import validar as _validar_existente


def validar_plan(plan: dict) -> dict:
    """Wrapper que valida un plan JSON usando el validador del sistema."""
    g_info = plan.get("grid", {})
    grid = Grid(
        ancho_m=g_info.get("ancho_m", 0),
        alto_m=g_info.get("alto_m", 0),
        escala=1.0,
    )
    for rec in plan.get("recintos", []):
        try:
            grid.colocar(
                rec["nombre"],
                rec.get("fila", 0), rec.get("col", 0),
                rec.get("ancho", rec.get("ancho_celdas", 0)),
                rec.get("alto",  rec.get("alto_celdas",  0)),
            )
        except ValueError:
            pass
    return _validar_existente(grid)
