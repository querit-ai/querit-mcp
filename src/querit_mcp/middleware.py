"""ASGI middleware for deriving a trusted client IP."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from starlette.types import ASGIApp, Receive, Scope, Send

_INTERNAL_CLIENT_IP_HEADER = b"x-querit-client-ip"


class ClientIPMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        trusted_proxy_cidrs: Iterable[str],
        forwarded_for_header: str,
    ) -> None:
        self.app = app
        self.trusted_networks = tuple(ipaddress.ip_network(cidr) for cidr in trusted_proxy_cidrs)
        self.forwarded_for_header = forwarded_for_header.lower().encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = [(key, value) for key, value in scope.get("headers", []) if key != _INTERNAL_CLIENT_IP_HEADER]
        client_ip = self._resolve_client_ip(scope, headers)
        if client_ip is not None:
            headers.append((_INTERNAL_CLIENT_IP_HEADER, client_ip.encode("ascii")))

        updated_scope = dict(scope)
        updated_scope["headers"] = headers
        await self.app(updated_scope, receive, send)

    def _resolve_client_ip(self, scope: Scope, headers: list[tuple[bytes, bytes]]) -> str | None:
        client = scope.get("client")
        if not client:
            return None
        try:
            peer = ipaddress.ip_address(client[0])
        except ValueError:
            return None

        if not any(peer in network for network in self.trusted_networks):
            return peer.compressed

        forwarded = next((value for key, value in headers if key.lower() == self.forwarded_for_header), None)
        if forwarded is None:
            return None
        candidate = forwarded.decode("latin-1").split(",", maxsplit=1)[0].strip()
        try:
            return ipaddress.ip_address(candidate).compressed
        except ValueError:
            return None
