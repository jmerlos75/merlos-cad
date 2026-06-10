# HANDOFF — ADIP Parte 2: Extracciones faltantes + fixes de import

## Contexto del proyecto

Aplicación de escritorio Python (tkinter + PIL/OpenGL) que actúa como editor CAD.
Ruta del proyecto: `C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI\`

Archivos a modificar en esta sesión:
- `adip_extract.py`       — agregar Partes 4, 5, 6 y fix de Parte 3
- `cad/dxf_import.py`    — fix WIPEOUT

---

## Estado actual de los ADIP

Ya existen y están correctos — NO tocar:
- `adip_hatch_audit.json`  — 788 hatches, 434 holes, 40 gradients ✅
- `adip_block_audit.json`  — 118 bloques, 982 inserts ✅
- `adip_styles_audit.json` — 49 TextStyles ✅, 15 DimStyles FALLIDOS ❌, 6 patrones sin .pat ❌

La extracción de DimStyles falló porque:
```python
doc.SetVariable("DIMSTYLE", ds_name)  # AutoCAD lanza excepción — DWG solo lectura
```
El script tiene esa lógica en PARTE 3 (~línea 455-480).

---

## TAREA 1 — Fix PARTE 3: DimStyles vía ezdxf (sin COM)

### Dónde está el problema
En `adip_extract.py`, PARTE 3 (~línea 454-481):
```python
for ds in doc.DimStyles:
    doc.SetVariable("DIMSTYLE", ds_name)   # ← esto falla siempre
    for var in DIMVARS_NEEDED:
        val = doc.GetVariable(var)
```

### Cómo arreglarlo
Agregar al inicio de `adip_extract.py` (antes de PARTE 3), justo después de que
ya terminó de conectar via COM, una función que lee los DimStyles directamente
del archivo DXF con ezdxf — sin necesidad de que AutoCAD esté abierto:

```python
def _leer_dimstyles_ezdxf(dxf_path: str) -> list:
    """
    Lee DimStyles directamente del DXF con ezdxf.
    No requiere AutoCAD abierto ni COM.
    Retorna lista de dicts con los parámetros de cada estilo.
    """
    try:
        import ezdxf
    except ImportError:
        return [{"_error": "ezdxf no instalado"}]

    records = []
    try:
        dxf_doc = ezdxf.readfile(dxf_path)
    except Exception as e:
        return [{"_error": f"No se pudo abrir el DXF: {e}"}]

    for ds in dxf_doc.dimstyles:
        rec = {"name": ds.name}
        try:
            dxf = ds.dxf
            rec["dimtxt"]   = round(float(dxf.get("dimtxt",   0.25)), 6)
            rec["dimasz"]   = round(float(dxf.get("dimasz",   0.18)), 6)
            rec["dimscale"] = round(float(dxf.get("dimscale", 1.0)),  6)
            rec["dimexo"]   = round(float(dxf.get("dimexo",   0.0625)), 6)
            rec["dimexe"]   = round(float(dxf.get("dimexe",   0.18)),  6)
            rec["dimdle"]   = round(float(dxf.get("dimdle",   0.0)),   6)
            rec["dimgap"]   = round(float(dxf.get("dimgap",   0.09)),  6)
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
```

### Dónde llamarla
Buscar en `adip_extract.py` la sección de PARTE 3 donde se arma `styles_audit`:
```python
styles_audit = {
    ...
    "dimstyles": {
        "count": len(dimstyles_data),
        "records": dimstyles_data,   # ← estos son los que fallan por COM
    },
    ...
}
```

**Reemplazar** `dimstyles_data` con la función nueva:
```python
# Obtener la ruta del DXF del documento activo
# doc.FullName devuelve la ruta completa del DWG/DXF abierto
_dxf_path = str(doc.FullName)
dimstyles_data = _leer_dimstyles_ezdxf(_dxf_path)
print(f"[STYLES] {len(dimstyles_data)} DimStyles leídos via ezdxf")
```

**Importante**: si `doc.FullName` devuelve un `.dwg`, ezdxf puede leerlo igual —
soporta DWG desde la versión 0.18. Si falla, decirle al usuario que exporte a DXF
primero y pasar esa ruta.

### Salida esperada en adip_styles_audit.json
```json
"dimstyles": {
  "count": 15,
  "records": [
    {
      "name": "ISO-25",
      "dimtxt": 0.25,
      "dimasz": 0.18,
      "dimscale": 1.0,
      "dimexo": 0.0625,
      "dimexe": 0.18,
      "dimgap": 0.09,
      "dimtxsty": "ROMANS",
      "_source": "ezdxf"
    },
    ...
  ]
}
```

---

## TAREA 2 — PARTE 4 nueva: Audit de Linetypes

### Por qué importa
El DWG puede usar linetypes que no están en acad.lin estándar (similar al problema
de los patrones custom). `setup_linetypes()` en el export carga los 70 estándar,
pero si el DWG tiene custom → se exporta como `Continuous`.

### Dónde agregar
En `adip_extract.py`, después de PARTE 3 (al final del archivo, antes del resumen).
Agregar una **PARTE 4 — LINETYPE AUDIT**:

```python
# ═══════════════════════════════════════════════════════════════════════════
# PARTE 4 — LINETYPE AUDIT
# ═══════════════════════════════════════════════════════════════════════════
print("\n[LTYPE] Extrayendo linetypes del documento...")

