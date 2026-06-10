"""
cad/renderer_opengl.py
======================
Renderizador CAD basado en OpenGL 3.3 Core Profile.

Estrategia de renderizado (offscreen):
  1. Contexto WGL creado sobre una ventana oculta (ctypes + Win32 API)
  2. Framebuffer Object (FBO) como superficie de render — nunca visible
  3. glReadPixels → PIL.Image RGB (mismo contrato que RendererPIL)
  4. Tkinter sigue mostrando el PhotoImage con paste() sin cambios

El engine NO sabe si el renderer es PIL u OpenGL — recibe PIL.Image siempre.

Estado de implementación:
  Semana 1 ✅  Contexto + FBO + shaders básicos + grid + Line entities
  Semana 2 ✅  Circle, Arc, Polyline, Spline, Ellipse, XLine, Leader, line width
  Semana 3 ✅  Text + Dimension + Leader — overlay PIL sobre imagen OpenGL
  Semana 4 ✅  Hatch + Insert — overlay PIL (reutiliza _block_cache de RendererPIL)
  Semana 5 ⏳  Canvas overlay, verificación threading
  Semana 6 ⏳  Layout / paper space
  Semana 7 ⏳  Tests SSIM, benchmarks
  Semana 8 ⏳  Cleanup, merge
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as _wt
import math
import sys
import threading
from typing import Any

from cad.renderer_base import BaseRenderer, RenderResult
import cad.viewport as _vp

# ── Imports opcionales ────────────────────────────────────────────────────────

try:
    from OpenGL import GL as _gl
    from OpenGL.GL import shaders as _glsl
    _OPENGL_OK = True
except ImportError:
    _OPENGL_OK = False

try:
    from PIL import Image as _PILImage
    import numpy as _np
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# ── Imports de entidades (lazy) ───────────────────────────────────────────────
# Se importan dentro de métodos para evitar circulares y reducir startup time.


# ── GLSL Shaders ──────────────────────────────────────────────────────────────
# Un único programa de shaders para toda la geometría básica (líneas, polilíneas,
# círculos, etc.). El color se pasa como atributo per-vertex.

_VERT_BASIC = """
#version 330 core
layout(location = 0) in vec2 aPos;
layout(location = 1) in vec3 aColor;

uniform mat4 uProjection;

out vec3 vColor;

void main() {
    gl_Position = uProjection * vec4(aPos, 0.0, 1.0);
    vColor = aColor;
}
"""

_FRAG_BASIC = """
#version 330 core
in  vec3 vColor;
out vec4 FragColor;

