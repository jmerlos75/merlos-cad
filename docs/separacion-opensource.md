# Separación Open Source — Estudio Merlos CAD
> Instrucciones para crear la versión pública del CAD, sin IA, sin módulos propietarios.  
> El resultado es un CAD desktop completo y funcional, listo para GitHub.  
> La versión comercial (con IA, normativa, bloques, plotting) queda en repositorio privado separado.

---

## 1. Estrategia

Crear una **copia limpia** del proyecto. No modificar el original.

```bash
# Crear copia del proyecto
cp -r "Estudio Merlos AI" "Estudio Merlos CAD OSS"
cd "Estudio Merlos CAD OSS"

# Crear repositorio git limpio (sin historial que pueda contener credenciales)
rm -rf .git
git init
git add .
git commit -m "Initial commit — Estudio Merlos CAD open source"
```

**¿Por qué git nuevo?** El historial del repo privado puede contener API keys, rutas personales, o datos del estudio en commits anteriores. Un repo limpio elimina ese riesgo.

---

## 2. Archivos — qué incluir y qué excluir

### ✅ Incluir sin cambios

| Archivo | Razón |
|---|---|
| `cad/entities.py` | Core del dominio — puro Python, sin dependencias propietarias |
| `cad/viewport.py` | Transforms matemáticos — puro Python |
| `cad/renderer_base.py` | Interfaz abstracta — necesaria para ambos renderers |
| `cad/renderer_opengl.py` | El renderer GL es el activo técnico principal del OSS |
| `cad/renderer_pil.py` | Fallback PIL — parte del core de render |
| `cad/dxf_import.py` | DXF es el formato universal — debe incluirse |
| `cad/dxf_export.py` | Ídem |
| `cad/dxf_roundtrip.py` | Validación DXF |
| `cad/dwg_converter.py` | Utilidad de conversión |
| `cad/viewport.py` | Ya mencionado |
| `cad/i18n.py` | Internacionalización — útil para la comunidad |
| `cad/__init__.py` | Necesario |

### ❌ Eliminar completamente

| Archivo/Directorio | Razón |
|---|---|
| `cad/mcp_server.py` | MCP server para AutoCAD — módulo propietario |
| `cad/layout.py` | Sistema de layouts/paper space — se excluye |
| `sle/` (directorio completo) | Motor de aprendizaje (SLE) — propietario |
| `hub/` o módulos del hub central | Hub de módulos — propietario |
| `dwg_to_sle.py` | Integración SLE — propietario |
| `app.py` (si existe aparte del engine) | Entry point del hub — propietario |
| Cualquier archivo `*.key`, `*.env`, `settings.json` con API keys | Credenciales |

### ⚠️ `cad/engine.py` — modificar (ver Sección 3)

Este es el trabajo principal. El 80% del archivo se conserva. Se elimina quirúrgicamente lo propietario.

---

## 3. Cirugía en `engine.py`

El archivo tiene 20 897 líneas. Lo que se elimina es específico y está bien delimitado.

---

### 3.1 Imports — eliminar

Buscar y eliminar estos imports en las primeras ~100 líneas:

```python
# ELIMINAR — SLE (motor de aprendizaje)
from sle.core.memory import Memoria as _SLEMem
from sle.integration.correction_capture import CapturaCorrecciones as _CC

# ELIMINAR — Layout/plotting
from cad.layout import (
    LayoutSheet, ViewportDef, PAPER_SIZES_MM,
    paper_size, default_viewport, auto_fit_viewport,
    layout_to_dict, layout_from_dict, render_layout_pil,
    VIEWPORT_SCALES, TB_HEIGHT_MM,
    get_scales, METRIC_SCALES, IMPERIAL_SCALES,
)

# ELIMINAR — MCP server (si se importa en engine.py)
# buscar: from cad.mcp_server import ...
```

Los imports que se **conservan**:
```python
# CONSERVAR — todo esto va al OSS
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import customtkinter as ctk
from cad.entities import (...)    # todo
from cad.viewport import (...)    # todo
from cad.renderer_pil import RendererPIL, RenderCtx, build_layer_cache, resolve_entity_props
from cad.renderer_base import select_renderer
from cad.i18n import (...)        # todo
```

