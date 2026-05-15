"""
scraper.py — Collecte la documentation Airflow + ressources Astronomer.

Sources :
  1. https://airflow.apache.org/docs/          ← doc officielle (~800 pages)
  2. https://www.astronomer.io/docs/            ← guides pratiques (~200 pages)
  3. https://www.astronomer.io/blog/            ← articles approfondis (~200 pages)

Usage :
  python backend/scripts/scraper.py
  python backend/scripts/scraper.py --source airflow --max-pages 100  # test rapide
"""

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

# ── Configuration ─────────────────────────────────────────────────────────────

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "scraped"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SOURCES = {
    "airflow": {
        "start_urls": [
            "https://airflow.apache.org/docs/apache-airflow/stable/",
        ],
        "allowed_domains": ["airflow.apache.org"],
        "allowed_paths": ["/docs/"],
        "exclude_patterns": [
            "_api/", "_modules/", "genindex", "search", "404",
            ".zip", ".pdf", ".png", ".jpg",
        ],
    },
    "astronomer_docs": {
        "start_urls": [
            "https://www.astronomer.io/docs/",
        ],
        "allowed_domains": ["www.astronomer.io"],
        "allowed_paths": ["/docs/"],
        "exclude_patterns": ["changelog", "release-notes", "api-reference"],
    },
    "astronomer_blog": {
        "start_urls": [
            "https://www.astronomer.io/blog/",
        ],
        "allowed_domains": ["www.astronomer.io"],
        "allowed_paths": ["/blog/"],
        "exclude_patterns": [],
    },
}

MAX_PAGES_PER_SOURCE = 1200
CONCURRENT_REQUESTS = 5          # parallélisme — ne pas surcharger le serveur
DELAY_BETWEEN_REQUESTS = 0.5     # secondes entre chaque requête
MIN_CONTENT_LENGTH = 300         # ignorer les pages trop courtes

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AirflowRAGBot/1.0; "
        "+https://github.com/votre-username/assistant-chatbot-support-client)"
    ),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ScrapedPage:
    url: str
    title: str
    content: str                  # texte nettoyé
    source: str                   # "airflow" | "astronomer_docs" | "astronomer_blog"
    content_hash: str             # SHA-256 pour déduplication
    scraped_at: str


# ── Nettoyage HTML → texte ─────────────────────────────────────────────────────

def extract_text(soup: BeautifulSoup, source: str) -> str:
    """
    Extrait le contenu principal d'une page selon la source.
    Supprime navigation, header, footer, pub, etc.
    """

    # ── Suppression des éléments inutiles ──────────────────────
    for tag in soup.find_all([
        "nav", "header", "footer", "script", "style",
        "aside", "form", "button", "iframe", "noscript",
    ]):
        tag.decompose()

    # Sélecteurs spécifiques par source
    selectors = {
        "airflow": ["div.document", "article", "main", "div#content"],
        "astronomer_docs": ["article", "main", "div.docs-content"],
        "astronomer_blog": ["article", "main", "div.blog-content"],
    }

    for selector in selectors.get(source, ["main", "article"]):
        content_div = soup.select_one(selector)
        if content_div:
            text = content_div.get_text(separator="\n", strip=True)
            break
    else:
        text = soup.get_text(separator="\n", strip=True)

    # ── Nettoyage du texte ──────────────────────────────────────
    lines = []
    for line in text.splitlines():
        line = line.strip()
        # Ignorer les lignes trop courtes (menus, breadcrumbs...)
        if len(line) > 30:
            lines.append(line)

    # Supprimer les lignes dupliquées consécutives
    cleaned = []
    prev = None
    for line in lines:
        if line != prev:
            cleaned.append(line)
        prev = line

    return "\n\n".join(cleaned)


def extract_title(soup: BeautifulSoup) -> str:
    """Extrait le titre de la page."""
    # Priorité : h1 > title > og:title
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)

    title = soup.find("title")
    if title:
        # Supprime le nom du site après " — " ou " | "
        raw = title.get_text(strip=True)
        for sep in [" — ", " | ", " - "]:
            if sep in raw:
                return raw.split(sep)[0].strip()
        return raw

    og_title = soup.find("meta", property="og:title")
    if og_title:
        return og_title.get("content", "")

    return "Sans titre"


# ── Filtrage des URLs ─────────────────────────────────────────────────────────

def is_valid_url(url: str, config: dict) -> bool:
    """Vérifie qu'une URL appartient au périmètre de scraping."""
    parsed = urlparse(url)

    # Domaine autorisé
    if parsed.netloc not in config["allowed_domains"]:
        return False

    # Chemin autorisé
    if not any(parsed.path.startswith(p) for p in config["allowed_paths"]):
        return False

    # Patterns exclus
    if any(ex in url for ex in config["exclude_patterns"]):
        return False

    # Pas de fragments (#section) ni de paramètres (?page=2)
    if parsed.fragment or parsed.query:
        return False

    return True


