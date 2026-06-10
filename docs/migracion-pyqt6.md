# Migración Estudio Merlos CAD — Tkinter → PyQt6
> Documento de arquitectura para ejecución en hilo independiente.  
> Contexto: CAD desktop con GL renderer, grips, snap, layers, AI. 20 897 líneas en engine.py.  
> Objetivo: software estable, empaquetable, sin freezes estructurales.

---

## 1. Estado actual del codebase

### Archivos y responsabilidades

| Archivo | Líneas | Depende de Tkinter | Rol |
|---|---|---|---|
| `cad/engine.py` | 20 897 | **Sí — todo** | UI + lógica + comandos + IA + archivos |
| `cad/entities.py` | ~3 000 | **No** | Entidades, snaps, geometría — reutilizable tal cual |
| `cad/renderer_opengl.py` | ~3 000 | **No** (solo GL + PIL) | Renderer GPU — reutilizable con cambio mínimo |
| `cad/renderer_pil.py` | ~2 500 | **No** (solo PIL) | Renderer fallback + `RenderCtx` dataclass |
| `cad/renderer_base.py` | 151 | **No** | Interfaz abstracta `BaseRenderer` — reutilizable |
| `cad/viewport.py` | 166 | **No** | Transforms mundo↔pantalla, zoom_to_fit — reutilizable |
| `cad/layout.py` | ~800 | **No** | Paper space, layouts, PDF — reutilizable |
| `cad/dxf_import.py` | ~600 | **No** | Importar DXF — reutilizable |
| `cad/dxf_export.py` | ~400 | **No** | Exportar DXF — reutilizable |
| `cad/mcp_server.py` | ~300 | **No** | Servidor MCP/IA — reutilizable |
| `cad/i18n.py` | ~200 | **No** | Traducciones — reutilizable |

### Diagnóstico

**El problema está contenido en `engine.py`.** Los demás módulos no usan Tkinter. La lógica de negocio (snap, grips, comandos, geometría, DXF, AI) está entremezclada con la UI en ese único archivo de 21 k líneas.

**El render pipeline ya es correcto:** `renderer_opengl.py` y `renderer_pil.py` retornan `RenderResult(image=PIL.Image)` — nunca tocan Tkinter. Solo hay que cambiar cómo se muestra esa imagen: en lugar de `ImageTk.PhotoImage` → `QPixmap`.

---

## 2. Por qué PyQt6

| Criterio | Tkinter | PyQt6 |
|---|---|---|
| Threading | Hilo único — todo congela | `QThread` + signals/slots — UI no bloquea |
| OpenGL | Hack externo (pyopengl + HWND) | `QOpenGLWidget` nativo |
| Packaging | PyInstaller 300 MB, arranca 15 s | Nuitka/PyInstaller 60 MB, arranca 2 s |
| Look | Inconsistente en Windows 11 | Nativo en Win/Mac/Linux |
| Referencia real | — | FreeCAD, QCAD, LibreCAD usan Qt |

---

## 3. Estrategia general: Strangler Fig

No reescribir todo de golpe. Separar primero, reemplazar después. El CAD sigue funcionando en Tkinter hasta que Qt esté listo.

```
engine.py (20k líneas, todo mezclado)
         ↓  Fase 1
cad/core/            ← lógica pura, cero tkinter
  commands.py        ← herramientas, snap, grips
  state.py           ← estado del documento
  events.py          ← protocolo de eventos
cad/ui_tk/           ← UI actual (Tkinter) — sigue funcionando
  engine_tk.py       ← delega a core, thin wrapper
cad/ui_qt/           ← nueva UI (Qt) — se construye en Fase 2-3
  engine_qt.py
  canvas_gl.py       ← QOpenGLWidget
  prop_panel.py
  cmd_bar.py
  toolbar.py
```

---

## 4. Fases de migración

---

### FASE 1 — Separar núcleo (sin romper nada)
**Duración estimada: 3-4 semanas**  
**Resultado: engine.py sigue funcionando. Aparece `cad/core/` con lógica pura.**

#### 1.1 Crear estructura de directorios

