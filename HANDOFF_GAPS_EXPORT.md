# HANDOFF — Gaps del pipeline DXF: estado real + único pendiente

## VERIFICACIÓN PREVIA OBLIGATORIA

Antes de implementar cualquier cosa, confirmar el estado actual ejecutando:

```bash
cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI"
python -c "
import ast
for f in ['cad/dxf_export.py', 'cad/dxf_import.py', 'cad/entities.py']:
    ast.parse(open(f, encoding='utf-8').read())
    print(f'OK {f}')
"
```

Si alguno falla con SyntaxError, corregirlo antes de continuar.

---

## ESTADO REAL DE LOS 4 GAPS (auditado en el código)

| Gap | Descripción | Estado | Dónde está |
|-----|-------------|--------|-----------|
| 1 | ATTRIB values en INSERT export | ✅ YA IMPLEMENTADO | `dxf_export.py` línea 1161 — `blk_ref.add_attrib(...)` |
| 2 | Patrones hatch custom (SKIRTING, CASCOTE...) | ✅ YA IMPLEMENTADO | `dxf_export.py` línea 68 — `_CUSTOM_HATCH_FALLBACK` dict |
| 3 | MTEXT con formato (negrita, itálica, tamaños) | ❌ PENDIENTE | `_strip_mtext()` borra todo — ver abajo |
| 4 | SHX → TTF mapping en export | ✅ YA IMPLEMENTADO | `dxf_export.py` línea 51 — `_SHX_TO_TTF` dict |

**Solo hay que implementar Gap 3.**

---

## GAP 3 — MTEXT con formato: instrucciones completas

### El problema

Cuando el DXF tiene un MTEXT como este:
```
{\fArial|b1|i0;SALA}{\fArial|b0|i0: }{\H0.8x;12.50 m²}
```
La función `_strip_mtext()` en `dxf_import.py` lo convierte a:
```
SALA: 12.50 M²
```
Se pierde la negrita, el tamaño mixto, la itálica. En el export sale todo igual.

### La solución: guardar el string original junto al texto limpio

La idea es simple:
- `content` = texto plano limpio (para mostrar en pantalla, buscar, editar)
- `raw_mtext` = string original con códigos (para exportar sin pérdidas)

En el export: si `raw_mtext` existe → usarlo. Si no → usar `content` como antes.

---

### CAMBIO 1 — `cad/entities.py`

Agregar el campo `raw_mtext` a la clase `Text`.

**Buscar** (línea ~407-416):
```python
@dataclass
class Text(Entity):
    x:       float = 0.0
    y:       float = 0.0
    content: str   = ""
    height:  float = 0.20
    angle:       float = 0.0   # grados CCW desde eje X
    halign:      int   = 0     # 0=left  1=center  2=right
    valign:      int   = 0     # 0=baseline/bottom  2=middle  3=top
    mtext_width: float = 0.0   # ancho del cuadro MTEXT en unidades mundo (0=auto)
    layer:       str   = "A-TEXTO"
```

**Reemplazar con**:
```python
@dataclass
class Text(Entity):
    x:       float = 0.0
    y:       float = 0.0
    content: str   = ""
    height:  float = 0.20
    angle:       float = 0.0   # grados CCW desde eje X
    halign:      int   = 0     # 0=left  1=center  2=right
    valign:      int   = 0     # 0=baseline/bottom  2=middle  3=top
    mtext_width: float = 0.0   # ancho del cuadro MTEXT en unidades mundo (0=auto)
    layer:       str   = "A-TEXTO"
    raw_mtext:   str   = ""    # string MTEXT original con códigos de formato
                               # ({\\fArial|b1;TEXTO}) — preservado para round-trip
                               # vacío en texto creado dentro del visor
```

Luego buscar el método `_kw()` en la misma clase (línea ~429):
```python
    def _kw(self) -> dict:
        """Campos comunes para todas las transformaciones."""
        return dict(content=self.content, height=self.height,
                    halign=self.halign, valign=self.valign,
                    mtext_width=self.mtext_width,
                    layer=self.layer, color=self.color,
                    linetype=self.linetype, linewidth=self.linewidth)
```

**Reemplazar con**:
```python
    def _kw(self) -> dict:
        """Campos comunes para todas las transformaciones."""
        return dict(content=self.content, height=self.height,
                    halign=self.halign, valign=self.valign,
                    mtext_width=self.mtext_width,
                    layer=self.layer, color=self.color,
                    linetype=self.linetype, linewidth=self.linewidth,
                    raw_mtext=self.raw_mtext)
```

