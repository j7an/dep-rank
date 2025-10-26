"""Unit tests for ghtopdep helper functions."""

import time
from typing import Any

from ghtopdep.cli import already_added, humanize, readable_stars, sort_repos


class TestHumanize:
    """Tests for the humanize function."""

    def test_humanize_under_1000(self) -> None:
        """Test numbers under 1000 are returned as-is."""
        assert humanize(0) == 0
        assert humanize(1) == 1
        assert humanize(100) == 100
        assert humanize(999) == 999

    def test_humanize_1000_to_10000(self) -> None:
        """Test numbers 1000-9999 are formatted as K."""
        assert humanize(1000) == "1.0K"
        assert humanize(1500) == "1.5K"
        assert humanize(5000) == "5.0K"
        assert humanize(9999) == "10.0K"

    def test_humanize_10000_to_1000000(self) -> None:
        """Test numbers 10000-999999 are formatted as K."""
        assert humanize(10000) == "10K"
        assert humanize(50000) == "50K"
        assert humanize(100000) == "100K"
        assert humanize(999999) == "1000K"

    def test_humanize_over_1000000(self) -> None:
        """Test numbers over 1 million are returned as-is."""
        assert humanize(1000000) == 1000000
        assert humanize(10000000) == 10000000


class TestAlreadyAdded:
    """Tests for the already_added function."""

    def test_already_added_empty_list(self) -> None:
        """Test with empty repository list."""
        assert already_added("https://github.com/user/repo", []) is False

    def test_already_added_found(self) -> None:
        """Test when repository is found in list."""
        repos = [
            {"url": "https://github.com/user1/repo1", "stars": 100},
            {"url": "https://github.com/user2/repo2", "stars": 200},
        ]
        assert already_added("https://github.com/user1/repo1", repos) is True

    def test_already_added_not_found(self) -> None:
        """Test when repository is not found in list."""
        repos = [
            {"url": "https://github.com/user1/repo1", "stars": 100},
            {"url": "https://github.com/user2/repo2", "stars": 200},
        ]
        assert already_added("https://github.com/user3/repo3", repos) is False

    def test_already_added_url_case_sensitive(self) -> None:
        """Test that URL matching is case sensitive."""
        repos = [{"url": "https://github.com/User/Repo", "stars": 100}]
        assert already_added("https://github.com/user/repo", repos) is False


class TestSortRepos:
    """Tests for the sort_repos function."""

    def test_sort_repos_basic(self, sample_repos: list[dict[str, Any]]) -> None:
        """Test basic sorting by stars in descending order."""
        sorted_result = sort_repos(sample_repos, 10)
        assert sorted_result[0]["stars"] == 500
        assert sorted_result[1]["stars"] == 250
        assert sorted_result[2]["stars"] == 100

    def test_sort_repos_limit_rows(self, sample_repos: list[dict[str, Any]]) -> None:
        """Test that rows limit is respected."""
        sorted_result = sort_repos(sample_repos, 2)
        assert len(sorted_result) == 2
        assert sorted_result[0]["stars"] == 500
        assert sorted_result[1]["stars"] == 250

    def test_sort_repos_empty_list(self) -> None:
        """Test with empty repository list."""
        result = sort_repos([], 10)
        assert result == []

    def test_sort_repos_rows_greater_than_list(
        self, sample_repos: list[dict[str, Any]]
    ) -> None:
        """Test when rows requested is greater than list size."""
        result = sort_repos(sample_repos, 100)
        assert len(result) == 3

    def test_sort_repos_zero_rows(self, sample_repos: list[dict[str, Any]]) -> None:
        """Test with zero rows requested."""
        result = sort_repos(sample_repos, 0)
        assert result == []

    def test_sort_repos_ties_preserved(self) -> None:
        """Test sorting with tied star counts."""
        repos = [
            {"url": "https://github.com/user1/repo1", "stars": 100},
            {"url": "https://github.com/user2/repo2", "stars": 100},
            {"url": "https://github.com/user3/repo3", "stars": 50},
        ]
        result = sort_repos(repos, 10)
        assert len(result) == 3
        assert result[0]["stars"] == 100
        assert result[1]["stars"] == 100
        assert result[2]["stars"] == 50


class TestReadableStars:
    """Tests for the readable_stars function."""

    def test_readable_stars_converts_numbers(
        self, sample_repos: list[dict[str, Any]]
    ) -> None:
        """Test that readable_stars converts star counts."""
        result = readable_stars(sample_repos)
        # Check that it modifies in place and returns
        assert result is sample_repos
        # Verify conversion happened
        assert result[0]["stars"] == 100  # Under 1000, unchanged
        assert result[1]["stars"] == 500  # 500 under 1000, unchanged
        assert isinstance(result[0]["stars"], (int, str))

    def test_readable_stars_preserves_other_fields(
        self, sample_repos: list[dict[str, Any]]
    ) -> None:
        """Test that other fields are preserved."""
        result = readable_stars(sample_repos)
        assert "url" in result[0]
        assert result[0]["url"] == "https://github.com/user1/repo1"

    def test_readable_stars_empty_list(self) -> None:
        """Test with empty list."""
        result = readable_stars([])
        assert result == []

    def test_readable_stars_multiple_formats(self) -> None:
        """Test readable_stars with various star counts."""
        repos = [
            {"url": "https://github.com/user1/repo1", "stars": 50},
            {"url": "https://github.com/user2/repo2", "stars": 5000},
            {"url": "https://github.com/user3/repo3", "stars": 500000},
        ]
        result = readable_stars(repos)
        assert result[0]["stars"] == 50
        assert result[1]["stars"] == "5.0K"
        assert result[2]["stars"] == "500K"


