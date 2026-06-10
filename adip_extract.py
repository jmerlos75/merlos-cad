"""
ADIP - AutoCAD Deep Inspection Plugin (Parte A — COM/Python)
Extrae lo que dxf_import.py no captura completamente:
  1. Hatch holes (NumberOfLoops > 1)
  2. SOLID,_O — identificar qué es
  3. Escalas de patrón vs dxf_scale
  4. Patrones custom — verificar si existen en acad.pat + extraer definición
  5. Inventario de bloques con atributos y recuento de instancias
     (ModelSpace + todos los Layouts/PaperSpace)
  6. TextStyles — font, height, xscale, oblique
  7. DimStyles  — dimtxt, dimasz, dimscale, dimexo/e, dimdec, sufijo, etc.

Salida:
  adip_hatch_audit.json   — hatches, holes, patrones
  adip_block_audit.json   — bloques, inserts (todos los layouts)
  adip_styles_audit.json  — textstyles + dimstyles + defs de patrones custom
"""

import sys, os, json, math, time
from collections import defaultdict, Counter
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

# ── Conexión COM ────────────────────────────────────────────────────────────
try:
    import win32com.client
except ImportError:
    print("ERROR: pywin32 no instalado")
    sys.exit(1)

try:
    acad = win32com.client.GetActiveObject('AutoCAD.Application')
    doc  = acad.ActiveDocument
    msp  = doc.ModelSpace
    print(f"[OK] AutoCAD {acad.Version}  |  {doc.Name}")
except Exception as e:
    print(f"ERROR: No se pudo conectar a AutoCAD: {e}")
    sys.exit(1)

# ── Rutas de acad.pat ── buscar también en los Support Paths de AutoCAD ─────
PAT_SEARCH_PATHS = [
    r"C:\Program Files\Autodesk\AutoCAD 2025\support\acad.pat",
    r"C:\Program Files\Autodesk\AutoCAD 2025\support\acadiso.pat",
    r"C:\Program Files\Autodesk\AutoCAD 2024\support\acad.pat",
    r"C:\Program Files\Autodesk\AutoCAD 2024\support\acadiso.pat",
    Path.home() / r"AppData\Roaming\Autodesk\AutoCAD 2025\R25.0\es-419\support\acad.pat",
    Path.home() / r"AppData\Roaming\Autodesk\AutoCAD 2025\R25.0\en-US\support\acad.pat",
    Path.home() / r"AppData\Roaming\Autodesk\AutoCAD 2024\R24.0\es-419\support\acad.pat",
]

# Agregar Support Paths configurados en AutoCAD (la forma más fiable)
try:
    _acad_support = acad.Preferences.Files.SupportPath
    for _sp in _acad_support.split(';'):
        _sp = _sp.strip()
        if _sp:
            for _pat in Path(_sp).glob("*.pat") if Path(_sp).is_dir() else []:
                if str(_pat) not in [str(p) for p in PAT_SEARCH_PATHS]:
                    PAT_SEARCH_PATHS.append(_pat)
    print(f"[PAT] AutoCAD SupportPath: {len(_acad_support.split(';'))} dirs")
except Exception as _e:
    print(f"[WARN] SupportPath no accesible: {_e}")


def _read_pat_file(path):
    """Lee un .pat y devuelve:
      names  → set de nombres en mayúsculas
      defs   → dict {NAME: [{"angle","x0","y0","dx","dy","dashes":[...]},...]}
    """
    names: set = set()
    defs: dict = {}
    current: str | None = None
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(';'):
                    continue
                if line.startswith('*'):
                    current = line[1:].split(',')[0].strip().upper()
                    names.add(current)
                    defs[current] = []
                elif current is not None:
                    try:
                        parts = [float(x.strip()) for x in line.split(',')]
                        if len(parts) >= 5:
                            defs[current].append({
                                "angle":  parts[0],
                                "x0":     parts[1],
                                "y0":     parts[2],
                                "dx":     parts[3],
                                "dy":     parts[4],
                                "dashes": [round(d, 6) for d in parts[5:]],
                            })
                    except (ValueError, IndexError):
                        pass
    except Exception:
        pass
    return names, defs


