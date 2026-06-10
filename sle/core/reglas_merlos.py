"""
sle/core/reglas_merlos.py
=========================
Reglas de diseño arquitectónico del Arq. Joseph Merlos.
Codificación directa del conocimiento experto que el arquitecto
enseña a sus alumnos — base del Spatial Learning Engine.

REGLA 1 — Zonificación funcional
    Pública  : sala, comedor, cocina, cochera, baño de invitados, vestíbulo
    Privada  : dormitorios, sala TV, baño principal, baño compartido
    Húmeda   : baños (todos), cuarto de pilas/lavado, piscina
    Servicio : cuarto de servicio, lavandería
    Húmedas van juntas (agrupadas) siempre que sea posible.

REGLA 2 — Orientación cardinal (Costa Rica / hemisferio norte tropical)
    Norte  → MEJOR luz: no da calor, no penetra horizontalmente.
               Van los espacios PÚBLICOS principales (sala > comedor > cocina).
               Ventanas principales abren al norte.
    Sur    → Luz angular, controlable con aleros.
               Van los DORMITORIOS (zona privada).
               También baños que se relacionan con dormitorios.
    Este   → Luz angular (madrugada), aceptable para área privada.
               Área privada siempre va junta — este y sur son los dos lados.
    Oeste  → PEOR luz: horizontal, da calor, encandila.
               Solo se detiene con árboles, tapias o parasoles.
               Van las ÁREAS HÚMEDAS: baños, cuarto de pilas, piscina, cochera.

    Intermedios:
    NorEste → buena luz     → privados o públicos secundarios
    NorOeste→ no tan mala   → cocina, comedor
    SurOeste→ húmedas       → baños, lavandería
    SurEste → no tan mala   → privados

Clasificación de baños (sub-tipo según contexto):
    baño_invitados  : adyacente a sala/comedor → zona pública, tolera oeste
    baño_principal  : adyacente a dormitorio principal → zona privada, sur
    baño_compartido : sirve a 2+ dormitorios, colocado entre ellos → sur/oeste
    baño            : genérico (sin clasificar aún)

Regla de baño compartido:
    - Con 3 dormitorios y uno tiene baño privado → baño_compartido entre los otros dos
    - Con 2 dormitorios sin baño privado → baño_compartido al lado de uno (preferencia sur/oeste)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sle.core.spatial_graph import SpatialGraph, NodoEspacial


# ═══════════════════════════════════════════════════════════════════════
# TABLAS DE CONOCIMIENTO
# ═══════════════════════════════════════════════════════════════════════

# ── Tipos húmedos (deben agruparse) ─────────────────────────────────
TIPOS_HUMEDOS: set[str] = {
    "bano", "bano_invitados", "bano_principal", "bano_compartido",
    "medio_bano",                               # toilette de visitas, sin ducha
    "lavanderia", "cuarto_servicio", "piscina",
}

# ── Zona base por tipo ───────────────────────────────────────────────
ZONA_BASE: dict[str, str] = {
    "sala":              "publica",
    "sala_comedor":      "publica",
    "comedor":           "publica",
    "cocina":            "publica",
    "cochera":           "publica",     # húmeda además
    "vestibulo":         "publica",
    "bano_invitados":    "publica",     # húmeda además — cerca de sala
    "medio_bano":        "publica",     # húmeda — toilette de visitas, sin ducha
    "dormitorio":        "privada",
    "dormitorio_principal": "privada",
    "sala_tv":           "privada",
    "estudio":           "privada",
    "bano_principal":    "privada",     # húmeda además
    "bano_compartido":   "privada",     # húmeda además
    "bano":              "humeda",      # sin clasificar aún
    "lavanderia":        "servicio",    # húmeda además
    "cuarto_servicio":   "servicio",    # húmeda además
    "piscina":           "humeda",
    "pasillo":           "circulacion",
    "otro":              "otro",
}

# ── Preferencias cardinales ──────────────────────────────────────────
# Escala: +2 ideal | +1 bueno | 0 neutro | -1 malo | -2 pésimo
PREFERENCIA_CARDINAL: dict[str, dict[str, int]] = {
    # Públicos → Norte primero
    "sala":             {"norte": 2,  "sur": 0,  "este": 0,  "oeste": -2,
                         "noreste": 1, "noroeste": 1, "sureste": -1, "suroeste": -2},
    "sala_comedor":     {"norte": 2,  "sur": 0,  "este": 0,  "oeste": -2,
                         "noreste": 1, "noroeste": 1, "sureste": -1, "suroeste": -2},
    "comedor":          {"norte": 2,  "sur": 0,  "este": 1,  "oeste": -1,
                         "noreste": 2, "noroeste": 1, "sureste": 0,  "suroeste": -1},
    "cocina":           {"norte": 1,  "sur": 0,  "este": 1,  "oeste": -1,
                         "noreste": 1, "noroeste": 1, "sureste": 0,  "suroeste": 0},
    "vestibulo":        {"norte": 1,  "sur": 0,  "este": 1,  "oeste": 0,
                         "noreste": 1, "noroeste": 1, "sureste": 0,  "suroeste": 0},
    "cochera":          {"norte": 0,  "sur": 0,  "este": 0,  "oeste": 2,
                         "noreste": 0, "noroeste": 1, "sureste": 0,  "suroeste": 1},
    # Privados → Sur / Este
    "dormitorio":       {"norte": -1, "sur": 2,  "este": 1,  "oeste": -1,
                         "noreste": 0, "noroeste": -1,"sureste": 2,  "suroeste": 1},
    "dormitorio_principal": {"norte": -1, "sur": 2, "este": 1, "oeste": -1,
                         "noreste": 0, "noroeste": -1,"sureste": 2,  "suroeste": 1},
    "sala_tv":          {"norte": 0,  "sur": 2,  "este": 1,  "oeste": -1,
                         "noreste": 0, "noroeste": -1,"sureste": 1,  "suroeste": 0},
    "estudio":          {"norte": 1,  "sur": 1,  "este": 1,  "oeste": -1,
                         "noreste": 1, "noroeste": 1, "sureste": 1,  "suroeste": 0},
    # Húmedas → Oeste primero
    "bano":             {"norte": -1, "sur": 1,  "este": 0,  "oeste": 2,
                         "noreste": -1,"noroeste": 1, "sureste": 0,  "suroeste": 2},
    "bano_invitados":   {"norte": 0,  "sur": 0,  "este": 0,  "oeste": 1,
                         "noreste": 0, "noroeste": 1, "sureste": 0,  "suroeste": 1},
    "bano_principal":   {"norte": -1, "sur": 1,  "este": 0,  "oeste": 2,
                         "noreste": -1,"noroeste": 0, "sureste": 1,  "suroeste": 2},
    "bano_compartido":  {"norte": -1, "sur": 1,  "este": 0,  "oeste": 2,
                         "noreste": -1,"noroeste": 0, "sureste": 0,  "suroeste": 2},
    "lavanderia":       {"norte": -2, "sur": 0,  "este": -1, "oeste": 2,
                         "noreste": -2,"noroeste": 0, "sureste": 0,  "suroeste": 2},
    "cuarto_servicio":  {"norte": -1, "sur": 0,  "este": 0,  "oeste": 2,
                         "noreste": -1,"noroeste": 1, "sureste": 0,  "suroeste": 2},
    "piscina":          {"norte": 1,  "sur": 0,  "este": 1,  "oeste": 1,
                         "noreste": 1, "noroeste": 1, "sureste": 1,  "suroeste": 1},
    "pasillo":          {"norte": 0,  "sur": 0,  "este": 0,  "oeste": 0,
                         "noreste": 0, "noroeste": 0, "sureste": 0,  "suroeste": 0},
}

_PREF_DEFAULT: dict[str, int] = {
    "norte": 0, "sur": 0, "este": 0, "oeste": 0,
    "noreste": 0, "noroeste": 0, "sureste": 0, "suroeste": 0,
}

# ── REGLA 3 — Jerarquía de proporciones zona pública ────────────────
# SALA > COMEDOR > COCINA (siempre, sin excepción)
# La cocina NUNCA puede ser más grande que la sala.
# Tolerancia del 5%: se permite que sean casi iguales.
TOLERANCIA_PROPORCION: float = 0.05   # 5% — margen de cuadrícula discreta

# ── REGLA 4 — Jerarquía de dormitorios ──────────────────────────────
# DORMITORIO PRINCIPAL > todos los dormitorios secundarios
# (también con tolerancia del 5%)

# ── REGLA 5 — Jerarquía de baños ─────────────────────────────────────
# BAÑO PRINCIPAL > BAÑO (compartido/secundario) > MEDIO BAÑO
# MEDIO BAÑO: sin área de ducha, solo para visitas (toilette)
#   — reconocido por nombre que contenga 'medio' o 'visita' o área ≤ 2.5 m²
AREA_MAX_MEDIO_BANO: float = 2.5      # m² — si un baño mide ≤ esto, es medio baño

# ── REGLA 6 — Dimensiones mínimas de cochera (INVU / CFIA Costa Rica) ─
# Formato: {"n_autos": (ancho_min_m, largo_min_m)}
DIMENSIONES_COCHERA: dict[int, tuple[float, float]] = {
    1: (2.5, 5.0),   # 1 auto  → mínimo 2.5 m × 5.0 m
    2: (5.0, 5.0),   # 2 autos → mínimo 5.0 m × 5.0 m (dos lado a lado)
    3: (7.5, 5.0),   # 3 autos → mínimo 7.5 m × 5.0 m (tres lado a lado)
}


# ═══════════════════════════════════════════════════════════════════════
# CLASIFICACIÓN DE BAÑOS
# ═══════════════════════════════════════════════════════════════════════

def clasificar_banos(grafo: "SpatialGraph") -> dict[str, str]:
    """
    Sub-clasifica cada nodo de tipo 'bano' según contexto topológico y área.

    Retorna dict: {nombre_recinto: subtipo}
    Subtipos:
      'medio_bano'      — toilette de visitas, sin ducha (área ≤ 2.5 m² o
                          nombre contiene 'medio'/'visita')
      'bano_invitados'  — adyacente a sala/comedor, área de servicio a visitas
      'bano_principal'  — adyacente a dormitorio principal
      'bano_compartido' — sirve a 2+ dormitorios
      'bano'            — sin clasificar
    """
    from sle.core.spatial_graph import detectar_tipo

    resultado: dict[str, str] = {}
    for nombre, nodo in grafo.nodos.items():
        if nodo.tipo not in ("bano", "medio_bano"):
            continue

        area = nodo.ancho * nodo.alto
        nombre_lower = nombre.lower()

        # ── Clasificación por nombre explícito (máxima prioridad) ────
        # Si el nombre ya indica el subtipo, lo respetamos sin importar
        # la topología — el generador y el usuario nombran con precisión.
        if "medio" in nombre_lower or "visita" in nombre_lower:
            resultado[nombre] = "medio_bano"
            continue
        if "principal" in nombre_lower:
            resultado[nombre] = "bano_principal"
            continue
        if "compartido" in nombre_lower or "compart" in nombre_lower:
            resultado[nombre] = "bano_compartido"
            continue
        if "invitado" in nombre_lower:
            resultado[nombre] = "bano_invitados"
            continue

        # ── Medio baño: por área pequeña (sin nombre explícito) ──────
        es_medio = area <= AREA_MAX_MEDIO_BANO
        if es_medio:
            resultado[nombre] = "medio_bano"
            continue

        # ── Clasificación topológica (fallback para nombres genéricos) ─
        vecinos = grafo.vecinos(nombre)
        tipos_vecinos = {v: detectar_tipo(v) for v in vecinos}

        # ¿Adyacente a dormitorio principal? → baño principal
        if any(t == "dormitorio_principal" for t in tipos_vecinos.values()):
            resultado[nombre] = "bano_principal"

        # ¿Adyacente a sala o comedor? → baño de invitados
        elif any(t in ("sala", "comedor", "sala_comedor", "vestibulo")
                 for t in tipos_vecinos.values()):
            resultado[nombre] = "bano_invitados"

        # ¿Adyacente a 2+ dormitorios? → baño compartido
        elif sum(1 for t in tipos_vecinos.values()
                 if t in ("dormitorio", "dormitorio_principal")) >= 2:
            resultado[nombre] = "bano_compartido"

        # ¿Adyacente a 1 dormitorio? → baño compartido (privado)
        elif any(t in ("dormitorio", "dormitorio_principal")
                 for t in tipos_vecinos.values()):
            resultado[nombre] = "bano_compartido"

        else:
            resultado[nombre] = "bano"

    return resultado


# ═══════════════════════════════════════════════════════════════════════
# ORIENTACIÓN CARDINAL DESDE POSICIÓN EN GRID
# ═══════════════════════════════════════════════════════════════════════

def orientacion_cardinal(
    fila: float, col: float, ancho: float, alto: float,
    grid_filas: float, grid_cols: float,
) -> str:
    """
    Determina la orientación dominante de un recinto dentro del grid.

    Sistema de coordenadas:
      fila 0    = Norte  |  fila máxima = Sur
      col 0     = Oeste  |  col máxima  = Este

    Retorna: 'norte'|'sur'|'este'|'oeste'|'noreste'|'noroeste'|'sureste'|'suroeste'|'centro'
    """
    if grid_filas <= 0 or grid_cols <= 0:
        return "centro"

    # Centroide normalizado [0,1]
    cx = (col + ancho / 2) / grid_cols    # 0=oeste, 1=este
    cy = (fila + alto / 2) / grid_filas   # 0=norte, 1=sur

    # Desplazamiento desde el centro (- = norte/oeste, + = sur/este)
    dx = cx - 0.5   # positivo = este
    dy = cy - 0.5   # positivo = sur

    umbral_principal = 0.15  # zona de influencia cardinal pura
    umbral_diagonal  = 0.08  # zona de influencia diagonal

    es_norte = dy < -umbral_principal
    es_sur   = dy >  umbral_principal
    es_este  = dx >  umbral_principal
    es_oeste = dx < -umbral_principal

    if es_norte and es_este:   return "noreste"
    if es_norte and es_oeste:  return "noroeste"
    if es_sur   and es_este:   return "sureste"
    if es_sur   and es_oeste:  return "suroeste"
    if es_norte:               return "norte"
    if es_sur:                 return "sur"
    if es_este:                return "este"
    if es_oeste:               return "oeste"
    return "centro"


def score_orientacion(tipo: str, orientacion: str) -> int:
    """
    Puntaje de adecuación del tipo de recinto a su orientación.
    Retorna -2..+2.
    """
    prefs = PREFERENCIA_CARDINAL.get(tipo, _PREF_DEFAULT)
    return prefs.get(orientacion, 0)


# ═══════════════════════════════════════════════════════════════════════
# ANÁLISIS COMPLETO DE REGLAS MERLOS
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ResultadoReglasMerlos:
    """Resultado del análisis de reglas Merlos para un plan."""
    score_total: int = 0                  # 0-100
    score_orientacion: int = 0            # sub-score orientación cardinal     (R2)
    score_zonificacion: int = 0           # sub-score zonificación funcional   (R1)
    score_humedas: int = 0                # sub-score agrupación de húmedas   (R1)
    score_proporciones: int = 0           # sub-score jerarquía de proporciones (R3+R4+R5)
    score_cochera: int = 0                # sub-score dimensiones de cochera   (R6)

    por_recinto: dict[str, dict] = field(default_factory=dict)
    # {nombre: {orientacion, score_orient, tipo_efectivo, zona, ...}}

    cumplidas: list[str] = field(default_factory=list)
    violaciones: list[str] = field(default_factory=list)
    sugerencias: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score_total": self.score_total,
            "score_orientacion": self.score_orientacion,
            "score_zonificacion": self.score_zonificacion,
            "score_humedas": self.score_humedas,
            "score_proporciones": self.score_proporciones,
            "score_cochera": self.score_cochera,
            "por_recinto": self.por_recinto,
            "cumplidas": self.cumplidas,
            "violaciones": self.violaciones,
            "sugerencias": self.sugerencias,
        }


def analizar_reglas_merlos(grafo: "SpatialGraph") -> ResultadoReglasMerlos:
    """
    Analiza un SpatialGraph contra las reglas del Arq. Merlos.
    Retorna ResultadoReglasMerlos con scores, cumplidas y violaciones.
    """
    from sle.core.spatial_graph import detectar_tipo

    res = ResultadoReglasMerlos()
    if not grafo.nodos:
        return res

    # Clasificar baños en contexto
    subtipos_bano = clasificar_banos(grafo)

    grid_filas = grafo.alto_m or 1.0
    grid_cols  = grafo.ancho_m or 1.0

    # ── Análisis por recinto ─────────────────────────────────────────
    scores_orient: list[int] = []
    tipos_publicos_norte: list[str] = []
    tipos_privados_sur:   list[str] = []
    tipos_humedos_oeste:  list[str] = []
    humedos_posiciones:   list[tuple[float, float]] = []  # (cx, cy) normalizado

    for nombre, nodo in grafo.nodos.items():
        tipo_efectivo = subtipos_bano.get(nombre, nodo.tipo)
        zona = ZONA_BASE.get(tipo_efectivo, "otro")
        es_humedo = tipo_efectivo in TIPOS_HUMEDOS

        orient = orientacion_cardinal(
            nodo.fila, nodo.col, nodo.ancho, nodo.alto,
            grid_filas, grid_cols,
        )
        sc = score_orientacion(tipo_efectivo, orient)
        scores_orient.append(sc)

        cx = (nodo.col + nodo.ancho / 2) / grid_cols
        cy = (nodo.fila + nodo.alto / 2) / grid_filas
        if es_humedo:
            humedos_posiciones.append((cx, cy))

        res.por_recinto[nombre] = {
            "tipo_base": nodo.tipo,
            "tipo_efectivo": tipo_efectivo,
            "zona": zona,
            "es_humedo": es_humedo,
            "orientacion": orient,
            "score_orientacion": sc,
        }

        # Acumular para validaciones
        if zona == "publica" and "norte" in orient:
            tipos_publicos_norte.append(nombre)
        if zona == "privada" and ("sur" in orient or "este" in orient):
            tipos_privados_sur.append(nombre)
        if es_humedo and ("oeste" in orient or "sur" in orient):
            tipos_humedos_oeste.append(nombre)

    # ── Score orientación ────────────────────────────────────────────
    if scores_orient:
        avg = sum(scores_orient) / len(scores_orient)
        # avg va de -2 a +2 → normalizar a 0-100
        res.score_orientacion = max(0, min(100, int((avg + 2) / 4 * 100)))

    # ── Score zonificación ───────────────────────────────────────────
    tipos = {n: grafo.nodos[n].tipo for n in grafo.nodos}
    n_publicos = sum(1 for t in tipos.values() if ZONA_BASE.get(t) == "publica")
    n_privados = sum(1 for t in tipos.values() if ZONA_BASE.get(t) == "privada")
    n_total = len(grafo.nodos) or 1

    # Verifica que haya zona pública Y privada definidas
    tiene_publicos = n_publicos > 0
    tiene_privados = n_privados > 0
    res.score_zonificacion = 100 if (tiene_publicos and tiene_privados) else (
        50 if (tiene_publicos or tiene_privados) else 0
    )

    # ── Score húmedas agrupadas ──────────────────────────────────────
    if len(humedos_posiciones) >= 2:
        # Medir dispersión: desviación estándar de las posiciones
        cxs = [p[0] for p in humedos_posiciones]
        cys = [p[1] for p in humedos_posiciones]
        mean_x = sum(cxs) / len(cxs)
        mean_y = sum(cys) / len(cys)
        std = math.sqrt(
            sum((x - mean_x)**2 + (y - mean_y)**2
                for x, y in humedos_posiciones) / len(humedos_posiciones)
        )
        # std < 0.2 → muy agrupado (100), std > 0.5 → muy disperso (0)
        res.score_humedas = max(0, min(100, int((0.5 - std) / 0.3 * 100)))
    elif len(humedos_posiciones) == 1:
        res.score_humedas = 100  # solo uno, automáticamente "agrupado"
    else:
        res.score_humedas = 50   # sin húmedas definidas

    # ── Reglas cumplidas y violaciones ───────────────────────────────
    # IMPORTANTE: evaluar primero — asignan score_proporciones y score_cochera
    _evaluar_regla1(grafo, subtipos_bano, res)
    _evaluar_regla2(grafo, res)
    _evaluar_regla3_proporciones_publicas(grafo, res)
    _evaluar_regla4_jerarquia_dormitorios(grafo, res)
    _evaluar_regla5_jerarquia_banos(grafo, subtipos_bano, res)
    _evaluar_regla6_cochera(grafo, res)

    # ── Score total ponderado (calculado DESPUÉS de los evaluadores) ─
    # R2 orientación: 40% | R1 zonificación: 20% | R1 húmedas: 10%
    # R3+R4+R5 proporciones: 20% | R6 cochera: 10%
    res.score_total = int(
        res.score_orientacion  * 0.40 +
        res.score_zonificacion * 0.20 +
        res.score_humedas      * 0.10 +
        res.score_proporciones * 0.20 +
        res.score_cochera      * 0.10
    )

    return res


# ─── Evaluadores de reglas individuales ────────────────────────────

def _evaluar_regla1(
    grafo: "SpatialGraph",
    subtipos_bano: dict[str, str],
    res: ResultadoReglasMerlos,
):
    """REGLA 1: Zonificación — públicos, privados, húmedas."""
    from sle.core.spatial_graph import detectar_tipo

    tipos_presentes: dict[str, list[str]] = {}
    for nombre, nodo in grafo.nodos.items():
        te = subtipos_bano.get(nombre, nodo.tipo)
        tipos_presentes.setdefault(te, []).append(nombre)

    # Verificar baño de invitados cerca de sala
    banos_inv = tipos_presentes.get("bano_invitados", [])
    salas = tipos_presentes.get("sala", []) + tipos_presentes.get("sala_comedor", [])
    if banos_inv and salas:
        for bi in banos_inv:
            vecinos_bi = grafo.vecinos(bi)
            if any(s in vecinos_bi for s in salas):
                res.cumplidas.append(f"REGLA1 ✓ {bi} (baño invitados) adyacente a sala")
            else:
                res.sugerencias.append(f"REGLA1 ○ {bi} debería estar más cerca de la sala")

    # Verificar baño compartido entre dormitorios
    banos_comp = tipos_presentes.get("bano_compartido", [])
    dorms = (tipos_presentes.get("dormitorio", [])
             + tipos_presentes.get("dormitorio_principal", []))
    for bc in banos_comp:
        vecinos_bc = grafo.vecinos(bc)
        dorms_adj = [d for d in dorms if d in vecinos_bc]
        if len(dorms_adj) >= 2:
            res.cumplidas.append(
                f"REGLA1 ✓ {bc} (baño compartido) entre {dorms_adj[0]} y {dorms_adj[1]}"
            )
        elif len(dorms_adj) == 1:
            res.cumplidas.append(f"REGLA1 ✓ {bc} adyacente a {dorms_adj[0]}")
        else:
            res.violaciones.append(f"REGLA1 ✗ {bc} no está adyacente a ningún dormitorio")

    # Verificar que área privada sea contigua
    nombres_privados = [
        n for n, nodo in grafo.nodos.items()
        if ZONA_BASE.get(subtipos_bano.get(n, nodo.tipo), "otro") == "privada"
    ]
    if len(nombres_privados) >= 2:
        # Todos los privados deben estar conectados entre sí (directa o via pasillo)
        conectados = _zona_es_contigua(grafo, set(nombres_privados))
        if conectados:
            res.cumplidas.append("REGLA1 ✓ Área privada agrupada y contigua")
        else:
            res.violaciones.append(
                "REGLA1 ✗ Área privada NO contigua — dormitorios separados"
            )

    # Verificar que húmedas estén agrupadas
    nombres_humedos = [
        n for n, nodo in grafo.nodos.items()
        if subtipos_bano.get(n, nodo.tipo) in TIPOS_HUMEDOS
    ]
    if len(nombres_humedos) >= 2:
        if _humedas_agrupadas(grafo, nombres_humedos):
            res.cumplidas.append(
                f"REGLA1 ✓ Áreas húmedas agrupadas ({len(nombres_humedos)} elementos)"
            )
        else:
            res.sugerencias.append(
                "REGLA1 ○ Áreas húmedas dispersas — intentar agrupar baños/pilas/piscina"
            )


def _evaluar_regla2(grafo: "SpatialGraph", res: ResultadoReglasMerlos):
    """REGLA 2: Orientación cardinal."""
    grid_filas = grafo.alto_m or 1.0
    grid_cols  = grafo.ancho_m or 1.0

    sala_al_norte = False
    dormitorio_al_sur = False
    humedas_al_oeste = False
    sala_al_oeste = False

    for nombre, datos in res.por_recinto.items():
        tipo = datos["tipo_efectivo"]
        orient = datos["orientacion"]

        if tipo in ("sala", "sala_comedor"):
            if "norte" in orient or "noreste" in orient or "noroeste" in orient:
                sala_al_norte = True
            if "oeste" in orient:
                sala_al_oeste = True

        if tipo in ("dormitorio", "dormitorio_principal"):
            if "sur" in orient or "este" in orient:
                dormitorio_al_sur = True

        if tipo in TIPOS_HUMEDOS - {"cochera"}:
            if "oeste" in orient or "sur" in orient:
                humedas_al_oeste = True

    if sala_al_norte:
        res.cumplidas.append("REGLA2 ✓ Sala/estar orientada al norte (mejor luz)")
    elif any(d["tipo_efectivo"] in ("sala","sala_comedor") for d in res.por_recinto.values()):
        res.violaciones.append(
            "REGLA2 ✗ Sala no está al norte — perderá la mejor luz natural"
        )
    if sala_al_oeste:
        res.violaciones.append(
            "REGLA2 ✗ Sala al oeste — peor orientación, da calor y encandila"
        )

    if dormitorio_al_sur:
        res.cumplidas.append(
            "REGLA2 ✓ Dormitorios orientados al sur/sureste/suroeste/este (zona privada correcta)"
        )
    elif any(d["tipo_efectivo"] in ("dormitorio","dormitorio_principal")
             for d in res.por_recinto.values()):
        res.sugerencias.append(
            "REGLA2 ○ Considerar mover dormitorios hacia el sur, sureste, suroeste o este"
        )

    if humedas_al_oeste:
        res.cumplidas.append("REGLA2 ✓ Áreas húmedas al oeste/sur (orientación correcta)")
    elif any(d["es_humedo"] for d in res.por_recinto.values()):
        res.sugerencias.append(
            "REGLA2 ○ Áreas húmedas podrían moverse hacia el oeste"
        )


# ═══════════════════════════════════════════════════════════════════════
# REGLAS 3-6 — PROPORCIONES Y DIMENSIONES
# ═══════════════════════════════════════════════════════════════════════

def _area(nodo: "NodoEspacial") -> float:
    """Área del recinto en m²."""
    return nodo.ancho * nodo.alto


def _evaluar_regla3_proporciones_publicas(
    grafo: "SpatialGraph",
    res: ResultadoReglasMerlos,
) -> None:
    """
    REGLA 3 — Jerarquía zona pública: SALA > COMEDOR > COCINA.
    La cocina NUNCA puede ser más grande que la sala.
    Tolerancia del 5% sobre área.
    """
    from sle.core.spatial_graph import detectar_tipo

    salas    = [n for n, nd in grafo.nodos.items()
                if detectar_tipo(n) in ("sala", "sala_comedor")]
    comedors = [n for n, nd in grafo.nodos.items()
                if detectar_tipo(n) == "comedor"]
    cocinas  = [n for n, nd in grafo.nodos.items()
                if detectar_tipo(n) == "cocina"]

    scores: list[int] = []

    if salas and comedors:
        a_sala    = max(_area(grafo.nodos[n]) for n in salas)
        a_comedor = max(_area(grafo.nodos[n]) for n in comedors)
        umbral = a_comedor * (1 - TOLERANCIA_PROPORCION)
        if a_sala >= umbral:
            res.cumplidas.append(
                f"REGLA3 ✓ SALA ({a_sala:.1f}m²) ≥ COMEDOR ({a_comedor:.1f}m²)"
            )
            scores.append(100)
        else:
            res.violaciones.append(
                f"REGLA3 ✗ SALA ({a_sala:.1f}m²) es MENOR que COMEDOR ({a_comedor:.1f}m²)"
            )
            scores.append(0)

    if comedors and cocinas:
        a_comedor = max(_area(grafo.nodos[n]) for n in comedors)
        a_cocina  = max(_area(grafo.nodos[n]) for n in cocinas)
        umbral = a_cocina * (1 - TOLERANCIA_PROPORCION)
        if a_comedor >= umbral:
            res.cumplidas.append(
                f"REGLA3 ✓ COMEDOR ({a_comedor:.1f}m²) ≥ COCINA ({a_cocina:.1f}m²)"
            )
            scores.append(100)
        else:
            res.violaciones.append(
                f"REGLA3 ✗ COMEDOR ({a_comedor:.1f}m²) es MENOR que COCINA ({a_cocina:.1f}m²)"
            )
            scores.append(0)

    if salas and cocinas:
        a_sala   = max(_area(grafo.nodos[n]) for n in salas)
        a_cocina = max(_area(grafo.nodos[n]) for n in cocinas)
        if a_cocina > a_sala * (1 + TOLERANCIA_PROPORCION):
            res.violaciones.append(
                f"REGLA3 ✗ COCINA ({a_cocina:.1f}m²) es MAYOR que SALA ({a_sala:.1f}m²) — nunca permitido"
            )
            scores.append(0)
        else:
            scores.append(100)

    res.score_proporciones = int(sum(scores) / len(scores)) if scores else 100


def _evaluar_regla4_jerarquia_dormitorios(
    grafo: "SpatialGraph",
    res: ResultadoReglasMerlos,
) -> None:
    """
    REGLA 4 — Jerarquía de dormitorios:
    DORMITORIO PRINCIPAL siempre > todos los dormitorios secundarios.
    """
    from sle.core.spatial_graph import detectar_tipo

    principales  = [n for n in grafo.nodos if detectar_tipo(n) == "dormitorio_principal"]
    secundarios  = [n for n in grafo.nodos if detectar_tipo(n) == "dormitorio"]

    if not principales or not secundarios:
        return   # no hay jerarquía que validar

    a_principal = max(_area(grafo.nodos[n]) for n in principales)
    violaciones_dorm: list[str] = []

    for ns in secundarios:
        a_sec = _area(grafo.nodos[ns])
        umbral = a_principal * (1 - TOLERANCIA_PROPORCION)
        if a_sec > umbral:
            violaciones_dorm.append(
                f"  {ns} ({a_sec:.1f}m²) ≥ dormitorio principal ({a_principal:.1f}m²)"
            )

    if violaciones_dorm:
        res.violaciones.append(
            f"REGLA4 ✗ Dormitorios secundarios demasiado grandes:\n" +
            "\n".join(violaciones_dorm)
        )
        # Penaliza el score de proporciones
        res.score_proporciones = int(res.score_proporciones * 0.6)
    else:
        res.cumplidas.append(
            f"REGLA4 ✓ DORMITORIO PRINCIPAL ({a_principal:.1f}m²) > "
            f"{len(secundarios)} dormitorios secundarios"
        )


def _evaluar_regla5_jerarquia_banos(
    grafo: "SpatialGraph",
    subtipos_bano: dict[str, str],
    res: ResultadoReglasMerlos,
) -> None:
    """
    REGLA 5 — Jerarquía de baños:
    BAÑO PRINCIPAL > BAÑO COMPARTIDO > MEDIO BAÑO
    El baño principal puede estar integrado o adyacente al dormitorio principal.
    El medio baño es solo para visitas, sin área de ducha.
    """
    def _area_por_subtipo(subtipo: str) -> list[float]:
        return [
            _area(grafo.nodos[n])
            for n, st in subtipos_bano.items()
            if st == subtipo
        ]

    areas_principal  = _area_por_subtipo("bano_principal")
    areas_compartido = _area_por_subtipo("bano_compartido")
    areas_medio      = _area_por_subtipo("medio_bano")
    areas_invitados  = _area_por_subtipo("bano_invitados")

    scores_bano: list[int] = []

    # BAÑO PRINCIPAL > BAÑO COMPARTIDO
    if areas_principal and areas_compartido:
        a_p = max(areas_principal)
        a_c = max(areas_compartido)
        if a_p >= a_c * (1 - TOLERANCIA_PROPORCION):
            res.cumplidas.append(
                f"REGLA5 ✓ BAÑO PRINCIPAL ({a_p:.1f}m²) ≥ BAÑO COMPARTIDO ({a_c:.1f}m²)"
            )
            scores_bano.append(100)
        else:
            res.violaciones.append(
                f"REGLA5 ✗ BAÑO COMPARTIDO ({a_c:.1f}m²) > BAÑO PRINCIPAL ({a_p:.1f}m²)"
            )
            scores_bano.append(0)

    # BAÑO (cualquiera) > MEDIO BAÑO
    areas_bano_mayor = areas_principal + areas_compartido + areas_invitados
    if areas_bano_mayor and areas_medio:
        a_mayor = min(areas_bano_mayor)   # el más pequeño de los baños completos
        a_medio = max(areas_medio)
        if a_mayor > a_medio * (1 + TOLERANCIA_PROPORCION):
            res.cumplidas.append(
                f"REGLA5 ✓ Baños completos > MEDIO BAÑO ({a_medio:.1f}m²) ✓"
            )
            scores_bano.append(100)
        else:
            res.sugerencias.append(
                f"REGLA5 ○ MEDIO BAÑO ({a_medio:.1f}m²) debería ser menor que los baños completos"
            )
            scores_bano.append(70)

    # Verificar adyacencia baño principal ↔ dormitorio principal
    from sle.core.spatial_graph import detectar_tipo
    banos_principales = [n for n, st in subtipos_bano.items() if st == "bano_principal"]
    dorm_principales  = [n for n in grafo.nodos if detectar_tipo(n) == "dormitorio_principal"]

    for bp in banos_principales:
        vecinos_bp = grafo.vecinos(bp)
        adyacente = any(dp in vecinos_bp for dp in dorm_principales)
        if adyacente:
            res.cumplidas.append(
                f"REGLA5 ✓ {bp} adyacente al dormitorio principal ✓"
            )
        else:
            res.sugerencias.append(
                f"REGLA5 ○ {bp} no está adyacente al dormitorio principal "
                f"(puede ser integrado o separado según cliente)"
            )

    # Actualizar score proporciones con promedio de baños
    if scores_bano:
        avg_bano = sum(scores_bano) / len(scores_bano)
        res.score_proporciones = int(
            res.score_proporciones * 0.7 + avg_bano * 0.3
        )


def _evaluar_regla6_cochera(
    grafo: "SpatialGraph",
    res: ResultadoReglasMerlos,
) -> None:
    """
    REGLA 6 — Dimensiones mínimas de cochera (INVU / CFIA Costa Rica):
      1 auto : 2.5 m × 5.0 m
      2 autos: 5.0 m × 5.0 m
      3 autos: 7.5 m × 5.0 m
    Detecta el número de autos según el nombre del recinto o las dimensiones.
    """
    from sle.core.spatial_graph import detectar_tipo

    cocheras = [n for n in grafo.nodos if detectar_tipo(n) == "cochera"]
    if not cocheras:
        res.score_cochera = 100   # sin cochera — no aplica
        return

    scores_c: list[int] = []
    for nombre in cocheras:
        nodo = grafo.nodos[nombre]
        nombre_lower = nombre.lower()

        # Detectar número de autos
        if "3" in nombre_lower or "tres" in nombre_lower or "triple" in nombre_lower:
            n_autos = 3
        elif "2" in nombre_lower or "dos" in nombre_lower or "doble" in nombre_lower:
            n_autos = 2
        else:
            n_autos = 1   # asumir 1 auto si no se especifica

        ancho_min, largo_min = DIMENSIONES_COCHERA[n_autos]
        # ancho y alto pueden estar en cualquier orientación — usar el mayor como largo
        dim_mayor = max(nodo.ancho, nodo.alto)
        dim_menor = min(nodo.ancho, nodo.alto)

        cumple_largo = dim_mayor >= largo_min - 0.1
        cumple_ancho = dim_menor >= ancho_min - 0.1

        if cumple_largo and cumple_ancho:
            res.cumplidas.append(
                f"REGLA6 ✓ {nombre} ({dim_menor:.1f}×{dim_mayor:.1f}m) "
                f"cumple para {n_autos} auto(s) [mín {ancho_min}×{largo_min}m]"
            )
            scores_c.append(100)
        else:
            faltantes = []
            if not cumple_ancho:
                faltantes.append(f"ancho {dim_menor:.1f}m < {ancho_min}m mínimo")
            if not cumple_largo:
                faltantes.append(f"largo {dim_mayor:.1f}m < {largo_min}m mínimo")
            res.violaciones.append(
                f"REGLA6 ✗ {nombre} ({dim_menor:.1f}×{dim_mayor:.1f}m) "
                f"no cumple para {n_autos} auto(s): {', '.join(faltantes)}"
            )
            scores_c.append(0)

    res.score_cochera = int(sum(scores_c) / len(scores_c))


# ─── Helpers ────────────────────────────────────────────────────────

def _zona_es_contigua(grafo: "SpatialGraph", nombres: set[str]) -> bool:
    """
    Verifica que todos los recintos en `nombres` estén conectados entre sí
    (directamente o a través de otros recintos del mismo conjunto).
    """
    if len(nombres) <= 1:
        return True
    inicio = next(iter(nombres))
    visitados: set[str] = {inicio}
    cola: list[str] = [inicio]
    while cola:
        actual = cola.pop()
        for vecino in grafo.vecinos(actual):
            if vecino in nombres and vecino not in visitados:
                visitados.add(vecino)
                cola.append(vecino)
    return visitados == nombres


def _humedas_agrupadas(grafo: "SpatialGraph", nombres_humedos: list[str]) -> bool:
    """
    Verifica que al menos la mitad de los húmedos sean adyacentes entre sí.
    """
    if len(nombres_humedos) <= 1:
        return True
    conjunto = set(nombres_humedos)
    con_vecino_humedo = 0
    for n in nombres_humedos:
        vecinos = grafo.vecinos(n)
        if any(v in conjunto for v in vecinos):
            con_vecino_humedo += 1
    return con_vecino_humedo >= len(nombres_humedos) / 2


# ═══════════════════════════════════════════════════════════════════════
# GUÍA DE DISEÑO (texto para inyectar al AI)
# ═══════════════════════════════════════════════════════════════════════

def guia_orientacion_para_prompt(norte_real: str = "norte") -> str:
    """
    Retorna el bloque de texto con las reglas de orientación de Merlos.
    Para inyectar en el system prompt o user message al generar un plan.

    norte_real: dirección del norte en el lote ('norte'|'sur'|'este'|'oeste')
    """
    return f"""
