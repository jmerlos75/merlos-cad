"""
cad/dwg_converter.py
====================
Convierte archivos DWG → DXF usando herramientas disponibles en el sistema.

Prioridad de detección:
  1. AutoCAD COM  (si AutoCAD está instalado — más confiable, nativo)
  2. GNU LibreDWG → dwg2dxf   (GPLv3)
  3. ODA File Converter        (gratuito)
  4. LibreCAD CLI (--convert)  (fallback)

Si ninguna herramienta está disponible lanza DwgConverterError con instrucciones.

Uso:
    from cad.dwg_converter import dwg_a_dxf, herramientas_disponibles

    ruta_dxf = dwg_a_dxf("plano.dwg")        # devuelve ruta .dxf temporal
    disponibles = herramientas_disponibles()  # lista de nombres encontrados
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


# ── Rutas estándar en Windows ────────────────────────────────────────────────

_ODA_PATHS_WIN = [
    r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
    r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
    r"C:\Program Files\ODA File Converter\ODAFileConverter.exe",
    r"C:\Program Files (x86)\ODA File Converter\ODAFileConverter.exe",
]

_LIBRECAD_PATHS_WIN = [
    r"C:\Program Files\LibreCAD\LibreCAD.exe",
    r"C:\Program Files (x86)\LibreCAD\LibreCAD.exe",
]

_DWG2DXF_PATHS_WIN = [
    r"C:\Program Files\LibreDWG\dwg2dxf.exe",
    r"C:\Program Files (x86)\LibreDWG\dwg2dxf.exe",
    r"C:\Program Files\LibreCAD\dwg2dxf.exe",
    r"C:\Program Files (x86)\LibreCAD\dwg2dxf.exe",
]

# dxf2dwg = herramienta de LibreDWG que ESCRIBE DWG nativo (sin AutoCAD)
_DXF2DWG_PATHS_WIN = [
    r"C:\Program Files\LibreDWG\dxf2dwg.exe",
    r"C:\Program Files (x86)\LibreDWG\dxf2dwg.exe",
]


def _bin_dir() -> str:
    """Carpeta 'bin' empaquetada con la app, donde puede vivir dxf2dwg.exe.

    Permite distribuir el conversor LibreDWG junto a la app sin instalar nada
    ni depender de AutoCAD. Estructura esperada:
        Estudio Merlos AI/bin/dxf2dwg.exe
    """
    # cad/dwg_converter.py  →  raíz de la app es el padre de 'cad'
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(raiz, "bin")


# Inyectar la carpeta bin/ empaquetada al frente de las rutas de búsqueda,
# para que un dxf2dwg.exe distribuido con la app tenga prioridad.
_BIN_DIR = _bin_dir()
_DXF2DWG_PATHS_WIN.insert(0, os.path.join(_BIN_DIR, "dxf2dwg.exe"))
_DWG2DXF_PATHS_WIN.insert(0, os.path.join(_BIN_DIR, "dwg2dxf.exe"))

# AcSaveAsType — valores DXF del enum de AutoCAD (probados en orden descendente)
# R2010=29, R2007=26, R2004=24, R2000=16, R14=9, R12=1
_ACAD_DXF_FORMATS = (29, 26, 24, 16, 9, 1)


class DwgConverterError(RuntimeError):
    """No se pudo convertir el DWG."""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _find_exe(name: str, extra_paths: list[str]) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for p in extra_paths:
        if os.path.isfile(p):
            return p
    return None


def _autocad_registrado() -> bool:
    """Verifica (rápido, sin lanzar AutoCAD) si está instalado via registro COM."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "AutoCAD.Application"):
            return True
    except Exception:
        return False


# ── Detección ────────────────────────────────────────────────────────────────

def herramientas_disponibles() -> list[str]:
    """
    Devuelve los nombres de las herramientas de conversión DWG→DXF disponibles.
    Posibles valores: "autocad", "dwg2dxf", "oda", "librecad"
    """
    disponibles = []
    if _autocad_registrado():
        disponibles.append("autocad")
    if _find_exe("dwg2dxf", _DWG2DXF_PATHS_WIN):
        disponibles.append("dwg2dxf")
    if _find_exe("ODAFileConverter", _ODA_PATHS_WIN):
        disponibles.append("oda")
    if _find_exe("librecad", _LIBRECAD_PATHS_WIN):
        disponibles.append("librecad")
    return disponibles


# ── Convertidores ─────────────────────────────────────────────────────────────

