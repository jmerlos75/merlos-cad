"""
cad/menu_icons.py
=================
Íconos Lucide-style 16×16 para los menús desplegables de EM CAD.
Generados con PIL/ImageDraw en memoria — sin archivos externos.
Trazo de línea blanco-azulado (#E2E8F0) sobre fondo transparente.
Dibujo en 32×32 con downsample LANCZOS → 16×16 para anti-aliasing.

Uso:
    from cad.menu_icons import build_icons
    self._menu_icons = build_icons()   # llamar UNA sola vez, guardar referencia
    photo = self._menu_icons["linea"]
"""
from __future__ import annotations
import math
import io
from PIL import Image, ImageDraw, ImageTk

# ── Paleta ────────────────────────────────────────────────────────────────────
_C   = "#E2E8F0"   # blanco-azulado — contraste sobre fondo #1E293B
_C2  = "#94A3B8"   # gris suave para detalles secundarios
_ACE = "#60A5FA"   # azul acento (detalle ocasional)
_S   = 32          # tamaño de trabajo (2× → downsample)
_LW  = 2           # grosor de línea estándar


# ── Primitivas ────────────────────────────────────────────────────────────────
def _new() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (_S, _S), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _done(img: Image.Image) -> Image.Image:
    """Downsample 32→16 con LANCZOS para bordes suaves."""
    return img.resize((16, 16), Image.LANCZOS)


def _photo(img: Image.Image) -> ImageTk.PhotoImage:
    return ImageTk.PhotoImage(image=img)


def _l(d, pts, lw=_LW, c=_C):
    """Línea(s) entre lista de puntos."""
    d.line(pts, fill=c, width=lw)


def _r(d, x0, y0, x1, y1, lw=_LW, c=_C):
    """Rectángulo outline."""
    d.rectangle([x0, y0, x1, y1], outline=c, width=lw)


def _e(d, x0, y0, x1, y1, lw=_LW, c=_C):
    """Elipse/círculo outline."""
    d.ellipse([x0, y0, x1, y1], outline=c, width=lw)


def _poly(d, pts, lw=_LW, c=_C):
    """Polígono outline (cierra automáticamente)."""
    closed = list(pts) + [pts[0]]
    d.line(closed, fill=c, width=lw)


def _arc(d, x0, y0, x1, y1, s, e, lw=_LW, c=_C):
    d.arc([x0, y0, x1, y1], start=s, end=e, fill=c, width=lw)


def _dot(d, x, y, r=2, c=_C):
    d.ellipse([x-r, y-r, x+r, y+r], fill=c)


def _arrow_h(d, x, y, right=True, lw=_LW):
    """Punta de flecha horizontal."""
    sign = 1 if right else -1
    _l(d, [(x, y), (x + sign*6, y - 3), (x + sign*6, y + 3), (x, y)], lw=lw)


# ══════════════════════════════════════════════════════════════════════════════
# ARCHIVO
# ══════════════════════════════════════════════════════════════════════════════

def ico_nuevo():
    img, d = _new()
    _poly(d, [(7,4),(21,4),(27,10),(27,28),(7,28)])
    _l(d, [(21,4),(21,10),(27,10)])
    return _done(img)

def ico_abrir():
    img, d = _new()
    _l(d, [(4,24),(4,14),(14,14),(16,10),(28,10),(28,24),(4,24)])
    _l(d, [(4,14),(4,10),(14,10)])
    return _done(img)

def ico_importar():
    img, d = _new()
    _r(d, 6, 16, 26, 27)
    _l(d, [(16,5),(16,22)], lw=2)
    _l(d, [(10,16),(16,22),(22,16)], lw=2)
    return _done(img)

def ico_guardar():
    img, d = _new()
    _r(d, 5, 4, 27, 27)
    _r(d, 9, 4, 23, 13)
    _r(d, 9, 18, 23, 27)
    _dot(d, 20, 8, r=2)
    return _done(img)

def ico_guardar_como():
    img, d = _new()
    _r(d, 5, 4, 23, 24)
    _r(d, 9, 4, 20, 12)
    _r(d, 9, 17, 20, 24)
    _l(d, [(25,18),(29,18)], lw=2)
    _l(d, [(27,16),(27,20)], lw=2)
    return _done(img)

def ico_exportar_dxf():
    img, d = _new()
    _r(d, 4, 16, 26, 27)
    _l(d, [(16,4),(16,21)], lw=2)
    _l(d, [(10,10),(16,4),(22,10)], lw=2)
    return _done(img)

def ico_exportar_png():
    img, d = _new()
    _r(d, 4, 6, 28, 26)
    _l(d, [(4,22),(10,14),(16,19),(22,12),(28,22)])
    _e(d, 20,8,26,14, lw=2)
    return _done(img)

def ico_exportar_pdf():
    img, d = _new()
    _poly(d, [(6,4),(20,4),(26,10),(26,28),(6,28)])
    _l(d, [(20,4),(20,10),(26,10)])
    _l(d, [(10,16),(22,16)])
    _l(d, [(10,20),(22,20)])
    _l(d, [(10,24),(17,24)])
    return _done(img)

def ico_lamina():
    img, d = _new()
    _poly(d, [(16,6),(6,12),(16,18),(26,12)])
    _l(d, [(6,16),(16,22),(26,16)])
    _l(d, [(24,24),(28,24)], lw=2)
    _l(d, [(26,22),(26,26)], lw=2)
    return _done(img)

