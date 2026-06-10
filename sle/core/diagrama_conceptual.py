"""
sle/core/diagrama_conceptual.py
================================
Capa 0.5 del SLE — Diagrama Funcional / Diagrama de Burbujas.

Metodología Arq. Joseph Merlos — Paso 0 del diseño:
    ANTES de dibujar paredes se organiza la lógica espacial,
    funcional y climática del proyecto mediante burbujas, relaciones
    y orientación cardinal.

Flujo completo:
    Brief del cliente
        → generar_programa()           [programa_arquitectonico.py]
        → generar_diagrama()           [este módulo] ← Paso 0
        → a_spatial_graph()            [diagrama → SpatialGraph]
        → analizar_reglas_merlos()     [reglas_merlos.py]
        → Plano AutoCAD

Dos entradas posibles al diagrama:
    A) Desde texto : generar_diagrama(programa, norte_lote)
    B) Desde imagen: el Hub envía la foto a Claude Vision con
                     prompt_lectura_imagen() y parsea la respuesta
                     con desde_dict(data)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sle.core.programa_arquitectonico import (
    ProgramaArquitectonico,
    EspacioPrograma,
    PerfilCliente,
    generar_programa,
    texto_programa_para_prompt,
)


# ═══════════════════════════════════════════════════════════════════════
# CONSTANTES — Zona, orientación ideal y tamaño por tipo de espacio
# ═══════════════════════════════════════════════════════════════════════

# Zona funcional por tipo (alineado con reglas_merlos.py ZONA_BASE)
ZONA_POR_TIPO: dict[str, str] = {
    "sala":                 "publica",
    "sala_comedor":         "publica",
    "comedor":              "publica",
    "cocina":               "publica",    # acceso público aunque sea servicio
    "vestibulo":            "publica",
    "cochera":              "publica",
    "piscina":              "publica",
    "terraza":              "publica",
    "pasillo":              "publica",
    "dormitorio_principal": "privada",
    "dormitorio":           "privada",
    "sala_tv":              "privada",
    "estudio":              "privada",
    "bano_principal":       "humeda",
    "bano_compartido":      "humeda",
    "bano_invitados":       "humeda",
    "medio_bano":           "humeda",
    "lavanderia":           "humeda",
    "cuarto_servicio":      "servicio",
    "cuarto_tecnico":       "servicio",
    "bodega":               "servicio",
}

# Orientaciones cardinales ideales por tipo (orden de prioridad)
ORIENTACION_IDEAL: dict[str, list[str]] = {
    "sala":                 ["norte", "noreste"],
    "sala_comedor":         ["norte", "noreste"],
    "comedor":              ["norte", "noreste"],
    "vestibulo":            ["norte"],
    "terraza":              ["norte", "noreste"],
    "piscina":              ["norte", "noreste"],
    "cochera":              ["norte", "noreste"],
    "cocina":               ["noreste", "norte"],
    "estudio":              ["norte", "noreste"],
    "dormitorio_principal": ["sur", "sureste", "suroeste", "este"],
    "dormitorio":           ["sur", "sureste", "suroeste", "este"],
    "sala_tv":              ["sur", "este"],
    "bano_principal":       ["oeste", "suroeste"],
    "bano_compartido":      ["oeste", "suroeste"],
    "bano_invitados":       ["oeste", "suroeste"],
    "medio_bano":           ["oeste", "suroeste"],
    "lavanderia":           ["oeste", "suroeste"],
    "cuarto_servicio":      ["oeste", "suroeste"],
    "cuarto_tecnico":       ["oeste", "suroeste"],
    "bodega":               ["oeste", "suroeste"],
    "pasillo":              [],   # sin orientación preferida
}

# Relaciones directas estándar entre tipos de espacios
# clave = tipo A   valor = lista de tipos con los que tiene relación DIRECTA
RELACIONES_ESTANDAR: dict[str, list[str]] = {
    "vestibulo":            ["sala", "cochera", "sala_comedor"],
    "sala":                 ["comedor", "vestibulo", "terraza", "piscina", "sala_tv"],
    "sala_comedor":         ["cocina", "vestibulo", "terraza"],
    "comedor":              ["sala", "cocina"],
    "cocina":               ["comedor", "sala_comedor", "lavanderia"],
    "lavanderia":           ["cocina", "cuarto_servicio"],
    "dormitorio_principal": ["bano_principal"],
    "dormitorio":           ["bano_compartido"],
    "bano_principal":       ["dormitorio_principal"],
    "bano_compartido":      ["dormitorio"],
    "bano_invitados":       ["sala", "vestibulo"],
    "medio_bano":           ["sala", "vestibulo"],
    "sala_tv":              ["sala", "dormitorio"],
    "estudio":              ["sala", "vestibulo"],
    "cochera":              ["vestibulo"],
    "terraza":              ["sala", "sala_comedor", "piscina"],
    "piscina":              ["terraza", "sala"],
    "cuarto_servicio":      ["lavanderia", "cuarto_tecnico"],
    "bodega":               ["cuarto_servicio", "cochera"],
    "pasillo":              ["dormitorio_principal", "dormitorio", "bano_compartido"],
}

# Umbral de tamaño de burbuja por área
_AREA_GRANDE  = 14.0  # m² — burbuja grande
_AREA_MEDIANO =  7.0  # m² — burbuja mediana / pequeña


# ═══════════════════════════════════════════════════════════════════════
# DATACLASSES
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class BurbujaEspacio:
    """
    Un espacio en el diagrama de burbujas.
    Representa un círculo en el diagrama funcional.
    """
    nombre: str                        # nombre del recinto  (ej. "Sala")
    tipo: str                          # tipo SLE             (ej. "sala")
    zona: str                          # publica|privada|humeda|servicio
    area_m2: float                     # área en m²
    tamano: str                        # "grande"|"mediano"|"pequeno"
    orientacion_ideal: list[str]       # ["norte", "noreste"] en orden de prioridad
    relaciones_directas: list[str]     # nombres de espacios con conexión directa
    relaciones_indirectas: list[str]   # nombres con conexión secundaria
    es_acceso: bool = False            # True si es el punto de entrada principal
    circulacion: str = "ninguna"       # "principal"|"secundaria"|"ninguna"
    nota: str = ""

    def to_dict(self) -> dict:
        return {
            "nombre": self.nombre,
            "tipo": self.tipo,
            "zona": self.zona,
            "area_m2": self.area_m2,
            "tamano": self.tamano,
            "orientacion_ideal": self.orientacion_ideal,
            "relaciones_directas": self.relaciones_directas,
            "relaciones_indirectas": self.relaciones_indirectas,
            "es_acceso": self.es_acceso,
            "circulacion": self.circulacion,
            "nota": self.nota,
        }


@dataclass
class DiagramaFuncional:
    """
    Diagrama de burbujas completo — resultado del Paso 0 del diseño.
    Contiene toda la lógica espacial ANTES de dibujar paredes.

    origen:
        'auto'        → generado por el sistema desde un brief de texto.
                        Las reglas R1/R2 aplican como violaciones.
        'arquitecto'  → dibujado por el arquitecto (imagen o descripción
                        intencional). El diagrama MANDA sobre R1/R2.
                        Las desviaciones de orientación/zonificación se
                        muestran como NOTAS informativas, no como errores.
                        R3-R6 (proporciones + INVU/CFIA) siguen aplicando.
    """
    nombre_proyecto: str
    tipo_proyecto: str                  # "casa"|"edificio"|"comercial"
    norte_lote: str                     # orientación del frente del lote
    area_total_m2: float
    perfil: str                         # perfil del cliente
    burbujas: list[BurbujaEspacio] = field(default_factory=list)
    acceso_principal: str = ""          # nombre del espacio de entrada
    porcentaje_circulacion: float = 0.20
    concepto: str = ""                  # concepto arquitectónico general
    decisiones: list[str] = field(default_factory=list)   # decisiones clave
    advertencias: list[str] = field(default_factory=list) # alertas de diseño
    origen: str = "auto"                # "auto" | "arquitecto"

    # ── Accesos rápidos ──────────────────────────────────────────────

    @property
    def zona_publica(self) -> list[BurbujaEspacio]:
        return [b for b in self.burbujas if b.zona == "publica"]

    @property
    def zona_privada(self) -> list[BurbujaEspacio]:
        return [b for b in self.burbujas if b.zona == "privada"]

    @property
    def zona_humeda(self) -> list[BurbujaEspacio]:
        return [b for b in self.burbujas if b.zona == "humeda"]

    @property
    def zona_servicio(self) -> list[BurbujaEspacio]:
        return [b for b in self.burbujas if b.zona == "servicio"]

    def burbuja(self, nombre: str) -> BurbujaEspacio | None:
        """Busca una burbuja por nombre (insensible a mayúsculas)."""
        n = nombre.lower()
        for b in self.burbujas:
            if b.nombre.lower() == n:
                return b
        return None

    def to_dict(self) -> dict:
        return {
            "nombre_proyecto": self.nombre_proyecto,
            "tipo_proyecto": self.tipo_proyecto,
            "norte_lote": self.norte_lote,
            "area_total_m2": self.area_total_m2,
            "perfil": self.perfil,
            "acceso_principal": self.acceso_principal,
            "porcentaje_circulacion": self.porcentaje_circulacion,
            "concepto": self.concepto,
            "decisiones": self.decisiones,
            "advertencias": self.advertencias,
            "origen": self.origen,
            "burbujas": [b.to_dict() for b in self.burbujas],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ═══════════════════════════════════════════════════════════════════════
# GENERADOR — Desde programa arquitectónico
# ═══════════════════════════════════════════════════════════════════════

def generar_diagrama(
    programa: ProgramaArquitectonico,
    norte_lote: str = "norte",
    nombre_proyecto: str = "Proyecto sin nombre",
    tipo_proyecto: str = "casa",
    concepto: str = "",
) -> DiagramaFuncional:
    """
    Genera el diagrama funcional completo a partir de un programa arquitectónico.

    Args:
        programa        : resultado de generar_programa()
        norte_lote      : dirección del norte del frente del lote
                          ('norte'|'sur'|'este'|'oeste')
        nombre_proyecto : nombre del proyecto
        tipo_proyecto   : 'casa'|'edificio'|'comercial'
        concepto        : concepto arquitectónico libre (opcional)

    Returns:
        DiagramaFuncional con todas las burbujas, relaciones y decisiones.
    """
    programa.calcular_totales()

    diagrama = DiagramaFuncional(
        nombre_proyecto=nombre_proyecto,
        tipo_proyecto=tipo_proyecto,
        norte_lote=norte_lote.lower(),
        area_total_m2=programa.area_total,
        perfil=programa.perfil.value,
        porcentaje_circulacion=programa.porcentaje_circulacion,
        concepto=concepto,
    )

    # Construir índice nombre → tipo de todos los espacios del programa
    nombres_en_programa: list[tuple[str, str]] = [
        (e.nombre, e.tipo) for e in programa.espacios
    ]

    # ── Paso 1: Crear burbujas ────────────────────────────────────────
    for espacio in programa.espacios:
        burbuja = _crear_burbuja(espacio, nombres_en_programa)
        diagrama.burbujas.append(burbuja)

    # ── Paso 2: Marcar acceso principal ──────────────────────────────
    acceso = _detectar_acceso(diagrama)
    diagrama.acceso_principal = acceso
    for b in diagrama.burbujas:
        if b.nombre == acceso:
            b.es_acceso = True
            b.circulacion = "principal"

    # ── Paso 3: Marcar circulaciones ─────────────────────────────────
    _asignar_circulaciones(diagrama)

    # ── Paso 4: Generar concepto si no se dio ─────────────────────────
    if not diagrama.concepto:
        diagrama.concepto = _generar_concepto(diagrama, programa)

    # ── Paso 5: Decisiones y advertencias ────────────────────────────
    _generar_decisiones(diagrama, programa, norte_lote)

    return diagrama


def _crear_burbuja(
    espacio: EspacioPrograma,
    todos: list[tuple[str, str]],
) -> BurbujaEspacio:
    """Crea una BurbujaEspacio a partir de un EspacioPrograma."""
    tipo = espacio.tipo

    # Zona
    zona = ZONA_POR_TIPO.get(tipo, "servicio")

    # Tamaño de burbuja según área
    if espacio.area_m2 >= _AREA_GRANDE:
        tamano = "grande"
    elif espacio.area_m2 >= _AREA_MEDIANO:
        tamano = "mediano"
    else:
        tamano = "pequeno"

    # Orientación ideal
    orientacion = ORIENTACION_IDEAL.get(tipo, [])

    # Relaciones directas — filtrar solo los que existen en el programa
    tipos_directos = RELACIONES_ESTANDAR.get(tipo, [])
    nombres_directos = _filtrar_existentes(tipos_directos, todos)

    # Relaciones indirectas — tipos que se relacionan con los directos
    tipos_indirectos: set[str] = set()
    for td in tipos_directos:
        for ti in RELACIONES_ESTANDAR.get(td, []):
            if ti != tipo and ti not in tipos_directos:
                tipos_indirectos.add(ti)
    nombres_indirectos = _filtrar_existentes(list(tipos_indirectos), todos)

    return BurbujaEspacio(
        nombre=espacio.nombre,
        tipo=tipo,
        zona=zona,
        area_m2=espacio.area_m2,
        tamano=tamano,
        orientacion_ideal=orientacion,
        relaciones_directas=nombres_directos,
        relaciones_indirectas=nombres_indirectos,
    )


def _filtrar_existentes(
    tipos_buscados: list[str],
    todos: list[tuple[str, str]],
) -> list[str]:
    """
    Dado una lista de tipos buscados, retorna los nombres de los espacios
    del programa que corresponden a esos tipos.
    """
    resultado: list[str] = []
    for tipo_b in tipos_buscados:
        for nombre, tipo in todos:
            if tipo == tipo_b and nombre not in resultado:
                resultado.append(nombre)
    return resultado


def _detectar_acceso(diagrama: DiagramaFuncional) -> str:
    """Detecta el espacio de acceso principal (en orden de prioridad)."""
    prioridad = ["vestibulo", "sala_comedor", "sala"]
    for tipo_buscado in prioridad:
        for b in diagrama.burbujas:
            if b.tipo == tipo_buscado:
                return b.nombre
    # Fallback: primer espacio público
    for b in diagrama.burbujas:
        if b.zona == "publica":
            return b.nombre
    return diagrama.burbujas[0].nombre if diagrama.burbujas else ""


def _asignar_circulaciones(diagrama: DiagramaFuncional):
    """Marca las circulaciones secundarias (pasillos, conectores)."""
    for b in diagrama.burbujas:
        if b.tipo in ("pasillo", "vestibulo") and not b.es_acceso:
            b.circulacion = "secundaria"
        elif b.zona == "privada" and b.tipo in ("dormitorio", "dormitorio_principal"):
            # Los dormitorios son nodos de circulación secundaria privada
            if b.circulacion == "ninguna":
                b.circulacion = "secundaria"


def _generar_concepto(
    diagrama: DiagramaFuncional,
    programa: ProgramaArquitectonico,
) -> str:
    """Genera el texto del concepto arquitectónico general."""
    n_dorm = programa.n_dormitorios
    norte = diagrama.norte_lote.upper()
    perfil = programa.perfil.value

    tiene_cochera  = any(b.tipo == "cochera"  for b in diagrama.burbujas)
    tiene_piscina  = any(b.tipo == "piscina"  for b in diagrama.burbujas)
    tiene_terraza  = any(b.tipo == "terraza"  for b in diagrama.burbujas)
    tiene_sala_tv  = any(b.tipo == "sala_tv"  for b in diagrama.burbujas)

    extras = []
    if tiene_cochera: extras.append("cochera")
    if tiene_piscina: extras.append("piscina")
    if tiene_terraza: extras.append("terraza")
    if tiene_sala_tv: extras.append("sala TV")

    extra_str = (", con " + " y ".join(extras)) if extras else ""

    return (
        f"Casa de {n_dorm} dormitorio(s) para perfil {perfil}{extra_str}. "
        f"Frente del lote hacia el {norte}. "
        f"Zona publica (sala-comedor-cocina) orientada al norte para maxima "
        f"iluminacion y confort. Zona privada (dormitorios) al sur/este con "
        f"privacidad y ventilacion cruzada. Humedas y servicios agrupados "
        f"al oeste/suroeste para aislar el calor de tarde."
    )


def _generar_decisiones(
    diagrama: DiagramaFuncional,
    programa: ProgramaArquitectonico,
    norte_lote: str,
):
    """Genera la lista de decisiones arquitectónicas clave."""
    d = diagrama.decisiones
    w = diagrama.advertencias
    norte = norte_lote.lower()

    # Decisión 1: acceso
    d.append(
        f"Acceso principal por {diagrama.acceso_principal} — "
        f"orientado al {norte} (frente del lote)."
    )

    # Decisión 2: zona pública
    pub = [b.nombre for b in diagrama.zona_publica
           if b.tipo not in ("vestibulo", "cochera", "pasillo")]
    if pub:
        d.append(
            f"Zona publica ({', '.join(pub)}) al norte: "
            f"mejor iluminacion, sin calor de tarde, luz pareja."
        )

    # Decisión 3: zona privada
    priv = [b.nombre for b in diagrama.zona_privada
            if b.tipo in ("dormitorio", "dormitorio_principal")]
    if priv:
        d.append(
            f"Zona privada ({', '.join(priv)}) al sur/sureste/suroeste/este: "
            f"privacidad, ventilacion cruzada, luz controlable con aleros."
        )

    # Decisión 4: humedas agrupadas
    hum = [b.nombre for b in diagrama.zona_humeda]
    if hum:
        d.append(
            f"Areas humedas ({', '.join(hum)}) agrupadas al oeste/suroeste: "
            f"aislan el calor de tarde y simplifican la instalacion sanitaria."
        )

    # Decisión 5: cochera
    for b in diagrama.burbujas:
        if b.tipo == "cochera":
            d.append(
                f"Cochera vinculada al vestibulo — acceso directo desde exterior "
                f"sin cruzar zona privada."
            )
            break

    # Decisión 6: ventilacion cruzada
    if programa.n_dormitorios >= 2:
        d.append(
            "Ventilacion cruzada: ventanas al sur/este en dormitorios + "
            "ventanas al norte en sala aseguran flujo de aire natural."
        )

    # Advertencias
    if norte_lote.lower() in ("oeste", "suroeste"):
        w.append(
            f"ALERTA: frente al {norte_lote.upper()} — orientacion desfavorable. "
            f"El area social recibira calor de tarde. "
            f"Considerar parasoles, aleros profundos o vegetacion."
        )

    tiene_dorm_principal = any(
        b.tipo == "dormitorio_principal" for b in diagrama.burbujas
    )
    tiene_bano_principal = any(
        b.tipo == "bano_principal" for b in diagrama.burbujas
    )
    if tiene_dorm_principal and not tiene_bano_principal:
        w.append(
            "El dormitorio principal no tiene bano propio — "
            "agregar bano principal o asegurar acceso privado al compartido."
        )


# ═══════════════════════════════════════════════════════════════════════
# LECTURA DESDE IMAGEN — Prompt de visión para Claude
# ═══════════════════════════════════════════════════════════════════════

def prompt_lectura_imagen() -> str:
    """
    Retorna el system prompt para que Claude Vision analice una imagen
    de diagrama de burbujas dibujado a mano y extraiga el DiagramaFuncional.

    Uso en el Hub:
        respuesta = cliente_anthropic.messages.create(
            model="claude-opus-4-5",
            system=prompt_lectura_imagen(),
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {...}},
                    {"type": "text", "text": "Analiza este diagrama."}
                ]
            }]
        )
        data = json.loads(respuesta.content[0].text)
        diagrama = desde_dict(data)
    """
    return """Eres un arquitecto experto en diagramas funcionales y programas arquitectónicos.
