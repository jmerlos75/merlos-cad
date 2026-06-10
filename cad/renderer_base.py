"""
cad/renderer_base.py
====================
Interfaz abstracta para renderers CAD.

Tanto RendererPIL como RendererOpenGL heredan de BaseRenderer.
Esto garantiza:
  - Contrato estable: el engine solo llama a .render(ctx) y .available()
  - Rollback seguro: ambos renderers son intercambiables
  - Validación: test_renderer_validation.py puede comparar PIL vs OpenGL
    usando la misma interfaz

Uso típico:
    from cad.renderer_base import BaseRenderer, RenderResult

    class MiRenderer(BaseRenderer):
        def available(self) -> bool: ...
        def render(self, ctx) -> RenderResult: ...
        def cleanup(self) -> None: ...
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


# ── Resultado de render ───────────────────────────────────────────────────────

@dataclass
class RenderResult:
    """
    Resultado estandarizado que retorna .render().

    Campos:
        image    — PIL.Image.Image (modo RGB) listo para Tkinter PhotoImage
                   SIEMPRE presente — OpenGL convierte su framebuffer a PIL Image
                   antes de retornar, para mantener la cadena engine→canvas intacta.
        backend  — 'pil' | 'opengl' — identifica quién lo generó
        frame_ms — tiempo de render en ms (para métricas/HUD)
    """
    image:    Any           # PIL.Image.Image
    backend:  str  = 'pil'
    frame_ms: float = 0.0


# ── Interfaz base ─────────────────────────────────────────────────────────────

class BaseRenderer(ABC):
    """
    Contrato mínimo que todo renderer CAD debe implementar.

    Reglas críticas de implementación:
    1. render() NUNCA toca Tkinter — solo opera sobre datos del RenderCtx
    2. render() es thread-safe: se llama desde un hilo de fondo
    3. cleanup() libera recursos GPU/PIL al cerrar la app
    4. available() no lanza excepciones — solo retorna True/False
    """

    @abstractmethod
    def available(self) -> bool:
        """
        Verifica si el renderer puede inicializarse en esta máquina.

        Para PIL: comprueba que Pillow esté instalado.
        Para OpenGL: intenta importar PyOpenGL + verifica soporte GL 3.3+.

        No lanza excepciones — retorna False si falla cualquier check.
        """

    @abstractmethod
    def render(self, ctx: Any) -> RenderResult:
        """
        Renderiza la escena completa y retorna un RenderResult.

        Parámetros:
            ctx — RenderCtx (dataclass frozen desde renderer_pil.py)
                  Contiene: W, H, scale, offset_x, offset_y, entities,
                  layers, block_defs, entity_index, grid_on, colores,
                  cancel_ev (threading.Event), config.

        Contrato:
            - Si ctx.cancel_ev está seteado a mitad del render → retornar
              RenderResult con la imagen parcial o la última imagen completa.
            - Retorna SIEMPRE un RenderResult válido (nunca None).
            - El campo image es siempre PIL.Image en modo RGB.
        """

    def cleanup(self) -> None:
        """
        Libera recursos al cerrar la app.

        PIL: no necesita hacer nada (GC de Python maneja la memoria).
        OpenGL: destruye VAOs, VBOs, shaders, el contexto GL.

        Implementación por defecto: no-op (para PIL no hace falta override).
        """

    def name(self) -> str:
        """Nombre legible del renderer para logs y HUD."""
        return self.__class__.__name__


# ── Helper de selección de renderer ──────────────────────────────────────────

def select_renderer(config: dict) -> "BaseRenderer":
    """
    Fábrica: instancia el renderer óptimo automáticamente.

    Lógica de selección:
      1. Si backend == 'pil' (forzado por usuario) → PIL siempre
      2. Si backend == 'opengl' (forzado) o 'auto' → intentar OpenGL
      3. Default sin config ('auto'): intentar OpenGL, fallback a PIL

    El usuario solo necesita intervenir para FORZAR PIL en casos
    especiales (GPU incompatible, debugging, etc.). En instalación
    nueva el sistema elige OpenGL si está disponible, sin configuración.

    Parámetros:
        config — dict de settings.json (puede estar vacío → auto)
    """
    from cad.renderer_pil import RendererPIL

    rendering_cfg = config.get('rendering', {})
    # 'auto' es el nuevo default — intenta OpenGL, cae a PIL si no hay GPU
    backend  = rendering_cfg.get('backend', 'auto').lower()
    fallback = rendering_cfg.get('fallback_to_pil_on_error', True)

    # El usuario forzó PIL explícitamente → respetarlo sin intentar OpenGL
    if backend == 'pil':
        return RendererPIL()

    # backend == 'opengl' o 'auto' → intentar OpenGL
    try:
        from cad.renderer_opengl import RendererOpenGL
        renderer = RendererOpenGL()
        if renderer.available():
            if backend == 'auto':
                print('[renderer] OpenGL disponible — usando aceleración GPU.')
            return renderer
        # OpenGL no disponible en este sistema
        if backend == 'opengl':
            print('[renderer] OpenGL no disponible, usando PIL como fallback.')
        return RendererPIL()
    except ImportError:
        print('[renderer] renderer_opengl.py no encontrado, usando PIL.')
        return RendererPIL()
    except Exception as exc:
        print(f'[renderer] OpenGL falló ({exc}), usando PIL.')
        return RendererPIL()
