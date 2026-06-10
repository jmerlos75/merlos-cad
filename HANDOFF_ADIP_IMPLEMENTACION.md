# HANDOFF — ADIP: Implementación pendiente en dxf_export.py

## Contexto del proyecto

Aplicación de escritorio Python (tkinter + PIL/OpenGL) que actúa como editor CAD.
Ruta del proyecto: `C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI\`

Archivos clave:
- `cad/dxf_import.py` — importa DXF/DWG al formato interno
- `cad/dxf_export.py` — exporta entidades internas a DXF (AC2018 via ezdxf)
- `cad/entities.py`   — clases de entidades internas (Line, Hatch, Text, Insert, etc.)

---

## Qué es ADIP

ADIP = AutoCAD Deep Inspection Plugin. Script Python que se conecta a AutoCAD
vía COM (pywin32) cuando hay un DWG abierto, extrae datos que ezdxf no expone
fácilmente, y los guarda en JSON para que el código los consuma.

### Cómo conectarse a AutoCAD

```python
import win32com.client
acad = win32com.client.GetActiveObject('AutoCAD.Application')
doc  = acad.ActiveDocument
msp  = doc.ModelSpace
```

Requisitos:
- pywin32 instalado (`pip install pywin32`)
- AutoCAD abierto con el DWG cargado
- Ejecutar desde: `C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI\`

### Cómo correr el ADIP

```
cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI"
python adip_extract.py
```

Genera los 3 archivos JSON descritos abajo.

---

## Archivos ADIP generados (ya existen)

### 1. `adip_hatch_audit.json` ✅
```
source: Planos Casa Merlos (cambio en cochera) v13.dwg
total_hatches: 788
total_with_holes: 434
total_gradients: 40
```
Patrones usados en el DWG:
| Patrón     | Count | En acad.pat |
|------------|-------|-------------|
| SOLID      | 368   | ✅          |
| LINE       | 196   | ✅          |
| AR-SAND    | 65    | ✅          |
| AR-CONC    | 50    | ✅          |
| SOLID,_O   | 40    | —           |
| SKIRTING   | 14    | ❌          |
| EARTH      | 13    | ✅          |
| CASCOTE    | 10    | ❌          |
| GRAVEL     | 7     | ✅          |
| ANSI31     | 7     | ✅          |
| WOOD-F5    | 5     | ❌          |
| XYLO1      | 5     | ❌          |
| NET        | 4     | ✅          |
| DOTS       | 2     | ✅          |
| ENDGRAIN   | 1     | ❌          |
| MADER-1    | 1     | ❌          |

**6 patrones custom sin .pat**: SKIRTING, CASCOTE, WOOD-F5, XYLO1, ENDGRAIN, MADER-1

### 2. `adip_block_audit.json` ✅
```
total_block_defs: 118
total_inserts: 982 (ModelSpace + Layouts/PaperSpace)
```
Top bloques: EJE×204, bloque×140, BOMBILLO×64, GANCHO ROPA×62, _ARCHTICK×49...

Bloque con ATTRIBS relevante:
- `EJE` — 204 inserts, tag=`E`, valores sample: ['A','B','H',...]
  (etiquetas de ejes estructurales A,B,C... / 1,2,3...)

### 3. `adip_styles_audit.json` ✅
```
TextStyles: 49 estilos
  Standard → arial.ttf
  ROMANS   → romans.shx
  ARCHIT   → archstyl.shx
  ... (46 más)

DimStyles: 15 estilos
  NOTA: la extracción via SetVariable falló en todos
  (AutoCAD bloqueó el cambio de DIMSTYLE por documento de solo lectura)
  → necesita re-extraer con DWG editable, O leer con ezdxf directamente

Hatch patterns:
  found_in_pat:  10 patrones con definición geométrica
  not_found:      6 patrones → CASCOTE, ENDGRAIN, MADER-1, SKIRTING, WOOD-F5, XYLO1
