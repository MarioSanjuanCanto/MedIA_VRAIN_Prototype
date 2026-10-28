"""Build a local reference gallery from attributed institutional portraits."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from media_core.config import DATA_DIR

logger = logging.getLogger(__name__)
GALLERY_DIR = DATA_DIR / "reference_faces"
PARLIAMENT_INDEX = "https://www.parlament.cat/web/composicio/ple-parlament/diputats-fotos/index.html"
USER_AGENT = "MedIA-face-indexer/0.1 (https://www.watchity.com/)"
WIKIPEDIA_API = "https://ca.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
LEGISLATURE_PAGE = "Llista de diputats del Parlament de Catalunya (XV Legislatura)"


def load_gallery(gallery_id: str = "TV04") -> list[dict]:
    manifest = GALLERY_DIR / gallery_id.upper() / "people.json"
    if not manifest.exists():
        return []
    try:
        rows = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    result = []
    for row in rows if isinstance(rows, list) else []:
        image = Path(row.get("image_path", ""))
        if not image.is_absolute():
            image = manifest.parent / image
        if row.get("name") and row.get("source_url") and image.is_file():
            result.append({**row, "image_path": str(image.resolve())})
    return result


def _profile_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = set()
    for anchor in soup.select('a[href*="p_codi="]'):
        url = urljoin(PARLIAMENT_INDEX, anchor.get("href", ""))
        code = parse_qs(urlparse(url).query).get("p_codi", [""])[0]
        if code.isdecimal():
            urls.add(f"https://www.parlament.cat/web/composicio/diputats-fitxa/index.html?p_codi={code}")
    return sorted(urls)


def _profile(html: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    photo = next(
        (img for img in soup.find_all("img") if "fotografia de" in img.get("alt", "").lower()),
        None,
    )
    if photo is None:
        return None
    name = heading.get_text(" ", strip=True) if heading else photo.get("alt", "")
    name = re.sub(r"^(?:(?:I|H)\.|Sra\.|Sr\.)\s*", "", name).strip()
    name = re.sub(r"^(?:Sra\.|Sr\.)\s*", "", name).strip()
    if not name:
        return None
    src = photo.get("src") or photo.get("data-src")
    if not src:
        return None
    return {"name": name, "role": "Diputado/a del Parlament de Catalunya", "source_url": url,
            "image_url": urljoin(url, src)}


def refresh_parliament_gallery() -> dict:
    """Fetch the official photo directory; retain an existing gallery on failure."""
    target = GALLERY_DIR / "TV04"
    target.mkdir(parents=True, exist_ok=True)
    added = []
    errors = 0
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            response = client.get(PARLIAMENT_INDEX)
            response.raise_for_status()
            links = _profile_links(response.text)
            for link in links:
                try:
                    detail = client.get(link)
                    detail.raise_for_status()
                    person = _profile(detail.text, link)
                    if not person:
                        continue
                    photo = client.get(person["image_url"])
                    photo.raise_for_status()
                    if not photo.headers.get("content-type", "").startswith("image/") or len(photo.content) > 8_000_000:
                        continue
                    code = parse_qs(urlparse(link).query)["p_codi"][0]
                    filename = f"{code}.jpg"
                    (target / filename).write_bytes(photo.content)
                    added.append({**person, "image_path": filename})
                except (httpx.HTTPError, OSError, ValueError, KeyError):
                    errors += 1
    except (httpx.HTTPError, OSError) as exc:
        fallback = refresh_commons_gallery()
        fallback["parliament_error"] = str(exc)
        return fallback
    if added:
        manifest = target / "people.json"
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(added, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest)
        return {"status": "ok", "count": len(added), "profile_errors": errors, "source": "Parlament"}
    return refresh_commons_gallery()


def _commons_portraits(wikitext: str) -> list[tuple[str, str]]:
    """Extract attributed Commons filenames from the XV-legislature member list."""
    portraits = []
    for block in re.findall(r"\{\{Wikidata list/diputats\b(.*?)\}\}", wikitext, re.DOTALL):
        name = re.search(r"\|\s*label\s*=\s*\[\[([^]|]+)", block)
        photo = re.search(r"\|\s*p18\s*=\s*\[\[(?:Fitxer|File):([^]|]+)", block)
        if name and photo:
            portraits.append((name.group(1).strip(), photo.group(1).strip()))
    return portraits


def refresh_commons_gallery() -> dict:
    """Fallback: use the public XV-legislature list and Commons photo metadata."""
    target = GALLERY_DIR / "TV04"
    target.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        with httpx.Client(timeout=25.0, headers={"User-Agent": USER_AGENT}) as client:
            listing = client.get(WIKIPEDIA_API, params={
                "action": "parse", "page": LEGISLATURE_PAGE, "prop": "wikitext", "format": "json"
            })
            listing.raise_for_status()
            wikitext = listing.json()["parse"]["wikitext"]["*"]
            portraits = _commons_portraits(wikitext)
            for start in range(0, len(portraits), 40):
                batch = portraits[start:start + 40]
                titles = {"File:" + filename: name for name, filename in batch}
                metadata = client.get(COMMONS_API, params={
                    "action": "query", "prop": "imageinfo", "iiprop": "url",
                    "iiurlwidth": 300, "titles": "|".join(titles), "format": "json"
                })
                metadata.raise_for_status()
                for page in metadata.json().get("query", {}).get("pages", {}).values():
                    title = page.get("title", "")
                    name = titles.get(title)
                    info = (page.get("imageinfo") or [{}])[0]
                    image_url = info.get("thumburl") or info.get("url")
                    if not name or not image_url:
                        continue
                    try:
                        image = client.get(image_url)
                        image.raise_for_status()
                        if not image.headers.get("content-type", "").startswith("image/") or len(image.content) > 8_000_000:
                            continue
                        filename = f"commons_{page['pageid']}.jpg"
                        (target / filename).write_bytes(image.content)
                        rows.append({"name": name, "role": "Diputado/a del Parlament de Catalunya",
                                     "source_url": info.get("descriptionurl", ""),
                                     "image_url": image_url, "image_path": filename})
                    except (httpx.HTTPError, OSError):
                        continue
    except (httpx.HTTPError, OSError, KeyError, ValueError) as exc:
        return {"status": "error", "reason": str(exc), "existing_count": len(load_gallery("TV04"))}
    if rows:
        manifest = target / "people.json"
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(manifest)
    return {"status": "ok" if rows else "no_results", "count": len(rows), "source": "Wikimedia Commons"}
