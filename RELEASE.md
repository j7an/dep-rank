# Release Guide

Quick reference for maintainers releasing a new version of `dep-rank`.

## Prerequisites

These are already configured. Verify once if something seems broken:

- [ ] CI is passing on `main`
- [ ] PyPI Trusted Publisher configured for the `pypi` environment
  (Settings → Environments → `pypi` → required reviewer set)
- [ ] TestPyPI Trusted Publisher configured for the `testpypi` environment
- [ ] Both environments use OIDC — no API tokens needed
- [ ] `Release Bot` GitHub App installed on this repo (needed to push signed
  tags; bypasses the recursion guard that blocks `GITHUB_TOKEN` tag pushes)
- [ ] `RELEASE_BOT_APP_ID` repository variable set
  (Settings → Secrets and variables → Actions → Variables tab)
- [ ] `RELEASE_BOT_PRIVATE_KEY` stored in **all three** required stores (same
  key value in each — see "Release Bot Credential Topology" below):
  - Repo **Actions** secret
    (Settings → Secrets and variables → Actions → Repository secrets)
  - Repo **Dependabot** secret
    (Settings → Secrets and variables → Dependabot → Repository secrets)
  - **`release` environment** secret
    (Settings → Environments → `release` → Environment secrets)
- [ ] `release` environment restricted to `main`
  (Settings → Environments → `release` → Deployment branches and tags →
  "Selected branches and tags" → pattern `main`)

---

## Release Bot Credential Topology

One GitHub App (`RELEASE_BOT_APP_ID`) signs tags and opens maintenance PRs. Its
private key is stored in three places because three workflows run in contexts
that cannot share a single store. **All three intentionally hold the same key
value** — this duplication is an accepted trade-off (a separate app per trust
level was considered and deferred).

| Store | Used by | Why this store |
|-------|---------|----------------|
| `release` environment secret | `tag-release` (Actions → Tag Release) | The pinned shared `tag-release` reusable workflow declares `environment: release`. GitHub uses the **environment** secret instead of the caller-passed one, which is what scopes the release credential to the environment. |
| Repo **Actions** secret | `pre-commit-autoupdate.yml` | Reads the secret directly in a normal scheduled job. |
| Repo **Dependabot** secret | `ci.yml` → `update-lockfile` | Dependabot-triggered runs can read **only** Dependabot secrets. |

**Access gating:**

- The **`release` environment** restricts the release credential to the `main`
  branch (deployment-branch policy, pattern `main`). This is the GitHub-enforced
  guard that `tag-release` can only run from `main`.
- The **`pypi` environment** remains the human publication-approval gate: it
  requires a reviewer before `release.yml` publishes to PyPI. `tag-release` is
  manually dispatched and intentionally has **no** reviewer/wait-timer on
  `release` — the human checkpoint lives at `pypi`.

> Note: the GitHub API can confirm each secret **exists** and when it was
> updated, but not that the three values are equal. Treat equality as an
> invariant you maintain during rotation (below), not something you can verify
> after the fact.

---

## Rotating the Release Bot Private Key

GitHub Apps support multiple active private keys, so rotation is zero-downtime:
add the new key everywhere, verify, then remove the old one.

1. **Generate** a new private key for the Release Bot App
   (App settings → Private keys → Generate a private key → downloads a `.pem`).
   Leave the existing key active for now.
2. **Update all three stores** with the new key's full `.pem` contents
   (they must stay equal — see topology table):
   - Settings → Secrets and variables → **Actions** → `RELEASE_BOT_PRIVATE_KEY`
   - Settings → Secrets and variables → **Dependabot** → `RELEASE_BOT_PRIVATE_KEY`
   - Settings → Environments → **`release`** → `RELEASE_BOT_PRIVATE_KEY`
