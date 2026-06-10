"""
sle/core/memory.py
==================
Memoria persistente del Spatial Learning Engine.

Almacena cada proyecto generado por el arquitecto Joseph Merlos en SQLite,
con extracción automática de features para búsqueda por similitud.

Las features se extraen del prompt en lenguaje natural (n_dormitorios,
n_banos, area_total, tiene_cochera, etc.) y se almacenan como columnas
indexadas para queries rápidas.

Uso:
    mem = Memoria()
    proj_id = mem.guardar_proyecto(plan_json, prompt_original, score=85)
    similares = mem.buscar_proyectos_similares(prompt_nuevo, n=3)
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Ruta a la base de datos — fija dentro de sle/data/
_DB_PATH = Path(__file__).parent.parent / "data" / "correcciones.db"


# ─── Extracción de features desde el prompt ──────────────────────────

@dataclass
class PromptFeatures:
    """Features extraídas del prompt en lenguaje natural."""
    n_dormitorios: int = 0
    n_banos: int = 0
    area_total_m2: float = 0.0
    tiene_cochera: bool = False
    tiene_estudio: bool = False
    tiene_cuarto_servicio: bool = False
    tiene_lavanderia: bool = False
    tiene_sala_comedor_integrado: bool = False
    es_compacto: bool = False
    es_planta_alta: bool = False

    def to_dict(self) -> dict:
        return {
            "n_dormitorios": self.n_dormitorios,
            "n_banos": self.n_banos,
            "area_total_m2": self.area_total_m2,
            "tiene_cochera": int(self.tiene_cochera),
            "tiene_estudio": int(self.tiene_estudio),
            "tiene_cuarto_servicio": int(self.tiene_cuarto_servicio),
            "tiene_lavanderia": int(self.tiene_lavanderia),
            "tiene_sala_comedor_integrado": int(self.tiene_sala_comedor_integrado),
            "es_compacto": int(self.es_compacto),
            "es_planta_alta": int(self.es_planta_alta),
        }


def extraer_features_prompt(prompt: str) -> PromptFeatures:
    """Parser heurístico del prompt para extraer features arquitectónicas."""
    p = prompt.lower()
    f = PromptFeatures()

    # Dormitorios: "3 dormitorios", "tres habitaciones", "un cuarto"
    palabras_num = {
        "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3,
        "cuatro": 4, "cinco": 5, "seis": 6,
    }
    pat_dorm = r"(\d+|un|una|uno|dos|tres|cuatro|cinco|seis)\s*(dormitorio|habitaci[oó]n|cuarto|rec[aá]mara)"
    m = re.search(pat_dorm, p)
    if m:
        val = m.group(1)
        f.n_dormitorios = int(val) if val.isdigit() else palabras_num.get(val, 1)

    # Baños
    pat_bano = r"(\d+|un|una|uno|dos|tres|cuatro)\s*(ba[ñn]o|servicio sanitario)"
    m = re.search(pat_bano, p)
    if m:
        val = m.group(1)
        f.n_banos = int(val) if val.isdigit() else palabras_num.get(val, 1)

    # Área total — "casa de 80m²", "120 metros cuadrados", "30 m2"
    pat_area = r"(\d+(?:\.\d+)?)\s*(?:m[²2]|metros?\s*cuadrados?)"
    m = re.search(pat_area, p)
    if m:
        f.area_total_m2 = float(m.group(1))

    # Booleanos por palabras clave
    f.tiene_cochera = any(w in p for w in ["cochera", "garage", "garaje", "parqueo"])
    f.tiene_estudio = any(w in p for w in ["estudio", "oficina", "home office"])
    f.tiene_cuarto_servicio = any(w in p for w in ["cuarto de servicio", "servicio"])
    f.tiene_lavanderia = any(w in p for w in ["lavander", "pila", "lavado"])
    f.tiene_sala_comedor_integrado = "sala comedor" in p or "sala-comedor" in p or "integrado" in p
    f.es_compacto = any(w in p for w in ["compacto", "compacta", "estudio", "minimalista"])
    f.es_planta_alta = any(w in p for w in ["dos pisos", "dos plantas", "planta alta", "segundo piso"])

    return f


# ─── Cálculo de similitud entre dos sets de features ─────────────────

def _similitud(fa: dict, fb: dict) -> float:
    """
    Similitud entre dos features dicts.
    Score 0.0 (totalmente distintos) — 1.0 (idénticos).
    Pondera más los campos arquitectónicamente relevantes.
    """
    pesos = {
        "n_dormitorios":               3.0,
        "n_banos":                     2.0,
        "area_total_m2":               3.0,
        "tiene_cochera":               1.5,
        "tiene_estudio":               1.0,
        "tiene_cuarto_servicio":       1.0,
        "tiene_lavanderia":            0.8,
        "tiene_sala_comedor_integrado":0.8,
        "es_compacto":                 1.5,
        "es_planta_alta":              1.0,
    }
    score = 0.0
    peso_total = 0.0
    for clave, peso in pesos.items():
        a, b = fa.get(clave, 0), fb.get(clave, 0)
        if clave == "area_total_m2":
            # Distancia normalizada — 50 m² de diferencia = score 0 para este campo
            if a == 0 and b == 0:
                contrib = 1.0
            elif a == 0 or b == 0:
                contrib = 0.0
            else:
                diff = abs(a - b)
                contrib = max(0.0, 1.0 - diff / 50.0)
        elif clave in ("n_dormitorios", "n_banos"):
            if a == b:
                contrib = 1.0
            elif abs(a - b) == 1:
                contrib = 0.5
            else:
                contrib = 0.0
        else:  # booleanos
            contrib = 1.0 if a == b else 0.0
        score += contrib * peso
        peso_total += peso
    return score / peso_total if peso_total > 0 else 0.0


# ─── Clase principal ────────────────────────────────────────────────

class Memoria:
    """
    Memoria persistente del SLE.
    Auto-crea la base de datos en sle/data/correcciones.db.
    """

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else _DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS proyectos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    prompt_original TEXT NOT NULL,
                    grid_ancho_m REAL,
                    grid_alto_m REAL,
                    area_total_m2 REAL,
                    score INTEGER,
                    aprobado INTEGER DEFAULT 1,
                    plan_json TEXT NOT NULL,
                    -- Features extraídas del prompt
                    n_dormitorios INTEGER DEFAULT 0,
                    n_banos INTEGER DEFAULT 0,
                    f_area_total_m2 REAL DEFAULT 0,
                    tiene_cochera INTEGER DEFAULT 0,
                    tiene_estudio INTEGER DEFAULT 0,
                    tiene_cuarto_servicio INTEGER DEFAULT 0,
                    tiene_lavanderia INTEGER DEFAULT 0,
                    tiene_sala_comedor_integrado INTEGER DEFAULT 0,
                    es_compacto INTEGER DEFAULT 0,
                    es_planta_alta INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS recintos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proyecto_id INTEGER NOT NULL,
                    nombre TEXT NOT NULL,
                    tipo TEXT,
                    fila INTEGER, col INTEGER,
                    ancho INTEGER, alto INTEGER,
                    area_m2 REAL,
                    FOREIGN KEY (proyecto_id) REFERENCES proyectos(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS conexiones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proyecto_id INTEGER NOT NULL,
                    recinto1 TEXT NOT NULL,
                    recinto2 TEXT,
                    tipo TEXT NOT NULL,           -- 'entre_recintos' | 'exterior'
                    lado TEXT,                    -- norte/sur/este/oeste (solo para exterior)
                    ancho REAL,
                    FOREIGN KEY (proyecto_id) REFERENCES proyectos(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS correcciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proyecto_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    tipo_cambio TEXT NOT NULL,    -- 'movido' | 'redimensionado' | 'eliminado' | 'añadido'
                    recinto TEXT,
                    json_antes TEXT,
                    json_despues TEXT,
                    nota TEXT,
                    FOREIGN KEY (proyecto_id) REFERENCES proyectos(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_proyectos_n_dorm ON proyectos(n_dormitorios);
                CREATE INDEX IF NOT EXISTS idx_proyectos_area ON proyectos(f_area_total_m2);
                CREATE INDEX IF NOT EXISTS idx_recintos_proyecto ON recintos(proyecto_id);
                CREATE INDEX IF NOT EXISTS idx_conexiones_proyecto ON conexiones(proyecto_id);
                CREATE INDEX IF NOT EXISTS idx_correcciones_proyecto ON correcciones(proyecto_id);
            """)

    # ─── Guardado ───────────────────────────────────────────────────

    def guardar_proyecto(
        self,
        plan: dict,
        prompt_original: str,
        score: int = 0,
        aprobado: bool = True,
    ) -> int:
        """
        Guarda un proyecto completo. Devuelve el proyecto_id.
        """
        features = extraer_features_prompt(prompt_original)
        feats = features.to_dict()

        # Si el prompt no mencionó dormitorios/baños, extraerlos del plan mismo
        if feats["n_dormitorios"] == 0 or feats["n_banos"] == 0:
            try:
                from sle.core.spatial_graph import detectar_tipo
                conteo: dict[str, int] = {}
                for rec in plan.get("recintos", []):
                    t = detectar_tipo(rec.get("nombre", ""))
                    conteo[t] = conteo.get(t, 0) + 1
                if feats["n_dormitorios"] == 0:
                    feats["n_dormitorios"] = (
                        conteo.get("dormitorio", 0) + conteo.get("dormitorio_principal", 0)
                    )
                if feats["n_banos"] == 0:
                    feats["n_banos"] = conteo.get("bano", 0)
                if feats["tiene_sala_comedor_integrado"] == 0:
                    feats["tiene_sala_comedor_integrado"] = int(
                        conteo.get("sala_comedor", 0) > 0
                        or (conteo.get("sala", 0) > 0 and conteo.get("comedor", 0) > 0)
                    )
            except Exception:
                pass

        grid = plan.get("grid", {})
        ancho_m = float(grid.get("ancho_m", 0) or 0)
        alto_m  = float(grid.get("alto_m", 0) or 0)
        area    = ancho_m * alto_m

        timestamp = datetime.utcnow().isoformat(timespec="seconds")

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO proyectos (
                    timestamp, prompt_original, grid_ancho_m, grid_alto_m,
                    area_total_m2, score, aprobado, plan_json,
                    n_dormitorios, n_banos, f_area_total_m2,
                    tiene_cochera, tiene_estudio, tiene_cuarto_servicio,
                    tiene_lavanderia, tiene_sala_comedor_integrado,
                    es_compacto, es_planta_alta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp, prompt_original, ancho_m, alto_m,
                    area, score, int(aprobado), json.dumps(plan, ensure_ascii=False),
                    feats["n_dormitorios"], feats["n_banos"], feats["area_total_m2"],
                    feats["tiene_cochera"], feats["tiene_estudio"], feats["tiene_cuarto_servicio"],
                    feats["tiene_lavanderia"], feats["tiene_sala_comedor_integrado"],
                    feats["es_compacto"], feats["es_planta_alta"],
                ),
            )
            proj_id = cur.lastrowid

            # Recintos
            for rec in plan.get("recintos", []):
                ancho = int(rec.get("ancho", rec.get("ancho_celdas", 0)))
                alto  = int(rec.get("alto",  rec.get("alto_celdas",  0)))
                area_r = ancho * alto
                conn.execute(
                    """
                    INSERT INTO recintos (proyecto_id, nombre, tipo, fila, col, ancho, alto, area_m2)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proj_id, rec["nombre"], self._detectar_tipo(rec["nombre"]),
                        rec.get("fila", 0), rec.get("col", 0),
                        ancho, alto, area_r,
                    ),
                )

            # Conexiones: puertas explícitas del plan
            puertas_guardadas: set[tuple] = set()
            for p in plan.get("puertas", []):
                tipo = p.get("tipo", "")
                if tipo == "entre_recintos":
                    r1, r2 = p.get("recinto1", ""), p.get("recinto2", "")
                    conn.execute(
                        """INSERT INTO conexiones (proyecto_id, recinto1, recinto2, tipo, ancho)
                           VALUES (?, ?, ?, ?, ?)""",
                        (proj_id, r1, r2, tipo, p.get("ancho", 1.0)),
                    )
                    puertas_guardadas.add(tuple(sorted([r1, r2])))
                elif tipo == "exterior":
                    conn.execute(
                        """INSERT INTO conexiones (proyecto_id, recinto1, recinto2, tipo, lado, ancho)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (proj_id, p.get("recinto", ""), None, tipo, p.get("lado", "sur"), p.get("ancho", 1.0)),
                    )

            # Conexiones: adyacencias calculadas automáticamente con SpatialGraph
            try:
                from sle.core.spatial_graph import SpatialGraph
                g = SpatialGraph.from_json(plan)
                for ar in g.aristas:
                    if ar.tipo == "adyacente" and ar.nodo_b:
                        par = tuple(sorted([ar.nodo_a, ar.nodo_b]))
                        if par not in puertas_guardadas:
                            conn.execute(
                                """INSERT INTO conexiones
                                   (proyecto_id, recinto1, recinto2, tipo, ancho)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (proj_id, ar.nodo_a, ar.nodo_b, "adyacente", 0.0),
                            )
                            puertas_guardadas.add(par)
            except Exception:
                pass  # Análisis de grafo es opcional — no falla el guardado

            conn.commit()

        # Guardar copia JSON del plan en disco
        proyectos_dir = self.db_path.parent / "proyectos"
        proyectos_dir.mkdir(parents=True, exist_ok=True)
        ruta_json = proyectos_dir / f"proyecto_{proj_id:05d}.json"
        ruta_json.write_text(
            json.dumps({
                "proyecto_id": proj_id,
                "timestamp": timestamp,
                "prompt": prompt_original,
                "score": score,
                "features": feats,
                "plan": plan,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return proj_id

    @staticmethod
    def _detectar_tipo(nombre: str) -> str:
        n = nombre.lower().replace("_", " ")
        if "principal" in n and ("dormitorio" in n or "cuarto" in n):
            return "dormitorio_principal"
        if "dormitorio" in n or "cuarto" in n or "habitaci" in n:
            return "dormitorio"
        if "cocina" in n:                    return "cocina"
        if "sala" in n and "comedor" in n:   return "sala_comedor"
        if "sala" in n:                      return "sala"
        if "comedor" in n:                   return "comedor"
        if "ba" in n or "wc" in n:           return "bano"
        if "pasillo" in n or "hall" in n:    return "pasillo"
        if "cochera" in n or "garage" in n:  return "cochera"
        if "lavander" in n:                  return "lavanderia"
        if "estudio" in n or "oficina" in n: return "estudio"
        return "otro"

    # ─── Búsqueda ───────────────────────────────────────────────────

    def buscar_proyectos_similares(
        self,
        prompt: str,
        n: int = 3,
        umbral: float = 0.5,
    ) -> list[dict]:
        """
        Devuelve los N proyectos más similares al prompt dado.
        Solo retorna proyectos con similitud >= umbral.
        """
        f_query = extraer_features_prompt(prompt).to_dict()

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM proyectos WHERE aprobado = 1 ORDER BY id DESC"
            ).fetchall()

        resultados = []
        for r in rows:
            f_proj = {
                "n_dormitorios": r["n_dormitorios"],
                "n_banos":       r["n_banos"],
                "area_total_m2": r["f_area_total_m2"],
                "tiene_cochera": r["tiene_cochera"],
                "tiene_estudio": r["tiene_estudio"],
                "tiene_cuarto_servicio":       r["tiene_cuarto_servicio"],
                "tiene_lavanderia":            r["tiene_lavanderia"],
                "tiene_sala_comedor_integrado":r["tiene_sala_comedor_integrado"],
                "es_compacto":   r["es_compacto"],
                "es_planta_alta":r["es_planta_alta"],
            }
            sim = _similitud(f_query, f_proj)
            if sim >= umbral:
                resultados.append({
                    "proyecto_id": r["id"],
                    "similitud": sim,
                    "timestamp": r["timestamp"],
                    "prompt_original": r["prompt_original"],
                    "score": r["score"],
                    "grid_ancho_m": r["grid_ancho_m"],
                    "grid_alto_m": r["grid_alto_m"],
                    "plan": json.loads(r["plan_json"]),
                })

        resultados.sort(key=lambda x: x["similitud"], reverse=True)
        return resultados[:n]

    def obtener_proyecto(self, proyecto_id: int) -> dict | None:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM proyectos WHERE id = ?", (proyecto_id,)).fetchone()
            if not r:
                return None
            recintos = [dict(x) for x in conn.execute(
                "SELECT * FROM recintos WHERE proyecto_id = ?", (proyecto_id,)
            ).fetchall()]
            conexiones = [dict(x) for x in conn.execute(
                "SELECT * FROM conexiones WHERE proyecto_id = ?", (proyecto_id,)
            ).fetchall()]
            return {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "prompt_original": r["prompt_original"],
                "score": r["score"],
                "plan": json.loads(r["plan_json"]),
                "recintos_db": recintos,
                "conexiones_db": conexiones,
            }

    def listar_proyectos(self, limite: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, timestamp, prompt_original, score, area_total_m2 "
                "FROM proyectos ORDER BY id DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ─── Correcciones ───────────────────────────────────────────────

    def guardar_correccion(
        self,
        proyecto_id: int,
        tipo_cambio: str,
        recinto: str | None = None,
        json_antes: dict | None = None,
        json_despues: dict | None = None,
        nota: str = "",
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO correcciones
                   (proyecto_id, timestamp, tipo_cambio, recinto, json_antes, json_despues, nota)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    proyecto_id,
                    datetime.utcnow().isoformat(timespec="seconds"),
                    tipo_cambio,
                    recinto,
                    json.dumps(json_antes, ensure_ascii=False) if json_antes else None,
                    json.dumps(json_despues, ensure_ascii=False) if json_despues else None,
                    nota,
                ),
            )
            return cur.lastrowid

    def top_correcciones(self, n: int = 10) -> list[dict]:
        """Correcciones más frecuentes — útil para detectar patrones del arquitecto."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT tipo_cambio, recinto, COUNT(*) as freq
                   FROM correcciones
                   GROUP BY tipo_cambio, recinto
                   ORDER BY freq DESC LIMIT ?""",
                (n,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ─── Análisis espacial ──────────────────────────────────────────

    def analizar_proyecto(self, proyecto_id: int) -> dict | None:
        """
        Retorna el análisis topológico completo de un proyecto guardado.
        Incluye: zonas, adyacencias, cadenas funcionales, validación CR.
        """
        datos = self.obtener_proyecto(proyecto_id)
        if not datos or not datos.get("plan"):
            return None
        try:
            from sle.core.spatial_graph import SpatialGraph
            g = SpatialGraph.from_json(datos["plan"])
            analisis = g.analizar()
            validacion = g.validate_topology()
            return {
                "proyecto_id": proyecto_id,
                "prompt": datos["prompt_original"],
                "score": datos["score"],
                **analisis,
                "validacion": validacion,
                "adyacencias": [
                    {"a": ar.nodo_a, "b": ar.nodo_b, "tipo": ar.tipo}
                    for ar in g.aristas if ar.nodo_b
                ],
            }
        except Exception as e:
            return {"error": str(e)}

    # ─── Estadísticas ───────────────────────────────────────────────

    def estadisticas(self) -> dict:
        with self._conn() as conn:
            n_proy   = conn.execute("SELECT COUNT(*) FROM proyectos").fetchone()[0]
            n_aprob  = conn.execute("SELECT COUNT(*) FROM proyectos WHERE aprobado = 1").fetchone()[0]
            n_corr   = conn.execute("SELECT COUNT(*) FROM correcciones").fetchone()[0]
            score_avg = conn.execute("SELECT AVG(score) FROM proyectos WHERE aprobado = 1").fetchone()[0]
            area_avg  = conn.execute("SELECT AVG(area_total_m2) FROM proyectos WHERE aprobado = 1").fetchone()[0]
        return {
            "n_proyectos": n_proy,
            "n_aprobados": n_aprob,
            "n_correcciones": n_corr,
            "score_promedio": round(score_avg or 0, 1),
            "area_promedio_m2": round(area_avg or 0, 1),
        }


# ─── Smoke test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db = tmp.name
    mem = Memoria(db_path=db)

    plan = {
        "grid": {"ancho_m": 10, "alto_m": 8},
        "recintos": [
            {"nombre": "SALA", "fila": 5, "col": 0, "ancho": 5, "alto": 3},
            {"nombre": "COCINA", "fila": 5, "col": 5, "ancho": 5, "alto": 3},
            {"nombre": "DORMITORIO_PRINCIPAL", "fila": 0, "col": 0, "ancho": 5, "alto": 5},
            {"nombre": "BANO", "fila": 0, "col": 5, "ancho": 5, "alto": 5},
        ],
        "puertas": [
            {"tipo": "exterior", "recinto": "SALA", "lado": "sur", "ancho": 1.1},
            {"tipo": "entre_recintos", "recinto1": "SALA", "recinto2": "COCINA", "ancho": 1.0},
        ],
    }
    pid = mem.guardar_proyecto(plan, "casa de 1 dormitorio con sala-comedor 80m²", score=85)
    print(f"Proyecto guardado: id={pid}")
    print(f"Estadísticas: {mem.estadisticas()}")

    similares = mem.buscar_proyectos_similares("casa pequeña 1 dormitorio sala comedor", n=3)
    print(f"Similares encontrados: {len(similares)}")
    for s in similares:
        print(f"  id={s['proyecto_id']} sim={s['similitud']:.2f} prompt={s['prompt_original'][:50]}")
