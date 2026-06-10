"""
dwg_to_sle.py
=============
Extractor DWG → JSON del SLE para Estudio Merlos AI.

Lee el plano activo en AutoCAD, detecta recintos usando BPOLY sobre
las etiquetas de texto, y genera el JSON estándar del SLE.

REQUISITOS:
  - AutoCAD abierto con el plano a convertir
  - Capa A-TEXTO con etiquetas centradas en cada recinto
  - Capa A-MURO con muros perimetrales
  - Capa con líneas de división de espacios (auto-detectada)
  - pywin32 instalado (ya lo tiene la app)

USO:
  python dwg_to_sle.py
  → Abre GUI, lee capas en vivo, extrae recintos, genera JSON
"""

import json
import math
import os
import sys
import time
import tempfile
import threading
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# ─── Colores UI ─────────────────────────────────────────────────────
ACCENT  = "#7C3AED"
SUCCESS = "#16A34A"
WARNING = "#EAB308"
ERROR   = "#DC2626"
BG      = "#0F172A"
BG_PAN  = "#1E293B"
BG_CARD = "#334155"
TEXT    = "#F8FAFC"
TEXT2   = "#94A3B8"
BORDER  = "#475569"


# ─── Conexión AutoCAD ────────────────────────────────────────────────

def conectar_autocad():
    """Retorna (doc, None) o (None, mensaje_error)."""
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        acad = win32com.client.GetActiveObject("AutoCAD.Application")
        doc  = acad.ActiveDocument
        return doc, None
    except ImportError:
        return None, "pywin32 no instalado. Corre: pip install pywin32"
    except Exception as e:
        return None, f"AutoCAD no encontrado: {e}"


def listar_capas(doc) -> list[dict]:
    """Lista todas las capas del documento."""
    capas = []
    for i in range(doc.Layers.Count):
        lay = doc.Layers.Item(i)
        capas.append({
            "nombre": lay.Name,
            "visible": lay.LayerOn,
            "congelada": lay.Freeze,
        })
    return capas


def diagnosticar(doc, log_fn=None) -> str:
    """
    Escanea el dibujo y reporta:
    - Entidades en ModelSpace (tipo + capa)
    - Bloques disponibles y sus entidades de texto
    Útil para depurar cuando no se encuentran textos.
    """
    log = log_fn or print
    lineas = []

    def out(msg):
        log(msg)
        lineas.append(msg)

    out("=== DIAGNÓSTICO DEL DIBUJO ===\n")

    # 1. ModelSpace
    ms = doc.ModelSpace
    out(f"ModelSpace: {ms.Count} entidades")
    tipos_ms = {}
    inserts_ms = []
    for i in range(ms.Count):
        try:
            ent = ms.Item(i)
            t = ent.EntityName
            tipos_ms[t] = tipos_ms.get(t, 0) + 1
            if t.upper() == "ACDBBLOCKREF":
                inserts_ms.append({
                    "nombre": ent.Name,
                    "capa": ent.Layer,
                    "ip": list(ent.InsertionPoint)[:2],
                })
        except Exception:
            pass

    for tipo, cnt in sorted(tipos_ms.items()):
        out(f"  {tipo}: {cnt}")

    if inserts_ms:
        out(f"\nINSERTs (bloques/xrefs) en ModelSpace: {len(inserts_ms)}")
        for ins in inserts_ms:
            out(f"  Nombre='{ins['nombre']}'  Capa={ins['capa']}  Pos=({ins['ip'][0]:.0f},{ins['ip'][1]:.0f})")

    # 2. Todos los bloques
    out(f"\nBloques definidos: {doc.Blocks.Count}")
    for i in range(doc.Blocks.Count):
        try:
            blk = doc.Blocks.Item(i)
            textos_blk = []
            for j in range(blk.Count):
                try:
                    ent = blk.Item(j)
                    if ent.EntityName.upper() in ("ACDBTEXT", "ACDBMTEXT"):
                        textos_blk.append(f"{ent.Layer}:'{ent.TextString[:20].strip()}'")
                except Exception:
                    pass
            if textos_blk:
                out(f"  [{blk.Name}] → {len(textos_blk)} textos:")
                for t in textos_blk[:10]:
                    out(f"    {t}")
                if len(textos_blk) > 10:
                    out(f"    ... y {len(textos_blk)-10} más")
        except Exception:
            pass

    out("\n=== FIN DIAGNÓSTICO ===")
    return "\n".join(lineas)


def leer_textos(doc, capa_texto: str) -> list[dict]:
    """
    Lee textos en la capa indicada.
    Soporta capas de xrefs con formato 'XREF$0$CAPA' — busca dentro del bloque.
    """
    textos = []
    capa_up = capa_texto.upper()

    # Separar prefijo xref si aplica
    xref_nombre = None
    capa_local_up = capa_up
    if "$0$" in capa_up:
        partes = capa_up.split("$0$", 1)
        xref_nombre = partes[0]
        capa_local_up = partes[1]

    def _scan_espacio(espacio, dx=0.0, dy=0.0, capa_match=None):
        for i in range(espacio.Count):
            try:
                ent = espacio.Item(i)
                if capa_match and ent.Layer.upper() != capa_match:
                    continue
                tipo = ent.EntityName.upper()
                if tipo not in ("ACDBTEXT", "ACDBMTEXT"):
                    continue
                ip = ent.InsertionPoint
                texto = ent.TextString.strip().upper().replace(" ", "_")
                if texto:
                    textos.append({"nombre": texto, "x": ip[0] + dx, "y": ip[1] + dy})
            except Exception:
                continue

    # 1. Buscar en ModelSpace directo
    _scan_espacio(doc.ModelSpace, capa_match=capa_up)

    # 2. Si es capa xref, buscar dentro del bloque del xref
    if not textos and xref_nombre:
        ms = doc.ModelSpace
        for i in range(ms.Count):
            try:
                ent = ms.Item(i)
                if ent.EntityName.upper() != "ACDBBLOCKREF":
                    continue
                # Normalizar nombre del bloque para comparar
                blk_name_up = ent.Name.upper().replace(" ", "_").replace("-", "_")
                xref_norm   = xref_nombre.replace(" ", "_").replace("-", "_")
                if blk_name_up != xref_norm:
                    continue
                ip = ent.InsertionPoint
                dx, dy = float(ip[0]), float(ip[1])
                blk = doc.Blocks.Item(ent.Name)
                _scan_espacio(blk, dx=dx, dy=dy, capa_match=capa_local_up)
            except Exception:
                continue

    # 3. Fallback: escanear todos los bloques que no sean *Model_Space/*Paper_Space
    if not textos:
        for i in range(doc.Blocks.Count):
            try:
                blk = doc.Blocks.Item(i)
                if blk.Name.startswith("*"):
                    continue
                _scan_espacio(blk, capa_match=capa_local_up)
            except Exception:
                continue

    return textos


def congelar_capas(doc, nombres_congelar: list[str]):
    """Congela capas por nombre (case-insensitive)."""
    nombres_up = [n.upper() for n in nombres_congelar]
    for i in range(doc.Layers.Count):
        lay = doc.Layers.Item(i)
        if lay.Name.upper() in nombres_up:
            try:
                lay.Freeze = True
            except Exception:
                pass


def descongelar_todas(doc, estado_original: dict):
    """Restaura el estado de congelamiento original."""
    for i in range(doc.Layers.Count):
        lay = doc.Layers.Item(i)
        orig = estado_original.get(lay.Name)
        if orig is not None:
            try:
                lay.Freeze = orig
            except Exception:
                pass


def guardar_estado_capas(doc) -> dict:
    """Guarda el estado actual de congelamiento de cada capa."""
    estado = {}
    for i in range(doc.Layers.Count):
        lay = doc.Layers.Item(i)
        estado[lay.Name] = lay.Freeze
    return estado


def _contar_en_capa(doc, capa: str) -> int:
    """Cuenta entidades en la capa (ModelSpace). Usado para verificar BPOLY."""
    capa_up = capa.upper()
    count = 0
    try:
        espacio = doc.ModelSpace
        for i in range(espacio.Count):
            try:
                if espacio.Item(i).Layer.upper() == capa_up:
                    count += 1
            except Exception:
                pass
    except Exception:
        pass
    return count


def esperar_autocad_libre(doc, timeout: float = 120.0, log_fn=None) -> bool:
    """
    Espera hasta que AutoCAD libere el hilo COM (no esté procesando LISP/comandos).
    Prueba un COM call sencillo cada 2 segundos hasta que responda o se agote el tiempo.
    Retorna True si AutoCAD quedó libre, False si timeout.
    """
    log = log_fn or print
    intervalo = 2.0
    elapsed = 0.0
    while elapsed < timeout:
        time.sleep(intervalo)
        elapsed += intervalo
        try:
            _ = doc.ActiveLayer   # COM call mínimo — falla si AutoCAD está ocupado
            log(f"AutoCAD libre después de {elapsed:.0f}s")
            return True
        except Exception:
            log(f"AutoCAD procesando BPOLYs... {elapsed:.0f}s")
    log(f"Timeout: AutoCAD no respondió en {timeout}s")
    return False


def ejecutar_todos_bpoly(doc, textos: list[dict], capa_destino: str = "SLE_RECINTOS", log_fn=None) -> None:
    """
    Ejecuta todos los BPOLYs en UN SOLO bloque LISP (progn).
    - LISP (command) es síncrono dentro de AutoCAD → secuencial garantizado
    - Números con punto decimal → inmune al locale español
    - Espera activamente hasta que AutoCAD libere (no adivina el tiempo)
    """
    log = log_fn or print

    # Crear capa destino si no existe
    try:
        lay = doc.Layers.Item(capa_destino)
    except Exception:
        lay = doc.Layers.Add(capa_destino)

    # Construir un solo progn con todos los BPOLYs
    lineas = [
        "(progn",
        f'  (setvar "CLAYER" "{capa_destino}")',
    ]
    for t in textos:
        x, y = t["x"], t["y"]
        lineas.append(f'  (command "_-BPOLY" (list {x:.4f} {y:.4f} 0.0) "")')
    lineas.append(")")
    lisp_completo = "\n".join(lineas) + "\n"

    log(f"Enviando bloque LISP con {len(textos)} BPOLYs a AutoCAD...")
    doc.SendCommand(lisp_completo)

    # Esperar activamente — no dormimos un tiempo fijo
    log("Esperando que AutoCAD termine (hasta 2 min)...")
    esperar_autocad_libre(doc, timeout=120.0, log_fn=log)


def leer_polilineas_capa(doc, capa: str) -> list[dict]:
    """
    Lee entidades en la capa y retorna su bounding box.
    Usa GetBoundingBox() — funciona para polilíneas, regiones, splines, etc.
    No exige Closed=True porque BPOLY puede crear polilíneas abiertas igualmente válidas.
    """
    polilineas = []
    capa_up = capa.upper()
    espacio = doc.ModelSpace
    for i in range(espacio.Count):
        try:
            ent = espacio.Item(i)
            if ent.Layer.upper() != capa_up:
                continue
            # GetBoundingBox devuelve (min_point, max_point) para cualquier entidad
            min_pt, max_pt = ent.GetBoundingBox()
            x_min, y_min = float(min_pt[0]), float(min_pt[1])
            x_max, y_max = float(max_pt[0]), float(max_pt[1])
            area = (x_max - x_min) * (y_max - y_min)
            if area < 0.01:          # descartar entidades degeneradas
                continue
            polilineas.append({
                "x_min": x_min, "y_min": y_min,
                "x_max": x_max, "y_max": y_max,
                "cx": (x_min + x_max) / 2,
                "cy": (y_min + y_max) / 2,
                "area": area,
                "tipo": ent.EntityName,
            })
        except Exception:
            continue
    return polilineas


def leer_lineas_capa(doc, *capas: str) -> list[dict]:
    """
    Lee todas las entidades AcDbLine de las capas indicadas.
    Retorna lista de {x1, y1, x2, y2}.
    """
    capas_up = {c.upper() for c in capas}
    lineas = []
    espacio = doc.ModelSpace
    for i in range(espacio.Count):
        try:
            ent = espacio.Item(i)
            if ent.Layer.upper() not in capas_up:
                continue
            if ent.EntityName.upper() != "ACDBLINE":
                continue
            sp = ent.StartPoint
            ep = ent.EndPoint
            lineas.append({
                "x1": float(sp[0]), "y1": float(sp[1]),
                "x2": float(ep[0]), "y2": float(ep[1]),
            })
        except Exception:
            continue
    return lineas