Incluirlo en `_kw` hace que translated/rotated/mirrored preserven el raw_mtext automáticamente.

---

### CAMBIO 2 — `cad/dxf_import.py`

Guardar el string original en el handler de MTEXT.

**Buscar** el handler MTEXT, la sección donde retorna el Text (línea ~380-426).
Actualmente termina con algo como:
```python
        return Text(x=e.dxf.insert.x*escala, y=e.dxf.insert.y*escala,
                    content=txt.upper(), height=h,
                    angle=ang, halign=halign, valign=valign,
                    mtext_width=mtext_w,
                    layer=lyr, color=col)
```

**Buscar la línea exacta**:
```python
        return Text(x=e.dxf.insert.x*escala, y=e.dxf.insert.y*escala,
                    content=txt.upper(), height=h,
                    angle=ang, halign=halign, valign=valign,
                    mtext_width=mtext_w,
                    layer=lyr, color=col)
```

**Reemplazar con**:
```python
        # Guardar el raw MTEXT solo si tiene códigos de formato reales
        # (negrita, itálica, tamaños mixtos, colores internos).
        # Si es texto plano sin códigos, raw_mtext queda vacío → sin cambio.
        _raw_to_store = ""
        try:
            _raw_check = getattr(e, "text", "") or ""
            # Tiene formato si contiene { o \f \b \i \H \C \W \A
            if _raw_check and any(c in _raw_check for c in ('{', '\\f', '\\b',
                                  '\\i', '\\H', '\\C', '\\W', '\\A')):
                _raw_to_store = _raw_check
        except Exception:
            pass

        return Text(x=e.dxf.insert.x*escala, y=e.dxf.insert.y*escala,
                    content=txt.upper(), height=h,
                    angle=ang, halign=halign, valign=valign,
                    mtext_width=mtext_w,
                    layer=lyr, color=col,
                    raw_mtext=_raw_to_store)
```

**Nota importante**: `e.text` en ezdxf devuelve el string MTEXT raw con todos los códigos.
`e.plain_text()` o `_strip_mtext(e.text)` devuelve el texto limpio. Usamos `e.text` para `raw_mtext`.

---

### CAMBIO 3 — `cad/dxf_export.py`

Usar `raw_mtext` en el export cuando está disponible.

**Buscar** el bloque `elif isinstance(e, Text):` (línea ~822).
Dentro de ese bloque, buscar donde se decide TEXT vs MTEXT:

```python
                if "\n" in _content or len(_content) > 80:
                    # Texto largo o multilinea → MTEXT
                    msp.add_mtext(_content.replace("\n", "\\P"),
                                  dxfattribs={**attribs,
                                  "char_height":      e.height,
                                  "insert":           (e.x, e.y, 0),
                                  "rotation":         _ang,
                                  "attachment_point": _attach,
                                  "style":            _active_txstyle})
                else:
                    # TEXT simple — halign + align_point para halign != 0
                    _txt_attrs = {**attribs,
                                  "height":   e.height,
                                  "insert":   (e.x, e.y, 0),
                                  "rotation": _ang,
                                  "halign":   _halign,
                                  "style":    _active_txstyle}
                    if _halign != 0:
                        # Para halign≠0, align_point es el punto de anclaje real
                        _txt_attrs["align_point"] = (e.x, e.y, 0)
                    msp.add_text(_content, dxfattribs=_txt_attrs)
```

**Reemplazar con**:
```python
                _raw = getattr(e, "raw_mtext", "") or ""

                if _raw:
                    # ── Tiene formato original: exportar MTEXT con códigos ────
                    # El string raw conserva negrita, itálica, tamaños mixtos, etc.
                    # La posición y escala se dan por dxfattribs, no por los códigos.
                    msp.add_mtext(_raw,
                                  dxfattribs={**attribs,
                                  "char_height":      e.height,
                                  "insert":           (e.x, e.y, 0),
                                  "rotation":         _ang,
                                  "attachment_point": _attach,
                                  "style":            _active_txstyle})

                elif "\n" in _content or len(_content) > 80:
                    # ── Texto largo o multilinea creado en el visor → MTEXT ──
                    msp.add_mtext(_content.replace("\n", "\\P"),
                                  dxfattribs={**attribs,
                                  "char_height":      e.height,
                                  "insert":           (e.x, e.y, 0),
                                  "rotation":         _ang,
                                  "attachment_point": _attach,
                                  "style":            _active_txstyle})
                else:
                    # ── TEXT simple ─────────────────────────────────────────
                    _txt_attrs = {**attribs,
                                  "height":   e.height,
                                  "insert":   (e.x, e.y, 0),
                                  "rotation": _ang,
                                  "halign":   _halign,
                                  "style":    _active_txstyle}
                    if _halign != 0:
                        _txt_attrs["align_point"] = (e.x, e.y, 0)
                    msp.add_text(_content, dxfattribs=_txt_attrs)
```