---

### 3.2 `__init__` — eliminar atributos SLE

Líneas ~511–519 en `engine.py`:

```python
# ELIMINAR este bloque completo del __init__:
self._sle_disponible  = False
self._sle_memoria     = None
self._sle_captura     = None
self._sle_prompt_cache = ""
try:
    from sle.core.memory import Memoria as _SLEMem
    from sle.integration.correction_capture import CapturaCorrecciones as _CC
    self._sle_memoria    = _SLEMem()
    self._sle_captura    = _CC(self._sle_memoria)
    self._sle_disponible = True
except Exception:
    pass
```

También eliminar:
```python
self._ia_streaming = False   # línea ~674
```

---

### 3.3 Menú — eliminar entradas propietarias

En `_build_menubar()` (~línea 867) y `_rebuild_menubar()` (~línea 1175):

**Eliminar el menú Hub completo.** Buscar el bloque que crea `hcm` (Hub/Módulos):
```python
# ELIMINAR — menú Hub con sus sub-items:
# "Terreno del Lote"        → _hub_lanzar("terreno")
# "Programa Arquitectónico" → _hub_lanzar("programa")
# "Diseño IA"               → _hub_lanzar("diseno")
# "DWG Extractor"           → _hub_lanzar("dwg")
```

**Eliminar del menú IA:**
```python
# ELIMINAR — ítem "Abrir asistente" (línea ~1148):
_cmd(iam, "Abrir asistente", "/", command=self._focus_ia_input, icon=_ic("ia_chat"))
```

**Eliminar del menú Archivo:**
```python
# ELIMINAR:
# "Exportar PDF"       → _exportar_pdf
# "Exportar PDF (layouts)" → _exportar_pdf_layouts
# "Plot / Imprimir"    → _plot_dialog
# "Exportar PDF rápido" → _exportar_pdf_rapido
```

**Conservar en menú Archivo:**
```python
# CONSERVAR:
# "Nuevo"             → _new_dwg
# "Abrir..."          → _abrir_json
# "Guardar"           → _guardar_json
# "Guardar como..."   → _guardar_json_como
# "Importar DXF..."   → _importar_dxf
# "Exportar DXF..."   → _exportar_dxf
# "Exportar PNG..."   → _exportar_png   ← esta se conserva
```

---

### 3.4 Toolbar — eliminar botón IA

En `_populate_top_toolbar()` (~línea 1768) y `_build_ui()` (~línea 1265):

```python
# ELIMINAR — botón IA en el toolbar superior (líneas ~1350-1355):
_ia_btn = ctk.CTkButton(
    text="✦ IA",
    command=self._focus_ia_input)
_ia_btn.pack(...)
_Tooltip(_ia_btn, "Asistente IA  [/consulta + Enter]")
```

---

### 3.5 Barra de comandos — simplificar

La barra de comandos en `_build_cmd_bar()` (~línea 1516) tiene dos modos: CAD y IA (cuando el input empieza con `/`).

**Qué eliminar:**

1. El badge toggle CAD/IA (líneas ~1574–1591). Reemplazar el botón dinámico por un label estático:
```python
# ELIMINAR (el botón que alterna CAD ↔ IA):
def _toggle_ia_mode():
    raw = self._cmd_var.get()
    if raw.startswith("/"):
        self._cmd_var.set("")
        ...
    else:
        self._cmd_var.set("/")
        ...

self._cmd_prompt_lbl = ctk.CTkButton(
    cf, text="CAD",
    command=_toggle_ia_mode)   # ← esto desaparece

# REEMPLAZAR por label estático simple:
self._cmd_prompt_lbl = tk.Label(
    cf, text="CMD",
    font=("Courier New", 10, "bold"),
    fg="#0A0A0A", bg=CV_CMD_FG,
    padx=6, pady=2)
self._cmd_prompt_lbl.pack(side="left", padx=(8, 6), pady=5)
```

