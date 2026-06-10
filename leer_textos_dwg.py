"""
leer_textos_dwg.py
==================
Lee todas las etiquetas de texto del DWG activo en AutoCAD.
Muestra: texto, posición X/Y, capa.

Uso:
    python leer_textos_dwg.py
"""
import win32com.client as win32

def main():
    acad = win32.Dispatch("AutoCAD.Application")
    doc  = acad.ActiveDocument
    print(f"DWG: {doc.Name}\n")

    textos = []
    for ent in doc.ModelSpace:
        try:
            obj = ent.ObjectName
            if obj not in ("AcDbText", "AcDbMText"):
                continue
            texto = ent.TextString.strip().replace("\r", " ").replace("\n", " ")
            ins   = ent.InsertionPoint
            textos.append({
                "texto": texto,
                "x": round(ins[0], 1),
                "y": round(ins[1], 1),
                "capa": ent.Layer,
                "tipo": obj,
            })
        except Exception:
            continue

    # Ordenar por X (nivel izquierdo primero) luego Y descendente
    textos.sort(key=lambda t: (t["x"], -t["y"]))

    print(f"{'TEXTO':<30} {'X':>10} {'Y':>10}  CAPA")
    print("-" * 65)
    for t in textos:
        print(f"  {t['texto']:<28} {t['x']:>10.1f} {t['y']:>10.1f}  {t['capa']}")

    # Detectar separación de niveles
    if textos:
        xs = [t["x"] for t in textos]
        x_min, x_max = min(xs), max(xs)
        x_mid = (x_min + x_max) / 2
        nivel1 = [t for t in textos if t["x"] <= x_mid]
        nivel2 = [t for t in textos if t["x"] >  x_mid]
        print(f"\n  Nivel 1 (izquierda, x <= {x_mid:.0f}): {len(nivel1)} etiquetas")
        print(f"  Nivel 2 (derecha,   x >  {x_mid:.0f}): {len(nivel2)} etiquetas")

if __name__ == "__main__":
    main()
