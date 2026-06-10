import base64
import io
import customtkinter as ctk
import json
import os
import sys
import threading
import time
from datetime import datetime
from tkinter import filedialog

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from modulos.autocad.ejecutor import EjecutorAutoCAD, TOOLS_SCHEMA, TOOLS_SCHEMA_COMPACTO, TOOLS_SCHEMA_MINI
from modulos.ia.proveedores import ClienteIA, PROVEEDORES

# ─── SLE — Spatial Learning Engine (opcional, no rompe la app si falta) ───
try:
    from sle.core.memory import Memoria as SLEMemoria
    from sle.integration.correction_capture import CapturaCorrecciones
    from sle.core.rag_context import obtener_contexto_rag
    _SLE_DISPONIBLE = True
except ImportError:
    _SLE_DISPONIBLE = False

# ─── Diagrama Conceptual — Paso 0 (opcional, no rompe la app si falta) ───
try:
    from sle.core.diagrama_conceptual import (
        ciclo_completo, aplicar_jerarquia_diagrama,
        desde_dict, prompt_lectura_imagen,
    )
    from sle.core.reglas_merlos import texto_analisis_merlos
    from sle.core.programa_arquitectonico import texto_programa
    from sle.core.diagrama_conceptual import texto_diagrama
    _DIAGRAMA_DISPONIBLE = True
except ImportError:
    _DIAGRAMA_DISPONIBLE = False

CONFIG_PATH = os.path.join(BASE_DIR, "config", "settings.json")
CONOCIMIENTO_PATH = os.path.join(BASE_DIR, "config", "conocimiento.json")
NORMATIVA_DIR = os.path.join(BASE_DIR, "config", "normativa")
NORMATIVA_INDEX_PATH = os.path.join(BASE_DIR, "config", "normativa_index.json")

ACCENT = "#2563EB"
ACCENT_HOVER = "#1D4ED8"
SUCCESS = "#16A34A"
WARNING = "#EAB308"
ERROR = "#DC2626"
BG_DARK = "#0F172A"
BG_PANEL = "#1E293B"
BG_CARD = "#334155"
TEXT_PRIMARY = "#F8FAFC"
TEXT_SECONDARY = "#94A3B8"
BORDER = "#475569"

SYSTEM_PROMPT_BASE = """\
Eres el núcleo arquitectónico Neural CAD del Estudio Merlos, Costa Rica.
Tienes herramientas AutoCAD. Tu función NO es conversar — es producir soluciones espaciales coherentes.

# MENTALIDAD ARQUITECTÓNICA

Piensa como arquitecto antes de como sistema CAD:
- ZONAS: pública (sala/cocina/comedor) abajo (calle) · privada (dormitorios/baños) arriba (norte)
- CIRCULACIÓN: pasillo como eje de transición entre zonas — nunca dormitorios que cruce la sala
- PROPORCIONES: dormitorio mín 3×3m · baño mín 1.5×2m · pasillo mín 1.2m · sala proporcional al proyecto
- HUMEDAD: baños y cocina en el mismo lateral (tuberías compartidas)
- ILUMINACIÓN: sala y dormitorios con acceso a muro exterior

# FLUJO OBLIGATORIO

## FASE 1 — PLANIFICACIÓN EN GRID

1. crear_grid_diseno (casa ~100m² → 10×10m, lote angosto → 8×14m, etc.)
2. colocar_recinto_en_grid para CADA recinto — NUNCA superpongas celdas
   - fila 0 = NORTE/ARRIBA | fila máx = CALLE/ABAJO
   - CADA recinto ocupa [fila..fila+alto-1] × [col..col+ancho-1]
3. validar_grid — corrige normativa INVU/CFIA Costa Rica
4. Solo cuando ok:true → pasar a FASE 2

## AUTO-CORRECCIÓN OBLIGATORIA

Si colocar_recinto_en_grid devuelve "error":
- Lee "sugerencia" → úsala directamente
- NUNCA repitas la posición fallida

## FASE 2 — DIBUJO EN AUTOCAD

5. dibujar_desde_grid — muros + vanos + etiquetas automáticas
6. Las puertas se generan automáticamente con los vanos. No uses agregar_puerta() manual.

# PUERTAS POR DEFECTO

COCHERA ↔ exterior sur (1.10m) + conexión interior a sala o pasillo
DORMITORIOS ↔ pasillo o zona de circulación (1.00m)
BAÑO ↔ pasillo o dormitorio (0.90m)
COCINA ↔ comedor (1.00m)

Ejecuta directamente. Sin descripciones. Sin explicaciones.
"""


def cargar_normativa_index() -> list:
    try:
        with open(NORMATIVA_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def guardar_normativa_index(data: list):
    with open(NORMATIVA_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def cargar_normativa_activa() -> str:
    index = cargar_normativa_index()
    texto = ""
    for item in index:
        if not item.get("activo", True):
            continue
        ruta = os.path.join(NORMATIVA_DIR, item["archivo"])
        if os.path.exists(ruta):
            with open(ruta, "r", encoding="utf-8") as f:
                contenido = f.read()
            texto += f"\n\n### {item['titulo']}\n{contenido}"
    return texto


def cargar_conocimiento() -> dict:
    try:
        with open(CONOCIMIENTO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"reglas": [], "lecciones_aprendidas": []}


def guardar_conocimiento(data: dict):
    os.makedirs(os.path.dirname(CONOCIMIENTO_PATH), exist_ok=True)
    with open(CONOCIMIENTO_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def construir_system_prompt() -> str:
    conocimiento = cargar_conocimiento()
    reglas_activas = [r for r in conocimiento.get("reglas", []) if r.get("activo", True)]

    secciones = {}
    for r in reglas_activas:
        cat = r.get("categoria", "General")
        if cat not in secciones:
            secciones[cat] = []
        secciones[cat].append(f"- {r['titulo']}: {r['instruccion']}")

    texto_reglas = ""
    for cat, items in secciones.items():
        texto_reglas += f"\n## {cat}\n"
        texto_reglas += "\n".join(items) + "\n"

    lecciones = conocimiento.get("lecciones_aprendidas", [])
    texto_lecciones = ""
    if lecciones:
        texto_lecciones = "\n## LECCIONES APRENDIDAS (errores pasados, NO repetir)\n"
        for l in lecciones:
            texto_lecciones += f"- {l['titulo']}: {l['instruccion']}\n"

    index = cargar_normativa_index()
    docs_activos = [i["titulo"] for i in index if i.get("activo", True)]
    texto_normativa = ""
    if docs_activos:
        lista = ", ".join(docs_activos)
        texto_normativa = (
            f"\n\n# NORMATIVA DISPONIBLE\n"
            f"Tienes acceso a estos documentos: {lista}.\n"
            f"Cuando necesites datos normativos (areas, retiros, accesibilidad, alturas, etc.) "
            f"usa la herramienta `consultar_normativa(tema)` para buscar la informacion exacta."
        )

    return SYSTEM_PROMPT_BASE + "\n\n# REGLAS DEL ESTUDIO\n" + texto_reglas + texto_lecciones + texto_normativa


def cargar_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "anthropic_api_key": "",
            "modelo": "claude-sonnet-4-20250514",
            "mcp_autocad_path": "",
            "tema": "dark",
            "idioma": "es",
        }


def guardar_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


class DialogoConfigIA(ctk.CTkToplevel):
    def __init__(self, parent, config: dict):
        super().__init__(parent)
        self.title("Configurar Proveedor de IA")
        self.geometry("560x520")
        self.resizable(False, False)
        self.configure(fg_color=BG_DARK)
        self.transient(parent)
        self.grab_set()
        self.resultado = None
        self.config = config.copy()

        ctk.CTkLabel(
            self, text="Proveedor de IA",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=TEXT_PRIMARY,
        ).pack(pady=(25, 5))

        ctk.CTkLabel(
            self, text="Selecciona el proveedor y modelo a usar",
            font=ctk.CTkFont(size=13), text_color=TEXT_SECONDARY,
        ).pack(pady=(0, 15))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=35)

        ctk.CTkLabel(form, text="Proveedor:", font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(anchor="w")
        nombres_proveedores = [PROVEEDORES[k]["nombre"] for k in PROVEEDORES]
        self._ids_proveedores = list(PROVEEDORES.keys())
        proveedor_actual = config.get("proveedor", "anthropic")
        nombre_actual = PROVEEDORES.get(proveedor_actual, PROVEEDORES["anthropic"])["nombre"]

        self.combo_proveedor = ctk.CTkComboBox(
            form, values=nombres_proveedores, height=38,
            fg_color=BG_CARD, border_color=BORDER, text_color=TEXT_PRIMARY,
            button_color=ACCENT, button_hover_color=ACCENT_HOVER, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, dropdown_hover_color=BORDER,
            font=ctk.CTkFont(size=13), command=self._on_proveedor_change,
        )
        self.combo_proveedor.set(nombre_actual)
        self.combo_proveedor.pack(fill="x", pady=(2, 12))

        ctk.CTkLabel(form, text="Modelo:", font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(anchor="w")
        self.combo_modelo = ctk.CTkComboBox(
            form, values=[], height=38,
            fg_color=BG_CARD, border_color=BORDER, text_color=TEXT_PRIMARY,
            button_color=ACCENT, button_hover_color=ACCENT_HOVER, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, dropdown_hover_color=BORDER,
            font=ctk.CTkFont(size=13),
        )
        self.combo_modelo.pack(fill="x", pady=(2, 12))

        ctk.CTkLabel(form, text="API Key:", font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(anchor="w")
        self.entry_key = ctk.CTkEntry(
            form, height=38, placeholder_text="sk-...",
            fg_color=BG_CARD, border_color=BORDER, text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=13),
        )
        self.entry_key.pack(fill="x", pady=(2, 6))

        self.lbl_key_info = ctk.CTkLabel(
            form, text="", font=ctk.CTkFont(size=11), text_color=TEXT_SECONDARY, anchor="w",
        )
        self.lbl_key_info.pack(anchor="w", pady=(0, 12))

        ctk.CTkLabel(form, text="URL personalizada (opcional):", font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(anchor="w")
        self.entry_url = ctk.CTkEntry(
            form, height=38, placeholder_text="https://api.ejemplo.com/v1",
            fg_color=BG_CARD, border_color=BORDER, text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=13),
        )
        self.entry_url.pack(fill="x", pady=(2, 20))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack()

        ctk.CTkButton(
            btn_frame, text="Guardar", width=140, height=38,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._guardar,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            btn_frame, text="Cancelar", width=140, height=38,
            fg_color=BG_CARD, hover_color=BORDER,
            font=ctk.CTkFont(size=14),
            command=self.destroy,
        ).pack(side="left", padx=10)

        self._on_proveedor_change(nombre_actual)
        # SEC-01: leer API key desde keyring (Windows Credential Manager), no desde config
        try:
            import keyring as _kr
            key_guardada = _kr.get_password("estudio-merlos-ai", proveedor_actual) or ""
        except Exception:
            key_guardada = ""
        if not key_guardada:
            # fallback: key antigua en texto plano (se migrará al guardar)
            key_guardada = config.get("api_keys", {}).get(proveedor_actual, config.get("anthropic_api_key", ""))
        if key_guardada:
            self.entry_key.insert(0, key_guardada)
        url_guardada = config.get("urls_custom", {}).get(proveedor_actual, "")
        if url_guardada:
            self.entry_url.insert(0, url_guardada)

    def _get_proveedor_id(self) -> str:
        nombre = self.combo_proveedor.get()
        for pid, info in PROVEEDORES.items():
            if info["nombre"] == nombre:
                return pid
        return "anthropic"

    def _on_proveedor_change(self, nombre):
        pid = self._get_proveedor_id()
        info = PROVEEDORES[pid]

        self.combo_modelo.configure(values=info["modelos"])
        modelo_guardado = self.config.get("modelos_seleccionados", {}).get(pid, info["modelos"][0])
        if modelo_guardado in info["modelos"]:
            self.combo_modelo.set(modelo_guardado)
        else:
            self.combo_modelo.set(info["modelos"][0])

        if not info["requiere_key"]:
            self.entry_key.delete(0, "end")
            self.entry_key.configure(placeholder_text="No requiere API key")
            self.lbl_key_info.configure(text="Este proveedor es local y gratuito", text_color=SUCCESS)
        else:
            self.entry_key.configure(placeholder_text="sk-...")
            if pid == "groq":
                self.lbl_key_info.configure(text="Gratis en console.groq.com — 30 req/min", text_color=SUCCESS)
            elif pid == "openrouter":
                self.lbl_key_info.configure(text="Modelos gratis disponibles en openrouter.ai", text_color=SUCCESS)
            elif pid == "deepseek":
                self.lbl_key_info.configure(text="Muy barato en platform.deepseek.com", text_color=SUCCESS)
            elif pid == "anthropic":
                self.lbl_key_info.configure(text="console.anthropic.com", text_color=TEXT_SECONDARY)
            else:
                self.lbl_key_info.configure(text="", text_color=TEXT_SECONDARY)

        if info["base_url"]:
            self.entry_url.delete(0, "end")
            self.entry_url.insert(0, info["base_url"])
        else:
            self.entry_url.delete(0, "end")

        # SEC-01: leer desde keyring
        try:
            import keyring as _kr
            key_guardada = _kr.get_password("estudio-merlos-ai", pid) or ""
        except Exception:
            key_guardada = ""
        if not key_guardada:
            key_guardada = self.config.get("api_keys", {}).get(pid, "")
        if pid == "anthropic" and not key_guardada:
            key_guardada = self.config.get("anthropic_api_key", "")
        self.entry_key.delete(0, "end")
        if key_guardada:
            self.entry_key.insert(0, key_guardada)

    def _guardar(self):
        pid = self._get_proveedor_id()
        info = PROVEEDORES[pid]
        key = self.entry_key.get().strip()

        if info["requiere_key"] and not key:
            self.lbl_key_info.configure(text="API Key requerida para este proveedor", text_color=ERROR)
            return

        if "api_keys" not in self.config:
            self.config["api_keys"] = {}
        if "modelos_seleccionados" not in self.config:
            self.config["modelos_seleccionados"] = {}
        if "urls_custom" not in self.config:
            self.config["urls_custom"] = {}

        self.config["proveedor"] = pid
        # SEC-01: guardar API key en keyring (Windows Credential Manager), NO en config
        try:
            import keyring as _kr
            _kr.set_password("estudio-merlos-ai", pid, key)
        except Exception:
            pass
        # Limpiar cualquier key en texto plano que pudiera quedar
        self.config.get("api_keys", {}).pop(pid, None)
        self.config.pop("anthropic_api_key", None)
        self.config["modelo"] = self.combo_modelo.get()
        self.config["modelos_seleccionados"][pid] = self.combo_modelo.get()

        url_custom = self.entry_url.get().strip()
        if url_custom:
            self.config["urls_custom"][pid] = url_custom

        self.resultado = self.config
        self.destroy()


class VentanaConocimiento(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Base de Conocimiento — Estudio Merlos AI")
        self.geometry("900x700")
        self.minsize(750, 500)
        self.configure(fg_color=BG_DARK)
        self.transient(parent)
        self.grab_set()

        self.conocimiento = cargar_conocimiento()
        self._construir_ui()

    def _construir_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(15, 5))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="Base de Conocimiento",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="Estas reglas se inyectan en cada llamada a Claude. Editalas para ensenarle.",
            font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.grid(row=0, column=2, sticky="e")

        ctk.CTkButton(
            btn_frame, text="+ Regla", width=90, height=32,
            fg_color=SUCCESS, hover_color="#15803D",
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=self._agregar_regla,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="+ Leccion", width=100, height=32,
            fg_color=WARNING, hover_color="#CA8A04", text_color=BG_DARK,
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=self._agregar_leccion,
        ).pack(side="left", padx=4)

        self.scroll = ctk.CTkScrollableFrame(
            self, fg_color=BG_PANEL, corner_radius=12,
        )
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        self.scroll.grid_columnconfigure(0, weight=1)

        self._renderizar_lista()

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 15))
        bottom.grid_columnconfigure(0, weight=1)

        self.lbl_status = ctk.CTkLabel(
            bottom, text="",
            font=ctk.CTkFont(size=11), text_color=SUCCESS, anchor="w",
        )
        self.lbl_status.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            bottom, text="Guardar Todo", width=140, height=38,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=14, weight="bold"), corner_radius=8,
            command=self._guardar,
        ).grid(row=0, column=1, sticky="e")

    def _renderizar_lista(self):
        for widget in self.scroll.winfo_children():
            widget.destroy()

        if self.conocimiento.get("reglas"):
            ctk.CTkLabel(
                self.scroll, text="REGLAS DEL ESTUDIO",
                font=ctk.CTkFont(size=13, weight="bold"), text_color=ACCENT, anchor="w",
            ).pack(fill="x", padx=10, pady=(10, 5))

            for i, regla in enumerate(self.conocimiento["reglas"]):
                self._crear_card_regla(regla, i, "regla")

        if self.conocimiento.get("lecciones_aprendidas"):
            ctk.CTkLabel(
                self.scroll, text="LECCIONES APRENDIDAS",
                font=ctk.CTkFont(size=13, weight="bold"), text_color=WARNING, anchor="w",
            ).pack(fill="x", padx=10, pady=(15, 5))

            for i, leccion in enumerate(self.conocimiento["lecciones_aprendidas"]):
                self._crear_card_regla(leccion, i, "leccion")

    def _crear_card_regla(self, regla: dict, indice: int, tipo: str):
        card = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=8)
        card.pack(fill="x", padx=8, pady=3)
        card.grid_columnconfigure(1, weight=1)

        activo = regla.get("activo", True)
        color_borde = SUCCESS if activo else ERROR

        indicador = ctk.CTkFrame(card, width=4, fg_color=color_borde, corner_radius=2)
        indicador.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(8, 6), pady=8)

        header_row = ctk.CTkFrame(card, fg_color="transparent")
        header_row.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(8, 2))
        header_row.grid_columnconfigure(0, weight=1)

        cat = regla.get("categoria", "General")
        titulo = regla.get("titulo", "")
        ctk.CTkLabel(
            header_row, text=f"[{cat}] {titulo}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT_PRIMARY if activo else TEXT_SECONDARY, anchor="w",
        ).grid(row=0, column=0, sticky="w")

        btn_row = ctk.CTkFrame(header_row, fg_color="transparent")
        btn_row.grid(row=0, column=1, sticky="e")

        toggle_text = "ON" if activo else "OFF"
        toggle_color = SUCCESS if activo else ERROR
        ctk.CTkButton(
            btn_row, text=toggle_text, width=42, height=24,
            fg_color=toggle_color, hover_color=BORDER,
            font=ctk.CTkFont(size=10, weight="bold"), corner_radius=4,
            command=lambda t=tipo, idx=indice: self._toggle_regla(t, idx),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btn_row, text="Editar", width=50, height=24,
            fg_color=BG_PANEL, hover_color=BORDER,
            font=ctk.CTkFont(size=10), corner_radius=4,
            command=lambda t=tipo, idx=indice: self._editar_regla(t, idx),
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            btn_row, text="X", width=28, height=24,
            fg_color=ERROR, hover_color="#B91C1C",
            font=ctk.CTkFont(size=10, weight="bold"), corner_radius=4,
            command=lambda t=tipo, idx=indice: self._eliminar_regla(t, idx),
        ).pack(side="left", padx=2)

        instruccion = regla.get("instruccion", "")
        ctk.CTkLabel(
            card, text=instruccion, wraplength=700,
            font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY, anchor="w",
            justify="left",
        ).grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 8))

    def _toggle_regla(self, tipo: str, indice: int):
        lista = "reglas" if tipo == "regla" else "lecciones_aprendidas"
        self.conocimiento[lista][indice]["activo"] = not self.conocimiento[lista][indice].get("activo", True)
        self._renderizar_lista()

    def _eliminar_regla(self, tipo: str, indice: int):
        lista = "reglas" if tipo == "regla" else "lecciones_aprendidas"
        del self.conocimiento[lista][indice]
        self._renderizar_lista()

    def _editar_regla(self, tipo: str, indice: int):
        lista = "reglas" if tipo == "regla" else "lecciones_aprendidas"
        regla = self.conocimiento[lista][indice]
        dialogo = DialogoRegla(self, regla, titulo_ventana="Editar Regla")
        self.wait_window(dialogo)
        if dialogo.resultado:
            self.conocimiento[lista][indice] = dialogo.resultado
            self._renderizar_lista()

    def _agregar_regla(self):
        nueva = {
            "id": f"regla_{int(time.time())}",
            "categoria": "General",
            "titulo": "",
            "instruccion": "",
            "activo": True,
        }
        dialogo = DialogoRegla(self, nueva, titulo_ventana="Nueva Regla")
        self.wait_window(dialogo)
        if dialogo.resultado:
            self.conocimiento["reglas"].append(dialogo.resultado)
            self._renderizar_lista()

    def _agregar_leccion(self):
        nueva = {
            "id": f"leccion_{int(time.time())}",
            "categoria": "Leccion",
            "titulo": "",
            "instruccion": "",
            "activo": True,
        }
        dialogo = DialogoRegla(self, nueva, titulo_ventana="Nueva Leccion Aprendida")
        self.wait_window(dialogo)
        if dialogo.resultado:
            if "lecciones_aprendidas" not in self.conocimiento:
                self.conocimiento["lecciones_aprendidas"] = []
            self.conocimiento["lecciones_aprendidas"].append(dialogo.resultado)
            self._renderizar_lista()

    def _guardar(self):
        guardar_conocimiento(self.conocimiento)
        self.lbl_status.configure(text="Guardado correctamente")
        self.after(3000, lambda: self.lbl_status.configure(text=""))


