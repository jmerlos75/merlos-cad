# build.ps1 — Build Merlos CAD .exe (MIT edition, Windows)
#
# Uso:
#   cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos CAD OSS"
#   .\build.ps1
#
# Output: C:\builds\merlos-cad-mit\merlos-cad\merlos-cad.exe

$REPO   = $PSScriptRoot
$DIST   = "C:\builds\merlos-cad-mit"
$BUNDLE = "$DIST\merlos-cad"

Write-Host "=== Merlos CAD OSS — PyInstaller build ===" -ForegroundColor Cyan

# 1. Smoke test
Write-Host "`n[1/3] Verificando imports..." -ForegroundColor Yellow
$check = python -c "import sys; sys.path.insert(0,r'$REPO'); from cad.engine import CADWindow; print('OK')" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: import fallido — $check" -ForegroundColor Red; exit 1
}
Write-Host "      $check"

# 2. Build
Write-Host "`n[2/3] Ejecutando PyInstaller..." -ForegroundColor Yellow
python -m PyInstaller "$REPO\merlos_cad.spec" --distpath $DIST --clean --noconfirm
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: PyInstaller falló" -ForegroundColor Red; exit 1
}

# 3. Post-build: copiar docs a raíz del bundle (PyInstaller 6 los pone en _internal/)
Write-Host "`n[3/3] Copiando documentación a raíz del bundle..." -ForegroundColor Yellow
Copy-Item "$REPO\CREDITS.md"   "$BUNDLE\CREDITS.md"   -Force
Copy-Item "$REPO\README.md"    "$BUNDLE\README.md"    -Force
Copy-Item "$REPO\README.es.md" "$BUNDLE\README.es.md" -Force

Write-Host "`n=== BUILD COMPLETO ===" -ForegroundColor Green
Write-Host "Bundle: $BUNDLE"
Write-Host "Exe:    $BUNDLE\merlos-cad.exe"
$sz = (Get-ChildItem $BUNDLE -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Tamaño total: {0:N0} MB" -f $sz)
