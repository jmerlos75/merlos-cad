# Prompt para continuar — GitHub Setup + OSS + Repo privado
> Copiar este texto completo como primer mensaje al nuevo hilo.
> El asistente debe hacer TODO junto con el usuario, paso a paso, tomando control cuando sea necesario.

---

## CONTEXTO — qué ya está hecho

- Cuenta GitHub creada: **jmerlos75** (Estudio Merlos IA)
- Token Personal de acceso generado y guardado por el usuario (empieza con `ghp_`)
- Git ya está instalado en la máquina
- El proyecto CAD está en: `C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI\`
- Los documentos de referencia están en esa carpeta bajo `docs\`:
  - `docs\separacion-opensource.md` — qué cortar de engine.py
  - `docs\github-setup.md` — pasos de setup
  - `docs\migracion-pyqt6.md` — para el futuro

---

## LO QUE FALTA HACER (en orden)

### BLOQUE A — Configurar Git con identidad

Abrir **Git Bash** (está en el escritorio o en el menú inicio) y ejecutar:
```bash
git config --global user.name "Joseph Merlos"
git config --global user.email "merlosv@hotmail.com"
```

---

### BLOQUE B — Crear el repositorio OSS público en GitHub

1. Abrir Chrome → ir a: `https://github.com/new`
2. Llenar así:
   - **Repository name:** `merlos-cad`
   - **Description:** `Open source CAD desktop with OpenGL renderer. AutoCAD-compatible commands, snap, grips, layers, DXF import/export.`
   - **Visibilidad:** ✅ **Public**
   - **NO** marcar Add README
   - **NO** marcar .gitignore
   - **License:** MIT License ← marcar esto
3. Click **Create repository**
4. Copiar la URL que aparece, ejemplo: `https://github.com/jmerlos75/merlos-cad.git`

---

### BLOQUE C — Crear repositorio privado en GitHub

1. Chrome → `https://github.com/new`
2. Llenar así:
   - **Repository name:** `merlos-cad-studio`
   - **Visibilidad:** ✅ **Private**
   - Sin README, sin .gitignore, sin licencia
3. Click **Create repository**
4. Copiar la URL: `https://github.com/jmerlos75/merlos-cad-studio.git`

---

### BLOQUE D — Crear la copia OSS limpia del proyecto

Abrir **Git Bash** y ejecutar:
```bash
cd "C:\Users\jmerl\OneDrive\Documentos"
xcopy /E /I /H "Estudio Merlos AI" "Estudio Merlos CAD OSS"
cd "Estudio Merlos CAD OSS"
rmdir /S /Q .git
git init
git branch -M main
```

---

### BLOQUE E — Aplicar los recortes al proyecto OSS

Este es el paso más largo. Abrir el archivo:
```
C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos CAD OSS\docs\separacion-opensource.md
```
Y ejecutar todos los pasos del **Orden de trabajo (Pasos 1–21)** usando el editor de código (VS Code o Cursor).

El asistente debe ayudar a editar `engine.py` directamente — es el archivo principal a modificar.

Al terminar, verificar que el CAD abre:
```bash
cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos CAD OSS"
python main.py
```

---

### BLOQUE F — Crear .gitignore y settings.default.json

En la carpeta `Estudio Merlos CAD OSS`, crear `.gitignore`:
```
__pycache__/
*.pyc
*.pyo
.env
settings.json
freeze_log.jsonl
recovery/
logs/
dist/
build/
*.spec
.venv/
venv/
*.key
*.pem
```

Crear `settings.default.json`:
```json
{
  "rendering": { "backend": "auto" },
  "snap": { "endpoint": true, "midpoint": true, "center": true, "intersection": true },
  "units": "mm",
  "language": "es"
}
```

---

### BLOQUE G — Verificación de seguridad antes de subir

En Git Bash, desde la carpeta OSS:
```bash
grep -r "sk-\|ghp_\|api_key\|merlosv@" cad/ --include="*.py"
grep -r "adip\|_build_perf\|_watchdog\|_ejecutar_ia" cad/engine.py
```
Ambos deben retornar **sin output**. Si hay líneas → limpiarlas primero.

---

### BLOQUE H — Primer commit y push OSS

```bash
cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos CAD OSS"
git add .
git status
git commit -m "Initial release — Estudio Merlos CAD v1.0 open source"
git remote add origin https://github.com/jmerlos75/merlos-cad.git
git push -u origin main
```
Cuando pida contraseña: pegar el token `ghp_...` (NO la contraseña de GitHub).

---

### BLOQUE I — Push del repo privado comercial

```bash
cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI"
git init
git branch -M main
git add .
git commit -m "Initial private commit — Estudio Merlos CAD Studio"
git remote add origin https://github.com/jmerlos75/merlos-cad-studio.git
git push -u origin main
```
Misma contraseña: token `ghp_...`

---

### BLOQUE J — Verificar en GitHub

1. Abrir `https://github.com/jmerlos75/merlos-cad` → debe mostrar los archivos públicos
2. Abrir `https://github.com/jmerlos75/merlos-cad-studio` → debe mostrar "Private"

---

## INSTRUCCIONES PARA EL ASISTENTE DEL NUEVO HILO

- Tomar control del computador cuando sea necesario (usar computer-use)
- Ejecutar los comandos de Git Bash directamente
- Editar engine.py directamente con las herramientas de edición de archivos
- No solo dar instrucciones — hacer los pasos junto con el usuario
- Si algo falla, diagnosticar y corregir antes de continuar
- El token del usuario está guardado en su máquina — pedirle que lo pegue cuando Git lo solicite
- Referirse siempre al documento `separacion-opensource.md` para saber exactamente qué cortar
- La carpeta del proyecto OSS es: `C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos CAD OSS`
- La carpeta del proyecto privado es: `C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI`