def bbox_desde_paredes(px: float, py: float, lineas: list[dict],
                        tol: float = 0.3, rango: float = 30.0) -> dict | None:
    """
    Proyecta rayos en 4 direcciones desde (px, py) y encuentra
    la línea de pared más cercana en cada dirección.
    Funciona con líneas horizontales y verticales (paredes rectas).
    tol: tolerancia para considerar si una línea es h o v
    rango: distancia máxima de búsqueda (en unidades DWG)
    """
    n = s = e = o = rango  # distancias hasta cada pared (inicialmente = máximo)

    for ln in lineas:
        x1, y1, x2, y2 = ln["x1"], ln["y1"], ln["x2"], ln["y2"]
        dx_ln = abs(x2 - x1)
        dy_ln = abs(y2 - y1)

        if dy_ln < tol and dx_ln > tol:
            # Línea HORIZONTAL
            y_ln = (y1 + y2) / 2
            xmin, xmax = min(x1, x2) - tol, max(x1, x2) + tol
            if not (xmin <= px <= xmax):
                continue
            dy = y_ln - py
            if 0 < dy < n:
                n = dy
            elif -s < dy < 0:
                s = -dy

        elif dx_ln < tol and dy_ln > tol:
            # Línea VERTICAL
            x_ln = (x1 + x2) / 2
            ymin, ymax = min(y1, y2) - tol, max(y1, y2) + tol
            if not (ymin <= py <= ymax):
                continue
            ddx = x_ln - px
            if 0 < ddx < e:
                e = ddx
            elif -o < ddx < 0:
                o = -ddx

    if n < rango and s < rango and e < rango and o < rango:
        return {
            "x_min": px - o,
            "y_min": py - s,
            "x_max": px + e,
            "y_max": py + n,
            "cx": px,
            "cy": py,
            "area": (o + e) * (s + n),
        }
    return None


def extraer_planta_desde_lineas(
    doc,
    capa_texto: str,
    capas_paredes: list[str],
    log_fn=None,
    escala_manual: float | None = None,
) -> dict | None:
    """
    Extrae recintos leyendo directamente las líneas de PAREDES.
    Usa motor de 8 rayos (0°,45°,90°,135°,180°,225°,270°,315°) para detectar
    polígonos reales — funciona con plantas ortogonales Y con ángulos de 45°.
    No usa BPOLY — no tiene problemas de COM deadlock.
    """
    log = log_fn or print
    log("Modo Paredes: motor 8 rayos (0/45/90/135/180/225/270/315°)...")

    # Importar motor de geometría
    try:
        from sle.core.geometria import poligono_desde_paredes, bbox_poligono
        _motor_geo = True
    except ImportError:
        log("  [aviso] sle.core.geometria no disponible — usando bbox ortogonal")
        _motor_geo = False

    textos = leer_textos(doc, capa_texto)
    log(f"Textos en {capa_texto}: {len(textos)}")
    for t in textos:
        log(f"  → {t['nombre']} en ({t['x']:.2f}, {t['y']:.2f})")

    if not textos:
        log("ERROR: No se encontraron textos.")
        return None

    lineas = leer_lineas_capa(doc, *capas_paredes)
    log(f"Líneas de paredes leídas: {len(lineas)} ({', '.join(capas_paredes)})")

    if not lineas:
        log("ERROR: No hay líneas en las capas de paredes.")
        return None

    # Determinar tolerancia y rango según escala del dibujo
    xs = [ln["x1"] for ln in lineas] + [ln["x2"] for ln in lineas]
    rango_x = max(xs) - min(xs)
    es_mm = rango_x > 1000
    tol       = 50.0     if es_mm else 0.05
    rango     = 30000.0  if es_mm else 30.0
    tol_dist  = 50.0     if es_mm else 0.5     # para detección de muros colineales
    min_dist  = 5.0      if es_mm else 0.005   # evita auto-impacto
    escala    = escala_manual or (1000.0 if es_mm else 1.0)
    log(f"Escala: {'mm' if es_mm else 'm'}  rango={rango}  tol_dist={tol_dist}")

    recintos      = []
    no_encontrados = []

    for t in textos:
        bbox   = None
        poligono_pts = None

        if _motor_geo:
            resultado = poligono_desde_paredes(
                t["x"], t["y"], lineas,
                rango=rango, min_dist=min_dist, tol_dist=tol_dist,
            )
            if resultado:
                bbox         = bbox_poligono(resultado["vertices"])
                poligono_pts = resultado["vertices"]
                n_v          = resultado["n_vertices"]
                area_m2      = round(resultado["area"] / (escala ** 2), 2)
                log(f"  {t['nombre']:20} {n_v} vértices  area={area_m2}m²")

        if bbox is None:
            # Fallback: bbox ortogonal (4 rayos) si el motor geo falla
            bbox = bbox_desde_paredes(t["x"], t["y"], lineas, tol=tol, rango=rango)
            if bbox:
                w = round((bbox["x_max"] - bbox["x_min"]) / escala, 2)
                h = round((bbox["y_max"] - bbox["y_min"]) / escala, 2)
                log(f"  {t['nombre']:20} {w}×{h}m [bbox ortogonal]")

        if bbox:
            dims = coords_a_grid(
                bbox["x_min"], bbox["y_min"],
                bbox["x_max"], bbox["y_max"],
                0.0, 0.0, escala=escala,    # origen relativo, se ajusta después
            )
            rec = {"nombre": t["nombre"], "_bbox": bbox, **dims}
            if poligono_pts:
                rec["_poligono"] = poligono_pts
            recintos.append(rec)
        else:
            no_encontrados.append(t["nombre"])
            log(f"  {t['nombre']:20} SIN PAREDES detectadas")

    if no_encontrados:
        log(f"Sin detección: {no_encontrados}")

    if not recintos:
        log("ERROR: No se encontró ningún recinto.")
        return None

    # Calcular origen global y recomputar coords relativas
    todos_x = [r["_bbox"]["x_min"] for r in recintos] + [r["_bbox"]["x_max"] for r in recintos]
    todos_y = [r["_bbox"]["y_min"] for r in recintos] + [r["_bbox"]["y_max"] for r in recintos]
    origen_x  = min(todos_x)
    origen_y  = max(todos_y)
    ancho_total = round((max(todos_x) - min(todos_x)) / escala)
    alto_total  = round((max(todos_y) - min(todos_y)) / escala)
    log(f"Grid: {ancho_total}×{alto_total} m")

    recintos_final = []
    for r in recintos:
        b    = r["_bbox"]
        dims = coords_a_grid(b["x_min"], b["y_min"], b["x_max"], b["y_max"],
                             origen_x, origen_y, escala=escala)
        rec_out = {"nombre": r["nombre"], **dims}
        # Incluir polígono real si existe (normalizado a metros relativos al origen)
        if "_poligono" in r:
            rec_out["poligono"] = [
                (round((vx - origen_x) / escala, 3),
                 round((origen_y - vy) / escala, 3))   # Y invertido: norte=0
                for vx, vy in r["_poligono"]
            ]
        recintos_final.append(rec_out)

    plan = {
        "grid": {"ancho_m": float(ancho_total), "alto_m": float(alto_total)},
        "recintos": recintos_final,
        "puertas": [
            {"tipo": "exterior", "recinto": recintos_final[0]["nombre"], "lado": "sur", "ancho": 1.1}
        ],
    }
    log(f"\nExtracción completa: {len(recintos_final)} recintos")

    # ── Análisis topológico SLE ──────────────────────────────────────
    try:
        from sle.core.spatial_graph import SpatialGraph
        g = SpatialGraph.from_json(plan)
        analisis = g.analizar()
        validacion = g.validate_topology()

        log("\n── Análisis SLE ──")
        zonas = analisis.get("zonas", {})
        log(f"  Zona pública    : {zonas.get('publica', 0)} recinto(s)")
        log(f"  Zona privada    : {zonas.get('privada', 0)} recinto(s)")
        log(f"  Circulación     : {zonas.get('circulacion', 0)} recinto(s)")

        adj = [(ar.nodo_a, ar.nodo_b) for ar in g.aristas
               if ar.tipo == "adyacente" and ar.nodo_b]
        log(f"  Adyacencias     : {len(adj)}")
        for a_n, b_n in adj:
            log(f"    {a_n} ↔ {b_n}")

        cadenas = analisis.get("cadenas_funcionales", [])
        if cadenas:
            log("  Cadenas funcionales:")
            for c in cadenas:
                log(f"    {' → '.join(c)}")

        # Enriquecer el plan con las adyacencias detectadas
        pares_existentes = {
            tuple(sorted([p.get("recinto1", ""), p.get("recinto2", "")]))
            for p in plan["puertas"] if p.get("tipo") == "entre_recintos"
        }
        for a_n, b_n in adj:
            par = tuple(sorted([a_n, b_n]))
            if par not in pares_existentes:
                plan["puertas"].append({
                    "tipo": "entre_recintos",
                    "recinto1": a_n,
                    "recinto2": b_n,
                    "ancho": 1.0,
                })
                pares_existentes.add(par)
        log(f"  Conexiones totales en plan: {len(plan['puertas'])}")

        if validacion["errores"]:
            for e in validacion["errores"]:
                log(f"  ⚠ ERROR: {e}")
        if validacion["advertencias"]:
            for w in validacion["advertencias"]:
                log(f"  ○ AVISO: {w}")
        if validacion["ok"] and not validacion["advertencias"]:
            log("  ✓ Topología válida")

        # Reglas Merlos
        try:
            from sle.core.reglas_merlos import analizar_reglas_merlos, texto_analisis_merlos
            res_merlos = analizar_reglas_merlos(g)
            log("")
            log(texto_analisis_merlos(res_merlos))
        except Exception:
            pass

    except ImportError:
        log("  (SpatialGraph no disponible — sin análisis topológico)")
    except Exception as e:
        log(f"  (Análisis topológico omitido: {e})")

    return plan


def leer_polilineas_existentes(doc, area_min_m2: float = 0.5) -> list[dict]:
    """
    Lee polilíneas ya existentes en ModelSpace (modo directo, sin BPOLY).
    Filtra por área mínima para excluir detalles pequeños.
    """
    polilineas = []
    espacio = doc.ModelSpace
    for i in range(espacio.Count):
        try:
            ent = espacio.Item(i)
            tipo = ent.EntityName.upper()
            if tipo not in ("ACDBPOLYLINE", "ACDB2DPOLYLINE"):
                continue
            min_pt, max_pt = ent.GetBoundingBox()
            x_min, y_min = float(min_pt[0]), float(min_pt[1])
            x_max, y_max = float(max_pt[0]), float(max_pt[1])
            area = (x_max - x_min) * (y_max - y_min)
            if area < area_min_m2:
                continue
            polilineas.append({
                "x_min": x_min, "y_min": y_min,
                "x_max": x_max, "y_max": y_max,
                "cx": (x_min + x_max) / 2,
                "cy": (y_min + y_max) / 2,
                "area": area,
                "tipo": ent.EntityName,
                "capa": ent.Layer,
            })
        except Exception:
            continue
    return polilineas