known_pat_names: set = set()
all_pat_defs:   dict = {}   # {PATNAME: [line_defs]}
pat_files_found = []

for p in PAT_SEARCH_PATHS:
    if Path(p).exists():
        names, defs = _read_pat_file(p)
        known_pat_names |= names
        for k, v in defs.items():
            if k not in all_pat_defs:
                all_pat_defs[k] = v
        pat_files_found.append(str(p))
        print(f"[PAT] {p}  ({len(names)} patrones)")

if not pat_files_found:
    print("[WARN] No se encontró ningún acad.pat — custom patterns sin verificar")


# ═══════════════════════════════════════════════════════════════════════════
# PARTE 1 — HATCH AUDIT
# ═══════════════════════════════════════════════════════════════════════════
print("\n[HATCH] Iterando ModelSpace...")
t0 = time.time()

hatch_records = []
error_count   = 0
total_ents    = 0

for e in msp:
    total_ents += 1
    try:
        if e.EntityName != 'AcDbHatch':
            continue

        pname    = str(e.PatternName)
        pscale   = float(e.PatternScale)
        pangle   = math.degrees(float(e.PatternAngle))
        ptype    = int(e.PatternType)        # 0=user 1=predefined 2=custom
        obj_type = int(e.HatchObjectType)    # 0=hatch 1=gradient
        loops    = int(e.NumberOfLoops)
        color    = int(e.color)
        layer    = str(e.Layer)
        handle   = str(e.Handle)

        grad_color1 = None
        grad_color2 = None
        if obj_type == 1:
            try:
                grad_color1 = str(e.GradientColor1)
                grad_color2 = str(e.GradientColor2)
            except Exception:
                pass

        loop_data = []
        for li in range(loops):
            try:
                loop_type    = int(e.GetLoopType(li))
                loop_pts_raw = e.GetLoopAtIndex(li)
                pts = []
                if loop_pts_raw is not None:
                    try:
                        flat = list(loop_pts_raw)
                        pts  = [[flat[i], flat[i+1]] for i in range(0, len(flat)-1, 2)]
                    except Exception:
                        pts = []
                loop_data.append({
                    "loop_index":    li,
                    "loop_type":     loop_type,
                    "vertex_count":  len(pts),
                    "sample_pts":    pts[:4],
                })
            except Exception as le:
                loop_data.append({"loop_index": li, "error": str(le)[:80]})

        pname_up    = pname.upper().replace(',_O', '').strip()
        in_pat_file = pname_up in known_pat_names if known_pat_names else None

        rec = {
            "handle":       handle,
            "layer":        layer,
            "color_aci":    color,
            "pattern":      pname,
            "pattern_type": ptype,
            "object_type":  obj_type,
            "scale":        round(pscale, 6),
            "angle_deg":    round(pangle, 4),
            "loops":        loops,
            "has_holes":    loops > 1,
            "loop_detail":  loop_data,
            "in_acad_pat":  in_pat_file,
        }

        if obj_type == 1:
            rec["gradient_color1"] = grad_color1
            rec["gradient_color2"] = grad_color2

        if ',' in pname:
            rec["flag_unusual_name"] = True
        if ptype == 2:
            rec["flag_custom_pattern"] = True
        if loops > 1:
            rec["flag_has_holes"] = True

        hatch_records.append(rec)

    except Exception as ex:
        error_count += 1
        if error_count <= 10:
            print(f"  [ERR hatch] {ex}")

print(f"[HATCH] {len(hatch_records)} hatches en {time.time()-t0:.1f}s  "
      f"| {total_ents} entidades | {error_count} errores")

pat_counter    = Counter(r['pattern'] for r in hatch_records)
holes_count    = sum(1 for r in hatch_records if r['has_holes'])
custom_pats    = {r['pattern'] for r in hatch_records if r.get('flag_custom_pattern')}
unusual_names  = {r['pattern'] for r in hatch_records if r.get('flag_unusual_name')}
gradient_count = sum(1 for r in hatch_records if r['object_type'] == 1)

