import sys, time
sys.path.insert(0, '.')

from modulos.autocad.ejecutor import EjecutorAutoCAD

PLAN = {
    "grid": {"ancho_m": 6, "alto_m": 5},
    "recintos": [
        {"nombre": "SALA",       "fila": 2, "col": 0, "ancho": 3, "alto": 2},
        {"nombre": "COMEDOR",    "fila": 2, "col": 3, "ancho": 3, "alto": 1},
        {"nombre": "COCINA",     "fila": 3, "col": 3, "ancho": 3, "alto": 2},
        {"nombre": "DORMITORIO", "fila": 0, "col": 0, "ancho": 4, "alto": 2},
        {"nombre": "BANO",       "fila": 0, "col": 4, "ancho": 2, "alto": 2},
    ],
    "puertas": [
        {"tipo": "exterior",       "recinto": "SALA",        "lado": "sur",  "ancho": 0.9},
        {"tipo": "entre_recintos", "recinto1": "SALA",       "recinto2": "COMEDOR",    "ancho": 0.8},
        {"tipo": "entre_recintos", "recinto1": "COMEDOR",    "recinto2": "COCINA",     "ancho": 0.8},
        {"tipo": "entre_recintos", "recinto1": "SALA",       "recinto2": "DORMITORIO", "ancho": 0.8},
        {"tipo": "entre_recintos", "recinto1": "DORMITORIO", "recinto2": "BANO",       "ancho": 0.7},
    ]
}

def log(msg):
    print(f"  {msg}")

print("Conectando a AutoCAD...")
ejecutor = EjecutorAutoCAD()

print("Ejecutando plan 30m2 con vanos y puertas...")
t0 = time.time()
resultado = ejecutor.ejecutar_plan_json(PLAN, on_log=log, forzar=True)
elapsed = time.time() - t0

print()
print(f"Tiempo: {elapsed:.1f}s")
print(f"OK: {resultado.get('ok')}")

if resultado.get("errores"):
    print("ERRORES:")
    for e in resultado["errores"]:
        print(f"  [X] {e}")

val = resultado.get("validacion", {})
if val:
    print(f"Score final: {val.get('score', '?')}/100")

print()
print("Listo. Revise AutoCAD.")