2. En `_on_cmd_change()` (~línea 14527) y `_ejecutar_comando()` (~línea ~13200): eliminar la rama que detecta `/` y llama a `_ejecutar_ia`:
```python
# ELIMINAR en _ejecutar_comando() o donde se detecte el prefijo IA:
if raw.startswith("/"):
    ia_prompt = raw[1:].strip()
    self._ia_streaming = True
    # ... todo el bloque de dispatch a _ejecutar_ia
    return
```

3. Los tags de color "ai" y "resp" en el historial se pueden conservar o simplificar:
```python
# CONSERVAR (son solo estilos de texto, no hacen daño):
self._hist_txt.tag_configure("ai",   foreground="#38BDF8")
self._hist_txt.tag_configure("resp", foreground=UI_WARN)
# O simplificar a solo "cad" y "err" si se prefiere
```

---

### 3.6 Métodos a eliminar completamente

Los siguientes métodos se eliminan del cuerpo de la clase. Cada uno está claramente delimitado por `def nombre(self` y el siguiente `def ` al mismo nivel de indentación.

#### IA / AI Bridge
| Método | Línea aprox. | Descripción |
|---|---|---|
| `_ejecutar_ia` | 14582 | Despacha prompt a la IA |
| `_llamar_ia_stream` | 17563 | Stream de respuesta IA |
| `_llamar_ia` | 17647 | Llamada síncrona a IA |
| `_parsear_respuesta_ia` | 17737 | Parsea JSON de la IA |
| `_ia_respuesta` | 17777 | Aplica respuesta IA al canvas |
| `_construir_ctx_proyecto` | 17501 | Contexto de proyecto para la IA |
| `_focus_ia_input` | 3484 | Foco al input IA |
| `_test_ia_connection` | 14851 | Prueba conexión con proveedor IA |
| `_guardar_config_ia` | 14832 | Guarda config de IA |
| `_leer_config_ia` | 14647 | Lee config de IA |

#### Hub / Módulos externos
| Método | Línea aprox. | Descripción |
|---|---|---|
| `_hub_lanzar` | 839 | Lanza módulos externos del hub |

#### SLE (Motor de aprendizaje)
| Método | Línea aprox. | Descripción |
|---|---|---|
| `_registrar_correccion_sle` | 14017 | Registra correcciones en SLE |
| `_ejecutar_slecorr` | ~14030 | Comando SLECORR |

#### Layouts / Paper Space / Plotting
| Método | Línea aprox. | Descripción |
|---|---|---|
| `_build_layout_tabs` | 11318 | Pestañas de layouts |
| `_new_layout` | 11422 | Crear nuevo layout |
| `_delete_layout` | 11493 | Eliminar layout |
| `_rename_layout_dialog` | 11509 | Renombrar layout |
| `_switch_to_layout` | 11543 | Cambiar de layout |
| `_fit_layout_view` | 11579 | Fit de viewport en layout |
| `_redraw_static_layout` | 11602 | Redraw layout |
| `_layout_viewport_fit` | 11656 | Fit viewport |
| `_page_setup_dialog` | 11679 | Setup de página |
| `_exportar_pdf` | 19418 | Exportar PDF |
| `_exportar_pdf_layouts` | 19428 | Exportar PDF con layouts |
| `_plot_dialog` | 19570 | Dialog de plot |
| `_plot_pick_window` | 20015 | Selección de ventana para plot |
| `_plot_render` | 20037 | Render para plot |
| `_exportar_pdf_rapido` | 20256 | PDF rápido |

#### Config — tab IA
En `_open_config()` (~línea 14877): eliminar la pestaña/sección de configuración de IA y proveedores. Conservar las pestañas de General, Snap, Colores, Dimensiones, Texto, Visual.

---

### 3.7 Comando SLECORR — eliminar del alias map

En `_CMD_ALIASES` (~línea 127) o donde esté registrado:
```python
# ELIMINAR:
"SLECORR": "slecorr",
```

---

### 3.8 Referencias residuales a SLE en código existente