scales_by_pat = defaultdict(list)
for r in hatch_records:
    scales_by_pat[r['pattern']].append(r['scale'])

pat_scale_summary = {}
for pat, scales in scales_by_pat.items():
    pat_scale_summary[pat] = {
        "count":  len(scales),
        "min":    round(min(scales), 6),
        "max":    round(max(scales), 6),
        "values": sorted(set(round(s, 4) for s in scales)),
    }

hatch_audit = {
    "source":           doc.Name,
    "autocad_version":  acad.Version,
    "total_hatches":    len(hatch_records),
    "total_with_holes": holes_count,
    "total_gradients":  gradient_count,
    "pat_files_used":   pat_files_found,
    "pattern_summary": {
        pat: {
            "count":       cnt,
            "scale_range": pat_scale_summary.get(pat, {}),
            "in_acad_pat": pat.upper().replace(',_O','').strip() in known_pat_names
                           if known_pat_names else None,
            "is_custom":   pat in custom_pats,
            "unusual_name": pat in unusual_names,
        }
        for pat, cnt in pat_counter.most_common()
    },
    "holes_detail":     [r for r in hatch_records if r['has_holes']],
    "unusual_patterns": [r for r in hatch_records if r.get('flag_unusual_name')][:20],
    "all_records":      hatch_records,
}

out_hatch = Path("adip_hatch_audit.json")
with open(out_hatch, 'w', encoding='utf-8') as f:
    json.dump(hatch_audit, f, indent=2, ensure_ascii=False)
print(f"[OK] {out_hatch}  ({out_hatch.stat().st_size // 1024} KB)")


# ═══════════════════════════════════════════════════════════════════════════
# PARTE 2 — BLOCK AUDIT  (ModelSpace + todos los Layouts)
# ═══════════════════════════════════════════════════════════════════════════
print("\n[BLOCK] Leyendo definiciones de bloques...")
t1 = time.time()

block_defs     = {}
insert_count   = Counter()
insert_attribs = defaultdict(list)

try:
    block_table = doc.Blocks
    for blk in block_table:
        bname = str(blk.Name)
        if bname.startswith('*'):
            continue
        ent_types  = Counter()
        attdef_tags = []
        ent_count  = 0
        try:
            for ent in blk:
                ent_count += 1
                etype = str(ent.EntityName)
                ent_types[etype] += 1
                if etype == 'AcDbAttributeDefinition':
                    try:
                        attdef_tags.append({
                            "tag":       str(ent.TagString),
                            "prompt":    str(ent.PromptString),
                            "default":   str(ent.TextString),
                            "invisible": bool(ent.Invisible),
                        })
                    except Exception:
                        pass
        except Exception:
            ent_count = -1

        block_defs[bname] = {
            "name":         bname,
            "entity_count": ent_count,
            "entity_types": dict(ent_types),
            "has_attdefs":  len(attdef_tags) > 0,
            "attdefs":      attdef_tags,
        }
except Exception as bte:
    print(f"  [ERR BlockTable] {bte}")


def _scan_space_for_inserts(space, label: str):
    """Cuenta inserts y lee atributos en un espacio (ModelSpace o Layout.Block)."""
    errs = 0
    count = 0
    try:
        for e in space:
            try:
                if e.EntityName != 'AcDbBlockReference':
                    continue
                bname = str(e.Name)
                insert_count[bname] += 1
                count += 1
                try:
                    attribs = e.GetAttributes()
                    if attribs:
                        tags = {str(a.TagString): str(a.TextString) for a in attribs}
                        insert_attribs[bname].append(tags)
                except Exception:
                    pass
            except Exception:
                errs += 1
    except Exception as se:
        print(f"  [ERR scan {label}] {se}")
    return count, errs


# ModelSpace
print("[BLOCK] Escaneando ModelSpace...")
ms_count, ms_err = _scan_space_for_inserts(msp, "ModelSpace")

