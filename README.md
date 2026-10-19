# Basket Aranjuez: cronicas WordPress 2.0

Pasa las cronicas de un Word y las fotos de un ZIP a las entradas semanales de Elementor. Incluye el perfil de equipos de la temporada 2026/27.

**La herramienta guarda borradores. La publicacion se hace manualmente en WordPress.** Las entradas publicadas estan protegidas y no se abren para editar.

## Primera instalacion en Windows

1. Descarga el codigo de este repositorio y descomprimelo en una carpeta fija.
2. Instala Python 3.11 o superior si no lo tienes. En su instalador, marca Add Python to PATH.
3. Haz doble clic en **install_windows.bat**. Espera a que termine de instalar los componentes y Chromium.
4. Haz doble clic en **wordpress_sync.bat**. Se abrira la aplicacion.
5. Pulsa **Iniciar sesion**. Introduce tu cuenta en el navegador de WordPress.
6. Espera a ver el panel de WordPress y pulsa **Guardar sesion** en la aplicacion. Si se guarda automaticamente, no necesitas pulsarlo.

La sesion queda en tu ordenador. Si WordPress vuelve a pedir la cuenta otro dia, repite los pasos 5 y 6. Las releases 1.x son anteriores a esta version: para usar la 2.0, utiliza el codigo de esta rama.

## Que necesitas cada semana

- El archivo Word con las cronicas, en formato .docx.
- El ZIP de fotos, si esta semana hay fotos nuevas.
- Las entradas de esa jornada ya creadas como borradores en WordPress, con los bloques de los equipos de esta temporada.

El Word puede incluir todas las categorias juntas. No tienes que separarlo por equipos ni por secciones.

## Proceso semanal: paso a paso

1. Abre **wordpress_sync.bat**.
2. En **Word de cronicas**, pulsa Buscar y selecciona el .docx. La fecha de la jornada se obtiene de su cabecera cuando tiene el formato habitual.
3. Si hay fotos, marca **Hay ZIP de fotos esta semana** y selecciona el ZIP. Si no hay, desmarca la casilla. El equipo sin foto recibira el escudo del club.
4. Comprueba la fecha del sabado. Por ejemplo, para el fin de semana del 17 y 18 de octubre de 2026 debe decir **2026-10-17**.
5. Pulsa **Buscar borradores de esta jornada**. Se rellenaran las URLs de las entradas encontradas. Las secciones publicadas quedan desmarcadas. Si hay dos borradores posibles para una seccion, se indica el problema para que no se elija uno al azar.
6. Marca solo las secciones que quieres preparar. Si una cronica ya esta terminada pero sigue en borrador, desmarcala tambien.
7. Pulsa **Comprobar y actualizar la jornada**. La aplicacion analizara el Word primero. Si hay errores de mapeo, se detiene antes de editar.
8. Revisa el resumen y confirma la actualizacion. Mantiene abiertos sus propios navegadores mientras trabaja; espera al mensaje final.
9. Abre las entradas en WordPress y revisa resultados, parciales, textos, fotos y equipos ausentes. Publica manualmente cuando todo sea correcto.

Tambien puedes usar **Comprobar dry-run** para ver el plan sin editar. Despues puedes pulsar **Actualizar WordPress**. Si cambias el Word, las fotos, las URLs o las secciones, hay que comprobar otra vez.

## Que hace la comprobacion del Word

El parser lee las cronicas, conserva los saltos de linea de las cabeceras y extrae equipo, competicion, grupo, resultado, parciales y texto. Admite resultados con ambos tanteos juntos o con cada tanteo junto al nombre de su equipo.

Despues compara cada bloque con las reglas de **config/seasons/2026-27/teams.yaml**. En esta temporada la identidad se resuelve por cabecera y competicion/grupo, no por el orden del documento. Dos partidos del mismo equipo se agrupan para incluir las dos cronicas.

El resumen indica que equipos se actualizaran, cuales faltan y que bloques pertenecen a otras secciones. Un equipo que no encaja en ninguna regla genera un error: no se adivina su identidad. Si cambia un grupo al comenzar otra fase, revisa el aviso y ajusta la regla de ese equipo antes de ejecutar.

## Como se actualiza Elementor

- Cada bloque se localiza por su nombre o un alias especifico de la plantilla.
- Los nombres del marcador se muestran como Basket Aranjuez para el club; los rivales conservan sus siglas y letras en mayusculas.
- Senior y las demas categorias tienen cuatro periodos; Alevin tiene seis. Los datos ausentes se representan con guiones.
- El tanteo ganador de cada periodo se pone en negrita. Un empate no destaca ninguno.
- Se conserva el roster existente. La cronica se sustituye por la nueva o por un guion cuando no hay texto.
- Las fotos del equipo usan un widget identificado, separado del patrocinador. El escudo ya subido se reutiliza, incluso si WordPress le ha puesto un sufijo numerico.
- Los equipos ausentes se eliminan con todos sus contenedores relacionados, no solo vaciando el titulo.
- Un fallo durante la seccion bloquea su guardado, para facilitar la revision.

## Configuracion y archivos

La aplicacion guarda tus rutas y URLs en **configuracion.yaml**. Normalmente no hace falta editar archivos a mano. Puedes usar **configuracion.example.yaml** como ejemplo.

- **config/seasons/2026-27/teams.yaml**: equipos y reglas de esta temporada.
- **config/runtime.yaml**: periodos, tiempos de espera, sitio y rutas.
- **config/selectors.yaml**: controles de Elementor.
- **resultados/fotos/basket_aranjuez.png**: escudo local que se sube si no existe en la biblioteca.
- **logs/**: actividad y errores de cada ejecucion.
- **output/wordpress_sync/**: planes, informes y comprobaciones.
- **screenshots/**: capturas por equipo y captura final.

No compartas el archivo de sesion de output/wordpress_sync/state ni tu configuracion local. No contienen codigo necesario para instalar el programa en otro equipo.

## Si algo falla

- **Pide login:** inicia sesion de nuevo y guardala cuando veas el panel de WordPress.
- **No encuentra borradores:** revisa la fecha y los titulos de las entradas; deben identificar la seccion y el fin de semana con su ano. Las entradas publicadas se omiten.
- **Mapeo ambiguo o grupo nuevo:** revisa el Word y las reglas del equipo. No confirmes una asignacion incorrecta.
- **Falta el ZIP:** desmarca la casilla de fotos; se usara el escudo.
- **Error de Elementor:** conserva los logs y las capturas. Revisa la entrada antes de publicar y corrige la causa antes de repetir.

## Consola y desarrollo

Desde la carpeta del proyecto, con el entorno de Python activado:

~~~powershell
python wordpress_sync.py gui
python wordpress_sync.py auth-login --site-key basket-aranjuez
python wordpress_sync.py discover-week --weekend 2026-10-10
python wordpress_sync.py dry-run-all --docx "ruta/al/Word.docx"
python wordpress_sync.py update-all --docx "ruta/al/Word.docx" --sections alevin senior
python -m unittest discover -s tests -p "test_wordpress_sync*.py" -q
~~~

update-all usa las URLs guardadas en configuracion.yaml. La ventana permite rellenarlas automaticamente con Buscar borradores.

Las pruebas reales estan en scripts/test_wordpress_v2.py: crean o reutilizan exclusivamente una copia PRUEBA V2 en borrador, basada en una entrada anterior al 17 de octubre de 2026. Verifican los datos guardados y que el original no haya cambiado. No publican.