```

---

## Qué falta hacer — 4 tareas de implementación

### TAREA 1 (CRÍTICA) — Patrones custom en dxf_export.py

**Problema**: Los 6 patrones custom (SKIRTING×14, CASCOTE×10, etc.) no están en
acad.pat. El export actual hace fallback a ANSI31 (líneas diagonales) → incorrecto.

**Archivo**: `cad/dxf_export.py` líneas ~1216–1239

**Lo que hay ahora**:
```python
try:
    hatch_ent.set_pattern_fill(_pat_dxf, scale=_exp_scale, angle=e.angle)
except Exception:
    try:
        hatch_ent.set_pattern_fill("ANSI31", scale=e.scale, angle=e.angle)
    except Exception:
        hatch_ent.set_solid_fill()
```

**Lo que hay que hacer**:
1. Cargar `adip_styles_audit.json` una sola vez al inicio del módulo
2. Para patrones con `status == "found_in_custom_pat"` o `"found_in_acad_pat"`:
   usar `set_pattern_fill(name)` normal (ya funciona)
3. Para patrones con `status == "not_found"` (los 6 custom):
   mapear a un patrón visualmente similar de acad.pat (fallback inteligente):
   ```python
   _CUSTOM_FALLBACK = {
       "SKIRTING":  ("LINE",   0.8, 45),   # madera → líneas paralelas
       "CASCOTE":   ("AR-SAND", 0.5, 0),   # cascote → arena
       "WOOD-F5":   ("LINE",   0.5, 0),    # madera  → líneas
       "XYLO1":     ("LINE",   0.5, 45),   # madera  → líneas
       "ENDGRAIN":  ("DOTS",   0.5, 0),    # end grain → puntos
       "MADER-1":   ("LINE",   0.5, 30),   # madera  → líneas
   }
   ```
   O mejor aún: si `adip_styles_audit.json` tiene las `lines` (definición geométrica),
   escribir el patrón inline con ezdxf:
   ```python
   hatch_ent.set_pattern_definition(lines_from_json)
   hatch_ent.dxf.pattern_name = original_name
   ```

**Criterio de éxito**: Al re-importar el DXF exportado en AutoCAD, los patrones
SKIRTING/CASCOTE/etc. se ven correctamente (no ANSI31).

---

### TAREA 2 (ALTA) — Exportar valores ATTRIB en INSERT

**Problema**: Los bloques INSERT que tienen atributos (ej. EJE con tag E='A','B','C')
se exportan como referencia de bloque vacía — los valores de las etiquetas se pierden.

**Archivo**: `cad/dxf_export.py` líneas ~1113–1134 (bloque `elif isinstance(e, Insert)`)

**Lo que hay ahora**:
```python
msp.add_blockref(
    e.block_name,
    insert=(e.x, e.y, 0),
    dxfattribs={...}
)
# ← nada más
```

**Lo que hay que hacer**:
Después del `add_blockref`, escribir los ATTRIBs:
```python
blk_ref = msp.add_blockref(e.block_name, ...)
# Escribir atributos si los hay
if getattr(e, 'attribs', None):
    for att in e.attribs:
        try:
            blk_ref.add_attrib(
                tag=att.tag,
                text=att.value,
                insert=(att.x, att.y, 0),
                dxfattribs={
                    "layer":  att.layer,
                    "height": att.height,
                    "rotation": att.angle,
                }
            )
        except Exception:
            pass
```

**Entidad interna relevante** (`cad/entities.py`):
```python
@dataclass
class Attrib(Entity):
    tag:   str   = ''
    value: str   = ''
    x:     float = 0.0
    y:     float = 0.0
    height: float = 0.2
    angle:  float = 0.0

@dataclass
class Insert(Entity):
    block_name: str  = ''
    x, y:       float = 0.0, 0.0
    scale_x, scale_y: float = 1.0, 1.0
    angle:      float = 0.0
    attribs:    list  = field(default_factory=list)  # [Attrib, ...]
