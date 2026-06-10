"""
sle/data/generador_sintetico.py
================================
Generador de plantas arquitectónicas sintéticas para el SLE.

Genera plantas JSON válidas siguiendo las Reglas Merlos y la normativa
INVU/CFIA de Costa Rica. Cada planta generada puede importarse directamente
al SLE sin necesidad de DWGs ni limpieza manual.

USO:
    python sle/data/generador_sintetico.py --cantidad 50 --importar
    python sle/data/generador_sintetico.py --cantidad 100 --salida plantas/
    from sle.data.generador_sintetico import generar_batch, importar_al_sle

ESTRATEGIA DE LAYOUT (Reglas Merlos):
    Norte (fila 0)  → Zona pública: sala, comedor, cocina
    Sur  (fila max) → Zona privada: dormitorios
    Oeste (col 0)   → Zona húmeda/cochera: cochera, baños, lavandería
    Este (col max)  → Dormitorios secundarios, baño principal
"""
from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# ─── Normativa INVU/CFIA — áreas mínimas en m² ───────────────────────

AREAS_MIN = {
    "SALA":                  10.0,
    "SALA_COMEDOR":          10.0,
    "COMEDOR":                6.0,
    "COCINA":                 5.0,
    "DORMITORIO_PRINCIPAL":   9.0,
    "DORMITORIO":             7.5,
    # ── Baños — 3 tipos reales (Arq. Merlos) ────────────────────────
    # Medio baño : 1.40×1.40 = 1.96 m²  — visitas, SIN ducha, zona pública
    # Baño compartido : 2.20×1.40 = 3.08 m² — completo, dorms secundarios
    # Baño principal  : 2.20×2.20 = 4.84 m² — puede tener jacuzzi, dorm principal
    "MEDIO_BANO":             1.80,   # ≈ 1.40×1.40
    "BANO_COMPARTIDO":        2.80,   # ≈ 2.20×1.40
    "BANO_PRINCIPAL":         4.50,   # ≈ 2.20×2.20+
    "LAVANDERIA":             2.0,
    "COCHERA":               12.5,   # 2.5m × 5.0m mínimo 1 auto
    "VESTIBULO":              2.0,
    "PASILLO":                1.2,   # ancho mínimo 1.2m (norma)
}

AREAS_MAX = {
    "SALA":                  30.0,
    "SALA_COMEDOR":          35.0,
    "COMEDOR":               16.0,
    "COCINA":                14.0,
    "DORMITORIO_PRINCIPAL":  20.0,
    "DORMITORIO":            14.0,
    "MEDIO_BANO":             2.5,   # si supera 2.5 ya no es medio baño
    "BANO_COMPARTIDO":        5.0,
    "BANO_PRINCIPAL":        10.0,   # tina / jacuzzi
    "LAVANDERIA":             5.0,
    "COCHERA":               25.0,   # 2 autos
    "VESTIBULO":              5.0,
    "PASILLO":                6.0,
}

# Dimensiones reales por recinto (ancho, alto) en metros — floats permitidos
# Fuente: práctica del Arq. Joseph Merlos, Costa Rica
DIMS_TIPICAS: dict[str, list[tuple[float, float]]] = {
    "SALA":                 [(4,3),(5,3),(4,4),(5,4),(6,3),(5,5),(6,4)],
    "SALA_COMEDOR":         [(5,4),(6,4),(5,5),(6,5),(7,4),(4,5),(8,4),(7,5)],
    "COMEDOR":              [(3,3),(4,3),(3,4),(4,4),(5,3)],
    "COCINA":               [(3,2),(3,3),(4,2),(4,3),(2,3),(5,3)],
    "DORMITORIO_PRINCIPAL": [(4,3),(4,4),(5,3),(5,4),(3,4),(4,5),(5,5)],
    "DORMITORIO":           [(3,3),(4,3),(3,4),(4,3),(3,3),(4,4)],
    "MEDIO_BANO":           [(1.4,1.4),(1.5,1.4),(1.4,1.5)],
    "BANO_COMPARTIDO":      [(2.2,1.4),(2.0,1.5),(2.2,1.5)],
    "BANO_PRINCIPAL":       [(2.2,2.2),(3.0,2.0),(2.5,2.2),(3.5,2.0),(3.0,2.5)],
    "LAVANDERIA":           [(2,2),(3,2),(2,3),(3,1),(4,2)],
    "COCHERA":              [(3,5),(5,5),(3,6),(5,6),(6,6),(3,7)],
    "VESTIBULO":            [(2,2),(2,1),(1,2),(3,1),(3,2)],
    "PASILLO":              [(4,1),(5,1),(6,1),(3,1),(2,1),(7,1)],
    # ── Recintos adicionales ────────────────────────────────────────
    "SALA_TV":              [(3,3),(4,3),(3,4),(4,4)],
    "ESTUDIO":              [(3,3),(4,3),(3,4),(2,3),(4,4)],
    "TERRAZA":              [(3,2),(4,2),(5,2),(4,3),(3,3)],
    "CUARTO_SERVICIO":      [(3,3),(2,3),(3,4)],
}


