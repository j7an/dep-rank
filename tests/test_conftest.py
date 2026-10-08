"""Self-tests for the shared HTTP transport double."""

import json

import httpx2
import pytest

from tests.conftest import FakeHTTP


def _client(fake: FakeHTTP) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(fake))


async def test_registrations_consumed_once_in_order() -> None:
    fake = FakeHTTP()
    fake.get("https://x.test/a?q=1", status=200, body=b"one")
    fake.get("https://x.test/a?q=1", status=429)
    async with _client(fake) as c:
        assert (await c.get("https://x.test/a", params={"q": 1})).content == b"one"
        assert (await c.get("https://x.test/a?q=1")).status_code == 429
        with pytest.raises(httpx2.ConnectError):
            await c.get("https://x.test/a?q=1")
    assert sum(len(v) for v in fake.requests.values()) == 3


async def test_payload_headers_exception_and_callback() -> None:
    fake = FakeHTTP()
    fake.post("https://x.test/j", payload={"k": 1}, headers={"ETag": '"e"'})
    fake.get("https://x.test/boom", exception=httpx2.ReadTimeout("slow"))

    async def cb(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(304, headers={"X-Seen": request.headers["If-None-Match"]})

    fake.get("https://x.test/cb", callback=cb)
    async with _client(fake) as c:
        r = await c.post("https://x.test/j", json={"query": "q"})
        assert r.json() == {"k": 1} and r.headers["ETag"] == '"e"'
        with pytest.raises(httpx2.ReadTimeout):
            await c.get("https://x.test/boom")
        assert (await c.get("https://x.test/cb", headers={"If-None-Match": "v"})).headers[
            "X-Seen"
        ] == "v"
    (req,) = fake.requests[("POST", "https://x.test/j")]
    assert json.loads(req.content) == {"query": "q"}