class DialogoRegla(ctk.CTkToplevel):
    def __init__(self, parent, regla: dict, titulo_ventana: str = "Regla"):
        super().__init__(parent)
        self.title(titulo_ventana)
        self.geometry("600x420")
        self.resizable(False, False)
        self.configure(fg_color=BG_DARK)
        self.transient(parent)
        self.grab_set()
        self.resultado = None
        self.regla = regla.copy()

        ctk.CTkLabel(
            self, text=titulo_ventana,
            font=ctk.CTkFont(size=18, weight="bold"), text_color=TEXT_PRIMARY,
        ).pack(pady=(20, 15))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=30)

        ctk.CTkLabel(form, text="Categoria:", font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(anchor="w")
        self.entry_cat = ctk.CTkEntry(
            form, height=34, fg_color=BG_CARD, border_color=BORDER, text_color=TEXT_PRIMARY,
        )
        self.entry_cat.pack(fill="x", pady=(2, 10))
        self.entry_cat.insert(0, regla.get("categoria", ""))

        ctk.CTkLabel(form, text="Titulo:", font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(anchor="w")
        self.entry_titulo = ctk.CTkEntry(
            form, height=34, fg_color=BG_CARD, border_color=BORDER, text_color=TEXT_PRIMARY,
        )
        self.entry_titulo.pack(fill="x", pady=(2, 10))
        self.entry_titulo.insert(0, regla.get("titulo", ""))

        ctk.CTkLabel(form, text="Instruccion:", font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY).pack(anchor="w")
        self.txt_instruccion = ctk.CTkTextbox(
            form, height=120, fg_color=BG_CARD, border_color=BORDER, border_width=1,
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=13), wrap="word",
        )
        self.txt_instruccion.pack(fill="x", pady=(2, 15))
        self.txt_instruccion.insert("1.0", regla.get("instruccion", ""))

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack()

        ctk.CTkButton(
            btn_frame, text="Guardar", width=140, height=38,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._guardar,
        ).pack(side="left", padx=10)

        ctk.CTkButton(
            btn_frame, text="Cancelar", width=140, height=38,
            fg_color=BG_CARD, hover_color=BORDER,
            font=ctk.CTkFont(size=14),
            command=self.destroy,
        ).pack(side="left", padx=10)

        self.entry_titulo.focus_set()

    def _guardar(self):
        titulo = self.entry_titulo.get().strip()
        instruccion = self.txt_instruccion.get("1.0", "end").strip()
        if not titulo or not instruccion:
            return
        self.regla["categoria"] = self.entry_cat.get().strip() or "General"
        self.regla["titulo"] = titulo
        self.regla["instruccion"] = instruccion
        self.resultado = self.regla
        self.destroy()


class VentanaNormativa(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Normativa de Referencia")
        self.geometry("860x640")
        self.minsize(700, 480)
        self.configure(fg_color=BG_DARK)
        self.transient(parent)
        self.grab_set()
        self.index = cargar_normativa_index()
        self._construir_ui()

    def _construir_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(15, 5))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="Normativa de Referencia",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="Los documentos activos se inyectan en cada consulta a la IA.",
            font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.grid(row=0, column=2, sticky="e")

        ctk.CTkButton(
            btn_frame, text="+ Agregar .md", width=120, height=32,
            fg_color=SUCCESS, hover_color="#15803D",
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=self._agregar_md,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="Resumir PDF/Doc", width=130, height=32,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=self._resumir_documento,
        ).pack(side="left", padx=4)

        self.scroll = ctk.CTkScrollableFrame(self, fg_color=BG_PANEL, corner_radius=12)
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        self.scroll.grid_columnconfigure(0, weight=1)

        self._renderizar()

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 15))
        bottom.grid_columnconfigure(0, weight=1)

        self.lbl_status = ctk.CTkLabel(bottom, text="", font=ctk.CTkFont(size=11), text_color=SUCCESS, anchor="w")
        self.lbl_status.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            bottom, text="Guardar", width=130, height=38,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=14, weight="bold"), corner_radius=8,
            command=self._guardar,
        ).grid(row=0, column=1, sticky="e")

    def _renderizar(self):
        for w in self.scroll.winfo_children():
            w.destroy()

        if not self.index:
            ctk.CTkLabel(
                self.scroll, text="No hay documentos cargados.",
                font=ctk.CTkFont(size=13), text_color=TEXT_SECONDARY,
            ).pack(pady=30)
            return

        for i, item in enumerate(self.index):
            card = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=10)
            card.pack(fill="x", padx=8, pady=5)
            card.grid_columnconfigure(1, weight=1)

            var = ctk.BooleanVar(value=item.get("activo", True))

            def _toggle(v=var, idx=i):
                self.index[idx]["activo"] = v.get()

            ctk.CTkSwitch(
                card, text="", variable=var, command=_toggle,
                width=44, progress_color=SUCCESS,
            ).grid(row=0, column=0, rowspan=2, padx=(12, 8), pady=12)

            ctk.CTkLabel(
                card, text=item["titulo"],
                font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_PRIMARY, anchor="w",
            ).grid(row=0, column=1, sticky="w", padx=4, pady=(10, 0))

            ctk.CTkLabel(
                card, text=item.get("descripcion", item["archivo"]),
                font=ctk.CTkFont(size=11), text_color=TEXT_SECONDARY, anchor="w",
            ).grid(row=1, column=1, sticky="w", padx=4, pady=(0, 10))

            ctk.CTkLabel(
                card, text=item["archivo"],
                font=ctk.CTkFont(size=10), text_color=BORDER, anchor="e",
            ).grid(row=0, column=2, padx=12, pady=(10, 0), sticky="e")

            ctk.CTkButton(
                card, text="Eliminar", width=80, height=26,
                fg_color="transparent", border_color=ERROR, border_width=1,
                text_color=ERROR, hover_color="#7F1D1D",
                font=ctk.CTkFont(size=11), corner_radius=6,
                command=lambda idx=i: self._eliminar(idx),
            ).grid(row=1, column=2, padx=12, pady=(0, 10), sticky="e")

    def _agregar_md(self):
        from tkinter import filedialog
        ruta = filedialog.askopenfilename(
            title="Seleccionar documento .md",
            filetypes=[("Markdown", "*.md"), ("Todos", "*.*")],
        )
        if not ruta:
            return
        import shutil
        nombre = os.path.basename(ruta)
        destino = os.path.join(NORMATIVA_DIR, nombre)
        os.makedirs(NORMATIVA_DIR, exist_ok=True)
        shutil.copy2(ruta, destino)

        if not any(item["archivo"] == nombre for item in self.index):
            self.index.append({
                "archivo": nombre,
                "titulo": nombre.replace("_", " ").replace(".md", "").title(),
                "descripcion": "Documento importado",
                "activo": True,
            })
        self._renderizar()
        self.lbl_status.configure(text=f"Importado: {nombre}")

    def _resumir_documento(self):
        from tkinter import filedialog
        ruta = filedialog.askopenfilename(
            title="Seleccionar documento a resumir",
            filetypes=[
                ("Documentos", "*.pdf *.docx *.doc *.txt *.md"),
                ("PDF", "*.pdf"),
                ("Word", "*.docx *.doc"),
                ("Texto", "*.txt *.md"),
                ("Todos", "*.*"),
            ],
        )
        if not ruta:
            return

        cliente = getattr(self.master, "cliente_ia", None)
        if not cliente:
            from tkinter import messagebox
            messagebox.showerror("Sin IA", "Configura primero un proveedor de IA en Configuración.")
            return

        self.lbl_status.configure(text="Extrayendo texto del documento...", text_color=WARNING)
        self.update()

        import threading
        threading.Thread(target=self._tarea_resumir, args=(ruta, cliente), daemon=True).start()

    def _tarea_resumir(self, ruta: str, cliente):
        try:
            texto = self._extraer_texto(ruta)
            if not texto.strip():
                self.after(0, self.lbl_status.configure, {"text": "No se pudo extraer texto del documento.", "text_color": ERROR})
                return

            nombre_base = os.path.splitext(os.path.basename(ruta))[0]
            self.after(0, self.lbl_status.configure, {"text": f"Resumiendo con IA ({len(texto):,} caracteres)...", "text_color": WARNING})

            system = (
                "Eres un experto en normativa arquitectónica y construcción en Costa Rica. "
                "Tu tarea es resumir documentos técnicos en formato Markdown estructurado. "
                "El resumen debe ser completo pero compacto: tablas para datos numéricos, "
                "encabezados claros por sección, listas para requisitos. "
                "Conserva TODOS los valores numéricos (medidas, áreas, porcentajes). "
                "Responde SOLO con el contenido Markdown, sin explicaciones adicionales."
            )

            MAX_CHARS = 80000
            if len(texto) > MAX_CHARS:
                partes = [texto[i:i+MAX_CHARS] for i in range(0, len(texto), MAX_CHARS)]
                resumen_final = f"# Resumen: {nombre_base}\n\n"
                for n, parte in enumerate(partes, 1):
                    msgs = [{"role": "user", "content": f"Resume esta parte {n}/{len(partes)} del documento '{nombre_base}':\n\n{parte}"}]
                    resp = cliente.llamar(system, msgs, [])
                    resumen_final += f"\n## Parte {n}\n{resp.texto}\n"
            else:
                msgs = [{"role": "user", "content": f"Resume este documento técnico en Markdown:\n\n**Documento:** {nombre_base}\n\n{texto}"}]
                resp = cliente.llamar(system, msgs, [])
                resumen_final = f"# Resumen: {nombre_base}\n> Generado automáticamente por Estudio Merlos AI\n\n{resp.texto}"

            nombre_md = nombre_base.lower().replace(" ", "_") + ".md"
            destino = os.path.join(NORMATIVA_DIR, nombre_md)
            os.makedirs(NORMATIVA_DIR, exist_ok=True)
            with open(destino, "w", encoding="utf-8") as f:
                f.write(resumen_final)

            if not any(item["archivo"] == nombre_md for item in self.index):
                self.index.append({
                    "archivo": nombre_md,
                    "titulo": nombre_base.replace("_", " ").title(),
                    "descripcion": f"Resumen generado desde {os.path.basename(ruta)}",
                    "activo": True,
                })
            guardar_normativa_index(self.index)
            self.after(0, self._renderizar)
            self.after(0, self.lbl_status.configure, {"text": f"Resumen guardado: {nombre_md}", "text_color": SUCCESS})

        except Exception as e:
            self.after(0, self.lbl_status.configure, {"text": f"Error: {e}", "text_color": ERROR})

    def _extraer_texto(self, ruta: str) -> str:
        ext = os.path.splitext(ruta)[1].lower()
        if ext == ".pdf":
            try:
                import pypdf
                texto = ""
                with open(ruta, "rb") as f:
                    reader = pypdf.PdfReader(f)
                    for page in reader.pages:
                        texto += page.extract_text() or ""
                return texto
            except ImportError:
                return ""
        elif ext in (".docx",):
            try:
                import docx
                doc = docx.Document(ruta)
                return "\n".join(p.text for p in doc.paragraphs)
            except ImportError:
                return ""
        elif ext in (".txt", ".md"):
            with open(ruta, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        return ""

    def _eliminar(self, idx: int):
        item = self.index.pop(idx)
        ruta = os.path.join(NORMATIVA_DIR, item["archivo"])
        if os.path.exists(ruta):
            os.remove(ruta)
        self._renderizar()

    def _guardar(self):
        guardar_normativa_index(self.index)
        self.lbl_status.configure(text="Guardado correctamente")


class VentanaConfirmacionBoceto(ctk.CTkToplevel):
    """
    Diálogo modal que muestra al arquitecto lo que la IA interpretó del boceto.
    Permite confirmar o escribir una corrección antes de dibujar.
    """

    W = 620   # ancho fijo de la ventana
    H = 680   # alto inicial (crece si hay muchos recintos)

    def __init__(self, parent, plan: dict, resultado: dict, evento):
        super().__init__(parent)
        self.title("Verificar interpretación del boceto")
        self.resizable(True, True)
        self.minsize(520, 520)
        self.grab_set()
        self.focus_force()

        self._plan = plan
        self._resultado = resultado
        self._evento = evento

        self._construir_ui()
        self._centrar()
        self.protocol("WM_DELETE_WINDOW", self._cancelar)

    def _centrar(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        # Usar el mayor entre el tamaño calculado y el mínimo definido
        w = max(self.winfo_width(), self.W)
        h = max(self.winfo_height(), self.H)
        x = (sw - w) // 2
        y = max(0, (sh - h) // 2 - 30)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _construir_ui(self):
        plan = self._plan
        g = plan.get("grid", {})
        recintos = plan.get("recintos", [])
        puertas = plan.get("puertas", [])

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)   # la tabla se estira verticalmente

        # ── Header ─────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=BG_PANEL, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hdr,
            text="Verificar interpretación del boceto",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).grid(row=0, column=0, padx=24, pady=(18, 4), sticky="w")

        ancho_m  = g.get("ancho_m", "?")
        alto_m   = g.get("alto_m", "?")
        area_bruta = (ancho_m * alto_m) if isinstance(ancho_m, (int, float)) else "?"

        # Fila de chips: dimensiones + puertas
        puertas_inter = [p for p in puertas if p.get("tipo") == "entre_recintos"]
        puertas_ext   = [p for p in puertas if p.get("tipo") == "exterior"]
        chips_txt = (
            f"Grid: {ancho_m} × {alto_m} m  |  {area_bruta} m² brutos  |  "
            f"{len(puertas_inter)} puertas int.  ·  {len(puertas_ext)} ext."
        )
        ctk.CTkLabel(
            hdr,
            text=chips_txt,
            font=ctk.CTkFont(size=12),
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).grid(row=1, column=0, padx=24, pady=(0, 14), sticky="w")

        ctk.CTkFrame(self, height=1, fg_color=BORDER).grid(row=1, column=0, sticky="ew")

        # ── Cuerpo: tabla de recintos ───────────────────────────
        body = ctk.CTkFrame(self, fg_color=BG_DARK)
        body.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            body,
            text=f"Recintos identificados  ({len(recintos)})",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT_PRIMARY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(14, 6))

        # Tabla scrollable que ocupa todo el espacio disponible
        tabla = ctk.CTkScrollableFrame(
            body,
            fg_color=BG_PANEL,
            corner_radius=0,
        )
        tabla.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        tabla.grid_columnconfigure(0, weight=1)

        area_total = 0.0
        for i, rec in enumerate(recintos):
            ancho_r = rec.get("ancho", rec.get("ancho_celdas", "?"))
            alto_r  = rec.get("alto",  rec.get("alto_celdas",  "?"))
            try:
                area_r = float(ancho_r) * float(alto_r)
                area_total += area_r
                dim_txt  = f"{float(ancho_r):.0f} × {float(alto_r):.0f} m"
                area_txt = f"{area_r:.1f} m²"
            except (TypeError, ValueError):
                dim_txt  = f"{ancho_r} × {alto_r} m"
                area_txt = "? m²"

            # Alternar color de fila
            bg_fila = BG_CARD if i % 2 == 0 else BG_PANEL
            fila_f = ctk.CTkFrame(tabla, fg_color=bg_fila, corner_radius=0, height=36)
            fila_f.grid(row=i, column=0, sticky="ew", padx=0, pady=0)
            fila_f.grid_columnconfigure(0, weight=1)
            fila_f.grid_propagate(False)

            nombre_limpio = rec.get("nombre", "?").replace("_", " ")
            ctk.CTkLabel(
                fila_f,
                text=f"  {nombre_limpio}",
                font=ctk.CTkFont(size=13),
                text_color=TEXT_PRIMARY,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=8)
            ctk.CTkLabel(
                fila_f,
                text=f"{dim_txt}    {area_txt}",
                font=ctk.CTkFont(size=12, family="Consolas"),
                text_color=TEXT_SECONDARY,
                anchor="e",
            ).grid(row=0, column=1, sticky="e", padx=16)

        # Fila de totales (fija, debajo de la tabla)
        totales = ctk.CTkFrame(body, fg_color=BG_PANEL, height=38, corner_radius=0)
        totales.grid(row=2, column=0, sticky="ew")
        totales.grid_columnconfigure(0, weight=1)
        totales.grid_propagate(False)
        ctk.CTkLabel(
            totales,
            text=f"  Total construido: {area_total:.1f} m²",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=SUCCESS, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=8)

        # ── Separador ───────────────────────────────────────────
        ctk.CTkFrame(self, height=1, fg_color=BORDER).grid(row=3, column=0, sticky="ew")

        # ── Campo de corrección ─────────────────────────────────
        corr_frame = ctk.CTkFrame(self, fg_color=BG_DARK)
        corr_frame.grid(row=4, column=0, sticky="ew", padx=24, pady=(16, 0))
        corr_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            corr_frame,
            text="Corrección del arquitecto   (dejar vacío si la interpretación es correcta)",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_SECONDARY,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        self.txt_correccion = ctk.CTkTextbox(
            corr_frame, height=80,
            fg_color=BG_PANEL, border_color=BORDER, border_width=1,
            corner_radius=8, font=ctk.CTkFont(size=13),
            text_color=TEXT_PRIMARY, wrap="word",
        )
        self.txt_correccion.grid(row=1, column=0, sticky="ew")

        placeholder = 'Ej: "Falta pasillo entre dormitorios. Cochera de 3×5m en esquina sur-oeste."'
        self.txt_correccion.insert("1.0", placeholder)
        self.txt_correccion.configure(text_color=TEXT_SECONDARY)
        self.txt_correccion.bind("<FocusIn>", self._limpiar_placeholder)
        self._placeholder_activo = True

        # ── Botones ─────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color=BG_DARK)
        btn_frame.grid(row=5, column=0, sticky="ew", padx=24, pady=18)
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            btn_frame, text="Cancelar", height=44,
            fg_color=BG_CARD, hover_color=BORDER,
            font=ctk.CTkFont(size=13), corner_radius=8,
            command=self._cancelar,
        ).grid(row=0, column=0, padx=(0, 8), sticky="ew")

        ctk.CTkButton(
            btn_frame, text="Generar planta  →", height=44,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=14, weight="bold"), corner_radius=8,
            command=self._confirmar,
        ).grid(row=0, column=1, padx=(8, 0), sticky="ew")

    def _limpiar_placeholder(self, _event=None):
        if self._placeholder_activo:
            self.txt_correccion.delete("1.0", "end")
            self.txt_correccion.configure(text_color=TEXT_PRIMARY)
            self._placeholder_activo = False

    def _confirmar(self):
        correccion = ""
        if not self._placeholder_activo:
            correccion = self.txt_correccion.get("1.0", "end").strip()
        self._resultado["accion"] = "continuar"
        self._resultado["correccion"] = correccion
        self._evento.set()
        self.destroy()

    def _cancelar(self):
        self._resultado["accion"] = "cancelar"
        self._evento.set()
        self.destroy()