# Linetypes estándar de AutoCAD (no necesitan extracción especial)
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
            n_dashes    = int(lt.NumberOfDashes)  # 0 = linea continua
            is_standard = name.upper() in _ACAD_STANDARD_LTYPES
            is_complex  = False  # linetypes con texto o shapes embebidos

            # Intentar leer la definición (segmentos)
            segments = []
            try:
                for i in range(n_dashes):
                    seg = round(float(lt.GetDashLengthAt(i)), 6)
                    segments.append(seg)
            except Exception:
                segments = []

            # Detectar linetypes complejos (tienen texto o shapes)
            try:
                is_complex = lt.HasTextElement or lt.HasShapeElement
            except Exception:
                is_complex = False

            rec = {
                "name":         name,
                "description":  description,
                "n_dashes":     n_dashes,
                "segments":     segments,   # longitudes + (positivo=línea, negativo=hueco, 0=punto)
                "is_standard":  is_standard,
                "is_complex":   is_complex,
            }
            ltype_records.append(rec)
        except Exception as lte:
            ltype_errors += 1

except Exception as lte2:
    print(f"  [ERR Linetypes] {lte2}")

# Separar custom de estándar
custom_ltypes   = [r for r in ltype_records if not r["is_standard"]]
standard_ltypes = [r for r in ltype_records if r["is_standard"]]
complex_ltypes  = [r for r in ltype_records if r.get("is_complex")]

print(f"[LTYPE] {len(ltype_records)} total | "
      f"{len(standard_ltypes)} estándar | "
      f"{len(custom_ltypes)} custom | "
      f"{len(complex_ltypes)} complejos (texto/shapes)")
if custom_ltypes:
    for lt in custom_ltypes:
        print(f"  CUSTOM: {lt['name']!r:30}  segs={lt['segments']}")
if complex_ltypes:
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
```

### Guardar en JSON separado
Al final de PARTE 4, agregar al dict de salida y guardarlo:
```python
out_ltype = Path("adip_ltype_audit.json")
with open(out_ltype, 'w', encoding='utf-8') as f:
    json.dump(ltype_audit, f, indent=2, ensure_ascii=False)
print(f"[OK] {out_ltype}  ({out_ltype.stat().st_size // 1024} KB)")
```

### Salida esperada
```json
{
  "total": 22,
  "standard_count": 18,
  "custom_count": 4,
  "complex_count": 1,
  "custom": [
    {
      "name": "LONG-DASH",
      "description": "Dash long ____  ____",
      "n_dashes": 2,
      "segments": [12.0, -3.0],
      "is_standard": false,
      "is_complex": false
    }
  ],
  "complex": [
    {
      "name": "GAS_LINE",
      "description": "Gas line ----GAS----GAS----",
      "is_complex": true
    }
  ]
}
```

---

## TAREA 3 — PARTE 5 nueva: Audit de Layouts / PaperSpace

### Por qué importa
Los planos de permiso tienen múltiples hojas (layouts). Cada layout tiene:
- Tamaño de papel
- Escala de los viewports (1:50, 1:100, etc.)
- Capas congeladas por viewport

Sin esto no se puede regenerar los PDFs con las mismas escalas que el DWG original.

### Dónde agregar
Después de PARTE 4, agregar **PARTE 5 — LAYOUT AUDIT**:

```python
# ═══════════════════════════════════════════════════════════════════════════
# PARTE 5 — LAYOUT AUDIT (PaperSpace + viewports)
# ═══════════════════════════════════════════════════════════════════════════
print("\n[LAYOUT] Extrayendo layouts y viewports...")

