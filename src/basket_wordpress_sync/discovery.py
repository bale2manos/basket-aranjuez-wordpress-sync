"""Read-only discovery of the current WordPress season and Elementor widgets."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from http.cookiejar import Cookie, CookieJar
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from .normalizer.text import compact_whitespace, normalize_lookup_key


def site_origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
        raise ValueError("La URL del sitio debe ser http(s), sin credenciales.")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def title_section(title: str) -> str | None:
    key = normalize_lookup_key(unescape(title))
    for prefix, section in (
        ("INFANTIL FEMENINO", "infantil_femenino"),
        ("INFANTIL MASCULINO", "infantil_masculino"),
        ("SUB 22", "sub22"), ("SUB22", "sub22"),
        ("SENIOR", "senior"), ("ALEVIN", "alevin"),
        ("CADETE", "cadete"), ("JUNIOR", "junior"),
    ):
        if key == prefix or key.startswith(prefix + " "):
            return section
    return None


def infer_weekend_from_docx(path: Path) -> date:
    from datetime import timedelta
    from docx import Document

    months = "ENERO FEBRERO MARZO ABRIL MAYO JUNIO JULIO AGOSTO SEPTIEMBRE OCTUBRE NOVIEMBRE DICIEMBRE".split()
    document = Document(str(path))
    for paragraph in document.paragraphs[:8]:
        text = normalize_lookup_key(paragraph.text)
        match = re.search(r"\b(\d{1,2}) (\d{1,2}) (?:DE )?([A-Z]+) (?:DE )?(20\d{2})\b", text)
        if not match or match.group(3) not in months:
            continue
        start = date(int(match.group(4)), months.index(match.group(3)) + 1, int(match.group(1)))
        if start.weekday() != 5 or (start + timedelta(days=1)).day != int(match.group(2)):
            raise ValueError("La cabecera del Word no identifica un fin de semana de sabado y domingo.")
        return start
    raise ValueError("No se encuentra la fecha de jornada en la cabecera del Word. Introduce el sabado manualmente.")


@dataclass
class DiscoveredWidget:
    widget_id: str
    widget_type: str
    text: str = ""


class ElementorInventory(HTMLParser):
    """Record actual widget identities; never infer fields from global positions."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.widgets: list[DiscoveredWidget] = []
        self.headings: list[str] = []
        self._stack: list[tuple[str, DiscoveredWidget | None]] = []
        self._heading: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        widget = None
        if attributes.get("data-widget_type"):
            widget = DiscoveredWidget(attributes.get("data-id", ""), attributes["data-widget_type"])
            self.widgets.append(widget)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self._stack.append((tag, widget))
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading = []

    def handle_endtag(self, tag: str) -> None:
        if self._heading is not None and tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            heading = compact_whitespace(" ".join(self._heading))
            if heading:
                self.headings.append(heading)
            self._heading = None
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading.append(data)
        for _, widget in reversed(self._stack):
            if widget is not None:
                widget.text = compact_whitespace(widget.text + " " + data)
                break


@dataclass
class DiscoveredEntry:
    post_id: int
    title: str
    section: str
    date: str
    status: str
    public_url: str
    editor_url: str
    team_headings: list[str] = field(default_factory=list)
    widgets: list[DiscoveredWidget] = field(default_factory=list)

    @property
    def editable(self) -> bool:
        return self.status == "draft"


def entry_from_post(post: dict, origin: str) -> DiscoveredEntry | None:
    title = unescape(str((post.get("title") or {}).get("rendered", "")))
    section = title_section(title)
    if not section:
        return None
    inventory = ElementorInventory()
    inventory.feed(str((post.get("content") or {}).get("rendered", "")))
    post_id = int(post["id"])
    headings = [text for text in inventory.headings if title_section(text) == section]
    return DiscoveredEntry(
        post_id, title, section, str(post.get("date", "")),
        str(post.get("status", "unknown")), str(post.get("link", "")),
        f"{site_origin(origin)}/wp-admin/post.php?post={post_id}&action=elementor",
        list(dict.fromkeys(headings)), inventory.widgets,
    )