def extract_links(soup: BeautifulSoup, base_url: str, config: dict) -> list[str]:
    """Extrait tous les liens valides d'une page."""
    links = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        # Ignorer les ancres et javascript
        if href.startswith("#") or href.startswith("javascript:"):
            continue
        full_url = urljoin(base_url, href)
        # Normaliser (supprimer le fragment)
        full_url = full_url.split("#")[0]
        if is_valid_url(full_url, config):
            links.append(full_url)
    return list(set(links))


# ── Scraper principal ─────────────────────────────────────────────────────────

class Scraper:
    def __init__(self, source_name: str, config: dict, max_pages: int = MAX_PAGES_PER_SOURCE):
        self.source_name = source_name
        self.config = config
        self.max_pages = max_pages
        self.visited: set[str] = set()
        self.to_visit: list[str] = list(config["start_urls"])
        self.pages: list[ScrapedPage] = []
        self.seen_hashes: set[str] = set()

    async def scrape(self) -> list[ScrapedPage]:
        """Lance le scraping de la source."""
        logger.info(f"[{self.source_name}] Démarrage du scraping...")

        async with httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)

            while self.to_visit and len(self.pages) < self.max_pages:
                # Prendre un batch d'URLs
                batch = []
                while self.to_visit and len(batch) < CONCURRENT_REQUESTS:
                    url = self.to_visit.pop(0)
                    if url not in self.visited:
                        self.visited.add(url)
                        batch.append(url)

                if not batch:
                    break

                tasks = [self._fetch_page(client, semaphore, url) for url in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, ScrapedPage):
                        self.pages.append(result)

                logger.info(
                    f"[{self.source_name}] {len(self.pages)} pages collectées "
                    f"| {len(self.to_visit)} URLs en attente"
                )

                await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

        logger.success(
            f"[{self.source_name}] Terminé — {len(self.pages)} pages collectées"
        )
        return self.pages

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        url: str,
    ) -> ScrapedPage | None:
        """Télécharge et parse une page."""
        async with semaphore:
            try:
                response = await client.get(url)

                if response.status_code != 200:
                    return None

                # Ignorer les non-HTML
                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type:
                    return None

                soup = BeautifulSoup(response.text, "html.parser")

                # Extraire le contenu
                text = extract_text(soup, self.source_name)
                title = extract_title(soup)

                # Ignorer les pages trop courtes
                if len(text) < MIN_CONTENT_LENGTH:
                    return None

                # Déduplication par hash du contenu
                content_hash = hashlib.sha256(text.encode()).hexdigest()
                if content_hash in self.seen_hashes:
                    return None
                self.seen_hashes.add(content_hash)

                # Découvrir de nouveaux liens
                new_links = extract_links(soup, url, self.config)
                for link in new_links:
                    if link not in self.visited and link not in self.to_visit:
                        self.to_visit.append(link)

                return ScrapedPage(
                    url=url,
                    title=title,
                    content=text,
                    source=self.source_name,
                    content_hash=content_hash,
                    scraped_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                )

            except Exception as e:
                logger.warning(f"Erreur sur {url}: {e}")
                return None


# ── Sauvegarde ────────────────────────────────────────────────────────────────

def save_pages(pages: list[ScrapedPage], source_name: str) -> Path:
    """
    Sauvegarde les pages en JSONL (une page par ligne).
    Format choisi pour faciliter le chargement par ingest.py.
    """
    output_file = OUTPUT_DIR / f"{source_name}.jsonl"

    with open(output_file, "w", encoding="utf-8") as f:
        for page in pages:
            f.write(json.dumps(asdict(page), ensure_ascii=False) + "\n")

    logger.success(f"Sauvegardé : {output_file} ({len(pages)} pages)")
    return output_file


# ── Point d'entrée ────────────────────────────────────────────────────────────

async def main(source_filter: str | None = None, max_pages: int = MAX_PAGES_PER_SOURCE):
    """Lance le scraping de toutes les sources (ou d'une seule)."""

    sources_to_scrape = (
        {source_filter: SOURCES[source_filter]}
        if source_filter and source_filter in SOURCES
        else SOURCES
    )

    all_stats = {}

    for source_name, config in sources_to_scrape.items():
        scraper = Scraper(source_name, config, max_pages=max_pages)
        pages = await scraper.scrape()
        output_file = save_pages(pages, source_name)
        all_stats[source_name] = len(pages)

    # ── Résumé ────────────────────────────────────────────────
    logger.info("\n─── RÉSUMÉ DU SCRAPING ────────────────────")
    total = 0
    for source, count in all_stats.items():
        logger.info(f"  {source:<25} {count:>4} pages")
        total += count
    logger.info(f"  {'TOTAL':<25} {total:>4} pages")
    logger.info(f"  Fichiers dans : {OUTPUT_DIR}")
    logger.info("───────────────────────────────────────────")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scraper documentation Airflow")
    parser.add_argument(
        "--source",
        choices=list(SOURCES.keys()),
        default=None,
        help="Scraper une seule source (défaut : toutes)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=MAX_PAGES_PER_SOURCE,
        help=f"Nombre max de pages par source (défaut : {MAX_PAGES_PER_SOURCE})",
    )
    args = parser.parse_args()

    asyncio.run(main(source_filter=args.source, max_pages=args.max_pages))