Buscar y eliminar con grep:
```bash
grep -n "_sle_\|_registrar_correccion\|sle_disponible\|slecorr" cad/engine.py
```

Cada línea encontrada será un `if self._sle_disponible:` que wrappea un bloque. Eliminar el bloque completo incluido el `if`.

Ejemplo (~línea 5614):
```python
# ELIMINAR este bloque:
if self._sle_disponible:
    tk.Button(..., command=self._registrar_correccion_sle)
```

---

## 4. `_on_cmd_change` — simplificar

Este método (~línea 14527) cambia el color y el prompt según si el input empieza con `/`. En la versión OSS no hay modo IA, así que se simplifica:

```python
# VERSIÓN OSS de _on_cmd_change:
def _on_cmd_change(self, *_):
    """Actualiza hint semántico mientras el usuario escribe."""
    raw = self._cmd_var.get().upper().strip()
    # Autocompletar hint
    hint = self._get_cmd_hint(raw)
    if hasattr(self, '_cmd_hint_lbl'):
        self._cmd_hint_lbl.configure(text=hint)
```

Eliminar toda la lógica de detección `/` y cambio de color del badge CAD↔IA.

---

## 5. Configuración — tab IA

En `_open_config()` (~línea 14877), la función construye pestañas. Eliminar la pestaña/sección "IA / Asistente" completa, que incluye:
- Selector de proveedor (OpenAI, Anthropic, Gemini, etc.)
- Campo de API key
- Selector de modelo
- Botón "Probar conexión"
- Todo el bloque `_lbl_ia`, `_on_prov`, `_test`, `_save_ia` que están anidados dentro de `_open_config`

Conservar todas las demás pestañas: General, Snap, Colores, Dimensiones, Texto, Visual, Rendimiento.

---

## 6. `settings.json` — limpiar antes de incluir

El archivo de configuración puede contener:
- API keys guardadas
- Rutas absolutas del sistema del estudio
- Datos de proyectos

**Acción:** Incluir un `settings.default.json` vacío como plantilla, y agregar `settings.json` al `.gitignore`.

```json
// settings.default.json — lo que va al repo
{
  "rendering": { "backend": "auto" },
  "snap": { "endpoint": true, "midpoint": true, "center": true },
  "units": "mm",
  "language": "es"
}
```

---

## 7. README para el repo OSS

Crear `README.md` con:

```markdown
# Estudio Merlos CAD

CAD desktop de código abierto con render OpenGL, compatible con comandos AutoCAD.

## Características
- Render OpenGL de alta performance (100k+ entidades)
- Snap inteligente: endpoint, midpoint, center, intersection, perpendicular, tangent
- Grips para edición directa
- Layers, linetypes, linewidths
- Importar/Exportar DXF
- Exportar PNG
- Comandos AutoCAD-compatibles: L, C, A, PL, TRIM, OFFSET, MIRROR, ARRAY...
- Undo/Redo ilimitado
- Dimensiones (lineal, angular, radio, diámetro)
- Hatch/Relleno
- Bloques e inserciones
- Grid, OSNAP, ORTHO
- Interfaz en español e inglés

## Instalación
pip install -r requirements.txt
python main.py

## Licencia
MIT
```

---

## 8. `.gitignore`

```gitignore
__pycache__/
*.pyc
*.pyo
.env
settings.json
*.key
recovery/
logs/
dist/
build/
*.spec
.venv/
venv/
```

---

## 9. `requirements.txt` — solo lo del OSS

```
customtkinter>=5.2
ezdxf>=1.1
Pillow>=10.0
PyOpenGL>=3.1
numpy>=1.24
```

Sin: `anthropic`, `openai`, `google-generativeai`, ni cualquier SDK de IA.

---

## 10. Interfaz sin íconos dibujados (Interfaz 1)

El CAD tiene dos modos de interfaz:
- **Interfaz 1** — texto puro, sin imágenes dibujadas en botones ni menús
- **Interfaz 2** — íconos PIL renderizados en el toolbar y en los menús desplegables (toggle con comando `MENUICONS`)