def extraer_planta_directo(
    doc,
    capa_texto: str,
    log_fn=None,
    escala_manual: float | None = None,
) -> dict | None:
    """
    Modo directo: lee polilíneas existentes sin usar BPOLY.
    Útil cuando los recintos ya están dibujados como polilíneas cerradas.
    """
    log = log_fn or print
    log("Modo directo: leyendo polilíneas existentes (sin BPOLY)...")

    textos = leer_textos(doc, capa_texto)
    log(f"Textos en {capa_texto}: {len(textos)}")
    for t in textos:
        log(f"  → {t['nombre']} en ({t['x']:.2f}, {t['y']:.2f})")

    if not textos:
        log("ERROR: No se encontraron textos.")
        return None

    polilineas = leer_polilineas_existentes(doc)
    log(f"Polilíneas existentes con área >= 0.5: {len(polilineas)}")

    if not polilineas:
        log("ERROR: No hay polilíneas en el dibujo.")
        return None

    # Auto-escala por tamaño típico de polilínea
    if escala_manual is not None:
        escala = escala_manual
    else:
        dims = sorted([p["x_max"] - p["x_min"] for p in polilineas])
        mediana = dims[len(dims) // 2]
        escala = 1000.0 if mediana > 50 else 1.0
        log(f"Escala auto: {'mm' if escala == 1000.0 else 'm'} (dim. típica {mediana:.2f})")

    todos_x = [p["x_min"] for p in polilineas] + [p["x_max"] for p in polilineas]
    todos_y = [p["y_min"] for p in polilineas] + [p["y_max"] for p in polilineas]
    origen_x = min(todos_x)
    origen_y = max(todos_y)
    ancho_total_m = round((max(todos_x) - min(todos_x)) / escala)
    alto_total_m  = round((max(todos_y) - min(todos_y)) / escala)
    log(f"Grid: {ancho_total_m}×{alto_total_m} m")

    recintos = []
    no_asociados = []
    margen = escala * 0.5

    for t in textos:
        pol = asociar_texto_a_polilinea(t, polilineas, margen=margen)
        if pol:
            dims = coords_a_grid(
                pol["x_min"], pol["y_min"],
                pol["x_max"], pol["y_max"],
                origen_x, origen_y, escala=escala,
            )
            recinto = {"nombre": t["nombre"], **dims}
            recintos.append(recinto)
            log(f"  {t['nombre']:20} fila={dims['fila']} col={dims['col']} "
                f"{dims['ancho']}×{dims['alto']}m  capa={pol['capa']}")
        else:
            no_asociados.append(t["nombre"])
            log(f"  {t['nombre']:20} SIN POLILÍNEA cercana")

    if no_asociados:
        log(f"Sin asociar: {no_asociados}")

    if not recintos:
        log("ERROR: No se pudo asociar ningún texto a polilínea.")
        return None

    plan = {
        "grid": {"ancho_m": float(ancho_total_m), "alto_m": float(alto_total_m)},
        "recintos": recintos,
        "puertas": [
            {"tipo": "exterior", "recinto": recintos[0]["nombre"], "lado": "sur", "ancho": 1.1}
        ],
    }
    log(f"\nExtracción directa completa: {len(recintos)} recintos")
    return plan


def asociar_texto_a_polilinea(
    texto: dict, polilineas: list[dict], margen: float = 0.5
) -> dict | None:
    """
    Encuentra la polilínea que contiene el punto de texto (con margen).
    margen: en las mismas unidades que las coords (metros o mm según escala).
    """
    tx, ty = texto["x"], texto["y"]
    mejor = None
    dist_min = float("inf")

    for p in polilineas:
        if (p["x_min"] - margen <= tx <= p["x_max"] + margen and
                p["y_min"] - margen <= ty <= p["y_max"] + margen):
            dist = math.sqrt((p["cx"] - tx) ** 2 + (p["cy"] - ty) ** 2)
            if dist < dist_min:
                dist_min = dist
                mejor = p

    return mejor


def coords_a_grid(
    x_min: float, y_min: float, x_max: float, y_max: float,
    origen_x: float, origen_y: float, escala: float = 1.0
) -> dict:
    """
    Convierte coordenadas DWG a fila/col/ancho/alto del grid del SLE.
    origen_x = X mínimo del plano (esquina oeste, col 0)
    origen_y = Y máximo del plano (esquina norte, fila 0)
    escala   = unidades DWG por metro (1.0 si m, 1000.0 si mm)
    """
    ancho_m = max(1, round((x_max - x_min) / escala))
    alto_m  = max(1, round((y_max - y_min) / escala))
    col     = max(0, round((x_min - origen_x) / escala))
    # fila 0 = norte (origen_y es el Y máximo); y_max del recinto → distancia desde norte
    fila    = max(0, round((origen_y - y_max) / escala))

    return {
        "col":   col,
        "fila":  fila,
        "ancho": ancho_m,
        "alto":  alto_m,
    }


def eliminar_entidades_capa(doc, capa: str):
    """Elimina todas las entidades en la capa indicada."""
    espacio = doc.ModelSpace
    a_borrar = []
    for i in range(espacio.Count):
        try:
            ent = espacio.Item(i)
            if ent.Layer.upper() == capa.upper():
                a_borrar.append(ent)
        except Exception:
            pass
    for ent in a_borrar:
        try:
            ent.Delete()
        except Exception:
            pass


# ─── Extractor principal ─────────────────────────────────────────────

def extraer_planta(
    doc,
    capa_texto: str,
    capas_congelar: list[str],
    log_fn=None,
    escala_manual: float | None = None,
) -> dict | None:
    """
    Flujo completo de extracción:
    1. Congela capas de mobiliario
    2. Lee textos (nombres de recintos)
    3. Corre BPOLY en cada posición de texto
    4. Lee polilíneas generadas
    5. Asocia texto → polilínea → recinto
    6. Construye JSON del SLE
    7. Restaura capas
    """
    log = log_fn or print
    CAPA_BPOLY = "SLE_RECINTOS"

    # Limpiar capa de trabajo anterior
    eliminar_entidades_capa(doc, CAPA_BPOLY)

    # 1. Guardar estado y congelar
    log("Guardando estado de capas...")
    estado_orig = guardar_estado_capas(doc)

    # Calcular capas visibles (las que NO se congelan)
    capas_a_congelar = [c for c in capas_congelar if c.upper() != capa_texto.upper()]
    todas_capas_nombres = [doc.Layers.Item(i).Name for i in range(doc.Layers.Count)]
    capas_visibles = [c for c in todas_capas_nombres if c not in capas_a_congelar]
    log(f"Capas VISIBLES durante extracción ({len(capas_visibles)}): {capas_visibles}")
    log(f"Capas CONGELADAS ({len(capas_a_congelar)}): {capas_a_congelar}")
    congelar_capas(doc, capas_a_congelar)
    try:
        doc.Regen(1)  # Regenerar para que BPOLY vea solo capas visibles
        time.sleep(0.3)
    except Exception:
        time.sleep(0.5)  # AutoCAD ocupado, esperar un poco

    # 2. Leer textos
    textos = leer_textos(doc, capa_texto)
    log(f"Textos encontrados en {capa_texto}: {len(textos)}")
    for t in textos:
        log(f"  → {t['nombre']} en ({t['x']:.2f}, {t['y']:.2f})")

    if not textos:
        descongelar_todas(doc, estado_orig)
        log("ERROR: No se encontraron textos en la capa indicada.")
        return None

    # 3. Ejecutar todos los BPOLYs en un solo bloque LISP
    ejecutar_todos_bpoly(doc, textos, CAPA_BPOLY, log_fn=log)

    # 4. Regen suave — AutoCAD ya está libre (esperar_autocad_libre lo garantizó)
    try:
        doc.Regen(1)
        time.sleep(0.3)
    except Exception:
        time.sleep(1.0)

    # 4b. Leer entidades en SLE_RECINTOS
    polilineas = leer_polilineas_capa(doc, CAPA_BPOLY)
    n_raw = _contar_en_capa(doc, CAPA_BPOLY)
    if polilineas:
        tipos = {}
        for p in polilineas:
            t = p.get("tipo", "?")
            tipos[t] = tipos.get(t, 0) + 1
        log(f"Entidades en {CAPA_BPOLY}: {n_raw} total → {len(polilineas)} con área válida  {tipos}")
    else:
        log(f"Entidades en {CAPA_BPOLY}: {n_raw} total → 0 con área válida")

    if not polilineas:
        descongelar_todas(doc, estado_orig)
        if n_raw == 0:
            log("ERROR: BPOLY no generó ninguna entidad.")
            log("Posible causa: los recintos tienen huecos en los muros.")
            log("Solución: cierra los huecos con la capa 'division de espacios'")
            log("          o usa PEDIT > Unir para cerrar polilíneas abiertas.")
        else:
            log(f"ERROR: Se crearon {n_raw} entidades pero ninguna tiene área válida.")
        return None

    # 5. Calcular origen y escala del plano
    todos_x = [p["x_min"] for p in polilineas] + [p["x_max"] for p in polilineas]
    todos_y = [p["y_min"] for p in polilineas] + [p["y_max"] for p in polilineas]
    origen_x = min(todos_x)
    origen_y = max(todos_y)   # Y máximo = fila 0 (norte)

    # Detectar o aplicar escala (unidades DWG por metro)
    if escala_manual is not None:
        escala = escala_manual
        log(f"Escala manual: {'mm' if escala == 1000.0 else 'm'} ({escala})")
    else:
        # Auto-detección: dimensiones > 50 → mm (INSUNITS=4 típico CR)
        anchos_tipicos = sorted([p["x_max"] - p["x_min"] for p in polilineas])
        dim_mediana = anchos_tipicos[len(anchos_tipicos) // 2]
        if dim_mediana > 50:
            escala = 1000.0
            log(f"Escala auto: MILIMETROS (dimensión típica {dim_mediana:.0f} mm = {dim_mediana/1000:.2f} m)")
        else:
            escala = 1.0
            log(f"Escala auto: METROS (dimensión típica {dim_mediana:.2f} m)")

    ancho_total_m = round((max(todos_x) - min(todos_x)) / escala)
    alto_total_m  = round((max(todos_y) - min(todos_y)) / escala)

    log(f"Grid detectado: {ancho_total_m}×{alto_total_m} m")
    log(f"Origen: ({origen_x:.2f}, {origen_y:.2f})")

    # 6. Asociar texto → polilínea
    recintos = []
    no_asociados = []

    for t in textos:
        margen_asoc = escala * 0.5   # 0.5m convertido a unidades DWG
        pol = asociar_texto_a_polilinea(t, polilineas, margen=margen_asoc)
        if pol:
            dims = coords_a_grid(
                pol["x_min"], pol["y_min"],
                pol["x_max"], pol["y_max"],
                origen_x, origen_y,
                escala=escala,
            )
            recinto = {"nombre": t["nombre"], **dims}
            recintos.append(recinto)
            area_m2 = dims["ancho"] * dims["alto"]
            log(f"  {t['nombre']:25} fila={dims['fila']} col={dims['col']} "
                f"{dims['ancho']}×{dims['alto']}m  ({area_m2}m²)")
        else:
            no_asociados.append(t["nombre"])
            log(f"  {t['nombre']:25} SIN POLILÍNEA — BPOLY falló aquí")

    if no_asociados:
        log(f"\nADVERTENCIA: {len(no_asociados)} recintos sin polilínea: {no_asociados}")

    # 7. Restaurar capas
    log("\nRestaurando capas originales...")
    descongelar_todas(doc, estado_orig)
    doc.Regen(1)

    if not recintos:
        log("ERROR: No se pudo extraer ningún recinto.")
        return None

    plan = {
        "grid": {"ancho_m": float(ancho_total_m), "alto_m": float(alto_total_m)},
        "recintos": recintos,
        "puertas": [
            {"tipo": "exterior", "recinto": recintos[0]["nombre"], "lado": "sur", "ancho": 1.1}
        ],
    }

    log(f"\nExtracción completa: {len(recintos)} recintos")
    return plan


# ─── Formulario Programa Arquitectónico ─────────────────────────────

def abrir_formulario_programa(parent=None, norte_lote: str | None = None):
    """
    Ventana de Programa Arquitectónico — Capa 0 del SLE.
    Standalone (--programa) o modal desde la GUI principal.

    norte_lote : orientación del norte real del lote
                 ("norte" | "sur" | "este" | "oeste").
                 Si es None lee config/settings.json; si tampoco existe → "norte".

    Layout: inputs arriba | resumen (izq) + editor dimensiones (der) | botones
    El editor permite ajustar ancho×largo por espacio.
    Al fijar un espacio, los restantes redistribuyen el área sobrante.
    """
    import customtkinter as ctk

    # ── Leer orientación desde config si no viene como arg ──────────
    if norte_lote is None:
        try:
            import json as _json
            _cfg_path = Path(__file__).parent / "config" / "settings.json"
            with open(_cfg_path, encoding="utf-8") as _f:
                norte_lote = _json.load(_f).get("norte_lote", "norte")
        except Exception:
            norte_lote = "norte"

    # ── Estado del terreno ────────────────────────────────────────────
    _terreno_data: dict = {}
    _terreno_path: list = [""]
    try:
        import json as _jt
        _cfg_path_t = Path(__file__).parent / "config" / "settings.json"
        with open(_cfg_path_t, encoding="utf-8") as _ft:
            _cfgt = _jt.load(_ft)
        _tp = _cfgt.get("terreno_json_path", "")
        if _tp and os.path.exists(_tp):
            with open(_tp, encoding="utf-8") as _ftd:
                _terreno_data.update(_jt.load(_ftd))
            _terreno_path[0] = _tp
    except Exception:
        pass

    OPCIONALES_DISP = [
        ("cochera",         "Cochera"),
        ("sala_tv",         "Sala TV"),
        ("estudio",         "Estudio / Oficina"),
        ("cuarto_servicio", "Cuarto de Servicio"),
        ("piscina",         "Piscina"),
    ]

    if parent:
        win = ctk.CTkToplevel(parent)
        win.grab_set()
    else:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        win = ctk.CTk()

    win.title("Programa Arquitectónico — Estudio Merlos")
    win.geometry("1100x860")
    win.minsize(900, 650)
    win.configure(fg_color=BG)

    # ── Título ────────────────────────────────────────────────────────
    ctk.CTkLabel(
        win, text="PROGRAMA ARQUITECTÓNICO",
        font=ctk.CTkFont(size=18, weight="bold"), text_color=ACCENT,
    ).pack(pady=(16, 2))
    ctk.CTkLabel(
        win, text="Capa 0 — Definición de áreas antes del diseño",
        font=ctk.CTkFont(size=12), text_color=TEXT2,
    ).pack(pady=(0, 6))

    # ── Banner terreno ─────────────────────────────────────────────────
    terr_banner = ctk.CTkFrame(win, fg_color=BG_CARD, corner_radius=10)
    terr_banner.pack(fill="x", padx=20, pady=(0, 6))

    var_terr_nombre = ctk.StringVar(value="Sin terreno cargado")
    var_terr_area   = ctk.StringVar(value="—")
    var_terr_huella = ctk.StringVar(value="—")

    t_r1 = ctk.CTkFrame(terr_banner, fg_color="transparent")
    t_r1.pack(fill="x", padx=14, pady=(8, 2))

    ctk.CTkLabel(t_r1, text="Lote:", font=ctk.CTkFont(size=11, weight="bold"),
                 text_color=TEXT2).pack(side="left", padx=(0, 4))
    ctk.CTkLabel(t_r1, textvariable=var_terr_nombre,
                 font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT,
                 ).pack(side="left", padx=(0, 16))
    ctk.CTkLabel(t_r1, text="Area lote:", font=ctk.CTkFont(size=11),
                 text_color=TEXT2).pack(side="left")
    ctk.CTkLabel(t_r1, textvariable=var_terr_area,
                 font=ctk.CTkFont(size=12, weight="bold"), text_color=SUCCESS,
                 ).pack(side="left", padx=(2, 16))
    ctk.CTkLabel(t_r1, text="Huella max:", font=ctk.CTkFont(size=11),
                 text_color=TEXT2).pack(side="left")
    ctk.CTkLabel(t_r1, textvariable=var_terr_huella,
                 font=ctk.CTkFont(size=12, weight="bold"), text_color=ACCENT,
                 ).pack(side="left", padx=(2, 0))
    ctk.CTkButton(t_r1, text="Cargar terreno JSON", width=160, height=26,
                  fg_color=BG_PAN, hover_color=BORDER, corner_radius=6,
                  font=ctk.CTkFont(size=11),
                  command=lambda: _cargar_terreno_ui(),
                  ).pack(side="right")

    t_r2 = ctk.CTkFrame(terr_banner, fg_color="transparent")
    t_r2.pack(fill="x", padx=14, pady=(0, 8))

    lbl_terr_retiros = ctk.CTkLabel(t_r2, text="Retiros: —",
                                     font=ctk.CTkFont(size=10), text_color=TEXT2)
    lbl_terr_retiros.pack(side="left")

    lbl_barra_prog = ctk.CTkLabel(t_r2, text="",
                                   font=ctk.CTkFont(size=11, weight="bold"),
                                   text_color=TEXT2)
    lbl_barra_prog.pack(side="right")

    # ── Panel entradas ────────────────────────────────────────────────
    inp = ctk.CTkFrame(win, fg_color=BG_PAN, corner_radius=10)
    inp.pack(fill="x", padx=20, pady=(0, 6))

    # m² + perfil
    f1 = ctk.CTkFrame(inp, fg_color="transparent")
    f1.pack(fill="x", padx=16, pady=(12, 4))
    ctk.CTkLabel(f1, text="m² totales:", font=ctk.CTkFont(size=13),
                 text_color=TEXT, width=160, anchor="w").pack(side="left")
    var_m2 = ctk.StringVar(value="80")
    ctk.CTkEntry(f1, textvariable=var_m2, width=100,
                 fg_color=BG_CARD, border_color=BORDER, text_color=TEXT,
                 font=ctk.CTkFont(size=15, weight="bold"),
                 ).pack(side="left", padx=(0, 14))
    lbl_perfil = ctk.CTkLabel(f1, text="Perfil: —",
                               font=ctk.CTkFont(size=12, weight="bold"),
                               text_color="#38BDF8", anchor="w")
    lbl_perfil.pack(side="left")

    # Dormitorios / Baños / Otros — en una sola fila compacta
    f2 = ctk.CTkFrame(inp, fg_color="transparent")
    f2.pack(fill="x", padx=16, pady=4)

    def _seg(parent, label, valores, default, var_int, color):
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12),
                     text_color=TEXT, width=130, anchor="w").pack(side="left")
        s = ctk.CTkSegmentedButton(
            parent, values=valores,
            fg_color=BG_CARD, selected_color=color,
            selected_hover_color=color, unselected_color=BG_CARD,
            text_color=TEXT, font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda v: var_int.set(int(v)),
        )
        s.set(str(default))
        s.pack(side="left", padx=(0, 18))
        return s

    var_ndorm   = ctk.IntVar(value=2)
    var_nbanios = ctk.IntVar(value=2)
    var_notros  = ctk.IntVar(value=0)

    seg  = _seg(f2, "N° dormitorios:", ["1","2","3","4"], 2, var_ndorm,   ACCENT)
    segb = _seg(f2, "N° baños:",       ["1","2","3","4"], 2, var_nbanios, "#0F766E")
    sego = _seg(f2, "Otros espacios:", ["0","1","2","3","4"], 0, var_notros, "#B45309")

    # Opcionales
    f3 = ctk.CTkFrame(inp, fg_color="transparent")
    f3.pack(fill="x", padx=16, pady=4)
    ctk.CTkLabel(f3, text="Extras:", font=ctk.CTkFont(size=13),
                 text_color=TEXT, width=160, anchor="w").pack(side="left", anchor="n")
    grd = ctk.CTkFrame(f3, fg_color="transparent")
    grd.pack(side="left")
    vars_opts = {}
    for i, (key, label) in enumerate(OPCIONALES_DISP):
        v = ctk.BooleanVar(value=(key == "cochera"))
        ctk.CTkCheckBox(grd, text=label, variable=v,
                        fg_color=ACCENT, hover_color="#6D28D9",
                        text_color=TEXT, font=ctk.CTkFont(size=12),
                        ).grid(row=i // 3, column=i % 3, sticky="w", padx=10, pady=2)
        vars_opts[key] = v

    # Circulación
    f4 = ctk.CTkFrame(inp, fg_color="transparent")
    f4.pack(fill="x", padx=16, pady=(4, 6))
    ctk.CTkLabel(f4, text="Circulación:", font=ctk.CTkFont(size=13),
                 text_color=TEXT, width=160, anchor="w").pack(side="left")
    var_circ = ctk.IntVar(value=20)
    lbl_circ = ctk.CTkLabel(f4, text="20%", width=40,
                             font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT)

    def _on_circ(v):
        n = int(float(v))
        var_circ.set(n)
        lbl_circ.configure(text=f"{n}%")

    sld = ctk.CTkSlider(f4, from_=0, to=20, number_of_steps=20,
                        fg_color=BG_CARD, progress_color=ACCENT,
                        button_color=ACCENT, width=180, command=_on_circ)
    sld.set(20)
    sld.pack(side="left", padx=(0, 6))
    lbl_circ.pack(side="left")
    ctk.CTkLabel(f4, text="  (0% sin pasillos — 20% estimado pre-diseño)",
                 font=ctk.CTkFont(size=11), text_color=TEXT2).pack(side="left")

    # ── Orientación del norte del lote ──────────────────────────────────
    f5 = ctk.CTkFrame(inp, fg_color="transparent")
    f5.pack(fill="x", padx=16, pady=(2, 12))

    ctk.CTkLabel(f5, text="Norte del lote:", font=ctk.CTkFont(size=13),
                 text_color=TEXT, width=160, anchor="w").pack(side="left")

    var_norte = ctk.StringVar(value=norte_lote)

    # Descripciones de orientación para tooltip
    _DESC_NORTE = {
        "norte": "Públicos (sala/comedor) al Norte",
        "sur":   "Públicos (sala/comedor) al Sur",
        "este":  "Públicos (sala/comedor) al Este",
        "oeste": "Públicos (sala/comedor) al Oeste",
    }

    _btns_norte: dict[str, ctk.CTkButton] = {}
    lbl_norte_desc = ctk.CTkLabel(
        f5, text="", width=240,
        font=ctk.CTkFont(size=11), text_color="#38BDF8", anchor="w",
    )

    def _sel_norte(val: str) -> None:
        var_norte.set(val)
        # Guardar en config para que el hub recuerde
        try:
            import json as _j
            _cp = Path(__file__).parent / "config" / "settings.json"
            _cfg = {}
            if _cp.exists():
                with open(_cp, encoding="utf-8") as _f:
                    _cfg = _j.load(_f)
            _cfg["norte_lote"] = val
            _cp.parent.mkdir(exist_ok=True)
            with open(_cp, "w", encoding="utf-8") as _f:
                _j.dump(_cfg, _f, indent=4, ensure_ascii=False)
        except Exception:
            pass
        lbl_norte_desc.configure(text=_DESC_NORTE.get(val, ""))
        for k, b in _btns_norte.items():
            b.configure(
                fg_color=SUCCESS if k == val else BG_CARD,
                text_color=TEXT if k == val else TEXT2,
            )

    # Rosa compacta: O N E en fila, S debajo centrado
    _CARDINALES_FILA1 = [("O", "oeste"), ("N", "norte"), ("E", "este")]
    _CARDINALES_FILA2 = [("S", "sur")]

    bruj = ctk.CTkFrame(f5, fg_color="transparent")
    bruj.pack(side="left", padx=(0, 10))

    fila_b1 = ctk.CTkFrame(bruj, fg_color="transparent")
    fila_b1.pack()
    fila_b2 = ctk.CTkFrame(bruj, fg_color="transparent")
    fila_b2.pack()

    for _letra, _val in _CARDINALES_FILA1:
        _b = ctk.CTkButton(fila_b1, text=_letra, width=28, height=28,
                           fg_color=BG_CARD, hover_color=SUCCESS,
                           text_color=TEXT2,
                           font=ctk.CTkFont(size=11, weight="bold"),
                           corner_radius=7,
                           command=lambda v=_val: _sel_norte(v))
        _b.pack(side="left", padx=2, pady=1)
        _btns_norte[_val] = _b

    for _letra, _val in _CARDINALES_FILA2:
        _b = ctk.CTkButton(fila_b2, text=_letra, width=28, height=28,
                           fg_color=BG_CARD, hover_color=SUCCESS,
                           text_color=TEXT2,
                           font=ctk.CTkFont(size=11, weight="bold"),
                           corner_radius=7,
                           command=lambda v=_val: _sel_norte(v))
        _b.pack(padx=2, pady=1)
        _btns_norte[_val] = _b

    lbl_norte_desc.pack(side="left")

    # Inicializar estado visual de los botones
    _sel_norte(norte_lote)

    # ── Botón generar ─────────────────────────────────────────────────
    btn_gen = ctk.CTkButton(
        win, text="Generar Programa", height=42,
        fg_color=SUCCESS, hover_color="#15803D",
        font=ctk.CTkFont(size=14, weight="bold"), corner_radius=8,
    )
    btn_gen.pack(pady=(4, 6))

    # ── Resultados: split izq (resumen) / der (editor dimensiones) ────
    res_outer = ctk.CTkFrame(win, fg_color=BG_PAN, corner_radius=10)
    res_outer.pack(fill="both", expand=True, padx=20, pady=(0, 6))
    res_outer.columnconfigure(0, weight=5)
    res_outer.columnconfigure(1, weight=4)
    res_outer.rowconfigure(0, weight=1)

    # Izquierda — resumen texto
    lframe = ctk.CTkFrame(res_outer, fg_color="transparent")
    lframe.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
    ctk.CTkLabel(lframe, text="RESUMEN", font=ctk.CTkFont(size=11, weight="bold"),
                 text_color=TEXT2).pack(anchor="w", pady=(0, 2))
    txt = ctk.CTkTextbox(lframe, font=ctk.CTkFont(family="Courier New", size=11),
                         fg_color=BG, text_color=TEXT, border_width=0,
                         wrap="none", activate_scrollbars=True)
    txt.pack(fill="both", expand=True)
    txt.configure(state="disabled")

    # Derecha — editor dimensiones
    rframe = ctk.CTkFrame(res_outer, fg_color=BG, corner_radius=8)
    rframe.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)

    ctk.CTkLabel(rframe, text="AJUSTAR DIMENSIONES",
                 font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT2,
                 ).pack(anchor="w", padx=10, pady=(6, 0))
    ctk.CTkLabel(rframe,
                 text="Ancho x Largo  (m)  — al cambiar uno se recalcula el area",
                 font=ctk.CTkFont(size=10), text_color=TEXT2,
                 ).pack(anchor="w", padx=10, pady=(0, 4))

    edit_scroll = ctk.CTkScrollableFrame(rframe, fg_color="transparent",
                                          scrollbar_button_color=BORDER,
                                          scrollbar_button_hover_color=ACCENT)
    edit_scroll.pack(fill="both", expand=True, padx=4, pady=(0, 4))

    lbl_total_edit = ctk.CTkLabel(rframe, text="",
                                   font=ctk.CTkFont(size=11, weight="bold"),
                                   text_color="#38BDF8")
    lbl_total_edit.pack(anchor="w", padx=10, pady=(0, 6))

    # ── Botones inferiores ────────────────────────────────────────────
    brow = ctk.CTkFrame(win, fg_color="transparent")
    brow.pack(fill="x", padx=20, pady=(0, 14))

    btn_sle = ctk.CTkButton(brow, text="Guardar en SLE", height=38,
                             fg_color=ACCENT, hover_color="#6D28D9",
                             font=ctk.CTkFont(size=13, weight="bold"),
                             corner_radius=8, state="disabled")
    btn_sle.pack(side="left", padx=(0, 8))

    btn_json_prog = ctk.CTkButton(brow, text="Exportar JSON", height=38,
                                   fg_color=BG_CARD, hover_color=BORDER,
                                   font=ctk.CTkFont(size=13), corner_radius=8,
                                   state="disabled")
    btn_json_prog.pack(side="left", padx=(0, 8))

    btn_entrenar_prog = ctk.CTkButton(brow, text="Reentrenar SLE", height=38,
                                      fg_color="#831843", hover_color="#9D174D",
                                      font=ctk.CTkFont(size=13, weight="bold"),
                                      corner_radius=8)
    btn_entrenar_prog.pack(side="left", padx=(0, 8))

    ctk.CTkButton(brow, text="Cerrar", height=38,
                  fg_color=BG_CARD, hover_color=BORDER,
                  font=ctk.CTkFont(size=13), corner_radius=8,
                  command=win.destroy).pack(side="right")

    # ── Estado ────────────────────────────────────────────────────────
    _prog_actual = {}
    # Lista de dicts por espacio: {nombre, tipo, area, ancho, largo,
    #   area_min, dim_min, fijo, var_a, var_l, lbl_area, lbl_ok}
    _filas_edit: list[dict] = []
    _timer_redistribuir = [None]

    def _actualizar_perfil(*_):
        try:
            from sle.core.programa_arquitectonico import PerfilCliente
            m2 = float(var_m2.get().replace(",", "."))
            p = PerfilCliente.desde_m2(m2)
            lbl_perfil.configure(text=f"Perfil: {p.descripcion}")
        except Exception:
            lbl_perfil.configure(text="Perfil: —")

    var_m2.trace_add("write", _actualizar_perfil)

    def _set_txt(contenido: str):
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        txt.insert("end", contenido)
        txt.configure(state="disabled")

    def _recalcular_dim(fila: dict, campo: str):
        """Cuando el usuario cambia ancho o largo de una fila."""
        try:
            a = float(fila["var_a"].get().replace(",", "."))
            l = float(fila["var_l"].get().replace(",", "."))
        except ValueError:
            # Valor no numérico — restaurar al último válido
            fila["var_a"].set(f"{fila['ancho']:.2f}")
            fila["var_l"].set(f"{fila['largo']:.2f}")
            return
        if a <= 0 or l <= 0:
            # Cero o negativo — restaurar al último válido
            fila["var_a"].set(f"{fila['ancho']:.2f}")
            fila["var_l"].set(f"{fila['largo']:.2f}")
            return
        area = round(a * l, 2)
        fila["area"]  = area
        fila["ancho"] = a
        fila["largo"] = l
        fila["fijo"]  = True
        ok = (area >= fila["area_min"] - 0.05 and
              min(a, l) >= fila["dim_min"] - 0.02)
        color = TEXT if ok else ERROR
        fila["lbl_area"].configure(text=f"{area:.1f}m²", text_color=color)
        fila["lbl_ok"].configure(
            text="OK" if ok else "!!",
            text_color=SUCCESS if ok else ERROR,
        )
        # Redistribuir con debounce 400ms
        if _timer_redistribuir[0]:
            win.after_cancel(_timer_redistribuir[0])
        _timer_redistribuir[0] = win.after(400, _redistribuir)

    def _redistribuir():
        """Redistribuye el área sobrante entre las filas NO fijas."""
        try:
            total_m2 = float(var_m2.get().replace(",", "."))
        except ValueError:
            return
        circ_pct = var_circ.get() / 100.0
        area_neta_max = total_m2 / (1 + circ_pct)

        fijos   = [f for f in _filas_edit if f["fijo"]]
        libres  = [f for f in _filas_edit if not f["fijo"]]
        area_fija = sum(f["area"] for f in fijos)
        area_libre_orig = sum(f["area_orig"] for f in libres)
        sobrante = area_neta_max - area_fija

        if area_libre_orig > 0 and libres:
            escala = sobrante / area_libre_orig
            for f in libres:
                nueva = max(f["area_min"], round(f["area_orig"] * escala, 1))
                dm = f["dim_min"]
                if dm > 0:
                    nuevo_a = max(dm, round((nueva * 0.75) ** 0.5, 2))
                    nuevo_l = round(nueva / nuevo_a, 2)
                else:
                    nuevo_a = nuevo_l = round(nueva ** 0.5, 2)
                f["area"]  = nueva
                f["ancho"] = nuevo_a
                f["largo"] = nuevo_l
                f["var_a"].set(f"{nuevo_a:.2f}")
                f["var_l"].set(f"{nuevo_l:.2f}")
                ok = (nueva >= f["area_min"] - 0.05 and
                      min(nuevo_a, nuevo_l) >= dm - 0.02)
                f["lbl_area"].configure(text=f"{nueva:.1f}m²",
                                        text_color=TEXT if ok else ERROR)
                f["lbl_ok"].configure(text="OK" if ok else "!!",
                                      text_color=SUCCESS if ok else ERROR)

        total_edit = sum(f["area"] for f in _filas_edit)
        circ_edit  = total_edit * circ_pct
        total_c    = total_edit + circ_edit
        diff       = total_c - total_m2
        color_tot  = SUCCESS if abs(diff) <= total_m2 * 0.03 else WARNING
        lbl_total_edit.configure(
            text=f"Neto: {total_edit:.1f}m²  +circ: {circ_edit:.1f}m²  "
                 f"= {total_c:.1f}m²  ({diff:+.1f}m²)",
            text_color=color_tot,
        )
        _actualizar_prog_actual()
        _actualizar_barra_huella()

    def _actualizar_prog_actual():
        """Sincroniza _prog_actual con las dimensiones del editor."""
        if not _prog_actual:
            return
        # Reconstruir lista de espacios desde _filas_edit (puede tener extras/eliminados)
        espacios_sync = []
        for fila in _filas_edit:
            var_nom  = fila.get("var_nombre", None)
            var_priv = fila.get("var_privacidad", None)
            nombre_str = var_nom.get() if var_nom else fila["nombre"]
            priv_str   = var_priv.get() if var_priv else (
                "privado" if fila["tipo"] in
                ("dormitorio_principal", "dormitorio", "bano_principal",
                 "bano_compartido", "bano_extra") else "publico"
            )
            espacios_sync.append({
                "nombre":       nombre_str,
                "tipo":         fila["tipo"],
                "privacidad":   priv_str,
                "area_m2":      round(fila["area"], 1),
                "dim_estimada": f"{fila['ancho']:.2f} x {fila['largo']:.2f}m",
                "area_min_cr":  fila["area_min"],
            })
        _prog_actual["espacios"] = espacios_sync

    def _aplicar_terreno_data():
        """Actualiza el banner con los datos de _terreno_data y pre-rellena var_m2."""
        td = _terreno_data
        if not td:
            var_terr_nombre.set("Sin terreno cargado")
            var_terr_area.set("—")
            var_terr_huella.set("—")
            lbl_terr_retiros.configure(text="Retiros: —")
            return
        var_terr_nombre.set(td.get("nombre", "Sin nombre"))
        area   = td.get("area_m2", 0)
        huella = td.get("huella_max_m2", 0)
        cob    = td.get("cobertura_max_pct", 75)
        var_terr_area.set(f"{area:,.0f} m2")
        var_terr_huella.set(f"{huella:,.0f} m2")
        r = td.get("retiros", {})
        lbl_terr_retiros.configure(
            text=(
                f"Retiros:  Frontal {r.get('frontal', '-')}m  "
                f"Posterior {r.get('posterior', '-')}m  "
                f"Lat-I {r.get('lateral_izq', '-')}m  "
                f"Lat-D {r.get('lateral_der', '-')}m  "
                f"|  Cobertura: {cob}%"
            )
        )
        # Pre-rellenar m² con la huella máxima si aún está en el default o excede
        if huella > 0:
            try:
                actual = float(var_m2.get().replace(",", "."))
            except ValueError:
                actual = 0
            if actual == 80 or actual > huella:
                var_m2.set(str(int(huella)))

    def _actualizar_barra_huella():
        """Compara el total del programa vs la huella del terreno. Colorea si supera."""
        huella = _terreno_data.get("huella_max_m2", 0)
        if huella <= 0 or not _filas_edit:
            lbl_barra_prog.configure(text="", text_color=TEXT2)
            return
        total_neto = sum(f["area"] for f in _filas_edit)
        circ_pct   = var_circ.get() / 100.0
        total_c    = total_neto * (1 + circ_pct)
        pct = total_c / huella * 100
        color = SUCCESS if pct <= 90 else (WARNING if pct <= 100 else ERROR)
        lbl_barra_prog.configure(
            text=f"Prog: {total_c:.0f} m2  /  Huella: {huella:.0f} m2  ({pct:.0f}%)",
            text_color=color,
        )

    def _cargar_terreno_ui():
        """Abre diálogo para cargar terreno JSON y actualiza el banner."""
        import json as _jc
        from tkinter import filedialog, messagebox
        # Traer la ventana al frente antes de abrir el diálogo (evita que quede detrás)
        win.lift()
        win.focus_force()
        ruta = filedialog.askopenfilename(
            parent=win,
            title="Cargar terreno del lote",
            filetypes=[("JSON terreno", "*.json"), ("Todos", "*.*")],
            initialdir=str(Path(__file__).parent),
        )
        if not ruta:
            return
        try:
            with open(ruta, encoding="utf-8") as fh:
                data = _jc.load(fh)
        except Exception as e:
            messagebox.showerror("Error al leer archivo", f"No se pudo abrir:\n{e}", parent=win)
            return
        # Verificar que es un JSON de terreno válido
        if "area_m2" not in data and "lineas" not in data:
            messagebox.showwarning(
                "Archivo incorrecto",
                "El archivo no parece un JSON de terreno.\n"
                "Guardá primero el terreno desde la herramienta 'Terreno del Lote'.",
                parent=win,
            )
            return
        _terreno_data.clear()
        _terreno_data.update(data)
        _terreno_path[0] = ruta
        # Guardar ruta en settings para próxima apertura automática
        try:
            _cfg_path3 = Path(__file__).parent / "config" / "settings.json"
            _cfg3 = {}
            if _cfg_path3.exists():
                with open(_cfg_path3, encoding="utf-8") as _f3:
                    _cfg3 = _jc.load(_f3)
            _cfg3["terreno_json_path"] = ruta
            with open(_cfg_path3, "w", encoding="utf-8") as _f3:
                _jc.dump(_cfg3, _f3, indent=4, ensure_ascii=False)
        except Exception:
            pass
        try:
            _aplicar_terreno_data()
        except Exception as e:
            messagebox.showerror("Error al aplicar terreno", str(e), parent=win)
            return
        try:
            _generar()
        except Exception as e:
            messagebox.showerror("Error al generar programa", str(e), parent=win)

    # ── Plantillas para espacios extras ──────────────────────────────
    _TPL_BANO  = {"tipo": "bano_extra",  "area_m2": 4.0,  "area_min_cr": 3.0,
                  "dim_min_cr": 1.5, "dim_estimada": "2.00 x 2.00 m"}
    _TPL_OTRO  = {"tipo": "otro",        "area_m2": 10.0, "area_min_cr": 0.0,
                  "dim_min_cr": 0.0, "dim_estimada": "3.00 x 3.33 m"}

    def _agregar_fila_editor(nombre, area, area_orig, ancho, largo,
                              area_min, dim_min, tipo, editable_nombre=False):
        """Crea UNA fila en el editor de dimensiones. Retorna el dict fila."""
        fila: dict = {
            "nombre": nombre, "tipo": tipo,
            "area": area, "area_orig": area_orig,
            "ancho": ancho, "largo": largo,
            "area_min": area_min, "dim_min": dim_min, "fijo": False,
        }
        row = ctk.CTkFrame(edit_scroll, fg_color=BG_CARD, corner_radius=6)
        row.pack(fill="x", padx=2, pady=2)
        fila["_row"] = row

        # ── Zona por defecto según tipo de espacio ────────────────────
        _ZONA_DEFAULT = {
            "sala": "publico", "comedor": "publico", "vestibulo": "publico",
            "cocina": "humedo", "lavanderia": "humedo",
            "bano_principal": "humedo", "bano_compartido": "humedo",
            "bano_extra": "humedo",
            "dormitorio_principal": "privado", "dormitorio": "privado",
        }
        _ZONA_CFG = {
            "publico": {"lbl": "PUB",  "color": "#0F766E"},
            "privado": {"lbl": "PRIV", "color": "#7C3AED"},
            "humedo":  {"lbl": "HUM",  "color": "#1D4ED8"},
        }
        _ZONA_CICLO = ["publico", "privado", "humedo"]
        zona_init = _ZONA_DEFAULT.get(tipo, "publico")
        var_zona = ctk.StringVar(value=zona_init)
        fila["var_privacidad"] = var_zona

        # Nombre: editable (Entry) para "otro", label para el resto
        if editable_nombre:
            var_nom = ctk.StringVar(value=nombre)
            fila["var_nombre"] = var_nom
            ctk.CTkEntry(row, textvariable=var_nom, width=90,
                         fg_color=BG, border_color="#B45309",
                         text_color=TEXT, font=ctk.CTkFont(size=11),
                         placeholder_text="Nombre...",
                         ).pack(side="left", padx=(4, 2))
        else:
            ctk.CTkLabel(row, text=nombre[:16], width=120, anchor="w",
                         font=ctk.CTkFont(size=11), text_color=TEXT,
                         ).pack(side="left", padx=(6, 2))

        # Toggle PUB → PRIV → HUM → PUB (todos los espacios)
        cfg0 = _ZONA_CFG[zona_init]
        btn_zona = ctk.CTkButton(
            row, text=cfg0["lbl"], width=44, height=24,
            fg_color=cfg0["color"], hover_color=cfg0["color"],
            text_color=TEXT, font=ctk.CTkFont(size=10, weight="bold"),
            corner_radius=5,
        )
        def _toggle_zona(b=btn_zona, v=var_zona,
                         ciclo=_ZONA_CICLO, cfg=_ZONA_CFG):
            idx  = ciclo.index(v.get())
            next_zona = ciclo[(idx + 1) % len(ciclo)]
            v.set(next_zona)
            c = cfg[next_zona]
            b.configure(text=c["lbl"], fg_color=c["color"],
                        hover_color=c["color"])
        btn_zona.configure(command=_toggle_zona)
        btn_zona.pack(side="left", padx=(0, 4))

        var_a = ctk.StringVar(value=f"{ancho:.2f}")
        var_l = ctk.StringVar(value=f"{largo:.2f}")
        fila["var_a"] = var_a
        fila["var_l"] = var_l

        ent_a = ctk.CTkEntry(row, textvariable=var_a, width=56,
                             fg_color=BG, border_color=BORDER,
                             text_color=TEXT, font=ctk.CTkFont(size=12))
        ent_a.pack(side="left", padx=2)
        ctk.CTkLabel(row, text="×", width=10, text_color=TEXT2,
                     font=ctk.CTkFont(size=12)).pack(side="left")
        ent_l = ctk.CTkEntry(row, textvariable=var_l, width=56,
                             fg_color=BG, border_color=BORDER,
                             text_color=TEXT, font=ctk.CTkFont(size=12))
        ent_l.pack(side="left", padx=2)

        ok_init = (area >= area_min - 0.05)
        lbl_area = ctk.CTkLabel(row, text=f"{area:.1f}m²", width=52,
                                font=ctk.CTkFont(size=11, weight="bold"),
                                text_color=TEXT if ok_init else ERROR)
        lbl_area.pack(side="left", padx=2)

        lbl_ok = ctk.CTkLabel(row, text="OK" if ok_init else "!!",
                              width=24, font=ctk.CTkFont(size=10),
                              text_color=SUCCESS if ok_init else ERROR)
        lbl_ok.pack(side="left")

        fila["lbl_area"] = lbl_area
        fila["lbl_ok"]   = lbl_ok

        # Botón ✕ eliminar
        def _eliminar(f=fila):
            if f in _filas_edit:
                _filas_edit.remove(f)
            f["_row"].destroy()
            _redistribuir()
        ctk.CTkButton(row, text="✕", width=28, height=24,
                      fg_color="#7F1D1D", hover_color=ERROR,
                      text_color=TEXT, font=ctk.CTkFont(size=10),
                      corner_radius=5, command=_eliminar,
                      ).pack(side="left", padx=(2, 4))

        _filas_edit.append(fila)

        ent_a.bind("<FocusOut>", lambda e, f=fila: _recalcular_dim(f, "a"))
        ent_a.bind("<Return>",   lambda e, f=fila: _recalcular_dim(f, "a"))
        ent_l.bind("<FocusOut>", lambda e, f=fila: _recalcular_dim(f, "l"))
        ent_l.bind("<Return>",   lambda e, f=fila: _recalcular_dim(f, "l"))

        return fila

    def _construir_editor(prog):
        """Construye las filas del panel de edición tras generar el programa."""
        for w in edit_scroll.winfo_children():
            w.destroy()
        _filas_edit.clear()

        # Cabecera
        hdr = ctk.CTkFrame(edit_scroll, fg_color="transparent")
        hdr.pack(fill="x", padx=2, pady=(0, 2))
        for txt_h, w in [("ESPACIO", 140), ("ANCHO", 58), ("", 10),
                          ("LARGO", 58), ("AREA", 52), ("", 52)]:
            ctk.CTkLabel(hdr, text=txt_h, width=w, anchor="w",
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=TEXT2).pack(side="left", padx=1)

        circ_pct = var_circ.get() / 100.0
        try:
            total_m2 = float(var_m2.get().replace(",", "."))
        except Exception:
            total_m2 = 0

        # ── Espacios generados automáticamente ───────────────────────
        n_banios_objetivo = var_nbanios.get()
        banios_generados  = 0     # cuenta baños ya incluidos en prog

        for e in prog.espacios:
            # Baños del programa: respetar el límite n_banios_objetivo
            es_bano = e.tipo in ("bano_principal", "bano_compartido", "bano_extra")
            if es_bano:
                if banios_generados >= n_banios_objetivo:
                    continue     # saltar este baño (usuario pidió menos)
                banios_generados += 1

            dim_est = e.dim_estimada
            try:
                partes = dim_est.replace("m", "").split("x")
                a0 = float(partes[0].strip())
                l0 = float(partes[1].strip())
            except Exception:
                a0 = l0 = round(e.area_m2 ** 0.5, 2)

            _agregar_fila_editor(
                nombre=e.nombre, area=e.area_m2, area_orig=e.area_m2,
                ancho=a0, largo=l0,
                area_min=e.area_min_cr, dim_min=e.dim_min_cr,
                tipo=e.tipo,
            )

        # ── Baños extras (si usuario pidió más que los generados) ────
        for i in range(banios_generados, n_banios_objetivo):
            num = i + 1
            nombre = f"Bano {num}" if num > 1 else "Bano Principal"
            try:
                partes = _TPL_BANO["dim_estimada"].replace("m","").split("x")
                a0 = float(partes[0].strip()); l0 = float(partes[1].strip())
            except Exception:
                a0 = l0 = 2.0
            _agregar_fila_editor(
                nombre=nombre, area=_TPL_BANO["area_m2"],
                area_orig=_TPL_BANO["area_m2"],
                ancho=a0, largo=l0,
                area_min=_TPL_BANO["area_min_cr"],
                dim_min=_TPL_BANO["dim_min_cr"],
                tipo="bano_extra",
            )

        # ── Otros espacios (nombre editable) ─────────────────────────
        for i in range(var_notros.get()):
            try:
                partes = _TPL_OTRO["dim_estimada"].replace("m","").split("x")
                a0 = float(partes[0].strip()); l0 = float(partes[1].strip())
            except Exception:
                a0 = l0 = 3.0
            _agregar_fila_editor(
                nombre=f"Otro {i+1}", area=_TPL_OTRO["area_m2"],
                area_orig=_TPL_OTRO["area_m2"],
                ancho=a0, largo=l0,
                area_min=0.0, dim_min=0.0,
                tipo="otro", editable_nombre=True,
            )

        # Totales iniciales
        total_edit = sum(f["area"] for f in _filas_edit)
        circ_v     = total_edit * circ_pct
        total_c    = total_edit + circ_v
        diff       = total_c - total_m2
        lbl_total_edit.configure(
            text=f"Neto: {total_edit:.1f}m²  +circ: {circ_v:.1f}m²  "
                 f"= {total_c:.1f}m²  ({diff:+.1f}m²)",
            text_color=SUCCESS if abs(diff) <= total_m2 * 0.03 else WARNING,
        )

    def _generar():
        try:
            total_m2 = float(var_m2.get().replace(",", "."))
        except ValueError:
            _set_txt("  !! Ingrese un numero valido de m2.\n")
            return

        n_dorm   = var_ndorm.get()
        opts     = [k for k, v in vars_opts.items() if v.get()]
        circ_pct = var_circ.get() / 100.0
        norte    = var_norte.get()

        try:
            from sle.core.programa_arquitectonico import generar_programa, texto_programa
            from sle.core.reglas_merlos import guia_orientacion_para_prompt
            prog = generar_programa(total_m2, n_dormitorios=n_dorm,
                                    opcionales=opts,
                                    porcentaje_circulacion=circ_pct)
        except Exception as e:
            _set_txt(f"  Error: {e}\n")
            return

        lbl_perfil.configure(text=f"Perfil: {prog.perfil.descripcion}")
        _prog_actual.clear()
        _prog_actual.update(prog.to_dict())
        _prog_actual["norte_lote"] = norte   # incluye orientación en el JSON

        # Texto del resumen: programa + guía de orientación
        _nombres_norte = {"norte": "Norte", "sur": "Sur",
                          "este": "Este", "oeste": "Oeste"}
        guia = guia_orientacion_para_prompt(norte_real=norte)
        encabezado_norte = (
            f"\n-- ORIENTACION DEL LOTE --\n"
            f"   Norte real del lote : {_nombres_norte.get(norte, norte.upper())}\n"
            f"{guia}\n"
        )
        _set_txt(texto_programa(prog) + encabezado_norte)
        _construir_editor(prog)

        ok = not prog.errores
        btn_sle.configure(state="normal" if ok else "disabled")
        btn_json_prog.configure(state="normal" if ok else "disabled")

        # ── Contexto compartido para IA del CAD Visor ─────────────────
        try:
            import json as _json
            _cfg_path2 = Path(__file__).parent / "config" / "settings.json"
            _cfg2 = {}
            if _cfg_path2.exists():
                with open(_cfg_path2, encoding="utf-8") as _f2:
                    _cfg2 = _json.load(_f2)
            _cfg2.setdefault("proyecto_activo", {})
            _cfg2["proyecto_activo"]["programa"] = {
                "total_m2":      _prog_actual.get("total_m2_pedido", total_m2),
                "area_total_m2": _prog_actual.get("area_total_m2", total_m2),
                "n_dormitorios": n_dorm,
                "perfil":        _prog_actual.get("perfil", ""),
                "norte_lote":    norte,
                "espacios": [
                    {
                        "nombre":   e["nombre"],
                        "area_m2":  e["area_m2"],
                        "cumple":   not bool(e.get("errores")),
                    }
                    for e in _prog_actual.get("espacios", [])
                ],
                "errores": _prog_actual.get("errores", []),
            }
            with open(_cfg_path2, "w", encoding="utf-8") as _f2:
                _json.dump(_cfg2, _f2, indent=4, ensure_ascii=False)
        except Exception:
            pass

    def _guardar_sle():
        if not _prog_actual:
            return
        try:
            from sle.core.memory import Memoria
            from sle.data.generador_sintetico import generar_desde_programa
            mem  = Memoria()
            total = _prog_actual.get("total_m2_pedido", 0)
            ndorm = _prog_actual.get("n_dormitorios", 0)
            perfil = _prog_actual.get("perfil", "")

            btn_sle.configure(text="Generando...", fg_color="#1D4ED8")
            win.update_idletasks()

            # Generar layout espacial real desde el programa arquitectónico
            # (antes se guardaba todo en fila=0, col=0 — ahora se distribuye)
            planta = generar_desde_programa(
                programa    = _prog_actual,
                n_candidatos = 5,
                norte_lote  = "norte",
            )
            plan = planta["plan"]
            plan["programa"] = _prog_actual  # adjuntar programa al plan
            score = planta.get("score", 85)
            prompt = planta.get("prompt", f"{ndorm} dormitorios {total}m2 clase {perfil}")

            pid = mem.guardar_proyecto(plan, prompt_original=prompt, score=score)
            btn_sle.configure(text=f"Guardado (id={pid}  score={score})",
                              fg_color="#065F46")
            win.after(4000, lambda: btn_sle.configure(
                text="Guardar en SLE", fg_color=ACCENT))
        except Exception as e:
            btn_sle.configure(text=f"Error: {str(e)[:30]}", fg_color=ERROR)

    def _exportar_json():
        if not _prog_actual:
            return
        import json
        from tkinter import filedialog
        total = _prog_actual.get("total_m2_pedido", 0)
        ndorm = _prog_actual.get("n_dormitorios", 0)
        ruta = filedialog.asksaveasfilename(
            title="Exportar programa",
            defaultextension=".json",
            initialfile=f"programa_{ndorm}dorm_{int(total)}m2.json",
            filetypes=[("JSON", "*.json")],
        )
        if ruta:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(_prog_actual, f, indent=2, ensure_ascii=False)

    def _reentrenar_prog():
        import threading

        def _tarea():
            try:
                win.after(0, lambda: btn_entrenar_prog.configure(
                    text="Entrenando...", state="disabled", fg_color="#6B21A8"))

                from sle.core.memory import Memoria
                from sle.learning.trainer import Trainer

                memoria = Memoria()
                n = memoria.estadisticas().get("n_aprobados", 0)
                win.after(0, lambda: btn_entrenar_prog.configure(
                    text=f"Entrenando ({n} plantas)..."))

                trainer = Trainer(memoria=memoria, tasa_aprendizaje=0.05)
                metricas = trainer.entrenar(n_epochs=15, max_pares=200)

                if "error" in metricas:
                    win.after(0, lambda: btn_entrenar_prog.configure(
                        text=f"Error: {metricas['error']}", state="normal",
                        fg_color="#7F1D1D"))
                    win.after(5000, lambda: btn_entrenar_prog.configure(
                        text="Reentrenar SLE", fg_color="#831843"))
                    return

                trainer.guardar_modelo()
                l_ini = metricas.get("loss_inicial", 0)
                l_fin = metricas.get("loss_final", 0)
                n_pos = metricas.get("n_positivos", 0)
                msg   = f"OK  {n_pos}p  {l_ini:.3f}→{l_fin:.3f}"

                win.after(0, lambda: btn_entrenar_prog.configure(
                    text=msg, state="normal", fg_color="#065F46"))
                win.after(5000, lambda: btn_entrenar_prog.configure(
                    text="Reentrenar SLE", fg_color="#831843"))

            except Exception as exc:
                err = str(exc)[:40]
                win.after(0, lambda: btn_entrenar_prog.configure(
                    text=f"Error: {err}", state="normal", fg_color="#7F1D1D"))
                win.after(5000, lambda: btn_entrenar_prog.configure(
                    text="Reentrenar SLE", fg_color="#831843"))

        threading.Thread(target=_tarea, daemon=True).start()

    btn_gen.configure(command=_generar)
    btn_sle.configure(command=_guardar_sle)
    btn_json_prog.configure(command=_exportar_json)
    btn_entrenar_prog.configure(command=_reentrenar_prog)

    # ── Auto-regenerar al cambiar controles ──────────────────────────
    # Debounce compartido para no regenerar en cada trazo del slider
    _timer_gen   = [None]
    _gui_lista   = [False]   # flag: evita disparos durante la construcción inicial

    def _generar_debounce(*_):
        if not _gui_lista[0]:
            return   # aún construyendo — ignorar
        if _timer_gen[0]:
            win.after_cancel(_timer_gen[0])
        _timer_gen[0] = win.after(350, _generar)

    # Selectores: dormitorios / baños / otros → regeneran el editor
    seg.configure( command=lambda v: (var_ndorm.set(int(v)),   _generar()))
    segb.configure(command=lambda v: (var_nbanios.set(int(v)), _generar()))
    sego.configure(command=lambda v: (var_notros.set(int(v)),  _generar()))

    # Checkboxes opcionales — marcar O desmarcar dispara regeneración
    for _v in vars_opts.values():
        _v.trace_add("write", _generar_debounce)

    # Slider de circulación — regenerar tras soltar (debounce 500ms)
    def _on_circ_full(v):
        _on_circ(v)
        _generar_debounce()
    sld.configure(command=_on_circ_full)

    def _arranque():
        _aplicar_terreno_data()   # carga terreno auto-detectado y pre-rellena m²
        _actualizar_perfil()
        _generar()
        _gui_lista[0] = True   # a partir de aquí los traces sí regeneran

    win.after(300, _arranque)

    if not parent:
        win.mainloop()


