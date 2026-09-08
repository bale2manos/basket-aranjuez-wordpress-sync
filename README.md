# Basket Aranjuez WordPress Sync

Herramienta local para pasar las cronicas semanales del club desde un Word y un ZIP de fotos a las entradas de WordPress editadas con Elementor.

La herramienta no crea entradas nuevas y no publica automaticamente. Edita las entradas que ya existen, guarda los cambios para revision y deja que una persona haga la publicacion final.

## La forma recomendada para Windows

Para una persona que solo quiere usar la herramienta, no hace falta conocer Python ni el codigo.

1. Entra en la pagina de releases de GitHub y descarga el ZIP mas reciente:
   https://github.com/bale2manos/basket-aranjuez-wordpress-sync/releases
2. Descomprime el ZIP completo en una carpeta local, por ejemplo:
   C:/BasketWordPressSync
3. No muevas ni borres las carpetas que hay dentro del programa. En especial, deben conservarse _internal y browsers.
4. Copia configuracion.example.yaml y llamalo configuracion.yaml.
5. Abre configuracion.yaml con el Bloc de notas y completa solo los datos de tu jornada.
6. Ejecuta BasketWordPressSync.exe.

Tambien puedes abrir wordpress_sync.bat y elegir la opcion de interfaz grafica. El resultado es el mismo.

## Configuracion semanal

Cada semana solo hay que cambiar estos datos en configuracion.yaml:

~~~yaml
docx_path: resultados/Cronicas de esta semana.docx
photos_zip_available: true
photos_zip_path: resultados/fotos/fotos.zip

entry_urls:
  senior: https://basketaranjuez.com/wp-admin/post.php?post=123&action=elementor
  alevin: https://basketaranjuez.com/wp-admin/post.php?post=124&action=elementor
~~~

### docx_path

Ruta del Word con todas las cronicas de la jornada. El documento puede contener categorias que no se van a actualizar; la herramienta las detecta, pero las ignora al elegir una seccion.

### photos_zip_available

Pon true si esta semana existe un ZIP nuevo de fotos y false si no existe.

- true: se buscan fotos nuevas para los equipos.
- false: no se intenta abrir ningun ZIP.
- Si un equipo no tiene foto, se usa basket_aranjuez.png.
- El logo se reutiliza durante la ejecucion y no se sube repetidamente.

### photos_zip_path

Ruta del ZIP de fotos. Solo se usa cuando photos_zip_available: true.

### entry_urls

URL de Elementor de cada entrada semanal ya creada. No son URLs de la web publica: deben terminar normalmente en:

~~~text
/wp-admin/post.php?post=ID&action=elementor
~~~

Si una seccion no se va a actualizar, dejala vacia o no la ejecutes.

## Proceso completo de cada semana

### 1. Preparar los archivos

Guarda en la carpeta indicada en la configuracion:

- El Word de cronicas.
- El ZIP de fotos, si existe.
- El logo basket_aranjuez.png, para usarlo como fallback.

Comprueba que el Word esta cerrado antes de iniciar la herramienta.

### 2. Iniciar sesion una vez

La primera vez, abre la aplicacion y pulsa Iniciar sesion. Se abrira Chromium. Haz el login de WordPress manualmente y, cuando Elementor o el panel de WordPress esten visibles, vuelve a la aplicacion y pulsa Guardar sesion.

La sesion se guarda localmente. En las semanas siguientes normalmente no hay que repetir el login. Si WordPress vuelve a pedir usuario y contrasena, repite este paso.

### 3. Guardar la configuracion

Pulsa Guardar configuracion y comprueba que las rutas y las URLs aparecen correctamente.

### 4. Ejecutar el dry-run

Pulsa Comprobar dry-run. Este paso no abre WordPress ni modifica nada.

El dry-run:

1. Lee todos los parrafos del Word en el orden original.
2. Detecta el comienzo de cada bloque por su encabezado.
3. Normaliza mayusculas, minusculas, tildes, espacios y separadores.
4. Extrae categoria, grupo, resultado, local, visitante, tanteos, jugadores y cronica.
5. Acepta partidos con 4, 5 o 6 periodos y avisa si falta algun tanteo.
6. Compara cada bloque con los alias de config/teams.yaml.
7. Filtra solo la seccion elegida: senior, alevin, infantil, cadete, junior o sub22.
8. Indica los equipos que se actualizaran.
9. Indica los equipos esperados que no aparecen y que se limpiaran.
10. Indica los equipos de otras categorias que se ignoraran.
11. Detalla errores de mapeo, resultados ambiguos o bloques incompletos.

No continues si el dry-run muestra un mapeo ambiguo o si los equipos no coinciden con lo esperado. Corrige el Word o la configuracion y repitelo.

### 5. Actualizar WordPress

En la aplicacion, selecciona la seccion y pulsa Actualizar WordPress.

La herramienta reutiliza el mismo navegador y procesa todos los equipos de esa seccion. Para cada equipo:

