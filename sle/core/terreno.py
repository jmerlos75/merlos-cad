"""
sle/core/terreno.py
===================
Motor de cálculo del terreno (lote) para Estudio Merlos AI.

Soporta derroteros en:
  - AZIMUT   : ángulo 0-360° desde Norte (horario), grados y minutos
  - RUMBO    : cuadrante N/S grados° minutos' E/W
  - COORDENADAS: ESTE/NORTE directas (CR-SIRGAS-CRTM05 u otro)

No tiene dependencias GUI — se importa desde terreno_app.py y otros módulos.
"""
from __future__ import annotations
import math
from typing import Optional

__all__ = [
    "rumbo_a_azimut", "azimut_a_delta",
    "vertices_desde_lineas", "area_poligono",
    "error_cierre", "perimetro",
    "crtm05_a_wgs84", "wgs84_a_crtm05", "norte_lote_auto",
]


# ════════════════════════════════════════════════════════════════════════
# CONVERSIONES ANGULARES
# ════════════════════════════════════════════════════════════════════════

def rumbo_a_azimut(ns: str, grados: float, minutos: float, ew: str) -> float:
    """
    Convierte rumbo cuadrantal a azimut (0-360°).

    Cuadrantes:
      N X° E  → azimut = X
      N X° W  → azimut = 360 - X
      S X° E  → azimut = 180 - X
      S X° W  → azimut = 180 + X
    """
    ang = grados + minutos / 60.0
    ns = (ns or "N").upper().strip()
    ew = (ew or "E").upper().strip()
    if ns == "N" and ew == "E":  return ang
    if ns == "N" and ew == "W":  return 360.0 - ang
    if ns == "S" and ew == "E":  return 180.0 - ang
    if ns == "S" and ew == "W":  return 180.0 + ang
    return ang


def azimut_a_delta(az_deg: float, dist: float) -> tuple[float, float]:
    """
    Desplazamiento (dEste, dNorte) dado azimut y distancia.
    Azimut: 0° = Norte, 90° = Este, 180° = Sur, 270° = Oeste.
    """
    r = math.radians(az_deg)
    return dist * math.sin(r), dist * math.cos(r)


# ════════════════════════════════════════════════════════════════════════
# CÁLCULO DE POLÍGONO
# ════════════════════════════════════════════════════════════════════════

def _az_de_linea(ln: dict, modo: str) -> float:
    """Azimut en grados decimales a partir de un dict de línea."""
    if modo == "azimut":
        return float(ln.get("az_g", 0)) + float(ln.get("az_m", 0)) / 60.0
    elif modo == "rumbo":
        return rumbo_a_azimut(
            ln.get("ns", "N"),
            float(ln.get("rum_g", 0)),
            float(ln.get("rum_m", 0)),
            ln.get("ew", "E"),
        )
    return 0.0


def _dist_de_linea(ln: dict) -> float:
    return float(ln.get("dist_m", 0)) + float(ln.get("dist_cm", 0)) / 100.0


def vertices_desde_lineas(
    lineas: list[dict],
    modo: str,
    inicio: tuple[float, float] = (0.0, 0.0),
) -> list[tuple[float, float]]:
    """
    Calcula los vértices del polígono del terreno.

    Para AZIMUT / RUMBO: acumula deltas desde `inicio`.
    Para COORDENADAS: usa directamente (este, norte) de cada línea,
      normalizadas respecto al primer punto.

    Args:
        lineas : lista de dicts con los datos de cada línea del derrotero.
        modo   : "azimut", "rumbo" o "coordenadas".
        inicio : punto de partida para azimut/rumbo.

    Returns:
        Lista de (este, norte) sin el punto de cierre repetido.
    """
    if modo == "coordenadas":
        pts = []
        for ln in lineas:
            try:
                pts.append((float(ln["este"]), float(ln["norte"])))
            except (KeyError, ValueError):
                pass
        if not pts:
            return []
        e0, n0 = pts[0]
        return [(e - e0, n - n0) for e, n in pts]

    # Azimut o Rumbo
    pts: list[tuple[float, float]] = [inicio]
    for ln in lineas:
        try:
            dist = _dist_de_linea(ln)
            az   = _az_de_linea(ln, modo)
            de, dn = azimut_a_delta(az, dist)
            x, y = pts[-1]
            pts.append((x + de, y + dn))
        except (ValueError, ZeroDivisionError):
            pts.append(pts[-1])     # punto repetido si hay error de entrada
    return pts[:-1]                 # sin el punto de cierre repetido


