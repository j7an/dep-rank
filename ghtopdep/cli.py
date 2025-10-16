import calendar
import json
import os
import sys
import textwrap
import datetime
from email.utils import formatdate, parsedate
from urllib.parse import urlparse

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

if pipdate.needs_checking(PACKAGE_NAME):
    msg = pipdate.check(PACKAGE_NAME, __version__)
    click.echo(msg)


class OneDayHeuristic(BaseHeuristic):
    cacheable_by_default_statuses = {
        200, 203, 204, 206, 300, 301, 404, 405, 410, 414, 501
    }

    def update_headers(self, response):
        if response.status not in self.cacheable_by_default_statuses:
            return {}

        date = parsedate(response.headers["date"])
        expires = datetime.datetime(*date[:6]) + datetime.timedelta(days=1)
        return {"expires": formatdate(calendar.timegm(expires.timetuple())), "cache-control": "public"}

    def warning(self, response):
        msg = "Automatically cached! Response is Stale."
        return "110 - {0}".format(msg)


def already_added(repo_url, repos):
    for repo in repos:
        if repo['url'] == repo_url:
            return True
    return False


def fetch_description(gh, relative_url):
    _, owner, repository = relative_url.split("/")
    repository = gh.repository(owner, repository)
    repo_description = " "
    if repository.description:
        repo_description = textwrap.shorten(repository.description, width=60, placeholder="...")
    return repo_description


def sort_repos(repos, rows):
    sorted_repos = sorted(repos, key=lambda i: i["stars"], reverse=True)
    return sorted_repos[:rows]


def humanize(num):
    if num < 1_000:
        return num
    elif num < 10_000:
        return "{}K".format(round(num / 100) / 10)
    elif num < 1_000_000:
        return "{}K".format(round(num / 1_000))
    else:
        return num


def readable_stars(repos):
    for repo in repos:
        repo["stars"] = humanize(repo["stars"])
    return repos


def show_result(repos, total_repos_count, more_than_zero_count, destinations, table):
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


def get_max_deps(sess, url):
    main_response = sess.get(url)
    parsed_node = HTMLParser(main_response.text)

    deps_count_element = parsed_node.css_first('.table-list-header-toggle .btn-link.selected')
    max_deps = int(deps_count_element.text().strip().split()[0].replace(',', ''))
    return max_deps


def validate_github_url(url):
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
        click.echo(f"Error: Invalid GitHub repository URL format", err=True)
        click.echo(f"Expected format: https://github.com/owner/repository", err=True)
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
def cli(url, repositories, search, table, rows, minstar, report, description, token):
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
        try:
            result = requests.get('{}/repos/{}/{}'.format(BASE_URL, owner, repository))
            if result.status_code != 404:
                sorted_repos = sort_repos(result.json(), rows)
                repos = readable_stars(sorted_repos)
                click.echo(tabulate(repos, headers="keys", tablefmt="github"))
                sys.exit()
        except requests.exceptions.ConnectionError as e:
            click.echo(e)

    if (description or search) and token:
        gh = github3.login(token=token)
        CacheControl(gh.session,
                     cache=FileCache(CACHE_DIR),
                     heuristic=OneDayHeuristic())
    elif (description or search) and not token:
        click.echo("Please provide token")
        sys.exit()

    destination = "repository"
    destinations = "repositories"
    if not repositories:
        destination = "package"
        destinations = "packages"

    repos = []
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

    page_url = "{0}/network/dependents?dependent_type={1}".format(url, destination.upper())
    
    max_deps = get_max_deps(sess, page_url)

    pbar = tqdm(total=max_deps)

    while True:
        response = sess.get(page_url)
        parsed_node = HTMLParser(response.text)
        dependents = parsed_node.css(ITEM_SELECTOR)
        total_repos_count += len(dependents)
        for dep in dependents:
            repo_stars_list = dep.css(STARS_SELECTOR)
            # only for ghost or private? packages
            if repo_stars_list:
                repo_stars = repo_stars_list[0].text().strip()
                repo_stars_num = int(repo_stars.replace(",", ""))
            else:
                continue

            if repo_stars_num != 0:
                more_than_zero_count += 1
            if repo_stars_num >= minstar:
                relative_repo_url = dep.css(REPO_SELECTOR)[0].attributes["href"]
                repo_url = "{0}{1}".format(GITHUB_URL, relative_repo_url)

                # can be listed same package
                is_already_added = already_added(repo_url, repos)
                if not is_already_added and repo_url != url:
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

        pagination_buttons = parsed_node.css(NEXT_BUTTON_SELECTOR)

        if len(pagination_buttons) == 2:
            page_url = pagination_buttons[1].attributes["href"]
        elif pagination_buttons and pagination_buttons[0].text() == "Next":
            page_url = pagination_buttons[0].attributes["href"]
        elif len(pagination_buttons) == 0 or pagination_buttons[0].text() == "Previous":
            break

        pbar.update(REPOS_PER_PAGE)

    pbar.close()

    if report:
        try:
            requests.post('{}/repos'.format(BASE_URL), json={"url": url, "owner": owner, "repository": repository, "deps": repos})
        except requests.exceptions.ConnectionError as e:
            click.echo(e)

    sorted_repos = sort_repos(repos, rows)

    if search:
        for repo in repos:
            repo_path = urlparse(repo["url"]).path[1:]
            for s in gh.search_code("{0} repo:{1}".format(search, repo_path)):
                click.echo("{0} with {1} stars".format(s.html_url, repo["stars"]))
    else:
        show_result(sorted_repos, total_repos_count, more_than_zero_count, destinations, table)