layout_records = []
try:
    for layout in doc.Layouts:
        lname = str(layout.Name)
        is_model = lname.lower() == 'model'

        rec = {
            "name":       lname,
            "is_model":   is_model,
            "viewports":  [],
        }

        if not is_model:
            try:
                rec["paper_width"]  = round(float(layout.PaperWidth),  4)
                rec["paper_height"] = round(float(layout.PaperHeight), 4)
                rec["plot_style"]   = str(layout.StyleSheet) if layout.StyleSheet else ""
                rec["scale_factor"] = round(float(layout.StandardScale), 6) if hasattr(layout, "StandardScale") else None
            except Exception as le:
                rec["_layout_error"] = str(le)[:80]

            # Iterar viewports en el layout
            try:
                for e in layout.Block:
                    try:
                        if str(e.EntityName) != 'AcDbViewport':
                            continue
                        # CustomScale = 0 significa "escala estándar"
                        # Para obtener la escala real hay que leer StandardScale
                        scale = 0.0
                        try:
                            scale = round(float(e.CustomScale), 8)
                        except Exception:
                            pass
                        vp_rec = {
                            "center_x":    round(float(e.CenterPoint[0]), 4),
                            "center_y":    round(float(e.CenterPoint[1]), 4),
                            "width":       round(float(e.Width),  4),
                            "height":      round(float(e.Height), 4),
                            "custom_scale": scale,
                            "on":          bool(e.On) if hasattr(e, "On") else True,
                        }
                        # Capas congeladas en este viewport
                        frozen = []
                        try:
                            for lyr in doc.Layers:
                                if e.IsFrozenLayer(lyr.Name):
                                    frozen.append(str(lyr.Name))
                            vp_rec["frozen_layers"] = frozen
                        except Exception:
                            vp_rec["frozen_layers"] = []
                        rec["viewports"].append(vp_rec)
                    except Exception:
                        pass
            except Exception as vpe:
                rec["_viewport_error"] = str(vpe)[:80]

        layout_records.append(rec)

except Exception as laye:
    print(f"  [ERR Layouts] {laye}")

model_layout     = next((l for l in layout_records if l["is_model"]), None)
paper_layouts    = [l for l in layout_records if not l["is_model"]]
total_viewports  = sum(len(l["viewports"]) for l in paper_layouts)

print(f"[LAYOUT] {len(paper_layouts)} PaperSpace layout(s) | "
      f"{total_viewports} viewport(s) total")
for lay in paper_layouts:
    w = lay.get("paper_width", "?")
    h = lay.get("paper_height", "?")
    n_vp = len(lay["viewports"])
    scales = [vp["custom_scale"] for vp in lay["viewports"] if vp["custom_scale"] != 0]
    print(f"  '{lay['name']}'  paper={w}×{h}  viewports={n_vp}  escalas={scales}")

layout_audit = {
    "total_layouts":    len(layout_records),
    "paper_layouts":    len(paper_layouts),
    "total_viewports":  total_viewports,
    "records":          layout_records,
}

out_layout = Path("adip_layout_audit.json")
with open(out_layout, 'w', encoding='utf-8') as f:
    json.dump(layout_audit, f, indent=2, ensure_ascii=False)
print(f"[OK] {out_layout}  ({out_layout.stat().st_size // 1024} KB)")
```

---

## TAREA 4 — PARTE 6 nueva: Audit de propiedades visuales faltantes

Agregar **PARTE 6 — VISUAL PROPERTIES AUDIT** para auditar cuántas entidades
usan transparencia, ltscale overrides, y para inventariar WIPEOUT e IMAGE:

```python
# ═══════════════════════════════════════════════════════════════════════════
# PARTE 6 — VISUAL PROPERTIES AUDIT
# ═══════════════════════════════════════════════════════════════════════════
print("\n[VISUAL] Auditando propiedades visuales (transparencia, ltscale, WIPEOUT, IMAGE)...")

visual_stats = {
    "wipeouts":      [],   # lista de {layer, x, y, w, h}
    "images":        [],   # lista de {layer, path, x, y, w, h}
    "transparent_layers": [],  # capas con transparencia > 0
    "entities_with_transparency": 0,
    "entities_with_ltscale_override": 0,
    "total_scanned": 0,
}