```
cad/
  core/
    __init__.py
    state.py          ← documento CAD: entities, layers, block_defs, undo stack
    commands.py       ← herramientas: line, circle, arc, grip, snap, trim, etc.
    snap_engine.py    ← _compute_snap, _snap_point_world (extraído de engine.py)
    grip_engine.py    ← _hot_grip, _apply_grip_move, _flush_move
    selection.py      ← _select_at, _select_window, _hit_entity
    file_io.py        ← _importar_dxf, _exportar_dxf, _guardar_json, _abrir_json
    ai_bridge.py      ← _ejecutar_ia, _llamar_ia_stream, _parsear_respuesta_ia
  ui_tk/              ← mover engine.py aquí eventualmente
  ui_qt/              ← nueva UI
```

#### 1.2 Extraer `CadState` (state.py)

`CadState` contiene el **estado del documento** que hoy vive como atributos en `self` dentro de `engine.py`. No tiene ninguna dependencia de UI.

```python
# cad/core/state.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import copy

@dataclass
class CadState:
    """Estado completo de un documento CAD. Sin dependencias de UI."""
    entities: list = field(default_factory=list)
    layers: dict = field(default_factory=dict)
    block_defs: dict = field(default_factory=dict)
    current_layer: str = "0"
    current_linetype: str = "bylayer"
    current_linewidth: int = 0
    # Viewport
    scale: float = 40.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    # Undo/Redo
    _undo_stack: list = field(default_factory=list, repr=False)
    _redo_stack: list = field(default_factory=list, repr=False)
    # Metadatos
    filepath: Optional[str] = None
    dirty: bool = False

    def snapshot(self):
        """Crea copia profunda para undo."""
        return copy.deepcopy(self.entities)

    def push_undo(self):
        self._undo_stack.append(self.snapshot())
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self.dirty = True

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append(self.snapshot())
        self.entities = self._undo_stack.pop()
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append(self.snapshot())
        self.entities = self._redo_stack.pop()
        return True
```

#### 1.3 Extraer `SnapEngine` (snap_engine.py)

En `engine.py` buscar los métodos relacionados con snap:
- `_compute_snap` (o similar)
- `_snap_point_world`
- `_flush_move` (la parte de cálculo de snap, no la de UI)

Grep para encontrarlos exactos:
```bash
grep -n "_snap\|_compute_snap\|snap_point" cad/engine.py | head -40
```

Moverlos a `cad/core/snap_engine.py` como funciones puras que reciben `(entities, layers, scale, wx, wy, snap_modes, snap_px)` y retornan el punto snap.

#### 1.4 Extraer `GripEngine` (grip_engine.py)

Métodos a extraer:
- `_compute_grips(entity)` → lista de puntos grip
- `_apply_grip_move(entity, grip_index, new_pos)` → nueva entidad
- `_find_hot_grip(entities, selection, sx, sy, px_threshold)` → (entity, grip_index) | None

Estos métodos no tienen UI. Solo operan sobre entidades y retornan entidades nuevas.

#### 1.5 Extraer `CommandDispatcher` (commands.py)

Crear un dispatcher que mapea string → función:

```python
# cad/core/commands.py
class CommandDispatcher:
    """Interpreta comandos CAD sin UI. Retorna resultados como data."""

    def __init__(self, state: CadState):
        self.state = state
        self._aliases = {
            "L": "line", "LINE": "line",
            "C": "circle", "CIRCLE": "circle",
            # ... todos los _CMD_ALIASES de engine.py
        }

    def dispatch(self, cmd: str) -> dict:
        """Ejecuta un comando. Retorna {'ok': bool, 'msg': str, 'action': str}"""
        normalized = self._aliases.get(cmd.upper(), cmd.upper())
        handler = getattr(self, f"_cmd_{normalized.lower()}", None)
        if handler:
            return handler()
        return {"ok": False, "msg": f"Comando desconocido: {cmd}"}
```

#### 1.6 Verificación de Fase 1

Al terminar, el CAD **debe seguir corriendo exactamente igual** en Tkinter. Los tests:
1. Abrir un DXF de 22 000+ entidades → no hay freeze
2. Crear línea, polilínea, círculo → funciona
3. Grip move → funciona
4. Snap endpoint/midpoint → funciona
5. Undo/Redo → funciona

---

### FASE 2 — Canvas Qt con render GL
**Duración estimada: 4-6 semanas**  
**Resultado: una ventana Qt que renderiza el CAD con el mismo GL renderer.**

#### 2.1 Instalar dependencias

