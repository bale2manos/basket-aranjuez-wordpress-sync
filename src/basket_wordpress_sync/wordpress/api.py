"""Authenticated reads and isolated draft copies for integration testing."""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re
import mimetypes
from pathlib import Path
from urllib.parse import urlencode, urlsplit, unquote

from ..discovery import site_origin
from ..normalizer.text import normalize_lookup_key


class WordPressApi:
    def __init__(self, request, origin: str) -> None:
        self.request = request
        self.origin = site_origin(origin)
        response = request.get(self.origin + "/wp-admin/edit.php", timeout=30000)
        if "wp-login.php" in response.url:
            raise RuntimeError("La sesion ha caducado.")
        match = re.search(r'\bwpApiSettings\s*=\s*(\{.*?\})\s*;', response.text(), re.DOTALL)
        self.nonce = json.loads(match.group(1)).get("nonce") if match else None
        if not self.nonce:
            raise RuntimeError("WordPress no ofrece una sesion REST valida.")

    def _json(self, method: str, path: str, **kwargs) -> dict:
        response = getattr(self.request, method)(self.origin + "/wp-json/wp/v2/" + path,
            headers={"X-WP-Nonce": self.nonce}, timeout=30000, **kwargs)
        if not response.ok:
            raise RuntimeError(f"WordPress REST: HTTP {response.status} al consultar {path.split('?')[0]}.")
        return response.json()

    def get_post(self, post_id: int) -> dict:
        return self._json("get", f"posts/{int(post_id)}?context=edit")

    def resolve_media(self, path: Path, *, reuse: bool) -> dict:
        if reuse:
            for page in range(1, 101):
                query = urlencode({"search": path.stem, "per_page": 100, "page": page})
                items = self._json("get", "media?" + query)
                for item in items:
                    filename = Path(unquote(urlsplit(item.get("source_url", "")).path)).name
                    candidate_stem = re.sub(r"(?:-\d+)?(?:-scaled)?$", "", Path(filename).stem, flags=re.I)
                    expected_stem = re.sub(r"(?:-scaled)?$", "", path.stem, flags=re.I)
                    if normalize_lookup_key(candidate_stem) == normalize_lookup_key(expected_stem):
                        return {"id": item["id"], "url": item["source_url"]}
                if len(items) < 100:
                    break
        item = self._json("post", "media", multipart={"file": {
            "name": path.name, "mimeType": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "buffer": path.read_bytes(),
        }})
        return {"id": item["id"], "url": item["source_url"]}

    @staticmethod
    def fingerprint(post: dict) -> str:
        data = {key: post.get(key) for key in ("id", "modified_gmt", "status", "title", "content", "meta")}
        return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def create_test_copy(self, source_id: int, *, before: date = date(2026, 10, 17)) -> tuple[dict, str]:
        source = self.get_post(source_id)
        if date.fromisoformat(source["date"][:10]) >= before:
            raise ValueError("La prueba solo admite plantillas de jornadas anteriores al 17 de octubre.")
        meta = source.get("meta") or {}
        if not meta.get("_elementor_data"):
            raise ValueError("La entrada de referencia no contiene una plantilla Elementor.")
        copied_meta = {key: meta[key] for key in (
            "_elementor_data", "_elementor_edit_mode", "_elementor_template_type", "_elementor_page_settings"
        ) if key in meta}
        payload = {
            "title": "PRUEBA V2 - " + source["title"].get("raw", source["title"]["rendered"]),
            "status": "draft", "content": source["content"].get("raw", ""),
            "template": source.get("template", ""), "meta": copied_meta,
        }
        clone = self._json("post", "posts", data=payload)
        if clone.get("status") != "draft" or clone.get("id") == source_id:
            raise RuntimeError("No se pudo crear una copia de prueba en borrador.")
        if self.fingerprint(source) != self.fingerprint(self.get_post(source_id)):
            raise RuntimeError("La referencia ha cambiado durante la copia. Prueba detenida.")
        return clone, self.fingerprint(source)
