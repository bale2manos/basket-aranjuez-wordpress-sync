# Version 2.0: verificacion de la temporada 2026/27

Estado: implementada y verificada el 19 de octubre de 2026. La ejecucion guarda borradores y protege las entradas publicadas.

## Temporada y documento

Se contrastaron los equipos con las publicaciones, las etiquetas de temporada y las vistas previas privadas de las siete secciones. El perfil tiene 34 equipos y esta separado del perfil historico de 2025/26.

El Word del 17-18 de octubre contiene 34 partidos: se asignaron sin ambiguedades a 32 equipos. Rubiocar y Junior Masculino B tienen dos partidos; Infantil Femenino de segundo ano Blanco y Negro no aparecen. El Word se utilizo como dato de entrada, sin modificar las entradas de esa jornada.

Se corrigieron cabeceras con saltos de linea, tanteos junto a cada nombre de equipo, separadores // y periodos vacios escritos como - -.

## Pruebas reales sin publicar

Solo se editaron copias con titulo PRUEBA V2, creadas como borradores independientes de entradas anteriores al 17 de octubre.

- Alevin del 10-11 de octubre, original 7985: se actualizaron cuatro bloques en la copia 8197 y tres en la copia 8207. La comprobacion leyo los valores realmente guardados de categoria, grupo, equipos, marcador, seis parciales y cronica. Verifico tambien fotos y conservacion del roster.
- En la copia 8207 se elimino el bloque completo de un equipo: desaparecieron sus 34 widgets, incluidos categoria, grupo, marcador, roster, cronica y patrocinador del bloque ausente. Los widgets de los otros equipos quedaron intactos. Despues se verifico otra actualizacion de los tres equipos restantes.
- Junior del 10-11 de octubre, original 8040: se actualizaron tres bloques en la copia 8214, con cuatro periodos. Se preparo un encabezado de esa copia para representar a Junior Masculino B y comprobar que sus dos partidos quedaban en la cronica guardada.
- Las verificaciones finales de las copias dieron cero errores y confirmaron estado draft. Las huellas de contenido y metadatos de las entradas originales no cambiaron.

Hubo fallos intermedios de prueba que permitieron corregir la busqueda de fotos fuera del documento y el borrado basado en geometria. El borrado actual utiliza el orden estructural de los contenedores de Elementor, entre el encabezado del equipo y el siguiente equipo.

Los informes completos y las capturas se conservan localmente en output/wordpress_sync y screenshots. No se incluyen sesiones, documentos Word ni datos privados en GitHub.

## Pruebas automaticas

85 pruebas de WordPress pasan en el espacio de trabajo y en una copia limpia del repositorio publico. Incluyen parser, mapeo, plan, fotos, sesiones, destinos protegidos y adaptadores existentes.

Las pruebas de fotos usan tambien ZIP sinteticos para comprobar seleccion fuera de la carpeta de fotos, nombres abreviados y rechazo de fotos sueltas o ZIP de semanas anteriores. La prueba real en WordPress verifica el escudo; no se utilizo un ZIP nuevo de fotos de esta jornada.

## Uso

Abre wordpress_sync.bat, selecciona el Word y el ZIP opcional, busca los borradores de la fecha detectada, marca las secciones y pulsa Comprobar y actualizar la jornada. Revisa y publica manualmente en WordPress.

Si una fase nueva cambia los grupos y un bloque deja de corresponder a una regla, el mapeo se detiene con error. Ajusta esa regla en config/seasons/2026-27/teams.yaml; no se asignan equipos por su posicion.

Esta entrega actualiza el codigo fuente. Los ejecutables de las releases 1.x corresponden a versiones anteriores.
