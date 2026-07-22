from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from typing import Callable
from urllib.parse import urlsplit, urlunsplit


# Product URLs are data: they are persisted, embedded in signed event IDs and
# ultimately shown in email and the private dashboard.  Keep the allow-list
# next to that boundary rather than duplicating permissive checks in every
# adapter.  Host names are exact, except that an entry also permits its own
# subdomains (for example ``aliexpress.com`` permits ``www.aliexpress.com``).
MERCHANT_HOSTS_BY_SITE_ID: dict[str, frozenset[str]] = {
    "fr:Castorama": frozenset({"castorama.fr", "www.castorama.fr"}),
    "fr:Auchan": frozenset({"auchan.fr", "www.auchan.fr"}),
    "fr:Boulanger": frozenset({"boulanger.com", "www.boulanger.com"}),
    "fr:Brico Dépôt France": frozenset({"bricodepot.fr", "www.bricodepot.fr"}),
    "fr:Costway France": frozenset({"costway.fr", "www.costway.fr"}),
    "fr:Rue du Commerce": frozenset({"rueducommerce.fr", "www.rueducommerce.fr"}),
    "fr:Electro Dépôt France": frozenset({"electrodepot.fr", "www.electrodepot.fr"}),
    "fr:EcoFlow France": frozenset({"fr.ecoflow.com"}),
    "fr:E.Leclerc France": frozenset({"e.leclerc", "www.e.leclerc"}),
    "fr:Maison Energy": frozenset({"maison-energy.com", "www.maison-energy.com"}),
    "fr:Create France": frozenset({"create-store.com", "www.create-store.com"}),
    "fr:Evolarshop France": frozenset({"evolarshop.fr", "www.evolarshop.fr"}),
    "fr:Klarstein France": frozenset({"klarstein.fr", "www.klarstein.fr"}),
    "fr:Trotec France": frozenset({"fr.trotec.com"}),
    "fr:De'Longhi France": frozenset({"delonghi.com", "www.delonghi.com"}),
    "fr:Lidl France": frozenset({"lidl.fr", "www.lidl.fr"}),
    "fr:Action France": frozenset({"action.com", "www.action.com"}),
    "fr:H2R Équipements": frozenset({"h2r-equipements.com", "www.h2r-equipements.com"}),
    "fr:Obelink France": frozenset({"obelink.fr", "www.obelink.fr"}),
    "fr:Narbonne Accessoires": frozenset({"narbonneaccessoires.fr", "www.narbonneaccessoires.fr"}),
    "fr:Mon Camping Car": frozenset({"mon-camping-car.com", "www.mon-camping-car.com"}),
    "nl:Coolblue": frozenset({"coolblue.nl", "www.coolblue.nl"}),
    "nl:MediaMarkt": frozenset({"mediamarkt.nl", "www.mediamarkt.nl"}),
    "nl:EP.nl": frozenset({"ep.nl", "www.ep.nl"}),
    "nl:Electro World": frozenset({"electroworld.nl", "www.electroworld.nl"}),
    "nl:Wehkamp": frozenset({"wehkamp.nl", "www.wehkamp.nl"}),
    "nl:Lidl": frozenset({"lidl.nl", "www.lidl.nl"}),
    "nl:GAMMA": frozenset({"gamma.nl", "www.gamma.nl"}),
    "nl:KARWEI": frozenset({"karwei.nl", "www.karwei.nl"}),
    "nl:Praxis": frozenset({"praxis.nl", "www.praxis.nl"}),
    "nl:Alternate.nl": frozenset({"alternate.nl", "www.alternate.nl"}),
    "nl:Trotec": frozenset({"nl.trotec.com"}),
    "nl:Klarstein": frozenset({"klarstein.nl", "www.klarstein.nl"}),
    "nl:FlinQ": frozenset({"flinqproducts.nl", "www.flinqproducts.nl"}),
    "nl:Action Webshop": frozenset({"shop.action.com"}),
    "nl:Expert.nl": frozenset({"expert.nl", "www.expert.nl"}),
    "nl:De'Longhi NL": frozenset({"delonghi.com", "www.delonghi.com"}),
    "nl:Obelink": frozenset({"obelink.nl", "www.obelink.nl"}),
    "nl:Kampeerwereld": frozenset({"kampeerwereld.nl", "www.kampeerwereld.nl"}),
    "nl:Create NL": frozenset({"create-store.com", "www.create-store.com"}),
    "nl:Costway NL": frozenset({"costway.com", "nl.costway.com"}),
    "nl:Evolarshop": frozenset({"evolarshop.nl", "www.evolarshop.nl"}),
    "nl:Airco voor in huis": frozenset({"aircovoorinhuis.nl", "www.aircovoorinhuis.nl"}),
    "nl:Solago": frozenset({"solago.nl", "www.solago.nl"}),
    "nl:Hubo": frozenset({"hubo.nl", "www.hubo.nl"}),
    "nl:Vrijbuiter": frozenset({"vrijbuiter.nl", "www.vrijbuiter.nl"}),
    "nl:Klimaatshop": frozenset({"klimaatshop.nl", "www.klimaatshop.nl"}),
    "nl:Airco-Webwinkel": frozenset({"airco-webwinkel.nl", "www.airco-webwinkel.nl"}),
    "nl:Bostools": frozenset({"bostools.nl", "www.bostools.nl"}),
    # Not production-registered yet, but their official API payloads still go
    # through the same model/event validation in diagnostics.
    "fr:AliExpress": frozenset({"aliexpress.com"}),
    "nl:AliExpress": frozenset({"aliexpress.com"}),
    "nl:Airco Tracker": frozenset({"airco-tracker.eu"}),
}

