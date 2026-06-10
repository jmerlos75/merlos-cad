"""
adip_fix_dimstyles.py
Extrae los DimStyles del DXF exportado y actualiza adip_styles_audit.json.

Uso:
  1. En AutoCAD: SAVEAS → DXF 2018 → misma carpeta, mismo nombre que el DWG
  2. python adip_fix_dimstyles.py
"""
import json, sys, glob, os
from pathlib import Path

# ── Buscar el DXF en la carpeta del proyecto ────────────────────────────────
PROJECT_DIR = Path(__file__).parent
dxf_files = sorted(PROJECT_DIR.glob("*.dxf"))

if not dxf_files:
    print("ERROR: No se encontró ningún .dxf en la carpeta del proyecto.")
    print("  En AutoCAD: SAVEAS → DXF 2018 → guardar en:")
    print(f"  {PROJECT_DIR}")
    sys.exit(1)

dxf_path = dxf_files[0]
print(f"[DXF] Usando: {dxf_path.name}")

# ── Leer DimStyles con ezdxf ─────────────────────────────────────────────────
try:
    import ezdxf
except ImportError:
    print("ERROR: ezdxf no instalado. Ejecute: pip install ezdxf")
    sys.exit(1)

try:
    doc = ezdxf.readfile(str(dxf_path))
except Exception as e:
    print(f"ERROR al abrir {dxf_path.name}: {e}")
    sys.exit(1)

records = []
for ds in doc.dimstyles:
    rec = {"name": ds.dxf.get("name", "Standard")}
    try:
        dxf = ds.dxf
        rec["dimtxt"]   = round(float(dxf.get("dimtxt",   0.25)),   6)
        rec["dimasz"]   = round(float(dxf.get("dimasz",   0.18)),   6)
        rec["dimscale"] = round(float(dxf.get("dimscale", 1.0)),    6)
        rec["dimexo"]   = round(float(dxf.get("dimexo",   0.0625)), 6)
        rec["dimexe"]   = round(float(dxf.get("dimexe",   0.18)),   6)
        rec["dimdle"]   = round(float(dxf.get("dimdle",   0.0)),    6)
        rec["dimgap"]   = round(float(dxf.get("dimgap",   0.09)),   6)
        rec["dimtad"]   = int(dxf.get("dimtad",   1))
        rec["dimdec"]   = int(dxf.get("dimdec",   2))
        rec["dimdsep"]  = str(dxf.get("dimdsep",  "."))
        rec["dimpost"]  = str(dxf.get("dimpost",  ""))
        rec["dimlunit"] = int(dxf.get("dimlunit", 2))
        rec["dimaunit"] = int(dxf.get("dimaunit", 0))
        rec["dimblk"]   = str(dxf.get("dimblk",   ""))
        rec["dimblk1"]  = str(dxf.get("dimblk1",  ""))
        rec["dimblk2"]  = str(dxf.get("dimblk2",  ""))
        rec["dimclrt"]  = int(dxf.get("dimclrt",  256))
        rec["dimclrd"]  = int(dxf.get("dimclrd",  256))
        rec["dimclre"]  = int(dxf.get("dimclre",  256))
        rec["dimtxsty"] = str(dxf.get("dimtxsty", "Standard"))
        rec["_source"]  = "ezdxf"
    except Exception as ex:
        rec["_error"] = str(ex)[:120]
    records.append(rec)

print(f"[DIM] {len(records)} DimStyles encontrados:")
for r in records:
    if r.get("_error"):
        print(f"  [ERR] {r['name']}: {r['_error']}")
    else:
        print(f"  {r['name']:<25}  txt={r.get('dimtxt','?')}  "
              f"asz={r.get('dimasz','?')}  scale={r.get('dimscale','?')}")

# ── Actualizar adip_styles_audit.json ────────────────────────────────────────
styles_path = PROJECT_DIR / "adip_styles_audit.json"
if not styles_path.exists():
    print(f"\nERROR: {styles_path.name} no existe. Corra primero adip_extract.py.")
    sys.exit(1)

with open(styles_path, encoding="utf-8") as f:
    audit = json.load(f)

audit["dimstyles"] = {
    "count":   len(records),
    "records": records,
}

with open(styles_path, "w", encoding="utf-8") as f:
    json.dump(audit, f, indent=2, ensure_ascii=False)

print(f"\n[OK] {styles_path.name} actualizado con {len(records)} DimStyles.")
