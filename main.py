#!/usr/bin/env python
"""
main.py  —  Hub Central · Estudio Merlos AI
============================================
Punto de entrada único. Lanza cada herramienta en su propio proceso
para evitar conflictos de COM / CTk roots.

Uso:
    python main.py
"""
from __future__ import annotations

import json
import os
import sys
import subprocess

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "settings.json")
sys.path.insert(0, BASE_DIR)

PYTHON = sys.executable

# ── Paleta ──────────────────────────────────────────────────────────────
BG      = "#0F172A"
BG_PAN  = "#1E293B"
BG_CARD = "#334155"
TEXT    = "#F8FAFC"
TEXT2   = "#94A3B8"
BORDER  = "#475569"

C_BLUE   = "#2563EB";  C_BLUE_H   = "#1D4ED8"
C_PURPLE = "#7C3AED";  C_PURPLE_H = "#6D28D9"
C_GREEN  = "#065F46";  C_GREEN_H  = "#047857"
C_AMBER  = "#B45309";  C_AMBER_H  = "#92400E"
C_TEAL   = "#0F766E";  C_TEAL_H   = "#0D9488"
C_SLATE  = "#334155";  C_SLATE_H  = "#475569"
C_ROSE   = "#9F1239";  C_ROSE_H   = "#881337"

# ── Definición de herramientas ───────────────────────────────────────────
HERRAMIENTAS: list[dict] = [
    {
        "icono":       "LT",
        "nombre":      "Terreno del Lote",
        "subtitulo":   "Capa 0 — Derrotero y normativa",
        "descripcion": "Ingresá el derrotero (azimut, rumbo o\ncoordenadas), retiros y cobertura.",
        "color": C_TEAL,   "hover": C_TEAL_H,
        "script": "terreno_app.py", "args": [],
    },
    {
        "icono":       "PA",
        "nombre":      "Programa Arquitectónico",
        "subtitulo":   "Capa 1 — Definición de áreas",
        "descripcion": "Define el programa antes del diseño:\nperfiles, áreas normativas, dimensiones.",
        "color": C_GREEN,  "hover": C_GREEN_H,
        "script": "dwg_to_sle.py", "args": ["--programa"],
        "tiene_brujula": True,
    },
    {
        "icono":       "EM",
        "nombre":      "Estudio Merlos AI",
        "subtitulo":   "AutoCAD + IA · Diseño generativo",
        "descripcion": "Motor principal: genera plantas en AutoCAD\nusando IA. Requiere AutoCAD abierto.",
        "color": C_BLUE,   "hover": C_BLUE_H,
        "script": "app.py", "args": [],
    },
    {
        "icono":       "D0",
        "nombre":      "Diseño — Paso 0",
        "subtitulo":   "Programa · Diagrama funcional · Reglas Merlos",
        "descripcion": "Define zonas, orientación y relaciones\nantes de dibujar una sola pared.",
        "color": C_GREEN,  "hover": C_GREEN_H,
        "script": "app.py", "args": ["--paso0"],
    },
    {
        "icono":       "DW",
        "nombre":      "DWG → SLE Extractor",
        "subtitulo":   "Extrae recintos de planos existentes",
        "descripcion": "Lee un DWG abierto en AutoCAD y extrae\nlos recintos al Spatial Learning Engine.\n⚠ Requiere AutoCAD abierto con el plano.",
        "color": C_PURPLE, "hover": C_PURPLE_H,
        "script": "dwg_to_sle.py", "args": [],
    },
    {
        "icono":       "CV",
        "nombre":      "CAD Visor",
        "subtitulo":   "Visor y editor CAD nativo · v1",
        "descripcion": "Editor vectorial propio: líneas, polilíneas,\ntextos, capas, snap. Exporta DXF y PNG.",
        "color": C_ROSE,   "hover": C_ROSE_H,
        "script": "cad_viewer.py", "args": [],
    },
]

# Puntos cardinales disponibles
CARDINALES = [
    ("N", "norte"),
    ("S", "sur"),
    ("E", "este"),
    ("O", "oeste"),
]


# ── Config persistida ────────────────────────────────────────────────────

def _leer_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _guardar_norte(valor: str) -> None:
    cfg = _leer_config()
    cfg["norte_lote"] = valor
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)


def _norte_guardado() -> str:
    return _leer_config().get("norte_lote", "norte")