class SeasonDiscovery:
    """Public API reader. GET requests only, without any editing capability."""

    def __init__(self, origin: str, *, timeout: int = 30) -> None:
        self.origin = site_origin(origin)
        self.timeout = timeout

    def fetch(self, *, since: date) -> list[DiscoveredEntry]:
        entries = []
        for page in range(1, 101):
            query = urlencode({
                "after": since.isoformat() + "T00:00:00", "per_page": 100,
                "page": page, "orderby": "date", "order": "desc",
                "_fields": "id,date,link,title,content,status",
            })
            request = Request(self.origin + "/wp-json/wp/v2/posts?" + query,
                              headers={"User-Agent": "BasketWordPressSync/2.0 (read-only)"})
            with urlopen(request, timeout=self.timeout) as response:
                posts = json.load(response)
                total_pages = int(response.headers.get("X-WP-TotalPages", "1"))
            if not isinstance(posts, list):
                raise ValueError("WordPress no ha devuelto una lista de entradas.")
            for post in posts:
                entry = entry_from_post(post, self.origin)
                if entry:
                    entries.append(entry)
            if not posts or page >= total_pages:
                break
        else:
            raise RuntimeError("Demasiadas paginas: limita la fecha de inicio de temporada.")
        return entries


def discovery_report(entries: list[DiscoveredEntry], since: date) -> dict:
    sections: dict[str, dict] = {}
    for entry in sorted(entries, key=lambda item: item.date, reverse=True):
        section = sections.setdefault(entry.section, {"teams": [], "reference_posts": [], "widget_types": []})
        section["reference_posts"].append(entry.post_id)
        for heading in entry.team_headings:
            if normalize_lookup_key(heading) not in {normalize_lookup_key(x) for x in section["teams"]}:
                section["teams"].append(heading)
        section["widget_types"] = sorted(set(section["widget_types"]) | {w.widget_type for w in entry.widgets})
    return {
        "version": "2.0", "since": since.isoformat(), "read_only": True,
        "sections": sections, "entries": [asdict(entry) for entry in entries],
        "notes": [
            "El catalogo publico no contiene borradores ni garantiza la plantilla completa del club.",
            "Las entradas publicadas son referencias de lectura y nunca destinos de edicion.",
            "Los equipos detectados deben contrastarse con el Word y los borradores de la jornada.",
        ],
    }