def _convertir_con_autocad(ruta_dwg: str, ruta_dxf: str) -> bool:
    """
    Usa AutoCAD COM para abrir el DWG y guardarlo como DXF.
    Reutiliza una instancia abierta o lanza una silenciosa.
    AutoCAD permanece cerrado si no estaba abierto antes.
    """
    try:
        import win32com.client
        import pythoncom
    except ImportError:
        return False

    acad = None
    doc  = None
    abrio_nueva_instancia = False

    try:
        pythoncom.CoInitialize()

        # Intentar conectar a instancia ya abierta primero
        try:
            acad = win32com.client.GetActiveObject("AutoCAD.Application")
        except Exception:
            acad = win32com.client.Dispatch("AutoCAD.Application")
            abrio_nueva_instancia = True
            acad.Visible = False   # silencioso

        doc = acad.Documents.Open(os.path.abspath(ruta_dwg))

        # Intentar guardar como DXF en distintas versiones de formato
        guardado = False
        for fmt in _ACAD_DXF_FORMATS:
            try:
                doc.SaveAs(os.path.abspath(ruta_dxf), fmt)
                guardado = True
                break
            except Exception:
                continue

        return guardado and os.path.isfile(ruta_dxf)

    except Exception as exc:
        print(f"[WARN] AutoCAD COM falló: {exc}")
        return False

    finally:
        # Cerrar el documento sin guardar cambios en el DWG original
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        # Si lanzamos AutoCAD solo para esto, cerrarlo
        if abrio_nueva_instancia and acad is not None:
            try:
                acad.Quit()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _convertir_con_dwg2dxf(ruta_dwg: str, ruta_dxf: str) -> bool:
    """GNU LibreDWG — dwg2dxf <entrada.dwg> -o <salida.dxf>"""
    exe = _find_exe("dwg2dxf", _DWG2DXF_PATHS_WIN)
    if not exe:
        return False
    try:
        r = subprocess.run(
            [exe, ruta_dwg, "-o", ruta_dxf],
            capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0 and os.path.isfile(ruta_dxf)
    except (subprocess.TimeoutExpired, OSError):
        return False


def _convertir_con_oda(ruta_dwg: str, ruta_dxf: str) -> bool:
    """
    ODA File Converter CLI:
      ODAFileConverter <dir_in> <dir_out> <ver_in> <ver_out> <recurse> <audit> [filtro]
    """
    exe = _find_exe("ODAFileConverter", _ODA_PATHS_WIN)
    if not exe:
        return False
    try:
        dir_in  = os.path.dirname(os.path.abspath(ruta_dwg))
        nombre  = os.path.basename(ruta_dwg)
        with tempfile.TemporaryDirectory() as dir_out:
            subprocess.run(
                [exe, dir_in, dir_out, "ACAD2018", "DXF", "0", "1", nombre],
                capture_output=True, text=True, timeout=120,
            )
            origen = os.path.join(dir_out, Path(nombre).stem + ".dxf")
            if os.path.isfile(origen):
                shutil.copy2(origen, ruta_dxf)
                return True
        return False
    except (subprocess.TimeoutExpired, OSError):
        return False


def _convertir_con_librecad(ruta_dwg: str, ruta_dxf: str) -> bool:
    """LibreCAD CLI --convert-to dxf (disponible en algunas versiones)."""
    exe = _find_exe("librecad", _LIBRECAD_PATHS_WIN)
    if not exe:
        return False
    try:
        subprocess.run(
            [exe, "--convert-to", "dxf", ruta_dwg],
            capture_output=True, text=True, timeout=60,
        )
        auto_dxf = Path(ruta_dwg).with_suffix(".dxf")
        if auto_dxf.is_file():
            if str(auto_dxf) != ruta_dxf:
                shutil.move(str(auto_dxf), ruta_dxf)
            return True
        return False
    except (subprocess.TimeoutExpired, OSError):
        return False


# ── Función principal ────────────────────────────────────────────────────────

def dwg_a_dxf(ruta_dwg: str, directorio_temp: str | None = None) -> str:
    """
    Convierte un DWG a DXF usando la primera herramienta disponible.

    Prioridad: AutoCAD COM → dwg2dxf → ODA → LibreCAD

    Returns:
        Ruta al archivo .dxf temporal generado.

    Raises:
        DwgConverterError si ninguna herramienta está disponible o todas fallan.
    """
    ruta_dwg = os.path.abspath(ruta_dwg)
    if not os.path.isfile(ruta_dwg):
        raise DwgConverterError(f"Archivo no encontrado: {ruta_dwg}")

    if directorio_temp is None:
        directorio_temp = tempfile.gettempdir()
    ruta_dxf = os.path.join(directorio_temp, Path(ruta_dwg).stem + "_importado.dxf")

    for nombre, fn in [
        ("AutoCAD COM",              _convertir_con_autocad),
        ("GNU LibreDWG (dwg2dxf)",   _convertir_con_dwg2dxf),
        ("ODA File Converter",        _convertir_con_oda),
        ("LibreCAD CLI",              _convertir_con_librecad),
    ]:
        if fn(ruta_dwg, ruta_dxf):
            return ruta_dxf

    raise DwgConverterError(
        "No se encontró ninguna herramienta para convertir DWG → DXF.\n\n"
        "Opciones (instale una):\n\n"
        "  • AutoCAD (ya lo tiene instalado)\n"
        "    Requiere: pip install pywin32\n"
        "    Luego el importador usa AutoCAD automáticamente.\n\n"
        "  • GNU LibreDWG (código abierto GPLv3):\n"
        "    https://github.com/LibreDWG/libredwg/releases\n"
        "    Ponga dwg2dxf.exe en el PATH o en C:\\Program Files\\LibreDWG\\\n\n"
        "  • ODA File Converter (gratuito):\n"
        "    https://www.opendesign.com/guestfiles/oda_file_converter\n\n"
        "  • Alternativa inmediata: en AutoCAD → Guardar como… → DXF R2018\n"
        "    y luego importe el .dxf al visor."
    )


def mensaje_instalacion() -> str:
    """Texto de ayuda para mostrar al usuario si no hay conversores."""
    return (
        "Para importar DWG directamente instale pywin32:\n"
        "  pip install pywin32\n\n"
        "El visor usará su AutoCAD instalado automáticamente.\n\n"
        "O bien: en AutoCAD → Guardar como… → DXF R2018"
    )