REGLAS DE DISEÑO — Arq. Joseph Merlos (Estudio Merlos, Costa Rica)

REGLA 1 — ZONIFICACIÓN:
  Pública  → sala, comedor, cocina, baño de invitados, cochera, vestíbulo
  Privada  → dormitorios, sala TV, baño principal, baño compartido
  Húmedas  → baños, cuarto de pilas, lavandería, piscina
  El área privada SIEMPRE va junta (contígua). Nunca separada.
  Las húmedas se agrupan: baños, pilas y piscina lo más juntos posible.
  Baño invitados → adyacente a sala. Baño compartido → entre los dormitorios.

REGLA 2 — ORIENTACIÓN CARDINAL (norte del lote = {norte_real.upper()}):
  NORTE    → Mejores espacios públicos (sala > comedor > cocina).
             Ventanas principales aquí. Luz pareja, sin calor, no penetra horizontal.
  SUR      → Dormitorios (privados). Ideal. Luz angular controlable con aleros.
  SURESTE  → Dormitorios. Muy bueno — casi igual que sur. Amanecer suave + privacidad.
  SUROESTE → Dormitorios secundarios aceptable. También húmedas y servicios preferidos.
  ESTE     → Dormitorios secundarios aceptable (amanecer suave). Área privada siempre junta.
  OESTE    → PEOR orientación. Solo áreas húmedas (baños, pilas, cochera).
             Da calor de tarde y encandila — controlar con tapias o parasoles.
  NORESTE  → Buena luz. Comedor, cocina, estudio, cochera.
  NOROESTE → Aceptable. Cocina. Evitar dormitorios y sala.

