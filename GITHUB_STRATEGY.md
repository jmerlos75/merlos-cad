# Estrategia GitHub — Open Core

## Dos repositorios

### Repo 1 — PÚBLICO (este repositorio)
`github.com/jmerlosv/merlos-cad`

Contiene el núcleo técnico: el motor CAD, el sistema de snaps, el DYN input.
Licencia: AGPL-3.0

```
merlos-cad/
├── README.md                   ← portada pública (ya creada en cad/)
├── LICENSE                     ← AGPL-3.0
├── CONTRIBUTING.md             ← cómo contribuir
├── requirements.txt            ← solo: customtkinter
├── cad_viewer.py               ← app standalone mínima
└── cad/
    ├── engine.py               ← motor CAD completo
    ├── dxf_export.py           ← exportar DXF
    └── assets/
        └── icon.ico
```

Qué NO va en este repo:
  - modulos/ia/          (proveedores de IA)
  - modulos/autocad/     (puente MCP con AutoCAD)
  - modulos/diseno/      (validador INVU/CFIA)
  - app.py               (app completa con todos los módulos)
  - sle/                 (formato propietario SLE)
  - config/              (llaves de API, configuraciones)
  - cualquier .env o credenciales

---

### Repo 2 — PRIVADO (nunca publicar)
`github.com/jmerlosv/merlos-studio-pro` (privado)

Contiene todo lo comercial. Depende del repo público como submódulo o paquete.

```
merlos-studio-pro/
├── app.py                       ← app completa Pro
├── main.py                      ← launcher con licencias
├── modulos/
│   ├── autocad/
│   │   ├── conector.py          ← puente MCP AutoCAD ($ $ $)
│   │   └── ejecutor.py
│   ├── ia/
│   │   └── proveedores.py       ← Claude, GPT, Gemini
│   └── diseno/
│       ├── validador.py         ← cumplimiento INVU/CFIA
│       ├── visualizador.py
│       └── grid.py
├── sle/                         ← formato propietario
├── config/                      ← configuración, llaves
└── licencias/                   ← sistema de licencias Pro
```

---

## Pasos para publicar el repo público

### 1. Crear el repositorio en GitHub
```
Nombre:      merlos-cad
Descripción: Modern AI-ready CAD engine for architects. Built in Python.
Visibilidad: Public
Licencia:    AGPL-3.0
Topics:      cad, architecture, python, autocad, ai, latam
```

### 2. Preparar los archivos
Copiar solo estos archivos a una carpeta limpia:
- cad/engine.py
- cad/dxf_export.py
- cad/__init__.py
- cad_viewer.py
- requirements.txt
- cad/README.md  (mover a raíz como README.md)
- assets/estudio_merlos_ai.ico (renombrar a assets/icon.ico)

### 3. Revisar engine.py antes de publicar
Buscar y eliminar/reemplazar:
- Cualquier clave de API hardcodeada
- Rutas absolutas con nombre de usuario (C:\Users\jmerl\...)
- Referencias a módulos privados (modulos.ia, modulos.autocad)
- Credenciales o tokens

Comando de verificación:
  grep -n "api_key\|token\|password\|C:\\Users\\jmerl" cad/engine.py

### 4. Crear LICENSE
Descargar el texto de AGPL-3.0 de:
https://www.gnu.org/licenses/agpl-3.0.txt
Guardarlo como LICENSE (sin extensión) en la raíz.

### 5. Crear CONTRIBUTING.md (mínimo)
```
# Contributing

1. Fork the repo
2. Create a branch: git checkout -b feature/my-feature
3. Make changes and run: python -m py_compile cad/engine.py
4. Open a Pull Request

For major changes, open an issue first to discuss.
```

### 6. Primer commit y push
```bash
git init
git add .
git commit -m "Initial release: Merlos CAD core engine v0.1.0"
git remote add origin https://github.com/jmerlosv/merlos-cad.git
git push -u origin main
```

---

## Qué publicar en redes (día del lanzamiento)

### LinkedIn (para arquitectos y firmas)
---
Lancé Merlos CAD como open source 🚀

Un motor CAD para arquitectos hecho en Python, con:
→ Dynamic Input: escribís la distancia y ángulo directamente
→ 9 tipos de snap con código de colores
→ Normativa INVU/CFIA integrada
→ Puente con AutoCAD vía IA (versión Pro)

Desarrollado en Costa Rica para arquitectos de Centroamérica.

Libre y gratuito en su núcleo.
github.com/jmerlosv/merlos-cad

#Arquitectura #CAD #OpenSource #CostaRica #CFIA
---

### Grupos de WhatsApp / Facebook de arquitectos CR
---
Hola a todos,

Estoy desarrollando una herramienta CAD para arquitectos costarricenses
que funciona con IA. El núcleo es de acceso libre.

Lo que me diferencia de AutoCAD:
- Entrada dinámica: dibujás escribiendo los valores directamente en el lienzo
- Normativa CR integrada (INVU, CFIA, retiros)
- Asistente IA que entiende términos arquitectónicos en español

¿Alguien estaría interesado en probarlo gratis por 30 días a cambio de feedback?

Enlace: github.com/jmerlosv/merlos-cad
---

---

## Siguientes hitos después del lanzamiento

Semana 1: Publicar repo + post LinkedIn
Semana 2: 5 estudios usando la versión beta Pro gratis
Semana 3: Recopilar feedback → priorizar roadmap
Semana 4: Primera versión del instalador .exe (PyInstaller)
Mes 2:    Primer cliente de pago
Mes 3:    Video demo en YouTube + Product Hunt launch
Mes 6:    10 clientes recurrentes = $250/mes mínimo
```

---

## Notas de protección intelectual

El motor CAD (engine.py) bajo AGPL protege contra:
- Que una empresa lo tome, lo mejore y lo use sin devolver cambios
- SaaS que lo use sin publicar su código fuente

Lo que la licencia NO protege:
- Alguien que lo use internamente sin redistribuir
- Que estudien el código y escriban uno similar desde cero

El verdadero foso defensivo es:
1. El puente MCP con AutoCAD (privado, difícil de replicar)
2. El validador INVU/CFIA (requiere conocimiento local profundo)
3. La velocidad de iteración y soporte en español
4. La relación directa con los estudios de arquitectura locales

Eso no se copia con git clone.
