#!/usr/bin/env python
"""
verificar_sle.py — Muestra todo lo que el SLE tiene aprendido.

Uso:
    python verificar_sle.py
    python verificar_sle.py --id 1          # analiza proyecto específico
    python verificar_sle.py --buscar "2 dormitorios cochera"
"""
from __future__ import annotations

import sys
from pathlib import Path

# Asegurar que la raíz del proyecto esté en el path
raiz = Path(__file__).parent
if str(raiz) not in sys.path:
    sys.path.insert(0, str(raiz))


def separador(titulo: str = "", ancho: int = 60):
    if titulo:
        print(f"\n{'─'*2} {titulo} {'─'*(ancho - len(titulo) - 4)}")
    else:
        print("─" * ancho)


def mostrar_estadisticas(mem):
    from sle.core.memory import Memoria
    stats = mem.estadisticas()
    separador("SLE — Estudio Merlos AI")
    print(f"  Proyectos en memoria : {stats['n_proyectos']}")
    print(f"  Aprobados            : {stats['n_aprobados']}")
    print(f"  Correcciones         : {stats['n_correcciones']}")
    print(f"  Score promedio       : {stats['score_promedio']}/100")
    print(f"  Área promedio        : {stats['area_promedio_m2']} m²")


def mostrar_proyecto(mem, proyecto_id: int):
    from sle.core.spatial_graph import SpatialGraph

    datos = mem.obtener_proyecto(proyecto_id)
    if not datos:
        print(f"  Proyecto {proyecto_id} no encontrado.")
        return

    separador(f"Proyecto [{proyecto_id:03d}]")
    print(f"  Fecha   : {datos['timestamp'][:10]}")
    print(f"  Prompt  : {datos['prompt_original'][:80]}")
    print(f"  Score   : {datos['score']}/100")

    plan = datos.get("plan", {})
    grid = plan.get("grid", {})
    print(f"  Grid    : {grid.get('ancho_m')} × {grid.get('alto_m')} m")

    recintos = plan.get("recintos", [])
    print(f"\n  Recintos ({len(recintos)}):")
    for r in recintos:
        print(f"    {r['nombre']:22} {r['ancho']}×{r['alto']}m  "
              f"[fila={r['fila']} col={r['col']}]")

    # Análisis topológico
    try:
        g = SpatialGraph.from_json(plan)
        a = g.analizar()
        val = g.validate_topology()

        print(f"\n  Zonas:")
        for zona, n in sorted(a.get("zonas", {}).items()):
            print(f"    {zona:15} {n} recinto(s)")

        cadenas = a.get("cadenas_funcionales", [])
        if cadenas:
            print(f"\n  Cadenas funcionales:")
            for c in cadenas:
                print(f"    {' → '.join(c)}")

        adjs = [(ar.nodo_a, ar.nodo_b) for ar in g.aristas
                if ar.tipo == "adyacente" and ar.nodo_b]
        if adjs:
            print(f"\n  Adyacencias ({len(adjs)}):")
            for a_n, b_n in adjs:
                print(f"    {a_n} ↔ {b_n}")

        print(f"\n  Topología: ", end="")
        if val["ok"] and not val["advertencias"]:
            print("✓ válida")
        else:
            print()
            for e in val["errores"]:
                print(f"    ⚠ {e}")
            for w in val["advertencias"]:
                print(f"    ○ {w}")

        print(f"  Firma topológica: {a.get('topologia_signature', '?')}")

        # Reglas Merlos
        try:
            from sle.core.reglas_merlos import analizar_reglas_merlos, texto_analisis_merlos
            res_m = analizar_reglas_merlos(g)
            print()
            print(texto_analisis_merlos(res_m))
        except Exception:
            pass

    except Exception as e:
        print(f"  (Análisis topológico no disponible: {e})")

    # Conexiones en DB
    conx = datos.get("conexiones_db", [])
    if conx:
        print(f"\n  Conexiones en DB ({len(conx)}):")
        for c in conx:
            b = c.get("recinto2") or "EXTERIOR"
            lado = f" [{c['lado']}]" if c.get("lado") else ""
            print(f"    {c['recinto1']:20} ↔ {b:20} {c['tipo']}{lado}")


def buscar_similares(mem, prompt: str):
    separador(f'Búsqueda: "{prompt[:50]}"')
    similares = mem.buscar_proyectos_similares(prompt, n=5, umbral=0.3)
    if not similares:
        print("  Sin coincidencias (umbral 0.30).")
        return
    for s in similares:
        print(f"  [{s['proyecto_id']:03d}] sim={s['similitud']:.2f}  "
              f"score={s['score']}  {s['prompt_original'][:60]}")


def main():
    try:
        from sle.core.memory import Memoria
    except ImportError as e:
        print(f"ERROR: No se puede importar el SLE: {e}")
        print("Asegurate de correr este script desde la carpeta 'Estudio Merlos AI'")
        sys.exit(1)

    mem = Memoria()
    mostrar_estadisticas(mem)

    args = sys.argv[1:]

    if "--id" in args:
        idx = args.index("--id")
        try:
            pid = int(args[idx + 1])
            mostrar_proyecto(mem, pid)
        except (IndexError, ValueError):
            print("Uso: python verificar_sle.py --id <número>")

    elif "--buscar" in args:
        idx = args.index("--buscar")
        try:
            prompt = " ".join(args[idx + 1:]) or "casa"
            buscar_similares(mem, prompt)
        except IndexError:
            print("Uso: python verificar_sle.py --buscar <texto>")

    else:
        # Sin args: mostrar todos
        proyectos = mem.listar_proyectos(limite=20)
        if not proyectos:
            print("\n  Sin proyectos guardados aún.")
            print("  Extrae un plano con dwg_to_sle.py y presiona 'Guardar en SLE'.")
            return

        separador(f"Últimos {len(proyectos)} proyecto(s)")
        for p in proyectos:
            print(f"  [{p['id']:03d}] {p['timestamp'][:10]}  score={p['score']:3d}  "
                  f"área={p['area_total_m2']:6.0f}m²  "
                  f"{p['prompt_original'][:55]}")

        print(f"\n  Tip: python verificar_sle.py --id {proyectos[0]['id']}")
        print(f"       python verificar_sle.py --buscar \"2 dormitorios\"")


if __name__ == "__main__":
    main()