def ico_modelo():
    img, d = _new()
    _poly(d, [(16,4),(27,10),(27,22),(16,28),(5,22),(5,10)])
    _l(d, [(16,4),(16,16)])
    _l(d, [(5,10),(16,16)])
    _l(d, [(27,10),(16,16)])
    return _done(img)

def ico_papel():
    img, d = _new()
    _r(d, 4, 4, 28, 28)
    _r(d, 8, 8, 20, 18)
    _r(d, 22, 8, 26, 12)
    _l(d, [(22,16),(26,16)], lw=1)
    _l(d, [(22,20),(26,24)], lw=1)
    return _done(img)

def ico_pag_setup():
    img, d = _new()
    _l(d, [(4,10),(28,10)])
    _l(d, [(4,18),(28,18)])
    _l(d, [(4,26),(28,26)])
    _e(d, 8,7,16,13)
    _e(d, 16,15,24,21)
    _e(d, 10,23,18,29)
    return _done(img)

def ico_salir():
    img, d = _new()
    _poly(d, [(6,4),(20,4),(20,28),(6,28)])
    _l(d, [(20,16),(28,16)], lw=2)
    _l(d, [(23,12),(28,16),(23,20)], lw=2)
    _dot(d, 14, 16, r=2)
    return _done(img)


# ══════════════════════════════════════════════════════════════════════════════
# DIBUJAR
# ══════════════════════════════════════════════════════════════════════════════

def ico_linea():
    img, d = _new()
    _l(d, [(6,26),(26,6)], lw=3)
    _dot(d, 6,26, r=3)
    _dot(d, 26,6, r=3)
    return _done(img)

def ico_polilinea():
    img, d = _new()
    pts = [(5,24),(10,10),(18,20),(26,8)]
    _l(d, pts, lw=2)
    for x,y in pts:
        _dot(d, x,y, r=2)
    return _done(img)

def ico_spline():
    img, d = _new()
    _l(d, [(5,24),(8,12),(14,20),(20,10),(27,16)], lw=2)
    _dot(d, 5,24, r=2); _dot(d, 27,16, r=2)
    return _done(img)

def ico_rectangulo():
    img, d = _new()
    _r(d, 5, 8, 27, 24, lw=3)
    return _done(img)

def ico_circulo():
    img, d = _new()
    _e(d, 4, 4, 28, 28, lw=3)
    return _done(img)

def ico_arco():
    img, d = _new()
    _arc(d, 4, 4, 28, 28, 200, 340, lw=3)
    _dot(d, 8,24, r=2); _dot(d, 24,24, r=2)
    return _done(img)

def ico_elipse():
    img, d = _new()
    _e(d, 4, 10, 28, 22, lw=3)
    return _done(img)

def ico_poligono():
    img, d = _new()
    cx, cy, r = 16, 16, 12
    pts = [(cx+r*math.cos(math.radians(90+i*60)),
            cy+r*math.sin(math.radians(90+i*60))) for i in range(6)]
    _poly(d, pts, lw=2)
    return _done(img)

def ico_texto():
    img, d = _new()
    _l(d, [(5,8),(27,8)], lw=3)
    _l(d, [(16,8),(16,26)], lw=3)
    return _done(img)

def ico_lider():
    img, d = _new()
    _l(d, [(5,25),(14,14)], lw=2)
    _l(d, [(5,25),(9,22),(7,18)], lw=1)
    _l(d, [(14,14),(27,14)])
    _l(d, [(14,19),(27,19)])
    return _done(img)

def ico_nube():
    img, d = _new()
    _arc(d, 3,14,13,24, 180, 0, lw=2)
    _arc(d, 9,8,21,20,  180, 0, lw=2)
    _arc(d, 17,12,27,22, 180, 0, lw=2)
    _l(d, [(3,19),(3,24),(27,24),(27,17)], lw=2)
    return _done(img)

def ico_xline():
    img, d = _new()
    _l(d, [(4,16),(28,16)], lw=2)
    _l(d, [(16,4),(16,28)], lw=2)
    _dot(d, 16,16, r=3)
    return _done(img)

def ico_bloque_def():
    img, d = _new()
    _r(d, 5, 5, 27, 27)
    _l(d, [(11,10),(11,22)], lw=2)
    _l(d, [(11,10),(17,10)], lw=2)
    _arc(d, 11,10,21,17, 270, 90, lw=2)
    _l(d, [(11,17),(18,17)], lw=2)
    _arc(d, 11,17,22,24, 270, 90, lw=2)
    _l(d, [(11,22),(18,22)], lw=2)
    return _done(img)

def ico_bloque_ins():
    img, d = _new()
    _r(d, 6, 16, 26, 27)
    _l(d, [(16,5),(16,20)], lw=2)
    _l(d, [(10,14),(16,20),(22,14)], lw=2)
    return _done(img)

def ico_imagen():
    img, d = _new()
    _r(d, 4, 6, 28, 26)
    _l(d, [(4,22),(10,14),(17,20),(22,13),(28,20)])
    _e(d, 19,8,26,15, lw=2)
    return _done(img)

def ico_atributos():
    img, d = _new()
    _r(d, 4, 4, 22, 22)
    _l(d, [(8,11),(18,11)])
    _l(d, [(8,16),(18,16)])
    _l(d, [(8,21),(14,21)])
    _l(d, [(23,18),(28,12),(26,10),(21,16),(23,18)], lw=2)
    _l(d, [(21,16),(20,22),(26,20)], lw=1)
    return _done(img)