# ── Transparencia por capas ──────────────────────────────────────────────
try:
    for lyr in doc.Layers:
        try:
            t = int(lyr.Transparency)  # 0-90
            if t > 0:
                visual_stats["transparent_layers"].append({
                    "name":         str(lyr.Name),
                    "transparency": t,
                })
        except Exception:
            pass
except Exception as te:
    print(f"  [WARN] Transparencia capas: {te}")

# ── Scan de entidades en ModelSpace ────────────────────────────────────
try:
    for e in msp:
        visual_stats["total_scanned"] += 1
        etype = str(e.EntityName)

        # WIPEOUT — inventariar
        if etype == 'AcDbWipeout':
            try:
                bb = e.GetBoundingBox()   # (min_pt, max_pt)
                visual_stats["wipeouts"].append({
                    "layer": str(e.Layer),
                    "x":     round(float(bb[0][0]), 4),
                    "y":     round(float(bb[0][1]), 4),
                    "w":     round(float(bb[1][0] - bb[0][0]), 4),
                    "h":     round(float(bb[1][1] - bb[0][1]), 4),
                })
            except Exception:
                visual_stats["wipeouts"].append({"layer": str(e.Layer)})

        # IMAGE — inventariar rutas
        elif etype == 'AcDbRasterImage':
            try:
                visual_stats["images"].append({
                    "layer": str(e.Layer),
                    "path":  str(e.ImageFile) if hasattr(e, 'ImageFile') else "?",
                })
            except Exception:
                visual_stats["images"].append({"layer": str(e.Layer)})

        # Transparencia por entidad
        try:
            t = int(e.Transparency)
            if t > 0:
                visual_stats["entities_with_transparency"] += 1
        except Exception:
            pass

        # LTSCALE override por entidad
        try:
            ltsc = float(e.LinetypeScale)
            if abs(ltsc - 1.0) > 0.001:
                visual_stats["entities_with_ltscale_override"] += 1
        except Exception:
            pass

except Exception as vse:
    print(f"  [WARN] Scan visual: {vse}")

# ── Plot style (CTB/STB) del documento ─────────────────────────────────
try:
    active_layout = doc.ActiveLayout
    visual_stats["plot_style_sheet"] = str(active_layout.StyleSheet) or ""
    visual_stats["plot_style_type"]  = "CTB" if (active_layout.StyleSheet or "").lower().endswith(".ctb") else "STB"
except Exception:
    visual_stats["plot_style_sheet"] = ""
    visual_stats["plot_style_type"]  = "unknown"

n_wipeouts = len(visual_stats["wipeouts"])
n_images   = len(visual_stats["images"])
n_transp_l = len(visual_stats["transparent_layers"])
print(f"[VISUAL] WIPEOUT={n_wipeouts} | IMAGE={n_images} | "
      f"capas transparentes={n_transp_l} | "
      f"entidades con transparency={visual_stats['entities_with_transparency']} | "
      f"ltscale overrides={visual_stats['entities_with_ltscale_override']}")
print(f"[VISUAL] Plot style: {visual_stats['plot_style_sheet'] or '(ninguno)'}")

out_visual = Path("adip_visual_audit.json")
with open(out_visual, 'w', encoding='utf-8') as f:
    json.dump(visual_stats, f, indent=2, ensure_ascii=False)