# ─── Estructuras de datos ─────────────────────────────────────────────

@dataclass
class Recinto:
    nombre: str
    fila:   float
    col:    float
    ancho:  float   # metros (puede ser decimal: 1.4, 2.2, etc.)
    alto:   float   # metros

    @property
    def area(self) -> float:
        return round(self.ancho * self.alto, 2)

    def to_dict(self) -> dict:
        return {
            "nombre": self.nombre,
            "fila":   self.fila,
            "col":    self.col,
            "ancho":  self.ancho,
            "alto":   self.alto,
        }


@dataclass
class Puerta:
    tipo:     str          # 'exterior' | 'entre_recintos'
    recinto:  str = ""     # para exterior
    recinto1: str = ""     # para entre_recintos
    recinto2: str = ""
    lado:     str = "norte"  # norte|sur|este|oeste (solo exterior)
    ancho:    float = 1.0

    def to_dict(self) -> dict:
        if self.tipo == "exterior":
            return {"tipo": self.tipo, "recinto": self.recinto,
                    "lado": self.lado, "ancho": self.ancho}
        return {"tipo": self.tipo, "recinto1": self.recinto1,
                "recinto2": self.recinto2, "ancho": self.ancho}


# ─── Utilidades ───────────────────────────────────────────────────────

def _dim(nombre: str, rng: random.Random) -> tuple[int, int]:
    """Elige dimensiones aleatorias válidas para el recinto."""
    opciones = DIMS_TIPICAS.get(nombre, [(3, 3)])
    return rng.choice(opciones)


def _son_adyacentes(a: Recinto, b: Recinto) -> bool:
    """True si dos recintos comparten al menos 1m de pared."""
    # Adyacentes en eje horizontal (comparten cara vertical)
    horiz = (
        a.fila < b.fila + b.alto and
        b.fila < a.fila + a.alto and
        (a.col + a.ancho == b.col or b.col + b.ancho == a.col)
    )
    # Adyacentes en eje vertical (comparten cara horizontal)
    vert = (
        a.col < b.col + b.ancho and
        b.col < a.col + a.ancho and
        (a.fila + a.alto == b.fila or b.fila + b.alto == a.fila)
    )
    return horiz or vert


# ─── Motor de generación ──────────────────────────────────────────────

