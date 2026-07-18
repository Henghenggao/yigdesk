"""Decision-ready, read-only tools exposed to Codex through MCP."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class ToolCallError(RuntimeError):
    """A tool failure with a recovery action suitable for an agent."""


class YigdeskToolClient:
    """Small HTTP client for the public read-only Yigdesk surface."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15,
        revision_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.revision_id = revision_id

    def get_deal_context(self) -> dict[str, Any]:
        payload = self._request("/api/state")
        workbook = payload["workbook"]
        return {
            "scenario_id": payload["scenario_id"],
            "request": payload["scenario"],
            "workbook": {
                "fingerprint": workbook["fingerprint"],
                "mode": workbook["mode"],
                "evidence_addresses": [cell["address"] for cell in workbook["cells"]],
            },
            "capabilities": payload["capabilities"],
            "revision": payload.get("revision"),
        }

    def preview_consequence(self) -> dict[str, Any]:
        return self._request("/api/analyze", {})["packet"]

    def inspect_evidence(self, address: str) -> dict[str, Any]:
        if not isinstance(address, str) or "!" not in address:
            raise ToolCallError(
                "A canonical workbook address is required. Choose an address returned by "
                "get_deal_context, for example 'Deal Model!B4'."
            )
        return self._request(f"/api/inspect?address={quote(address)}")

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-Yigdesk-Action": "codex-mcp"}
        if self.revision_id:
            headers["X-Yigdesk-Revision"] = self.revision_id
        request = Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method="GET" if payload is None else "POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = _error_detail(error)
            if error.code == 404:
                raise ToolCallError(
                    f"{detail} Choose an address returned by get_deal_context, then retry "
                    "inspect_evidence with that exact address."
                ) from error
            raise ToolCallError(
                f"Yigdesk rejected the tool call ({error.code}): {detail} "
                "Refresh context and retry only if the source fingerprint is unchanged."
            ) from error
        except (URLError, TimeoutError) as error:
            raise ToolCallError(
                "Yigdesk is unavailable. Confirm the local app is healthy, then retry the tool call."
            ) from error


def _error_detail(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
        return str(payload.get("error") or payload.get("code") or "Request failed.")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "Request failed."
