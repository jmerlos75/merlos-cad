# Merlos CAD

> A modern, AI-ready CAD engine for architects — built in Python, open at the core.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)]()

---

## ¿Qué es esto? / What is this?

**Merlos CAD** is a vector CAD editor designed from the ground up for architects.
Unlike traditional CAD tools, it is built with AI integration as a first-class feature,
not an afterthought.

Developed at [Estudio Merlos](https://github.com/jmerlosv) in Costa Rica,
it follows **INVU / CFIA** architectural standards natively.

```
Dibuja una planta de 3 habitaciones con retiros INVU

→ The engine understands what you mean.
```

---

## Key Features

### Dynamic Input (DYN) — Draw at speed
After placing the first point of any entity, just type and hit Enter.
No mouse gymnastics. No dialog boxes.

| Tool | You type | Result |
|------|----------|--------|
| Line | `3.5` → `Tab` → `90` → `Enter` | 3.5 m line at 90° |
| Circle | `r` `0.75` → `Enter` | Circle radius 0.75 m |
| Circle | `Tab` → `1.5` → `Enter` | Circle diameter 1.5 m |
| Rectangle | `4.5` → `Tab` → `3.0` → `Enter` | 4.5 × 3.0 m room |
| Rotate | `45` → `Enter` | Rotate selection 45° |
| Move | `2.5` → `Tab` → `0` → `Enter` | Move 2.5 m horizontal |

### Smart Snaps — 9 types, color-coded
`END` `MID` `CEN` `QUA` `INT` `PER` `TAN` `NEA` `GRI`

- **Center snap on polygons** — not just circles. Works on any closed polyline.
- Each snap type has a distinct color and symbol on the crosshair.
- Crosshair color changes to match the active snap type.

### Entity Model
- `Line`, `Polyline` (open/closed), `Circle`, `Arc`, `Text`
- Full layer system with visibility, lock, color, and line weight
- Undo / Redo stack
- DXF export

### Modifier Tools
All modifiers (Move, Copy, Rotate, Scale, Mirror, Offset) support DYN input.
The floating panel appears immediately after placing the base point —
type the value, press Enter, done.

---

## Installation

```bash
git clone https://github.com/jmerlosv/merlos-cad.git
cd merlos-cad
pip install -r requirements.txt
python cad_viewer.py
```

**Requirements**: Python 3.10+, customtkinter, tkinter (usually bundled with Python on Windows/macOS)

```
customtkinter>=5.2.0
```

---

## Architecture

```
merlos-cad/                  ← this repository (open source, AGPL-3.0)
├── cad/
│   ├── engine.py            ← Core CAD engine (~4000 lines)
│   │   ├── Entity model     (Line, Polyline, Circle, Arc, Text)
│   │   ├── Layer system
│   │   ├── Snap engine      (9 types + spatial index)
│   │   ├── DYN input        (floating panel + keyboard capture)
│   │   ├── Modifier ops     (Move, Copy, Rotate, Scale, Mirror, Offset)
│   │   └── Render pipeline  (static + dynamic two-layer)
│   └── dxf_export.py        ← DXF r12 export
├── cad_viewer.py            ← Standalone viewer app
├── requirements.txt
└── README.md
```

The commercial layer (AI hub, AutoCAD MCP bridge, INVU/CFIA compliance checker)
lives in a separate private repository and is available via **Estudio Merlos Pro**.

---

## Keyboard Reference

| Key | Action |
|-----|--------|
| `L` | Line |
| `PL` | Polyline |
| `C` | Circle |
| `R` | Rectangle |
| `A` | Arc |
| `M` | Move |
| `CO` | Copy |
| `RO` | Rotate |
| `SC` | Scale |
| `MI` | Mirror |
| `O` | Offset |
| `E` / `Del` | Erase |
| `U` / `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `F3` | Snap on/off |
| `F8` | Ortho on/off |
| `F12` | DYN on/off |
| `Esc` | Cancel |
| `Space` | Repeat last command |
| `/` | AI prompt (Pro) |

---

## Snaps Panel

Access via `F3` or the toolbar. Each snap type can be toggled independently:

| Code | Description | Symbol |
|------|-------------|--------|
| `END` | Endpoints of lines and arcs | Yellow square |
| `MID` | Midpoints of segments | Cyan diamond |
| `CEN` | Center of circles, arcs, and closed polygons | Magenta circle |
| `QUA` | Circle quadrants (0°, 90°, 180°, 270°) | Orange square |
| `INT` | Intersections between entities | Amber cross |
| `GRI` | Grid intersections | Green plus |
| `PER` | Perpendicular foot | Blue angle |
| `TAN` | Tangent to circle/arc | Violet circle |
| `NEA` | Nearest point on entity | Orange dot |

---

## Costa Rica / LATAM

This engine was built to serve architects in Central America.
Out of the box it follows the conventions of Costa Rican practice:

- Wall thickness conventions (concrete 0.15 m, gypsum 0.10 m)
- Door widths (exterior 1.10 m, interior 1.00 m)
- CFIA dimensioning format (3 lines × 4 sides)
- Axis naming (letters vertical A, B, C... / numbers horizontal 1, 2, 3...)
- Room labeling in uppercase on layer `A-TEXTO`

Full INVU/CFIA compliance validation is part of **Estudio Merlos Pro**.

---

## Roadmap

- [x] DYN input for all drawing tools and modifiers
- [x] 9-type snap engine with spatial index
- [x] Polygon center snap (CEN on closed polylines)
- [x] Two-layer render pipeline (static + dynamic)
- [x] DXF export
- [ ] DXF import
- [ ] Hatch / Fill patterns
- [ ] Auto-dimensioning from entity geometry
- [ ] PDF export
- [ ] Block / component library
- [ ] Selection cycling (`Tab` on overlapping entities)

---

## Contributing

Pull requests are welcome. For major changes please open an issue first.

The engine is a single-file design intentionally — `engine.py` is self-contained
so it can be embedded in other projects without dependency hell.

```bash
# Run the syntax check before submitting
python -m py_compile cad/engine.py
```

---

## Commercial Version — Estudio Merlos Pro

The open-source engine powers a professional desktop application for architecture firms:

| Feature | Core (this repo) | Pro |
|---------|-----------------|-----|
| Drawing tools | ✅ | ✅ |
| DYN input | ✅ | ✅ |
| Snaps (9 types) | ✅ | ✅ |
| DXF export | ✅ | ✅ |
| AI assistant | ❌ | ✅ |
| AutoCAD bridge | ❌ | ✅ |
| INVU/CFIA checker | ❌ | ✅ |
| Auto-dimensioning | ❌ | ✅ |
| Area schedule | ❌ | ✅ |
| Priority support | ❌ | ✅ |

**Pro starts at $25/month per seat.**
Contact: merlosv@hotmail.com

---

## License

The core engine is licensed under [GNU AGPL v3.0](LICENSE).

In plain terms:
- Free to use, modify, and distribute
- If you use it in a commercial product or SaaS, you must publish your source code
- If you want to embed it in a closed commercial product, contact us for a commercial license

---

*Built in Costa Rica 🇨🇷 — Estudio Merlos, 2025*