def ico_hatch():
    img, d = _new()
    _r(d, 4, 4, 28, 28)
    for i in range(7):
        off = i * 4
        _l(d, [(5+off, 27), (27, 5+off)], lw=1)
    return _done(img)


# ══════════════════════════════════════════════════════════════════════════════
# EDITAR
# ══════════════════════════════════════════════════════════════════════════════

def ico_deshacer():
    img, d = _new()
    _arc(d, 6, 8, 26, 24, 160, 340, lw=2)
    _l(d, [(6,15),(6,8),(13,8)], lw=2)
    return _done(img)

def ico_rehacer():
    img, d = _new()
    _arc(d, 6, 8, 26, 24, 200, 20, lw=2)
    _l(d, [(26,15),(26,8),(19,8)], lw=2)
    return _done(img)

def ico_sel_todo():
    img, d = _new()
    # Marco de selección con trazo punteado
    for x0,y0,x1,y1 in [(4,4,16,4),(20,4,28,4),(28,4,28,16),(28,20,28,28),
                          (28,28,16,28),(12,28,4,28),(4,28,4,20),(4,16,4,4)]:
        _l(d, [(x0,y0),(x1,y1)], lw=1)
    _l(d, [(9,16),(13,20),(22,11)], lw=2)
    return _done(img)

def ico_borrar():
    img, d = _new()
    _l(d, [(4,10),(28,10)], lw=2)
    _r(d, 8, 10, 24, 27)
    _l(d, [(12,6),(12,10)])
    _l(d, [(20,6),(20,10)])
    _l(d, [(12,6),(20,6)])
    _l(d, [(13,14),(13,24)])
    _l(d, [(19,14),(19,24)])
    return _done(img)

def ico_mover():
    img, d = _new()
    for pts in [[(16,4),(16,10)],[(16,22),(16,28)],
                [(4,16),(10,16)],[(22,16),(28,16)]]:
        _l(d, pts, lw=2)
    _l(d, [(13,6),(16,3),(19,6)])
    _l(d, [(13,26),(16,29),(19,26)])
    _l(d, [(6,13),(3,16),(6,19)])
    _l(d, [(26,13),(29,16),(26,19)])
    return _done(img)

def ico_copiar():
    img, d = _new()
    _r(d, 4, 10, 20, 26)
    d.rectangle([12, 4, 28, 20], fill="#1E293B")
    _r(d, 12, 4, 28, 20)
    return _done(img)

def ico_rotar():
    img, d = _new()
    _arc(d, 5, 6, 27, 26, 50, 310, lw=2)
    _l(d, [(22,8),(27,6),(25,12)], lw=2)
    return _done(img)

def ico_escalar():
    img, d = _new()
    _r(d, 4, 16, 14, 26)
    _l(d, [(14,16),(28,4)], lw=2)
    _l(d, [(22,4),(28,4),(28,10)], lw=2)
    return _done(img)

def ico_espejo():
    img, d = _new()
    _l(d, [(16,4),(16,28)], lw=1, c=_C2)
    _poly(d, [(6,8),(13,16),(6,24)], lw=2)
    _poly(d, [(26,8),(19,16),(26,24)], lw=2)
    return _done(img)

def ico_offset():
    img, d = _new()
    _l(d, [(6,10),(6,22),(22,22)], lw=3)
    _l(d, [(10,14),(10,26),(26,26)], lw=2, c=_C2)
    return _done(img)

def ico_alinear():
    img, d = _new()
    _l(d, [(4,16),(28,16)], lw=2)
    _l(d, [(8,10),(15,10)])
    _l(d, [(8,22),(20,22)])
    _l(d, [(8,10),(8,22)], lw=1)
    _l(d, [(15,10),(15,22)], lw=1)
    return _done(img)

def ico_array():
    img, d = _new()
    for r in range(3):
        for c in range(3):
            x = 4 + c*9
            y = 4 + r*9
            _r(d, x, y, x+7, y+7, lw=1)
    return _done(img)

def ico_recortar():
    img, d = _new()
    _e(d, 4,4,14,14, lw=2)
    _e(d, 4,18,14,28, lw=2)
    _l(d, [(10,10),(27,27)], lw=2)
    _l(d, [(10,22),(27,5)], lw=2)
    return _done(img)

def ico_extender():
    img, d = _new()
    _l(d, [(4,16),(20,16)], lw=2)
    _l(d, [(20,11),(27,16),(20,21)], lw=2)
    _l(d, [(26,9),(26,23)], lw=2)
    return _done(img)

def ico_fillet():
    img, d = _new()
    _l(d, [(6,26),(6,14)], lw=2)
    _arc(d, 6,6,22,22, 180, 270, lw=2)
    _l(d, [(14,6),(26,6)], lw=2)
    return _done(img)

def ico_chamfer():
    img, d = _new()
    _l(d, [(6,26),(6,12)], lw=2)
    _l(d, [(6,12),(20,6)], lw=2)
    _l(d, [(20,6),(26,6)], lw=2)
    return _done(img)

def ico_partir():
    img, d = _new()
    _l(d, [(4,16),(11,16)], lw=3)
    _l(d, [(21,16),(28,16)], lw=3)
    _l(d, [(14,11),(18,21)], lw=1, c=_C2)
    return _done(img)

