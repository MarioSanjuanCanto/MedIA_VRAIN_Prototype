"""Optional browser-based reverse-image candidate discovery with Google Lens."""

from __future__ import annotations

import logging
import hashlib
import ipaddress
import json
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from media_core.config import FACES_CACHE_DIR
from media_core.services.face_matching import cosine_similarity, face_feature

logger = logging.getLogger(__name__)
TRUSTED_HOSTS = ("wikipedia.org", "wikimedia.org", "parlament.cat", "congreso.es", "senado.es")


def _trusted(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return urlparse(url).scheme == "https" and any(
        host == domain or host.endswith("." + domain) for domain in TRUSTED_HOSTS
    )


def _public_https(url: str) -> bool:
    """Reject local and private destinations before fetching a search result."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(
            ipaddress.ip_address(item[4][0]).is_global for item in addresses
        )
    except (OSError, ValueError):
        return False


def _attributed_person(soup: BeautifulSoup, page_url: str) -> tuple[str, str] | None:
    """Read a Person structured-data record, or a trusted profile page."""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        pending = [data]
        while pending:
            node = pending.pop()
            if isinstance(node, list):
                pending.extend(node)
            elif isinstance(node, dict):
                kind = node.get("@type", "")
                if kind == "Person" or isinstance(kind, list) and "Person" in kind:
                    name = node.get("name", "")
                    image = node.get("image", "")
                    if isinstance(image, dict):
                        image = image.get("url", "")
                    if isinstance(name, str) and isinstance(image, str) and name and image:
                        return name.strip(), urljoin(page_url, image)
                pending.extend(node.values())
    if _trusted(page_url):
        heading = soup.find("h1")
        photo = soup.find("meta", property="og:image")
        if heading and photo and photo.get("content"):
            return heading.get_text(" ", strip=True), urljoin(page_url, photo["content"])
    return None


def search_image_in_browser(image_path: Path, limit: int = 12) -> dict:
    """Upload a crop in a browser and collect candidate image/page links.

    This only retrieves leads. Identity is decided later by face comparison.
    Chromium must already be installed; no profile or user session is reused.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "unavailable", "reason": "playwright_not_installed", "results": []}

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel="chrome")
            try:
                page = browser.new_page(locale="es-ES")
                page.goto("https://lens.google.com/", wait_until="domcontentloaded", timeout=20_000)
                uploader = page.locator('input[type="file"]').first
                if uploader.count() == 0:
                    return {"status": "unavailable", "reason": "upload_control_missing", "results": []}
                uploader.set_input_files(str(image_path))
                page.wait_for_url(lambda url: "lens.google.com" in url, timeout=20_000)
                page.wait_for_timeout(1800)
                candidates = page.locator("a:has(img)").evaluate_all(
                    """els => els.map(a => ({
                      url: a.href,
                      title: (a.innerText || a.getAttribute('aria-label') || '').trim(),
                      image_url: a.querySelector('img')?.src || ''
                    }))"""
                )
                results = []
                seen = set()
                for item in candidates:
                    url = item.get("url", "")
                    image_url = item.get("image_url", "")
                    if (
                        urlparse(url).scheme not in {"http", "https"}
                        or urlparse(image_url).scheme not in {"http", "https"}
                        or url in seen
                    ):
                        continue
                    seen.add(url)
                    results.append({"page_url": url, "title": item.get("title", ""), "image_url": image_url})
                    if len(results) >= limit:
                        break
                return {"status": "ok" if results else "no_results", "results": results}
            finally:
                browser.close()
    except Exception as exc:
        logger.warning("Visual browser search failed: %s", exc)
        return {"status": "unavailable", "reason": type(exc).__name__, "results": []}


def verify_web_results(crop_path: Path, results: list[dict], limit: int = 8) -> list[dict]:
    """Compare the crop with portraits attributed by institutional/encyclopedic pages."""
    query = face_feature(crop_path)
    if query is None:
        return []
    cache = FACES_CACHE_DIR / "web_references"
    cache.mkdir(parents=True, exist_ok=True)
    verified = []
    with httpx.Client(timeout=8.0, follow_redirects=False, headers={"User-Agent": "MedIA/1.0"}) as client:
        for item in results[:limit]:
            page_url = item.get("page_url", "")
            if not _public_https(page_url):
                continue
            try:
                response = client.get(page_url)
                response.raise_for_status()
                if len(response.content) > 2_000_000:
                    continue
                soup = BeautifulSoup(response.text, "html.parser")
                attributed = _attributed_person(soup, page_url)
                if not attributed:
                    continue
                name, image_url = attributed
                if not _public_https(image_url):
                    continue
                image = client.get(image_url)
                image.raise_for_status()
                if not image.headers.get("content-type", "").startswith("image/") or len(image.content) > 8_000_000:
                    continue
                image_path = cache / (hashlib.sha256(image_url.encode()).hexdigest() + ".jpg")
                image_path.write_bytes(image.content)
                reference = face_feature(image_path)
                if reference is None:
                    continue
                score = cosine_similarity(query, reference)
                verified.append({"name": name, "similarity": round(score, 4),
                                 "source_url": page_url, "reference_image_url": image_url})
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.debug("Could not verify web image: %s", exc)
    return sorted(verified, key=lambda row: row["similarity"], reverse=True)
