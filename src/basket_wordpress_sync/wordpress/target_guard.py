"""Check a target before loading Elementor, which can acquire editing locks."""
from __future__ import annotations

import re
import json
from urllib.parse import parse_qs, urlsplit

from ..discovery import site_origin


def require_unpublished_target(request, entry_url: str, allowed_origins: set[str]) -> dict:
    origin = site_origin(entry_url)
    if origin not in allowed_origins:
        raise ValueError("La entrada no pertenece al sitio de WordPress configurado.")
    query = parse_qs(urlsplit(entry_url).query)
    ids = query.get("post", [])
    if len(ids) != 1 or not ids[0].isdigit() or int(ids[0]) < 1:
        raise ValueError("La URL de Elementor debe identificar una unica entrada con post=ID.")
    post_id = int(ids[0])
    endpoint = f"{origin}/wp-json/wp/v2/posts/{post_id}"
    response = request.get(endpoint, timeout=30000)
    if response.status in {401, 403, 404}:
        admin = request.get(f"{origin}/wp-admin/edit.php", timeout=30000)
        if "wp-login.php" in admin.url:
            raise RuntimeError("La sesion ha caducado. Inicia sesion y guardala de nuevo.")
        configuration = re.search(r'\bwpApiSettings\s*=\s*(\{.*?\})\s*;', admin.text(), re.DOTALL)
        try:
            nonce = json.loads(configuration.group(1)).get("nonce") if configuration else None
        except (ValueError, AttributeError):
            nonce = None
        if not isinstance(nonce, str) or not re.fullmatch(r"[a-zA-Z0-9]+", nonce):
            raise RuntimeError("No se pudo verificar el estado de la entrada. No se abrira Elementor.")
        response = request.get(endpoint, headers={"X-WP-Nonce": nonce}, timeout=30000)
    if not response.ok:
        raise RuntimeError("WordPress no permite comprobar la entrada; edicion bloqueada.")
    payload = response.json()
    if payload.get("id") != post_id or payload.get("status") != "draft":
        raise RuntimeError(
            f"Entrada protegida: post={post_id}, estado={payload.get('status', 'desconocido')}. "
            "Solo se permite editar borradores. Usa una copia de prueba sin publicar."
        )
    return payload
