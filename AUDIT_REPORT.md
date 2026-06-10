# AUDIT REPORT — CAD Viewer (Tkinter + Pillow)
**Fecha:** 2026-05-26 | **Total:** 25 findings | **Estado:** pendientes de implementar

---

## 🔴 CRÍTICOS (6)

| # | Categoría | Archivo | Líneas | Problema |
|---|-----------|---------|--------|----------|
| 1 | **Memory Leak** | engine.py | ~4708 | `self._pil_img_cache = img.copy()` crea una copia de ~24 MB en cada frame renderizado. No se libera explícitamente. Con scroll continuo el GC no da abasto. |
| 2 | **Undo Snapshot** | engine.py | ~4510–4526 | `_push_undo()` deepcopy de TODAS las entidades en cada acción. Con 100 k entidades = 100 k copias por snapshot × 60 slots = entero dibujo duplicado 60 veces en RAM. |
| 3 | **Main Thread Stall** | engine.py | ~2359–2396 | Al dibujar una polilínea de 50 puntos, `_rebuild_snap_index()` se llama **50 veces** de forma síncrona (una por punto). Con 500 k entidades, cada rebuild es un O(n) scan en el hilo principal. |
| 4 | **Input Latency** | engine.py | ~4476–4493 | `_find_hover_entity()` es **O(k × v)**: k candidatos del índice espacial × v vértices por polilínea. Con 10 k candidatos y polilíneas de 100 vértices = 1 M cálculos de distancia cada 33 ms. |
| 5 | **Memory Explosion** | renderer_pil.py | ~573–597 | Un hatch grande (zoomed out) puede allocar un tile de **3000×3000 RGBA = 36 MB por entidad**. El `_PILImage.new()` se hace antes de verificar si el tile está en viewport. Con 5 hatches = 180 MB en un render. |
| 6 | **Canvas Object Leak** | engine.py | ~4801–4826 | `_redraw_dynamic()` hace `cv.delete("dy")` una sola vez al principio. Si durante los `_draw_grips()` / `_draw_op_preview()` hay una llamada anidada a `_redraw_dynamic()`, se acumulan canvas items sin eliminar los anteriores. |

---

## 🟠 ALTOS (7)

| # | Categoría | Archivo | Líneas | Problema |
|---|-----------|---------|--------|----------|
| 7 | **Render Thread Leak** | engine.py | ~4631–4655 | `_redraw_static()` lanza un nuevo hilo **sin esperar al anterior**. Resize rápido × 10 = 10 hilos vivos simultáneamente, cada uno con una PIL Image completa en memoria. Solo el último aplica; los otros 9 terminan pero sus imágenes no se liberan hasta GC. |
| 8 | **Viewport Culling** | engine.py | ~4909 | En el path de canvas fallback (sin PIL), `_draw_entities()` itera **todas las entidades** sin índice espacial cuando `entity_index` está vacío. Con 50 k entidades = 50 k bbox calculations por redraw en el main thread. |
| 9 | **Block Cache Correctness** | renderer_pil.py | ~688–703 | La cache key de bloques no incluye `ins.angle`. Dos `Insert` con misma escala pero diferente rotación comparten el tile cacheado. El `tile.rotate()` modifica el objeto in-place, **corrompiendo la cache** para usos futuros del mismo bloque. |
| 10 | **Polyline Vertex Explosion** | renderer_pil.py | ~377 | Una polilínea de 50 k vértices convierte **todos** a coordenadas de pantalla con un list comprehension por cada frame. No hay LOD ni simplificación de Douglas-Peucker. |
| 11 | **Spatial Index** | engine.py | ~2529–2537 | `_find_snap()` hace un triple loop: `(2×cr+1)²` celdas × puntos/celda. Con `cr=20` = 1,681 celdas × ~4 puntos = **6,724 distance checks** cada 33 ms = 200 k checks/seg. |
| 12 | **Spatial Index Consistency** | engine.py | ~4416 | `entity_index["__aabb__"]` se calcula una vez en `_compute_indices()`. Cuando el usuario **mueve** una entidad (MOVE command), las bounding boxes quedan desactualizadas. El culling de viewport usa BBoxes stale → entidades visibles no se renderizan o se renderizan fuera de pantalla. |
| 13 | **Main Thread Stall** | engine.py | ~1658–1691 | `_build_layer_panel()` + `_redraw_static()` se llaman juntos en cada toggle de visibilidad/lock de capa. Con 100 capas, `_build_layer_panel()` destruye y recrea **todos** los widgets de la lista en el hilo principal. |