def _generar_planta(
    n_dormitorios: int,
    tiene_cochera: bool,
    tiene_lavanderia: bool,
    sala_comedor_integrado: bool,
    norte_lote: str,
    rng: random.Random,
    extras: list[str] | None = None,
) -> dict:
    """
    Genera una planta siguiendo las Reglas Merlos.

    Layout optimizado — HÚMEDAS AL OESTE, DORMITORIOS AL SUR:

    ┌──────┬───────┬─────────────────┬─────────┬────────┐  Norte
    │COCHE │MEDIO  │ SALA /          │ COMEDOR │ COCINA │  fila 0
    │RA    │ BANO  │ SALA-COMEDOR    │         │        │
    │(NW)  │ (NW)  │   (Norte)       │  (N/NE) │ (NE)   │
    ├──────┴───────┴─────────────────┴─────────┴────────┤
    │        VESTIBULO (transición, fila completa)       │
    ├───────────────┬────────────────────────────────────┤  Sur
    │BANO_COMPARTIDO│  DORM_2  (sur/suroeste)             │
    │   col=0 (O)   │  DORM_3  (sur/suroeste)             │
    │LAVANDERIA     │                                    │
    │   col=0 (O/SO)│                                    │
    │BANO_PRINCIPAL │  DORM_PRINCIPAL (sur/suroeste)      │
    │   col=0 (SO)  │    col=bpw — más al sur de todos   │
    └───────────────┴────────────────────────────────────┘

    Cochera     → Noroeste (+1/+2) ✓
    Medio baño  → Noroeste (+1)    ✓ — acceso desde vestíbulo
    Sala        → Norte (+2)       ✓✓
    Baños/lavan → Oeste (+2)       ✓✓ — columna húmeda col=0
    Dorms sec.  → Sur/Suroeste     ✓  — centrados horizontalmente
    Dorm princ. → Sur/Suroeste     ✓✓ — el más profundo al sur
    """
    recintos: list[Recinto] = []
    puertas:  list[Puerta]  = []

    # ── 0. Cochera (Noroeste, col=0) ──────────────────────────────────
    cochera_rec = None
    col_pub = 0  # la zona pública empieza aquí
    if tiene_cochera:
        ccw, cch = _dim("COCHERA", rng)
        cochera_rec = Recinto("COCHERA", 0, 0, ccw, cch)
        recintos.append(cochera_rec)
        col_pub = ccw

    # ── 0b. Medio baño de visitas (Noroeste, pegado a cochera/entrada) ─
    # 1.40×1.40 — sin ducha, para visitas. Va junto a la cochera/entrada
    # en el lado OESTE de la zona pública → húmeda al oeste ✓
    mbw, mbh = _dim("MEDIO_BANO", rng)
    medio_bano = Recinto("MEDIO_BANO", 0, col_pub, mbw, mbh)
    recintos.append(medio_bano)
    col_sala = col_pub + mbw   # sala empieza después del medio baño

    # ── 1. Zona pública (Norte, centro/este) ──────────────────────────
    cur_col = col_sala
    fila_pub = 0

    if sala_comedor_integrado:
        aw, ah = _dim("SALA_COMEDOR", rng)
        sala = Recinto("SALA_COMEDOR", fila_pub, cur_col, aw, ah)
        recintos.append(sala)
        cur_col += aw
    else:
        sw, sh = _dim("SALA", rng)
        sala = Recinto("SALA", fila_pub, cur_col, sw, sh)
        recintos.append(sala)
        cur_col += sw
        cw2, ch2 = _dim("COMEDOR", rng)
        comedor = Recinto("COMEDOR", fila_pub, cur_col, cw2, min(ch2, sh))
        recintos.append(comedor)
        cur_col += cw2

    kw, kh = _dim("COCINA", rng)
    cocina = Recinto("COCINA", fila_pub, cur_col, kw, kh)
    recintos.append(cocina)
    cur_col += kw

    # Ancho base de la zona pública (sin contar cochera aún)
    ancho_pub = cur_col   # col_pub + mbw + sala + comedor + cocina

    # Alto máximo de la zona pública (excluyendo cochera)
    pub_recintos = [r for r in recintos if r.nombre != "COCHERA"]
    alto_pub = max(r.fila + r.alto for r in pub_recintos)

    # Cochera: extender su alto para que llegue al vestíbulo
    if cochera_rec and cochera_rec.alto < alto_pub:
        cochera_rec.alto = alto_pub

    # Ancho total inicial = ancho de la zona pública
    ancho_total = ancho_pub

    # ── 2. Vestíbulo de transición (fila completa) ────────────────────
    vh = 1.0
    vestibulo = Recinto("VESTIBULO", alto_pub, 0, ancho_total, vh)
    recintos.append(vestibulo)
    fila_priv = alto_pub + vh

    # ── 3. Zona privada — diseño con HÚMEDAS AL OESTE ─────────────────
    #
    # COLUMNA OESTE (col=0): baños + lavandería
    #   ┌─ BANO_COMPARTIDO (alineado con dorms secundarios)
    #   ├─ LAVANDERIA (debajo del baño compartido)
    #   └─ BANO_PRINCIPAL (alineado con DORM_PRINCIPAL, al fondo)
    #
    # COLUMNA DORMS (col=col_dorm): centrada horizontalmente
    #   ┌─ DORM_2, DORM_3... (secundarios, parte superior)
    #   └─ DORM_PRINCIPAL (al fondo — más al sur de todos)
    #
    # Así: baños quedan "oeste/suroeste", dorms quedan "sur/suroeste"

    # ── 3a. Dimensiones de los baños (necesarias para calcular columnas) ─
    bpw, bph = _dim("BANO_PRINCIPAL", rng)    # 2.20-3.50 × 2.00-2.20
    bw,  bh  = _dim("BANO_COMPARTIDO", rng)   # 2.20 × 1.40

    # Ancho de la columna húmeda = máximo entre bano_principal y bano_compartido
    col_humedo_ancho = max(bw, bpw)

    # Columna de dormitorios: centrada en el grid para orientación "sur" ✓
    # Se calcula DESPUÉS de saber ancho_total (público)
    # col_dorm ≈ 35-45% del ancho total — ni muy oeste ni muy este
    col_dorm = max(col_humedo_ancho, round(ancho_total * 0.35, 1))

    # ── 3b. Dormitorios secundarios (parte superior zona privada) ─────
    fila_dorm_sec = fila_priv
    dorms_sec: list[Recinto] = []

    for i in range(n_dormitorios - 1):
        dw, dh = _dim("DORMITORIO", rng)
        nombre = f"DORMITORIO_{i+2}"
        dorm = Recinto(nombre, fila_dorm_sec, col_dorm, dw, dh)
        recintos.append(dorm)
        dorms_sec.append(dorm)
        fila_dorm_sec += dh

    # ── 3c. Baño compartido (Oeste, alineado con dorms secundarios) ───
    # Altura = span de los dorms secundarios para que sean adyacentes
    if dorms_sec:
        altura_sec = fila_dorm_sec - fila_priv
        # Si el baño compartido es más alto que los dorms, lo ajustamos
        bh_real = min(bh, altura_sec) if altura_sec > 0 else bh
        bano_comp = Recinto("BANO_COMPARTIDO", fila_priv, 0, bw, bh_real)
    else:
        # Sin dorms secundarios: igual lo colocamos en la zona privada
        bano_comp = Recinto("BANO_COMPARTIDO", fila_priv, 0, bw, bh)
    recintos.append(bano_comp)

    # ── 3d. Lavandería (Oeste, debajo del baño compartido) ────────────
    fila_lav = max(bano_comp.fila + bano_comp.alto,
                   fila_priv + (dorms_sec[-1].alto if dorms_sec else 0))
    if tiene_lavanderia:
        lw, lh = _dim("LAVANDERIA", rng)
        lavanderia = Recinto("LAVANDERIA", fila_lav, 0, lw, lh)
        recintos.append(lavanderia)
        fila_lav += lh

    # ── 3e. Dormitorio principal (el más al SUR — máxima privacidad) ──
    # Va AL FONDO de la zona privada para obtener orientación "sur" ✓✓
    fila_dorm_prin = max(fila_dorm_sec, fila_lav)

    dpw, dph = _dim("DORMITORIO_PRINCIPAL", rng)
    # REGLA 4: dorm principal > todos los secundarios
    for ds in dorms_sec:
        intentos = 0
        while dpw * dph <= ds.ancho * ds.alto and intentos < 10:
            dpw, dph = _dim("DORMITORIO_PRINCIPAL", rng)
            intentos += 1

    dorm_principal = Recinto("DORMITORIO_PRINCIPAL", fila_dorm_prin, col_dorm, dpw, dph)
    recintos.append(dorm_principal)

    # ── 3f. Baño principal (Oeste, junto al dormitorio principal) ─────
    # También en la columna oeste → orientación "oeste/suroeste" (+2) ✓
    bph_real = min(bph, dph)
    bano_princ = Recinto("BANO_PRINCIPAL", fila_dorm_prin, 0, bpw, bph_real)
    recintos.append(bano_princ)

    # ── 4. Ajustar ancho_total y vestíbulo ────────────────────────────
    ancho_priv = col_dorm + max(
        dpw,
        *(ds.ancho for ds in dorms_sec) if dorms_sec else (0,),
    )
    ancho_total = max(ancho_total, ancho_priv)
    vestibulo.ancho = ancho_total

    # ── 5. Calcular dimensiones finales del grid ──────────────────────
    alto_total = max(r.fila + r.alto for r in recintos)

    # ── 6. Generar puertas ────────────────────────────────────────────
    # Acceso principal → sala/sala_comedor por el norte
    sala_rec = next((r for r in recintos
                     if r.nombre in ("SALA", "SALA_COMEDOR")), None)
    if sala_rec:
        puertas.append(Puerta("exterior", recinto=sala_rec.nombre,
                              lado="norte", ancho=1.1))

    # Cochera → exterior al norte
    if cochera_rec:
        puertas.append(Puerta("exterior", recinto="COCHERA",
                              lado="norte", ancho=2.5))

    # ── 6b. Recintos extras (estudio, sala TV, terraza, cuarto servicio) ─
    extras_lista = extras or []
    for extra_nombre in extras_lista:
        ew, eh = _dim(extra_nombre, rng)
        # Posición: al este de la zona pública (junto a cocina o al final)
        # o al sur junto a dormitorios según tipo
        if extra_nombre in ("ESTUDIO", "SALA_TV"):
            # Zona pública, al este de la cocina
            cocina_rec = next((r for r in recintos if r.nombre == "COCINA"), None)
            if cocina_rec:
                extra_rec = Recinto(extra_nombre, fila_pub,
                                    cocina_rec.col + cocina_rec.ancho, ew, eh)
                recintos.append(extra_rec)
                # Actualizar ancho total
                ancho_total = max(ancho_total, extra_rec.col + extra_rec.ancho)
                # Extender vestíbulo
                vestibulo.ancho = ancho_total
        elif extra_nombre == "TERRAZA":
            # Al norte, antes de la sala (frente al jardín)
            sala_rec2 = next((r for r in recintos
                              if r.nombre in ("SALA", "SALA_COMEDOR")), None)
            if sala_rec2:
                extra_rec = Recinto(extra_nombre, -eh,
                                    sala_rec2.col, ew, eh)
                recintos.append(extra_rec)
        elif extra_nombre == "CUARTO_SERVICIO":
            # Al sur, junto a la lavandería o al este de húmedas
            extra_rec = Recinto(extra_nombre, fila_priv + 1.0,
                                0, ew, eh)
            recintos.append(extra_rec)

    # Conexiones interiores (adyacencias con puerta)
    _agregar_puertas_adyacentes(recintos, puertas, rng)

    # ── 7. Prompt descriptivo ─────────────────────────────────────────
    n_banos = _contar_banos(recintos)
    extras_desc = []
    if tiene_cochera:            extras_desc.append("cochera")
    if tiene_lavanderia:         extras_desc.append("lavandería")
    if sala_comedor_integrado:   extras_desc.append("sala-comedor integrado")
    for e in extras_lista:
        extras_desc.append(e.lower().replace("_", " "))
    extras_txt = (", " + ", ".join(extras_desc)) if extras_desc else ""
    area_aprox = sum(r.area for r in recintos if r.fila >= 0)
    prompt = (
        f"Casa de {n_dormitorios} dormitorio{'s' if n_dormitorios>1 else ''}, "
        f"{n_banos} baño{'s' if n_banos>1 else ''}, "
        f"sala, comedor, cocina{extras_txt}, "
        f"aproximadamente {round(area_aprox)} m², norte del lote: {norte_lote}"
    )

    return {
        "prompt": prompt,
        "plan": {
            "grid": {
                "ancho_m": ancho_total,
                "alto_m":  alto_total,
            },
            "recintos": [r.to_dict() for r in recintos],
            "puertas":  [p.to_dict() for p in puertas],
        }
    }


