from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import zipfile

from .models import TeamConfig
from .normalizer.text import normalize_lookup_key, strip_accents


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(slots=True)
class PhotoAsset:
    source_name: str
    safe_path: Path
    lookup_key: str


@dataclass(slots=True)
class ResolvedPhoto:
    path: Path
    fallback: bool
    source_name: str
    matched_alias: str | None = None


class PhotoRepository:
    def __init__(
        self,
        *,
        root_dir: Path,
        photos_dir: Path,
        cache_dir: Path,
        fallback_name: str,
        photos_zip_available: bool = True,
        photos_zip_path: Path | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.photos_dir = photos_dir
        self.cache_dir = cache_dir
        self.fallback_name = fallback_name
        self.photos_zip_available = photos_zip_available
        self.photos_zip_path = photos_zip_path
        self._indexed = False
        self._assets: list[PhotoAsset] = []
        self._fallback_path: Path | None = None

    def resolve_for_team(self, team: TeamConfig) -> ResolvedPhoto:
        self._ensure_index()
        if not self.photos_zip_available:
            fallback_path = self._fallback()
            return ResolvedPhoto(
                path=fallback_path,
                fallback=True,
                source_name=self.fallback_name,
                matched_alias=None,
            )
        alias_candidates = list(team.photo_aliases) or [team.web_block_name, *team.heading_aliases]
        scored: list[tuple[int, str, PhotoAsset]] = []
        for alias in alias_candidates:
            alias_key = self._alias_key(alias)
            if not alias_key:
                continue
            for asset in self._assets:
                score = self._match_score(alias_key, asset.lookup_key)
                if score > 0:
                    scored.append((score, alias, asset))

        if scored:
            scored.sort(key=lambda item: (item[0], len(item[2].lookup_key)), reverse=True)
            _, alias, asset = scored[0]
            return ResolvedPhoto(
                path=asset.safe_path,
                fallback=False,
                source_name=asset.source_name,
                matched_alias=alias,
            )

        fallback_path = self._fallback()
        return ResolvedPhoto(
            path=fallback_path,
            fallback=True,
            source_name=self.fallback_name,
            matched_alias=None,
        )

    def _ensure_index(self) -> None:
        if self._indexed:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._assets = []
        self._fallback_path = None

        if not self.photos_dir.exists():
            self._indexed = True
            return

        selected_zip = self._selected_zip_path()
        for path in sorted(self.photos_dir.iterdir(), key=lambda item: item.name.lower()):
            if path.is_dir():
                continue
            suffix = path.suffix.lower()
            if suffix in IMAGE_SUFFIXES:
                self._add_image(path, path.name)
                continue
            if suffix == ".zip" and self.photos_zip_available and selected_zip is not None and path.resolve() == selected_zip:
                self._extract_zip(path)

        self._indexed = True

    def _selected_zip_path(self) -> Path | None:
        if not self.photos_zip_available:
            return None
        if self.photos_zip_path is not None and self.photos_zip_path.exists():
            return self.photos_zip_path.resolve()
        zip_candidates = [path for path in self.photos_dir.iterdir() if path.is_file() and path.suffix.lower() == ".zip"]
        if not zip_candidates:
            return None
        return max(zip_candidates, key=lambda path: path.stat().st_mtime).resolve()

    def _extract_zip(self, zip_path: Path) -> None:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                suffix = Path(member.filename).suffix.lower()
                if suffix not in IMAGE_SUFFIXES:
                    continue
                source_name = Path(member.filename).name
                with archive.open(member) as source:
                    target = self._safe_target_path(source_name)
                    with target.open("wb") as handle:
                        shutil.copyfileobj(source, handle)
                self._register_asset(source_name, target)

    def _add_image(self, source_path: Path, source_name: str) -> None:
        target = self._safe_target_path(source_name)
        if source_path.resolve() != target.resolve():
            shutil.copy2(source_path, target)
        self._register_asset(source_name, target)

    def _register_asset(self, source_name: str, safe_path: Path) -> None:
        key = self._alias_key(Path(source_name).stem)
        asset = PhotoAsset(source_name=source_name, safe_path=safe_path, lookup_key=key)
        self._assets.append(asset)
        if normalize_lookup_key(source_name) == normalize_lookup_key(self.fallback_name):
            self._fallback_path = safe_path

    def _fallback(self) -> Path:
        if self._fallback_path is not None:
            return self._fallback_path
        expected = self.photos_dir / self.fallback_name
        if expected.exists():
            self._add_image(expected, expected.name)
            assert self._fallback_path is not None
            return self._fallback_path
        raise FileNotFoundError(f"No se encontró la imagen fallback '{self.fallback_name}' en {self.photos_dir}.")

    def _safe_target_path(self, source_name: str) -> Path:
        stem = self._safe_stem(Path(source_name).stem)
        suffix = Path(source_name).suffix.lower() or ".jpg"
        candidate = self.cache_dir / f"{stem}{suffix}"
        counter = 2
        while candidate.exists():
            if normalize_lookup_key(candidate.stem) == normalize_lookup_key(stem):
                return candidate
            candidate = self.cache_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        return candidate

    def _safe_stem(self, value: str) -> str:
        text = strip_accents(value or "")
        text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
        return text or "photo"

    def _alias_key(self, value: str) -> str:
        return normalize_lookup_key(value).replace(" ", "")

    def _match_score(self, alias_key: str, photo_key: str) -> int:
        if not alias_key or not photo_key:
            return 0
        if alias_key == photo_key:
            return 200
        if alias_key in photo_key:
            return 150 + len(alias_key)
        if photo_key in alias_key:
            return 120 + len(photo_key)
        alias_tokens = [token for token in re.findall(r"[A-Z0-9]+", alias_key) if token]
        photo_tokens = [token for token in re.findall(r"[A-Z0-9]+", photo_key) if token]
        overlap = len(set(alias_tokens) & set(photo_tokens))
        if overlap:
            return 40 + overlap
        return 0