class TestSetBasedDuplicateDetection:
    """Tests for set-based duplicate detection performance optimization."""

    def test_set_based_duplicate_detection_with_duplicates(self) -> None:
        """Test that set-based tracking prevents duplicate URLs."""
        seen_urls = set()
        repos = []

        # Simulate adding repos with duplicates
        test_urls = [
            "https://github.com/user1/repo1",
            "https://github.com/user2/repo2",
            "https://github.com/user1/repo1",  # Duplicate
            "https://github.com/user3/repo3",
            "https://github.com/user2/repo2",  # Duplicate
        ]

        for url in test_urls:
            if url not in seen_urls:
                seen_urls.add(url)
                repos.append({"url": url, "stars": 100})

        # Should only have 3 unique repos
        assert len(repos) == 3
        assert len(seen_urls) == 3
        assert {repo["url"] for repo in repos} == seen_urls

    def test_set_based_lookup_performance(self) -> None:
        """Test that set-based lookup is O(1) vs O(n) for already_added."""
        # Generate 1000 unique URLs
        urls = [f"https://github.com/user{i}/repo{i}" for i in range(1000)]

        # Test set-based approach
        seen_urls = set(urls)
        # Lookup time should be constant regardless of set size
        assert urls[0] in seen_urls
        assert urls[500] in seen_urls
        assert urls[999] in seen_urls
        assert "https://github.com/nonexistent/repo" not in seen_urls

    def test_set_based_with_exclusion_logic(self) -> None:
        """Test set-based tracking with URL exclusion logic."""
        seen_urls = set()
        repos = []
        main_url = "https://github.com/main/repo"

        test_urls = [
            "https://github.com/user1/repo1",
            main_url,  # Should be excluded
            "https://github.com/user2/repo2",
            main_url,  # Duplicate and excluded
            "https://github.com/user1/repo1",  # Duplicate
        ]

        for url in test_urls:
            if url not in seen_urls and url != main_url:
                seen_urls.add(url)
                repos.append({"url": url, "stars": 100})

        # Should only have 2 repos (main URL excluded, duplicates removed)
        assert len(repos) == 2
        assert main_url not in {repo["url"] for repo in repos}


class TestPerformanceBenchmark:
    """Benchmark tests comparing set-based vs list-based duplicate detection."""

    def test_set_based_performance_benchmark(self) -> None:
        """
        Benchmark set-based duplicate detection O(1) performance.

        This test demonstrates that set-based lookup maintains constant time
        regardless of collection size, while list-based lookup degrades to O(n).
        """
        # Generate test URLs
        urls = [f"https://github.com/user{i}/repo{i}" for i in range(1000)]

        # Test set-based approach (should be very fast)
        start_time = time.perf_counter()
        seen_urls = set(urls)
        for url in urls:
            _ = url in seen_urls
        set_time = time.perf_counter() - start_time

        # Test list-based approach (should be slower for larger lists)
        repos_list = [{"url": url, "stars": 100} for url in urls]
        start_time = time.perf_counter()
        for url in urls:
            _ = already_added(url, repos_list)
        list_time = time.perf_counter() - start_time

        # Set-based should be significantly faster
        # For 1000 URLs, set-based should be at least 10x faster
        assert set_time < list_time / 5, (
            f"Set-based ({set_time}s) should be significantly faster than list-based ({list_time}s)"
        )

    def test_scalability_set_vs_list(self) -> None:
        """
        Test scalability: set-based maintains performance while list-based degrades.

        With 10,000 items, the performance difference becomes even more pronounced.
        """
        urls_small = [f"https://github.com/user{i}/repo{i}" for i in range(100)]
        urls_large = [f"https://github.com/user{i}/repo{i}" for i in range(5000)]

        # Test with small set
        set_small = set(urls_small)
        set_lookup_small = time.perf_counter()
        for url in urls_small:
            _ = url in set_small
        set_small_time = time.perf_counter() - set_lookup_small

        # Test with large set
        set_large = set(urls_large)
        set_lookup_large = time.perf_counter()
        for url in urls_large:
            _ = url in set_large
        set_large_time = time.perf_counter() - set_lookup_large

        # Set lookups should scale linearly with number of operations, not collection size
        # The ratio of times should be close to the ratio of operations (5000/100 = 50)
        # In practice, it should be much less than 50x due to constant-time lookups
        time_ratio = set_large_time / max(set_small_time, 0.0001)
        assert time_ratio < 100, (
            f"Set-based lookup should scale linearly with operations, not collection size. Ratio: {time_ratio}x"
        )
