# GitHub — Setup completo OSS + Proyecto comercial privado
> Pasos en orden. Ejecutar uno a uno sin saltarse ninguno.

---

## PARTE 1 — Prerrequisitos (una sola vez)

### 1.1 Crear cuenta GitHub
1. Ir a https://github.com
2. Click **Sign up**
3. Usar email: `merlosv@hotmail.com`
4. Elegir username — recomendado: `estudio-merlos` o `jmerlos-arch`
5. Plan: **Free** es suficiente (repositorios privados incluidos)
6. Verificar email

### 1.2 Verificar que Git está instalado
```bash
git --version
# Si no retorna nada: descargar desde https://git-scm.com/download/win
```

### 1.3 Configurar Git con tu identidad (una sola vez)
```bash
git config --global user.name "Joseph Merlos"
git config --global user.email "merlosv@hotmail.com"
```

### 1.4 Autenticación GitHub — Personal Access Token
GitHub ya no acepta contraseñas. Se usa un token:
1. GitHub → tu foto de perfil → **Settings**
2. Scroll hasta abajo → **Developer settings**
3. **Personal access tokens** → **Tokens (classic)**
4. **Generate new token (classic)**
5. Note: `CAD push token`
6. Expiration: `No expiration` (o 1 año)
7. Scopes: marcar **repo** (incluye todo lo necesario)
8. **Generate token** → copiar el token (empieza con `ghp_...`)
9. **Guardar el token** en un lugar seguro — solo se muestra una vez

---

## PARTE 2 — Crear la copia OSS limpia

### 2.1 Duplicar el proyecto (sin historial)
```bash
# Ir al directorio padre
cd "C:\Users\jmerl\OneDrive\Documentos"

# Copiar carpeta completa
xcopy /E /I /H "Estudio Merlos AI" "Estudio Merlos CAD OSS"

# Entrar a la copia
cd "Estudio Merlos CAD OSS"
```

### 2.2 Eliminar historial git anterior (si existe)
```bash
# En la carpeta "Estudio Merlos CAD OSS":
rmdir /S /Q .git

# Iniciar repo limpio
git init
git branch -M main
```

### 2.3 Aplicar los recortes del documento separacion-opensource.md
Ejecutar todos los pasos del documento `docs/separacion-opensource.md` (Secciones 3 a 12).

Al terminar, verificar que el CAD abre correctamente:
```bash
python main.py
```

### 2.4 Verificación de credenciales antes de subir
```bash
# Buscar API keys, rutas personales, emails
grep -r "sk-\|ghp_\|api_key\|merlosv@\|C:\\Users\\jmerl" cad/ --include="*.py"
grep -r "adip\|ADIP\|_build_perf\|_watchdog\|_ejecutar_ia" cad/engine.py

# Resultado esperado: sin output en ambos
```

### 2.5 Crear .gitignore
Crear el archivo `.gitignore` en la raíz del proyecto OSS:
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

### 2.6 Crear settings.default.json
```json
{
  "rendering": { "backend": "auto" },
  "snap": {
    "endpoint": true,
    "midpoint": true,
    "center": true,
    "intersection": true,
    "perpendicular": true
  },
  "units": "mm",
  "language": "es"
}
```

### 2.7 Primer commit OSS
```bash
# Desde la carpeta "Estudio Merlos CAD OSS"
git add .
git status
# Revisar que no aparezca settings.json, freeze_log.jsonl ni archivos sensibles

git commit -m "Initial release — Estudio Merlos CAD v1.0 open source"
```

---

## PARTE 3 — Crear repositorio OSS en GitHub

### 3.1 Crear el repo en GitHub
1. GitHub → **+** (esquina superior derecha) → **New repository**
2. Repository name: `merlos-cad`  
   *(o `estudio-merlos-cad` — debe ser todo minúsculas, sin espacios)*
3. Description: `Open source CAD desktop with OpenGL renderer. AutoCAD-compatible commands, snap, grips, layers, DXF import/export.`
4. Visibilidad: **Public**
5. **NO** marcar "Add a README" (ya tienes el tuyo)
6. **NO** marcar .gitignore (ya tienes el tuyo)
7. License: **MIT License** ← marcar esto
8. Click **Create repository**

### 3.2 Conectar y subir
```bash
# Desde la carpeta "Estudio Merlos CAD OSS"
# Reemplazar TU_USERNAME con tu username de GitHub
git remote add origin https://github.com/TU_USERNAME/merlos-cad.git

# Subir
git push -u origin main
```

Cuando pida contraseña: pegar el token `ghp_...` del Paso 1.4 (no tu contraseña de GitHub).

### 3.3 Verificar en GitHub
Abrir `https://github.com/TU_USERNAME/merlos-cad` en el navegador.
Verificar que aparecen los archivos y que el README se muestra.

---

## PARTE 4 — Mantener el proyecto comercial privado

### 4.1 El proyecto original ("Estudio Merlos AI") sigue en tu máquina
No se toca. Sigue siendo tu repo privado de desarrollo.

### 4.2 Crear repo privado en GitHub (opcional, para backup)
1. GitHub → **+** → **New repository**
2. Name: `merlos-cad-studio` (o el nombre que prefieras)
3. Visibilidad: **Private** ← importante
4. **Create repository**

```bash
# Desde la carpeta original "Estudio Merlos AI"
cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI"

# Si no tiene git aún:
git init
git branch -M main

git remote add origin https://github.com/TU_USERNAME/merlos-cad-studio.git
git add .
git commit -m "Initial private commit"
git push -u origin main
```

---

## PARTE 5 — Workflow después del setup

### Actualizar el OSS cuando haya mejoras en el core
```bash
# 1. Hacer el fix/mejora en el proyecto privado ("Estudio Merlos AI")
# 2. Copiar SOLO el archivo modificado a la copia OSS
#    Ejemplo: si se mejoró renderer_opengl.py
copy "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI\cad\renderer_opengl.py" ^
     "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos CAD OSS\cad\renderer_opengl.py"

# 3. En la carpeta OSS: commit y push
cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos CAD OSS"
git add cad/renderer_opengl.py
git commit -m "perf: improve tessellation cache hit rate"
git push
```

### Convención de commit messages para el OSS
```
feat: nueva funcionalidad
fix:  corrección de bug
perf: mejora de rendimiento
docs: cambio en documentación
refactor: reorganización sin cambio de funcionalidad
```

---

## Resumen de URLs finales

| Qué | URL |
|---|---|
| Repo OSS público | `https://github.com/TU_USERNAME/merlos-cad` |
| Repo privado comercial | `https://github.com/TU_USERNAME/merlos-cad-studio` |
| Carpeta OSS local | `C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos CAD OSS` |
| Carpeta comercial local | `C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI` |
