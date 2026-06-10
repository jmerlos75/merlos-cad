"""
sle/core/spatial_graph.py
=========================
Representación de plantas arquitectónicas como grafos espaciales.

Nodos = recintos con atributos (tipo, área, zona pública/privada, posición)
Aristas = conexiones físicas (adyacencia) y funcionales (puertas)

Esta representación es útil para:
- Análisis topológico (¿qué conecta con qué?)
- Cálculo de similitud entre plantas (no por geometría exacta sino por topología)
- Detección de patrones (cadenas funcionales, zonificación)
- Visualización futura
- Input futuro para un GNN cuando haya suficiente data

NO requiere dependencias externas (NetworkX, etc.) — implementación pura Python.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


# ─── Detector de tipo y zona ────────────────────────────────────────

TIPOS_PUBLICOS  = {"sala", "comedor", "cocina", "sala_comedor", "cochera"}
TIPOS_PRIVADOS  = {"dormitorio", "dormitorio_principal", "bano", "estudio"}
TIPOS_SERVICIO  = {"lavanderia", "cuarto_servicio"}
TIPOS_CIRCULACION = {"pasillo"}


def detectar_tipo(nombre: str) -> str:
    n = nombre.lower().replace("_", " ").replace("-", " ")
    if "principal" in n and ("dormitorio" in n or "cuarto" in n or "habitaci" in n):
        return "dormitorio_principal"
    if "dormitorio" in n or "cuarto" in n or "habitaci" in n or "recamara" in n:
        return "dormitorio"
    if "cocina" in n:                       return "cocina"
    if "sala" in n and "comedor" in n:      return "sala_comedor"
    if "sala" in n or "living" in n:        return "sala"
    if "comedor" in n:                      return "comedor"
    if "principal" in n and ("bano" in n or "baño" in n): return "bano_principal"
    if "compartido" in n and ("bano" in n or "baño" in n): return "bano_compartido"
    if "medio" in n and ("bano" in n or "baño" in n): return "medio_bano"
    if "bano" in n or "baño" in n or "wc" in n: return "bano"
    if "pasillo" in n or "hall" in n or "circulacion" in n: return "pasillo"
    if "vestibulo" in n or "vestíbulo" in n or "acceso" in n: return "vestibulo"
    if "balcon" in n or "balcón" in n or "terraza" in n:      return "terraza"
    if "cochera" in n or "garage" in n or "garaje" in n:    return "cochera"
    if "lavander" in n or "pila" in n:      return "lavanderia"
    if "estudio" in n or "oficina" in n:    return "estudio"
    if "servicio" in n:                     return "cuarto_servicio"
    return "otro"


def zona_de_tipo(tipo: str) -> str:
    if tipo in TIPOS_PUBLICOS:      return "publica"
    if tipo in TIPOS_PRIVADOS:      return "privada"
    if tipo in TIPOS_SERVICIO:      return "servicio"
    if tipo in TIPOS_CIRCULACION:   return "circulacion"
    return "otro"


# ─── Estructuras de datos ───────────────────────────────────────────

@dataclass
class NodoEspacial:
    """Un recinto como nodo del grafo."""
    nombre: str
    tipo: str
    zona: str
    fila: float
    col: float
    ancho: float   # puede ser decimal (ej: 2.2, 1.4) para baños y detalles
    alto: float

    @property
    def area_m2(self) -> float:
        return float(self.ancho * self.alto)

    @property
    def centroide(self) -> tuple[float, float]:
        cx = self.col + self.ancho / 2
        cy = self.fila + self.alto / 2
        return (cx, cy)

    def to_dict(self) -> dict:
        return {
            "nombre": self.nombre,
            "tipo": self.tipo,
            "zona": self.zona,
            "fila": self.fila, "col": self.col,
            "ancho": self.ancho, "alto": self.alto,
            "area_m2": self.area_m2,
        }


@dataclass
class AristaEspacial:
    """Una conexión entre dos recintos (o un recinto y el exterior)."""
    nodo_a: str
    nodo_b: str | None        # None = conexión al exterior
    tipo: str                 # 'adyacente' (sin puerta) | 'puerta' | 'exterior'
    lado: str | None = None   # solo para tipo='exterior'
    ancho: float = 1.0

    def to_dict(self) -> dict:
        return {
            "nodo_a": self.nodo_a,
            "nodo_b": self.nodo_b,
            "tipo": self.tipo,
            "lado": self.lado,
            "ancho": self.ancho,
        }


# ─── Grafo espacial ─────────────────────────────────────────────────

class SpatialGraph:
    """Representación de una planta como grafo espacial."""

    def __init__(self, ancho_m: float = 0, alto_m: float = 0):
        self.ancho_m: float = ancho_m
        self.alto_m: float = alto_m
        self.nodos: dict[str, NodoEspacial] = {}
        self.aristas: list[AristaEspacial] = []

    # ── Construcción ────────────────────────────────────────────────

    @classmethod
    def from_json(cls, plan: dict) -> "SpatialGraph":
        """
        Construye un grafo desde el JSON de plan estándar del Engine.
        Detecta adyacencias automáticamente del grid + procesa puertas.
        """
        grid = plan.get("grid", {})
        g = cls(
            ancho_m=float(grid.get("ancho_m", 0) or 0),
            alto_m=float(grid.get("alto_m", 0) or 0),
        )

        # Nodos
        for rec in plan.get("recintos", []):
            nombre = rec["nombre"]
            tipo = detectar_tipo(nombre)
            nodo = NodoEspacial(
                nombre=nombre,
                tipo=tipo,
                zona=zona_de_tipo(tipo),
                fila=float(rec.get("fila", 0)),
                col=float(rec.get("col", 0)),
                ancho=float(rec.get("ancho", rec.get("ancho_celdas", 0))),
                alto=float(rec.get("alto", rec.get("alto_celdas", 0))),
            )
            g.nodos[nombre] = nodo

        # Aristas: 1) adyacencias detectadas del grid
        pares_adyacentes = g._detectar_adyacencias()
        for (a, b) in pares_adyacentes:
            g.aristas.append(AristaEspacial(nodo_a=a, nodo_b=b, tipo="adyacente"))

        # Aristas: 2) puertas del plan (sobrescriben adyacencia simple)
        for p in plan.get("puertas", []):
            tipo_p = p.get("tipo", "")
            if tipo_p == "entre_recintos":
                a = p.get("recinto1", "")
                b = p.get("recinto2", "")
                ancho = float(p.get("ancho", 1.0))
                # Reemplazar la arista adyacente por una con puerta
                g._set_puerta(a, b, ancho)
            elif tipo_p == "exterior":
                r = p.get("recinto", "")
                lado = p.get("lado", "sur")
                ancho = float(p.get("ancho", 1.1))
                g.aristas.append(AristaEspacial(
                    nodo_a=r, nodo_b=None,
                    tipo="exterior", lado=lado, ancho=ancho,
                ))

        return g

    def _detectar_adyacencias(self) -> list[tuple[str, str]]:
        """Detecta pares de recintos que comparten al menos una celda de borde."""
        nombres = list(self.nodos.keys())
        adyacencias = set()
        for i, n1 in enumerate(nombres):
            r1 = self.nodos[n1]
            f1_min, f1_max = r1.fila, r1.fila + r1.alto - 1
            c1_min, c1_max = r1.col,  r1.col  + r1.ancho - 1
            for n2 in nombres[i + 1:]:
                r2 = self.nodos[n2]
                f2_min, f2_max = r2.fila, r2.fila + r2.alto - 1
                c2_min, c2_max = r2.col,  r2.col  + r2.ancho - 1
                # Adyacencia horizontal: filas se solapan, columnas se tocan
                solapa_fila = not (f1_max < f2_min or f2_max < f1_min)
                solapa_col  = not (c1_max < c2_min or c2_max < c1_min)
                toca_horiz = solapa_fila and (c1_max + 1 == c2_min or c2_max + 1 == c1_min)
                toca_vert  = solapa_col  and (f1_max + 1 == f2_min or f2_max + 1 == f1_min)
                if toca_horiz or toca_vert:
                    adyacencias.add(tuple(sorted([n1, n2])))
        return list(adyacencias)

    def _set_puerta(self, a: str, b: str, ancho: float):
        """Marca como 'puerta' la arista entre a-b. Crea la arista si no existe."""
        pair = tuple(sorted([a, b]))
        for ar in self.aristas:
            if ar.tipo in ("adyacente", "puerta") and tuple(sorted([ar.nodo_a, ar.nodo_b or ""])) == pair:
                ar.tipo = "puerta"
                ar.ancho = ancho
                return
        self.aristas.append(AristaEspacial(nodo_a=a, nodo_b=b, tipo="puerta", ancho=ancho))

    def to_json(self) -> dict:
        """Reconstruye el JSON de plan estándar desde el grafo."""
        recintos = [
            {
                "nombre": n.nombre, "fila": n.fila, "col": n.col,
                "ancho": n.ancho, "alto": n.alto,
            }
            for n in self.nodos.values()
        ]
        puertas = []
        for ar in self.aristas:
            if ar.tipo == "exterior":
                puertas.append({
                    "tipo": "exterior",
                    "recinto": ar.nodo_a,
                    "lado": ar.lado or "sur",
                    "ancho": ar.ancho,
                })
            elif ar.tipo == "puerta":
                puertas.append({
                    "tipo": "entre_recintos",
                    "recinto1": ar.nodo_a,
                    "recinto2": ar.nodo_b,
                    "ancho": ar.ancho,
                })
        return {
            "grid": {"ancho_m": self.ancho_m, "alto_m": self.alto_m},
            "recintos": recintos,
            "puertas": puertas,
        }

    # ── Consultas ───────────────────────────────────────────────────

    def vecinos(self, nombre: str, solo_con_puerta: bool = False) -> list[str]:
        """Vecinos del nodo. Si solo_con_puerta=True, solo conexiones con puerta."""
        out = set()
        for ar in self.aristas:
            if ar.tipo == "exterior":
                continue
            if solo_con_puerta and ar.tipo != "puerta":
                continue
            if ar.nodo_a == nombre and ar.nodo_b:
                out.add(ar.nodo_b)
            elif ar.nodo_b == nombre:
                out.add(ar.nodo_a)
        return sorted(out)

    def recintos_por_tipo(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for n in self.nodos.values():
            out.setdefault(n.tipo, []).append(n.nombre)
        return out

    def recintos_por_zona(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for n in self.nodos.values():
            out.setdefault(n.zona, []).append(n.nombre)
        return out

    def cadenas_funcionales(self) -> list[list[str]]:
        """
        Detecta cadenas topológicas típicas:
        - Cadena social: SALA → COMEDOR → COCINA
        - Cadena privada: PASILLO → DORMITORIOS
        - Cadena baño: DORMITORIO ↔ BAÑO
        """
        cadenas = []
        tipos = self.recintos_por_tipo()

        # Cadena social: sala→comedor→cocina
        salas = tipos.get("sala", []) + tipos.get("sala_comedor", [])
        comedores = tipos.get("comedor", [])
        cocinas = tipos.get("cocina", [])

        for s in salas:
            for c in comedores:
                if c in self.vecinos(s):
                    for k in cocinas:
                        if k in self.vecinos(c):
                            cadenas.append([s, c, k])

        # Cadena privada: pasillo → dormitorios
        for p in tipos.get("pasillo", []):
            dorms_conectados = [
                d for d in tipos.get("dormitorio", []) + tipos.get("dormitorio_principal", [])
                if d in self.vecinos(p)
            ]
            if dorms_conectados:
                cadenas.append([p] + dorms_conectados)

        # Cadena baño-dormitorio
        for b in tipos.get("bano", []):
            for d in tipos.get("dormitorio", []) + tipos.get("dormitorio_principal", []):
                if d in self.vecinos(b):
                    cadenas.append([d, b])

        return cadenas

    def acceso_exterior(self) -> list[tuple[str, str]]:
        """Recintos con acceso exterior + su lado (sur/norte/este/oeste)."""
        return [(ar.nodo_a, ar.lado or "?") for ar in self.aristas if ar.tipo == "exterior"]

    # ── Análisis topológico ─────────────────────────────────────────

    def analizar(self) -> dict:
        """Análisis arquitectónico completo del grafo."""
        area_total = sum(n.area_m2 for n in self.nodos.values())
        tipos = self.recintos_por_tipo()
        zonas = self.recintos_por_zona()
        cadenas = self.cadenas_funcionales()
        accesos = self.acceso_exterior()

        # Topología
        n_dormitorios = len(tipos.get("dormitorio", [])) + len(tipos.get("dormitorio_principal", []))
        n_banos = len(tipos.get("bano", []))
        n_recintos = len(self.nodos)
        n_puertas_int = sum(1 for a in self.aristas if a.tipo == "puerta")
        n_puertas_ext = len(accesos)

        # Recintos aislados (sin vecinos con puerta o exteriores)
        aislados = []
        for n in self.nodos:
            con_acceso = (
                any(a.nodo_a == n and a.tipo == "exterior" for a in self.aristas)
                or len(self.vecinos(n, solo_con_puerta=True)) > 0
            )
            if not con_acceso:
                aislados.append(n)

        return {
            "area_construida_m2": area_total,
            "area_grid_m2": self.ancho_m * self.alto_m,
            "n_recintos": n_recintos,
            "n_dormitorios": n_dormitorios,
            "n_banos": n_banos,
            "n_puertas_interiores": n_puertas_int,
            "n_accesos_exteriores": n_puertas_ext,
            "zonas": {z: len(rs) for z, rs in zonas.items()},
            "tipos": {t: len(rs) for t, rs in tipos.items()},
            "cadenas_funcionales": cadenas,
            "accesos": accesos,
            "recintos_aislados": aislados,
            "topologia_signature": self.signature(),
        }

    def signature(self) -> str:
        """
        Hash topológico — dos plantas con la misma estructura de tipos y
        conexiones producirán la misma firma, independientemente de
        coordenadas o nombres específicos.
        Útil para detectar plantas "iguales en estructura".
        """
        # Multiset de tipos
        tipos_count = sorted(
            (t, len(rs)) for t, rs in self.recintos_por_tipo().items()
        )
        # Multiset de aristas por tipos
        aristas_tipos = []
        for ar in self.aristas:
            if ar.tipo == "exterior":
                t_a = self.nodos[ar.nodo_a].tipo if ar.nodo_a in self.nodos else "?"
                aristas_tipos.append(("EXT", t_a, ar.lado or ""))
            elif ar.tipo == "puerta":
                t_a = self.nodos[ar.nodo_a].tipo if ar.nodo_a in self.nodos else "?"
                t_b = self.nodos[ar.nodo_b].tipo if ar.nodo_b in self.nodos and ar.nodo_b else "?"
                aristas_tipos.append(("INT", *sorted([t_a, t_b])))
        aristas_tipos.sort()

        payload = json.dumps({
            "tipos": tipos_count,
            "aristas": aristas_tipos,
        }, sort_keys=True)
        return hashlib.sha1(payload.encode()).hexdigest()[:12]

    def analizar_reglas_merlos(self) -> dict:
        """
        Analiza el grafo contra las reglas de diseño del Arq. Merlos.
        Incluye orientación cardinal, zonificación y agrupación de húmedas.
        Retorna el dict serializable del ResultadoReglasMerlos.
        """
        try:
            from sle.core.reglas_merlos import analizar_reglas_merlos
            resultado = analizar_reglas_merlos(self)
            return resultado.to_dict()
        except Exception as e:
            return {"error": str(e)}

    def similar_a(self, otro: "SpatialGraph", umbral: float = 0.75) -> bool:
        """¿Tiene topología similar a `otro`? Compara firmas y proporción de tipos."""
        if self.signature() == otro.signature():
            return True
        # Comparación más laxa: ¿comparten al menos `umbral` proporción de tipos?
        a_tipos = self.recintos_por_tipo()
        b_tipos = otro.recintos_por_tipo()
        comunes = set(a_tipos.keys()) & set(b_tipos.keys())
        if not comunes:
            return False
        union = set(a_tipos.keys()) | set(b_tipos.keys())
        return len(comunes) / len(union) >= umbral

    # ── Validación topológica básica ────────────────────────────────

    def validate_topology(self) -> dict:
        """
        Validación rápida de coherencia topológica (no normativa CR).
        Devuelve {ok, errores, advertencias}.
        """
        errores = []
        advertencias = []

        a = self.analizar()
        if a["recintos_aislados"]:
            errores.append(f"Recintos aislados: {a['recintos_aislados']}")
        if a["n_accesos_exteriores"] == 0:
            errores.append("Sin acceso exterior — la planta no tiene entrada")

        # Dormitorios sin baño cercano
        tipos = self.recintos_por_tipo()
        banos = tipos.get("bano", [])
        if banos:
            for d in tipos.get("dormitorio", []) + tipos.get("dormitorio_principal", []):
                vecinos_d = self.vecinos(d)
                if not any(b in vecinos_d or b in self.vecinos(v) for b in banos for v in vecinos_d):
                    advertencias.append(f"{d}: sin baño cercano (>2 saltos)")

        # Cocina sin comedor/sala
        for k in tipos.get("cocina", []):
            v = self.vecinos(k)
            if not any(t in tipos.get("comedor", []) + tipos.get("sala", []) + tipos.get("sala_comedor", []) for t in v):
                advertencias.append(f"{k}: aislada, sin comedor/sala adyacente")

        return {
            "ok": len(errores) == 0,
            "errores": errores,
            "advertencias": advertencias,
        }


# ─── Smoke test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    plan = {
        "grid": {"ancho_m": 10, "alto_m": 8},
        "recintos": [
            {"nombre": "SALA",       "fila": 5, "col": 0, "ancho": 5, "alto": 3},
            {"nombre": "COMEDOR",    "fila": 5, "col": 5, "ancho": 5, "alto": 3},
            {"nombre": "COCINA",     "fila": 0, "col": 5, "ancho": 5, "alto": 3},
            {"nombre": "DORMITORIO_PRINCIPAL", "fila": 0, "col": 0, "ancho": 5, "alto": 3},
            {"nombre": "BANO",       "fila": 3, "col": 0, "ancho": 3, "alto": 2},
            {"nombre": "PASILLO",    "fila": 3, "col": 3, "ancho": 7, "alto": 2},
        ],
        "puertas": [
            {"tipo": "exterior",       "recinto": "SALA",    "lado": "sur", "ancho": 1.1},
            {"tipo": "entre_recintos", "recinto1": "SALA",   "recinto2": "COMEDOR",   "ancho": 1.0},
            {"tipo": "entre_recintos", "recinto1": "COMEDOR","recinto2": "COCINA",    "ancho": 1.0},
            {"tipo": "entre_recintos", "recinto1": "PASILLO","recinto2": "DORMITORIO_PRINCIPAL", "ancho": 1.0},
            {"tipo": "entre_recintos", "recinto1": "PASILLO","recinto2": "BANO",      "ancho": 0.9},
        ],
    }
    g = SpatialGraph.from_json(plan)
    print(f"Nodos: {len(g.nodos)}, Aristas: {len(g.aristas)}")
    analisis = g.analizar()
    print(f"Topology signature: {analisis['topologia_signature']}")
    print(f"Zonas: {analisis['zonas']}")
    print(f"Cadenas funcionales:")
    for c in analisis["cadenas_funcionales"]:
        print(f"  {' -> '.join(c)}")
    print(f"Accesos: {analisis['accesos']}")
    print(f"Aislados: {analisis['recintos_aislados']}")

    val = g.validate_topology()
    print(f"Topology OK: {val['ok']}")
    if val["advertencias"]:
        for a in val["advertencias"]:
            print(f"  ADV: {a}")
