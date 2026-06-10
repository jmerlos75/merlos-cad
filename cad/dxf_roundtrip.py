"""
cad/dxf_roundtrip.py
====================
Validador y corrector de fidelidad en el ciclo import → export DXF.

Dos funciones principales:

  pre_export_fix(entities, layers)
      Correcciones rápidas sobre las entidades en memoria ANTES de exportar.
      Sin I/O — siempre se ejecuta en el hilo de export.
      Retorna lista de strings con los fixes aplicados.

  validate_roundtrip(entities_orig, layers_orig, block_defs, exported_path, escala)
      Reimporta el DXF recién exportado y compara contra las entidades
      originales. Retorna un RoundtripReport con discrepancias y correcciones.
      Solo se usa bajo demanda (es lento: hace un importar_dxf completo).
"""
from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ── Tolerancias de comparación ────────────────────────────────────────────────

_TOL_COORD  = 1e-4   # metros — diferencia de coordenada aceptable
_TOL_SCALE  = 0.02   # 2 % — diferencia relativa de escala aceptable
_TOL_ANGLE  = 0.5    # grados — diferencia de ángulo aceptable


# ── Resultado ─────────────────────────────────────────────────────────────────

@dataclass
class RoundtripIssue:
    severity:  str   # "error" | "warning" | "info"
    entity:    str   # tipo de entidad afectada
    field:     str   # campo con discrepancia
    original:  str   # valor original
    exported:  str   # valor en el DXF exportado
    corrected: bool  = False
    layer:     str   = ""

    def __str__(self) -> str:
        mark = "✅" if self.corrected else ("❌" if self.severity == "error" else "⚠")
        capa = f"  [{self.layer}]" if self.layer else ""
        corr = "  (corregido)" if self.corrected else ""
        return f"{mark} {self.entity}.{self.field}{capa}: {self.original!r} → {self.exported!r}{corr}"