**La versión OSS usa únicamente Interfaz 1.** Eliminar todo el sistema de íconos dibujados.

---

### 10.1 Archivo a eliminar

```
cad/menu_icons.py   ← eliminar completo
```

Este archivo contiene `build_icons()` y `build_icons_pil()` que dibujan los íconos con PIL. No va al OSS.

---

### 10.2 En `_build_ui()` — eliminar infraestructura de íconos (~línea 1267)

```python
# ELIMINAR este bloque completo de _build_ui():
self._iconable_btns: list  = []   # (btn, icon_key, label, size)
self._ctk_icon_cache: dict = {}   # CTkImage refs — anti-GC
self._pil_icons: dict      = {}   # PIL Images base
try:
    from cad.menu_icons import build_icons_pil as _bip
    self._pil_icons = _bip()
except Exception as _bip_e:
    print(f"[build_ui] íconos PIL no disponibles: {_bip_e}")
```

---

### 10.3 En `_build_menubar()` — simplificar `_ic()` (~línea 876)

```python
# ELIMINAR:
try:
    from cad.menu_icons import build_icons as _build_icons
    self._menu_icons = _build_icons()
except Exception:
    self._menu_icons = {}

def _ic(name):
    return self._menu_icons.get(name) or self._menu_icons.get("blank")

# REEMPLAZAR _ic por una función que siempre retorna None:
def _ic(name):
    return None
```

Esto hace que todos los `icon=_ic("xxx")` en los menús pasen `icon=None` automáticamente — sin íconos, sin errores, sin cambiar ninguna otra línea del menú.

---

### 10.4 Métodos a eliminar del sistema de íconos

| Método | Línea aprox. | Descripción |
|---|---|---|
| `_ctk_ico` | 1198 | Cache de CTkImage — solo usada para íconos |
| `_reg_btn` | 1210 | Registra botones en el sistema de íconos |
| `_apply_icon_mode` | 1214 | Aplica/quita íconos a los botones registrados |
| `_toggle_menu_icons` | 1231 | Toggle MENUICONS on/off |

---

### 10.5 Eliminar llamadas a `_reg_btn` y `_apply_icon_mode`

Buscar todas las referencias:
```bash
grep -n "_reg_btn\|_apply_icon_mode\|_toggle_menu_icons\|_ctk_ico" cad/engine.py
```

Cada línea encontrada: eliminar esa línea (son llamadas como `self._reg_btn(btn, "key", "label")` y `self._apply_icon_mode()` que ya no tienen función).

La única referencia a `_apply_icon_mode` que no es el método en sí está en `_build_ui` ~línea 1502 y en `_populate_toolbar`. Eliminar esas llamadas.

---

### 10.6 Eliminar comando MENUICONS de los aliases

En `_CMD_ALIASES` (~línea 237):
```python
# ELIMINAR:
"MENUICONS": "menu_icons_toggle", "MIC": "menu_icons_toggle",
```

En el diccionario de descripciones de comandos (~línea 331):
```python
# ELIMINAR:
"menu_icons_toggle": "MENUICONS — activa/desactiva íconos en los menús desplegables",
```

---

### 10.7 Verificación

Después de estos cambios, correr:
```bash
grep -n "menu_icons\|_pil_icons\|_iconable\|_ctk_icon\|build_icons\|_reg_btn\|_apply_icon" cad/engine.py
```
Resultado esperado: solo pueden quedar referencias dentro de métodos ya eliminados (0 líneas activas).

El CAD debe arrancar mostrando la Interfaz 1 — texto puro, igual que cuando `menu_icons = false` en settings. Esta es la interfaz estable y la correcta para el OSS.

---

## 11. ADIP Extract — eliminar del diálogo de importar DXF

ADIP es un módulo propietario de extracción de datos. Aparece **únicamente** dentro del método `_importar_dxf()` (~línea 18760). No tiene métodos propios ni aliases de comando — es solo un bloque de UI dentro del dialog de importación.

---

### 11.1 Bloque a eliminar en `_importar_dxf()` (~líneas 18760–18816)

