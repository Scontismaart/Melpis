"""Network-free SSRF, pinned-DNS, redirect, and streamed-size regressions."""

import asyncio
import socket

import httpcore
import httpx
import pytest

from src.core.documenti import web_extractor as web


PUBLIC_IPS = {
    "public.example": "93.184.216.34",
    "next.example": "1.1.1.1",
}


class _BytesStream(httpx.AsyncByteStream):
    def __init__(self, content):
        self.content = content

    async def __aiter__(self):
        yield self.content

    async def aclose(self):
        return None


def _mock_network(monkeypatch, handler):
    resolved = []
    transports = []

    async def resolve(host, port):
        resolved.append((host, port))
        if host not in PUBLIC_IPS:
            raise ValueError("Accesso a indirizzi locali o privati non consentito.")
        return [PUBLIC_IPS[host]]

    def make_transport(hostname, port, addresses):
        transports.append((hostname, port, tuple(addresses)))
        return httpx.MockTransport(handler)

    monkeypatch.setattr(web, "_resolve_public_addresses", resolve)
    monkeypatch.setattr(web, "_PinnedHTTPTransport", make_transport)
    return resolved, transports


@pytest.mark.parametrize(
    "url",
    [
        "http://user:password@public.example/",
        "https://@public.example/",
        "http://public.example:8080/",
        "https://public.example:8443/",
        "file:///etc/passwd",
    ],
)
@pytest.mark.asyncio
async def test_credentials_unsupported_scheme_and_nonstandard_ports_are_rejected(
    monkeypatch, url,
):
    async def unexpected_resolve(*_args):
        pytest.fail("rejected URL must not trigger DNS")

    monkeypatch.setattr(web, "_resolve_public_addresses", unexpected_resolve)
    with pytest.raises(ValueError):
        await web.estrai_da_url(url)


@pytest.mark.asyncio
async def test_private_literal_redirect_is_rejected_before_a_second_request(monkeypatch):
    requests = []
    original_resolve = web._resolve_public_addresses

    async def handler(request):
        requests.append(request.url)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    async def resolve(host, port):
        if host == "public.example":
            return [PUBLIC_IPS[host]]
        return await original_resolve(host, port)

    monkeypatch.setattr(web, "_resolve_public_addresses", resolve)
    monkeypatch.setattr(web, "_PinnedHTTPTransport", lambda *_args: httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="Accesso a indirizzi locali o privati non consentito"):
        await web.estrai_da_url("https://public.example/start")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_mixed_public_and_private_dns_answers_fail_closed(monkeypatch):
    async def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.2.3.4", 443)),
        ]

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="Accesso a indirizzi locali o privati non consentito"):
        await web._resolve_public_addresses("mixed.example", 443)


@pytest.mark.asyncio
async def test_public_dns_addresses_are_normalized_for_connection_pinning(monkeypatch):
    async def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
        ]

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    assert await web._resolve_public_addresses("public.example", 443) == [
        "93.184.216.34", "2606:4700:4700::1111",
    ]


@pytest.mark.asyncio
async def test_pinned_backend_connects_to_validated_ip_not_re_resolved_hostname():
    backend = web._PinnedNetworkBackend("public.example", 443, ["93.184.216.34"])
    connected = []

    class FakeBackend:
        async def connect_tcp(self, host, port, **kwargs):
            connected.append((host, port, kwargs))
            return object()

    backend.backend = FakeBackend()
    await backend.connect_tcp("public.example", 443, timeout=2.0)
    assert connected[0][0:2] == ("93.184.216.34", 443)

    with pytest.raises(httpcore.ConnectError, match="pinned_host_mismatch"):
        await backend.connect_tcp("other.example", 443)


@pytest.mark.asyncio
async def test_production_transport_constructs_direct_pinned_pool_without_proxy():
    transport = web._PinnedHTTPTransport("public.example", 443, ["93.184.216.34"])
    try:
        backend = transport._pool._network_backend
        assert isinstance(backend, web._PinnedNetworkBackend)
        assert backend.hostname == "public.example"
        assert backend.addresses == ("93.184.216.34",)
        assert transport._pool._proxy is None
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_each_redirect_hop_is_validated_and_uses_its_pinned_address(monkeypatch):
    seen = []

    async def handler(request):
        seen.append((request.url.host, request.url.path))
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "https://next.example/article"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            stream=_BytesStream(b"<html><title>Test</title><body><p>Useful public article text for extraction.</p></body></html>"),
        )

    resolved, pinned = _mock_network(monkeypatch, handler)
    result = await web.estrai_da_url("https://public.example/start")

    assert resolved == [("public.example", 443), ("next.example", 443)]
    assert pinned == [
        ("public.example", 443, (PUBLIC_IPS["public.example"],)),
        ("next.example", 443, (PUBLIC_IPS["next.example"],)),
    ]
    assert seen == [("public.example", "/start"), ("next.example", "/article")]
    assert result["titolo"] == "Test"
    assert result["url"] == "https://public.example/start"


@pytest.mark.asyncio
async def test_redirect_loop_is_bounded(monkeypatch):
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(302, headers={"location": "/again"})

    _mock_network(monkeypatch, handler)
    with pytest.raises(ValueError, match="redirect non consentito"):
        await web.estrai_da_url("https://public.example/start")
    assert count == web.MAX_REDIRECTS + 1


@pytest.mark.asyncio
async def test_streamed_body_cap_applies_without_content_length(monkeypatch):
    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * web.MAX_WEB_BYTES
            yield b"y"

        async def aclose(self):
            return None

    async def handler(_request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, stream=OversizedStream())

    _mock_network(monkeypatch, handler)
    with pytest.raises(ValueError, match="dimensione massima consentita"):
        await web.estrai_da_url("https://public.example/plain")


@pytest.mark.asyncio
async def test_compressed_response_is_rejected_without_unbounded_decompression(monkeypatch):
    async def handler(_request):
        return httpx.Response(
            200,
            headers={"content-type": "text/plain", "content-encoding": "gzip"},
            stream=_BytesStream(b"small compressed body"),
        )

    _mock_network(monkeypatch, handler)
    with pytest.raises(ValueError, match="Codifica della risposta web non supportata"):
        await web.estrai_da_url("https://public.example/plain")


@pytest.mark.asyncio
async def test_download_timeout_is_bounded_and_error_does_not_expose_transport_details(monkeypatch):
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.05)
            yield b"late"

        async def aclose(self):
            return None

    async def handler(_request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, stream=SlowStream())

    _mock_network(monkeypatch, handler)
    with pytest.raises(ValueError, match="Timeout") as error:
        await web.estrai_da_url("https://public.example/plain", timeout=0.005)
    assert "public.example" not in str(error.value)


@pytest.mark.asyncio
async def test_environment_proxy_settings_are_ignored(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9999")
    observed = []
    base_client = httpx.AsyncClient

    class TrustEnvCheckClient(base_client):
        def __init__(self, *args, **kwargs):
            observed.append((kwargs.get("trust_env"), kwargs.get("follow_redirects")))
            super().__init__(*args, **kwargs)

    async def handler(_request):
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, stream=_BytesStream(b"safe response"),
        )

    monkeypatch.setattr(httpx, "AsyncClient", TrustEnvCheckClient)
    _mock_network(monkeypatch, handler)
    result = await web.estrai_da_url("https://public.example/plain")
    assert result["testo"] == "safe response"
    assert observed == [(False, False)]