Analiza la imagen y extrae TODA la información del diagrama de burbujas en formato JSON.

INSTRUCCIONES:
1. Identifica cada espacio (burbuja/círculo) con su nombre y tamaño relativo.
2. Determina la zona de cada espacio: "publica", "privada", "humeda" o "servicio".
3. Lee las áreas en m² si están escritas. Si no, estímalas por tamaño de burbuja.
4. Identifica las relaciones: flechas, líneas o contacto entre burbujas.
5. Determina la orientación cardinal (dónde está el NORTE).
6. Identifica el acceso principal (entrada al proyecto).
7. Lee el programa de áreas si está escrito en el diagrama.

CLASIFICACIÓN DE TAMAÑO DE BURBUJA:
- grande  → sala, comedor, cochera, terraza, piscina (≥14 m²)
- mediano → dormitorios, cocina, estudio (7-14 m²)
- pequeno → baños, pilas, bodega, vestíbulos (<7 m²)

RETORNA ÚNICAMENTE un JSON con esta estructura exacta (sin texto adicional):
{
  "nombre_proyecto": "...",
  "tipo_proyecto": "casa|edificio|comercial",
  "norte_lote": "norte|sur|este|oeste",
  "area_total_m2": 0.0,
  "perfil": "baja|media|alta",
  "acceso_principal": "nombre del espacio de entrada",
  "concepto": "descripción breve del concepto observado",
  "burbujas": [
    {
      "nombre": "Sala",
      "tipo": "sala",
      "zona": "publica",
      "area_m2": 20.0,
      "tamano": "grande",
      "orientacion_ideal": ["norte"],
      "relaciones_directas": ["Comedor", "Terraza"],
      "relaciones_indirectas": ["Cocina"],
      "es_acceso": false,
      "circulacion": "ninguna",
      "nota": ""
    }
  ],
  "decisiones": [],
  "advertencias": []
}

