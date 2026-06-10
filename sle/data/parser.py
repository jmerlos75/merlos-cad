"""
sle/data/parser.py
==================
Parser de datasets de plantas arquitectónicas para el SLE.

Convierte distintos formatos de entrada al JSON estándar del Engine:
    {
      "grid": {"ancho_m": N, "alto_m": N},
      "recintos": [{"nombre": ..., "fila": ..., "col": ..., "ancho": ..., "alto": ...}],
      "puertas": [...]
    }

Fuentes soportadas:
1. JSON propio del Engine (passthrough)
2. ArchitectureDataset.json (formato LIFULL HOME'S / similar público)
3. HouseGAN format (JSON con nodos y bordes)
4. CSV tabular simple (nombre, fila, col, ancho, alto)
5. Carpeta de proyectos (batch import desde sle/data/proyectos/)

También incluye `DatasetLoader.importar_a_memoria()` que carga un
directorio de JSONs y los guarda directamente en la Memoria del SLE.
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


# ─── Parsers por formato ─────────────────────────────────────────────

def parse_sle_json(data: dict) -> dict | None:
    """
    JSON propio del Engine: passthrough con validación mínima.
    Retorna None si no tiene la estructura mínima esperada.
    """
    if "recintos" not in data:
        return None
    if "grid" not in data:
        data = {"grid": {"ancho_m": 0, "alto_m": 0}, **data}
    return data


def parse_housegan_json(data: dict) -> dict | None:
    """
    Formato HouseGAN: nodos con tipo y coordenadas bbox [x1,y1,x2,y2]
    normalizadas 0–256. Convierte a grid en metros (1 celda ≈ 0.5m).

    Espera:
      data["nodes"]: list of {"label": str, "bbox": [x1,y1,x2,y2]}
      data["edges"]: list of [i, j]  (opcional)
    """
    nodes = data.get("nodes") or data.get("rms")  # HouseGAN usa "rms"
    if not nodes:
        return None

    ESCALA = 256 / 20.0  # 256px → 20m (aprox)

    recintos = []
    puertas = []

    for i, n in enumerate(nodes):
        # Soporte para formato dict con "label"/"bbox" o lista [label, bbox]
        if isinstance(n, dict):
            nombre = str(n.get("label") or n.get("room_type") or f"REC_{i}")
            bbox   = n.get("bbox") or n.get("box") or [0, 0, 10, 10]
        elif isinstance(n, (list, tuple)) and len(n) >= 2:
            nombre = str(n[0])
            bbox   = n[1] if isinstance(n[1], (list, tuple)) else [0, 0, 10, 10]
        else:
            continue

        x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
        col   = round(x1 / ESCALA)
        fila  = round(y1 / ESCALA)
        ancho = max(1, round((x2 - x1) / ESCALA))
        alto  = max(1, round((y2 - y1) / ESCALA))

        recintos.append({
            "nombre": nombre.upper().replace(" ", "_"),
            "fila": fila, "col": col,
            "ancho": ancho, "alto": alto,
        })

    # Aristas → conexiones entre recintos (puertas interiores)
    for edge in data.get("edges", []):
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            i, j = int(edge[0]), int(edge[1])
            if i < len(recintos) and j < len(recintos):
                puertas.append({
                    "tipo": "entre_recintos",
                    "recinto1": recintos[i]["nombre"],
                    "recinto2": recintos[j]["nombre"],
                    "ancho": 1.0,
                })

    if not recintos:
        return None

    # Calcular grid bounding box
    max_col  = max(r["col"]  + r["ancho"] for r in recintos)
    max_fila = max(r["fila"] + r["alto"]  for r in recintos)

    return {
        "grid": {"ancho_m": float(max_col), "alto_m": float(max_fila)},
        "recintos": recintos,
        "puertas": puertas,
    }


def parse_csv_tabular(path: Path) -> list[dict]:
    """
    CSV con columnas: nombre,fila,col,ancho,alto[,ancho_m_grid,alto_m_grid]
    Permite múltiples proyectos si tiene columna 'proyecto_id'.
    Retorna lista de planes JSON.
    """
    planes: dict[str, dict] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row.get("proyecto_id", "0")
            if pid not in planes:
                planes[pid] = {
                    "grid": {
                        "ancho_m": float(row.get("ancho_m_grid") or 0),
                        "alto_m":  float(row.get("alto_m_grid")  or 0),
                    },
                    "recintos": [],
                    "puertas": [],
                }
            try:
                rec = {
                    "nombre": row["nombre"].upper().strip(),
                    "fila":  int(float(row["fila"])),
                    "col":   int(float(row["col"])),
                    "ancho": int(float(row["ancho"])),
                    "alto":  int(float(row["alto"])),
                }
                planes[pid]["recintos"].append(rec)
            except (KeyError, ValueError) as e:
                logger.warning(f"Fila CSV ignorada: {row} — {e}")

    # Recalcular grid si no vino en el CSV
    for plan in planes.values():
        if plan["grid"]["ancho_m"] == 0 and plan["recintos"]:
            plan["grid"]["ancho_m"] = float(max(r["col"] + r["ancho"] for r in plan["recintos"]))
        if plan["grid"]["alto_m"] == 0 and plan["recintos"]:
            plan["grid"]["alto_m"]  = float(max(r["fila"] + r["alto"]  for r in plan["recintos"]))

    return list(planes.values())


# ─── Auto-detector de formato ────────────────────────────────────────

def auto_parse(raw: dict | list | str | Path) -> list[dict]:
    """
    Detecta el formato automáticamente y devuelve una lista de planes JSON.

    Acepta:
    - dict: intenta parse_sle_json → parse_housegan_json
    - list: asume lista de planes → parsea cada uno
    - str/Path: lee el archivo y detecta por extensión (.csv, .json)
    """
    if isinstance(raw, (str, Path)):
        path = Path(raw)
        if path.suffix.lower() == ".csv":
            return parse_csv_tabular(path)
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)

    if isinstance(raw, list):
        resultados = []
        for item in raw:
            parsed = auto_parse(item)
            resultados.extend(parsed)
        return resultados

    if isinstance(raw, dict):
        # 1) Intenta formato propio
        plan = parse_sle_json(raw)
        if plan:
            return [plan]
        # 2) Intenta HouseGAN
        plan = parse_housegan_json(raw)
        if plan:
            return [plan]
        logger.warning("No se pudo detectar el formato del JSON")
        return []

    return []


# ─── Loader de carpeta ───────────────────────────────────────────────

class DatasetLoader:
    """
    Carga datasets desde un directorio y los importa a la Memoria del SLE.

    Uso:
        loader = DatasetLoader("sle/data/public_datasets/")
        n = loader.importar_a_memoria(memoria, prompt_template="planta pública")
    """

    def __init__(self, directorio: str | Path):
        self.directorio = Path(directorio)

    def iterar_planes(self) -> Iterator[tuple[Path, dict]]:
        """Itera sobre todos los archivos del directorio y parsea cada uno."""
        for path in sorted(self.directorio.rglob("*")):
            if path.suffix.lower() not in (".json", ".csv"):
                continue
            try:
                planes = auto_parse(path)
                for plan in planes:
                    if plan.get("recintos"):
                        yield path, plan
            except Exception as e:
                logger.warning(f"Error al parsear {path}: {e}")

    def importar_a_memoria(
        self,
        memoria: Any,   # sle.core.memory.Memoria
        prompt_template: str = "planta importada desde dataset público",
        score: int = 0,
        aprobado: bool = False,   # No aprobadas por defecto (son datos de entrenamiento)
    ) -> int:
        """
        Importa todos los planes del directorio a la Memoria.
        Retorna el número de planes importados.
        """
        importados = 0
        for path, plan in self.iterar_planes():
            try:
                # Generar prompt descriptivo desde el plan
                recintos = plan.get("recintos", [])
                n_rec = len(recintos)
                grid = plan.get("grid", {})
                prompt = (
                    f"{prompt_template} | {n_rec} recintos | "
                    f"grid {grid.get('ancho_m', 0)}×{grid.get('alto_m', 0)}m | "
                    f"fuente: {path.name}"
                )
                memoria.guardar_proyecto(
                    plan,
                    prompt_original=prompt,
                    score=score,
                    aprobado=aprobado,
                )
                importados += 1
            except Exception as e:
                logger.warning(f"No se pudo guardar {path}: {e}")
        return importados

    def estadisticas(self) -> dict:
        """Estadísticas del directorio de datasets."""
        archivos = list(self.directorio.rglob("*.json")) + list(self.directorio.rglob("*.csv"))
        total_planes = sum(1 for _, _ in self.iterar_planes())
        return {
            "directorio": str(self.directorio),
            "archivos": len(archivos),
            "planes_totales": total_planes,
        }


# ─── Smoke test ─────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test 1: parse JSON propio
    plan_sle = {
        "grid": {"ancho_m": 10, "alto_m": 8},
        "recintos": [
            {"nombre": "SALA", "fila": 5, "col": 0, "ancho": 5, "alto": 3},
            {"nombre": "COCINA", "fila": 5, "col": 5, "ancho": 5, "alto": 3},
        ],
        "puertas": [{"tipo": "exterior", "recinto": "SALA", "lado": "sur", "ancho": 1.1}],
    }
    result = auto_parse(plan_sle)
    print(f"Test SLE JSON: {len(result)} plan(es), recintos={len(result[0]['recintos'])}")

    # Test 2: parse HouseGAN
    plan_hgan = {
        "nodes": [
            {"label": "Living Room", "bbox": [10, 10, 100, 80]},
            {"label": "Kitchen",     "bbox": [100, 10, 180, 80]},
            {"label": "Bedroom",     "bbox": [10, 80, 180, 180]},
        ],
        "edges": [[0, 1], [0, 2]],
    }
    result2 = auto_parse(plan_hgan)
    print(f"Test HouseGAN: {len(result2)} plan(es), recintos={len(result2[0]['recintos'])}")
    for r in result2[0]["recintos"]:
        print(f"  {r}")