ZONA PRIVADA — orientaciones válidas (de mejor a peor):
  1. SUR       → ideal, privacidad total
  2. SURESTE   → excelente, luz suave de mañana
  3. SUROESTE  → bueno, funciona bien con aleros
  4. ESTE      → aceptable para secundarios

ORDEN DE PRIORIDAD al orientar:
  1. Sala al norte (siempre)
  2. Dormitorios al sur / sureste / suroeste / este (siempre juntos)
  3. Húmedas al oeste / suroeste (siempre agrupadas)
  4. Cocina y comedor: norte o noreste

REGLA 3 — JERARQUÍA DE ÁREAS PÚBLICAS:
  SALA > COMEDOR > COCINA (en área, tolerancia ±5%)
  La cocina NUNCA puede ser más grande que la sala.
  Si el presupuesto lo permite, el comedor puede igualar la cocina pero nunca superarla.

REGLA 4 — JERARQUÍA DE DORMITORIOS:
  DORMITORIO PRINCIPAL > TODOS los dormitorios secundarios (en área).
  El dormitorio principal debe ser claramente el mayor. Sin excepciones.

REGLA 5 — JERARQUÍA DE BAÑOS:
  BAÑO PRINCIPAL > BAÑO COMPARTIDO > MEDIO BAÑO (en área).
  Medio bano = solo inodoro + lavamanos (sin ducha), area <= 2.5 m2. Uso de visitas.
  El baño principal debe ser adyacente o integrado al dormitorio principal.
  El baño compartido se ubica entre los dormitorios secundarios.
  Orientacion ideal de baños: oeste, suroeste, sureste (zona sur/oeste siempre).