Localizar y eliminar el bloque completo delimitado por los comentarios:

```python
# ELIMINAR desde aquí:
# ── ADIP Extract ──────────────────────────────────────────
_adip_var = tk.BooleanVar(value=False)
frm_adip = ctk.CTkFrame(dlg, fg_color=UI_PAN, corner_radius=8)
frm_adip.pack(fill="x", padx=20, pady=4)
_frm_adip_inner = ctk.CTkFrame(frm_adip, fg_color="transparent")
_frm_adip_inner.pack(side="left", padx=10, pady=6, fill="x", expand=True)
ctk.CTkCheckBox(_frm_adip_inner, text="🔍 Ejecutar ADIP Extract antes de abrir", ...)
ctk.CTkLabel(_frm_adip_inner, ...)
# Barra de progreso ADIP (oculta hasta que se active)
frm_adip_prog = ctk.CTkFrame(...)
_adip_lbl_var = tk.StringVar(value="")
# ... hasta:
_thr.Thread(target=_run_adip, daemon=True).start()
# ── ELIMINAR hasta aquí ───────────────────────────────────
```

El bloque ocupa aproximadamente líneas **18760–18816**. Son ~56 líneas que incluyen:
- El checkbox "🔍 Ejecutar ADIP Extract antes de abrir"
- La descripción/tooltip del checkbox
- La barra de progreso ADIP
- La función interna `_run_adip()`
- La función interna `_set_adip_done()`
- El `threading.Thread` que lo lanza

---

### 11.2 Verificar que el diálogo de importación sigue funcionando

Después de eliminar el bloque, el dialog de importar DXF debe abrirse normalmente sin el checkbox de ADIP. El flujo de importación sigue igual — solo desaparece esa opción.

```bash
# Grep de verificación — resultado esperado: sin output
grep -n "adip\|ADIP" cad/engine.py
```

---

## 12. Panel Perf — eliminar completamente

El panel `📊 Perf` es la pestaña de diagnóstico de rendimiento en Configuración. Contiene métricas en tiempo real, freeze detector, benchmark de capacidad, Health Monitor y heatmap de funciones. **Todo esto es código de desarrollo interno** — no debe aparecer en el OSS.

Se elimina de raíz: la pestaña, todos sus métodos, el sistema watchdog, el freeze detector y los atributos de estado.

---

### 12.1 En `_open_config()` — eliminar la pestaña Perf (~línea 15042)

```python
# ANTES:
for tab_name in ("🖱 General", "📐 Cotas", "🖊 Texto", "🎨 Visual", "🤖 IA", "📊 Perf"):

# DESPUÉS (también elimina IA que ya se quitó en Sección 3):
for tab_name in ("🖱 General", "📐 Cotas", "🖊 Texto", "🎨 Visual"):
```

También eliminar las dos líneas que construyen el tab (~línea 16081):
```python
# ELIMINAR:
t6 = _scroll_tab(tabs.tab("📊 Perf"))
self._build_perf_tab(t6, _HEAD, _SMALL, _TINY, _card, _section, win)
```

---

### 12.2 Métodos a eliminar completamente

| Método | Línea aprox. | Descripción |
|---|---|---|
| `_build_perf_tab` | 16146 | Construye toda la pestaña Perf (~1150 líneas, incluye todas las funciones internas: `_run_benchmark`, `_toggle_diag`, `_poll_diag`, `_poll_gl_errors`, `_copy_bench`, `_run_capacity_bench`, `_fd_refresh`, `_fd_copy`, `_fd_clear`, `_hm_run`, `_hm_copy`, `_hm_poll`) |
| `_start_watchdog` | 17295 | Inicia el hilo watchdog de freeze detection |
| `_heartbeat_ack` | 17305 | Heartbeat del watchdog |
| `_watchdog_worker` | 17312 | Worker del hilo watchdog |
| `_capture_freeze` | 17342 | Registra un evento de freeze |
| `_perf_report` | 17385 | Genera reporte de rendimiento en texto |