# ─── GUI ────────────────────────────────────────────────────────────

def abrir_gui(doc, capas: list[dict]):
    import customtkinter as ctk

    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.title("DWG → SLE Extractor — Estudio Merlos AI")
    root.geometry("860x700")
    root.configure(fg_color=BG)

    # ── Header ──────────────────────────────────────────────────
    hdr = ctk.CTkFrame(root, fg_color="transparent")
    hdr.pack(fill="x", padx=20, pady=(20, 5))

    ctk.CTkLabel(
        hdr, text="DWG → SLE Extractor",
        font=ctk.CTkFont(size=20, weight="bold"), text_color=ACCENT,
    ).pack(side="left")

    nombre_plano = doc.Name if doc else "Sin plano"
    ctk.CTkLabel(
        hdr, text=f"  ·  {nombre_plano}",
        font=ctk.CTkFont(size=13), text_color=TEXT2,
    ).pack(side="left")

    # ── Config ──────────────────────────────────────────────────
    cfg_card = ctk.CTkFrame(root, fg_color=BG_PAN, corner_radius=12)
    cfg_card.pack(fill="x", padx=20, pady=5)

    cfg_inner = ctk.CTkFrame(cfg_card, fg_color="transparent")
    cfg_inner.pack(fill="x", padx=15, pady=12)
    cfg_inner.grid_columnconfigure((1, 3), weight=1)

    # Capa de texto
    ctk.CTkLabel(
        cfg_inner, text="Capa etiquetas:",
        font=ctk.CTkFont(size=12), text_color=TEXT2,
    ).grid(row=0, column=0, sticky="w", padx=(0, 8))

    nombres_capas = [c["nombre"] for c in capas]
    # Detectar capa de texto: prioridad A-TEXTO exacto > sin xref > cualquier "texto"
    nombres_up = {c.upper(): c for c in nombres_capas}
    default_texto = (
        nombres_up.get("A-TEXTO")
        or nombres_up.get("A-TEXT")
        or nombres_up.get("TEXTO")
        or next((c for c in nombres_capas if "texto" in c.lower() and "$0$" not in c), None)
        or next((c for c in nombres_capas if "texto" in c.lower()), None)
        or (nombres_capas[0] if nombres_capas else "A-TEXTO")
    )

    combo_texto = ctk.CTkComboBox(
        cfg_inner, values=nombres_capas, width=200,
        fg_color=BG_CARD, border_color=BORDER, text_color=TEXT,
        button_color=ACCENT, dropdown_fg_color=BG_CARD, dropdown_text_color=TEXT,
        font=ctk.CTkFont(size=12),
    )
    combo_texto.set(default_texto)
    combo_texto.grid(row=0, column=1, sticky="ew", padx=(0, 20))

    # Capa división espacios
    ctk.CTkLabel(
        cfg_inner, text="División espacios:",
        font=ctk.CTkFont(size=12), text_color=TEXT2,
    ).grid(row=0, column=2, sticky="w", padx=(0, 8))

    default_div = next((c for c in nombres_capas if "division" in c.lower() or "espacios" in c.lower()), "")

    combo_div = ctk.CTkComboBox(
        cfg_inner, values=["(ninguna)"] + nombres_capas, width=200,
        fg_color=BG_CARD, border_color=BORDER, text_color=TEXT,
        button_color=ACCENT, dropdown_fg_color=BG_CARD, dropdown_text_color=TEXT,
        font=ctk.CTkFont(size=12),
    )
    combo_div.set(default_div if default_div else "(ninguna)")
    combo_div.grid(row=0, column=3, sticky="ew")

    # ── Escala ──────────────────────────────────────────────────
    cfg_row2 = ctk.CTkFrame(cfg_card, fg_color="transparent")
    cfg_row2.pack(fill="x", padx=15, pady=(0, 10))

    ctk.CTkLabel(
        cfg_row2, text="Unidades DWG:",
        font=ctk.CTkFont(size=12), text_color=TEXT2,
    ).grid(row=0, column=0, sticky="w", padx=(0, 8))

    combo_escala = ctk.CTkComboBox(
        cfg_row2, values=["Auto-detectar", "Milímetros (mm → m)", "Metros (directo)"],
        width=220,
        fg_color=BG_CARD, border_color=BORDER, text_color=TEXT,
        button_color=ACCENT, dropdown_fg_color=BG_CARD, dropdown_text_color=TEXT,
        font=ctk.CTkFont(size=12),
    )
    combo_escala.set("Auto-detectar")
    combo_escala.grid(row=0, column=1, sticky="w")

    ctk.CTkLabel(
        cfg_row2, text="  (INSUNITS=4 mm es lo normal en CR)",
        font=ctk.CTkFont(size=10), text_color=TEXT2,
    ).grid(row=0, column=2, sticky="w", padx=8)

    # ── Selección de capas a congelar ───────────────────────────
    ctk.CTkLabel(
        root, text="Capas a CONGELAR durante extracción (desmarcar las que DEBEN verse):",
        font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT2, anchor="w",
    ).pack(fill="x", padx=20, pady=(10, 2))

    # Capas que siempre deben estar visibles (nunca congelar)
    # Incluye variaciones comunes de capa de muros en CR
    SIEMPRE_VISIBLES = {
        "a-muro", "a-muros", "muro", "muros",
        "a-pared", "a-paredes", "pared", "paredes",
        "a-wall", "wall", "walls",
        "a-texto", "a-text", "texto",
        "division", "espacios",
        "0",
    }

    scroll_capas = ctk.CTkScrollableFrame(
        root, fg_color=BG_PAN, corner_radius=8, height=140,
    )
    scroll_capas.pack(fill="x", padx=20, pady=(0, 5))

    vars_capas: dict[str, ctk.BooleanVar] = {}
    for capa in capas:
        nombre = capa["nombre"]
        # Por defecto: congelar todo lo que no sea muro/texto/división
        es_muro_o_texto = any(k in nombre.lower() for k in SIEMPRE_VISIBLES)
        default_val = not es_muro_o_texto

        var = ctk.BooleanVar(value=default_val)
        vars_capas[nombre] = var

        fila = ctk.CTkFrame(scroll_capas, fg_color="transparent")
        fila.pack(fill="x", padx=5, pady=1)

        ctk.CTkCheckBox(
            fila, text=nombre, variable=var,
            font=ctk.CTkFont(size=11), text_color=TEXT if not default_val else TEXT2,
            fg_color=ACCENT, hover_color="#6D28D9", checkmark_color=TEXT,
        ).pack(side="left")

        if not capa["visible"]:
            ctk.CTkLabel(
                fila, text="(oculta)", font=ctk.CTkFont(size=10), text_color=ERROR,
            ).pack(side="left", padx=4)

    # ── Log ─────────────────────────────────────────────────────
    log_card = ctk.CTkFrame(root, fg_color=BG_PAN, corner_radius=12)
    log_card.pack(fill="both", expand=True, padx=20, pady=5)

    ctk.CTkLabel(
        log_card, text="Log",
        font=ctk.CTkFont(size=11, weight="bold"), text_color=TEXT2, anchor="w",
    ).pack(fill="x", padx=12, pady=(8, 2))

    log_txt = ctk.CTkTextbox(
        log_card, fg_color=BG_CARD, border_width=0,
        font=ctk.CTkFont(family="Consolas", size=11),
        text_color=TEXT2, wrap="word", state="disabled",
    )
    log_txt.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    plan_resultado: dict = {}

    def log(msg: str):
        def _write():
            log_txt.configure(state="normal")
            log_txt.insert("end", msg + "\n")
            log_txt.see("end")
            log_txt.configure(state="disabled")
        root.after(0, _write)

    # ── Botones ──────────────────────────────────────────────────
    btn_frame = ctk.CTkFrame(root, fg_color="transparent")
    btn_frame.pack(fill="x", padx=20, pady=(0, 15))

    btn_extraer = ctk.CTkButton(
        btn_frame, text="Extraer recintos", height=40,
        fg_color=ACCENT, hover_color="#6D28D9",
        font=ctk.CTkFont(size=14, weight="bold"), corner_radius=8,
    )
    btn_extraer.pack(side="left", padx=(0, 8))

    btn_guardar = ctk.CTkButton(
        btn_frame, text="Guardar en SLE", height=40,
        fg_color=SUCCESS, hover_color="#15803D",
        font=ctk.CTkFont(size=14, weight="bold"), corner_radius=8,
        state="disabled",
    )
    btn_guardar.pack(side="left", padx=(0, 8))

    btn_json = ctk.CTkButton(
        btn_frame, text="Exportar JSON", height=40,
        fg_color=BG_CARD, hover_color=BORDER,
        font=ctk.CTkFont(size=13), corner_radius=8,
        state="disabled",
    )
    btn_json.pack(side="left", padx=(0, 8))

    btn_programa = ctk.CTkButton(
        btn_frame, text="Programa", height=40,
        fg_color="#065F46", hover_color="#047857",
        font=ctk.CTkFont(size=13, weight="bold"), corner_radius=8,
        command=lambda: abrir_formulario_programa(parent=root),
    )
    btn_programa.pack(side="left", padx=(0, 8))

    btn_directo = ctk.CTkButton(
        btn_frame, text="Modo Directo", height=40,
        fg_color="#0369A1", hover_color="#075985",
        font=ctk.CTkFont(size=13, weight="bold"), corner_radius=8,
    )
    btn_directo.pack(side="left", padx=(0, 8))

    btn_paredes = ctk.CTkButton(
        btn_frame, text="Modo Paredes", height=40,
        fg_color="#7C3AED", hover_color="#6D28D9",
        font=ctk.CTkFont(size=13, weight="bold"), corner_radius=8,
    )
    btn_paredes.pack(side="left", padx=(0, 8))

    btn_diag = ctk.CTkButton(
        btn_frame, text="Diagnosticar", height=40,
        fg_color=WARNING, hover_color="#CA8A04", text_color="#0F172A",
        font=ctk.CTkFont(size=13, weight="bold"), corner_radius=8,
    )
    btn_diag.pack(side="left", padx=(0, 8))

    btn_entrenar = ctk.CTkButton(
        btn_frame, text="Reentrenar SLE", height=40,
        fg_color="#831843", hover_color="#9D174D",
        font=ctk.CTkFont(size=13, weight="bold"), corner_radius=8,
    )
    btn_entrenar.pack(side="left", padx=(0, 8))

    ctk.CTkButton(
        btn_frame, text="Cerrar", height=40,
        fg_color=BG_CARD, hover_color=BORDER,
        font=ctk.CTkFont(size=13), corner_radius=8,
        command=root.destroy,
    ).pack(side="right")

    # ── Lógica Reentrenar SLE ────────────────────────────────────────
    def _reentrenar_sle():
        import threading

        def _tarea():
            try:
                root.after(0, lambda: btn_entrenar.configure(
                    text="Entrenando...", state="disabled", fg_color="#6B21A8"))

                from sle.core.memory import Memoria
                from sle.learning.trainer import Trainer

                memoria = Memoria()
                stats = memoria.estadisticas()
                n = stats.get("n_aprobados", 0)

                root.after(0, lambda: btn_entrenar.configure(
                    text=f"Entrenando ({n} plantas)..."))

                trainer = Trainer(memoria=memoria, tasa_aprendizaje=0.05)
                metricas = trainer.entrenar(n_epochs=15, max_pares=200)

                if "error" in metricas:
                    msg = f"Error: {metricas['error']}"
                    root.after(0, lambda: btn_entrenar.configure(
                        text=msg, state="normal", fg_color="#7F1D1D"))
                    root.after(5000, lambda: btn_entrenar.configure(
                        text="Reentrenar SLE", fg_color="#831843"))
                    return

                trainer.guardar_modelo()

                mejora  = metricas.get("mejora", 0)
                l_ini   = metricas.get("loss_inicial", 0)
                l_fin   = metricas.get("loss_final", 0)
                n_pos   = metricas.get("n_positivos", 0)
                msg_ok  = f"OK  {n_pos} plantas  loss {l_ini:.3f}→{l_fin:.3f} ({mejora:+.3f})"

                root.after(0, lambda: btn_entrenar.configure(
                    text=msg_ok, state="normal", fg_color="#065F46"))
                root.after(6000, lambda: btn_entrenar.configure(
                    text="Reentrenar SLE", fg_color="#831843"))

            except Exception as exc:
                err = str(exc)[:40]
                root.after(0, lambda: btn_entrenar.configure(
                    text=f"Error: {err}", state="normal", fg_color="#7F1D1D"))
                root.after(5000, lambda: btn_entrenar.configure(
                    text="Reentrenar SLE", fg_color="#831843"))

        threading.Thread(target=_tarea, daemon=True).start()

    btn_entrenar.configure(command=_reentrenar_sle)

    # ── Lógica botones ───────────────────────────────────────────

    def _tarea_extraccion():
        # win32com requiere CoInitialize + reconexión propia en cada hilo.
        # NO se puede pasar el objeto COM del hilo principal.
        import pythoncom
        pythoncom.CoInitialize()

        root.after(0, lambda: btn_extraer.configure(state="disabled", text="Extrayendo..."))
        root.after(0, lambda: btn_guardar.configure(state="disabled"))
        root.after(0, lambda: btn_json.configure(state="disabled"))

        # Leer opciones de GUI (antes de cualquier COM call)
        capa_txt  = combo_texto.get()
        capa_div  = combo_div.get()
        congelar  = [n for n, v in vars_capas.items() if v.get()]

        escala_sel = combo_escala.get()
        escala_manual = None
        if "Milímetros" in escala_sel:
            escala_manual = 1000.0
        elif "Metros" in escala_sel:
            escala_manual = 1.0

        if capa_div and capa_div != "(ninguna)" and capa_div in congelar:
            congelar.remove(capa_div)

        log(f"Capa texto: {capa_txt}")
        log(f"División espacios: {capa_div if capa_div != '(ninguna)' else 'ninguna'}")
        log(f"Escala: {escala_sel}")
        log(f"Capas a congelar: {len(congelar)}\n")

        # Reconectar a AutoCAD desde este hilo
        try:
            import win32com.client
            acad_local = win32com.client.GetActiveObject("AutoCAD.Application")
            doc_local  = acad_local.ActiveDocument
            log(f"Conectado al plano: {doc_local.Name}")
        except Exception as e:
            log(f"ERROR: No se puede reconectar a AutoCAD: {e}")
            log("Asegúrate de que AutoCAD esté abierto con el plano.")
            root.after(0, lambda: btn_extraer.configure(state="normal", text="Extraer recintos"))
            return

        try:
            plan = extraer_planta(doc_local, capa_txt, congelar, log_fn=log, escala_manual=escala_manual)
            if plan:
                plan_resultado.clear()
                plan_resultado.update(plan)
                n = len(plan["recintos"])
                log(f"\nJSON generado con {n} recintos.")
                log(f"Grid: {plan['grid']['ancho_m']}x{plan['grid']['alto_m']} m")
                root.after(0, lambda: btn_guardar.configure(state="normal"))
                root.after(0, lambda: btn_json.configure(state="normal"))
            else:
                log("\nExtracción fallida. Revisa las capas y vuelve a intentar.")
        except Exception as e:
            log(f"\nError inesperado: {e}")
            import traceback
            log(traceback.format_exc())

        root.after(0, lambda: btn_extraer.configure(state="normal", text="Extraer recintos"))

    def _extraer():
        threading.Thread(target=_tarea_extraccion, daemon=True).start()

    def _guardar_en_sle():
        if not plan_resultado:
            return
        try:
            from sle.core.memory import Memoria
            mem = Memoria()
            # Usar nombre del plano capturado al inicio (doc del hilo principal está bien aquí)
            nombre_dwg = (doc.Name if doc else "plano").replace(".dwg", "").replace(".DWG", "")
            prompt = f"extraído de DWG: {nombre_dwg}"
            pid = mem.guardar_proyecto(plan_resultado, prompt_original=prompt, score=80, aprobado=True)
            log(f"\nGuardado en SLE: id={pid}  prompt='{prompt}'")
            btn_guardar.configure(text="Guardado!", fg_color="#065F46")
            root.after(3000, lambda: btn_guardar.configure(text="Guardar en SLE", fg_color=SUCCESS))
        except Exception as e:
            log(f"Error al guardar en SLE: {e}")

    def _exportar_json():
        if not plan_resultado:
            return
        from tkinter import filedialog
        nombre_dwg = (doc.Name if doc else "plano").replace(".dwg", "").replace(".DWG", "")
        ruta = filedialog.asksaveasfilename(
            title="Guardar JSON del plano",
            defaultextension=".json",
            initialfile=f"{nombre_dwg}_sle.json",
            filetypes=[("JSON", "*.json")],
        )
        if ruta:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump({
                    "prompt_original": f"extraído de DWG: {nombre_dwg}",
                    "score": 80,
                    "plan": plan_resultado,
                }, f, indent=2, ensure_ascii=False)
            log(f"JSON exportado: {ruta}")

    def _modo_directo():
        """Extrae leyendo polilíneas existentes, sin usar BPOLY."""
        def _tarea():
            import pythoncom
            pythoncom.CoInitialize()
            root.after(0, lambda: btn_directo.configure(state="disabled", text="Extrayendo..."))
            root.after(0, lambda: btn_guardar.configure(state="disabled"))
            root.after(0, lambda: btn_json.configure(state="disabled"))

            capa_txt = combo_texto.get()
            escala_sel = combo_escala.get()
            escala_manual = None
            if "Milímetros" in escala_sel:
                escala_manual = 1000.0
            elif "Metros" in escala_sel:
                escala_manual = 1.0

            try:
                import win32com.client
                acad_local = win32com.client.GetActiveObject("AutoCAD.Application")
                doc_local  = acad_local.ActiveDocument
                log(f"Modo Directo — plano: {doc_local.Name}")
                plan = extraer_planta_directo(doc_local, capa_txt, log_fn=log, escala_manual=escala_manual)
                if plan:
                    plan_resultado.clear()
                    plan_resultado.update(plan)
                    root.after(0, lambda: btn_guardar.configure(state="normal"))
                    root.after(0, lambda: btn_json.configure(state="normal"))
                    log(f"JSON listo: {len(plan['recintos'])} recintos")
                else:
                    log("Modo Directo fallido.")
            except Exception as e:
                log(f"Error: {e}")
                import traceback
                log(traceback.format_exc())
            root.after(0, lambda: btn_directo.configure(state="normal", text="Modo Directo"))
        threading.Thread(target=_tarea, daemon=True).start()

    def _diagnosticar():
        def _tarea():
            import pythoncom
            pythoncom.CoInitialize()
            try:
                import win32com.client
                acad_local = win32com.client.GetActiveObject("AutoCAD.Application")
                doc_local  = acad_local.ActiveDocument
                diagnosticar(doc_local, log_fn=log)
            except Exception as e:
                log(f"Error en diagnóstico: {e}")
        threading.Thread(target=_tarea, daemon=True).start()

    def _modo_paredes():
        """Extrae recintos usando ray-casting desde líneas de paredes. No usa BPOLY."""
        def _tarea():
            import pythoncom
            pythoncom.CoInitialize()
            root.after(0, lambda: btn_paredes.configure(state="disabled", text="Analizando..."))
            root.after(0, lambda: btn_guardar.configure(state="disabled"))
            root.after(0, lambda: btn_json.configure(state="disabled"))

            capa_txt  = combo_texto.get()
            capa_div  = combo_div.get()
            escala_sel = combo_escala.get()
            escala_manual = None
            if "Milímetros" in escala_sel:
                escala_manual = 1000.0
            elif "Metros" in escala_sel:
                escala_manual = 1.0

            # Capas de paredes: siempre incluir capas conocidas de muros/paredes + división si está configurada
            capas_paredes = list(SIEMPRE_VISIBLES)
            if capa_div and capa_div != "(ninguna)":
                capas_paredes.append(capa_div)

            log("── Modo Paredes (ray-casting) ──")
            log(f"Capa texto: {capa_txt}")
            log(f"Capas de paredes/división: {capas_paredes}")
            log(f"Escala: {escala_sel}")

            try:
                import win32com.client
                acad_local = win32com.client.GetActiveObject("AutoCAD.Application")
                doc_local  = acad_local.ActiveDocument
                log(f"Conectado al plano: {doc_local.Name}")

                plan = extraer_planta_desde_lineas(
                    doc_local,
                    capa_txt,
                    capas_paredes,
                    log_fn=log,
                    escala_manual=escala_manual,
                )
                if plan:
                    plan_resultado.clear()
                    plan_resultado.update(plan)
                    n = len(plan["recintos"])
                    log(f"\nJSON listo: {n} recintos.")
                    log(f"Grid: {plan['grid']['ancho_m']} × {plan['grid']['alto_m']} m")
                    root.after(0, lambda: btn_guardar.configure(state="normal"))
                    root.after(0, lambda: btn_json.configure(state="normal"))
                else:
                    log("\nModo Paredes no encontró recintos. Verifica las capas y etiquetas.")
            except Exception as e:
                log(f"\nError en Modo Paredes: {e}")
                import traceback
                log(traceback.format_exc())

            root.after(0, lambda: btn_paredes.configure(state="normal", text="Modo Paredes"))

        threading.Thread(target=_tarea, daemon=True).start()

    btn_extraer.configure(command=_extraer)
    btn_directo.configure(command=_modo_directo)
    btn_paredes.configure(command=_modo_paredes)
    btn_guardar.configure(command=_guardar_en_sle)
    btn_json.configure(command=_exportar_json)
    btn_diag.configure(command=_diagnosticar)

    # Log inicial
    log(f"Plano activo: {doc.Name}")
    log(f"Capas encontradas: {len(capas)}")
    log(f"Capa de texto detectada: {default_texto}")
    if default_div:
        log(f"Capa division espacios detectada: {default_div}")
    log("\nRevisa la configuracion y presiona 'Extraer recintos'.")

    root.mainloop()