@dataclass
class RoundtripReport:
    issues:             list[RoundtripIssue] = field(default_factory=list)
    entities_original:  int = 0
    entities_exported:  int = 0
    duration_ms:        float = 0.0

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" and not i.corrected for i in self.issues)

    @property
    def n_errors(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def n_warnings(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def n_corrected(self) -> int:
        return sum(1 for i in self.issues if i.corrected)

    def summary(self) -> str:
        if not self.issues:
            return "✅ Export verificado — sin discrepancias"
        parts = []
        if self.n_errors:
            parts.append(f"{self.n_errors} error(es)")
        if self.n_warnings:
            parts.append(f"{self.n_warnings} aviso(s)")
        if self.n_corrected:
            parts.append(f"{self.n_corrected} corregido(s)")
        delta = self.entities_exported - self.entities_original
        if delta < 0:
            parts.append(f"{abs(delta)} entidad(es) perdida(s)")
        return "  ".join(parts) if parts else "✅ OK"

    def lines(self) -> list[str]:
        out = [
            f"ROUNDTRIP  orig={self.entities_original}  exp={self.entities_exported}"
            f"  t={self.duration_ms:.0f}ms",
        ]
        for iss in self.issues:
            out.append(str(iss))
        return out


# ── PRE-EXPORT FIX ────────────────────────────────────────────────────────────

def pre_export_fix(entities: list, layers: dict) -> list[str]:
    """
    Aplica correcciones in-place a las entidades ANTES del export DXF.
    No hace I/O. Retorna lista de fixes aplicados (para log).

    Correcciones aplicadas:
      Hatch  — normalizar ángulo 0-360
             — fijar dxf_scale para hatches de app (dxf_scale=0, pattern≠SOLID)
             — skip boundary < 3 pts (no exportable)
      Text   — reemplazar content None/vacío por espacio (ezdxf rechaza "")
             — clip height <= 0 → 0.05
      Line   — detectar longitud cero (warning, no se altera)
      Layer  — limpiar nombres con caracteres inválidos DXF
      Insert — detectar block_name vacío
    """
    from cad.entities import Hatch, Text, Line, Insert, Layer as _Layer

    fixes: list[str] = []

    # ── capas ─────────────────────────────────────────────────────────────────
    # Caracteres inválidos en nombres de capa DXF (especificación AC2018).
    # NO incluir tildes ni ñ — DXF AC2018 es UTF-8 y los soporta perfectamente.
    # Solo los ASCII de control y operadores de ruta/comodín son inválidos.
    _BAD_CHARS = set('<>/\\"*?|')
    for lname, lyr in list(layers.items()):
        cleaned = "".join("_" if (c in _BAD_CHARS or ord(c) < 32) else c
                          for c in lname)
        if cleaned != lname:
            # Renombrar la capa y actualizar entidades
            layers[cleaned] = layers.pop(lname)
            layers[cleaned].name = cleaned
            for e in entities:
                if e.layer == lname:
                    e.layer = cleaned
            fixes.append(f"layer: nombre '{lname}' → '{cleaned}' (caracteres inválidos DXF)")

    # ── entidades ─────────────────────────────────────────────────────────────
    for e in entities:
        etype = type(e).__name__

        if isinstance(e, Hatch):
            # 1. Ángulo fuera de rango
            if not (0.0 <= e.angle < 360.0):
                orig = e.angle
                e.angle = e.angle % 360.0
                fixes.append(f"Hatch[{e.layer}].angle {orig:.1f}° → {e.angle:.1f}°")

            # 2. dxf_scale=0 en hatch con patrón (creado en app, no importado)
            #    Para export a INSUNITS=6 (metros), usar e.scale directamente.
            #    Anotamos dxf_scale=e.scale para que el exporter lo use.
            if e.pattern not in ("SOLID",) and e.dxf_scale <= 0.0:
                e.dxf_scale = e.scale
                fixes.append(
                    f"Hatch[{e.layer}].dxf_scale ← {e.scale:.6f} (patrón app-creado)")

            # 3. Boundary insuficiente — marcarlo para que el exporter lo salte
            if len(e.boundary) < 3:
                fixes.append(
                    f"Hatch[{e.layer}] boundary={len(e.boundary)} pts → será ignorado en export")

        elif isinstance(e, Text):
            # 4. Contenido None o vacío — ezdxf rechaza strings vacíos
            if not e.content:
                e.content = " "
                fixes.append(f"Text[{e.layer}] content vacío → ' '")

            # 5. Altura inválida
            if e.height <= 0:
                orig_h = e.height
                e.height = 0.05
                fixes.append(f"Text[{e.layer}].height {orig_h} → 0.05")

        elif isinstance(e, Line):
            # 6. Línea de longitud cero (warning, no se altera)
            dx = e.x2 - e.x1; dy = e.y2 - e.y1
            if dx * dx + dy * dy < 1e-12:
                fixes.append(f"Line[{e.layer}] longitud=0 — será ignorada en export")

        elif isinstance(e, Insert):
            # 7. Insert sin block_name
            if not getattr(e, "block_name", None):
                fixes.append(f"Insert[{e.layer}] sin block_name — será ignorado en export")

    return fixes


# ── VALIDADOR DE ROUNDTRIP ────────────────────────────────────────────────────

def validate_roundtrip(
    entities_orig: list,
    layers_orig:   dict,
    block_defs:    dict,
    exported_path: str,
    escala:        float = 1.0,
) -> RoundtripReport:
    """
    Reimporta el DXF exportado y compara contra las entidades originales.
    Retorna un RoundtripReport con todas las discrepancias encontradas.

    Algoritmo de matching:
      Agrupa por (tipo, capa) y empareja por proximidad de coordenada
      de referencia (centroide, punto de inserción, etc.).
    """
    import time
    t0 = time.perf_counter()

    from cad.dxf_import import importar_dxf
    from cad.entities import (Hatch, Text, Line, Polyline, Circle, Arc,
                               Dimension, Insert, Spline, Ellipse)

    report = RoundtripReport(entities_original=len(entities_orig))

    # ── Reimportar ────────────────────────────────────────────────────────────
    try:
        res = importar_dxf(exported_path, escala=escala)
    except Exception as ex:
        report.issues.append(RoundtripIssue(
            severity="error", entity="DXF", field="import",
            original="ok", exported=f"ERROR: {ex}"))
        return report

    ents_exp = res.entities
    report.entities_exported = len(ents_exp)

    # ── Diferencia de conteo por tipo ─────────────────────────────────────────
    def _count(ents, cls):
        return sum(1 for e in ents if isinstance(e, cls))

    for cls in [Line, Polyline, Circle, Arc, Text, Dimension,
                Hatch, Insert, Spline, Ellipse]:
        n_orig = _count(entities_orig, cls)
        n_exp  = _count(ents_exp, cls)
        if n_orig != n_exp:
            sev = "error" if n_exp < n_orig else "warning"
            report.issues.append(RoundtripIssue(
                severity=sev, entity=cls.__name__, field="count",
                original=str(n_orig), exported=str(n_exp)))

    # ── Comparar Hatches ──────────────────────────────────────────────────────
    orig_hatches = [e for e in entities_orig if isinstance(e, Hatch)]
    exp_hatches  = [e for e in ents_exp      if isinstance(e, Hatch)]

    for oh in orig_hatches:
        if not oh.boundary or len(oh.boundary) < 3:
            continue
        oh_cx = sum(p[0] for p in oh.boundary) / len(oh.boundary)
        oh_cy = sum(p[1] for p in oh.boundary) / len(oh.boundary)

        # Buscar el hatch exportado más cercano en la misma capa
        best_eh = None
        best_d  = float("inf")
        for eh in exp_hatches:
            if eh.layer != oh.layer or not eh.boundary:
                continue
            eh_cx = sum(p[0] for p in eh.boundary) / len(eh.boundary)
            eh_cy = sum(p[1] for p in eh.boundary) / len(eh.boundary)
            d = math.hypot(eh_cx - oh_cx, eh_cy - oh_cy)
            if d < best_d:
                best_d = d
                best_eh = eh

        if best_eh is None or best_d > _TOL_COORD * 100:
            report.issues.append(RoundtripIssue(
                severity="warning", entity="Hatch", field="match",
                original="presente", exported="no encontrado",
                layer=oh.layer))
            continue

        # Comparar patrón
        if oh.pattern != best_eh.pattern:
            corrected = False
            # Si cayó en fallback ANSI31 → intento de corrección no aplica aquí
            # (el pattern_name debe estar en el sistema del receptor)
            report.issues.append(RoundtripIssue(
                severity="warning", entity="Hatch", field="pattern",
                original=oh.pattern, exported=best_eh.pattern,
                layer=oh.layer, corrected=corrected))

        # Comparar escala (dxf_scale vs dxf_scale reimportado)
        scale_orig = oh.dxf_scale if oh.dxf_scale > 0 else oh.scale
        scale_exp  = best_eh.dxf_scale if best_eh.dxf_scale > 0 else best_eh.scale
        if scale_orig > 0:
            rel_diff = abs(scale_exp - scale_orig) / scale_orig
            if rel_diff > _TOL_SCALE:
                report.issues.append(RoundtripIssue(
                    severity="error", entity="Hatch", field="scale",
                    original=f"{scale_orig:.6g}", exported=f"{scale_exp:.6g}",
                    layer=oh.layer))

        # Comparar ángulo
        ang_diff = abs((oh.angle % 360) - (best_eh.angle % 360))
        ang_diff = min(ang_diff, 360 - ang_diff)
        if ang_diff > _TOL_ANGLE:
            report.issues.append(RoundtripIssue(
                severity="warning", entity="Hatch", field="angle",
                original=f"{oh.angle:.1f}°", exported=f"{best_eh.angle:.1f}°",
                layer=oh.layer))

    # ── Comparar Texts ────────────────────────────────────────────────────────
    orig_texts = [e for e in entities_orig if isinstance(e, Text)]
    exp_texts  = [e for e in ents_exp      if isinstance(e, Text)]

    for ot in orig_texts:
        best_et = None
        best_d  = float("inf")
        for et in exp_texts:
            if et.layer != ot.layer:
                continue
            d = math.hypot(et.x - ot.x, et.y - ot.y)
            if d < best_d:
                best_d = d
                best_et = et

        if best_et is None or best_d > _TOL_COORD * 100:
            continue

        # Comparar contenido (ignorar diferencias de espaciado y codificación menor)
        orig_content = (ot.content or "").strip()
        exp_content  = (best_et.content or "").strip()
        if orig_content != exp_content and orig_content:
            report.issues.append(RoundtripIssue(
                severity="warning", entity="Text", field="content",
                original=orig_content[:40], exported=exp_content[:40],
                layer=ot.layer))

    # ── Comparar capas ────────────────────────────────────────────────────────
    orig_layer_names = set(layers_orig.keys())
    exp_layer_names  = set(res.layers.keys())
    lost_layers = orig_layer_names - exp_layer_names - {"0"}
    if lost_layers:
        report.issues.append(RoundtripIssue(
            severity="warning", entity="Layer", field="names",
            original=", ".join(sorted(lost_layers)),
            exported="ausente(s) en export"))

    report.duration_ms = (time.perf_counter() - t0) * 1000
    return report


# ── EXPORT VERIFICADO (export + validate + report) ────────────────────────────

def export_verified(
    entities:   list,
    layers:     dict,
    block_defs: dict,
    ruta_final: str,
    escala:     float = 1.0,
) -> tuple[list[str], RoundtripReport | None]:
    """
    Pipeline completo:
      1. pre_export_fix  — correcciones rápidas in-place
      2. exportar_dxf    — genera el DXF en temp
      3. validate_roundtrip — reimporta y compara
      4. Mueve temp a ruta_final

    Retorna (fixes_aplicados, report).
    Si validate=False, report es None.
    """
    from cad.dxf_export import exportar_dxf

    # 1. Pre-fix
    fixes = pre_export_fix(entities, layers)

    # 2. Export a temp
    tmp_dir  = os.path.dirname(ruta_final)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".dxf", dir=tmp_dir)
    os.close(tmp_fd)
    try:
        exportar_dxf(entities, layers, tmp_path, block_defs=block_defs)

        # 3. Validate
        report = validate_roundtrip(
            entities, layers, block_defs, tmp_path, escala=escala)

        # 4. Mover temp → final
        if os.path.exists(ruta_final):
            os.replace(tmp_path, ruta_final)
        else:
            os.rename(tmp_path, ruta_final)

    except Exception:
        try: os.remove(tmp_path)
        except OSError: pass
        raise

    return fixes, report
