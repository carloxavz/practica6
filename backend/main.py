import io
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from functools import cache

from flask import Flask, Response, jsonify, request
from google.cloud import storage, vision
from PIL import Image, ImageOps, UnidentifiedImageError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("backend")

BUCKET = os.environ.get("BUCKET", "")

# Las fotos de un teléfono pesan varios MB. La galería no necesita tanto: una
# miniatura de 1024 px pesa unos 100 KB y es lo único que sale del backend
# hacia los navegadores. Los originales se guardan pero no se sirven.
LADO_MINIATURA = 1024
MAX_ETIQUETAS = 8
MAX_FOTOS_GALERIA = 24

app = Flask(__name__)
# Cloud Run admite peticiones de hasta 32 MiB. Aquí se corta antes.
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


# Los clientes se crean la primera vez que se usan, no al importar el módulo.
# Así el contenedor arranca aunque no tenga credenciales, por ejemplo con
# docker run en Cloud Shell.
@cache
def bucket():
    return storage.Client().bucket(BUCKET)


@cache
def vision_cliente():
    return vision.ImageAnnotatorClient()


def ahora():
    return datetime.now(timezone.utc)


@app.after_request
def cors(respuesta):
    respuesta.headers["Access-Control-Allow-Origin"] = "*"
    respuesta.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    respuesta.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return respuesta


@app.errorhandler(413)
def demasiado_grande(_):
    return jsonify({"error": "La foto pesa más de 20 MB"}), 413


def sin_bucket():
    log.warning("BUCKET está vacía: no hay dónde guardar las fotos")
    return jsonify({"error": "El backend no tiene un bucket configurado"}), 503


def hacer_miniatura(datos):
    """Devuelve la miniatura en JPEG o lanza ValueError si no es una imagen."""
    try:
        imagen = Image.open(io.BytesIO(datos))
        imagen.load()
    except (UnidentifiedImageError, OSError):
        raise ValueError("El archivo no es una imagen")
    # Los teléfonos guardan la orientación en los metadatos EXIF; sin esto,
    # muchas fotos verticales saldrían acostadas.
    imagen = ImageOps.exif_transpose(imagen).convert("RGB")
    imagen.thumbnail((LADO_MINIATURA, LADO_MINIATURA))
    salida = io.BytesIO()
    imagen.save(salida, format="JPEG", quality=82, optimize=True)
    return salida.getvalue()


def etiquetar(datos_jpeg):
    """Pide a Cloud Vision las etiquetas de la imagen.

    Devuelve (etiquetas, None) si funcionó o (None, mensaje) si no. La foto se
    guarda igual: que falle la IA no es razón para perder la imagen.
    """
    try:
        respuesta = vision_cliente().label_detection(
            image=vision.Image(content=datos_jpeg), max_results=MAX_ETIQUETAS
        )
        if respuesta.error.message:
            raise RuntimeError(respuesta.error.message)
        etiquetas = [
            {"nombre": e.description, "confianza": round(e.score, 3)}
            for e in respuesta.label_annotations
        ]
        return etiquetas, None
    except Exception as error:  # noqa: BLE001
        log.warning("Vision no respondió: %s", error)
        return None, str(error)


def como_json(blob):
    meta = blob.metadata or {}
    etiquetas = meta.get("etiquetas")
    return {
        "id": blob.name.removeprefix("miniaturas/").removesuffix(".jpg"),
        "autor": meta.get("autor", ""),
        "creado": blob.time_created.isoformat() if blob.time_created else None,
        "etiquetas": json.loads(etiquetas) if etiquetas else None,
        "vision": meta.get("vision", "sin etiquetas"),
        "miniatura": f"/fotos/{blob.name.removeprefix('miniaturas/').removesuffix('.jpg')}/miniatura",
    }


@app.get("/fotos")
def listar():
    if not BUCKET:
        return sin_bucket()
    # Un solo listado (operación de clase A) trae nombre, fecha y metadatos de
    # cada objeto. Ordenar aquí no cuesta nada más.
    blobs = list(bucket().list_blobs(prefix="miniaturas/"))
    blobs.sort(key=lambda b: b.time_created, reverse=True)
    return jsonify([como_json(b) for b in blobs[:MAX_FOTOS_GALERIA]])


@app.post("/fotos")
def subir():
    if not BUCKET:
        return sin_bucket()
    autor = (request.form.get("autor") or "").strip()
    if not autor or len(autor) > 40:
        return jsonify({"error": "Falta tu nombre, o tiene más de 40 caracteres"}), 400
    archivo = request.files.get("foto")
    if archivo is None or not archivo.filename:
        return jsonify({"error": "Falta la foto"}), 400

    original = archivo.read()
    try:
        miniatura = hacer_miniatura(original)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    # Vision trabaja sobre la miniatura: es más liviana y para etiquetas
    # basta con una imagen pequeña.
    etiquetas, error_vision = etiquetar(miniatura)

    foto_id = f"{ahora():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    extension = (archivo.mimetype or "image/jpeg").split("/")[-1]

    bucket().blob(f"originales/{foto_id}.{extension}").upload_from_string(
        original, content_type=archivo.mimetype or "application/octet-stream"
    )
    # Los metadatos viajan en la misma subida: una sola operación de clase A
    # por miniatura, en vez de subir y después modificar.
    blob = bucket().blob(f"miniaturas/{foto_id}.jpg")
    blob.metadata = {
        "autor": autor,
        "etiquetas": json.dumps(etiquetas, ensure_ascii=False) if etiquetas else "",
        "vision": "ok" if etiquetas is not None else f"error: {error_vision}",
    }
    blob.upload_from_string(miniatura, content_type="image/jpeg")
    blob.reload()
    log.info("Foto %s guardada por %s, %s etiquetas", foto_id, autor, len(etiquetas or []))
    return jsonify(como_json(blob)), 201


@app.get("/fotos/<foto_id>/miniatura")
def miniatura(foto_id):
    if not BUCKET:
        return sin_bucket()
    blob = bucket().blob(f"miniaturas/{foto_id}.jpg")
    if not blob.exists():
        return jsonify({"error": "No existe esa foto"}), 404
    # Una miniatura nunca cambia: el navegador puede guardarla un día entero
    # y no volver a pedirla.
    return Response(
        blob.download_as_bytes(),
        content_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