```bash
pip install PyQt6 PyQt6-Qt6 PyQt6-sip
# PyOpenGL ya está instalado (viene del renderer actual)
pip install PyInstaller  # o Nuitka para packaging
```

#### 2.2 Crear `CanvasGL` (QOpenGLWidget)

Este es el componente central. Reemplaza `tkinter.Canvas` + el hack de HWND que usa el renderer actual para obtener el contexto GL.

```python
# cad/ui_qt/canvas_gl.py
from __future__ import annotations
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QWheelEvent, QKeyEvent
import threading

class CanvasGL(QOpenGLWidget):
    """
    Widget de render principal. Equivalente al tkinter.Canvas actual.

    Diferencia clave vs Tkinter:
    - El contexto GL es nativo Qt — no necesita el hack HWND de renderer_opengl.py
    - paintGL() corre en el hilo de UI pero Qt lo maneja correctamente
    - Los eventos de mouse/teclado van a métodos Qt, no a binds de Tkinter
    """

    # Señales que emite el canvas hacia la ventana principal
    mouse_moved   = pyqtSignal(float, float)   # (sx, sy) en pantalla
    mouse_pressed = pyqtSignal(float, float, int)  # (sx, sy, button)
    mouse_released= pyqtSignal(float, float, int)
    key_pressed   = pyqtSignal(int, str)        # (Qt.Key, text)
    size_changed  = pyqtSignal(int, int)        # (W, H)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._render_lock = threading.Lock()
        self._pending_image = None   # PIL.Image esperando ser pintada

    def initializeGL(self):
        """Qt llama esto una vez, con el contexto GL activo."""
        # El renderer_opengl.py actual tiene _init_gl() — llamarlo aquí
        # después de adaptar que no necesite HWND
        pass

    def resizeGL(self, w: int, h: int):
        self.size_changed.emit(w, h)

    def paintGL(self):
        """Qt llama esto para repintar. Aquí se muestra la imagen del renderer."""
        # Opción A: usar el resultado PIL del renderer actual → QPixmap
        # Opción B: dibujar directamente con GL (más rápido, Fase 3)
        # Para Fase 2 usar Opción A (mínimo cambio al renderer)
        from PyQt6.QtGui import QPainter, QPixmap
        from PIL.ImageQt import ImageQt
        with self._render_lock:
            img = self._pending_image
        if img is not None:
            qt_img = ImageQt(img)
            pixmap = QPixmap.fromImage(qt_img)
            painter = QPainter(self)
            painter.drawPixmap(0, 0, pixmap)
            painter.end()

    def set_frame(self, pil_image):
        """Llamado desde el hilo de render cuando hay nueva imagen."""
        with self._render_lock:
            self._pending_image = pil_image
        self.update()  # schedula repaint (thread-safe en Qt)

    # ── Eventos de mouse → señales ──────────────────────────────────
    def mouseMoveEvent(self, e: QMouseEvent):
        self.mouse_moved.emit(e.position().x(), e.position().y())

    def mousePressEvent(self, e: QMouseEvent):
        btn = e.button().value  # 1=left, 2=right, 4=middle
        self.mouse_pressed.emit(e.position().x(), e.position().y(), btn)

    def mouseReleaseEvent(self, e: QMouseEvent):
        btn = e.button().value
        self.mouse_released.emit(e.position().x(), e.position().y(), btn)

    def wheelEvent(self, e: QWheelEvent):
        # Convertir a formato compatible con el zoom actual
        delta = e.angleDelta().y()
        # Emitir o manejar zoom directamente
        pass

    def keyPressEvent(self, e: QKeyEvent):
        self.key_pressed.emit(e.key(), e.text())
```

#### 2.3 Adaptar `renderer_opengl.py` para Qt

El renderer actual usa un hack para obtener el HWND de Windows y crear un contexto GL propio. Con Qt esto desaparece — Qt maneja el contexto GL a través de `QOpenGLWidget`.

**Cambios necesarios en `renderer_opengl.py`:**

1. Eliminar todo el código de `_ensure_discrete_gpu_preference()`, `_WNDCLASSEX`, `_def_wndproc`, `_try_wgl_nv_gpu_affinity` — estos son workarounds para crear un contexto GL sin Qt.

2. Cambiar el método `available()` para no intentar crear una ventana GL oculta — Qt ya tiene el contexto.

