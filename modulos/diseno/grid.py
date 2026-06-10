class Grid:
    """Cuadricula de diseno - cada celda = escala metros."""

    def __init__(self, ancho_m: float, alto_m: float, escala: float = 1.0):
        self.escala = escala
        self.ancho_m = ancho_m
        self.alto_m = alto_m
        self.cols = int(round(ancho_m / escala))
        self.rows = int(round(alto_m / escala))
        self.cells = [[None] * self.cols for _ in range(self.rows)]
        self.recintos = {}
        self.norte = "arriba"
        self.calle = "abajo"

    def colocar(self, nombre: str, fila: int, col: int, ancho_celdas: int, alto_celdas: int):
        if fila < 0 or col < 0 or fila + alto_celdas > self.rows or col + ancho_celdas > self.cols:
            raise ValueError(
                f"Posicion fuera del grid. Grid es {self.rows}x{self.cols}, "
                f"intentaste colocar en ({fila},{col}) de {alto_celdas}x{ancho_celdas}"
            )

        for r in range(fila, fila + alto_celdas):
            for c in range(col, col + ancho_celdas):
                if self.cells[r][c] is not None:
                    raise ValueError(
                        f"Celda ({r},{c}) ya ocupada por '{self.cells[r][c]}'. "
                        f"No se puede colocar '{nombre}'"
                    )

        for r in range(fila, fila + alto_celdas):
            for c in range(col, col + ancho_celdas):
                self.cells[r][c] = nombre

        self.recintos[nombre] = {
            "fila": fila,
            "col": col,
            "ancho": ancho_celdas,
            "alto": alto_celdas,
            "ancho_m": ancho_celdas * self.escala,
            "alto_m": alto_celdas * self.escala,
            "area_m2": ancho_celdas * alto_celdas * (self.escala ** 2),
        }
        return self.recintos[nombre]

    def quitar(self, nombre: str):
        if nombre not in self.recintos:
            return False
        info = self.recintos[nombre]
        for r in range(info["fila"], info["fila"] + info["alto"]):
            for c in range(info["col"], info["col"] + info["ancho"]):
                if self.cells[r][c] == nombre:
                    self.cells[r][c] = None
        del self.recintos[nombre]
        return True

    def to_ascii(self) -> str:
        """Representacion ASCII del grid para que la IA la lea."""
        ancho_etiqueta = 6
        lineas = []
        header = "    " + "".join(f"{c:^{ancho_etiqueta}}" for c in range(self.cols))
        lineas.append(header)
        for r in range(self.rows):
            linea = f"{r:3} "
            for c in range(self.cols):
                celda = self.cells[r][c]
                if celda is None:
                    linea += f"{'.':^{ancho_etiqueta}}"
                else:
                    nombre_corto = celda[:ancho_etiqueta - 1]
                    linea += f"{nombre_corto:^{ancho_etiqueta}}"
            lineas.append(linea)
        return "\n".join(lineas)

    def adyacencias(self) -> dict:
        """Para cada recinto, lista los recintos con los que comparte pared."""
        result = {}
        for nombre, info in self.recintos.items():
            vecinos = set()
            for r in range(info["fila"], info["fila"] + info["alto"]):
                for c in range(info["col"], info["col"] + info["ancho"]):
                    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < self.rows and 0 <= nc < self.cols:
                            vecino = self.cells[nr][nc]
                            if vecino is not None and vecino != nombre:
                                vecinos.add(vecino)
            result[nombre] = sorted(vecinos)
        return result

    def toca_borde(self, nombre: str, lado: str) -> bool:
        if nombre not in self.recintos:
            return False
        info = self.recintos[nombre]
        if lado == "norte":
            return info["fila"] == 0
        if lado == "sur":
            return info["fila"] + info["alto"] == self.rows
        if lado == "este":
            return info["col"] + info["ancho"] == self.cols
        if lado == "oeste":
            return info["col"] == 0
        return False

    def bordes_que_toca(self, nombre: str) -> list:
        return [lado for lado in ["norte", "sur", "este", "oeste"] if self.toca_borde(nombre, lado)]

    def celdas_vacias(self) -> int:
        return sum(1 for fila in self.cells for celda in fila if celda is None)

    def encontrar_posicion_libre(self, ancho_celdas: int, alto_celdas: int) -> dict:
        """Busca la primera posicion libre donde cabe un recinto de ancho x alto.
        Escanea fila por fila de arriba-abajo, izq-derecha.
        Retorna dict con fila, col o None si no hay espacio."""
        for r in range(self.rows - alto_celdas + 1):
            for c in range(self.cols - ancho_celdas + 1):
                libre = all(
                    self.cells[r + dr][c + dc] is None
                    for dr in range(alto_celdas)
                    for dc in range(ancho_celdas)
                )
                if libre:
                    return {"fila": r, "col": c}
        return None

    def zonas_libres_compactas(self) -> list:
        """Retorna lista de rectangulos libres (fila, col, ancho, alto) disponibles."""
        visitado = [[False] * self.cols for _ in range(self.rows)]
        zonas = []
        for r in range(self.rows):
            for c in range(self.cols):
                if self.cells[r][c] is None and not visitado[r][c]:
                    # Expandir rectangulo hacia abajo y derecha
                    max_c = c
                    while max_c + 1 < self.cols and self.cells[r][max_c + 1] is None:
                        max_c += 1
                    max_r = r
                    while max_r + 1 < self.rows:
                        fila_libre = all(self.cells[max_r + 1][cc] is None for cc in range(c, max_c + 1))
                        if fila_libre:
                            max_r += 1
                        else:
                            break
                    ancho = max_c - c + 1
                    alto = max_r - r + 1
                    for dr in range(alto):
                        for dc in range(ancho):
                            visitado[r + dr][c + dc] = True
                    zonas.append({"fila": r, "col": c, "ancho": ancho, "alto": alto,
                                  "area_m2": ancho * alto * self.escala ** 2})
        return sorted(zonas, key=lambda z: z["area_m2"], reverse=True)

    def to_dict(self) -> dict:
        return {
            "ancho_m": self.ancho_m,
            "alto_m": self.alto_m,
            "escala": self.escala,
            "cols": self.cols,
            "rows": self.rows,
            "recintos": self.recintos,
            "norte": self.norte,
            "calle": self.calle,
            "celdas_vacias": self.celdas_vacias(),
        }