def ico_explotar():
    img, d = _new()
    for a in range(0, 360, 45):
        r = math.radians(a)
        _l(d, [(16+5*math.cos(r), 16+5*math.sin(r)),
               (16+12*math.cos(r), 16+12*math.sin(r))], lw=2)
    return _done(img)

def ico_propiedades():
    img, d = _new()
    _e(d, 4, 4, 28, 28, lw=2)
    _l(d, [(16,10),(16,12)], lw=3)
    _l(d, [(16,15),(16,23)], lw=3)
    return _done(img)

def ico_matchprop():
    img, d = _new()
    _l(d, [(12,4),(24,16)], lw=2)
    _l(d, [(22,14),(28,20),(24,24),(18,18),(22,14)], lw=1)
    _l(d, [(10,6),(4,12),(8,14),(12,20),(14,16)], lw=1)
    _r(d, 6, 22, 14, 28, lw=2, c=_ACE)
    return _done(img)


# ══════════════════════════════════════════════════════════════════════════════
# COTAS
# ══════════════════════════════════════════════════════════════════════════════

def ico_dim_h():
    img, d = _new()
    _l(d, [(4,16),(28,16)], lw=2)
    _l(d, [(4,10),(4,22)], lw=1)
    _l(d, [(28,10),(28,22)], lw=1)
    _l(d, [(4,16),(9,13)])
    _l(d, [(4,16),(9,19)])
    _l(d, [(28,16),(23,13)])
    _l(d, [(28,16),(23,19)])
    return _done(img)

def ico_dim_v():
    img, d = _new()
    _l(d, [(16,4),(16,28)], lw=2)
    _l(d, [(10,4),(22,4)], lw=1)
    _l(d, [(10,28),(22,28)], lw=1)
    _l(d, [(16,4),(13,9)])
    _l(d, [(16,4),(19,9)])
    _l(d, [(16,28),(13,23)])
    _l(d, [(16,28),(19,23)])
    return _done(img)

def ico_dim_a():
    img, d = _new()
    _l(d, [(6,26),(26,6)], lw=2)
    _l(d, [(3,22),(8,28)], lw=1)
    _l(d, [(22,2),(28,8)], lw=1)
    _l(d, [(6,26),(11,24)])
    _l(d, [(6,26),(8,21)])
    _l(d, [(26,6),(21,8)])
    _l(d, [(26,6),(24,11)])
    return _done(img)

def ico_dim_ang():
    img, d = _new()
    _l(d, [(8,26),(8,6)], lw=2)
    _l(d, [(8,26),(26,26)], lw=2)
    _arc(d, 8,6,28,26, 180, 270, lw=2)
    _l(d, [(8,6),(4,6)], lw=1)
    _l(d, [(26,26),(26,30)], lw=1)
    return _done(img)

def ico_dim_r():
    img, d = _new()
    _e(d, 4, 4, 22, 22, lw=2)
    _l(d, [(13,13),(27,27)], lw=2)
    _l(d, [(25,22),(27,27),(22,25)])
    return _done(img)

def ico_dim_d():
    img, d = _new()
    _e(d, 4, 4, 28, 28, lw=2)
    _l(d, [(8,24),(24,8)], lw=2)
    return _done(img)

def ico_dim_arc():
    img, d = _new()
    _arc(d, 4, 4, 28, 28, 210, 330, lw=3)
    _l(d, [(4,22),(8,20)])
    _l(d, [(4,22),(6,26)])
    _l(d, [(28,22),(24,20)])
    _l(d, [(28,22),(26,26)])
    return _done(img)

def ico_dim_co():
    img, d = _new()
    for x0, x1 in [(4,12),(12,22)]:
        _l(d, [(x0,16),(x1,16)], lw=2)
        _l(d, [(x0,11),(x0,21)], lw=1)
    _l(d, [(22,11),(22,21)], lw=1)
    return _done(img)

def ico_dim_ba():
    img, d = _new()
    _l(d, [(4,26),(28,26)], lw=1)
    for x in [8, 16, 22]:
        _l(d, [(x,26),(x,10)], lw=2)
        _l(d, [(x-4,10),(x+4,10)], lw=1)
    return _done(img)

def ico_dim_sp():
    img, d = _new()
    _l(d, [(6,9),(26,9)])
    _l(d, [(6,16),(26,16)], lw=2)
    _l(d, [(6,23),(26,23)])
    _l(d, [(16,10),(13,6)])
    _l(d, [(16,10),(19,6)])
    _l(d, [(16,22),(13,26)])
    _l(d, [(16,22),(19,26)])
    return _done(img)

def ico_dim_ord():
    img, d = _new()
    _l(d, [(4,16),(28,16)], lw=1, c=_C2)
    _l(d, [(16,4),(16,28)], lw=1, c=_C2)
    _l(d, [(4,16),(10,16)], lw=3)
    _l(d, [(16,4),(16,10)], lw=3)
    return _done(img)

def ico_dim_cfg():
    img, d = _new()
    _l(d, [(4,10),(28,10)])
    _l(d, [(4,22),(28,22)])
    _e(d, 11,7,19,13)
    _e(d, 5,19,13,25)
    return _done(img)


# ══════════════════════════════════════════════════════════════════════════════
# CAPAS
# ══════════════════════════════════════════════════════════════════════════════

def ico_capas():
    img, d = _new()
    for dy in [-5, 0, 5]:
        cx, cy = 16, 16+dy
        _poly(d, [(cx,cy-5),(cx-10,cy),(cx,cy+5),(cx+10,cy)], lw=2)
    return _done(img)