# Todos los Layouts (PaperSpace y los nombrados)
print("[BLOCK] Escaneando Layouts (PaperSpace)...")
layout_count = 0
layout_err   = 0
try:
    for layout in doc.Layouts:
        lname = str(layout.Name)
        if lname.lower() == 'model':
            continue   # ya escaneado
        try:
            lc, le = _scan_space_for_inserts(layout.Block, lname)
            layout_count += lc
            layout_err   += le
        except Exception as le2:
            print(f"  [WARN layout '{lname}'] {le2}")
except Exception as lay_e:
    print(f"  [WARN Layouts] {lay_e}")

total_inserts = sum(insert_count.values())
print(f"[BLOCK] {len(block_defs)} defs | "
      f"MS={ms_count} + Layouts={layout_count} = {total_inserts} inserts | "
      f"errs={ms_err+layout_err}")

block_audit = {
    "source":            doc.Name,
    "autocad_version":   acad.Version,
    "total_block_defs":  len(block_defs),
    "total_inserts":     total_inserts,
    "scanned_spaces":    ["ModelSpace", "Layouts/PaperSpace"],
    "blocks":            {},
}

for bname, bdef in sorted(block_defs.items()):
    count          = insert_count.get(bname, 0)
    sample_attribs = insert_attribs.get(bname, [])[:3]
    block_audit["blocks"][bname] = {
        **bdef,
        "insert_count":   count,
        "sample_attribs": sample_attribs,
    }

block_audit["top_blocks"] = [
    {"name": n, "inserts": c}
    for n, c in insert_count.most_common(20)
]

out_block = Path("adip_block_audit.json")
with open(out_block, 'w', encoding='utf-8') as f:
    json.dump(block_audit, f, indent=2, ensure_ascii=False)
print(f"[OK] {out_block}  ({out_block.stat().st_size // 1024} KB)")


# ═══════════════════════════════════════════════════════════════════════════
# PARTE 3 — STYLES AUDIT  (TextStyles + DimStyles + custom pattern defs)
# ═══════════════════════════════════════════════════════════════════════════
print("\n[STYLES] Extrayendo TextStyles...")

# ── TextStyles ──────────────────────────────────────────────────────────────
textstyles_data = []
try:
    for ts in doc.TextStyles:
        try:
            rec = {
                "name":        str(ts.Name),
                "font_file":   str(ts.FontFile),
                "bigfont":     str(ts.BigFontFile) if ts.BigFontFile else "",
                "height":      round(float(ts.Height), 6),
                "x_scale":     round(float(ts.Width),  6),
                "oblique_deg": round(math.degrees(float(ts.ObliqueAngle)), 4),
                "is_shx":      str(ts.FontFile).lower().endswith('.shx'),
            }
            textstyles_data.append(rec)
        except Exception as tse:
            textstyles_data.append({"name": "?", "error": str(tse)[:80]})
except Exception as tse2:
    print(f"  [ERR TextStyles] {tse2}")

print(f"[STYLES] {len(textstyles_data)} TextStyles leídos")

# ── DimStyles — via ezdxf (no requiere COM ni documento editable) ────────────

def _leer_dimstyles_ezdxf(dxf_path: str) -> list:
    """
    Lee DimStyles directamente del DXF/DWG con ezdxf.
    No requiere AutoCAD abierto ni que el documento sea editable.
    """
    try:
        import ezdxf as _ezdxf
    except ImportError:
        return [{"name": "_error", "_error": "ezdxf no instalado"}]

    records = []
    try:
        dxf_doc = _ezdxf.readfile(dxf_path)
    except Exception as e:
        # Si falla con el .dwg, intentar buscar un .dxf exportado al lado
        import os as _os
        dxf_alt = _os.path.splitext(dxf_path)[0] + ".dxf"
        if _os.path.exists(dxf_alt):
            try:
                dxf_doc = _ezdxf.readfile(dxf_alt)
            except Exception as e2:
                return [{"name": "_error",
                         "_error": f"DWG: {e} | DXF alt: {e2}"}]
        else:
            return [{"name": "_error",
                     "_error": f"No se pudo abrir '{dxf_path}': {e}. "
                               f"Exporte a DXF desde AutoCAD y vuelva a correr."}]

    for ds in dxf_doc.dimstyles:
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

    return records