def _contar_banos(recintos: list[Recinto]) -> int:
    """Cuenta todos los tipos de baño (incluyendo medio baño)."""
    return sum(1 for r in recintos
               if any(k in r.nombre for k in ("BANO", "MEDIO_BANO")))


def _agregar_puertas_adyacentes(
    recintos: list[Recinto],
    puertas: list[Puerta],
    rng: random.Random,
) -> None:
    """Agrega puertas entre recintos adyacentes siguiendo jerarquía funcional."""

    # Pares prioritarios (siempre conectar si son adyacentes)
    prioridad = [
        # Zona pública
        ("VESTIBULO",           "SALA"),
        ("VESTIBULO",           "SALA_COMEDOR"),
        ("SALA",                "COMEDOR"),
        ("COMEDOR",             "COCINA"),
        ("SALA_COMEDOR",        "COCINA"),
        ("COCHERA",             "SALA"),
        ("COCHERA",             "SALA_COMEDOR"),
        ("COCHERA",             "VESTIBULO"),
        # Medio baño: acceso desde zona pública o vestíbulo
        # Queda al oeste (junto a cochera/entrada) — para visitas únicamente
        ("MEDIO_BANO",          "SALA"),
        ("MEDIO_BANO",          "SALA_COMEDOR"),
        ("MEDIO_BANO",          "COCHERA"),
        ("MEDIO_BANO",          "VESTIBULO"),
        # Zona privada
        ("DORMITORIO_PRINCIPAL","BANO_PRINCIPAL"),
        ("VESTIBULO",           "DORMITORIO_PRINCIPAL"),
        ("VESTIBULO",           "PASILLO"),
    ]

    nombres = {r.nombre: r for r in recintos}
    ya_conectados: set[frozenset] = set()

    def _conectar(n1: str, n2: str, ancho: float = 1.0):
        par = frozenset([n1, n2])
        if par in ya_conectados:
            return
        r1 = nombres.get(n1)
        r2 = nombres.get(n2)
        if r1 and r2 and _son_adyacentes(r1, r2):
            puertas.append(Puerta("entre_recintos",
                                  recinto1=n1, recinto2=n2, ancho=ancho))
            ya_conectados.add(par)

    # Prioridad explícita
    for n1, n2 in prioridad:
        _conectar(n1, n2)

    # Dormitorios secundarios → vestíbulo
    dorms_sec = [r.nombre for r in recintos
                 if r.nombre.startswith("DORMITORIO_") and
                 r.nombre != "DORMITORIO_PRINCIPAL"]
    for dn in dorms_sec:
        _conectar("VESTIBULO", dn)

    # Dormitorio principal → vestíbulo
    _conectar("VESTIBULO", "DORMITORIO_PRINCIPAL")

    # Baño compartido → dormitorios secundarios
    for dn in dorms_sec:
        _conectar("BANO_COMPARTIDO", dn)

    # Lavandería → cocina o vestíbulo
    _conectar("LAVANDERIA", "COCINA")
    _conectar("LAVANDERIA", "VESTIBULO")


