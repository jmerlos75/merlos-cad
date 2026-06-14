# Merlos CAD

> A modern CAD engine for architects — built in Python, open at the core.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)]()

🌐 [Español](README.es.md)

---

## What is this?

**Merlos CAD** is a vector CAD editor designed for architects.
Built at [Estudio Merlos](https://github.com/jmerlos75) in Costa Rica,
it follows **INVU / CFIA** architectural standards natively.

```
Draw with keyboard commands — no dialog boxes, no mouse gymnastics.
```

---

## Features

### Dynamic Input (DYN) — Draw at speed

After placing the first point, just type and hit Enter.

| Tool | You type | Result |
|------|----------|--------|
| Line | `3.5` → `Tab` → `90` → `Enter` | 3.5 m line at 90° |
| Circle | `r` `0.75` → `Enter` | Circle radius 0.75 m |
| Rectangle | `4.5` → `Tab` → `3.0` → `Enter` | 4.5 × 3.0 m room |
| Move | `2.5` → `Tab` → `0` → `Enter` | Move 2.5 m horizontal |

### Smart Snaps — 9 types, color-coded

`END` `MID` `CEN` `QUA` `INT` `PER` `TAN` `NEA` `GRI`

Center snap works on any closed polyline, not just circles.

### Entity Model

`Line` `Polyline` `Circle` `Arc` `Text` `Hatch` `Dimension` `Leader` `Block/Insert` `Ellipse` `Spline`

Full layer system · Undo/Redo · DXF import/export

### Modifiers

Move · Copy · Rotate · Scale · Mirror · Offset · Trim · Extend · Fillet · Chamfer · Break · Explode · Array · Match Properties

---

## Installation

```bash
git clone https://github.com/jmerlos75/merlos-cad.git
cd merlos-cad
pip install -r requirements.txt
python cad_viewer.py
```

**Requirements:** Python 3.10+, Windows

---

## Keyboard Reference

| Key | Action |
|-----|--------|
| `L` | Line |
| `PL` | Polyline |
| `C` | Circle |
| `R` | Rectangle |
| `A` | Arc |
| `EL` | Ellipse |
| `SPL` | Spline |
| `T` | Text |
| `BH` | Hatch |
| `B` | Block definition |
| `I` | Insert block |
| `DH` / `DV` | Horizontal / Vertical dimension |
| `DA` | Angular dimension |
| `LD` | Leader |
| `M` | Move |
| `CO` | Copy |
| `RO` | Rotate |
| `SC` | Scale |
| `MI` | Mirror |
| `O` | Offset |
| `TR` | Trim |
| `EX` | Extend |
| `F` | Fillet |
| `CH` | Chamfer |
| `BR` | Break |
| `X` | Explode |
| `AR` | Array |
| `E` / `Del` | Erase |
| `U` / `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `F3` | Snap on/off |
| `F8` | Ortho on/off |
| `Esc` | Cancel |
| `Space` | Repeat last command |

---

## Architecture

```
merlos-cad/
├── cad/
│   ├── core/            ← Pure Python nucleus (shared with Studio)
│   │   ├── state.py     — CadState (entities, layers, undo stack)
│   │   ├── snap_engine.py
│   │   ├── grip_engine.py
│   │   ├── selection.py
│   │   ├── tools.py     — Drawing tools
│   │   ├── dimensions.py
│   │   └── commands.py
│   ├── engine.py        ← Tkinter UI + render pipeline
│   ├── entities.py      ← Entity dataclasses
│   ├── dxf_import.py    ← DXF/DWG import (ezdxf)
│   └── dxf_export.py    ← DXF export
├── cad_viewer.py        ← Entry point
└── requirements.txt
```

---

## Costa Rica / LATAM

Built for architects in Central America. Out of the box:

- Wall thickness: concrete 0.15 m, gypsum 0.10 m
- Door widths: exterior 1.10 m, interior 1.00 m
- CFIA dimensioning format (3 lines × 4 sides)
- Axis naming: letters vertical (A, B, C…) / numbers horizontal (1, 2, 3…)
- Room labels in uppercase on layer `A-TEXTO`

---

## Commercial Version — Estudio Merlos Pro

| Feature | Core (this repo) | Pro |
|---------|-----------------|-----|
| All drawing tools | ✅ | ✅ |
| DXF import / export | ✅ | ✅ |
| Snaps (9 types) | ✅ | ✅ |
| Qt GPU renderer | ❌ | ✅ |
| AI assistant | ❌ | ✅ |
| AutoCAD bridge (MCP) | ❌ | ✅ |
| INVU/CFIA checker | ❌ | ✅ |
| Priority support | ❌ | ✅ |

Contact: merlosv@hotmail.com

---

## Credits

See [CREDITS.md](CREDITS.md) for open source acknowledgements.

---

## License

[GNU AGPL v3.0](LICENSE) — free to use, modify, and distribute.
Commercial embedding requires a separate license — contact merlosv@hotmail.com.

---

*Built in Costa Rica 🇨🇷 — Estudio Merlos, 2026*