def area_poligono(verts: list[tuple[float, float]]) -> float:
    """Área en m² por fórmula de Gauss (shoelace). Siempre positiva."""
    n = len(verts)
    if n < 3:
        return 0.0
    total = sum(
        verts[i][0] * verts[(i + 1) % n][1]
        - verts[(i + 1) % n][0] * verts[i][1]
        for i in range(n)
    )
    return abs(total) / 2.0


def perimetro(verts: list[tuple[float, float]]) -> float:
    """Perímetro total en metros."""
    n = len(verts)
    if n < 2:
        return 0.0
    return sum(
        math.hypot(verts[(i + 1) % n][0] - verts[i][0],
                   verts[(i + 1) % n][1] - verts[i][1])
        for i in range(n)
    )


def error_cierre(
    lineas: list[dict],
    modo: str,
) -> tuple[float, float]:
    """
    Error de cierre del derrotero: (dEste, dNorte) acumulados.
    Para un polígono perfecto ambos deben ser ~0.
    Solo aplica a azimut/rumbo (en coordenadas siempre es 0).
    """
    if modo == "coordenadas":
        return 0.0, 0.0
    de_total = dn_total = 0.0
    for ln in lineas:
        try:
            dist = _dist_de_linea(ln)
            az   = _az_de_linea(ln, modo)
            de, dn = azimut_a_delta(az, dist)
            de_total += de
            dn_total += dn
        except (ValueError, ZeroDivisionError):
            pass
    return de_total, dn_total


# ════════════════════════════════════════════════════════════════════════
# COORDENADAS GEOGRÁFICAS
# ════════════════════════════════════════════════════════════════════════

# Parámetros CR-SIRGAS-CRTM05 (EPSG:5367) — WGS84 ellipsoid
_A   = 6_378_137.0          # semi-eje mayor (m)
_F   = 1 / 298.257_223_563  # aplanamiento
_B   = _A * (1 - _F)
_E2  = 1 - (_B / _A) ** 2   # excentricidad² ≈ 0.006694379990
_K0  = 0.9999               # factor de escala CRTM05
_LON0= math.radians(-84.0)  # meridiano central CRTM05
_FE  = 500_000.0            # falso Este (m)
_FN  = 0.0                  # falso Norte (m)


