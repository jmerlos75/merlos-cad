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
    from cad import mcp_server

    app = CADWindow()
    mcp_server.set_engine(app)
    mcp_server.start_server(port=6789)
    app.run()


if __name__ == "__main__":
    main()