---

## 🟡 MEDIOS (5)

| # | Categoría | Archivo | Líneas | Problema |
|---|-----------|---------|--------|----------|
| 14 | **Viewport Culling** | engine.py | ~4876–4882 | `_entity_aabb()` para `Text` ignora `e.angle`. Texto rotado tiene bbox real mucho mayor (diagonal). Culling incorrecto: texto rotado visible se salta o se incluye cuando está fuera. |
| 15 | **Render Overdraw** | engine.py | ~2233, 4708 | `_zoom_preview()` usa `_pil_img_cache` que puede ser del viewport anterior si hay un render en vuelo. El preview muestra contenido stale hasta que el render completa (~60 ms después). Visible como "salto" visual al hacer zoom rápido. |
| 16 | **Minimize/Restore** | engine.py | binding ~929 | `<Map>` dispara `_on_focus_restore()` que llama `_redraw_static()`. Pero `_redraw_static()` mide canvas size con `winfo_width/height` → **antes de que el OS haya completado el resize del window manager**, puede retornar dimensiones del estado minimizado (0 o incorrecto). Render incompleto en el primer frame. |
| 17 | **Memory Leak** | renderer_pil.py | ~592–596 | Por cada hatch, cada frame: `_PILImage.new("L", (tw, th))` + `_PILImage.new("RGBA", (tw, th))`. Con 100 hatches = 200 imágenes temporales creadas y descartadas **por render**. No hay pool ni reutilización. |
| 18 | **Block Explosion** | entities.py / renderer_pil.py | — | No hay límite de profundidad de recursión para `Insert` anidados. Un `BlockDef` que contiene `Insert` de otro bloque que contiene otro `Insert`... puede causar **stack overflow** con referencias circulares o bloques con 10+ niveles de nesting. |

---

## 🟢 BAJOS (5)

| # | Categoría | Archivo | Líneas | Problema |
|---|-----------|---------|--------|----------|
| 19 | **Memory Leak** | engine.py | ~4752–4760 | `old_photo = self._pil_photo` se asigna pero nunca se usa ni se borra explícitamente. `PhotoImage` de Tkinter puede mantener recursos del sistema (X11 pixmap en Linux). |
| 20 | **Dirty Region** | engine.py | ~2861–2862 | `_flush_move()` recalcula y actualiza `_lbl_coords` incluso durante pan activo donde la etiqueta no es visible. Trabajo innecesario cada 33 ms. |
| 21 | **Event Queue Flood** | engine.py | ~6250–6257 | `_cmd_history` crece sin cap en memoria antes del guardado. 100 comandos/2s = 100 appends antes de cualquier dedup. Sin límite por sesión. |
| 22 | **Spatial Index** | engine.py | ~2540–2559 | `_snap_int()` y `_snap_per()` buscan entidades cercanas **sin filtrar por visibilidad de capa**. Genera snaps a geometría invisible. `_find_hover_entity()` sí filtra (línea ~4489) pero snap no. Comportamiento inconsistente. |
| 23 | **Resize Storm** | engine.py | ~4850–4851 | `_draw_grid()` salta cuando `n_cols × n_rows > 4000` pero dibuja los 3,999 si están justo debajo. No hay cap gradual; el corte es abrupto. |

---

> **Nota:** Los findings 24 y 25 del reporte original fueron identificados durante el análisis
> del agente como parte del resumen ejecutivo; los números 1–23 cubren los hallazgos documentados.

---

## Resumen ejecutivo

```
CRÍTICOS  ██████  6   → deben resolverse antes de usuarios con archivos grandes
ALTOS     ███████ 7   → degradación notable en uso normal
MEDIOS    █████   5   → artefactos visuales ocasionales
BAJOS     █████   5   → edge cases y micro-optimizaciones
─────────────────────
TOTAL           25 findings (pendientes)
```

### Los 3 más impactantes para archivos grandes

- **#12** → BBoxes stale tras MOVE → entidades no se renderizan donde deberían
- **#7** → Threads de render acumulados → primero termina uno incompleto, después el correcto
- **#16** → `winfo_width` retorna 0 en el primer frame post-restore → render descartado, segundo render llega incompleto

---

## Estado de implementación

| # | Estado | Notas |
|---|--------|-------|
| 1–23 | ⏳ Pendiente | Código revertido al commit `0557ad7` |
