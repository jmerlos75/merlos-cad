"""
entrenar_sle.py
===============
Re-entrena el SimilitudModel del SLE con todos los proyectos
aprobados que hay en memoria (actualmente ~269 plantas).

Uso:
    python entrenar_sle.py
    python entrenar_sle.py --epochs 20 --lr 0.08 --ver-similares
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SLE_DIR = Path(__file__).parent
sys.path.insert(0, str(SLE_DIR))

from sle.core.memory import Memoria
from sle.learning.trainer import Trainer
from sle.learning.model import SimilitudModel


def main():
    parser = argparse.ArgumentParser(description="Entrena el modelo SLE")
    parser.add_argument("--epochs", type=int, default=15, help="Épocas de entrenamiento")
    parser.add_argument("--lr", type=float, default=0.05, help="Tasa de aprendizaje")
    parser.add_argument("--umbral-ok",  type=int, default=80, help="Score mínimo = positivo")
    parser.add_argument("--umbral-neg", type=int, default=65, help="Score máximo = negativo")
    parser.add_argument("--max-pares",  type=int, default=200, help="Pares por época")
    parser.add_argument("--ver-similares", action="store_true",
                        help="Muestra ejemplos de similitudes después de entrenar")
    args = parser.parse_args()

    memoria = Memoria()
    stats = memoria.estadisticas()
    print(f"\n{'='*60}")
    print(f"  SLE — Entrenamiento del modelo de similitud")
    print(f"{'='*60}")
    print(f"  Proyectos en memoria : {stats['n_proyectos']}")
    print(f"  Aprobados            : {stats['n_aprobados']}")
    print(f"  Score promedio       : {stats['score_promedio']}")
    print(f"  Umbral positivo      : >= {args.umbral_ok}")
    print(f"  Umbral negativo      : <  {args.umbral_neg}")
    print(f"  Épocas               : {args.epochs}")
    print(f"  Tasa aprendizaje     : {args.lr}")
    print(f"{'='*60}\n")

    trainer = Trainer(
        memoria=memoria,
        tasa_aprendizaje=args.lr,
        umbral_ok=args.umbral_ok,
        umbral_negativo=args.umbral_neg,
    )

    print("Entrenando...")
    metricas = trainer.entrenar(n_epochs=args.epochs, max_pares=args.max_pares)

    if "error" in metricas:
        print(f"\n[ERR] {metricas['error']}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  RESULTADO")
    print(f"{'='*60}")
    print(f"  Proyectos positivos  : {metricas['n_positivos']}")
    print(f"  Proyectos negativos  : {metricas['n_negativos']}")
    print(f"  Loss inicial         : {metricas['loss_inicial']:.4f}")
    print(f"  Loss final           : {metricas['loss_final']:.4f}")
    print(f"  Mejora               : {metricas['mejora']:+.4f}")
    print(f"  Tiempo               : {metricas['tiempo_s']:.1f}s")

    # Historial de pérdida
    print(f"\n  Historial loss por época:")
    for h in trainer.historial:
        barra = "█" * int(h["loss"] * 40)
        print(f"    Época {h['epoch']:>2}: {h['loss']:.4f}  {barra}")

    # Guardar modelo
    ruta = trainer.guardar_modelo()
    print(f"\n  Modelo guardado en: {ruta}")

    # Opcional: mostrar similitudes de ejemplo
    if args.ver_similares:
        _mostrar_ejemplos_similitud(memoria, trainer.modelo)

    print(f"\n[OK] Entrenamiento completado.\n")


def _mostrar_ejemplos_similitud(memoria: Memoria, modelo: SimilitudModel):
    """Muestra algunos ejemplos de similitud con el modelo entrenado."""
    from sle.core.spatial_graph import SpatialGraph

    print(f"\n{'='*60}")
    print(f"  EJEMPLOS DE SIMILITUD (modelo entrenado)")
    print(f"{'='*60}")

    proyectos = memoria.listar_proyectos(limite=20)
    if len(proyectos) < 4:
        print("  Pocos proyectos para mostrar ejemplos.")
        return

    # Vectorizar los primeros 10
    grafos = []
    for p in proyectos[:10]:
        full = memoria.obtener_proyecto(p["id"])
        if full:
            try:
                g = SpatialGraph.from_json(full["plan"])
                grafos.append((p["id"], p["prompt_original"][:50], g))
            except Exception:
                pass

    if len(grafos) < 2:
        return

    # Comparar el primero con los demás
    id0, prompt0, g0 = grafos[0]
    print(f"\n  Query: [{id0}] {prompt0}")
    print(f"  {'ID':>5}  {'SIM':>5}  Prompt")
    print(f"  {'-'*58}")
    similitudes = []
    for id_, prompt, g in grafos[1:]:
        sim = modelo.predecir_similitud(g0, g)
        similitudes.append((sim, id_, prompt))

    similitudes.sort(reverse=True)
    for sim, id_, prompt in similitudes:
        barra = "#" * int(sim * 20)
        prompt_safe = prompt.encode("ascii", "replace").decode("ascii")
        print(f"  [{id_:>4}]  {sim:.3f}  {barra}  {prompt_safe}")


if __name__ == "__main__":
    main()