3. El método `render(ctx)` sigue igual — toma un `RenderCtx` y retorna `RenderResult(image=PIL.Image)`.

4. Agregar un método `render_direct(ctx)` que dibuje directamente en el framebuffer de Qt (Fase 3 optimización).

**Patrón de cambio en `renderer_opengl.py`:**

```python
# ANTES (líneas ~200-450 de renderer_opengl.py):
# Crea ventana oculta Win32, obtiene HDC, crea contexto WGL
# Todo esto para tener un contexto GL sin Qt

# DESPUÉS:
# La clase RendererOpenGL recibe el QOpenGLWidget como parámetro
# y usa su contexto GL ya inicializado

class RendererOpenGL(BaseRenderer):
    def __init__(self, qt_widget=None):
        self._qt_widget = qt_widget  # QOpenGLWidget o None
        # Si qt_widget es None → modo legacy (Tkinter)
        # Si qt_widget no es None → usar contexto Qt
```

#### 2.4 Crear ventana principal Qt (`MainWindow`)

```python
# cad/ui_qt/main_window.py
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QSplitter, QDockWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from cad.ui_qt.canvas_gl import CanvasGL
from cad.core.state import CadState

class CadMainWindow(QMainWindow):
    """
    Ventana principal del CAD.
    Equivalente a la clase CADApp en engine.py.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Estudio Merlos CAD")
        self.resize(1400, 900)

        # Estado del documento
        self.state = CadState()

        # Canvas central
        self.canvas = CanvasGL(self)

        # Conectar señales del canvas
        self.canvas.mouse_moved.connect(self._on_mouse_move)
        self.canvas.mouse_pressed.connect(self._on_mouse_press)
        self.canvas.mouse_released.connect(self._on_mouse_release)
        self.canvas.key_pressed.connect(self._on_key_press)
        self.canvas.size_changed.connect(self._on_resize)

        self.setCentralWidget(self.canvas)

        # Panels laterales como QDockWidget (equivalente a los paneles de layers/props)
        self._build_docks()
        self._build_menu()
        self._build_toolbar()
        self._build_cmd_bar()

        # Hilo de render
        self._render_thread = RenderThread(self)
        self._render_thread.frame_ready.connect(self.canvas.set_frame)
        self._render_thread.start()
```

#### 2.5 Crear `RenderThread` (hilo dedicado de render)

Esta es la mejora arquitectural clave. En Tkinter, el render bloqueaba el hilo principal. Con Qt, el render corre en un `QThread` dedicado.

```python
# cad/ui_qt/render_thread.py
from PyQt6.QtCore import QThread, pyqtSignal
from cad.renderer_base import select_renderer, RenderCtx  # RenderCtx desde renderer_pil

class RenderThread(QThread):
    """
    Hilo dedicado de render. Nunca bloquea la UI.

    Qt garantiza que frame_ready se entregue en el hilo de UI
    gracias al mecanismo de signals/slots con conexión queued.
    """
    frame_ready = pyqtSignal(object)  # PIL.Image

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._renderer = select_renderer(config={})
        self._render_requested = threading.Event()
        self._running = True

    def request_render(self):
        """Llamado desde el hilo UI cuando hay cambios."""
        self._render_requested.set()

    def run(self):
        while self._running:
            self._render_requested.wait(timeout=0.050)
            self._render_requested.clear()
            if not self._running:
                break
            ctx = self._build_ctx()
            result = self._renderer.render(ctx)
            self.frame_ready.emit(result.image)

    def _build_ctx(self):
        """Construye RenderCtx desde el estado actual. Thread-safe."""
        w = self._window
        # Leer estado (con lock si es necesario)
        return RenderCtx(
            W=w.canvas.width(),
            H=w.canvas.height(),
            scale=w.state.scale,
            offset_x=w.state.offset_x,
            offset_y=w.state.offset_y,
            entities=w.state.entities,
            layers=w.state.layers,
            # ... resto de campos
        )

    def stop(self):
        self._running = False
        self._render_requested.set()
```

#### 2.6 Verificación de Fase 2

- La ventana Qt abre y muestra el canvas GL
- `zoom_to_fit` funciona → las entidades se renderizan
- Pan con click medio funciona
- Zoom con scroll funciona
- Los freezes de Tkinter **han desaparecido** porque el render está en su propio hilo

