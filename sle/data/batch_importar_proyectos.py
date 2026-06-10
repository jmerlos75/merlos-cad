"""
sle/data/batch_importar_proyectos.py
=====================================
Importa en lote los proyectos propios de Estudio Merlos AI al SLE.
Abre cada DWG en AutoCAD, extrae recintos y los importa.

Uso:
    python -m sle.data.batch_importar_proyectos
    python -m sle.data.batch_importar_proyectos --solo "NIVEL 1.dwg"
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SLE_DIR = Path(r"C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI")
CARPETA = Path(r"C:\Users\jmerl\OneDrive\Documentos\respaldo planos")

sys.path.insert(0, str(SLE_DIR))

# ─── Lista curada de DWGs a procesar ─────────────────────────────────
# Orden: más recientes primero, parejas de niveles juntas
DWGS_OBJETIVO = [
    # Proyectos recientes con capas Merlos
    "casa dos pisos plantas arquitectonicas.dwg",
    "correcciones2.dwg",
    "correcciones.dwg",
    "PLANTA NIVEL 1.dwg",
    "PLANTA NIVEL 2.dwg",
    "NIVEL 1.dwg",
    "NIVEL 2.dwg",
    "Planos Casa Merlos (cambio en cochera).dwg",
    "PLANO VIVIANA AMPLIACION FINAL.dwg",
    # Proyectos de clientes
    "Casa Edwin Rodriguez.dwg",
    "Casa Erick Hernandez.dwg",
    "Joseph Merlos Aptos Marzo 13.dwg",
]


def encontrar_dwg(nombre: str) -> Path | None:
    """Busca el DWG en la carpeta (recursivo, case-insensitive)."""
    for p in CARPETA.rglob("*.dwg"):
        if p.name.lower() == nombre.lower():
            return p
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solo", help="Procesar solo este archivo")
    parser.add_argument("--desde", help="Empezar desde este archivo (saltar anteriores)")
    args = parser.parse_args()

    # Conectar AutoCAD
    try:
        import win32com.client as win32
        acad = win32.Dispatch("AutoCAD.Application")
        acad.Visible = True
        print(f"[OK] AutoCAD: {acad.Name}\n")
    except Exception as e:
        print(f"[ERR] AutoCAD no disponible: {e}")
        sys.exit(1)

    from sle.core.memory import Memoria
    from sle.data.importar_dwg_merlos import importar_dwg_doc

    memoria = Memoria()

    # Determinar lista a procesar
    lista = [args.solo] if args.solo else DWGS_OBJETIVO

    if args.desde and not args.solo:
        idx = next((i for i, n in enumerate(lista)
                    if n.lower() == args.desde.lower()), 0)
        lista = lista[idx:]

    print(f"DWGs a procesar: {len(lista)}")
    for n in lista:
        ruta = encontrar_dwg(n)
        estado = str(ruta) if ruta else "[NO ENCONTRADO]"
        print(f"  {n:<55} {estado}")

    print()

    ok_total     = 0
    fallido_total = []
    stats_antes  = memoria.estadisticas()
    n_antes      = stats_antes.get("n_aprobados", 0)

    for nombre in lista:
        ruta = encontrar_dwg(nombre)
        if not ruta:
            print(f"\n[SKIP] {nombre} — archivo no encontrado")
            fallido_total.append(f"{nombre} (no encontrado)")
            continue

        print(f"\n{'='*60}")
        print(f"  {nombre}")
        print(f"{'='*60}")

        try:
            # Abrir DWG
            doc = acad.Documents.Open(str(ruta))
            time.sleep(2)

            # Importar
            niveles = importar_dwg_doc(doc, memoria, verbose=True)
            ok_total += niveles

            doc.Close(False)

        except Exception as e:
            print(f"  [ERR] {e}")
            fallido_total.append(nombre)
            try:
                acad.ActiveDocument.Close(False)
            except Exception:
                pass

        time.sleep(1)

    # Resumen
    stats = memoria.estadisticas()
    n_despues = stats.get("n_aprobados", 0)

    print(f"\n{'='*60}")
    print(f"  RESUMEN BATCH")
    print(f"{'='*60}")
    print(f"  Archivos procesados: {len(lista) - len(fallido_total)}/{len(lista)}")
    print(f"  Niveles importados:  {ok_total}")
    print(f"  Plantas SLE antes:   {n_antes}")
    print(f"  Plantas SLE ahora:   {n_despues}  (+{n_despues - n_antes})")
    print(f"  Score promedio:      {stats.get('score_promedio', '?')}")
    if fallido_total:
        print(f"\n  Fallidos:")
        for f in fallido_total:
            print(f"    - {f}")


if __name__ == "__main__":
    main()
