"""
sle/data/importar_dwg_prefab.py
================================
Importador de DWGs prefabricados sin etiquetas de texto.

Estrategia:
  1. Abre cada DWG en AutoCAD via COM (pywin32)
  2. Extrae polilíneas CERRADAS de capa 'paredes'
  3. Clasifica cada polígono por área y proporción
  4. Construye grafo espacial + importa al SLE

Uso:
    python importar_dwg_prefab.py
    python importar_dwg_prefab.py --carpeta "C:/ruta/custom" --capa WALLS
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

# ─── Rutas ───────────────────────────────────────────────────────────
CARPETA_DWG  = Path(r"C:\Users\jmerl\OneDrive\Documentos\distribuciones pequeñas")
CAPA_PAREDES = "paredes"   # capa de líneas de muro
CAPA_PAR     = "PAR"       # capa con polilíneas de contorno de recintos
CAPA_CERO    = "0"         # capa 0 genérica

SLE_DIR = Path(r"C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI")
sys.path.insert(0, str(SLE_DIR))

from sle.core.spatial_graph import SpatialGraph, NodoEspacial, AristaEspacial, zona_de_tipo
from sle.core.reglas_merlos import analizar_reglas_merlos as evaluar_planta
from sle.core.memory        import Memoria

# ─── Clasificación por geometría ─────────────────────────────────────

# Umbrales de área en m²
AREA_MAX_BANO_MEDIO    = 2.5   # ≤ 2.5 m² → medio_bano (visitas)
AREA_MAX_BANO_COMP     = 4.0   # 2.5–4.0 m² → bano_compartido
AREA_MAX_BANO_PRINC    = 8.0   # 4.0–8.0 m² → bano_principal
AREA_MIN_COCHERA       = 14.0  # ≥ 14 m² y muy ancha → cochera
AREA_MIN_SALA          = 10.0
AREA_MIN_DORMITORIO    = 7.0
AREA_MIN_COCINA        = 5.0


def _proporcion(ancho: float, alto: float) -> float:
    """Retorna max/min. 1.0 = cuadrado, >2.5 = muy rectangular."""
    if min(ancho, alto) == 0:
        return 999
    return max(ancho, alto) / min(ancho, alto)


def clasificar_por_geometria(area: float, ancho: float, alto: float,
                              posicion_relativa_x: float,
                              posicion_relativa_y: float) -> str:
    """
    Clasifica un recinto sin nombre usando área, proporción y posición.
    posicion_relativa_x/y: 0.0–1.0 dentro del bounding box total del plano.
    """
    prop = _proporcion(ancho, alto)
    dim_min = min(ancho, alto)
    dim_max = max(ancho, alto)

    # ── Baños (área pequeña) ──
    if area <= AREA_MAX_BANO_MEDIO:
        # Cuadrado o casi cuadrado pequeño → medio baño
        if prop <= 1.3:
            return "medio_bano"
        return "bano"

    if area <= AREA_MAX_BANO_COMP:
        # 2.5–4.0 m², proporción ~1.5 (ej: 2.2×1.4) → bano_compartido
        if prop <= 2.0:
            return "bano_compartido"
        return "bano"

    if area <= AREA_MAX_BANO_PRINC:
        # 4.0–8.0 m², cuadrado → bano_principal
        if prop <= 1.5:
            return "bano_principal"
        return "bano"

    # ── Lavandería: área pequeña/mediana, muy rectangular ──
    if 2.0 <= area <= 6.0 and prop >= 2.5:
        return "lavanderia"

    # ── Cochera: muy grande y ancha ──
    if area >= AREA_MIN_COCHERA and (dim_max / dim_min >= 1.5 or area >= 20):
        return "cochera"

    # ── Dormitorios ──
    if AREA_MIN_DORMITORIO <= area <= 25.0:
        if prop <= 2.0:
            # ¿Principal? Si es el más grande del rango y hacia el sur
            if area >= 12.0 and posicion_relativa_y >= 0.5:
                return "dormitorio_principal"
            return "dormitorio"

    # ── Sala / comedor / sala-comedor ──
    if area >= AREA_MIN_SALA:
        if prop >= 1.8:
            return "sala_comedor"
        if posicion_relativa_x <= 0.5:
            return "sala"
        return "comedor"

    # ── Cocina ──
    if AREA_MIN_COCINA <= area < AREA_MIN_SALA:
        return "cocina"

    # ── Pasillo / vestíbulo ──
    if area < AREA_MIN_COCINA and prop >= 2.0:
        return "pasillo"

    return "otro"


def refinar_clasificacion_global(recintos: list[dict]) -> list[dict]:
    """
    Segunda pasada: ajuste global usando contexto del conjunto.
    - El dormitorio más grande → dormitorio_principal
    - Si no hay sala pero hay sala_comedor, ok
    - Asignar 'vestibulo' al recinto central con más adyacencias
    """
    dorms = [r for r in recintos if r["tipo"] in ("dormitorio", "dormitorio_principal")]
    if dorms:
        # El más grande → principal
        mayor = max(dorms, key=lambda r: r["area"])
        mayor["tipo"] = "dormitorio_principal"
        # El resto → dormitorio
        for d in dorms:
            if d is not mayor and d["tipo"] == "dormitorio_principal":
                d["tipo"] = "dormitorio"

    # Si hay más de 1 "sala_comedor", el más pequeño → comedor
    salas = [r for r in recintos if r["tipo"] == "sala_comedor"]
    if len(salas) > 1:
        salas_sorted = sorted(salas, key=lambda r: r["area"])
        salas_sorted[0]["tipo"] = "comedor"

    return recintos


# ─── Extracción desde AutoCAD ─────────────────────────────────────────

def _puntos_a_bbox(puntos: list[tuple[float,float]]) -> tuple[float,float,float,float]:
    """Retorna (x_min, y_min, x_max, y_max)."""
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    return min(xs), min(ys), max(xs), max(ys)


def _area_poligono(puntos: list[tuple[float,float]]) -> float:
    """Área por fórmula de Shoelace."""
    n = len(puntos)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += puntos[i][0] * puntos[j][1]
        area -= puntos[j][0] * puntos[i][1]
    return abs(area) / 2.0


def _coords_a_puntos(coords_raw) -> list[tuple[float,float]]:
    """Convierte array plano de coordenadas a lista de (x,y)."""
    coords = list(coords_raw)
    pts = []
    # Detectar si es XY o XYZ
    if len(coords) >= 6 and len(coords) % 3 == 0 and len(coords) % 2 != 0:
        for i in range(0, len(coords), 3):
            pts.append((coords[i], coords[i+1]))
    else:
        for i in range(0, len(coords), 2):
            pts.append((coords[i], coords[i+1]))
    return pts


def _bbox_entidad(ent) -> tuple[float,float,float,float] | None:
    """Obtiene bbox de cualquier entidad via GetBoundingBox."""
    try:
        min_pt, max_pt = ent.GetBoundingBox()
        return (min_pt[0], min_pt[1], max_pt[0], max_pt[1])
    except Exception:
        return None


POLY_TYPES = ("AcDbPolyline", "AcDb2dPolyline", "AcDbLwPolyline", "AcDb3dPolyline")


def _es_polilinea_cerrada(ent, tolerancia: float = 0.5) -> bool:
    """
    Verifica cierre: flag Closed=True O primer/último punto a < tolerancia.
    Necesario porque polilíneas dibujadas manualmente a menudo no tienen Closed=True.
    """
    try:
        if ent.Closed:
            return True
    except Exception:
        pass
    try:
        pts = _coords_a_puntos(ent.Coordinates)
        if len(pts) < 3:
            return False
        dx = abs(pts[0][0] - pts[-1][0])
        dy = abs(pts[0][1] - pts[-1][1])
        return (dx < tolerancia and dy < tolerancia)
    except Exception:
        return False


def _poly_a_recinto(ent, fuente: str) -> dict | None:
    """Convierte una polilínea a dict de recinto. None si inválido."""
    try:
        pts = _coords_a_puntos(ent.Coordinates)
        if len(pts) < 3:
            return None
        area_raw = _area_poligono(pts)
        if area_raw < 0.5:
            return None
        bbox = _puntos_a_bbox(pts)
        x0, y0, x1, y1 = bbox
        return {
            "area":   area_raw,
            "ancho":  abs(x1 - x0),
            "alto":   abs(y1 - y0),
            "cx":     (x0 + x1) / 2,
            "cy":     (y0 + y1) / 2,
            "bbox":   bbox,
            "fuente": fuente,
        }
    except Exception:
        return None


def extraer_recintos_acad(doc) -> list[dict]:
    """
    Extrae recintos del DWG usando múltiples estrategias.
    Retorna la fuente que dé MÁS recintos válidos.

    Orden de preferencia:
      A. Polilíneas en 'PAR'  (contornos de recintos, 17 en ZITRO)
      B. Hatches en 'paredes' (área exacta, pero pueden estar explotados)
      C. Polilíneas en '0'
      D. Todas las polilíneas (cualquier capa)
    """
    mspace = doc.ModelSpace
    candidatos: dict[str, list[dict]] = {
        "PAR": [], "hatch_paredes": [], "capa_0": [], "todas": []
    }

    for ent in mspace:
        try:
            obj = ent.ObjectName
            layer = ent.Layer

            # ── Hatches ──────────────────────────────────────────────
            if obj == "AcDbHatch" and layer.lower() == "paredes":
                try:
                    area_raw = float(ent.Area)
                    if area_raw < 0.5:
                        continue
                    bbox = _bbox_entidad(ent)
                    if bbox is None:
                        continue
                    x0, y0, x1, y1 = bbox
                    candidatos["hatch_paredes"].append({
                        "area":   area_raw,
                        "ancho":  abs(x1 - x0),
                        "alto":   abs(y1 - y0),
                        "cx":     (x0 + x1) / 2,
                        "cy":     (y0 + y1) / 2,
                        "bbox":   bbox,
                        "fuente": "hatch_paredes",
                    })
                except Exception:
                    pass
                continue

            # ── Polilíneas ───────────────────────────────────────────
            if obj not in POLY_TYPES:
                continue
            if not _es_polilinea_cerrada(ent):
                continue

            rec = _poly_a_recinto(ent, f"poly_{layer}")
            if rec is None:
                continue

            if layer.upper() == "PAR":
                candidatos["PAR"].append(rec)
            elif layer == "0":
                candidatos["capa_0"].append(rec)
            candidatos["todas"].append(rec)

        except Exception:
            continue

    # ── Imprimir conteos ─────────────────────────────────────────────
    for fuente, lista in candidatos.items():
        if lista:
            print(f"    [{fuente}]: {len(lista)} recintos candidatos")

    # ── Elegir la fuente con más recintos ────────────────────────────
    orden = ["PAR", "hatch_paredes", "capa_0", "todas"]
    mejor = []
    mejor_nombre = ""
    for nombre in orden:
        lista = candidatos[nombre]
        if len(lista) > len(mejor):
            mejor = lista
            mejor_nombre = nombre

    if mejor:
        print(f"    >> Usando fuente '{mejor_nombre}' ({len(mejor)} recintos)")
    else:
        print("    [ERR] Ninguna estrategia encontro recintos")

    return mejor


def detectar_escala(polilineas: list[dict]) -> float:
    """
    Detecta si el DWG está en mm (valores > 1000) o en metros.
    Retorna factor de conversión a metros.
    """
    if not polilineas:
        return 1.0
    areas = [p["area"] for p in polilineas]
    area_max = max(areas)
    # Si área máxima > 10000 → probablemente mm²
    if area_max > 10_000:
        return 0.001   # mm → m
    elif area_max > 100:
        return 0.01    # cm → m
    return 1.0


def normalizar_a_metros(polilineas: list[dict], factor: float) -> list[dict]:
    """Aplica factor de escala a todas las dimensiones."""
    for p in polilineas:
        p["area"]  = p["area"]  * (factor ** 2)
        p["ancho"] = p["ancho"] * factor
        p["alto"]  = p["alto"]  * factor
        p["cx"]    = p["cx"]    * factor
        p["cy"]    = p["cy"]    * factor
        p["bbox"]  = tuple(v * factor for v in p["bbox"])
    return polilineas


# ─── Construcción del grafo ───────────────────────────────────────────

def _bboxes_son_adyacentes(b1, b2, tolerancia: float = 0.3) -> bool:
    """Dos bounding boxes son adyacentes si se tocan o superponen ligeramente."""
    x0a, y0a, x1a, y1a = b1
    x0b, y0b, x1b, y1b = b2
    overlap_x = x0a <= x1b + tolerancia and x0b <= x1a + tolerancia
    overlap_y = y0a <= y1b + tolerancia and y0b <= y1a + tolerancia
    return overlap_x and overlap_y


def construir_grafo(recintos: list[dict],
                    ancho_m: float = 0,
                    alto_m: float = 0) -> SpatialGraph:
    """Construye SpatialGraph a partir de la lista de recintos clasificados."""
    grafo = SpatialGraph(ancho_m=ancho_m, alto_m=alto_m)

    # Bounding box global para calcular posición relativa
    all_cx = [r["cx"] for r in recintos]
    all_cy = [r["cy"] for r in recintos]
    if not all_cx:
        return grafo

    cx_min, cx_max = min(all_cx), max(all_cx)
    cy_min, cy_max = min(all_cy), max(all_cy)
    rng_x = cx_max - cx_min or 1
    rng_y = cy_max - cy_min or 1

    nodos = []
    for i, r in enumerate(recintos):
        # Posición relativa 0–1
        rx = (r["cx"] - cx_min) / rng_x
        ry = (r["cy"] - cy_min) / rng_y

        tipo = clasificar_por_geometria(
            area=r["area"],
            ancho=r["ancho"],
            alto=r["alto"],
            posicion_relativa_x=rx,
            posicion_relativa_y=ry,
        )
        r["tipo"] = tipo
        r["pos_rel_x"] = rx
        r["pos_rel_y"] = ry

        # Fila/col en grid aproximado (1 celda ≈ 1 m)
        nodo = NodoEspacial(
            nombre=f"{tipo.upper()}_{i+1}",
            tipo=tipo,
            zona=zona_de_tipo(tipo),
            fila=round(ry * 10, 1),
            col=round(rx * 10, 1),
            ancho=round(r["ancho"], 2),
            alto=round(r["alto"], 2),
        )
        grafo.agregar_nodo(nodo)
        nodos.append((nodo.nombre, r["bbox"]))

    # Segunda pasada: refinar tipos globalmente
    recintos_info = [{"tipo": r["tipo"], "area": r["area"]} for r in recintos]
    recintos_info = refinar_clasificacion_global(recintos_info)
    for i, (nombre, _) in enumerate(nodos):
        nodo = grafo.nodos[nombre]
        nodo.tipo = recintos_info[i]["tipo"]
        # Actualizar nombre también
        nuevo_nombre = f"{recintos_info[i]['tipo'].upper()}_{i+1}"
        if nuevo_nombre != nombre:
            grafo.nodos[nuevo_nombre] = nodo
            grafo.nodos[nuevo_nombre].nombre = nuevo_nombre
            del grafo.nodos[nombre]
            nodos[i] = (nuevo_nombre, nodos[i][1])

    # Adyacencias
    nombres_lista = list(grafo.nodos.keys())
    bboxes = {n: b for n, b in nodos if n in grafo.nodos}

    for i in range(len(nombres_lista)):
        for j in range(i + 1, len(nombres_lista)):
            na = nombres_lista[i]
            nb = nombres_lista[j]
            if na not in bboxes or nb not in bboxes:
                continue
            if _bboxes_son_adyacentes(bboxes[na], bboxes[nb]):
                grafo.agregar_arista(na, nb, tipo_relacion="adyacente")

    return grafo


# ─── Proceso principal ────────────────────────────────────────────────

def procesar_dwg(acad, ruta_dwg: Path, memoria: Memoria) -> bool:
    """Procesa un DWG y lo importa al SLE. Retorna True si tuvo éxito."""
    nombre = ruta_dwg.stem
    print(f"\n{'='*55}")
    print(f"  Procesando: {nombre}")
    print(f"{'='*55}")

    try:
        # Abrir DWG
        acad.ActiveDocument  # verifica conexión
        doc = acad.Documents.Open(str(ruta_dwg))
        time.sleep(2)  # esperar que cargue

        # Extraer recintos (multicapa: hatch → PAR → 0 → todo)
        polilineas = extraer_recintos_acad(doc)
        print(f"  Recintos brutos encontrados: {len(polilineas)}")

        if len(polilineas) < 2:
            print(f"  [ERR] Muy pocos recintos — DWG sin hatches ni polilíneas cerradas")
            doc.Close(False)
            return False

        # Escala
        factor = detectar_escala(polilineas)
        if factor != 1.0:
            escala_str = {0.001: "mm->m", 0.01: "cm->m"}.get(factor, str(factor))
            print(f"  Escala detectada: {escala_str}")
            polilineas = normalizar_a_metros(polilineas, factor)

        # Filtrar ruido (área < 1 m²)
        polilineas = [p for p in polilineas if p["area"] >= 1.0]
        print(f"  Recintos >= 1m2: {len(polilineas)}")

        if len(polilineas) < 2:
            print(f"  [ERR] Insuficientes recintos validos")
            doc.Close(False)
            return False

        # Calcular bounding box total para grafo
        all_cx = [r["cx"] for r in polilineas]
        all_cy = [r["cy"] for r in polilineas]
        ancho_total = (max(r["bbox"][2] for r in polilineas) -
                       min(r["bbox"][0] for r in polilineas))
        alto_total  = (max(r["bbox"][3] for r in polilineas) -
                       min(r["bbox"][1] for r in polilineas))

        id_planta = f"prefab_{nombre}_{uuid.uuid4().hex[:6]}"
        grafo = construir_grafo(polilineas, ancho_total, alto_total)

        # Mostrar recintos detectados
        print(f"\n  Recintos clasificados ({len(grafo.nodos)}):")
        for nomb, nodo in grafo.nodos.items():
            area_aprox = nodo.ancho * nodo.alto
            print(f"    {nodo.tipo:<22} {nodo.ancho:.1f}x{nodo.alto:.1f}m = {area_aprox:.1f}m2")

        # Evaluar con reglas Merlos
        resultado = evaluar_planta(grafo)
        print(f"\n  Score total: {resultado.score_total}/100")
        print(f"    Orientacion:   {resultado.score_orientacion}")
        print(f"    Zonificacion:  {resultado.score_zonificacion}")
        print(f"    Proporciones:  {resultado.score_proporciones}")
        print(f"    Cochera:       {resultado.score_cochera}")

        # Importar al SLE (umbral bajo para prefabs, sin reglas estrictas de Merlos)
        UMBRAL_PREFAB = 30   # solo filtrar basura
        if resultado.score_total < UMBRAL_PREFAB:
            print(f"  [WARN] Score muy bajo ({resultado.score_total}) — importando igual (prefab)")

        plan = {
            "origen":  "prefab_dwg",
            "archivo": ruta_dwg.name,
            "grid": {
                "ancho_m": round(ancho_total, 1),
                "alto_m":  round(alto_total, 1),
            },
            "recintos": [
                {
                    "nombre": n.nombre,
                    "tipo":   n.tipo,
                    "fila":   n.fila,
                    "col":    n.col,
                    "ancho":  n.ancho,
                    "alto":   n.alto,
                }
                for n in grafo.nodos.values()
            ],
            "adyacencias": [
                {"de": ar.nodo_a, "hacia": ar.nodo_b, "tipo": ar.tipo}
                for ar in grafo.aristas
            ],
            "scores": {
                "total":        resultado.score_total,
                "orientacion":  resultado.score_orientacion,
                "zonificacion": resultado.score_zonificacion,
                "proporciones": resultado.score_proporciones,
                "cochera":      resultado.score_cochera,
            }
        }

        pid = memoria.guardar_proyecto(
            plan            = plan,
            prompt_original = f"DWG prefabricado: {ruta_dwg.name}",
            score           = resultado.score_total,
            aprobado        = True,
        )
        print(f"  [OK] Importado al SLE — id={pid}  score={resultado.score_total}")

        doc.Close(False)
        return True

    except Exception as e:
        print(f"  [ERR] {e}")
        try:
            acad.ActiveDocument.Close(False)
        except Exception:
            pass
        return False


def main():
    parser = argparse.ArgumentParser(description="Importar DWGs prefabricados al SLE")
    parser.add_argument("--carpeta", default=str(CARPETA_DWG))
    parser.add_argument("--capa",    default=CAPA_PAREDES)
    parser.add_argument("--solo",    help="Solo procesar este archivo (ej: ZITRO.dwg)")
    args = parser.parse_args()

    carpeta = Path(args.carpeta)
    dwgs = sorted(carpeta.glob("*.dwg"))

    if args.solo:
        dwgs = [d for d in dwgs if d.name.lower() == args.solo.lower()]

    if not dwgs:
        print("[ERR] No se encontraron DWGs en", carpeta)
        sys.exit(1)

    print(f"DWGs a procesar: {len(dwgs)}")
    for d in dwgs:
        print(f"  - {d.name}")

    # Conectar a AutoCAD
    try:
        import win32com.client as win32
        acad = win32.Dispatch("AutoCAD.Application")
        acad.Visible = True
        print(f"\n[OK] AutoCAD conectado: {acad.Name}")
    except Exception as e:
        print(f"[ERR] No se pudo conectar a AutoCAD: {e}")
        print("      Asegurese de que AutoCAD este abierto.")
        sys.exit(1)

    # Conectar al SLE
    memoria = Memoria()

    total   = len(dwgs)
    ok      = 0
    fallido = []

    for dwg in dwgs:
        exito = procesar_dwg(acad, dwg, memoria)
        if exito:
            ok += 1
        else:
            fallido.append(dwg.name)

    # Resumen
    print(f"\n{'='*55}")
    print(f"  RESUMEN IMPORTACION")
    print(f"{'='*55}")
    print(f"  Procesados: {total}")
    print(f"  Importados: {ok}  [OK]")
    print(f"  Fallidos:   {len(fallido)}")
    for f in fallido:
        print(f"    - {f}")

    stats = memoria.estadisticas()
    print(f"\n  SLE total plantas: {stats.get('total_plantas', '?')}")
    print(f"  SLE score promedio: {stats.get('score_promedio', '?')}")


if __name__ == "__main__":
    main()