AFFILIATE_HOSTS = frozenset({"awin1.com", "www.awin1.com", "s.click.aliexpress.com"})

HostResolver = Callable[[str], Iterable[str]]


def normalized_https_url(value: str, *, max_length: int = 4_096) -> str:
    """Return a normalized public HTTPS URL or raise ``ValueError``.

    User-info, non-standard ports, control characters and fragments are not
    valid in persisted product links.  Stripping fragments also keeps product
    identity deterministic.
    """

    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError("Invalid HTTPS URL")
    candidate = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        raise ValueError("Invalid HTTPS URL")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid HTTPS URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ValueError("Invalid HTTPS URL")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ValueError("Invalid HTTPS URL") from exc
    if not host or len(host) > 253:
        raise ValueError("Invalid HTTPS URL")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # Fetching an IP literal is never necessary for a retailer adapter and
        # makes DNS/merchant policy impossible to enforce consistently.
        raise ValueError("IP literals are not allowed in HTTPS URLs")
    path = parsed.path or "/"
    return urlunsplit(("https", host, path, parsed.query, ""))


def validate_public_https_url(
    value: str,
    *,
    resolver: HostResolver | None = None,
    max_length: int = 2_000,
) -> str:
    """Normalize a URL and prove that all current DNS answers are public.

    The hardened fetcher calls this immediately before each network request,
    including every retry and redirect hop.  Rejecting a hostname when *any*
    answer is non-public prevents dual-answer DNS from smuggling a private
    target alongside a public address.

    ``.test`` is RFC-reserved and cannot be delegated in public DNS.  It is
    accepted without resolution solely so deterministic transport unit tests
    can replace the HTTP session without depending on the network.
    """

    normalized = normalized_https_url(value, max_length=max_length)
    host = (urlsplit(normalized).hostname or "").lower().rstrip(".")
    if host.endswith(".test"):
        return normalized

    resolve = resolver or _system_host_addresses
    try:
        addresses = tuple(dict.fromkeys(str(address).strip() for address in resolve(host)))
    except (OSError, socket.gaierror) as exc:
        raise RuntimeError(f"Could not resolve HTTPS host {host}") from exc
    if not addresses or any(not address for address in addresses):
        raise RuntimeError(f"Could not resolve HTTPS host {host}")

    for raw_address in addresses:
        # A scoped IPv6 address is local by construction.  Removing the scope
        # only makes parsing deterministic; the address still fails is_global.
        address_text = raw_address.split("%", 1)[0]
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:
            raise RuntimeError(f"Resolver returned an invalid address for {host}") from exc
        if (
            not address.is_global
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError(f"HTTPS host {host} resolves to a non-public address")
    return normalized


def merchant_hosts_for_site(site: str) -> frozenset[str]:
    """Return the configured network boundary for a display-name site.

    Adapters are country-bound in the registry only after construction, so
    discovery code knows the display name but not always its final ``site_id``.
    Unioning the entries with the same display name is safe because each entry
    is still an explicitly reviewed merchant hostname.
    """

    display_name = str(site).strip()
    hosts: set[str] = set()
    for site_id, configured in MERCHANT_HOSTS_BY_SITE_ID.items():
        if site_id.split(":", 1)[-1] == display_name:
            hosts.update(configured)
    if not hosts:
        raise ValueError(f"No merchant host allow-list is configured for {display_name}")
    return frozenset(hosts)


def validate_discovered_merchant_url(value: str, *, site: str) -> str:
    """Validate a URL discovered in retailer-controlled HTML/XML/JSON."""

    normalized = normalized_https_url(value, max_length=2_000)
    host = (urlsplit(normalized).hostname or "").lower()
    allowed = merchant_hosts_for_site(site)
    if not host_is_allowed(host, allowed):
        raise ValueError(f"Discovered URL host is not allowed for {site}")
    return normalized


def _system_host_addresses(host: str) -> tuple[str, ...]:
    records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return tuple(str(record[4][0]) for record in records)


def validate_product_url(value: str, *, site_id: str) -> str:
    normalized = normalized_https_url(value, max_length=2_000)
    host = (urlsplit(normalized).hostname or "").lower()
    allowed = MERCHANT_HOSTS_BY_SITE_ID.get(site_id)
    if allowed is None and ":" in site_id:
        # Parser helpers construct Products before the country registry applies
        # its explicit market. Resolve that short-lived default-country site ID
        # by the unique display name; the final Product is validated again when
        # the registry replaces its country.
        display_name = site_id.split(":", 1)[1]
        candidates = [
            hosts
            for candidate_site_id, hosts in MERCHANT_HOSTS_BY_SITE_ID.items()
            if candidate_site_id.split(":", 1)[-1] == display_name
        ]
        if len(candidates) == 1:
            allowed = candidates[0]
    # RFC 2606's .test namespace is intentionally non-routable and is used by
    # the unit suite's synthetic adapters. Unknown real merchants fail closed.
    if host.endswith(".test"):
        return normalized
    if not allowed or not host_is_allowed(host, allowed):
        raise ValueError(f"Product URL host is not allowed for {site_id}")
    return normalized


def validate_affiliate_url(value: str) -> str:
    normalized = normalized_https_url(value)
    host = (urlsplit(normalized).hostname or "").lower()
    if not host_is_allowed(host, AFFILIATE_HOSTS):
        raise ValueError("Affiliate URL host is not allowed")
    return normalized


def host_is_allowed(host: str, allowed_hosts: Iterable[str]) -> bool:
    candidate = host.lower().rstrip(".")
    return any(
        candidate == allowed.lower().rstrip(".")
        or candidate.endswith("." + allowed.lower().rstrip("."))
        for allowed in allowed_hosts
    )


def redirect_host_allowed(origin_host: str, destination_host: str, extra_hosts: Iterable[str] = ()) -> bool:
    """Allow redirects only to the same host, its www peer, or an explicit host.

    Public-suffix parsing is deliberately avoided. Treating the last two DNS
    labels as a registrable domain would make unrelated ``*.co.uk`` (and
    similar) hosts equivalent. Any sibling/API/CDN subdomain therefore needs a
    narrow per-call allow-list; only an exact ``www``/bare-host change is
    implicit.
    """

    origin = origin_host.lower().rstrip(".")
    destination = destination_host.lower().rstrip(".")
    if host_is_allowed(destination, extra_hosts):
        return True
    if destination == origin:
        return True
    if origin.startswith("www."):
        return destination == origin[4:]
    return destination == "www." + origin