```

**Criterio de éxito**: Al exportar y reimportar, los ejes EJE mantienen sus letras/números.

---

### TAREA 3 (MEDIA) — DimStyles: leer con ezdxf y guardar en settings.json

**Problema**: La extracción COM de DimStyles falló (DWG de solo lectura).
Alternativa: leer directamente del DXF con ezdxf (no necesita AutoCAD).

**Cómo hacerlo**:
```python
import ezdxf
dwg = ezdxf.readfile("ruta/al/plano.dxf")

dimstyles = {}
for ds in dwg.dimstyles:
    rec = {
        "dimtxt":    ds.dxf.get("dimtxt", 0.25),
        "dimasz":    ds.dxf.get("dimasz", 0.18),
        "dimscale":  ds.dxf.get("dimscale", 1.0),
        "dimexo":    ds.dxf.get("dimexo", 0.0625),
        "dimexe":    ds.dxf.get("dimexe", 0.18),
        "dimdec":    ds.dxf.get("dimdec", 2),
        "dimpost":   ds.dxf.get("dimpost", ""),
        "dimtxsty":  ds.dxf.get("dimtxsty", "Standard"),
    }
    dimstyles[ds.name] = rec
```

Luego guardar en `config/settings.json` sección `"dimstyles"`.

**Dónde se usa**: `cad/dxf_export.py` función `_leer_dimstyles()` línea ~28,
y `cad/dxf_import.py` para aplicar el estilo correcto al importar cotas.

**Criterio de éxito**: Las cotas del DXF exportado tienen la misma apariencia
(tamaño de texto, flechas) que las del DWG original.

---

### TAREA 4 (MEDIA) — TextStyles: mapear fuentes .shx a TTF en export

**Problema**: El DWG usa fuentes .shx (romans.shx, archstyl.shx) que no existen
en sistemas sin AutoCAD. El export escribe esas fuentes → texto invisible en
visores que no tienen AutoCAD.

**Lo que hay en adip_styles_audit.json**:
```json
{"name": "ROMANS",  "font_file": "romans.shx",  "is_shx": true},
{"name": "ARCHIT",  "font_file": "archstyl.shx", "is_shx": true}
```

**Lo que hay que hacer** en `cad/dxf_export.py`:
```python
_SHX_TO_TTF = {
    "romans.shx":   "Arial",
    "archstyl.shx": "Arial",
    "simplex.shx":  "Arial",
    "txt.shx":      "Arial",
    "monotxt.shx":  "Courier New",
    "complex.shx":  "Arial",
    "italic.shx":   "Times New Roman",
}
# Al exportar textstyle:
font = _SHX_TO_TTF.get(ts_font_file.lower(), ts_font_file)
```

**Criterio de éxito**: El DXF exportado se ve correctamente en Inkscape, LibreCAD,
BricsCAD y cualquier visor sin AutoCAD instalado.

---

## Orden recomendado de implementación

```
1. TAREA 2 — ATTRIB export      (3 líneas de código, impacto inmediato en ejes)
2. TAREA 1 — Patrones custom    (fallback inteligente en dxf_export.py)
3. TAREA 4 — SHX→TTF mapping    (dict estático, 10 líneas)
4. TAREA 3 — DimStyles          (script ezdxf separado → settings.json)
```

---

## Notas importantes

- **ezdxf**: ya instalado, versión compatible con AC2018
- **`_CACHE_VERSION`** en `dxf_import.py`: actualmente = 9. Si se modifica la
  lógica de import hay que incrementarlo para invalidar el caché en disco.
- El caché de DXF vive en `~/.estudio_merlos_ai/dxf_cache/`
- **No tocar** `adip_hatch_audit.json` ni `adip_block_audit.json` — ya tienen
  los datos correctos del DWG real.
- `adip_styles_audit.json` tiene los 49 textstyles y los 10 pattern defs encontrados.
  Los 6 patrones custom (SKIRTING, CASCOTE, WOOD-F5, XYLO1, ENDGRAIN, MADER-1)
  tienen `"status": "not_found"` → usar _CUSTOM_FALLBACK mapping.
