import math
import os
import pythoncom
import win32com.client
import json
import time
from typing import Any

from modulos.diseno.grid import Grid
from modulos.diseno.validador import validar as validar_grid, detectar_tipo
from modulos.diseno.visualizador import generar_preview


HERRAMIENTAS_PLANIFICACION = {
    "crear_grid_diseno",
    "colocar_recinto_en_grid",
    "quitar_recinto_de_grid",
    "ver_grid_ascii",
    "validar_grid",
    "ver_preview_grid",
    "dibujar_desde_grid",
    "info_grid",
}


class EjecutorAutoCAD:
    def __init__(self, on_log=None):
        self.on_log = on_log or (lambda msg: None)
        self._acad = None
        self._doc = None
        self._grid: Grid = None

    def _log(self, msg: str):
        self.on_log(msg)

    def _conectar(self):
        pythoncom.CoInitialize()
        try:
            self._acad = win32com.client.GetActiveObject("AutoCAD.Application")
            self._doc = self._acad.ActiveDocument
            return True
        except Exception as e:
            self._log(f"No se pudo conectar a AutoCAD: {e}")
            return False

    def verificar(self) -> bool:
        pythoncom.CoInitialize()
        try:
            acad = win32com.client.GetActiveObject("AutoCAD.Application")
            return acad is not None
        except Exception:
            return False

    def plano_activo(self) -> str:
        pythoncom.CoInitialize()
        try:
            acad = win32com.client.GetActiveObject("AutoCAD.Application")
            return acad.ActiveDocument.Name
        except Exception:
            return ""

    def ejecutar_plan_json(self, plan: dict, on_log=None, forzar: bool = False) -> dict:
        """Ejecuta un plan JSON completo sin pasar por la IA.
        plan = {
          "grid": {"ancho_m": 10, "alto_m": 8},
          "recintos": [{"nombre":"SALA","fila":4,"col":0,"ancho":5,"alto":4}, ...],
          "puertas": [
            {"tipo":"entre_recintos","recinto1":"SALA","recinto2":"COCINA"},
            {"tipo":"exterior","recinto":"SALA","lado":"sur","ancho":1.10}
          ]
        }
        """
        log = on_log or self._log
        resultados = []

        # 1. Crear grid
        g = plan.get("grid", {})
        r = self._crear_grid_diseno({"ancho_m": g.get("ancho_m", 10), "alto_m": g.get("alto_m", 8)})
        log(f"Grid {g.get('ancho_m')}x{g.get('alto_m')}m creado")
        resultados.append(r)

        # 2. Colocar recintos (con auto-corrección si hay conflicto)
        for rec in plan.get("recintos", []):
            params = {
                "nombre": rec["nombre"],
                "fila": rec.get("fila", 0),
                "col": rec.get("col", 0),
                "ancho_celdas": rec.get("ancho", rec.get("ancho_celdas", 2)),
                "alto_celdas": rec.get("alto", rec.get("alto_celdas", 2)),
            }
            r = self._colocar_recinto_en_grid(params)
            if "error" in r and r.get("sugerencia"):
                sug = r["sugerencia"]
                log(f"Conflicto en {rec['nombre']} → moviendo a fila={sug['fila']} col={sug['col']}")
                params["fila"] = sug["fila"]
                params["col"] = sug["col"]
                r = self._colocar_recinto_en_grid(params)
            log(f"Recinto {rec['nombre']}: {'OK' if r.get('ok') else r.get('error','?')}")
            resultados.append(r)

        # 2b. Verificar conectividad — compactar recintos flotantes
        self._compactar_recintos_flotantes(log)

        # 3. Validar — con auto-corrección de dimensiones
        val = self._validar_grid({})
        if not val.get("ok"):
            errores_dim = [e for e in val["errores"] if "lado menor" in e]
            otros_errores = [e for e in val["errores"] if "lado menor" not in e]

            # Auto-corregir errores de dimensión: expandir recinto
            for err in errores_dim:
                # Formato: "NOMBRE: lado menor X.Xm < Y.Ym minimo para tipo"
                nombre_err = err.split(":")[0].strip()
                if nombre_err in self._grid.recintos:
                    info = self._grid.recintos[nombre_err]
                    nueva_ancho = max(info["ancho"] + 1, info["ancho"])
                    nueva_alto = max(info["alto"] + 1, info["alto"])
                    # Si el lado menor es ancho, expandir ancho; si es alto, expandir alto
                    if info["ancho_m"] <= info["alto_m"]:
                        nueva_ancho = info["ancho"] + 1
                    else:
                        nueva_alto = info["alto"] + 1
                    self._grid.quitar(nombre_err)
                    r_exp = self._colocar_recinto_en_grid({
                        "nombre": nombre_err,
                        "fila": info["fila"], "col": info["col"],
                        "ancho_celdas": nueva_ancho, "alto_celdas": nueva_alto,
                    })
                    if r_exp.get("ok"):
                        log(f"Auto-expandido {nombre_err} a {nueva_ancho}x{nueva_alto}m")
                    elif r_exp.get("sugerencia"):
                        sug = r_exp["sugerencia"]
                        self._colocar_recinto_en_grid({
                            "nombre": nombre_err,
                            "fila": sug["fila"], "col": sug["col"],
                            "ancho_celdas": nueva_ancho, "alto_celdas": nueva_alto,
                        })
                        log(f"Auto-expandido {nombre_err} movido a fila={sug['fila']} col={sug['col']}")

            # Re-validar después de correcciones
            val = self._validar_grid({})
            errores_restantes = [e for e in val["errores"] if "lado menor" not in e]
            if not val.get("ok") and errores_restantes:
                if forzar:
                    log(f"Advertencias (forzar=True — dibujando igual): {errores_restantes}")
                else:
                    log(f"BLOQUEADO por errores: {val['errores']}")
                    return {"ok": False, "errores": val["errores"], "resultados": resultados}

        log(f"Validación: Score {val.get('score', 0)}/100 — {'OK' if val.get('ok') else 'con advertencias'}")

        # 4. Dibujar con vanos de puertas integrados
        puertas_plan = plan.get("puertas", [])
        self._aperturas_pending = self._calcular_aperturas_de_plan(puertas_plan)
        log(f"Aperturas calculadas: {len(self._aperturas_pending['inter'])} inter-recintos, "
            f"{len(self._aperturas_pending['ext'])} exteriores")
        try:
            r = self._dibujar_desde_grid({"origen_x": 0, "origen_y": 0}, forzar=forzar)
        finally:
            self._aperturas_pending = None

        log(f"Dibujado: {r.get('muros_count', 0)} muros con vanos, {len(r.get('recintos_dibujados', []))} recintos")
        resultados.append(r)

        self._zoom_todo({})
        return {"ok": True, "resultados": resultados, "validacion": val}

    def _compactar_recintos_flotantes(self, log=None):
        """Si hay recintos sin ningún vecino, los mueve junto al bloque principal."""
        if not self._grid or len(self._grid.recintos) < 2:
            return
        log = log or self._log
        ady = self._grid.adyacencias()

        # Encontrar bloque principal (recinto con más vecinos)
        recintos_flotantes = [n for n, v in ady.items() if len(v) == 0]
        if not recintos_flotantes:
            return

        # Para cada flotante, pegarlo al borde del bloque conectado
        conectados = [n for n in self._grid.recintos if n not in recintos_flotantes]
        if not conectados:
            return

        for nombre in recintos_flotantes:
            info = self._grid.recintos[nombre]
            ancho = info["ancho"]
            alto = info["alto"]
            self._grid.quitar(nombre)

            # Buscar posición adyacente a algún recinto conectado
            colocado = False
            for ref_nombre in conectados:
                ref = self._grid.recintos[ref_nombre]
                # Intentar: abajo del ref, arriba, derecha, izquierda
                candidatos = [
                    (ref["fila"] + ref["alto"], ref["col"]),
                    (ref["fila"] - alto, ref["col"]),
                    (ref["fila"], ref["col"] + ref["ancho"]),
                    (ref["fila"], ref["col"] - ancho),
                ]
                for cf, cc in candidatos:
                    if cf < 0 or cc < 0:
                        continue
                    try:
                        self._grid.colocar(nombre, cf, cc, ancho, alto)
                        log(f"Compactado {nombre} → fila={cf} col={cc} (junto a {ref_nombre})")
                        conectados.append(nombre)
                        colocado = True
                        break
                    except ValueError:
                        continue
                if colocado:
                    break

            if not colocado:
                # Fallback: usar sugerencia de posición libre
                sug = self._grid.encontrar_posicion_libre(ancho, alto)
                if sug:
                    self._grid.colocar(nombre, sug["fila"], sug["col"], ancho, alto)
                    log(f"Reubicado {nombre} → fila={sug['fila']} col={sug['col']}")

    def ejecutar(self, herramienta: str, params: dict) -> dict:
        if herramienta == "consultar_normativa":
            try:
                self._log(f"Ejecutando: consultar_normativa")
                resultado = self._consultar_normativa(params)
                self._log(f"Completado: consultar_normativa")
                return resultado
            except Exception as e:
                return {"error": str(e)}

        metodos_planificacion = {
            "crear_grid_diseno": self._crear_grid_diseno,
            "colocar_recinto_en_grid": self._colocar_recinto_en_grid,
            "quitar_recinto_de_grid": self._quitar_recinto_de_grid,
            "ver_grid_ascii": self._ver_grid_ascii,
            "validar_grid": self._validar_grid,
            "ver_preview_grid": self._ver_preview_grid,
            "info_grid": self._info_grid,
        }

        metodos_dibujo_inteligente = {
            "dibujar_desde_grid": self._dibujar_desde_grid,
            "agregar_puerta_entre_recintos": self._agregar_puerta_entre_recintos,
            "agregar_puerta_exterior": self._agregar_puerta_exterior,
            "ver_manifest": self._ver_manifest,
        }

        if herramienta in metodos_planificacion:
            try:
                self._log(f"Ejecutando: {herramienta}")
                resultado = metodos_planificacion[herramienta](params)
                self._log(f"Completado: {herramienta}")
                return resultado
            except Exception as e:
                self._log(f"Error en {herramienta}: {e}")
                return {"error": str(e)}

        if herramienta in metodos_dibujo_inteligente:
            try:
                self._log(f"Ejecutando: {herramienta}")
                resultado = metodos_dibujo_inteligente[herramienta](params)
                self._log(f"Completado: {herramienta}")
                return resultado
            except Exception as e:
                self._log(f"Error en {herramienta}: {e}")
                return {"error": str(e)}

        if not self._conectar():
            return {"error": "AutoCAD no esta abierto"}

        metodos = {
            "dibujar_muro": self._dibujar_muro,
            "dibujar_rectangulo": self._dibujar_rectangulo,
            "dibujar_linea": self._dibujar_linea,
            "dibujar_polilinea": self._dibujar_polilinea,
            "dibujar_circulo": self._dibujar_circulo,
            "insertar_texto": self._insertar_texto,
            "crear_capa": self._crear_capa,
            "listar_capas": self._listar_capas,
            "leer_info_plano": self._leer_info_plano,
            "agregar_puerta": self._agregar_puerta,
            "agregar_ventana": self._agregar_ventana,
            "agregar_columna": self._agregar_columna,
            "aplicar_estandares_capas": self._aplicar_estandares_capas,
            "insertar_norte": self._insertar_norte,
            "insertar_escala": self._insertar_escala,
            "cotar_planta": self._cotar_planta,
            "exportar_pdf": self._exportar_pdf,
            "listar_objetos_por_capa": self._listar_objetos_por_capa,
            "zoom_todo": self._zoom_todo,
        }

        fn = metodos.get(herramienta)
        if not fn:
            return {"error": f"Herramienta no encontrada: {herramienta}"}

        try:
            self._log(f"Ejecutando: {herramienta}")
            resultado = fn(params)
            self._log(f"Completado: {herramienta}")
            return resultado
        except Exception as e:
            self._log(f"Error en {herramienta}: {e}")
            return {"error": str(e)}

    # ── Herramientas de planificacion ──────────────────────

    def _consultar_normativa(self, p: dict) -> dict:
        tema = p.get("tema", "").lower()
        if not tema:
            return {"error": "Debe especificar un tema a buscar"}

        normativa_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "normativa")
        index_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "normativa_index.json")

        try:
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
        except Exception:
            return {"ok": False, "resultado": "No hay documentos de normativa cargados."}

        palabras = [p for p in tema.replace(",", " ").split() if len(p) > 2]
        resultados = []

        for item in index:
            if not item.get("activo", True):
                continue
            ruta = os.path.join(normativa_dir, item["archivo"])
            if not os.path.exists(ruta):
                continue
            with open(ruta, "r", encoding="utf-8") as f:
                lineas = f.readlines()

            secciones_relevantes = []
            i = 0
            while i < len(lineas):
                linea = lineas[i]
                if any(p in linea.lower() for p in palabras):
                    inicio = max(0, i - 2)
                    fin = min(len(lineas), i + 15)
                    bloque = "".join(lineas[inicio:fin]).strip()
                    if bloque not in secciones_relevantes:
                        secciones_relevantes.append(bloque)
                    i = fin
                else:
                    i += 1

            if secciones_relevantes:
                resultados.append(f"### {item['titulo']}\n" + "\n---\n".join(secciones_relevantes[:5]))

        if not resultados:
            return {
                "ok": True,
                "resultado": f"No se encontraron secciones sobre '{tema}' en los documentos de normativa cargados.",
            }

        return {
            "ok": True,
            "tema": tema,
            "resultado": "\n\n".join(resultados),
        }

    def _crear_grid_diseno(self, p: dict) -> dict:
        ancho = float(p["ancho_m"])
        alto = float(p["alto_m"])
        escala = float(p.get("escala", 1.0))
        self._grid = Grid(ancho, alto, escala)
        self._grid.norte = p.get("norte", "arriba")
        self._grid.calle = p.get("calle", "abajo")
        g = self._grid
        return {
            "ok": True,
            "filas": g.rows,
            "cols": g.cols,
            "ancho_m": ancho,
            "alto_m": alto,
            "regla": (
                f"Grid {g.rows}x{g.cols}. "
                f"Recinto en fila=F col=C ancho=W alto=H ocupa celdas filas [F..F+H-1] cols [C..C+W-1]. "
                f"Verifica que NO se solapen antes de colocar."
            ),
            "ascii": g.to_ascii(),
        }

    def _colocar_recinto_en_grid(self, p: dict) -> dict:
        if not self._grid:
            return {"error": "Primero crea el grid con crear_grid_diseno"}
        nombre = str(p["nombre"])
        fila = int(p["fila"])
        col = int(p["col"])
        ancho_celdas = int(p["ancho_celdas"])
        alto_celdas = int(p["alto_celdas"])
        try:
            info = self._grid.colocar(nombre, fila, col, ancho_celdas, alto_celdas)
            return {
                "ok": True,
                "recinto": nombre,
                "area_m2": info["area_m2"],
                "dimensiones_m": f"{info['ancho_m']}x{info['alto_m']}",
            }
        except ValueError as e:
            # Dar al IA información suficiente para corregirse sola
            g = self._grid
            # Detectar celdas en conflicto
            conflictos = []
            for dr in range(alto_celdas):
                for dc in range(ancho_celdas):
                    r, c = fila + dr, col + dc
                    if 0 <= r < g.rows and 0 <= c < g.cols and g.cells[r][c] is not None:
                        conflictos.append({"celda": [r, c], "ocupada_por": g.cells[r][c]})
            # Sugerir posición libre para el mismo tamaño
            sugerencia = g.encontrar_posicion_libre(ancho_celdas, alto_celdas)
            # Zonas libres disponibles
            zonas = g.zonas_libres_compactas()[:3]
            return {
                "error": str(e),
                "conflictos": conflictos[:3],
                "sugerencia": sugerencia,
                "zonas_libres": zonas,
                "grid_actual": g.to_ascii(),
                "instruccion": (
                    f"Usa fila={sugerencia['fila']} col={sugerencia['col']} para '{nombre}', "
                    f"o elige otra zona libre. NO repitas la misma posicion."
                ) if sugerencia else "No hay espacio para ese tamaño. Reduce ancho_celdas o alto_celdas.",
            }

    def _quitar_recinto_de_grid(self, p: dict) -> dict:
        if not self._grid:
            return {"error": "No hay grid activo"}
        nombre = p["nombre"]
        if self._grid.quitar(nombre):
            return {"ok": True, "removido": nombre}
        return {"error": f"Recinto '{nombre}' no existe en el grid"}

    def _ver_grid_ascii(self, p: dict) -> dict:
        if not self._grid:
            return {"error": "No hay grid activo"}
        return {
            "ok": True,
            "ascii": self._grid.to_ascii(),
            "recintos": list(self._grid.recintos.keys()),
        }

    def _validar_grid(self, p: dict) -> dict:
        if not self._grid:
            return {"error": "No hay grid activo"}
        resultado = validar_grid(self._grid)
        return resultado

    def _ver_preview_grid(self, p: dict) -> dict:
        if not self._grid:
            return {"error": "No hay grid activo"}
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ruta = os.path.join(base, "cache", "previews", f"preview_{int(time.time())}.png")

        # Obtener validación para mostrar en el preview
        validacion = validar_grid(self._grid)

        info = generar_preview(self._grid, ruta, validacion=validacion)
        return {
            "ok": True,
            "path": info["path"],
            "png_base64": info["base64"],
            "media_type": "image/png",
            "ancho_px": info["width"],
            "alto_px": info["height"],
            "score": validacion.get("score", 0),
            "mensaje": f"Preview con validación (Score {validacion.get('score', 0)}/100). Analiza: proporciones, accesos, adyacencias.",
        }

    def _info_grid(self, p: dict) -> dict:
        if not self._grid:
            return {"ok": True, "activo": False, "mensaje": "No hay grid activo"}
        return {
            "ok": True,
            "activo": True,
            "grid": self._grid.to_dict(),
            "adyacencias": self._grid.adyacencias(),
        }

    def _calcular_walls_grid(self) -> list:
        """Traza los muros del grid como segmentos unicos compartidos."""
        g = self._grid
        walls = []

        # Muros horizontales (entre fila r-1 y r)
        for r in range(g.rows + 1):
            seg_inicio = None
            seg_l1 = None
            seg_l2 = None
            for c in range(g.cols):
                arriba = g.cells[r - 1][c] if r > 0 else None
                abajo = g.cells[r][c] if r < g.rows else None
                es_muro = arriba != abajo

                if es_muro:
                    if seg_inicio is None:
                        seg_inicio = c
                        seg_l1 = arriba
                        seg_l2 = abajo
                    elif arriba != seg_l1 or abajo != seg_l2:
                        walls.append({
                            "tipo": "horizontal", "fila": r,
                            "col_inicio": seg_inicio, "col_fin": c,
                            "lado1": seg_l1, "lado2": seg_l2,
                        })
                        seg_inicio = c
                        seg_l1 = arriba
                        seg_l2 = abajo
                else:
                    if seg_inicio is not None:
                        walls.append({
                            "tipo": "horizontal", "fila": r,
                            "col_inicio": seg_inicio, "col_fin": c,
                            "lado1": seg_l1, "lado2": seg_l2,
                        })
                        seg_inicio = None
            if seg_inicio is not None:
                walls.append({
                    "tipo": "horizontal", "fila": r,
                    "col_inicio": seg_inicio, "col_fin": g.cols,
                    "lado1": seg_l1, "lado2": seg_l2,
                })

        # Muros verticales (entre col c-1 y c)
        for c in range(g.cols + 1):
            seg_inicio = None
            seg_l1 = None
            seg_l2 = None
            for r in range(g.rows):
                izq = g.cells[r][c - 1] if c > 0 else None
                der = g.cells[r][c] if c < g.cols else None
                es_muro = izq != der

                if es_muro:
                    if seg_inicio is None:
                        seg_inicio = r
                        seg_l1 = izq
                        seg_l2 = der
                    elif izq != seg_l1 or der != seg_l2:
                        walls.append({
                            "tipo": "vertical", "col": c,
                            "fila_inicio": seg_inicio, "fila_fin": r,
                            "lado1": seg_l1, "lado2": seg_l2,
                        })
                        seg_inicio = r
                        seg_l1 = izq
                        seg_l2 = der
                else:
                    if seg_inicio is not None:
                        walls.append({
                            "tipo": "vertical", "col": c,
                            "fila_inicio": seg_inicio, "fila_fin": r,
                            "lado1": seg_l1, "lado2": seg_l2,
                        })
                        seg_inicio = None
            if seg_inicio is not None:
                walls.append({
                    "tipo": "vertical", "col": c,
                    "fila_inicio": seg_inicio, "fila_fin": g.rows,
                    "lado1": seg_l1, "lado2": seg_l2,
                })

        return walls

    def _calcular_coords_grid(self, p: dict) -> dict:
        """Devuelve diccionarios x_col y y_fila con coords reales segun ajustes."""
        g = self._grid
        origen_x = float(p.get("origen_x", 0.0))
        origen_y = float(p.get("origen_y", 0.0))
        ajustes = p.get("ajustes_dimensiones", {})

        ancho_col = {c: g.escala for c in range(g.cols)}
        alto_fila = {r: g.escala for r in range(g.rows)}

        for c_str, valor in ajustes.get("columnas", {}).items():
            try:
                ancho_col[int(c_str)] = float(valor)
            except (ValueError, TypeError):
                pass
        for r_str, valor in ajustes.get("filas", {}).items():
            try:
                alto_fila[int(r_str)] = float(valor)
            except (ValueError, TypeError):
                pass

        x_col = {0: origen_x}
        for c in range(1, g.cols + 1):
            x_col[c] = x_col[c - 1] + ancho_col.get(c - 1, g.escala)

        ancho_total = x_col[g.cols] - origen_x
        alto_total_temp = sum(alto_fila.values())

        # Y invertida: fila 0 es la parte superior del plano (Y maxima)
        y_arriba = origen_y + alto_total_temp
        y_fila = {0: y_arriba}
        for r in range(1, g.rows + 1):
            y_fila[r] = y_fila[r - 1] - alto_fila.get(r - 1, g.escala)

        return {
            "x_col": x_col, "y_fila": y_fila,
            "origen_x": origen_x, "origen_y": origen_y,
            "ancho_total": ancho_total, "alto_total": alto_total_temp,
        }

    def _dibujar_desde_grid(self, p: dict, forzar: bool = False) -> dict:
        if not self._grid:
            return {"error": "No hay grid activo. Crea uno primero con crear_grid_diseno"}

        validacion = validar_grid(self._grid)
        if not validacion["ok"] and not forzar:
            return {
                "error": "BLOQUEADO: el grid tiene errores de normativa. Corrige antes de dibujar.",
                "errores": validacion["errores"],
                "instruccion": "Usa quitar_recinto_de_grid y colocar_recinto_en_grid para corregir. Luego valida de nuevo.",
            }

        if not self._conectar():
            return {"error": "AutoCAD no esta abierto"}

        self._aplicar_estandares_capas({})
        coords = self._calcular_coords_grid(p)
        x_col = coords["x_col"]
        y_fila = coords["y_fila"]

        # 1. Calcular y dibujar muros unicos (sin duplicados)
        walls = self._calcular_walls_grid()
        muros_dibujados = []
        esquinas_detectadas = {}  # Para validar que sean cuadradas

        # Aperturas pendientes (set por ejecutar_plan_json antes de llamar aquí)
        aperturas_data = getattr(self, "_aperturas_pending", None)

        for w in walls:
            es_exterior = w["lado1"] is None or w["lado2"] is None
            espesor = 0.15

            # ── Calcular aperturas para este muro ──────────────
            aperturas_para_muro = []
            if aperturas_data:
                r1, r2 = w.get("lado1"), w.get("lado2")
                if r1 is not None and r2 is not None:
                    key = frozenset([r1, r2])
                    if key in aperturas_data["inter"]:
                        ap = aperturas_data["inter"][key]
                        if w["tipo"] == "horizontal":
                            cx = (x_col[w["col_inicio"]] + x_col[w["col_fin"]]) / 2
                            aperturas_para_muro.append({"centro": cx, "ancho": ap["ancho"]})
                        else:
                            cy = (y_fila[w["fila_inicio"]] + y_fila[w["fila_fin"]]) / 2
                            aperturas_para_muro.append({"centro": cy, "ancho": ap["ancho"]})
                else:
                    # Muro exterior: identificar recinto y lado
                    recinto = r2 if r1 is None else r1
                    if recinto and recinto in self._grid.recintos:
                        info_r = self._grid.recintos[recinto]
                        lado = None
                        if w["tipo"] == "horizontal":
                            if w["fila"] == info_r["fila"]:
                                lado = "norte"
                            elif w["fila"] == info_r["fila"] + info_r["alto"]:
                                lado = "sur"
                        else:
                            if w["col"] == info_r["col"]:
                                lado = "oeste"
                            elif w["col"] == info_r["col"] + info_r["ancho"]:
                                lado = "este"
                        if lado and (recinto, lado) in aperturas_data["ext"]:
                            ap = aperturas_data["ext"][(recinto, lado)]
                            if w["tipo"] == "horizontal":
                                cx = (x_col[w["col_inicio"]] + x_col[w["col_fin"]]) / 2
                                aperturas_para_muro.append({"centro": cx, "ancho": ap["ancho"]})
                            else:
                                cy = (y_fila[w["fila_inicio"]] + y_fila[w["fila_fin"]]) / 2
                                aperturas_para_muro.append({"centro": cy, "ancho": ap["ancho"]})

            # ── Dibujar muro (con o sin vano) ──────────────────
            if w["tipo"] == "horizontal":
                x1 = x_col[w["col_inicio"]]
                x2 = x_col[w["col_fin"]]
                y = y_fila[w["fila"]]
                self._dibujar_muro_lineas_con_vano(x1, y, x2, y, espesor, "A-MURO", aperturas_para_muro)
                muros_dibujados.append({"tipo": "H", "x1": x1, "y1": y, "x2": x2, "y2": y,
                                        "exterior": es_exterior,
                                        "entre": [w["lado1"], w["lado2"]]})
                esquinas_detectadas[(round(x1, 3), round(y, 3))] = "H"
                esquinas_detectadas[(round(x2, 3), round(y, 3))] = "H"
            else:
                x = x_col[w["col"]]
                y1 = y_fila[w["fila_inicio"]]
                y2 = y_fila[w["fila_fin"]]
                self._dibujar_muro_lineas_con_vano(x, y1, x, y2, espesor, "A-MURO", aperturas_para_muro)
                muros_dibujados.append({"tipo": "V", "x1": x, "y1": y1, "x2": x, "y2": y2,
                                        "exterior": es_exterior,
                                        "entre": [w["lado1"], w["lado2"]]})
                esquinas_detectadas[(round(x, 3), round(y1, 3))] = "V"
                esquinas_detectadas[(round(x, 3), round(y2, 3))] = "V"

        # 2. Etiquetar recintos en su centro real
        recintos_info = {}
        for nombre, info in self._grid.recintos.items():
            x_left = x_col[info["col"]]
            x_right = x_col[info["col"] + info["ancho"]]
            y_top = y_fila[info["fila"]]
            y_bot = y_fila[info["fila"] + info["alto"]]
            cx = (x_left + x_right) / 2
            cy = (y_top + y_bot) / 2

            ancho_real = x_right - x_left
            alto_real = y_top - y_bot
            altura_texto = min(0.30, ancho_real / max(len(nombre), 4) * 0.5)
            altura_texto = max(altura_texto, 0.15)

            self._insertar_texto({
                "texto": nombre.upper(),
                "x": cx, "y": cy,
                "altura": altura_texto,
                "capa": "A-TEXTO",
            })
            recintos_info[nombre] = {
                "centro": [round(cx, 3), round(cy, 3)],
                "x_min": round(x_left, 3), "y_min": round(y_bot, 3),
                "x_max": round(x_right, 3), "y_max": round(y_top, 3),
                "ancho_m": round(ancho_real, 3),
                "alto_m": round(alto_real, 3),
            }

        # 3. Guardar manifesto en memoria para que otras tools lo usen
        self._manifest = {
            "recintos": recintos_info,
            "muros": muros_dibujados,
            "x_col": x_col,
            "y_fila": y_fila,
        }

        self._zoom_todo({})

        # Validar calidad de esquinas detectadas
        esquinas_de_interseccion = {}
        for esquina, tipo in esquinas_detectadas.items():
            if esquina not in esquinas_de_interseccion:
                esquinas_de_interseccion[esquina] = []
            esquinas_de_interseccion[esquina].append(tipo)

        esquinas_perfectas = sum(1 for v in esquinas_de_interseccion.values() if len(v) == 2)
        esquinas_totales = len(esquinas_de_interseccion)

        score_esquinas = (esquinas_perfectas / max(esquinas_totales, 1)) * 100 if esquinas_totales > 0 else 100

        return {
            "ok": True,
            "recintos_dibujados": list(recintos_info.keys()),
            "manifest": recintos_info,
            "muros_count": len(muros_dibujados),
            "esquinas_totales": esquinas_totales,
            "esquinas_perfectas": esquinas_perfectas,
            "score_esquinas": round(score_esquinas, 1),
            "validacion": validacion,
            "score_layout": validacion.get("score", 0),
            "mensaje": (
                f"✓ Dibujados {len(recintos_info)} recintos con {len(muros_dibujados)} muros. "
                f"Esquinas: {esquinas_perfectas}/{esquinas_totales} perfectas ({score_esquinas:.0f}%). "
                f"Score layout: {validacion.get('score', 0)}/100. "
                f"Usa agregar_puerta_entre_recintos para añadir puertas."
            ),
        }

    def _agregar_puerta_entre_recintos(self, p: dict) -> dict:
        """Coloca una puerta en el muro compartido entre dos recintos."""
        if not self._grid:
            return {"error": "No hay grid activo"}
        if not hasattr(self, "_manifest") or not self._manifest:
            return {"error": "Primero ejecuta dibujar_desde_grid"}
        if not self._conectar():
            return {"error": "AutoCAD no esta abierto"}

        r1 = str(p["recinto1"])
        r2 = str(p["recinto2"])
        ancho_puerta = float(p.get("ancho", 1.0))

        manifest = self._manifest
        if r1 not in manifest["recintos"] or r2 not in manifest["recintos"]:
            return {"error": f"Recintos no encontrados: {r1}, {r2}"}

        muro_compartido = None
        for m in manifest["muros"]:
            entre = m["entre"]
            if (r1 in entre and r2 in entre):
                muro_compartido = m
                break

        if not muro_compartido:
            return {"error": f"{r1} y {r2} no comparten muro"}

        if muro_compartido["tipo"] == "H":
            cx = (muro_compartido["x1"] + muro_compartido["x2"]) / 2
            y = muro_compartido["y1"]
            self._agregar_puerta({
                "x": cx - ancho_puerta / 2,
                "y": y,
                "ancho": ancho_puerta,
                "rotacion": 0,
                "capa": "A-PUERTA",
            })
            return {"ok": True, "puerta_en": [cx, y], "entre": [r1, r2]}
        else:
            x = muro_compartido["x1"]
            cy = (muro_compartido["y1"] + muro_compartido["y2"]) / 2
            self._agregar_puerta({
                "x": x,
                "y": cy - ancho_puerta / 2,
                "ancho": ancho_puerta,
                "rotacion": 90,
                "capa": "A-PUERTA",
            })
            return {"ok": True, "puerta_en": [x, cy], "entre": [r1, r2]}

    def _agregar_puerta_exterior(self, p: dict) -> dict:
        """Coloca una puerta en el muro exterior de un recinto."""
        if not hasattr(self, "_manifest") or not self._manifest:
            return {"error": "Primero ejecuta dibujar_desde_grid"}
        if not self._conectar():
            return {"error": "AutoCAD no esta abierto"}

        recinto = str(p["recinto"])
        lado = str(p.get("lado", "sur")).lower()
        ancho_puerta = float(p.get("ancho", 1.10))

        manifest = self._manifest
        if recinto not in manifest["recintos"]:
            return {"error": f"Recinto no encontrado: {recinto}"}

        muro_exterior = None
        for m in manifest["muros"]:
            if not m["exterior"]:
                continue
            if recinto not in m["entre"]:
                continue
            es_lado_correcto = False
            if lado == "sur" and m["tipo"] == "H" and m["y1"] < manifest["recintos"][recinto]["centro"][1]:
                es_lado_correcto = True
            elif lado == "norte" and m["tipo"] == "H" and m["y1"] > manifest["recintos"][recinto]["centro"][1]:
                es_lado_correcto = True
            elif lado == "este" and m["tipo"] == "V" and m["x1"] > manifest["recintos"][recinto]["centro"][0]:
                es_lado_correcto = True
            elif lado == "oeste" and m["tipo"] == "V" and m["x1"] < manifest["recintos"][recinto]["centro"][0]:
                es_lado_correcto = True
            if es_lado_correcto:
                muro_exterior = m
                break

        if not muro_exterior:
            return {"error": f"{recinto} no tiene muro exterior hacia {lado}"}

        if muro_exterior["tipo"] == "H":
            cx = (muro_exterior["x1"] + muro_exterior["x2"]) / 2
            y = muro_exterior["y1"]
            self._agregar_puerta({
                "x": cx - ancho_puerta / 2, "y": y,
                "ancho": ancho_puerta, "rotacion": 0, "capa": "A-PUERTA",
            })
            return {"ok": True, "puerta_en": [cx, y], "recinto": recinto, "lado": lado}
        else:
            x = muro_exterior["x1"]
            cy = (muro_exterior["y1"] + muro_exterior["y2"]) / 2
            self._agregar_puerta({
                "x": x, "y": cy - ancho_puerta / 2,
                "ancho": ancho_puerta, "rotacion": 90, "capa": "A-PUERTA",
            })
            return {"ok": True, "puerta_en": [x, cy], "recinto": recinto, "lado": lado}

    def _ver_manifest(self, p: dict) -> dict:
        if not hasattr(self, "_manifest") or not self._manifest:
            return {"error": "Aun no se ha dibujado nada. Ejecuta dibujar_desde_grid primero"}
        return {"ok": True, "manifest": self._manifest["recintos"], "muros_count": len(self._manifest["muros"])}

    def _pt(self, x: float, y: float, z: float = 0.0):
        return win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R8, [x, y, z]
        )

    def _set_capa(self, nombre: str):
        try:
            self._doc.ActiveLayer = self._doc.Layers.Item(nombre)
        except Exception:
            layer = self._doc.Layers.Add(nombre)
            self._doc.ActiveLayer = layer

    def _crear_capa(self, p: dict) -> dict:
        nombre = p["nombre"]
        color = p.get("color", 7)
        try:
            layer = self._doc.Layers.Item(nombre)
        except Exception:
            layer = self._doc.Layers.Add(nombre)
        layer.Color = color
        return {"ok": True, "capa": nombre, "color": color}

    def _listar_capas(self, p: dict) -> dict:
        capas = []
        for i in range(self._doc.Layers.Count):
            layer = self._doc.Layers.Item(i)
            capas.append({"nombre": layer.Name, "color": layer.Color, "visible": layer.LayerOn})
        return {"capas": capas}

    def _leer_info_plano(self, p: dict) -> dict:
        return {
            "nombre": self._doc.Name,
            "ruta": self._doc.FullName,
            "unidades": self._doc.GetVariable("INSUNITS"),
            "objetos": self._doc.ModelSpace.Count,
        }

    def _dibujar_rectangulo(self, p: dict) -> dict:
        x, y = p["x"], p["y"]
        w, h = p["ancho"], p["alto"]
        capa = p.get("capa", "0")
        self._set_capa(capa)
        pts = [x, y, 0, x + w, y, 0, x + w, y + h, 0, x, y + h, 0]
        arr = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, pts)
        poly = self._doc.ModelSpace.AddLightWeightPolyline(arr)
        poly.Closed = True
        poly.Layer = capa
        return {"ok": True, "handle": poly.Handle}

    def _dibujar_linea(self, p: dict) -> dict:
        capa = p.get("capa", "0")
        self._set_capa(capa)
        line = self._doc.ModelSpace.AddLine(
            self._pt(p["x1"], p["y1"]),
            self._pt(p["x2"], p["y2"]),
        )
        line.Layer = capa
        return {"ok": True, "handle": line.Handle}

    def _dibujar_polilinea(self, p: dict) -> dict:
        puntos = p["puntos"]
        capa = p.get("capa", "0")
        self._set_capa(capa)
        pts = []
        for pt in puntos:
            pts.extend([pt["x"], pt["y"], 0])
        arr = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, pts)
        poly = self._doc.ModelSpace.AddLightWeightPolyline(arr)
        poly.Layer = capa
        return {"ok": True, "handle": poly.Handle}

    def _dibujar_circulo(self, p: dict) -> dict:
        capa = p.get("capa", "0")
        self._set_capa(capa)
        circ = self._doc.ModelSpace.AddCircle(
            self._pt(p["x"], p["y"]), p["radio"]
        )
        circ.Layer = capa
        return {"ok": True, "handle": circ.Handle}

    def _insertar_texto(self, p: dict) -> dict:
        capa = p.get("capa", "A-TEXTO")
        self._set_capa(capa)
        texto = self._doc.ModelSpace.AddText(
            p["texto"], self._pt(p["x"], p["y"]), p.get("altura", 0.20)
        )
        texto.Layer = capa
        texto.Alignment = 1  # center
        texto.TextAlignmentPoint = self._pt(p["x"], p["y"])
        return {"ok": True, "handle": texto.Handle}

    def _dibujar_muro(self, p: dict) -> dict:
        x1, y1 = p["x1"], p["y1"]
        x2, y2 = p["x2"], p["y2"]
        tipo = p.get("tipo", "bloque")
        espesor = p.get("espesor", 0.15 if tipo == "bloque" else 0.10)
        capa = p.get("capa", "A-MURO")

        self._crear_capa({"nombre": capa, "color": 1})

        dx = x2 - x1
        dy = y2 - y1
        length = math.sqrt(dx * dx + dy * dy)
        if length == 0:
            return {"error": "Muro de longitud cero"}

        nx = -dy / length * (espesor / 2)
        ny = dx / length * (espesor / 2)

        # Dibujar muro como polilínea cerrada (rectángulo) para esquinas perfectas
        pts = [
            x1 + nx, y1 + ny,
            x2 + nx, y2 + ny,
            x2 - nx, y2 - ny,
            x1 - nx, y1 - ny,
        ]

        self._set_capa(capa)
        arr = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, pts)
        pline = self._doc.ModelSpace.AddLightWeightPolyline(arr)
        pline.Layer = capa
        pline.Closed = True

        return {
            "ok": True,
            "handle": pline.Handle,
            "espesor": espesor,
            "longitud": round(length, 4),
        }

    def _dibujar_muro_lineas_con_vano(self, x1: float, y1: float, x2: float, y2: float,
                                      espesor: float, capa: str, aperturas: list) -> dict:
        """
        Dibuja muro como dos lineas paralelas con vanos (gaps) y jambas + arco de puerta.
        aperturas: lista de {'centro': float, 'ancho': float}
          - muro horizontal (y1==y2): centro = X absoluto
          - muro vertical  (x1==x2): centro = Y absoluto
        Si aperturas vacio dibuja muro solido (sin polilinea, dos lineas).
        """
        self._crear_capa({"nombre": capa, "color": 1})
        self._crear_capa({"nombre": "A-PUERTA", "color": 3})

        handles = []

        def _ln(ax1, ay1, ax2, ay2, layer):
            if abs(ax2 - ax1) < 1e-6 and abs(ay2 - ay1) < 1e-6:
                return
            self._set_capa(layer)
            ln = self._doc.ModelSpace.AddLine(self._pt(ax1, ay1), self._pt(ax2, ay2))
            ln.Layer = layer
            handles.append(ln.Handle)

        es_horizontal = (abs(x2 - x1) >= abs(y2 - y1))

        if es_horizontal:
            y_top = y1 + espesor / 2
            y_bot = y1 - espesor / 2
            x_from, x_to = min(x1, x2), max(x1, x2)

            # Build gaps sorted by x
            gaps = sorted(
                (a["centro"] - a["ancho"] / 2, a["centro"] + a["ancho"] / 2)
                for a in aperturas
            )

            def _draw_h_line(y_val):
                pos = x_from
                for gx1, gx2 in gaps:
                    gx1 = max(gx1, x_from)
                    gx2 = min(gx2, x_to)
                    if gx1 > pos + 1e-6:
                        _ln(pos, y_val, gx1, y_val, capa)
                    pos = max(pos, gx2)
                if pos < x_to - 1e-6:
                    _ln(pos, y_val, x_to, y_val, capa)

            _draw_h_line(y_top)
            _draw_h_line(y_bot)

            # Jambas + door symbols
            y_mid = (y_top + y_bot) / 2
            for a in aperturas:
                cx = a["centro"]
                hw = a["ancho"] / 2
                _ln(cx - hw, y_bot, cx - hw, y_top, "A-PUERTA")   # jamba izq
                _ln(cx + hw, y_bot, cx + hw, y_top, "A-PUERTA")   # jamba der
                # Panel de puerta (linea)
                _ln(cx - hw, y_mid, cx + hw, y_mid, "A-PUERTA")
                # Arco 90° desde jamba izquierda hacia arriba
                try:
                    self._set_capa("A-PUERTA")
                    arc = self._doc.ModelSpace.AddArc(
                        self._pt(cx - hw, y_mid), a["ancho"],
                        math.radians(0), math.radians(90)
                    )
                    arc.Layer = "A-PUERTA"
                    handles.append(arc.Handle)
                except Exception:
                    pass

        else:
            # Vertical wall
            x_left = x1 - espesor / 2
            x_right = x1 + espesor / 2
            y_from, y_to = min(y1, y2), max(y1, y2)

            gaps = sorted(
                (a["centro"] - a["ancho"] / 2, a["centro"] + a["ancho"] / 2)
                for a in aperturas
            )

            def _draw_v_line(x_val):
                pos = y_from
                for gy1, gy2 in gaps:
                    gy1 = max(gy1, y_from)
                    gy2 = min(gy2, y_to)
                    if gy1 > pos + 1e-6:
                        _ln(x_val, pos, x_val, gy1, capa)
                    pos = max(pos, gy2)
                if pos < y_to - 1e-6:
                    _ln(x_val, pos, x_val, y_to, capa)

            _draw_v_line(x_left)
            _draw_v_line(x_right)

            x_mid = (x_left + x_right) / 2
            for a in aperturas:
                cy = a["centro"]
                hw = a["ancho"] / 2
                _ln(x_left, cy - hw, x_right, cy - hw, "A-PUERTA")   # jamba bot
                _ln(x_left, cy + hw, x_right, cy + hw, "A-PUERTA")   # jamba top
                # Panel
                _ln(x_mid, cy - hw, x_mid, cy + hw, "A-PUERTA")
                # Arco 90° desde jamba inferior hacia la derecha
                try:
                    self._set_capa("A-PUERTA")
                    arc = self._doc.ModelSpace.AddArc(
                        self._pt(x_mid, cy - hw), a["ancho"],
                        math.radians(90), math.radians(180)
                    )
                    arc.Layer = "A-PUERTA"
                    handles.append(arc.Handle)
                except Exception:
                    pass

        return {"ok": True, "handles_count": len(handles)}

    def _calcular_aperturas_de_plan(self, puertas: list) -> dict:
        """
        Procesa la lista de puertas del plan JSON y retorna dicts para lookup durante dibujo.
        Retorna: {
          "inter": { frozenset([r1,r2]): {"ancho": float} },
          "ext":   { (recinto, lado): {"ancho": float} }
        }
        """
        inter = {}
        ext = {}
        for p in puertas:
            tipo = p.get("tipo", "")
            if tipo == "entre_recintos":
                r1 = str(p.get("recinto1", ""))
                r2 = str(p.get("recinto2", ""))
                ancho = float(p.get("ancho", 1.0))
                if r1 and r2:
                    inter[frozenset([r1, r2])] = {"ancho": ancho}
            elif tipo == "exterior":
                recinto = str(p.get("recinto", ""))
                lado = str(p.get("lado", "sur")).lower()
                ancho = float(p.get("ancho", 1.10))
                if recinto:
                    ext[(recinto, lado)] = {"ancho": ancho}
        return {"inter": inter, "ext": ext}

    def _agregar_puerta(self, p: dict) -> dict:
        x, y = p["x"], p["y"]
        ancho = p.get("ancho", 1.0)
        rot = p.get("rotacion", 0)
        capa = p.get("capa", "A-PUERTA")

        self._crear_capa({"nombre": capa, "color": 3})
        self._set_capa(capa)

        rad = math.radians(rot)
        cos_r, sin_r = math.cos(rad), math.sin(rad)

        ex = x + ancho * cos_r
        ey = y + ancho * sin_r

        panel = self._doc.ModelSpace.AddLine(self._pt(x, y), self._pt(ex, ey))
        panel.Layer = capa

        arc = self._doc.ModelSpace.AddArc(
            self._pt(x, y), ancho, math.radians(rot), math.radians(rot + 90)
        )
        arc.Layer = capa

        return {"ok": True, "handle_panel": panel.Handle, "handle_arco": arc.Handle}

    def _agregar_ventana(self, p: dict) -> dict:
        x, y = p["x"], p["y"]
        ancho = p.get("ancho", 1.2)
        rot = p.get("rotacion", 0)
        capa = p.get("capa", "A-VENTANA")

        self._crear_capa({"nombre": capa, "color": 5})
        self._set_capa(capa)

        rad = math.radians(rot)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        espesor_muro = 0.15

        half = ancho / 2
        sx = x - half * cos_r
        sy = y - half * sin_r
        ex_v = x + half * cos_r
        ey_v = y + half * sin_r

        perp_x = -sin_r
        perp_y = cos_r

        offsets = [-espesor_muro / 2, 0, espesor_muro / 2]
        handles = []
        for off in offsets:
            ox = perp_x * off
            oy = perp_y * off
            line = self._doc.ModelSpace.AddLine(
                self._pt(sx + ox, sy + oy),
                self._pt(ex_v + ox, ey_v + oy),
            )
            line.Layer = capa
            handles.append(line.Handle)

        j1 = self._doc.ModelSpace.AddLine(
            self._pt(sx + perp_x * (-espesor_muro / 2), sy + perp_y * (-espesor_muro / 2)),
            self._pt(sx + perp_x * (espesor_muro / 2), sy + perp_y * (espesor_muro / 2)),
        )
        j1.Layer = capa

        j2 = self._doc.ModelSpace.AddLine(
            self._pt(ex_v + perp_x * (-espesor_muro / 2), ey_v + perp_y * (-espesor_muro / 2)),
            self._pt(ex_v + perp_x * (espesor_muro / 2), ey_v + perp_y * (espesor_muro / 2)),
        )
        j2.Layer = capa

        return {"ok": True, "handles": handles}

    def _agregar_columna(self, p: dict) -> dict:
        cx, cy = p["x"], p["y"]
        w, h = p["ancho"], p["alto"]
        capa = p.get("capa", "A-ESTRUCTURA")

        self._crear_capa({"nombre": capa, "color": 4})
        return self._dibujar_rectangulo({
            "x": cx - w / 2, "y": cy - h / 2,
            "ancho": w, "alto": h, "capa": capa,
        })

    def _aplicar_estandares_capas(self, p: dict) -> dict:
        capas = [
            ("A-MURO", 1), ("A-MURO-GYP", 8), ("A-PUERTA", 3),
            ("A-VENTANA", 5), ("A-COTA", 2), ("A-TEXTO", 7),
            ("A-EJES", 6), ("A-ESTRUCTURA", 4), ("A-CUADRO", 8),
            ("A-MOBILIARIO", 30), ("A-PISO", 31), ("A-SANITARIO", 40),
            ("A-ELECTRICO", 14), ("A-TECHO", 9),
        ]
        creadas = []
        for nombre, color in capas:
            self._crear_capa({"nombre": nombre, "color": color})
            creadas.append(nombre)
        return {"ok": True, "capas_creadas": creadas}

    def _insertar_norte(self, p: dict) -> dict:
        x, y = p["x"], p["y"]
        radio = p.get("radio", 0.40)
        capa = p.get("capa", "A-TEXTO")

        self._crear_capa({"nombre": capa, "color": 7})
        self._set_capa(capa)

        circ = self._doc.ModelSpace.AddCircle(self._pt(x, y), radio)
        circ.Layer = capa

        flecha = self._doc.ModelSpace.AddLine(
            self._pt(x, y - radio * 0.5), self._pt(x, y + radio)
        )
        flecha.Layer = capa

        txt = self._doc.ModelSpace.AddText("N", self._pt(x - 0.08, y + radio + 0.10), 0.20)
        txt.Layer = capa

        return {"ok": True}

    def _insertar_escala(self, p: dict) -> dict:
        x, y = p["x"], p["y"]
        escala = p.get("escala", 100)
        longitud = p.get("longitud_barra", 1.0)
        capa = p.get("capa", "A-TEXTO")

        self._crear_capa({"nombre": capa, "color": 7})
        self._set_capa(capa)

        barra = self._doc.ModelSpace.AddLine(
            self._pt(x, y), self._pt(x + longitud, y)
        )
        barra.Layer = capa

        for pos in [x, x + longitud / 2, x + longitud]:
            tick = self._doc.ModelSpace.AddLine(
                self._pt(pos, y - 0.05), self._pt(pos, y + 0.05)
            )
            tick.Layer = capa

        txt = self._doc.ModelSpace.AddText(
            f"1:{escala}", self._pt(x, y - 0.25), 0.15
        )
        txt.Layer = capa

        return {"ok": True}

    def _cotar_planta(self, p: dict) -> dict:
        x, y = p["x"], p["y"]
        ancho, alto = p["ancho"], p["alto"]
        offset = p.get("offset", 0.80)
        capa = p.get("capa", "A-COTA")

        self._crear_capa({"nombre": capa, "color": 2})
        self._set_capa(capa)

        cotas_creadas = 0

        # Bottom - total
        d = self._doc.ModelSpace.AddDimAligned(
            self._pt(x, y), self._pt(x + ancho, y),
            self._pt(x + ancho / 2, y - offset)
        )
        d.Layer = capa
        cotas_creadas += 1

        # Top - total
        d = self._doc.ModelSpace.AddDimAligned(
            self._pt(x, y + alto), self._pt(x + ancho, y + alto),
            self._pt(x + ancho / 2, y + alto + offset)
        )
        d.Layer = capa
        cotas_creadas += 1

        # Left - total
        d = self._doc.ModelSpace.AddDimAligned(
            self._pt(x, y), self._pt(x, y + alto),
            self._pt(x - offset, y + alto / 2)
        )
        d.Layer = capa
        cotas_creadas += 1

        # Right - total
        d = self._doc.ModelSpace.AddDimAligned(
            self._pt(x + ancho, y), self._pt(x + ancho, y + alto),
            self._pt(x + ancho + offset, y + alto / 2)
        )
        d.Layer = capa
        cotas_creadas += 1

        return {"ok": True, "cotas": cotas_creadas}

    def _exportar_pdf(self, p: dict) -> dict:
        nombre = p.get("nombre", "plano")
        if not nombre.endswith(".pdf"):
            nombre += ".pdf"
        return {"ok": False, "mensaje": f"Exportar PDF requiere configuracion de layout. Nombre: {nombre}"}

    def _listar_objetos_por_capa(self, p: dict) -> dict:
        capa = p["capa"]
        objetos = []
        ms = self._doc.ModelSpace
        for i in range(ms.Count):
            obj = ms.Item(i)
            if obj.Layer == capa:
                objetos.append({
                    "tipo": obj.ObjectName,
                    "handle": obj.Handle,
                })
        return {"capa": capa, "objetos": objetos, "total": len(objetos)}

    def _zoom_todo(self, p: dict) -> dict:
        self._acad.ZoomExtents()
        return {"ok": True}