class VentanaSLE(ctk.CTkToplevel):
    """
    Panel de control del Spatial Learning Engine.
    Muestra estadísticas, top correcciones, seeding y aprobación de plantas.
    """

    def __init__(self, parent, memoria: "SLEMemoria | None", captura: "CapturaCorrecciones | None"):
        super().__init__(parent)
        self.title("SLE — Spatial Learning Engine")
        self.geometry("860x680")
        self.minsize(700, 500)
        self.configure(fg_color=BG_DARK)
        self.transient(parent)
        # grab_set() diferido: evita fallar antes de que la ventana esté mapeada
        self.after(150, self._activar_grab)

        self.memoria = memoria
        self.captura = captura
        self._construir_ui()
        self._refrescar()
        self.lift()
        self.focus_force()

    def _activar_grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    def _construir_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Header ────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(15, 5))
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            hdr, text="Spatial Learning Engine",
            font=ctk.CTkFont(size=20, weight="bold"), text_color="#A78BFA",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            hdr, text="El SLE aprende del estilo de Joseph Merlos proyecto a proyecto.",
            font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

        btn_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        btn_frame.grid(row=0, column=2, sticky="e")

        ctk.CTkButton(
            btn_frame, text="Refrescar", width=90, height=32,
            fg_color=BG_CARD, hover_color=BORDER,
            font=ctk.CTkFont(size=12), corner_radius=8,
            command=self._refrescar,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="Importar JSONs", width=120, height=32,
            fg_color="#7C3AED", hover_color="#6D28D9",
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=self._importar_jsons,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="Importar DWG", width=120, height=32,
            fg_color="#0F766E", hover_color="#0D9488",
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=self._lanzar_dwg_extractor,
        ).pack(side="left", padx=4)

        # ── Cuerpo scrollable ──────────────────────────────────
        self.scroll = ctk.CTkScrollableFrame(
            self, fg_color=BG_PANEL, corner_radius=12,
        )
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)
        self.scroll.grid_columnconfigure(0, weight=1)

        # ── Barra inferior ─────────────────────────────────────
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 15))
        bottom.grid_columnconfigure(0, weight=1)

        self.lbl_status = ctk.CTkLabel(
            bottom, text="",
            font=ctk.CTkFont(size=11), text_color=SUCCESS, anchor="w",
        )
        self.lbl_status.grid(row=0, column=0, sticky="w")

    # ── Renderizado ───────────────────────────────────────────

    def _refrescar(self):
        for w in self.scroll.winfo_children():
            w.destroy()

        if not self.memoria:
            self._render_sin_sle()
            return

        try:
            stats = self.memoria.estadisticas()
            correcciones = self.memoria.top_correcciones(10)
            proyectos = self.memoria.listar_proyectos(limite=8)
        except Exception as e:
            self._render_error(str(e))
            return

        self._render_stats(stats)
        self._render_correcciones(correcciones)
        self._render_proyectos(proyectos)

    def _render_sin_sle(self):
        ctk.CTkLabel(
            self.scroll,
            text="SLE no disponible.\nInstala las dependencias: pip install -e .",
            font=ctk.CTkFont(size=14), text_color=TEXT_SECONDARY,
        ).pack(pady=40)

    def _render_error(self, msg: str):
        ctk.CTkLabel(
            self.scroll, text=f"Error al cargar datos:\n{msg}",
            font=ctk.CTkFont(size=13), text_color=ERROR,
        ).pack(pady=30)

    def _render_stats(self, stats: dict):
        # Título sección
        ctk.CTkLabel(
            self.scroll, text="ESTADÍSTICAS",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=ACCENT, anchor="w",
        ).pack(fill="x", padx=10, pady=(10, 5))

        card = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=10)
        card.pack(fill="x", padx=8, pady=4)

        chips = [
            ("Proyectos totales",  str(stats.get("n_proyectos", 0)),  TEXT_PRIMARY),
            ("Aprobados",          str(stats.get("n_aprobados", 0)),   SUCCESS),
            ("Correcciones",       str(stats.get("n_correcciones", 0)), WARNING),
            ("Score promedio",     f"{stats.get('score_promedio', 0)}/100", ACCENT),
            ("Área promedio",      f"{stats.get('area_promedio_m2', 0)} m²", TEXT_SECONDARY),
        ]

        frame = ctk.CTkFrame(card, fg_color="transparent")
        frame.pack(fill="x", padx=15, pady=12)

        for i, (label, valor, color) in enumerate(chips):
            col_frame = ctk.CTkFrame(frame, fg_color=BG_PANEL, corner_radius=8)
            col_frame.grid(row=0, column=i, padx=5, pady=2, sticky="ew")
            frame.grid_columnconfigure(i, weight=1)

            ctk.CTkLabel(
                col_frame, text=valor,
                font=ctk.CTkFont(size=18, weight="bold"), text_color=color,
            ).pack(pady=(10, 2))
            ctk.CTkLabel(
                col_frame, text=label,
                font=ctk.CTkFont(size=10), text_color=TEXT_SECONDARY,
            ).pack(pady=(0, 8))

        # Barra de progreso de aprendizaje
        n = stats.get("n_aprobados", 0)
        progreso = min(n / 50.0, 1.0)  # 50 proyectos = aprendizaje completo
        fase = "Iniciando" if n < 5 else "Aprendiendo" if n < 20 else "Entrenado" if n < 50 else "Óptimo"
        color_barra = ERROR if n < 5 else WARNING if n < 20 else SUCCESS

        prog_frame = ctk.CTkFrame(card, fg_color="transparent")
        prog_frame.pack(fill="x", padx=15, pady=(0, 12))

        ctk.CTkLabel(
            prog_frame,
            text=f"Fase de aprendizaje: {fase}  ({n}/50 proyectos aprobados)",
            font=ctk.CTkFont(size=11), text_color=TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, 4))

        barra = ctk.CTkProgressBar(
            prog_frame, fg_color=BG_DARK, progress_color=color_barra,
            height=8, corner_radius=4,
        )
        barra.pack(fill="x")
        barra.set(progreso)

    def _render_correcciones(self, correcciones: list):
        ctk.CTkLabel(
            self.scroll, text="TOP CORRECCIONES DETECTADAS",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=WARNING, anchor="w",
        ).pack(fill="x", padx=10, pady=(15, 5))

        if not correcciones:
            ctk.CTkLabel(
                self.scroll,
                text="  Sin correcciones registradas aún. Aprueba o corrige plantas para alimentar el SLE.",
                font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY, anchor="w",
            ).pack(fill="x", padx=10, pady=4)
            return

        max_freq = max((c.get("freq", 1) for c in correcciones), default=1)

        for c in correcciones:
            card = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=8)
            card.pack(fill="x", padx=8, pady=2)
            card.grid_columnconfigure(1, weight=1)

            freq = c.get("freq", 1)
            pct = freq / max_freq

            indicador = ctk.CTkFrame(card, width=5, fg_color=WARNING, corner_radius=2)
            indicador.grid(row=0, column=0, sticky="ns", padx=(8, 6), pady=8)

            info_frame = ctk.CTkFrame(card, fg_color="transparent")
            info_frame.grid(row=0, column=1, sticky="ew", padx=4, pady=8)
            info_frame.grid_columnconfigure(0, weight=1)

            tipo = c.get("tipo_cambio", "?")
            recinto = c.get("recinto") or "general"
            ctk.CTkLabel(
                info_frame,
                text=f"{tipo}  →  {recinto}",
                font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_PRIMARY, anchor="w",
            ).grid(row=0, column=0, sticky="w")

            barra_corr = ctk.CTkProgressBar(
                info_frame, fg_color=BG_DARK, progress_color=WARNING,
                height=4, corner_radius=2,
            )
            barra_corr.grid(row=1, column=0, sticky="ew", pady=(4, 0))
            barra_corr.set(pct)

            ctk.CTkLabel(
                card, text=f"{freq}×",
                font=ctk.CTkFont(size=13, weight="bold"), text_color=WARNING,
            ).grid(row=0, column=2, padx=12)

    def _render_proyectos(self, proyectos: list):
        ctk.CTkLabel(
            self.scroll, text="ÚLTIMOS PROYECTOS EN MEMORIA",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_SECONDARY, anchor="w",
        ).pack(fill="x", padx=10, pady=(15, 5))

        if not proyectos:
            ctk.CTkLabel(
                self.scroll,
                text="  Sin proyectos guardados. Usa 'Importar JSONs' o aprueba plantas desde la app.",
                font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY, anchor="w",
            ).pack(fill="x", padx=10, pady=4)
            return

        for p in proyectos:
            card = ctk.CTkFrame(self.scroll, fg_color=BG_CARD, corner_radius=8)
            card.pack(fill="x", padx=8, pady=2)
            card.grid_columnconfigure(1, weight=1)

            score = p.get("score", 0)
            color_score = SUCCESS if score >= 80 else WARNING if score >= 60 else ERROR

            ctk.CTkLabel(
                card, text=f"{score}",
                font=ctk.CTkFont(size=16, weight="bold"), text_color=color_score,
                width=48,
            ).grid(row=0, column=0, rowspan=2, padx=12, pady=10)

            prompt = p.get("prompt_original", "")[:70]
            ctk.CTkLabel(
                card, text=prompt,
                font=ctk.CTkFont(size=12), text_color=TEXT_PRIMARY, anchor="w",
            ).grid(row=0, column=1, sticky="w", padx=4, pady=(8, 2))

            area = p.get("area_total_m2", 0)
            ts = p.get("timestamp", "")[:10]
            ctk.CTkLabel(
                card, text=f"{area:.0f} m²  ·  {ts}",
                font=ctk.CTkFont(size=10), text_color=TEXT_SECONDARY, anchor="w",
            ).grid(row=1, column=1, sticky="w", padx=4, pady=(0, 8))

    # ── Acciones ─────────────────────────────────────────────

    def _importar_jsons(self):
        """Importar uno o varios archivos JSON de plantas existentes."""
        from tkinter import filedialog
        rutas = filedialog.askopenfilenames(
            title="Seleccionar plantas JSON para importar al SLE",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
        )
        if not rutas or not self.memoria:
            return

        importados = 0
        errores = 0
        for ruta in rutas:
            try:
                with open(ruta, encoding="utf-8") as f:
                    data = json.load(f)

                # Soporte para el formato con envoltorio {"plan": {...}} o plan directo
                if "plan" in data:
                    plan = data["plan"]
                    prompt = data.get("prompt", data.get("prompt_original", f"importado: {ruta}"))
                    score  = data.get("score", 80)
                else:
                    plan = data
                    prompt = f"importado desde archivo: {ruta}"
                    score  = 80

                if not plan.get("recintos"):
                    errores += 1
                    continue

                self.memoria.guardar_proyecto(plan, prompt_original=prompt, score=score, aprobado=True)
                importados += 1
            except Exception as e:
                errores += 1
                print(f"Error importando {ruta}: {e}")

        msg = f"Importados {importados} proyecto(s)"
        if errores:
            msg += f" · {errores} con error"
        self.lbl_status.configure(text=msg, text_color=SUCCESS if not errores else WARNING)
        self.after(4000, lambda: self.lbl_status.configure(text=""))
        self._refrescar()

    def _lanzar_dwg_extractor(self):
        """Lanza dwg_to_sle.py en proceso independiente (requiere AutoCAD abierto)."""
        import subprocess
        import sys as _sys
        ruta = os.path.join(BASE_DIR, "dwg_to_sle.py")
        subprocess.Popen([_sys.executable, ruta], cwd=BASE_DIR)
        self.lbl_status.configure(
            text="DWG Extractor lanzado — asegurate de tener AutoCAD abierto con el plano.",
            text_color=WARNING,
        )
        self.after(6000, lambda: self.lbl_status.configure(text=""))