void main() {
    FragColor = vec4(vColor, 1.0);
}
"""

# ── Shaders de texto (font atlas) ────────────────────────────────────────────
_VERT_TEXT = """
#version 330 core
layout(location=0) in vec2 aPos;
layout(location=1) in vec2 aUV;
layout(location=2) in vec3 aColor;
uniform mat4 uProjection;
out vec2 vUV;
out vec3 vColor;
void main() {
    gl_Position = uProjection * vec4(aPos, 0.0, 1.0);
    vUV    = aUV;
    vColor = aColor;
}
"""

_FRAG_TEXT = """
#version 330 core
in vec2 vUV;
in vec3 vColor;
out vec4 FragColor;
uniform sampler2D uAtlas;
void main() {
    float dist  = texture(uAtlas, vUV).a;
    // SDF: fwidth adapta el suavizado al zoom actual (sub-pixel a zoom bajo,
    // nítido a zoom alto). Con atlas bitmap (sin SDF) los valores son 0/1 y
    // fwidth≈0, por lo que el resultado es idéntico al threshold anterior.
    float fw    = fwidth(dist) * 0.7 + 0.004;
    float alpha = smoothstep(0.5 - fw, 0.5 + fw, dist);
    if (alpha < 0.01) discard;
    FragColor = vec4(vColor, alpha);
}
"""

# ── Shaders de fill (hatch SOLID → triangle fan) ─────────────────────────────
_VERT_FILL = """
#version 330 core
layout(location=0) in vec2 aPos;
layout(location=1) in vec3 aColor;
uniform mat4 uProjection;
out vec3 vColor;
void main() {
    gl_Position = uProjection * vec4(aPos, 0.0, 1.0);
    vColor = aColor;
}
"""

_FRAG_FILL = """
#version 330 core
in  vec3 vColor;
out vec4 FragColor;
uniform float uAlpha;
void main() {
    FragColor = vec4(vColor, uAlpha);
}
"""


# ── GPU discreta: pistas al driver ───────────────────────────────────────────
# NvOptimusEnablement / AmdPowerXpressRequestHighPerformance:
#   El driver inspecciona estas variables en módulos cargados. En Python no
#   se exportan desde python.exe, pero algunos drivers las leen en DLLs hijas.
try:
    NvOptimusEnablement              = ctypes.c_uint32(1)
    AmdPowerXpressRequestHighPerformance = ctypes.c_int32(1)
except Exception:
    pass

# Patrones de GPU integrada (iGPU) por marca
_IGPU_PATTERNS = (
    "INTEL HD", "INTEL UHD", "INTEL IRIS", "INTEL(R) HD", "INTEL(R) UHD",
    "INTEL(R) IRIS",
    "RADEON(TM) GRAPHICS", "RADEON VEGA", "AMD RADEON(TM) GRAPHICS",
    "RADEON(TM) RX VEGA",
    "LLVMPIPE", "SOFTPIPE", "SWRAST", "MICROSOFT BASIC",
)
_DISCRETE_PATTERNS = (
    "GEFORCE", "QUADRO", "RTX", "GTX", "TESLA",
    "RADEON RX", "RADEON PRO", "RADEON VII", "RADEON W",
    "ARC A",
)


def _is_integrated_gpu(gpu_name: str) -> bool:
    u = gpu_name.upper()
    if any(p in u for p in _DISCRETE_PATTERNS):
        return False
    if any(p in u for p in _IGPU_PATTERNS):
        return True
    if "INTEL" in u:
        return True
    return False


def _print_gpu_fix_instructions(vendor: str) -> None:
    """Imprime instrucciones específicas por fabricante para forzar GPU discreta."""
    print("[OpenGL] ═══════════════════════════════════════════════════")
    print("[OpenGL]  SOLUCIÓN: Forzar GPU discreta manualmente")
    print("[OpenGL] ═══════════════════════════════════════════════════")
    v = vendor.upper()
    if "NVIDIA" in v or "INTEL" in v:
        print("[OpenGL]  NVIDIA Control Panel (método más confiable para OpenGL):")
        print("[OpenGL]  1. Clic derecho en escritorio → 'Panel de control NVIDIA'")
        print("[OpenGL]  2. Administrar configuración 3D → Configuración global")
        print("[OpenGL]  3. 'Procesador de gráficos preferido'")
        print("[OpenGL]     → 'Procesador NVIDIA de alto rendimiento'")
        print("[OpenGL]  4. Aplicar → Reiniciar la app")
    if "AMD" in v or "ATI" in v or "INTEL" in v:
        print("[OpenGL]  AMD Radeon Settings:")
        print("[OpenGL]  1. Clic derecho escritorio → 'AMD Radeon Software'")
        print("[OpenGL]  2. Sistema → Gráficos intercambiables")
        print("[OpenGL]  3. Seleccionar 'Alto rendimiento' para python.exe")
    print("[OpenGL]  Windows (alternativa D3D):")
    print("[OpenGL]  Configuración → Sistema → Pantalla → Gráficos")
    print("[OpenGL]  Agregar python.exe → Alto rendimiento")
    print("[OpenGL] ═══════════════════════════════════════════════════")


def _try_wgl_nv_gpu_affinity(hdc: int, pfd) -> int:
    """Intenta crear un DC de afinidad NVIDIA via WGL_NV_gpu_affinity.
    Devuelve el nuevo affinity HDC si tiene éxito, 0 si no está disponible."""
    try:
        ptr_enum   = _wglGetProcAddress(b"wglEnumGpusNV")
        ptr_create = _wglGetProcAddress(b"wglCreateAffinityDCNV")
        if not ptr_enum or not ptr_create:
            return 0

        ENUM_T   = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint,
                                       ctypes.POINTER(ctypes.c_void_p))
        CREATE_T = ctypes.WINFUNCTYPE(ctypes.c_void_p,
                                       ctypes.POINTER(ctypes.c_void_p))
        wglEnumGpusNV        = ENUM_T(ptr_enum)
        wglCreateAffinityDCNV = CREATE_T(ptr_create)

        gpu_handles = []
        for i in range(8):
            h = ctypes.c_void_p()
            if not wglEnumGpusNV(i, ctypes.byref(h)):
                break
            if h.value:
                gpu_handles.append(h.value)

        if not gpu_handles:
            return 0

        # Null-terminated list — usar todos los GPUs encontrados (NVIDIA maneja el orden)
        null_terminated = gpu_handles + [None]
        gpu_list = (ctypes.c_void_p * len(null_terminated))(*null_terminated)
        affinity_hdc = wglCreateAffinityDCNV(gpu_list)
        if not affinity_hdc:
            return 0

        # Aplicar pixel format al affinity DC
        fmt = _ChoosePixelFormat(affinity_hdc, ctypes.byref(pfd))
        if fmt and _SetPixelFormat(affinity_hdc, fmt, ctypes.byref(pfd)):
            print(f"[OpenGL] WGL_NV_gpu_affinity: affinity DC creado ({len(gpu_handles)} GPU(s))")
            return affinity_hdc
        return 0
    except Exception as exc:
        print(f"[OpenGL] WGL_NV_gpu_affinity no disponible: {exc}")
        return 0


def _ensure_discrete_gpu_preference() -> bool:
    """Escribe GpuPreference=2 en registro Windows (aplica a D3D, no a WGL).
    Devuelve True si ya estaba configurado."""
    try:
        import winreg, sys, os
        key_path = r"SOFTWARE\Microsoft\DirectX\UserGpuPreferences"
        app_path = os.path.abspath(sys.executable)
        pref_value = "GpuPreference=2;"

        # Leer valor actual para saber si ya estaba configurado
        already_set = False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path,
                                0, winreg.KEY_READ) as rk:
                current, _ = winreg.QueryValueEx(rk, app_path)
                already_set = (current == pref_value)
        except Exception:
            pass

        if already_set:
            return True

        # Escribir preferencia
        try:
            reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path,
                                     0, winreg.KEY_SET_VALUE)
        except FileNotFoundError:
            reg_key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path,
                                         0, winreg.KEY_SET_VALUE)
        with reg_key:
            winreg.SetValueEx(reg_key, app_path, 0, winreg.REG_SZ, pref_value)
        return False
    except Exception as exc:
        print(f"[OpenGL]   No se pudo escribir registro: {exc}")
        print("[OpenGL]   Manual: Configuración → Sistema → Pantalla → Gráficos")
        print("[OpenGL]          Agregar Python → Opción: Alto rendimiento")
        return False


# ── Win32 para contexto offscreen (Windows-only) ──────────────────────────────

_GDI32    = ctypes.WinDLL('gdi32')
_USER32   = ctypes.WinDLL('user32')
_KERNEL32 = ctypes.WinDLL('kernel32')
_OPENGL32 = ctypes.WinDLL('opengl32')

# ── Tipos correctos para Win32/WGL en 64-bit ─────────────────────────────────
# Sin restype declarado, ctypes usa c_int (32-bit) que trunca handles en 64-bit.

_DefWindowProcW = _USER32.DefWindowProcW
_DefWindowProcW.restype  = ctypes.c_ssize_t
_DefWindowProcW.argtypes = [
    _wt.HWND, _wt.UINT,
    ctypes.c_size_t,     # WPARAM
    ctypes.c_ssize_t,    # LPARAM
]

# GetDC / ReleaseDC
_GetDC = _USER32.GetDC
_GetDC.restype  = ctypes.c_void_p   # HDC — handle 64-bit en Windows x64
_GetDC.argtypes = [ctypes.c_void_p]  # HWND

_ReleaseDC = _USER32.ReleaseDC
_ReleaseDC.restype  = ctypes.c_int
_ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

_DestroyWindow = _USER32.DestroyWindow
_DestroyWindow.restype  = ctypes.c_bool
_DestroyWindow.argtypes = [ctypes.c_void_p]

# WGL
_wglCreateContext = _OPENGL32.wglCreateContext
_wglCreateContext.restype  = ctypes.c_void_p
_wglCreateContext.argtypes = [ctypes.c_void_p]

_wglDeleteContext = _OPENGL32.wglDeleteContext
_wglDeleteContext.restype  = ctypes.c_bool
_wglDeleteContext.argtypes = [ctypes.c_void_p]

_wglMakeCurrent = _OPENGL32.wglMakeCurrent
_wglMakeCurrent.restype  = ctypes.c_bool
_wglMakeCurrent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

_wglGetProcAddress = _OPENGL32.wglGetProcAddress
_wglGetProcAddress.restype  = ctypes.c_void_p
_wglGetProcAddress.argtypes = [ctypes.c_char_p]

_glGetError_raw = _OPENGL32.glGetError
_glGetError_raw.restype  = ctypes.c_uint
_glGetError_raw.argtypes = []

_glGetString_raw = _OPENGL32.glGetString
_glGetString_raw.restype  = ctypes.c_char_p
_glGetString_raw.argtypes = [ctypes.c_uint]

# Kernel32
_GetModuleHandleW = _KERNEL32.GetModuleHandleW
_GetModuleHandleW.restype  = ctypes.c_void_p   # HINSTANCE
_GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

# User32 — handles adicionales
_RegisterClassExW = _USER32.RegisterClassExW
_RegisterClassExW.restype  = ctypes.c_ushort   # ATOM
_RegisterClassExW.argtypes = [ctypes.c_void_p] # WNDCLASSEX*

_CreateWindowExW = _USER32.CreateWindowExW
_CreateWindowExW.restype  = ctypes.c_void_p    # HWND
_CreateWindowExW.argtypes = [
    ctypes.c_ulong,   # dwExStyle
    ctypes.c_wchar_p, # lpClassName
    ctypes.c_wchar_p, # lpWindowName
    ctypes.c_ulong,   # dwStyle
    ctypes.c_int,     # X
    ctypes.c_int,     # Y
    ctypes.c_int,     # nWidth
    ctypes.c_int,     # nHeight
    ctypes.c_void_p,  # hWndParent
    ctypes.c_void_p,  # hMenu
    ctypes.c_void_p,  # hInstance
    ctypes.c_void_p,  # lpParam
]

# GDI32
_ChoosePixelFormat = _GDI32.ChoosePixelFormat
_ChoosePixelFormat.restype  = ctypes.c_int
_ChoosePixelFormat.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

_SetPixelFormat = _GDI32.SetPixelFormat
_SetPixelFormat.restype  = ctypes.c_bool
_SetPixelFormat.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

PFD_DRAW_TO_WINDOW = 0x00000004
PFD_SUPPORT_OPENGL = 0x00000020
PFD_DOUBLEBUFFER   = 0x00000001   # requerido por NVIDIA ICD para exponer WGL extensions
PFD_TYPE_RGBA      = 0
PFD_MAIN_PLANE     = 0
WS_POPUP           = 0x80000000
CS_OWNDC           = 0x0020

WGL_CONTEXT_MAJOR_VERSION_ARB    = 0x2091
WGL_CONTEXT_MINOR_VERSION_ARB    = 0x2092
WGL_CONTEXT_PROFILE_MASK_ARB     = 0x9126
WGL_CONTEXT_CORE_PROFILE_BIT_ARB = 0x00000001


class _PIXELFORMATDESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ('nSize',           _wt.WORD),
        ('nVersion',        _wt.WORD),
        ('dwFlags',         _wt.DWORD),
        ('iPixelType',      _wt.BYTE),
        ('cColorBits',      _wt.BYTE),
        ('cRedBits',        _wt.BYTE),   ('cRedShift',       _wt.BYTE),
        ('cGreenBits',      _wt.BYTE),   ('cGreenShift',     _wt.BYTE),
        ('cBlueBits',       _wt.BYTE),   ('cBlueShift',      _wt.BYTE),
        ('cAlphaBits',      _wt.BYTE),   ('cAlphaShift',     _wt.BYTE),
        ('cAccumBits',      _wt.BYTE),
        ('cAccumRedBits',   _wt.BYTE),   ('cAccumGreenBits', _wt.BYTE),
        ('cAccumBlueBits',  _wt.BYTE),   ('cAccumAlphaBits', _wt.BYTE),
        ('cDepthBits',      _wt.BYTE),
        ('cStencilBits',    _wt.BYTE),
        ('cAuxBuffers',     _wt.BYTE),
        ('iLayerType',      _wt.BYTE),
        ('bReserved',       _wt.BYTE),
        ('dwLayerMask',     _wt.DWORD),
        ('dwVisibleMask',   _wt.DWORD),
        ('dwDamageMask',    _wt.DWORD),
    ]


# En Windows 64-bit: WPARAM = UINT_PTR, LPARAM = LONG_PTR (ambos 8 bytes)
WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,                    # LRESULT
    _wt.HWND, _wt.UINT,
    ctypes.c_size_t,                     # WPARAM (UINT_PTR)
    ctypes.c_ssize_t)                    # LPARAM (LONG_PTR)


class _WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ('cbSize',        _wt.UINT),
        ('style',         _wt.UINT),
        ('lpfnWndProc',   WNDPROCTYPE),
        ('cbClsExtra',    ctypes.c_int),
        ('cbWndExtra',    ctypes.c_int),
        ('hInstance',     _wt.HINSTANCE),
        ('hIcon',         _wt.HICON),
        ('hCursor',       _wt.HANDLE),
        ('hbrBackground', _wt.HBRUSH),
        ('lpszMenuName',  _wt.LPCWSTR),
        ('lpszClassName', _wt.LPCWSTR),
        ('hIconSm',       _wt.HICON),
    ]


def _def_wndproc(hwnd, msg, wparam, lparam):
    """Procedimiento de ventana mínimo para la ventana oculta GL."""
    return _DefWindowProcW(hwnd, msg, wparam, lparam)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _h2rgbf(hex_color: str) -> tuple:
    """Convierte '#RRGGBB' a (R, G, B) floats 0..1 para glClearColor."""
    h = hex_color.lstrip('#')
    try:
        return (int(h[0:2], 16) / 255,
                int(h[2:4], 16) / 255,
                int(h[4:6], 16) / 255)
    except Exception:
        return (0.0, 0.0, 0.0)


def _h2rgbf3(hex_color: str) -> tuple:
    """Retorna (r, g, b) como floats 0..1 para vertex attributes."""
    return _h2rgbf(hex_color)


def _ortho(left: float, right: float,
           bottom: float, top: float,
           near: float = -1.0, far: float = 1.0) -> list:
    """Retorna una matriz ortográfica 4×4 como lista plana (column-major, OpenGL)."""
    rl = right - left
    tb = top - bottom
    fn = far - near
    return [
        2.0/rl,        0.0,           0.0,          0.0,
        0.0,           2.0/tb,        0.0,          0.0,
        0.0,           0.0,          -2.0/fn,       0.0,
        -(right+left)/rl, -(top+bottom)/tb, -(far+near)/fn, 1.0,
    ]


def _proj_matrix(ctx: Any) -> list:
    """
    Proyección ortográfica que mapea coordenadas mundo → clip space.

    OP-1: proyección Y-down. bottom/top están intercambiados respecto a la
    convención Y-up estándar, lo que invierte el eje Y en clip space:
      bottom = oy/sc   → clip Y=-1 → OpenGL screen-bottom → glReadPixels row 0
      top    = -(H-oy)/sc → clip Y=+1 → OpenGL screen-top → glReadPixels last row

    Efecto: la fila visual superior queda en glReadPixels row 0, igual que
    el origen PIL (Y-down). _read_pixels ya NO necesita flip vertical.

    Prueba: clip_y_down = -clip_y_up → PIL row = (H-1)*(clip_y_up+1)/2
    idéntico al camino Y-up+flip. El overlay PIL usa _vp.w2s() independiente.
    """
    sc = ctx.scale
    ox, oy = ctx.offset_x, ctx.offset_y
    W,  H  = ctx.W, ctx.H

    left   = -ox / sc              # mundo x en pixel 0
    right  = (W - ox) / sc         # mundo x en pixel W
    bottom = oy / sc               # OP-1: era top (Y-up) → ahora bottom (Y-down)
    top    = -(H - oy) / sc        # OP-1: era bottom (Y-up) → ahora top (Y-down)

    return _ortho(left, right, bottom, top)


# ── LOD helpers (C1) ─────────────────────────────────────────────────────────

def _entity_px(e: Any, sc: float) -> float:
    """
    Tamaño aproximado de la entidad en píxeles de pantalla (dimensión mayor).
    Se usa para LOD: entidades < LOD_MIN_PX se omiten en el render GL.
    """
    from cad.entities import Line, Circle, Arc, Polyline, Spline, Ellipse, XLine
    if isinstance(e, Line):
        return max(abs(e.x2 - e.x1), abs(e.y2 - e.y1)) * sc
    if isinstance(e, (Circle, Arc)):
        return e.radius * 2 * sc
    if isinstance(e, Ellipse):
        return max(e.rx, e.ry) * 2 * sc
    if isinstance(e, (Polyline, Spline)):
        if not e.points:
            return 0.0
        xs = [p[0] for p in e.points]
        ys = [p[1] for p in e.points]
        return max(max(xs) - min(xs), max(ys) - min(ys)) * sc
    return sc   # XLine y otros: siempre visibles

_LOD_MIN_PX = 0.8   # entidades menores a este umbral (px) se omiten en GL
_insert_bbox_cache: dict = {}  # {block_name: (w, h, cx, cy)} — persiste entre tessellations


# ── Tessellación — Semana 2 ──────────────────────────────────────────────────
# Todas las funciones retornan una lista plana de vértices
# [x0, y0, r, g, b, x1, y1, r, g, b, ...] lista para _draw_lines_raw()
# usando GL_LINES (pares de puntos = un segmento cada par).

def _n_segs(radius: float, scale: float) -> int:
    """Número de segmentos para tessellación de círculo/arco según LOD."""
    px = radius * scale          # radio en píxeles
    if px < 4:   return 8
    if px < 20:  return 16
    if px < 80:  return 32
    if px < 300: return 64
    return 128


def _tess_circle(cx: float, cy: float, radius: float,
                 col: tuple, scale: float) -> list:
    """Tessella un círculo completo como N segmentos GL_LINES."""
    n = _n_segs(radius, scale)
    verts = []
    step = 2 * math.pi / n
    for i in range(n):
        a0 = i * step
        a1 = (i + 1) * step
        x0 = cx + radius * math.cos(a0)
        y0 = cy + radius * math.sin(a0)
        x1 = cx + radius * math.cos(a1)
        y1 = cy + radius * math.sin(a1)
        verts += [x0, y0, col[0], col[1], col[2],
                  x1, y1, col[0], col[1], col[2]]
    return verts


def _tess_arc(cx: float, cy: float, radius: float,
              start_ang: float, end_ang: float, ccw: bool,
              col: tuple, scale: float) -> list:
    """
    Tessella un arco como N segmentos GL_LINES.

    Ángulos en grados, convención AutoCAD:
      ccw=True  → de start_ang a end_ang en sentido antihorario
      ccw=False → de start_ang a end_ang en sentido horario
    """
    n = _n_segs(radius, scale)
    sa = math.radians(start_ang)
    ea = math.radians(end_ang)

    if ccw:
        if ea <= sa:
            ea += 2 * math.pi
        sweep = ea - sa
    else:
        if ea >= sa:
            ea -= 2 * math.pi
        sweep = ea - sa   # negativo

    step = sweep / max(n, 4)
    verts = []
    for i in range(abs(int(sweep / abs(step))) if step else 0):
        a0 = sa + i * step
        a1 = sa + (i + 1) * step
        x0 = cx + radius * math.cos(a0)
        y0 = cy + radius * math.sin(a0)
        x1 = cx + radius * math.cos(a1)
        y1 = cy + radius * math.sin(a1)
        verts += [x0, y0, col[0], col[1], col[2],
                  x1, y1, col[0], col[1], col[2]]
    return verts


def _tess_polyline(points: list, closed: bool, col: tuple) -> list:
    """Tessella una polilínea como segmentos GL_LINES."""
    if len(points) < 2:
        return []
    verts = []
    pts = points + [points[0]] if (closed and len(points) > 2) else points
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        verts += [x0, y0, col[0], col[1], col[2],
                  x1, y1, col[0], col[1], col[2]]
    return verts


def _tess_spline(e: Any, col: tuple) -> list:
    """Tessella una Spline usando sus puntos interpolados."""
    ipts = e.interp(n_seg=20)
    if len(ipts) < 2:
        return []
    verts = []
    for i in range(len(ipts) - 1):
        x0, y0 = ipts[i]
        x1, y1 = ipts[i + 1]
        verts += [x0, y0, col[0], col[1], col[2],
                  x1, y1, col[0], col[1], col[2]]
    return verts


def _tess_ellipse(e: Any, col: tuple, scale: float) -> list:
    """Tessella una Ellipse usando los puntos de su contorno."""
    bpts = e._pts_on_boundary(72)
    if len(bpts) < 2:
        return []
    bpts = list(bpts) + [bpts[0]]   # cerrar
    verts = []
    for i in range(len(bpts) - 1):
        x0, y0 = bpts[i]
        x1, y1 = bpts[i + 1]
        verts += [x0, y0, col[0], col[1], col[2],
                  x1, y1, col[0], col[1], col[2]]
    return verts


def _apply_dash_to_path(pts: list, col: tuple,
                        dash_px: tuple, sc: float) -> list:
    """Aplica patrón de guiones continuamente a lo largo de una ruta de puntos.

    pts      — lista de (x, y) en coordenadas mundo
    col      — (r, g, b) floats 0-1
    dash_px  — patrón en píxeles (ya incluye ltf): (draw, gap, draw, gap …)
    sc       — escala actual px/unidad-modelo

    Retorna lista plana [x,y,r,g,b, x,y,r,g,b, …] para GL_LINES.
    Si dash_px está vacío devuelve la ruta como línea continua.
    """
    if len(pts) < 2:
        return []

    # Ruta sólida (CONTINUOUS)
    if not dash_px:
        verts = []
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]; x1, y1 = pts[i + 1]
            verts += [x0, y0, col[0], col[1], col[2],
                      x1, y1, col[0], col[1], col[2]]
        return verts

    # Convertir patrón de px a unidades mundo
    sc_ = max(sc, 0.001)
    pat = [d / sc_ for d in dash_px]
    pat_len = len(pat)
    if sum(pat) <= 0:
        return []

    verts   = []
    draw    = True
    pat_idx = 0
    pat_pos = 0.0   # posición dentro del elemento actual del patrón

    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        dx, dy = x1 - x0, y1 - y0
        seg_len = math.hypot(dx, dy)
        if seg_len < 1e-9:
            continue
        ux, uy = dx / seg_len, dy / seg_len
        seg_pos = 0.0

        while seg_pos < seg_len:
            elem_remaining = pat[pat_idx % pat_len] - pat_pos
            seg_remaining  = seg_len - seg_pos
            advance        = min(elem_remaining, seg_remaining)

            if draw and advance > 0:
                sx = x0 + ux * seg_pos
                sy = y0 + uy * seg_pos
                ex = x0 + ux * (seg_pos + advance)
                ey = y0 + uy * (seg_pos + advance)
                verts += [sx, sy, col[0], col[1], col[2],
                          ex, ey, col[0], col[1], col[2]]

            seg_pos += advance
            pat_pos += advance

            if pat_pos + 1e-9 >= pat[pat_idx % pat_len]:
                pat_pos  = 0.0
                pat_idx += 1
                draw     = not draw

    return verts


def _point_in_poly(x: float, y: float, poly: list) -> bool:
    """Ray-casting point-in-polygon."""
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i];  xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj-xi)*(y-yi)/(yj-yi) + xi):
            inside = not inside
        j = i
    return inside


def _clip_seg_to_poly(px, py, qx, qy, poly):
    """Clip segment PQ to polygon. Returns list of (x0,y0,x1,y1) segments."""
    dx, dy = qx-px, qy-py
    ts = [0.0, 1.0]
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i];  bx, by = poly[(i+1) % n]
        nx2, ny2 = -(by-ay), (bx-ax)
        denom = nx2*dx + ny2*dy
        if abs(denom) < 1e-12:
            continue
        t = (nx2*(ax-px) + ny2*(ay-py)) / denom
        ts.append(t)
    ts.sort()
    segs = []
    for i in range(len(ts)-1):
        t_mid = (ts[i]+ts[i+1]) / 2
        mx, my = px + t_mid*dx, py + t_mid*dy
        if _point_in_poly(mx, my, poly):
            t0, t1 = ts[i], ts[i+1]
            segs.append((px+t0*dx, py+t0*dy, px+t1*dx, py+t1*dy))
    return segs


def _earcut_poly(pts: list) -> list:
    """Ear-clipping triangulation para polígonos simples sin huecos.
    Entrada: lista de (x,y). Salida: lista de (x0,y0, x1,y1, x2,y2).
    O(n²) — suficiente para boundaries < 200 vértices.
    """
    n = len(pts)
    if n < 3:
        return []
    if n == 3:
        return [(pts[0][0], pts[0][1], pts[1][0], pts[1][1],
                 pts[2][0], pts[2][1])]

    # Garantizar winding CCW (área con signo positiva)
    area2 = sum((pts[i][0] * pts[(i+1) % n][1] -
                 pts[(i+1) % n][0] * pts[i][1]) for i in range(n))
    if area2 < 0:
        pts = list(reversed(pts))

    def _cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])

    def _pt_in_tri(p, a, b, c):
        # Ignorar vértices coincidentes con los vértices del triángulo
        # (ocurre en bridge edges de polígonos con huecos — no bloquean el ear)
        _e = 1e-9
        if ((abs(p[0]-a[0]) < _e and abs(p[1]-a[1]) < _e) or
                (abs(p[0]-b[0]) < _e and abs(p[1]-b[1]) < _e) or
                (abs(p[0]-c[0]) < _e and abs(p[1]-c[1]) < _e)):
            return False
        d1 = _cross(a, b, p); d2 = _cross(b, c, p); d3 = _cross(c, a, p)
        neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
        pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
        return not (neg and pos)

    indices = list(range(len(pts)))
    result  = []
    limit   = len(indices) * len(indices) + 10

    while len(indices) > 3 and limit > 0:
        limit -= 1
        found = False
        ni = len(indices)
        for i in range(ni):
            a = pts[indices[(i-1) % ni]]
            b = pts[indices[i]]
            c = pts[indices[(i+1) % ni]]
            if _cross(a, b, c) <= 1e-12:
                continue
            if any(_pt_in_tri(pts[indices[j]], a, b, c)
                   for j in range(ni) if j not in ((i-1)%ni, i, (i+1)%ni)):
                continue
            result.append((a[0], a[1], b[0], b[1], c[0], c[1]))
            indices.pop(i)
            found = True
            break
        if not found:
            break

    if len(indices) == 3:
        a, b, c = [pts[i] for i in indices]
        result.append((a[0], a[1], b[0], b[1], c[0], c[1]))
    return result


def _bridge_hole(outer: list, hole: list) -> list:
    """Merge un hueco en el boundary exterior usando un bridge edge para earcut.

    El hueco debe tener winding CW (opuesto al outer CCW) para que el área del
    merged polygon sea outer_area - hole_area. Si llega CCW se invierte aquí.
    Retorna un único polígono con bridge edges repetidos listo para _earcut_poly.
    """
    if len(hole) < 3:
        return outer
    # Garantizar winding CW en el hole (opuesto a outer CCW)
    _ha = sum(hole[i][0] * hole[(i + 1) % len(hole)][1] -
              hole[(i + 1) % len(hole)][0] * hole[i][1]
              for i in range(len(hole)))
    if _ha > 0:   # CCW → invertir a CW
        hole = list(reversed(hole))
    # Vértice más a la derecha del hueco (punto de partida del bridge)
    hi = max(range(len(hole)), key=lambda i: hole[i][0])
    hx, hy = hole[hi]
    # Rayo horizontal desde (hx, hy) → arista del outer más cercana a la derecha
    best_x  = float('inf')
    best_vi = 0
    n = len(outer)
    for i in range(n):
        p1 = outer[i]; p2 = outer[(i + 1) % n]
        y1, y2 = p1[1], p2[1]
        if (y1 <= hy < y2) or (y2 <= hy < y1):
            t  = (hy - y1) / (y2 - y1)
            ix = p1[0] + t * (p2[0] - p1[0])
            if hx <= ix < best_x:
                best_x  = ix
                best_vi = i if outer[i][0] >= outer[(i + 1) % n][0] else (i + 1) % n
    # hole_seq empieza en el vértice más a la derecha del hole
    hole_seq = hole[hi:] + hole[:hi]
    # Merged: outer[0..V] + hole(desde hi) + [hole[hi]] + outer[V..n-1]
    # outer[V] al inicio de outer[best_vi:] actúa como bridge-back al outer.
    return outer[:best_vi + 1] + hole_seq + [hole_seq[0]] + outer[best_vi:]


def _tess_hatch_gl(e: Any, line_rows: dict, tri_rows: dict,
                    gl_ids: set, resolve, ctx: Any) -> None:
    """Tessella Hatch para el pipeline VRAM con límites seguros.

    Límites de seguridad:
    - sp_px < 2.0 (sub-píxel): solid fill GPU sin importar tamaño de boundary.
    - Boundary > 200 verts y sp_px >= 2.0 → PIL fallback.
    - Número de líneas por familia > 600 → PIL fallback (hatch enorme).
    """
    col_t, lw, dash = resolve(e)
    if col_t is None:
        return
    if len(e.boundary) < 3:
        return
    r, g, b = col_t
    _e_layer = getattr(e, 'layer', '0') or '0'   # clave de capa para VBO
    _tl = tri_rows[_e_layer]                       # lista de triángulos de esta capa
    pts = e.boundary
    sc  = ctx.scale
    n   = len(pts)

    is_solid = e.pattern.upper() in ("SOLID", "") or getattr(e, 'is_gradient', False)

    # ── Patrón sub-píxel: solid fill GPU antes de chequear tamaño de boundary ─
    # sp_px < 2.0 → líneas invisibles, indistinguible de solid fill.
    # Se maneja aquí (antes del límite de 200 verts) para cubrir boundaries
    # complejos que de otro modo caerían al PIL overlay y tardarían 1-2 s.
    # Estrategia: convexo → fan exacto; cóncavo ≤500 verts → earcut exacto;
    # cóncavo >500 verts → fan desde centroide (artefactos invisibles a esta escala).
    if not is_solid:
        sp_w  = e.scale if e.scale and e.scale > 1e-9 else 0.25
        sp_px = sp_w * sc
        if sp_px < 2.0:
            # Sub-píxel: solid fill. Con holes, merge y earcut para respetar vanos.
            _hl = getattr(e, 'holes', [])
            if _hl:
                _mp = list(pts)
                for _h in _hl:
                    if len(_h) >= 3 and len(_mp) + len(_h) <= 1500:
                        _mp = _bridge_hole(_mp, _h)
                _tris = _earcut_poly(_mp)
                if _tris:
                    for x0, y0, x1, y1, x2, y2 in _tris:
                        _tl += [x0, y0, r, g, b, x1, y1, r, g, b, x2, y2, r, g, b]
                    gl_ids.add(id(e))
                    return
                # earcut failed con holes → fall through al fan sin holes
            ok = False
            if _poly_is_convex(pts):
                cx = sum(p[0] for p in pts) / n
                cy = sum(p[1] for p in pts) / n
                for i in range(n):
                    p1 = pts[i]; p2 = pts[(i + 1) % n]
                    _tl += [cx, cy, r, g, b, p1[0], p1[1], r, g, b, p2[0], p2[1], r, g, b]
                ok = True
            elif n <= 1000:
                tris = _earcut_poly(pts)
                if tris:
                    for x0, y0, x1, y1, x2, y2 in tris:
                        _tl += [x0, y0, r, g, b, x1, y1, r, g, b, x2, y2, r, g, b]
                    ok = True
            if not ok:
                # Cóncavo grande: centroid fan O(n), artefactos invisibles a sp_px<2
                cx = sum(p[0] for p in pts) / n
                cy = sum(p[1] for p in pts) / n
                for i in range(n):
                    p1 = pts[i]; p2 = pts[(i + 1) % n]
                    _tl += [cx, cy, r, g, b, p1[0], p1[1], r, g, b, p2[0], p2[1], r, g, b]
            gl_ids.add(id(e))
            return

    # Boundary muy complejo → PIL lo maneja mejor (solo para patrones visibles sp_px≥2)
    # 500 verts: earcut O(n²) ≈ 125k ops ≈ 15ms — aceptable vs 1400ms PIL
    if n > 500:
        raise ValueError("complex boundary")

    if is_solid:
        if getattr(e, 'is_gradient', False):
            # Gradient → solid fill GPU usando el color primario del gradiente.
            # Evita el PIL fallback (~15ms/hatch) para los 40 hatches de ventana.
            gc = getattr(e, 'gradient_color', None)
            if gc and isinstance(gc, str) and gc.startswith('#'):
                try:
                    _gh = gc.lstrip('#')
                    r, g, b = (int(_gh[0:2], 16) / 255.0,
                               int(_gh[2:4], 16) / 255.0,
                               int(_gh[4:6], 16) / 255.0)
                except Exception:
                    pass
        # Merge holes para earcut-with-holes (ventanas/puertas en muros SOLID)
        holes = getattr(e, 'holes', [])
        if holes:
            merged = list(pts)
            for hole in holes:
                if len(hole) >= 3 and len(merged) + len(hole) <= 1500:
                    merged = _bridge_hole(merged, hole)
            tris = _earcut_poly(merged)
            if tris:
                for x0, y0, x1, y1, x2, y2 in tris:
                    _tl += [x0, y0, r, g, b, x1, y1, r, g, b, x2, y2, r, g, b]
                gl_ids.add(id(e))
                return
            # earcut-with-holes falló → fall back a outer boundary only
        if _poly_is_convex(pts):
            # Triangle fan desde centroide (O(n), caso rápido — sin holes)
            cx = sum(p[0] for p in pts) / n
            cy = sum(p[1] for p in pts) / n
            for i in range(n):
                p1 = pts[i];  p2 = pts[(i+1) % n]
                _tl += [cx, cy, r, g, b,
                        p1[0], p1[1], r, g, b,
                        p2[0], p2[1], r, g, b]
        else:
            # Earcut para polígonos cóncavos sin holes
            tris = _earcut_poly(pts)
            if not tris:
                raise ValueError("earcut failed")
            for x0, y0, x1, y1, x2, y2 in tris:
                _tl += [x0, y0, r, g, b,
                        x1, y1, r, g, b,
                        x2, y2, r, g, b]
        gl_ids.add(id(e))
        return

    # ── Patrón de líneas: sp_px >= 2.0, boundary <= 200 verts ────────────────
    sp_w  = e.scale if e.scale and e.scale > 1e-9 else 0.25
    sp_px = sp_w * sc

    xs = [p[0] for p in pts];  ys = [p[1] for p in pts]
    bx0, bx1 = min(xs), max(xs)
    by0, by1 = min(ys), max(ys)
    diag_w  = math.hypot(bx1-bx0, by1-by0)
    n_lines = int(diag_w / sp_w) + 2

    if n_lines > 2000:
        raise ValueError("too many lines")       # demasiado denso, PIL fallback

    cx_w = (bx0+bx1)/2;  cy_w = (by0+by1)/2

    # Familias de líneas del patrón
    plines = getattr(e, 'pattern_lines', [])
    if plines:
        families = []
        for pl_ang, pl_sp in plines:
            if abs(pl_sp) < 1e-9:
                continue
            sp2 = abs(pl_sp)
            if sp2 * sc < 2.0:
                continue                         # demasiado denso
            families.append((pl_ang + e.angle, sp2))
        if not families:
            raise ValueError("all families too dense")
    else:
        from cad.renderer_pil import RendererPIL as _RPIL
        pat_info = _RPIL._HATCH_PATTERNS.get(e.pattern.upper())
        if pat_info:
            base_ang, is_cross = pat_info
            ang = e.angle if e.angle != 0.0 else base_ang
            families = [(ang, sp_w)]
            if is_cross:
                families.append((ang+90, sp_w))
        else:
            families = [(e.angle if e.angle else 45.0, sp_w)]

    half = diag_w + sp_w * 2
    for ang_deg, sp_f in families:
        n_f = int(diag_w / sp_f) + 2
        if n_f > 2000:
            continue                             # familia muy densa, omitir
        a_rad = math.radians(ang_deg)
        ca, sa   = math.cos(a_rad), math.sin(a_rad)
        pca, psa = math.cos(a_rad + math.pi/2), math.sin(a_rad + math.pi/2)
        for i in range(-n_f, n_f+1):
            d  = i * sp_f
            mx = cx_w + pca*d;  my = cy_w + psa*d
            p0x = mx - ca*half;  p0y = my - sa*half
            p1x = mx + ca*half;  p1y = my + sa*half
            for (sx0, sy0, sx1, sy1) in _clip_seg_to_poly(p0x, p0y, p1x, p1y, pts):
                line_rows[(_e_layer, lw)].append((sx0, sy0, r, g, b, sx1, sy1, r, g, b))

    gl_ids.add(id(e))


def _tess_insert_gl(ins: Any, line_rows: dict, curve_arrs: dict,
                    tri_rows: dict, gl_ids: set, resolve, ctx: Any,
                    depth: int = 0, fail_log: dict = None,
                    ins_layer: str = '0',
                    block_geom_cache: dict = None,
                    block_cache_layer_idx: dict = None) -> None:
    """Tessella un Insert/BlockRef expandiendo sus entidades hijo en GL.
    Aplica la transformación del insert (traslación + rotación + escala).
    depth limita recursión para bloques anidados (máx 3).
    ins_layer: capa del insert padre — todos los segmentos generados se
    atribuyen a esta capa en _vram_bufs para que el toggle de visibilidad
    de la capa afecte a todo el bloque instantáneamente.
    """
    if depth > 3:
        raise ValueError("nested too deep")

    block_name = getattr(ins, 'block_name', None)
    block_defs = getattr(ctx, 'block_defs', {})
    block = block_defs.get(block_name) if block_name else None
    if not block or not getattr(block, 'entities', None):
        raise ValueError("block not found")

    angle = math.radians(getattr(ins, 'angle', 0.0) or 0.0)
    sx    = getattr(ins, 'scale_x', 1.0) or 1.0
    sy    = getattr(ins, 'scale_y', 1.0) or 1.0
    ix    = getattr(ins, 'x', 0.0) or 0.0
    iy    = getattr(ins, 'y', 0.0) or 0.0
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)

    def _tp(x, y):
        """Transforma un punto del espacio bloque al espacio mundo."""
        x *= sx; y *= sy
        return x * cos_a - y * sin_a + ix, x * sin_a + y * cos_a + iy

    from cad.entities import Line as _L, Circle as _C, Arc as _A, Polyline as _PL, Spline as _Sp, Ellipse as _El

    sc = ctx.scale

    # ── Screen-Space LOD ────────────────────────────────────────────────────
    # bbox cacheado por nombre de bloque a nivel de módulo (persiste entre tessellations)
    if block_name not in _insert_bbox_cache:
        _xs: list = []; _ys: list = []
        for _c in block.entities:
            if hasattr(_c, 'x1') and hasattr(_c, 'x2'):
                _xs += [_c.x1, _c.x2]; _ys += [_c.y1, _c.y2]
            elif hasattr(_c, 'cx'):
                _r = getattr(_c, 'radius', 0) or 0
                _xs += [_c.cx - _r, _c.cx + _r]; _ys += [_c.cy - _r, _c.cy + _r]
            elif hasattr(_c, 'points') and _c.points:
                _xs += [p[0] for p in _c.points]; _ys += [p[1] for p in _c.points]
        if _xs and _ys:
            _bw = max(_xs) - min(_xs); _bh = max(_ys) - min(_ys)
            _bcx = (max(_xs) + min(_xs)) * 0.5; _bcy = (max(_ys) + min(_ys)) * 0.5
        else:
            _bw = _bh = 1.0; _bcx = _bcy = 0.0
        _insert_bbox_cache[block_name] = (_bw, _bh, _bcx, _bcy)

    _bbw, _bbh, _bbcx, _bbcy = _insert_bbox_cache[block_name]
    _scale_max = max(abs(sx), abs(sy))
    _bbox_px = max(_bbw * _scale_max, _bbh * _scale_max) * sc

    if _bbox_px < 3.0:
        gl_ids.add(id(ins))
        return

    if _bbox_px < 5.0:
        # Proxy: 4 líneas del contorno del bbox (con transform completo aplicado)
        try:
            _col, _lw, _ = resolve(ins)
            if _col is not None:
                _r2, _g2, _b2 = _col
                _hw = _bbw * 0.5;  _hh = _bbh * 0.5
                _corners = [(_bbcx - _hw, _bbcy - _hh), (_bbcx + _hw, _bbcy - _hh),
                            (_bbcx + _hw, _bbcy + _hh), (_bbcx - _hw, _bbcy + _hh)]
                _tpts = [_tp(cx, cy) for cx, cy in _corners]
                for _i in range(4):
                    _p1, _p2 = _tpts[_i], _tpts[(_i + 1) % 4]
                    line_rows[(ins_layer, _lw)].append((_p1[0], _p1[1], _r2, _g2, _b2,
                                                        _p2[0], _p2[1], _r2, _g2, _b2))
        except Exception:
            pass
        gl_ids.add(id(ins))
        return
    # ────────────────────────────────────────────────────────────────────────

    # ── Cache key (escala+rotación en key para que arcos sean correctos) ──
    _sc      = ctx.scale
    _lod_now = int(math.log2(max(_sc, 0.001)))
    _bgc_key = (block_name, _lod_now, ins_layer,
                round(sx, 2), round(sy, 2),
                round(angle % (2 * math.pi) / math.pi * 180, 0))

    # ── Cache HIT ─────────────────────────────────────────────────────────
    if block_geom_cache is not None and _bgc_key in block_geom_cache:
        for _lw_px, _arr_pts in block_geom_cache[_bgc_key].items():
            _arr_w = _arr_pts.copy()
            _arr_w[0::10] += ix;  _arr_w[1::10] += iy
            _arr_w[5::10] += ix;  _arr_w[6::10] += iy
            curve_arrs[(ins_layer, _lw_px)].append(_arr_w)
        gl_ids.add(id(ins))
        return
    # ────────────────────────────────────────────────────────────────────────

    _cache_accum: dict = {}          # lw_px → list[np.ndarray 1D pre-translation]
    _skip_cache = block_geom_cache is None

    for child in block.entities:
        try:
            col, lw, dash = resolve(child)
            if col is None:
                continue
            r, g, b = col

            if isinstance(child, _L):
                if _entity_px(child, sc) < _LOD_MIN_PX: continue
                x1, y1 = _tp(child.x1, child.y1)
                x2, y2 = _tp(child.x2, child.y2)
                line_rows[(ins_layer, lw)].append((x1, y1, r, g, b, x2, y2, r, g, b))
                if not _skip_cache:
                    _cache_accum.setdefault(lw, []).append(
                        _np.array([x1-ix, y1-iy, r, g, b, x2-ix, y2-iy, r, g, b],
                                  dtype=_np.float32))

            elif isinstance(child, _C):
                if _entity_px(child, sc) < _LOD_MIN_PX: continue
                cx2, cy2 = _tp(child.cx, child.cy)
                radius   = child.radius * max(abs(sx), abs(sy))
                n = _n_segs(radius, sc)
                a_arr = _np.linspace(0.0, 2*math.pi, n+1, dtype=_np.float32)
                xs = _np.float32(cx2) + _np.float32(radius) * _np.cos(a_arr)
                ys = _np.float32(cy2) + _np.float32(radius) * _np.sin(a_arr)
                seg = _np.empty(n * 10, dtype=_np.float32)
                seg[0::10]=xs[:-1]; seg[1::10]=ys[:-1]
                seg[2::10]=r;       seg[3::10]=g;       seg[4::10]=b
                seg[5::10]=xs[1:];  seg[6::10]=ys[1:]
                seg[7::10]=r;       seg[8::10]=g;       seg[9::10]=b
                curve_arrs[(ins_layer, lw)].append(seg)
                if not _skip_cache:
                    _sp = seg.copy(); _sp[0::10]-=ix; _sp[1::10]-=iy
                    _sp[5::10]-=ix;   _sp[6::10]-=iy
                    _cache_accum.setdefault(lw, []).append(_sp)

            elif isinstance(child, _A):
                if _entity_px(child, sc) < _LOD_MIN_PX: continue
                cx2, cy2 = _tp(child.cx, child.cy)
                radius   = child.radius * max(abs(sx), abs(sy))
                # Rotar los ángulos del arco
                sa2 = child.start_ang + math.degrees(angle)
                ea2 = child.end_ang   + math.degrees(angle)
                n   = _n_segs(radius, sc)
                sar = math.radians(sa2); ear = math.radians(ea2)
                if child.ccw:
                    if ear <= sar: ear += 2*math.pi
                else:
                    if ear >= sar: ear -= 2*math.pi
                sweep = ear - sar
                step  = sweep / max(n, 4)
                cnt   = abs(int(sweep / abs(step))) if step else 0
                if cnt < 1: continue
                a_arr = sar + _np.arange(cnt+1, dtype=_np.float32)*_np.float32(step)
                xs = _np.float32(cx2) + _np.float32(radius)*_np.cos(a_arr)
                ys = _np.float32(cy2) + _np.float32(radius)*_np.sin(a_arr)
                seg = _np.empty(cnt * 10, dtype=_np.float32)
                seg[0::10]=xs[:-1]; seg[1::10]=ys[:-1]
                seg[2::10]=r;       seg[3::10]=g;       seg[4::10]=b
                seg[5::10]=xs[1:];  seg[6::10]=ys[1:]
                seg[7::10]=r;       seg[8::10]=g;       seg[9::10]=b
                curve_arrs[(ins_layer, lw)].append(seg)
                if not _skip_cache:
                    _sp = seg.copy(); _sp[0::10]-=ix; _sp[1::10]-=iy
                    _sp[5::10]-=ix;   _sp[6::10]-=iy
                    _cache_accum.setdefault(lw, []).append(_sp)

            elif isinstance(child, _PL):
                if len(child.points) < 2: continue
                tpts = [_tp(p[0], p[1]) for p in child.points]
                if child.closed and len(tpts) > 2: tpts.append(tpts[0])
                pa = _np.asarray(tpts, dtype=_np.float32)
                n_seg = len(pa) - 1
                if n_seg < 1: continue
                seg = _np.empty(n_seg * 10, dtype=_np.float32)
                seg[0::10]=pa[:-1,0]; seg[1::10]=pa[:-1,1]
                seg[2::10]=r;         seg[3::10]=g;         seg[4::10]=b
                seg[5::10]=pa[1:,0];  seg[6::10]=pa[1:,1]
                seg[7::10]=r;         seg[8::10]=g;         seg[9::10]=b
                curve_arrs[(ins_layer, lw)].append(seg)
                if not _skip_cache:
                    _sp = seg.copy(); _sp[0::10]-=ix; _sp[1::10]-=iy
                    _sp[5::10]-=ix;   _sp[6::10]-=iy
                    _cache_accum.setdefault(lw, []).append(_sp)

            elif isinstance(child, _Sp):
                ipts = child.interp(n_seg=20)
                if len(ipts) < 2: continue
                tpts = [_tp(p[0], p[1]) for p in ipts]
                pa = _np.asarray(tpts, dtype=_np.float32)
                n_seg = len(pa) - 1
                if n_seg < 1: continue
                seg = _np.empty(n_seg * 10, dtype=_np.float32)
                seg[0::10]=pa[:-1,0]; seg[1::10]=pa[:-1,1]
                seg[2::10]=r;         seg[3::10]=g;         seg[4::10]=b
                seg[5::10]=pa[1:,0];  seg[6::10]=pa[1:,1]
                seg[7::10]=r;         seg[8::10]=g;         seg[9::10]=b
                curve_arrs[(ins_layer, lw)].append(seg)
                if not _skip_cache:
                    _sp = seg.copy(); _sp[0::10]-=ix; _sp[1::10]-=iy
                    _sp[5::10]-=ix;   _sp[6::10]-=iy
                    _cache_accum.setdefault(lw, []).append(_sp)

            elif isinstance(child, _El):
                if _entity_px(child, sc) < _LOD_MIN_PX: continue
                bpts = child._pts_on_boundary(48)
                if len(bpts) < 2: continue
                tpts = [_tp(p[0], p[1]) for p in bpts]
                tpts.append(tpts[0])   # cerrar
                pa = _np.asarray(tpts, dtype=_np.float32)
                n_seg = len(pa) - 1
                if n_seg < 1: continue
                seg = _np.empty(n_seg * 10, dtype=_np.float32)
                seg[0::10]=pa[:-1,0]; seg[1::10]=pa[:-1,1]
                seg[2::10]=r;         seg[3::10]=g;         seg[4::10]=b
                seg[5::10]=pa[1:,0];  seg[6::10]=pa[1:,1]
                seg[7::10]=r;         seg[8::10]=g;         seg[9::10]=b
                curve_arrs[(ins_layer, lw)].append(seg)
                if not _skip_cache:
                    _sp = seg.copy(); _sp[0::10]-=ix; _sp[1::10]-=iy
                    _sp[5::10]-=ix;   _sp[6::10]-=iy
                    _cache_accum.setdefault(lw, []).append(_sp)

            # Inserts anidados — no cachear padre (no podemos capturar su aporte)
            elif type(child).__name__ in ('Insert', 'BlockRef'):
                _skip_cache = True
                _tess_insert_gl(child, line_rows, curve_arrs, tri_rows,
                                gl_ids, resolve, ctx, depth+1, fail_log,
                                ins_layer=ins_layer,
                                block_geom_cache=block_geom_cache,
                                block_cache_layer_idx=block_cache_layer_idx)

            else:
                # Tipo no manejado — registrar para diagnóstico
                if fail_log is not None:
                    _tn = type(child).__name__
                    _fk = f"unhandled_child:{_tn}"
                    fail_log[_fk] = fail_log.get(_fk, 0) + 1

        except Exception:
            continue  # child problemático → omitir, no fallar todo el bloque

    # ── Populate cache (fuera del try/except de children) ─────────────────
    try:
        if not _skip_cache and _cache_accum:
            _entry: dict = {}
            for _lw_px, _arrs in _cache_accum.items():
                _entry[_lw_px] = _np.concatenate(_arrs) if len(_arrs) > 1 else _arrs[0]
            block_geom_cache[_bgc_key] = _entry
            if block_cache_layer_idx is not None:
                block_cache_layer_idx.setdefault(ins_layer, set()).add(block_name)
    except Exception:
        pass  # fallo en populate → sin cache, geometría ya está en line_rows/curve_arrs
    # ────────────────────────────────────────────────────────────────────────

    gl_ids.add(id(ins))


def _tess_dim_gl(e: Any, lw_lines: dict, tri_rows: dict,
                  gl_ids: set, resolve, ctx: Any) -> None:
    """Tessella una Dimension en coordenadas mundo para el pipeline VRAM.

    Líneas de extensión + línea de cota → lw_lines[lw] (GL_LINES, coords mundo).
    Flechas closed_filled → tri_rows[layer] (GL_TRIANGLES, coords mundo).
    El texto de la cota se encola en _dim_text_queue del ctx para font atlas.
    Lanza excepción si la cota no puede tessellarse → fallback PIL automático.
    """
    from cad.entities import Dimension as _Dim
    col_t, lw, dash = resolve(e)
    if col_t is None:
        return   # entidad oculta — no añadir a gl_ids

    r, g, b = col_t
    _e_layer = getattr(e, 'layer', '0') or '0'   # clave de capa para VBO
    _tl = tri_rows[_e_layer]                       # lista de triángulos de esta capa

    # ── Leer dimstyle ──────────────────────────────────────────────────
    dimstyles = ctx.config.get("dimstyles", {}).get("styles", {})
    ds        = dimstyles.get(e.style, {})
    DIMASZ    = float(ds.get("arrow_size",  0.06))
    EXT_OFF   = float(ds.get("ext_offset",  0.02))
    EXT_OVER  = float(ds.get("ext_overshoot", ds.get("ext_beyond", 0.03)))
    DIMBLK    = ds.get("arrow_type", "closed_filled")
    skip_ext  = getattr(e, 'no_ext', False)

    def _seg(x1, y1, x2, y2):
        """Agrega un segmento a line_rows con el lw de la cota."""
        lw_lines[(_e_layer, lw)].append((x1, y1, r, g, b, x2, y2, r, g, b))

    def _ext(sx0, sy0, ex0, ey0):
        """Línea de extensión con gap y prolongación en coords mundo."""
        dx, dy = ex0-sx0, ey0-sy0
        d = math.hypot(dx, dy)
        if d < EXT_OFF + 1e-9:
            return
        gx = sx0 + dx/d * EXT_OFF
        gy = sy0 + dy/d * EXT_OFF
        ox_ = ex0 + dx/d * EXT_OVER
        oy_ = ey0 + dy/d * EXT_OVER
        _seg(gx, gy, ox_, oy_)

    def _arrow_tri(tip_x, tip_y, base_x, base_y):
        """Flecha en coords mundo. Tipo según DIMBLK del dimstyle."""
        dx, dy = base_x-tip_x, base_y-tip_y
        d = math.hypot(dx, dy)
        if d < 1e-9:
            return
        ux, uy = dx/d, dy/d
        nx, ny = -uy, ux

        if DIMBLK in ("closed_filled", "", "default"):
            half = DIMASZ * 0.35
            p1x = tip_x + ux*DIMASZ + nx*half
            p1y = tip_y + uy*DIMASZ + ny*half
            p2x = tip_x + ux*DIMASZ - nx*half
            p2y = tip_y + uy*DIMASZ - ny*half
            _tl.extend([tip_x, tip_y, r, g, b,
                        p1x,   p1y,   r, g, b,
                        p2x,   p2y,   r, g, b])

        elif DIMBLK == "architectural":
            # ArchTick: línea diagonal 45° centrada en tip, longitud = 2×DIMASZ
            h = DIMASZ
            _seg(tip_x - ux*h - nx*h, tip_y - uy*h - ny*h,
                 tip_x + ux*h + nx*h, tip_y + uy*h + ny*h)

        elif DIMBLK == "dot":
            # Círculo (16 segmentos) centrado en tip
            r_ = DIMASZ * 0.5
            for i in range(16):
                a0 = 2*math.pi*i/16
                a1 = 2*math.pi*(i+1)/16
                _seg(tip_x + r_*math.cos(a0), tip_y + r_*math.sin(a0),
                     tip_x + r_*math.cos(a1), tip_y + r_*math.sin(a1))
        # "none" → sin flecha

    x1, y1 = e.p1;  x2, y2 = e.p2;  px, py = e.pos

    if e.dim_type in ("H", "V", "A"):
        if e.dim_type == "H":
            d1 = (x1, py);  d2 = (x2, py)
            e1s, e1e = (x1,y1), d1
            e2s, e2e = (x2,y2), d2
        elif e.dim_type == "V":
            d1 = (px, y1);  d2 = (px, y2)
            e1s, e1e = (x1,y1), d1
            e2s, e2e = (x2,y2), d2
        elif getattr(e, 'rot_angle', None) is not None:
            ar = math.radians(e.rot_angle)
            ux_, uy_ = math.cos(ar), math.sin(ar)
            nx_, ny_ = -uy_, ux_
            dim_n = px*nx_ + py*ny_
            off1  = dim_n - (x1*nx_ + y1*ny_)
            off2  = dim_n - (x2*nx_ + y2*ny_)
            d1 = (x1+nx_*off1, y1+ny_*off1)
            d2 = (x2+nx_*off2, y2+ny_*off2)
            e1s, e1e = (x1,y1), d1
            e2s, e2e = (x2,y2), d2
        else:   # alineada
            dlen = math.hypot(x2-x1, y2-y1)
            if dlen < 1e-9:
                raise ValueError("zero length")
            ux_, uy_ = (x2-x1)/dlen, (y2-y1)/dlen
            nx_, ny_ = -uy_, ux_
            off = (px-x1)*nx_ + (py-y1)*ny_
            d1 = (x1+nx_*off, y1+ny_*off)
            d2 = (x2+nx_*off, y2+ny_*off)
            e1s, e1e = (x1,y1), d1
            e2s, e2e = (x2,y2), d2

        if not skip_ext:
            _ext(*e1s, *e1e)
            _ext(*e2s, *e2e)
        _seg(d1[0], d1[1], d2[0], d2[1])
        _arrow_tri(d1[0], d1[1], d2[0], d2[1])
        _arrow_tri(d2[0], d2[1], d1[0], d1[1])

    elif e.dim_type == "D":
        opx = x1-(x2-x1);  opy = y1-(y2-y1)
        _seg(opx, opy, x2, y2)
        _arrow_tri(x2, y2, opx, opy)
        _arrow_tri(opx, opy, x2, y2)

    elif e.dim_type == "R":
        _seg(x1, y1, x2, y2)
        _arrow_tri(x2, y2, x1, y1)

    elif e.dim_type == "ANG":
        # ── Cota angular en GPU ───────────────────────────────────────────
        # p1=centro, p2=brazo1, pos=brazo2, text_pos determina el radio.
        cx, cy   = x1, y1                    # centro del ángulo
        arm1x, arm1y = x2, y2               # punto en brazo 1
        arm2x, arm2y = px, py               # punto en brazo 2

        d1 = math.hypot(arm1x-cx, arm1y-cy)
        d2 = math.hypot(arm2x-cx, arm2y-cy)
        if d1 < 1e-9 or d2 < 1e-9:
            raise ValueError("ANG degenerate")

        # Radio del arco: usar text_pos si existe, sino 60% del brazo más corto
        tp = getattr(e, 'text_pos', None)
        if tp and len(tp) >= 2:
            arc_r = math.hypot(tp[0]-cx, tp[1]-cy)
        else:
            arc_r = min(d1, d2) * 0.6
        if arc_r < 1e-9:
            raise ValueError("ANG zero radius")

        # Ángulos de los dos brazos desde el centro
        a1 = math.atan2(arm1y-cy, arm1x-cx)
        a2 = math.atan2(arm2y-cy, arm2x-cx)

        # Elegir la dirección del arco que da el ángulo agudo (≤ 180°)
        diff = (a2 - a1) % (2*math.pi)
        if diff > math.pi:
            a1, a2 = a2, a1     # invertir para ir por el arco corto
            diff   = 2*math.pi - diff

        # Puntos del arco en los brazos
        arc_e1x = cx + math.cos(a1)*arc_r
        arc_e1y = cy + math.sin(a1)*arc_r
        arc_e2x = cx + math.cos(a2)*arc_r
        arc_e2y = cy + math.sin(a2)*arc_r

        # Líneas de extensión: desde el punto del brazo hasta el arco
        if not skip_ext:
            _ext(arm1x, arm1y, arc_e1x, arc_e1y)
            _ext(arm2x, arm2y, arc_e2x, arc_e2y)

        # Arco como polilínea de N segmentos
        n_arc = max(8, int(abs(diff) * arc_r * 20))   # densidad proporcional
        n_arc = min(n_arc, 64)
        for i in range(n_arc):
            ta  = a1 + diff * i     / n_arc
            tb  = a1 + diff * (i+1) / n_arc
            sx_ = cx + math.cos(ta)*arc_r
            sy_ = cy + math.sin(ta)*arc_r
            ex_ = cx + math.cos(tb)*arc_r
            ey_ = cy + math.sin(tb)*arc_r
            _seg(sx_, sy_, ex_, ey_)

        # Flechas en los extremos del arco (tangente al arco)
        tang1x = -math.sin(a1)*arc_r
        tang1y =  math.cos(a1)*arc_r
        _arrow_tri(arc_e1x, arc_e1y, arc_e1x-tang1x*0.01, arc_e1y-tang1y*0.01)
        tang2x = -math.sin(a2)*arc_r
        tang2y =  math.cos(a2)*arc_r
        _arrow_tri(arc_e2x, arc_e2y, arc_e2x+tang2x*0.01, arc_e2y+tang2y*0.01)

    else:
        raise ValueError(f"dim type {e.dim_type} stays in PIL")

    # Cota tessellada OK — registrar para que PIL la omita
    gl_ids.add(id(e))

    # Texto de la cota: encolar en ctx para que _render_text_gl lo pinte
    dim_queue = getattr(ctx, '_dim_text_queue', None)
    if dim_queue is not None:
        dimstyles_cfg = ctx.config.get("dimstyles", {}).get("styles", {})
        ds2 = dimstyles_cfg.get(e.style, {})
        DIMTXT  = float(ds2.get("text_height", 0.20))
        DIMGAP  = float(ds2.get("text_offset",  0.05))
        DIMLFAC = float(ds2.get("scale_factor", 1.0))
        DIMDEC  = int(ds2.get("precision", 2))
        DIMPOST = ds2.get("suffix", "")
        txt = e.text_override if e.text_override else _dim_label(e, DIMLFAC, DIMDEC, DIMPOST)

        # Color del texto desde dimstyle — bylayer usa color de la cota
        _tc = ds2.get("text_color", "bylayer")
        if _tc and _tc.lower() not in ("bylayer", "byblock", "") and _tc.startswith("#"):
            try:
                h = _tc.lstrip("#")
                cr, cg, cb = int(h[0:2],16)/255, int(h[2:4],16)/255, int(h[4:6],16)/255
            except Exception:
                cr, cg, cb = r, g, b
        else:
            cr, cg, cb = r, g, b   # bylayer — mismo color que líneas de la cota

        # Posición del texto: punto medio de la línea de cota
        if e.dim_type in ("H", "V", "A"):
            tx = (d1[0]+d2[0])/2;  ty = (d1[1]+d2[1])/2
            dx_ = d2[0]-d1[0];  dy_ = d2[1]-d1[1]
            dlen2 = math.hypot(dx_, dy_)
            if dlen2 > 1e-9:
                nxs = -dy_/dlen2;  nys = dx_/dlen2
                off_t = DIMGAP + DIMTXT/2
                tx += nxs*off_t;  ty += nys*off_t
            ang = 0.0
            if e.dim_type == "A" and ds2.get("text_align", "aligned") == "aligned":
                ang = math.degrees(math.atan2(dy_, dx_))
        elif e.dim_type in ("R", "D"):
            tx = (x1+x2)/2;  ty = (y1+y2)/2
            ang = 0.0
        else:
            tx, ty, ang = px, py, 0.0
        dim_queue.append((tx, ty, txt, DIMTXT, ang, cr, cg, cb))


def _dim_label(e: Any, lfac: float, dec: int, post: str) -> str:
    """Genera el texto de la cota sin acceso al PIL renderer."""
    v = e.measurement() * lfac
    if e.dim_type == "ANG":
        return f"{v:.1f}°"
    if e.dim_type == "D":
        return f"Ø{v:.{dec}f}{post}"
    if e.dim_type == "R":
        return f"R{v:.{dec}f}{post}"
    return f"{v:.{dec}f}{post}"


def _tess_xline(e: Any, col: tuple, ctx: Any) -> list:
    """Tessella una XLine (línea infinita) clipeada al viewport."""
    dx = e.x2 - e.x1
    dy = e.y2 - e.y1
    d  = math.hypot(dx, dy)
    if d < 1e-10:
        return []
    ux, uy = dx / d, dy / d

    sc, ox, oy = ctx.scale, ctx.offset_x, ctx.offset_y
    W, H = ctx.W, ctx.H
    margin = max(W, H) + 500

    # Proyectar punto de referencia a pantalla
    sx1 = ox + e.x1 * sc
    sy1 = oy - e.y1 * sc

    t_values = []
    if abs(ux * sc) > 1e-6:
        for bnd in (-margin - sx1, W + margin - sx1):
            t_values.append(bnd / (ux * sc))
    if abs(uy * sc) > 1e-6:
        for bnd in (-margin - sy1, H + margin - sy1):
            t_values.append(bnd / (-uy * sc))
    if not t_values:
        return []

    t_min, t_max = min(t_values), max(t_values)
    wx0 = e.x1 + ux * t_min;  wy0 = e.y1 + uy * t_min
    wx1 = e.x1 + ux * t_max;  wy1 = e.y1 + uy * t_max
    return [wx0, wy0, col[0], col[1], col[2],
            wx1, wy1, col[0], col[1], col[2]]


def _tess_leader(e: Any, col: tuple, scale: float) -> list:
    """Tessella un Leader como polilínea (sin texto, sin flecha — Semana 3)."""
    return _tess_polyline(e.points, False, col)


# ── Font Atlas ───────────────────────────────────────────────────────────────
# Cache de métricas expuesto a nivel de módulo para que engine.py lo lea
# sin necesidad de tener acceso a la instancia del renderer.
_atlas_glyph_uvs: dict = {}   # char → (u0, v0, u1, v1, adv_px, h_px)
_atlas_base_px:   int  = 16   # tamaño en px con que se construyó el atlas
_atlas_sdf_pad:   int  = 0    # padding SDF en px (0 = atlas bitmap clásico)


def _sdf_l1(arr: "_np.ndarray", spread: float) -> "_np.ndarray":
    """
    SDF aproximado usando distancia L1 (taxicab) con numpy puro.

    arr    — 2D float32 array [0,1] (1=dentro del glifo)
    spread — radio en píxeles del gradiente; valores |dist|>spread se clampean

    Retorna float32 [0,1] donde 0.5 = borde del glifo, >0.5 = interior.
    L1 es visualmente indistinguible de Euclidean para spread ≤ 6px.
    """
    h, w = arr.shape
    INF  = float(spread * 4 + 1)

    def _dt(mask: "_np.ndarray") -> "_np.ndarray":
        """Distancia L1 a la región True más cercana (numpy vectorizado)."""
        d = _np.where(mask, 0.0, INF).astype(_np.float32)
        # Pasada horizontal (vectorizada sobre filas completas)
        for j in range(1, w):
            _np.minimum(d[:, j], d[:, j - 1] + 1.0, out=d[:, j])
        for j in range(w - 2, -1, -1):
            _np.minimum(d[:, j], d[:, j + 1] + 1.0, out=d[:, j])
        # Pasada vertical
        for i in range(1, h):
            _np.minimum(d[i], d[i - 1] + 1.0, out=d[i])
        for i in range(h - 2, -1, -1):
            _np.minimum(d[i], d[i + 1] + 1.0, out=d[i])
        return d

    inside = arr > 0.5
    d_in  = _dt(inside)    # dist de cada px al interior más cercano (0 si ya es interior)
    d_out = _dt(~inside)   # dist de cada px al exterior más cercano (0 si ya es exterior)
    # px interior:  d_in=0, d_out=dist_al_borde  → sdf = 0.5 + d_out/(2s) > 0.5
    # px exterior:  d_out=0, d_in=dist_al_borde  → sdf = 0.5 - d_in/(2s) < 0.5
    sdf = 0.5 + (d_out - d_in) / (2.0 * spread)
    return _np.clip(sdf, 0.0, 1.0).astype(_np.float32)


class _FontAtlas:
    """
    Textura VRAM con caracteres ASCII + Latin-1 pre-renderizados como SDF.

    Cada glifo se renderiza en una celda con `sdf_pad` píxeles de margen y se
    convierte a Signed Distance Field via L1 DT.  El shader lee el canal alpha
    y usa smoothstep(fwidth) para borde siempre nítido, a cualquier zoom.

    glyph_uvs[ch] = (u0, v0, u1, v1, adv_px, h_px)
        adv_px / h_px = dimensiones REALES del glifo (sin el margen SDF).
        Los UV abarcan la celda completa (glifo + margen).
    """
    ATLAS_W = 2048
    ATLAS_H = 1024

    def __init__(self):
        self.texture_id = None
        self.glyph_uvs: dict = {}   # char → (u0, v0, u1, v1, adv_px, h_px)
        self.base_px   = 64
        self.sdf_pad   = 3          # px de gradiente SDF a cada lado del glifo

    def build(self) -> None:
        """Renderiza caracteres con PIL, genera SDF y sube la textura a VRAM."""
        from PIL import Image as _I, ImageDraw as _D, ImageFont as _F

        # ── Intentar usar numpy para SDF ─────────────────────────────────
        try:
            import numpy as _np_local
            _use_sdf = True
        except ImportError:
            _use_sdf = False

        img = _I.new("RGBA", (self.ATLAS_W, self.ATLAS_H), (0, 0, 0, 0))
        font = None
        for path in ("arial.ttf", "C:/Windows/Fonts/arial.ttf",
                     "C:/Windows/Fonts/segoeui.ttf"):
            try:
                font = _F.truetype(path, self.base_px)
                break
            except Exception:
                pass
        if font is None:
            font = _F.load_default()

        pad   = self.sdf_pad if _use_sdf else 2
        x = y = pad
        row_h = 0

        for code in range(32, 256):
            ch  = chr(code)
            try:
                bbox = font.getbbox(ch)
            except Exception:
                bbox = (0, 0, self.base_px // 2, self.base_px)
            # Dimensiones reales del glifo (avance y alto sin margen SDF)
            gw = max(1, bbox[2] - bbox[0])
            gh = max(1, bbox[3] - bbox[1])
            # Celda en el atlas (glifo + margen SDF en los 4 lados)
            cw = gw + 2 * pad
            ch_ = gh + 2 * pad

            if x + cw > self.ATLAS_W:
                x = pad; y += row_h + pad; row_h = 0
            if y + ch_ > self.ATLAS_H:
                break   # atlas lleno — caracteres restantes sin glifo

            if _use_sdf:
                # ── Renderizar glifo en imagen temporal y generar SDF ──────
                tmp  = _I.new("L", (cw, ch_), 0)
                _D.Draw(tmp).text((pad - bbox[0], pad - bbox[1]),
                                  ch, fill=255, font=font)
                arr  = _np_local.array(tmp, dtype=_np_local.float32) / 255.0
                sdf  = _sdf_l1(arr, spread=float(pad))
                # Canal alpha del atlas = valor SDF
                rgba = _np_local.zeros((ch_, cw, 4), dtype=_np_local.uint8)
                rgba[:, :, 3] = (sdf * 255).astype(_np_local.uint8)
                img.paste(_I.fromarray(rgba, "RGBA"), (x, y))
            else:
                # ── Fallback bitmap (si numpy no está disponible) ──────────
                draw = _I.ImageDraw.Draw(img) if hasattr(_I, 'ImageDraw') \
                       else _D.Draw(img)
                draw.text((x + pad - bbox[0], y + pad - bbox[1]),
                          ch, fill=(255, 255, 255, 255), font=font)

            u0 = x  / self.ATLAS_W;  v0 = y   / self.ATLAS_H
            u1 = (x + cw) / self.ATLAS_W; v1 = (y + ch_) / self.ATLAS_H
            # adv_px / h_px = tamaño REAL del glifo (sin margen) para métricas
            self.glyph_uvs[ch] = (u0, v0, u1, v1, float(gw), float(gh))
            x += cw + 2
            row_h = max(row_h, ch_)

        raw = img.tobytes()
        self.texture_id = _gl.glGenTextures(1)
        _gl.glBindTexture(_gl.GL_TEXTURE_2D, self.texture_id)
        _gl.glTexImage2D(_gl.GL_TEXTURE_2D, 0, _gl.GL_RGBA,
                         self.ATLAS_W, self.ATLAS_H, 0,
                         _gl.GL_RGBA, _gl.GL_UNSIGNED_BYTE, raw)
        _gl.glTexParameteri(_gl.GL_TEXTURE_2D,
                            _gl.GL_TEXTURE_MIN_FILTER, _gl.GL_LINEAR)
        _gl.glTexParameteri(_gl.GL_TEXTURE_2D,
                            _gl.GL_TEXTURE_MAG_FILTER, _gl.GL_LINEAR)
        _gl.glBindTexture(_gl.GL_TEXTURE_2D, 0)
        # Publicar métricas globales para engine._text_world_bbox
        global _atlas_glyph_uvs, _atlas_base_px, _atlas_sdf_pad
        _atlas_glyph_uvs = self.glyph_uvs
        _atlas_base_px   = self.base_px
        _atlas_sdf_pad   = self.sdf_pad if _use_sdf else 0
        _mode = "SDF" if _use_sdf else "bitmap"
        print(f"[OpenGL] font atlas {self.ATLAS_W}×{self.ATLAS_H}"
              f"  chars={len(self.glyph_uvs)}  px={self.base_px}"
              f"  pad={pad}  mode={_mode}")


# ── Clase principal ───────────────────────────────────────────────────────────

class RendererOpenGL(BaseRenderer):
    """
    Renderer OpenGL 3.3 Core Profile para el visor CAD.

    Renderiza offscreen a un FBO y retorna PIL.Image (mismo contrato
    que RendererPIL). El engine no necesita saber que es OpenGL.
    """

    def __init__(self):
        # ── Estado de contexto ────────────────────────────────────────────
        self._ctx_ready   = False    # True después de _init_context()
        self._ctx_error   = None     # str si _init_context() falló
        self._hwnd        = None     # HWND de la ventana oculta
        self._hdc         = None     # HDC
        self._hglrc       = None     # HGLRC (contexto GL)

        # ── FBO ──────────────────────────────────────────────────────────
        self._fbo         = None
        self._rbo_color   = None
        self._rbo_depth   = None
        self._fbo_W       = 0
        self._fbo_H       = 0

        # ── Shaders ──────────────────────────────────────────────────────
        self._prog_basic  = None     # programa GL para geometría básica
        self._uloc_basic_proj: int = -1  # uniform location cacheada
        self._uloc_text_proj:  int = -1
        self._uloc_text_atlas: int = -1
        self._uloc_fill_proj:  int = -1
        self._vao         = None     # VAO para líneas
        self._vbo         = None     # VBO para líneas

        self._prog_text   = None     # shader de texto (atlas texturado)
        self._vao_text    = None
        self._vbo_text    = None
        self._font_atlas  = None     # _FontAtlas, inicializado lazy

        self._prog_fill   = None     # shader fill (triangle fan, hatch SOLID)
        self._vao_fill    = None
        self._vbo_fill    = None

        # ── Thread GL dedicado ────────────────────────────────────────────
        # NVIDIA WGL no permite usar el mismo contexto GL desde threads
        # distintos aunque se libere con wglMakeCurrent(NULL,NULL).
        # Solución: un único thread GL que vive para siempre, mantiene el
        # contexto current permanentemente y procesa los renders en cola.
        import queue as _q
        self._gl_queue      = _q.Queue()       # (ctx, result_event, result_box)
        self._last_render_img = None           # último frame bueno — fallback en timeout
        self._gl_thread = threading.Thread(
            target=self._gl_worker, daemon=True, name="CAD-GL-Thread")
        self._gl_thread.start()

        # Lock solo para proteger state de Python (no para GL — lo hace el thread)
        self._gl_lock = threading.Lock()

        # ── Helper PIL para texto/dimensiones (Semana 3) ──────────────────
        # Text, Dimension y Leader se renderizan en una segunda pasada PIL
        # sobre la imagen OpenGL — idéntico a RendererPIL, sin texturas GL.
        self._pil_helper = None

        # ── VBO cache (OP-4) ─────────────────────────────────────────────
        # Almacena {lw: np.ndarray float32} de la última escena.
        # Válido mientras (id(entities), lod_level) no cambie.
        # XLines quedan fuera del cache (dependen del viewport para clipear).
        self._vbo_cache     = {}    # lw_px → np.ndarray float32
        self._vbo_cache_key = None  # (id(entities), lod_level)

        # ── VRAM persistent buffers (OP-5) ───────────────────────────────
        # Geometría en GPU — re-upload solo en cache miss, NO en pan/zoom.
        # Pan/zoom solo cambia uProjection uniform (gratis). Con 600k ents
        # el primer upload puede tardar ~20s; todos los frames siguientes son <5ms.
        self._vram_bufs        = {}    # lw_px → (vao_h, vbo_h, n_verts) GL_LINES
        self._vram_bufs_key    = None  # igual que _vbo_cache_key cuando VRAM es válido
        self._vram_layer_ranges= {}    # lw_px → {layer_name: (start_vert, n_verts)}
        # Triángulos de flechas de cotas y hatches SOLID — mismo cache key que _vram_bufs
        self._vram_tri_buf         = None  # (vao_h, vbo_h, n_verts) GL_TRIANGLES o None
        self._vram_tri_layer_ranges: dict = {}  # layer_name → (start_vert, n_verts)
        # Copia CPU del último tri_arr — necesaria para tessellation incremental
        self._cpu_tri_arr: '_np.ndarray | None' = None
        # XLines: se redibujan en cada frame (viewport-dep) — guardamos entidades + colores
        self._xline_ents:   list = []  # [(entity, col, lw_px), ...]
        # IDs de cotas tesselladas en GL — el overlay PIL las omite
        self._dim_gl_ids:   set = set()
        # IDs de hatches tessellados en GL — el overlay PIL los omite
        self._hatch_gl_ids:  set = set()
        # IDs de insertos tessellados en GL — el overlay PIL los omite
        self._insert_gl_ids: set = set()
        # IDs de leaders tessellados en GL — el overlay PIL los omite
        self._leader_gl_ids: set = set()
        # ── Block geometry cache ──────────────────────────────────────────
        # Key: (block_name, lod, ins_layer, sx_r, sy_r, ang_r)
        # Value: dict[lw_px → np.ndarray float32 stride-10, pre-translation]
        # Persiste entre LOD changes; se limpia al abrir DXF o cambiar color de capa.
        self._block_geom_cache: dict = {}
        self._block_cache_layer_index: dict = {}  # ins_layer → set[block_name]
        # Bounds del área que fue tessellada (coords mundo) — para verificar
        # si el viewport actual está cubierto sin necesidad de tile_x/tile_y.
        self._tess_vp_bounds: tuple = None   # (vx0e, vy0e, vx1e, vy1e)

        # ── Background tessellation (600k entities sin freeze) ────────────
        # CPU tessellation corre en hilo de fondo; GPU upload en GL thread.
        import queue as _q2
        self._tess_result_q:     _q2.Queue = _q2.Queue(maxsize=1)
        self._tess_pending:      bool = False  # True mientras corre el hilo
        self._tess_just_uploaded:bool = False  # GL thread lo setea tras subir resultado; render() lo lee
        self._tess_done_cb = None  # callable() → redraw cuando tessellator termina
        self._tess_stale:        bool = False  # True cuando VRAM es del frame anterior
        self._tess_progress:  str  = ""      # mensaje legible del avance (hilo BG escribe, UI lee)

        # ── Text VBO cache (texto en espacio pantalla, key incluye viewport) ─
        # Evita reconstruir quads de texto cuando viewport y entidades no cambian.
        self._vbo_text_cache_key = None   # (entity_key, viewport_key)
        self._vbo_text_n_verts   = 0      # vértices cargados en _vbo_text

        # ── Overlay lazy cache (C2) ───────────────────────────────────────
        # La imagen del overlay PIL (Text/Dim/Hatch/Insert) se cachea y se
        # reutiliza mientras el viewport y las entidades no cambien.
        # En pan continuo el overlay cuesta 0ms — se compone por alpha.
        self._overlay_img  = None   # PIL.Image RGBA del overlay cacheado
        self._overlay_mask = None   # canal alpha pre-extraído del overlay
        self._overlay_ox   = None  # offset_x cuando se construyó el overlay (None = inválido)
        self._overlay_oy   = None  # offset_y cuando se construyó el overlay
        self._overlay_key  = None   # (sc, ox, oy, id(entities))

        # _gl_prev_scale: detectar cambio de escala en el thread GL
        # para forzar sync readback ese frame y evitar mismatch GL/overlay.
        self._gl_prev_scale = 0.0

        # ── Diagnóstico de cache (activar desde panel Perf) ─────────────
        self._cache_diag:       bool  = False  # True → activa diagnóstico
        self._diag_miss_log:    list  = []     # [(ts, razón)] ring buffer 20 entradas
        self._diag_last_key:    tuple = ()     # último cache_key completo (10 campos)
        self._diag_prev_key:    tuple = ()     # key del frame anterior (para comparar)
        self._diag_miss_count:   int  = 0   # misses desde activación
        self._diag_frame_count:  int  = 0   # frames desde activación
        self._diag_overlay_counts: dict = {} # {'Hatch':N, 'Insert':N, ...} último frame
        self._diag_dim_types:      dict = {} # {'ANG':N, 'H':N, ...} dims en PIL por tipo
        self._diag_overlay_timing: dict = {} # {'Insert':74.2, 'Hatch':11.3, ...} ms por tipo
        self._dim_fail_log:        dict = {} # {tipo:excepción: count} solo cuando _cache_diag=True
        self._ins_fail_log:        dict = {} # {bloque:excepción: count} solo cuando _cache_diag=True

        # ── Log de errores del GL worker (visible en panel Perf → GL ERRORS) ──
        self._gl_error_log:    list = []   # [(ts, tipo, msg, tb)] ring 20
        self._gl_worker_alive: bool = True # False si el thread sale del loop

        # ── Contadores de renders (para Health Monitor) ───────────────────
        self._gl_render_count:  int = 0   # total de llamadas a render()
        self._gl_timeout_count: int = 0   # renders que hicieron timeout 3s

        # ── Métricas de performance (leídas por el panel 📊 Perf) ─────────
        self._perf_stats: dict = {
            "gl_ms":      0.0,
            "frame_ms":   0.0,
            "draw_ms":    0.0,
            "text_ms":    0.0,
            "pbo_ms":     0.0,
            "overlay_ms": 0.0,
            "total_ms":   0.0,
            "vram_kb":    0,
            "cache_hit":  False,
            "n_ents":     0,
        }


        # ── PBO triple-buffer (OP-2) ─────────────────────────────────────
        # pbos[0/1/2] — triple ring para solapar glReadPixels GPU→CPU.
        # write_idx avanza 0→1→2→0. Se lee el PBO de 2 frames atrás,
        # dando al DMA ~1 frame completo para terminar → 0ms stall.
        self._pbos           = [None, None, None]
        self._pbo_W          = 0
        self._pbo_H          = 0
        self._pbo_idx        = 0    # índice de escritura del frame actual
        self._pbo_warmup     = 0    # 0→1→2: init warmup (necesita 2 frames para llenar ring)
        self._pbo_force_sync = False  # True → 1 sync frame tras viewport change

    # ── Interfaz BaseRenderer ─────────────────────────────────────────────────

    def available(self) -> bool:
        """
        True si PyOpenGL está disponible y el sistema reporta soporte GL.
        No crea contexto — solo verifica que las librerías estén presentes.
        """
        if not _OPENGL_OK:
            return False
        if not _PIL_OK:
            return False
        if sys.platform != 'win32':
            # Por ahora solo Windows (WGL). Linux/macOS: Semana 7+
            return False
        try:
            _OPENGL32.wglGetCurrentContext  # acceso básico a la DLL
            return True
        except Exception:
            return False

    def _gl_worker(self) -> None:
        """
        Thread GL dedicado — vive durante toda la vida del renderer.

        NVIDIA WGL no permite usar el mismo contexto GL desde threads distintos
        aunque se libere entre renders. Este thread es el único que toca el
        contexto GL: lo inicializa una vez, lo mantiene current permanentemente
        y procesa todos los renders en orden FIFO.

        Protocolo de cola:
          - Tarea normal: (ctx, result_list)   → result_list[0] = PIL.Image
          - Señal de parada: None
        """
        while True:
            task = self._gl_queue.get()
            if task is None:      # señal de cleanup()
                break

            ctx, result_list, done_ev = task
            img = None
            try:
                img = self._render_gl(ctx)
            except Exception as exc:
                import traceback as _tb
                import time as _tlog
                _ts   = _tlog.strftime("%H:%M:%S")
                _type = type(exc).__name__
                _msg  = str(exc)
                _tb_s = _tb.format_exc()
                print(f"[OpenGL] render error: {exc}")
                print(_tb_s)
                # Ring buffer 20 entradas — siempre visible en panel Perf
                self._gl_error_log.append((_ts, _type, _msg, _tb_s))
                if len(self._gl_error_log) > 20:
                    self._gl_error_log.pop(0)
            finally:
                # GARANTÍA: done_ev.set() siempre se llama aunque _render_gl o
                # _fallback_image lancen. Sin esto el thread muere silenciosamente
                # y todos los renders siguientes hacen timeout indefinidamente.
                if img is None:
                    try:
                        img = self._fallback_image(ctx, "render error").image
                    except Exception:
                        try:
                            W = getattr(ctx, 'W', None) or 800
                            H = getattr(ctx, 'H', None) or 600
                            img = _PILImage.new("RGB", (W, H), (10, 10, 10))
                        except Exception:
                            img = _PILImage.new("RGB", (800, 600), (10, 10, 10))
                result_list.append(img)
                done_ev.set()
        self._gl_worker_alive = False  # el thread salió del loop (cleanup o excepción no capturada)

    def _render_gl(self, ctx: Any) -> Any:
        """Ejecuta el pipeline GL completo (llamado solo desde _gl_worker)."""
        # ── Init de una sola vez ──────────────────────────────────────────
        if not self._ctx_ready and self._ctx_error is None:
            self._init_context()

        if self._ctx_error:
            return self._fallback_image(ctx, self._ctx_error).image

        if not self._ctx_ready:
            return self._fallback_image(ctx, "contexto no inicializado").image

        # Cancel check antes de empezar
        if ctx.cancel_ev and ctx.cancel_ev.is_set():
            self._pbo_warmup = 0
            self._pbo_force_sync = False
            # No resetear _overlay_key: el overlay cacheado sigue válido
            # si el viewport no cambió. Se invalidará solo cuando el key real difiera.
            return self._fallback_image(ctx, "cancelado").image

        # ── FBO ──────────────────────────────────────────────────────────
        self._ensure_fbo(ctx.W, ctx.H)

        # ── Render ────────────────────────────────────────────────────────
        _gl.glBindFramebuffer(_gl.GL_FRAMEBUFFER, self._fbo)
        _gl.glViewport(0, 0, ctx.W, ctx.H)

        if ctx.cancel_ev and ctx.cancel_ev.is_set():
            _gl.glBindFramebuffer(_gl.GL_FRAMEBUFFER, 0)
            return self._fallback_image(ctx, "cancelado").image

        import time as _t_rgl
        _tf0 = _t_rgl.perf_counter()
        self._render_frame(ctx)
        _gl.glFlush()
        _tf1 = _t_rgl.perf_counter()

        # PBO double-buffered async readback (OP-2).
        # Si la escala O la posición cambiaron, forzar sync este frame: la imagen
        # GL del PBO (frame N-1) y el overlay PIL (frame N) deben ser del mismo
        # viewport. Sin sync, un pan/zoom entre frames produce cotas desfasadas.
        _prev_sc = getattr(self, '_gl_prev_scale', ctx.scale)
        _prev_ox = getattr(self, '_gl_prev_ox',    ctx.offset_x)
        _prev_oy = getattr(self, '_gl_prev_oy',    ctx.offset_y)
        self._gl_prev_scale = ctx.scale
        self._gl_prev_ox    = ctx.offset_x
        self._gl_prev_oy    = ctx.offset_y
        viewport_changed = (
            abs(ctx.scale    - _prev_sc) > 0.001 or
            abs(ctx.offset_x - _prev_ox) > 0.5   or
            abs(ctx.offset_y - _prev_oy) > 0.5
        )
        if viewport_changed:
            # El ring PBO tiene 2 frames de lag: read_idx = (write_idx+1)%3 lee datos
            # de hace 2 renders. Con solo 1 sync frame (_pbo_force_sync), el render
            # inmediatamente siguiente sigue leyendo el PBO[stale] (pre-cambio) →
            # snap-back visual cuando _tess_done_cb dispara un 2º redraw al mismo sc.
            # Solución: reset completo del ring (_pbo_warmup=0) → 2 renders síncronos
            # hasta que el ring se rellene con datos del viewport actual.
            self._pbo_warmup = 0   # 2 sync frames: limpia lag del ring triple-PBO

        self._ensure_pbos(ctx.W, ctx.H)
        _tp0 = _t_rgl.perf_counter()
        img = self._read_pixels_pbo(ctx.W, ctx.H)
        _tp1 = _t_rgl.perf_counter()
        self._perf_stats["frame_ms"] = (_tf1 - _tf0) * 1000
        self._perf_stats["pbo_ms"]   = (_tp1 - _tp0) * 1000
        _gl.glBindFramebuffer(_gl.GL_FRAMEBUFFER, 0)
        return img

    def render(self, ctx: Any) -> RenderResult:
        """
        Renderiza la escena completa y retorna RenderResult con PIL.Image.

        Encola el trabajo en el thread GL dedicado y espera el resultado.
        Llamado desde cualquier hilo (engine.py usa un hilo de fondo).
        """
        import time as _t
        self._gl_render_count += 1
        result_list: list = []
        done_ev = threading.Event()
        t0 = _t.perf_counter()
        self._gl_queue.put((ctx, result_list, done_ev))
        _gl_ok = done_ev.wait(timeout=3.0)
        if not _gl_ok:
            self._gl_timeout_count += 1
            print("[OpenGL] WARNING: GL worker timeout — usando último frame cacheado")
            t_gl = (_t.perf_counter() - t0) * 1000
            img = self._last_render_img
            if img is None:
                img = _PILImage.new("RGB", (ctx.W, ctx.H), (10, 10, 10))
            self._perf_stats.update({"gl_ms": t_gl, "overlay_ms": 0,
                                     "total_ms": t_gl, "n_ents": 0})
            return RenderResult(image=img, backend='opengl')

        # Si el GL thread subió un resultado de tessellation durante este render,
        # hacer un segundo pass inmediato para obtener la imagen con la geometría nueva.
        # Esto garantiza que _apply_render() reciba la imagen correcta sin depender
        # del _render_gen ni del poll (fix: entidades dibujadas no aparecen).
        if self._tess_just_uploaded:
            self._tess_just_uploaded = False
            result_list2: list = []
            done_ev2 = threading.Event()
            self._gl_queue.put((ctx, result_list2, done_ev2))
            if not done_ev2.wait(timeout=3.0):
                print("[OpenGL] WARNING: GL worker timeout (2° pass) — usando 1° pass")
            elif result_list2:
                result_list[0] = result_list2[0]

        t_gl = (_t.perf_counter() - t0) * 1000
        img = result_list[0] if result_list else self._last_render_img
        if img is None:
            img = _PILImage.new("RGB", (ctx.W, ctx.H), (10, 10, 10))

        # ── Overlay PIL ───────────────────────────────────────────────────
        # Pan activo (cancelled=True): pegar overlay cacheado desplazado por
        #   el delta de pan en píxeles → cotas siguen a la geometría GL.
        # Zoom / pausa (cancelled=False): reconstruir overlay fresco.
        #   El sync readback forzado en zoom (_render_gl) garantiza que la
        #   imagen GL y el overlay tengan siempre la misma escala.
        t1 = _t.perf_counter()
        cancelled = ctx.cancel_ev is not None and ctx.cancel_ev.is_set()
        # El overlay cacheado solo es válido si pertenece al mismo conjunto de
        # entidades (mismo id). Si el usuario abrió un archivo nuevo, id(entities)
        # cambia → no reusar overlay del archivo anterior.
        _ov_same_file = (
            self._overlay_key is not None and
            len(self._overlay_key) > 3 and
            self._overlay_key[3] == id(ctx.entities)
        )
        if cancelled:
            # Pan activo: NUNCA llamar _render_text_dim_overlay cuando cancel_ev está set.
            # Si lo llamáramos, el loop de dibujado rompería en la primera iteración
            # (cancel_ev.is_set()=True) y guardaría un canvas vacío en _overlay_key,
            # haciendo que los hatches desaparezcan en el siguiente cache hit.
            if self._overlay_img is not None \
                    and self._overlay_ox is not None and _ov_same_file:
                dx = int(round(ctx.offset_x - self._overlay_ox))
                dy = int(round(ctx.offset_y - self._overlay_oy))
                if dx == 0 and dy == 0:
                    img.paste(self._overlay_img, mask=self._overlay_mask)
                else:
                    img.paste(self._overlay_img, (dx, dy), mask=self._overlay_mask)
            # else: sin overlay válido para este frame → GL only.
            # El overlay se construirá en el próximo frame no cancelado.
            t_overlay = (_t.perf_counter() - t1) * 1000
        else:
            img = self._render_text_dim_overlay(img, ctx)
            # _overlay_ox/_oy se actualizan DENTRO de _render_text_dim_overlay
            # solo cuando hay reconstrucción real. No tocar aquí para no
            # desincronizar la posición de referencia cuando usa el cache.
            t_overlay = (_t.perf_counter() - t1) * 1000

        total = t_gl + t_overlay
        n = len(ctx.entities) if ctx.entities else 0
        self._perf_stats.update({
            "gl_ms":      t_gl,
            "overlay_ms": t_overlay,
            "total_ms":   total,
            "n_ents":     n,
        })
        if total > 40:
            print(f"[PERF] GL={t_gl:.0f}ms  overlay={t_overlay:.0f}ms  "
                  f"total={total:.0f}ms  ents={n}  sc={ctx.scale:.1f}")
        if img is not None:
            self._last_render_img = img
        return RenderResult(image=img, backend='opengl')

    def cleanup(self) -> None:
        """Libera recursos GL al cerrar la app."""
        # Enviar señal de parada al thread GL y esperar que termine
        self._gl_queue.put(None)
        self._gl_thread.join(timeout=3.0)

        # Los recursos GL se limpian desde el thread GL antes de salir,
        # pero hacemos un intento adicional desde el thread principal.
        if not self._ctx_ready:
            return
        try:
            # El contexto ya está current en el thread GL (que terminó).
            # Intentar limpiar recursos; si falla (contexto ya liberado) es OK.
            if self._vbo and bool(_gl.glDeleteBuffers):
                _gl.glDeleteBuffers(1, [self._vbo])
            if self._vao and bool(_gl.glDeleteVertexArrays):
                _gl.glDeleteVertexArrays(1, [self._vao])
            if self._vram_bufs and bool(_gl.glDeleteBuffers):
                old_vaos = [v[0] for v in self._vram_bufs.values()]
                old_vbos = [v[1] for v in self._vram_bufs.values()]
                _gl.glDeleteVertexArrays(len(old_vaos),
                                         (ctypes.c_uint * len(old_vaos))(*old_vaos))
                _gl.glDeleteBuffers(len(old_vbos),
                                    (ctypes.c_uint * len(old_vbos))(*old_vbos))
                self._vram_bufs = {}
            if self._prog_basic and bool(_gl.glDeleteProgram):
                _gl.glDeleteProgram(self._prog_basic)
            self._delete_pbos()
            self._delete_fbo()
            _wglMakeCurrent(None, None)
            _wglDeleteContext(self._hglrc)
            _ReleaseDC(self._hwnd, self._hdc)
            _DestroyWindow(self._hwnd)
        except Exception as exc:
            pass   # silenciar errores de cleanup (contexto ya liberado)
        self._ctx_ready = False

    def name(self) -> str:
        return "OpenGL 3.3"

    # ── Inicialización del contexto ───────────────────────────────────────────

    def _init_context(self) -> None:
        """
        Crea un contexto OpenGL 3.3 Core Profile offscreen en Windows.

        Secuencia:
          1. Registrar clase de ventana oculta
          2. Crear HWND + HDC
          3. Crear contexto GL legacy (1.x) para obtener puntero a wglCreateContextAttribsARB
          4. Crear contexto GL 3.3 Core Profile
          5. Compilar shaders
          6. Crear VAO + VBO permanentes para líneas
        """
        try:
            hinstance = _GetModuleHandleW(None)

            _wndproc_cb = WNDPROCTYPE(_def_wndproc)
            self._wndproc_cb = _wndproc_cb   # mantener referencia viva

            cls_name = "CAD_GL_Offscreen"
            wc = _WNDCLASSEX()
            wc.cbSize        = ctypes.sizeof(_WNDCLASSEX)
            wc.style         = CS_OWNDC
            wc.lpfnWndProc   = _wndproc_cb
            wc.hInstance     = hinstance
            wc.lpszClassName = cls_name
            _RegisterClassExW(ctypes.byref(wc))

            hwnd = _CreateWindowExW(
                0, cls_name, "GL", WS_POPUP,
                0, 0, 1, 1, None, None, hinstance, None)
            if not hwnd:
                raise RuntimeError(f"CreateWindowEx falló: {ctypes.GetLastError()}")

            hdc = _GetDC(hwnd)
            if not hdc:
                raise RuntimeError("GetDC falló")

            # ── Pixel format ──────────────────────────────────────────────
            pfd = _PIXELFORMATDESCRIPTOR()
            pfd.nSize      = ctypes.sizeof(_PIXELFORMATDESCRIPTOR)
            pfd.nVersion   = 1
            pfd.dwFlags    = (PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL
                              | PFD_DOUBLEBUFFER)   # ICD NVIDIA requiere doublebuffer
            pfd.iPixelType = PFD_TYPE_RGBA
            pfd.cColorBits = 32
            pfd.cDepthBits = 24
            pfd.cStencilBits = 8
            pfd.iLayerType = PFD_MAIN_PLANE

            fmt = _ChoosePixelFormat(hdc, ctypes.byref(pfd))
            if not fmt:
                raise RuntimeError("ChoosePixelFormat falló")
            if not _SetPixelFormat(hdc, fmt, ctypes.byref(pfd)):
                raise RuntimeError("SetPixelFormat falló")

            # ── Contexto legacy (bootstrap) ────────────────────────────────
            hglrc_legacy = _wglCreateContext(hdc)
            if not hglrc_legacy:
                raise RuntimeError("wglCreateContext (legacy) falló")
            if not _wglMakeCurrent(hdc, hglrc_legacy):
                raise RuntimeError(
                    f"wglMakeCurrent (legacy) falló — "
                    f"error Win32: {ctypes.GetLastError()}")

            # ── Trigger ICD: forzar que el driver cargue sus funciones ───────
            _glGetError_raw()   # trigger ICD initialization

            render_hdc = hdc   # WGL por defecto ya usa GPU discreta en este sistema

            # ── Obtener wglCreateContextAttribsARB ────────────────────────
            ptr = _wglGetProcAddress(b"wglCreateContextAttribsARB")

            if ptr:
                # Crear contexto 3.3 Core Profile
                WGLCCA = ctypes.WINFUNCTYPE(
                    ctypes.c_void_p,
                    ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_int))
                wglCreateContextAttribsARB = WGLCCA(ptr)

                attribs = (ctypes.c_int * 7)(
                    WGL_CONTEXT_MAJOR_VERSION_ARB, 3,
                    WGL_CONTEXT_MINOR_VERSION_ARB, 3,
                    WGL_CONTEXT_PROFILE_MASK_ARB,
                    WGL_CONTEXT_CORE_PROFILE_BIT_ARB,
                    0)
                hglrc = wglCreateContextAttribsARB(render_hdc, None, attribs)
                if not hglrc:
                    raise RuntimeError(
                        "wglCreateContextAttribsARB retornó NULL — "
                        "GPU no soporta OpenGL 3.3 Core Profile")
                # Cambiar al contexto 3.3 y eliminar el legacy
                _wglMakeCurrent(None, None)
                _wglDeleteContext(hglrc_legacy)
                hglrc_legacy = None
                if not _wglMakeCurrent(render_hdc, hglrc):
                    raise RuntimeError("wglMakeCurrent (3.3) falló")
            else:
                # Sin wglCreateContextAttribsARB el contexto legacy (GL 1.x)
                # no soporta GLSL 3.30 — no podemos usar shaders, abortar.
                _wglMakeCurrent(None, None)
                _wglDeleteContext(hglrc_legacy)
                hglrc_legacy = None
                raise RuntimeError(
                    "wglCreateContextAttribsARB no disponible — "
                    "driver OpenGL antiguo o contexto GL no inicializado. "
                    "Asegurese de que el driver de GPU soporte OpenGL 3.3+")

            self._hwnd  = hwnd
            self._hdc   = hdc
            self._hglrc = hglrc

            # ── Verificar versión GL y GPU activo ─────────────────────────
            ver_raw      = _glGetString_raw(0x1F02)   # GL_VERSION
            renderer_raw = _glGetString_raw(0x1F01)   # GL_RENDERER
            vendor_raw   = _glGetString_raw(0x1F00)   # GL_VENDOR
            ver      = ver_raw.decode()      if ver_raw      else 'unknown'
            gpu_name = renderer_raw.decode() if renderer_raw else 'unknown'
            vendor   = vendor_raw.decode()   if vendor_raw   else 'unknown'
            self._gpu_name = gpu_name
            print(f"[OpenGL] contexto GL: {ver}")
            print(f"[OpenGL] GPU activo : {gpu_name} ({vendor})")

            # ── Detectar iGPU y dar instrucciones específicas ─────────────
            if _is_integrated_gpu(gpu_name):
                print("[OpenGL] ⚠  GPU INTEGRADA activa — rendimiento reducido.")
                print("[OpenGL]    WGL_NV_gpu_affinity no disponible o no resolvió el problema.")
                _ensure_discrete_gpu_preference()   # registro D3D (parcial)
                _print_gpu_fix_instructions(vendor)  # instrucciones por fabricante
            else:
                _ensure_discrete_gpu_preference()   # guardar preferencia para consistencia

            # Limpiar errores GL pre-existentes antes de usar PyOpenGL
            while _glGetError_raw():
                pass

            # ── Shaders ──────────────────────────────────────────────────
            self._build_shaders()

            # ── VAO + VBO permanentes para geometría dinámica ─────────────
            self._vao = _gl.glGenVertexArrays(1)
            self._vbo = _gl.glGenBuffers(1)

            _gl.glBindVertexArray(self._vao)
            _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, self._vbo)

            # Atributo 0: posición (vec2)
            _gl.glEnableVertexAttribArray(0)
            _gl.glVertexAttribPointer(0, 2, _gl.GL_FLOAT, False,
                                      5 * 4, ctypes.c_void_p(0))
            # Atributo 1: color (vec3)
            _gl.glEnableVertexAttribArray(1)
            _gl.glVertexAttribPointer(1, 3, _gl.GL_FLOAT, False,
                                      5 * 4, ctypes.c_void_p(2 * 4))

            _gl.glBindVertexArray(0)

            self._ctx_ready = True

        except Exception as exc:
            self._ctx_error = str(exc)
            print(f"[OpenGL] _init_context error: {exc}")

    # ── Shaders ───────────────────────────────────────────────────────────────

    def _build_shaders(self) -> None:
        """Compila y linka todos los shaders GL."""
        try:
            vert = _glsl.compileShader(_VERT_BASIC, _gl.GL_VERTEX_SHADER)
            frag = _glsl.compileShader(_FRAG_BASIC, _gl.GL_FRAGMENT_SHADER)
            self._prog_basic = _glsl.compileProgram(vert, frag)
        except Exception as exc:
            raise RuntimeError(f"Shader basic failed: {exc}")

        # Shader de texto (font atlas)
        try:
            vt = _glsl.compileShader(_VERT_TEXT, _gl.GL_VERTEX_SHADER)
            ft = _glsl.compileShader(_FRAG_TEXT, _gl.GL_FRAGMENT_SHADER)
            self._prog_text = _glsl.compileProgram(vt, ft)
            # VAO/VBO para texto (stride=7: x,y,u,v,r,g,b)
            self._vao_text = _gl.glGenVertexArrays(1)
            self._vbo_text = _gl.glGenBuffers(1)
            _gl.glBindVertexArray(self._vao_text)
            _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, self._vbo_text)
            stride = 7 * 4   # 7 floats × 4 bytes
            _gl.glVertexAttribPointer(0, 2, _gl.GL_FLOAT, False, stride,
                                      ctypes.c_void_p(0))
            _gl.glEnableVertexAttribArray(0)
            _gl.glVertexAttribPointer(1, 2, _gl.GL_FLOAT, False, stride,
                                      ctypes.c_void_p(2 * 4))
            _gl.glEnableVertexAttribArray(1)
            _gl.glVertexAttribPointer(2, 3, _gl.GL_FLOAT, False, stride,
                                      ctypes.c_void_p(4 * 4))
            _gl.glEnableVertexAttribArray(2)
            _gl.glBindVertexArray(0)
        except Exception as exc:
            print(f"[OpenGL] text shader failed (text usara PIL): {exc}")
            self._prog_text = None

        # Shader de fill (hatch SOLID)
        try:
            vf = _glsl.compileShader(_VERT_FILL, _gl.GL_VERTEX_SHADER)
            ff = _glsl.compileShader(_FRAG_FILL, _gl.GL_FRAGMENT_SHADER)
            self._prog_fill = _glsl.compileProgram(vf, ff)
            self._vao_fill  = _gl.glGenVertexArrays(1)
            self._vbo_fill  = _gl.glGenBuffers(1)
            _gl.glBindVertexArray(self._vao_fill)
            _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, self._vbo_fill)
            stride_f = 5 * 4   # x,y,r,g,b
            _gl.glVertexAttribPointer(0, 2, _gl.GL_FLOAT, False, stride_f,
                                      ctypes.c_void_p(0))
            _gl.glEnableVertexAttribArray(0)
            _gl.glVertexAttribPointer(1, 3, _gl.GL_FLOAT, False, stride_f,
                                      ctypes.c_void_p(2 * 4))
            _gl.glEnableVertexAttribArray(1)
            _gl.glBindVertexArray(0)
        except Exception as exc:
            print(f"[OpenGL] fill shader failed (hatch usara PIL): {exc}")
            self._prog_fill = None

        # ── Cachear uniform locations (evita glGetUniformLocation cada frame) ──
        self._uloc_basic_proj = (
            _gl.glGetUniformLocation(self._prog_basic, "uProjection")
            if self._prog_basic else -1)
        self._uloc_text_proj  = (
            _gl.glGetUniformLocation(self._prog_text, "uProjection")
            if self._prog_text else -1)
        self._uloc_text_atlas = (
            _gl.glGetUniformLocation(self._prog_text, "uAtlas")
            if self._prog_text else -1)
        self._uloc_fill_proj  = (
            _gl.glGetUniformLocation(self._prog_fill, "uProjection")
            if self._prog_fill else -1)

    # ── FBO ───────────────────────────────────────────────────────────────────

    def _ensure_fbo(self, W: int, H: int) -> None:
        """Crea o redimensiona el FBO para el tamaño de canvas dado."""
        if self._fbo is not None and self._fbo_W == W and self._fbo_H == H:
            return
        self._delete_fbo()

        # Color renderbuffer
        fbo = _gl.glGenFramebuffers(1)
        _gl.glBindFramebuffer(_gl.GL_FRAMEBUFFER, fbo)

        rbo_color = _gl.glGenRenderbuffers(1)
        _gl.glBindRenderbuffer(_gl.GL_RENDERBUFFER, rbo_color)
        _gl.glRenderbufferStorage(_gl.GL_RENDERBUFFER, _gl.GL_RGB8, W, H)
        _gl.glFramebufferRenderbuffer(
            _gl.GL_FRAMEBUFFER, _gl.GL_COLOR_ATTACHMENT0,
            _gl.GL_RENDERBUFFER, rbo_color)

        rbo_depth = _gl.glGenRenderbuffers(1)
        _gl.glBindRenderbuffer(_gl.GL_RENDERBUFFER, rbo_depth)
        _gl.glRenderbufferStorage(_gl.GL_RENDERBUFFER,
                                   _gl.GL_DEPTH_COMPONENT24, W, H)
        _gl.glFramebufferRenderbuffer(
            _gl.GL_FRAMEBUFFER, _gl.GL_DEPTH_ATTACHMENT,
            _gl.GL_RENDERBUFFER, rbo_depth)

        status = _gl.glCheckFramebufferStatus(_gl.GL_FRAMEBUFFER)
        if status != _gl.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"FBO incompleto: 0x{status:X}")

        _gl.glBindFramebuffer(_gl.GL_FRAMEBUFFER, 0)

        self._fbo       = fbo
        self._rbo_color = rbo_color
        self._rbo_depth = rbo_depth
        self._fbo_W     = W
        self._fbo_H     = H

    def _delete_fbo(self) -> None:
        if self._fbo is not None:
            _gl.glDeleteFramebuffers(1, [self._fbo])
            _gl.glDeleteRenderbuffers(1, [self._rbo_color])
            _gl.glDeleteRenderbuffers(1, [self._rbo_depth])
            self._fbo = self._rbo_color = self._rbo_depth = None
            self._fbo_W = self._fbo_H = 0

    # ── Render frame ─────────────────────────────────────────────────────────

    def _render_frame(self, ctx: Any) -> None:
        """Renderiza la escena completa en el FBO actual."""
        # ── Antialiasing de líneas ─────────────────────────────────────────
        # GL_LINE_SMOOTH suaviza líneas diagonales y curvas sin necesitar MSAA.
        # Deprecated en Core Profile pero soportado por la mayoría de drivers.
        _gl.glEnable(_gl.GL_LINE_SMOOTH)
        _gl.glHint(_gl.GL_LINE_SMOOTH_HINT, _gl.GL_NICEST)
        _gl.glEnable(_gl.GL_BLEND)
        _gl.glBlendFunc(_gl.GL_SRC_ALPHA, _gl.GL_ONE_MINUS_SRC_ALPHA)

        # ── Clear ─────────────────────────────────────────────────────────
        bg = _h2rgbf(ctx.bg_color)
        _gl.glClearColor(bg[0], bg[1], bg[2], 1.0)
        _gl.glClear(_gl.GL_COLOR_BUFFER_BIT | _gl.GL_DEPTH_BUFFER_BIT)

        # ── Proyección ────────────────────────────────────────────────────
        proj = _proj_matrix(ctx)

        # ── Usar el programa básico ───────────────────────────────────────
        _gl.glUseProgram(self._prog_basic)
        _gl.glUniformMatrix4fv(self._uloc_basic_proj, 1, False,
                               (ctypes.c_float * 16)(*proj))

        # ── Grid ──────────────────────────────────────────────────────────
        if ctx.grid_on and ctx.scale >= 10:
            self._render_grid(ctx, proj)

        # ── Ejes ──────────────────────────────────────────────────────────
        self._render_axes(ctx, proj)

        # Hatch SOLID: PIL overlay maneja todos correctamente (convexos,
        # cóncavos, extrusion invertida). _render_hatches_gl reservado
        # para cuando se implemente earcut triangulation robusta.

        # ── Entidades (geometría: líneas, círculos, arcos…) ──────────────
        if ctx.cancel_ev and ctx.cancel_ev.is_set():
            return
        self._render_entities(ctx, proj)

        # ── Texto en GPU (font atlas) ─────────────────────────────────────
        if not (ctx.cancel_ev and ctx.cancel_ev.is_set()):
            try:
                import time as _t_txt
                _tt0 = _t_txt.perf_counter()
                self._render_text_gl(ctx, proj)
                self._perf_stats["text_ms"] = (_t_txt.perf_counter() - _tt0) * 1000
            except Exception as _exc:
                print(f"[OpenGL] text render error: {_exc}")

        # ── Restaurar estado (no afectar overlay PIL) ──────────────────────
        _gl.glDisable(_gl.GL_LINE_SMOOTH)
        _gl.glDisable(_gl.GL_BLEND)

    # ── Grid ─────────────────────────────────────────────────────────────────

    def _render_grid(self, ctx: Any, proj: list) -> None:
        """Dibuja la cuadrícula con GL_LINES.
        Lógica de 3 niveles de zoom idéntica a PIL _draw_grid."""
        sc   = ctx.scale
        ox, oy = ctx.offset_x, ctx.offset_y
        W,  H  = ctx.W, ctx.H

        # Usar grid_major/minor del ctx (configurables), con fallback a constantes PIL
        gmin = getattr(ctx, "grid_minor", None)
        gmaj = getattr(ctx, "grid_major", None)
        if gmin is None or gmaj is None:
            from cad.renderer_pil import GRID_MAJOR, GRID_MINOR
            gmin = GRID_MINOR; gmaj = GRID_MAJOR

        # 3 niveles de zoom — igual que PIL
        if sc >= 160:
            step_f, step_m = gmin,      gmaj
        elif sc >= 40:
            step_f, step_m = gmaj,      gmaj * 5
        elif sc >= 10:
            step_f, step_m = gmaj * 5,  gmaj * 10
        else:
            return

        vx0, vy0, vx1, vy1 = _vp.viewport_world(sc, ox, oy, W, H)
        verts = []
        gc  = _h2rgbf3(ctx.grid_color)
        gcm = _h2rgbf3(ctx.grid_maj_color)

        for step, c_minor, c_major in [(step_f, gc, gc), (step_m, gcm, gcm)]:
            # Verticales
            x = math.floor(vx0 / step) * step
            while x <= vx1 + step:
                c = c_major if abs(round(x / gmaj) * gmaj - x) < gmaj * 1e-4 else c_minor
                verts += [x, vy0, c[0], c[1], c[2],
                          x, vy1, c[0], c[1], c[2]]
                x += step
            # Horizontales
            y = math.floor(vy0 / step) * step
            while y <= vy1 + step:
                c = c_major if abs(round(y / gmaj) * gmaj - y) < gmaj * 1e-4 else c_minor
                verts += [vx0, y, c[0], c[1], c[2],
                          vx1, y, c[0], c[1], c[2]]
                y += step

        if verts:
            self._draw_lines_raw(verts)

    # ── Ejes ─────────────────────────────────────────────────────────────────

    def _render_axes(self, ctx: Any, proj: list) -> None:
        """Dibuja el eje X e Y en el origen mundo."""
        sc = ctx.scale
        ox, oy = ctx.offset_x, ctx.offset_y
        W,  H  = ctx.W, ctx.H
        vx0, vy0, vx1, vy1 = _vp.viewport_world(sc, ox, oy, W, H)
        ac = _h2rgbf3(ctx.axis_color)
        verts = [
            vx0, 0.0, ac[0], ac[1], ac[2],
            vx1, 0.0, ac[0], ac[1], ac[2],
            0.0, vy0, ac[0], ac[1], ac[2],
            0.0, vy1, ac[0], ac[1], ac[2],
        ]
        self._draw_lines_raw(verts)

    # ── Entidades ────────────────────────────────────────────────────────────

    def _render_entities(self, ctx: Any, proj: list) -> None:
        """
        Renderiza todas las entidades del viewport.

        OP-4: VBO cache por (id(entities), lod_level).
        - Entidades world-only (Line/Circle/Arc/Polyline/Spline/Ellipse)
          se cachean en numpy float32 y se reusan en pan puro.
        - XLines siempre se reconstruyen (dependen del viewport para clipear).
        """
        from cad.entities import (Line, Circle, Arc, Polyline, Spline,
                                   Ellipse, XLine, Leader)
        from cad.renderer_pil import (
            build_layer_cache, resolve_entity_props, _query_viewport
        )
        from collections import defaultdict

        sc = ctx.scale
        ox, oy = ctx.offset_x, ctx.offset_y
        W,  H  = ctx.W, ctx.H

        # Culling: solo entidades en el viewport
        vx0, vy0, vx1, vy1 = _vp.viewport_world(sc, ox, oy, W, H)
        candidates = _query_viewport(
            ctx.entity_index, ctx.entity_cell, vx0, vy0, vx1, vy1)
        if not candidates:
            candidates = ctx.entities

        # ── Viewport extendido para tessellation ─────────────────────────
        # Solo activo cuando el viewport NORMAL ya hace culling >15% del total.
        # En zoom-out (todo visible) no hay ventaja → usa comportamiento anterior.
        # Evita llamada extra a _query_viewport si no es necesaria.
        _n_total    = len(ctx.entities) if ctx.entities else 1
        _tess_vp_mode = False
        _ext_cands    = None
        _cands_culled = (candidates is not ctx.entities and
                         len(candidates) < int(_n_total * 0.85))
        if _cands_culled and ctx.entity_index:
            _vw = vx1 - vx0; _vh = vy1 - vy0
            _flat_all = ctx.entity_index.get("__flat__")
            _ext_q = _query_viewport(
                ctx.entity_index, ctx.entity_cell,
                vx0 - 1.5*_vw, vy0 - 1.5*_vh, vx1 + 1.5*_vw, vy1 + 1.5*_vh)
            if (_ext_q and _ext_q is not _flat_all and
                    len(_ext_q) < int(_n_total * 0.85)):
                _ext_cands    = _ext_q
                _tess_vp_mode = True

        _ltscale  = float(ctx.config.get("ltscale", 1.0))
        lyr_cache = build_layer_cache(ctx.layers, sc, ltscale=_ltscale)

        # ── Cache key ────────────────────────────────────────────────────
        lod = int(math.log2(max(sc, 0.001)))
        _lts_key  = round(float(ctx.config.get("ltscale", 1.0)) * 1000)
        _sel_ver  = getattr(ctx, 'sel_version', 0)
        # int(_tess_vp_mode) invalida el cache al cambiar entre viewport y full-scan.
        cache_key = (id(ctx.entities), len(ctx.entities) if ctx.entities else 0,
                     lod, _lts_key, _sel_ver, int(_tess_vp_mode))

        # ── Detección de adición incremental ────────────────────────────
        # Solo si id(entities) es el mismo (lista mutada in-place), len creció y
        # todos los demás campos son idénticos (no hubo zoom ni cambio de ltscale).
        _prev_key = self._vbo_cache_key
        _is_incremental = (
            _prev_key is not None and
            _prev_key[0] == cache_key[0] and   # mismo id(entities)
            cache_key[1] > _prev_key[1] and    # len creció → solo adiciones al final
            _prev_key[2:] == cache_key[2:]      # lod/ltscale/sel_ver/vp_mode sin cambio
        )
        _incr_start = _prev_key[1] if _is_incremental else 0

        # ── Viewport bounds check (solo en viewport mode) ─────────────────
        # VRAM es válido si el viewport actual está dentro de los bounds del
        # área tessellada. En full-scan siempre es válido.
        _vp_valid = True
        if _tess_vp_mode:
            if self._tess_vp_bounds is not None:
                _bx0, _by0, _bx1, _by1 = self._tess_vp_bounds
                _vp_valid = (vx0 >= _bx0 - 1e-6 and vy0 >= _by0 - 1e-6 and
                             vx1 <= _bx1 + 1e-6 and vy1 <= _by1 + 1e-6)
            else:
                _vp_valid = False   # sin bounds todavía → necesita tessellation

        cache_hit = (cache_key == self._vbo_cache_key) and _vp_valid

        # ── Diagnóstico de cache miss ────────────────────────────────────
        # ── Diagnóstico de cache — actualiza ring buffer + consola ──────────
        _diag_on = getattr(self, '_cache_diag', False)
        if _diag_on:
            # Rastrear todos los frames para hit rate
            self._diag_frame_count += 1
            self._diag_prev_key = self._diag_last_key
            self._diag_last_key = cache_key

        if not cache_hit and self._vbo_cache_key is not None and _diag_on:
            prev = self._vbo_cache_key
            _LABELS = ("id(entities)", "len(entities)", "lod", "ltscale_key",
                       "sel_version", "vp_mode", "vx0", "vy0", "vx1", "vy1")
            n = max(len(prev), len(cache_key))
            full_labels = list(_LABELS) + [f"field_{i}" for i in range(len(_LABELS), n)]
            reasons = [full_labels[i] for i in range(min(len(prev), len(cache_key)))
                       if prev[i] != cache_key[i]]
            if not _vp_valid and not reasons:
                reasons = ["viewport_out_of_bounds"]
            reason_str = ' | '.join(reasons) or 'unknown'
            # Ring buffer para el panel UI (máx 20 entradas)
            import time as _t_diag
            self._diag_miss_log.append((_t_diag.strftime("%H:%M:%S"), reason_str))
            if len(self._diag_miss_log) > 20:
                self._diag_miss_log.pop(0)
            self._diag_miss_count += 1
            print(f"[CACHE MISS] {reason_str}  prev={prev}  new={cache_key}")

        # ── Batches cacheables (coordenadas mundo) ────────────────────────
        # ─── 0. Recoger resultado del hilo de tessellation si terminó ────
        import queue as _q_mod
        try:
            _bg = self._tess_result_q.get_nowait()
            if _bg.get('cache_key') == cache_key:
                self._upload_tess_result(_bg)
                self._pbo_warmup = 0    # nueva geometría: forzar sync para mostrarla de inmediato
                self._tess_just_uploaded = True   # señal: render() debe hacer 2º pass
            # Si la clave no coincide (viewport cambió mientras tessellaba)
            # descartamos el resultado; el próximo frame lanzará otro hilo.
            self._tess_pending = False
            cache_hit = (cache_key == self._vbo_cache_key)
        except _q_mod.Empty:
            pass

        # ─── 1. Fast path: VRAM válida → solo bind+draw, 0 upload ────────
        # IMPORTANTE: cache_hit se evalúa separado de _vram_bufs para que
        # escenas vacías (entities=[]) también entren aquí y no relancen
        # el tessellator indefinidamente (loop infinito con ents=0).
        if cache_hit and self._vram_bufs_key == cache_key:
            self._perf_stats["cache_hit"] = True
            _layers_dict = ctx.layers or {}
            # ¿Todas las capas visibles? → 1 draw call por lw (rendimiento máximo).
            # Si alguna está apagada → draw calls por rango de capa dentro del VBO.
            _any_hidden = any(
                not getattr(lyr, 'visible', True) or getattr(lyr, 'frozen', False)
                for lyr in _layers_dict.values()
            )
            import time as _t_draw
            _td0 = _t_draw.perf_counter()
            for lw_px, (vao_h, vbo_h, n_verts) in self._vram_bufs.items():
                _gl.glLineWidth(float(lw_px))
                if not _any_hidden or lw_px not in self._vram_layer_ranges:
                    # Fast path: todas visibles → 1 draw call, mismo costo que antes
                    self._draw_vbo(vao_h, n_verts)
                else:
                    # Slow path (solo cuando hay capas apagadas): draw por rango
                    _gl.glBindVertexArray(vao_h)
                    for layer_name, (start, count) in self._vram_layer_ranges[lw_px].items():
                        _lyr = _layers_dict.get(layer_name)
                        if _lyr and (not getattr(_lyr, 'visible', True) or
                                     getattr(_lyr, 'frozen', False)):
                            continue
                        _gl.glDrawArrays(_gl.GL_LINES, start, count)
                    _gl.glBindVertexArray(0)
            _gl.glLineWidth(1.0)
            if self._vram_tri_buf is not None:
                tri_vao, tri_vbo, tri_n = self._vram_tri_buf
                _gl.glBindVertexArray(tri_vao)
                if not _any_hidden or not self._vram_tri_layer_ranges:
                    _gl.glDrawArrays(_gl.GL_TRIANGLES, 0, tri_n)
                else:
                    for _tl_name, (_ts, _tc) in self._vram_tri_layer_ranges.items():
                        _tl_lyr = _layers_dict.get(_tl_name)
                        if _tl_lyr and (not getattr(_tl_lyr, 'visible', True) or
                                        getattr(_tl_lyr, 'frozen', False)):
                            continue
                        _gl.glDrawArrays(_gl.GL_TRIANGLES, _ts, _tc)
                _gl.glBindVertexArray(0)
            # XLines: viewport-dependent → redibujar con el viewport actual en cada frame
            self._draw_xlines(ctx)
            self._perf_stats["draw_ms"] = (_t_draw.perf_counter() - _td0) * 1000
            return

        # ─── 2. Cache miss: lanzar tessellation en hilo de fondo ─────────
        self._perf_stats["cache_hit"] = False

        # Pan debounce: si el miss es ÚNICAMENTE por viewport bounds (_vp_valid=False)
        # y la cache_key geométrica no cambió, Y el engine reporta que el viewport
        # cambió hace <300ms (is_panning=True) → skip tessellation, dibujar con
        # VRAM viejo (continuidad visual sin cost de CPU).
        # Misses por otros motivos (zoom LOD, sel_version, entidad nueva) → lanzar siempre.
        _only_vp_miss = (cache_key == self._vbo_cache_key) and not _vp_valid
        _skip_tess    = _only_vp_miss and getattr(ctx, 'is_panning', False)

        if not self._tess_pending and not _skip_tess:
            # Snapshot de entidades para thread safety.
            # Incremental: solo las nuevas (desde _incr_start).
            # Viewport mode: solo entidades del viewport extendido (3× área).
            # Full-scan: lista completa (zoom muy alejado o sin spatial index).
            if _is_incremental:
                _ents_snap = list(ctx.entities)[_incr_start:]
            else:
                _ents_snap = _ext_cands if _tess_vp_mode else list(ctx.entities)
            # Bounds del área tessellada (world coords) — para verificar en frames futuros
            _tess_bounds = (vx0 - 1.5*_vw, vy0 - 1.5*_vh, vx1 + 1.5*_vw, vy1 + 1.5*_vh) if _tess_vp_mode else None
            _cfg_snap   = dict(ctx.config)
            _sel_color  = ctx.select_color
            _cancel_ev  = ctx.cancel_ev

            import threading as _thr_mod
            _thr_mod.Thread(
                target=self._tessellate_and_enqueue,
                args=(_ents_snap, _cfg_snap, cache_key, sc,
                      lyr_cache, _ltscale, _sel_color, _cancel_ev, ctx,
                      _tess_bounds, _is_incremental),
                daemon=True
            ).start()
            self._tess_pending = True

        # Dibujar VRAM anterior mientras tessellation corre (continuidad visual).
        # _vram_bufs_key=None indica reset explícito (ej. _new_dwg) — no mostrar stale.
        if self._vram_bufs and self._vram_bufs_key is not None:
            _layers_dict2 = ctx.layers or {}
            _any_hidden2  = any(
                not getattr(lyr, 'visible', True) or getattr(lyr, 'frozen', False)
                for lyr in _layers_dict2.values()
            )
            for lw_px, (vao_h, vbo_h, n_verts) in self._vram_bufs.items():
                _gl.glLineWidth(float(lw_px))
                if not _any_hidden2 or lw_px not in self._vram_layer_ranges:
                    self._draw_vbo(vao_h, n_verts)
                else:
                    _gl.glBindVertexArray(vao_h)
                    for layer_name, (start, count) in self._vram_layer_ranges[lw_px].items():
                        _lyr2 = _layers_dict2.get(layer_name)
                        if _lyr2 and (not getattr(_lyr2, 'visible', True) or
                                      getattr(_lyr2, 'frozen', False)):
                            continue
                        _gl.glDrawArrays(_gl.GL_LINES, start, count)
                    _gl.glBindVertexArray(0)
            _gl.glLineWidth(1.0)
            if self._vram_tri_buf is not None:
                tri_vao, tri_vbo, tri_n = self._vram_tri_buf
                _gl.glBindVertexArray(tri_vao)
                if not _any_hidden2 or not self._vram_tri_layer_ranges:
                    _gl.glDrawArrays(_gl.GL_TRIANGLES, 0, tri_n)
                else:
                    for _tl_name2, (_ts2, _tc2) in self._vram_tri_layer_ranges.items():
                        _tl_lyr2 = _layers_dict2.get(_tl_name2)
                        if _tl_lyr2 and (not getattr(_tl_lyr2, 'visible', True) or
                                         getattr(_tl_lyr2, 'frozen', False)):
                            continue
                        _gl.glDrawArrays(_gl.GL_TRIANGLES, _ts2, _tc2)
                _gl.glBindVertexArray(0)
        # XLines: redibujar con viewport actual (siempre, incluso durante tessellation)
        self._draw_xlines(ctx)

    def _draw_xlines(self, ctx: Any) -> None:
        """Dibuja las XLines con el viewport ACTUAL — siempre fresco, nunca stale.
        Llamado en cada frame (fast path y miss path) porque las XLines son
        viewport-dependent: sus endpoints se calculan del viewport para clipear."""
        if not self._xline_ents:
            return
        for e, col, lw_px in self._xline_ents:
            verts = _tess_xline(e, col, ctx)
            if verts:
                _gl.glLineWidth(float(lw_px))
                self._draw_lines_np(_np.array(verts, dtype=_np.float32))
        _gl.glLineWidth(1.0)

    # ── Background tessellation (CPU-only, hilo de fondo) ────────────────────

    def _tessellate_and_enqueue(self, ents_snap, cfg_snap, cache_key, sc,
                                lyr_cache, ltscale, select_color, cancel_ev, ctx,
                                tess_bounds=None, incremental=False):
        """Corre en hilo de fondo: tessellate CPU-only y encola el resultado.
        NO hace llamadas GL. El GL thread consume el resultado en el próximo frame.
        tess_bounds: (vx0e, vy0e, vx1e, vy1e) del área tessellada en viewport mode,
                     None si es full-scan. Devuelto en el resultado para actualizar
                     self._tess_vp_bounds en el GL thread.
        """
        from collections import defaultdict as _dd
        from cad.entities import (Line, Circle, Arc, Polyline, Ellipse,
                                   Spline, XLine, Dimension as _Dim, Hatch as _Hatch)
        from cad.renderer_pil import resolve_entity_props

        typed: dict = _dd(list)
        for e in ents_snap:
            typed[type(e)].append(e)

        line_rows:  dict = _dd(list)
        curve_flat: dict = _dd(list)
        curve_arrs: dict = _dd(list)
        xline_flat: dict = _dd(list)
        tri_rows:   dict = _dd(list)   # layer_name → [x,y,r,g,b, ...] GL_TRIANGLES
        new_dim_gl_ids:    set = set()
        new_hatch_gl_ids:  set = set()
        new_insert_gl_ids: set = set()
        new_leader_gl_ids: set = set()
        cancelled = False

        def _resolve(e):
            lp = lyr_cache.get(e.layer, (True, False, "#FFFFFF", 1, ()))
            ch, lw, dash, skip = resolve_entity_props(
                e, lp, select_color, scale=sc, ltscale=ltscale)
            if skip:
                return None, 0, ()
            return _h2rgbf3(ch), max(1, int(lw)), dash

        def _segs_from_pts(xs, ys, col, n_seg):
            r, g, b = col
            seg = _np.empty(n_seg * 10, dtype=_np.float32)
            seg[0::10] = xs[:-1]; seg[1::10] = ys[:-1]
            seg[2::10] = r;       seg[3::10] = g;       seg[4::10] = b
            seg[5::10] = xs[1:];  seg[6::10] = ys[1:]
            seg[7::10] = r;       seg[8::10] = g;       seg[9::10] = b
            return seg

        def _chk():
            return cancel_ev is not None and cancel_ev.is_set()

        _n_total = len(ents_snap)
        self._tess_progress = f"⟳ Geometría — {_n_total:,} entidades"

        # ── Lines (batch numpy por capa — fast path BYLAYER) ─────────────
        # 95%+ de líneas en planos arquitectónicos son BYLAYER sin overrides.
        # Agrupar por capa permite procesar cada grupo con una sola operación
        # numpy en lugar de N iteraciones Python + N llamadas _resolve.
        _all_lines = typed.get(Line, [])
        if _all_lines and not _chk():
            _lines_by_layer: dict = {}
            _lines_ovr:      list = []
            for _e in _all_lines:
                _ec  = _e.color
                _elw = getattr(_e, 'linewidth', 0) or 0
                _elt = (getattr(_e, 'linetype', 'bylayer') or 'bylayer').upper()
                if ((_ec and _ec.lower() not in ('bylayer', '')) or
                        _elw > 0 or _elt not in ('BYLAYER', '') or _e.selected):
                    _lines_ovr.append(_e)
                else:
                    _lay = _e.layer
                    if _lay not in _lines_by_layer:
                        _lines_by_layer[_lay] = []
                    _lines_by_layer[_lay].append(_e)

            # Fast path: cada capa = una operación numpy
            # Nota: se tessella AUNQUE la capa esté apagada — el skip de visibilidad
            # ocurre en draw time (_render_entities fast path) sin re-tessellation.
            for _lay, _elst in _lines_by_layer.items():
                if _chk(): cancelled = True; break
                _lp = lyr_cache.get(_lay, (True, False, "#FFFFFF", 1, ()))
                _vis, _locked, _col_hex, _lw_l, _dash = _lp
                # No saltear capas apagadas — la geometría permanece en VRAM y el
                # draw call se omite en tiempo de dibujado según lyr.visible.
                _col_hex = "#555555" if _locked else _col_hex
                _lw = max(1, int(_lw_l))
                _rgb = _h2rgbf3(_col_hex)
                if _dash:
                    for _e in _elst:
                        if _entity_px(_e, sc) < _LOD_MIN_PX: continue
                        curve_flat[(_lay, _lw)] += _apply_dash_to_path(
                            [(_e.x1, _e.y1), (_e.x2, _e.y2)], _rgb, _dash, sc)
                else:
                    _x1 = _np.array([_e.x1 for _e in _elst], dtype=_np.float32)
                    _y1 = _np.array([_e.y1 for _e in _elst], dtype=_np.float32)
                    _x2 = _np.array([_e.x2 for _e in _elst], dtype=_np.float32)
                    _y2 = _np.array([_e.y2 for _e in _elst], dtype=_np.float32)
                    _mask = (_np.maximum(_np.abs(_x2 - _x1),
                                          _np.abs(_y2 - _y1)) * sc) >= _LOD_MIN_PX
                    _x1 = _x1[_mask]; _y1 = _y1[_mask]
                    _x2 = _x2[_mask]; _y2 = _y2[_mask]
                    _n = int(_mask.sum())
                    if _n == 0: continue
                    _r, _g, _b = _rgb
                    _arr = _np.empty(_n * 10, dtype=_np.float32)
                    _arr[0::10] = _x1; _arr[1::10] = _y1
                    _arr[2::10] = _r;  _arr[3::10] = _g;  _arr[4::10] = _b
                    _arr[5::10] = _x2; _arr[6::10] = _y2
                    _arr[7::10] = _r;  _arr[8::10] = _g;  _arr[9::10] = _b
                    curve_arrs[(_lay, _lw)].append(_arr)

            # Fallback: líneas con overrides de color/LW/linetype o seleccionadas
            for e in _lines_ovr:
                if cancelled or _chk(): cancelled = True; break
                col, lw, dash = _resolve(e)
                if col is None: continue
                if _entity_px(e, sc) < _LOD_MIN_PX: continue
                if dash:
                    curve_flat[(e.layer, lw)] += _apply_dash_to_path(
                        [(e.x1, e.y1), (e.x2, e.y2)], col, dash, sc)
                else:
                    line_rows[(e.layer, lw)].append((e.x1, e.y1, col[0], col[1], col[2],
                                                     e.x2, e.y2, col[0], col[1], col[2]))

        self._tess_progress = f"⟳ Curvas — {_n_total:,} entidades"
        # ── Circles ──────────────────────────────────────────────────────
        _all_circles = typed.get(Circle, [])
        if _all_circles:
            _rc = _np.array([e.radius for e in _all_circles], dtype=_np.float32)
            typed[Circle] = [e for e, ok in zip(_all_circles,
                                                  _rc * 2.0 * sc >= _LOD_MIN_PX) if ok]
        for e in typed.get(Circle, []):
            if cancelled or _chk(): cancelled = True; break
            col, lw, dash = _resolve(e)
            if col is None: continue
            n = _n_segs(e.radius, sc)
            if dash:
                step = 2 * math.pi / n
                pts = [(e.cx + e.radius * math.cos(i * step),
                        e.cy + e.radius * math.sin(i * step))
                       for i in range(n + 1)]
                curve_flat[(e.layer, lw)] += _apply_dash_to_path(pts, col, dash, sc)
            else:
                a  = _np.linspace(0.0, 2*math.pi, n+1, dtype=_np.float32)
                xs = _np.float32(e.cx) + _np.float32(e.radius) * _np.cos(a)
                ys = _np.float32(e.cy) + _np.float32(e.radius) * _np.sin(a)
                curve_arrs[(e.layer, lw)].append(_segs_from_pts(xs, ys, col, n))

        # ── Arcs ─────────────────────────────────────────────────────────
        _all_arcs = typed.get(Arc, [])
        if _all_arcs:
            _ra = _np.array([e.radius for e in _all_arcs], dtype=_np.float32)
            typed[Arc] = [e for e, ok in zip(_all_arcs,
                                               _ra * 2.0 * sc >= _LOD_MIN_PX) if ok]
        for e in typed.get(Arc, []):
            if cancelled or _chk(): cancelled = True; break
            col, lw, dash = _resolve(e)
            if col is None: continue
            n  = _n_segs(e.radius, sc)
            sa = math.radians(e.start_ang); ea = math.radians(e.end_ang)
            if e.ccw:
                if ea <= sa: ea += 2 * math.pi
            else:
                if ea >= sa: ea -= 2 * math.pi
            sweep = ea - sa
            step  = sweep / max(n, 4)
            cnt   = abs(int(sweep / abs(step))) if step else 0
            if cnt < 1: continue
            if dash:
                pts = [(e.cx + e.radius * math.cos(sa + i * step),
                        e.cy + e.radius * math.sin(sa + i * step))
                       for i in range(cnt + 1)]
                curve_flat[(e.layer, lw)] += _apply_dash_to_path(pts, col, dash, sc)
            else:
                a  = sa + _np.arange(cnt+1, dtype=_np.float32) * _np.float32(step)
                xs = _np.float32(e.cx) + _np.float32(e.radius) * _np.cos(a)
                ys = _np.float32(e.cy) + _np.float32(e.radius) * _np.sin(a)
                curve_arrs[(e.layer, lw)].append(_segs_from_pts(xs, ys, col, cnt))

        # ── Polylines ─────────────────────────────────────────────────────
        for e in typed.get(Polyline, []):
            if cancelled or _chk(): cancelled = True; break
            col, lw, dash = _resolve(e)
            if col is None: continue
            if _entity_px(e, sc) < _LOD_MIN_PX or len(e.points) < 2: continue
            if dash:
                pts = list(e.points)
                if e.closed and len(pts) > 2: pts = pts + [pts[0]]
                curve_flat[(e.layer, lw)] += _apply_dash_to_path(pts, col, dash, sc)
            else:
                pa = _np.asarray(e.points, dtype=_np.float32)
                if e.closed and len(pa) > 2: pa = _np.vstack([pa, pa[0:1]])
                n_seg = len(pa) - 1
                if n_seg < 1: continue
                curve_arrs[(e.layer, lw)].append(_segs_from_pts(pa[:,0], pa[:,1], col, n_seg))

        # ── Ellipse ───────────────────────────────────────────────────────
        for e in typed.get(Ellipse, []):
            if cancelled or _chk(): cancelled = True; break
            col, lw, dash = _resolve(e)
            if col is None: continue
            if _entity_px(e, sc) < _LOD_MIN_PX: continue
            bpts = list(e._pts_on_boundary(72))
            if not bpts: continue
            bpts_c = bpts + [bpts[0]]
            if dash:
                curve_flat[(e.layer, lw)] += _apply_dash_to_path(bpts_c, col, dash, sc)
            else:
                pa = _np.asarray(bpts_c, dtype=_np.float32)
                curve_arrs[(e.layer, lw)].append(_segs_from_pts(pa[:,0], pa[:,1], col, len(pa)-1))

        # ── Spline ────────────────────────────────────────────────────────
        for e in typed.get(Spline, []):
            if cancelled: break
            col, lw, dash = _resolve(e)
            if col is None or len(e.points) < 2: continue
            if _entity_px(e, sc) < _LOD_MIN_PX: continue
            ipts = list(e.interp(n_seg=20))
            if len(ipts) < 2: continue
            if dash:
                curve_flat[(e.layer, lw)] += _apply_dash_to_path(ipts, col, dash, sc)
            else:
                pa = _np.asarray(ipts, dtype=_np.float32)
                n_seg = len(pa) - 1
                curve_arrs[(e.layer, lw)].append(_segs_from_pts(pa[:,0], pa[:,1], col, n_seg))

        # ── XLines (viewport-dep, se incluyen en el snapshot del viewport) ─
        xline_ents: list = []
        for e in typed.get(XLine, []):
            if cancelled: break
            col, lw, dash = _resolve(e)
            if col is None: continue
            xline_flat[lw] += _tess_xline(e, col, ctx)
            xline_ents.append((e, col, lw))  # guardar para redibujar en cache-hit

        self._tess_progress = f"⟳ Anotaciones — {_n_total:,} entidades"
        # ── Dimensions ────────────────────────────────────────────────────
        # El fail-log solo se activa cuando el diagnóstico está abierto.
        # En producción no se registra nada — evita string-formatting en cada
        # excepción (~1s extra en full-scan con 600k entidades).
        _diag_on = getattr(self, '_cache_diag', False)
        _dim_fail_log = self._dim_fail_log if _diag_on else None
        for e in typed.get(_Dim, []):
            if cancelled: break
            try:
                _tess_dim_gl(e, lw_lines=line_rows, tri_rows=tri_rows,
                             gl_ids=new_dim_gl_ids, resolve=_resolve, ctx=ctx)
            except Exception as _ex:
                if _dim_fail_log is not None:
                    _k = f"{getattr(e,'dim_type','?')}:{type(_ex).__name__}:{str(_ex)[:40]}"
                    _dim_fail_log[_k] = _dim_fail_log.get(_k, 0) + 1

        self._tess_progress = f"⟳ Rellenos — {_n_total:,} entidades"
        # ── Hatches ───────────────────────────────────────────────────────
        for e in typed.get(_Hatch, []):
            if cancelled: break
            try:
                _tess_hatch_gl(e, line_rows=line_rows, tri_rows=tri_rows,
                                gl_ids=new_hatch_gl_ids, resolve=_resolve, ctx=ctx)
            except Exception:
                pass

        self._tess_progress = f"⟳ Bloques — {_n_total:,} entidades"
        # ── Insertos / Bloques ────────────────────────────────────────────
        from cad.entities import Insert as _Insert
        _ins_fail_log = self._ins_fail_log if _diag_on else None
        for e in typed.get(_Insert, []):
            if cancelled: break
            try:
                _tess_insert_gl(e, line_rows=line_rows, curve_arrs=curve_arrs,
                                 tri_rows=tri_rows, gl_ids=new_insert_gl_ids,
                                 resolve=_resolve, ctx=ctx, fail_log=_ins_fail_log,
                                 ins_layer=getattr(e, 'layer', '0') or '0',
                                 block_geom_cache=self._block_geom_cache,
                                 block_cache_layer_idx=self._block_cache_layer_index)
            except Exception as _ex:
                if _ins_fail_log is not None:
                    _k = f"{getattr(e,'block_name','?')}:{type(_ex).__name__}:{str(_ex)[:40]}"
                    _ins_fail_log[_k] = _ins_fail_log.get(_k, 0) + 1

        # ── Leaders ───────────────────────────────────────────────────────
        from cad.entities import Leader as _Leader
        for e in typed.get(_Leader, []):
            if cancelled: break
            try:
                col, lw, dash = _resolve(e)
                if col is None or len(e.points) < 2: continue
                r, g, b = col
                # Polilínea del líder
                pa = _np.asarray(e.points, dtype=_np.float32)
                n_seg = len(pa) - 1
                if n_seg >= 1:
                    seg = _np.empty(n_seg * 10, dtype=_np.float32)
                    seg[0::10] = pa[:-1, 0]; seg[1::10] = pa[:-1, 1]
                    seg[2::10] = r;           seg[3::10] = g; seg[4::10] = b
                    seg[5::10] = pa[1:, 0];  seg[6::10] = pa[1:, 1]
                    seg[7::10] = r;           seg[8::10] = g; seg[9::10] = b
                    curve_arrs[(getattr(e, 'layer', '0') or '0', lw)].append(seg)
                # Flecha en el primer punto (triángulo relleno)
                p0x, p0y = e.points[0]
                p1x, p1y = e.points[1]
                dx, dy = p1x - p0x, p1y - p0y
                _d = math.hypot(dx, dy)
                if _d > 1e-9:
                    arr_sz = e.arrow_size if e.arrow_size > 1e-9 else 0.05
                    ux, uy = dx / _d, dy / _d
                    nx, ny = -uy, ux
                    _ldr_layer = getattr(e, 'layer', '0') or '0'
                    tri_rows[_ldr_layer] += [
                        p0x, p0y, r, g, b,
                        p0x+ux*arr_sz+nx*arr_sz*0.35, p0y+uy*arr_sz+ny*arr_sz*0.35, r, g, b,
                        p0x+ux*arr_sz-nx*arr_sz*0.35, p0y+uy*arr_sz-ny*arr_sz*0.35, r, g, b,
                    ]
                # Texto del líder vía font atlas
                if e.text:
                    _ldr_q = getattr(ctx, '_dim_text_queue', None)
                    if _ldr_q is not None:
                        lx, ly = e.points[-1]
                        _ldr_q.append((lx, ly, e.text, 0.20, 0.0, r, g, b))
                new_leader_gl_ids.add(id(e))
            except Exception:
                pass

        if cancelled:
            # Aunque cancelado, señalizar la queue para que _tess_pending se libere.
            # Sin esto: _tess_pending queda True permanentemente → ningún tessellator
            # nuevo puede arrancar → dibujo muestra datos stale (o vacío) para siempre.
            try:
                self._tess_result_q.put_nowait({'cache_key': None, 'cancelled': True})
            except Exception:
                pass   # queue llena = ya hay resultado pendiente, también libera pending
            return

        # ── Construir arrays numpy ─────────────────────────────────────────
        # El tessellator acumula por (layer_name, lw_px) para poder rastrear
        # qué vértices pertenecen a cada capa. Sin embargo, para el draw loop
        # mantenemos UN VBO por lw_px (igual que antes de la feature de visibilidad)
        # para no multiplicar los draw calls y preservar el rendimiento en cache hit.
        #
        # layer_ranges[lw_px][layer_name] = (start_vert, n_verts) permite
        # al draw loop omitir capas apagadas con glDrawArrays(start, count)
        # en lugar de un draw call completo. En estado estable (todas visibles)
        # → 1 draw call por lw = rendimiento idéntico al original.

        # Agrupar (layer, lw) → lw, recolectando arrays y calculando rangos
        _per_lw: dict = {}   # lw → dict{layer: [arrays]}
        for (layer, lw), _arr in [
            (key, _np.concatenate(
                ([_np.array(line_rows[key], dtype=_np.float32).ravel()]
                 if line_rows[key] else []) +
                ([_np.array(curve_flat[key], dtype=_np.float32)]
                 if curve_flat[key] else []) +
                list(curve_arrs[key])
            ) if (line_rows[key] or curve_flat[key] or curve_arrs[key]) else None)
            for key in (set(line_rows) | set(curve_flat) | set(curve_arrs))
        ]:
            if _arr is None or _arr.size == 0:
                continue
            _per_lw.setdefault(lw, {})[layer] = _arr

        cached      = {}   # lw_px → concatenated float32 array (todos los layers)
        layer_ranges= {}   # lw_px → {layer_name: (start_vertex, n_verts)}
        for lw, layer_arrays in _per_lw.items():
            offset = 0
            ranges = {}
            parts  = []
            for layer in sorted(layer_arrays):   # orden determinista
                arr = layer_arrays[layer]
                n   = arr.size // 5              # 5 floats por vértice: x,y,r,g,b
                ranges[layer] = (offset, n)
                offset += n
                parts.append(arr)
            if parts:
                cached[lw]       = _np.concatenate(parts) if len(parts) > 1 else parts[0]
                layer_ranges[lw] = ranges

        # Encolar resultado para que el GL thread lo suba a VRAM
        # ── Texto en espacio mundo (mismo hilo, evita 500ms en GL thread) ──
        text_arr       = None
        text_cache_key = None
        atlas = self._font_atlas   # safe: solo lectura, inicializado antes
        if atlas is not None and atlas.glyph_uvs:
            from cad.entities import Text as _Text
            from cad.renderer_pil import _query_viewport as _qvp
            _lod_key  = int(math.log2(max(sc, 0.001)))
            _sel_ver  = getattr(ctx, 'sel_version', 0)
            _lts_key2 = round(float(cfg_snap.get("ltscale", 1.0)) * 1000)
            # Usar len(ctx.entities) — NO len(ents_snap).
            # En tile mode ents_snap es un subconjunto visible; si se usara
            # len(ents_snap), el text_cache_key nunca coincidiría con el que
            # calcula _render_text_gl (que usa len(ctx.entities)), causando
            # text cache miss cada frame → rebuild quads 35-40ms en steady state.
            _ent_key2 = (id(ctx.entities),
                         len(ctx.entities) if ctx.entities else 0,
                         _sel_ver, _lts_key2)
            text_cache_key = (_ent_key2, _lod_key)

            # Viewport para culling
            ox = ctx.offset_x; oy = ctx.offset_y
            W  = ctx.W;        H  = ctx.H
            vx0, vy0, vx1, vy1 = _vp.viewport_world(sc, ox, oy, W, H)
            txt_cands = _qvp(ctx.entity_index, ctx.entity_cell, vx0, vy0, vx1, vy1)
            if not txt_cands:
                txt_cands = []

            base_px  = float(atlas.base_px)
            _sdf_px  = int(getattr(atlas, 'sdf_pad', 0))  # 0 → bitmap compat
            lyr_def  = (True, False, "#FFFFFF", 1, ())
            quads: list = []

            for e in txt_cands:
                if not isinstance(e, _Text): continue
                if e.height * sc < 5.0: continue
                lp = lyr_cache.get(e.layer, lyr_def)
                col_hex, _, _, skip = resolve_entity_props(
                    e, lp, select_color, scale=sc, ltscale=ltscale)
                if skip: continue
                try: r, g, b = _h2rgbf3(col_hex)
                except Exception: r, g, b = 1.0, 1.0, 1.0
                sf_w  = e.height / base_px
                angle  = e.angle
                lh_w   = e.height * 1.4   # line height en unidades mundo
                lines  = e.content.split('\n') if '\n' in e.content else [e.content]
                n_lines = len(lines)
                _vl    = getattr(e, 'valign', 0)
                _h     = e.height   # altura de glifo en espacio mundo
                # v_base: desplazamiento vertical base (mundo, Y↑)
                # valign=3 (top): top de línea 0 en e.y → bajar un glifo (-height)
                # valign=2 (mid): centrar bloque en e.y
                # valign=0 (baseline): e.y es la baseline → sin desplazamiento
                v_base = (0.0                              if _vl == 0 else
                          (n_lines * lh_w) * 0.5 - _h    if _vl == 2 else
                          -_h)                             # 3=top

                # Para MTEXT con cuadro anclado a la derecha (halign=2, multilínea):
                # el cuadro tiene borde derecho en e.x y texto izquierda-justificado
                # dentro del cuadro. Calculamos el borde izquierdo = e.x - ancho_máx.
                # Para texto de una sola línea o TEXT genuinamente right-justified,
                # el comportamiento es idéntico (max_w = w_línea → right-align).
                _box_left = None
                if e.halign == 2 and n_lines > 1:
                    _max_lw = max(
                        sum((atlas.glyph_uvs.get(ch) or atlas.glyph_uvs.get('?') or
                             (0,0,0,0,0,0,0))[4] * sf_w for ch in ln)
                        for ln in lines if ln) if any(lines) else 0.0
                    _box_left = e.x - _max_lw

                if abs(angle) < 0.01:
                    for li, line in enumerate(lines):
                        if not line: continue
                        y_off = v_base + (-li * lh_w)
                        if _box_left is not None:
                            # MTEXT multilínea TR: left-justify desde borde izquierdo del cuadro
                            cx_w = _box_left
                        elif e.halign in (1, 2):
                            total_w_w = sum(
                                (atlas.glyph_uvs.get(ch) or atlas.glyph_uvs.get('?') or
                                 (0,0,0,0,0,0))[4] * sf_w for ch in line)
                            adj_x_w = total_w_w/2 if e.halign==1 else total_w_w
                            cx_w = e.x - adj_x_w
                        else:
                            cx_w = e.x
                        for ch in line:
                            info = atlas.glyph_uvs.get(ch) or atlas.glyph_uvs.get('?')
                            if not info: continue
                            u0,v0,u1,v1,adv,h_g = info
                            _sp  = _sdf_px * sf_w
                            pw_w = (adv + 2*_sdf_px)*sf_w; ph_w = (h_g + 2*_sdf_px)*sf_w
                            _qx  = cx_w - _sp;  _qy = e.y + y_off - _sp
                            _qt  = _qy + ph_w;  _qr = _qx + pw_w
                            quads += [_qx,_qt,u0,v0,r,g,b, _qr,_qt,u1,v0,r,g,b,
                                      _qr,_qy,u1,v1,r,g,b, _qx,_qt,u0,v0,r,g,b,
                                      _qr,_qy,u1,v1,r,g,b, _qx,_qy,u0,v1,r,g,b]
                            cx_w += adv * sf_w   # avance real (sin pad SDF)
                else:
                    ar = math.radians(angle); ca = math.cos(ar); sa = math.sin(ar)
                    for li, line in enumerate(lines):
                        if not line: continue
                        # Cada línea se desplaza perpendicular a la dirección del texto
                        perp_off = v_base + (-li * lh_w)
                        if _box_left is not None:
                            align_off_w = -(e.x - _box_left)   # offset negativo → shift a izquierda
                        elif e.halign in (1, 2):
                            total_w_w = sum(
                                (atlas.glyph_uvs.get(ch) or atlas.glyph_uvs.get('?') or
                                 (0,0,0,0,0,0))[4] * sf_w for ch in line)
                            align_off_w = total_w_w/2 if e.halign==1 else total_w_w
                        else:
                            align_off_w = 0.0
                        st_x = e.x - align_off_w*ca + perp_off*(-sa)
                        st_y = e.y - align_off_w*sa + perp_off*ca
                        cur_w = 0.0
                        for ch in line:
                            info = atlas.glyph_uvs.get(ch) or atlas.glyph_uvs.get('?')
                            if not info: continue
                            u0,v0,u1,v1,adv,h_g = info
                            _sp  = _sdf_px * sf_w
                            pw_w = (adv + 2*_sdf_px)*sf_w; ph_w = (h_g + 2*_sdf_px)*sf_w
                            # bl desplazado -pad a lo largo del texto y -pad en perpendicular
                            bl_x = st_x + (cur_w - _sp)*ca + _sp*sa
                            bl_y = st_y + (cur_w - _sp)*sa - _sp*ca
                            tl_x = bl_x + ph_w*(-sa); tl_y = bl_y + ph_w*ca
                            br_x = bl_x + pw_w*ca;    br_y = bl_y + pw_w*sa
                            tr_x = br_x + ph_w*(-sa); tr_y = br_y + ph_w*ca
                            quads += [tl_x,tl_y,u0,v0,r,g,b, tr_x,tr_y,u1,v0,r,g,b,
                                      br_x,br_y,u1,v1,r,g,b, tl_x,tl_y,u0,v0,r,g,b,
                                      br_x,br_y,u1,v1,r,g,b, bl_x,bl_y,u0,v1,r,g,b]
                            cur_w += adv * sf_w   # avance real (sin pad SDF)

            # ── Texto de cotas (espacio mundo, mismo atlas) ──────────────
            for (wx, wy, txt, height, ang, *_tc) in list(getattr(ctx, '_dim_text_queue', [])):
                if not txt or height * sc < 5.0: continue
                sf_w_d = height / base_px
                rd, gd, bd = (_tc[0], _tc[1], _tc[2]) if len(_tc) == 3 else (1.0, 1.0, 1.0)
                total_w_d = sum(
                    (atlas.glyph_uvs.get(c) or atlas.glyph_uvs.get('?') or
                     (0,0,0,0,0,0))[4] * sf_w_d for c in txt)
                if abs(ang) < 0.5:
                    cx_d = wx - total_w_d / 2
                    for ch in txt:
                        info = atlas.glyph_uvs.get(ch) or atlas.glyph_uvs.get('?')
                        if not info: continue
                        u0, v0, u1, v1, adv, h_g = info
                        _sp_d = _sdf_px * sf_w_d
                        pw_w  = (adv + 2*_sdf_px)*sf_w_d; ph_w = (h_g + 2*_sdf_px)*sf_w_d
                        _qxd  = cx_d - _sp_d; _qyd = wy - _sp_d
                        _qtd  = _qyd + ph_w;  _qrd = _qxd + pw_w
                        quads += [_qxd, _qtd, u0, v0, rd, gd, bd,
                                  _qrd, _qtd, u1, v0, rd, gd, bd,
                                  _qrd, _qyd, u1, v1, rd, gd, bd,
                                  _qxd, _qtd, u0, v0, rd, gd, bd,
                                  _qrd, _qyd, u1, v1, rd, gd, bd,
                                  _qxd, _qyd, u0, v1, rd, gd, bd]
                        cx_d += adv * sf_w_d   # avance real
                else:
                    ar = math.radians(ang); ca = math.cos(ar); sa = math.sin(ar)
                    st_x = wx - (total_w_d / 2) * ca
                    st_y = wy - (total_w_d / 2) * sa
                    cur_w = 0.0
                    for ch in txt:
                        info = atlas.glyph_uvs.get(ch) or atlas.glyph_uvs.get('?')
                        if not info: continue
                        u0, v0, u1, v1, adv, h_g = info
                        _sp_d = _sdf_px * sf_w_d
                        pw_w  = (adv + 2*_sdf_px)*sf_w_d; ph_w = (h_g + 2*_sdf_px)*sf_w_d
                        bl_x  = st_x + (cur_w - _sp_d)*ca + _sp_d*sa
                        bl_y  = st_y + (cur_w - _sp_d)*sa - _sp_d*ca
                        tl_x  = bl_x + ph_w * (-sa);     tl_y = bl_y + ph_w * ca
                        br_x  = bl_x + pw_w * ca;        br_y = bl_y + pw_w * sa
                        tr_x  = br_x + ph_w * (-sa);     tr_y = br_y + ph_w * ca
                        quads += [tl_x, tl_y, u0, v0, rd, gd, bd,
                                  tr_x, tr_y, u1, v0, rd, gd, bd,
                                  br_x, br_y, u1, v1, rd, gd, bd,
                                  tl_x, tl_y, u0, v0, rd, gd, bd,
                                  br_x, br_y, u1, v1, rd, gd, bd,
                                  bl_x, bl_y, u0, v1, rd, gd, bd]
                        cur_w += adv * sf_w_d   # avance real

            if quads:
                text_arr = _np.array(quads, dtype=_np.float32)

        self._tess_progress = ""   # tessellation completa — limpiar indicador
        # Construir tri_arr ordenado por capa + tri_layer_ranges para filtrado por capa
        # (análogo a layer_ranges para GL_LINES, permite toggle de capa en triángulos)
        _tri_parts: list = []
        _tri_layer_ranges: dict = {}
        _tri_offset = 0
        for _lyr_n in sorted(tri_rows.keys()):
            _lyr_data = tri_rows[_lyr_n]
            if not _lyr_data:
                continue
            _n_v = len(_lyr_data) // 5
            _tri_layer_ranges[_lyr_n] = (_tri_offset, _n_v)
            _tri_offset += _n_v
            _tri_parts.append(_np.array(_lyr_data, dtype=_np.float32))
        tri_arr = _np.concatenate(_tri_parts) if _tri_parts else None
        try:
            self._tess_result_q.put_nowait({
                'cache_key':        cache_key,
                'cached':           cached,
                'layer_ranges':     layer_ranges,
                'tri_layer_ranges': _tri_layer_ranges,
                'tri_arr':          tri_arr,
                'xline_flat':     xline_flat,
                'xline_ents':     xline_ents,
                'dim_gl_ids':     new_dim_gl_ids,
                'hatch_gl_ids':   new_hatch_gl_ids,
                'insert_gl_ids':  new_insert_gl_ids,
                'leader_gl_ids':  new_leader_gl_ids,
                'text_arr':       text_arr,
                'text_cache_key': text_cache_key,
                'tess_bounds':    tess_bounds,
                'incremental':    incremental,
            })
        except Exception:
            pass   # queue llena (frame posterior la leerá)

        # Notificar al engine que hay resultado listo → dispara redraw
        cb = self._tess_done_cb
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def _upload_tess_result(self, result: dict) -> None:
        """Upload a VRAM de arrays producidos por _tessellate_and_enqueue.
        Llamado exclusivamente desde el GL thread (_render_entities).
        """
        # ── Modo incremental: fusionar delta con estado existente ─────────
        # IMPORTANTE: la condición no depende de si _vbo_cache está vacío.
        # Siempre se ejecuta cuando incremental=True para restaurar tri_arr,
        # hatch_gl_ids, etc. aunque no haya líneas previas en _vbo_cache.
        if result.get('incremental'):
            # ── UPLOAD INCREMENTAL: solo tocar VBOs con datos nuevos ─────
            # Evita borrar y re-subir todos los VBOs (potencialmente 2MB+)
            # cuando solo se agrega 1 entidad. Solo actualizamos el lw_px que
            # cambió y, si no hay triángulos nuevos, dejamos _vram_tri_buf intacto.
            delta_cached = result['cached']   # solo la geometría nueva

            # 1. Actualizar en GPU solo los lw_px que tienen datos nuevos
            for lw_px, delta_arr in delta_cached.items():
                existing = self._vbo_cache.get(lw_px)
                if existing is not None and existing.size > 0:
                    new_arr = _np.concatenate([existing, delta_arr])
                else:
                    new_arr = delta_arr
                # Actualizar CPU cache para este lw_px
                self._vbo_cache[lw_px] = new_arr
                # Borrar solo el VBO de este lw_px (los demás quedan intactos)
                if lw_px in self._vram_bufs:
                    _old_vao, _old_vbo, _ = self._vram_bufs[lw_px]
                    _gl.glDeleteVertexArrays(1, (ctypes.c_uint * 1)(_old_vao))
                    _gl.glDeleteBuffers(1,    (ctypes.c_uint * 1)(_old_vbo))
                # Subir array fusionado para este lw_px
                _raw_h = _gl.glGenBuffers(1)
                _vbo_h = int(_raw_h[0]) if hasattr(_raw_h, '__len__') else int(_raw_h)
                _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, _vbo_h)
                _gl.glBufferData(_gl.GL_ARRAY_BUFFER, new_arr.nbytes, new_arr,
                                 _gl.GL_STATIC_DRAW)
                _vao_h = self._make_vao_for_vbo(_vbo_h)
                self._vram_bufs[lw_px] = (_vao_h, _vbo_h, new_arr.size // 5)

            # 2. Tri VBO (flechas + hatches SOLID): solo actualizar si hay nuevos
            _delta_tri = result.get('tri_arr')
            if _delta_tri is not None and _delta_tri.size > 0:
                # Hay triángulos nuevos → mergear y re-uploadear
                _prev_tri = self._cpu_tri_arr
                _merged_tri = (_np.concatenate([_prev_tri, _delta_tri])
                               if _prev_tri is not None and _prev_tri.size > 0
                               else _delta_tri)
                if self._vram_tri_buf is not None:
                    _ov, _ob, _ = self._vram_tri_buf
                    _gl.glDeleteVertexArrays(1, (ctypes.c_uint * 1)(_ov))
                    _gl.glDeleteBuffers(1,    (ctypes.c_uint * 1)(_ob))
                _raw_t = _gl.glGenBuffers(1)
                _tvbo  = int(_raw_t[0]) if hasattr(_raw_t, '__len__') else int(_raw_t)
                _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, _tvbo)
                _gl.glBufferData(_gl.GL_ARRAY_BUFFER, _merged_tri.nbytes, _merged_tri,
                                 _gl.GL_STATIC_DRAW)
                _tvao = self._make_vao_for_vbo(_tvbo)
                self._vram_tri_buf = (_tvao, _tvbo, _merged_tri.size // 5)
                self._cpu_tri_arr  = _merged_tri
            # else: sin triángulos nuevos → _vram_tri_buf y _cpu_tri_arr sin tocar

            # 3. IDs y metadata
            self._dim_gl_ids    = result['dim_gl_ids']    | self._dim_gl_ids
            self._hatch_gl_ids  = result['hatch_gl_ids']  | self._hatch_gl_ids
            self._insert_gl_ids = result.get('insert_gl_ids', set()) | self._insert_gl_ids
            self._leader_gl_ids = result.get('leader_gl_ids', set()) | self._leader_gl_ids
            self._vbo_cache_key = result['cache_key']
            self._vram_bufs_key = result['cache_key']
            self._xline_ents    = self._xline_ents + result.get('xline_ents', [])
            # layer_ranges: los rangos del delta son locales (empiezan en 0) y no
            # coinciden con las posiciones reales en el VBO fusionado → no actualizar.
            # El próximo miss completo (zoom, etc.) recalcula ranges correctos.

            return   # ← EARLY RETURN — el upload completo no es necesario

        # ── Upload completo (tessellation full, no incremental) ──────────
        cache_key       = result['cache_key']
        cached          = result['cached']
        xline_flat      = result.get('xline_flat', {})
        dim_gl_ids      = result['dim_gl_ids']
        hatch_gl_ids    = result['hatch_gl_ids']
        insert_gl_ids   = result.get('insert_gl_ids', set())
        leader_gl_ids   = result.get('leader_gl_ids', set())

        # Actualizar CPU cache y sets de IDs
        self._vbo_cache       = cached
        self._vbo_cache_key   = cache_key
        self._dim_gl_ids      = dim_gl_ids
        self._hatch_gl_ids    = hatch_gl_ids
        self._insert_gl_ids   = insert_gl_ids
        self._leader_gl_ids   = leader_gl_ids
        # layer_ranges[lw_px][layer_name] = (start_vertex, n_verts)
        # Permite omitir capas apagadas con glDrawArrays(start, count) sin re-tessellation
        self._vram_layer_ranges = result.get('layer_ranges', {})
        # Guardar bounds del área tessellada para el check de viewport en próximos frames
        self._tess_vp_bounds  = result.get('tess_bounds')

        # XLines: guardar entidades para redibujarlas en cada frame (viewport-dep)
        self._xline_ents = result.get('xline_ents', [])

        # Liberar VBOs de líneas anteriores
        if self._vram_bufs:
            old_vaos = [v[0] for v in self._vram_bufs.values()]
            old_vbos = [v[1] for v in self._vram_bufs.values()]
            _gl.glDeleteVertexArrays(len(old_vaos),
                                     (ctypes.c_uint * len(old_vaos))(*old_vaos))
            _gl.glDeleteBuffers(len(old_vbos),
                                (ctypes.c_uint * len(old_vbos))(*old_vbos))
            self._vram_bufs = {}

        # Subir arrays nuevos
        for lw, arr in cached.items():
            raw_h = _gl.glGenBuffers(1)
            vbo_h = int(raw_h[0]) if hasattr(raw_h, '__len__') else int(raw_h)
            _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, vbo_h)
            _gl.glBufferData(_gl.GL_ARRAY_BUFFER, arr.nbytes, arr, _gl.GL_STATIC_DRAW)
            vao_h = self._make_vao_for_vbo(vbo_h)
            self._vram_bufs[lw] = (vao_h, vbo_h, arr.size // 5)

        self._vram_bufs_key = cache_key
        _vk = sum(arr.nbytes for arr in cached.values()) // 1024
        if _vk > 0:
            self._perf_stats["vram_kb"] = _vk

        # Liberar VBO de triángulos anterior
        if self._vram_tri_buf is not None:
            old_vao, old_vbo, _ = self._vram_tri_buf
            _gl.glDeleteVertexArrays(1, (ctypes.c_uint * 1)(old_vao))
            _gl.glDeleteBuffers(1, (ctypes.c_uint * 1)(old_vbo))
            self._vram_tri_buf = None

        arr_tri = result.get('tri_arr')   # ya convertido a numpy en hilo de fondo
        if arr_tri is not None and arr_tri.size > 0:
            raw_t = _gl.glGenBuffers(1)
            tvbo = int(raw_t[0]) if hasattr(raw_t, '__len__') else int(raw_t)
            _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, tvbo)
            _gl.glBufferData(_gl.GL_ARRAY_BUFFER, arr_tri.nbytes, arr_tri,
                             _gl.GL_STATIC_DRAW)
            tvao = self._make_vao_for_vbo(tvbo)
            self._vram_tri_buf = (tvao, tvbo, arr_tri.size // 5)
        self._vram_tri_layer_ranges = result.get('tri_layer_ranges', {})

        # ── Texto: subir array precalculado en hilo de fondo ─────────────
        text_arr       = result.get('text_arr')
        text_cache_key = result.get('text_cache_key')
        if text_arr is not None and text_cache_key is not None and \
                self._vao_text is not None and self._vbo_text is not None:
            _gl.glBindVertexArray(self._vao_text)
            _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, self._vbo_text)
            _gl.glBufferData(_gl.GL_ARRAY_BUFFER, text_arr.nbytes, text_arr,
                             _gl.GL_STATIC_DRAW)
            self._vbo_text_n_verts   = text_arr.size // 7
            self._vbo_text_cache_key = text_cache_key
            _gl.glBindVertexArray(0)

        # Guardar copia CPU del tri_arr para futuros merges incrementales.
        # Solo para tessellations full — el path incremental hace early return
        # y actualiza _cpu_tri_arr directamente dentro de su bloque.
        self._cpu_tri_arr = result.get('tri_arr')

    # ── Text GPU ─────────────────────────────────────────────────────────────

    def _render_text_gl(self, ctx: Any, proj: list) -> None:
        """Dibuja texto con font atlas en GPU — vértices en espacio MUNDO.
        Pan/zoom = solo actualiza uProjection (0 rebuild). Cache igual que geometría."""
        if self._prog_text is None or self._vao_text is None:
            return

        if self._font_atlas is None:
            self._font_atlas = _FontAtlas()
            self._font_atlas.build()
        atlas = self._font_atlas
        if not atlas.glyph_uvs:
            return

        sc = ctx.scale
        _sel_ver = getattr(ctx, 'sel_version', 0)
        _lts_key = round(float(ctx.config.get("ltscale", 1.0)) * 1000)
        _ent_key = (id(ctx.entities), len(ctx.entities) if ctx.entities else 0,
                    _sel_ver, _lts_key)
        _lod_key = int(math.log2(max(sc, 0.001)))
        _txt_key = (_ent_key, _lod_key)

        def _draw_text():
            _gl.glUseProgram(self._prog_text)
            _gl.glUniformMatrix4fv(self._uloc_text_proj, 1, False,
                                   (ctypes.c_float * 16)(*proj))
            _gl.glUniform1i(self._uloc_text_atlas, 0)
            _gl.glActiveTexture(_gl.GL_TEXTURE0)
            _gl.glBindTexture(_gl.GL_TEXTURE_2D, atlas.texture_id)
            _gl.glEnable(_gl.GL_BLEND)
            _gl.glBlendFunc(_gl.GL_SRC_ALPHA, _gl.GL_ONE_MINUS_SRC_ALPHA)
            _gl.glDisable(_gl.GL_DEPTH_TEST)
            _gl.glBindVertexArray(self._vao_text)
            _gl.glDrawArrays(_gl.GL_TRIANGLES, 0, self._vbo_text_n_verts)
            _gl.glBindVertexArray(0)
            _gl.glBindTexture(_gl.GL_TEXTURE_2D, 0)
            _gl.glDisable(_gl.GL_BLEND)
            _gl.glEnable(_gl.GL_DEPTH_TEST)

        # ── Fast path: VRAM válida → solo bind+draw ───────────────────────
        self._perf_stats["text_verts"] = self._vbo_text_n_verts
        if _txt_key == self._vbo_text_cache_key and self._vbo_text_n_verts > 0:
            _draw_text()
            return

        # ── Cache miss: hilo de fondo reconstruye — dibujar VBO anterior ───
        # Los vértices están en espacio MUNDO: uProjection actual los posiciona
        # correctamente aunque el VBO sea de un LOD anterior.
        if self._vbo_text_n_verts > 0:
            _draw_text()


    # ── Hatch GPU ────────────────────────────────────────────────────────────

    @staticmethod
    def _poly_is_convex(pts: list) -> bool:
        """True si el polígono es convexo (todos los cross products tienen el mismo signo)."""
        n = len(pts)
        if n < 3:
            return True
        sign = None
        for i in range(n):
            ax = pts[(i+1) % n][0] - pts[i][0]
            ay = pts[(i+1) % n][1] - pts[i][1]
            bx = pts[(i+2) % n][0] - pts[(i+1) % n][0]
            by = pts[(i+2) % n][1] - pts[(i+1) % n][1]
            cross = ax * by - ay * bx
            if abs(cross) < 1e-10:
                continue
            s = cross > 0
            if sign is None:
                sign = s
            elif s != sign:
                return False
        return True

    # ── Overlay PIL para Dimension / Leader / Hatch-patrón / Insert ──────────

    def _get_pil_helper(self):
        """Instancia lazy de RendererPIL para usar sus métodos de texto/cota."""
        if self._pil_helper is None:
            from cad.renderer_pil import RendererPIL
            self._pil_helper = RendererPIL()
        return self._pil_helper

    def _render_dims_gl_DISABLED(self, ctx: Any, proj: list) -> None:
        """DESHABILITADO — ver diagnóstico: margin excesivo + sin cache.
        Revertido a PIL. Reimplementar con viewport exacto + VRAM cache.
        """
        from cad.entities import Dimension
        from cad.renderer_pil import (build_layer_cache, resolve_entity_props,
                                       _query_viewport)

        self._dim_gl_ids = set()

        sc = ctx.scale
        ox, oy = ctx.offset_x, ctx.offset_y
        W,  H  = ctx.W, ctx.H

        # Culling con margen amplio (dims tienen líneas largas)
        vx0, vy0, vx1, vy1 = _vp.viewport_world(sc, ox, oy, W, H)
        margin = max(abs(vx1-vx0), abs(vy1-vy0)) * 0.5
        candidates = _query_viewport(
            ctx.entity_index, ctx.entity_cell,
            vx0-margin, vy0-margin, vx1+margin, vy1+margin)
        if not candidates:
            candidates = ctx.entities

        dims = [e for e in candidates if isinstance(e, Dimension)]
        if not dims:
            return

        lyr_cache = build_layer_cache(
            ctx.layers, sc, ltscale=float(ctx.config.get("ltscale", 1.0)))
        dimstyles = ctx.config.get("dimstyles", {}).get("styles", {})

        line_verts: list = []   # GL_LINES:     x,y,r,g,b × 2 por segmento
        tri_verts:  list = []   # GL_TRIANGLES: x,y,r,g,b × 3 por triángulo

        def _seg(ax, ay, bx, by, cr, cg, cb):
            line_verts.extend([ax, ay, cr, cg, cb, bx, by, cr, cg, cb])

        def _ext(sw, ew, gap_w, over_w, cr, cg, cb):
            """Línea de extensión en coords mundo con gap y prolongación."""
            sx, sy = sw;  ex, ey = ew
            dx, dy = ex-sx, ey-sy
            d = math.hypot(dx, dy)
            if d < gap_w + 1e-9:
                return
            gx = sx + dx/d*gap_w;  gy = sy + dy/d*gap_w
            ox_ = ex + dx/d*over_w; oy_ = ey + dy/d*over_w
            _seg(gx, gy, ox_, oy_, cr, cg, cb)

        def _arrow_tri(tip_w, dir_dx, dir_dy, size_w, cr, cg, cb):
            """Triángulo relleno: tip en coords mundo, dirección hacia base."""
            d = math.hypot(dir_dx, dir_dy)
            if d < 1e-9:
                return
            ux, uy = dir_dx/d, dir_dy/d
            nx, ny = -uy, ux
            tx, ty = tip_w
            p1x = tx + ux*size_w + nx*size_w*0.35
            p1y = ty + uy*size_w + ny*size_w*0.35
            p2x = tx + ux*size_w - nx*size_w*0.35
            p2y = ty + uy*size_w - ny*size_w*0.35
            tri_verts.extend([
                tx,  ty,  cr, cg, cb,
                p1x, p1y, cr, cg, cb,
                p2x, p2y, cr, cg, cb,
            ])

        for e in dims:
            # LOD: skip muy pequeñas
            if math.hypot(e.p2[0]-e.p1[0], e.p2[1]-e.p1[1]) * sc < 5.0:
                continue

            lp = lyr_cache.get(e.layer, (True, False, "#FFFFFF", 1, ()))
            col_hex, lw_px, _dash, skip = resolve_entity_props(
                e, lp, ctx.select_color, scale=sc,
                ltscale=float(ctx.config.get("ltscale", 1.0)))
            if skip:
                continue

            try:
                lr, lg, lb = _h2rgbf3(col_hex)
            except Exception:
                lr, lg, lb = 1.0, 1.0, 1.0

            # Parámetros del dimstyle (en unidades mundo)
            ds = dimstyles.get(e.style or "", {})
            _DIMASZ    = float(ds.get("arrow_size",      0.06))
            _EXT_OFF   = float(ds.get("ext_offset",      0.02))
            _EXT_OVER  = float(ds.get("ext_overshoot",
                                       ds.get("ext_beyond", 0.03)))
            _DIMBLK    = ds.get("arrow_type", "closed_filled")
            _lc        = ds.get("line_color", "bylayer")
            if _lc and _lc.lower() not in ("bylayer", "byblock", ""):
                try:
                    lr, lg, lb = _h2rgbf3(_lc)
                except Exception:
                    pass

            x1, y1 = e.p1;  x2, y2 = e.p2;  px, py = e.pos
            _skip_ext = getattr(e, 'no_ext', False)

            def _arrows(tip_w, base_w):
                if _DIMBLK in ("closed_filled", "", "default"):
                    bx, by = base_w;  tx, ty = tip_w
                    _arrow_tri(tip_w, bx-tx, by-ty, _DIMASZ, lr, lg, lb)
                elif _DIMBLK == "dot":
                    # Approximar como pequeño círculo (16 segmentos)
                    r = _DIMASZ * 0.5
                    tx, ty = tip_w
                    for i in range(16):
                        a0 = 2*math.pi*i/16;  a1_ = 2*math.pi*(i+1)/16
                        _seg(tx+r*math.cos(a0), ty+r*math.sin(a0),
                             tx+r*math.cos(a1_), ty+r*math.sin(a1_),
                             lr, lg, lb)
                elif _DIMBLK == "architectural":
                    # Tick diagonal
                    bx, by = base_w;  tx, ty = tip_w
                    d_ = math.hypot(bx-tx, by-ty)
                    if d_ < 1e-9: return
                    ux_ = (bx-tx)/d_;  uy_ = (by-ty)/d_
                    nx_ = -uy_;         ny_ = ux_
                    h = _DIMASZ
                    _seg(tx-ux_*h-nx_*h, ty-uy_*h-ny_*h,
                         tx+ux_*h+nx_*h, ty+uy_*h+ny_*h, lr, lg, lb)
                # "none" → sin flecha

            try:
                if e.dim_type in ("H", "V", "A"):
                    # Calcular puntos d1, d2 y extensiones (igual que PIL)
                    if e.dim_type == "H":
                        d1=(x1,py); d2=(x2,py)
                        e1s,e1e=(x1,y1),(x1,py); e2s,e2e=(x2,y2),(x2,py)
                    elif e.dim_type == "V":
                        d1=(px,y1); d2=(px,y2)
                        e1s,e1e=(x1,y1),(px,y1); e2s,e2e=(x2,y2),(px,y2)
                    elif getattr(e, 'rot_angle', None) is not None:
                        ar = math.radians(e.rot_angle)
                        ux_,uy_ = math.cos(ar), math.sin(ar)
                        nx_,ny_ = -uy_, ux_
                        dn = px*nx_+py*ny_
                        o1 = dn-(x1*nx_+y1*ny_)
                        d1 = (x1+nx_*o1, y1+ny_*o1)
                        o2 = dn-(x2*nx_+y2*ny_)
                        d2 = (x2+nx_*o2, y2+ny_*o2)
                        e1s,e1e=(x1,y1),d1; e2s,e2e=(x2,y2),d2
                    else:  # A alineada
                        dl = math.hypot(x2-x1,y2-y1)
                        if dl < 1e-9: continue
                        ux_,uy_ = (x2-x1)/dl,(y2-y1)/dl
                        nx_,ny_ = -uy_, ux_
                        off = (px-x1)*nx_+(py-y1)*ny_
                        d1 = (x1+nx_*off, y1+ny_*off)
                        d2 = (x2+nx_*off, y2+ny_*off)
                        e1s,e1e=(x1,y1),d1; e2s,e2e=(x2,y2),d2

                    if not _skip_ext:
                        _ext(e1s, e1e, _EXT_OFF, _EXT_OVER, lr, lg, lb)
                        _ext(e2s, e2e, _EXT_OFF, _EXT_OVER, lr, lg, lb)
                    _seg(d1[0],d1[1], d2[0],d2[1], lr,lg,lb)
                    _arrows(d1, d2); _arrows(d2, d1)
                    self._dim_gl_ids.add(id(e))

                elif e.dim_type in ("R", "D"):
                    if e.dim_type == "D":
                        opx,opy = x1-(x2-x1), y1-(y2-y1)
                        _seg(opx,opy, x2,y2, lr,lg,lb)
                        _arrows((x2,y2),(opx,opy)); _arrows((opx,opy),(x2,y2))
                    else:
                        _seg(x1,y1, x2,y2, lr,lg,lb)
                        _arrows((x2,y2),(x1,y1))
                    self._dim_gl_ids.add(id(e))

                elif e.dim_type == "ANG":
                    cx_w, cy_w = x1, y1
                    r_w = math.hypot(x2-cx_w, y2-cy_w)
                    if r_w*sc < 3: continue
                    a1_r = math.atan2(y2-cy_w, x2-cx_w)
                    a2_r = math.atan2(py-cy_w, px-cx_w)
                    ext_r = (a2_r-a1_r) % (2*math.pi)
                    if ext_r > math.pi: ext_r -= 2*math.pi
                    n = max(8, min(64, int(abs(math.degrees(ext_r))/3)))
                    step = ext_r/max(n, 1)
                    for i in range(n):
                        a_s = a1_r+i*step;  a_e = a1_r+(i+1)*step
                        _seg(cx_w+r_w*math.cos(a_s), cy_w+r_w*math.sin(a_s),
                             cx_w+r_w*math.cos(a_e), cy_w+r_w*math.sin(a_e),
                             lr, lg, lb)
                    self._dim_gl_ids.add(id(e))

            except Exception:
                self._dim_gl_ids.discard(id(e))   # PIL fallback

        # ── Upload y draw GL_LINES ────────────────────────────────────────
        if line_verts:
            self._draw_lines_np(_np.array(line_verts, dtype=_np.float32))

        # ── Upload y draw GL_TRIANGLES (flechas) ─────────────────────────
        if tri_verts:
            arr_t = _np.array(tri_verts, dtype=_np.float32)
            n_t = arr_t.size // 5
            _gl.glBindVertexArray(self._vao)
            _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, self._vbo)
            _gl.glBufferData(_gl.GL_ARRAY_BUFFER, arr_t.nbytes, arr_t,
                             _gl.GL_STREAM_DRAW)
            _gl.glDrawArrays(_gl.GL_TRIANGLES, 0, n_t)
            _gl.glBindVertexArray(0)

    def _render_text_dim_overlay(self, img: Any, ctx: Any) -> Any:
        """
        Segunda pasada PIL: renderiza Text/Dim/Leader/Hatch/Insert sobre la
        imagen OpenGL.

        C2 — Overlay lazy cache:
          La imagen del overlay se cachea en RGBA y se reutiliza mientras
          (scale, offset_x, offset_y, id(entities)) no cambie.
          En pan continuo el costo es ~0ms (solo alpha_composite).
          En zoom o edición se recalcula completo.
        """
        if img is None or not _PIL_OK:
            return img

        # Tessellator de fondo activo → _dim_gl_ids/_hatch_gl_ids incompletos.
        # Reusar overlay cacheado (con pan shift) en lugar de reconstruir con
        # datos stale. El overlay anterior persiste ~200-400ms hasta que el
        # tessellator termina y el próximo LOD change rebuild usa ids correctos.
        # Guarda: solo reusar si el overlay pertenece al mismo archivo
        # (mismo id de entities). Si el usuario abrió un archivo nuevo mientras
        # el tessellator corría, NO pegar overlay del archivo anterior.
        _ov_same_file_here = (
            self._overlay_key is not None and
            len(self._overlay_key) > 3 and
            self._overlay_key[3] == id(ctx.entities)
        )
        if self._tess_pending and self._overlay_img is not None and _ov_same_file_here:
            ox_now = ctx.offset_x
            oy_now = ctx.offset_y
            dx = int(round(ox_now - self._overlay_ox)) if self._overlay_ox is not None else 0
            dy = int(round(oy_now - self._overlay_oy)) if self._overlay_oy is not None else 0
            if dx == 0 and dy == 0:
                img.paste(self._overlay_img, mask=self._overlay_mask)
            else:
                img.paste(self._overlay_img, (dx, dy), mask=self._overlay_mask)
            return img

        from cad.entities import Text, Dimension, Leader, Hatch, Insert
        from cad.renderer_pil import (
            build_layer_cache, resolve_entity_props, _query_viewport
        )
        try:
            from PIL import ImageDraw as _D
        except ImportError:
            return img

        sc  = ctx.scale
        ox, oy = ctx.offset_x, ctx.offset_y
        W,  H  = ctx.W, ctx.H

        # ── Culling + filtrado ────────────────────────────────────────────
        vx0, vy0, vx1, vy1 = _vp.viewport_world(sc, ox, oy, W, H)
        candidates = _query_viewport(
            ctx.entity_index, ctx.entity_cell, vx0, vy0, vx1, vy1)
        if not candidates:
            return img   # Viewport vacío → overlay nulo, sin iterar N entidades

        _gpu_text   = self._prog_text is not None
        _dim_gl     = self._dim_gl_ids     # cotas ya en VRAM
        _hatch_gl   = self._hatch_gl_ids   # hatches ya en VRAM
        _insert_gl  = self._insert_gl_ids  # insertos ya en VRAM
        _leader_gl  = self._leader_gl_ids  # leaders ya en VRAM

        def _needs_overlay(e: Any) -> bool:
            if isinstance(e, Text):      return not _gpu_text
            if isinstance(e, Dimension): return id(e) not in _dim_gl
            if isinstance(e, Hatch):     return id(e) not in _hatch_gl
            if isinstance(e, Insert):    return id(e) not in _insert_gl
            if isinstance(e, Leader):    return id(e) not in _leader_gl
            return False

        overlay_ents = [e for e in candidates if _needs_overlay(e)]

        # Métricas de overlay para el panel de diagnóstico
        if getattr(self, '_cache_diag', False):
            from collections import Counter as _Ctr
            from cad.entities import Dimension as _DiagDim
            _ov_counts = _Ctr(type(e).__name__ for e in overlay_ents)
            self._diag_overlay_counts = dict(_ov_counts)
            # Desglose de dim_type en atributo separado (evita dict anidado)
            self._diag_dim_types = dict(_Ctr(
                getattr(e, 'dim_type', '?')
                for e in overlay_ents
                if isinstance(e, _DiagDim)
            ))
        else:
            self._diag_overlay_counts = {}
            self._diag_dim_types = {}

        if not overlay_ents:
            self._overlay_img = None
            self._overlay_key = None
            self._overlay_ox  = None
            self._overlay_oy  = None
            return img

        # C2 — Overlay LOD: skip entidades demasiado pequeñas para verse
        lod_ents = []
        for e in overlay_ents:
            if isinstance(e, Text):
                if e.height * sc < 5.0:   # alineado con _LOD_TEXT_PX de PIL
                    continue
            elif isinstance(e, Dimension):
                p1, p2 = e.p1, e.p2
                if math.hypot(p2[0]-p1[0], p2[1]-p1[1]) * sc < 5.0:
                    continue
                # Si el texto sería sub-pixel (<4px), el overlay PIL no aporta
                # nada visible — GL ya dibuja las líneas de extensión y de cota.
                # Usa 0.20m como altura mínima de texto (default DIMTXT "Arq-50").
                # A sc<20 las flechas y el número son ilegibles → skip todo.
                if 0.20 * sc < 4.0:
                    continue
            elif isinstance(e, Hatch):
                if e.boundary:
                    xs = [p[0] for p in e.boundary]
                    ys = [p[1] for p in e.boundary]
                    if max(max(xs)-min(xs), max(ys)-min(ys)) * sc < 2.0:
                        continue
                # Patrón no-SOLID a zoom bajo: las líneas del patrón quedan
                # sub-pixel e invisibles — skip para no procesar PIL inútilmente.
                # e.scale es el espaciado del patrón en unidades de dibujo;
                # si pat_scale * sc < 6.0 → separación < 6px → ruido invisible.
                # SOLID y "" siempre se dibujan (fill de color sólido, bajo costo).
                if e.pattern.upper() not in ("SOLID", ""):
                    _pat_sc = (getattr(e, 'scale', 1.0) or 1.0)
                    if _pat_sc * sc < 6.0:
                        continue
            elif isinstance(e, Insert):
                # Skip inserts cuyo bloque es sub-píxel en cualquier dirección
                sx = getattr(e, 'scale_x', 1.0) or 1.0
                sy = getattr(e, 'scale_y', 1.0) or 1.0
                if max(abs(sx), abs(sy)) * sc < 2.0:
                    continue
            lod_ents.append(e)
        if not lod_ents:
            return img
        overlay_ents = lod_ents

        # ── Cache key: viewport + entidades ──────────────────────────────
        _sel_ver_ov = getattr(ctx, 'sel_version', 0)
        _ov_lod = int(math.log2(max(sc, 0.001)))
        _ov_eid = id(ctx.entities)
        overlay_key = (_ov_lod, int(round(ox)), int(round(oy)), _ov_eid, _sel_ver_ov)

        if overlay_key == self._overlay_key and self._overlay_img is not None:
            img.paste(self._overlay_img, mask=self._overlay_mask)
            return img

        # Pan-tolerant cache: si solo cambió ox/oy (mismo lod + entidades + sel)
        # reusar overlay con shift hasta W/3 × H/3 píxeles — evita rebuild en
        # cada frame al hacer zoom/stop sin cambio de lod. Entidades en el borde
        # entrante pueden faltar brevemente (mismo comportamiento que en pan activo).
        if (self._overlay_key is not None and self._overlay_img is not None
                and self._overlay_ox is not None):
            pk = self._overlay_key
            if (pk[0] == _ov_lod and pk[3] == _ov_eid and pk[4] == _sel_ver_ov):
                dx = int(round(ox - self._overlay_ox))
                dy = int(round(oy - self._overlay_oy))
                if abs(dx) <= W // 3 and abs(dy) <= H // 3:
                    if dx == 0 and dy == 0:
                        img.paste(self._overlay_img, mask=self._overlay_mask)
                    else:
                        img.paste(self._overlay_img, (dx, dy), mask=self._overlay_mask)
                    return img

        # ── Dibujar overlay ───────────────────────────────────────────────
        lyr_cache = build_layer_cache(ctx.layers, sc,
                                      ltscale=float(ctx.config.get("ltscale", 1.0)))
        pil       = self._get_pil_helper()

        try:
            from PIL import Image as _PI
            canvas = _PI.new('RGBA', (W, H), (0, 0, 0, 0))
        except Exception:
            return img
        draw = _D.Draw(canvas)

        def _sort_key(e):
            if isinstance(e, Hatch):  return 0
            if isinstance(e, Insert): return 1
            return 2

        # Telemetría por tipo — solo cuando diagnóstico activo (0 overhead en producción)
        _diag_timing = {} if getattr(self, '_cache_diag', False) else None

        for e in sorted(overlay_ents, key=_sort_key):
            if ctx.cancel_ev and ctx.cancel_ev.is_set():
                break
            # Protección contra hatches ultra-densos a zoom alto (evita tiempos >1s)
            if isinstance(e, Hatch) and e.pattern.upper() not in ("SOLID", ""):
                if e.boundary and e.scale and e.scale > 1e-9:
                    _bxs = [p[0] for p in e.boundary]
                    _bys = [p[1] for p in e.boundary]
                    _diag = math.hypot(max(_bxs) - min(_bxs), max(_bys) - min(_bys))
                    if int(_diag / e.scale) + 2 > 2000:
                        continue   # demasiadas líneas → omitir en overlay
            lyr_props = lyr_cache.get(e.layer, (True, False, "#FFFFFF", 1, ()))
            col_hex, lw, dash, skip = resolve_entity_props(
                e, lyr_props, ctx.select_color,
                scale=sc, ltscale=float(ctx.config.get("ltscale", 1.0)))
            if skip:
                continue
            try:
                if _diag_timing is not None:
                    import time as _tm; _t0 = _tm.perf_counter()
                pil._draw_entity(draw, canvas, e,
                                 col_hex, max(1, int(lw)), dash, ctx)
                if _diag_timing is not None:
                    _k = type(e).__name__
                    _diag_timing[_k] = _diag_timing.get(_k, 0.0) + (_tm.perf_counter()-_t0)*1000
            except Exception as exc:
                print(f"[OpenGL overlay] {type(e).__name__} error: {exc}")

        # Guardar telemetría de tiempo por tipo para el panel de diagnóstico
        if _diag_timing is not None:
            self._diag_overlay_timing = _diag_timing

        # Red de seguridad: si el loop fue interrumpido por cancel_ev, el canvas
        # es parcial o vacío. No guardarlo en cache — si lo hiciéramos, el próximo
        # frame haría cache hit con el canvas vacío y los hatches serían invisibles.
        # (Fix A en render() previene llegar aquí con cancelled=True, pero esta
        # guarda cubre el caso donde la cancelación ocurre MID-loop.)
        if ctx.cancel_ev and ctx.cancel_ev.is_set():
            return img  # _overlay_key no se actualiza → próximo render sin cancelar reconstruye

        self._overlay_img  = canvas
        self._overlay_mask = canvas.split()[3]
        self._overlay_key  = overlay_key
        self._overlay_ox   = ox   # posición exacta donde se construyó este overlay
        self._overlay_oy   = oy
        img.paste(canvas, mask=self._overlay_mask)

        return img

    # ── Draw helpers ──────────────────────────────────────────────────────────

    def _draw_lines_np(self, arr: "_np.ndarray") -> None:
        """
        Sube un numpy float32 array y dibuja con GL_LINES.

        PyOpenGL acepta ndarray directamente en glBufferData, evitando la
        conversión ctypes. GL_STATIC_DRAW porque el array viene del cache.
        """
        n_verts = arr.size // 5
        if n_verts == 0:
            return
        _gl.glBindVertexArray(self._vao)
        _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, self._vbo)
        _gl.glBufferData(_gl.GL_ARRAY_BUFFER, arr.nbytes, arr,
                         _gl.GL_STATIC_DRAW)
        _gl.glDrawArrays(_gl.GL_LINES, 0, n_verts)
        _gl.glBindVertexArray(0)

    def _make_vao_for_vbo(self, vbo_h: int) -> int:
        """Crea un VAO dedicado para un VBO VRAM, configurado UNA sola vez.
        Cada VBO tiene su propio VAO → glBindVertexArray es suficiente para dibujar.
        """
        vao_h = int(_gl.glGenVertexArrays(1))
        _gl.glBindVertexArray(vao_h)
        _gl.glBindBuffer(_gl.GL_ARRAY_BUFFER, vbo_h)
        _gl.glEnableVertexAttribArray(0)
        _gl.glVertexAttribPointer(0, 2, _gl.GL_FLOAT, False,
                                  5 * 4, ctypes.c_void_p(0))
        _gl.glEnableVertexAttribArray(1)
        _gl.glVertexAttribPointer(1, 3, _gl.GL_FLOAT, False,
                                  5 * 4, ctypes.c_void_p(2 * 4))
        _gl.glBindVertexArray(0)
        return vao_h

    def _draw_vbo(self, vao_h: int, n_verts: int) -> None:
        """Dibuja desde un VAO/VBO persistente en VRAM.
        Solo glBindVertexArray + glDrawArrays — sin tocar atributos cada frame.
        """
        if n_verts == 0:
            return
        _gl.glBindVertexArray(vao_h)
        _gl.glDrawArrays(_gl.GL_LINES, 0, n_verts)
        _gl.glBindVertexArray(0)

    def _draw_lines_raw(self, verts: list) -> None:
        """Sube una lista Python de vértices y dibuja con GL_LINES (grid/ejes)."""
        if not verts:
            return
        self._draw_lines_np(_np.array(verts, dtype=_np.float32))

    # ── PBO helpers (OP-2) ───────────────────────────────────────────────────

    def _ensure_pbos(self, W: int, H: int) -> None:
        """Crea o recrea los tres PBOs cuando el tamaño del canvas cambia."""
        if self._pbos[0] is not None and self._pbo_W == W and self._pbo_H == H:
            return
        self._delete_pbos()
        size = W * H * 3
        handles = _gl.glGenBuffers(3)
        for h in handles:
            _gl.glBindBuffer(_gl.GL_PIXEL_PACK_BUFFER, h)
            _gl.glBufferData(_gl.GL_PIXEL_PACK_BUFFER, size, None,
                             _gl.GL_STREAM_READ)
        _gl.glBindBuffer(_gl.GL_PIXEL_PACK_BUFFER, 0)
        self._pbos           = list(handles)
        self._pbo_W          = W
        self._pbo_H          = H
        self._pbo_idx        = 0
        self._pbo_warmup     = 0
        self._pbo_force_sync = False

    def _delete_pbos(self) -> None:
        if self._pbos[0] is not None:
            _gl.glDeleteBuffers(3, self._pbos)
            self._pbos           = [None, None, None]
            self._pbo_W          = self._pbo_H = 0
            self._pbo_warmup     = 0
            self._pbo_force_sync = False
            self._pbo_idx        = 0

    def _read_pixels_pbo(self, W: int, H: int) -> "_PILImage.Image":
        """
        OP-2: readback PBO triple-buffered.

        Triple ring 0→1→2→0: lee el PBO de 2 frames atrás.
        El DMA de ese frame tuvo ~1 frame completo para terminar → 0ms stall.

          write_idx: PBO donde va el glReadPixels de este frame
          read_idx = (write_idx + 1) % 3: PBO escrito hace 2 frames

        Primeros 2 frames: fallback sync hasta llenar el ring.
        """
        write_idx = self._pbo_idx
        read_idx  = (write_idx + 1) % 3

        # Paso 1 — glReadPixels async al PBO de escritura (FBO bound).
        _gl.glBindBuffer(_gl.GL_PIXEL_PACK_BUFFER, self._pbos[write_idx])
        _gl.glReadPixels(0, 0, W, H, _gl.GL_RGB, _gl.GL_UNSIGNED_BYTE,
                         ctypes.c_void_p(0))
        _gl.glBindBuffer(_gl.GL_PIXEL_PACK_BUFFER, 0)

        # Avanzar write pointer
        self._pbo_idx = (write_idx + 1) % 3

        if self._pbo_warmup < 2:
            # Primeros 2 frames tras init/resize: llenar el ring antes de leer
            self._pbo_warmup += 1
            return self._read_pixels(W, H)

        if self._pbo_force_sync:
            # 1 frame sync tras viewport change: alinear GL y overlay
            self._pbo_force_sync = False
            return self._read_pixels(W, H)

        # Paso 2 — Mapear PBO lector (frame anterior ya listo en CPU)
        size = W * H * 3
        _gl.glBindBuffer(_gl.GL_PIXEL_PACK_BUFFER, self._pbos[read_idx])
        ptr = _gl.glMapBufferRange(_gl.GL_PIXEL_PACK_BUFFER, 0, size,
                                   _gl.GL_MAP_READ_BIT)
        # Doble comprobación: PyOpenGL a veces devuelve un ctypes wrapper de NULL
        # que es truthy pero cuya dirección real es 0 → segfault en ctypes.string_at.
        _ptr_addr = getattr(ptr, 'value', ptr) if ptr is not None else 0
        if not ptr or not _ptr_addr:
            # Map falló (driver inusual) — fallback sync
            _gl.glBindBuffer(_gl.GL_PIXEL_PACK_BUFFER, 0)
            return self._read_pixels(W, H)

        try:
            buf = ctypes.string_at(ptr, size)
        except Exception:
            # ctypes.string_at con puntero inválido → fallback sync
            try: _gl.glUnmapBuffer(_gl.GL_PIXEL_PACK_BUFFER)
            except Exception: pass
            _gl.glBindBuffer(_gl.GL_PIXEL_PACK_BUFFER, 0)
            return self._read_pixels(W, H)
        _gl.glUnmapBuffer(_gl.GL_PIXEL_PACK_BUFFER)
        _gl.glBindBuffer(_gl.GL_PIXEL_PACK_BUFFER, 0)

        arr = _np.frombuffer(buf, dtype=_np.uint8).reshape(H, W, 3)
        return _PILImage.fromarray(arr, 'RGB')

    # ── Leer píxeles ─────────────────────────────────────────────────────────

    def _read_pixels(self, W: int, H: int) -> "_PILImage.Image":
        """
        Lee el FBO y retorna una PIL.Image RGB.

        OP-3 + OP-1: la proyección Y-down (_proj_matrix) hace que glReadPixels
        row 0 ya sea la fila visual superior → no se necesita flip.
        frombuffer + reshape(H,W,3) + ascontiguousarray es suficiente.
        """
        raw = _gl.glReadPixels(0, 0, W, H, _gl.GL_RGB, _gl.GL_UNSIGNED_BYTE,
                               outputType='raw')
        arr = _np.frombuffer(raw, dtype=_np.uint8).reshape(H, W, 3)
        return _PILImage.fromarray(_np.ascontiguousarray(arr), 'RGB')

    # ── Imagen de fallback ────────────────────────────────────────────────────

    @staticmethod
    def _fallback_image(ctx: Any, reason: str) -> RenderResult:
        """Retorna una imagen negra con mensaje de error si OpenGL falla."""
        try:
            img = _PILImage.new("RGB", (ctx.W, ctx.H), (10, 10, 10))
            from PIL import ImageDraw as _D
            d = _D.Draw(img)
            d.text((10, 10), f"[OpenGL] {reason}", fill=(200, 80, 80))
        except Exception:
            img = None
        return RenderResult(image=img, backend='opengl-fallback')
