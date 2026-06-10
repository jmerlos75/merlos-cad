"""
terreno_app.py
==============
Editor de derrotero del terreno (lote) — Estudio Merlos AI.

Permite ingresar el derrotero en tres formatos:
  AZIMUT      : azimut° minutos'  distancia m cm
  RUMBO       : N/S grados° minutos' E/W  distancia m cm
  COORDENADAS : ESTE / NORTE (CR-SIRGAS-CRTM05 u otro)

Calcula: polígono, área, error de cierre, huella máxima según retiros/cobertura.
Guarda/carga en JSON.  Puede lanzarse desde main.py (hub) o directo.
"""
from __future__ import annotations
import json, math, os, sys, tkinter as tk
from tkinter import filedialog, messagebox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from sle.core.terreno import (
    vertices_desde_lineas, area_poligono,
    error_cierre as _error_cierre, perimetro,
    crtm05_a_wgs84, wgs84_a_crtm05, norte_lote_auto,
)

# ─── Paleta ─────────────────────────────────────────────────────────────────
BG      = "#0F172A"
BG_PAN  = "#1E293B"
BG_CARD = "#334155"
TEXT    = "#F8FAFC"
TEXT2   = "#94A3B8"
BORDER  = "#475569"
ACCENT  = "#2563EB"
C_GREEN = "#065F46"
SUCCESS = "#16A34A"
WARNING = "#EAB308"
C_ERROR = "#DC2626"


# ════════════════════════════════════════════════════════════════════════
# HELPERS DE EXTRACCIÓN DE DATOS DE FILAS
# ════════════════════════════════════════════════════════════════════════

def _float(s: str, default: float = 0.0) -> float:
    try:
        return float(s.strip()) if s.strip() else default
    except (ValueError, AttributeError):
        return default


def _linea_de_fila(f: dict, modo: str) -> dict:
    """Extrae un dict de línea desde el dict de widgets de una fila."""
    ln: dict = {
        "dist_m":  _float(f["var_dist_m"].get()),
        "dist_cm": _float(f["var_dist_cm"].get()),
        "frente":   f["var_frente"].get(),
        "desc":     f["var_desc"].get(),
    }
    if modo == "azimut":
        ln["az_g"] = _float(f["var_az_g"].get())
        ln["az_m"] = _float(f["var_az_m"].get())
    elif modo == "rumbo":
        ln["ns"]    = f["var_ns"].get()
        ln["rum_g"] = _float(f["var_rum_g"].get())
        ln["rum_m"] = _float(f["var_rum_m"].get())
        ln["ew"]    = f["var_ew"].get()
    else:  # coordenadas
        ln["este"]  = _float(f["var_este"].get())
        ln["norte"] = _float(f["var_norte"].get())
    return ln


def _lineas_de_filas(filas: list[dict], modo: str) -> list[dict]:
    return [_linea_de_fila(f, modo) for f in filas]


# ════════════════════════════════════════════════════════════════════════
# GUI PRINCIPAL
# ════════════════════════════════════════════════════════════════════════