TIPOS válidos para el campo "tipo":
sala, sala_comedor, comedor, cocina, vestibulo, cochera, terraza, piscina,
dormitorio_principal, dormitorio, sala_tv, estudio,
bano_principal, bano_compartido, bano_invitados, medio_bano,
lavanderia, cuarto_servicio, bodega, pasillo, cuarto_tecnico
"""


def desde_dict(data: dict) -> DiagramaFuncional:
    """
    Construye un DiagramaFuncional desde el dict JSON
    que devuelve Claude Vision al leer una imagen.
    """
    burbujas: list[BurbujaEspacio] = []
    for b in data.get("burbujas", []):
        burbujas.append(BurbujaEspacio(
            nombre=b.get("nombre", ""),
            tipo=b.get("tipo", ""),
            zona=b.get("zona", "publica"),
            area_m2=float(b.get("area_m2", 0.0)),
            tamano=b.get("tamano", "mediano"),
            orientacion_ideal=b.get("orientacion_ideal", []),
            relaciones_directas=b.get("relaciones_directas", []),
            relaciones_indirectas=b.get("relaciones_indirectas", []),
            es_acceso=b.get("es_acceso", False),
            circulacion=b.get("circulacion", "ninguna"),
            nota=b.get("nota", ""),
        ))

    return DiagramaFuncional(
        nombre_proyecto=data.get("nombre_proyecto", "Proyecto"),
        tipo_proyecto=data.get("tipo_proyecto", "casa"),
        norte_lote=data.get("norte_lote", "norte"),
        area_total_m2=float(data.get("area_total_m2", 0.0)),
        perfil=data.get("perfil", "media"),
        acceso_principal=data.get("acceso_principal", ""),
        concepto=data.get("concepto", ""),
        decisiones=data.get("decisiones", []),
        advertencias=data.get("advertencias", []),
        origen="arquitecto",   # siempre arquitecto cuando viene de imagen
        burbujas=burbujas,
    )


def desde_json(json_str: str) -> DiagramaFuncional:
    """Construye un DiagramaFuncional desde un string JSON."""
    return desde_dict(json.loads(json_str))


# ═══════════════════════════════════════════════════════════════════════
# SALIDA DE TEXTO — GUI, log y prompt
# ═══════════════════════════════════════════════════════════════════════

_ZONA_LABEL = {
    "publica":  "PUBLICA ",
    "privada":  "PRIVADA ",
    "humeda":   "HUMEDA  ",
    "servicio": "SERVICIO",
}
_TAMANO_LABEL = {
    "grande":  "( O )",   # burbuja grande
    "mediano": "( o )",   # burbuja mediana
    "pequeno": "( . )",   # burbuja pequeña
}


def texto_diagrama(diagrama: DiagramaFuncional) -> str:
    """
    Formatea el diagrama funcional para mostrar en GUI, log o inyectar
    como contexto al AI. ASCII puro, seguro en cualquier consola.
    """
    lineas = [
        f"==========================================",
        f"  DIAGRAMA FUNCIONAL -- Paso 0",
        f"  {diagrama.nombre_proyecto}",
        f"==========================================",
        f"  Tipo      : {diagrama.tipo_proyecto.upper()}",
        f"  Norte lote: {diagrama.norte_lote.upper()}",
        f"  Area total: {diagrama.area_total_m2:.0f} m2",
        f"  Perfil    : {diagrama.perfil}",
        f"  Acceso    : {diagrama.acceso_principal}",
        f"",
        f"  {'ESPACIO':<24} {'ZONA':<10} {'TAM':<7} {'AREA':>6}  ORIENTACION IDEAL",
        f"  {'-'*72}",
    ]

    for b in diagrama.burbujas:
        zona_lbl  = _ZONA_LABEL.get(b.zona, b.zona[:8].ljust(8))
        tam_lbl   = _TAMANO_LABEL.get(b.tamano, "( ? )")
        orient    = ", ".join(b.orientacion_ideal[:2]) if b.orientacion_ideal else "libre"
        acceso_mk = " <ACCESO>" if b.es_acceso else ""
        lineas.append(
            f"  {tam_lbl} {b.nombre:<20} {zona_lbl}  {b.area_m2:>5.1f}m2  {orient}{acceso_mk}"
        )

    # Relaciones
    lineas += ["", "  RELACIONES DIRECTAS:", "  " + "-" * 50]
    for b in diagrama.burbujas:
        if b.relaciones_directas:
            rels = " -- ".join(b.relaciones_directas)
            lineas.append(f"    {b.nombre:22} -->  {rels}")

    # Zonas agrupadas
    lineas += ["", "  ZONIFICACION:", "  " + "-" * 50]
    for zona_key, zona_lbl in [
        ("publica",  "PUBLICA "),
        ("privada",  "PRIVADA "),
        ("humeda",   "HUMEDA  "),
        ("servicio", "SERVICIO"),
    ]:
        esp = [b.nombre for b in diagrama.burbujas if b.zona == zona_key]
        if esp:
            lineas.append(f"    [{zona_lbl}] {', '.join(esp)}")

    # Concepto
    if diagrama.concepto:
        lineas += ["", "  CONCEPTO:", "  " + "-" * 50]
        # Partir el concepto en líneas de ~60 caracteres
        palabras = diagrama.concepto.split()
        linea_actual = "    "
        for palabra in palabras:
            if len(linea_actual) + len(palabra) + 1 > 64:
                lineas.append(linea_actual)
                linea_actual = "    " + palabra + " "
            else:
                linea_actual += palabra + " "
        if linea_actual.strip():
            lineas.append(linea_actual)

    # Decisiones
    if diagrama.decisiones:
        lineas += ["", "  DECISIONES ARQUITECTONICAS:", "  " + "-" * 50]
        for i, d in enumerate(diagrama.decisiones, 1):
            # Partir líneas largas
            palabras = d.split()
            linea_actual = f"    {i}. "
            for palabra in palabras:
                if len(linea_actual) + len(palabra) + 1 > 70:
                    lineas.append(linea_actual)
                    linea_actual = "       " + palabra + " "
                else:
                    linea_actual += palabra + " "
            if linea_actual.strip():
                lineas.append(linea_actual)

    # Advertencias
    if diagrama.advertencias:
        lineas += ["", "  [!!] ADVERTENCIAS:", "  " + "-" * 50]
        for a in diagrama.advertencias:
            lineas.append(f"    x {a}")

    return "\n".join(lineas)


def texto_diagrama_para_prompt(diagrama: DiagramaFuncional) -> str:
    """
    Version compacta para inyectar al AI como contexto del Paso 0.
    Resume el diagrama en pocas lineas para no saturar el prompt.
    """
    pub  = [b.nombre for b in diagrama.zona_publica]
    priv = [b.nombre for b in diagrama.zona_privada]
    hum  = [b.nombre for b in diagrama.zona_humeda]
    serv = [b.nombre for b in diagrama.zona_servicio]

    lineas = [
        "DIAGRAMA FUNCIONAL (Paso 0 — pre-diseno):",
        f"  Norte del lote: {diagrama.norte_lote.upper()}",
        f"  Acceso principal: {diagrama.acceso_principal}",
        f"  Zona publica  : {', '.join(pub)}",
        f"  Zona privada  : {', '.join(priv)}",
        f"  Zona humeda   : {', '.join(hum)}",
    ]
    if serv:
        lineas.append(f"  Zona servicio : {', '.join(serv)}")

    lineas += [
        "  Relaciones clave:",
    ]
    for b in diagrama.burbujas:
        if b.relaciones_directas and b.tamano in ("grande", "mediano"):
            rels = ", ".join(b.relaciones_directas[:3])
            lineas.append(f"    {b.nombre} --> {rels}")

    if diagrama.concepto:
        lineas.append(f"  Concepto: {diagrama.concepto[:120]}...")

    lineas.append(
        "  REGLA: respetar zonificacion y relaciones definidas en este diagrama."
    )
    return "\n".join(lineas)


# ═══════════════════════════════════════════════════════════════════════
# SMOKE TEST — Casa 3 dormitorios, frente al norte, perfil media
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
# LAYOUT — Convierte DiagramaFuncional → SpatialGraph con posiciones
# ═══════════════════════════════════════════════════════════════════════

def _dims_espacio(area_m2: float, tipo: str = "") -> tuple[int, int]:
    """
    Calcula dimensiones enteras (ancho, alto) en celdas para un espacio.
    1 celda = 1 m. Respeta dimensiones mínimas normativas por tipo.
    Mínimo 2×2 para cualquier espacio.
    """
    import math

    # Dimensiones mínimas por tipo (ancho_min, alto_min) en metros
    _DIMS_MIN: dict[str, tuple[int, int]] = {
        "cochera":              (3, 5),   # INVU 1 auto: 2.75×5.0m → 3×5
        "dormitorio_principal": (3, 3),
        "dormitorio":           (3, 3),
        "sala":                 (3, 3),
        "comedor":              (3, 3),
        "cocina":               (2, 3),
        "bano_principal":       (2, 3),
        "bano_compartido":      (2, 2),
        "lavanderia":           (2, 2),
        "vestibulo":            (2, 2),
        "pasillo":              (2, 2),
    }

    mn_a, mn_b = _DIMS_MIN.get(tipo, (2, 2))

    lado_menor = max(mn_a, round(math.sqrt(area_m2 * 0.75)))
    lado_mayor = max(mn_b, max(lado_menor, math.ceil(area_m2 / lado_menor)))
    return (lado_menor, lado_mayor)   # (ancho, alto)


def _regiones_por_norte(norte_lote: str, grid_w: int, grid_h: int) -> dict[str, dict]:
    """
    Define la región (col_ini, col_fin, fila_ini, fila_fin) de cada zona
    en el grid, ajustada según dónde está el norte del lote.

    Coordenadas del grid: fila=0 = NORTE físico (siempre).
    El frente del lote puede apuntar a cualquier cardinal, pero el grid
    siempre tiene norte arriba — rotamos las zonas en consecuencia.

    Zonas:
        publica  → van hacia el norte (fila baja)
        privada  → van hacia el sur  (fila alta)
        humeda   → van hacia el oeste (col baja)
        servicio → oeste/suroeste
    """
    # Franja húmeda en el lado oeste (columnas 0..hum_w)
    hum_w = max(2, grid_w // 5)
    main_col_ini = hum_w
    main_col_fin = grid_w

    # Franja pública en el norte (filas 0..pub_h)
    pub_h = max(3, grid_h // 3)
    priv_fila_ini = pub_h

    if norte_lote == "norte":
        # Frente al norte: pública arriba, privada abajo
        pub_region  = {"col_ini": main_col_ini, "col_fin": main_col_fin,
                       "fila_ini": 0,           "fila_fin": pub_h}
        priv_region = {"col_ini": main_col_ini, "col_fin": main_col_fin,
                       "fila_ini": priv_fila_ini, "fila_fin": grid_h}

    elif norte_lote == "sur":
        # Norte físico sigue siendo fila=0, pero el frente del lote es al sur.
        # Pública quiere ir al norte → va abajo en el lote (alta fila),
        # pero como norte físico es fila=0 en el grid, la ponemos arriba igual
        # para maximizar el score. El arquitecto girará el plano en AutoCAD.
        pub_region  = {"col_ini": main_col_ini, "col_fin": main_col_fin,
                       "fila_ini": 0,           "fila_fin": pub_h}
        priv_region = {"col_ini": main_col_ini, "col_fin": main_col_fin,
                       "fila_ini": priv_fila_ini, "fila_fin": grid_h}

    elif norte_lote == "este":
        # Norte arriba, frente al este → pública al este (col alta)
        east_col = max(main_col_ini, grid_w - grid_w // 3)
        pub_region  = {"col_ini": east_col,     "col_fin": grid_w,
                       "fila_ini": 0,           "fila_fin": grid_h}
        priv_region = {"col_ini": main_col_ini, "col_fin": east_col,
                       "fila_ini": priv_fila_ini, "fila_fin": grid_h}

    else:  # oeste — frente al oeste (desfavorable)
        pub_region  = {"col_ini": main_col_ini, "col_fin": main_col_fin,
                       "fila_ini": 0,           "fila_fin": pub_h}
        priv_region = {"col_ini": main_col_ini, "col_fin": main_col_fin,
                       "fila_ini": priv_fila_ini, "fila_fin": grid_h}

    hum_region  = {"col_ini": 0, "col_fin": hum_w,
                   "fila_ini": 0, "fila_fin": grid_h}
    serv_region = {"col_ini": 0, "col_fin": hum_w,
                   "fila_ini": grid_h // 2, "fila_fin": grid_h}

    return {
        "publica":  pub_region,
        "privada":  priv_region,
        "humeda":   hum_region,
        "servicio": serv_region,
    }


def _empacar_espacios(
    espacios: list[BurbujaEspacio],
    region: dict,
) -> list[tuple[BurbujaEspacio, int, int, int, int]]:
    """
    Empaca una lista de espacios dentro de una región del grid
    usando el algoritmo de estanterías (shelf packing):
    coloca izquierda → derecha, salta a nueva fila cuando se acaba el ancho.

    Retorna lista de (burbuja, col, fila, ancho, alto).
    """
    col_ini   = region["col_ini"]
    col_fin   = region["col_fin"]
    fila_ini  = region["fila_ini"]
    region_w  = max(1, col_fin - col_ini)

    resultados: list[tuple[BurbujaEspacio, int, int, int, int]] = []

    cur_col  = col_ini
    cur_fila = fila_ini
    altura_estante = 0

    # Ordenar: más grandes primero para mejor empaquetado
    espacios_sorted = sorted(espacios, key=lambda b: b.area_m2, reverse=True)

    for burbuja in espacios_sorted:
        ancho, alto = _dims_espacio(burbuja.area_m2, burbuja.tipo)

        # Si no cabe en el ancho restante, nueva estantería
        if cur_col + ancho > col_fin and cur_col != col_ini:
            cur_col   = col_ini
            cur_fila += altura_estante
            altura_estante = 0

        # Si el espacio es más ancho que toda la región, ajustar ancho
        # pero respetar el alto mínimo normativo
        if ancho > region_w:
            ancho = region_w
            _, alto_min = _dims_espacio(burbuja.area_m2, burbuja.tipo)
            alto = max(alto_min, math.ceil(burbuja.area_m2 / max(1, ancho)))

        resultados.append((burbuja, cur_col, cur_fila, ancho, alto))

        cur_col += ancho
        altura_estante = max(altura_estante, alto)

    return resultados


def a_spatial_graph(
    diagrama: DiagramaFuncional,
    grid_w: int = 10,
) -> "SpatialGraph":
    """
    Convierte un DiagramaFuncional en un SpatialGraph con posiciones
    en grid, listo para analizar_reglas_merlos().

    El layout respeta:
    - Zona pública  → norte (filas bajas)
    - Zona privada  → sur   (filas altas)
    - Zona húmeda   → oeste (columnas bajas)
    - Zona servicio → oeste/suroeste

    Args:
        diagrama : DiagramaFuncional generado por generar_diagrama()
        grid_w   : ancho del grid en celdas/metros (default 10m)

    Returns:
        SpatialGraph con nodos posicionados y aristas de puertas.
    """
    # Importar aquí para evitar dependencia circular en el módulo
    from sle.core.spatial_graph import SpatialGraph, NodoEspacial, AristaEspacial
    import math

    # Agrupar burbujas por zona
    por_zona: dict[str, list[BurbujaEspacio]] = {
        "publica": [], "privada": [], "humeda": [], "servicio": []
    }
    for b in diagrama.burbujas:
        zona = b.zona if b.zona in por_zona else "servicio"
        por_zona[zona].append(b)

    # Ancho de franja húmeda (oeste)
    hum_w = max(2, grid_w // 5)
    main_w = grid_w - hum_w  # ancho disponible para público y privado

    # Alturas de franja calculadas desde el área real de cada zona
    def _altura_franja(burbujas: list[BurbujaEspacio]) -> int:
        if not burbujas:
            return 0
        area = sum(b.area_m2 for b in burbujas)
        return max(4, math.ceil(area / main_w) + 2)

    pub_h  = _altura_franja(por_zona["publica"])
    priv_h = _altura_franja(por_zona["privada"])
    hum_h  = pub_h + priv_h   # la franja húmeda corre todo el alto

    grid_h = pub_h + priv_h + 2  # +2 padding

    # Construir regiones con dimensiones dinámicas
    if diagrama.norte_lote in ("norte", "sur"):
        pub_region  = {"col_ini": hum_w, "col_fin": grid_w,
                       "fila_ini": 0,     "fila_fin": pub_h}
        priv_region = {"col_ini": hum_w, "col_fin": grid_w,
                       "fila_ini": pub_h, "fila_fin": pub_h + priv_h}
    elif diagrama.norte_lote == "este":
        east_col    = max(hum_w + 2, grid_w - grid_w // 3)
        pub_region  = {"col_ini": east_col, "col_fin": grid_w,
                       "fila_ini": 0,       "fila_fin": grid_h}
        priv_region = {"col_ini": hum_w,    "col_fin": east_col,
                       "fila_ini": pub_h,   "fila_fin": grid_h}
    else:  # oeste
        pub_region  = {"col_ini": hum_w, "col_fin": grid_w,
                       "fila_ini": 0,     "fila_fin": pub_h}
        priv_region = {"col_ini": hum_w, "col_fin": grid_w,
                       "fila_ini": pub_h, "fila_fin": pub_h + priv_h}

    hum_region  = {"col_ini": 0, "col_fin": hum_w,
                   "fila_ini": 0, "fila_fin": hum_h}
    serv_region = {"col_ini": 0, "col_fin": hum_w,
                   "fila_ini": hum_h // 2, "fila_fin": hum_h}

    regiones_map = {
        "publica":  pub_region,
        "privada":  priv_region,
        "humeda":   hum_region,
        "servicio": serv_region,
    }

    # Empacar cada zona en su región
    colocados: list[tuple[BurbujaEspacio, int, int, int, int]] = []
    for zona, burbujas in por_zona.items():
        if burbujas:
            region = regiones_map[zona]
            colocados += _empacar_espacios(burbujas, region)

    # Determinar dimensiones finales del grid
    max_col  = max((col + ancho for _, col, _, ancho, _ in colocados), default=grid_w)
    max_fila = max((fila + alto for _, _, fila, _, alto in colocados), default=grid_h)

    # Construir SpatialGraph
    grafo = SpatialGraph(ancho_m=float(max_col), alto_m=float(max_fila))

    # Construir índice nombre → posición para relaciones
    pos_por_nombre: dict[str, tuple[int, int, int, int]] = {}

    for burbuja, col, fila, ancho, alto in colocados:
        nodo = NodoEspacial(
            nombre=burbuja.nombre,
            tipo=burbuja.tipo,
            zona=burbuja.zona,
            fila=fila,
            col=col,
            ancho=ancho,
            alto=alto,
        )
        grafo.nodos[burbuja.nombre] = nodo
        pos_por_nombre[burbuja.nombre] = (col, fila, ancho, alto)

    # Generar aristas desde relaciones directas → puertas interiores
    pares_vistos: set[tuple[str, str]] = set()
    for burbuja in diagrama.burbujas:
        for vecino_nombre in burbuja.relaciones_directas:
            if vecino_nombre in grafo.nodos:
                par = tuple(sorted([burbuja.nombre, vecino_nombre]))
                if par not in pares_vistos:
                    pares_vistos.add(par)
                    # Tipo de puerta según zona
                    ancho_puerta = 1.10 if burbuja.zona == "publica" else 1.00
                    grafo.aristas.append(AristaEspacial(
                        nodo_a=burbuja.nombre,
                        nodo_b=vecino_nombre,
                        tipo="puerta",
                        ancho=ancho_puerta,
                    ))

    # Arista exterior — acceso principal
    acceso = diagrama.acceso_principal
    if acceso and acceso in grafo.nodos:
        # El lado de la puerta exterior depende del norte del lote
        lado_entrada = {
            "norte": "norte", "sur": "sur",
            "este": "este",   "oeste": "oeste",
        }.get(diagrama.norte_lote, "norte")
        grafo.aristas.append(AristaEspacial(
            nodo_a=acceso,
            nodo_b=None,
            tipo="exterior",
            lado=lado_entrada,
            ancho=1.10,
        ))

    return grafo


# ═══════════════════════════════════════════════════════════════════════
# JERARQUÍA DEL DIAGRAMA — El arquitecto manda sobre R1 y R2
# ═══════════════════════════════════════════════════════════════════════

def aplicar_jerarquia_diagrama(
    resultado: "ResultadoReglasMerlos",
    diagrama: DiagramaFuncional,
) -> "ResultadoReglasMerlos":
    """
    Cuando el diagrama es del arquitecto (origen='arquitecto'), las
    violaciones de R1 (zonificación) y R2 (orientación cardinal) se
    convierten en notas informativas, no en errores.

    Razonamiento:
        El diagrama de burbujas es la INTENCIÓN DE DISEÑO del arquitecto.
        Puede haber razones válidas para romper la orientación ideal:
        vistas, forma del lote, requerimiento del cliente, concepto
        arquitectónico, topografía, etc.

        Las reglas R3-R6 (jerarquía de proporciones y normativa INVU/CFIA)
        SIEMPRE aplican porque son compromisos del propio arquitecto y
        requisitos legales — no dependen de la orientación del lote.

    Resultado:
        - Violaciones R1/R2  → se mueven a 'notas_arquitecto' (texto
          informativo) y se eliminan de 'violaciones'
        - Score bioclimático  → se mantiene como referencia informativa
          (el arquitecto sabe que está sacrificando algo y lo acepta)
        - R3-R6              → sin cambios
    """
    if diagrama.origen != "arquitecto":
        return resultado

    # Inicializar lista de notas si no existe
    if not hasattr(resultado, "notas_arquitecto"):
        resultado.notas_arquitecto = []

    nuevas_violaciones: list[str] = []
    for v in resultado.violaciones:
        if "REGLA1" in v or "REGLA2" in v:
            # Reformatear como nota informativa
            nota = (
                v.replace("✗", "→")
                 .replace("[!!]", "")
                 .strip()
            )
            resultado.notas_arquitecto.append(
                f"NOTA bioclimatica: {nota} "
                f"[arquitecto justifica por decision de diseno]"
            )
        else:
            nuevas_violaciones.append(v)

    resultado.violaciones = nuevas_violaciones

    # También mover sugerencias R1/R2 a notas
    nuevas_sugerencias: list[str] = []
    for s in resultado.sugerencias:
        if "REGLA1" in s or "REGLA2" in s:
            resultado.notas_arquitecto.append(
                f"NOTA bioclimatica: {s.replace('○', '').strip()} "
                f"[arquitecto justifica por decision de diseno]"
            )
        else:
            nuevas_sugerencias.append(s)
    resultado.sugerencias = nuevas_sugerencias

    return resultado


# ═══════════════════════════════════════════════════════════════════════
# CICLO COMPLETO — Brief → Programa → Diagrama → Grafo → Reglas
# ═══════════════════════════════════════════════════════════════════════

def ciclo_completo(
    total_m2: float,
    n_dormitorios: int,
    norte_lote: str = "norte",
    opcionales: list[str] | None = None,
    nombre_proyecto: str = "Proyecto",
    tipo_proyecto: str = "casa",
    concepto: str = "",
    grid_w: int = 10,
) -> dict[str, Any]:
    """
    Ejecuta el Paso 0 completo del diseño arquitectónico:

        Brief del cliente
            → Programa arquitectónico (áreas + normativa CR)
            → Diagrama funcional (burbujas + relaciones + orientación)
            → SpatialGraph (posiciones en grid)
            → Análisis reglas Merlos (score + violaciones + sugerencias)

    Args:
        total_m2      : metros cuadrados totales de construcción
        n_dormitorios : número de dormitorios (1-4)
        norte_lote    : orientación del norte del lote ('norte'|'sur'|'este'|'oeste')
        opcionales    : espacios adicionales (['cochera','sala_tv','piscina'...])
        nombre_proyecto: nombre del proyecto
        tipo_proyecto  : 'casa'|'edificio'|'comercial'
        concepto       : concepto arquitectónico libre (opcional)
        grid_w         : ancho del grid en metros (default 10)

    Returns:
        dict con claves:
            'programa'  : ProgramaArquitectonico
            'diagrama'  : DiagramaFuncional
            'grafo'     : SpatialGraph
            'reglas'    : ResultadoReglasMerlos
            'texto'     : resumen completo en texto ASCII
    """
    from sle.core.reglas_merlos import analizar_reglas_merlos, texto_analisis_merlos
    from sle.core.programa_arquitectonico import texto_programa

    # ── Paso 0a: Programa arquitectónico ──────────────────────────────
    programa = generar_programa(
        total_m2=total_m2,
        n_dormitorios=n_dormitorios,
        opcionales=opcionales or [],
    )

    # ── Paso 0b: Diagrama funcional ───────────────────────────────────
    diagrama = generar_diagrama(
        programa=programa,
        norte_lote=norte_lote,
        nombre_proyecto=nombre_proyecto,
        tipo_proyecto=tipo_proyecto,
        concepto=concepto,
    )

    # ── Paso 0c: SpatialGraph con posiciones ──────────────────────────
    grafo = a_spatial_graph(diagrama, grid_w=grid_w)

    # ── Paso 0d: Análisis reglas Merlos ───────────────────────────────
    reglas = analizar_reglas_merlos(grafo)

    # ── Paso 0e: Aplicar jerarquía del diagrama (arquitecto manda) ────
    aplicar_jerarquia_diagrama(reglas, diagrama)

    # ── Texto resumen completo ────────────────────────────────────────
    separador = "\n" + "=" * 50 + "\n"
    texto = (
        texto_programa(programa)
        + separador
        + texto_diagrama(diagrama)
        + separador
        + texto_analisis_merlos(reglas)
    )

    return {
        "programa": programa,
        "diagrama": diagrama,
        "grafo":    grafo,
        "reglas":   reglas,
        "texto":    texto,
    }


# ═══════════════════════════════════════════════════════════════════════
# SMOKE TEST
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    # ── Ciclo completo: casa 3 dorm, 140m², frente norte, con cochera ──
    resultado = ciclo_completo(
        total_m2=140,
        n_dormitorios=3,
        norte_lote="norte",
        opcionales=["cochera"],
        nombre_proyecto="Casa Familia Merlos — Prueba",
    )

    print(resultado["texto"])

    print()
    print("=" * 50)
    print("GRAFO — Nodos y aristas:")
    g = resultado["grafo"]
    print(f"  Nodos: {len(g.nodos)}  |  Aristas: {len(g.aristas)}")
    for nombre, nodo in g.nodos.items():
        print(f"  [{nodo.zona[:3].upper()}] {nombre:24} fila={nodo.fila:2d} col={nodo.col:2d}"
              f" {nodo.ancho}x{nodo.alto}={nodo.area_m2:.0f}m2")
    print()
    val = g.validate_topology()
    print(f"Topologia: {'OK' if val['ok'] else 'CON ERRORES'}")
    for e in val.get("errores", []):
        print(f"  [!!] {e}")
    for a in val.get("advertencias", []):
        print(f"  [--] {a}")