# ─── Entry point ────────────────────────────────────────────────────

def main():
    print("DWG to SLE Extractor — Estudio Merlos AI")
    print("=" * 45)

    doc, error = conectar_autocad()
    if error:
        print(f"ERROR: {error}")
        print("Abre AutoCAD con el plano antes de correr este script.")
        input("Presiona Enter para salir...")
        return

    # Esperar si AutoCAD está ocupado procesando comandos previos
    for intento in range(15):
        try:
            nombre = doc.Name
            break
        except Exception:
            if intento == 0:
                print("AutoCAD ocupado, esperando que termine...")
            time.sleep(2.0)
    else:
        print("ERROR: AutoCAD no respondió. Espera a que termine de procesar y vuelve a correr.")
        input("Presiona Enter para salir...")
        return

    print(f"Conectado a AutoCAD: {nombre}")
    capas = listar_capas(doc)
    print(f"Capas encontradas: {len(capas)}")

    try:
        abrir_gui(doc, capas)
    except ImportError:
        print("customtkinter no disponible.")
        print("Instala con: pip install customtkinter")
    except Exception as e:
        print(f"\nERROR al abrir la ventana: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        input("\nPresiona Enter para salir...")


if __name__ == "__main__":
    import argparse as _argparse
    _parser = _argparse.ArgumentParser(add_help=False)
    _parser.add_argument("--programa", action="store_true")
    _parser.add_argument("--norte", default=None,
                         choices=["norte", "sur", "este", "oeste"],
                         help="Orientación del norte del lote")
    _args, _ = _parser.parse_known_args()

    if _args.programa:
        abrir_formulario_programa(norte_lote=_args.norte)
    else:
        main()