# ─── API pública ──────────────────────────────────────────────────────

def generar_batch(
    cantidad: int = 50,
    perfiles: list[dict] | None = None,
    seed: int | None = None,
) -> list[dict]:
    """
    Genera `cantidad` plantas sintéticas variadas.

    perfiles: lista de dicts con parámetros opcionales. Si es None,
              se generan perfiles aleatorios dentro de rangos realistas.

    Retorna lista de dicts {"prompt": ..., "plan": {...}, "score": int}
    """
    rng = random.Random(seed)
    resultados = []

    perfiles_default = [
        # ── 1 dormitorio — apartamento / casa mínima ─────────────────
        {"n_dormitorios": 1, "tiene_cochera": False, "tiene_lavanderia": True,
         "sala_comedor_integrado": True,  "norte_lote": "norte",
         "extras": []},
        {"n_dormitorios": 1, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": True,  "norte_lote": "norte",
         "extras": []},
        {"n_dormitorios": 1, "tiene_cochera": False, "tiene_lavanderia": False,
         "sala_comedor_integrado": True,  "norte_lote": "este",
         "extras": ["ESTUDIO"]},
        {"n_dormitorios": 1, "tiene_cochera": False, "tiene_lavanderia": True,
         "sala_comedor_integrado": True,  "norte_lote": "sur",
         "extras": ["TERRAZA"]},

        # ── 2 dormitorios — casa pequeña ─────────────────────────────
        {"n_dormitorios": 2, "tiene_cochera": False, "tiene_lavanderia": True,
         "sala_comedor_integrado": True,  "norte_lote": "norte",
         "extras": []},
        {"n_dormitorios": 2, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "norte",
         "extras": []},
        {"n_dormitorios": 2, "tiene_cochera": False, "tiene_lavanderia": False,
         "sala_comedor_integrado": True,  "norte_lote": "este",
         "extras": []},
        {"n_dormitorios": 2, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": True,  "norte_lote": "sur",
         "extras": []},
        {"n_dormitorios": 2, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "oeste",
         "extras": ["ESTUDIO"]},
        {"n_dormitorios": 2, "tiene_cochera": False, "tiene_lavanderia": True,
         "sala_comedor_integrado": True,  "norte_lote": "norte",
         "extras": ["TERRAZA"]},
        {"n_dormitorios": 2, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "norte",
         "extras": ["SALA_TV"]},

        # ── 3 dormitorios — casa mediana ─────────────────────────────
        {"n_dormitorios": 3, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "norte",
         "extras": []},
        {"n_dormitorios": 3, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": True,  "norte_lote": "norte",
         "extras": []},
        {"n_dormitorios": 3, "tiene_cochera": False, "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "norte",
         "extras": []},
        {"n_dormitorios": 3, "tiene_cochera": True,  "tiene_lavanderia": False,
         "sala_comedor_integrado": False, "norte_lote": "oeste",
         "extras": []},
        {"n_dormitorios": 3, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "sur",
         "extras": ["ESTUDIO"]},
        {"n_dormitorios": 3, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": True,  "norte_lote": "este",
         "extras": ["SALA_TV"]},
        {"n_dormitorios": 3, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "norte",
         "extras": ["TERRAZA", "SALA_TV"]},
        {"n_dormitorios": 3, "tiene_cochera": False, "tiene_lavanderia": True,
         "sala_comedor_integrado": True,  "norte_lote": "sur",
         "extras": ["CUARTO_SERVICIO"]},
        {"n_dormitorios": 3, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "norte",
         "extras": ["ESTUDIO", "TERRAZA"]},

        # ── 4 dormitorios — casa grande ──────────────────────────────
        {"n_dormitorios": 4, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "norte",
         "extras": []},
        {"n_dormitorios": 4, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "sur",
         "extras": []},
        {"n_dormitorios": 4, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "norte",
         "extras": ["ESTUDIO"]},
        {"n_dormitorios": 4, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": True,  "norte_lote": "norte",
         "extras": ["SALA_TV", "TERRAZA"]},
        {"n_dormitorios": 4, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "este",
         "extras": ["CUARTO_SERVICIO"]},

        # ── 5 dormitorios — casa residencial grande ──────────────────
        {"n_dormitorios": 5, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "norte",
         "extras": ["ESTUDIO"]},
        {"n_dormitorios": 5, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "norte",
         "extras": ["SALA_TV", "CUARTO_SERVICIO"]},
        {"n_dormitorios": 5, "tiene_cochera": True,  "tiene_lavanderia": True,
         "sala_comedor_integrado": False, "norte_lote": "sur",
         "extras": ["ESTUDIO", "TERRAZA"]},
    ]

    pool = perfiles or perfiles_default
    generados  = 0
    intentos   = 0
    max_intentos = cantidad * 10

    while generados < cantidad and intentos < max_intentos:
        intentos += 1
        perfil = rng.choice(pool)
        try:
            planta = _generar_planta(
                n_dormitorios          = perfil["n_dormitorios"],
                tiene_cochera          = perfil["tiene_cochera"],
                tiene_lavanderia       = perfil["tiene_lavanderia"],
                sala_comedor_integrado = perfil["sala_comedor_integrado"],
                norte_lote             = perfil["norte_lote"],
                rng                    = rng,
                extras                 = perfil.get("extras", []),
            )
            score = _calcular_score(planta["plan"])
            planta["score"] = score

            if score >= 60:
                resultados.append(planta)
                generados += 1
        except Exception:
            continue

    return resultados


