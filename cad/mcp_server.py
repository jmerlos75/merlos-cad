"""
cad/mcp_server.py
=================
Servidor MCP embebido en el CAD Estudio Merlos.

Expone herramientas para que Claude Code inspeccione y controle el dibujo
en tiempo real mientras el CAD está abierto.

Uso:
    # El engine lo inicia automáticamente al arrancar
    from cad.mcp_server import start_server, set_engine
    set_engine(self)
    start_server(port=6789)

Conexión en Claude Code (.claude/settings.json):
    {
      "mcpServers": {
        "cad": {
          "type": "sse",
          "url": "http://localhost:6789/sse"
        }
      }
    }
"""
from __future__ import annotations

import threading
import traceback
from typing import Any

# ── Estado compartido ─────────────────────────────────────────────────────────
_engine: Any = None          # referencia al CADWindow (seteado desde engine.py)
_lock = threading.Lock()     # protege acceso concurrente al engine
_PORT = 6789

# ── FastMCP server ────────────────────────────────────────────────────────────
try:
    from mcp.server.fastmcp import FastMCP
    _mcp = FastMCP(
        "CAD Estudio Merlos",
        instructions=(
            "Servidor MCP del CAD Estudio Merlos. "
            "Permite leer entidades, capas, viewport y ejecutar comandos CAD. "
            "Todas las coordenadas son en metros (unidades mundo del dibujo)."
        ),
    )
    _MCP_OK = True
except Exception:
    _MCP_OK = False
    _mcp = None


def set_engine(engine: Any) -> None:
    """Llamado por CADWindow.__init__ para registrar la instancia del engine."""
    global _engine
    _engine = engine


def start_server(port: int = _PORT) -> None:
    """Inicia el servidor MCP en un thread daemon."""
    if not _MCP_OK:
        print("[MCP] fastmcp no disponible — servidor MCP no iniciado")
        return

    def _run():
        try:
            import uvicorn
            from mcp.server.fastmcp import FastMCP as _FM
            # run() bloquea; corre en thread daemon → termina con el proceso
            _mcp.run(transport="sse")
        except Exception as exc:
            print(f"[MCP] error en servidor: {exc}")

    t = threading.Thread(target=_run, daemon=True, name="CAD-MCP-Server")
    t.start()
    print(f"[MCP] servidor iniciado en http://localhost:{_PORT}/sse")


# ── Helpers de serialización ──────────────────────────────────────────────────

def _entity_to_dict(e: Any, idx: int) -> dict:
    """Convierte una Entity a dict JSON-serializable."""
    from cad.entities import (Line, Polyline, Circle, Arc, Text,
                               Dimension, Hatch, Insert, Leader,
                               Ellipse, Spline, XLine)
    d: dict = {
        "id":       idx,
        "type":     type(e).__name__,
        "layer":    getattr(e, "layer", "0"),
        "color":    getattr(e, "color", None),
        "selected": getattr(e, "selected", False),
    }
    if isinstance(e, Line):
        d.update(x1=e.x1, y1=e.y1, x2=e.x2, y2=e.y2)
    elif isinstance(e, Polyline):
        d.update(points=list(e.points), closed=e.closed)
    elif isinstance(e, Circle):
        d.update(cx=e.cx, cy=e.cy, radius=e.radius)
    elif isinstance(e, Arc):
        d.update(cx=e.cx, cy=e.cy, radius=e.radius,
                 start_ang=e.start_ang, end_ang=e.end_ang, ccw=e.ccw)
    elif isinstance(e, Text):
        d.update(x=e.x, y=e.y, content=e.content,
                 height=e.height, angle=e.angle)
    elif isinstance(e, Dimension):
        d.update(p1=e.p1, p2=e.p2, pos=e.pos,
                 dim_type=e.dim_type,
                 text_override=getattr(e, "text_override", None))
    elif isinstance(e, Hatch):
        d.update(pattern=e.pattern,
                 boundary=list(e.boundary) if e.boundary else [])
    elif isinstance(e, Insert):
        d.update(block_name=e.block_name, x=e.x, y=e.y,
                 scale_x=getattr(e, "scale_x", 1.0),
                 scale_y=getattr(e, "scale_y", 1.0),
                 angle=getattr(e, "angle", 0.0))
    elif isinstance(e, Ellipse):
        d.update(cx=e.cx, cy=e.cy, rx=e.rx, ry=e.ry,
                 angle=getattr(e, "angle", 0.0))
    elif isinstance(e, Spline):
        d.update(points=list(e.points))
    elif isinstance(e, XLine):
        d.update(x1=e.x1, y1=e.y1, x2=e.x2, y2=e.y2)
    elif isinstance(e, Leader):
        d.update(points=list(e.points),
                 text=getattr(e, "text", ""))
    return d