def abrir_terreno(parent=None):
    """Abre la ventana del editor de terreno (llamable desde el hub)."""
    import customtkinter as ctk

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    if parent:
        win = ctk.CTkToplevel(parent)
    else:
        win = ctk.CTk()

    win.title("Terreno del Lote — Estudio Merlos AI")
    win.geometry("1180x780")
    win.minsize(980, 640)
    win.configure(fg_color=BG)

    # ── Estado ──────────────────────────────────────────────────────────
    var_modo        = ctk.StringVar(value="azimut")
    var_nombre      = ctk.StringVar(value="Terreno sin nombre")
    var_area        = ctk.StringVar(value="—  m²")
    var_perim       = ctk.StringVar(value="—  m")
    var_cierre      = ctk.StringVar(value="—")
    var_huella      = ctk.StringVar(value="—  m²")
    var_norte_det   = ctk.StringVar(value="—")          # norte detectado
    var_inicio_este  = ctk.StringVar(value="")          # punto 1 CRTM05 (azimut/rumbo)
    var_inicio_norte = ctk.StringVar(value="")
    _wgs84_cache: list[list[tuple]] = [[]]              # cache de puntos WGS84
    filas: list[dict] = []
    _timer = [None]

    # ── Header ──────────────────────────────────────────────────────────
    hdr = ctk.CTkFrame(win, fg_color="transparent")
    hdr.pack(fill="x", padx=24, pady=(18, 6))

    logo = ctk.CTkFrame(hdr, width=48, height=48, fg_color=C_GREEN, corner_radius=12)
    logo.pack(side="left", padx=(0, 14))
    logo.pack_propagate(False)
    ctk.CTkLabel(logo, text="LT",
                 font=ctk.CTkFont(size=17, weight="bold"),
                 text_color=TEXT).place(relx=0.5, rely=0.5, anchor="center")

    col_tit = ctk.CTkFrame(hdr, fg_color="transparent")
    col_tit.pack(side="left")
    ctk.CTkLabel(col_tit, text="Terreno del Lote",
                 font=ctk.CTkFont(size=20, weight="bold"),
                 text_color=TEXT, anchor="w").pack(anchor="w")
    ctk.CTkLabel(col_tit, text="Ingresá el derrotero — Azimut · Rumbo · Coordenadas",
                 font=ctk.CTkFont(size=11), text_color=TEXT2, anchor="w").pack(anchor="w")

    # Nombre del terreno
    ctk.CTkEntry(hdr, textvariable=var_nombre, width=240, height=30,
                 font=ctk.CTkFont(size=12), fg_color=BG_CARD,
                 placeholder_text="Nombre del lote / proyecto"
                 ).pack(side="right", padx=4)
    ctk.CTkLabel(hdr, text="Lote:", font=ctk.CTkFont(size=11),
                 text_color=TEXT2).pack(side="right")

    ctk.CTkFrame(win, height=1, fg_color=BORDER).pack(fill="x", padx=24, pady=(4, 8))

    # ── Body: izquierda (tabla) + derecha (canvas) ───────────────────────
    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=18, pady=0)
    body.columnconfigure(0, weight=55)
    body.columnconfigure(1, weight=45)
    body.rowconfigure(0, weight=1)

    # ══ Panel izquierdo ════════════════════════════════════════════════
    left = ctk.CTkFrame(body, fg_color=BG_PAN, corner_radius=14)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
    left.columnconfigure(0, weight=1)
    # row 0: modo_bar  |  row 1: inicio_panel  |  row 2: cabecera
    # row 3: tabla (expand)  |  row 4: botones
    left.rowconfigure(3, weight=1)

    # — Selector de modo —
    modo_bar = ctk.CTkFrame(left, fg_color="transparent")
    modo_bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))

    ctk.CTkLabel(modo_bar, text="Tipo de derrotero:",
                 font=ctk.CTkFont(size=12, weight="bold"),
                 text_color=TEXT2).pack(side="left", padx=(0, 10))

    _btn_modos: dict[str, ctk.CTkButton] = {}

    def _sel_modo(m: str):
        var_modo.set(m)
        for k, b in _btn_modos.items():
            b.configure(fg_color=ACCENT if k == m else BG_CARD,
                        text_color=TEXT  if k == m else TEXT2)
        # Mostrar panel CRTM05 solo en azimut/rumbo
        if m == "coordenadas":
            inicio_panel.grid_remove()
        else:
            inicio_panel.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 2))
        _actualizar_cabecera()
        for f in filas:
            _mostrar_widgets(f, m)
        _recalcular()

    for lbl, m in [("AZIMUT", "azimut"), ("RUMBO", "rumbo"), ("COORDENADAS", "coordenadas")]:
        b = ctk.CTkButton(modo_bar, text=lbl, width=110, height=28,
                          corner_radius=7,
                          font=ctk.CTkFont(size=11, weight="bold"),
                          fg_color=ACCENT if m == "azimut" else BG_CARD,
                          text_color=TEXT if m == "azimut" else TEXT2,
                          hover_color=ACCENT,
                          command=lambda x=m: _sel_modo(x))
        b.pack(side="left", padx=2)
        _btn_modos[m] = b

    # Panel "Punto 1 en CRTM05" — row 1, visible solo en azimut/rumbo
    inicio_panel = ctk.CTkFrame(left, fg_color=BG_CARD, corner_radius=8)
    inicio_panel.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 2))

    ctk.CTkLabel(inicio_panel, text="Punto 1 (CR-SIRGAS-CRTM05):",
                 font=ctk.CTkFont(size=10, weight="bold"),
                 text_color=TEXT2).pack(side="left", padx=(10, 6))
    ctk.CTkLabel(inicio_panel, text="ESTE:", font=ctk.CTkFont(size=10),
                 text_color=TEXT2).pack(side="left")
    e_ini_e = ctk.CTkEntry(inicio_panel, textvariable=var_inicio_este,
                            width=100, height=24, font=ctk.CTkFont(size=10),
                            fg_color=BG, placeholder_text="ej. 489129.20")
    e_ini_e.pack(side="left", padx=(2, 10))
    e_ini_e.bind("<KeyRelease>", lambda _: _recalcular())

    ctk.CTkLabel(inicio_panel, text="NORTE:", font=ctk.CTkFont(size=10),
                 text_color=TEXT2).pack(side="left")
    e_ini_n = ctk.CTkEntry(inicio_panel, textvariable=var_inicio_norte,
                            width=110, height=24, font=ctk.CTkFont(size=10),
                            fg_color=BG, placeholder_text="ej. 1104402.08")
    e_ini_n.pack(side="left", padx=(2, 10))
    e_ini_n.bind("<KeyRelease>", lambda _: _recalcular())

    lbl_ini_hint = ctk.CTkLabel(inicio_panel,
                 text="Acepta lat/lon de Google Maps o CRTM05",
                 font=ctk.CTkFont(size=9), text_color=BORDER)
    lbl_ini_hint.pack(side="left", padx=(6, 0))

    def _normalizar_inicio(*_):
        """Auto-convierte lat/lon a CRTM05 si el usuario pegó coordenadas de Google Maps."""
        try:
            e_raw = float(var_inicio_este.get().strip())
            n_raw = float(var_inicio_norte.get().strip())
        except ValueError:
            return
        # Detectar si son lat/lon: lat ∈ [7,12], lon ∈ [-88,-80]
        es_latlon = (7.0 <= e_raw <= 12.0 and -88.0 <= n_raw <= -80.0)
        if es_latlon:
            este, norte = wgs84_a_crtm05(e_raw, n_raw)
            var_inicio_este.set(f"{este:.2f}")
            var_inicio_norte.set(f"{norte:.2f}")
            lbl_ini_hint.configure(
                text=f"Convertido de lat/lon  ({e_raw}, {n_raw})",
                text_color=SUCCESS)
        else:
            lbl_ini_hint.configure(
                text="Acepta lat/lon de Google Maps o CRTM05",
                text_color=BORDER)
        _recalcular()

    e_ini_e.bind("<FocusOut>", _normalizar_inicio)
    e_ini_n.bind("<FocusOut>", _normalizar_inicio)

    # — Cabecera de columnas —
    cab_frame = ctk.CTkFrame(left, fg_color=BG_CARD, corner_radius=8, height=28)
    cab_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 1))
    cab_frame.pack_propagate(False)

    def _actualizar_cabecera():
        for w in cab_frame.winfo_children():
            w.destroy()
        modo = var_modo.get()
        cols = _cols_cabecera(modo)
        # Encabezado columna LÍNEA (ancho igual al label de fila: 52px)
        ctk.CTkLabel(cab_frame, text="LÍNEA", width=52,
                     font=ctk.CTkFont(size=9, weight="bold"),
                     text_color=TEXT2).pack(side="left", padx=(4, 2))
        for txt, w in cols:
            ctk.CTkLabel(cab_frame, text=txt, width=w,
                         font=ctk.CTkFont(size=9, weight="bold"),
                         text_color=TEXT2, anchor="center").pack(side="left", padx=1)
        # Espacio para botón borrar
        ctk.CTkLabel(cab_frame, text="", width=32).pack(side="left")

    def _cols_cabecera(modo: str) -> list[tuple[str, int]]:
        if modo == "azimut":
            return [("Az°", 46), ("Az'", 38), ("Dist m", 58), ("cm", 40),
                    ("FRENTE", 60), ("COLINDA CON", 130)]
        elif modo == "rumbo":
            return [("N/S", 40), ("Rum°", 46), ("Rum'", 38), ("E/W", 40),
                    ("Dist m", 58), ("cm", 40), ("FRENTE", 60), ("COLINDA CON", 90)]
        else:
            return [("ESTE (m)", 120), ("NORTE (m)", 120), ("FRENTE", 60), ("COLINDA CON", 130)]

    # — Tabla scrollable —
    tabla = ctk.CTkScrollableFrame(left, fg_color="transparent", corner_radius=0)
    tabla.grid(row=3, column=0, sticky="nsew", padx=12, pady=2)

    # — Botones de tabla —
    btn_bar = ctk.CTkFrame(left, fg_color="transparent")
    btn_bar.grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 10))

    ctk.CTkButton(btn_bar, text="＋  Agregar línea", width=150, height=34,
                  fg_color=ACCENT, hover_color="#1D4ED8", corner_radius=8,
                  font=ctk.CTkFont(size=13, weight="bold"),
                  command=lambda: _agregar_fila()).pack(side="left", padx=(0, 8))
    ctk.CTkButton(btn_bar, text="Limpiar todo", width=110, height=34,
                  fg_color=BG_CARD, hover_color=BORDER, corner_radius=8,
                  font=ctk.CTkFont(size=11),
                  command=lambda: _limpiar()).pack(side="left", padx=(0, 8))
    ctk.CTkButton(btn_bar, text="Pegar portapapeles", width=160, height=34,
                  fg_color=BG_CARD, hover_color=BORDER, corner_radius=8,
                  font=ctk.CTkFont(size=11),
                  command=lambda: _pegar_portapapeles()).pack(side="left")

    # ══ Panel derecho: canvas + resultados ════════════════════════════
    right = ctk.CTkFrame(body, fg_color=BG_PAN, corner_radius=14)
    right.grid(row=0, column=1, sticky="nsew")
    right.rowconfigure(0, weight=1)
    right.columnconfigure(0, weight=1)

    canvas = tk.Canvas(right, bg="#1E293B", highlightthickness=0)
    canvas.grid(row=0, column=0, sticky="nsew", padx=10, pady=(12, 4))

    res_frame = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=10)
    res_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))

    def _res_row(parent, lbl_txt, var, color, r):
        ctk.CTkLabel(parent, text=lbl_txt,
                     font=ctk.CTkFont(size=11), text_color=TEXT2
                     ).grid(row=r, column=0, sticky="w", padx=12, pady=(6 if r==0 else 2, 2))
        ctk.CTkLabel(parent, textvariable=var,
                     font=ctk.CTkFont(size=13, weight="bold"), text_color=color
                     ).grid(row=r, column=1, sticky="w", padx=8, pady=(6 if r==0 else 2, 2))

    _res_row(res_frame, "Area del lote:",    var_area,     SUCCESS,  0)
    _res_row(res_frame, "Perimetro:",        var_perim,    TEXT2,    1)
    _res_row(res_frame, "Error de cierre:",  var_cierre,   WARNING,  2)
    _res_row(res_frame, "Huella maxima:",    var_huella,   ACCENT,   3)
    _res_row(res_frame, "Frente hacia:",     var_norte_det, C_GREEN,  4)

    ctk.CTkButton(res_frame, text="Ver en mapa (satelital)",
                  height=30, fg_color="#0F766E",
                  hover_color="#0D9488", corner_radius=7,
                  font=ctk.CTkFont(size=11, weight="bold"),
                  command=lambda: _ver_mapa()
                  ).grid(row=5, column=0, columnspan=2,
                         sticky="ew", padx=10, pady=(4, 8))

    # ── Footer: retiros + normativa + guardar/cargar ──────────────────
    foot = ctk.CTkFrame(win, fg_color=BG_PAN, corner_radius=12)
    foot.pack(fill="x", padx=18, pady=(5, 12))

    var_ret_f   = ctk.StringVar(value="3.00")
    var_ret_p   = ctk.StringVar(value="3.00")
    var_ret_li  = ctk.StringVar(value="1.50")
    var_ret_ld  = ctk.StringVar(value="1.50")
    var_cob     = ctk.StringVar(value="75")
    var_norma   = ctk.StringVar(value="Código Urbano CR")

    ret_row = ctk.CTkFrame(foot, fg_color="transparent")
    ret_row.pack(fill="x", padx=14, pady=8)

    ctk.CTkLabel(ret_row, text="Retiros (m):",
                 font=ctk.CTkFont(size=11, weight="bold"),
                 text_color=TEXT2).pack(side="left", padx=(0, 8))

    for lbl_r, var_r in [
        ("Frontal",   var_ret_f),
        ("Posterior", var_ret_p),
        ("Lat-Izq",   var_ret_li),
        ("Lat-Der",   var_ret_ld),
    ]:
        ctk.CTkLabel(ret_row, text=f"{lbl_r}:", font=ctk.CTkFont(size=11),
                     text_color=TEXT2).pack(side="left")
        e = ctk.CTkEntry(ret_row, textvariable=var_r, width=52, height=26,
                         font=ctk.CTkFont(size=11), fg_color=BG_CARD)
        e.pack(side="left", padx=(2, 10))
        e.bind("<KeyRelease>", lambda _: _recalcular())

    ctk.CTkLabel(ret_row, text="Cobertura:", font=ctk.CTkFont(size=11),
                 text_color=TEXT2).pack(side="left")
    e_cob = ctk.CTkEntry(ret_row, textvariable=var_cob, width=40, height=26,
                         font=ctk.CTkFont(size=11), fg_color=BG_CARD)
    e_cob.pack(side="left", padx=(2, 2))
    e_cob.bind("<KeyRelease>", lambda _: _recalcular())
    ctk.CTkLabel(ret_row, text="%  Normativa:", font=ctk.CTkFont(size=11),
                 text_color=TEXT2).pack(side="left", padx=(2, 4))
    ctk.CTkEntry(ret_row, textvariable=var_norma, width=170, height=26,
                 font=ctk.CTkFont(size=11), fg_color=BG_CARD).pack(side="left", padx=(0, 14))

    ctk.CTkButton(ret_row, text="Guardar JSON", width=120, height=30,
                  fg_color=C_GREEN, hover_color="#047857", corner_radius=7,
                  font=ctk.CTkFont(size=12, weight="bold"),
                  command=lambda: _guardar()).pack(side="right", padx=4)
    ctk.CTkButton(ret_row, text="Cargar JSON", width=110, height=30,
                  fg_color=BG_CARD, hover_color=BORDER, corner_radius=7,
                  font=ctk.CTkFont(size=11),
                  command=lambda: _cargar()).pack(side="right", padx=4)

    # ══════════════════════════════════════════════════════════════════
    # FUNCIONES INTERNAS
    # ══════════════════════════════════════════════════════════════════

    def _recalcular(*_):
        """Recalcula área, error de cierre, huella y redibuja canvas."""
        if _timer[0]:
            win.after_cancel(_timer[0])
        _timer[0] = win.after(120, _recalcular_ahora)

    def _recalcular_ahora():
        modo  = var_modo.get()
        lns   = _lineas_de_filas(filas, modo)
        verts = vertices_desde_lineas(lns, modo)

        if len(verts) >= 3:
            area = area_poligono(verts)
            peri = perimetro(verts)
            var_area.set(f"{area:,.2f}  m2")
            var_perim.set(f"{peri:,.2f}  m")
            try:
                cob = _float(var_cob.get(), 75) / 100.0
                var_huella.set(f"{area * cob:,.2f}  m2  ({var_cob.get()}%)")
            except Exception:
                var_huella.set("—")
        else:
            var_area.set("—  m2")
            var_perim.set("—  m")
            var_huella.set("—  m2")

        de, dn = _error_cierre(lns, modo)
        dist_err = math.hypot(de, dn)
        if modo == "coordenadas":
            var_cierre.set("n/a (coordenadas directas)")
        elif dist_err < 0.05:
            var_cierre.set(f"OK  {dist_err:.4f} m")
        else:
            var_cierre.set(f"!! {dist_err:.3f} m  (dE={de:+.3f}  dN={dn:+.3f})")

        # Auto-detectar norte del lote
        _actualizar_norte_det(verts, lns, modo)

        # Calcular WGS84 y cachear
        _actualizar_wgs84(verts, lns, modo)

        _dibujar(verts, lns)

    def _verts_absolutos_crtm05(verts, modo) -> list[tuple[float, float]]:
        """Convierte vértices relativos a coordenadas CRTM05 absolutas."""
        if modo == "coordenadas":
            # En modo coordenadas, las filas tienen directamente ESTE/NORTE
            return [(f["var_este"].get(), f["var_norte"].get())
                    for f in filas if f["var_este"].get() and f["var_norte"].get()]
        # En azimut/rumbo: sumar el punto de inicio al polígono relativo
        try:
            e0 = float(var_inicio_este.get())
            n0 = float(var_inicio_norte.get())
            return [(e0 + v[0], n0 + v[1]) for v in verts]
        except (ValueError, AttributeError):
            return []

    def _actualizar_norte_det(verts, lns, modo):
        """Detecta la dirección del frente y actualiza var_norte_det."""
        idx_frente = next((i for i, ln in enumerate(lns) if ln.get("frente")), -1)
        if idx_frente < 0 or len(verts) < 3:
            var_norte_det.set("— (marca FRENTE en la tabla)")
            return
        # Intentar con coordenadas absolutas CRTM05
        abs_verts = _verts_absolutos_crtm05(verts, modo)
        if abs_verts and len(abs_verts) >= 3:
            try:
                av = [(float(e), float(n)) for e, n in abs_verts]
                nd = norte_lote_auto(av, idx_frente)
            except (ValueError, TypeError):
                nd = norte_lote_auto(verts, idx_frente)
        else:
            nd = norte_lote_auto(verts, idx_frente)
        _NOMBRES = {"norte": "Norte  (frente mira al N)",
                    "sur":   "Sur    (frente mira al S)",
                    "este":  "Este   (frente mira al E)",
                    "oeste": "Oeste  (frente mira al O)"}
        var_norte_det.set(_NOMBRES.get(nd, nd.capitalize()))
        # Guardar en config para que lo use el Programa Arquitectonico
        try:
            cfg_path = os.path.join(BASE_DIR, "config", "settings.json")
            cfg = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, encoding="utf-8") as fh:
                    cfg = json.load(fh)
            cfg["norte_lote"] = nd
            os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
            with open(cfg_path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=4, ensure_ascii=False)
        except Exception:
            pass

    def _actualizar_wgs84(verts, lns, modo):
        """Convierte el polígono a WGS84 y lo guarda en caché."""
        abs_verts = _verts_absolutos_crtm05(verts, modo)
        if not abs_verts:
            _wgs84_cache[0] = []
            return
        try:
            pts = [(float(e), float(n)) for e, n in abs_verts]
            _wgs84_cache[0] = [crtm05_a_wgs84(e, n) for e, n in pts]
        except Exception:
            _wgs84_cache[0] = []

    def _ver_mapa():
        """Abre el lote en mapa satelital (folium → navegador, o Google Maps)."""
        pts = _wgs84_cache[0]
        if not pts:
            # Intentar calcular ahora
            modo  = var_modo.get()
            lns   = _lineas_de_filas(filas, modo)
            verts = vertices_desde_lineas(lns, modo)
            _actualizar_wgs84(verts, lns, modo)
            pts   = _wgs84_cache[0]
        if not pts:
            messagebox.showwarning(
                "Sin coordenadas",
                "Para ver en mapa necesito coordenadas CRTM05.\n\n"
                "- Modo COORDENADAS: usa los valores ESTE/NORTE del derrotero.\n"
                "- Modo AZIMUT/RUMBO: ingresa el Punto 1 (ESTE/NORTE) en el campo superior."
            )
            return

        lat_c = sum(p[0] for p in pts) / len(pts)
        lon_c = sum(p[1] for p in pts) / len(pts)

        try:
            import folium

            m = folium.Map(
                location=[lat_c, lon_c], zoom_start=19,
                tiles="https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
                attr="Google Satellite",
            )
            # Polígono del lote
            folium.Polygon(
                locations=pts,
                color="#2563EB", fill=True,
                fill_color="#2563EB", fill_opacity=0.25, weight=3,
                tooltip=var_nombre.get(),
            ).add_to(m)
            # Vértices numerados
            for i, (lat, lon) in enumerate(pts):
                folium.CircleMarker(
                    [lat, lon], radius=6,
                    color="white", fill=True, fill_color="#2563EB",
                    tooltip=f"Punto {i+1}",
                ).add_to(m)
            # Marcar frente en verde
            lns = _lineas_de_filas(filas, var_modo.get())
            idx_f = next((i for i, ln in enumerate(lns) if ln.get("frente")), -1)
            if idx_f >= 0 and idx_f + 1 < len(pts):
                folium.PolyLine(
                    [pts[idx_f], pts[(idx_f+1) % len(pts)]],
                    color="#16A34A", weight=5, tooltip="FRENTE",
                ).add_to(m)
            # Guardar y abrir
            html_path = os.path.join(BASE_DIR, "_terreno_mapa.html")
            m.save(html_path)
            import webbrowser
            webbrowser.open(f"file:///{html_path.replace(os.sep, '/')}")

        except ImportError:
            # Fallback: Google Maps satelital en el centroide
            import webbrowser
            url = f"https://maps.google.com/maps?q={lat_c},{lon_c}&t=k&z=18"
            webbrowser.open(url)
            messagebox.showinfo(
                "Abriendo Google Maps",
                f"Google Maps satelital en:\n  {lat_c:.6f}N  {lon_c:.6f}W\n\n"
                "Para ver el polígono exacto instalá folium:\n"
                "  python -m pip install folium"
            )

    def _dibujar(verts: list[tuple], lns: list[dict]):
        canvas.delete("all")
        W = canvas.winfo_width()
        H = canvas.winfo_height()
        if W < 20 or H < 20:
            return
        if len(verts) < 2:
            canvas.create_text(W // 2, H // 2, text="Ingresá el derrotero",
                               fill=TEXT2, font=("Consolas", 12))
            return

        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        span_x = (max(xs) - min(xs)) or 1.0
        span_y = (max(ys) - min(ys)) or 1.0

        margin = 40
        scale  = min((W - margin * 2) / span_x,
                     (H - margin * 2) / span_y) * 0.82

        cx = W / 2 - (min(xs) + max(xs)) / 2 * scale
        cy = H / 2 + (min(ys) + max(ys)) / 2 * scale

        def sc(e, n):          # screen coords (Y invertido)
            return cx + e * scale, cy - n * scale

        # Retiros (bounding-box aproximado, sólo orientativo)
        if len(verts) >= 3:
            try:
                rf  = _float(var_ret_f.get(), 3)
                rp  = _float(var_ret_p.get(), 3)
                rli = _float(var_ret_li.get(), 1.5)
                rld = _float(var_ret_ld.get(), 1.5)
                rx1, ry2 = sc(min(xs) + rli, min(ys) + rp)
                rx2, ry1 = sc(max(xs) - rld, max(ys) - rf)
                canvas.create_rectangle(rx1, ry1, rx2, ry2,
                                        outline=WARNING, dash=(6, 3), width=1)
                canvas.create_text(rx2 - 2, ry1 + 2, text="retiros",
                                   fill=WARNING, font=("Consolas", 8), anchor="ne")
            except Exception:
                pass

        # Polígono del lote
        pts_sc = [sc(e, n) for e, n in verts]
        flat   = [c for p in pts_sc for c in p]
        if len(flat) >= 6:
            canvas.create_polygon(flat, fill="#0f3460", outline=ACCENT,
                                  width=2, joinstyle=tk.ROUND)

        # Líneas individuales con etiqueta de distancia
        n_verts = len(verts)
        for i, ln in enumerate(lns):
            p1 = verts[i % n_verts]
            p2 = verts[(i + 1) % n_verts]
            s1, s2 = sc(*p1), sc(*p2)
            color = SUCCESS if ln.get("frente") else ACCENT
            width = 3     if ln.get("frente") else 2
            canvas.create_line(s1[0], s1[1], s2[0], s2[1],
                                fill=color, width=width)
            # Etiqueta distancia en el centro de la línea
            mx, my = (s1[0] + s2[0]) / 2, (s1[1] + s2[1]) / 2
            dist = ln.get("dist_m", 0) + ln.get("dist_cm", 0) / 100.0
            if dist > 0:
                dist_txt = f"{dist:.2f}m"
                canvas.create_text(mx, my - 7, text=dist_txt,
                                   fill=TEXT2, font=("Consolas", 7))
            if ln.get("frente"):
                canvas.create_text(mx, my + 7, text="FRENTE",
                                   fill=SUCCESS, font=("Consolas", 7, "bold"))

        # Puntos y numeración
        for i, (e, n) in enumerate(verts):
            sx, sy = sc(e, n)
            canvas.create_oval(sx - 4, sy - 4, sx + 4, sy + 4,
                               fill=ACCENT, outline="white", width=1)
            canvas.create_text(sx + 7, sy - 7, text=str(i + 1),
                               fill=TEXT, font=("Consolas", 9, "bold"))

        # Flecha Norte
        nax, nay = W - 28, 42
        canvas.create_line(nax, nay + 15, nax, nay - 15,
                           fill=TEXT, width=2, arrow=tk.LAST,
                           arrowshape=(8, 10, 4))
        canvas.create_text(nax, nay + 24, text="N",
                           fill=TEXT, font=("Consolas", 9, "bold"))

        # Escala gráfica
        if scale > 0:
            raw_m = 50 / scale
            mag = 10 ** math.floor(math.log10(raw_m)) if raw_m > 0 else 1
            seg_m  = round(raw_m / mag) * mag
            seg_px = seg_m * scale
            bx, by = margin, H - margin + 8
            canvas.create_line(bx, by, bx + seg_px, by, fill=TEXT2, width=2)
            canvas.create_text(bx + seg_px / 2, by + 10,
                               text=f"{seg_m:.0f} m",
                               fill=TEXT2, font=("Consolas", 8))

        # Área en canvas
        if len(verts) >= 3:
            a = area_poligono(verts)
            canvas.create_text(10, H - 12, text=f"  Área: {a:,.2f} m²",
                               fill=SUCCESS, font=("Consolas", 9, "bold"), anchor="sw")

    # ── Gestión de filas ───────────────────────────────────────────────

    def _agregar_fila(datos: dict | None = None):
        idx  = len(filas)
        modo = var_modo.get()

        fondo = BG_CARD if idx % 2 == 0 else BG_PAN
        row   = ctk.CTkFrame(tabla, fg_color=fondo, corner_radius=6)
        row.pack(fill="x", pady=1)

        # Etiqueta de línea: "1-2", "2-3", etc.
        lbl_n = ctk.CTkLabel(row, text=f"{idx+1}-{idx+2}", width=52,
                             font=ctk.CTkFont(size=11, weight="bold"),
                             text_color=TEXT2)
        lbl_n.pack(side="left", padx=(4, 2))

        # Variables
        var_az_g  = ctk.StringVar(value=datos.get("az_g",  "") if datos else "")
        var_az_m  = ctk.StringVar(value=datos.get("az_m",  "") if datos else "")
        var_ns    = ctk.StringVar(value=datos.get("ns",    "N") if datos else "N")
        var_rum_g = ctk.StringVar(value=datos.get("rum_g", "") if datos else "")
        var_rum_m = ctk.StringVar(value=datos.get("rum_m", "") if datos else "")
        var_ew    = ctk.StringVar(value=datos.get("ew",    "E") if datos else "E")
        var_este  = ctk.StringVar(value=datos.get("este",  "") if datos else "")
        var_norte = ctk.StringVar(value=datos.get("norte", "") if datos else "")
        var_dist_m  = ctk.StringVar(value=datos.get("dist_m",  "") if datos else "")
        var_dist_cm = ctk.StringVar(value=datos.get("dist_cm", "00") if datos else "00")
        var_frente  = ctk.BooleanVar(value=bool(datos.get("frente", False)) if datos else False)
        var_desc    = ctk.StringVar(value=datos.get("desc", "") if datos else "")

        def _mk(var, w=50, **kw):
            e = ctk.CTkEntry(row, textvariable=var, width=w, height=26,
                             font=ctk.CTkFont(size=11),
                             fg_color=BG if idx % 2 == 0 else BG_CARD, **kw)
            e.bind("<KeyRelease>", lambda _: _recalcular())
            return e

        # Construir widgets
        e_az_g  = _mk(var_az_g,  46)
        e_az_m  = _mk(var_az_m,  38)

        om_ns   = ctk.CTkOptionMenu(row, variable=var_ns, values=["N", "S"],
                                     width=44, height=26, font=ctk.CTkFont(size=11),
                                     command=lambda _: _recalcular())
        e_rum_g = _mk(var_rum_g, 46)
        e_rum_m = _mk(var_rum_m, 38)
        om_ew   = ctk.CTkOptionMenu(row, variable=var_ew, values=["E", "W"],
                                     width=44, height=26, font=ctk.CTkFont(size=11),
                                     command=lambda _: _recalcular())

        e_este  = _mk(var_este,  120)
        e_norte = _mk(var_norte, 120)

        e_dist_m  = _mk(var_dist_m,  58)
        e_dist_cm = _mk(var_dist_cm, 40)

        chk = ctk.CTkCheckBox(row, variable=var_frente, text="", width=60, height=26,
                              checkbox_width=18, checkbox_height=18,
                              command=_recalcular)

        e_desc = _mk(var_desc, 120)

        def _borrar():
            filas.remove(fila)
            row.destroy()
            _renumerar()
            _recalcular()

        btn_del = ctk.CTkButton(row, text="✕", width=30, height=26,
                                fg_color=C_ERROR, hover_color="#991b1b",
                                font=ctk.CTkFont(size=11),
                                command=_borrar)
        btn_del.pack(side="right", padx=(2, 4))

        fila = {
            "row": row, "lbl_n": lbl_n,
            "var_az_g":   var_az_g,  "var_az_m": var_az_m,
            "var_ns":     var_ns,    "var_rum_g": var_rum_g,
            "var_rum_m":  var_rum_m, "var_ew":    var_ew,
            "var_este":   var_este,  "var_norte": var_norte,
            "var_dist_m": var_dist_m, "var_dist_cm": var_dist_cm,
            "var_frente": var_frente, "var_desc": var_desc,
            # widgets agrupados por modo
            "w_azimut": [e_az_g, e_az_m, e_dist_m, e_dist_cm, chk, e_desc],
            "w_rumbo":  [om_ns, e_rum_g, e_rum_m, om_ew, e_dist_m, e_dist_cm, chk, e_desc],
            "w_coord":  [e_este, e_norte, chk, e_desc],
        }
        filas.append(fila)
        _mostrar_widgets(fila, modo)
        # Renumerar diferido (espera que _renumerar esté definida en este scope)
        win.after(1, lambda: _renumerar())

    def _mostrar_widgets(f: dict, modo: str):
        todos = set(f["w_azimut"]) | set(f["w_rumbo"]) | set(f["w_coord"])
        for w in todos:
            try: w.pack_forget()
            except Exception: pass
        key = {"azimut": "w_azimut", "rumbo": "w_rumbo", "coordenadas": "w_coord"}.get(modo, "w_azimut")
        for w in f[key]:
            w.pack(side="left", padx=1)

    def _renumerar():
        n = len(filas)
        for i, f in enumerate(filas):
            # La última línea cierra de vuelta al punto 1
            dest = (i + 1) % n + 1 if n > 1 else 2
            f["lbl_n"].configure(text=f"{i+1}-{dest}")
            f["row"].configure(fg_color=BG_CARD if i % 2 == 0 else BG_PAN)

    def _limpiar():
        for f in filas[:]:
            f["row"].destroy()
        filas.clear()
        _recalcular()

    def _pegar_portapapeles():
        """
        Intenta parsear texto pegado del portapapeles.
        Acepta filas separadas por salto de línea, columnas por tab o espacio.
        """
        try:
            txt = win.clipboard_get()
        except Exception:
            messagebox.showwarning("Sin datos", "El portapapeles está vacío.")
            return
        modo = var_modo.get()
        lineas_txt = [l.strip() for l in txt.strip().splitlines() if l.strip()]
        agregadas = 0
        for linea in lineas_txt:
            parts = linea.replace(",", ".").split()
            if len(parts) < 2:
                continue
            datos: dict = {}
            try:
                if modo == "azimut" and len(parts) >= 3:
                    datos = {"az_g": parts[0], "az_m": parts[1],
                             "dist_m": parts[2], "dist_cm": parts[3] if len(parts) > 3 else "0"}
                elif modo == "rumbo" and len(parts) >= 5:
                    datos = {"ns": parts[0], "rum_g": parts[1], "rum_m": parts[2],
                             "ew": parts[3], "dist_m": parts[4],
                             "dist_cm": parts[5] if len(parts) > 5 else "0"}
                elif modo == "coordenadas" and len(parts) >= 2:
                    datos = {"este": parts[0], "norte": parts[1]}
                if datos:
                    _agregar_fila(datos)
                    agregadas += 1
            except Exception:
                pass
        if agregadas:
            _renumerar()
            _recalcular()
        else:
            messagebox.showinfo("Sin resultados",
                                "No se pudo parsear el portapapeles.\n"
                                "Asegúrate de que el modo coincide con el formato.")

    def _guardar():
        modo  = var_modo.get()
        lns   = _lineas_de_filas(filas, modo)
        verts = vertices_desde_lineas(lns, modo)
        area  = area_poligono(verts) if len(verts) >= 3 else 0.0
        try:
            cob = _float(var_cob.get(), 75) / 100.0
        except Exception:
            cob = 0.75

        data = {
            "nombre":   var_nombre.get(),
            "modo":     modo,
            "lineas":   lns,
            "poligono": [(round(e, 4), round(n, 4)) for e, n in verts],
            "area_m2":  round(area, 2),
            "perimetro_m": round(perimetro(verts), 2) if len(verts) >= 2 else 0,
            "retiros": {
                "frontal":      _float(var_ret_f.get(),  3.0),
                "posterior":    _float(var_ret_p.get(),  3.0),
                "lateral_izq":  _float(var_ret_li.get(), 1.5),
                "lateral_der":  _float(var_ret_ld.get(), 1.5),
            },
            "cobertura_max_pct": _float(var_cob.get(), 75),
            "huella_max_m2":     round(area * cob, 2),
            "normativa":         var_norma.get(),
        }
        win.lift(); win.focus_force()
        ruta = filedialog.asksaveasfilename(
            parent=win,
            title="Guardar terreno",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile=f"terreno_{var_nombre.get()[:20].replace(' ','_')}.json",
        )
        if ruta:
            with open(ruta, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            # Guardar ruta en settings para que el PA la cargue automáticamente
            try:
                cfg_path = os.path.join(BASE_DIR, "config", "settings.json")
                cfg = {}
                if os.path.exists(cfg_path):
                    with open(cfg_path, encoding="utf-8") as fc:
                        cfg = json.load(fc)
                cfg["terreno_json_path"] = ruta
                # ── Contexto compartido para IA del CAD Visor ─────────
                cfg.setdefault("proyecto_activo", {})
                cfg["proyecto_activo"].setdefault("nombre", data["nombre"])
                cfg["proyecto_activo"]["terreno"] = {
                    "area_m2":          data["area_m2"],
                    "perimetro_m":      data["perimetro_m"],
                    "huella_max_m2":    data["huella_max_m2"],
                    "cobertura_max_pct": data["cobertura_max_pct"],
                    "retiros":          data["retiros"],
                    "norte_lote":       var_norte_det.get() or cfg.get("norte_lote", "norte"),
                }
                os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
                with open(cfg_path, "w", encoding="utf-8") as fc:
                    json.dump(cfg, fc, indent=4, ensure_ascii=False)
            except Exception:
                pass
            messagebox.showinfo("Guardado", f"Terreno guardado en:\n{ruta}")

    def _cargar():
        win.lift(); win.focus_force()
        ruta = filedialog.askopenfilename(
            parent=win,
            title="Cargar terreno JSON",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
        )
        if not ruta:
            return
        try:
            with open(ruta, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")
            return

        _limpiar()
        modo = data.get("modo", "azimut")
        _sel_modo(modo)

        var_nombre.set(data.get("nombre", ""))
        ret = data.get("retiros", {})
        var_ret_f.set(str(ret.get("frontal",     3.0)))
        var_ret_p.set(str(ret.get("posterior",   3.0)))
        var_ret_li.set(str(ret.get("lateral_izq", 1.5)))
        var_ret_ld.set(str(ret.get("lateral_der", 1.5)))
        var_cob.set(str(data.get("cobertura_max_pct", 75)))
        var_norma.set(data.get("normativa", ""))

        for ln in data.get("lineas", []):
            _agregar_fila(ln)

        _renumerar()
        _recalcular()

    # ── Inicialización ─────────────────────────────────────────────────
    _actualizar_cabecera()
    # Ocultar panel inicio si arranca en azimut (se mostrará al seleccionar)
    # En azimut sí se muestra desde el inicio
    for _ in range(4):                  # 4 filas vacías por defecto
        _agregar_fila()

    win.after(250, _recalcular_ahora)   # primer cálculo tras renderizar
    win.mainloop() if not parent else None


# ════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════════

def main():
    abrir_terreno()


if __name__ == "__main__":
    main()