---

### FASE 3 — Migrar UI completa
**Duración estimada: 3-4 semanas**  
**Resultado: toda la UI en Qt. Tkinter eliminado.**

#### 3.1 Panel de capas (Layers panel)

En Tkinter: `_populate_right()` ~línea 2050, con `tk.Frame`, `tk.Label`, `tk.Checkbutton`, canvas scrollable.

En Qt:

```python
# cad/ui_qt/layers_panel.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QPushButton,
    QLineEdit, QLabel, QCheckBox, QColorDialog
)
from PyQt6.QtCore import pyqtSignal

class LayersPanel(QWidget):
    layer_visibility_changed = pyqtSignal(str, bool)
    layer_lock_changed       = pyqtSignal(str, bool)
    layer_color_changed      = pyqtSignal(str, str)
    active_layer_changed     = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # Filtro
        self._filter = QLineEdit(placeholderText="Filtrar capas...")
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        # Lista de capas — QTreeWidget reemplaza el canvas scrollable de Tkinter
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["", "Nombre", "Color", "Tipo"])
        self._tree.setColumnWidth(0, 30)   # vis/lock icons
        layout.addWidget(self._tree)

        # Botones
        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("+ Nueva")
        self._btn_del = QPushButton("Eliminar")
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_del)
        layout.addLayout(btn_row)

    def refresh(self, layers: dict, active_layer: str, filter_text: str = ""):
        """Reconstruye la lista de capas. Sin freezes — Qt maneja el scroll."""
        self._tree.clear()
        for name, lyr in layers.items():
            if filter_text and filter_text.lower() not in name.lower():
                continue
            item = QTreeWidgetItem([
                "👁" if lyr.visible else " ",
                name,
                lyr.color or "#FFFFFF",
                lyr.linetype or "continuous"
            ])
            self._tree.addTopLevelItem(item)

    def _apply_filter(self, text: str):
        # Llamar refresh con el filtro
        pass
```

#### 3.2 Panel de propiedades (Properties panel)

En Tkinter: `_rebuild_prop_panel()` ~línea 3500, se reconstruye completo en cada cambio. Esta es la fuente de freezes de `tk.OptionMenu`.

En Qt: usar `QFormLayout` con widgets que se actualizan en lugar de recrearse.

```python
# cad/ui_qt/prop_panel.py
from PyQt6.QtWidgets import (
    QWidget, QFormLayout, QLineEdit,
    QComboBox, QLabel, QPushButton, QFrame
)
from PyQt6.QtCore import pyqtSignal

class PropPanel(QWidget):
    """
    Panel de propiedades de entidad seleccionada.

    DIFERENCIA CLAVE vs Tkinter:
    En Tkinter: se destruía y recreaba todo con cada selección → freeze.
    Aquí: los widgets son persistentes, solo se actualiza su contenido.
    QComboBox.clear() + addItems() es un único batch Tcl-free.
    """
    property_changed = pyqtSignal(str, object)  # (prop_name, new_value)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QFormLayout(self)

        # Widgets persistentes — se crean una vez
        self._lbl_type  = QLabel("—")
        self._cmb_layer = QComboBox()
        self._cmb_lt    = QComboBox()   # linetype
        self._cmb_lw    = QComboBox()   # linewidth
        self._btn_color = QPushButton()

        self._layout.addRow("Tipo:", self._lbl_type)
        self._layout.addRow("Capa:", self._cmb_layer)
        self._layout.addRow("Tipo línea:", self._cmb_lt)
        self._layout.addRow("Grosor:", self._cmb_lw)
        self._layout.addRow("Color:", self._btn_color)

        self._cmb_layer.currentTextChanged.connect(
            lambda v: self.property_changed.emit("layer", v))
        self._cmb_lt.currentTextChanged.connect(
            lambda v: self.property_changed.emit("linetype", v))

        self._btn_color.clicked.connect(self._pick_color)

    def update_selection(self, entities: list, layers: dict):
        """Actualiza el panel con la selección actual. Sin recrear widgets."""
        if not entities:
            self._lbl_type.setText("— Ninguna entidad")
            return

        e = entities[0]
        self._lbl_type.setText(type(e).__name__.upper())

        # QComboBox: actualizar lista y valor en batch (sin N round-trips)
        layer_names = list(layers.keys())
        self._cmb_layer.blockSignals(True)
        self._cmb_layer.clear()
        self._cmb_layer.addItems(layer_names)
        idx = self._cmb_layer.findText(e.layer)
        if idx >= 0:
            self._cmb_layer.setCurrentIndex(idx)
        self._cmb_layer.blockSignals(False)

    def _pick_color(self):
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor()
        if color.isValid():
            self.property_changed.emit("color", color.name().upper())
```