def _calcular_score(plan: dict) -> int:
    """Calcula el score Merlos de un plan. Retorna 0-100."""
    try:
        from sle.core.spatial_graph import SpatialGraph
        from sle.core.reglas_merlos import analizar_reglas_merlos
        g = SpatialGraph.from_json(plan)
        resultado = analizar_reglas_merlos(g)
        return resultado.score_total
    except Exception:
        return 75  # score neutro si falla el análisis


def importar_al_sle(
    plantas: list[dict],
    score_minimo: int = 60,
    verbose: bool = True,
) -> tuple[int, int]:
    """
    Importa una lista de plantas al SLE.

    Retorna (importadas, rechazadas).
    """
    from sle.core.memory import Memoria
    mem = Memoria()
    importadas = 0
    rechazadas = 0

    for p in plantas:
        score = p.get("score", 0)
        if score < score_minimo:
            rechazadas += 1
            continue
        try:
            pid = mem.guardar_proyecto(
                plan            = p["plan"],
                prompt_original = p.get("prompt", "planta sintética"),
                score           = score,
                aprobado        = True,
            )
            importadas += 1
            if verbose:
                n_rec = len(p["plan"].get("recintos", []))
                area  = round(p["plan"]["grid"]["ancho_m"] * p["plan"]["grid"]["alto_m"], 1)
                print(f"  [OK] id={pid:4d}  score={score:3d}  {n_rec} recintos  "
                      f"~{area}m2  {p.get('prompt','')[:60]}")
        except Exception as e:
            rechazadas += 1
            if verbose:
                print(f"  [ERR] {e}")

    return importadas, rechazadas