---

### CAMBIO 4 — `_CACHE_VERSION` en `dxf_import.py`

Incrementar para que los archivos en caché se re-importen con el nuevo campo.

**Buscar** (línea ~1623):
```python
_CACHE_VERSION = 9
```

**Reemplazar con**:
```python
_CACHE_VERSION = 10   # raw_mtext field en Text para round-trip MTEXT
```

El caché vive en `~/.estudio_merlos_ai/dxf_cache/`. Al incrementar la versión,
los archivos ya cacheados se re-importan automáticamente la próxima vez que se abren.

---

## Verificación después de implementar

### 1. Syntax check
```bash
python -c "
import ast
for f in ['cad/entities.py', 'cad/dxf_import.py', 'cad/dxf_export.py']:
    ast.parse(open(f, encoding='utf-8').read())
    print(f'OK {f}')
"
```

### 2. Test funcional rápido
```python
# test_mtext_roundtrip.py
import ezdxf, sys
sys.path.insert(0, '.')
from cad.dxf_import import importar_dxf
from cad.dxf_export import exportar_dxf

# Crear DXF de prueba con MTEXT formateado
doc = ezdxf.new('R2018')
msp = doc.modelspace()
msp.add_mtext(
    r'{\fArial|b1|i0;SALA COMEDOR}{\fArial|b0;: }{\H0.8x;25.50 m\U+00B2}',
    dxfattribs={"char_height": 0.25, "insert": (0, 0, 0)}
)
doc.saveas("test_mtext.dxf")

# Importar
result = importar_dxf("test_mtext.dxf")
textos = [e for e in result.entities if hasattr(e, 'content')]
for t in textos:
    print(f"content   : {t.content!r}")
    print(f"raw_mtext : {t.raw_mtext!r}")
    assert t.raw_mtext != "", "ERROR: raw_mtext está vacío — no se guardó"
    assert "SALA" in t.content,  "ERROR: content vacío — strip_mtext falló"

# Re-exportar
exportar_dxf("test_mtext_out.dxf", result.entities, result.layers)

# Verificar que el DXF de salida tiene los códigos de formato
with open("test_mtext_out.dxf", encoding="utf-8", errors="replace") as f:
    contenido = f.read()
assert "b1" in contenido or "\\b" in contenido, \
    "WARN: formato bold no encontrado en DXF exportado"

print("✅ MTEXT round-trip OK")
```

Ejecutar:
```bash
cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI"
python test_mtext_roundtrip.py
```

---

## Resumen de archivos modificados

| Archivo | Cambio | Línea aprox |
|---------|--------|------------|
| `cad/entities.py` | Agregar campo `raw_mtext: str = ""` a clase `Text` | ~415 |
| `cad/entities.py` | Agregar `raw_mtext=self.raw_mtext` en `_kw()` | ~434 |
| `cad/dxf_import.py` | Guardar `e.text` en `raw_mtext` si tiene códigos de formato | ~422 |
| `cad/dxf_export.py` | En export Text: si `raw_mtext` → usarlo para MTEXT | ~841 |
| `cad/dxf_import.py` | `_CACHE_VERSION = 10` | ~1623 |

---

## Qué NO tocar

- `adip_hatch_audit.json` — ya correcto
- `adip_block_audit.json` — ya correcto
- `adip_styles_audit.json` — ya correcto
- Los Gaps 1, 2 y 4 — ya implementados, no modificar

---

## Impacto esperado del Gap 3

| Escenario | Antes | Después |
|-----------|-------|---------|
| MTEXT con negrita importado y re-exportado | Pierde negrita | Negrita preservada |
| MTEXT con tamaño mixto (encabezado grande + texto pequeño) | Todo mismo tamaño | Tamaños preservados |
| MTEXT con itálica | Pierde itálica | Itálica preservada |
| Texto creado en el visor (sin raw_mtext) | Sin cambio | Sin cambio |
| TEXT simple (no MTEXT) | Sin cambio | Sin cambio |

El cambio es **aditivo y no destructivo**: solo agrega un campo opcional.
Si `raw_mtext` está vacío (texto creado en el visor), el comportamiento es idéntico al actual.
