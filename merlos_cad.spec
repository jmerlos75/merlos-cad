# merlos_cad.spec — PyInstaller spec para Merlos CAD (MIT edition)
#
# Uso:
#   cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos CAD OSS"
#   pyinstaller merlos_cad.spec --distpath C:\builds\merlos-cad-mit --clean --noconfirm
#
# El bundle queda en: C:\builds\merlos-cad-mit\merlos-cad\
# (fuera del proyecto para evitar que Windows Defender bloquee el build).

from pathlib import Path
import importlib.util

def _pkg_dir(name):
    spec = importlib.util.find_spec(name)
    if spec is None:
        raise RuntimeError(f"Paquete no encontrado: {name}")
    return Path(spec.submodule_search_locations[0])

# ─── Rutas ────────────────────────────────────────────────────────────────────
REPO = Path(SPECPATH)   # raíz del repo OSS

# ─── Datos a empaquetar ───────────────────────────────────────────────────────
datas = [
    (str(_pkg_dir("customtkinter")), "customtkinter"),
    (str(_pkg_dir("ezdxf")),         "ezdxf"),
    (str(REPO / "config" / "settings.json"), "config"),
    (str(REPO / "CREDITS.md"),   "."),
    (str(REPO / "README.md"),    "."),
    (str(REPO / "README.es.md"), "."),
]

# ─── Hidden imports ───────────────────────────────────────────────────────────
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
    [str(REPO / "main.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "anthropic", "openai", "pypdf", "docx", "python_docx",
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
    exclude_binaries=True,
    name="merlos-cad",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
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