# ── Lanzador ────────────────────────────────────────────────────────────

def lanzar(script: str, args: list[str]) -> None:
    """Abre la herramienta en un proceso independiente (no bloquea el hub)."""
    ruta = os.path.join(BASE_DIR, script)
    subprocess.Popen([PYTHON, ruta, *args], cwd=BASE_DIR)


def _stats_sle() -> dict:
    try:
        from sle.core.memory import Memoria
        return Memoria().estadisticas()
    except Exception:
        return {}


# ── Widget brújula ──────────────────────────────────────────────────────

def _crear_brujula(parent, var_norte, color_activo: str) -> None:
    """
    Agrega una brújula compacta al widget parent.
    Muestra N/S/E/O en forma de rosa; el seleccionado se ilumina.
    Actualiza var_norte (StringVar) y guarda en config al cambiar.
    """
    import customtkinter as ctk

    marco = ctk.CTkFrame(parent, fg_color="transparent")
    marco.pack(fill="x", padx=18, pady=(4, 4))

    ctk.CTkLabel(marco, text="Norte del lote:",
                 font=ctk.CTkFont(size=11, weight="bold"),
                 text_color=TEXT2, anchor="w",
                 ).pack(side="left", padx=(0, 10))

    # 4 botones en una sola fila horizontal: O · N · E · S
    btns: dict[str, ctk.CTkButton] = {}

    lbl_estado = ctk.CTkLabel(marco, text="",
                              font=ctk.CTkFont(size=11, weight="bold"),
                              text_color=color_activo, anchor="w", width=80)

    def _seleccionar(val: str) -> None:
        var_norte.set(val)
        _guardar_norte(val)
        label_map = {"norte": "Norte", "sur": "Sur",
                     "este": "Este", "oeste": "Oeste"}
        lbl_estado.configure(text=label_map.get(val, val.capitalize()))
        for k, b in btns.items():
            b.configure(
                fg_color=color_activo if k == val else BG_CARD,
                text_color=TEXT  if k == val else TEXT2,
            )

    for letra, val in [("O", "oeste"), ("N", "norte"), ("E", "este"), ("S", "sur")]:
        b = ctk.CTkButton(
            marco, text=letra, width=30, height=30,
            fg_color=BG_CARD, hover_color=color_activo,
            text_color=TEXT2, font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=8,
            command=lambda v=val: _seleccionar(v),
        )
        b.pack(side="left", padx=2)
        btns[val] = b

    lbl_estado.pack(side="left", padx=(10, 0))

    # Aplicar estado inicial
    _seleccionar(var_norte.get())


# ── GUI ──────────────────────────────────────────────────────────────────

