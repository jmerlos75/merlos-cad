MIN_AREAS = {
    "dormitorio_principal": 9.0,
    "dormitorio": 7.5,
    "cocina": 5.0,
    "sala": 10.0,
    "sala_comedor": 10.0,
    "comedor": 7.0,
    "bano": 2.5,
    "cochera": 12.0,
    "lavanderia": 3.0,
    "estudio": 6.0,
}

MIN_DIMENSIONES = {
    "pasillo": 1.20,
    "bano": 1.50,
    "cocina": 2.10,
    "dormitorio": 2.40,
}

ADYACENCIAS_RECOMENDADAS = {
    "bano": ["dormitorio", "dormitorio_principal", "pasillo"],
    "cocina": ["comedor", "sala_comedor", "pasillo"],
    "comedor": ["sala", "cocina", "sala_comedor"],
    "cochera": ["sala", "comedor", "pasillo", "exterior"],
    "dormitorio": ["pasillo"],
    "dormitorio_principal": ["pasillo"],
    "lavanderia": ["cocina", "pasillo", "exterior"],
}

DEBE_TOCAR_BORDE = ["cochera"]


def detectar_tipo(nombre: str) -> str:
    n = nombre.lower().replace(" ", "_").replace("-", "_")
    if "principal" in n or "master" in n:
        if "dormitorio" in n or "cuarto" in n or "habitacion" in n:
            return "dormitorio_principal"
    if "dormitorio" in n or "cuarto" in n or "habitacion" in n or "recamara" in n:
        return "dormitorio"
    if "cocina" in n:
        return "cocina"
    if "sala_comedor" in n or "salacomedor" in n:
        return "sala_comedor"
    if "sala" in n or "living" in n:
        return "sala"
    if "comedor" in n:
        return "comedor"
    if "bano" in n or "baño" in n or "wc" in n or "servicio_sanitario" in n:
        return "bano"
    if "pasillo" in n or "circulacion" in n or "hall" in n:
        return "pasillo"
    if "cochera" in n or "garage" in n or "garaje" in n or "parqueo" in n:
        return "cochera"
    if "lavanderia" in n or "lavado" in n or "pila" in n:
        return "lavanderia"
    if "estudio" in n or "oficina" in n:
        return "estudio"
    if "terraza" in n or "balcon" in n or "patio" in n:
        return "exterior"
    return "otro"


def _calcular_score_distribución(grid, ady: dict) -> int:
    """Score 0-100 de qué tan bueno es el layout. Consideras:
    - Uso de espacio (min 75%)
    - Adyacencias correctas
    - Dimensiones dentro de rango
    - Circulación clara (pasillo conectado)
    """
    score = 100
    vacias = grid.celdas_vacias()
    total = grid.rows * grid.cols
    pct_uso = 100 - (vacias / total * 100 if total > 0 else 0)

    if pct_uso < 75:
        score -= int((75 - pct_uso) * 0.5)

    adyacencias_ok = 0
    adyacencias_total = 0
    for nombre, datos in grid.recintos.items():
        tipo = detectar_tipo(nombre)
        if tipo in ADYACENCIAS_RECOMENDADAS:
            adyacencias_total += 1
            vecinos = ady.get(nombre, [])
            tipos_vecinos = [detectar_tipo(v) for v in vecinos]
            requeridos = ADYACENCIAS_RECOMENDADAS[tipo]
            cumple = any(req in tipos_vecinos for req in requeridos)
            if "exterior" in requeridos and grid.bordes_que_toca(nombre):
                cumple = True
            if cumple:
                adyacencias_ok += 1

    if adyacencias_total > 0:
        pct_ady = (adyacencias_ok / adyacencias_total) * 100
        if pct_ady < 100:
            score -= int((100 - pct_ady) * 0.3)

    proporciones_ok = 0
    proporciones_total = 0
    for nombre, datos in grid.recintos.items():
        tipo = detectar_tipo(nombre)
        if tipo != "pasillo":
            proporciones_total += 1
            ratio = max(datos["ancho_m"], datos["alto_m"]) / max(min(datos["ancho_m"], datos["alto_m"]), 0.01)
            if ratio <= 3.5:
                proporciones_ok += 1

    if proporciones_total > 0:
        pct_prop = (proporciones_ok / proporciones_total) * 100
        if pct_prop < 100:
            score -= int((100 - pct_prop) * 0.2)

    return max(0, min(100, score))


def _sugerir_reorganizacion(grid, ady: dict) -> list:
    """Genera sugerencias específicas de reorganización."""
    sugerencias = []
    vacias = grid.celdas_vacias()
    total = grid.rows * grid.cols
    pct_vacio = (vacias / total) * 100 if total > 0 else 0

    if pct_vacio > 15:
        sugerencias.append(
            f"💡 Hay {vacias} celdas vacías ({pct_vacio:.0f}%). Intenta: agrandar COCINA o SALA, "
            f"o mover recintos para compactar el layout."
        )

    for nombre, datos in grid.recintos.items():
        tipo = detectar_tipo(nombre)

        if tipo in MIN_AREAS:
            min_req = MIN_AREAS[tipo]
            if datos["area_m2"] < min_req * 1.2:
                deficit = (min_req * 1.2) - datos["area_m2"]
                sugerencias.append(
                    f"💡 {nombre} está algo pequeño (solo {deficit:.1f}m² más y sería cómodo). "
                    f"Considera mover a posición más central."
                )

        if tipo in ("dormitorio", "dormitorio_principal"):
            vecinos = ady.get(nombre, [])
            tipos_vecinos = [detectar_tipo(v) for v in vecinos]
            if "pasillo" not in tipos_vecinos:
                sugerencias.append(
                    f"💡 {nombre} no tiene pasillo directo. Mejor: muévelo adyacente al pasillo "
                    f"para mejor circulación."
                )

    return sugerencias[:3]