def ico_nueva_capa():
    img, d = _new()
    _poly(d, [(16,6),(6,12),(16,18),(26,12)], lw=2)
    _l(d, [(6,16),(16,22),(26,16)])
    _l(d, [(24,24),(28,24)], lw=2)
    _l(d, [(26,22),(26,26)], lw=2)
    return _done(img)

def ico_layiso():
    img, d = _new()
    _arc(d, 4,12,28,22, 0, 180, lw=2)
    _arc(d, 4,12,28,22, 180, 360, lw=2)
    _e(d, 12,12,20,20, lw=2)
    _dot(d, 16,16, r=3)
    return _done(img)

def ico_layoff():
    img, d = _new()
    _arc(d, 4,12,28,22, 0, 180, lw=2)
    _arc(d, 4,12,28,22, 180, 360, lw=2)
    _e(d, 12,12,20,20, lw=2, c=_C2)
    _l(d, [(4,4),(28,28)], lw=2)
    return _done(img)

def ico_layon():
    img, d = _new()
    _arc(d, 4,10,28,22, 0, 180, lw=2)
    _arc(d, 4,10,28,22, 180, 360, lw=2)
    _e(d, 12,12,20,20, lw=2)
    _dot(d, 16,16, r=3)
    _l(d, [(20,24),(23,28),(29,19)], lw=2, c=_ACE)
    return _done(img)

def ico_laylock():
    img, d = _new()
    _r(d, 8, 16, 24, 28)
    _arc(d, 9,7,23,20, 180, 0, lw=2)
    _e(d, 14,20,18,24)
    _l(d, [(16,22),(16,26)])
    return _done(img)

def ico_layulk():
    img, d = _new()
    _r(d, 8, 16, 24, 28)
    _arc(d, 14,5,28,18, 180, 0, lw=2)
    _l(d, [(14,11),(8,11),(8,16)], lw=2)
    _e(d, 14,20,18,24)
    _l(d, [(16,22),(16,26)])
    return _done(img)

def ico_laymcur():
    img, d = _new()
    _poly(d, [(16,6),(6,12),(16,18),(26,12)], lw=2)
    _l(d, [(6,16),(16,22),(26,16)])
    _l(d, [(16,22),(16,29)], lw=2)
    _l(d, [(12,26),(16,30),(20,26)], lw=2)
    return _done(img)


# ══════════════════════════════════════════════════════════════════════════════
# MEDIR
# ══════════════════════════════════════════════════════════════════════════════

def ico_dist():
    img, d = _new()
    _r(d, 4, 13, 28, 19)
    for x in [8,12,16,20,24]:
        _l(d, [(x,13),(x,16)])
    _dot(d, 4,16, r=2); _dot(d, 28,16, r=2)
    return _done(img)

def ico_measure():
    img, d = _new()
    _l(d, [(4,16),(28,16)], lw=1)
    for x in range(4, 29, 5):
        _l(d, [(x,12),(x,20)], lw=2)
    return _done(img)

def ico_area():
    img, d = _new()
    _poly(d, [(6,26),(6,10),(20,6),(26,18),(18,26)], lw=2)
    d.polygon([(6,26),(6,10),(20,6),(26,18),(18,26)],
              fill=(255,255,255,25))
    return _done(img)

def ico_id_pt():
    img, d = _new()
    _l(d, [(4,16),(28,16)], lw=1, c=_C2)
    _l(d, [(16,4),(16,28)], lw=1, c=_C2)
    _e(d, 11,11,21,21, lw=2)
    _dot(d, 16,16, r=3)
    return _done(img)

def ico_list_ent():
    img, d = _new()
    for i, (x0,x1) in enumerate([(9,11),(13,28),(9,11),(13,24),(9,11),(13,26),(9,11),(13,20)]):
        y = 6 + i*3
        _l(d, [(x0,y),(x1,y)])
    return _done(img)


# ══════════════════════════════════════════════════════════════════════════════
# VER
# ══════════════════════════════════════════════════════════════════════════════

def ico_zoom_e():
    img, d = _new()
    _e(d, 4, 4, 22, 22, lw=2)
    _l(d, [(20,20),(28,28)], lw=3)
    _r(d, 8, 8, 18, 18, lw=1, c=_C2)
    _l(d, [(8,13),(18,13)], lw=2); _l(d, [(13,8),(13,18)], lw=2)
    return _done(img)

def ico_zoom_a():
    img, d = _new()
    _e(d, 4, 4, 22, 22, lw=2)
    _l(d, [(20,20),(28,28)], lw=3)
    _r(d, 7, 7, 19, 19, lw=1)
    return _done(img)

def ico_zoom_w():
    img, d = _new()
    _e(d, 4, 4, 22, 22, lw=2)
    _l(d, [(20,20),(28,28)], lw=3)
    # ventana punteada dentro
    for pts in [[(6,9),(12,9)],[(15,9),(19,9)],
                [(6,9),(6,19)],[(6,19),(12,19)],
                [(15,19),(19,19)],[(19,9),(19,14)],[(19,16),(19,19)]]:
        _l(d, pts, lw=1, c=_ACE)
    return _done(img)

def ico_zoom_p():
    img, d = _new()
    _e(d, 4, 4, 22, 22, lw=2)
    _l(d, [(20,20),(28,28)], lw=3)
    _arc(d, 8,9,20,21, 200, 20, lw=2)
    _l(d, [(8,12),(8,8),(13,8)], lw=2)
    return _done(img)