# ─── Bridge: Programa Arquitectónico → Planta SLE ────────────────────

def generar_desde_programa(
    programa: "dict | Any",
    n_candidatos: int = 5,
    norte_lote: str = "norte",
    seed: int | None = None,
) -> dict:
    """
    Genera la MEJOR distribución espacial a partir de un programa
    arquitectónico (dict o ProgramaArquitectonico).

    Produce n_candidatos layouts, los puntúa con las Reglas Merlos,
    y devuelve el de mayor score listo para guardar en el SLE.

    Args:
        programa       : dict de _prog_actual (dwg_to_sle.py) o
                         ProgramaArquitectonico.to_dict()
        n_candidatos   : cuántos layouts generar antes de elegir el mejor
        norte_lote     : orientación del norte en el lote ('norte','sur','este','oeste')
        seed           : seed para reproducibilidad (None = aleatorio)

    Returns:
        {
            "prompt": str,
            "plan":   { "grid": {...}, "recintos": [...], "puertas": [...] },
            "score":  int,
        }
    """
    rng = random.Random(seed)

    # ── Normalizar entrada ────────────────────────────────────────────
    # Acepta tanto el objeto como su representación dict
    if hasattr(programa, "to_dict"):
        prog_dict = programa.to_dict()
    else:
        prog_dict = dict(programa)

    # ── Extraer parámetros del programa ──────────────────────────────
    n_dorm = int(prog_dict.get("n_dormitorios", 2))
    n_dorm = max(1, min(n_dorm, 5))

    tipos_presentes = {
        e.get("tipo", "").lower()
        for e in prog_dict.get("espacios", [])
    }
    nombres_presentes = {
        e.get("nombre", "").lower()
        for e in prog_dict.get("espacios", [])
    }
    todos_presentes = tipos_presentes | nombres_presentes

    tiene_cochera   = any("cochera" in t or "garage" in t or "garaje" in t
                          for t in todos_presentes)
    tiene_lavanderia = any("lavander" in t or "pila" in t
                           for t in todos_presentes)
    sala_integrada  = any("sala_comedor" in t or "sala comedor" in t
                          for t in todos_presentes)

    # Extras: recintos que no son los habituales
    _tipos_base = {
        "dormitorio_principal", "dormitorio", "sala", "sala_comedor",
        "comedor", "cocina", "bano", "bano_principal", "bano_compartido",
        "medio_bano", "bano_invitados", "pasillo", "vestibulo",
        "lavanderia", "cochera",
    }
    _nombres_extra_map = {
        "estudio": "ESTUDIO", "oficina": "ESTUDIO",
        "sala_tv": "SALA_TV", "sala tv": "SALA_TV",
        "terraza": "TERRAZA", "balcon": "TERRAZA", "balcón": "TERRAZA",
        "cuarto_servicio": "CUARTO_SERVICIO",
        "cuarto de servicio": "CUARTO_SERVICIO",
    }
    extras: list[str] = []
    for t in todos_presentes:
        for key, val in _nombres_extra_map.items():
            if key in t and val not in extras:
                extras.append(val)
                break

    # ── Generar candidatos ────────────────────────────────────────────
    candidatos: list[dict] = []
    max_intentos = n_candidatos * 8
    intentos = 0

    while len(candidatos) < n_candidatos and intentos < max_intentos:
        intentos += 1
        # Variar ligeramente la orientación si se generan varios candidatos
        orientaciones = ["norte", "sur", "este", "oeste"]
        norte = norte_lote if len(candidatos) < 2 else rng.choice(orientaciones)
        try:
            planta = _generar_planta(
                n_dormitorios          = n_dorm,
                tiene_cochera          = tiene_cochera,
                tiene_lavanderia       = tiene_lavanderia,
                sala_comedor_integrado = sala_integrada,
                norte_lote             = norte,
                rng                    = rng,
                extras                 = extras,
            )
            score = _calcular_score(planta["plan"])
            planta["score"] = score
            candidatos.append(planta)
        except Exception:
            continue

    if not candidatos:
        raise RuntimeError("No se pudo generar ninguna distribución válida.")

    # ── Devolver el mejor candidato ───────────────────────────────────
    mejor = max(candidatos, key=lambda p: p["score"])

    # Enriquecer el prompt con datos del programa
    total_m2   = prog_dict.get("total_m2_pedido", 0)
    perfil     = prog_dict.get("perfil", "")
    if total_m2:
        mejor["prompt"] = (
            f"Casa de {n_dorm} dormitorio{'s' if n_dorm > 1 else ''}, "
            f"{total_m2}m2, clase {perfil}  —  "
            f"{mejor['prompt']}"
        )

    return mejor


