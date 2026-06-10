#!/usr/bin/env python
"""
crear_acceso_directo.py
=======================
Crea el icono y el acceso directo en el escritorio para
"Estudio Merlos AI — Hub Central" (main.py).

Ejecutar UNA vez:
    python crear_acceso_directo.py
"""
from __future__ import annotations
import os
import sys

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
MAIN_PY   = os.path.join(BASE_DIR, "main.py")
ICON_PATH = os.path.join(BASE_DIR, "estudio_merlos_ai.ico")
PYTHONW   = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
# Usar siempre la ruta real del escritorio (OneDrive la puede mover)
def _desktop_real() -> str:
    try:
        import ctypes, ctypes.wintypes
        buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(0, 0, 0, 0, buf)
        return buf.value
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")

DESKTOP   = _desktop_real()
SHORTCUT  = os.path.join(DESKTOP, "Estudio Merlos AI.lnk")

# ── Colores del hub ──────────────────────────────────────────────────────
COLOR_BG   = (15,  23, 42)   # #0F172A  — fondo oscuro
COLOR_BLUE = (37,  99, 235)  # #2563EB  — azul principal
COLOR_TEXT = (248, 250, 252) # #F8FAFC  — blanco


# ════════════════════════════════════════════════════════════════════════
# 1. GENERAR ICONO  (.ico  con 6 tamaños)
# ════════════════════════════════════════════════════════════════════════

def _generar_icono() -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  [!] Pillow no disponible — saltando creacion de icono.")
        return False

    SIZES = [256, 128, 64, 48, 32, 16]
    imagenes: list[Image.Image] = []

    for sz in SIZES:
        img  = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # ── Fondo: cuadrado redondeado azul con sombra sutil ────────
        m  = max(1, sz // 16)          # margen
        r  = max(2, sz // 5)           # radio esquinas

        # Sombra (desplazada 1 píxel, semitransparente)
        if sz >= 32:
            sombra = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sombra)
            sd.rounded_rectangle(
                [m + 2, m + 2, sz - m + 2, sz - m + 2],
                radius=r, fill=(0, 0, 0, 80),
            )
            img = Image.alpha_composite(img, sombra)
            draw = ImageDraw.Draw(img)

        draw.rounded_rectangle(
            [m, m, sz - m, sz - m],
            radius=r, fill=(*COLOR_BLUE, 255),
        )

        # ── Letra "EM" centrada ──────────────────────────────────────
        texto     = "EM"
        font_sz   = max(6, int(sz * 0.36))
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont

        # Intentar fuentes Bold disponibles en Windows
        for nombre_fuente in ["arialbd.ttf", "calibrib.ttf", "segoeuib.ttf",
                              "verdanab.ttf", "arial.ttf"]:
            try:
                font = ImageFont.truetype(nombre_fuente, font_sz)
                break
            except Exception:
                pass
        else:
            font = ImageFont.load_default()

        bbox   = draw.textbbox((0, 0), texto, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (sz - tw) / 2 - bbox[0]
        y = (sz - th) / 2 - bbox[1] - max(0, sz // 20)  # leve ajuste óptico

        draw.text((x, y), texto, font=font, fill=(*COLOR_TEXT, 255))

        imagenes.append(img)

    # ── Guardar .ico ─────────────────────────────────────────────────
    imagenes[0].save(
        ICON_PATH, format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=imagenes[1:],
    )
    print(f"  Icono creado: {ICON_PATH}")
    return True


# ════════════════════════════════════════════════════════════════════════
# 2. CREAR ACCESO DIRECTO  (.lnk  en el escritorio)
# ════════════════════════════════════════════════════════════════════════

def _crear_lnk() -> None:
    import win32com.client

    shell    = win32com.client.Dispatch("WScript.Shell")
    acceso   = shell.CreateShortcut(SHORTCUT)

    # Usar pythonw.exe → sin ventana de consola al abrir
    if os.path.exists(PYTHONW):
        acceso.TargetPath      = PYTHONW
        acceso.Arguments       = f'"{MAIN_PY}"'
    else:
        # Fallback: python.exe normal
        acceso.TargetPath      = sys.executable
        acceso.Arguments       = f'"{MAIN_PY}"'

    acceso.WorkingDirectory    = BASE_DIR
    acceso.Description         = "Estudio Merlos AI — Hub Central"
    acceso.WindowStyle         = 1           # ventana normal

    if os.path.exists(ICON_PATH):
        acceso.IconLocation    = ICON_PATH
    else:
        # Fallback: icono del propio Python
        acceso.IconLocation    = sys.executable + ",0"

    acceso.Save()
    print(f"  Acceso directo: {SHORTCUT}")


# ════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════

def main() -> None:
    print()
    print("=" * 54)
    print("  Estudio Merlos AI — Creando acceso directo")
    print("=" * 54)
    print()

    # 1. Icono
    print("[ 1 / 2 ]  Generando icono...")
    ok_ico = _generar_icono()
    if not ok_ico and not os.path.exists(ICON_PATH):
        print("  Continuando sin icono personalizado.")

    # 2. Acceso directo
    print("[ 2 / 2 ]  Creando acceso directo en el escritorio...")
    try:
        _crear_lnk()
    except Exception as e:
        print(f"  [!] Error al crear el acceso directo: {e}")
        print("  Asegurate de tener pywin32 instalado: pip install pywin32")
        return

    print()
    print("  Listo. Busca el icono 'Estudio Merlos AI' en tu escritorio.")
    print("  Doble clic para abrir el hub sin consola.")
    print()


if __name__ == "__main__":
    main()
    input("  Presiona Enter para cerrar...")
