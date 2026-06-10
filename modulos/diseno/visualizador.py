import os
import base64
from PIL import Image, ImageDraw, ImageFont

COLORES = {
    "dormitorio_principal": (59, 130, 246),
    "dormitorio": (147, 197, 253),
    "cocina": (251, 191, 36),
    "sala": (52, 211, 153),
    "sala_comedor": (52, 211, 153),
    "comedor": (167, 139, 250),
    "bano": (6, 182, 212),
    "pasillo": (156, 163, 175),
    "cochera": (107, 114, 128),
    "lavanderia": (244, 114, 182),
    "estudio": (252, 165, 165),
    "exterior": (134, 239, 172),
    "otro": (209, 213, 219),
}


def _color_recinto(nombre: str):
    from modulos.diseno.validador import detectar_tipo
    return COLORES.get(detectar_tipo(nombre), COLORES["otro"])


def _texto_legible(color_bg):
    luminancia = (0.299 * color_bg[0] + 0.587 * color_bg[1] + 0.114 * color_bg[2]) / 255
    return (15, 23, 42) if luminancia > 0.5 else (248, 250, 252)


def _cargar_font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
        except Exception:
            return ImageFont.load_default()


def generar_preview(grid, ruta_salida: str, escala_px: int = 60, validacion: dict = None) -> dict:
    """Genera preview mejorado con líneas de circulación y panel de errores/sugerencias."""
    from modulos.diseno.validador import detectar_tipo

    margen = 80
    ancho_img = grid.cols * escala_px + margen * 2
    alto_img = grid.rows * escala_px + margen * 2 + 60

    panel_ancho = 0
    if validacion and (validacion.get("errores") or validacion.get("advertencias") or validacion.get("sugerencias")):
        panel_ancho = 300

    img = Image.new("RGB", (ancho_img + panel_ancho, alto_img), (15, 23, 42))
    draw = ImageDraw.Draw(img)

    font_grande = _cargar_font(int(escala_px * 0.32))
    font_med = _cargar_font(int(escala_px * 0.24))
    font_chico = _cargar_font(int(escala_px * 0.20))
    font_muy_chico = _cargar_font(int(escala_px * 0.15))

    # Grid de fondo
    for r in range(grid.rows + 1):
        y = margen + r * escala_px
        draw.line([(margen, y), (margen + grid.cols * escala_px, y)],
                  fill=(71, 85, 105), width=1)
    for c in range(grid.cols + 1):
        x = margen + c * escala_px
        draw.line([(x, margen), (x, margen + grid.rows * escala_px)],
                  fill=(71, 85, 105), width=1)

    # Etiquetas de coordenadas
    for c in range(grid.cols):
        x = margen + c * escala_px + escala_px // 2
        draw.text((x - 8, margen - 22), str(c), fill=(148, 163, 184), font=font_chico)
    for r in range(grid.rows):
        y = margen + r * escala_px + escala_px // 2
        draw.text((margen - 26, y - 8), str(r), fill=(148, 163, 184), font=font_chico)

    # Pintar recintos primero
    for nombre, info in grid.recintos.items():
        color = _color_recinto(nombre)
        x1 = margen + info["col"] * escala_px
        y1 = margen + info["fila"] * escala_px
        x2 = x1 + info["ancho"] * escala_px
        y2 = y1 + info["alto"] * escala_px

        draw.rectangle([x1, y1, x2, y2], fill=color, outline=(15, 23, 42), width=3)

        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        nombre_up = nombre.upper()
        txt_color = _texto_legible(color)
        bbox = draw.textbbox((0, 0), nombre_up, font=font_grande)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        draw.text((cx - w // 2, cy - h - 2), nombre_up, fill=txt_color, font=font_grande)

        dim_txt = f"{info['ancho_m']:.1f} x {info['alto_m']:.1f}m"
        bbox2 = draw.textbbox((0, 0), dim_txt, font=font_med)
        w2 = bbox2[2] - bbox2[0]
        draw.text((cx - w2 // 2, cy + 2), dim_txt, fill=txt_color, font=font_med)

        area_txt = f"{info['area_m2']:.1f} m2"
        bbox3 = draw.textbbox((0, 0), area_txt, font=font_chico)
        w3 = bbox3[2] - bbox3[0]
        draw.text((cx - w3 // 2, cy + 24), area_txt, fill=txt_color, font=font_chico)

    # Líneas de circulación SOBRE los recintos (tenues pero visibles)
    if validacion:
        ady = validacion.get("adyacencias", {})
        procesados = set()
        for nombre, vecinos in ady.items():
            if nombre not in grid.recintos:
                continue
            info1 = grid.recintos[nombre]
            cx1 = margen + (info1["col"] + info1["ancho"] / 2) * escala_px
            cy1 = margen + (info1["fila"] + info1["alto"] / 2) * escala_px

            for vecino in vecinos:
                par = tuple(sorted([nombre, vecino]))
                if par in procesados or vecino not in grid.recintos:
                    continue
                procesados.add(par)
                info2 = grid.recintos[vecino]
                cx2 = margen + (info2["col"] + info2["ancho"] / 2) * escala_px
                cy2 = margen + (info2["fila"] + info2["alto"] / 2) * escala_px

                # Línea punteada simulada (segmentos cortos)
                dx = cx2 - cx1
                dy = cy2 - cy1
                import math
                dist = math.hypot(dx, dy)
                if dist > 0:
                    pasos = int(dist / 12)
                    for i in range(0, pasos, 2):
                        fx1 = cx1 + dx * (i / max(pasos, 1))
                        fy1 = cy1 + dy * (i / max(pasos, 1))
                        fx2 = cx1 + dx * (min(i + 1, pasos) / max(pasos, 1))
                        fy2 = cy1 + dy * (min(i + 1, pasos) / max(pasos, 1))
                        draw.line([(fx1, fy1), (fx2, fy2)], fill=(220, 220, 255), width=2)

    # Norte
    norte_x = ancho_img - margen - 20
    norte_y = margen + 20
    draw.line([(norte_x, norte_y), (norte_x, norte_y - 30)], fill=(248, 250, 252), width=2)
    draw.polygon([
        (norte_x - 6, norte_y - 24),
        (norte_x + 6, norte_y - 24),
        (norte_x, norte_y - 38),
    ], fill=(248, 250, 252))
    draw.text((norte_x - 5, norte_y + 4), "N", fill=(248, 250, 252), font=font_med)

    # Indicador de calle (asume sur)
    calle_y = margen + grid.rows * escala_px + 25
    draw.text((margen, calle_y), "← CALLE / FRENTE →",
              fill=(251, 191, 36), font=font_med)

    # Titulo
    titulo = f"Layout {grid.ancho_m}x{grid.alto_m}m  |  {len(grid.recintos)} recintos"
    draw.text((margen, alto_img - 40), titulo, fill=(248, 250, 252), font=font_med)

    # Panel de validación (derecha)
    if validacion and panel_ancho > 0:
        panel_x = ancho_img
        panel_y = margen

        draw.rectangle([panel_x, panel_y, panel_x + panel_ancho, alto_img],
                      fill=(25, 35, 55), outline=(71, 85, 105), width=2)

        y_offset = panel_y + 10

        score = validacion.get("score", 0)
        if score >= 80:
            color_score = (22, 163, 74)
        elif score >= 60:
            color_score = (251, 191, 36)
        else:
            color_score = (220, 38, 38)

        draw.text((panel_x + 10, y_offset), f"Score: {score}/100",
                 fill=color_score, font=font_med)
        y_offset += 35

        errores = validacion.get("errores", [])
        if errores:
            draw.text((panel_x + 10, y_offset), "ERRORES:",
                     fill=(220, 38, 38), font=font_chico)
            y_offset += 20
            for err in errores[:2]:
                texto_corto = err[:35] + "..." if len(err) > 35 else err
                draw.text((panel_x + 15, y_offset), f"• {texto_corto}",
                         fill=(252, 165, 165), font=font_muy_chico)
                y_offset += 18

        advertencias = validacion.get("advertencias", [])
        if advertencias:
            draw.text((panel_x + 10, y_offset), "ADVERTENCIAS:",
                     fill=(251, 191, 36), font=font_chico)
            y_offset += 20
            for adv in advertencias[:2]:
                texto_corto = adv[:35] + "..." if len(adv) > 35 else adv
                draw.text((panel_x + 15, y_offset), f"• {texto_corto}",
                         fill=(252, 217, 89), font=font_muy_chico)
                y_offset += 18

        sugerencias = validacion.get("sugerencias", [])
        if sugerencias:
            draw.text((panel_x + 10, y_offset), "SUGERENCIAS:",
                     fill=(52, 211, 153), font=font_chico)
            y_offset += 20
            for sug in sugerencias[:2]:
                texto_corto = sug[:35] + "..." if len(sug) > 35 else sug
                draw.text((panel_x + 15, y_offset), f"{texto_corto}",
                         fill=(126, 239, 201), font=font_muy_chico)
                y_offset += 18

    os.makedirs(os.path.dirname(ruta_salida) or ".", exist_ok=True)
    img.save(ruta_salida, "PNG")

    with open(ruta_salida, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    return {"path": ruta_salida, "base64": b64, "width": ancho_img + panel_ancho, "height": alto_img}


def generar_preview_simple(grid, ruta_salida: str, escala_px: int = 60) -> dict:
    """Preview sin panel lateral — para IAs sin vision o Groq."""
    return generar_preview(grid, ruta_salida, escala_px=escala_px, validacion=None)
