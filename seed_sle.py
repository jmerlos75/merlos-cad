"""
seed_sle.py
===========
Script de seeding para el Spatial Learning Engine.

Permite cargar plantas existentes (JSON) al SLE para que empiece a
aprender desde el primer día — antes de que la app genere proyectos nuevos.

USO SIMPLE (consola):
    python seed_sle.py
    → Abre diálogo de GUI para seleccionar archivos JSON

USO DIRECTO (script):
    python seed_sle.py --directorio "C:/mis_plantas" --score 90

FORMATOS ACEPTADOS:
  1. JSON del Engine: {"grid": {...}, "recintos": [...], "puertas": [...]}
  2. JSON con envoltorio:
     {
       "prompt": "casa de 3 dormitorios...",
       "score": 85,
       "plan": {"grid": {...}, "recintos": [...]}
     }
  3. Carpeta de proyectos: cualquier .json dentro será parseado automáticamente
"""
import argparse
import json
import os
import sys

# Asegura que el directorio del estudio esté en el path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def _importar_archivo(ruta: str, memoria, score_default: int = 85) -> tuple[bool, str]:
    """Importa un JSON al SLE. Retorna (ok, mensaje)."""
    try:
        with open(ruta, encoding="utf-8") as f:
            data = json.load(f)

        # Soporte para envoltorio
        if "plan" in data and isinstance(data["plan"], dict):
            plan   = data["plan"]
            prompt = data.get("prompt") or data.get("prompt_original") or f"importado: {os.path.basename(ruta)}"
            score  = data.get("score", score_default)
        elif "recintos" in data:
            plan   = data
            prompt = data.get("prompt_original") or f"importado: {os.path.basename(ruta)}"
            score  = score_default
        else:
            return False, f"Formato no reconocido en {ruta}"

        if not plan.get("recintos"):
            return False, f"Sin recintos en {ruta}"

        pid = memoria.guardar_proyecto(plan, prompt_original=prompt, score=score, aprobado=True)
        n_rec = len(plan.get("recintos", []))
        return True, f"OK  id={pid}  {n_rec} recintos  score={score}  — {os.path.basename(ruta)}"

    except json.JSONDecodeError as e:
        return False, f"JSON inválido en {ruta}: {e}"
    except Exception as e:
        return False, f"Error en {ruta}: {e}"


def importar_directorio(directorio: str, memoria, score_default: int = 85) -> tuple[int, int]:
    """Importa todos los JSON de un directorio. Retorna (importados, errores)."""
    ok_count = err_count = 0
    for fname in sorted(os.listdir(directorio)):
        if not fname.lower().endswith(".json"):
            continue
        ruta = os.path.join(directorio, fname)
        ok, msg = _importar_archivo(ruta, memoria, score_default)
        print(f"  {'✓' if ok else '✗'} {msg}")
        if ok:
            ok_count += 1
        else:
            err_count += 1
    return ok_count, err_count


def importar_con_gui(memoria) -> tuple[int, int]:
    """Abre un diálogo gráfico para seleccionar archivos JSON."""
    try:
        import customtkinter as ctk
        from tkinter import filedialog

        root = ctk.CTk()
        root.withdraw()

        rutas = filedialog.askopenfilenames(
            title="Seleccionar plantas JSON para el SLE",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
            parent=root,
        )
        root.destroy()

        if not rutas:
            print("No se seleccionaron archivos.")
            return 0, 0

        ok_count = err_count = 0
        for ruta in rutas:
            ok, msg = _importar_archivo(ruta, memoria)
            print(f"  {'✓' if ok else '✗'} {msg}")
            if ok:
                ok_count += 1
            else:
                err_count += 1
        return ok_count, err_count

    except ImportError:
        print("customtkinter no disponible — usa --directorio para modo consola.")
        return 0, 0


