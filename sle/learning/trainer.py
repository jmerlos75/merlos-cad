"""
sle/learning/trainer.py
=======================
Entrenamiento del SimilitudModel del SLE.

Fase 1 — Entrenamiento sin gradientes:
  Ajusta los pesos del vector de encoding por importancia relativa,
  usando la memoria del arquitecto como señal de entrenamiento.
  No requiere PyTorch.

Flujo:
  1. Carga todos los proyectos aprobados de la Memoria
  2. Para cada par (A, B) con alta similitud real → ajusta pesos para
     acercarlos en el espacio de embedding
  3. Para cada par (A, C) con baja similitud real → aleja pesos
  4. Guarda el modelo entrenado

Fase futura (Fase 2): trainer completo con PyTorch + GNN.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from sle.core.spatial_graph import SpatialGraph
from sle.core.memory import Memoria
from sle.core.encoder import encode_graph, cosine_similarity, DIM_VECTOR
from sle.learning.model import SimilitudModel
from sle.learning.loss_functions import loss_total, PESOS_DEFAULT

logger = logging.getLogger(__name__)


# ─── Entrenador Fase 1 ───────────────────────────────────────────────

class Trainer:
    """
    Entrenador del SimilitudModel por ajuste de pesos supervisado.

    El arquitecto Joseph Merlos proporciona señal implícita:
    - Proyectos aprobados (score >= umbral_ok) son positivos
    - El resto son negativos

    Algoritmo: gradient-free weight update por correlación
    (similar a Perceptron ponderado pero sobre similitudes).
    """

    def __init__(
        self,
        memoria: Memoria | None = None,
        modelo: SimilitudModel | None = None,
        tasa_aprendizaje: float = 0.05,
        umbral_ok: int = 80,
        umbral_negativo: int = 60,
    ):
        self.memoria = memoria or Memoria()
        self.modelo  = modelo or SimilitudModel()
        self.lr      = tasa_aprendizaje
        self.umbral_ok  = umbral_ok
        self.umbral_neg = umbral_negativo
        self.historial: list[dict] = []

    # ── Preparación de datos ─────────────────────────────────────────

    def _cargar_pares(self) -> tuple[list[dict], list[dict]]:
        """
        Carga proyectos de memoria y los divide en positivos/negativos.
        """
        todos = self.memoria.listar_proyectos(limite=500)
        positivos, negativos = [], []
        for p in todos:
            if p["score"] >= self.umbral_ok:
                positivos.append(p)
            elif p["score"] < self.umbral_neg:
                negativos.append(p)
        return positivos, negativos

    def _vectorizar_proyecto(self, proy: dict) -> list[float] | None:
        """Obtiene el vector de encoding de un proyecto."""
        full = self.memoria.obtener_proyecto(proy["id"])
        if not full:
            return None
        plan = full["plan"]
        try:
            g = SpatialGraph.from_json(plan)
            return encode_graph(g)
        except Exception as e:
            logger.warning(f"Error al vectorizar proyecto {proy['id']}: {e}")
            return None

    # ── Paso de entrenamiento ────────────────────────────────────────

    def _actualizar_pesos(
        self,
        v_pred: list[float],
        v_pos: list[float],
        v_neg: list[float],
    ) -> float:
        """
        Actualiza pesos del modelo para acercar v_pred a v_pos
        y alejarlo de v_neg. Retorna la loss antes de actualizar.
        """
        pesos = self.modelo.pesos

        # Loss actual
        sim_pos = cosine_similarity(
            [x * p for x, p in zip(v_pred, pesos)],
            [x * p for x, p in zip(v_pos,  pesos)],
        )
        sim_neg = cosine_similarity(
            [x * p for x, p in zip(v_pred, pesos)],
            [x * p for x, p in zip(v_neg,  pesos)],
        )
        loss_antes = max(0.0, sim_neg - sim_pos + 0.2)

        if loss_antes == 0.0:
            return 0.0  # ya correctamente ordenado

        # Gradiente aproximado por diferencia de componentes
        for i in range(len(pesos)):
            a, b, c = v_pred[i], v_pos[i], v_neg[i]
            # Incrementar peso si el componente distingue positivo de negativo
            grad = (a * b - a * c)  # correlación positiva - negativa
            pesos[i] = max(0.01, pesos[i] + self.lr * grad)

        # Normalizar pesos para mantener escala
        norm = sum(pesos) / len(pesos)
        self.modelo.pesos = [p / norm for p in pesos]

        return loss_antes

    # ── Entrenamiento completo ───────────────────────────────────────

    def entrenar(
        self,
        n_epochs: int = 10,
        max_pares: int = 100,
    ) -> dict:
        """
        Entrena el modelo con los proyectos en memoria.

        Retorna métricas del entrenamiento:
        {
            "epochs": n,
            "n_positivos": int,
            "n_negativos": int,
            "loss_inicial": float,
            "loss_final": float,
            "mejora": float,
        }
        """
        logger.info("Iniciando entrenamiento del SLE...")
        t0 = time.time()

        positivos, negativos = self._cargar_pares()
        logger.info(f"Datos: {len(positivos)} positivos, {len(negativos)} negativos")

        if len(positivos) < 2:
            logger.warning("Insuficientes proyectos positivos (<2). Entrenamiento omitido.")
            return {"error": "datos_insuficientes", "n_positivos": len(positivos)}

        # Pre-vectorizar
        vecs_pos = [v for p in positivos if (v := self._vectorizar_proyecto(p)) is not None]
        vecs_neg = [v for p in negativos if (v := self._vectorizar_proyecto(p)) is not None]

        if len(vecs_pos) < 2:
            logger.warning("No se pudieron vectorizar suficientes proyectos.")
            return {"error": "vectorizacion_fallida"}

        # Si no hay negativos, crear negativos artificiales (permutando dimensiones)
        if not vecs_neg:
            import random
            vecs_neg = []
            for v in vecs_pos[:3]:
                v_perm = v[:]
                random.shuffle(v_perm)
                vecs_neg.append(v_perm)

        # Calcular loss inicial
        loss_inicial = self._calcular_loss_promedio(vecs_pos, vecs_neg)

        # Bucle de entrenamiento
        for epoch in range(n_epochs):
            epoch_loss = 0.0
            n_pares = 0

            # Para cada par de positivos + un negativo
            for i in range(min(len(vecs_pos) - 1, max_pares)):
                v_pred = vecs_pos[i]
                v_pos  = vecs_pos[(i + 1) % len(vecs_pos)]
                v_neg  = vecs_neg[i % len(vecs_neg)]

                loss = self._actualizar_pesos(v_pred, v_pos, v_neg)
                epoch_loss += loss
                n_pares += 1

            avg_loss = epoch_loss / max(n_pares, 1)
            self.historial.append({"epoch": epoch + 1, "loss": round(avg_loss, 4)})
            logger.debug(f"Epoch {epoch+1}/{n_epochs} — loss: {avg_loss:.4f}")

        loss_final = self._calcular_loss_promedio(vecs_pos, vecs_neg)
        mejora = loss_inicial - loss_final

        metricas = {
            "epochs": n_epochs,
            "n_positivos": len(vecs_pos),
            "n_negativos": len(vecs_neg),
            "loss_inicial": round(loss_inicial, 4),
            "loss_final":   round(loss_final, 4),
            "mejora":       round(mejora, 4),
            "tiempo_s":     round(time.time() - t0, 2),
        }

        self.modelo.entrenado = True
        self.modelo.metricas = metricas
        logger.info(f"Entrenamiento completado: loss {loss_inicial:.4f} → {loss_final:.4f}")
        return metricas

    def _calcular_loss_promedio(self, vecs_pos: list, vecs_neg: list) -> float:
        losses = []
        for i in range(min(len(vecs_pos) - 1, 50)):
            vp = vecs_pos[i]
            vpos = vecs_pos[(i + 1) % len(vecs_pos)]
            vneg = vecs_neg[i % len(vecs_neg)]
            pesos = self.modelo.pesos
            vp_w  = [x * p for x, p in zip(vp,   pesos)]
            vpos_w = [x * p for x, p in zip(vpos, pesos)]
            vneg_w = [x * p for x, p in zip(vneg, pesos)]
            loss = max(0.0, cosine_similarity(vp_w, vneg_w) - cosine_similarity(vp_w, vpos_w) + 0.2)
            losses.append(loss)
        return sum(losses) / max(len(losses), 1)

    # ── Guardado ─────────────────────────────────────────────────────

    def guardar_modelo(self, path: Path | str | None = None) -> Path:
        """Guarda el modelo entrenado. Por defecto en sle/data/modelo_similitud.json"""
        if path is None:
            path = Path(__file__).parent.parent / "data" / "modelo_similitud.json"
        p = Path(path)
        self.modelo.guardar(p)
        logger.info(f"Modelo guardado en {p}")
        return p


# ─── CLI / Smoke test ───────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    import logging
    logging.basicConfig(level=logging.INFO)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db = tmp.name

    mem = Memoria(db_path=db)

    # Sembrar datos de entrenamiento
    for i in range(5):
        plan = {
            "grid": {"ancho_m": 10 + i, "alto_m": 8},
            "recintos": [
                {"nombre": "SALA",       "fila": 5, "col": 0, "ancho": 5+i, "alto": 3},
                {"nombre": "COCINA",     "fila": 5, "col": 5, "ancho": 5,   "alto": 3},
                {"nombre": "DORMITORIO", "fila": 0, "col": 0, "ancho": 5,   "alto": 5},
                {"nombre": "BANO",       "fila": 0, "col": 5, "ancho": 5,   "alto": 5},
            ],
            "puertas": [{"tipo": "exterior", "recinto": "SALA", "lado": "sur", "ancho": 1.1}],
        }
        mem.guardar_proyecto(plan, f"casa {i+1} dormitorio sala-comedor {80+i*5}m²", score=85+i)

    trainer = Trainer(memoria=mem)
    metricas = trainer.entrenar(n_epochs=5, max_pares=20)
    print(f"\nMétricas de entrenamiento:")
    for k, v in metricas.items():
        print(f"  {k}: {v}")

    ruta = trainer.guardar_modelo()
    print(f"\nModelo guardado en: {ruta}")