def rankear_candidatos(
    candidatos: list[dict],
    top: int = 3,
) -> list[dict]:
    """
    Ordena una lista de candidatos por score (mayor primero).
    Útil para mostrar al arquitecto las top-N opciones.

    Args:
        candidatos : lista de dicts {"prompt", "plan", "score"}
        top        : cuántos devolver

    Returns:
        lista ordenada, máximo `top` elementos
    """
    ordenados = sorted(candidatos, key=lambda p: p["score"], reverse=True)
    return ordenados[:top]


# ─── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generador de plantas sintéticas para el SLE"
    )
    parser.add_argument("--cantidad",  type=int,  default=50,
                        help="Número de plantas a generar (default: 50)")
    parser.add_argument("--importar",  action="store_true",
                        help="Importar directamente al SLE")
    parser.add_argument("--salida",    type=str,  default=None,
                        help="Directorio donde guardar los JSON generados")
    parser.add_argument("--score-min", type=int,  default=60,
                        help="Score mínimo para importar (default: 60)")
    parser.add_argument("--seed",      type=int,  default=None,
                        help="Seed para reproducibilidad")
    args = parser.parse_args()

    print(f"\n{'='*55}")
    print(f"  Generador Sintético SLE — Estudio Merlos AI")
    print(f"{'='*55}")
    print(f"  Cantidad solicitada : {args.cantidad}")
    print(f"  Score mínimo        : {args.score_min}")
    print(f"  Seed                : {args.seed or 'aleatorio'}")
    print(f"{'='*55}\n")

    print("Generando plantas...")
    plantas = generar_batch(cantidad=args.cantidad, seed=args.seed)
    print(f"\nGeneradas: {len(plantas)} plantas válidas\n")

    # Estadísticas
    scores = [p["score"] for p in plantas]
    if scores:
        print(f"  Score promedio : {sum(scores)/len(scores):.1f}")
        print(f"  Score mínimo   : {min(scores)}")
        print(f"  Score máximo   : {max(scores)}")
        areas = [p["plan"]["grid"]["ancho_m"] * p["plan"]["grid"]["alto_m"]
                 for p in plantas]
        print(f"  Área promedio  : {sum(areas)/len(areas):.0f} m²")
        dorms = [sum(1 for r in p["plan"]["recintos"]
                     if "DORMITORIO" in r["nombre"])
                 for p in plantas]
        from collections import Counter
        dist = Counter(dorms)
        print(f"  Distribución dormitorios: {dict(sorted(dist.items()))}")

    # Guardar a disco
    if args.salida:
        salida = Path(args.salida)
        salida.mkdir(parents=True, exist_ok=True)
        for i, p in enumerate(plantas):
            ruta = salida / f"sintetico_{i+1:04d}.json"
            ruta.write_text(
                json.dumps(p, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        print(f"\n  Guardadas {len(plantas)} plantas en: {salida}")

    # Importar al SLE
    if args.importar:
        print(f"\nImportando al SLE (score >= {args.score_min})...\n")
        ok, fail = importar_al_sle(plantas, score_minimo=args.score_min)
        print(f"\n{'='*55}")
        print(f"  Importadas : {ok}")
        print(f"  Rechazadas : {fail}")
        print(f"  Total SLE  : ver pantalla SLE — Memoria IA")
        print(f"{'='*55}\n")
    elif not args.salida:
        print("\nUso sugerido:")
        print("  python sle/data/generador_sintetico.py --cantidad 50 --importar")
        print("  python sle/data/generador_sintetico.py --cantidad 100 --salida plantas/ --importar")