def ico_zoom_xp():
    img, d = _new()
    _e(d, 4, 4, 20, 20, lw=2)
    _l(d, [(18,18),(26,26)], lw=3)
    # XP texto
    _l(d, [(22,10),(28,16)])
    _l(d, [(28,10),(22,16)])
    return _done(img)

def ico_regen():
    img, d = _new()
    _arc(d, 4, 4, 28, 28, 30, 290, lw=2)
    _l(d, [(4,18),(4,28),(13,25)], lw=2)
    return _done(img)

def ico_pan():
    img, d = _new()
    for x in [10,14,18,22]:
        _l(d, [(x,20),(x,10 if x != 10 else 14)], lw=2)
    _l(d, [(10,20),(22,20)], lw=2)
    _arc(d, 8,18,24,28, 0, 180, lw=2)
    return _done(img)

def ico_scroll():
    img, d = _new()
    _r(d, 10, 4, 22, 28, lw=2)
    _e(d, 13,10,19,17, lw=2)
    return _done(img)

def ico_snap():
    img, d = _new()
    _arc(d, 4, 4, 28, 20, 0, 180, lw=3)
    _l(d, [(4,12),(4,28)], lw=3)
    _l(d, [(28,12),(28,28)], lw=3)
    _l(d, [(4,28),(10,28)], lw=3)
    _l(d, [(22,28),(28,28)], lw=3)
    return _done(img)

def ico_grid():
    img, d = _new()
    for x in [6,14,22]:
        for y in [6,14,22]:
            _dot(d, x, y, r=2)
    return _done(img)

def ico_ortho():
    img, d = _new()
    _l(d, [(6,26),(6,6)], lw=2)
    _l(d, [(6,26),(26,26)], lw=2)
    _arc(d, 6,16,16,26, 270, 0, lw=2)
    return _done(img)

def ico_dyn():
    img, d = _new()
    _poly(d, [(8,4),(8,20),(14,14),(16,20),(20,8)], lw=2)
    _r(d, 16, 12, 28, 22, lw=1)
    _l(d, [(18,16),(26,16)])
    _l(d, [(18,19),(23,19)])
    return _done(img)

def ico_lts():
    img, d = _new()
    for x in [4,11,18]:
        _l(d, [(x,16),(x+6,16)], lw=3)
    _dot(d, 26,16, r=2)
    _dot(d, 29,16, r=2)
    return _done(img)

def ico_colores():
    img, d = _new()
    _e(d, 4, 4, 28, 28, lw=2)
    for a, col in [(30,"#F87171"),(90,"#4ADE80"),(150,"#60A5FA"),
                   (210,"#FACC15"),(270,"#C084FC"),(330,"#FB923C")]:
        r = math.radians(a)
        x, y = 16+8*math.cos(r), 16+8*math.sin(r)
        d.ellipse([x-3,y-3,x+3,y+3], fill=col)
    return _done(img)


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

def ico_cfg_general():
    img, d = _new()
    _e(d, 8, 8, 24, 24, lw=2)
    for a in range(0, 360, 45):
        r = math.radians(a)
        _l(d, [(16+10*math.cos(r), 16+10*math.sin(r)),
               (16+14*math.cos(r), 16+14*math.sin(r))], lw=2)
    _dot(d, 16, 16, r=3)
    return _done(img)

def ico_cfg_cotas():
    return ico_dim_cfg()

def ico_cfg_texto():
    img, d = _new()
    _l(d, [(4,8),(28,8)], lw=3)
    _l(d, [(16,8),(16,27)], lw=3)
    return _done(img)

def ico_cfg_visual():
    img, d = _new()
    # Paleta: círculo con 3 sectores de color usando arcos
    _e(d, 4, 4, 28, 28, lw=2)
    _arc(d, 4, 4, 28, 28,   0, 120, lw=4, c="#F87171")
    _arc(d, 4, 4, 28, 28, 120, 240, lw=4, c="#4ADE80")
    _arc(d, 4, 4, 28, 28, 240, 360, lw=4, c="#60A5FA")
    _e(d, 4, 4, 28, 28, lw=2)
    return _done(img)

def ico_cfg_ia():
    img, d = _new()
    _e(d, 4, 8, 28, 26, lw=2)
    _l(d, [(16,8),(16,26)])
    _l(d, [(4,17),(28,17)])
    _dot(d, 10,12, r=2); _dot(d, 22,12, r=2)
    _dot(d, 10,22, r=2); _dot(d, 22,22, r=2)
    return _done(img)

def ico_menu_ctx():
    img, d = _new()
    _r(d, 6, 4, 26, 28)
    _l(d, [(10,10),(22,10)])
    _l(d, [(10,15),(22,15)])
    _l(d, [(10,20),(22,20)])
    _l(d, [(10,25),(16,25)])
    return _done(img)


# ══════════════════════════════════════════════════════════════════════════════
# IA / HUB / AYUDA
# ══════════════════════════════════════════════════════════════════════════════

def ico_ia_chat():
    img, d = _new()
    _arc(d, 4,4,28,24, 0, 180, lw=2)
    _arc(d, 4,4,28,24, 180, 360, lw=2)
    _l(d, [(10,28),(14,22)], lw=2)
    _l(d, [(9,14),(23,14)])
    _l(d, [(9,18),(18,18)])
    return _done(img)

