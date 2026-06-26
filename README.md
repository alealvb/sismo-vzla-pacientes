# Buscador de Pacientes — Sismo Venezuela 2026

Herramienta web para **localizar personas ingresadas en hospitales** tras el sismo.
Agrega listas públicas (Google Docs y PDFs) de una carpeta compartida de Google Drive,
las normaliza a un único índice buscable y publica una página estática con búsqueda
instantánea, tolerante a acentos y errores de escritura.

Un **job automático** revisa la carpeta cada ~5 minutos y, si hay cambios, regenera
los datos y republica el sitio.

## Cómo funciona

```
Carpeta pública de Drive
  ├─ Google Docs (REGISTRO MAESTRO: N° | Hospital | Nombre | Edad)
  └─ PDFs consolidados (NUM | Apellidos Nombres | CI | Edad | Sexo | Procedencia | Hospital)
                │
                ▼
     scripts/sync.py   ── lista la carpeta, exporta los Docs a texto,
                          descarga y extrae texto de los PDFs, parsea,
                          unifica nombres de hospital, deduplica y
                          genera  web/data.json
                │
                ▼
     web/index.html     ── buscador 100% en el navegador (sin backend)
```

- **Privacidad:** el número de cédula (CI) se usa solo para deduplicar y **nunca**
  se incluye en `web/data.json`. Se publican nombre, edad, sexo, procedencia y hospital.
- **Cambios:** cada corrida compara contra el `data.json` anterior y reporta altas/bajas.

## Estructura

| Ruta | Qué es |
|------|--------|
| `scripts/sync.py` | Ingesta + parseo + generación de `web/data.json` (solo stdlib + `pypdf`) |
| `web/index.html` | Página del buscador (HTML/CSS/JS en un solo archivo) |
| `web/data.json` | Índice generado (se versiona para tener historial de cambios) |
| `.github/workflows/sync.yml` | Job que sincroniza y publica en GitHub Pages |
| `requirements.txt` | Dependencias de Python (`pypdf`) |

## Uso local

```bash
pip install -r requirements.txt
python scripts/sync.py              # genera web/data.json
cd web && python3 -m http.server 8765
# abrir http://localhost:8765
```

## Despliegue (GitHub Pages + Actions)

1. Crear el repo y subir el código (ver comandos abajo).
2. En **Settings → Pages → Build and deployment → Source**, elegir **GitHub Actions**.
3. El workflow corre cada ~5 min (y se puede lanzar a mano desde la pestaña **Actions →
   Sincronizar y publicar → Run workflow**). En cada corrida: sincroniza, hace commit de
   `web/data.json` si cambió y republica el sitio.

> Nota: el cron de GitHub Actions tiene granularidad de ~5 min y puede retrasarse en
> momentos de alta carga de la plataforma. Es el límite práctico del plan gratuito.

## Aviso

Recopilación **no oficial** de listas públicas; puede contener errores u omisiones.
Que un nombre no aparezca **no** significa que la persona no esté ingresada.
Confirmar siempre directamente con el hospital.