#### 3.3 Barra de comandos (Command bar)

En Tkinter: `_build_cmd_bar()` ~línea 1516. Un `tk.Entry` con historial y autocompletado.

En Qt: `QLineEdit` + `QTextEdit` para historial.

```python
# cad/ui_qt/cmd_bar.py
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QTextEdit
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QKeyEvent

class CmdBar(QWidget):
    command_entered = pyqtSignal(str)  # El usuario presionó Enter

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Historial de comandos (solo lectura)
        self._history = QTextEdit()
        self._history.setReadOnly(True)
        self._history.setMaximumHeight(80)
        layout.addWidget(self._history)

        # Input
        self._input = QLineEdit()
        self._input.setPlaceholderText("Comando: L, C, A, PL, ESC...")
        self._input.returnPressed.connect(self._on_enter)
        layout.addWidget(self._input)

        self._cmd_history: list[str] = []
        self._hist_idx = -1

    def _on_enter(self):
        text = self._input.text().strip()
        if text:
            self._cmd_history.append(text)
            self._hist_idx = -1
            self._input.clear()
            self.command_entered.emit(text)

    def echo(self, msg: str, tag: str = "cad"):
        """Agrega mensaje al historial."""
        colors = {"cad": "#00FF41", "err": "#DC2626", "ia": "#2563EB"}
        color = colors.get(tag, "#94A3B8")
        self._history.append(f'<span style="color:{color}">{msg}</span>')
        # Scroll al final
        sb = self._history.verticalScrollBar()
        sb.setValue(sb.maximum())

    def keyPressEvent(self, e: QKeyEvent):
        """Flechas arriba/abajo para historial."""
        if e.key() == Qt.Key.Key_Up and self._cmd_history:
            self._hist_idx = min(self._hist_idx + 1, len(self._cmd_history) - 1)
            self._input.setText(self._cmd_history[-(self._hist_idx + 1)])
        elif e.key() == Qt.Key.Key_Down:
            if self._hist_idx > 0:
                self._hist_idx -= 1
                self._input.setText(self._cmd_history[-(self._hist_idx + 1)])
            else:
                self._hist_idx = -1
                self._input.clear()
        else:
            super().keyPressEvent(e)
```

#### 3.4 HUD (overlay de información)

En Tkinter: `_draw_hud()` dibujaba `create_text` en el canvas cada frame.

En Qt: usar `QPainter` en `paintEvent` del `CanvasGL`, superpuesto sobre la imagen GL.

```python
# En CanvasGL.paintGL():
def paintGL(self):
    # 1. Mostrar imagen del renderer
    painter = QPainter(self)
    if self._pending_image:
        pixmap = QPixmap.fromImage(ImageQt(self._pending_image))
        painter.drawPixmap(0, 0, pixmap)

    # 2. HUD superpuesto (sin crear ítems Tcl — puro Qt)
    painter.setPen(QColor("#475569"))
    painter.setFont(QFont("Courier New", 8))
    hud_lines = self._hud_lines  # lista de strings actualizada por el engine
    H = self.height()
    W = self.width()
    for i, line in enumerate(reversed(hud_lines)):
        painter.drawText(W - 10, H - 10 - i * 14,
                         line)  # Qt maneja el anchor automáticamente
    painter.end()
```

#### 3.5 Toolbar y menús

En Tkinter: `_build_menubar()`, `_populate_top_toolbar()`, `_populate_toolbar()`.

En Qt: `QMenuBar`, `QToolBar` — equivalentes directos.

