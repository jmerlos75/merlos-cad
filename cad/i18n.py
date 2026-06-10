"""
cad/i18n.py — Internacionalización de Merlos CAD
=================================================
5 idiomas: ES · EN · ZH · RU · PT
Uso:  from cad.i18n import t, set_language, LANGUAGES, current_lang
"""
from __future__ import annotations

# ── Idiomas disponibles ──────────────────────────────────────────────
LANGUAGES: dict[str, str] = {
    "es": "Español",
    "en": "English",
    "zh": "中文",
    "ru": "Русский",
    "pt": "Português",
}

_lang: str = "es"   # idioma activo


def set_language(code: str) -> None:
    global _lang
    if code in LANGUAGES:
        _lang = code


def current_lang() -> str:
    return _lang


def t(key: str) -> str:
    """Traduce una clave al idioma activo. Fallback: español → inglés → clave."""
    row = _STRINGS.get(key, {})
    return row.get(_lang) or row.get("es") or row.get("en") or key


# ══════════════════════════════════════════════════════════════════════
# TABLA DE TRADUCCIONES
# ══════════════════════════════════════════════════════════════════════
_STRINGS: dict[str, dict[str, str]] = {

    # ── PROMPTS DE HERRAMIENTA ────────────────────────────────────────

    "line_p0": {
        "es": "LINE  Especifique primer punto:  [X,Y]  [@X,Y]  [@dist<ang]",
        "en": "LINE  Specify first point:  [X,Y]  [@X,Y]  [@dist<ang]",
        "zh": "LINE  指定第一点:  [X,Y]  [@X,Y]  [@dist<ang]",
        "ru": "LINE  Укажите первую точку:  [X,Y]  [@X,Y]  [@dist<ang]",
        "pt": "LINE  Especifique o primeiro ponto:  [X,Y]  [@X,Y]  [@dist<ang]",
    },
    "line_p1": {
        "es": "LINE  Especifique punto siguiente o [Deshacer/U]:  [X,Y]  [dist]",
        "en": "LINE  Specify next point or [Undo/U]:  [X,Y]  [dist]",
        "zh": "LINE  指定下一点或 [撤销/U]:  [X,Y]  [dist]",
        "ru": "LINE  Укажите следующую точку или [Отменить/U]:  [X,Y]  [dist]",
        "pt": "LINE  Especifique o próximo ponto ou [Desfazer/U]:  [X,Y]  [dist]",
    },
    "line_p2": {
        "es": "LINE  Especifique punto siguiente o [Cerrar/C  Deshacer/U]:",
        "en": "LINE  Specify next point or [Close/C  Undo/U]:",
        "zh": "LINE  指定下一点或 [闭合/C  撤销/U]:",
        "ru": "LINE  Укажите следующую точку или [Замкнуть/C  Отменить/U]:",
        "pt": "LINE  Especifique o próximo ponto ou [Fechar/C  Desfazer/U]:",
    },
    "pline_p0": {
        "es": "PLINE  Especifique punto inicial:  [X,Y]  [@X,Y]",
        "en": "PLINE  Specify start point:  [X,Y]  [@X,Y]",
        "zh": "PLINE  指定起点:  [X,Y]  [@X,Y]",
        "ru": "PLINE  Укажите начальную точку:  [X,Y]  [@X,Y]",
        "pt": "PLINE  Especifique o ponto inicial:  [X,Y]  [@X,Y]",
    },
    "pline_p1": {
        "es": "PLINE  Especifique punto siguiente o [Cerrar/C  Deshacer/U]:  [X,Y]  [dist]",
        "en": "PLINE  Specify next point or [Close/C  Undo/U]:  [X,Y]  [dist]",
        "zh": "PLINE  指定下一点或 [闭合/C  撤销/U]:  [X,Y]  [dist]",
        "ru": "PLINE  Укажите следующую точку или [Замкнуть/C  Отменить/U]:  [X,Y]  [dist]",
        "pt": "PLINE  Especifique o próximo ponto ou [Fechar/C  Desfazer/U]:  [X,Y]  [dist]",
    },
    "pline_p2": {
        "es": "PLINE  Especifique punto siguiente o [Cerrar/C  Deshacer/U]:",
        "en": "PLINE  Specify next point or [Close/C  Undo/U]:",
        "zh": "PLINE  指定下一点或 [闭合/C  撤销/U]:",
        "ru": "PLINE  Укажите следующую точку или [Замкнуть/C  Отменить/U]:",
        "pt": "PLINE  Especifique o próximo ponto ou [Fechar/C  Desfazer/U]:",
    },
    "spline_p0": {
        "es": "SPLINE  Especifique primer punto de control:  [X,Y]",
        "en": "SPLINE  Specify first control point:  [X,Y]",
        "zh": "SPLINE  指定第一个控制点:  [X,Y]",
        "ru": "SPLINE  Укажите первую контрольную точку:  [X,Y]",
        "pt": "SPLINE  Especifique o primeiro ponto de controle:  [X,Y]",
    },
    "spline_p1": {
        "es": "SPLINE  Próximo punto de control  [Enter=finalizar  C=cerrar]:  [X,Y]",
        "en": "SPLINE  Next control point  [Enter=finish  C=close]:  [X,Y]",
        "zh": "SPLINE  下一个控制点  [Enter=完成  C=闭合]:  [X,Y]",
        "ru": "SPLINE  Следующая контрольная точка  [Enter=завершить  C=замкнуть]:  [X,Y]",
        "pt": "SPLINE  Próximo ponto de controle  [Enter=finalizar  C=fechar]:  [X,Y]",
    },
    "spline_p2": {
        "es": "SPLINE  Próximo punto de control  [Enter=finalizar  C=cerrar]:",
        "en": "SPLINE  Next control point  [Enter=finish  C=close]:",
        "zh": "SPLINE  下一个控制点  [Enter=完成  C=闭合]:",
        "ru": "SPLINE  Следующая контрольная точка  [Enter=завершить  C=замкнуть]:",
        "pt": "SPLINE  Próximo ponto de controle  [Enter=finalizar  C=fechar]:",
    },
    "rect_p0": {
        "es": "REC  Especifique primera esquina:  [X,Y]",
        "en": "REC  Specify first corner:  [X,Y]",
        "zh": "REC  指定第一角点:  [X,Y]",
        "ru": "REC  Укажите первый угол:  [X,Y]",
        "pt": "REC  Especifique o primeiro canto:  [X,Y]",
    },
    "rect_p1": {
        "es": "REC  Especifique esquina opuesta:  [X,Y]  [ancho,alto]",
        "en": "REC  Specify opposite corner:  [X,Y]  [width,height]",
        "zh": "REC  指定对角点:  [X,Y]  [宽,高]",
        "ru": "REC  Укажите противоположный угол:  [X,Y]  [ширина,высота]",
        "pt": "REC  Especifique o canto oposto:  [X,Y]  [largura,altura]",
    },
    "circle_p0": {
        "es": "CIRCLE  Especifique centro del círculo:  [X,Y]",
        "en": "CIRCLE  Specify circle center:  [X,Y]",
        "zh": "CIRCLE  指定圆心:  [X,Y]",
        "ru": "CIRCLE  Укажите центр окружности:  [X,Y]",
        "pt": "CIRCLE  Especifique o centro do círculo:  [X,Y]",
    },
    "circle_p1": {
        "es": "CIRCLE  Especifique radio o [Diámetro/D]:  [valor]  (Tab = cambiar modo)",
        "en": "CIRCLE  Specify radius or [Diameter/D]:  [value]  (Tab = toggle mode)",
        "zh": "CIRCLE  指定半径或 [直径/D]:  [值]  (Tab = 切换模式)",
        "ru": "CIRCLE  Укажите радиус или [Диаметр/D]:  [значение]  (Tab = сменить режим)",
        "pt": "CIRCLE  Especifique raio ou [Diâmetro/D]:  [valor]  (Tab = alternar modo)",
    },
    "arc_p0": {
        "es": "ARC  Especifique punto inicial del arco:  [X,Y]",
        "en": "ARC  Specify arc start point:  [X,Y]",
        "zh": "ARC  指定圆弧起点:  [X,Y]",
        "ru": "ARC  Укажите начальную точку дуги:  [X,Y]",
        "pt": "ARC  Especifique o ponto inicial do arco:  [X,Y]",
    },
    "arc_p1": {
        "es": "ARC  Especifique segundo punto del arco:  [X,Y]",
        "en": "ARC  Specify second point of arc:  [X,Y]",
        "zh": "ARC  指定圆弧上的第二点:  [X,Y]",
        "ru": "ARC  Укажите вторую точку дуги:  [X,Y]",
        "pt": "ARC  Especifique o segundo ponto do arco:  [X,Y]",
    },
    "arc_p2": {
        "es": "ARC  Especifique punto final del arco:  [X,Y]",
        "en": "ARC  Specify arc end point:  [X,Y]",
        "zh": "ARC  指定圆弧终点:  [X,Y]",
        "ru": "ARC  Укажите конечную точку дуги:  [X,Y]",
        "pt": "ARC  Especifique o ponto final do arco:  [X,Y]",
    },
    "text_p0": {
        "es": "TEXT  Especifique punto de inserción:  [X,Y]",
        "en": "TEXT  Specify insertion point:  [X,Y]",
        "zh": "TEXT  指定插入点:  [X,Y]",
        "ru": "TEXT  Укажите точку вставки:  [X,Y]",
        "pt": "TEXT  Especifique o ponto de inserção:  [X,Y]",
    },
    "text_p1": {
        "es": "TEXT  Escribe el texto y presiona Enter:",
        "en": "TEXT  Type the text and press Enter:",
        "zh": "TEXT  输入文字并按 Enter:",
        "ru": "TEXT  Введите текст и нажмите Enter:",
        "pt": "TEXT  Digite o texto e pressione Enter:",
    },
    "select_p0": {
        "es": "SELECT  Clic en entidad o arrastra para seleccionar área:",
        "en": "SELECT  Click entity or drag to select area:",
        "zh": "SELECT  单击图元或拖动选择区域:",
        "ru": "SELECT  Щёлкните объект или выделите область:",
        "pt": "SELECT  Clique em entidade ou arraste para selecionar área:",
    },

    # ── PROMPTS DE OPERACIÓN ─────────────────────────────────────────

    "op_move_base": {
        "es": "MOVE  Especifique punto base:  [X,Y]",
        "en": "MOVE  Specify base point:  [X,Y]",
        "zh": "MOVE  指定基点:  [X,Y]",
        "ru": "MOVE  Укажите базовую точку:  [X,Y]",
        "pt": "MOVE  Especifique o ponto base:  [X,Y]",
    },
    "op_move_dest": {
        "es": "MOVE  Especifique punto destino:  [X,Y]  [@X,Y]",
        "en": "MOVE  Specify destination point:  [X,Y]  [@X,Y]",
        "zh": "MOVE  指定目标点:  [X,Y]  [@X,Y]",
        "ru": "MOVE  Укажите точку назначения:  [X,Y]  [@X,Y]",
        "pt": "MOVE  Especifique o ponto destino:  [X,Y]  [@X,Y]",
    },
    "op_copy_dest": {
        "es": "COPY  Especifique punto destino:  [X,Y]  [@X,Y]  (Enter = terminar)",
        "en": "COPY  Specify destination point:  [X,Y]  [@X,Y]  (Enter = done)",
        "zh": "COPY  指定目标点:  [X,Y]  [@X,Y]  (Enter = 完成)",
        "ru": "COPY  Укажите точку назначения:  [X,Y]  [@X,Y]  (Enter = завершить)",
        "pt": "COPY  Especifique o ponto destino:  [X,Y]  [@X,Y]  (Enter = concluir)",
    },
    "op_rotate_base": {
        "es": "ROTATE  Especifique punto base de rotación:  [X,Y]",
        "en": "ROTATE  Specify base point of rotation:  [X,Y]",
        "zh": "ROTATE  指定旋转基点:  [X,Y]",
        "ru": "ROTATE  Укажите базовую точку вращения:  [X,Y]",
        "pt": "ROTATE  Especifique o ponto base de rotação:  [X,Y]",
    },
    "op_rotate_angle": {
        "es": "ROTATE  Especifique ángulo de rotación:  [grados]  (CCW positivo)",
        "en": "ROTATE  Specify rotation angle:  [degrees]  (CCW positive)",
        "zh": "ROTATE  指定旋转角度:  [度]  (逆时针为正)",
        "ru": "ROTATE  Укажите угол поворота:  [градусы]  (против часовой = плюс)",
        "pt": "ROTATE  Especifique o ângulo de rotação:  [graus]  (CCW positivo)",
    },
    "op_scale_base": {
        "es": "SCALE  Especifique punto base:  [X,Y]",
        "en": "SCALE  Specify base point:  [X,Y]",
        "zh": "SCALE  指定基点:  [X,Y]",
        "ru": "SCALE  Укажите базовую точку:  [X,Y]",
        "pt": "SCALE  Especifique o ponto base:  [X,Y]",
    },
    "op_scale_factor": {
        "es": "SCALE  Especifique factor de escala:  [número]  (ej: 2 = doble)",
        "en": "SCALE  Specify scale factor:  [number]  (e.g. 2 = double)",
        "zh": "SCALE  指定缩放比例:  [数值]  (例: 2 = 两倍)",
        "ru": "SCALE  Укажите масштабный коэффициент:  [число]  (напр.: 2 = двойной)",
        "pt": "SCALE  Especifique o fator de escala:  [número]  (ex: 2 = dobro)",
    },
    "op_mirror_p1": {
        "es": "MIRROR  Especifique primer punto del eje de simetría:  [X,Y]",
        "en": "MIRROR  Specify first point of mirror line:  [X,Y]",
        "zh": "MIRROR  指定镜像线第一点:  [X,Y]",
        "ru": "MIRROR  Укажите первую точку оси симметрии:  [X,Y]",
        "pt": "MIRROR  Especifique o primeiro ponto do eixo de simetria:  [X,Y]",
    },
    "op_mirror_p2": {
        "es": "MIRROR  Especifique segundo punto del eje de simetría:  [X,Y]",
        "en": "MIRROR  Specify second point of mirror line:  [X,Y]",
        "zh": "MIRROR  指定镜像线第二点:  [X,Y]",
        "ru": "MIRROR  Укажите вторую точку оси симметрии:  [X,Y]",
        "pt": "MIRROR  Especifique o segundo ponto do eixo de simetria:  [X,Y]",
    },
    "op_mirror_keep": {
        "es": "MIRROR  ¿Borrar originales?  [S/Y = Sí     N = No     Enter = No]:",
        "en": "MIRROR  Delete source objects?  [Y = Yes     N = No     Enter = No]:",
        "zh": "MIRROR  是否删除原始对象?  [Y = 是     N = 否     Enter = 否]:",
        "ru": "MIRROR  Удалить исходные объекты?  [Y = Да     N = Нет     Enter = Нет]:",
        "pt": "MIRROR  Apagar originais?  [S/Y = Sim     N = Não     Enter = Não]:",
    },
    "op_offset_dist": {
        "es": "OFFSET  Especifique distancia de desfase:  [valor]",
        "en": "OFFSET  Specify offset distance:  [value]",
        "zh": "OFFSET  指定偏移距离:  [值]",
        "ru": "OFFSET  Укажите расстояние смещения:  [значение]",
        "pt": "OFFSET  Especifique a distância de desvio:  [valor]",
    },
    "op_offset_sel": {
        "es": "OFFSET  Clic en entidad a desfasar:",
        "en": "OFFSET  Click entity to offset:",
        "zh": "OFFSET  单击要偏移的图元:",
        "ru": "OFFSET  Щёлкните объект для смещения:",
        "pt": "OFFSET  Clique na entidade para desviar:",
    },
    "op_offset_side": {
        "es": "OFFSET  Mueva cursor al lado deseado y haga clic para fijar:",
        "en": "OFFSET  Move cursor to desired side and click to confirm:",
        "zh": "OFFSET  将光标移至所需侧并单击确认:",
        "ru": "OFFSET  Переместите курсор на нужную сторону и щёлкните:",
        "pt": "OFFSET  Mova o cursor para o lado desejado e clique para confirmar:",
    },
    "op_trim_obj": {
        "es": "TRIM  Clic en el segmento a eliminar  (bordes auto-detectados):",
        "en": "TRIM  Click on the segment to remove  (edges auto-detected):",
        "zh": "TRIM  单击要删除的线段  (自动检测边界):",
        "ru": "TRIM  Щёлкните сегмент для удаления  (кромки авто-определяются):",
        "pt": "TRIM  Clique no segmento a eliminar  (bordas auto-detectadas):",
    },
    "op_extend_obj": {
        "es": "EXTEND  Clic cerca del extremo a extender  (límite auto-detectado):",
        "en": "EXTEND  Click near the endpoint to extend  (boundary auto-detected):",
        "zh": "EXTEND  单击要延伸的端点附近  (自动检测边界):",
        "ru": "EXTEND  Щёлкните рядом с концом для продления  (граница авто):",
        "pt": "EXTEND  Clique perto da extremidade a estender  (limite auto):",
    },
    "op_fillet_r": {
        "es": "FILLET  Especifique radio de empalme — escribe valor y Enter (o Enter = mantener):",
        "en": "FILLET  Specify fillet radius — type value and Enter (or Enter = keep):",
        "zh": "FILLET  指定圆角半径 — 输入值并按 Enter (或 Enter = 保持):",
        "ru": "FILLET  Укажите радиус скругления — введите значение и Enter (или Enter = сохранить):",
        "pt": "FILLET  Especifique o raio de arredondamento — escreva valor e Enter (ou Enter = manter):",
    },
    "op_fillet_p1": {
        "es": "FILLET  Clic en 1ª línea  (escribe número para cambiar radio):",
        "en": "FILLET  Click 1st line  (type number to change radius):",
        "zh": "FILLET  单击第一条线  (输入数字更改半径):",
        "ru": "FILLET  Щёлкните 1-ю линию  (введите число для изменения радиуса):",
        "pt": "FILLET  Clique na 1ª linha  (digite número para alterar raio):",
    },
    "op_fillet_p2": {
        "es": "FILLET  Clic en 2ª línea:",
        "en": "FILLET  Click 2nd line:",
        "zh": "FILLET  单击第二条线:",
        "ru": "FILLET  Щёлкните 2-ю линию:",
        "pt": "FILLET  Clique na 2ª linha:",
    },
    "op_dist_p1": {
        "es": "DISTANCE  Especifique primer punto:  [X,Y]",
        "en": "DISTANCE  Specify first point:  [X,Y]",
        "zh": "DISTANCE  指定第一点:  [X,Y]",
        "ru": "DISTANCE  Укажите первую точку:  [X,Y]",
        "pt": "DISTANCE  Especifique o primeiro ponto:  [X,Y]",
    },
    "op_dist_p2": {
        "es": "DISTANCE  Especifique segundo punto:  [X,Y]",
        "en": "DISTANCE  Specify second point:  [X,Y]",
        "zh": "DISTANCE  指定第二点:  [X,Y]",
        "ru": "DISTANCE  Укажите вторую точку:  [X,Y]",
        "pt": "DISTANCE  Especifique o segundo ponto:  [X,Y]",
    },
    "op_zoom_w1": {
        "es": "ZOOM WINDOW  Especifique primera esquina:",
        "en": "ZOOM WINDOW  Specify first corner:",
        "zh": "ZOOM WINDOW  指定第一角点:",
        "ru": "ZOOM WINDOW  Укажите первый угол:",
        "pt": "ZOOM WINDOW  Especifique o primeiro canto:",
    },
    "op_zoom_w2": {
        "es": "ZOOM WINDOW  Especifique esquina opuesta:",
        "en": "ZOOM WINDOW  Specify opposite corner:",
        "zh": "ZOOM WINDOW  指定对角点:",
        "ru": "ZOOM WINDOW  Укажите противоположный угол:",
        "pt": "ZOOM WINDOW  Especifique o canto oposto:",
    },
    "op_matchprop_src": {
        "es": "MATCHPROP  Seleccione entidad de origen:",
        "en": "MATCHPROP  Select source entity:",
        "zh": "MATCHPROP  选择源图元:",
        "ru": "MATCHPROP  Выберите исходный объект:",
        "pt": "MATCHPROP  Selecione a entidade de origem:",
    },
    "op_matchprop_dst": {
        "es": "MATCHPROP  Seleccione entidad destino:",
        "en": "MATCHPROP  Select destination entity:",
        "zh": "MATCHPROP  选择目标图元:",
        "ru": "MATCHPROP  Выберите целевой объект:",
        "pt": "MATCHPROP  Selecione a entidade destino:",
    },

    # ── PROMPTS DE SELECCIÓN PREVIA (antes de confirmar con Enter) ────

    "op_sel_move": {
        "es": "MOVE  ☐ Clic en entidades a mover — Enter para confirmar:",
        "en": "MOVE  ☐ Click entities to move — Enter to confirm:",
        "zh": "MOVE  ☐ 单击要移动的图元 — Enter 确认:",
        "ru": "MOVE  ☐ Щёлкните объекты для переноса — Enter для подтверждения:",
        "pt": "MOVE  ☐ Clique nas entidades a mover — Enter para confirmar:",
    },
    "op_sel_copy": {
        "es": "COPY  ☐ Clic en entidades a copiar — Enter para confirmar:",
        "en": "COPY  ☐ Click entities to copy — Enter to confirm:",
        "zh": "COPY  ☐ 单击要复制的图元 — Enter 确认:",
        "ru": "COPY  ☐ Щёлкните объекты для копирования — Enter для подтверждения:",
        "pt": "COPY  ☐ Clique nas entidades a copiar — Enter para confirmar:",
    },
    "op_sel_rotate": {
        "es": "ROTATE  ☐ Clic en entidades a rotar — Enter para confirmar:",
        "en": "ROTATE  ☐ Click entities to rotate — Enter to confirm:",
        "zh": "ROTATE  ☐ 单击要旋转的图元 — Enter 确认:",
        "ru": "ROTATE  ☐ Щёлкните объекты для поворота — Enter для подтверждения:",
        "pt": "ROTATE  ☐ Clique nas entidades a girar — Enter para confirmar:",
    },
    "op_sel_scale": {
        "es": "SCALE  ☐ Clic en entidades a escalar — Enter para confirmar:",
        "en": "SCALE  ☐ Click entities to scale — Enter to confirm:",
        "zh": "SCALE  ☐ 单击要缩放的图元 — Enter 确认:",
        "ru": "SCALE  ☐ Щёлкните объекты для масштабирования — Enter для подтверждения:",
        "pt": "SCALE  ☐ Clique nas entidades a escalar — Enter para confirmar:",
    },
    "op_sel_mirror": {
        "es": "MIRROR  ☐ Clic en entidades a reflejar — Enter para confirmar:",
        "en": "MIRROR  ☐ Click entities to mirror — Enter to confirm:",
        "zh": "MIRROR  ☐ 单击要镜像的图元 — Enter 确认:",
        "ru": "MIRROR  ☐ Щёлкните объекты для отражения — Enter для подтверждения:",
        "pt": "MIRROR  ☐ Clique nas entidades a espelhar — Enter para confirmar:",
    },
    "op_sel_align": {
        "es": "ALIGN  ☐ Clic en entidades a alinear — Enter para confirmar:",
        "en": "ALIGN  ☐ Click entities to align — Enter to confirm:",
        "zh": "ALIGN  ☐ 单击要对齐的图元 — Enter 确认:",
        "ru": "ALIGN  ☐ Щёлкните объекты для выравнивания — Enter для подтверждения:",
        "pt": "ALIGN  ☐ Clique nas entidades a alinhar — Enter para confirmar:",
    },
    "op_sel_array": {
        "es": "ARRAY  ☐ Clic en entidades a arreglar — Enter para confirmar:",
        "en": "ARRAY  ☐ Click entities to array — Enter to confirm:",
        "zh": "ARRAY  ☐ 单击要阵列的图元 — Enter 确认:",
        "ru": "ARRAY  ☐ Щёлкните объекты для массива — Enter для подтверждения:",
        "pt": "ARRAY  ☐ Clique nas entidades a arranjar — Enter para confirmar:",
    },
    "op_array_type": {
        "es": "ARRAY  Seleccione tipo:  [R] Rectangular   [P] Polar   Enter = Rectangular:",
        "en": "ARRAY  Select type:  [R] Rectangular   [P] Polar   Enter = Rectangular:",
        "zh": "ARRAY  选择类型:  [R] 矩形   [P] 环形   Enter = 矩形:",
        "ru": "ARRAY  Выберите тип:  [R] Прямоугольный   [P] Круговой   Enter = Прямоугольный:",
        "pt": "ARRAY  Selecione tipo:  [R] Retangular   [P] Polar   Enter = Retangular:",
    },
    "op_array_pol_ctr": {
        "es": "ARRAY POLAR  Clic en centro de rotación:  [X,Y]",
        "en": "ARRAY POLAR  Click on rotation center:  [X,Y]",
        "zh": "ARRAY POLAR  单击旋转中心:  [X,Y]",
        "ru": "ARRAY POLAR  Щёлкните центр вращения:  [X,Y]",
        "pt": "ARRAY POLAR  Clique no centro de rotação:  [X,Y]",
    },
    "op_array_pol_n": {
        "es": "ARRAY POLAR  Número de elementos — escribe y Enter  (Enter = 6):",
        "en": "ARRAY POLAR  Number of items — type and Enter  (Enter = 6):",
        "zh": "ARRAY POLAR  元素数量 — 输入并按 Enter  (Enter = 6):",
        "ru": "ARRAY POLAR  Количество элементов — введите и Enter  (Enter = 6):",
        "pt": "ARRAY POLAR  Número de elementos — escreva e Enter  (Enter = 6):",
    },
    "op_copy_base": {
        "es": "COPY  Especifique punto base:  [X,Y]",
        "en": "COPY  Specify base point:  [X,Y]",
        "zh": "COPY  指定基点:  [X,Y]",
        "ru": "COPY  Укажите базовую точку:  [X,Y]",
        "pt": "COPY  Especifique o ponto base:  [X,Y]",
    },

    # ── PROMPTS DE COTA LINEAL ────────────────────────────────────────

    "op_dim_lp1": {
        "es": "DIM  Especifique primer punto de extensión:  [X,Y]",
        "en": "DIM  Specify first extension point:  [X,Y]",
        "zh": "DIM  指定第一条延伸线原点:  [X,Y]",
        "ru": "DIM  Укажите первую точку выносной линии:  [X,Y]",
        "pt": "DIM  Especifique o primeiro ponto de extensão:  [X,Y]",
    },
    "op_dim_lp2": {
        "es": "DIM  Especifique segundo punto de extensión:  [X,Y]",
        "en": "DIM  Specify second extension point:  [X,Y]",
        "zh": "DIM  指定第二条延伸线原点:  [X,Y]",
        "ru": "DIM  Укажите вторую точку выносной линии:  [X,Y]",
        "pt": "DIM  Especifique o segundo ponto de extensão:  [X,Y]",
    },
    "op_dim_lpos": {
        "es": "DIM  Especifique posición de la línea de cota:  [X,Y]",
        "en": "DIM  Specify dimension line location:  [X,Y]",
        "zh": "DIM  指定尺寸线位置:  [X,Y]",
        "ru": "DIM  Укажите положение размерной линии:  [X,Y]",
        "pt": "DIM  Especifique a posição da linha de cota:  [X,Y]",
    },

    # ── PROMPTS DE COTA RADIO / DIÁMETRO ─────────────────────────────

    "op_dim_r_obj": {
        "es": "DIMR/DIMD  Clic en el círculo o arco a dimensionar:",
        "en": "DIMR/DIMD  Click on circle or arc to dimension:",
        "zh": "DIMR/DIMD  单击要标注的圆或圆弧:",
        "ru": "DIMR/DIMD  Щёлкните окружность или дугу для простановки размера:",
        "pt": "DIMR/DIMD  Clique no círculo ou arco a dimensionar:",
    },
    "op_dim_r_pt": {
        "es": "DIMR/DIMD  Especifique posición del texto de cota:  [X,Y]",
        "en": "DIMR/DIMD  Specify dimension text location:  [X,Y]",
        "zh": "DIMR/DIMD  指定尺寸文字位置:  [X,Y]",
        "ru": "DIMR/DIMD  Укажите положение текста размера:  [X,Y]",
        "pt": "DIMR/DIMD  Especifique a posição do texto de cota:  [X,Y]",
    },

    # ── PROMPTS DE COTA LONGITUD DE ARCO ─────────────────────────────

    "op_dim_arc_obj": {
        "es": "DAR  Clic sobre el arco a dimensionar:",
        "en": "DAR  Click on the arc to dimension:",
        "zh": "DAR  单击要标注长度的圆弧:",
        "ru": "DAR  Щёлкните дугу для простановки размера длины:",
        "pt": "DAR  Clique no arco a dimensionar:",
    },
    "op_dim_arc_pos": {
        "es": "DAR  Especifique posición del texto de cota:  [X,Y]",
        "en": "DAR  Specify dimension text location:  [X,Y]",
        "zh": "DAR  指定尺寸文字位置:  [X,Y]",
        "ru": "DAR  Укажите положение текста размера:  [X,Y]",
        "pt": "DAR  Especifique a posição do texto de cota:  [X,Y]",
    },

    # ── PROMPTS DE COTA ORDENADA ──────────────────────────────────────

    "op_dim_ord_p1": {
        "es": "DOR  Especifique punto a medir  (snap a vértice):  [X,Y]",
        "en": "DOR  Specify point to measure  (snap to vertex):  [X,Y]",
        "zh": "DOR  指定要标注的点  (捕捉至顶点):  [X,Y]",
        "ru": "DOR  Укажите точку для измерения  (привязка к вершине):  [X,Y]",
        "pt": "DOR  Especifique o ponto a medir  (snap a vértice):  [X,Y]",
    },
    "op_dim_ord_p2": {
        "es": "DOR  Especifique extremo del líder:  [X,Y]",
        "en": "DOR  Specify leader endpoint:  [X,Y]",
        "zh": "DOR  指定引线端点:  [X,Y]",
        "ru": "DOR  Укажите конец выноски:  [X,Y]",
        "pt": "DOR  Especifique o extremo do líder:  [X,Y]",
    },

    # ── PROMPTS DE COTA ANGULAR ───────────────────────────────────────

    "op_dim_ang_cen": {
        "es": "DIMANG  Especifique vértice del ángulo:  [X,Y]",
        "en": "DIMANG  Specify angle vertex:  [X,Y]",
        "zh": "DIMANG  指定角度顶点:  [X,Y]",
        "ru": "DIMANG  Укажите вершину угла:  [X,Y]",
        "pt": "DIMANG  Especifique o vértice do ângulo:  [X,Y]",
    },
    "op_dim_ang_p1": {
        "es": "DIMANG  Especifique primer punto del eje:  [X,Y]",
        "en": "DIMANG  Specify first axis point:  [X,Y]",
        "zh": "DIMANG  指定第一条角度线的端点:  [X,Y]",
        "ru": "DIMANG  Укажите первую точку оси:  [X,Y]",
        "pt": "DIMANG  Especifique o primeiro ponto do eixo:  [X,Y]",
    },
    "op_dim_ang_p2": {
        "es": "DIMANG  Especifique segundo punto del eje:  [X,Y]",
        "en": "DIMANG  Specify second axis point:  [X,Y]",
        "zh": "DIMANG  指定第二条角度线的端点:  [X,Y]",
        "ru": "DIMANG  Укажите вторую точку оси:  [X,Y]",
        "pt": "DIMANG  Especifique o segundo ponto do eixo:  [X,Y]",
    },

    # ── PROMPTS DE COTA CONTINUA / LÍNEA BASE ─────────────────────────

    "op_dim_chain": {
        "es": "DIMCHAIN  Especifique siguiente punto  (ESC = finalizar):  [X,Y]",
        "en": "DIMCHAIN  Specify next point  (ESC = finish):  [X,Y]",
        "zh": "DIMCHAIN  指定下一点  (ESC = 完成):  [X,Y]",
        "ru": "DIMCHAIN  Укажите следующую точку  (ESC = завершить):  [X,Y]",
        "pt": "DIMCHAIN  Especifique o próximo ponto  (ESC = finalizar):  [X,Y]",
    },

    # ── PROMPT DE ESPACIADO DE COTAS ─────────────────────────────────

    "op_dim_sp": {
        "es": "DSP  Clic en cota BASE — luego en destinos — Enter = aplicar:",
        "en": "DSP  Click BASE dimension — then targets — Enter = apply:",
        "zh": "DSP  单击基准标注 — 然后单击目标 — Enter = 应用:",
        "ru": "DSP  Щёлкните БАЗОВЫЙ размер — затем цели — Enter = применить:",
        "pt": "DSP  Clique na cota BASE — depois nos destinos — Enter = aplicar:",
    },

    # ── PROMPT DE HATCH ───────────────────────────────────────────────

    "op_hatch_pts": {
        "es": "HATCH  Clic en vértices del contorno — Enter = cerrar | ESC = cancelar:",
        "en": "HATCH  Click boundary vertices — Enter = close | ESC = cancel:",
        "zh": "HATCH  单击边界顶点 — Enter = 闭合 | ESC = 取消:",
        "ru": "HATCH  Щёлкните вершины контура — Enter = замкнуть | ESC = отмена:",
        "pt": "HATCH  Clique nos vértices do contorno — Enter = fechar | ESC = cancelar:",
    },

    # ── PROMPT DE INSERCIÓN DE BLOQUE ────────────────────────────────

    "op_insert_place": {
        "es": "INSERT  Clic para posicionar el bloque:  [X,Y]",
        "en": "INSERT  Click to place the block:  [X,Y]",
        "zh": "INSERT  单击以放置块:  [X,Y]",
        "ru": "INSERT  Щёлкните для размещения блока:  [X,Y]",
        "pt": "INSERT  Clique para posicionar o bloco:  [X,Y]",
    },

    # ── PROMPT DE DEFINICIÓN DE BLOQUE ───────────────────────────────

    "op_block_name": {
        "es": "BLOCK  Escriba el nombre del bloque y presione Enter:",
        "en": "BLOCK  Type the block name and press Enter:",
        "zh": "BLOCK  输入块名称并按 Enter:",
        "ru": "BLOCK  Введите имя блока и нажмите Enter:",
        "pt": "BLOCK  Digite o nome do bloco e pressione Enter:",
    },

    # ── PROMPT DE CAPA ACTIVA ─────────────────────────────────────────

    "op_laymcur": {
        "es": "LAYMCUR  Clic en entidad para hacer su capa activa:",
        "en": "LAYMCUR  Click entity to set its layer as current:",
        "zh": "LAYMCUR  单击图元以将其图层设为当前层:",
        "ru": "LAYMCUR  Щёлкните объект, чтобы сделать его слой текущим:",
        "pt": "LAYMCUR  Clique na entidade para ativar a sua camada:",
    },

    # ── PROMPTS RESTANTES (capas, herramientas especiales) ───────────

    "op_layiso_pick": {
        "es": "LAYISO  Clic en entidad para aislar su capa  (ESC = cancelar):",
        "en": "LAYISO  Click entity to isolate its layer  (ESC = cancel):",
        "zh": "LAYISO  单击图元以隔离其图层  (ESC = 取消):",
        "ru": "LAYISO  Щёлкните объект для изоляции его слоя  (ESC = отмена):",
        "pt": "LAYISO  Clique na entidade para isolar a sua camada  (ESC = cancelar):",
    },
    "op_id_pick": {
        "es": "ID  Clic en punto para mostrar coordenadas:  [X,Y]",
        "en": "ID  Click point to display coordinates:  [X,Y]",
        "zh": "ID  单击点以显示坐标:  [X,Y]",
        "ru": "ID  Щёлкните точку для отображения координат:  [X,Y]",
        "pt": "ID  Clique no ponto para mostrar coordenadas:  [X,Y]",
    },
    "op_polygon_sides": {
        "es": "POLÍGONO  Especifique número de lados — escribe y Enter (o Enter = mantener):",
        "en": "POLYGON  Specify number of sides — type and Enter (or Enter = keep):",
        "zh": "POLYGON  指定边数 — 输入并按 Enter (或 Enter = 保持):",
        "ru": "POLYGON  Укажите количество сторон — введите и Enter (или Enter = сохранить):",
        "pt": "POLYGON  Especifique o número de lados — escreva e Enter (ou Enter = manter):",
    },
    "op_pan_mode": {
        "es": "PAN  Clic y arrastra para desplazar la vista  ·  ESC para salir:",
        "en": "PAN  Click and drag to pan the view  ·  ESC to exit:",
        "zh": "PAN  单击并拖动以平移视图  ·  ESC 退出:",
        "ru": "PAN  Нажмите и перетащите для панорамирования  ·  ESC для выхода:",
        "pt": "PAN  Clique e arraste para deslocar a vista  ·  ESC para sair:",
    },
    "op_image_place": {
        "es": "IMG  Clic en punto de origen de la imagen  (esquina inferior-izquierda):",
        "en": "IMG  Click image origin point  (lower-left corner):",
        "zh": "IMG  单击图像原点  (左下角):",
        "ru": "IMG  Щёлкните точку начала изображения  (нижний левый угол):",
        "pt": "IMG  Clique no ponto de origem da imagem  (canto inferior-esquerdo):",
    },
    "op_image_width": {
        "es": "IMG  Especifique ancho en metros — escribe y Enter:",
        "en": "IMG  Specify width in meters — type and Enter:",
        "zh": "IMG  指定宽度（米）— 输入并按 Enter:",
        "ru": "IMG  Укажите ширину в метрах — введите и Enter:",
        "pt": "IMG  Especifique a largura em metros — escreva e Enter:",
    },
    "op_eattedit_pick": {
        "es": "EATTEDIT  Clic en bloque con atributos para editar:",
        "en": "EATTEDIT  Click block with attributes to edit:",
        "zh": "EATTEDIT  单击包含属性的块进行编辑:",
        "ru": "EATTEDIT  Щёлкните блок с атрибутами для редактирования:",
        "pt": "EATTEDIT  Clique no bloco com atributos para editar:",
    },

    # ── HINT MODO IA ─────────────────────────────────────────────────

    "ia_hint": {
        "es": "IA activa — escribe tu consulta en lenguaje natural",
        "en": "AI active — type your query in natural language",
        "zh": "AI 已激活 — 用自然语言输入你的查询",
        "ru": "ИИ активен — введите запрос на естественном языке",
        "pt": "IA ativa — escreva sua consulta em linguagem natural",
    },

    # ── BOTONES DE HERRAMIENTA ───────────────────────────────────────

    "tool_select": {
        "es": "Seleccionar", "en": "Select",
        "zh": "选择", "ru": "Выбор", "pt": "Selecionar",
    },
    "tool_line": {
        "es": "Línea", "en": "Line",
        "zh": "直线", "ru": "Отрезок", "pt": "Linha",
    },
    "tool_polyline": {
        "es": "Polilínea", "en": "Polyline",
        "zh": "多段线", "ru": "Полилиния", "pt": "Polilinha",
    },
    "tool_rect": {
        "es": "Rectángulo", "en": "Rectangle",
        "zh": "矩形", "ru": "Прямоугольник", "pt": "Retângulo",
    },
    "tool_circle": {
        "es": "Círculo", "en": "Circle",
        "zh": "圆", "ru": "Окружность", "pt": "Círculo",
    },
    "tool_arc": {
        "es": "Arco", "en": "Arc",
        "zh": "圆弧", "ru": "Дуга", "pt": "Arco",
    },
    "tool_text": {
        "es": "Texto", "en": "Text",
        "zh": "文字", "ru": "Текст", "pt": "Texto",
    },

    # ── BOTONES DE OPERACIÓN ─────────────────────────────────────────

    "op_move": {
        "es": "Mover", "en": "Move",
        "zh": "移动", "ru": "Перенести", "pt": "Mover",
    },
    "op_copy": {
        "es": "Copiar", "en": "Copy",
        "zh": "复制", "ru": "Копировать", "pt": "Copiar",
    },
    "op_rotate": {
        "es": "Rotar", "en": "Rotate",
        "zh": "旋转", "ru": "Повернуть", "pt": "Girar",
    },
    "op_scale": {
        "es": "Escala", "en": "Scale",
        "zh": "缩放", "ru": "Масштаб", "pt": "Escala",
    },
    "op_mirror": {
        "es": "Espejo", "en": "Mirror",
        "zh": "镜像", "ru": "Зеркало", "pt": "Espelho",
    },
    "op_offset": {
        "es": "Desfase", "en": "Offset",
        "zh": "偏移", "ru": "Смещение", "pt": "Desvio",
    },
    "op_trim": {
        "es": "Recortar", "en": "Trim",
        "zh": "修剪", "ru": "Обрезать", "pt": "Aparar",
    },
    "op_extend": {
        "es": "Extender", "en": "Extend",
        "zh": "延伸", "ru": "Удлинить", "pt": "Estender",
    },
    "op_erase": {
        "es": "Borrar", "en": "Erase",
        "zh": "删除", "ru": "Удалить", "pt": "Apagar",
    },
    "op_dist": {
        "es": "Distancia", "en": "Distance",
        "zh": "距离", "ru": "Расстояние", "pt": "Distância",
    },
    "op_area": {
        "es": "Área", "en": "Area",
        "zh": "面积", "ru": "Площадь", "pt": "Área",
    },

    # ── UI GENERAL ───────────────────────────────────────────────────

    "btn_snap":  {"es": "SNAP",  "en": "SNAP",  "zh": "捕捉", "ru": "ПРИВЯЗ", "pt": "SNAP"},
    "btn_grid":  {"es": "GRID",  "en": "GRID",  "zh": "栅格", "ru": "СЕТКА",  "pt": "GRID"},
    "btn_ortho": {"es": "ORTHO", "en": "ORTHO", "zh": "正交", "ru": "ОРТО",   "pt": "ORTHO"},

    "menu_file":   {"es": "Archivo",    "en": "File",    "zh": "文件", "ru": "Файл",    "pt": "Arquivo"},
    "menu_edit":   {"es": "Editar",     "en": "Edit",    "zh": "编辑", "ru": "Правка",  "pt": "Editar"},
    "menu_draw":   {"es": "Dibujar",    "en": "Draw",    "zh": "绘图", "ru": "Чертить", "pt": "Desenhar"},
    "menu_modify": {"es": "Modificar",  "en": "Modify",  "zh": "修改", "ru": "Изменить","pt": "Modificar"},
    "menu_view":   {"es": "Ver",        "en": "View",    "zh": "视图", "ru": "Вид",     "pt": "Ver"},
    "menu_layers": {"es": "Capas",      "en": "Layers",  "zh": "图层", "ru": "Слои",    "pt": "Camadas"},
    "menu_measure":{"es": "Medir",      "en": "Measure", "zh": "测量", "ru": "Измерить","pt": "Medir"},
    "menu_ia":     {"es": "Asistente IA","en": "AI Assistant","zh": "AI助手","ru": "ИИ помощник","pt": "Assistente IA"},
    "menu_config": {"es": "Configuración","en": "Settings","zh": "设置","ru": "Настройки","pt": "Configurações"},

    "cfg_cursor":  {"es": "CURSOR",     "en": "CURSOR",  "zh": "光标", "ru": "КУРСОР",  "pt": "CURSOR"},
    "cfg_snap":    {"es": "SNAPS",      "en": "SNAPS",   "zh": "捕捉", "ru": "ПРИВЯЗКИ","pt": "SNAPS"},
    "cfg_ia":      {"es": "ASISTENTE IA","en": "AI ASSISTANT","zh": "AI 助手","ru": "ИИ ПОМОЩНИК","pt": "ASSISTENTE IA"},
    "cfg_lang":    {"es": "IDIOMA",     "en": "LANGUAGE","zh": "语言", "ru": "ЯЗЫК",    "pt": "IDIOMA"},
    "cfg_cursor_size": {
        "es": "Tamaño brazos (CURSORSIZE):",
        "en": "Crosshair size (CURSORSIZE):",
        "zh": "十字光标大小 (CURSORSIZE):",
        "ru": "Размер перекрестья (CURSORSIZE):",
        "pt": "Tamanho do cursor (CURSORSIZE):",
    },
    "cfg_cursor_hint": {
        "es": "5% = corto moderno  ·  30% = equilibrado  ·  100% = AutoCAD clásico",
        "en": "5% = short modern  ·  30% = balanced  ·  100% = classic AutoCAD",
        "zh": "5% = 现代短式  ·  30% = 均衡  ·  100% = 经典AutoCAD",
        "ru": "5% = короткий  ·  30% = сбалансированный  ·  100% = классический AutoCAD",
        "pt": "5% = curto moderno  ·  30% = equilibrado  ·  100% = AutoCAD clássico",
    },
    "cfg_lang_hint": {
        "es": "El idioma se aplica al reiniciar la aplicación.",
        "en": "Language applies after restarting the application.",
        "zh": "语言设置在重启应用后生效。",
        "ru": "Язык применяется после перезапуска приложения.",
        "pt": "O idioma é aplicado ao reiniciar o aplicativo.",
    },
    "cfg_snap_global": {
        "es": "SNAP global activado",
        "en": "Global SNAP enabled",
        "zh": "全局捕捉已启用",
        "ru": "Глобальная привязка включена",
        "pt": "SNAP global ativado",
    },

    # ── MENSAJES DE ERROR / ECHO ─────────────────────────────────────

    "err_select_first": {
        "es": "!! Seleccione entidades primero",
        "en": "!! Select entities first",
        "zh": "!! 请先选择图元",
        "ru": "!! Сначала выберите объекты",
        "pt": "!! Selecione entidades primeiro",
    },
    "err_unknown_cmd": {
        "es": "!! Comando desconocido  (F1 para ayuda)",
        "en": "!! Unknown command  (F1 for help)",
        "zh": "!! 未知命令  (F1 查看帮助)",
        "ru": "!! Неизвестная команда  (F1 — справка)",
        "pt": "!! Comando desconhecido  (F1 para ajuda)",
    },
    "err_invalid_coord": {
        "es": "!! Coordenada inválida  (ej: 1.5,3.0  @1,0  @2<45)",
        "en": "!! Invalid coordinate  (e.g. 1.5,3.0  @1,0  @2<45)",
        "zh": "!! 坐标无效  (例: 1.5,3.0  @1,0  @2<45)",
        "ru": "!! Неверная координата  (напр.: 1.5,3.0  @1,0  @2<45)",
        "pt": "!! Coordenada inválida  (ex: 1.5,3.0  @1,0  @2<45)",
    },

    # ── PROMPTS NUEVAS HERRAMIENTAS ──────────────────────────────────────

    "ellipse_p0": {
        "es": "ELLIPSE  Especifique centro de la elipse:  [X,Y]",
        "en": "ELLIPSE  Specify ellipse center:  [X,Y]",
        "zh": "ELLIPSE  指定椭圆中心:  [X,Y]",
        "ru": "ELLIPSE  Укажите центр эллипса:  [X,Y]",
        "pt": "ELLIPSE  Especifique o centro da elipse:  [X,Y]",
    },
    "ellipse_p1": {
        "es": "ELLIPSE  Especifique extremo del eje mayor:  [X,Y]",
        "en": "ELLIPSE  Specify end of major axis:  [X,Y]",
        "zh": "ELLIPSE  指定长轴端点:  [X,Y]",
        "ru": "ELLIPSE  Укажите конец большой оси:  [X,Y]",
        "pt": "ELLIPSE  Especifique o extremo do eixo maior:  [X,Y]",
    },
    "ellipse_p2": {
        "es": "ELLIPSE  Especifique radio menor (distancia al eje menor):  [valor]",
        "en": "ELLIPSE  Specify minor radius (distance to minor axis):  [value]",
        "zh": "ELLIPSE  指定短半轴长度:  [值]",
        "ru": "ELLIPSE  Укажите малый радиус:  [значение]",
        "pt": "ELLIPSE  Especifique o raio menor:  [valor]",
    },
    "polygon_p0": {
        "es": "POLYGON  Especifique centro  (escribe número de lados, por defecto 6):  [X,Y]",
        "en": "POLYGON  Specify center  (type number of sides, default 6):  [X,Y]",
        "zh": "POLYGON  指定中心  (输入边数，默认6):  [X,Y]",
        "ru": "POLYGON  Укажите центр  (введите число сторон, по умолчанию 6):  [X,Y]",
        "pt": "POLYGON  Especifique o centro  (escreva número de lados, padrão 6):  [X,Y]",
    },
    "polygon_p1": {
        "es": "POLYGON  Especifique vértice/radio:  [X,Y]",
        "en": "POLYGON  Specify vertex/radius point:  [X,Y]",
        "zh": "POLYGON  指定顶点/半径点:  [X,Y]",
        "ru": "POLYGON  Укажите вершину/радиус:  [X,Y]",
        "pt": "POLYGON  Especifique o vértice/raio:  [X,Y]",
    },
    "xline_p0": {
        "es": "XLINE  Especifique punto de referencia:  [X,Y]",
        "en": "XLINE  Specify reference point:  [X,Y]",
        "zh": "XLINE  指定参考点:  [X,Y]",
        "ru": "XLINE  Укажите опорную точку:  [X,Y]",
        "pt": "XLINE  Especifique o ponto de referência:  [X,Y]",
    },
    "xline_p1": {
        "es": "XLINE  Especifique punto de dirección:  [X,Y]",
        "en": "XLINE  Specify direction point:  [X,Y]",
        "zh": "XLINE  指定方向点:  [X,Y]",
        "ru": "XLINE  Укажите точку направления:  [X,Y]",
        "pt": "XLINE  Especifique o ponto de direção:  [X,Y]",
    },
    "cloud_p0": {
        "es": "CLOUD  Especifique primer vértice del contorno:  [X,Y]",
        "en": "CLOUD  Specify first boundary vertex:  [X,Y]",
        "zh": "CLOUD  指定第一个边界点:  [X,Y]",
        "ru": "CLOUD  Укажите первую вершину контура:  [X,Y]",
        "pt": "CLOUD  Especifique o primeiro vértice do contorno:  [X,Y]",
    },
    "cloud_p1": {
        "es": "CLOUD  Especifique siguiente vértice  (Enter=cerrar):  [X,Y]",
        "en": "CLOUD  Specify next vertex  (Enter=close):  [X,Y]",
        "zh": "CLOUD  指定下一顶点  (Enter=闭合):  [X,Y]",
        "ru": "CLOUD  Укажите следующую вершину  (Enter=замкнуть):  [X,Y]",
        "pt": "CLOUD  Especifique o próximo vértice  (Enter=fechar):  [X,Y]",
    },
    "leader_p0": {
        "es": "LEADER  Especifique primer punto (punta de flecha):  [X,Y]",
        "en": "LEADER  Specify first point (arrow tip):  [X,Y]",
        "zh": "LEADER  指定第一点（箭头尖端）:  [X,Y]",
        "ru": "LEADER  Укажите первую точку (кончик стрелки):  [X,Y]",
        "pt": "LEADER  Especifique o primeiro ponto (ponta da seta):  [X,Y]",
    },
    "leader_p1": {
        "es": "LEADER  Especifique siguiente punto del segmento  (Enter=finalizar):  [X,Y]",
        "en": "LEADER  Specify next segment point  (Enter=finish):  [X,Y]",
        "zh": "LEADER  指定下一段点  (Enter=完成):  [X,Y]",
        "ru": "LEADER  Укажите следующую точку сегмента  (Enter=завершить):  [X,Y]",
        "pt": "LEADER  Especifique o próximo ponto do segmento  (Enter=finalizar):  [X,Y]",
    },
    "leader_p2": {
        "es": "LEADER  Especifique siguiente punto  (Enter=finalizar):",
        "en": "LEADER  Specify next point  (Enter=finish):",
        "zh": "LEADER  指定下一点  (Enter=完成):",
        "ru": "LEADER  Укажите следующую точку  (Enter=завершить):",
        "pt": "LEADER  Especifique o próximo ponto  (Enter=finalizar):",
    },

    # ── PROMPTS DE NUEVAS OPERACIONES ────────────────────────────────────

    "op_array": {
        "es": "ARRAY  Seleccione entidades primero, luego [AR]",
        "en": "ARRAY  Select entities first, then [AR]",
        "zh": "ARRAY  请先选择图元，然后 [AR]",
        "ru": "ARRAY  Сначала выберите объекты, затем [AR]",
        "pt": "ARRAY  Selecione entidades primeiro, depois [AR]",
    },
    "op_chamfer_d": {
        "es": "CHAMFER  Especifique distancia de chaflán — escribe valor y Enter (o Enter = mantener):",
        "en": "CHAMFER  Specify chamfer distance — type value and Enter (or Enter = keep):",
        "zh": "CHAMFER  指定倒角距离 — 输入值并按 Enter (或 Enter = 保持):",
        "ru": "CHAMFER  Укажите расстояние фаски — введите значение и Enter (или Enter = сохранить):",
        "pt": "CHAMFER  Especifique a distância do chanfro — escreva valor e Enter (ou Enter = manter):",
    },
    "op_chamfer_p1": {
        "es": "CHAMFER  Clic en 1ª línea  (escribe número para cambiar distancia):",
        "en": "CHAMFER  Click 1st line  (type number to change distance):",
        "zh": "CHAMFER  单击第一条线  (输入数字更改距离):",
        "ru": "CHAMFER  Щёлкните 1-ю линию  (введите число для изменения расстояния):",
        "pt": "CHAMFER  Clique na 1ª linha  (digite número para alterar distância):",
    },
    "op_chamfer_p2": {
        "es": "CHAMFER  Clic en 2ª línea:",
        "en": "CHAMFER  Click 2nd line:",
        "zh": "CHAMFER  单击第二条线:",
        "ru": "CHAMFER  Щёлкните 2-ю линию:",
        "pt": "CHAMFER  Clique na 2ª linha:",
    },
    "op_break_p1": {
        "es": "BREAK  Clic en entidad en el punto de ruptura:",
        "en": "BREAK  Click entity at the break point:",
        "zh": "BREAK  在断点处单击图元:",
        "ru": "BREAK  Щёлкните объект в точке разрыва:",
        "pt": "BREAK  Clique na entidade no ponto de ruptura:",
    },
    "op_break_p2": {
        "es": "BREAK  Especifique 2º punto de ruptura  (Enter = mismo punto):",
        "en": "BREAK  Specify 2nd break point  (Enter = same point):",
        "zh": "BREAK  指定第二个断点  (Enter = 同一点):",
        "ru": "BREAK  Укажите 2-ю точку разрыва  (Enter = та же точка):",
        "pt": "BREAK  Especifique o 2º ponto de ruptura  (Enter = mesmo ponto):",
    },
    "op_align_sp1": {
        "es": "ALIGN  Especifique punto fuente 1:  [X,Y]",
        "en": "ALIGN  Specify source point 1:  [X,Y]",
        "zh": "ALIGN  指定源点 1:  [X,Y]",
        "ru": "ALIGN  Укажите исходную точку 1:  [X,Y]",
        "pt": "ALIGN  Especifique o ponto fonte 1:  [X,Y]",
    },
    "op_align_sp2": {
        "es": "ALIGN  Especifique punto fuente 2:  [X,Y]",
        "en": "ALIGN  Specify source point 2:  [X,Y]",
        "zh": "ALIGN  指定源点 2:  [X,Y]",
        "ru": "ALIGN  Укажите исходную точку 2:  [X,Y]",
        "pt": "ALIGN  Especifique o ponto fonte 2:  [X,Y]",
    },
    "op_align_dp1": {
        "es": "ALIGN  Especifique punto destino 1:  [X,Y]",
        "en": "ALIGN  Specify destination point 1:  [X,Y]",
        "zh": "ALIGN  指定目标点 1:  [X,Y]",
        "ru": "ALIGN  Укажите точку назначения 1:  [X,Y]",
        "pt": "ALIGN  Especifique o ponto destino 1:  [X,Y]",
    },
    "op_align_dp2": {
        "es": "ALIGN  Especifique punto destino 2:  [X,Y]",
        "en": "ALIGN  Specify destination point 2:  [X,Y]",
        "zh": "ALIGN  指定目标点 2:  [X,Y]",
        "ru": "ALIGN  Укажите точку назначения 2:  [X,Y]",
        "pt": "ALIGN  Especifique o ponto destino 2:  [X,Y]",
    },
    "op_measure_p1": {
        "es": "MEASURE  Especifique primer punto:  [X,Y]",
        "en": "MEASURE  Specify first point:  [X,Y]",
        "zh": "MEASURE  指定第一点:  [X,Y]",
        "ru": "MEASURE  Укажите первую точку:  [X,Y]",
        "pt": "MEASURE  Especifique o primeiro ponto:  [X,Y]",
    },
    "op_measure_next": {
        "es": "MEASURE  Siguiente punto  (ESC=total):  [X,Y]",
        "en": "MEASURE  Next point  (ESC=total):  [X,Y]",
        "zh": "MEASURE  下一点  (ESC=总计):  [X,Y]",
        "ru": "MEASURE  Следующая точка  (ESC=итог):  [X,Y]",
        "pt": "MEASURE  Próximo ponto  (ESC=total):  [X,Y]",
    },
}