def crtm05_a_wgs84(este: float, norte: float) -> tuple[float, float]:
    """
    Convierte coordenadas CR-SIRGAS-CRTM05 (EPSG:5367) a WGS84 (lat, lon).

    Usa la serie de Helmert (TM inverso exacto), sin dependencias externas.
    Precisión: < 1 mm dentro de Costa Rica.

    Args:
        este, norte : coordenadas en metros (CRTM05)

    Returns:
        (latitud, longitud) en grados decimales WGS84
        Lat positivo = Norte; Lon negativo = Oeste
    """
    e2 = _E2
    e  = math.sqrt(e2)
    e_prime2 = e2 / (1.0 - e2)

    x = (este  - _FE) / _K0
    y = (norte - _FN) / _K0

    # Latitud del pie (phi1) usando serie de Bessel
    e1 = (1.0 - math.sqrt(1.0 - e2)) / (1.0 + math.sqrt(1.0 - e2))

    M  = y
    mu = M / (_A * (1.0 - e2/4.0 - 3.0*e2**2/64.0 - 5.0*e2**3/256.0))

    phi1 = (mu
            + (3.0*e1/2.0   - 27.0*e1**3/32.0) * math.sin(2.0*mu)
            + (21.0*e1**2/16.0 - 55.0*e1**4/32.0) * math.sin(4.0*mu)
            + (151.0*e1**3/96.0) * math.sin(6.0*mu)
            + (1097.0*e1**4/512.0) * math.sin(8.0*mu))

    sin_phi1 = math.sin(phi1)
    cos_phi1 = math.cos(phi1)
    tan_phi1 = math.tan(phi1)

    N1 = _A / math.sqrt(1.0 - e2 * sin_phi1**2)
    T1 = tan_phi1**2
    C1 = e_prime2 * cos_phi1**2
    R1 = _A * (1.0 - e2) / (1.0 - e2 * sin_phi1**2) ** 1.5
    D  = x / (N1 * _K0)

    # Latitud (radianes)
    lat_rad = phi1 - (N1 * tan_phi1 / R1) * (
        D**2 / 2.0
        - (5.0 + 3.0*T1 + 10.0*C1 - 4.0*C1**2 - 9.0*e_prime2) * D**4 / 24.0
        + (61.0 + 90.0*T1 + 298.0*C1 + 45.0*T1**2 - 252.0*e_prime2 - 3.0*C1**2) * D**6 / 720.0
    )

    # Longitud (radianes)
    lon_rad = _LON0 + (
        D
        - (1.0 + 2.0*T1 + C1) * D**3 / 6.0
        + (5.0 - 2.0*C1 + 28.0*T1 - 3.0*C1**2 + 8.0*e_prime2 + 24.0*T1**2) * D**5 / 120.0
    ) / cos_phi1

    return math.degrees(lat_rad), math.degrees(lon_rad)