3. **Verify each consumer** with the new key before deleting the old one:
   - **pre-commit autoupdate** (Actions store): `gh workflow run pre-commit-autoupdate.yml`,
     then confirm the run succeeds (it mints an app token early).
   - **lockfile update** (Dependabot store): the `update-lockfile` job runs only
     on Dependabot PRs and cannot be dispatched manually. Confirm via the next
     Dependabot PR's `update-lockfile` job succeeding, or re-run that job on the
     most recent open Dependabot PR's CI run. If no Dependabot PR is open, this
     store is validated by the next one.
   - **tag-release** (`release` env store): exercised by the next real release.
     A failed mint surfaces as the "Mint GitHub App token" step failing.
4. **Delete the old private key** in the App settings once all three consumers
   have run successfully on the new key.

---

## Releasing a New Version

### Stable release (UI-driven, recommended)

1. Go to **Actions → Tag Release → Run workflow**
2. Select the `main` branch and pick a `bump`:
   - `auto` — infer from Conventional Commits since the last tag
     (`feat:` → minor, `fix:` / `chore:` / `docs:` → patch,
     `<type>!:` or `BREAKING CHANGE:` → major)
   - `patch` / `minor` / `major` — override the auto analysis
3. Click **Run workflow**. The shared `tag-release.yml` reusable workflow
   computes the next version, creates and pushes a signed `vX.Y.Z` tag
   via the Release Bot App
4. The `v*` tag push triggers `release.yml` (test → build → TestPyPI →
   PyPI → GitHub Release)

The "auto" bump analyzer reads commit subjects since the last tag, so
**Conventional Commits** matter: every commit in a PR should match the PR's
user-visible intent, not the per-commit diff shape. A stray `feat:` in an
otherwise-`fix:` PR will flip a patch release to minor.

### Pre-release (manual tag push)

The UI only offers `auto/patch/minor/major`, so pre-releases use a manual
tag push. The same `release.yml` runs and the classifier flags the release
as prerelease automatically:

    git checkout main && git pull origin main
    git tag v0.2.0rc1       # or v0.2.0a1, v0.2.0b1, v0.2.0.dev1
    git push origin v0.2.0rc1

Use **PEP 440 canonical** forms (no hyphens): `a1` / `b1` / `rc1` / `.dev1`.

---

## What Happens Next (Automated)

The `release.yml` workflow runs five jobs in sequence:

| Job | What it does |
|-----|--------------|
| `test` | Runs linting, type checking, and tests as a CI gate |
| `build` | Verifies the tag is on `main`, builds sdist + wheel via `uv build`, uploads artifacts |
| `publish-testpypi` | Publishes to TestPyPI, polls for availability, installs and smoke-tests the package |
| `publish-pypi` | **Waits for a required reviewer to approve** the `pypi` environment, then publishes |
| `github-release` | Creates a **draft** GitHub Release with auto-generated notes and attached artifacts |

Monitor progress at:
`https://github.com/j7an/dep-rank/actions`

---

## After the Workflow Completes

- [ ] Approve the `pypi` environment deployment when GitHub prompts you
- [ ] Verify the live package: `pip install "dep-rank==${VERSION}"`
- [ ] Smoke-test: `dep-rank --version`
- [ ] Open the draft GitHub Release, review auto-generated notes, and click **Publish release**

---

## Recovering from a Failed Release

### Build job failed (stable release)

The tag already exists. Fix the issue on `main`, then delete the tag and
re-run the Tag Release workflow:

    git tag -d "v${VERSION}"
    git push origin ":refs/tags/v${VERSION}"
    # merge the fix to main, then:
    # Actions → Tag Release → Run workflow → same bump as before

### Build job failed (pre-release)

Same delete-and-re-push, but the re-tag is manual:

    git tag -d "v${VERSION}"
    git push origin ":refs/tags/v${VERSION}"
    # merge the fix to main, then:
    git tag "v${VERSION}" && git push origin "v${VERSION}"

### Published to TestPyPI but PyPI failed

The wheel and sdist are already uploaded to TestPyPI (immutable). You can
publish to PyPI manually using the artifacts from the failed workflow run,
or bump to a patch version and re-release via the Tag Release workflow.

### GitHub Release not created

The `github-release` job only runs if `publish-pypi` succeeds. If it was
skipped, create the release manually:

    gh release create "v${VERSION}" dist/* \
      --title "v${VERSION}" \
      --generate-notes \
      --draft
