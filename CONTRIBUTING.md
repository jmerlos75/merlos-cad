# Contributing to Merlos CAD

Thank you for your interest in contributing.
This is a focused project — quality over quantity.

## What we welcome

- Bug fixes in `engine.py`
- New snap types or improvements to existing ones
- Performance improvements to the render pipeline
- Additional entity types (Spline, Ellipse, Dimension)
- DXF import (currently export only)
- Tests

## What belongs in the Pro version

Features related to AI, AutoCAD connectivity, or INVU/CFIA compliance
are part of the commercial layer and are not developed here.

## How to contribute

1. Fork the repository
2. Create a feature branch:
   ```bash
   git checkout -b fix/snap-intersection-edge-case
   ```
3. Make your changes
4. Verify syntax:
   ```bash
   python -m py_compile cad/engine.py
   ```
5. Test manually by running `python cad_viewer.py`
6. Open a Pull Request with a clear description of the change

## Code style

- The engine is intentionally a single file (`engine.py`) for portability
- Follow the existing naming conventions (`_snake_case` for private methods)
- Add a comment block above new methods explaining purpose and parameters
- Constants go at the top of the file in the `# ─── Constantes ───` section

## Reporting bugs

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Python version and OS

## Questions

Open a Discussion (not an Issue) for questions about the architecture or roadmap.

---

*Estudio Merlos — Costa Rica 🇨🇷*