print("[STYLES] Extrayendo DimStyles (via ezdxf, sin COM)...")
dimstyles_data = _leer_dimstyles_ezdxf(str(doc.FullName))
print(f"[STYLES] {len(dimstyles_data)} DimStyles leídos")

# ── Custom pattern definitions ───────────────────────────────────────────────
# Para cada patrón en el DWG que NO esté en acad.pat, guardar su definición
# si la encontramos en algún .pat personalizado; o marcarla como "not found".
print("[STYLES] Extrayendo definiciones de patrones custom...")

custom_pattern_defs = {}
used_patterns = set(pat_counter.keys())

for pname in sorted(used_patterns):
    pname_up = pname.upper().replace(',_O', '').strip()
    if pname_up in known_pat_names:
        # Está en acad.pat — guardar definición de referencia si está disponible
        if pname_up in all_pat_defs and all_pat_defs[pname_up]:
            custom_pattern_defs[pname] = {
                "status":   "found_in_acad_pat",
                "lines":    all_pat_defs[pname_up],
            }
        else:
            custom_pattern_defs[pname] = {"status": "found_in_acad_pat", "lines": []}
    else:
        # Buscar en todos los .pat disponibles
        found_def = all_pat_defs.get(pname_up)
        if found_def:
            custom_pattern_defs[pname] = {
                "status": "found_in_custom_pat",
                "lines":  found_def,
            }
        else:
            custom_pattern_defs[pname] = {
                "status": "not_found",
                "lines":  [],
                "note":   "Patrón personalizado sin .pat accesible — necesita fallback en export",
            }

# Resumen de los que faltan
missing_pats = {p: d for p, d in custom_pattern_defs.items() if d["status"] == "not_found"}
found_pats   = {p: d for p, d in custom_pattern_defs.items() if d["status"] != "not_found"}
print(f"[STYLES] Patrones: {len(found_pats)} con definición | {len(missing_pats)} sin .pat")
if missing_pats:
    print(f"  Sin definición: {', '.join(sorted(missing_pats))}")

# ── Ensamblar salida ─────────────────────────────────────────────────────────
styles_audit = {
    "source":          doc.Name,
    "autocad_version": acad.Version,
    "textstyles": {
        "count": len(textstyles_data),
        "records": textstyles_data,
    },
    "dimstyles": {
        "count": len(dimstyles_data),
        "records": dimstyles_data,
    },
    "hatch_patterns": {
        "total_used":      len(used_patterns),
        "found_in_pat":    len(found_pats),
        "not_found":       len(missing_pats),
        "missing_list":    sorted(missing_pats.keys()),
        "definitions":     custom_pattern_defs,
    },
}

out_styles = Path("adip_styles_audit.json")
with open(out_styles, 'w', encoding='utf-8') as f:
    json.dump(styles_audit, f, indent=2, ensure_ascii=False)
print(f"[OK] {out_styles}  ({out_styles.stat().st_size // 1024} KB)")


# ═══════════════════════════════════════════════════════════════════════════
# PARTE 4 — LINETYPE AUDIT
# ═══════════════════════════════════════════════════════════════════════════
print("\n[LTYPE] Extrayendo linetypes del documento...")

_ACAD_STANDARD_LTYPES = {
    "BYLAYER", "BYBLOCK", "CONTINUOUS",
    "CENTER", "CENTER2", "CENTERX2",
    "DASHED", "DASHED2", "DASHEDX2",
    "DASHDOT", "DASHDOT2", "DASHDOTX2",
    "DIVIDE", "DIVIDE2", "DIVIDEX2",
    "DOT", "DOT2", "DOTX2",
    "HIDDEN", "HIDDEN2", "HIDDENX2",
    "PHANTOM", "PHANTOM2", "PHANTOMX2",
    "BORDER", "BORDER2", "BORDERX2",
    "ACAD_ISO02W100", "ACAD_ISO03W100", "ACAD_ISO04W100",
    "ACAD_ISO05W100", "ACAD_ISO06W100", "ACAD_ISO07W100",
    "ACAD_ISO08W100", "ACAD_ISO09W100", "ACAD_ISO10W100",
    "ACAD_ISO11W100", "ACAD_ISO12W100", "ACAD_ISO13W100",
    "ACAD_ISO14W100", "ACAD_ISO15W100",
}