TOOLS_SCHEMA = [
    # ─── Planificacion espacial (grid) ───────────────────
    {
        "name": "crear_grid_diseno",
        "description": "FASE 1 - Inicia un grid de diseno. Cada celda = 1m por defecto. Usar SIEMPRE antes de dibujar para planear espacialmente. Define el terreno disponible.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ancho_m": {"type": "number", "description": "Ancho del terreno en metros"},
                "alto_m": {"type": "number", "description": "Alto del terreno en metros"},
                "escala": {"type": "number", "description": "Metros por celda (default 1.0)"},
                "norte": {"type": "string", "description": "Direccion del norte: arriba|abajo|izquierda|derecha (default arriba)"},
                "calle": {"type": "string", "description": "Direccion del frente/calle: arriba|abajo|izquierda|derecha (default abajo)"},
            },
            "required": ["ancho_m", "alto_m"],
        },
    },
    {
        "name": "colocar_recinto_en_grid",
        "description": "FASE 1 - Coloca un recinto en el grid. fila=0 es ARRIBA, col=0 es IZQUIERDA. Las celdas se cuentan desde 0. ancho_celdas y alto_celdas determinan el tamano del recinto.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string", "description": "Nombre del recinto: SALA, COCINA, DORMITORIO1, BANO, COCHERA, etc."},
                "fila": {"type": "integer", "description": "Fila inicial (esquina superior izquierda)"},
                "col": {"type": "integer", "description": "Columna inicial (esquina superior izquierda)"},
                "ancho_celdas": {"type": "integer", "description": "Numero de celdas de ancho"},
                "alto_celdas": {"type": "integer", "description": "Numero de celdas de alto"},
            },
            "required": ["nombre", "fila", "col", "ancho_celdas", "alto_celdas"],
        },
    },
    {
        "name": "quitar_recinto_de_grid",
        "description": "FASE 1 - Quita un recinto del grid para reubicarlo.",
        "input_schema": {
            "type": "object",
            "properties": {"nombre": {"type": "string"}},
            "required": ["nombre"],
        },
    },
    {
        "name": "ver_grid_ascii",
        "description": "FASE 1 - Muestra el grid actual como texto ASCII. Util para verificar la distribucion.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "validar_grid",
        "description": "FASE 1 - Valida el grid contra reglas: areas minimas, adyacencias, accesos, proporciones. Devuelve errores y advertencias. USAR DESPUES DE COLOCAR TODOS LOS RECINTOS.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ver_preview_grid",
        "description": "FASE 1 - Genera una IMAGEN PNG del plano esquematico para que TU LA VEAS. Devuelve la imagen embebida. Analiza si las proporciones son correctas, si hay flujo de circulacion logico, si la cochera esta bien ubicada.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "info_grid",
        "description": "FASE 1 - Info actual del grid: dimensiones, recintos colocados, adyacencias.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "dibujar_desde_grid",
        "description": "FASE 2 - Dibuja el grid en AutoCAD con MUROS COMPARTIDOS (sin duplicados) y etiquetas centradas automaticamente. Devuelve un MANIFEST con las coordenadas reales de cada recinto. DESPUES de llamar esto, USA agregar_puerta_entre_recintos para puertas - NO inventes coordenadas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "origen_x": {"type": "number", "description": "X del origen (default 0)"},
                "origen_y": {"type": "number", "description": "Y del origen (default 0)"},
                "ajustes_dimensiones": {
                    "type": "object",
                    "description": "Ajustes para pasar de medidas enteras a reales. Ejemplo: {\"columnas\": {\"0\": 3.5}, \"filas\": {\"2\": 2.8}}",
                    "properties": {
                        "columnas": {"type": "object"},
                        "filas": {"type": "object"},
                    },
                },
            },
        },
    },
    {
        "name": "agregar_puerta_entre_recintos",
        "description": "FASE 2 - Coloca una puerta automaticamente en el muro compartido entre dos recintos. NO necesitas inventar coordenadas. Solo da los nombres de los recintos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recinto1": {"type": "string", "description": "Nombre del primer recinto (ej. SALA)"},
                "recinto2": {"type": "string", "description": "Nombre del segundo recinto (ej. PASILLO)"},
                "ancho": {"type": "number", "description": "Ancho de la puerta (default 1.00m interior)"},
            },
            "required": ["recinto1", "recinto2"],
        },
    },
    {
        "name": "agregar_puerta_exterior",
        "description": "FASE 2 - Coloca una puerta exterior en el muro del recinto hacia un lado del terreno. NO inventes coordenadas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recinto": {"type": "string", "description": "Recinto que da al exterior (ej. COCHERA, SALA)"},
                "lado": {"type": "string", "description": "norte|sur|este|oeste. Default 'sur' (calle)"},
                "ancho": {"type": "number", "description": "Ancho puerta exterior (default 1.10m)"},
            },
            "required": ["recinto"],
        },
    },
    {
        "name": "ver_manifest",
        "description": "FASE 2 - Devuelve el estado actual de lo dibujado en AutoCAD: coordenadas reales de cada recinto, centros, dimensiones. Usalo si necesitas saber donde quedo algo.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ─── Dibujo en AutoCAD ───────────────────────────────
    {
        "name": "dibujar_muro",
        "description": "Dibuja un muro con espesor real (doble linea). tipo='bloque' usa 0.15m, tipo='gypsum' usa 0.10m.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x1": {"type": "number", "description": "X inicio del eje del muro"},
                "y1": {"type": "number", "description": "Y inicio del eje del muro"},
                "x2": {"type": "number", "description": "X final del eje del muro"},
                "y2": {"type": "number", "description": "Y final del eje del muro"},
                "espesor": {"type": "number", "description": "Espesor en metros (default segun tipo)"},
                "tipo": {"type": "string", "description": "'bloque' (0.15m) o 'gypsum' (0.10m)"},
                "capa": {"type": "string", "description": "Capa (default A-MURO)"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
    },
    {
        "name": "dibujar_rectangulo",
        "description": "Dibuja un rectangulo (polilinea cerrada). x,y es esquina inferior izquierda.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "ancho": {"type": "number"}, "alto": {"type": "number"},
                "capa": {"type": "string"},
            },
            "required": ["x", "y", "ancho", "alto", "capa"],
        },
    },
    {
        "name": "dibujar_linea",
        "description": "Dibuja una linea entre dos puntos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x1": {"type": "number"}, "y1": {"type": "number"},
                "x2": {"type": "number"}, "y2": {"type": "number"},
                "capa": {"type": "string"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
    },
    {
        "name": "dibujar_polilinea",
        "description": "Dibuja polilinea 2D por lista de puntos [{x,y}, ...].",
        "input_schema": {
            "type": "object",
            "properties": {
                "puntos": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}, "required": ["x", "y"]},
                },
                "capa": {"type": "string"},
            },
            "required": ["puntos", "capa"],
        },
    },
    {
        "name": "dibujar_circulo",
        "description": "Dibuja un circulo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "radio": {"type": "number"}, "capa": {"type": "string"},
            },
            "required": ["x", "y", "radio", "capa"],
        },
    },
    {
        "name": "insertar_texto",
        "description": "Inserta texto/etiqueta en el plano. Siempre MAYUSCULAS.",
        "input_schema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
                "altura": {"type": "number", "description": "Altura del texto (default 0.20)"},
                "capa": {"type": "string"},
            },
            "required": ["texto", "x", "y"],
        },
    },
    {
        "name": "crear_capa",
        "description": "Crea o actualiza una capa con nombre y color.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string"},
                "color": {"type": "integer"},
            },
            "required": ["nombre", "color"],
        },
    },
    {
        "name": "agregar_puerta",
        "description": "Inserta puerta (panel + arco). Exterior=1.10m, Interior=1.00m. x,y es punto de bisagra.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "ancho": {"type": "number", "description": "Ancho del vano (1.00 o 1.10)"},
                "rotacion": {"type": "number", "description": "Grados (0=abre +X, 90=abre +Y)"},
                "capa": {"type": "string"},
            },
            "required": ["x", "y", "ancho", "rotacion"],
        },
    },
    {
        "name": "agregar_ventana",
        "description": "Inserta ventana (3 lineas paralelas + jambas). x,y es centro del vano.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "ancho": {"type": "number"}, "rotacion": {"type": "number"},
                "capa": {"type": "string"},
            },
            "required": ["x", "y", "ancho", "rotacion"],
        },
    },
    {
        "name": "agregar_columna",
        "description": "Inserta columna estructural en x,y (centro).",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "ancho": {"type": "number"}, "alto": {"type": "number"},
                "capa": {"type": "string"},
            },
            "required": ["x", "y", "ancho", "alto"],
        },
    },
    {
        "name": "aplicar_estandares_capas",
        "description": "Crea el set completo de capas estandar del estudio (A-MURO, A-PUERTA, A-VENTANA, etc.).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "insertar_norte",
        "description": "Coloca simbolo de norte (circulo + flecha + N).",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "radio": {"type": "number"}, "capa": {"type": "string"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "insertar_escala",
        "description": "Coloca barra de escala grafica y texto 1:X.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "escala": {"type": "integer"},
                "longitud_barra": {"type": "number"},
                "capa": {"type": "string"},
            },
            "required": ["x", "y", "escala"],
        },
    },
    {
        "name": "cotar_planta",
        "description": "Genera cotas alineadas en 4 lados de la planta. x,y = esquina inferior izquierda.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"}, "y": {"type": "number"},
                "ancho": {"type": "number"}, "alto": {"type": "number"},
                "offset": {"type": "number", "description": "Distancia cota-muro (default 0.80)"},
                "capa": {"type": "string"},
            },
            "required": ["x", "y", "ancho", "alto"],
        },
    },
    {
        "name": "leer_info_plano",
        "description": "Lee info general del plano activo (nombre, ruta, unidades, cantidad de objetos).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "listar_capas",
        "description": "Lista todas las capas del plano con nombre, color y visibilidad.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "listar_objetos_por_capa",
        "description": "Lista objetos de una capa con tipo y handle.",
        "input_schema": {
            "type": "object",
            "properties": {"capa": {"type": "string"}},
            "required": ["capa"],
        },
    },
    {
        "name": "zoom_todo",
        "description": "Hace zoom extents para ver todo el dibujo.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "consultar_normativa",
        "description": (
            "Busca informacion en los documentos de normativa del estudio (Código Urbano CR, Ley 7600, etc.). "
            "Usa esta herramienta cuando necesites: areas minimas, retiros, alturas, accesibilidad, "
            "requisitos de ventilacion/iluminacion, o cualquier dato del codigo de construccion. "
            "Devuelve solo las secciones relevantes al tema consultado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tema": {
                    "type": "string",
                    "description": "Tema a buscar. Ej: 'areas minimas dormitorio', 'retiro frontal', 'rampa accesibilidad', 'ventanas iluminacion'",
                },
            },
            "required": ["tema"],
        },
    },
]

