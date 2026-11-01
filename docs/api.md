# API del backend y cómo se guardan las fotos

Todas las respuestas son JSON y llevan `Access-Control-Allow-Origin: *`, así que el frontend las puede llamar desde otro dominio. Los errores tienen la forma `{"error": "mensaje para mostrar"}`.

## Variables de entorno

| Servicio | Variable | Para qué |
|---|---|---|
| backend | `BUCKET` | Nombre del bucket donde se guardan las fotos. Si está vacía, el backend responde `503` en todas las rutas |
| frontend | `BACKEND_URL` | URL del backend, que `server.py` escribe dentro de la página |

Cloud Run pone además `PORT`, que los dos servicios respetan.

## Endpoints

### `POST /fotos`

Formulario `multipart/form-data` con dos campos: `foto` (el archivo) y `autor` (texto, entre 1 y 40 caracteres).

El backend abre la imagen, corrige la orientación que traen las fotos de teléfono, la reduce a una miniatura de 1024 píxeles por su lado mayor, le pide etiquetas a Cloud Vision usando esa miniatura, y guarda en el bucket el original y la miniatura. Responde `201`:

```json
{
  "id": "20261026-185734-44f39d",
  "autor": "Laura",
  "creado": "2026-10-26T18:57:34.561251+00:00",
  "etiquetas": [
    {"nombre": "Cup", "confianza": 0.96},
    {"nombre": "Coffee cup", "confianza": 0.95}
  ],
  "vision": "ok",
  "miniatura": "/fotos/20261026-185734-44f39d/miniatura"
}
```

Si Vision no responde (por ejemplo, porque la API no está habilitada), la foto se guarda igual: `etiquetas` es `null`, `vision` trae el error resumido y el backend deja una advertencia en sus registros.

Errores: `400` si falta el nombre o la foto, o si el archivo no es una imagen; `413` si pesa más de 20 MB; `503` si no hay bucket.

### `GET /fotos`

Las últimas 24 fotos, de la más reciente a la más antigua, con la misma forma de arriba. Es un solo listado del bucket por llamada.

### `GET /fotos/<id>/miniatura`

La miniatura en JPEG, con `Cache-Control: public, max-age=86400` para que el navegador no la vuelva a pedir durante un día. `404` si no existe.

## Cómo quedan las fotos en el bucket

```
originales/20261026-185734-44f39d.jpeg    la foto tal cual llegó
miniaturas/20261026-185734-44f39d.jpg     1024 px, JPEG, con metadatos
```

Las "carpetas" `originales/` y `miniaturas/` no existen como tales: son parte del nombre de cada objeto, y la consola las dibuja a partir de la barra.

La miniatura lleva tres metadatos propios, escritos en la misma operación con que se sube:

| Metadato | Contenido |
|---|---|
| `autor` | El nombre que escribió quien subió la foto |
| `etiquetas` | Las etiquetas en JSON, o vacío si Vision no respondió |
| `vision` | `ok`, o `error: ...` con el motivo |

Los originales se guardan pero el backend nunca los sirve: lo único que sale hacia los navegadores son las miniaturas.
