# Inicio rapido para WordPress Sync

## Una vez por ordenador

1. Instala Python 3.11 o superior.
2. Descarga el ZIP de la release y descomprimelo.
3. Ejecuta `install_windows.bat`.
4. Copia `configuracion.example.yaml` como `configuracion.yaml` y completa sus rutas y URLs. Solo usa `config/runtime.yaml`, `config/teams.yaml` y `config/selectors.yaml` si necesitas ajustes avanzados.
5. Coloca el escudo en `resultados/fotos/basket_aranjuez.png`.

## Cada jornada

1. Copia el Word en `resultados/`.
2. Si hay fotos, activa `photos_zip_available: true` y escribe la ruta del ZIP en `config/runtime.yaml`.
3. Revisa las URLs de las entradas en `section_entry_urls` o en `config.py`.
4. Abre `BasketWordPressSync.exe` si has descargado el ejecutable, o ejecuta `wordpress_sync.bat gui` desde el codigo fuente.
5. Pulsa `Iniciar sesion` y, cuando termines en el navegador, `Guardar sesion`.
6. Ejecuta primero:

   `wordpress_sync.bat dry-run-all --docx "resultados\Cronicas.docx"`

7. Si el resumen es correcto, pulsa `Actualizar WordPress` o ejecuta:

   `wordpress_sync.bat update-all --docx "resultados\Cronicas.docx"`

La herramienta guarda borrador o actualizacion para revision, pero nunca pulsa Publicar automaticamente. Antes de actualizar crea una copia de la configuracion y, si falla un equipo, bloquea el guardado de toda la seccion. Los logs, capturas, estados de sesion y documentos semanales quedan fuera del repositorio.