# ── Mapa de claves de prompt por (tool, n_pts) ───────────────────────
TOOL_PROMPT_KEYS: dict[tuple, str] = {
    ("line",     0): "line_p0",
    ("line",     1): "line_p1",
    ("line",     2): "line_p2",
    ("polyline", 0): "pline_p0",
    ("polyline", 1): "pline_p1",
    ("polyline", 2): "pline_p2",
    ("spline",   0): "spline_p0",
    ("spline",   1): "spline_p1",
    ("spline",   2): "spline_p2",
    ("rect",     0): "rect_p0",
    ("rect",     1): "rect_p1",
    ("circle",   0): "circle_p0",
    ("circle",   1): "circle_p1",
    ("arc",      0): "arc_p0",
    ("arc",      1): "arc_p1",
    ("arc",      2): "arc_p2",
    ("text",     0): "text_p0",
    ("text",     1): "text_p1",
    ("select",   0): "select_p0",
    # Nuevas herramientas
    ("ellipse",  0): "ellipse_p0",
    ("ellipse",  1): "ellipse_p1",
    ("ellipse",  2): "ellipse_p2",
    ("polygon",  0): "polygon_p0",
    ("polygon",  1): "polygon_p1",
    ("xline",    0): "xline_p0",
    ("xline",    1): "xline_p1",
    ("cloud",    0): "cloud_p0",
    ("cloud",    1): "cloud_p1",
    ("cloud",    2): "cloud_p1",
    ("leader",   0): "leader_p0",
    ("leader",   1): "leader_p1",
    ("leader",   2): "leader_p2",
}