REGLA 6 — COCHERA (norma INVU/CFIA Costa Rica):
  1 auto  → mínimo 2.50 m × 5.00 m
  2 autos → mínimo 5.00 m × 5.00 m
  3 autos → mínimo 7.50 m × 5.00 m
  Siempre en zona pública. Idealmente al oeste o noreste del lote.
"""


def texto_analisis_merlos(resultado: ResultadoReglasMerlos) -> str:
    """Formatea el resultado del análisis para mostrar en GUI o log (ASCII seguro)."""

    def _barra(score: int, ancho: int = 20) -> str:
        """Barra de progreso ASCII. score 0-100."""
        lleno = round(score * ancho / 100)
        return "[" + "#" * lleno + "-" * (ancho - lleno) + f"] {score:3d}/100"

    lineas = [
        f"==========================================",
        f"  REGLAS MERLOS -- Score: {resultado.score_total:3d}/100",
        f"==========================================",
        f"",
        f"  R2 Orientacion cardinal : {_barra(resultado.score_orientacion)}",
        f"  R1 Zonificacion         : {_barra(resultado.score_zonificacion)}",
        f"  R1 Humedas agrupadas    : {_barra(resultado.score_humedas)}",
        f"  R3-5 Proporciones       : {_barra(resultado.score_proporciones)}",
        f"  R6 Cochera (INVU)       : {_barra(resultado.score_cochera)}",
        f"",
    ]

    if resultado.cumplidas:
        lineas.append("  [OK] Cumplidas:")
        for c in resultado.cumplidas:
            lineas.append(f"       + {c}")
        lineas.append("")

    if resultado.violaciones:
        lineas.append("  [!!] Violaciones (corregir):")
        for v in resultado.violaciones:
            lineas.append(f"       x {v}")
        lineas.append("")

    if resultado.sugerencias:
        lineas.append("  [--] Sugerencias:")
        for s in resultado.sugerencias:
            lineas.append(f"       > {s}")
        lineas.append("")

    # Notas del arquitecto — R1/R2 justificadas por decision de diseno
    notas = getattr(resultado, "notas_arquitecto", [])
    if notas:
        lineas.append("  [ARQ] Notas bioclimaticas (diagrama del arquitecto manda):")
        for n in notas:
            lineas.append(f"       ~ {n}")
        lineas.append("")

    lineas.append("  Detalle por recinto:")
    lineas.append("  " + "-" * 60)
    for nombre, datos in resultado.por_recinto.items():
        sc = datos["score_orientacion"]
        orient = datos["orientacion"]
        tipo = datos["tipo_efectivo"]
        icon = "[OK]" if sc >= 1 else ("[--]" if sc == 0 else "[!!]")
        lineas.append(
            f"    {icon} {nombre:22} [{tipo:20}] {orient:10} score={sc:+d}"
        )

    return "\n".join(lineas)


# ═══════════════════════════════════════════════════════════════════════
# SMOKE TEST
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from sle.core.spatial_graph import SpatialGraph

    # Plan de prueba basado en PLANO VIVIANA
    plan = {
        "grid": {"ancho_m": 6, "alto_m": 10},
        "recintos": [
            {"nombre": "SALA",       "fila": 0, "col": 2, "ancho": 3, "alto": 3},
            {"nombre": "COMEDOR",    "fila": 0, "col": 2, "ancho": 3, "alto": 3},
            {"nombre": "COCINA",     "fila": 0, "col": 0, "ancho": 3, "alto": 3},
            {"nombre": "DORMITORIO", "fila": 4, "col": 0, "ancho": 3, "alto": 3},
            {"nombre": "DORMITORIO2","fila": 7, "col": 0, "ancho": 3, "alto": 3},
            {"nombre": "BAÑO",       "fila": 4, "col": 3, "ancho": 2, "alto": 2},
            {"nombre": "PASILLO",    "fila": 3, "col": 0, "ancho": 1, "alto": 1},
            {"nombre": "VESTIBULO",  "fila": 2, "col": 2, "ancho": 1, "alto": 1},
        ],
        "puertas": [
            {"tipo": "exterior", "recinto": "SALA", "lado": "norte", "ancho": 1.1},
            {"tipo": "entre_recintos", "recinto1": "PASILLO", "recinto2": "DORMITORIO", "ancho": 1.0},
            {"tipo": "entre_recintos", "recinto1": "PASILLO", "recinto2": "BAÑO", "ancho": 0.9},
        ],
    }

    g = SpatialGraph.from_json(plan)
    resultado = analizar_reglas_merlos(g)
    print(texto_analisis_merlos(resultado))
    print(f"\nGuía de diseño:\n{guia_orientacion_para_prompt()}")