def ico_sle():
    img, d = _new()
    _e(d, 4, 4, 28, 28, lw=2)
    _l(d, [(17,9),(12,17),(18,17),(13,25)], lw=3, c=_ACE)
    return _done(img)

def ico_terreno():
    img, d = _new()
    _poly(d, [(4,6),(28,6),(24,26),(8,26)], lw=2)
    _e(d, 12,10,20,18, lw=2)
    _l(d, [(16,18),(16,24)], lw=2)
    return _done(img)

def ico_programa():
    img, d = _new()
    _r(d, 6, 4, 26, 28)
    _l(d, [(10,10),(22,10)])
    _l(d, [(10,15),(22,15)])
    _l(d, [(10,20),(16,20)])
    _dot(d, 21,21, r=3, c=_ACE)
    return _done(img)

def ico_diseno():
    img, d = _new()
    _l(d, [(8,24),(20,8),(25,13),(13,29),(8,24)], lw=2)
    _l(d, [(20,8),(25,13)], lw=2)
    return _done(img)

def ico_dwg_ext():
    img, d = _new()
    _poly(d, [(6,4),(20,4),(26,10),(26,28),(6,28)], lw=2)
    _l(d, [(20,4),(20,10),(26,10)])
    _l(d, [(10,18),(16,14),(22,18)], lw=2, c=_ACE)
    return _done(img)

def ico_ayuda():
    img, d = _new()
    _e(d, 4, 4, 28, 28, lw=2)
    _l(d, [(16,20),(16,22)], lw=3)
    _arc(d, 10,10,22,20, 200, 0, lw=2)
    _l(d, [(16,17),(16,19)], lw=2)
    return _done(img)

def ico_atajos():
    img, d = _new()
    _r(d, 4, 10, 28, 26)
    for x in [8,13,18,23]:
        _r(d, x, 13, x+3, 17, lw=1)
    _r(d, 8, 20, 20, 23, lw=1)
    return _done(img)

def ico_select():
    """Cursor de selección — flecha diagonal con sombra."""
    img, d = _new()
    _poly(d, [(8, 4), (8, 22), (12, 18), (16, 27), (19, 25), (15, 16), (21, 16), (8, 4)])
    return _done(img)


def ico_blank():
    """Ícono transparente para mantener alineación en ítems sin ícono."""
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    return img


# ══════════════════════════════════════════════════════════════════════════════
# BUILDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def build_icons() -> dict[str, ImageTk.PhotoImage]:
    """Genera todos los íconos como PhotoImage.
    Llamar UNA sola vez después de crear la ventana Tk.
    Guardar la referencia en self._menu_icons para evitar GC.
    """
    _MAP: dict[str, callable] = {
        # HERRAMIENTAS
        "select":       ico_select,
        # ARCHIVO
        "nuevo":        ico_nuevo,
        "abrir":        ico_abrir,
        "importar":     ico_importar,
        "guardar":      ico_guardar,
        "guardar_as":   ico_guardar_como,
        "exportar_dxf": ico_exportar_dxf,
        "exportar_png": ico_exportar_png,
        "exportar_pdf": ico_exportar_pdf,
        "lamina":       ico_lamina,
        "modelo":       ico_modelo,
        "papel":        ico_papel,
        "pag_setup":    ico_pag_setup,
        "salir":        ico_salir,
        # DIBUJAR
        "linea":       ico_linea,
        "polilinea":   ico_polilinea,
        "spline":      ico_spline,
        "rectangulo":  ico_rectangulo,
        "circulo":     ico_circulo,
        "arco":        ico_arco,
        "elipse":      ico_elipse,
        "poligono":    ico_poligono,
        "texto":       ico_texto,
        "lider":       ico_lider,
        "nube":        ico_nube,
        "xline":       ico_xline,
        "bloque_def":  ico_bloque_def,
        "bloque_ins":  ico_bloque_ins,
        "imagen":      ico_imagen,
        "atributos":   ico_atributos,
        "hatch":       ico_hatch,
        # EDITAR
        "deshacer":    ico_deshacer,
        "rehacer":     ico_rehacer,
        "sel_todo":    ico_sel_todo,
        "borrar":      ico_borrar,
        "mover":       ico_mover,
        "copiar":      ico_copiar,
        "rotar":       ico_rotar,
        "escalar":     ico_escalar,
        "espejo":      ico_espejo,
        "offset":      ico_offset,
        "alinear":     ico_alinear,
        "array":       ico_array,
        "recortar":    ico_recortar,
        "extender":    ico_extender,
        "fillet":      ico_fillet,
        "chamfer":     ico_chamfer,
        "partir":      ico_partir,
        "explotar":    ico_explotar,
        "propiedades": ico_propiedades,
        "matchprop":   ico_matchprop,
        # COTAS
        "dim_h":   ico_dim_h,
        "dim_v":   ico_dim_v,
        "dim_a":   ico_dim_a,
        "dim_ang": ico_dim_ang,
        "dim_r":   ico_dim_r,
        "dim_d":   ico_dim_d,
        "dim_arc": ico_dim_arc,
        "dim_co":  ico_dim_co,
        "dim_ba":  ico_dim_ba,
        "dim_sp":  ico_dim_sp,
        "dim_ord": ico_dim_ord,
        "dim_cfg": ico_dim_cfg,
        # CAPAS
        "capas":      ico_capas,
        "nueva_capa": ico_nueva_capa,
        "layiso":     ico_layiso,
        "layoff":     ico_layoff,
        "layon":      ico_layon,
        "laylock":    ico_laylock,
        "layulk":     ico_layulk,
        "laymcur":    ico_laymcur,
        # MEDIR
        "dist":     ico_dist,
        "measure":  ico_measure,
        "area":     ico_area,
        "id_pt":    ico_id_pt,
        "list_ent": ico_list_ent,
        # VER
        "zoom_e":  ico_zoom_e,
        "zoom_a":  ico_zoom_a,
        "zoom_w":  ico_zoom_w,
        "zoom_p":  ico_zoom_p,
        "zoom_xp": ico_zoom_xp,
        "regen":   ico_regen,
        "pan":     ico_pan,
        "scroll":  ico_scroll,
        "snap":    ico_snap,
        "grid":    ico_grid,
        "ortho":   ico_ortho,
        "dyn":     ico_dyn,
        "lts":     ico_lts,
        "colores": ico_colores,
        # CONFIG
        "cfg_general": ico_cfg_general,
        "cfg_cotas":   ico_cfg_cotas,
        "cfg_texto":   ico_cfg_texto,
        "cfg_visual":  ico_cfg_visual,
        "cfg_ia":      ico_cfg_ia,
        "menu_ctx":    ico_menu_ctx,
        # IA / HUB
        "ia_chat":   ico_ia_chat,
        "sle":       ico_sle,
        "terreno":   ico_terreno,
        "programa":  ico_programa,
        "diseno":    ico_diseno,
        "dwg_ext":   ico_dwg_ext,
        # AYUDA
        "ayuda":   ico_ayuda,
        "atajos":  ico_atajos,
        # Placeholder
        "blank":   ico_blank,
    }

    result: dict[str, ImageTk.PhotoImage] = {}
    errors = []
    for name, fn in _MAP.items():
        try:
            result[name] = _photo(fn())
        except Exception as exc:
            errors.append(f"  {name}: {exc}")

    if errors:
        print(f"[menu_icons] {len(errors)} error(es) al generar íconos:")
        for e in errors:
            print(e)

    return result


