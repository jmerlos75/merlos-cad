"""
cad_viewer.py — Estudio Merlos CAD · Visor v1 · Entry point
============================================================
Uso:
    python cad_viewer.py
"""
from __future__ import annotations
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def main() -> None:
    from cad.engine import CADWindow
    app = CADWindow()
    app.run()


if __name__ == "__main__":
    main()