class EstudioMerlosAI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Estudio Merlos AI")
        self.geometry("1200x750")
        self.minsize(1000, 650)
        self.configure(fg_color=BG_DARK)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.config = cargar_config()
        self.ejecutor = EjecutorAutoCAD(on_log=self._log_actividad)
        self.cliente_ia = None
        self._estado_autocad = False
        self._plano_activo = ""
        self._ejecutando = False
        self._ultimo_prompt = ""

        # SLE — Spatial Learning Engine
        self._sle_memoria = None
        self._sle_captura = None
        if _SLE_DISPONIBLE:
            try:
                self._sle_memoria = SLEMemoria()
                self._sle_captura = CapturaCorrecciones(self._sle_memoria)
            except Exception as e:
                print(f"SLE no disponible: {e}")

        self._construir_ui()
        self._iniciar_monitoreo()

        # Argumentos de línea de comandos
        if "--paso0" in sys.argv:
            self.after(300, lambda: self._seleccionar_modulo("diagrama"))

        proveedor = self.config.get("proveedor", "anthropic")
        key = self.config.get("api_keys", {}).get(proveedor, self.config.get("anthropic_api_key", ""))
        if not key and PROVEEDORES.get(proveedor, {}).get("requiere_key", True):
            self.after(500, self._pedir_api_key)
        else:
            self._inicializar_ia()

    def _construir_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._crear_sidebar()
        self._crear_panel_principal()
        self._crear_barra_estado()

    def _crear_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=240, fg_color=BG_PANEL, corner_radius=0)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        sidebar.grid_propagate(False)

        logo_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        logo_frame.pack(fill="x", pady=(25, 5), padx=20)

        ctk.CTkLabel(
            logo_frame, text="EM",
            font=ctk.CTkFont(size=28, weight="bold"), text_color=ACCENT,
            width=50, height=50, fg_color=BG_CARD, corner_radius=12,
        ).pack(side="left", padx=(0, 12))

        titulo_frame = ctk.CTkFrame(logo_frame, fg_color="transparent")
        titulo_frame.pack(side="left", fill="x")
        ctk.CTkLabel(
            titulo_frame, text="Estudio Merlos",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            titulo_frame, text="AI Assistant",
            font=ctk.CTkFont(size=12), text_color=ACCENT, anchor="w",
        ).pack(anchor="w")

        sep = ctk.CTkFrame(sidebar, height=1, fg_color=BORDER)
        sep.pack(fill="x", padx=20, pady=15)

        ctk.CTkLabel(
            sidebar, text="MODULOS", font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_SECONDARY, anchor="w",
        ).pack(fill="x", padx=25, pady=(0, 8))

        self.btn_autocad = ctk.CTkButton(
            sidebar, text="  AutoCAD", anchor="w", height=40,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=14), corner_radius=8,
            command=lambda: self._seleccionar_modulo("autocad"),
        )
        self.btn_autocad.pack(fill="x", padx=15, pady=2)

        color_diag = "#065F46" if _DIAGRAMA_DISPONIBLE else BG_CARD
        self.btn_diagrama = ctk.CTkButton(
            sidebar, text="  Diseno — Paso 0", anchor="w", height=40,
            fg_color="transparent", hover_color=BG_CARD,
            font=ctk.CTkFont(size=14), text_color=TEXT_SECONDARY,
            corner_radius=8,
            command=lambda: self._seleccionar_modulo("diagrama"),
            state="normal" if _DIAGRAMA_DISPONIBLE else "disabled",
        )
        self.btn_diagrama.pack(fill="x", padx=15, pady=2)

        for nombre in ["Revit", "Presupuestos"]:
            btn = ctk.CTkButton(
                sidebar, text=f"  {nombre}", anchor="w", height=40,
                fg_color="transparent", hover_color=BG_CARD,
                font=ctk.CTkFont(size=14), text_color=TEXT_SECONDARY,
                corner_radius=8, state="disabled",
            )
            btn.pack(fill="x", padx=15, pady=2)
            ctk.CTkLabel(
                sidebar, text="Proximamente",
                font=ctk.CTkFont(size=10), text_color=TEXT_SECONDARY, anchor="w",
            ).pack(fill="x", padx=40, pady=(0, 4))

        sidebar_bottom = ctk.CTkFrame(sidebar, fg_color="transparent")
        sidebar_bottom.pack(side="bottom", fill="x", padx=15, pady=15)

        ctk.CTkButton(
            sidebar_bottom, text="Normativa", height=36,
            fg_color="#065F46", hover_color="#047857",
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=self._abrir_normativa,
        ).pack(fill="x", pady=(0, 6))

        ctk.CTkButton(
            sidebar_bottom, text="Base de Conocimiento", height=36,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=self._abrir_conocimiento,
        ).pack(fill="x", pady=(0, 6))

        color_sle = "#7C3AED" if _SLE_DISPONIBLE else BG_CARD
        self.btn_sle = ctk.CTkButton(
            sidebar_bottom, text="SLE — Memoria IA", height=36,
            fg_color=color_sle, hover_color="#6D28D9",
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=self._abrir_sle,
        )
        self.btn_sle.pack(fill="x", pady=(0, 6))

        ctk.CTkButton(
            sidebar_bottom, text="Configuracion", height=36,
            fg_color=BG_CARD, hover_color=BORDER,
            font=ctk.CTkFont(size=12), corner_radius=8,
            command=self._pedir_api_key,
        ).pack(fill="x")

    def _crear_panel_principal(self):
        self.panel_autocad = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        self.panel_autocad.grid(row=0, column=1, sticky="nsew")
        self.panel_autocad.grid_columnconfigure(0, weight=1)
        self.panel_autocad.grid_rowconfigure(2, weight=1)
        self.panel = self.panel_autocad   # alias de compatibilidad

        header = ctk.CTkFrame(self.panel, fg_color="transparent", height=60)
        header.grid(row=0, column=0, sticky="ew", padx=25, pady=(20, 10))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text="AutoCAD Studio",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        self.lbl_estado_acad = ctk.CTkLabel(
            header, text="AutoCAD: Verificando...",
            font=ctk.CTkFont(size=12), text_color=WARNING,
        )
        self.lbl_estado_acad.grid(row=0, column=1, sticky="e")

        self._crear_seccion_generar()
        self._crear_seccion_log()

    def _crear_seccion_generar(self):
        card = ctk.CTkFrame(self.panel, fg_color=BG_PANEL, corner_radius=12)
        card.grid(row=1, column=0, sticky="ew", padx=25, pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text="Generar Planta Arquitectonica",
            font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT_PRIMARY, anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 5))

        ctk.CTkLabel(
            card, text="Describe el proyecto en lenguaje natural",
            font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY, anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 10))

        self.txt_descripcion = ctk.CTkTextbox(
            card, height=80, fg_color=BG_CARD, border_color=BORDER,
            border_width=1, corner_radius=8, font=ctk.CTkFont(size=13),
            text_color=TEXT_PRIMARY, wrap="word",
        )
        self.txt_descripcion.grid(row=2, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 12))
        self.txt_descripcion.insert("1.0", "Ej: Casa de 3 habitaciones, 2 banos, sala, cocina, comedor y cochera")

        btn_grid = ctk.CTkFrame(card, fg_color="transparent")
        btn_grid.grid(row=3, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 18))

        self._botones_cmd = []

        # Fila 0: dos acciones primarias con igual ancho
        frame_primarios = ctk.CTkFrame(btn_grid, fg_color="transparent")
        frame_primarios.grid(row=0, column=0, columnspan=3, sticky="ew",
                             pady=(0, 6))
        frame_primarios.grid_columnconfigure((0, 1), weight=1)

        for i, (texto, color, hover, cmd) in enumerate([
            ("Generar Planta", ACCENT, "#1D4ED8", self._cmd_generar_planta),
            ("Desde Boceto  📷", "#7C3AED", "#6D28D9", self._cmd_importar_boceto),
        ]):
            btn = ctk.CTkButton(
                frame_primarios, text=texto, height=44, fg_color=color,
                hover_color=hover,
                font=ctk.CTkFont(size=13, weight="bold"),
                corner_radius=8, command=cmd,
            )
            btn.grid(row=0, column=i, padx=4, sticky="ew")
            self._botones_cmd.append(btn)

        # Filas 1+: utilidades
        utilidades = [
            ("Limpiar Esquinas", self._cmd_limpiar_esquinas),
            ("Insertar Puerta", self._cmd_insertar_puerta),
            ("Insertar Ventana", self._cmd_insertar_ventana),
            ("Agregar Cotas", self._cmd_agregar_cotas),
            ("Aplicar Estandares", self._cmd_aplicar_estandares),
        ]
        for i, (texto, cmd) in enumerate(utilidades):
            btn = ctk.CTkButton(
                btn_grid, text=texto, height=34, fg_color=BG_CARD,
                hover_color=BORDER,
                font=ctk.CTkFont(size=11),
                corner_radius=8, command=cmd,
            )
            fila = 1 + i // 3
            col = i % 3
            btn.grid(row=fila, column=col, padx=4, pady=2, sticky="ew")
            self._botones_cmd.append(btn)

        btn_grid.grid_columnconfigure((0, 1, 2), weight=1)

        self.progress = ctk.CTkProgressBar(
            card, fg_color=BG_CARD, progress_color=ACCENT, height=6, corner_radius=3,
        )
        self.progress.grid(row=4, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 15))
        self.progress.set(0)

    def _crear_seccion_log(self):
        log_card = ctk.CTkFrame(self.panel, fg_color=BG_PANEL, corner_radius=12)
        log_card.grid(row=2, column=0, sticky="nsew", padx=25, pady=(0, 15))
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)

        log_header = ctk.CTkFrame(log_card, fg_color="transparent")
        log_header.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 5))
        log_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            log_header, text="Actividad",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_PRIMARY, anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            log_header, text="Limpiar", width=70, height=28,
            fg_color=BG_CARD, hover_color=BORDER,
            font=ctk.CTkFont(size=11), corner_radius=6,
            command=self._limpiar_log,
        ).grid(row=0, column=1, sticky="e")

        self.txt_log = ctk.CTkTextbox(
            log_card, fg_color=BG_CARD, border_width=0, corner_radius=8,
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=TEXT_SECONDARY, wrap="word", state="disabled",
        )
        self.txt_log.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 15))

        self._log_actividad("Estudio Merlos AI iniciado")
        self._log_actividad("Esperando conexion con AutoCAD...")

    def _crear_barra_estado(self):
        barra = ctk.CTkFrame(self, height=32, fg_color=BG_PANEL, corner_radius=0)
        barra.grid(row=1, column=1, sticky="ew")
        barra.grid_columnconfigure(1, weight=1)

        self.lbl_conexion = ctk.CTkLabel(
            barra, text="  Claude: No configurado",
            font=ctk.CTkFont(size=11), text_color=TEXT_SECONDARY, anchor="w",
        )
        self.lbl_conexion.grid(row=0, column=0, padx=15, sticky="w")

        self.lbl_plano = ctk.CTkLabel(
            barra, text="Plano: --",
            font=ctk.CTkFont(size=11), text_color=TEXT_SECONDARY, anchor="e",
        )
        self.lbl_plano.grid(row=0, column=2, padx=15, sticky="e")

    # ── Proveedor IA ────────────────────────────────────────

    def _inicializar_ia(self):
        proveedor = self.config.get("proveedor", "anthropic")
        info = PROVEEDORES.get(proveedor, PROVEEDORES["anthropic"])
        # SEC-01: leer desde keyring; fallback a texto plano si no migrado aún
        try:
            import keyring as _kr
            key = _kr.get_password("estudio-merlos-ai", proveedor) or ""
        except Exception:
            key = ""
        if not key:
            key = self.config.get("api_keys", {}).get(proveedor, self.config.get("anthropic_api_key", ""))
        modelo = self.config.get("modelo", info["modelos"][0])

        if info["requiere_key"] and not key:
            return

        try:
            self.cliente_ia = ClienteIA(proveedor, key, modelo)
            nombre = info["nombre"]
            self._log_actividad(f"IA conectada: {nombre} ({modelo})")
            self.lbl_conexion.configure(
                text=f"  IA: {nombre}", text_color=SUCCESS,
            )
        except Exception as e:
            self._log_actividad(f"Error al conectar IA: {e}")

    def _pedir_api_key(self):
        dialogo = DialogoConfigIA(self, self.config)
        self.wait_window(dialogo)
        if dialogo.resultado:
            self.config = dialogo.resultado
            guardar_config(self.config)
            self._log_actividad("Configuracion guardada")
            self._inicializar_ia()

    # ── Monitoreo AutoCAD ────────────────────────────────────

    def _iniciar_monitoreo(self):
        def monitor():
            while True:
                try:
                    acad_ok = self.ejecutor.verificar()
                    plano = self.ejecutor.plano_activo() if acad_ok else ""
                    self.after(0, self._actualizar_estado_ui, acad_ok, plano)
                except Exception:
                    self.after(0, self._actualizar_estado_ui, False, "")
                time.sleep(3)

        threading.Thread(target=monitor, daemon=True).start()

    def _actualizar_estado_ui(self, acad_ok: bool, plano: str):
        if acad_ok != self._estado_autocad:
            self._estado_autocad = acad_ok
            if acad_ok:
                self.lbl_estado_acad.configure(
                    text="AutoCAD: Conectado", text_color=SUCCESS,
                )
                self._log_actividad("AutoCAD detectado")
            else:
                self.lbl_estado_acad.configure(
                    text="AutoCAD: No detectado", text_color=ERROR,
                )

        if plano != self._plano_activo:
            self._plano_activo = plano
            self.lbl_plano.configure(
                text=f"Plano: {plano}" if plano else "Plano: --",
            )

    # ── Loop Agentico ────────────────────────────────────────

    def _set_botones(self, estado: str):
        for btn in self._botones_cmd:
            btn.configure(state=estado)

    def _ejecutar_con_claude(self, prompt_usuario: str, requiere_autocad: bool = False):
        if not self.cliente_ia:
            self._log_actividad("Error: IA no configurada")
            self._pedir_api_key()
            return

        if requiere_autocad and not self._estado_autocad:
            self._log_actividad("Error: AutoCAD no esta abierto")
            return

        if self._ejecutando:
            self._log_actividad("Ya hay una tarea en ejecucion...")
            return

        self._ejecutando = True
        self.after(0, lambda: self._set_botones("disabled"))
        self.after(0, lambda: self.progress.set(0.05))
        nombre_ia = PROVEEDORES.get(self.cliente_ia.proveedor_id, {}).get("nombre", "IA")
        self._log_actividad(f"Enviando a {nombre_ia}: {prompt_usuario[:80]}...")

        def tarea():
            try:
                # ── Modo Planificador (Ollama/modelos locales) ──────────────
                if self.cliente_ia.modo_planner:
                    self._tarea_planner(prompt_usuario)
                    return

                messages = [{"role": "user", "content": prompt_usuario}]
                system_prompt = construir_system_prompt()
                iteracion = 0
                max_iteraciones = 150

                while iteracion < max_iteraciones:
                    iteracion += 1
                    progreso = min(0.1 + (iteracion / max_iteraciones) * 0.8, 0.9)
                    self.after(0, lambda p=progreso: self.progress.set(p))

                    self.after(0, self._log_actividad, f"IA pensando... (paso {iteracion})")

                    max_hist = self.cliente_ia.max_historial_msgs
                    msgs_enviar = messages[-max_hist:] if max_hist and len(messages) > max_hist else messages
                    if msgs_enviar and msgs_enviar[0].get("role") != "user":
                        msgs_enviar = messages[:1] + msgs_enviar

                    if self.cliente_ia.tools_mini:
                        schema = TOOLS_SCHEMA_MINI
                    elif self.cliente_ia.tools_compacto:
                        schema = TOOLS_SCHEMA_COMPACTO
                    else:
                        schema = TOOLS_SCHEMA
                    respuesta = self.cliente_ia.llamar(system_prompt, msgs_enviar, schema)

                    if respuesta.texto:
                        for linea in respuesta.texto.strip().split("\n"):
                            if linea.strip():
                                self.after(0, self._log_actividad, f"IA: {linea.strip()}")

                    # Si el modelo respondió solo con texto sin llamar tools, corregirlo
                    if respuesta.terminado and not respuesta.tool_calls:
                        # Verificar si hay trabajo pendiente (grid sin dibujar)
                        if self.ejecutor._grid and not hasattr(self.ejecutor, '_manifest'):
                            # Forzar un recordatorio para que complete el dibujo
                            messages.append({"role": "user", "content": "IMPORTANTE: No ejecutaste las herramientas. Debes llamar crear_grid_diseno y dibujar_desde_grid. Hazlo ahora."})
                            self.after(0, self._log_actividad, "Reintentando: el modelo no llamó herramientas")
                            continue
                        self.after(0, self._log_actividad, "Tarea completada")
                        break

                    if respuesta.terminado:
                        self.after(0, self._log_actividad, "Tarea completada")
                        break

                    tool_results = []
                    for tc in respuesta.tool_calls:
                        self.after(0, self._log_actividad,
                                   f"Ejecutando: {tc.name}({json.dumps(tc.input, ensure_ascii=False)[:80]})")

                        resultado = self.ejecutor.ejecutar(tc.name, tc.input)

                        imagen_b64 = None
                        if isinstance(resultado, dict) and "png_base64" in resultado:
                            imagen_b64 = resultado.pop("png_base64")
                            resultado["_imagen_adjunta"] = True

                        resultado_str = json.dumps(resultado, ensure_ascii=False)

                        max_chars = self.cliente_ia.max_resultado_chars
                        if len(resultado_str) > max_chars:
                            resultado_str = resultado_str[:max_chars] + "...[truncado]"

                        if isinstance(resultado, dict) and "error" in resultado:
                            self.after(0, self._log_actividad, f"  Error: {resultado['error']}")
                        else:
                            extra = " [imagen adjuntada]" if imagen_b64 else ""
                            self.after(0, self._log_actividad, f"  OK: {resultado_str[:100]}{extra}")

                        tr = {"id": tc.id, "content": resultado_str}
                        if imagen_b64 and self.cliente_ia.soporta_vision:
                            tr["image_base64"] = imagen_b64
                        tool_results.append(tr)

                    nuevos_msgs = self.cliente_ia.construir_mensajes_resultado(respuesta, tool_results)
                    messages.extend(nuevos_msgs)

                self.after(0, lambda: self.progress.set(1.0))
                self.after(2000, lambda: self.progress.set(0))

            except Exception as e:
                self.after(0, self._log_actividad, f"Error: {e}")
                self.after(0, lambda: self.progress.set(0))
            finally:
                self._ejecutando = False
                self.after(0, lambda: self._set_botones("normal"))

        threading.Thread(target=tarea, daemon=True).start()

    # ── Comandos ─────────────────────────────────────────────

    def _cmd_generar_planta(self):
        desc = self.txt_descripcion.get("1.0", "end").strip()
        if not desc or desc.startswith("Ej:"):
            self._log_actividad("Escribe una descripcion del proyecto")
            return
        self._ultimo_prompt = desc  # ← SLE: guardar prompt para auto-save
        self._ejecutar_con_claude(
            f"Genera la planta arquitectonica para: {desc}\n\n"
            "PROCESO:\n"
            "FASE 1 - Planificacion: crea grid, coloca recintos, valida, ver_preview_grid (analiza visualmente la imagen y corrige si es necesario).\n"
            "FASE 2 - Dibujo: cuando el grid sea bueno, llama dibujar_desde_grid en AutoCAD."
        )

    def _cmd_limpiar_esquinas(self):
        self._ejecutar_con_claude(
            "Primero lee la info del plano con leer_info_plano y lista los objetos de la capa A-MURO. "
            "Luego analiza las esquinas que necesitan limpieza y reporta lo que encontraste."
        )

    def _cmd_insertar_puerta(self):
        self._ejecutar_con_claude(
            "Primero lee la info del plano. Luego analiza donde hacen falta puertas "
            "y agregalas con agregar_puerta. Exterior=1.10m, Interior=1.00m. "
            "Al final haz zoom_todo."
        )

    def _cmd_insertar_ventana(self):
        self._ejecutar_con_claude(
            "Primero lee la info del plano. Luego analiza donde hacen falta ventanas "
            "y agregalas con agregar_ventana. Minimo 15% del area del recinto. "
            "Al final haz zoom_todo."
        )

    def _cmd_agregar_cotas(self):
        self._ejecutar_con_claude(
            "Primero lee la info del plano para conocer las dimensiones. "
            "Luego agrega cotas con cotar_planta. Offset 0.80m, capa A-COTA. "
            "Al final haz zoom_todo."
        )

    def _cmd_aplicar_estandares(self):
        self._ejecutar_con_claude(
            "Aplica todos los estandares del estudio: "
            "1) aplicar_estandares_capas, "
            "2) insertar_norte en la esquina superior derecha del plano, "
            "3) insertar_escala debajo del plano a escala 1:100. "
            "Al final haz zoom_todo."
        )

    # ── Modo Planificador ─────────────────────────────────────

    # ═══════════════════════════════════════════════════════
    # NEURAL CAD ARCHITECTURAL ENGINE — CEREBRO CENTRAL
    # Aplica a todos los modos con visión (Claude / GPT-4o)
    # ═══════════════════════════════════════════════════════
    SYSTEM_NEURAL_CAD = """\
# SPATIAL ARCHITECTURAL REASONING ENGINE — ESTUDIO MERLOS AI

---
## CAPA 1 — IDENTIDAD COGNITIVA

Eres un **Spatial Architectural Reasoning Engine** conectado a AutoCAD.
No eres un chatbot. No eres un generador de comandos CAD.
Eres un motor de razonamiento espacial arquitectónico que produce plantas coherentes, habitables y construibles.

Piensas simultáneamente como:
arquitecto · evaluador espacial · razonador topológico · validador normativo · crítico visual

---
## CAPA 2 — REGLAS DURAS (dominan siempre sobre el razonamiento)

### CONTRATO DE SALIDA
Responde SOLO con JSON válido. Sin texto. Sin explicaciones. Sin markdown.

Formato exacto:
{"grid":{"ancho_m":X,"alto_m":Y},"recintos":[
  {"nombre":"NOMBRE_EN_MAYUSCULAS","fila":F,"col":C,"ancho":W,"alto":H}
],"puertas":[
  {"tipo":"entre_recintos","recinto1":"R1","recinto2":"R2","ancho":1.0},
  {"tipo":"exterior","recinto":"R","lado":"sur","ancho":1.1}
]}

### REGLAS DE GRID
- fila 0 = NORTE/ARRIBA (privado) · fila máx = CALLE/SUR (público)
- Recinto en fila F, col C, ancho W, alto H → ocupa exactamente [F..F+H-1] × [C..C+W-1]
- NUNCA solapar celdas de diferentes recintos
- Sin huecos mayores a 4 celdas consecutivas
- Todos los recintos deben compartir al menos un lado con otro

### NORMATIVA INVU/CFIA — COSTA RICA
| Recinto | Área mínima | Dimensión mínima |
|---|---|---|
| Dormitorio principal | 9.0 m² | 3.5 × 3.5 m |
| Dormitorio adicional | 7.5 m² | 3.0 × 3.0 m |
| Baño completo | 2.5 m² | 2.0 × 2.5 m |
| Cocina | 5.0 m² | 3.0 × 3.0 m |
| Sala | 10.0 m² | 4.0 × 3.5 m |
| Comedor | 7.0 m² | 3.0 × 3.0 m |
| Cochera | 12.0 m² | 3.0 × 5.0 m |
| Lavandería | 3.0 m² | — |
| Pasillo | — | 1.5 m ancho mínimo |

### ERRORES FATALES — NUNCA PRODUCIR
- Recinto sin acceso por puerta
- Circulación principal rota
- Dormitorio accesible solo cruzando sala
- Baño sin acceso lógico desde pasillo o dormitorio
- Proporción > 3.5:1 en recintos que no sean pasillo
- Recintos flotantes sin vecino compartido

---
## CAPA 3 — RAZONAMIENTO ESPACIAL

### PIPELINES DE ENTRADA

**Pipeline A — Visual externo (bocetos / fotos)**
- Identificar: recintos, etiquetas, accesos, circulación, límites, orientación
- Inferir: jerarquía espacial, lógica funcional, intención del arquitecto
- Tolerar: líneas torcidas, fotos inclinadas, dibujos incompletos, sombras, perspectivas leves

**Pipeline B — Evaluación visual interna (preview PNG)**
- NO describir la imagen
- CRITICAR arquitectónicamente: proporciones, circulación, zonas, coherencia espacial
- COMBINAR: score programático (normativa dura) + evaluación espacial visual (coherencia)

### ENTRADAS MULTIMODALES
| Input | Interpretación |
|---|---|
| Texto | Intención arquitectónica |
| Imagen / boceto | Relaciones espaciales |
| JSON | Estructura paramétrica |
| Preview PNG | Coherencia visual |
| Score | Validación normativa |

### PENSAMIENTO TOPOLÓGICO
Piensa en conexiones espaciales, no solo en coordenadas:
- Sala ↔ Comedor ↔ Cocina (cadena social pública)
- Pasillo ↔ Dormitorios (eje de acceso privado)
- Cochera ↔ Sala o Pasillo (transición exterior-interior)
- Baño ↔ Dormitorio o Pasillo (acceso higiénico privado)

### ZONIFICACIÓN
**Pública** (filas inferiores / calle): sala · comedor · cocina · cochera
**Privada** (filas superiores / norte): dormitorios · baños privados
**Servicio**: lavandería · patio · cuarto técnico
**Circulación**: pasillos · conexiones (mín 1.20 m ancho)

### REGLAS ESPACIALES
- Dormitorios: agrupados en zona privada — nunca mezclados con zona pública
- Cocina: siempre conectada a comedor — nunca aislada
- Baños: acceso desde pasillo o dormitorio — nunca directamente desde sala o cocina
- Sala: nodo social principal — conectada a entrada, comedor y circulación
- Cochera: acceso directo desde exterior (calle) + conexión interior a sala/pasillo
- Zonas húmedas: baños y cocina en el mismo lateral (tuberías compartidas)
- Iluminación: sala y dormitorio principal deben tocar muro exterior

### VOCABULARIO LOCAL (Costa Rica)
"cuarto" / "habitación" / "recámara" → DORMITORIO
"sala-comedor" → separar en SALA + COMEDOR
"servicio" / "pila" → LAVANDERIA
"garage" / "cochera" / "garaje" / "parqueo" → COCHERA
"medio baño" / "toilet" → BANO (2×2 m mínimo)
"estudio" / "oficina" → ESTUDIO

### PRIORIDAD DE DECISIONES
1. Circulación — todos los recintos alcanzables
2. Accesibilidad — puertas lógicas, anchos mínimos
3. Coherencia funcional — zonas correctas
4. Privacidad — gradiente norte → calle
5. Proporciones — dimensiones habitables
6. Balance visual
7. Estética

### PRINCIPIO DE PLAUSIBILIDAD
Toda planta debe sentirse como una primera propuesta de un arquitecto junior competente.
NO: geometría aleatoria · cajas sin lógica espacial · celdas arbitrariamente colocadas.
La IA no reemplaza al arquitecto. Produce el punto de partida que él editará.
"""

    # Activador de modo planner — se adjunta a SYSTEM_NEURAL_CAD para generación inicial
    _NEURAL_FORMATO_JSON = """\

---
## MODO: GENERADOR DE PLANTA

Genera el layout completo para el proyecto solicitado.
Aplica la Capa 2 (normativa + grid) y la Capa 3 (razonamiento espacial) del Engine.
Responde SOLO con JSON. El formato y las reglas no negociables están en la Capa 2.
"""

    SYSTEM_SKETCH = """\
---
## MODO: INTÉRPRETE DE BOCETOS ARQUITECTÓNICOS

Recibirás una foto de un boceto o planta arquitectónica. Tu función es convertir la intención visual en estructura espacial paramétrica.

### ANÁLISIS EN 3 NIVELES

**Nivel 1 — Elementos espaciales**
recintos · etiquetas escritas · mobiliario representado · accesos · circulación · límites · dimensiones · orientación

**Nivel 2 — Relaciones topológicas**
- Qué conecta con qué (cadena: sala↔comedor↔cocina · pasillo↔dormitorios)
- Qué es público / privado / servicio
- Jerarquía espacial implícita del boceto

**Nivel 3 — Intención arquitectónica**
- Infiere la lógica funcional aunque el boceto sea imperfecto
- Tolera: líneas torcidas · fotos inclinadas · sombras · dibujos incompletos · perspectivas leves

### IDENTIFICACIÓN POR MOBILIARIO (cuando no hay etiqueta escrita)
Si ves estos elementos dibujados, identifica el recinto:
- Inodoro + ducha/tina + lavamanos → BANO
- Estufa/fogón + fregadero + refrigeradora (REF) → COCINA
- Cama dibujada → DORMITORIO
- Sofá + mesa de centro → SALA
- Mesa rectangular con sillas → COMEDOR
- Carro/auto + portón → COCHERA
- Lavadora + pila/fregona → LAVANDERIA
- Escritorio + silla de oficina → ESTUDIO

**REGLA CRÍTICA:** Un recinto con estufa, fregadero o REF dibujados ES COCINA — no recibidor, no pasillo, no sala.
Si hay muebles de cocina en un área sin etiqueta, ese recinto ES COCINA.

### VOCABULARIO LOCAL (Costa Rica)
"cuarto" / "habitación" / "recámara" → DORMITORIO
"sala-comedor" → separar en SALA + COMEDOR si el espacio lo permite, o SALA_COMEDOR si están completamente integrados
"servicio" / "pila" → LAVANDERIA
"garage" / "cochera" / "garaje" / "parqueo" → COCHERA
"medio baño" / "toilet" / "SS" → BANO (2×2 m mínimo)
"estudio" / "oficina" → ESTUDIO
"recibidor" / "entrada" / "vestíbulo" → PASILLO (si es pequeño) o recinto propio

### DIMENSIONADO
Si hay medidas escritas en el boceto: úsalas directamente (redondear al entero más cercano).
Si no hay medidas: estimar por proporción visual con lógica constructiva CR.
Casa típica CR: 60–150 m² · Grid mínimo 6×5 m · máximo 16×14 m
DORMITORIO: mín 3×3 m · SALA: mín 4×3 m · BAÑO: mín 2×2 m
COCINA: mín 2×3 m · COCHERA: mín 3×5 m · PASILLO: mín 1.5 m ancho

### ORIENTACIÓN EN EL GRID
fila 0 = norte/arriba (privado) · fila_max = calle/frente (público)
Si el boceto indica calle/frente → esa zona va en filas inferiores.
Dormitorios → filas superiores · Sala/cocina/cochera → filas inferiores.

### PUERTAS
Conexión entre dos recintos → tipo "entre_recintos"
Acceso desde exterior → tipo "exterior" con el lado indicado (default "sur")

### CONTRATO DE SALIDA
Responde ÚNICAMENTE con JSON válido. Sin texto. Sin explicaciones.
El formato exacto está en la Capa 2 del Engine. Aplícalo.
{"grid":{"ancho_m":X,"alto_m":Y},"recintos":[
  {"nombre":"NOMBRE_EN_MAYUSCULAS","fila":F,"col":C,"ancho":W,"alto":H}
],"puertas":[
  {"tipo":"entre_recintos","recinto1":"R1","recinto2":"R2","ancho":1.0},
  {"tipo":"exterior","recinto":"R","lado":"sur","ancho":1.1}
]}
"""

    SYSTEM_PLANNER = """\
Eres un planificador de plantas arquitectónicas. Responde SOLO con JSON válido, sin texto extra.

PRINCIPIOS DE DISTRIBUCIÓN (aplica en este orden de prioridad):

1. GRADIENTE DE PRIVACIDAD (eje norte→calle):
   - fila 0 = NORTE/ARRIBA = PRIVADO (dormitorios, baños)
   - fila máx = SUR/ABAJO = CALLE/PÚBLICO (sala, comedor, cocina, cochera)

2. ZONAS HÚMEDAS JUNTAS: cocina y baños en el mismo lateral (mismas cols) — comparten tuberías

3. PASILLO como eje de circulación: si hay >2 dormitorios, usar PASILLO de 2 celdas de alto
   que conecte zona privada con zona pública

4. ILUMINACIÓN: sala y dormitorios en borde exterior (tocan muro perimetral)

5. COMPACIDAD: todos los recintos adyacentes entre sí, sin huecos grandes

EJEMPLO — 3 dormitorios + 2 baños + sala + comedor + cocina en 10×12m:
{"grid":{"ancho_m":10,"alto_m":12},"recintos":[
  {"nombre":"DORMITORIO_PRINCIPAL","fila":0,"col":0,"ancho":5,"alto":4},
  {"nombre":"BANO_PRINCIPAL",      "fila":0,"col":5,"ancho":3,"alto":4},
  {"nombre":"DORMITORIO_2",        "fila":0,"col":8,"ancho":2,"alto":4},
  {"nombre":"PASILLO",             "fila":4,"col":0,"ancho":10,"alto":2},
  {"nombre":"DORMITORIO_3",        "fila":6,"col":0,"ancho":4,"alto":3},
  {"nombre":"BANO",                "fila":6,"col":4,"ancho":3,"alto":3},
  {"nombre":"SALA",                "fila":6,"col":7,"ancho":3,"alto":3},
  {"nombre":"COCINA",              "fila":9,"col":0,"ancho":4,"alto":3},
  {"nombre":"COMEDOR",             "fila":9,"col":4,"ancho":6,"alto":3}
],"puertas":[
  {"tipo":"entre_recintos","recinto1":"DORMITORIO_PRINCIPAL","recinto2":"PASILLO","ancho":1.0},
  {"tipo":"entre_recintos","recinto1":"BANO_PRINCIPAL","recinto2":"DORMITORIO_PRINCIPAL","ancho":1.0},
  {"tipo":"entre_recintos","recinto1":"DORMITORIO_2","recinto2":"PASILLO","ancho":1.0},
  {"tipo":"entre_recintos","recinto1":"DORMITORIO_3","recinto2":"PASILLO","ancho":1.0},
  {"tipo":"entre_recintos","recinto1":"BANO","recinto2":"PASILLO","ancho":1.0},
  {"tipo":"entre_recintos","recinto1":"SALA","recinto2":"PASILLO","ancho":1.0},
  {"tipo":"entre_recintos","recinto1":"COCINA","recinto2":"COMEDOR","ancho":1.0},
  {"tipo":"exterior","recinto":"SALA","lado":"sur","ancho":1.1}
]}

REGLAS ESTRICTAS:
- NUNCA solapar: fila F col C ancho W alto H → ocupa filas [F..F+H-1] cols [C..C+W-1]
- DIMENSIONES MÍNIMAS EN CELDAS: SALA≥4×3, COCINA≥3×3, DORMITORIO≥3×3, BAÑO≥2×2, PASILLO≥ancho_grid×2
- Todos los recintos deben compartir al menos un lado con otro
- Llenar el grid sin huecos > 4 celdas

Responde ÚNICAMENTE el JSON. Sin texto adicional.
"""

    SYSTEM_EVALUADOR = """\
---
## MODO: EVALUADOR VISUAL ARQUITECTÓNICO

Recibes un preview PNG del layout actual + score programático + JSON del plan.

### LEYENDA DEL PREVIEW
Azul oscuro = dormitorio principal · Azul claro = dormitorios · Amarillo = cocina
Verde = sala · Violeta = comedor · Cyan = baño · Gris = pasillo · Rosa = lavandería
Líneas punteadas = circulación y adyacencias entre recintos
Panel lateral = score numérico · errores · advertencias · sugerencias del validador

### PRINCIPIO DE DOBLE VALIDACIÓN
El score programático valida NORMAS DURAS (áreas, dimensiones, normativa INVU/CFIA).
Tu evaluación visual valida COHERENCIA ESPACIAL (topología, circulación, sensación espacial).
Debes combinar ambas fuentes para tu decisión.

### CRITERIOS ARQUITECTÓNICOS (evalúa en este orden)
1. GRADIENTE DE PRIVACIDAD — dormitorios/baños al norte · sala/cocina al sur (calle)
2. CIRCULACIÓN — ¿existe transición entre zona privada y pública? ¿pasillo funcional?
3. ZONAS HÚMEDAS — ¿baños y cocina en el mismo lateral?
4. ILUMINACIÓN — ¿sala y dormitorio principal tocan muro exterior?
5. PROPORCIONES — ¿dimensiones habitables? ¿sin formas con ratio > 3.5:1?
6. ACCESOS — ¿cada recinto tiene puerta lógica? ¿ningún recinto aislado?
7. TOPOLOGÍA — ¿cadena sala↔comedor↔cocina coherente? ¿pasillo conecta dormitorios?

### RESPUESTA (elige EXACTAMENTE UNA)
- Score ≥ 70 Y criterios 1 + 2 se cumplen → responde: {"aceptar": true}
- Hay mejoras arquitectónicas importantes → responde JSON completo mejorado

RESPONDE SOLO JSON. Sin texto. Sin explicaciones.
El formato exacto del JSON mejorado está en la Capa 2 del Engine.
"""

    # ── Helpers planner ───────────────────────────────────────

    def _extraer_json_plan(self, texto: str) -> dict:
        """Extrae y parsea el primer JSON válido del texto del modelo."""
        import re
        texto = texto.strip()
        if "```" in texto:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.DOTALL)
            if m:
                texto = m.group(1)
        inicio = texto.find("{")
        fin = texto.rfind("}") + 1
        if inicio >= 0 and fin > inicio:
            texto = texto[inicio:fin]
        return json.loads(texto)

    def _colocar_recintos_en_grid(self, plan: dict):
        """Ejecuta solo la fase de grilla del plan (sin dibujar), con auto-corrección."""
        g = plan.get("grid", {})
        self.ejecutor._crear_grid_diseno({
            "ancho_m": g.get("ancho_m", 10),
            "alto_m": g.get("alto_m", 8),
        })
        for rec in plan.get("recintos", []):
            params = {
                "nombre": rec["nombre"],
                "fila": int(rec.get("fila", 0)),
                "col": int(rec.get("col", 0)),
                "ancho_celdas": int(rec.get("ancho", rec.get("ancho_celdas", 2))),
                "alto_celdas": int(rec.get("alto", rec.get("alto_celdas", 2))),
            }
            r = self.ejecutor._colocar_recinto_en_grid(params)
            if "error" in r and r.get("sugerencia"):
                sug = r["sugerencia"]
                params["fila"] = sug["fila"]
                params["col"] = sug["col"]
                self.ejecutor._colocar_recinto_en_grid(params)

        def _log_compactar(m):
            self.after(0, self._log_actividad, m)

        self.ejecutor._compactar_recintos_flotantes(_log_compactar)

    def _reconstruir_plan_desde_grid(self, puertas_originales: list) -> dict:
        """Construye plan JSON desde el estado actual del grid (usa posiciones reales)."""
        g = self.ejecutor._grid
        recintos = []
        for nombre, info in g.recintos.items():
            recintos.append({
                "nombre": nombre,
                "fila": info["fila"],
                "col": info["col"],
                "ancho": info["ancho"],
                "alto": info["alto"],
            })
        return {
            "grid": {"ancho_m": g.ancho_m, "alto_m": g.alto_m},
            "recintos": recintos,
            "puertas": puertas_originales,
        }

    def _construir_mensaje_evaluacion(self, prompt_original: str, ascii_grid: str,
                                      val: dict, plan: dict, preview_b64: str = None) -> list:
        """
        Construye el mensaje para el evaluador.
        Si hay preview_b64 Y el proveedor soporta visión: envía imagen + texto.
        Si no: envía solo texto con ASCII grid.
        """
        score = val.get("score", 0)
        texto_contexto = (
            f"Solicitud original: {prompt_original}\n\n"
            f"Score de validación: {score}/100\n"
            f"Errores: {val.get('errores', [])}\n"
            f"Advertencias: {val.get('advertencias', [])}\n"
            f"Sugerencias del validador: {val.get('sugerencias', [])}\n\n"
            f"Plan JSON actual:\n{json.dumps(plan, ensure_ascii=False)}"
        )

        usa_vision = (
            preview_b64 is not None
            and self.cliente_ia.soporta_vision
        )

        if usa_vision:
            # Mensaje multimodal: imagen + texto
            # El modelo VE el preview con colores, proporciones y panel de score
            intro = (
                "Analiza este preview arquitectónico del layout generado.\n"
                "El panel derecho muestra score y errores del validador.\n"
                "Los colores identifican tipo de recinto. Las líneas punteadas "
                "muestran circulación entre recintos adyacentes.\n\n"
                + texto_contexto
            )

            tipo_proveedor = self.cliente_ia.tipo  # "anthropic" o "openai"

            if tipo_proveedor == "anthropic":
                contenido = [
                    {"type": "text", "text": intro},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": preview_b64,
                        },
                    },
                ]
            else:
                # OpenAI / OpenRouter / DeepSeek / etc.
                contenido = [
                    {"type": "text", "text": intro},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{preview_b64}"},
                    },
                ]
            return [{"role": "user", "content": contenido}]
        else:
            # Fallback texto: ASCII grid
            texto_ascii = (
                f"Solicitud original: {prompt_original}\n\n"
                f"GRID ASCII (fila 0=norte/arriba, fila_max=calle/abajo):\n{ascii_grid}\n\n"
                + texto_contexto
            )
            return [{"role": "user", "content": texto_ascii}]

    def _evaluar_y_mejorar_plan(self, plan: dict, prompt_original: str) -> dict:
        """
        Ronda 2: genera preview visual, lo envía a la IA con visión (o ASCII a modelos sin visión).
        Si score < 70 pide mejoras; si score >= 70 acepta.
        Retorna el plan final (original, mejorado, o reconstruido desde grid actual).
        """
        # Colocar recintos en grid temporal para feedback
        self._colocar_recintos_en_grid(plan)

        val = self.ejecutor._validar_grid({})
        score = val.get("score", 0)
        ascii_grid = self.ejecutor._grid.to_ascii()

        self.after(0, self._log_actividad, f"Evaluación inicial: Score {score}/100")

        # Generar preview PNG para feedback visual
        preview_b64 = None
        if self.cliente_ia.soporta_vision:
            try:
                prev_result = self.ejecutor._ver_preview_grid({})
                preview_b64 = prev_result.get("png_base64")
                if preview_b64:
                    self.after(0, self._log_actividad, "Preview generado — evaluación visual activada")
            except Exception as e:
                self.after(0, self._log_actividad, f"Preview no disponible: {e}")
        else:
            self.after(0, self._log_actividad, "Modelo sin visión — usando feedback ASCII")

        if score >= 70:
            self.after(0, self._log_actividad, "Layout aceptable — procediendo al dibujo")
            return self._reconstruir_plan_desde_grid(plan.get("puertas", []))

        # Score bajo — pedir mejoras con visión o ASCII
        modo = "visual" if preview_b64 else "ASCII"
        self.after(0, self._log_actividad, f"Score {score}/100 — solicitando mejoras ({modo})...")

        try:
            eval_msgs = self._construir_mensaje_evaluacion(
                prompt_original, ascii_grid, val, plan, preview_b64
            )
            # Neural CAD como base del evaluador (si tiene visión, ya tiene el cerebro)
            system_eval = (
                self.SYSTEM_NEURAL_CAD + "\n\n---\n\n" + self.SYSTEM_EVALUADOR
                if self.cliente_ia.soporta_vision
                else self.SYSTEM_EVALUADOR
            )
            respuesta_eval = self.cliente_ia.llamar(system_eval, eval_msgs, [])
            texto_eval = respuesta_eval.texto.strip()

            # Verificar si acepta
            if '"aceptar"' in texto_eval and "true" in texto_eval.lower():
                self.after(0, self._log_actividad, f"IA acepta el layout ({modo}) — procediendo")
                return self._reconstruir_plan_desde_grid(plan.get("puertas", []))

            # Intentar parsear plan mejorado
            plan_mejorado = self._extraer_json_plan(texto_eval)
            n_rec = len(plan_mejorado.get("recintos", []))
            self.after(0, self._log_actividad,
                f"Plan mejorado recibido ({modo}): {n_rec} recintos")
            return plan_mejorado

        except (json.JSONDecodeError, ValueError, Exception) as e:
            self.after(0, self._log_actividad, f"Sin mejoras válidas ({e}) — usando plan actual")
            return self._reconstruir_plan_desde_grid(plan.get("puertas", []))

    # ── Boceto fotográfico ────────────────────────────────────

    def _actualizar_descripcion(self, texto: str):
        """Actualiza el textbox de descripción (desde hilo secundario)."""
        self.txt_descripcion.delete("1.0", "end")
        self.txt_descripcion.insert("1.0", texto)

    def _cmd_importar_boceto(self):
        """Botón 'Desde Boceto': abre diálogo de archivo y lanza análisis."""
        if not self.cliente_ia:
            self._log_actividad("Error: configure un proveedor de IA primero")
            return

        if not self.cliente_ia.soporta_vision:
            self._log_actividad(
                f"El proveedor actual no soporta visión. "
                "Para importar bocetos usa Claude o GPT-4o."
            )
            return

        ruta = filedialog.askopenfilename(
            title="Seleccionar boceto arquitectónico",
            filetypes=[
                ("Imágenes", "*.jpg *.jpeg *.png *.bmp *.webp *.tiff *.heic"),
                ("Todos los archivos", "*.*"),
            ],
        )
        if not ruta:
            return

        # Nota adicional del arquitecto (si escribió algo en el textbox)
        texto_actual = self.txt_descripcion.get("1.0", "end").strip()
        placeholder = "Ej: Casa de 3 habitaciones, 2 banos, sala, cocina, comedor y cochera"
        descripcion_adicional = "" if texto_actual == placeholder else texto_actual

        if self._ejecutando:
            self._log_actividad("Ya hay una tarea en curso, espera a que termine")
            return

        self._ejecutando = True
        self._set_botones("disabled")
        threading.Thread(
            target=self._tarea_boceto,
            args=(ruta, descripcion_adicional),
            daemon=True,
        ).start()

    def _tarea_boceto(self, ruta_imagen: str, descripcion_adicional: str = ""):
        """
        Flujo: foto boceto → análisis visual → JSON plan → evaluación → AutoCAD.
        Mismo pipeline que _tarea_planner pero la Ronda 1 viene de la imagen.
        """
        try:
            nombre_archivo = os.path.basename(ruta_imagen)
            self.after(0, self._log_actividad, f"Boceto: {nombre_archivo}")
            self.after(0, lambda: self.progress.set(0.08))

            # ── Preparar imagen ────────────────────────────────
            from PIL import Image

            img = Image.open(ruta_imagen).convert("RGB")

            # Redimensionar si supera 2048px (límite razonable para APIs)
            max_dim = 2048
            if max(img.size) > max_dim:
                ratio = max_dim / max(img.size)
                nuevo_w = int(img.width * ratio)
                nuevo_h = int(img.height * ratio)
                img = img.resize((nuevo_w, nuevo_h), Image.LANCZOS)
                self.after(0, self._log_actividad,
                    f"Imagen ajustada a {nuevo_w}×{nuevo_h}px")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=92)
            b64 = base64.b64encode(buf.getvalue()).decode()
            media_type = "image/jpeg"

            self.after(0, self._log_actividad,
                f"Imagen lista ({len(b64) // 1024} KB) — enviando a IA...")
            self.after(0, lambda: self.progress.set(0.20))

            # ── Construir mensaje multimodal ───────────────────
            texto_prompt = "Analiza este boceto arquitectónico y genera el JSON de planta."
            if descripcion_adicional:
                texto_prompt += f"\nNota del arquitecto: {descripcion_adicional}"

            tipo_proveedor = self.cliente_ia.tipo
            if tipo_proveedor == "anthropic":
                contenido = [
                    {"type": "text", "text": texto_prompt},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                ]
            else:
                contenido = [
                    {"type": "text", "text": texto_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{b64}"},
                    },
                ]

            msgs = [{"role": "user", "content": contenido}]

            # ── Ronda 1: Boceto → JSON plan ────────────────────
            # Neural CAD + instrucciones de boceto + formato JSON
            system_boceto = (
                self.SYSTEM_NEURAL_CAD + "\n\n---\n\n" + self.SYSTEM_SKETCH
            )
            self.after(0, self._log_actividad,
                "Ronda 1 [Neural CAD]: interpretando boceto...")
            respuesta = self.cliente_ia.llamar(system_boceto, msgs, [])
            texto_resp = respuesta.texto

            plan = self._extraer_json_plan(texto_resp)

            g = plan.get("grid", {})
            recintos_nombres = [r["nombre"] for r in plan.get("recintos", [])]
            self.after(0, self._log_actividad,
                f"Boceto interpretado: {g.get('ancho_m')}×{g.get('alto_m')}m — "
                f"{', '.join(recintos_nombres)}")

            self.after(0, self._actualizar_descripcion,
                f"[Boceto] {nombre_archivo}\n"
                f"Recintos: {', '.join(recintos_nombres)}")

            self.after(0, lambda: self.progress.set(0.38))

            # ── Ronda 1.5: Confirmación del arquitecto ─────────
            self.after(0, self._log_actividad,
                "Mostrando interpretación — esperando confirmación...")

            resultado_dialogo = {}
            evento_dialogo = threading.Event()

            def _abrir_dialogo():
                VentanaConfirmacionBoceto(
                    self, plan, resultado_dialogo, evento_dialogo
                )

            self.after(0, _abrir_dialogo)
            evento_dialogo.wait(timeout=600)   # 10 min máximo

            accion = resultado_dialogo.get("accion", "cancelar")
            correccion = resultado_dialogo.get("correccion", "").strip()

            if accion == "cancelar":
                self.after(0, self._log_actividad, "Boceto cancelado por el arquitecto")
                return

            # ── Corrección si el arquitecto escribió algo ──────
            if correccion:
                self.after(0, self._log_actividad,
                    f"Corrigiendo interpretación: {correccion[:60]}...")
                self.after(0, lambda: self.progress.set(0.50))
                plan = self._reinterpretar_boceto(
                    plan, correccion, b64, media_type
                )
                recintos_nombres = [r["nombre"] for r in plan.get("recintos", [])]
                self.after(0, self._log_actividad,
                    f"Plan corregido: {', '.join(recintos_nombres)}")
            else:
                self.after(0, self._log_actividad, "Interpretación confirmada — procediendo")

            self.after(0, lambda: self.progress.set(0.55))

            # ── Ronda 2: Evaluar visualmente ──────────────────
            self.after(0, self._log_actividad, "Ronda 2: evaluando distribución...")
            contexto = f"Boceto: {nombre_archivo}. {descripcion_adicional}".strip(". ")
            plan_final = self._evaluar_y_mejorar_plan(plan, contexto)

            # ── Ronda 3: Dibujar en AutoCAD ───────────────────
            self.after(0, lambda: self.progress.set(0.65))
            self.after(0, self._log_actividad, "Ronda 3: dibujando en AutoCAD...")

            def log_boceto(msg):
                self.after(0, self._log_actividad, msg)

            resultado = self.ejecutor.ejecutar_plan_json(plan_final, on_log=log_boceto, forzar=True)

            self.after(0, lambda: self.progress.set(1.0))
            if resultado.get("ok"):
                score = resultado.get("validacion", {}).get("score", 0)
                self.after(0, self._log_actividad,
                    f"✓ Boceto convertido a planta — Score {score}/100")
                # ─── SLE: auto-guardar boceto aprobado ─────────────
                prompt_boceto = f"boceto: {nombre_archivo}"
                if descripcion_adicional:
                    prompt_boceto += f" | {descripcion_adicional}"
                self._guardar_plan_en_sle(plan_final, prompt_boceto, score)
            else:
                self.after(0, self._log_actividad,
                    f"Error al dibujar: {resultado.get('errores', '?')}")

        except json.JSONDecodeError as e:
            self.after(0, self._log_actividad, f"No se pudo interpretar el boceto: {e}")
        except FileNotFoundError:
            self.after(0, self._log_actividad, f"Archivo no encontrado: {ruta_imagen}")
        except Exception as e:
            self.after(0, self._log_actividad, f"Error en boceto: {e}")
        finally:
            self._ejecutando = False
            self.after(0, lambda: self._set_botones("normal"))
            self.after(0, lambda: self.progress.set(0))

    def _reinterpretar_boceto(self, plan_original: dict, correccion: str,
                              b64: str, media_type: str) -> dict:
        """
        Llama a la IA con el boceto original + el plan interpretado + la corrección
        del arquitecto. Retorna un plan JSON corregido.
        """
        prompt_correccion = (
            f"El arquitecto revisó la interpretación y pide esta corrección:\n"
            f"\"{correccion}\"\n\n"
            f"Plan anterior interpretado:\n"
            f"{json.dumps(plan_original, ensure_ascii=False, indent=2)}\n\n"
            f"Analiza nuevamente el boceto aplicando la corrección indicada "
            f"y genera el JSON completo corregido."
        )

        tipo_proveedor = self.cliente_ia.tipo
        if tipo_proveedor == "anthropic":
            contenido = [
                {"type": "text", "text": prompt_correccion},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64,
                    },
                },
            ]
        else:
            contenido = [
                {"type": "text", "text": prompt_correccion},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{b64}"},
                },
            ]

        system_corr = self.SYSTEM_NEURAL_CAD + "\n\n---\n\n" + self.SYSTEM_SKETCH
        msgs = [{"role": "user", "content": contenido}]
        try:
            resp = self.cliente_ia.llamar(system_corr, msgs, [])
            return self._extraer_json_plan(resp.texto)
        except (json.JSONDecodeError, Exception) as e:
            self.after(0, self._log_actividad,
                f"No se pudo parsear corrección ({e}) — usando plan anterior")
            return plan_original

    def _tarea_planner(self, prompt_usuario: str):
        """Flujo planificador con 2 rondas: generación → evaluación → dibujo con vanos."""
        texto = ""
        try:
            # ── Ronda 1: Generar JSON inicial ─────────────────
            # Neural CAD para providers con visión · Planner liviano para Ollama
            usar_neural = not self.cliente_ia.modo_planner
            system_gen = (
                self.SYSTEM_NEURAL_CAD + self._NEURAL_FORMATO_JSON
                if usar_neural else self.SYSTEM_PLANNER
            )
            modo_label = "Neural CAD" if usar_neural else "Planner"
            self.after(0, self._log_actividad,
                f"Ronda 1 [{modo_label}]: generando layout arquitectónico...")
            self.after(0, lambda: self.progress.set(0.15))

            msgs = [{"role": "user", "content": f"Genera el JSON de planta para: {prompt_usuario}"}]
            respuesta = self.cliente_ia.llamar(system_gen, msgs, [])
            texto = respuesta.texto
            plan = self._extraer_json_plan(texto)

            g = plan.get("grid", {})
            self.after(0, self._log_actividad,
                f"Plan inicial: {g.get('ancho_m')}×{g.get('alto_m')}m, "
                f"{len(plan.get('recintos', []))} recintos, {len(plan.get('puertas', []))} puertas")

            # ── Ronda 2: Evaluar y mejorar ──────────────────
            self.after(0, lambda: self.progress.set(0.40))
            plan_final = self._evaluar_y_mejorar_plan(plan, prompt_usuario)

            # ── Ronda 3: Ejecutar en AutoCAD con vanos ──────
            self.after(0, lambda: self.progress.set(0.60))
            self.after(0, self._log_actividad, "Ejecutando en AutoCAD con vanos de puertas...")

            def log_planner(msg):
                self.after(0, self._log_actividad, msg)

            resultado = self.ejecutor.ejecutar_plan_json(plan_final, on_log=log_planner, forzar=True)

            self.after(0, lambda: self.progress.set(1.0))
            if resultado.get("ok"):
                score_final = resultado.get("validacion", {}).get("score", 0)
                self.after(0, self._log_actividad,
                    f"✓ Planta generada — Score final {score_final}/100 — vanos y puertas integrados")
                # ─── SLE: auto-guardar plan aprobado ───────────────
                self._guardar_plan_en_sle(plan_final, self._ultimo_prompt or prompt_usuario, score_final)
            else:
                self.after(0, self._log_actividad,
                    f"Error: {resultado.get('errores', 'desconocido')}")

        except json.JSONDecodeError as e:
            self.after(0, self._log_actividad, f"Error JSON del plan: {e}")
            self.after(0, self._log_actividad, f"Respuesta (inicio): {texto[:300]}")
        except Exception as e:
            self.after(0, self._log_actividad, f"Error en planner: {e}")
        finally:
            self._ejecutando = False
            self.after(0, lambda: self._set_botones("normal"))
            self.after(0, lambda: self.progress.set(0))

    # ── Log ──────────────────────────────────────────────────

    def _log_actividad(self, mensaje: str):
        ts = datetime.now().strftime("%H:%M:%S")
        def _write():
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", f"[{ts}] {mensaje}\n")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        if threading.current_thread() is threading.main_thread():
            _write()
        else:
            self.after(0, _write)

    def _limpiar_log(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")
        self._log_actividad("Log limpiado")

    def _abrir_normativa(self):
        VentanaNormativa(self)

    def _abrir_conocimiento(self):
        VentanaConocimiento(self)

    def _abrir_sle(self):
        VentanaSLE(self, self._sle_memoria, self._sle_captura)

    def _guardar_plan_en_sle(self, plan: dict, prompt: str, score: int):
        """Guarda un plan aprobado en la Memoria del SLE (no bloqueante).
        También escribe sle/data/baseline_plan.json para que engine.py pueda
        detectar correcciones hechas sobre la planta en el CAD (comando SLECORR).
        """
        if not self._sle_captura or not plan:
            return
        def _guardar():
            try:
                import json
                from pathlib import Path

                pid = self._sle_captura.registrar_aprobacion_directa(
                    plan=plan, prompt=prompt, score=score,
                )
                self.after(0, self._log_actividad, f"SLE: plan guardado (id={pid})")

                # ── Escribir baseline para engine.py (SLECORR) ────────
                try:
                    baseline_path = (
                        Path(__file__).parent / "sle" / "data" / "baseline_plan.json"
                    )
                    baseline_path.parent.mkdir(parents=True, exist_ok=True)
                    baseline_path.write_text(
                        json.dumps({"prompt": prompt, "plan": plan},
                                   ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass

                # Actualizar badge del botón SLE con número de proyectos
                if self._sle_memoria:
                    stats = self._sle_memoria.estadisticas()
                    n = stats.get("n_aprobados", 0)
                    self.after(0, self.btn_sle.configure, {"text": f"SLE — Memoria IA  ({n})"})
            except Exception as e:
                self.after(0, self._log_actividad, f"SLE: no se guardó ({e})")
        threading.Thread(target=_guardar, daemon=True).start()

    def _seleccionar_modulo(self, modulo: str):
        """Alterna entre el panel AutoCAD y el panel Diseño Paso 0."""
        if modulo == "autocad":
            self.panel_autocad.grid()
            if hasattr(self, "panel_diagrama"):
                self.panel_diagrama.grid_remove()
            self.btn_autocad.configure(fg_color=ACCENT, text_color=TEXT_PRIMARY)
            self.btn_diagrama.configure(fg_color="transparent", text_color=TEXT_SECONDARY)

        elif modulo == "diagrama" and _DIAGRAMA_DISPONIBLE:
            self.panel_autocad.grid_remove()
            if not hasattr(self, "panel_diagrama"):
                self._crear_panel_diagrama()
            self.panel_diagrama.grid()
            self.btn_autocad.configure(fg_color="transparent", text_color=TEXT_SECONDARY)
            self.btn_diagrama.configure(fg_color="#065F46", text_color=TEXT_PRIMARY)

    # ════════════════════════════════════════════════════════════════
    # PANEL DISEÑO — PASO 0
    # ════════════════════════════════════════════════════════════════

    def _crear_panel_diagrama(self):
        """Crea el panel del Paso 0: Programa + Diagrama Funcional + Reglas Merlos."""
        p = ctk.CTkFrame(self, fg_color=BG_DARK, corner_radius=0)
        p.grid(row=0, column=1, sticky="nsew")
        p.grid_columnconfigure(0, weight=1)
        p.grid_rowconfigure(1, weight=1)
        self.panel_diagrama = p

        # ── Header ──────────────────────────────────────────────────
        hdr = ctk.CTkFrame(p, fg_color="transparent", height=60)
        hdr.grid(row=0, column=0, sticky="ew", padx=25, pady=(20, 8))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            hdr, text="Diseno — Paso 0",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            hdr, text="Programa arquitectonico + Diagrama funcional + Reglas Merlos",
            font=ctk.CTkFont(size=12), text_color=TEXT_SECONDARY,
        ).grid(row=1, column=0, sticky="w")

        # ── Cuerpo: izquierda (formulario) + derecha (resultados) ───
        body = ctk.CTkFrame(p, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=25, pady=(0, 15))
        body.grid_columnconfigure(0, weight=0, minsize=280)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self._crear_form_diagrama(body)
        self._crear_resultado_diagrama(body)

    def _crear_form_diagrama(self, parent):
        """Formulario izquierdo: datos del proyecto."""
        form = ctk.CTkScrollableFrame(
            parent, fg_color=BG_PANEL, corner_radius=12, width=270,
        )
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        form.grid_columnconfigure(0, weight=1)

        def lbl(texto, bold=False, color=TEXT_PRIMARY):
            ctk.CTkLabel(
                form, text=texto, anchor="w",
                font=ctk.CTkFont(size=12, weight="bold" if bold else "normal"),
                text_color=color,
            ).pack(fill="x", padx=14, pady=(6, 1))

        def sep():
            ctk.CTkFrame(form, height=1, fg_color=BORDER).pack(fill="x", padx=14, pady=8)

        # Nombre
        lbl("Nombre del proyecto", bold=True)
        self.diag_nombre = ctk.CTkEntry(
            form, placeholder_text="Casa Familia...",
            fg_color=BG_CARD, border_color=BORDER, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        self.diag_nombre.pack(fill="x", padx=14, pady=(0, 4))

        sep()

        # Área total
        lbl("Area total (m2)", bold=True)
        self.diag_m2 = ctk.CTkEntry(
            form, placeholder_text="120",
            fg_color=BG_CARD, border_color=BORDER, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=13),
        )
        self.diag_m2.pack(fill="x", padx=14, pady=(0, 4))

        # Dormitorios
        lbl("Dormitorios", bold=True)
        fila_dorm = ctk.CTkFrame(form, fg_color="transparent")
        fila_dorm.pack(fill="x", padx=14, pady=(0, 4))
        self.diag_dorm = ctk.IntVar(value=3)
        for n in [1, 2, 3, 4]:
            ctk.CTkRadioButton(
                fila_dorm, text=str(n), variable=self.diag_dorm, value=n,
                font=ctk.CTkFont(size=13), text_color=TEXT_PRIMARY,
                fg_color=ACCENT,
            ).pack(side="left", padx=6)

        sep()

        # Norte del lote
        lbl("Norte del lote", bold=True)
        self.diag_norte = ctk.CTkOptionMenu(
            form,
            values=["norte", "sur", "este", "oeste"],
            fg_color=BG_CARD, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            text_color=TEXT_PRIMARY, font=ctk.CTkFont(size=12),
        )
        self.diag_norte.set("norte")
        self.diag_norte.pack(fill="x", padx=14, pady=(0, 4))

        sep()

        # Opcionales
        lbl("Espacios opcionales", bold=True)
        self._diag_opts: dict[str, ctk.BooleanVar] = {}
        opts = [("cochera", "Cochera"), ("sala_tv", "Sala TV"),
                ("estudio", "Estudio"), ("piscina", "Piscina")]
        for key, label in opts:
            var = ctk.BooleanVar(value=(key == "cochera"))
            self._diag_opts[key] = var
            ctk.CTkCheckBox(
                form, text=label, variable=var,
                font=ctk.CTkFont(size=12), text_color=TEXT_PRIMARY,
                fg_color=ACCENT, hover_color=ACCENT_HOVER,
            ).pack(anchor="w", padx=18, pady=2)

        sep()

        # Origen
        lbl("Origen del diagrama", bold=True)
        self.diag_origen = ctk.StringVar(value="auto")
        for val, label, desc in [
            ("auto",       "Generar desde brief",
             "El sistema crea el diagrama automaticamente"),
            ("arquitecto", "Diagrama del arquitecto",
             "Sube tu diagrama — sus decisiones mandan sobre R1/R2"),
        ]:
            ctk.CTkRadioButton(
                form, text=label, variable=self.diag_origen, value=val,
                font=ctk.CTkFont(size=12), text_color=TEXT_PRIMARY,
                fg_color="#065F46",
                command=self._toggle_origen_diagrama,
            ).pack(anchor="w", padx=18, pady=(4, 0))
            ctk.CTkLabel(
                form, text=desc, anchor="w",
                font=ctk.CTkFont(size=10), text_color=TEXT_SECONDARY,
            ).pack(anchor="w", padx=32, pady=(0, 2))

        # Botón cargar imagen (solo si origen=arquitecto)
        self.btn_cargar_img = ctk.CTkButton(
            form, text="  Cargar imagen del diagrama",
            fg_color=BG_CARD, hover_color=BORDER, text_color=TEXT_SECONDARY,
            font=ctk.CTkFont(size=11), corner_radius=8, height=32,
            command=self._cmd_cargar_imagen_diagrama,
            state="disabled",
        )
        self.btn_cargar_img.pack(fill="x", padx=14, pady=(4, 2))
        self.lbl_img_cargada = ctk.CTkLabel(
            form, text="", anchor="w",
            font=ctk.CTkFont(size=10), text_color=SUCCESS,
        )
        self.lbl_img_cargada.pack(fill="x", padx=14)
        self._diag_imagen_path: str = ""

        sep()

        # Botón generar
        self.btn_generar_diag = ctk.CTkButton(
            form, text="Generar Paso 0", height=42,
            fg_color="#065F46", hover_color="#047857",
            font=ctk.CTkFont(size=14, weight="bold"), corner_radius=8,
            command=self._cmd_generar_diagrama,
        )
        self.btn_generar_diag.pack(fill="x", padx=14, pady=(0, 6))

        self.diag_progress = ctk.CTkProgressBar(
            form, fg_color=BG_CARD, progress_color="#065F46", height=5, corner_radius=3,
        )
        self.diag_progress.pack(fill="x", padx=14, pady=(0, 10))
        self.diag_progress.set(0)

    def _crear_resultado_diagrama(self, parent):
        """Panel derecho: resultado con score + texto completo."""
        res = ctk.CTkFrame(parent, fg_color=BG_PANEL, corner_radius=12)
        res.grid(row=0, column=1, sticky="nsew")
        res.grid_columnconfigure(0, weight=1)
        res.grid_rowconfigure(1, weight=1)

        # Score banner
        score_bar = ctk.CTkFrame(res, fg_color=BG_CARD, corner_radius=8)
        score_bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        score_bar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            score_bar, text="Score Merlos",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT_SECONDARY,
        ).grid(row=0, column=0, padx=14, pady=8)

        self.diag_lbl_score = ctk.CTkLabel(
            score_bar, text="— / 100",
            font=ctk.CTkFont(size=20, weight="bold"), text_color=ACCENT,
        )
        self.diag_lbl_score.grid(row=0, column=1, sticky="w", padx=6)

        self.diag_score_bar = ctk.CTkProgressBar(
            score_bar, fg_color=BG_PANEL, progress_color=SUCCESS,
            height=8, corner_radius=4, width=180,
        )
        self.diag_score_bar.grid(row=0, column=2, padx=(0, 14), pady=8)
        self.diag_score_bar.set(0)

        # Textbox de resultado
        self.diag_txt_resultado = ctk.CTkTextbox(
            res, fg_color=BG_DARK, text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Courier New", size=11),
            wrap="none", corner_radius=0,
        )
        self.diag_txt_resultado.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 0))
        self.diag_txt_resultado.insert("1.0",
            "Configure el proyecto en el formulario y presione 'Generar Paso 0'.\n\n"
            "El sistema ejecutara:\n"
            "  1. Programa arquitectonico (areas + normativa INVU/CFIA)\n"
            "  2. Diagrama funcional (burbujas + zonas + orientacion)\n"
            "  3. Analisis Reglas Merlos (score + violaciones)\n"
        )
        self.diag_txt_resultado.configure(state="disabled")

    def _toggle_origen_diagrama(self):
        """Activa/desactiva el botón de imagen según el origen seleccionado."""
        es_arq = self.diag_origen.get() == "arquitecto"
        self.btn_cargar_img.configure(
            state="normal" if es_arq else "disabled",
            text_color=TEXT_PRIMARY if es_arq else TEXT_SECONDARY,
            fg_color=BG_CARD if es_arq else BG_DARK,
        )
        if not es_arq:
            self._diag_imagen_path = ""
            self.lbl_img_cargada.configure(text="")

    def _cmd_cargar_imagen_diagrama(self):
        """Abre un selector de archivo para cargar el diagrama del arquitecto."""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Seleccionar imagen del diagrama",
            filetypes=[("Imagenes", "*.png *.jpg *.jpeg *.webp *.gif"),
                       ("Todos", "*.*")],
        )
        if path:
            self._diag_imagen_path = path
            nombre = os.path.basename(path)
            self.lbl_img_cargada.configure(text=f"  {nombre}")

    def _cmd_generar_diagrama(self):
        """Ejecuta el ciclo Paso 0 en un hilo secundario."""
        if self._ejecutando:
            return

        # Validar entrada
        try:
            m2 = float(self.diag_m2.get().strip() or "0")
            if m2 < 30:
                self._diag_mostrar_error("Ingrese el area total en m2 (minimo 30 m2).")
                return
        except ValueError:
            self._diag_mostrar_error("El area debe ser un numero (ej: 120).")
            return

        n_dorm    = self.diag_dorm.get()
        norte     = self.diag_norte.get()
        nombre    = self.diag_nombre.get().strip() or "Proyecto Merlos"
        opcionales = [k for k, v in self._diag_opts.items() if v.get()]
        origen    = self.diag_origen.get()
        img_path  = self._diag_imagen_path

        # Bloquear UI
        self._ejecutando = True
        self.btn_generar_diag.configure(state="disabled", text="Procesando...")
        self.diag_progress.set(0)
        self._diag_animar_progress(True)

        def _tarea():
            try:
                resultado = None

                # ── Camino A: imagen del arquitecto ──────────────────
                if origen == "arquitecto" and img_path:
                    resultado = self._diag_desde_imagen(
                        img_path, nombre, norte, m2
                    )

                # ── Camino B: brief de texto ──────────────────────────
                if resultado is None:
                    resultado = ciclo_completo(
                        total_m2=m2,
                        n_dormitorios=n_dorm,
                        norte_lote=norte,
                        opcionales=opcionales,
                        nombre_proyecto=nombre,
                    )
                    if origen == "arquitecto":
                        resultado["diagrama"].origen = "arquitecto"
                        aplicar_jerarquia_diagrama(
                            resultado["reglas"], resultado["diagrama"]
                        )

                self.after(0, lambda r=resultado: self._diag_mostrar_resultado(r))

            except Exception as e:
                self.after(0, lambda err=str(e): self._diag_mostrar_error(err))
            finally:
                self.after(0, self._diag_finalizar)

        threading.Thread(target=_tarea, daemon=True).start()

    def _diag_desde_imagen(self, img_path: str, nombre: str,
                            norte: str, m2: float) -> dict:
        """
        Llama al proveedor IA con la imagen y extrae el DiagramaFuncional.
        Luego completa el ciclo (grafo + reglas).
        """
        import base64
        from sle.core.spatial_graph import SpatialGraph
        from sle.core.reglas_merlos import analizar_reglas_merlos

        # Leer imagen
        with open(img_path, "rb") as f:
            img_bytes = f.read()
        img_b64 = base64.standard_b64encode(img_bytes).decode()

        ext = os.path.splitext(img_path)[1].lower().lstrip(".")
        media_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
        media_type = media_map.get(ext, "image/png")

        # Llamar al proveedor IA
        if not self.cliente_ia:
            raise RuntimeError("No hay proveedor IA configurado.")

        respuesta = self.cliente_ia.cliente.messages.create(
            model=self.cliente_ia.modelo,
            max_tokens=4096,
            system=prompt_lectura_imagen(),
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Analiza este diagrama de burbujas.\n"
                            f"Proyecto: {nombre}\n"
                            f"Norte del lote: {norte}\n"
                            f"Area total aproximada: {m2} m2\n"
                            f"Extrae todos los espacios, relaciones y orientacion "
                            f"y retorna SOLO el JSON."
                        ),
                    },
                ],
            }],
        )

        texto = respuesta.content[0].text.strip()
        # Limpiar si viene con ```json ... ```
        if texto.startswith("```"):
            texto = texto.split("```")[1]
            if texto.startswith("json"):
                texto = texto[4:]
        texto = texto.strip()

        import json as _json
        data = _json.loads(texto)
        data["nombre_proyecto"] = nombre
        data["norte_lote"]      = norte
        data.setdefault("area_total_m2", m2)

        diagrama = desde_dict(data)   # origen="arquitecto" ya lo pone desde_dict
        diagrama.nombre_proyecto = nombre
        diagrama.norte_lote      = norte

        # Completar ciclo
        from sle.core.diagrama_conceptual import a_spatial_graph
        grafo  = a_spatial_graph(diagrama)
        reglas = analizar_reglas_merlos(grafo)
        aplicar_jerarquia_diagrama(reglas, diagrama)

        texto_res = (
            texto_programa.__doc__ and "" or ""   # placeholder
        )
        texto_res = (
            texto_diagrama(diagrama)
            + "\n" + "=" * 50 + "\n"
            + texto_analisis_merlos(reglas)
        )

        return {
            "programa":  None,
            "diagrama":  diagrama,
            "grafo":     grafo,
            "reglas":    reglas,
            "texto":     texto_res,
        }

    def _diag_mostrar_resultado(self, resultado: dict):
        """Actualiza la UI con el resultado del ciclo Paso 0."""
        reglas = resultado["reglas"]
        score  = reglas.score_total

        # Score banner
        color_score = SUCCESS if score >= 70 else (WARNING if score >= 50 else ERROR)
        self.diag_lbl_score.configure(
            text=f"{score} / 100", text_color=color_score,
        )
        self.diag_score_bar.configure(progress_color=color_score)
        self.diag_score_bar.set(score / 100)

        # Texto resultado
        self.diag_txt_resultado.configure(state="normal")
        self.diag_txt_resultado.delete("1.0", "end")
        self.diag_txt_resultado.insert("1.0", resultado["texto"])
        self.diag_txt_resultado.configure(state="disabled")

        # ── Contexto compartido para IA del CAD Visor ─────────────────
        try:
            import json as _json
            _cfg2 = {}
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, encoding="utf-8") as _f2:
                    _cfg2 = _json.load(_f2)
            _cfg2.setdefault("proyecto_activo", {})
            nombre = getattr(self, "diag_nombre", None)
            nombre = nombre.get().strip() if nombre else "Proyecto"
            norte  = getattr(self, "diag_norte",  None)
            norte  = norte.get() if norte else "norte"
            # Observaciones: violaciones + sugerencias del análisis reglas Merlos
            obs = (
                [v.descripcion for v in reglas.violaciones[:4]]
                + [s for s in reglas.sugerencias[:3]]
            )
            _cfg2["proyecto_activo"]["diseno_paso0"] = {
                "score":         score,
                "nombre":        nombre,
                "norte":         norte,
                "observaciones": obs,
            }
            with open(CONFIG_PATH, "w", encoding="utf-8") as _f2:
                _json.dump(_cfg2, _f2, indent=4, ensure_ascii=False)
        except Exception:
            pass

    def _diag_mostrar_error(self, mensaje: str):
        """Muestra un error en el textbox de resultado."""
        self.diag_txt_resultado.configure(state="normal")
        self.diag_txt_resultado.delete("1.0", "end")
        self.diag_txt_resultado.insert("1.0", f"[ERROR]\n\n{mensaje}")
        self.diag_txt_resultado.configure(state="disabled")

    def _diag_finalizar(self):
        """Restaura la UI después de procesar."""
        self._ejecutando = False
        self.btn_generar_diag.configure(state="normal", text="Generar Paso 0")
        self._diag_animar_progress(False)
        self.diag_progress.set(1)

    def _diag_animar_progress(self, activo: bool):
        """Anima la barra de progreso mientras procesa."""
        if activo and self._ejecutando:
            v = self.diag_progress.get()
            self.diag_progress.set((v + 0.03) % 1.0)
            self.after(80, lambda: self._diag_animar_progress(True))


def main():
    app = EstudioMerlosAI()
    app.mainloop()


if __name__ == "__main__":
    main()