```python
# En CadMainWindow._build_menu():
def _build_menu(self):
    mb = self.menuBar()

    # Archivo
    m_file = mb.addMenu("Archivo")
    m_file.addAction("Nuevo", self._cmd_new, "Ctrl+N")
    m_file.addAction("Abrir...", self._cmd_open, "Ctrl+O")
    m_file.addAction("Guardar", self._cmd_save, "Ctrl+S")
    m_file.addSeparator()
    m_file.addAction("Importar DXF...", self._cmd_import_dxf)
    m_file.addAction("Exportar DXF...", self._cmd_export_dxf)

    # Editar
    m_edit = mb.addMenu("Editar")
    m_edit.addAction("Deshacer", self.state.undo, "Ctrl+Z")
    m_edit.addAction("Rehacer", self.state.redo, "Ctrl+Y")

    # Herramientas
    m_tools = mb.addMenu("Herramientas")
    for cmd, label in [("line","Línea"), ("circle","Círculo"),
                       ("arc","Arco"), ("polyline","Polilínea")]:
        m_tools.addAction(label, lambda c=cmd: self._set_tool(c))
```

#### 3.6 Packaging con PyInstaller

```bash
# Crear spec file
pyi-makespec --windowed --name "EstudioMerlosCAD" main_qt.py

# Editar el spec para incluir recursos
# datas=[('cad/fonts/*', 'cad/fonts'), ('cad/blocks/*', 'cad/blocks')]

# Build
pyinstaller EstudioMerlosCAD.spec
```

Con Nuitka (recomendado para distribución):
```bash
pip install nuitka
python -m nuitka --standalone --enable-plugin=pyqt6 --windows-disable-console main_qt.py
```

---

## 5. Mapa de equivalencias Tkinter → Qt

| Componente Tkinter | Equivalente Qt | Notas |
|---|---|---|
| `tk.Tk()` | `QApplication` + `QMainWindow` | |
| `tk.Canvas` | `QOpenGLWidget` (CanvasGL) | |
| `tk.Label` | `QLabel` | |
| `tk.Button` | `QPushButton` | |
| `tk.Entry` | `QLineEdit` | |
| `tk.Frame` | `QWidget` + layout | |
| `tk.OptionMenu` | `QComboBox` | Sin freezes — batch update |
| `tk.Menu` | `QMenu` | |
| `tk.Menubar` | `QMenuBar` | |
| `tk.Text` | `QTextEdit` | |
| `tk.Scrollbar` | Incluido en `QScrollArea` | |
| `tk.Canvas` scroll | `QScrollArea` | |
| `tk.after(ms, fn)` | `QTimer.singleShot(ms, fn)` | |
| `tk.after(0, fn)` | `QTimer.singleShot(0, fn)` | |
| `threading.Thread` | `QThread` | Con signals/slots |
| `root.after_idle(fn)` | `QTimer.singleShot(0, fn)` | |
| `widget.pack()` | `layout.addWidget()` | |
| `widget.place()` | `layout.setAlignment()` o posición absoluta |  |
| `widget.configure()` | `widget.setText()`, `widget.setStyleSheet()` | Sin round-trips |
| `canvas.create_text()` | `QPainter.drawText()` en paintEvent | Persistente |
| `canvas.create_line()` | `QPainter.drawLine()` | |
| `canvas.itemconfigure()` | Actualizar datos, llamar `update()` | |
| `messagebox.askyesno()` | `QMessageBox.question()` | |
| `filedialog.askopenfilename()` | `QFileDialog.getOpenFileName()` | |
| `colorchooser.askcolor()` | `QColorDialog.getColor()` | |
| `customtkinter.CTkButton` | `QPushButton` con stylesheet | |
| `customtkinter.CTkLabel` | `QLabel` | Sin el freeze de round-trips CTk |

---

## 6. Puntos críticos que NO deben cambiar

### 6.1 `RenderCtx` — no tocar

El dataclass `RenderCtx` en `renderer_pil.py` es la interfaz entre el engine y los renderers. **No cambiar su estructura.** Solo agregar el campo `is_panning: bool = False` que ya fue agregado en una sesión anterior.

### 6.2 `renderer_opengl.py` — cambio mínimo

El renderer GL ya está bien escrito. El único cambio necesario es en la inicialización del contexto GL (líneas ~200-450): reemplazar el hack Win32/WGL con el contexto Qt. Todo lo demás (shaders, VBOs, tessellation, PBO) se conserva.

### 6.3 `entities.py` — cero cambios

Las entidades son Python puro. No hay ninguna dependencia de Tkinter. Copiar sin modificar.

### 6.4 `viewport.py` — cero cambios

