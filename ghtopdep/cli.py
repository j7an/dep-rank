import calendar
import json
import os
import sys
import textwrap
import datetime
from email.utils import formatdate, parsedate
from urllib.parse import urlparse
from typing import Optional, Dict, List, Any, Tuple, Union

import appdirs
import click
import github3
import pipdate
import requests
from urllib3.util.retry import Retry
from cachecontrol.caches import FileCache
from cachecontrol.heuristics import BaseHeuristic
from cachecontrol import CacheControl, CacheControlAdapter
from tqdm import tqdm
from selectolax.parser import HTMLParser
from tabulate import tabulate

from .__version__ import __version__

PACKAGE_NAME = "ghtopdep"
CACHE_DIR = appdirs.user_cache_dir(PACKAGE_NAME)
NEXT_BUTTON_SELECTOR = "#dependents > div.paginate-container > div > a"
ITEM_SELECTOR = "#dependents > div.Box > div.flex-items-center"
REPO_SELECTOR = "span > a.text-bold"
STARS_SELECTOR = "div > span:nth-child(1)"
GITHUB_URL = "https://github.com"
REPOS_PER_PAGE = 30
MAX_PAGES = 1000  # Safety limit to prevent infinite loops
REQUEST_TIMEOUT = 30  # Timeout for requests in seconds

if pipdate.needs_checking(PACKAGE_NAME):
    msg = pipdate.check(PACKAGE_NAME, __version__)
    click.echo(msg)


class OneDayHeuristic(BaseHeuristic):
    cacheable_by_default_statuses = {
        200, 203, 204, 206, 300, 301, 404, 405, 410, 414, 501
    }

    def update_headers(self, response: Any) -> Dict[str, str]:
        if response.status not in self.cacheable_by_default_statuses:
            return {}

        date_header = response.headers.get("date")
        if not date_header:
            return {}

        try:
            date = parsedate(date_header)
            if not date:
                return {}
            expires = datetime.datetime(*date[:6]) + datetime.timedelta(days=1)
            return {"expires": formatdate(calendar.timegm(expires.timetuple())), "cache-control": "public"}
        except (TypeError, ValueError, OverflowError):
            # If date parsing fails, don't cache
            return {}

    def warning(self, response: Any) -> str:
        warning_msg = "Automatically cached! Response is Stale."
        return "110 - {0}".format(warning_msg)


def already_added(repo_url: str, repos: List[Dict[str, Any]]) -> bool:
    """
    Check if a repository URL is already in the repos list.

    DEPRECATED: This function is no longer used internally.
    Use set-based tracking for O(1) duplicate detection instead.
    Kept for backward compatibility with external code.

    Args:
        repo_url: The repository URL to check
        repos: List of repository dictionaries

    Returns:
        bool: True if repo_url is found in repos, False otherwise
    """
    for repo in repos:
        if repo['url'] == repo_url:
            return True
    return False


def fetch_description(gh: Any, relative_url: str) -> str:
    """
    Fetch repository description from GitHub API.

    Args:
        gh: Authenticated GitHub session
        relative_url: Relative GitHub URL (e.g., "/owner/repository")

    Returns:
        str: Repository description (shortened to 60 chars) or empty string on error
    """
    try:
        # Validate and parse the URL
        url_parts = relative_url.split("/")
        if len(url_parts) < 3:
            click.echo(f"Warning: Invalid relative URL format: {relative_url}", err=True)
            return ""

        owner, repository = url_parts[1], url_parts[2]

        if not owner or not repository:
            click.echo(f"Warning: Empty owner or repository in URL: {relative_url}", err=True)
            return ""

        # Fetch repository from GitHub API
        try:
            repo_obj = gh.repository(owner, repository)
        except Exception as e:
            click.echo(f"Warning: Failed to fetch repository {owner}/{repository}: {e}", err=True)
            return ""

        # Handle case where repository doesn't exist or is inaccessible
        if not repo_obj:
            click.echo(f"Warning: Repository not found: {owner}/{repository}", err=True)
            return ""

        repo_description = " "
        if repo_obj.description:
            repo_description = textwrap.shorten(repo_obj.description, width=60, placeholder="...")
        return repo_description

    except Exception as e:
        click.echo(f"Warning: Unexpected error fetching description for {relative_url}: {e}", err=True)
        return ""