> **Nota:** `_build_perf_tab` es un método enorme (~1 150 líneas, de la 16146 a ~17294). Eliminarlo de golpe es la acción más limpia. Todo su contenido son funciones internas anidadas — no hay llamadas externas a ninguna de ellas.

---

### 12.3 Atributos de estado a eliminar del `__init__`

Localizar el bloque "Ring buffer para panel 📊 Perf" y "Freeze Detector" (~líneas 552–585) y eliminarlos:

```python
# ELIMINAR — Ring buffer Perf:
self._perf_ring: list = []
self._perf_ring_max = 60

# ELIMINAR — Freeze Detector (~líneas 563–585):
import time as _t_fd, threading as _thr_fd
self._watchdog_active   = False
self._watchdog_ack_time = _t_fd.perf_counter()
self._freeze_reported   = False
self._native_dialog_open = False
self._freeze_events: list = []
self._freeze_lock       = _thr_fd.Lock()
self._last_freeze_op    = ''
self._refresh_freeze_panel = lambda: None
_fd_dir = os.path.dirname(os.path.abspath(__file__))
self._freeze_log_path   = os.path.normpath(
    os.path.join(_fd_dir, '..', 'freeze_log.jsonl'))
try:
    import json as _j_fd
    with open(self._freeze_log_path, 'r', encoding='utf-8') as _ff:
        for _ln in _ff:
            _ln = _ln.strip()
            if _ln:
                self._freeze_events.append(_j_fd.loads(_ln))
    self._freeze_events = self._freeze_events[-50:]
except Exception:
    pass

# CONSERVAR — estos son para el HUD (fps y ms en pantalla, no para el panel Perf):
self._fps_times: list = []
self._render_ms: float = 0.0
```

---

### 12.4 Eliminar la llamada a `_start_watchdog` (~línea 758)

```python
# ELIMINAR esta línea del __init__ o de run():
self._start_watchdog()
```

---

### 12.5 Eliminar `_last_freeze_op` assignments dispersos

Hay 4 asignaciones de `self._last_freeze_op` en métodos de render. Buscarlas y eliminar **solo esa línea** en cada caso (no el método completo):

```bash
grep -n "_last_freeze_op" cad/engine.py
# Retorna ~líneas: 8325, 8373, 8477, 8733
```

Cada una es una línea simple como:
```python
self._last_freeze_op = '_snapshot'   # ← eliminar esta línea
```

---

### 12.6 Eliminar `_perf_ring` del render loop (~líneas 8696–8698)

En `_apply_render()` hay un bloque que guarda métricas en el ring buffer:

```python
# ELIMINAR este bloque:
self._perf_ring.append(snap)
if len(self._perf_ring) > self._perf_ring_max:
    self._perf_ring.pop(0)
```

También eliminar la construcción del dict `snap` que lo precede (~líneas 8678–8695) si su único uso es alimentar `_perf_ring`. Verificar con:
```bash
grep -n "_perf_ring\|perf_ring" cad/engine.py
```

---

### 12.7 `_last_roundtrip_result` y `_active_pbar_count`

Estas variables (~líneas 558–561) alimentan el Health Monitor dentro del panel Perf. Eliminarlas:

```python
# ELIMINAR:
self._last_roundtrip_result = None
self._active_pbar_count: int = 0
```

Buscar si hay asignaciones a `_last_roundtrip_result` en el código de export DXF y eliminarlas también:
```bash
grep -n "_last_roundtrip_result\|_active_pbar_count" cad/engine.py
```

---

### 12.8 Verificación final del panel Perf

```bash
# Ninguna de estas búsquedas debe retornar líneas activas:
grep -n "_build_perf_tab\|_start_watchdog\|_watchdog\|_freeze_event\|_freeze_log\|_capture_freeze\|_perf_ring\|_perf_report\|_last_freeze_op\|_last_roundtrip\|_active_pbar" cad/engine.py
```

La configuración debe abrir mostrando solo 4 pestañas: **General, Cotas, Texto, Visual**. Sin rastro de métricas, benchmarks, freeze logs ni diagnósticos.

