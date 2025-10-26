"""Tests for URL validation and parsing functions."""

import pytest

from ghtopdep.cli import validate_github_url


class TestValidateGithubUrl:
    """Tests for the validate_github_url function."""

    def test_valid_github_url(self) -> None:
        """Test validation of valid GitHub URL."""
        owner, repo = validate_github_url("https://github.com/user/myrepo")
        assert owner == "user"
        assert repo == "myrepo"

    def test_valid_github_url_www(self) -> None:
        """Test validation of valid GitHub URL with www."""
        owner, repo = validate_github_url("https://www.github.com/user/myrepo")
        assert owner == "user"
        assert repo == "myrepo"

    def test_valid_github_url_http(self) -> None:
        """Test validation of GitHub URL with http (not https)."""
        owner, repo = validate_github_url("http://github.com/user/myrepo")
        assert owner == "user"
        assert repo == "myrepo"

    def test_github_url_with_trailing_slash(self) -> None:
        """Test validation of GitHub URL with trailing slash."""
        owner, repo = validate_github_url("https://github.com/user/myrepo/")
        assert owner == "user"
        assert repo == "myrepo"

    def test_github_url_with_hyphen(self) -> None:
        """Test validation of GitHub URL with hyphen in names."""
        owner, repo = validate_github_url("https://github.com/my-user/my-repo")
        assert owner == "my-user"
        assert repo == "my-repo"

    def test_github_url_with_underscore(self) -> None:
        """Test validation of GitHub URL with underscore in names."""
        owner, repo = validate_github_url("https://github.com/my_user/my_repo")
        assert owner == "my_user"
        assert repo == "my_repo"

    def test_github_url_with_dot(self) -> None:
        """Test validation of GitHub URL with dot in names."""
        owner, repo = validate_github_url("https://github.com/my.user/my.repo")
        assert owner == "my.user"
        assert repo == "my.repo"

    def test_github_url_with_numbers(self) -> None:
        """Test validation of GitHub URL with numbers."""
        owner, repo = validate_github_url("https://github.com/user123/repo456")
        assert owner == "user123"
        assert repo == "repo456"

    def test_empty_url_raises_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that empty URL raises SystemExit."""
        with pytest.raises(SystemExit):
            validate_github_url("")
        captured = capsys.readouterr()
        assert "Error: URL cannot be empty" in captured.err

    def test_none_url_raises_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that None URL raises SystemExit."""
        with pytest.raises(SystemExit):
            validate_github_url(None)  # type: ignore[arg-type]
        captured = capsys.readouterr()
        assert "Error: URL cannot be empty" in captured.err

    def test_invalid_netloc_raises_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that non-GitHub URL raises error."""
        with pytest.raises(SystemExit):
            validate_github_url("https://gitlab.com/user/repo")
        captured = capsys.readouterr()
        assert "Error: URL must be a GitHub repository URL" in captured.err

    def test_missing_repo_path_raises_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that URL without path raises error."""
        with pytest.raises(SystemExit):
            validate_github_url("https://github.com")
        captured = capsys.readouterr()
        assert "Error: Invalid GitHub URL - missing repository path" in captured.err

    def test_missing_repo_name_raises_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that URL with only owner raises error."""
        with pytest.raises(SystemExit):
            validate_github_url("https://github.com/user")
        captured = capsys.readouterr()
        assert "Error: Invalid GitHub repository URL format" in captured.err

    def test_too_many_path_segments_raises_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that URL with too many path segments raises error."""
        with pytest.raises(SystemExit):
            validate_github_url("https://github.com/user/repo/extra")
        captured = capsys.readouterr()
        assert "Error: Invalid GitHub repository URL format" in captured.err

    def test_empty_owner_raises_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that empty owner name raises error."""
        with pytest.raises(SystemExit):
            validate_github_url("https://github.com//repo")
        captured = capsys.readouterr()
        assert "Error: Invalid GitHub repository URL format" in captured.err

    def test_empty_repo_raises_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that empty repo name raises error."""
        with pytest.raises(SystemExit):
            validate_github_url("https://github.com/user/")
        captured = capsys.readouterr()
        assert "Error: Invalid GitHub repository URL format" in captured.err

    def test_invalid_owner_characters_raises_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that invalid characters in owner raise error."""
        with pytest.raises(SystemExit):
            validate_github_url("https://github.com/user@invalid/repo")
        captured = capsys.readouterr()
        assert "Error: Invalid owner name" in captured.err

    def test_invalid_repo_characters_raises_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that invalid characters in repo raise error."""
        with pytest.raises(SystemExit):
            validate_github_url("https://github.com/user/repo@invalid")
        captured = capsys.readouterr()
        assert "Error: Invalid repository name" in captured.err

    def test_owner_with_spaces_raises_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that spaces in owner raise error."""
        with pytest.raises(SystemExit):
            validate_github_url("https://github.com/user name/repo")
        captured = capsys.readouterr()
        assert "Error: Invalid owner name" in captured.err

    def test_repo_with_spaces_raises_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that spaces in repo raise error."""
        with pytest.raises(SystemExit):
            validate_github_url("https://github.com/user/repo name")
        captured = capsys.readouterr()
        assert "Error: Invalid repository name" in captured.err

    def test_github_url_case_sensitive(self) -> None:
        """Test that validation preserves case."""
        owner, repo = validate_github_url("https://github.com/MyUser/MyRepo")
        assert owner == "MyUser"
        assert repo == "MyRepo"

    def test_url_with_query_parameters(self) -> None:
        """Test that URL with query parameters is handled."""
        # urlparse strips query parameters from path, so they are ignored
        owner, repo = validate_github_url("https://github.com/user/repo?tab=readme")
        assert owner == "user"
        assert repo == "repo"

    def test_url_with_fragment(self) -> None:
        """Test that URL with fragment is handled."""
        # urlparse strips fragment from path, so they are ignored
        owner, repo = validate_github_url("https://github.com/user/repo#readme")
        assert owner == "user"
        assert repo == "repo"

    def test_special_characters_in_owner(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test various special characters that should fail."""
        invalid_urls = [
            "https://github.com/user!/repo",
            "https://github.com/user#/repo",
            "https://github.com/user$/repo",
            "https://github.com/user%/repo",
        ]
        for url in invalid_urls:
            with pytest.raises(SystemExit):
                validate_github_url(url)
            captured = capsys.readouterr()
            assert "Error:" in captured.err

    def test_special_characters_in_repo(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test various special characters in repo that should fail."""
        invalid_urls = [
            "https://github.com/user/repo!",
            "https://github.com/user/repo$",
            "https://github.com/user/repo%",
        ]
        for url in invalid_urls:
            with pytest.raises(SystemExit):
                validate_github_url(url)
            captured = capsys.readouterr()
            assert "Error: Invalid repository name" in captured.err
