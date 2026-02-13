import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.db.database import async_session
from app.db.repositories import create_post, get_all_sources, get_existing_external_ids

logger = logging.getLogger(__name__)


async def parse_web_sources():
    """Parse all registered web sources for new content."""
    logger.info("Starting web sources parsing...")

    async with async_session() as session:
        sources = await get_all_sources(session, source_type="web")

        for source in sources:
            try:
                await _parse_single_web_source(session, source.id, source.identifier)
            except Exception as e:
                logger.error(f"Error parsing web source {source.identifier}: {e}")


async def _parse_single_web_source(session, source_id: int, url: str):
    """Parse a single web source — try RSS first, then fallback to HTML scraping."""
    logger.info(f"Parsing web source: {url}")

    # Try RSS first
    articles = await _try_rss(url)

    if not articles:
        # Fallback to HTML scraping
        articles = await _try_html_scrape(url)

    if not articles:
        logger.debug(f"No articles found at {url}")
        return

    new_count = 0
    external_ids = [article["url"] for article in articles if article.get("url")]
    existing_ids = await get_existing_external_ids(session, source_id=source_id, external_ids=external_ids)
    for article in articles:
        article_url = article.get("url")
        if not article_url or article_url in existing_ids:
            continue
        post = await create_post(
            session=session,
            source_id=source_id,
            external_id=article_url,
            content=article["content"],
            reactions_count=0,
            published_at=article.get("published_at"),
            commit=False,
        )
        if post:
            new_count += 1

    if new_count > 0:
        await session.commit()
        logger.info(f"Parsed {new_count} new articles from {url}")


async def _try_rss(url: str) -> list[dict]:
    """Try to find and parse RSS feed from a URL."""
    articles = []

    # Common RSS feed paths
    rss_paths = [
        "/rss", "/feed", "/rss.xml", "/atom.xml", "/feed.xml",
        "/feeds/posts/default", "/rss/", "/feed/",
    ]

    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    urls_to_try = [url]  # Try the URL itself first (maybe it IS an RSS feed)
    for path in rss_paths:
        urls_to_try.append(urljoin(base_url, path))

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for rss_url in urls_to_try:
            try:
                response = await client.get(rss_url, headers={"User-Agent": "Mozilla/5.0"})
                if response.status_code != 200:
                    continue

                content_type = response.headers.get("content-type", "")
                text = response.text

                # Quick check if this looks like RSS/Atom
                if "<rss" not in text and "<feed" not in text and "<channel" not in text:
                    # Also check for RSS link in HTML
                    if "text/html" in content_type:
                        rss_link = _find_rss_link_in_html(text, base_url)
                        if rss_link and rss_link not in urls_to_try:
                            urls_to_try.append(rss_link)
                    continue

                feed = feedparser.parse(text)
                if feed.entries:
                    for entry in feed.entries[:15]:
                        content = _extract_feed_content(entry)
                        if content and len(content) >= 50:
                            pub_date = _parse_feed_date(entry)
                            articles.append({
                                "url": entry.get("link", rss_url),
                                "content": content,
                                "published_at": pub_date,
                            })
                    if articles:
                        logger.info(f"Found RSS feed at {rss_url}")
                        return articles

            except Exception as e:
                logger.debug(f"RSS attempt failed for {rss_url}: {e}")
                continue

    return articles


def _find_rss_link_in_html(html: str, base_url: str) -> Optional[str]:
    """Find RSS feed link in HTML head."""
    try:
        soup = BeautifulSoup(html, "lxml")
        link = soup.find("link", attrs={"type": "application/rss+xml"})
        if not link:
            link = soup.find("link", attrs={"type": "application/atom+xml"})
        if link and link.get("href"):
            href = link["href"]
            if href.startswith("/"):
                return urljoin(base_url, href)
            return href
    except Exception:
        pass
    return None


def _extract_feed_content(entry) -> str:
    """Extract text content from a feedparser entry."""
    # Try content field first
    if hasattr(entry, "content") and entry.content:
        raw = entry.content[0].get("value", "")
    elif hasattr(entry, "summary"):
        raw = entry.summary
    elif hasattr(entry, "description"):
        raw = entry.description
    else:
        raw = entry.get("title", "")

    # Strip HTML tags
    if "<" in raw:
        soup = BeautifulSoup(raw, "lxml")
        text = soup.get_text(separator=" ", strip=True)
    else:
        text = raw

    # Prepend title if available
    title = entry.get("title", "")
    if title and not text.startswith(title):
        text = f"{title}\n\n{text}"

    return text.strip()


def _parse_feed_date(entry) -> Optional[datetime]:
    """Parse publication date from a feedparser entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6])
        except Exception:
            pass
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        try:
            return datetime(*entry.updated_parsed[:6])
        except Exception:
            pass
    return None


async def _try_html_scrape(url: str) -> list[dict]:
    """Fallback: scrape HTML page and extract article text."""
    articles = []

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if response.status_code != 200:
                return articles

            soup = BeautifulSoup(response.text, "lxml")

            # Remove script, style, nav, footer
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            # Try to find article elements
            article_elements = soup.find_all("article")
            if not article_elements:
                # Try common content containers
                article_elements = soup.find_all("div", class_=lambda c: c and any(
                    kw in c.lower() for kw in ["article", "post", "entry", "content", "news"]
                ))

            if article_elements:
                for elem in article_elements[:10]:
                    # Find the title
                    title_tag = elem.find(["h1", "h2", "h3"])
                    title = title_tag.get_text(strip=True) if title_tag else ""

                    # Find the link
                    link_tag = elem.find("a", href=True)
                    article_url = link_tag["href"] if link_tag else url
                    if article_url.startswith("/"):
                        article_url = urljoin(url, article_url)

                    # Get text
                    text = elem.get_text(separator=" ", strip=True)
                    if title and not text.startswith(title):
                        text = f"{title}\n\n{text}"

                    if text and len(text) >= 50:
                        articles.append({
                            "url": article_url,
                            "content": text[:5000],  # Limit content length
                            "published_at": None,
                        })
            else:
                # Last resort: extract main body text
                body = soup.find("body")
                if body:
                    text = body.get_text(separator=" ", strip=True)
                    if text and len(text) >= 100:
                        articles.append({
                            "url": url,
                            "content": text[:5000],
                            "published_at": None,
                        })

        except Exception as e:
            logger.error(f"HTML scrape failed for {url}: {e}")

    return articles