TOOLS_ESENCIALES = {"crear_grid_diseno", "colocar_recinto_en_grid", "quitar_recinto_de_grid",
                    "validar_grid", "ver_grid_ascii", "ver_preview_grid", "info_grid",
                    "dibujar_desde_grid", "agregar_puerta_entre_recintos",
                    "agregar_puerta_exterior", "consultar_normativa", "zoom_todo"}

TOOLS_SCHEMA_COMPACTO = [t for t in TOOLS_SCHEMA if t["name"] in TOOLS_ESENCIALES]

# Schema ultra-compacto para proveedores con límite bajo de tokens (Groq free tier)
TOOLS_SCHEMA_MINI = [
    {"name": "crear_grid_diseno", "description": "Inicia grid. ancho_m y alto_m en metros.",
     "input_schema": {"type": "object", "properties": {
         "ancho_m": {"type": "number"}, "alto_m": {"type": "number"},
         "norte": {"type": "string"}, "calle": {"type": "string"}},
     "required": ["ancho_m", "alto_m"]}},
    {"name": "colocar_recinto_en_grid", "description": "Coloca recinto. fila/col desde 0 (fila 0=arriba). ancho_celdas y alto_celdas en celdas de 1m.",
     "input_schema": {"type": "object", "properties": {
         "nombre": {"type": "string"}, "fila": {"type": "integer"}, "col": {"type": "integer"},
         "ancho_celdas": {"type": "integer"}, "alto_celdas": {"type": "integer"}},
     "required": ["nombre", "fila", "col", "ancho_celdas", "alto_celdas"]}},
    {"name": "quitar_recinto_de_grid", "description": "Elimina recinto del grid.",
     "input_schema": {"type": "object", "properties": {"nombre": {"type": "string"}}, "required": ["nombre"]}},
    {"name": "validar_grid", "description": "Valida areas minimas y normativa. Ejecutar antes de dibujar.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "ver_grid_ascii", "description": "Muestra el grid en ASCII para revisar la distribución.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "dibujar_desde_grid", "description": "FASE 2: dibuja en AutoCAD. Solo si validar_grid no tiene errores.",
     "input_schema": {"type": "object", "properties": {
         "origen_x": {"type": "number"}, "origen_y": {"type": "number"}}}},
    {"name": "agregar_puerta_entre_recintos", "description": "Puerta entre dos recintos adyacentes. No inventes coordenadas.",
     "input_schema": {"type": "object", "properties": {
         "recinto1": {"type": "string"}, "recinto2": {"type": "string"},
         "ancho": {"type": "number"}},
     "required": ["recinto1", "recinto2"]}},
    {"name": "agregar_puerta_exterior", "description": "Puerta al exterior. lado: sur/norte/este/oeste.",
     "input_schema": {"type": "object", "properties": {
         "recinto": {"type": "string"}, "lado": {"type": "string"}, "ancho": {"type": "number"}},
     "required": ["recinto", "lado"]}},
    {"name": "zoom_todo", "description": "Zoom extents en AutoCAD.",
     "input_schema": {"type": "object", "properties": {}}},
]
