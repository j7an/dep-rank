"""GitHub dependents page HTML scraper."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import aiohttp
from selectolax.parser import HTMLParser

from dep_rank.core.cache import SqliteCache
from dep_rank.core.models import DependentType, Repository
from dep_rank.core.validation import validate_github_url

ITEM_SELECTOR = "#dependents > div.Box > div.flex-items-center"
REPO_SELECTOR = "span > a.text-bold"
STARS_SELECTOR = "div > div > span"
NEXT_BUTTON_SELECTOR = "#dependents > div.paginate-container > div > a"
GITHUB_URL = "https://github.com"
MAX_PAGES = 1000
REQUEST_TIMEOUT = 30


def parse_dependents_page(html: str) -> tuple[list[Repository], str | None]:
    """Parse a single GitHub dependents HTML page.

    Returns:
        Tuple of (list of repositories, next page URL or None).
    """
    tree = HTMLParser(html)
    repos: list[Repository] = []

    for item in tree.css(ITEM_SELECTOR):
        repo_node = item.css_first(REPO_SELECTOR)
        stars_node = item.css_first(STARS_SELECTOR)

        if not repo_node or not stars_node:
            continue

        href = repo_node.attributes.get("href", "")
        if not href:
            continue

        stars_text = stars_node.text(strip=True)
        try:
            stars = int(stars_text.replace(",", ""))
        except (ValueError, AttributeError):
            continue

        parts = href.strip("/").split("/")
        if len(parts) != 2:
            continue

        owner, name = parts
        repos.append(
            Repository(
                owner=owner,
                name=name,
                url=f"{GITHUB_URL}/{owner}/{name}",
                stars=stars,
            )
        )

    # Find next page URL
    next_url: str | None = None
    links = tree.css(NEXT_BUTTON_SELECTOR)
    if len(links) == 2:
        next_href = links[1].attributes.get("href")
        if next_href:
            next_url = f"{GITHUB_URL}{next_href}" if next_href.startswith("/") else next_href
    elif len(links) == 1:
        link_text = links[0].text(strip=True)
        if link_text == "Next":
            next_href = links[0].attributes.get("href")
            if next_href:
                next_url = f"{GITHUB_URL}{next_href}" if next_href.startswith("/") else next_href

    return repos, next_url


async def scrape_dependents(
    session: aiohttp.ClientSession,
    url: str,
    dependent_type: DependentType = DependentType.REPOSITORY,
    min_stars: int = 5,
    cache: SqliteCache | None = None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> list[Repository]:
    """Scrape GitHub dependents pages and return repositories sorted by stars.

    Args:
        session: aiohttp client session.
        url: GitHub repository URL.
        dependent_type: REPOSITORY or PACKAGE.
        min_stars: Minimum star count to include.
        cache: Optional SQLite cache for HTTP responses.
        on_progress: Optional async callback(current_page, estimated_total_pages).
    """
    owner, repo = validate_github_url(url)
    base_url = f"{GITHUB_URL}/{owner}/{repo}/network/dependents"
    current_url: str | None = f"{base_url}?dependent_type={dependent_type.value}"

    all_repos: list[Repository] = []
    seen_urls: set[str] = set()
    source_url = f"{GITHUB_URL}/{owner}/{repo}"
    page = 0

    while current_url and page < MAX_PAGES:
        page += 1

        html: str | None = None
        if cache:
            cached = await cache.get(current_url)
            if cached and cached["body"] is not None:
                html = cached["body"].decode("utf-8")

        if html is None:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            headers: dict[str, str] = {}
            if cache:
                cached = await cache.get(current_url)
                if cached and cached["etag"]:
                    headers["If-None-Match"] = cached["etag"]

            async with session.get(
                current_url,
                timeout=timeout,
                headers=headers,
            ) as resp:
                if resp.status == 304 and cache:
                    html = None
                elif resp.status == 200:
                    body = await resp.read()
                    html = body.decode("utf-8")
                    etag = resp.headers.get("ETag")
                    if cache:
                        await cache.put(
                            current_url,
                            body,
                            etag=etag,
                            ttl=3600,
                        )
                else:
                    break

        if html is None:
            break

        repos, next_url = parse_dependents_page(html)

        for repo_obj in repos:
            if repo_obj.url in seen_urls or repo_obj.url == source_url:
                continue
            if repo_obj.stars >= min_stars:
                seen_urls.add(repo_obj.url)
                all_repos.append(repo_obj)

        if on_progress:
            await on_progress(page, 0)

        current_url = next_url

    all_repos.sort(key=lambda r: r.stars, reverse=True)
    return all_repos
