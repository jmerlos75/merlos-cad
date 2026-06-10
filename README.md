# Merlos CAD

Editor vectorial CAD de escritorio, construido en Python con renderizado OpenGL + overlay PIL.

## Características

- Dibujo vectorial: líneas, polilíneas, arcos, círculos, elipses, splines, texto, cotas
- Capas, snap de objeto, modo ortogonal
- Importar y exportar DXF (AC2018)
- Exportar PNG
- Bloques, hatch (relleno), líderes
- Renderer GPU con cache VRAM — fluido en planos de 60k+ entidades

## Requisitos

- Python 3.10+
- `customtkinter`, `Pillow`, `ezdxf`, `PyOpenGL`, `numpy`

```bash
pip install -r requirements.txt
```

## Uso

```bash
python main.py
```

## Autor

Joseph Merlos — [Estudio Merlos](https://github.com/jmerlos75)

## Licencia

MIT © 2026 Joseph Merlos
