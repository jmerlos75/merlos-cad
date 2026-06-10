"""
sle/data/importar_dwg_merlos.py
================================
Importa DWGs con capas estilo Merlos (A-TEXTO, A-PAREDES) al SLE.
Soporta múltiples niveles en el mismo archivo.

Uso:
    python -m sle.data.importar_dwg_merlos
    python -m sle.data.importar_dwg_merlos --dwg "C:/ruta/plano.dwg"
    python -m sle.data.importar_dwg_merlos --dwg "plano.dwg" --activo
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from pathlib import Path
from typing import Optional

SLE_DIR = Path(r"C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI")
sys.path.insert(0, str(SLE_DIR))

from sle.core.spatial_graph import SpatialGraph, NodoEspacial, AristaEspacial, zona_de_tipo, detectar_tipo
from sle.core.reglas_merlos import analizar_reglas_merlos
from sle.core.memory        import Memoria

# ─── Limpieza de texto MText ──────────────────────────────────────────

_RE_MTEXT = re.compile(r'\\[pP][^;]*;|\\[A-Za-z][^;]*;|[{}]')

def limpiar_mtext(s: str) -> str:
    """Elimina códigos de formato MText: \\pxqc; \\pxql; etc."""
    s = _RE_MTEXT.sub("", s)
    return s.strip().upper()


# ─── Geometría ────────────────────────────────────────────────────────

def _coords_a_puntos(coords_raw) -> list[tuple[float, float]]:
    coords = list(coords_raw)
    pts = []
    if len(coords) >= 6 and len(coords) % 3 == 0 and len(coords) % 2 != 0:
        for i in range(0, len(coords), 3):
            pts.append((coords[i], coords[i + 1]))
    else:
        for i in range(0, len(coords), 2):
            pts.append((coords[i], coords[i + 1]))
    return pts


def _area_poligono(pts: list[tuple[float, float]]) -> float:
    n = len(pts)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += pts[i][0] * pts[j][1]
        area -= pts[j][0] * pts[i][1]
    return abs(area) / 2.0


def _punto_en_poligono(px: float, py: float,
                        pts: list[tuple[float, float]]) -> bool:
    """Ray casting: True si (px,py) está dentro del polígono."""
    n = len(pts)
    dentro = False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi):
            dentro = not dentro
        j = i
    return dentro


def _bbox_pts(pts: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _es_cerrada(ent, tol: float = 0.5) -> bool:
    try:
        if ent.Closed:
            return True
    except Exception:
        pass
    try:
        pts = _coords_a_puntos(ent.Coordinates)
        if len(pts) < 3:
            return False
        return abs(pts[0][0] - pts[-1][0]) < tol and abs(pts[0][1] - pts[-1][1]) < tol
    except Exception:
        return False


# ─── Extracción desde AutoCAD ─────────────────────────────────────────

# Capas que SÍ contienen muros (se leen para ray-casting)
CAPAS_PAREDES = (
    "A-PAREDES", "A-PAREDES 0", "A-PAREDES 1", "A-PAREDES 2",
    "A-PAREDES 3", "A-PAREDES 4", "A-WALL", "PAREDES",
    "DIVISION DE ESPACIOS", "PAR", "PARED ZINK", "PARED ZINC",
    "A-PAREDES GYPSUM", "A-MURO",
)

# Capas que NUNCA se deben leer (cotas, ejes, mobiliario, etc.)
CAPAS_EXCLUIR = {
    "a-cota", "a-ejes", "a-eje", "ejes", "cotas", "cota",
    "a-muebles", "muebles", "muebles1", "a-texto", "texto",
    "a-puertas", "puertas", "a-ventanas", "ventanas",
    "puertas ventanas", "a-escaleras", "escaleras",
    "a-techo", "techo", "a-cielo", "cielo",
    "elec", "toma", "agua", "alcantarillado",
    "imagen", "defpoints", "barandas",
}

POLY_TYPES = ("AcDbPolyline", "AcDb2dPolyline", "AcDbLwPolyline", "AcDb3dPolyline")


def extraer_textos(mspace) -> list[dict]:
    """Lee todas las etiquetas de A-TEXTO."""
    textos = []
    for ent in mspace:
        try:
            if ent.ObjectName not in ("AcDbText", "AcDbMText"):
                continue
            if ent.Layer != "A-TEXTO":
                continue
            texto = limpiar_mtext(ent.TextString)
            if not texto:
                continue
            ins = ent.InsertionPoint
            textos.append({
                "nombre": texto,
                "x": float(ins[0]),
                "y": float(ins[1]),
            })
        except Exception:
            continue
    return textos


def bbox_de_recintos(textos: list[dict], margen: float = 8.0) -> tuple[float,float,float,float]:
    """
    Calcula el bounding box de las etiquetas de recintos + margen.
    Usado para aislar la planta dentro de un DWG completo con fachadas/cortes.
    """
    if not textos:
        return (-1e9, -1e9, 1e9, 1e9)
    xs = [t["x"] for t in textos]
    ys = [t["y"] for t in textos]
    return (min(xs) - margen, min(ys) - margen,
            max(xs) + margen, max(ys) + margen)


def extraer_lineas_paredes(mspace, capas_extra: tuple = (),
                            zona: tuple | None = None) -> list[dict]:
    """
    Lee todas las líneas de las capas de muros.
    - Excluye capas de cotas, ejes, mobiliario.
    - Si se pasa 'zona' (x0,y0,x1,y1), solo lee líneas dentro de ese bbox.
      Esto permite aislar la planta en DWGs con fachadas/cortes/estructural.
    """
    lineas = []
    capas_ok = {c.lower() for c in CAPAS_PAREDES + capas_extra}
    capas_no = CAPAS_EXCLUIR
    zx0, zy0, zx1, zy1 = zona if zona else (-1e9, -1e9, 1e9, 1e9)

    for ent in mspace:
        try:
            if ent.ObjectName != "AcDbLine":
                continue
            capa = ent.Layer.lower()
            if capa in capas_no:
                continue
            if capa not in capas_ok:
                continue
            sp = ent.StartPoint
            ep = ent.EndPoint
            x1, y1 = float(sp[0]), float(sp[1])
            x2, y2 = float(ep[0]), float(ep[1])
            # Filtro de zona: al menos un extremo dentro del bbox de la planta
            if not (zx0 <= x1 <= zx1 and zy0 <= y1 <= zy1) and \
               not (zx0 <= x2 <= zx1 and zy0 <= y2 <= zy1):
                continue
            lineas.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        except Exception:
            continue
    return lineas


def diagnosticar_capas_dwg(mspace) -> dict:
    """
    Analiza el DWG activo y clasifica las capas encontradas.
    Útil para verificar antes de importar.
    """
    from collections import defaultdict
    capas: dict[str, dict] = defaultdict(lambda: {"lineas": 0, "otros": 0, "total": 0})

    for ent in mspace:
        try:
            capa = ent.Layer
            capas[capa]["total"] += 1
            if ent.ObjectName == "AcDbLine":
                capas[capa]["lineas"] += 1
            else:
                capas[capa]["otros"] += 1
        except Exception:
            continue

    capas_ok  = {c.lower() for c in CAPAS_PAREDES}
    capas_no  = CAPAS_EXCLUIR

    print("\n  DIAGNÓSTICO DE CAPAS:")
    print(f"  {'CAPA':<25} {'LÍNEAS':>7} {'OTROS':>7}  ESTADO")
    print(f"  {'-'*55}")
    for capa, info in sorted(capas.items()):
        capa_l = capa.lower()
        if capa_l in capas_no:
            estado = "[EXCLUIDA]"
        elif capa_l in capas_ok:
            estado = "[MURO OK]"
        elif info["lineas"] > 5:
            estado = "[?lineas - revisar]"
        else:
            estado = ""
        print(f"  {capa:<25} {info['lineas']:>7} {info['otros']:>7}  {estado}")

    return dict(capas)


def asignar_geometria_a_textos(textos: list[dict],
                                lineas: list[dict],
                                escala: float = 1.0) -> list[dict]:
    """
    Para cada texto usa el motor de 8 rayos (0/45/90/135/180/225/270/315°)
    para encontrar el polígono real del recinto — funciona con plantas
    ortogonales Y con ángulos de 45°.
    Fallback: bbox ortogonal con 4 rayos.
    """
    from sle.core.geometria import poligono_desde_paredes, bbox_poligono

    # Determinar tolerancias según escala del dibujo
    xs = ([ln["x1"] for ln in lineas] + [ln["x2"] for ln in lineas]) or [0, 1]
    rango_x = max(xs) - min(xs)
    es_mm   = rango_x > 1000
    tol      = 50.0    if es_mm else 0.05
    rango    = 30000.0 if es_mm else 30.0
    min_dist = 5.0     if es_mm else 0.005
    tol_dist = 50.0    if es_mm else 0.5
    esc      = escala if escala != 1.0 else (1000.0 if es_mm else 1.0)

    recintos = []
    for t in textos:
        px, py = t["x"], t["y"]
        bbox_raw = None
        ancho = alto = area = 3.0

        # Motor 8 rayos
        resultado = poligono_desde_paredes(
            px, py, lineas,
            rango=rango, min_dist=min_dist, tol_dist=tol_dist,
        )
        if resultado:
            verts  = resultado["vertices"]
            bb     = bbox_poligono(verts)
            ancho  = round((bb["x_max"] - bb["x_min"]) / esc, 2)
            alto   = round((bb["y_max"] - bb["y_min"]) / esc, 2)
            area   = round(resultado["area"] / (esc ** 2), 2)
            bbox_raw = (bb["x_min"], bb["y_min"], bb["x_max"], bb["y_max"])
            estimado = False
        else:
            # Fallback bbox ortogonal
            from dwg_to_sle import bbox_desde_paredes
            try:
                bb = bbox_desde_paredes(px, py, lineas, tol=tol, rango=rango)
                if bb:
                    ancho = round((bb["x_max"] - bb["x_min"]) / esc, 2)
                    alto  = round((bb["y_max"] - bb["y_min"]) / esc, 2)
                    area  = round(ancho * alto, 2)
                    bbox_raw = (bb["x_min"], bb["y_min"], bb["x_max"], bb["y_max"])
                    estimado = False
                else:
                    bbox_raw = (px - 1.5*esc, py - 1.5*esc, px + 1.5*esc, py + 1.5*esc)
                    estimado = True
            except Exception:
                bbox_raw = (px - 1.5*esc, py - 1.5*esc, px + 1.5*esc, py + 1.5*esc)
                estimado = True

        x0, y0, x1, y1 = bbox_raw
        recintos.append({
            "nombre":   t["nombre"],
            "x":        px,
            "y":        py,
            "ancho":    max(ancho, 0.5),
            "alto":     max(alto,  0.5),
            "area":     max(area,  0.25),
            "bbox":     bbox_raw,
            "cx":       (x0 + x1) / 2,
            "cy":       (y0 + y1) / 2,
            "estimado": estimado,
        })
    return recintos


# ─── Construcción del grafo ───────────────────────────────────────────

def _bboxes_adyacentes(b1, b2, tol: float = 0.4) -> bool:
    x0a, y0a, x1a, y1a = b1
    x0b, y0b, x1b, y1b = b2
    return (x0a <= x1b + tol and x0b <= x1a + tol and
            y0a <= y1b + tol and y0b <= y1a + tol)


def construir_grafo(recintos: list[dict],
                    factor: float = 1.0) -> SpatialGraph:
    """Construye SpatialGraph desde lista de recintos con nombre y geometría."""
    # Bounding box del nivel
    all_x0 = min(r["bbox"][0] for r in recintos)
    all_y0 = min(r["bbox"][1] for r in recintos)
    all_x1 = max(r["bbox"][2] for r in recintos)
    all_y1 = max(r["bbox"][3] for r in recintos)

    ancho_total = (all_x1 - all_x0) * factor
    alto_total  = (all_y1 - all_y0) * factor
    rng_x = (all_x1 - all_x0) or 1
    rng_y = (all_y1 - all_y0) or 1

    grafo = SpatialGraph(ancho_m=round(ancho_total, 1),
                         alto_m=round(alto_total, 1))

    nodos_bbox = {}
    conteo_tipos: dict[str, int] = {}

    for r in recintos:
        nombre_raw = r["nombre"]
        tipo = detectar_tipo(nombre_raw)

        # Sufijo numérico si hay duplicados del mismo tipo
        conteo_tipos[tipo] = conteo_tipos.get(tipo, 0) + 1
        n = conteo_tipos[tipo]
        nombre_nodo = f"{tipo.upper()}_{n}" if n > 1 else tipo.upper()

        # Posición en grid (relativa, escala 0-10)
        rx = ((r["cx"] - all_x0) / rng_x)
        ry = ((r["cy"] - all_y0) / rng_y)

        nodo = NodoEspacial(
            nombre=nombre_nodo,
            tipo=tipo,
            zona=zona_de_tipo(tipo),
            fila=round(ry * 10, 1),
            col=round(rx * 10, 1),
            ancho=round(r["ancho"] * factor, 2),
            alto=round(r["alto"]  * factor, 2),
        )
        grafo.nodos[nombre_nodo] = nodo
        nodos_bbox[nombre_nodo] = r["bbox"]

    # Adyacencias
    nombres = list(grafo.nodos.keys())
    for i in range(len(nombres)):
        for j in range(i + 1, len(nombres)):
            na, nb = nombres[i], nombres[j]
            if _bboxes_adyacentes(nodos_bbox[na], nodos_bbox[nb]):
                grafo.aristas.append(AristaEspacial(nodo_a=na, nodo_b=nb, tipo="adyacente"))

    return grafo


# ─── Pipeline principal ───────────────────────────────────────────────

def detectar_escala_textos(textos: list[dict]) -> float:
    """Si las coordenadas son >1000 → mm. Si >100 → cm."""
    if not textos:
        return 1.0
    xs = [t["x"] for t in textos]
    x_rng = max(xs) - min(xs)
    if x_rng > 10_000:
        return 0.001
    if x_rng > 500:
        return 0.01
    return 1.0


def separar_niveles(textos: list[dict]) -> list[list[dict]]:
    """
    Separa textos en niveles según agrupación en X.
    Usa gap grande en X para detectar separación entre plantas.
    """
    if not textos:
        return []
    textos_sorted = sorted(textos, key=lambda t: t["x"])
    xs = [t["x"] for t in textos_sorted]

    # Detectar gap > 20% del rango total entre grupos
    x_rng = xs[-1] - xs[0]
    gap_min = x_rng * 0.20

    grupos = [[textos_sorted[0]]]
    for t in textos_sorted[1:]:
        if t["x"] - grupos[-1][-1]["x"] > gap_min:
            grupos.append([])
        grupos[-1].append(t)

    return grupos


def importar_dwg_doc(doc, memoria: "Memoria", verbose: bool = True) -> int:
    """
    Procesa un documento AutoCAD ya abierto e importa sus niveles al SLE.
    Retorna el número de niveles importados.
    Puede llamarse desde el batch processor.
    """
    nombre_archivo = Path(doc.Name).stem
    if verbose:
        print(f"  DWG: {doc.Name}")

    mspace = doc.ModelSpace

    # ── Diagnóstico de capas ──────────────────────────────────────────
    if verbose:
        diagnosticar_capas_dwg(mspace)

    # ── Leer textos ───────────────────────────────────────────────────
    textos = extraer_textos(mspace)
    if verbose:
        print(f"\n  Etiquetas en A-TEXTO: {len(textos)}")
        for t in textos:
            print(f"    ({t['x']:6.1f}, {t['y']:6.1f})  {t['nombre']}")

    if not textos:
        print("  [ERR] No se encontraron textos en A-TEXTO — saltando")
        return 0

    # ── Escala ────────────────────────────────────────────────────────
    factor = detectar_escala_textos(textos)
    if factor != 1.0:
        print(f"  Escala detectada: {'mm->m' if factor==0.001 else 'cm->m'}")
        for t in textos:
            t["x"] *= factor
            t["y"] *= factor

    # ── Zona de la planta ─────────────────────────────────────────────
    margen_zona = 8.0 / factor if factor != 1.0 else 8.0
    zona_planta = bbox_de_recintos(textos, margen=margen_zona)
    if verbose:
        print(f"  Zona planta: x=[{zona_planta[0]:.1f}..{zona_planta[2]:.1f}]"
              f"  y=[{zona_planta[1]:.1f}..{zona_planta[3]:.1f}]")

    # ── Líneas de paredes ─────────────────────────────────────────────
    lineas = extraer_lineas_paredes(mspace, zona=zona_planta)
    if factor != 1.0:
        for ln in lineas:
            ln["x1"] *= factor; ln["y1"] *= factor
            ln["x2"] *= factor; ln["y2"] *= factor
    if verbose:
        print(f"  Lineas de paredes (en zona): {len(lineas)}")

    if not lineas:
        print("  [ERR] Sin líneas de paredes en zona — saltando")
        return 0

    # ── Separar niveles ───────────────────────────────────────────────
    niveles = separar_niveles(textos)
    if verbose:
        print(f"  Niveles detectados: {len(niveles)}")

    total_importados = 0

    for i, textos_nivel in enumerate(niveles, start=1):
        print(f"\n{'='*55}")
        print(f"  NIVEL {i}  ({len(textos_nivel)} recintos)")
        print(f"{'='*55}")

        recintos = asignar_geometria_a_textos(textos_nivel, lineas, escala=factor)

        print(f"\n  Recintos clasificados:")
        for r in recintos:
            tipo = detectar_tipo(r["nombre"])
            est  = " [estimado]" if r.get("estimado") else ""
            print(f"    {r['nombre']:<28} {r['ancho']:.1f}x{r['alto']:.1f}m"
                  f"  ({tipo}){est}")

        grafo    = construir_grafo(recintos)
        resultado = analizar_reglas_merlos(grafo)
        print(f"\n  Score: {resultado.score_total}/100  "
              f"(orient={resultado.score_orientacion} "
              f"zon={resultado.score_zonificacion} "
              f"prop={resultado.score_proporciones} "
              f"coch={resultado.score_cochera})")

        plan = {
            "origen":  "dwg_merlos",
            "archivo": doc.Name,
            "nivel":   i,
            "grid":    {"ancho_m": grafo.ancho_m, "alto_m": grafo.alto_m},
            "recintos": [
                {"nombre": n.nombre, "tipo": n.tipo, "zona": n.zona,
                 "fila": n.fila, "col": n.col, "ancho": n.ancho, "alto": n.alto}
                for n in grafo.nodos.values()
            ],
            "adyacencias": [
                {"de": ar.nodo_a, "hacia": ar.nodo_b, "tipo": ar.tipo}
                for ar in grafo.aristas
            ],
            "scores": {
                "total": resultado.score_total,
                "orientacion": resultado.score_orientacion,
                "zonificacion": resultado.score_zonificacion,
                "proporciones": resultado.score_proporciones,
                "cochera": resultado.score_cochera,
            }
        }

        pid = memoria.guardar_proyecto(
            plan            = plan,
            prompt_original = f"DWG {doc.Name} — Nivel {i}",
            score           = resultado.score_total,
            aprobado        = True,
        )
        print(f"  [OK] Importado — id={pid}")
        total_importados += 1

    return total_importados


def importar_dwg(ruta: Optional[str] = None, usar_activo: bool = True):
    """
    Importa un DWG al SLE (modo interactivo — DWG activo en AutoCAD).
    """
    import win32com.client as win32

    try:
        acad = win32.Dispatch("AutoCAD.Application")
        print(f"[OK] AutoCAD: {acad.Name}")
    except Exception as e:
        print(f"[ERR] AutoCAD no disponible: {e}")
        return

    if ruta and not usar_activo:
        doc = acad.Documents.Open(str(ruta))
        import time; time.sleep(2)
    else:
        doc = acad.ActiveDocument

    memoria = Memoria()
    total_importados = importar_dwg_doc(doc, memoria, verbose=True)

    stats = memoria.estadisticas()
    print(f"\n{'='*55}")
    print(f"  Niveles importados: {total_importados}")
    print(f"  SLE total plantas:  {stats.get('n_aprobados', '?')}")
    print(f"  SLE score promedio: {stats.get('score_promedio', '?')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dwg",    help="Ruta al DWG (opcional, usa activo si no se da)")
    parser.add_argument("--activo", action="store_true", default=True,
                        help="Usar el DWG activo en AutoCAD (por defecto)")
    args = parser.parse_args()
    importar_dwg(ruta=args.dwg, usar_activo=True)


if __name__ == "__main__":
    main()