ltype_records = []
ltype_errors  = 0
try:
    for lt in doc.Linetypes:
        try:
            name        = str(lt.Name)
            description = str(lt.Description) if lt.Description else ""
            n_dashes    = int(lt.NumberOfDashes)
            is_standard = name.upper() in _ACAD_STANDARD_LTYPES
            is_complex  = False

            segments = []
            try:
                for i in range(n_dashes):
                    seg = round(float(lt.GetDashLengthAt(i)), 6)
                    segments.append(seg)
            except Exception:
                segments = []

            try:
                is_complex = lt.HasTextElement or lt.HasShapeElement
            except Exception:
                is_complex = False

            ltype_records.append({
                "name":        name,
                "description": description,
                "n_dashes":    n_dashes,
                "segments":    segments,
                "is_standard": is_standard,
                "is_complex":  is_complex,
            })
        except Exception:
            ltype_errors += 1
except Exception as lte2:
    print(f"  [ERR Linetypes] {lte2}")

custom_ltypes   = [r for r in ltype_records if not r["is_standard"]]
standard_ltypes = [r for r in ltype_records if r["is_standard"]]
complex_ltypes  = [r for r in ltype_records if r.get("is_complex")]

print(f"[LTYPE] {len(ltype_records)} total | {len(standard_ltypes)} estándar | "
      f"{len(custom_ltypes)} custom | {len(complex_ltypes)} complejos")
for lt in custom_ltypes:
    print(f"  CUSTOM: {lt['name']!r:30}  segs={lt['segments']}")
for lt in complex_ltypes:
    print(f"  COMPLEJO: {lt['name']!r:28}  desc={lt['description'][:40]!r}")

ltype_audit = {
    "total":          len(ltype_records),
    "standard_count": len(standard_ltypes),
    "custom_count":   len(custom_ltypes),
    "complex_count":  len(complex_ltypes),
    "custom":         custom_ltypes,
    "complex":        complex_ltypes,
    "all_records":    ltype_records,
}

out_ltype = Path("adip_ltype_audit.json")
with open(out_ltype, 'w', encoding='utf-8') as f:
    json.dump(ltype_audit, f, indent=2, ensure_ascii=False)
print(f"[OK] {out_ltype}  ({out_ltype.stat().st_size // 1024} KB)")


# ═══════════════════════════════════════════════════════════════════════════
# PARTE 5 — LAYOUT AUDIT  (PaperSpace + viewports)
# ═══════════════════════════════════════════════════════════════════════════
print("\n[LAYOUT] Extrayendo layouts y viewports...")

layout_records = []
try:
    for layout in doc.Layouts:
        lname    = str(layout.Name)
        is_model = lname.lower() == 'model'

        rec = {"name": lname, "is_model": is_model, "viewports": []}

        if not is_model:
            try:
                rec["paper_width"]  = round(float(layout.PaperWidth),  4)
                rec["paper_height"] = round(float(layout.PaperHeight), 4)
                rec["plot_style"]   = str(layout.StyleSheet) if layout.StyleSheet else ""
                if hasattr(layout, "StandardScale"):
                    rec["scale_factor"] = round(float(layout.StandardScale), 6)
            except Exception as le:
                rec["_layout_error"] = str(le)[:80]

            try:
                for e in layout.Block:
                    try:
                        if str(e.EntityName) != 'AcDbViewport':
                            continue
                        scale = 0.0
                        try:
                            scale = round(float(e.CustomScale), 8)
                        except Exception:
                            pass
                        vp_rec = {
                            "center_x":     round(float(e.CenterPoint[0]), 4),
                            "center_y":     round(float(e.CenterPoint[1]), 4),
                            "width":        round(float(e.Width),  4),
                            "height":       round(float(e.Height), 4),
                            "custom_scale": scale,
                            "on":           bool(e.On) if hasattr(e, "On") else True,
                        }
                        frozen = []
                        try:
                            for lyr in doc.Layers:
                                if e.IsFrozenLayer(lyr.Name):
                                    frozen.append(str(lyr.Name))
                        except Exception:
                            pass
                        vp_rec["frozen_layers"] = frozen
                        rec["viewports"].append(vp_rec)
                    except Exception:
                        pass
            except Exception as vpe:
                rec["_viewport_error"] = str(vpe)[:80]

        layout_records.append(rec)
