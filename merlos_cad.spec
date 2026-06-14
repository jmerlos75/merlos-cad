# merlos_cad.spec — PyInstaller spec para Merlos CAD (MIT edition)
#
# Uso:
#   cd C:\Users\jmerl\Desktop\merlos-cad
#   pyinstaller merlos_cad.spec --distpath C:\builds\merlos-cad-mit --clean --noconfirm
#
# El bundle queda en: C:\builds\merlos-cad-mit\merlos-cad\
# (fuera del proyecto para evitar que Windows Defender bloquee el build).
#
# Para rebuild rápido (sin --clean):
#   pyinstaller merlos_cad.spec --distpath C:\builds\merlos-cad-mit --noconfirm

from pathlib import Path
import importlib.util

def _pkg_dir(name):
    """Devuelve el directorio raíz del paquete instalado."""
    spec = importlib.util.find_spec(name)
    if spec is None:
        raise RuntimeError(f"Paquete no encontrado: {name}")
    return Path(spec.submodule_search_locations[0])

# ─── Rutas ────────────────────────────────────────────────────────────────────
REPO = Path(SPECPATH)   # directorio del .spec == raíz del repo

# ─── Datos a empaquetar ───────────────────────────────────────────────────────
datas = [
    # customtkinter: fuentes, íconos, temas JSON
    (str(_pkg_dir("customtkinter")), "customtkinter"),

    # ezdxf: fuentes de texto DXF, tablas de linetypes, etc.
    (str(_pkg_dir("ezdxf")), "ezdxf"),

    # Assets propios del repo
    (str(REPO / "assets"), "assets"),
    (str(REPO / "config" / "settings.json"), "config"),

    # Documentación MIT que va junto al .exe
    (str(REPO / "CREDITS.md"),   "."),
    (str(REPO / "README.es.md"), "."),
]

# ─── Hidden imports ───────────────────────────────────────────────────────────
# PIL.ImageGrab: importado con `from PIL import ImageGrab` dentro de un try
# ezdxf: algunos sub-módulos se importan dinámicamente por nombre
hiddenimports = [
    "PIL.ImageGrab",
    "PIL._tkinter_finder",
    "ezdxf.addons",
    "ezdxf.addons.drawing",
    "ezdxf.entities",
    "ezdxf.layouts",
    "ezdxf.sections",
    "ezdxf.fonts",
    "ezdxf.fonts.fonts",
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.ttk",
    "_tkinter",
]

# ─── Análisis ─────────────────────────────────────────────────────────────────
a = Analysis(
    [str(REPO / "cad_viewer.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # No usados en la edición MIT
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "matplotlib", "scipy", "pandas",
        "IPython", "notebook", "jupyter",
        "test", "unittest",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,              # onedir: binarios en _internal/
    name="merlos-cad",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                          # UPX puede disparar Windows Defender
    console=False,                      # sin ventana de consola (app GUI)
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=str(REPO / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="merlos-cad",
)
