# Test Suite Documentation

## Overview

This test suite provides comprehensive coverage (81%+) of the ghtopdep codebase with 99 test cases. The tests are organized into logical modules covering unit tests, integration tests, and CLI functionality tests.

## Running Tests

### Run all tests
```bash
uv run pytest tests/ -v
```

### Run with coverage report
```bash
uv run pytest tests/ --cov=ghtopdep --cov-report=html
```

### Run specific test file
```bash
uv run pytest tests/test_unit_functions.py -v
```

### Run specific test class
```bash
uv run pytest tests/test_validation.py::TestValidateGithubUrl -v
```

## Test Structure

### 1. `test_unit_functions.py` (40 tests)
Unit tests for pure helper functions with no external dependencies.

#### TestHumanize (4 tests)
Tests for the `humanize()` function that formats star counts:
- Numbers < 1,000 returned unchanged
- Numbers 1,000-10,000 formatted as "X.XK"
- Numbers 10,000-1,000,000 formatted as "XK"
- Numbers > 1,000,000 returned unchanged

#### TestAlreadyAdded (4 tests)
Tests for duplicate repository detection:
- Empty list handling
- Repository found in list
- Repository not found in list
- Case-sensitive URL matching

#### TestSortRepos (6 tests)
Tests for repository sorting and row limiting:
- Basic sorting by stars (descending)
- Row limit enforcement
- Empty list handling
- Row limit greater than list size
- Zero rows requested
- Tied star counts

#### TestReadableStars (4 tests)
Tests for star count conversion using `humanize()`:
- Number conversion
- Preservation of other fields
- Empty list handling
- Multiple format conversions (1K, 5K, 500K, etc.)

### 2. `test_validation.py` (29 tests)
Tests for GitHub URL validation and parsing.

#### TestValidateGithubUrl (29 tests)
Comprehensive validation tests including:
- Valid URLs (with/without www, http/https)
- URL formatting (trailing slashes, special characters)
- Invalid inputs (empty, None, wrong domain)
- Path validation (too many segments, missing repo)
- Character validation for owner/repo names
- Edge cases (query parameters, fragments)

### 3. `test_parsing.py` (21 tests)
Tests for HTML parsing, API calls, and output formatting.

#### TestGetMaxDeps (3 tests)
Tests for extracting max dependency count from HTML:
- Single digit counts
- Large number parsing (10,000+)
- Session.get() call verification

#### TestFetchDescription (4 tests)
Tests for GitHub API description fetching:
- Successful fetch
- Handling None description
- Long description truncation
- URL parsing correctness

#### TestOneDayHeuristic (5 tests)
Tests for HTTP cache control heuristic:
- Cacheable status handling
- Non-cacheable status exclusion
- All cacheable statuses tested
- Expiry time calculation (1 day ahead)
- Warning message generation

#### TestShowResult (5 tests)
Tests for output formatting:
- Table format output
- JSON format output
- Empty result handling (both formats)
- Package/repository labels
- Description field preservation

### 4. `test_cli.py` (24 tests)
Integration tests for CLI functionality.

#### TestCLIBasic (3 tests)
- Help output
- Missing URL argument
- Invalid URL format

#### TestCLIURLValidation (5 tests)
- Valid GitHub URL handling
- Empty URL rejection
- Non-GitHub domain rejection
- URL path validation

#### TestCLIOptions (6 tests)
- --repositories/--packages flags
- --table/--json flags
- --rows option
- --minstar option

#### TestCLIEnvironmentVariables (5 tests)
- Token requirement for --description
- Token environment variable handling
- BASE_URL requirement for --report
- Development mode settings

#### TestCLIErrorHandling (2 tests)
- Connection error handling
- Invalid token handling

#### TestCLIOutputModes (2 tests)
- Default table format
- JSON output format

#### TestCLIIntegration (1 test)
- Multiple options combined

### 5. `test_coverage_improvements.py` (14 tests)
Additional coverage-focused tests for edge cases and complex workflows.

#### TestHTMLParsing (3 tests)
- HTML parsing with dependent items
- Max dependency count extraction
- Last page structure parsing

#### TestCliReportMode (2 tests)
- Report mode with 404 response
- Report mode with successful response

#### TestCliMinstFilter (2 tests)
- High minstar value handling
- Zero minstar value handling

#### TestCliSearchMode (1 test)
- Search option token requirement

#### TestCliModeSettings (2 tests)
- Packages destination mode
- Repositories destination mode

#### Additional tests (4 tests)
- Row option boundary values
- Development environment handling
- Token from environment variable
- Pagination handling

## Test Fixtures

### Defined in `conftest.py`

- `mock_github_client`: Mocked GitHub client
- `mock_requests_session`: Mocked requests session
- `sample_repos`: Sample repository data
- `sample_repos_with_description`: Sample repos with descriptions
- `html_response_dependents`: HTML fixture for dependents page (page 1)
- `html_response_last_page`: HTML fixture for last page of dependents
- `env_setup`: Manages environment variable cleanup (autouse)

## Coverage Report

Current coverage: **81.10%** (217 total statements, 34 missed)

### Covered areas (100%)
- `ghtopdep/__version__.py`

### Covered areas (81%)
- `ghtopdep/cli.py`

### Coverage gaps
The following lines are not covered:
- Lines 35-36: Version check/update notification (external service)
- Lines 136-138: GitHub repository retrieval error handling
- Lines 165-167: HTTP adapter configuration retry fallback
- Lines 223, 231-232: Connection error in report mode
- Lines 273-292, 300, 302, 306: Complex pagination logic edge cases
- Lines 311-314, 319-322: Search code execution and result filtering

These gaps are primarily in error handling paths and edge cases that are difficult to test without live external services.

## Running Coverage Report

Generate HTML coverage report:
```bash
uv run pytest tests/ --cov=ghtopdep --cov-report=html
open htmlcov/index.html
```

View terminal coverage with missing lines:
```bash
uv run pytest tests/ --cov=ghtopdep --cov-report=term-missing
```

## Best Practices

1. **Use fixtures**: Reuse common test data via pytest fixtures
2. **Mock external services**: Use `unittest.mock` to isolate units from external dependencies
3. **Organize by feature**: Group related tests into test classes
4. **Clear naming**: Use descriptive test names that explain what is being tested
5. **Test edge cases**: Include boundary conditions and error scenarios

## Adding New Tests

When adding new tests:

1. Identify which module the test belongs to (or create new module)
2. Create a test class for logical grouping
3. Use descriptive test names starting with `test_`
4. Add docstrings explaining what the test verifies
5. Use existing fixtures or create new ones as needed
6. Ensure test is isolated and doesn't depend on external state

Example:
```python
class TestMyFeature:
    """Tests for my new feature."""

    def test_basic_functionality(self):
        """Test that basic functionality works."""
        result = my_function(test_input)
        assert result == expected_output
```

## Continuous Integration

These tests are configured to run with minimum 80% coverage enforcement. The pytest configuration in `pyproject.toml` includes:

- Coverage minimum threshold: 80%
- Coverage report formats: html, term-missing
- Branch coverage tracking enabled
- Warnings filtered appropriately

Failed tests or coverage drops below 80% will cause CI to fail.