OP_PROMPT_KEYS: dict[str, str] = {
    # ── Mover ────────────────────────────────────────────────────────
    "move_sel":      "op_sel_move",
    "move_base":     "op_move_base",
    "move_dest":     "op_move_dest",

    # ── Copiar ───────────────────────────────────────────────────────
    "copy_sel":      "op_sel_copy",
    "copy_base":     "op_copy_base",
    "copy_dest":     "op_copy_dest",

    # ── Rotar ────────────────────────────────────────────────────────
    "rotate_sel":    "op_sel_rotate",
    "rotate_base":   "op_rotate_base",
    "rotate_angle":  "op_rotate_angle",

    # ── Escalar ──────────────────────────────────────────────────────
    "scale_sel":     "op_sel_scale",
    "scale_base":    "op_scale_base",
    "scale_factor":  "op_scale_factor",

    # ── Espejo ───────────────────────────────────────────────────────
    "mirror_sel":    "op_sel_mirror",
    "mirror_p1":     "op_mirror_p1",
    "mirror_p2":     "op_mirror_p2",
    "mirror_keep":   "op_mirror_keep",

    # ── Offset ───────────────────────────────────────────────────────
    "offset_dist":   "op_offset_dist",
    "offset_sel":    "op_offset_sel",
    "offset_side":   "op_offset_side",

    # ── Recortar / Extender / Fillet / Chamfer ───────────────────────
    "trim_obj":      "op_trim_obj",
    "extend_obj":    "op_extend_obj",
    "fillet_r":      "op_fillet_r",
    "fillet_p1":     "op_fillet_p1",
    "fillet_p2":     "op_fillet_p2",
    "chamfer_d":     "op_chamfer_d",

    # ── Distancia / Zoom / Matchprop ─────────────────────────────────
    "dist_p1":       "op_dist_p1",
    "dist_p2":       "op_dist_p2",
    "zoom_w1":       "op_zoom_w1",
    "zoom_w2":       "op_zoom_w2",
    "matchprop_src": "op_matchprop_src",
    "matchprop_dst": "op_matchprop_dst",

    # ── Cotas lineales ───────────────────────────────────────────────
    "dim_lp1":       "op_dim_lp1",
    "dim_lp2":       "op_dim_lp2",
    "dim_lpos":      "op_dim_lpos",

    # ── Cotas radio / diámetro ───────────────────────────────────────
    "dim_r_obj":     "op_dim_r_obj",
    "dim_r_pt":      "op_dim_r_pt",

    # ── Cota longitud de arco ────────────────────────────────────────
    "dim_arc_obj":   "op_dim_arc_obj",
    "dim_arc_pos":   "op_dim_arc_pos",

    # ── Cota ordenada ────────────────────────────────────────────────
    "dim_ord_p1":    "op_dim_ord_p1",
    "dim_ord_p2":    "op_dim_ord_p2",

    # ── Cota angular ─────────────────────────────────────────────────
    "dim_ang_cen":   "op_dim_ang_cen",
    "dim_ang_p1":    "op_dim_ang_p1",
    "dim_ang_p2":    "op_dim_ang_p2",

    # ── Cota continua / línea base / espaciado ────────────────────────
    "dim_chain_next":"op_dim_chain",
    "dim_sp_pick":   "op_dim_sp",

    # ── Hatch ────────────────────────────────────────────────────────
    "hatch_pts":     "op_hatch_pts",

    # ── Bloques ──────────────────────────────────────────────────────
    "insert_place":  "op_insert_place",
    "block_name":    "op_block_name",

    # ── Capas ────────────────────────────────────────────────────────
    "laymcur":       "op_laymcur",

    # ── Nuevas operaciones ───────────────────────────────────────────
    "chamfer_p1":    "op_chamfer_p1",
    "chamfer_p2":    "op_chamfer_p2",
    "break_p1":      "op_break_p1",
    "break_p2":      "op_break_p2",
    "align_sel":     "op_sel_align",
    "align_sp1":     "op_align_sp1",
    "align_sp2":     "op_align_sp2",
    "align_dp1":     "op_align_dp1",
    "align_dp2":     "op_align_dp2",
    "measure_p1":    "op_measure_p1",
    "measure_next":  "op_measure_next",

    # ── Array ────────────────────────────────────────────────────────
    "array_sel":     "op_sel_array",
    "array_type":    "op_array_type",
    "array_pol_ctr": "op_array_pol_ctr",
    "array_pol_n":   "op_array_pol_n",

    # ── Herramientas especiales ───────────────────────────────────────
    "layiso_pick":   "op_layiso_pick",
    "id_pick":       "op_id_pick",
    "polygon_sides": "op_polygon_sides",
    "pan_mode":      "op_pan_mode",
    "image_place":   "op_image_place",
    "image_width":   "op_image_width",
    "eattedit_pick": "op_eattedit_pick",
}