def wgs84_a_crtm05(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    """
    Convierte WGS84 (lat, lon) a CR-SIRGAS-CRTM05 (Este, Norte).
    Fórmula TM directa (Helmert). Precisión < 1m en Costa Rica.

    Args:
        lat_deg : latitud en grados decimales (positivo = Norte)
        lon_deg : longitud en grados decimales (negativo = Oeste)

    Returns:
        (Este, Norte) en metros CRTM05
    """
    phi = math.radians(lat_deg)
    lam = math.radians(lon_deg)
    ep2 = _E2 / (1.0 - _E2)

    N   = _A / math.sqrt(1.0 - _E2 * math.sin(phi) ** 2)
    T   = math.tan(phi) ** 2
    C   = ep2 * math.cos(phi) ** 2
    Av  = math.cos(phi) * (lam - _LON0)

    M = _A * (
        (1.0 - _E2/4.0 - 3.0*_E2**2/64.0 - 5.0*_E2**3/256.0) * phi
        - (3.0*_E2/8.0 + 3.0*_E2**2/32.0 + 45.0*_E2**3/1024.0) * math.sin(2.0*phi)
        + (15.0*_E2**2/256.0 + 45.0*_E2**3/1024.0) * math.sin(4.0*phi)
        - (35.0*_E2**3/3072.0) * math.sin(6.0*phi)
    )

    x = _K0 * N * (
        Av
        + (1.0 - T + C) * Av**3 / 6.0
        + (5.0 - 18.0*T + T**2 + 72.0*C - 58.0*ep2) * Av**5 / 120.0
    )
    y = _K0 * (
        M + N * math.tan(phi) * (
            Av**2 / 2.0
            + (5.0 - T + 9.0*C + 4.0*C**2) * Av**4 / 24.0
            + (61.0 - 58.0*T + T**2 + 600.0*C - 330.0*ep2) * Av**6 / 720.0
        )
    )
    return x + _FE, y + _FN


def norte_lote_auto(
    vertices_absolutos: list[tuple[float, float]],
    idx_frente: int,
) -> str:
    """
    Detecta hacia qué dirección cardinal apunta el FRENTE del lote.
    Usa la posición del lado frente relativa al centroide del polígono.

    Funciona con coordenadas CRTM05 (Y = Norte geográfico).

    Args:
        vertices_absolutos : lista (Este, Norte) del polígono completo
        idx_frente         : índice del vértice INICIO del lado frente

    Returns:
        "norte" | "sur" | "este" | "oeste"
    """
    n = len(vertices_absolutos)
    if n < 3:
        return "norte"

    # Centroide del lote
    cx = sum(v[0] for v in vertices_absolutos) / n
    cy = sum(v[1] for v in vertices_absolutos) / n

    # Centro del lado frente
    p1 = vertices_absolutos[idx_frente % n]
    p2 = vertices_absolutos[(idx_frente + 1) % n]
    fx = (p1[0] + p2[0]) / 2.0
    fy = (p1[1] + p2[1]) / 2.0

    # Vector del centroide hacia el frente (→ dirección exterior)
    dx = fx - cx   # + = Este
    dy = fy - cy   # + = Norte

    # Azimut del exterior (0°=N, 90°=E, 180°=S, 270°=O)
    az = (math.degrees(math.atan2(dx, dy))) % 360.0

    if az < 45 or az >= 315:  return "norte"
    if 45  <= az < 135:       return "este"
    if 135 <= az < 225:       return "sur"
    return "oeste"


# ════════════════════════════════════════════════════════════════════════
# SMOKE TEST
# ════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 54)
    print("  terreno.py — Tests básicos")
    print("=" * 54)

    # Test 1: Azimut — lote rectangular 20×10m orientado al Norte
    print("\n[ 1 ] Lote 20×10m — Azimut")
    lns = [
        {"az_g": 0,   "az_m": 0, "dist_m": 20, "dist_cm": 0},   # Norte 20m
        {"az_g": 90,  "az_m": 0, "dist_m": 10, "dist_cm": 0},   # Este 10m
        {"az_g": 180, "az_m": 0, "dist_m": 20, "dist_cm": 0},   # Sur 20m
        {"az_g": 270, "az_m": 0, "dist_m": 10, "dist_cm": 0},   # Oeste 10m
    ]
    v = vertices_desde_lineas(lns, "azimut")
    a = area_poligono(v)
    de, dn = error_cierre(lns, "azimut")
    print(f"  Vértices : {[tuple(round(c,2) for c in p) for p in v]}")
    print(f"  Área     : {a:.2f} m²  (esperado 200.0)")
    print(f"  Cierre   : dE={de:.4f} dN={dn:.4f}  (esperado 0,0)")
    assert abs(a - 200.0) < 0.01, f"Area incorrecta: {a}"
    assert abs(de) < 0.001 and abs(dn) < 0.001
    print("  [OK]")

    # Test 2: Rumbo — mismo lote
    print("\n[ 2 ] Lote 20×10m — Rumbo")
    lns_r = [
        {"ns": "N", "rum_g": 0, "rum_m": 0, "ew": "E", "dist_m": 20, "dist_cm": 0},
        {"ns": "N", "rum_g": 90, "rum_m": 0, "ew": "E", "dist_m": 10, "dist_cm": 0},  # S90E = E
        {"ns": "S", "rum_g": 0, "rum_m": 0, "ew": "E", "dist_m": 20, "dist_cm": 0},
        {"ns": "S", "rum_g": 90, "rum_m": 0, "ew": "W", "dist_m": 10, "dist_cm": 0},  # S90W = O
    ]
    # N0°E = az0°, S90°E = az90° ✓ ... pero rumbo N90°E en realidad = Este
    # Ajustamos: la conversión S90°E → 180-90=90° también es Este ✓
    v2 = vertices_desde_lineas(lns_r, "rumbo")
    a2 = area_poligono(v2)
    print(f"  Área     : {a2:.2f} m²  (esperado 200.0)")
    assert abs(a2 - 200.0) < 0.01
    print("  [OK]")

    # Test 3: Conversión rumbo_a_azimut
    print("\n[ 3 ] rumbo_a_azimut")
    casos = [
        ("N", 0, 33, "W", 359.45),    # N 0°33' W → 360 - 0.55 = 359.45
        ("N", 75, 40, "E",  75.667),  # N75°40'E → 75.667
        ("S", 10, 22, "W", 190.367),  # S10°22'W → 180 + 10.367 = 190.367
        ("S", 78, 33, "W", 258.55),   # S78°33'W → 180 + 78.55 = 258.55
    ]
    for ns, g, m, ew, esperado in casos:
        r = rumbo_a_azimut(ns, g, m, ew)
        ok = abs(r - esperado) < 0.01
        print(f"  {ns}{g}d{m:02d}'{ew} = {r:.3f}  (esp {esperado})  {'[OK]' if ok else '[FAIL]'}")
        assert ok, f"Fallo: {r} vs {esperado}"

    # Test 4: Coordenadas directas
    print("\n[ 4 ] Coordenadas directas (CR-SIRGAS-CRTM05)")
    lns_c = [
        {"este": 489129.20, "norte": 1104402.08},
        {"este": 489127.23, "norte": 1104402.43},
        {"este": 489126.04, "norte": 1104398.61},
        {"este": 489118.17, "norte": 1104400.03},
        {"este": 489125.88, "norte": 1104424.97},
        {"este": 489135.65, "norte": 1104422.83},
    ]
    v4 = vertices_desde_lineas(lns_c, "coordenadas")
    a4 = area_poligono(v4)
    print(f"  Área     : {a4:.2f} m²")
    print(f"  Vértices : {len(v4)}")
    assert len(v4) == 6
    print("  [OK]")

    # Test 5: crtm05_a_wgs84 — punto conocido
    print("\n[ 5 ] crtm05_a_wgs84 (TM inverso)")
    # Centroide aproximado del PDF: Este=489129, Norte=1104402
    lat, lon = crtm05_a_wgs84(489129.20, 1104402.08)
    print(f"  E=489129 N=1104402 -> lat={lat:.6f} lon={lon:.6f}")
    # Deberia estar en Costa Rica (lat 8-11, lon -82 a -86)
    assert 8.0 < lat < 11.0, f"Latitud fuera de CR: {lat}"
    assert -86.0 < lon < -82.0, f"Longitud fuera de CR: {lon}"
    print("  [OK] - Ubicado en Costa Rica")

    # Verificar que el punto central (Este=500000, Norte=0) da lon=-84, lat=0
    lat0, lon0 = crtm05_a_wgs84(500000, 0)
    assert abs(lon0 - (-84.0)) < 0.001, f"Meridiano central incorrecto: {lon0}"
    assert abs(lat0) < 0.001, f"Ecuador incorrecto: {lat0}"
    print("  [OK] - Meridiano central y ecuador correctos")

    # Test 6: norte_lote_auto
    print("\n[ 6 ] norte_lote_auto")
    # Lote con frente al norte (lado de arriba)
    v_sq = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert norte_lote_auto(v_sq, 2) == "norte", "Frente norte fallido"  # lado 2-3 = arriba
    assert norte_lote_auto(v_sq, 0) == "sur",   "Frente sur fallido"    # lado 0-1 = abajo
    assert norte_lote_auto(v_sq, 1) == "este",  "Frente este fallido"
    assert norte_lote_auto(v_sq, 3) == "oeste", "Frente oeste fallido"
    print("  [OK] - N/S/E/O detectados correctamente")

    print("\n" + "=" * 54)
    print("  Todos los tests OK")
    print("=" * 54)