def abrir_gui_avanzada(memoria):
    """GUI completa con estadísticas, importación y aprobación de plantas."""
    try:
        import customtkinter as ctk
        from tkinter import filedialog

        ACCENT  = "#7C3AED"
        SUCCESS = "#16A34A"
        WARNING = "#EAB308"
        ERROR   = "#DC2626"
        BG      = "#0F172A"
        BG_PAN  = "#1E293B"
        BG_CARD = "#334155"
        TEXT    = "#F8FAFC"
        TEXT2   = "#94A3B8"
        BORDER  = "#475569"

        ctk.set_appearance_mode("dark")
        root = ctk.CTk()
        root.title("SLE Seeding Tool — Estudio Merlos AI")
        root.geometry("800x600")
        root.configure(fg_color=BG)

        # ── Header ──────────────────────────────────────────────
        hdr = ctk.CTkFrame(root, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(20, 10))

        ctk.CTkLabel(
            hdr, text="SLE Seeding Tool",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=ACCENT,
        ).pack(side="left")

        ctk.CTkLabel(
            hdr,
            text="Carga plantas existentes para que el SLE aprenda tu estilo desde el inicio.",
            font=ctk.CTkFont(size=12), text_color=TEXT2,
        ).pack(side="left", padx=15)

        # ── Stats card ──────────────────────────────────────────
        stats_card = ctk.CTkFrame(root, fg_color=BG_PAN, corner_radius=12)
        stats_card.pack(fill="x", padx=20, pady=5)

        stats_frame = ctk.CTkFrame(stats_card, fg_color="transparent")
        stats_frame.pack(fill="x", padx=15, pady=12)

        lbl_stats = ctk.CTkLabel(
            stats_frame, text="Cargando estadísticas...",
            font=ctk.CTkFont(size=12), text_color=TEXT2,
        )
        lbl_stats.pack(side="left")

        def actualizar_stats():
            try:
                s = memoria.estadisticas()
                lbl_stats.configure(
                    text=f"Proyectos: {s['n_proyectos']}  ·  "
                         f"Aprobados: {s['n_aprobados']}  ·  "
                         f"Score promedio: {s['score_promedio']}/100  ·  "
                         f"Correcciones: {s['n_correcciones']}",
                    text_color=SUCCESS if s['n_aprobados'] > 0 else WARNING,
                )
            except Exception as e:
                lbl_stats.configure(text=f"Error: {e}", text_color=ERROR)

        actualizar_stats()

        # ── Log ─────────────────────────────────────────────────
        log_card = ctk.CTkFrame(root, fg_color=BG_PAN, corner_radius=12)
        log_card.pack(fill="both", expand=True, padx=20, pady=5)

        ctk.CTkLabel(
            log_card, text="Log de importación",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=TEXT,
            anchor="w",
        ).pack(fill="x", padx=15, pady=(10, 5))

        log_txt = ctk.CTkTextbox(
            log_card, fg_color=BG_CARD, border_width=0, corner_radius=8,
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=TEXT2, wrap="word", state="disabled",
        )
        log_txt.pack(fill="both", expand=True, padx=15, pady=(0, 12))

        def log(msg: str, color: str = TEXT2):
            log_txt.configure(state="normal")
            log_txt.insert("end", msg + "\n")
            log_txt.see("end")
            log_txt.configure(state="disabled")

        # ── Botones ──────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(root, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(5, 20))

        def _importar_archivos():
            rutas = filedialog.askopenfilenames(
                title="Seleccionar plantas JSON",
                filetypes=[("JSON", "*.json"), ("Todos", "*.*")],
            )
            if not rutas:
                return
            ok_n = err_n = 0
            for ruta in rutas:
                ok, msg = _importar_archivo(ruta, memoria)
                log(f"{'✓' if ok else '✗'} {msg}")
                if ok: ok_n += 1
                else:  err_n += 1
            log(f"\n→ {ok_n} importados · {err_n} errores\n")
            actualizar_stats()

        def _importar_carpeta():
            from tkinter import filedialog as fd
            directorio = fd.askdirectory(title="Seleccionar carpeta con JSONs")
            if not directorio:
                return
            log(f"Importando carpeta: {directorio}")
            ok_n, err_n = importar_directorio(directorio, memoria)
            log(f"→ {ok_n} importados · {err_n} errores\n")
            actualizar_stats()

        def _ver_top_correcciones():
            top = memoria.top_correcciones(10)
            if not top:
                log("Sin correcciones registradas aún.")
                return
            log("\nTop correcciones:")
            for c in top:
                log(f"  {c.get('freq', 1)}× [{c.get('tipo_cambio','?')}] → {c.get('recinto') or 'general'}")
            log("")

        for texto, color, cmd in [
            ("Importar archivos JSON", ACCENT, _importar_archivos),
            ("Importar carpeta",       "#065F46", _importar_carpeta),
            ("Ver top correcciones",   WARNING, _ver_top_correcciones),
        ]:
            ctk.CTkButton(
                btn_frame, text=texto, height=38,
                fg_color=color, hover_color=BORDER,
                font=ctk.CTkFont(size=13, weight="bold"), corner_radius=8,
                command=cmd,
            ).pack(side="left", padx=4)

        ctk.CTkButton(
            btn_frame, text="Cerrar", height=38,
            fg_color=BG_CARD, hover_color=BORDER,
            font=ctk.CTkFont(size=13), corner_radius=8,
            command=root.destroy,
        ).pack(side="right")

        log("Listo. Selecciona archivos JSON para importar al SLE.")
        log(f"Base de datos: {memoria.db_path}\n")

        root.mainloop()

    except ImportError:
        print("customtkinter no disponible.")
        print("Instala con: pip install customtkinter")


# ─── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Seed del Spatial Learning Engine de Estudio Merlos AI",
    )
    parser.add_argument(
        "--directorio", "-d",
        help="Carpeta con archivos JSON para importar (modo consola, sin GUI)",
    )
    parser.add_argument(
        "--score", "-s", type=int, default=85,
        help="Score a asignar a los proyectos importados (default: 85)",
    )
    parser.add_argument(
        "--gui", action="store_true",
        help="Abrir GUI avanzada (default cuando no se pasa --directorio)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  SLE Seeding Tool — Estudio Merlos AI")
    print("=" * 60)

    try:
        from sle.core.memory import Memoria
        memoria = Memoria()
        print(f"  Base de datos: {memoria.db_path}")
        stats = memoria.estadisticas()
        print(f"  Estado actual: {stats['n_aprobados']} proyectos aprobados")
        print()
    except ImportError:
        print("ERROR: El SLE no está instalado o el path no es correcto.")
        print("  Asegúrate de estar en el directorio del Estudio Merlos AI.")
        sys.exit(1)

    if args.directorio:
        print(f"Importando desde: {args.directorio}")
        ok, err = importar_directorio(args.directorio, memoria, score_default=args.score)
        print()
        print(f"Resultado: {ok} importados · {err} errores")
        stats2 = memoria.estadisticas()
        print(f"Total en memoria: {stats2['n_aprobados']} proyectos aprobados")
    else:
        # Modo GUI
        abrir_gui_avanzada(memoria)


if __name__ == "__main__":
    main()
