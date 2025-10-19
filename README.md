# GHTOPDEP

CLI tool for sorting dependents repo by stars

> **Note:** This repository is a fork and independent continuation of the original [ghtopdep](https://github.com/andriyor/ghtopdep) by Andriy Orehov. This fork is **not published to PyPI** and is distributed via git-only installation.

## Requirements

- Python 3.11 and up
- Python development libraries

## Installation

### From git repository

```sh
$ pip install git+https://github.com/j7an/ghtopdep.git#egg=ghtopdep
```

### From source

```sh
$ git clone https://github.com/j7an/ghtopdep
$ cd ghtopdep
$ pip install .
```

Or using UV (recommended):

```sh
$ git clone https://github.com/j7an/ghtopdep
$ cd ghtopdep
$ uv pip install .
```

### Using docker (from source)

First `docker build` the image once:

```sh
$ git clone https://github.com/j7an/ghtopdep
$ cd ghtopdep
$ docker build . -t ghtopdep
```

Then you can `docker run` it:

```sh
$ docker run --rm -it ghtopdep --help
```

## Python development Installation

### Ubuntu/Debian

```sh
sudo apt install python3-dev
```

### CentOS/RHEL

```sh
sudo yum install python3-devel
```

## Version upgrade

```sh
# Navigate to your cloned repository
$ cd ghtopdep
# Pull latest changes
$ git pull origin main
# Reinstall with new updates
$ pip install --upgrade .
```

## Usage

If you want retrieve packages or repositories description you need pass token.
To prevent rale limit being exceeded for unauthentIcated requests, ghtopdep needs an access token.
For public repositories, [create a token](https://github.com/settings/tokens/new?scopes=public_repo&description=ghtopdep)
with the public_repo permission.

### Environment Variables

You can configure ghtopdep using environment variables in `~/.bashrc` or `~/.zshrc`:

**GHTOPDEP_TOKEN** - GitHub personal access token for API requests:
```sh
export GHTOPDEP_TOKEN="********************"
```

**GHTOPDEP_BASE_URL** - Base URL for report mode (required when using `--report` flag):
```sh
export GHTOPDEP_BASE_URL="https://your-server.com"
```

**GHTOPDEP_ENV** - Set to "development" to use local development server (defaults to `http://127.0.0.1:3000`):
```sh
export GHTOPDEP_ENV="development"
```

Alternatively, you can pass the token as option --token

```sh
➜ ghtopdep --help
Usage: ghtopdep [OPTIONS] URL

Options:
  --repositories / --packages  Sort repositories or packages (default
                               repositories)
  --table / --json             View mode
  --description                Show description of packages or repositories
                               (performs additional request per repository)
  --rows INTEGER               Number of showing repositories (default=10)
  --minstar INTEGER            Minimum number of stars (default=5)
  --search TEXT                search code at dependents
                               (repositories/packages)
  --token TEXT
  --help                       Show this message and exit.
```

### Table view (by default)

```sh
➜ ghtopdep https://github.com/pytest-dev/pytest
| url                                               | stars   |
|---------------------------------------------------|---------|
| https://github.com/pallets/flask                  | 67K     |
| https://github.com/encode/httpx                   | 13K     |
| https://github.com/tiangolo/fastapi               | 75K     |
| https://github.com/psf/black                      | 38K     |
| https://github.com/python-poetry/poetry           | 31K     |
| https://github.com/pre-commit/pre-commit          | 12K     |
| https://github.com/pytest-dev/pytest-cov          | 1.7K    |
| https://github.com/pytest-dev/pytest-asyncio      | 1.4K    |
| https://github.com/pytest-dev/pytest-mock         | 1.8K    |
| https://github.com/spulec/freezegun               | 4.0K    |
found 1800 repositories others repositories are private
found 950 repositories with more than zero star
~ via 🐍 3.11.0 took 2m 15s
```

### JSON view

```sh
➜ ghtopdep https://github.com/pytest-dev/pytest --json
[{"url": "https://github.com/tiangolo/fastapi", "stars": 75000}, {"url": "https://github.com/pallets/flask", "stars": 67000}, {"url": "https://github.com/psf/black", "stars": 38000}, {"url": "https://github.com/python-poetry/poetry", "stars": 31000}, {"url": "https://github.com/encode/httpx", "stars": 13000}, {"url": "https://github.com/pre-commit/pre-commit", "stars": 12000}, {"url": "https://github.com/spulec/freezegun", "stars": 4000}, {"url": "https://github.com/pytest-dev/pytest-mock", "stars": 1800}, {"url": "https://github.com/pytest-dev/pytest-cov", "stars": 1700}, {"url": "https://github.com/pytest-dev/pytest-asyncio", "stars": 1400}]
```

you can sort packages and fetch their description

```sh
➜ ghtopdep https://github.com/pytest-dev/pytest --description --packages
| url                                            | stars   | description                                                  |
|------------------------------------------------|---------|--------------------------------------------------------------|
| https://github.com/pytest-dev/pytest-cov       | 1.7K    | Coverage plugin for pytest                                   |
| https://github.com/pytest-dev/pytest-asyncio   | 1.4K    | Pytest support for asyncio                                   |
| https://github.com/pytest-dev/pytest-mock      | 1.8K    | Thin-wrapper around the mock package for easier use with... |
| https://github.com/pytest-dev/pytest-xdist     | 1.4K    | pytest plugin for distributed testing and loop-on-failures   |
| https://github.com/pytest-dev/pytest-django    | 1.3K    | A Django plugin for pytest                                   |
| https://github.com/Teemu/pytest-sugar          | 1.1K    | A plugin that changes the default look and feel of pytest    |
| https://github.com/pytest-dev/pytest-timeout   | 567     | pytest plugin to abort hanging tests                         |
| https://github.com/pytest-dev/pytest-html      | 724     | Plugin for generating HTML reports for pytest results        |
| https://github.com/eisensheng/pytest-catchlog  | 89      | py.test plugin to catch log messages                         |
| https://github.com/man-group/pytest-plugins    | 531     | A grab-bag of nifty pytest plugins                           |
found 420 packages others packages are private
found 280 packages with more than zero star
```

also ghtopdep support code searching at dependents (repositories/packages)

```sh
➜ ghtopdep https://github.com/rob-balfre/svelte-select --search=isMulti --minstar=0
https://github.com/andriyor/linkorg-frontend/blob/7eed49c332f127c8541281b85def80e54c882920/src/App.svelte with 0 stars
https://github.com/andriyor/linkorg-frontend/blob/7eed49c332f127c8541281b85def80e54c882920/src/providers/Post.svelte with 0 stars
https://github.com/jdgaravito/bitagora_frontend/blob/776a23f5e848995d3eba90563d55c96429470c48/src/Events/AddEvent.svelte with 0 stars
https://github.com/gopear/OlcsoSor/blob/b1fa1d877a59f7daf41a86fecb21137c91652d77/src/routes/index.svelte with 3 stars
https://github.com/openstate/allmanak/blob/ff9ac0833e5e63f7c17f99c5c2355b4e46c48148/app/src/routes/index.svelte with 3 stars
https://github.com/openstate/allmanak/blob/e6d7aa72a8878eefc6f63a27c983894de1cef294/app/src/components/ReportForm.svelte with 3 stars
https://github.com/wolbodo/members/blob/d091f1e44b4e8cb8cc31f39ea6f6e9c36211d019/sapper/src/components/Member.html with 1 stars
```

## Development setup

Using [UV](https://docs.astral.sh/uv/) - a fast Python package and project manager

### Installing UV

**macOS and Linux:**
```sh
$ curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**
```powershell
$ powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Alternative (via pip):**
```sh
$ pip install uv
```

### Basic Development Workflow

**Install dependencies:**
```sh
$ uv sync
```

**Run the application:**
```sh
$ uv run ghtopdep https://github.com/pytest-dev/pytest
```

**Build the package:**
```sh
$ uv build
```

**Publish to PyPI:**
```sh
$ uv publish
```

### Common Development Tasks

**Add a new dependency:**
```sh
$ uv add <package-name>
```

**Add a development dependency:**
```sh
$ uv add --dev <package-name>
```

**Remove a dependency:**
```sh
$ uv remove <package-name>
```

**Run tests:**
```sh
$ uv run pytest
```

**Run any Python script:**
```sh
$ uv run python script.py
```

## License

[MIT](https://choosealicense.com/licenses/mit/)

## Alternatives

[ghtracker](https://github.com/zer0yu/ghtracker) - not provide full result https://github.com/zer0yu/ghtracker/issues/2

[github-by-stars](https://github.com/hacker-DOM/github-by-stars) - complex setup


## References

[Allow dependents to be sorted by stars · Issue #1537 · isaacs/github](https://github.com/isaacs/github/issues/1537)

[Sorting the insights dependency graph lists · community · Discussion #5575](https://github.com/orgs/community/discussions/5575)