class AdminInventory(HTMLParser):
    """Read post identities from the existing posts list without opening an editor."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[dict] = []
        self._row: dict | None = None
        self._title: list[str] | None = None

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        row_id = re.fullmatch(r"post-(\d+)", attributes.get("id", ""))
        if tag == "tr" and row_id:
            statuses = [token[7:] for token in attributes.get("class", "").split() if token.startswith("status-")]
            self._row = {"post_id": int(row_id.group(1)), "status": statuses[0] if len(statuses) == 1 else "unknown", "title": ""}
        if self._row is not None and tag == "a" and "row-title" in attributes.get("class", "").split():
            self._title = []

    def handle_data(self, data) -> None:
        if self._title is not None:
            self._title.append(data)

    def handle_endtag(self, tag) -> None:
        if tag == "a" and self._row is not None and self._title is not None:
            self._row["title"] = compact_whitespace(" ".join(self._title))
            self._title = None
        if tag == "tr" and self._row is not None:
            if self._row["title"]:
                self.entries.append(self._row)
            self._row = None
            self._title = None


def title_matches_weekend(title: str, weekend: date) -> bool:
    if weekend.weekday() != 5:
        raise ValueError("Selecciona el sabado de la jornada.")
    from datetime import timedelta

    end = weekend + timedelta(days=1)
    months = ("ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE")
    key = normalize_lookup_key(title)
    # A title must explicitly identify the full weekend and year, never just be latest.
    if weekend.month != end.month:
        pattern = rf"\b0?{weekend.day} (?:DE )?{months[weekend.month - 1]} (?:{weekend.year} )?(?:Y )?0?{end.day} (?:DE )?{months[end.month - 1]} (?:DE )?{end.year}\b"
    else:
        pattern = rf"\b0?{weekend.day} (?:Y )?0?{end.day} (?:DE )?{months[weekend.month - 1]} (?:DE )?{weekend.year}\b"
    return bool(re.search(pattern, key))


def select_weekend_drafts(entries: list[dict], weekend: date, origin: str) -> dict:
    selected: dict[str, str] = {}
    errors = []
    protected = []
    candidates: dict[str, list[dict]] = {}
    for entry in entries:
        if not title_matches_weekend(entry["title"], weekend):
            continue
        section = title_section(entry["title"])
        if not section:
            continue
        if entry["status"] != "draft":
            protected.append(entry)
            continue
        candidates.setdefault(section, []).append(entry)
    protected_sections = {title_section(entry["title"]) for entry in protected}
    for section, items in candidates.items():
        if len(items) != 1 or section in protected_sections:
            errors.append(f"{section}: destino ambiguo o cronica ya hecha; no se selecciona.")
        else:
            selected[section] = f"{site_origin(origin)}/wp-admin/post.php?post={items[0]['post_id']}&action=elementor"
    return {"weekend": weekend.isoformat(), "read_only": True, "entry_urls": selected, "protected": protected, "errors": errors}


def inspect_saved_session(origin: str, storage_state: Path, *, weekend: date) -> dict:
    """Read all draft/published post rows with the saved session; no POST requests."""
    if weekend.weekday() != 5:
        raise ValueError("Selecciona el sabado de la jornada.")
    origin = site_origin(origin)
    host = urlsplit(origin).hostname
    state = json.loads(storage_state.read_text(encoding="utf-8"))
    jar = CookieJar()
    for item in state.get("cookies", []):
        domain = item["domain"]
        if domain.lstrip(".") != host:
            continue
        expires = item.get("expires", -1)
        jar.set_cookie(Cookie(
            version=0, name=item["name"], value=item["value"], port=None, port_specified=False,
            domain=domain, domain_specified=domain.startswith("."), domain_initial_dot=domain.startswith("."),
            path=item.get("path", "/"), path_specified=True, secure=item.get("secure", False),
            expires=int(expires) if expires > 0 else None, discard=expires <= 0,
            comment=None, comment_url=None, rest={"HttpOnly": item.get("httpOnly", False)}, rfc2109=False,
        ))
    opener = build_opener(HTTPCookieProcessor(jar))
    all_entries: dict[int, dict] = {}
    for status in ("draft", "publish", "pending", "future", "private"):
        for page in range(1, 101):
            query = urlencode({"post_type": "post", "post_status": status, "paged": page, "s": str(weekend.year)})
            with opener.open(origin + "/wp-admin/edit.php?" + query, timeout=30) as response:
                if site_origin(response.url) != origin or "wp-login.php" in response.url:
                    raise RuntimeError("La sesion de WordPress ha caducado. Inicia sesion y guardala de nuevo.")
                content = response.read().decode("utf-8", errors="replace")
            if 'id="loginform"' in content:
                raise RuntimeError("WordPress solicita iniciar sesion de nuevo.")
            if 'id="the-list"' not in content:
                raise RuntimeError("No se reconoce el listado de entradas de WordPress; no se eligen destinos.")
            inventory = AdminInventory()
            inventory.feed(content)
            fresh = [entry for entry in inventory.entries if entry["post_id"] not in all_entries]
            all_entries.update((entry["post_id"], entry) for entry in fresh)
            if not fresh or 'class="next-page button"' not in content:
                break
        else:
            raise RuntimeError("El listado tiene demasiadas paginas para inspeccionarlo por completo.")
    return select_weekend_drafts(list(all_entries.values()), weekend, origin)
