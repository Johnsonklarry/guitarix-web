"""Node-RED service client connector."""

from __future__ import annotations

from typing import Any

from forge_core.services.config import Credential, validate_base_url
from forge_core.services.http import ServiceClient


class NodeRedClient:
    """Client connector for Node-RED instances."""

    def __init__(
        self,
        base_url: str,
        credential: str | Credential | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = validate_base_url(base_url).rstrip("/")
        if hasattr(credential, "reveal"):
            self._token = credential.reveal()
        else:
            self._token = credential
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self.client = ServiceClient(base_url=self.base_url, default_headers=headers, timeout=timeout)

    def check_connectivity(self) -> bool:
        """Check whether the Node-RED instance is reachable."""
        try:
            status, _, _ = self.client.request("GET", "/settings")
            return 200 <= status < 300
        except Exception:
            return False

    def get_flows(self) -> Any:
        """Query flow configurations and status from the Node-RED instance."""
        status, _, body = self.client.request("GET", "/flows")
        if status != 200:
            raise RuntimeError(f"Failed to fetch flows: HTTP {status}")
        return body

    def get_flow_status(self) -> Any:
        """Query Node-RED flow status via the flows endpoint."""
        return self.get_flows()
