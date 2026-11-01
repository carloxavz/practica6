# Las fotos del grupo. El nombre lleva el ID del proyecto porque los nombres
# de bucket son únicos en todo Google Cloud, no solo dentro de tu proyecto.
resource "google_storage_bucket" "fotos" {
  name          = "${var.proyecto}-${var.prefijo}-fotos"
  location      = var.region
  storage_class = "STANDARD"

  # Un solo juego de permisos para todo el bucket, sin listas por objeto.
  uniform_bucket_level_access = true

  # Nadie puede hacer público el bucket ni lo que contiene, ni siquiera por error.
  public_access_prevention = "enforced"

  # Sin esto, destroy falla si el bucket tiene fotos adentro.
  force_destroy = true

  # Limpieza automática: cada objeto (original y miniatura) se borra solo
  # cuando cumple 7 días desde que se subió.
  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

# El backend puede crear, leer y listar objetos de este bucket, y de ningún otro.
resource "google_storage_bucket_iam_member" "backend_objetos" {
  bucket = google_storage_bucket.fotos.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.backend.email}"
}
