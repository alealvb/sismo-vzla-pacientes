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
                          genera  docs/data.json
                │
                ▼
     docs/index.html    ── buscador 100% en el navegador (sin backend)
```

- **Privacidad:** el número de cédula (CI) se usa solo para deduplicar y **nunca**
  se incluye en `docs/data.json`. Se publican nombre, edad, sexo, procedencia y hospital.
- **Cambios:** cada corrida compara contra el `data.json` anterior y reporta altas/bajas.

## Estructura

| Ruta | Qué es |
|------|--------|
| `scripts/sync.py` | Ingesta + parseo + generación de `docs/data.json` (solo stdlib + `pypdf`) |
| `docs/index.html` | Página del buscador (HTML/CSS/JS en un solo archivo) |
| `docs/data.json` | Índice generado (se versiona para tener historial de cambios) |
| `.github/workflows/sync.yml` | Job que sincroniza los datos cada ~5 min |
| `requirements.txt` | Dependencias de Python (`pypdf`) |

## Uso local

```bash
pip install -r requirements.txt
python scripts/sync.py              # genera docs/data.json
cd docs && python3 -m http.server 8765
# abrir http://localhost:8765
```

## Despliegue

**Sitio (GitHub Pages, sin Actions):** en **Settings → Pages → Build and deployment**,
elegir **Deploy from a branch**, rama `main`, carpeta `/docs`. El sitio queda en
`https://<usuario>.github.io/<repo>/` y se republica solo en cada push a `docs/`.
No requiere GitHub Actions ni facturación.

**Actualización automática (job):** el workflow `sync.yml` corre cada ~5 min (y a mano
desde **Actions → Sincronizar datos → Run workflow**), regenera `docs/data.json` y lo
commitea si cambió; Pages republica solo.

> Requiere que **GitHub Actions** esté habilitado en la cuenta. Si la cuenta tiene un
> bloqueo de facturación, los runs fallan con *"account is locked due to a billing issue"*;
> hay que resolverlo en **Settings → Billing**. Mientras tanto, se puede correr
> `python scripts/sync.py` localmente (o por `cron`) y hacer `git push` para actualizar.

> Nota: el cron de GitHub Actions tiene granularidad de ~5 min y puede retrasarse en
> momentos de alta carga de la plataforma.

## Aviso

Recopilación **no oficial** de listas públicas; puede contener errores u omisiones.
Que un nombre no aparezca **no** significa que la persona no esté ingresada.
Confirmar siempre directamente con el hospital.
