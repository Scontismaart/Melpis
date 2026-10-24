"""
web_extractor.py
----------------
Recupero e parsing del contenuto testuale da pagine web pubbliche (URL).
Rimuove script, stili, intestazioni e navigazione per estrarre il testo principale.
"""

import asyncio
import ipaddress
from html.parser import HTMLParser
import logging
import re
import socket
import ssl
from urllib.parse import urljoin, urlparse, urlunparse

import httpcore
import httpx

logger = logging.getLogger(__name__)

TAGS_DA_IGNORARE = {
    "script", "style", "nav", "footer", "header", "noscript",
    "svg", "iframe", "button", "input", "form", "select", "option"
}

MAX_WEB_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_REDIRECTS = 5
ALLOWED_PORTS = {"http": {80}, "https": {443}}


def _canonical_host(hostname: str) -> str:
    host = hostname.rstrip(".").lower()
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        return host.encode("idna").decode("ascii")


def _public_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if (not address.is_global or address.is_private or address.is_loopback
                or address.is_link_local or address.is_reserved or address.is_multicast
                or address.is_unspecified):
            return None
        return address
    except ValueError:
        return None


def _parse_public_url(url: str):
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.hostname.lower().rstrip(".") in {"localhost", "localhost.localdomain"}:
            raise ValueError("local_destination")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            if _public_ip(parsed.hostname) is None:
                raise ValueError("local_destination")
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if port not in ALLOWED_PORTS[parsed.scheme]:
            raise ValueError
        hostname = _canonical_host(parsed.hostname)
        if not hostname:
            raise ValueError
        return parsed, hostname, port
    except ValueError as exc:
        if str(exc) == "local_destination":
            raise ValueError("Accesso a indirizzi locali o privati non consentito.") from None
        raise ValueError("URL non valido o destinazione non consentita.") from None
    except UnicodeError:
        raise ValueError("URL non valido o destinazione non consentita.") from None


async def _resolve_public_addresses(hostname: str, port: int) -> list[str]:
    """Resolve once, reject mixed/private answer sets, and return pinned IPs."""
    try:
        ipaddress.ip_address(hostname)
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal:
        literal = _public_ip(hostname)
        if literal is None:
            raise ValueError("Accesso a indirizzi locali o privati non consentito.")
        return [str(literal)]
    try:
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        addresses = []
        for family, _, _, _, sockaddr in records:
            if family not in (socket.AF_INET, socket.AF_INET6):
                raise ValueError("unsafe")
            address = _public_ip(sockaddr[0])
            if address is None:
                raise ValueError("unsafe")
            value = str(address)
            if value not in addresses:
                addresses.append(value)
        if addresses:
            return addresses
    except (OSError, socket.gaierror, ValueError, UnicodeError):
        pass
    raise ValueError("Accesso a indirizzi locali o privati non consentito.")


class _PinnedNetworkBackend:
    """httpcore backend that dials only an already validated address."""

    def __init__(self, hostname: str, port: int, addresses: list[str]):
        self.hostname = hostname
        self.port = port
        self.addresses = tuple(addresses)
        self.backend = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            requested_host = _canonical_host(host)
        except (ValueError, UnicodeError):
            raise httpcore.ConnectError("pinned_host_mismatch") from None
        if requested_host != self.hostname or port != self.port:
            raise httpcore.ConnectError("pinned_host_mismatch")
        # Only the selected vetted IP is passed to the socket layer. TLS SNI and
        # certificate checks still use the original URL hostname in httpcore.
        return await self.backend.connect_tcp(
            self.addresses[0], port, timeout=timeout,
            local_address=local_address, socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("unix_socket_disabled")

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


class _PinnedHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, hostname: str, port: int, addresses: list[str]):
        super().__init__(verify=True, trust_env=False, proxy=None, retries=0)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
            network_backend=_PinnedNetworkBackend(hostname, port, addresses),
        )


def _safe_page_error():
    return ValueError("Impossibile raggiungere la pagina web o destinazione non consentita.")


