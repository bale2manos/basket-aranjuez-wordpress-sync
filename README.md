# Generador de jornadas Basket Aranjuez

Proyecto Python para leer un Excel de partidos y generar imagenes verticales 1080x1350 separadas por local y visitante, junto con `summary.json` y log de ejecucion.

## Uso

1. Instala dependencias:

```bash
python -m pip install -r requirements.txt
```

2. Revisa `config.py` y ajusta rutas si lo necesitas.

3. Ejecuta el modo que quieras:

```bash
python main.py
python main.py --mode horarios
python main.py --mode resultados --results-doc-path "F:\ruta\cronicas.docx"
```

Sin flags, el programa usa `config.py` como fuente principal.

## Modos

- `horarios`: comportamiento original. Genera tarjetas con `VS` en el bloque central.
- `resultados`: reutiliza el mismo layout y sustituye `VS` por el marcador `local-visitante`.

La salida se organiza por jornada y por modo:

- `output/jornada_YYYY-MM-DD/horarios/`
- `output/jornada_YYYY-MM-DD/resultados/`

## Resultados y overrides

- El modo `resultados` lee un `.docx` desde `results_doc_path` o `--results-doc-path`.
- Si un partido no encuentra marcador en el documento, no se renderiza en esa pasada.
- Los faltantes se escriben en `data/result_overrides.json` para que puedas completar manualmente el campo `result` y relanzar el generador.
- `summary.json` incluye `render_mode`, `result_text`, `result_source` y las rutas usadas para el documento y los overrides.

## Estructura

- `config.py`: configuracion centralizada.
- `main.py`: entrada principal y CLI.
- `src/generador_horarios/`: lectura, normalizacion, resultados, render y pipeline.
- `data/club_logo_map.csv`: mapa editable de nombres de club a logo.
- `data/venue_map.csv`: mapa editable de sedes.
- `data/result_overrides.json`: resultados pendientes o corregidos manualmente.
- `output/`: salidas generadas por jornada.

## Notas

- Si faltan `club_logo_map.csv` o `venue_map.csv`, el proyecto los genera automaticamente con propuestas iniciales basadas en el Excel.
- Si falta un logo rival, el hueco se deja vacio y se registra warning sin interrumpir la ejecucion.
- Si falta el logo fijo de Basket Aranjuez, el render sigue adelante dejando ese hueco vacio.


## WordPress Sync v1

Nueva herramienta local para preparar y automatizar la edici?n semanal en WordPress/Elementor sin tocar el flujo existente de im?genes.

### Comandos

```bash
python wordpress_sync.py auth-login
python wordpress_sync.py dry-run --section senior
python wordpress_sync.py dry-run-all
python wordpress_sync.py update-block --section senior --team "Senior Femenino A"
python wordpress_sync.py update-section --section senior
python wordpress_sync.py update-all
```

Todos los comandos aceptan `--docx`, `--entry-url` o `--site-key` como override puntual, pero si no se pasan toman los valores por defecto desde `config.py`.

Antes del primer uso real de Playwright, instala Chromium:

```bash
python -m playwright install chromium
```

### Configuración

- `config/teams.yaml`: mapeo de equipos y aliases por secci?n.
- `config/selectors.yaml`: selectores ajustables de WordPress/Elementor.
- `config/runtime.yaml`: navegador, sesi?n, rutas de artefactos y pol?ticas por defecto.
- `config.py`: DOCX por defecto, `site_key`, orden de secciones y `entry_url` por secci?n.

### Artefactos

- `logs/`: log legible y JSONL por ejecuci?n.
- `output/wordpress_sync/`: `parsed_chronicles.json` y `run_plan.json`.
- `screenshots/`: capturas antes/despu?s y capturas de error.

### Notas de la v1

- El login se hace manualmente una vez y se reutiliza la sesi?n guardada.
- La herramienta ya contempla las secciones `alevin`, `infantil_femenino`, `infantil_masculino`, `cadete`, `junior`, `sub22` y `senior`.
- Se actualizan widgets `Encabezado` y `Editor de texto`; no se publica autom?ticamente.

## Distribucion para terceros

La version distribuible incluye `install_windows.bat`, que crea el entorno virtual, instala Python dependencies y descarga Chromium. Despues se puede usar `wordpress_sync.bat` sin activar manualmente el entorno.

Para una instalacion nueva:

1. Ejecuta `install_windows.bat`.
2. Copia `config.py.example` como `config.py`.
3. Revisa `config/runtime.yaml`, `config/teams.yaml` y las URLs de las entradas.
4. Ejecuta `wordpress_sync.bat auth-login` y completa el login en el navegador.
5. Ejecuta primero `wordpress_sync.bat dry-run-all --docx "resultados\\Cronicas.docx"`.
6. Si el resumen es correcto, ejecuta `wordpress_sync.bat update-all --docx "resultados\\Cronicas.docx"`.

Los documentos, fotos, logs y sesiones de WordPress son datos locales y no forman parte de la distribucion publica. Consulta [INICIO_RAPIDO.md](INICIO_RAPIDO.md) para el flujo semanal.