# ── Herramientas MCP ──────────────────────────────────────────────────────────

if _MCP_OK:

    @_mcp.tool()
    def get_entities(
        layer: str = "",
        entity_type: str = "",
        selected_only: bool = False,
        limit: int = 200,
    ) -> dict:
        """
        Lista las entidades del dibujo activo.

        Args:
            layer: filtrar por nombre de capa (vacío = todas)
            entity_type: filtrar por tipo (Line, Circle, Text, Dimension, etc.)
            selected_only: solo entidades seleccionadas
            limit: máximo de entidades a retornar (default 200)

        Returns:
            {"total": N, "returned": M, "entities": [...]}
        """
        with _lock:
            if _engine is None:
                return {"error": "CAD no iniciado"}
            ents = list(_engine.entities)

        filtered = []
        for i, e in enumerate(ents):
            if layer and getattr(e, "layer", "") != layer:
                continue
            if entity_type and type(e).__name__.lower() != entity_type.lower():
                continue
            if selected_only and not getattr(e, "selected", False):
                continue
            filtered.append(_entity_to_dict(e, i))
            if len(filtered) >= limit:
                break

        return {
            "total":    len(ents),
            "returned": len(filtered),
            "entities": filtered,
        }

    @_mcp.tool()
    def get_entity(entity_id: int) -> dict:
        """
        Retorna los detalles completos de una entidad por su índice.

        Args:
            entity_id: índice de la entidad en la lista (campo 'id' de get_entities)
        """
        with _lock:
            if _engine is None:
                return {"error": "CAD no iniciado"}
            ents = _engine.entities
            if entity_id < 0 or entity_id >= len(ents):
                return {"error": f"id {entity_id} fuera de rango (total={len(ents)})"}
            return _entity_to_dict(ents[entity_id], entity_id)

    @_mcp.tool()
    def get_layers() -> dict:
        """
        Lista todas las capas del dibujo con sus propiedades.

        Returns:
            {"active_layer": "...", "layers": [{name, color, visible, locked}, ...]}
        """
        with _lock:
            if _engine is None:
                return {"error": "CAD no iniciado"}
            layers = {
                name: {
                    "name":    name,
                    "color":   lyr.color,
                    "visible": lyr.visible,
                    "locked":  lyr.locked,
                }
                for name, lyr in _engine.layers.items()
            }
            active = _engine.active_layer
        return {"active_layer": active, "layers": layers}

    @_mcp.tool()
    def get_viewport() -> dict:
        """
        Retorna el estado del viewport: escala, offset, tamaño de canvas.

        Returns:
            {scale, offset_x, offset_y, canvas_w, canvas_h,
             world_left, world_right, world_top, world_bottom}
        """
        with _lock:
            if _engine is None:
                return {"error": "CAD no iniciado"}
            sc = _engine.scale
            ox = _engine.offset_x
            oy = _engine.offset_y
            try:
                W = _engine.canvas.winfo_width()
                H = _engine.canvas.winfo_height()
            except Exception:
                W, H = 800, 600

        return {
            "scale":        sc,
            "offset_x":     ox,
            "offset_y":     oy,
            "canvas_w":     W,
            "canvas_h":     H,
            "world_left":   -ox / sc,
            "world_right":  (W - ox) / sc,
            "world_top":    oy / sc,
            "world_bottom": -(H - oy) / sc,
        }

    @_mcp.tool()
    def get_stats() -> dict:
        """
        Resumen estadístico del dibujo: conteo por tipo y por capa.
        """
        with _lock:
            if _engine is None:
                return {"error": "CAD no iniciado"}
            ents  = list(_engine.entities)
            layers = dict(_engine.layers)

        by_type: dict = {}
        by_layer: dict = {}
        for e in ents:
            t = type(e).__name__
            by_type[t] = by_type.get(t, 0) + 1
            lyr = getattr(e, "layer", "0")
            by_layer[lyr] = by_layer.get(lyr, 0) + 1

        return {
            "total_entities": len(ents),
            "total_layers":   len(layers),
            "by_type":        by_type,
            "by_layer":       by_layer,
        }

    @_mcp.tool()
    def select_entities(ids: list[int]) -> dict:
        """
        Selecciona entidades por sus índices (reemplaza la selección actual).

        Args:
            ids: lista de índices (campo 'id' de get_entities)

        Returns:
            {"selected": N, "errors": [...]}
        """
        errors = []
        with _lock:
            if _engine is None:
                return {"error": "CAD no iniciado"}
            ents = _engine.entities
            # deseleccionar todo
            for e in ents:
                e.selected = False
            # seleccionar los pedidos
            count = 0
            for idx in ids:
                if 0 <= idx < len(ents):
                    ents[idx].selected = True
                    count += 1
                else:
                    errors.append(f"id {idx} fuera de rango")

        # Redibujar desde el thread principal
        if _engine is not None:
            try:
                _engine.root.after(0, _engine._redraw)
            except Exception:
                pass

        return {"selected": count, "errors": errors}

    @_mcp.tool()
    def execute_command(cmd: str, args: str = "") -> dict:
        """
        Ejecuta un comando CAD (equivalente a escribirlo en la barra de comandos).

        Ejemplos de comandos: L, PL, REC, C, A, T, E, M, CO, RO, SC, MI, ZE, ZA,
        DH, DV, DA, H, SAVE, SAVEAS, LAYISO, LAYON.

        Args:
            cmd: alias del comando (case-insensitive)
            args: argumentos adicionales (opcional)

        Returns:
            {"ok": true/false, "message": "..."}
        """
        if _engine is None:
            return {"ok": False, "message": "CAD no iniciado"}

        result = {"ok": False, "message": ""}

        def _do():
            try:
                _engine._ejecutar_accion(cmd.lower(), args)
                result["ok"] = True
                result["message"] = f"Comando '{cmd}' ejecutado"
            except Exception as exc:
                result["ok"] = False
                result["message"] = f"Error: {exc}\n{traceback.format_exc()}"

        # Ejecutar en el thread principal de tkinter y esperar
        ev = threading.Event()

        def _do_and_signal():
            _do()
            ev.set()

        try:
            _engine.root.after(0, _do_and_signal)
            ev.wait(timeout=5.0)
        except Exception as exc:
            result["message"] = f"Error al programar comando: {exc}"

        return result

    @_mcp.tool()
    def get_block_defs() -> dict:
        """
        Lista los bloques (BlockDef) definidos en el dibujo.

        Returns:
            {"total": N, "blocks": [{name, entity_count}, ...]}
        """
        with _lock:
            if _engine is None:
                return {"error": "CAD no iniciado"}
            defs = getattr(_engine, "block_defs", {})

        blocks = [
            {"name": name, "entity_count": len(getattr(bd, "entities", []))}
            for name, bd in defs.items()
        ]
        return {"total": len(blocks), "blocks": blocks}

    @_mcp.tool()
    def zoom_to_point(x: float, y: float, scale: float = 0.0) -> dict:
        """
        Mueve el viewport para centrar el punto mundo (x, y).

        Args:
            x, y: coordenadas mundo en metros
            scale: nueva escala (px/m). 0 = mantener escala actual.

        Returns:
            {"ok": true, "viewport": {...}}
        """
        if _engine is None:
            return {"ok": False, "message": "CAD no iniciado"}

        result: dict = {}

        def _do():
            try:
                W = _engine.canvas.winfo_width()
                H = _engine.canvas.winfo_height()
                sc = scale if scale > 0 else _engine.scale
                _engine.offset_x = W / 2 - x * sc
                _engine.offset_y = H / 2 + y * sc
                _engine.scale    = sc
                _engine._redraw()
                result["ok"] = True
                result["viewport"] = {
                    "scale": sc, "offset_x": _engine.offset_x,
                    "offset_y": _engine.offset_y,
                }
            except Exception as exc:
                result["ok"] = False
                result["message"] = str(exc)

        ev = threading.Event()

        def _do_and_signal():
            _do()
            ev.set()

        try:
            _engine.root.after(0, _do_and_signal)
            ev.wait(timeout=3.0)
        except Exception as exc:
            result = {"ok": False, "message": str(exc)}

        return result