except Exception as laye:
    print(f"  [ERR Layouts] {laye}")

paper_layouts   = [l for l in layout_records if not l["is_model"]]
total_viewports = sum(len(l["viewports"]) for l in paper_layouts)
print(f"[LAYOUT] {len(paper_layouts)} PaperSpace layout(s) | {total_viewports} viewport(s)")
for lay in paper_layouts:
    w = lay.get("paper_width", "?")
    h = lay.get("paper_height", "?")
    scales = [vp["custom_scale"] for vp in lay["viewports"] if vp["custom_scale"] != 0]
    print(f"  '{lay['name']}'  paper={w}×{h}  vp={len(lay['viewports'])}  escalas={scales}")

layout_audit = {
    "total_layouts":   len(layout_records),
    "paper_layouts":   len(paper_layouts),
    "total_viewports": total_viewports,
    "records":         layout_records,
}

out_layout = Path("adip_layout_audit.json")
with open(out_layout, 'w', encoding='utf-8') as f:
    json.dump(layout_audit, f, indent=2, ensure_ascii=False)
print(f"[OK] {out_layout}  ({out_layout.stat().st_size // 1024} KB)")


# ═══════════════════════════════════════════════════════════════════════════
# PARTE 6 — VISUAL PROPERTIES AUDIT  (WIPEOUT, IMAGE, transparencia, ltscale)
# ═══════════════════════════════════════════════════════════════════════════
print("\n[VISUAL] Auditando propiedades visuales (transparencia, ltscale, WIPEOUT, IMAGE)...")

visual_stats = {
    "wipeouts":                       [],
    "images":                         [],
    "transparent_layers":             [],
    "entities_with_transparency":     0,
    "entities_with_ltscale_override": 0,
    "total_scanned":                  0,
}

try:
    for lyr in doc.Layers:
        try:
            t = int(lyr.Transparency)
            if t > 0:
                visual_stats["transparent_layers"].append(
                    {"name": str(lyr.Name), "transparency": t})
        except Exception:
            pass
except Exception as te:
    print(f"  [WARN] Transparencia capas: {te}")

try:
    for e in msp:
        visual_stats["total_scanned"] += 1
        etype = str(e.EntityName)

        if etype == 'AcDbWipeout':
            try:
                bb = e.GetBoundingBox()
                visual_stats["wipeouts"].append({
                    "layer": str(e.Layer),
                    "x": round(float(bb[0][0]), 4), "y": round(float(bb[0][1]), 4),
                    "w": round(float(bb[1][0] - bb[0][0]), 4),
                    "h": round(float(bb[1][1] - bb[0][1]), 4),
                })
            except Exception:
                visual_stats["wipeouts"].append({"layer": str(e.Layer)})

        elif etype == 'AcDbRasterImage':
            try:
                visual_stats["images"].append({
                    "layer": str(e.Layer),
                    "path":  str(e.ImageFile) if hasattr(e, 'ImageFile') else "?",
                })
            except Exception:
                visual_stats["images"].append({"layer": str(e.Layer)})

        try:
            if int(e.Transparency) > 0:
                visual_stats["entities_with_transparency"] += 1
        except Exception:
            pass

        try:
            if abs(float(e.LinetypeScale) - 1.0) > 0.001:
                visual_stats["entities_with_ltscale_override"] += 1
        except Exception:
            pass