print(f"[OK] {out_visual}  ({out_visual.stat().st_size // 1024} KB)")
```

---

## TAREA 5 — Fix en dxf_import.py: WIPEOUT como Hatch blanco

### Dónde está
`cad/dxf_import.py`, línea ~57-65:
```python
_SKIP_TYPES = frozenset({
    "SEQEND", "VIEWPORT",
    "ACAD_PROXY_ENTITY", "UNKNOWN", "BODY", "REGION", "3DSOLID",
    "RAY", "WIPEOUT", "OLE2FRAME", "IMAGE", "UNDERLAY",     # ← WIPEOUT aquí
    ...
})
```

### Cómo arreglarlo

**Paso 1**: Quitar `"WIPEOUT"` de `_SKIP_TYPES`:
```python
_SKIP_TYPES = frozenset({
    "SEQEND", "VIEWPORT",
    "ACAD_PROXY_ENTITY", "UNKNOWN", "BODY", "REGION", "3DSOLID",
    "RAY", "OLE2FRAME", "IMAGE", "UNDERLAY",   # ← WIPEOUT removido
    "MESH", "SURFACE", "PLANESURFACE", "EXTRUDEDSURFACE",
    "LOFTEDSURFACE", "REVOLVEDSURFACE", "SWEPTSURFACE",
})
```

**Paso 2**: En la función `_prim_from_dxf` (donde están todos los `if t == "LINE"`, etc.),
buscar justo antes del `if t == "SOLID":` (~línea 721) y agregar:

```python
    # ── WIPEOUT — polígono de enmascaramiento ──────────────────────────────
    # Un wipeout "borra" todo lo que hay debajo dibujando un polígono relleno
    # del color de fondo. Lo convertimos a Hatch sólido blanco en capa especial.
    if t == "WIPEOUT":
        try:
            boundary = e.boundary   # ezdxf expone el polígono de contorno
            if boundary is not None:
                pts = [(float(v.x) * escala, float(v.y) * escala)
                       for v in boundary]
                if len(pts) >= 3:
                    return Hatch(
                        boundary=pts,
                        pattern="SOLID",
                        color="#FFFFFF",      # blanco = borra debajo
                        layer=lyr or "WIPEOUT",
                        holes=[],
                        scale=1.0,
                        angle=0.0,
                    )
        except Exception:
            pass
        return None
```

**Notas importantes**:
- La capa `"WIPEOUT"` debe existir o crearse en el import. Si `lyr` ya está seteado
  al leer la entidad, úsalo directamente. Si no, usa `"WIPEOUT"` como fallback.
- La entidad `Hatch` ya acepta todos esos campos — no hay que cambiar `entities.py`.
- `e.boundary` en ezdxf para WIPEOUT devuelve los vértices del polígono.
  Si falla, intentar con `e.dxf.get("boundary", None)` o iterar `e.vertices`.

**Verificación syntax después de editar**:
```bash
python -c "import ast; ast.parse(open('cad/dxf_import.py', encoding='utf-8').read()); print('OK')"
```

---

## Resumen de archivos que se generan/modifican

### Archivos nuevos que genera el ADIP actualizado:
| Archivo | Contenido |
|---------|-----------|
| `adip_ltype_audit.json`  | Linetypes: total, custom, complejos, definiciones |
| `adip_layout_audit.json` | Layouts: papel, escala, viewports, capas congeladas |
| `adip_visual_audit.json` | WIPEOUT, IMAGE, transparencia, LTSCALE, plot style CTB |

### Archivos actualizados:
| Archivo | Cambio |
|---------|--------|
| `adip_styles_audit.json` | DimStyles ahora con datos reales via ezdxf (no error) |
| `cad/dxf_import.py`      | WIPEOUT → Hatch blanco (no más silently dropped) |

### Archivos que NO se tocan:
- `adip_hatch_audit.json` ✅ completo
- `adip_block_audit.json` ✅ completo

---

## Orden de implementación

```
1. adip_extract.py — TAREA 1: fix DimStyles (función _leer_dimstyles_ezdxf)
2. adip_extract.py — TAREA 2: PARTE 4 Linetypes
3. adip_extract.py — TAREA 3: PARTE 5 Layouts
4. adip_extract.py — TAREA 4: PARTE 6 Visual Properties
5. cad/dxf_import.py — TAREA 5: WIPEOUT handler
6. Correr: python adip_extract.py  (con AutoCAD + DWG abierto)
7. Verificar que los 3 JSONs nuevos se generaron
8. Verificar syntax de dxf_import.py
```

---

## Cómo correr el ADIP

AutoCAD debe estar abierto con el DWG activo. Luego:
```
cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI"
python adip_extract.py
```

Si el DWG está en modo solo lectura y ezdxf falla al leerlo directamente,
el usuario puede exportarlo primero a DXF desde AutoCAD:
```
Comando AutoCAD: EXPORTAR → DXF → "merlos_export.dxf"
```
Y actualizar la función para usar esa ruta de DXF.

---

## Nota sobre el _CACHE_VERSION

Si se modifica `cad/dxf_import.py` (la TAREA 5), hay que incrementar:
```python
# Línea ~1623 de dxf_import.py:
_CACHE_VERSION = 9   # ← cambiar a 10
```
Esto fuerza que los archivos DXF en caché se re-importen con la nueva lógica.
El caché vive en `~/.estudio_merlos_ai/dxf_cache/`.
