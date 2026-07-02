"""Workspace join client — send join requests to K12 parent workspace.

Adapted from the Tampermonkey userscript:
  子号加入K12母号代码.txt

The flow:
  1. Use the account's access_token as Bearer token
  2. POST /backend-api/accounts/{workspace_id}/invites/{request|accept}
  3. Auto-accepted by parent workspace (no approval needed)
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from curl_cffi import requests

CHATGPT_BASE = "https://chatgpt.com"


def join_workspace(
    access_token: str,
    workspace_id: str,
    route: str = "request",
    max_retries: int = 3,
    retry_backoff_ms: int = 5000,
    session: requests.Session | None = None,
    proxy: str = "",
) -> dict:
    """Send a single workspace join request.

    Args:
        access_token: The account's Bearer token
        workspace_id: Parent workspace UUID
        route: "request" (child asks to join) or "accept" (child accepts invite)
        max_retries: Max retry attempts on non-auth errors
        retry_backoff_ms: Backoff between retries (multiplied by attempt)
        proxy: SOCKS5/HTTP proxy URL

    Returns:
        {ok: bool, status_code: int, body: str, workspace_id: str}
    """
    device_id = str(uuid.uuid4())
    url = f"{CHATGPT_BASE}/backend-api/accounts/{workspace_id}/invites/{route}"
    headers = {
        "accept": "*/*",
        "authorization": f"Bearer {access_token}",
        "content-type": "application/json",
        "oai-device-id": device_id,
        "oai-language": "en-US",
    }

    if session:
        _session = session
        should_close = False
    else:
        kwargs = {"impersonate": "chrome", "verify": False}
        if proxy:
            kwargs["proxy"] = proxy
        _session = requests.Session(**kwargs)
        should_close = True

    try:
        for attempt in range(max_retries):
            try:
                resp = _session.post(
                    url,
                    headers=headers,
                    data="",
                    timeout=30,
                )
                body = resp.text[:500] if resp.text else ""
                status = resp.status_code

                if status in (401, 403):
                    return {
                        "ok": False,
                        "status_code": status,
                        "body": body,
                        "workspace_id": workspace_id,
                        "error": "Token expired (401/403). Re-login needed.",
                    }

                if resp.ok:
                    return {
                        "ok": True,
                        "status_code": status,
                        "body": body,
                        "workspace_id": workspace_id,
                    }

                # Non-auth error — retry with backoff
                if attempt < max_retries - 1:
                    time.sleep(retry_backoff_ms * (attempt + 1) / 1000.0)

            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_backoff_ms / 1000.0)
                else:
                    return {
                        "ok": False,
                        "status_code": 0,
                        "body": "",
                        "workspace_id": workspace_id,
                        "error": str(e),
                    }

        return {
            "ok": False,
            "status_code": status if 'status' in dir() else 0,
            "body": body if 'body' in dir() else "",
            "workspace_id": workspace_id,
            "error": f"Max retries ({max_retries}) exhausted",
        }
    finally:
        if should_close:
            _session.close()


def join_workspaces(
    access_token: str,
    workspace_ids: list[str],
    route: str = "request",
    max_retries: int = 3,
    retry_backoff_ms: int = 5000,
    interval_ms: int = 1500,
    proxy: str = "",
) -> list[dict]:
    """Join multiple workspaces sequentially.

    Args:
        access_token: The account's Bearer token
        workspace_ids: List of parent workspace UUIDs
        route: "request" or "accept"
        max_retries: Max retries per workspace
        retry_backoff_ms: Backoff between retries
        interval_ms: Delay between different workspace requests
        proxy: SOCKS5/HTTP proxy URL

    Returns:
        List of result dicts, one per workspace_id
    """
    results = []
    session = None
    try:
        kwargs = {"impersonate": "chrome", "verify": False}
        if proxy:
            kwargs["proxy"] = proxy
        session = requests.Session(**kwargs)
        for i, ws_id in enumerate(workspace_ids):
            result = join_workspace(
                access_token=access_token,
                workspace_id=ws_id.strip(),
                route=route,
                max_retries=max_retries,
                retry_backoff_ms=retry_backoff_ms,
                session=session,
                proxy=proxy,
            )
            results.append(result)
            if i < len(workspace_ids) - 1:
                time.sleep(interval_ms / 1000.0)
    finally:
        if session:
            session.close()
    return results
