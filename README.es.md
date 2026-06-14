# Merlos CAD

> Motor CAD moderno para arquitectos — construido en Python, núcleo abierto.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)]()

🌐 [English](README.md)

---

## ¿Qué es esto?

**Merlos CAD** es un editor CAD vectorial diseñado para arquitectos.
Desarrollado en [Estudio Merlos](https://github.com/jmerlos75) en Costa Rica,
sigue de forma nativa las normas **INVU / CFIA**.

```
Dibujá con comandos de teclado — sin cuadros de diálogo, sin depender del ratón.
```

---

## Funcionalidades

### Entrada Dinámica (DYN) — Dibujá rápido

Después de colocar el primer punto, escribís el valor y Enter.

| Herramienta | Escribís | Resultado |
|-------------|----------|-----------|
| Línea | `3.5` → `Tab` → `90` → `Enter` | Línea de 3.5 m a 90° |
| Círculo | `r` `0.75` → `Enter` | Círculo radio 0.75 m |
| Rectángulo | `4.5` → `Tab` → `3.0` → `Enter` | Recinto de 4.5 × 3.0 m |
| Mover | `2.5` → `Tab` → `0` → `Enter` | Mover 2.5 m horizontal |

### Snaps inteligentes — 9 tipos con código de color

`FIN` `MED` `CEN` `CUA` `INT` `PER` `TAN` `CEP` `CUA`

El snap de centro funciona en cualquier polilínea cerrada, no solo círculos.

### Entidades

`Línea` `Polilínea` `Círculo` `Arco` `Texto` `Hatch` `Cota` `Leader` `Bloque/Insertar` `Elipse` `Spline`

Sistema de capas completo · Deshacer/Rehacer · Importar/exportar DXF

### Modificadores

Mover · Copiar · Rotar · Escalar · Espejo · Desfase · Recortar · Extender · Empalme · Chaflán · Romper · Explotar · Arreglo · Igualar propiedades

---

## Instalación

```bash
git clone https://github.com/jmerlos75/merlos-cad.git
cd merlos-cad
pip install -r requirements.txt
python cad_viewer.py
```

**Requisitos:** Python 3.10+, Windows

---

## Referencia de teclas

| Tecla | Acción |
|-------|--------|
| `L` | Línea |
| `PL` | Polilínea |
| `C` | Círculo |
| `R` | Rectángulo |
| `A` | Arco |
| `EL` | Elipse |
| `SPL` | Spline |
| `T` | Texto |
| `BH` | Hatch / Relleno |
| `B` | Definir bloque |
| `I` | Insertar bloque |
| `DH` / `DV` | Cota horizontal / vertical |
| `DA` | Cota angular |
| `LD` | Línea de nota (Leader) |
| `M` | Mover |
| `CO` | Copiar |
| `RO` | Rotar |
| `SC` | Escalar |
| `MI` | Espejo |
| `O` | Desfase |
| `TR` | Recortar |
| `EX` | Extender |
| `F` | Empalme (Fillet) |
| `CH` | Chaflán |
| `BR` | Romper |
| `X` | Explotar |
| `AR` | Arreglo (Array) |
| `E` / `Supr` | Borrar |
| `U` / `Ctrl+Z` | Deshacer |
| `Ctrl+Y` | Rehacer |
| `F3` | Snaps on/off |
| `F8` | Orto on/off |
| `Esc` | Cancelar |
| `Espacio` | Repetir último comando |

---

## Arquitectura

```
merlos-cad/
├── cad/
│   ├── core/            ← Núcleo Python puro (compartido con Studio)
│   │   ├── state.py     — CadState (entidades, capas, pila de deshacer)
│   │   ├── snap_engine.py
│   │   ├── grip_engine.py
│   │   ├── selection.py
│   │   ├── tools.py     — Herramientas de dibujo
│   │   ├── dimensions.py
│   │   └── commands.py
│   ├── engine.py        ← UI Tkinter + pipeline de renderizado
│   ├── entities.py      ← Dataclasses de entidades
│   ├── dxf_import.py    ← Importar DXF/DWG (ezdxf)
│   └── dxf_export.py    ← Exportar DXF
├── cad_viewer.py        ← Punto de entrada
└── requirements.txt
```

---

## Costa Rica / Latinoamérica

Desarrollado para arquitectos de Centroamérica. Incluye de fábrica:

- Espesores de muro: concreto 0.15 m, gypsum 0.10 m
- Anchos de puerta: exterior 1.10 m, interior 1.00 m
- Formato de cotas CFIA (3 líneas × 4 lados)
- Nomenclatura de ejes: letras verticales (A, B, C…) / números horizontales (1, 2, 3…)
- Rótulos de recinto en MAYÚSCULAS en capa `A-TEXTO`

---

## Versión Comercial — Estudio Merlos Pro

| Funcionalidad | Core (este repo) | Pro |
|---------------|-----------------|-----|
| Todas las herramientas de dibujo | ✅ | ✅ |
| Importar / exportar DXF | ✅ | ✅ |
| Snaps (9 tipos) | ✅ | ✅ |
| Renderer Qt con GPU | ❌ | ✅ |
| Asistente IA | ❌ | ✅ |
| Puente AutoCAD (MCP) | ❌ | ✅ |
| Verificador INVU/CFIA | ❌ | ✅ |
| Soporte prioritario | ❌ | ✅ |

Contacto: merlosv@hotmail.com

---

## Créditos

Ver [CREDITS.md](CREDITS.md) para reconocimientos de tecnologías de código abierto.

---

## Licencia

[GNU AGPL v3.0](LICENSE) — libre para usar, modificar y distribuir.
El uso en productos comerciales cerrados requiere una licencia separada — contactar merlosv@hotmail.com.

---

*Hecho en Costa Rica 🇨🇷 — Estudio Merlos, 2026*