def build_icons_pil(size: int = 18) -> "dict[str, Image.Image]":
    """Genera todos los íconos como PIL Image redimensionados a `size`×`size`.
    Usar para CTkImage (no necesita ventana Tk activa).
    """
    _BUILD_MAP = {
        "select":       ico_select,
        "nuevo":        ico_nuevo,
        "abrir":        ico_abrir,
        "importar":     ico_importar,
        "guardar":      ico_guardar,
        "guardar_as":   ico_guardar_como,
        "exportar_dxf": ico_exportar_dxf,
        "exportar_png": ico_exportar_png,
        "exportar_pdf": ico_exportar_pdf,
        "deshacer":     ico_deshacer,
        "rehacer":      ico_rehacer,
        "linea":        ico_linea,
        "polilinea":    ico_polilinea,
        "spline":       ico_spline,
        "rectangulo":   ico_rectangulo,
        "circulo":      ico_circulo,
        "arco":         ico_arco,
        "texto":        ico_texto,
        "elipse":       ico_elipse,
        "poligono":     ico_poligono,
        "xline":        ico_xline,
        "nube":         ico_nube,
        "lider":        ico_lider,
        "hatch":        ico_hatch,
        "bloque_ins":   ico_bloque_ins,
        "bloque_def":   ico_bloque_def,
        "imagen":       ico_imagen,
        "atributos":    ico_atributos,
        "dim_h":        ico_dim_h,
        "dim_v":        ico_dim_v,
        "dim_a":        ico_dim_a,
        "dim_ang":      ico_dim_ang,
        "dim_r":        ico_dim_r,
        "dim_co":       ico_dim_co,
        "dim_ba":       ico_dim_ba,
        "dim_sp":       ico_dim_sp,
        "dim_d":        ico_dim_d,
        "dim_arc":      ico_dim_arc,
        "dim_ord":      ico_dim_ord,
        "borrar":       ico_borrar,
        "mover":        ico_mover,
        "copiar":       ico_copiar,
        "array":        ico_array,
        "rotar":        ico_rotar,
        "escalar":      ico_escalar,
        "espejo":       ico_espejo,
        "offset":       ico_offset,
        "alinear":      ico_alinear,
        "recortar":     ico_recortar,
        "extender":     ico_extender,
        "fillet":       ico_fillet,
        "chamfer":      ico_chamfer,
        "partir":       ico_partir,
        "explotar":     ico_explotar,
        "matchprop":    ico_matchprop,
        "dist":         ico_dist,
        "id_pt":        ico_id_pt,
        "list_ent":     ico_list_ent,
        "area":         ico_area,
        "measure":      ico_measure,
        "laymcur":      ico_laymcur,
        "zoom_e":       ico_zoom_e,
        "snap":         ico_snap,
        "grid":         ico_grid,
        "ortho":        ico_ortho,
        "lts":          ico_lts,
        "layiso":       ico_layiso,
        "layon":        ico_layon,
    }
    result: dict[str, Image.Image] = {}
    for name, fn in _BUILD_MAP.items():
        try:
            img16 = fn()
            result[name] = img16.resize((size, size), Image.LANCZOS) if size != 16 else img16
        except Exception as exc:
            print(f"[build_icons_pil] {name}: {exc}")
    return result