- Localiza el encabezado del equipo como ancla del bloque.
- Edita los widgets de Elementor uno por uno.
- Actualiza categoria, grupo, nombres de equipos, marcador y parciales.
- Mantiene la plantilla y el roster existente cuando corresponde.
- Actualiza la cronica y pone - si no hay cronica util.
- Pone en negrita el tanteo ganador de cada periodo.
- Usa la foto nueva del equipo o el escudo del club como fallback.
- Si un equipo no aparece en el Word, elimina todo su contenido relacionado, incluidos cronica, marcador, categoria, jugadores y foto, segun el modo configurado.

Si un equipo tiene dos partidos, se crean dos cronicas dentro de su bloque siempre que el Word los identifique como partidos distintos.

### 6. Revisar y publicar manualmente

La herramienta guarda la entrada para revision, pero no pulsa Publicar.

Abre la entrada en WordPress y revisa:

- Orden de los equipos.
- Nombre, categoria y grupo.
- Resultado y parciales.
- Negritas de los parciales ganadores.
- Cronicas y fotos.
- Equipos que no jugaron y han sido eliminados.

Cuando todo sea correcto, publica manualmente desde WordPress.

## Uso por consola

La interfaz grafica es la opcion recomendada. Para diagnostico o usuarios avanzados se puede usar PowerShell desde la carpeta del proyecto:

~~~powershell
./wordpress_sync.bat gui
~~~

Login manual:

~~~powershell
python wordpress_sync.py auth-login --site-key basket-aranjuez
~~~

Dry-run de una seccion:

~~~powershell
python wordpress_sync.py dry-run --docx "resultados/Cronicas.docx" --section infantil
~~~

Actualizacion de una seccion completa:

~~~powershell
python wordpress_sync.py update-section --docx "resultados/Cronicas.docx" --section infantil --entry-url "URL_DE_ELEMENTOR"
~~~

Actualizacion de senior y alevin en una sola ejecucion:

~~~powershell
python wordpress_sync.py update-weekend --docx "resultados/Cronicas.docx" --senior-entry-url "URL_DE_ELEMENTOR_SENIOR" --alevin-entry-url "URL_DE_ELEMENTOR_ALEVIN"
~~~

Para actualizar varias secciones con la configuracion central, usa la opcion de la interfaz grafica o el comando disponible en la version distribuida. Haz siempre primero el dry-run.

## Archivos importantes

No es necesario editar todos estos archivos cada semana:

- configuracion.yaml: configuracion sencilla para el usuario.
- config/teams.yaml: nombres, alias y orden de los equipos.
- config/runtime.yaml: tiempos, fotos, borrados y comportamiento avanzado.
- config/selectors.yaml: selectores de Elementor; solo se toca si cambia la interfaz de WordPress.
- config.py: compatibilidad con configuraciones antiguas; no es la opcion recomendada.

La configuracion unificada configuracion.yaml tiene prioridad para las rutas, URLs y opciones de uso diario.

## Seguridad y comportamiento conservador

- No se guardan contrasenas en YAML.
- La sesion de WordPress queda guardada localmente.
- No se modifica el HTML global del post.
- La edicion se hace widget a widget dentro de Elementor.
- Si un widget no se encuentra, se registra el error y se continua cuando es seguro.
- Si hay errores de equipo, la herramienta puede bloquear el guardado para evitar una entrada a medio actualizar.
- Se generan logs legibles, logs JSONL, un plan de ejecucion y capturas.
- Antes de editar se puede generar un backup de la configuracion y del plan.
- La publicacion final siempre requiere una accion manual.

## Donde mirar si algo falla

Cada ejecucion deja sus resultados en:

- output/: Word parseado y plan de ejecucion.
- logs/: log legible y log JSONL.
- screenshots/: capturas antes, despues y de errores.
- storage/: sesion local de Playwright.

### WordPress pide login otra vez

Ejecuta auth-login, haz el login manual y guarda la sesion. Despues repite el dry-run y la actualizacion.

### El dry-run dice que falta un equipo

Busca el bloque en el Word y revisa el nombre del equipo. Si el equipo aparece con un patrocinador o una variante de nombre, añade un alias en config/teams.yaml.

### El dry-run dice mapeo ambiguo

No actualices. Revisa el resultado, el encabezado y los alias. Añade un alias mas especifico o corrige el Word.

### No hay ZIP de fotos

Pon photos_zip_available: false. La herramienta no intentara abrir un ZIP inexistente y usara el escudo del club cuando no haya foto de equipo.

### Elementor no encuentra un widget

No publiques. Revisa la captura de error y los logs. Si ha cambiado la interfaz de Elementor, ajusta primero config/selectors.yaml.

### Una entrada queda a medio actualizar

No pulses publicar. Conserva la carpeta de output, logs y screenshots para diagnostico y vuelve a ejecutar despues de corregir el problema.

## Para desarrolladores

Si se quiere ejecutar desde el codigo fuente:

1. Instala Python 3.11 o superior.
2. Ejecuta install_windows.bat.
3. Copia configuracion.example.yaml a configuracion.yaml.
4. Ejecuta wordpress_sync.bat gui.

Los tests se ejecutan con:

~~~powershell
python -m unittest discover -s tests -q
~~~

La logica esta separada en parser, normalizador, mapper, planner y adaptadores de Elementor para que los cambios de WordPress no afecten al resto del proyecto.