---

## 13. Orden de trabajo recomendado

```
Paso 1:  cp -r del proyecto → nueva carpeta OSS
Paso 2:  git init limpio en la copia
Paso 3:  Eliminar archivos completos: sle/, mcp_server.py, layout.py, menu_icons.py
Paso 4:  engine.py — eliminar imports (Sección 3.1)
Paso 5:  engine.py — eliminar bloque SLE del __init__ (Sección 3.2)
Paso 6:  engine.py — eliminar bloque Freeze Detector + Perf Ring del __init__ (Sección 12.3)
Paso 7:  engine.py — eliminar self._start_watchdog() (Sección 12.4)
Paso 8:  engine.py — simplificar barra de comandos / eliminar toggle IA (Sección 3.5)
Paso 9:  engine.py — eliminar pestaña "📊 Perf" de _open_config (Sección 12.1)
Paso 10: engine.py — eliminar métodos IA, hub, SLE, layout (Sección 3.6)
Paso 11: engine.py — eliminar métodos Perf: _build_perf_tab, watchdog, _capture_freeze, _perf_report (Sección 12.2)
Paso 12: engine.py — eliminar bloque ADIP de _importar_dxf (Sección 11.1)
Paso 13: engine.py — eliminar sistema de íconos (Sección 10)
Paso 14: engine.py — eliminar _last_freeze_op assignments dispersos (Sección 12.5)
Paso 15: engine.py — eliminar bloque _perf_ring en _apply_render (Sección 12.6)
Paso 16: engine.py — eliminar _last_roundtrip_result y _active_pbar_count (Sección 12.7)
Paso 17: grep residual completo:
         grep "sle_\|_ia\b\|_hub\|layout\|_plot\|_pil_icons\|_iconable\|adip\|_perf_ring\|_freeze\|_watchdog\|_build_perf" cad/engine.py
Paso 18: Correr el CAD — verificar que abre con Interfaz 1, sin errores
Paso 19: Probar flujo completo: abrir DXF, línea, grip, snap, undo, exportar DXF, configuración (4 pestañas)
Paso 20: Crear settings.default.json, README.md, .gitignore
Paso 21: git add + git commit inicial
```

---

## 12. Verificación final antes de publicar

```bash
# 1. Verificar que no hay credenciales en el código
grep -r "sk-\|api_key\|password\|secret\|Bearer" cad/ --include="*.py"

# 2. Verificar que no hay rutas personales hardcodeadas
grep -r "C:\\Users\\jmerl\|merlosv@\|Estudio Merlos" cad/ --include="*.py"

# 3. Verificar que el CAD arranca con Interfaz 1 (sin íconos)
python main.py

# 4. Verificar imports — no debe haber ningún import de sle, mcp, layout en engine.py
grep "from sle\|from cad.mcp\|from cad.layout\|import sle\|_ejecutar_ia\|_llamar_ia" cad/engine.py
# Resultado esperado: sin output

# 5. Verificar que el sistema de íconos está eliminado
grep "_pil_icons\|_iconable_btns\|build_icons\|menu_icons_toggle" cad/engine.py
# Resultado esperado: sin output

# 6. Verificar que menu_icons.py no existe
ls cad/menu_icons.py
# Resultado esperado: "No such file or directory"

# 7. Verificar que ADIP está eliminado
grep "adip\|ADIP" cad/engine.py
# Resultado esperado: sin output

# 8. Verificar que el panel Perf está eliminado
grep "_build_perf_tab\|_start_watchdog\|_watchdog\|_freeze_event\|_capture_freeze\|_perf_ring\|_perf_report\|_last_freeze_op" cad/engine.py
# Resultado esperado: sin output

# 9. Verificar configuración abre con exactamente 4 pestañas
grep "tab_name\|tabs.tab" cad/engine.py | grep -i "perf\|IA\|ia"
# Resultado esperado: sin output (Perf e IA no aparecen en ningún tab)
```

Si alguno de estos grep retorna líneas activas → limpiarlas antes de publicar.
