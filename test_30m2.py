import re, json, sys, time
sys.path.insert(0, '.')

from modulos.diseno.grid import Grid
from modulos.diseno.validador import validar
from modulos.diseno.visualizador import generar_preview

PLAN_JSON = {
    "grid": {"ancho_m": 6, "alto_m": 5},
    "recintos": [
        {"nombre": "SALA",       "fila": 2, "col": 0, "ancho": 3, "alto": 2},
        {"nombre": "COMEDOR",    "fila": 2, "col": 3, "ancho": 3, "alto": 1},
        {"nombre": "COCINA",     "fila": 3, "col": 3, "ancho": 3, "alto": 2},
        {"nombre": "DORMITORIO", "fila": 0, "col": 0, "ancho": 4, "alto": 2},
        {"nombre": "BANO",       "fila": 0, "col": 4, "ancho": 2, "alto": 2},
    ],
    "puertas": [
        {"tipo": "exterior",        "recinto": "SALA",        "lado": "sur",  "ancho": 0.9},
        {"tipo": "entre_recintos",  "recinto1": "SALA",       "recinto2": "COMEDOR",    "ancho": 0.8},
        {"tipo": "entre_recintos",  "recinto1": "COMEDOR",    "recinto2": "COCINA",     "ancho": 0.8},
        {"tipo": "entre_recintos",  "recinto1": "SALA",       "recinto2": "DORMITORIO", "ancho": 0.8},
        {"tipo": "entre_recintos",  "recinto1": "DORMITORIO", "recinto2": "BANO",       "ancho": 0.7},
    ]
}

plan = PLAN_JSON
g_info = plan["grid"]
grid = Grid(g_info["ancho_m"], g_info["alto_m"], escala=1.0)

for r in plan["recintos"]:
    grid.colocar(r["nombre"], r["fila"], r["col"], r["ancho"], r["alto"])

val = validar(grid)

print("=== VALIDACION PROGRAMATICA ===")
print(f"Score: {val['score']}/100  |  OK: {val['ok']}")
if val["errores"]:
    print("ERRORES:")
    for e in val["errores"]: print(f"  [X] {e}")
else:
    print("  Sin errores")
if val["advertencias"]:
    print("ADVERTENCIAS:")
    for a in val["advertencias"]: print(f"  [!] {a}")
else:
    print("  Sin advertencias")
if val["sugerencias"]:
    print("SUGERENCIAS:")
    for s in val["sugerencias"]:
        print(f"  [i] {s}".encode('ascii','replace').decode())
print()
print("INFO:")
for i in val["info"]: print(f"  {i}")

print()
print("=== RECINTOS ===")
for nombre, info in grid.recintos.items():
    print(f"  {nombre}: {info['ancho_m']:.0f}x{info['alto_m']:.0f}m = {info['area_m2']:.0f} m2")

print()
print("=== ADYACENCIAS ===")
ady = grid.adyacencias()
for nombre, vecinos in ady.items():
    print(f"  {nombre} -> {vecinos}")

print()
print("=== TOPOLOGIA FUNCIONAL ===")
checks = [
    ("Sala <-> Comedor",     "COMEDOR"    in ady.get("SALA", [])),
    ("Comedor <-> Cocina",   "COCINA"     in ady.get("COMEDOR", [])),
    ("Sala <-> Dormitorio",  "DORMITORIO" in ady.get("SALA", [])),
    ("Dormitorio <-> Bano",  "BANO"       in ady.get("DORMITORIO", [])),
    ("Sala toca sur (calle)",grid.toca_borde("SALA", "sur")),
    ("Dormitorio toca norte",grid.toca_borde("DORMITORIO", "norte")),
    ("Bano toca norte",      grid.toca_borde("BANO", "norte")),
]
for label, ok in checks:
    print(f"  [{'OK' if ok else 'NO'}] {label}")

vacias = grid.celdas_vacias()
total  = grid.rows * grid.cols
print(f"\n  Celdas vacias: {vacias}/{total} ({vacias/total*100:.0f}%)")

print()
print("=== GRID ASCII ===")
print(grid.to_ascii())

# Preview
ruta = "cache/previews/test_30m2.png"
info_prev = generar_preview(grid, ruta, escala_px=80, validacion=val)
print(f"\nPreview guardado: {info_prev['path']}")
print(f"Tamano: {info_prev['width']}x{info_prev['height']}px")