async def _read_limited_response(response) -> bytes:
    content_encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if content_encoding not in {"", "identity"}:
        raise ValueError("Codifica della risposta web non supportata.")
    content_length = response.headers.get("content-length")
    if content_length is not None:
        if not content_length.isascii() or not content_length.isdigit():
            raise ValueError("Risposta web non valida.") from None
        length = int(content_length)
        if length > MAX_WEB_BYTES:
            raise ValueError("La pagina supera la dimensione massima consentita (5 MB).")
    output = bytearray()
    async for part in response.aiter_raw():
        if len(output) + len(part) > MAX_WEB_BYTES:
            raise ValueError("La pagina supera la dimensione massima consentita (5 MB).")
        output.extend(part)
    return bytes(output)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tag_stack: list[str] = []
        self.text_parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag_lower = tag.lower()
        self.tag_stack.append(tag_lower)
        if tag_lower == "title":
            self._in_title = True
        elif tag_lower in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower == "title":
            self._in_title = False
        if self.tag_stack and self.tag_stack[-1] == tag_lower:
            self.tag_stack.pop()
        elif tag_lower in self.tag_stack:
            while self.tag_stack and self.tag_stack[-1] != tag_lower:
                self.tag_stack.pop()
            if self.tag_stack:
                self.tag_stack.pop()
        if tag_lower in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str):
        if self._in_title and not self.title:
            self.title = data.strip()
        if any(ignored in self.tag_stack for ignored in TAGS_DA_IGNORARE):
            return
        cleaned = data.strip()
        if cleaned:
            self.text_parts.append(cleaned + " ")

    def get_text(self) -> str:
        raw = "".join(self.text_parts)
        # Normalizza righe multiple e spazi vuoti
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]
        filtered = [l for l in lines if l]
        return "\n\n".join(filtered)


async def estrai_da_url(url: str, timeout: float = 15.0) -> dict[str, str]:
    """Scarica ed estrae il testo principale da una pagina web.

    Restituisce un dizionario con 'titolo', 'testo' e 'url'.
    Solleva ValueError in caso di URL non valido, SSRF, errore di rete o contenuto vuoto.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL non valido.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout < float("inf"):
        raise ValueError("Timeout non valido.")
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE):
        url = "https://" + url

    original_url = url

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "identity",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        async with asyncio.timeout(timeout):
            current_url = url
            for redirect_count in range(MAX_REDIRECTS + 1):
                parsed, hostname, port = _parse_public_url(current_url)
                addresses = await _resolve_public_addresses(parsed.hostname, port)
                transport = _PinnedHTTPTransport(hostname, port, addresses)
                request_url = urlunparse(parsed._replace(fragment=""))
                redirect_url = None
                async with httpx.AsyncClient(
                    transport=transport,
                    timeout=httpx.Timeout(timeout),
                    follow_redirects=False,
                    trust_env=False,
                    headers=headers,
                ) as client:
                    async with client.stream("GET", request_url) as resp:
                        if 300 <= resp.status_code < 400:
                            location = resp.headers.get("location")
                            if not location or redirect_count >= MAX_REDIRECTS:
                                raise ValueError("La pagina ha restituito un redirect non consentito.")
                            redirect_url = urljoin(current_url, location)
                        else:
                            if resp.status_code >= 400:
                                raise ValueError("La pagina ha restituito un errore HTTP.")
                            body = await _read_limited_response(resp)
                            content_type = resp.headers.get("content-type", "").lower()
                            charset = resp.encoding or "utf-8"
                            try:
                                response_text = body.decode(charset, errors="replace")
                            except LookupError:
                                response_text = body.decode("utf-8", errors="replace")
                            if "text/html" not in content_type and "application/xhtml" not in content_type:
                                if content_type.startswith("text/"):
                                    testo = response_text.strip()
                                    if not testo:
                                        raise ValueError("La pagina non contiene testo.")
                                    return {"titolo": parsed.netloc, "testo": testo, "url": original_url}
                                raise ValueError("Tipo di contenuto non supportato. L'URL deve puntare a una pagina web.")
                            html_text = response_text
                if redirect_url is None:
                    break
                current_url = redirect_url
            else:
                raise ValueError("Troppi redirect dalla pagina web.")
    except asyncio.TimeoutError:
        raise ValueError("Timeout durante il recupero della pagina web.") from None
    except httpx.RequestError:
        raise _safe_page_error() from None

    parser = _HTMLTextExtractor()
    parser.feed(html_text)
    testo = parser.get_text()

    if not testo or len(testo) < 30:
        raise ValueError("Non è stato possibile estrarre testo sufficiente dalla pagina web.")

    titolo = parser.title or urlparse(original_url).netloc
    return {
        "titolo": titolo[:150],
        "testo": testo,
        "url": original_url,
    }
