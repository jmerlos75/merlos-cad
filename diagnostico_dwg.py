"""
diagnostico_dwg.py
==================
Diagnóstico rápido del DWG activo en AutoCAD.
Muestra: capas, tipos de entidad, conteos.

Uso:
    python diagnostico_dwg.py
"""
import win32com.client as win32
from collections import defaultdict

def main():
    try:
        acad = win32.Dispatch("AutoCAD.Application")
        doc  = acad.ActiveDocument
        print(f"[OK] AutoCAD: {acad.Name}")
        print(f"     DWG activo: {doc.Name}")
        print(f"     Ruta: {doc.FullName}")
    except Exception as e:
        print(f"[ERR] No se pudo conectar a AutoCAD: {e}")
        return

    mspace = doc.ModelSpace

    # Conteo por capa y tipo
    capas   = defaultdict(int)         # capa -> total entidades
    tipos   = defaultdict(int)         # ObjectName -> total
    por_capa_tipo = defaultdict(lambda: defaultdict(int))  # capa -> tipo -> n

    total = 0
    for ent in mspace:
        try:
            layer = ent.Layer
            tipo  = ent.ObjectName
            capas[layer] += 1
            tipos[tipo]  += 1
            por_capa_tipo[layer][tipo] += 1
            total += 1
        except Exception:
            continue

    print(f"\n{'='*55}")
    print(f"  TOTAL entidades en ModelSpace: {total}")
    print(f"{'='*55}")

    print(f"\n  CAPAS ({len(capas)}):")
    for capa, n in sorted(capas.items(), key=lambda x: -x[1]):
        tipos_en_capa = ", ".join(f"{t}:{c}" for t, c in por_capa_tipo[capa].items())
        print(f"    {capa:<20} {n:>4} ent  [{tipos_en_capa}]")

    print(f"\n  TIPOS DE ENTIDAD:")
    for tipo, n in sorted(tipos.items(), key=lambda x: -x[1]):
        print(f"    {tipo:<30} {n:>4}")

    # Polilíneas cerradas específicamente
    print(f"\n  POLILINEAS CERRADAS por capa:")
    poly_types = {"AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline",
                  "AcDbLwPolyline"}
    cerradas = defaultdict(int)
    for ent in mspace:
        try:
            if ent.ObjectName not in poly_types:
                continue
            if ent.Closed:
                cerradas[ent.Layer] += 1
        except Exception:
            continue

    if cerradas:
        for capa, n in sorted(cerradas.items(), key=lambda x: -x[1]):
            print(f"    {capa:<20} {n:>4} polilíneas cerradas")
    else:
        print("    (ninguna polilínea cerrada encontrada)")

    # Líneas (LINE) por capa
    print(f"\n  LINEAS (LINE) por capa:")
    lineas = defaultdict(int)
    for ent in mspace:
        try:
            if ent.ObjectName == "AcDbLine":
                lineas[ent.Layer] += 1
        except Exception:
            continue

    if lineas:
        for capa, n in sorted(lineas.items(), key=lambda x: -x[1]):
            print(f"    {capa:<20} {n:>4} lineas")
    else:
        print("    (ninguna línea encontrada)")

if __name__ == "__main__":
    main()
