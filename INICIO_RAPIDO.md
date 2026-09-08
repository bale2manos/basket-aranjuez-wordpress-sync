# Inicio rapido para WordPress Sync

## Una vez por ordenador

1. Instala Python 3.11 o superior.
2. Descarga el ZIP de la release y descomprimelo.
3. Ejecuta `install_windows.bat`.
4. Copia `config.py.example` como `config.py` y completa `config/runtime.yaml` y `config/teams.yaml`.
5. Coloca el escudo en `resultados/fotos/basket_aranjuez.png`.

## Cada jornada

1. Copia el Word en `resultados/`.
2. Si hay fotos, activa `photos_zip_available: true` y escribe la ruta del ZIP en `config/runtime.yaml`.
3. Revisa las URLs de las entradas en `section_entry_urls` o en `config.py`.
4. Ejecuta `wordpress_sync.bat auth-login` una vez si la sesion no esta guardada.
5. Ejecuta primero:

   `wordpress_sync.bat dry-run-all --docx "resultados\Cronicas.docx"`

6. Si el resumen es correcto, ejecuta:

   `wordpress_sync.bat update-all --docx "resultados\Cronicas.docx"`

La herramienta guarda borrador o actualizacion para revision, pero nunca pulsa Publicar automaticamente. Los logs, capturas, estados de sesion y documentos semanales quedan fuera del repositorio.