except Exception as vse:
    print(f"  [WARN] Scan visual: {vse}")

try:
    active_layout = doc.ActiveLayout
    visual_stats["plot_style_sheet"] = str(active_layout.StyleSheet) or ""
    visual_stats["plot_style_type"]  = (
        "CTB" if (active_layout.StyleSheet or "").lower().endswith(".ctb") else "STB")
except Exception:
    visual_stats["plot_style_sheet"] = ""
    visual_stats["plot_style_type"]  = "unknown"

print(f"[VISUAL] WIPEOUT={len(visual_stats['wipeouts'])} | "
      f"IMAGE={len(visual_stats['images'])} | "
      f"capas transparentes={len(visual_stats['transparent_layers'])} | "
      f"ents transparency={visual_stats['entities_with_transparency']} | "
      f"ltscale overrides={visual_stats['entities_with_ltscale_override']}")
print(f"[VISUAL] Plot style: {visual_stats['plot_style_sheet'] or '(ninguno)'}")

out_visual = Path("adip_visual_audit.json")
with open(out_visual, 'w', encoding='utf-8') as f:
    json.dump(visual_stats, f, indent=2, ensure_ascii=False)
print(f"[OK] {out_visual}  ({out_visual.stat().st_size // 1024} KB)")


# ── Resumen en consola ───────────────────────────────────────────────────────
print("\n" + "="*60)
print("RESUMEN HATCH AUDIT")
print("="*60)
print(f"  Total hatches   : {len(hatch_records)}")
print(f"  Con holes       : {holes_count}")
print(f"  Gradientes      : {gradient_count}")
print(f"\n  {'Patrón':<22} {'n':>4}   in_pat  custom  unusual")
print(f"  {'-'*54}")
for pat, info in hatch_audit['pattern_summary'].items():
    f_p = "SI " if info['in_acad_pat'] else ("NO " if info['in_acad_pat'] is False else "?  ")
    f_c = "SI " if info['is_custom']   else "   "
    f_u = "!" if info['unusual_name']  else " "
    print(f"  {pat:<22} {info['count']:>4}   {f_p}     {f_c}     {f_u}")

print("\n" + "="*60)
print("RESUMEN BLOCK AUDIT")
print("="*60)
print(f"  Definiciones    : {len(block_defs)}")
print(f"  Inserts totales : {total_inserts}  (MS + Layouts)")
print(f"\n  Top 10 bloques más usados:")
for item in block_audit['top_blocks'][:10]:
    has_att = "  [ATTRIBS]" if block_defs.get(item['name'], {}).get('has_attdefs') else ""
    print(f"    {item['name']:<32}  ×{item['inserts']}{has_att}")

print("\n" + "="*60)
print("RESUMEN STYLES AUDIT")
print("="*60)
print(f"  TextStyles      : {len(textstyles_data)}")
for ts in textstyles_data:
    if ts.get('name') not in ('', 'Standard'):
        print(f"    {ts['name']:<20}  font={ts.get('font_file','')}  h={ts.get('height',0)}")
print(f"\n  DimStyles       : {len(dimstyles_data)}")
for ds in dimstyles_data:
    ds_name = ds.get('name', '?')
    if ds.get('_error'):
        print(f"    [ERROR] {ds['_error'][:80]}")
    else:
        print(f"    {ds_name:<20}  dimtxt={ds.get('dimtxt','?')}  "
              f"dimasz={ds.get('dimasz','?')}  scale={ds.get('dimscale','?')}")
print(f"\n  Patrones con definición  : {len(found_pats)}")
print(f"  Patrones SIN definición  : {len(missing_pats)}")
if missing_pats:
    for p in sorted(missing_pats):
        print(f"    ⚠  {p}  ({pat_counter.get(p,0)} instancias)")

print(f"\n[DONE] Archivos generados:")
print(f"  {out_hatch.resolve()}")
print(f"  {out_block.resolve()}")
print(f"  {out_styles.resolve()}")
print(f"  {out_ltype.resolve()}")
print(f"  {out_layout.resolve()}")
print(f"  {out_visual.resolve()}")
