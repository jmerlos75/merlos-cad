# Estudio Merlos AI

Aplicacion de escritorio para el Estudio de Arquitectura Joseph Merlos.
Conecta con AutoCAD via MCP y utiliza Claude AI para generar plantas arquitectonicas.

## Instalacion

```bash
cd "C:\Users\jmerl\OneDrive\Documentos\Estudio Merlos AI"
pip install -r requirements.txt
```

## Uso

```bash
python app.py
```

O usar el acceso directo "Estudio Merlos AI" en el escritorio.

## Configuracion

Al iniciar por primera vez, la app pedira la API key de Anthropic.
Tambien se puede configurar manualmente en `config/settings.json`.

## Requisitos

- Python 3.10+
- AutoCAD instalado
- MCP AutoCAD corriendo en equipo de agentes
- API key de Anthropic

## Modulos

- **AutoCAD**: Generacion de plantas, muros, puertas, ventanas, cotas
- **Revit**: Proximamente
- **Presupuestos**: Proximamente