def main() -> None:
    import customtkinter as ctk

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("Estudio Merlos AI — Hub")
    root.geometry("980x780")
    root.minsize(860, 680)
    root.resizable(True, True)
    root.configure(fg_color=BG)

    # Orientación global — persiste entre sesiones
    var_norte = ctk.StringVar(value=_norte_guardado())

    # ── Header ─────────────────────────────────────────────────────
    hdr = ctk.CTkFrame(root, fg_color="transparent")
    hdr.pack(fill="x", padx=32, pady=(26, 6))

    logo = ctk.CTkFrame(hdr, width=54, height=54, fg_color=C_BLUE, corner_radius=14)
    logo.pack(side="left", padx=(0, 18))
    logo.pack_propagate(False)
    ctk.CTkLabel(logo, text="EM",
                 font=ctk.CTkFont(size=20, weight="bold"),
                 text_color=TEXT).place(relx=0.5, rely=0.5, anchor="center")

    titulo_col = ctk.CTkFrame(hdr, fg_color="transparent")
    titulo_col.pack(side="left", fill="y")
    ctk.CTkLabel(titulo_col, text="Estudio Merlos AI",
                 font=ctk.CTkFont(size=22, weight="bold"),
                 text_color=TEXT, anchor="w").pack(anchor="w")
    ctk.CTkLabel(titulo_col, text="Hub Central — selecciona la herramienta",
                 font=ctk.CTkFont(size=12), text_color=TEXT2, anchor="w").pack(anchor="w")

    stats = _stats_sle()
    if stats.get("n_proyectos", 0) > 0:
        badge = (
            f"SLE  ·  {stats['n_proyectos']} proyectos  ·  "
            f"Score {stats['score_promedio']}/100  ·  "
            f"{stats['n_aprobados']} aprobados"
        )
    else:
        badge = "SLE · sin proyectos aún"

    ctk.CTkLabel(hdr, text=badge,
                 font=ctk.CTkFont(size=11, weight="bold"),
                 text_color="#A78BFA", fg_color="#2D1B69",
                 corner_radius=8, padx=12, pady=5,
                 ).pack(side="right")

    ctk.CTkFrame(root, height=1, fg_color=BORDER).pack(fill="x", padx=32, pady=(6, 16))

    # ── Grid 3+2 (fila 0: 3 cols, fila 1: 2 cols centradas) ──────────
    grid = ctk.CTkFrame(root, fg_color="transparent")
    grid.pack(fill="both", expand=True, padx=28)
    grid.columnconfigure((0, 1, 2), weight=1, uniform="col")
    grid.rowconfigure(0, weight=1)
    grid.rowconfigure(1, weight=1)

    # Mapeo: 6 tools → grilla 3×2 completa
    _POS = [(0,0),(0,1),(0,2),(1,0),(1,1),(1,2)]

    for idx, h in enumerate(HERRAMIENTAS):
        fila, col = _POS[idx]
        tiene_brujula = h.get("tiene_brujula", False)

        card = ctk.CTkFrame(grid, fg_color=BG_PAN, corner_radius=16,
                            border_width=1, border_color=BORDER)
        card.grid(row=fila, column=col, padx=9, pady=9, sticky="nsew")
        card.columnconfigure(1, weight=1)

        # ── Icono + nombre ──
        ico = ctk.CTkFrame(card, width=46, height=46,
                           fg_color=h["color"], corner_radius=12)
        ico.grid(row=0, column=0, padx=(18, 12), pady=(16, 2), sticky="nw")
        ico.grid_propagate(False)
        ctk.CTkLabel(ico, text=h["icono"],
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=TEXT).place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(card, text=h["nombre"],
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=TEXT, anchor="w",
                     ).grid(row=0, column=1, sticky="sw", padx=(0, 16), pady=(16, 0))

        ctk.CTkLabel(card, text=h["subtitulo"],
                     font=ctk.CTkFont(size=11), text_color=h["color"], anchor="w",
                     ).grid(row=1, column=0, columnspan=2,
                     padx=18, pady=(0, 2), sticky="w")

        ctk.CTkLabel(card, text=h["descripcion"],
                     font=ctk.CTkFont(size=11), text_color=TEXT2,
                     justify="left", anchor="nw", wraplength=310,
                     ).grid(row=2, column=0, columnspan=2,
                     padx=18, pady=(2, 4), sticky="nw")

        # ── Brújula (solo card PA) ──────────────────────────────
        if tiene_brujula:
            sep = ctk.CTkFrame(card, height=1, fg_color=BORDER)
            sep.grid(row=3, column=0, columnspan=2, sticky="ew",
                     padx=14, pady=(2, 2))

            bruj_cont = ctk.CTkFrame(card, fg_color="transparent")
            bruj_cont.grid(row=4, column=0, columnspan=2, sticky="ew")
            _crear_brujula(bruj_cont, var_norte, h["color"])

            fila_btn = 5
        else:
            fila_btn = 3

        # ── Botón Abrir ──────────────────────────────────────────
        if tiene_brujula:
            def _abrir_pa(s=h["script"], a=h["args"]):
                lanzar(s, a + ["--norte", var_norte.get()])
            cmd = _abrir_pa
        else:
            cmd = lambda s=h["script"], a=h["args"]: lanzar(s, a)

        ctk.CTkButton(
            card,
            text=f"Abrir  {h['nombre'].split()[0]} →",
            height=34,
            fg_color=h["color"],
            hover_color=h["hover"],
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=9,
            command=cmd,
        ).grid(row=fila_btn, column=0, columnspan=2,
               padx=18, pady=(2, 12), sticky="ew")

    # ── Footer ────────────────────────────────────────────────────
    ctk.CTkLabel(root,
                 text="Estudio Merlos AI  v0.1  ·  Costa Rica  ·  python main.py",
                 font=ctk.CTkFont(size=10), text_color=BORDER,
                 ).pack(pady=(6, 10))

    root.mainloop()


if __name__ == "__main__":
    main()