def validar(grid) -> dict:
    errores = []
    advertencias = []
    info = []
    sugerencias = []

    ady = grid.adyacencias()

    # 1. Areas minimas
    for nombre, datos in grid.recintos.items():
        tipo = detectar_tipo(nombre)
        if tipo in MIN_AREAS:
            min_req = MIN_AREAS[tipo]
            if datos["area_m2"] < min_req:
                errores.append(
                    f"{nombre}: area {datos['area_m2']:.1f}m2 < minimo {min_req}m2 ({tipo})"
                )

    # 2. Dimensiones minimas
    for nombre, datos in grid.recintos.items():
        tipo = detectar_tipo(nombre)
        if tipo in MIN_DIMENSIONES:
            dim_min = MIN_DIMENSIONES[tipo]
            menor = min(datos["ancho_m"], datos["alto_m"])
            if menor < dim_min:
                errores.append(
                    f"{nombre}: lado menor {menor:.1f}m < {dim_min}m minimo para {tipo}"
                )

    # 3. Adyacencias recomendadas
    for nombre, datos in grid.recintos.items():
        tipo = detectar_tipo(nombre)
        if tipo in ADYACENCIAS_RECOMENDADAS:
            vecinos = ady.get(nombre, [])
            tipos_vecinos = [detectar_tipo(v) for v in vecinos]
            requeridos = ADYACENCIAS_RECOMENDADAS[tipo]
            cumple = any(req in tipos_vecinos for req in requeridos)
            if "exterior" in requeridos and grid.bordes_que_toca(nombre):
                cumple = True
            if not cumple:
                advertencias.append(
                    f"{nombre} ({tipo}) deberia estar junto a: {', '.join(requeridos)}. "
                    f"Vecinos actuales: {vecinos or 'ninguno'}"
                )

    # 4. Recintos que deben tocar borde (calle/exterior)
    for nombre, datos in grid.recintos.items():
        tipo = detectar_tipo(nombre)
        if tipo in DEBE_TOCAR_BORDE:
            if not grid.bordes_que_toca(nombre):
                errores.append(
                    f"{nombre} ({tipo}) debe tocar un borde del terreno (calle/exterior)"
                )

    # 5. Detectar dormitorios sin acceso a pasillo
    for nombre, datos in grid.recintos.items():
        tipo = detectar_tipo(nombre)
        if tipo in ("dormitorio", "dormitorio_principal"):
            vecinos = ady.get(nombre, [])
            tipos_vecinos = [detectar_tipo(v) for v in vecinos]
            if "pasillo" not in tipos_vecinos and "sala" not in tipos_vecinos and "sala_comedor" not in tipos_vecinos:
                advertencias.append(
                    f"{nombre}: no tiene acceso por pasillo o sala. Posible problema de circulacion"
                )

    # 6. Espacios desperdiciados
    vacias = grid.celdas_vacias()
    total = grid.rows * grid.cols
    pct_vacio = (vacias / total) * 100 if total > 0 else 0
    if pct_vacio > 25:
        advertencias.append(
            f"{pct_vacio:.0f}% del terreno sin uso ({vacias} celdas vacias). "
            "Considera reorganizar o agrandar recintos."
        )

    # 7. Proporciones extremas
    for nombre, datos in grid.recintos.items():
        ratio = max(datos["ancho_m"], datos["alto_m"]) / max(min(datos["ancho_m"], datos["alto_m"]), 0.01)
        if ratio > 3.5:
            tipo = detectar_tipo(nombre)
            if tipo != "pasillo":
                advertencias.append(
                    f"{nombre}: proporcion {ratio:.1f}:1 muy alargada. Considera mas cuadrado."
                )

    # 8. Score de distribución
    score = _calcular_score_distribución(grid, ady)

    # 9. Sugerencias inteligentes
    sugerencias = _sugerir_reorganizacion(grid, ady)

    # 10. Info general
    info.append(f"Terreno: {grid.ancho_m}x{grid.alto_m}m ({total} celdas)")
    info.append(f"Recintos: {len(grid.recintos)}")
    info.append(f"Celdas usadas: {total - vacias} ({100 - pct_vacio:.0f}%)")
    info.append(f"Score distribución: {score}/100")

    return {
        "ok": len(errores) == 0,
        "errores": errores,
        "advertencias": advertencias,
        "sugerencias": sugerencias,
        "info": info,
        "score": score,
        "adyacencias": ady,
    }
