"""HTTP client for HC Account Migration backend API with HMAC-SHA256 authentication."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp

logger = logging.getLogger("red.dabblebot.hclinkapprovals.client")


class HCLinkClientError(Exception):
    """Base exception for HCLink client errors."""


class HCLinkNetworkError(HCLinkClientError):
    """Network connection failure or timeout after retry."""


class HCLinkAPIError(HCLinkClientError):
    """HTTP error returned by backend API."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"API Error [{status_code}]: {detail}")
        self.status_code = status_code
        self.detail = detail


class HCLinkAuthError(HCLinkAPIError):
    """Authentication or signature verification failure."""


def generate_hmac_signature(secret: str, timestamp: int, body: bytes) -> str:
    """
    Generate canonical HMAC-SHA256 signature mirroring backend's hmac_auth.py.
    Format: timestamp (int string) + ':' + body bytes, HMAC-SHA256, lowercase hex.
    """
    msg = f"{timestamp}:".encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def create_auth_headers(
    secret: str,
    body: bytes = b"",
    timestamp: Optional[int] = None,
) -> Dict[str, str]:
    """
    Build canonical HMAC authentication headers.
    Provides both X-HCLink-* and X-HC-* headers for full backend interop.
    """
    ts = timestamp if timestamp is not None else int(time.time())
    sig = generate_hmac_signature(secret, ts, body)
    return {
        "X-HCLink-Timestamp": str(ts),
        "X-HCLink-Signature": sig,
        "X-HC-Timestamp": str(ts),
        "X-HC-Signature": sig,
    }


class HCLinkClient:
    """Asynchronous client for the HC Account Migration backend."""

    def __init__(
        self,
        base_url: str = "https://migrate.navalbattlezone.com:9443",
        shared_secret: str = "",
        timeout_seconds: float = 10.0,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.shared_secret = shared_secret
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close underlying client session if owned."""
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()

    def update_config(self, base_url: Optional[str] = None, shared_secret: Optional[str] = None) -> None:
        """Update client endpoint or shared secret."""
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        if shared_secret is not None:
            self.shared_secret = shared_secret

    async def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        max_retries: int = 1,
    ) -> Any:
        """
        Execute an HTTP request with HMAC signing and single retry on network error.
        """
        url = f"{self.base_url}{path}"
        body_bytes = b""
        headers: Dict[str, str] = {}

        if json_data is not None:
            body_bytes = json.dumps(json_data, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"

        # Sign request with HMAC secret
        if self.shared_secret:
            auth_headers = create_auth_headers(self.shared_secret, body=body_bytes)
            headers.update(auth_headers)

        session = await self._get_session()
        attempts = 0
        last_error: Optional[Exception] = None

        while attempts <= max_retries:
            attempts += 1
            try:
                async with session.request(
                    method=method,
                    url=url,
                    data=body_bytes if body_bytes else None,
                    headers=headers,
                    timeout=self.timeout,
                ) as resp:
                    resp_text = await resp.text()

                    # Try parsing JSON response
                    try:
                        data = json.loads(resp_text) if resp_text else {}
                    except json.JSONDecodeError:
                        data = {"detail": resp_text}

                    if resp.status in (200, 201):
                        return data

                    detail = data.get("detail", resp_text) if isinstance(data, dict) else resp_text
                    if resp.status == 401:
                        raise HCLinkAuthError(resp.status, detail)
                    raise HCLinkAPIError(resp.status, detail)

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                logger.warning(
                    "Network error calling %s %s (attempt %d/%d): %s",
                    method,
                    url,
                    attempts,
                    max_retries + 1,
                    exc,
                )
                if attempts <= max_retries:
                    await asyncio.sleep(0.5)
                    continue

        raise HCLinkNetworkError(f"Network error after {max_retries + 1} attempts calling {url}: {last_error}")

    async def get_pending_reviews(self) -> List[Dict[str, Any]]:
        """Fetch list of pending migration sessions in staff_review state."""
        result = await self._request("GET", "/api/staff/pending")
        if isinstance(result, list):
            return result
        return []

    async def get_session_detail(self, token: str) -> Dict[str, Any]:
        """Fetch detail and verification object for a specific session."""
        result = await self._request("GET", f"/api/staff/{token}/detail")
        if isinstance(result, dict):
            return result
        return {}

    async def get_session_plots(self, token: str) -> List[str]:
        """Fetch list of plot / ship names for a specific session."""
        result = await self._request("GET", f"/api/staff/{token}/plots")
        if isinstance(result, list):
            return [str(p) for p in result]
        if isinstance(result, dict) and "plots" in result:
            return [str(p) for p in result["plots"]]
        return []

    async def approve_migration(
        self,
        token: str,
        discord_id: str,
        old_uuid: Optional[str] = None,
        old_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Approve a migration session."""
        payload: Dict[str, Any] = {"discord_id": str(discord_id)}
        if old_uuid:
            payload["old_uuid"] = old_uuid
        if old_name:
            payload["old_name"] = old_name
        return await self._request("POST", f"/api/staff/{token}/approve", json_data=payload)

    async def reject_migration(
        self,
        token: str,
        discord_id: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reject a migration session."""
        payload: Dict[str, Any] = {"discord_id": str(discord_id)}
        if reason:
            payload["reason"] = reason
        return await self._request("POST", f"/api/staff/{token}/reject", json_data=payload)

    async def health_check(self) -> Tuple[bool, str, Optional[int]]:
        """
        Check connectivity and HMAC authentication against backend.
        Returns (is_ok, message, pending_count).
        """
        try:
            pending = await self.get_pending_reviews()
            return True, f"Connected successfully ({len(pending)} pending reviews).", len(pending)
        except HCLinkAuthError as auth_err:
            return False, f"HMAC authentication failed: {auth_err.detail}", None
        except HCLinkAPIError as api_err:
            return False, f"Backend returned HTTP {api_err.status_code}: {api_err.detail}", None
        except HCLinkNetworkError as net_err:
            return False, f"Network connection failed: {net_err}", None
        except Exception as exc:
            return False, f"Unexpected error: {exc}", None
