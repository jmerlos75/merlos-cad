"""
sle/core/programa_arquitectonico.py
====================================
Capa 0 del SLE — Programa Arquitectónico.

Proceso completo:
    1. El cliente define cuántos m² va a construir
    2. Se clasifica el perfil (clase social / presupuesto)
    3. Se genera el programa arquitectónico: lista de espacios con m²
    4. Regla del 20%: area_total = suma_recintos * 1.20 (pasillos)
       En el diseño real ese % varía de 0 a 20 — es estimación pre-diseño
    5. Se valida contra normativa CR (INVU/CFIA) — mínimos obligatorios
    6. Se ajustan áreas para que la suma cuadre con el total pedido

Normativa CR (INVU/CFIA) — áreas mínimas:
    Dormitorio principal  >= 9.0 m²
    Dormitorio adicional  >= 7.5 m²
    Cocina                >= 5.0 m²
    Sala-comedor          >= 10.0 m²
    Baño                  >= 3.0 m²   (estimado operativo)
    Cuarto de pilas       >= 2.0 m²   (estimado operativo)

Perfiles de cliente (rangos típicos en CR):
    micro       : 25–40 m²   (solución mínima / emergencia habitacional)
    baja        : 40–60 m²   (clase baja / vivienda social)
    media_baja  : 60–90 m²   (clase media baja)
    media       : 90–130 m²  (clase media)
    media_alta  : 130–180 m² (clase media alta)
    alta        : 180–300 m² (clase alta)
    premium     : 300+ m²    (lujo)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════════
# NORMATIVA CR — INVU/CFIA
# Dos restricciones simultáneas por espacio:
#   area_min : metros cuadrados mínimos
#   dim_min  : dimensión mínima del lado menor (metros)
# Ambas deben cumplirse — ninguna sola es suficiente.
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class NormativaEspacio:
    area_min: float   # m² — área mínima obligatoria
    dim_min:  float   # m  — dimensión mínima del lado menor
    fuente:   str = "INVU/CFIA"
    nota:     str = ""

    @property
    def dim_max_lado_mayor(self) -> float:
        """Lado mayor máximo coherente: area_min / dim_min."""
        if self.dim_min > 0:
            return round(self.area_min / self.dim_min, 2)
        return 0.0

    def cumple(self, area: float, dim_menor: float | None = None) -> tuple[bool, list[str]]:
        """
        Verifica si un espacio cumple la normativa.
        dim_menor: dimensión del lado más corto en metros (opcional).
        Retorna (cumple, lista_de_errores).
        """
        errores = []
        if area < self.area_min - 0.05:
            errores.append(f"area {area:.1f}m2 < minimo {self.area_min}m2")
        if dim_menor is not None and dim_menor < self.dim_min - 0.05:
            errores.append(
                f"lado menor {dim_menor:.2f}m < minimo {self.dim_min}m"
            )
        return len(errores) == 0, errores


# Tabla completa de normativa CR
NORMATIVA_CR: dict[str, NormativaEspacio] = {
    # Dormitorios — INVU Art. 30
    "dormitorio_principal": NormativaEspacio(
        area_min=9.0, dim_min=2.5,
        nota="Cualquier dormitorio. Lado menor >= 2.50m"
    ),
    "dormitorio": NormativaEspacio(
        area_min=7.5, dim_min=2.5,
        nota="Dormitorio adicional. Lado menor >= 2.50m"
    ),
    # Sala-comedor — INVU Art. 31
    "sala_comedor": NormativaEspacio(
        area_min=10.0, dim_min=2.5,
        nota="Sala-comedor integrada. Lado menor >= 2.50m"
    ),
    "sala": NormativaEspacio(
        area_min=6.5, dim_min=2.5,
        nota="Sala independiente. Lado menor >= 2.50m"
    ),
    "comedor": NormativaEspacio(
        area_min=7.5, dim_min=2.5,
        nota="Comedor independiente. Lado menor >= 2.50m"
    ),
    # Cocina — INVU Art. 32
    "cocina": NormativaEspacio(
        area_min=5.0, dim_min=2.0,
        nota="Cocina. Lado menor >= 2.00m"
    ),
    # Baños (estimados operativos — INVU no especifica dim_min para baños)
    "bano":             NormativaEspacio(area_min=3.0, dim_min=1.2),
    "bano_principal":   NormativaEspacio(area_min=3.5, dim_min=1.4),
    "bano_compartido":  NormativaEspacio(area_min=3.0, dim_min=1.2),
    "bano_invitados":   NormativaEspacio(area_min=2.5, dim_min=1.0,
                                          nota="Medio bano social (inodoro + lavatorio)"),
    # Servicio
    "lavanderia":       NormativaEspacio(area_min=2.0, dim_min=1.2,
                                          nota="Cuarto de pilas"),
    "cuarto_servicio":  NormativaEspacio(area_min=6.0, dim_min=2.0),
    # Circulacion — INVU Art. 41
    "pasillo":          NormativaEspacio(area_min=0.0, dim_min=1.2,
                                          nota="Ancho minimo pasillo >= 1.20m"),
    "vestibulo":        NormativaEspacio(area_min=2.0, dim_min=1.2),
    # Otros
    "cochera":          NormativaEspacio(area_min=12.0, dim_min=2.75,
                                          nota="1 vehiculo: 2.75 x 5.00m minimo"),
    "sala_tv":          NormativaEspacio(area_min=7.5, dim_min=2.5),
    "estudio":          NormativaEspacio(area_min=6.0, dim_min=2.0),
    "piscina":          NormativaEspacio(area_min=0.0, dim_min=0.0),
}

# Compatibilidad hacia atrás — dict plano de areas mínimas
AREAS_MINIMAS_CR: dict[str, float] = {
    k: v.area_min for k, v in NORMATIVA_CR.items()
}
DIM_MINIMAS_CR: dict[str, float] = {
    k: v.dim_min for k, v in NORMATIVA_CR.items()
}

# Ventana mínima: 15% del área del recinto (normativa CR)
VENTANA_MIN_PORCENTAJE = 0.15


# ═══════════════════════════════════════════════════════════════════════
# PERFIL DE CLIENTE
# ═══════════════════════════════════════════════════════════════════════

class PerfilCliente(Enum):
    BAJA  = "baja"   #  35–80 m²
    MEDIA = "media"  #  80–150 m²
    ALTA  = "alta"   # 150–400 m²

    @classmethod
    def desde_m2(cls, total_m2: float) -> "PerfilCliente":
        """Clasifica automáticamente el perfil según los m² totales."""
        if total_m2 < 80:  return cls.BAJA
        if total_m2 < 150: return cls.MEDIA
        return cls.ALTA

    @property
    def rango_m2(self) -> tuple[float, float]:
        return {
            "baja":  (35,   80),
            "media": (80,  150),
            "alta":  (150, 400),
        }[self.value]

    @property
    def descripcion(self) -> str:
        return {
            "baja":  "Clase baja (35-80 m2)",
            "media": "Clase media (80-150 m2)",
            "alta":  "Clase alta (150-400 m2)",
        }[self.value]


# ═══════════════════════════════════════════════════════════════════════
# DISTRIBUCIÓN TÍPICA POR N° DE DORMITORIOS
# ═══════════════════════════════════════════════════════════════════════
# Proporciones (suman 1.0) sobre el área NETA (sin pasillos).
# Cada programa define el "molde" — las áreas reales se escalan al total.

_PROG_1_DORM = [
    ("dormitorio_principal", 0.22),
    ("sala_comedor",         0.28),
    ("cocina",               0.14),
    ("bano",                 0.12),
    ("lavanderia",           0.08),
    ("vestibulo",            0.16),
]

_PROG_2_DORM = [
    ("dormitorio_principal", 0.18),
    ("dormitorio",           0.14),
    ("sala",                 0.18),
    ("comedor",              0.12),
    ("cocina",               0.10),
    ("bano_principal",       0.08),
    ("bano_compartido",      0.06),
    ("lavanderia",           0.05),
    ("vestibulo",            0.09),
]

_PROG_3_DORM = [
    ("dormitorio_principal", 0.16),
    ("dormitorio",           0.12),   # x2 — se duplica el recinto
    ("dormitorio",           0.10),
    ("sala",                 0.16),
    ("comedor",              0.10),
    ("cocina",               0.09),
    ("bano_principal",       0.07),
    ("bano_compartido",      0.06),
    ("lavanderia",           0.04),
    ("vestibulo",            0.05),
    ("pasillo",              0.05),
]

_PROG_4_DORM = [
    ("dormitorio_principal", 0.14),
    ("dormitorio",           0.10),   # x3
    ("dormitorio",           0.09),
    ("dormitorio",           0.08),
    ("sala",                 0.14),
    ("comedor",              0.09),
    ("cocina",               0.08),
    ("bano_principal",       0.06),
    ("bano_compartido",      0.05),
    ("bano_invitados",       0.04),
    ("lavanderia",           0.04),
    ("vestibulo",            0.04),
    ("pasillo",              0.05),
]

PROGRAMAS_BASE: dict[int, list[tuple[str, float]]] = {
    1: _PROG_1_DORM,
    2: _PROG_2_DORM,
    3: _PROG_3_DORM,
    4: _PROG_4_DORM,
}

# Espacios adicionales opcionales con proporción del área neta
OPCIONALES: dict[str, float] = {
    "cochera":          0.08,
    "cuarto_servicio":  0.06,
    "sala_tv":          0.06,
    "estudio":          0.06,
    "piscina":          0.10,
}


# ═══════════════════════════════════════════════════════════════════════
# ESPACIO DEL PROGRAMA
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class EspacioPrograma:
    """Un espacio en el programa arquitectónico."""
    nombre: str              # nombre del recinto
    tipo: str                # tipo SLE (dormitorio_principal, sala, etc.)
    area_m2: float           # área calculada
    area_min_cr: float       # mínimo normativa CR — área
    dim_min_cr: float = 0.0  # mínimo normativa CR — lado menor
    cumple_norma: bool = True
    errores_norma: list[str] = field(default_factory=list)
    es_opcional: bool = False
    nota: str = ""

    @property
    def dim_menor_estimada(self) -> float:
        """Estimación del lado menor para el área calculada (proporciones equilibradas)."""
        if self.dim_min_cr > 0:
            # Usar como base el lado mínimo requerido
            return max(self.dim_min_cr, round(math.sqrt(self.area_m2 * 0.75), 2))
        return round(math.sqrt(self.area_m2), 2)

    @property
    def dim_mayor_estimada(self) -> float:
        """Estimación del lado mayor para el área calculada."""
        dm = self.dim_menor_estimada
        if dm > 0:
            return round(self.area_m2 / dm, 2)
        return round(math.sqrt(self.area_m2), 2)

    @property
    def dim_estimada(self) -> str:
        """Dimensiones estimadas como string 'ancho x largo m'."""
        return f"{self.dim_menor_estimada:.2f} x {self.dim_mayor_estimada:.2f}m"

    def to_dict(self) -> dict:
        return {
            "nombre": self.nombre,
            "tipo": self.tipo,
            "area_m2": round(self.area_m2, 1),
            "area_min_cr": self.area_min_cr,
            "dim_min_cr": self.dim_min_cr,
            "dim_estimada": f"{self.dim_menor_estimada:.2f} x {self.dim_mayor_estimada:.2f}m",
            "cumple_norma": self.cumple_norma,
            "errores_norma": self.errores_norma,
        }


# ═══════════════════════════════════════════════════════════════════════
# PROGRAMA ARQUITECTÓNICO
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ProgramaArquitectonico:
    """
    Programa completo: lista de espacios con sus m² para un proyecto.
    Entrada del diseño antes de zonificar u orientar.
    """
    total_m2_pedido: float
    perfil: PerfilCliente
    n_dormitorios: int
    porcentaje_circulacion: float = 0.20  # 20% estimado pre-diseño

    espacios: list[EspacioPrograma] = field(default_factory=list)

    # Totales calculados
    area_neta: float = 0.0          # suma de recintos (sin pasillos)
    area_circulacion: float = 0.0   # estimado de pasillos
    area_total: float = 0.0         # neta + circulacion
    diferencia_m2: float = 0.0      # total - pedido (positivo = se pasó)

    errores: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    def calcular_totales(self):
        """Recalcula los totales después de modificar espacios."""
        self.area_neta = sum(e.area_m2 for e in self.espacios
                             if e.tipo != "pasillo")
        self.area_circulacion = self.area_neta * self.porcentaje_circulacion
        self.area_total = self.area_neta + self.area_circulacion
        self.diferencia_m2 = round(self.area_total - self.total_m2_pedido, 1)

    def to_dict(self) -> dict:
        self.calcular_totales()
        return {
            "total_m2_pedido": self.total_m2_pedido,
            "perfil": self.perfil.value,
            "n_dormitorios": self.n_dormitorios,
            "area_neta_m2": round(self.area_neta, 1),
            "area_circulacion_m2": round(self.area_circulacion, 1),
            "area_total_m2": round(self.area_total, 1),
            "diferencia_m2": self.diferencia_m2,
            "porcentaje_circulacion": self.porcentaje_circulacion,
            "espacios": [e.to_dict() for e in self.espacios],
            "errores": self.errores,
            "advertencias": self.advertencias,
        }

    def como_recintos_sle(self) -> list[dict]:
        """
        Convierte el programa al formato de recintos del SLE (sin posición).
        Útil para pasarle al generador de planta.
        """
        recintos = []
        conteo: dict[str, int] = {}
        for e in self.espacios:
            conteo[e.tipo] = conteo.get(e.tipo, 0) + 1
            n = conteo[e.tipo]
            nombre = e.nombre.upper().replace(" ", "_")
            lado = round(math.sqrt(e.area_m2))
            recintos.append({
                "nombre": nombre,
                "tipo": e.tipo,
                "area_m2": round(e.area_m2, 1),
                "ancho": lado,   # estimado cuadrado — el diseño ajustará
                "alto": lado,
                "fila": 0, "col": 0,  # sin posición aún
            })
        return recintos


# ═══════════════════════════════════════════════════════════════════════
# GENERADOR DE PROGRAMA
# ═══════════════════════════════════════════════════════════════════════

def generar_programa(
    total_m2: float,
    n_dormitorios: int = 2,
    opcionales: list[str] | None = None,
    porcentaje_circulacion: float = 0.20,
    perfil: PerfilCliente | None = None,
) -> ProgramaArquitectonico:
    """
    Genera el programa arquitectónico para un proyecto.

    Args:
        total_m2            : metros cuadrados totales de construcción
        n_dormitorios       : número de dormitorios (1-4)
        opcionales          : lista de tipos adicionales (cochera, sala_tv, etc.)
        porcentaje_circulacion: estimado de pasillos sobre área neta (default 20%)
        perfil              : perfil del cliente (auto-detecta si no se da)

    Returns:
        ProgramaArquitectonico con todos los espacios calculados y validados
    """
    perfil = perfil or PerfilCliente.desde_m2(total_m2)
    n_dorm = max(1, min(n_dormitorios, 4))
    opcionales = opcionales or []

    prog = ProgramaArquitectonico(
        total_m2_pedido=total_m2,
        perfil=perfil,
        n_dormitorios=n_dorm,
        porcentaje_circulacion=porcentaje_circulacion,
    )

    # Área neta disponible (descontando circulación)
    # total = neta * (1 + circ_pct) → neta = total / (1 + circ_pct)
    area_neta_disponible = total_m2 / (1 + porcentaje_circulacion)

    # Proporciones base
    base = PROGRAMAS_BASE.get(n_dorm, _PROG_2_DORM)[:]

    # Agregar opcionales con sus proporciones
    prop_opcionales_total = 0.0
    opcionales_validos = []
    for opt in opcionales:
        if opt in OPCIONALES:
            opcionales_validos.append((opt, OPCIONALES[opt]))
            prop_opcionales_total += OPCIONALES[opt]

    # Normalizar proporciones base para dejar espacio a los opcionales
    factor_reduccion = 1.0 - prop_opcionales_total
    if factor_reduccion < 0.5:
        factor_reduccion = 0.5  # no reducir más del 50%
    base_ajustada = [(t, p * factor_reduccion) for t, p in base]

    todos_tipos = base_ajustada + opcionales_validos

    # Calcular áreas brutas
    areas_brutas: dict = {}
    conteo_tipo: dict[str, int] = {}
    for tipo, proporcion in todos_tipos:
        area = area_neta_disponible * proporcion
        conteo_tipo[tipo] = conteo_tipo.get(tipo, 0) + 1
        n = conteo_tipo[tipo]
        clave = f"{tipo}_{n}" if conteo_tipo[tipo] > 1 else tipo
        areas_brutas[clave] = (tipo, area, n)

    # Aplicar mínimos normativos (área Y dimensión) y ajustar
    areas_finales: list[tuple[str, str, float]] = []  # (clave, tipo, area)
    suma_minimos = 0.0

    for clave, (tipo, area_bruta, n) in areas_brutas.items():
        norma = NORMATIVA_CR.get(tipo)
        area_min = norma.area_min if norma else 0.0
        dim_min  = norma.dim_min  if norma else 0.0
        # El área mínima real también debe respetar la dimensión mínima
        # Si dim_min = 2.5 y el espacio es muy alargado, el área real necesaria
        # puede ser mayor: área >= dim_min * dim_min (cuadrado mínimo)
        area_min_efectiva = max(area_min, dim_min * dim_min if dim_min > 0 else 0)
        area_final = max(area_bruta, area_min_efectiva)
        areas_finales.append((clave, tipo, area_final))
        suma_minimos += area_min_efectiva

    # Verificar que los mínimos no superen el área disponible
    if suma_minimos > area_neta_disponible:
        prog.errores.append(
            f"NORMATIVA: Los minimos CR ({suma_minimos:.1f}m2) superan el "
            f"area neta disponible ({area_neta_disponible:.1f}m2). "
            f"Aumentar total o reducir el programa."
        )

    # Escalar las áreas para que la suma se ajuste al área neta disponible
    suma_actual = sum(a for _, _, a in areas_finales)
    if suma_actual > 0:
        escalar = area_neta_disponible / suma_actual
        areas_escaladas: list[tuple[str, str, float]] = []
        for clave, tipo, area in areas_finales:
            norma = NORMATIVA_CR.get(tipo)
            area_min = norma.area_min if norma else 0.0
            dim_min  = norma.dim_min  if norma else 0.0
            area_min_ef = max(area_min, dim_min * dim_min if dim_min > 0 else 0)
            area_escalada = max(area_min_ef, area * escalar)
            areas_escaladas.append((clave, tipo, area_escalada))
    else:
        areas_escaladas = areas_finales

    # Construir lista de EspacioPrograma
    conteo_nombre: dict[str, int] = {}
    tipos_opcionales = {o for o, _ in opcionales_validos}
    for clave, tipo, area in areas_escaladas:
        norma = NORMATIVA_CR.get(tipo)
        area_min = norma.area_min if norma else 0.0
        dim_min  = norma.dim_min  if norma else 0.0

        # Validar área y dimensión estimada
        cumple, errores_norma = True, []
        if norma:
            # Para la dim estimada: lado menor = max(dim_min, sqrt(area*0.75))
            dim_menor_est = max(dim_min, round(math.sqrt(area * 0.75), 2)) if dim_min > 0 else math.sqrt(area)
            cumple, errores_norma = norma.cumple(area, dim_menor_est)

        # Nombre legible
        conteo_nombre[tipo] = conteo_nombre.get(tipo, 0) + 1
        n = conteo_nombre[tipo]
        nombre = _nombre_legible(tipo, n)

        espacio = EspacioPrograma(
            nombre=nombre,
            tipo=tipo,
            area_m2=round(area, 1),
            area_min_cr=area_min,
            dim_min_cr=dim_min,
            cumple_norma=cumple,
            errores_norma=errores_norma,
            es_opcional=tipo in tipos_opcionales,
        )
        prog.espacios.append(espacio)

    # Calcular totales y validar
    prog.calcular_totales()
    _validar_programa(prog)

    return prog


def _nombre_legible(tipo: str, n: int) -> str:
    """Genera nombre de recinto a partir del tipo."""
    nombres: dict[str, str] = {
        "dormitorio_principal": "Dormitorio Principal",
        "dormitorio":           "Dormitorio",
        "sala":                 "Sala",
        "sala_comedor":         "Sala-Comedor",
        "comedor":              "Comedor",
        "cocina":               "Cocina",
        "bano":                 "Bano",
        "bano_principal":       "Bano Principal",
        "bano_compartido":      "Bano Compartido",
        "bano_invitados":       "Bano Visitas",
        "lavanderia":           "Cuarto de Pilas",
        "cuarto_servicio":      "Cuarto de Servicio",
        "cochera":              "Cochera",
        "vestibulo":            "Vestibulo",
        "pasillo":              "Pasillo",
        "sala_tv":              "Sala TV",
        "estudio":              "Estudio",
        "piscina":              "Piscina",
    }
    base = nombres.get(tipo, tipo.replace("_", " ").title())
    # Solo el primero de cada tipo sin número, el resto con número
    if n > 1:
        return f"{base} {n}"
    return base


def _validar_programa(prog: ProgramaArquitectonico):
    """Valida el programa contra normativa CR (área Y dimensión mínima)."""

    # Verificar cada espacio
    for e in prog.espacios:
        if not e.cumple_norma:
            for err in e.errores_norma:
                prog.errores.append(f"{e.nombre}: {err}")

    # Advertencias según perfil vs. área pedida
    rango = prog.perfil.rango_m2
    if prog.total_m2_pedido < rango[0]:
        prog.advertencias.append(
            f"El area pedida ({prog.total_m2_pedido}m2) es ajustada para "
            f"{prog.n_dormitorios} dormitorios — considerar reducir el programa."
        )

    # Diferencia entre total calculado y pedido
    if abs(prog.diferencia_m2) > prog.total_m2_pedido * 0.05:
        signo = "excede" if prog.diferencia_m2 > 0 else "queda por debajo de"
        prog.advertencias.append(
            f"El programa {signo} el area pedida en {abs(prog.diferencia_m2):.1f}m2."
        )

    # Anotar ventana mínima y dimensiones en nota
    for e in prog.espacios:
        partes = []
        if e.dim_min_cr > 0:
            partes.append(
                f"lado menor >= {e.dim_min_cr}m  "
                f"(est. {e.dim_menor_estimada:.2f} x {e.dim_mayor_estimada:.2f}m)"
            )
        if e.tipo in ("dormitorio", "dormitorio_principal",
                      "sala", "sala_comedor", "comedor"):
            ventana_min = round(e.area_m2 * VENTANA_MIN_PORCENTAJE, 2)
            partes.append(f"ventana >= {ventana_min}m2")
        e.nota = "  |  ".join(partes)


# ═══════════════════════════════════════════════════════════════════════
# TEXTO DE SALIDA
# ═══════════════════════════════════════════════════════════════════════

def texto_programa(prog: ProgramaArquitectonico) -> str:
    """Formatea el programa para mostrar en GUI, log o prompt."""
    prog.calcular_totales()
    lineas = [
        f"== PROGRAMA ARQUITECTONICO ==",
        f"   Cliente    : {prog.perfil.descripcion}",
        f"   Pedido     : {prog.total_m2_pedido} m2 totales",
        f"   Dormitorios: {prog.n_dormitorios}",
        f"",
        f"   Area neta recintos   : {prog.area_neta:.1f} m2",
        f"   Circulacion est. {int(prog.porcentaje_circulacion*100):2d}% : "
        f"{prog.area_circulacion:.1f} m2",
        f"   TOTAL PROGRAMA       : {prog.area_total:.1f} m2",
        f"   Diferencia vs pedido : {prog.diferencia_m2:+.1f} m2",
        f"",
        f"   {'ESPACIO':<26} {'AREA':>5}  {'MIN':>4}  {'DIM MIN':>7}  DIMENSIONES EST.",
        f"   {'-'*72}",
    ]

    for e in prog.espacios:
        estado = "OK" if e.cumple_norma else "!!"
        dim_min_str = f">={e.dim_min_cr:.2f}m" if e.dim_min_cr > 0 else "  ---  "
        dim_est_str = f"{e.dim_menor_estimada:.2f} x {e.dim_mayor_estimada:.2f} m"
        lineas.append(
            f"   [{estado}] {e.nombre:<22} {e.area_m2:>5.1f}m2"
            f"  {e.area_min_cr:>4.1f}  {dim_min_str:>7}  {dim_est_str}"
        )
        # Mostrar restricciones en línea extra si hay
        if not e.cumple_norma:
            for err in e.errores_norma:
                lineas.append(f"         !! {err}")
        if e.tipo in ("dormitorio", "dormitorio_principal",
                      "sala", "sala_comedor", "comedor"):
            ventana_min = round(e.area_m2 * VENTANA_MIN_PORCENTAJE, 2)
            lineas.append(f"         ventana >= {ventana_min}m2 (15% del area)")

    if prog.errores:
        lineas.append(f"\n   ERRORES ({len(prog.errores)}):")
        for err in prog.errores:
            lineas.append(f"   [!!] {err}")
    if prog.advertencias:
        lineas.append(f"\n   Advertencias ({len(prog.advertencias)}):")
        for adv in prog.advertencias:
            lineas.append(f"   [->] {adv}")

    return "\n".join(lineas)


def texto_programa_para_prompt(prog: ProgramaArquitectonico) -> str:
    """
    Versión compacta para inyectar al AI junto con el prompt del arquitecto.
    """
    prog.calcular_totales()
    lineas = [
        "PROGRAMA ARQUITECTONICO PRE-APROBADO:",
        f"  Total: {prog.total_m2_pedido}m2  |  Perfil: {prog.perfil.value}  "
        f"|  {prog.n_dormitorios} dormitorio(s)",
        f"  Area neta recintos: {prog.area_neta:.0f}m2  +  "
        f"circulacion estimada {int(prog.porcentaje_circulacion*100)}%: "
        f"{prog.area_circulacion:.0f}m2  =  {prog.area_total:.0f}m2",
        "  Espacios:",
    ]
    for e in prog.espacios:
        lineas.append(f"    - {e.nombre}: {e.area_m2:.1f}m2")
    lineas.append(
        "  REGLA: ningun espacio puede quedar por debajo de su minimo CR. "
        "La circulacion (pasillos) puede variar de 0% a 20% segun el diseno real."
    )
    return "\n".join(lineas)


# ═══════════════════════════════════════════════════════════════════════
# SMOKE TEST
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    casos = [
        (35,  1, []),
        (80,  2, []),
        (100, 3, ["cochera"]),
        (180, 3, ["cochera", "sala_tv"]),
        (250, 4, ["cochera", "cuarto_servicio", "sala_tv"]),
    ]
    for total, ndorm, opts in casos:
        prog = generar_programa(total, n_dormitorios=ndorm, opcionales=opts)
        print(texto_programa(prog))
        print()
