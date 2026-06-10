"""
sle/core/rag_context.py
=======================
Retrieval Augmented Generation para el Spatial Reasoning Engine.

Cuando el arquitecto solicita una nueva planta:
1. Extrae features del prompt nuevo
2. Busca en memoria los 2-3 proyectos más similares aprobados antes
3. Construye un bloque de contexto compacto con sus distribuciones
4. Ese bloque se inyecta como parte del system prompt o user message

Resultado: Claude no parte de cero — aprende del estilo del arquitecto
desde el primer proyecto guardado.
"""
from __future__ import annotations

from sle.core.memory import Memoria
from sle.core.spatial_graph import SpatialGraph


def _resumir_proyecto_para_contexto(proyecto: dict) -> str:
    """
    Resume un proyecto previo en formato compacto que Claude pueda
    usar como referencia. Enfocado en TOPOLOGÍA, no en geometría exacta.
    """
    plan = proyecto["plan"]
    grid = plan.get("grid", {})
    sim = proyecto.get("similitud", 0)
    score = proyecto.get("score", 0)

    g = SpatialGraph.from_json(plan)
    a = g.analizar()

    # Resumen de recintos: NOMBRE  ancho×alto  ubicación zonal
    lineas_rec = []
    for nombre, nodo in g.nodos.items():
        zona_corta = {
            "publica": "pub",
            "privada": "priv",
            "servicio": "serv",
            "circulacion": "circ",
        }.get(nodo.zona, "?")
        lineas_rec.append(
            f"  {nombre:30} {nodo.ancho}×{nodo.alto}m  fila={nodo.fila} col={nodo.col}  [{zona_corta}]"
        )

    # Conexiones funcionales
    cadenas_txt = []
    for c in a["cadenas_funcionales"]:
        cadenas_txt.append("    " + " ↔ ".join(c))

    accesos_txt = ", ".join(f"{r}({l})" for r, l in a["accesos"])

    return (
        f"### Proyecto previo similar (sim={sim:.2f}, score={score}/100)\n"
        f"Prompt original: \"{proyecto.get('prompt_original','')[:120]}\"\n"
        f"Grid: {grid.get('ancho_m')}×{grid.get('alto_m')}m  |  "
        f"{a['n_recintos']} recintos  |  acceso: {accesos_txt or 'ninguno'}\n"
        f"Distribución:\n"
        + "\n".join(lineas_rec)
        + ("\nCadenas topológicas:\n" + "\n".join(cadenas_txt) if cadenas_txt else "")
    )


def obtener_contexto_rag(
    prompt_usuario: str,
    memoria: Memoria | None = None,
    n: int = 2,
    umbral: float = 0.55,
) -> str:
    """
    Devuelve un bloque de texto formateado con los N proyectos más
    similares al prompt actual. Cadena vacía si no hay coincidencias.

    Este bloque debe inyectarse al inicio del user message en la llamada
    a la IA — NO en el system prompt, para mantener el system prompt
    estable y aprovechar el prompt caching.
    """
    memoria = memoria or Memoria()

    try:
        similares = memoria.buscar_proyectos_similares(
            prompt_usuario, n=n, umbral=umbral
        )
    except Exception:
        return ""

    if not similares:
        return ""

    bloques = [_resumir_proyecto_para_contexto(p) for p in similares]
    encabezado = (
        "═══════════════════════════════════════════════════════════════\n"
        "CONTEXTO RAG — Proyectos previos del arquitecto Joseph Merlos\n"
        "═══════════════════════════════════════════════════════════════\n"
        "Los siguientes proyectos fueron generados y aprobados antes por\n"
        "el arquitecto para necesidades similares. Úsalos como REFERENCIA\n"
        "ESTILÍSTICA — no copies literalmente, pero respeta el patrón de\n"
        "zonificación, dimensiones y relaciones funcionales que el\n"
        "arquitecto ha preferido históricamente.\n"
        "═══════════════════════════════════════════════════════════════\n"
    )
    cierre = (
        "═══════════════════════════════════════════════════════════════\n"
        "FIN CONTEXTO RAG — Aplica el patrón al nuevo proyecto:\n\n"
    )
    return encabezado + "\n\n".join(bloques) + "\n\n" + cierre


def estadisticas_rag(memoria: Memoria | None = None) -> dict:
    """Cuántos proyectos hay para RAG actualmente."""
    memoria = memoria or Memoria()
    return memoria.estadisticas()


# ─── Smoke test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db = tmp.name
    mem = Memoria(db_path=db)

    # Sembrar 2 proyectos
    mem.guardar_proyecto(
        {
            "grid": {"ancho_m": 10, "alto_m": 8},
            "recintos": [
                {"nombre": "SALA", "fila": 5, "col": 0, "ancho": 5, "alto": 3},
                {"nombre": "COCINA", "fila": 5, "col": 5, "ancho": 5, "alto": 3},
                {"nombre": "DORMITORIO", "fila": 0, "col": 0, "ancho": 5, "alto": 5},
                {"nombre": "BANO", "fila": 0, "col": 5, "ancho": 5, "alto": 5},
            ],
            "puertas": [{"tipo": "exterior", "recinto": "SALA", "lado": "sur", "ancho": 1.1}],
        },
        prompt_original="casa de 1 dormitorio 80m² estilo compacto sala-comedor integrado",
        score=88,
    )
    mem.guardar_proyecto(
        {
            "grid": {"ancho_m": 12, "alto_m": 10},
            "recintos": [
                {"nombre": "SALA", "fila": 7, "col": 0, "ancho": 6, "alto": 3},
                {"nombre": "COCINA", "fila": 7, "col": 6, "ancho": 6, "alto": 3},
                {"nombre": "DORMITORIO_PRINCIPAL", "fila": 0, "col": 0, "ancho": 6, "alto": 4},
                {"nombre": "BANO", "fila": 0, "col": 6, "ancho": 3, "alto": 4},
                {"nombre": "DORMITORIO_2", "fila": 0, "col": 9, "ancho": 3, "alto": 4},
                {"nombre": "PASILLO", "fila": 4, "col": 0, "ancho": 12, "alto": 3},
            ],
            "puertas": [{"tipo": "exterior", "recinto": "SALA", "lado": "sur", "ancho": 1.1}],
        },
        prompt_original="casa de 2 dormitorios 120m² con cochera",
        score=92,
    )

    # Probar RAG
    nuevo = "necesito casa de 1 dormitorio 75m² compacta"
    ctx = obtener_contexto_rag(nuevo, memoria=mem, n=2)
    print(ctx)
    print(f"\nEstadísticas: {estadisticas_rag(mem)}")