def sort_repos(repos: List[Dict[str, Any]], rows: int) -> List[Dict[str, Any]]:
    sorted_repos = sorted(repos, key=lambda i: i["stars"], reverse=True)
    return sorted_repos[:rows]


def humanize(num: int) -> Union[int, str]:
    """Convert large numbers to human-readable format (e.g., 1500 -> 1.5K).

    Args:
        num: Number to humanize

    Returns:
        Original number if < 1000 or >= 1000000, formatted string with K suffix otherwise
    """
    if num < 1_000:
        return num
    elif num < 10_000:
        return "{}K".format(round(num / 100) / 10)
    elif num < 1_000_000:
        return "{}K".format(round(num / 1_000))
    else:
        return num


def readable_stars(repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for repo in repos:
        repo["stars"] = humanize(repo["stars"])
    return repos


def show_result(
    repos: List[Dict[str, Any]],
    total_repos_count: int,
    more_than_zero_count: int,
    destinations: str,
    table: bool
) -> None:
    if table:
        if repos:
            repos = readable_stars(repos)
            click.echo(tabulate(repos, headers="keys", tablefmt="github"))
            click.echo("found {0} {1} others {2} are private".format(total_repos_count, destinations, destinations))
            click.echo("found {0} {1} with more than zero star".format(more_than_zero_count, destinations))
        else:
            click.echo("Doesn't find any {0} that match search request".format(destinations))
    else:
        click.echo(json.dumps(repos))


def get_max_deps(sess: requests.Session, url: str, timeout: int = REQUEST_TIMEOUT) -> int:
    """
    Fetch the maximum number of dependents from GitHub's dependents page.

    Args:
        sess: Requests session
        url: The GitHub dependents URL
        timeout: Request timeout in seconds (default: 30)

    Returns:
        int: Maximum number of dependents

    Raises:
        SystemExit: If unable to fetch or parse the data
    """
    try:
        main_response = sess.get(url, timeout=timeout)
        main_response.raise_for_status()
    except requests.exceptions.Timeout:
        click.echo(f"Error: Request timeout while fetching {url}", err=True)
        sys.exit(1)
    except requests.exceptions.ConnectionError as e:
        click.echo(f"Error: Connection failed while fetching {url}: {e}", err=True)
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        click.echo(f"Error: HTTP error while fetching {url}: {e}", err=True)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        click.echo(f"Error: Request failed while fetching {url}: {e}", err=True)
        sys.exit(1)

    try:
        parsed_node = HTMLParser(main_response.text)
    except Exception as e:
        click.echo(f"Error: Failed to parse HTML response: {e}", err=True)
        sys.exit(1)

    try:
        deps_count_element = parsed_node.css_first('.table-list-header-toggle .btn-link.selected')
        if not deps_count_element:
            click.echo("Error: Could not find dependents count element in page", err=True)
            click.echo("The page structure may have changed or the URL is invalid", err=True)
            sys.exit(1)

        element_text = deps_count_element.text()
        if not element_text:
            click.echo("Error: Dependents count element has no text content", err=True)
            sys.exit(1)

        # Extract number from text (e.g., "1,234 Repositories")
        count_str = element_text.strip().split()[0].replace(',', '')
        max_deps = int(count_str)
        return max_deps

    except (ValueError, IndexError) as e:
        click.echo(f"Error: Could not parse dependents count from page: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: Unexpected error while parsing dependents count: {e}", err=True)
        sys.exit(1)


def validate_github_url(url: str) -> Tuple[str, str]:
    """
    Validate and parse a GitHub repository URL.

    Args:
        url: The GitHub repository URL to validate

    Returns:
        tuple: (owner, repository) if valid

    Raises:
        SystemExit: If URL is invalid with appropriate error message
    """
    if not url or not isinstance(url, str):
        click.echo("Error: URL cannot be empty", err=True)
        sys.exit(1)
    
    try:
        parsed = urlparse(url)
    except Exception as e:
        click.echo(f"Error: Invalid URL format - {e}", err=True)
        sys.exit(1)
    
    # Validate it's a GitHub URL
    if parsed.netloc and parsed.netloc not in ['github.com', 'www.github.com']:
        click.echo(f"Error: URL must be a GitHub repository URL (github.com), got: {parsed.netloc}", err=True)
        sys.exit(1)
    
    # Extract and validate path segments
    path = parsed.path.strip('/')
    if not path:
        click.echo("Error: Invalid GitHub URL - missing repository path", err=True)
        click.echo("Expected format: https://github.com/owner/repository", err=True)
        sys.exit(1)
    
    path_segments = path.split('/')
    
    # GitHub repo URLs should have exactly 2 segments: owner/repo
    if len(path_segments) != 2:
        click.echo("Error: Invalid GitHub repository URL format", err=True)
        click.echo("Expected format: https://github.com/owner/repository", err=True)
        click.echo(f"Got {len(path_segments)} path segment(s): {'/'.join(path_segments)}", err=True)
        sys.exit(1)
    
    owner, repository = path_segments
    
    # Validate owner and repository are not empty
    if not owner or not repository:
        click.echo("Error: Both owner and repository names must be non-empty", err=True)
        click.echo("Expected format: https://github.com/owner/repository", err=True)
        sys.exit(1)
    
    # Basic validation for valid GitHub username/repo name characters
    # GitHub allows alphanumeric, hyphens, underscores, and dots
    import re
    valid_pattern = re.compile(r'^[a-zA-Z0-9._-]+$')
    
    if not valid_pattern.match(owner):
        click.echo(f"Error: Invalid owner name '{owner}' - must contain only alphanumeric characters, dots, hyphens, or underscores", err=True)
        sys.exit(1)
    
    if not valid_pattern.match(repository):
        click.echo(f"Error: Invalid repository name '{repository}' - must contain only alphanumeric characters, dots, hyphens, or underscores", err=True)
        sys.exit(1)
    
    return owner, repository


@click.command()
@click.argument("url")
@click.option("--repositories/--packages", default=True, help="Sort repositories or packages (default repositories)")
@click.option("--table/--json", default=True, help="View mode")
@click.option("--report", is_flag=True, help="Report")
@click.option("--description", is_flag=True, help="Show description of packages or repositories (performs additional "
                                                  "request per repository)")
@click.option("--rows", default=10, help="Number of showing repositories (default=10)")
@click.option("--minstar", default=5, help="Minimum number of stars (default=5)")
@click.option("--search", help="search code at dependents (repositories/packages)")
@click.option("--token", envvar="GHTOPDEP_TOKEN")
def cli(
    url: str,
    repositories: bool,
    search: Optional[str],
    table: bool,
    rows: int,
    minstar: int,
    report: bool,
    description: bool,
    token: Optional[str]
) -> None:
    MODE = os.environ.get("GHTOPDEP_ENV")
    BASE_URL = os.environ.get("GHTOPDEP_BASE_URL")

    # Handle report mode - require BASE_URL to be set
    if report and not BASE_URL:
        click.echo("Error: GHTOPDEP_BASE_URL environment variable is required for report mode", err=True)
        sys.exit(1)

    # Default to development URL if MODE is set to development
    if MODE == "development" and not BASE_URL:
        BASE_URL = 'http://127.0.0.1:3000'

    # Validate and parse the GitHub URL
    owner, repository = validate_github_url(url)

    gh = None

    if report:
        report_url = '{}/repos/{}/{}'.format(BASE_URL, owner, repository)
        try:
            result = requests.get(report_url, timeout=30)

            # Handle successful response
            if result.status_code == 200:
                try:
                    sorted_repos = sort_repos(result.json(), rows)
                    repos = readable_stars(sorted_repos)
                    click.echo(tabulate(repos, headers="keys", tablefmt="github"))
                    sys.exit()
                except (ValueError, KeyError) as e:
                    click.echo(f"Error: Invalid response format from report server: {e}", err=True)
                    sys.exit(1)
            elif result.status_code == 404:
                # 404 means no cached data available, continue with scraping
                pass
            else:
                click.echo(f"Error: Report server returned status {result.status_code}", err=True)
                sys.exit(1)

        except requests.exceptions.Timeout:
            click.echo(f"Error: Report server request timeout ({report_url})", err=True)
            sys.exit(1)
        except requests.exceptions.ConnectionError as e:
            click.echo(f"Error: Could not connect to report server ({BASE_URL}): {e}", err=True)
            sys.exit(1)
        except requests.exceptions.HTTPError as e:
            click.echo(f"Error: HTTP error from report server: {e}", err=True)
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            click.echo(f"Error: Request failed to report server: {e}", err=True)
            sys.exit(1)

    if (description or search) and token:
        gh = github3.login(token=token)
        CacheControl(gh.session,
                     cache=FileCache(CACHE_DIR),
                     heuristic=OneDayHeuristic())
    elif (description or search) and not token:
        click.echo("Please provide token")
        sys.exit(1)

    destination = "repository"
    destinations = "repositories"
    if not repositories:
        destination = "package"
        destinations = "packages"

    repos = []
    seen_urls = set()  # Set-based tracking for O(1) duplicate detection
    more_than_zero_count = 0
    total_repos_count = 0

    sess = requests.session()
    retries = Retry(
        total=15,
        backoff_factor=15,
        status_forcelist=[429])
    adapter = CacheControlAdapter(max_retries=retries,
                                  cache=FileCache(CACHE_DIR),
                                  heuristic=OneDayHeuristic())
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)

    page_url: str | None = "{0}/network/dependents?dependent_type={1}".format(url, destination.upper())
    assert page_url is not None  # Type guard: page_url is definitely a string at this point

    max_deps = get_max_deps(sess, page_url)

    pbar = tqdm(total=max_deps)

    page_count = 0
    while True:
        page_count += 1

        # Safety limit to prevent infinite loops
        if page_count > MAX_PAGES:
            click.echo(f"Warning: Reached maximum page limit ({MAX_PAGES}), stopping pagination", err=True)
            break

        # Type guard: ensure page_url is not None
        if not page_url:
            break

        try:
            response = sess.get(page_url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.Timeout:
            click.echo(f"Warning: Request timeout on page {page_count}, stopping pagination", err=True)
            break
        except requests.exceptions.ConnectionError as e:
            click.echo(f"Warning: Connection error on page {page_count}: {e}", err=True)
            break
        except requests.exceptions.HTTPError as e:
            click.echo(f"Warning: HTTP error on page {page_count}: {e}", err=True)
            break
        except requests.exceptions.RequestException as e:
            click.echo(f"Warning: Request error on page {page_count}: {e}", err=True)
            break

        try:
            parsed_node = HTMLParser(response.text)
        except Exception as e:
            click.echo(f"Warning: Failed to parse HTML on page {page_count}: {e}", err=True)
            break

        try:
            dependents = parsed_node.css(ITEM_SELECTOR)
            total_repos_count += len(dependents)

            for dep in dependents:
                try:
                    repo_stars_list = dep.css(STARS_SELECTOR)
                    # only for ghost or private? packages
                    if not repo_stars_list:
                        continue

                    repo_stars = repo_stars_list[0].text().strip()
                    if not repo_stars:
                        continue

                    try:
                        repo_stars_num = int(repo_stars.replace(",", ""))
                    except ValueError:
                        click.echo(f"Warning: Could not parse star count '{repo_stars}'", err=True)
                        continue

                    if repo_stars_num != 0:
                        more_than_zero_count += 1

                    if repo_stars_num >= minstar:
                        repo_selector_list = dep.css(REPO_SELECTOR)
                        if not repo_selector_list:
                            continue

                        try:
                            relative_repo_url = repo_selector_list[0].attributes.get("href")
                            if not relative_repo_url:
                                continue
                        except (KeyError, AttributeError):
                            click.echo("Warning: Could not extract repository URL", err=True)
                            continue

                        repo_url = "{0}{1}".format(GITHUB_URL, relative_repo_url)

                        # Set-based duplicate detection (O(1) lookup) - can be listed same package
                        if repo_url not in seen_urls and repo_url != url:
                            seen_urls.add(repo_url)
                            if description:
                                repo_description = fetch_description(gh, relative_repo_url)
                                repos.append({
                                    "url": repo_url,
                                    "stars": repo_stars_num,
                                    "description": repo_description
                                })
                            else:
                                repos.append({
                                    "url": repo_url,
                                    "stars": repo_stars_num
                                })

                except Exception as e:
                    click.echo(f"Warning: Error processing dependent on page {page_count}: {e}", err=True)
                    continue

        except Exception as e:
            click.echo(f"Warning: Error processing page {page_count}: {e}", err=True)
            break

        try:
            pagination_buttons = parsed_node.css(NEXT_BUTTON_SELECTOR)

            if len(pagination_buttons) == 2:
                page_url = pagination_buttons[1].attributes.get("href")
                if not page_url:
                    break
            elif pagination_buttons and pagination_buttons[0].text() == "Next":
                page_url = pagination_buttons[0].attributes.get("href")
                if not page_url:
                    break
            elif len(pagination_buttons) == 0 or pagination_buttons[0].text() == "Previous":
                break
            else:
                break

        except Exception as e:
            click.echo(f"Warning: Error reading pagination controls on page {page_count}: {e}", err=True)
            break

        pbar.update(REPOS_PER_PAGE)

    pbar.close()

    if report:
        report_post_url = '{}/repos'.format(BASE_URL)
        try:
            payload = {"url": url, "owner": owner, "repository": repository, "deps": repos}
            response = requests.post(report_post_url, json=payload, timeout=30)

            # Check if the POST was successful
            if response.status_code not in [200, 201]:
                click.echo(f"Error: Report server returned status {response.status_code}", err=True)
                sys.exit(1)

        except requests.exceptions.Timeout:
            click.echo(f"Error: Report server POST request timeout ({report_post_url})", err=True)
            sys.exit(1)
        except requests.exceptions.ConnectionError as e:
            click.echo(f"Error: Could not connect to report server to submit results ({BASE_URL}): {e}", err=True)
            sys.exit(1)
        except requests.exceptions.HTTPError as e:
            click.echo(f"Error: HTTP error while submitting report: {e}", err=True)
            sys.exit(1)
        except requests.exceptions.RequestException as e:
            click.echo(f"Error: Request failed while submitting report: {e}", err=True)
            sys.exit(1)

    sorted_repos = sort_repos(repos, rows)

    if search:
        assert gh is not None, "gh should be initialized when search is provided"
        for repo in repos:
            try:
                repo_path = urlparse(repo["url"]).path[1:]
                if not repo_path:
                    click.echo(f"Warning: Could not extract path from repo URL: {repo['url']}", err=True)
                    continue

                try:
                    search_query = "{0} repo:{1}".format(search, repo_path)
                    search_results = gh.search_code(search_query)

                    for s in search_results:
                        try:
                            if hasattr(s, 'html_url'):
                                click.echo("{0} with {1} stars".format(s.html_url, repo["stars"]))
                            else:
                                click.echo("Warning: Search result missing html_url attribute", err=True)
                        except Exception as e:
                            click.echo(f"Warning: Error displaying search result: {e}", err=True)
                            continue

                except Exception as e:
                    click.echo(f"Warning: GitHub API search failed for {repo_path}: {e}", err=True)
                    continue

            except Exception as e:
                click.echo(f"Warning: Error processing search for repository: {e}", err=True)
                continue
    else:
        show_result(sorted_repos, total_repos_count, more_than_zero_count, destinations, table)