Funciones puras matemáticas. No hay dependencia de UI.

### 6.5 `dxf_import.py`, `dxf_export.py` — cero cambios

Solo usan `ezdxf` y las entidades del dominio.

### 6.6 Snap y grips — lógica preservada

Los algoritmos de snap (R-tree, hash grid, `_dist_pt_seg`) y grips (`_apply_grip_move`, `_flush_move`) ya funcionan correctamente después de las correcciones de la sesión anterior. Migrar la lógica sin modificarla.

---

## 7. Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Contexto GL en Qt diferente al hack Win32 | Alto | Testear `renderer_opengl.py` con `QOpenGLWidget` en un archivo separado antes de integrar |
| `PIL.ImageQt` conversión lenta en Fase 2 | Medio | Es temporal. Fase 3 dibuja GL directamente sin PIL→QPixmap |
| Snap/grip regression en la migración | Alto | Mantener los tests funcionales en Tkinter como referencia hasta que Qt pase todos |
| Pérdida de atajos de teclado | Bajo | Mapear todos los `bind("<Key>")` de Tkinter a `keyPressEvent` de Qt antes de eliminar Tkinter |
| AI bridge (llamadas HTTP) bloqueando UI | Bajo | Ya usa `threading.Thread` — solo mover a `QThread` para integrarse mejor con Qt |

---

## 8. Punto de entrada nuevo (`main_qt.py`)

```python
# main_qt.py — reemplaza el entry point actual
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from cad.ui_qt.main_window import CadMainWindow

def main():
    # Para pantallas HiDPI (Retina, Windows 4K)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("Estudio Merlos CAD")
    app.setOrganizationName("Estudio Merlos")

    # Stylesheet global (equivalente a la paleta UI_BG, UI_PAN, etc.)
    app.setStyleSheet("""
        QMainWindow, QWidget { background-color: #0F172A; color: #F8FAFC; }
        QToolBar  { background: #1E293B; border: none; }
        QMenuBar  { background: #1E293B; color: #F8FAFC; }
        QMenu     { background: #334155; color: #F8FAFC; }
        QMenu::item:selected { background: #2563EB; }
        QPushButton { background: #334155; color: #F8FAFC;
                      border: 1px solid #475569; padding: 4px 8px; }
        QPushButton:hover { background: #2563EB; }
        QLineEdit { background: #1E293B; color: #F8FAFC;
                    border: 1px solid #475569; padding: 2px; }
        QComboBox { background: #334155; color: #F8FAFC;
                    border: 1px solid #475569; }
        QTreeWidget { background: #1E293B; color: #F8FAFC;
                      border: none; }
    """)

    window = CadMainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
```

---

## 9. Orden de ejecución recomendado

```
Semana 1-2:   Fase 1.1–1.3  — CadState + SnapEngine extraídos, tests pasan
Semana 3-4:   Fase 1.4–1.6  — GripEngine + CommandDispatcher, engine.py sin cambios visibles
Semana 5-6:   Fase 2.1–2.4  — CanvasGL + RenderThread, ventana Qt abre con GL
Semana 7-8:   Fase 2.5–2.6  — RenderThread completo, pan/zoom funcionan en Qt
Semana 9-10:  Fase 3.1–3.3  — Layers panel + Props panel + CmdBar en Qt
Semana 11-12: Fase 3.4–3.6  — HUD + Toolbar + Menus + Packaging
Semana 13:    Estabilización — Comparar Qt vs Tkinter feature por feature
Semana 14:    Deploy         — Packaging, installer, distribución
```

---

## 10. Cómo usar este documento en otro hilo

1. Leer este documento completo antes de escribir código.
2. Empezar SIEMPRE por Fase 1 — no saltar a Qt sin extraer el núcleo.
3. Después de cada sub-fase, ejecutar el CAD actual (Tkinter) para verificar que no se rompió nada.
4. Los archivos `entities.py`, `viewport.py`, `renderer_base.py`, `dxf_import.py`, `dxf_export.py` son intocables — copiar sin modificar.
5. El primer archivo nuevo a crear es `cad/core/state.py` con `CadState`.
6. Nunca importar `tkinter` en ningún archivo dentro de `cad/core/`.
7. Cuando haya duda sobre cómo funciona algo en `engine.py`, buscar por nombre de método con grep antes de asumir.
