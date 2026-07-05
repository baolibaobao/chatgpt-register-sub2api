"""Pipeline orchestrator — wires register → join → re-login → export.

The complete flow for one account:
  [1] Register account → get personal-scope tokens
  [2] Join parent K12 workspace → auto-accepted
  [3] Re-login with Team space selection → get team-scope tokens
  [4] Export team-scope tokens as sub2api JSON

Each account proceeds independently through all 4 stages.
Results are written to registered_accounts.json after each success.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chatgpt_register_sub2api.register.registrar import register_worker
from chatgpt_register_sub2api.workspace.joiner import join_workspaces
from chatgpt_register_sub2api.login.login_flow import re_login_for_team_token
from chatgpt_register_sub2api.export.sub2api import export_sub2api_json
from chatgpt_register_sub2api.utils.jwt import decode_jwt_payload

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def load_accounts(path: Path) -> list[dict[str, Any]]:
    """Load registered accounts from JSON file."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_accounts(path: Path, accounts: list[dict[str, Any]]) -> None:
    """Save accounts to JSON file (atomic write)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(accounts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def merge_accounts(
    existing: list[dict[str, Any]],
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge account records by email, preserving unrelated existing accounts."""
    merged: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for account in existing:
        email = str(account.get("email") or "").strip().lower()
        if email:
            index[email] = len(merged)
        merged.append(account)
    for account in updates:
        email = str(account.get("email") or "").strip().lower()
        if email and email in index:
            merged[index[email]] = account
        else:
            if email:
                index[email] = len(merged)
            merged.append(account)
    return merged


# ── Pipeline stages ─────────────────────────────────────────────────


def run_register(
    config: dict[str, Any],
    accounts_file: Path,
    count: int | None = None,
) -> list[dict[str, Any]]:
    """Stage 1: Register N ChatGPT accounts.

    Returns list of newly registered account records.
    """
    reg_cfg = config.get("registration", {})
    mail_cfg = config.get("mail", {})
    proxy_cfg = config.get("proxy", {})

    total = count or int(reg_cfg.get("total", 10))
    threads = int(reg_cfg.get("threads", 3))
    proxy = str(proxy_cfg.get("url", "")).strip()
    flaresolverr_url = str(proxy_cfg.get("flaresolverr_url", "")).strip()

    logger.info(f"Starting registration: {total} accounts, {threads} threads")
    if proxy:
        logger.info(f"Proxy: {proxy}")
    if flaresolverr_url:
        logger.info(f"FlareSolverr: {flaresolverr_url}")

    results: list[dict[str, Any]] = []
    existing = load_accounts(accounts_file)
    success_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {
            executor.submit(
                register_worker,
                index=i,
                proxy=proxy,
                flaresolverr_url=flaresolverr_url,
                mail_config=mail_cfg,
            ): i
            for i in range(1, total + 1)
        }

        for future in as_completed(futures):
            result = future.result()
            if result["ok"]:
                success_count += 1
                account = result["result"]
                results.append(account)
                existing.append(account)
                save_accounts(accounts_file, existing)
                logger.info(
                    f"[{result['index']}/{total}] ✓ {account['email']} "
                    f"({result.get('cost_seconds', 0):.1f}s)"
                )
            else:
                fail_count += 1
                logger.warning(
                    f"[{result['index']}/{total}] ✗ {result.get('error', 'unknown')}"
                )

    logger.info(
        f"Registration complete: {success_count} success, {fail_count} failed"
    )
    return results


def run_join_workspace(
    config: dict[str, Any],
    accounts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stage 2: Join each account to the K12 parent workspace.

    Modifies account records in-place with join status.
    """
    ws_cfg = config.get("workspace", {})
    if not ws_cfg.get("enabled", True):
        logger.info("Workspace join disabled — skipping")
        return accounts

    workspace_ids = ws_cfg.get("ids", [])
    if not workspace_ids:
        logger.warning("No workspace IDs configured — skipping join")
        return accounts

    route = str(ws_cfg.get("route", "request")).strip() or "request"
    max_retries = int(ws_cfg.get("max_retries", 3))
    retry_backoff = int(ws_cfg.get("retry_backoff_ms", 5000))
    proxy = str(config.get("proxy", {}).get("url", "")).strip()

    logger.info(
        f"Joining {len(accounts)} accounts to {len(workspace_ids)} workspace(s)"
    )

    for account in accounts:
        email = account.get("email", "?")
        access_token = account.get("access_token", "")
        if not access_token:
            logger.warning(f"[{email}] No access_token — skipping join")
            account["join_status"] = "skipped"
            continue

        results = join_workspaces(
            access_token=access_token,
            workspace_ids=workspace_ids,
            route=route,
            max_retries=max_retries,
            retry_backoff_ms=retry_backoff,
            proxy=proxy,
        )

        all_ok = all(r["ok"] for r in results)
        account["join_status"] = "ok" if all_ok else "failed"
        account["join_results"] = results

        if all_ok:
            logger.info(f"[{email}] ✓ Joined {len(workspace_ids)} workspace(s)")
        else:
            errors = [r.get("error", "?") for r in results if not r["ok"]]
            logger.warning(f"[{email}] ✗ Join failed: {', '.join(errors)}")

    return accounts


def run_re_login(
    config: dict[str, Any],
    accounts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stage 3: Re-login each account with Team space selection.

    Gets team-scoped tokens for accounts that successfully joined.
    NOTE: This step requires browser-based OAuth login flow and is
    currently skipped by default. Use registration tokens directly.
    """
    ws_cfg = config.get("workspace", {})
    re_login_enabled = ws_cfg.get("re_login_enabled", False)

    if not re_login_enabled:
        logger.info("Team re-login disabled — using registration tokens for export")
        for account in accounts:
            account["team_login_status"] = "skipped"
        return accounts

    mail_cfg = config.get("mail", {})
    proxy_cfg = config.get("proxy", {})
    proxy = str(proxy_cfg.get("url", "")).strip()
    flaresolverr_url = str(proxy_cfg.get("flaresolverr_url", "")).strip()
    workspace_ids = ws_cfg.get("ids", [])

    logger.info(f"Re-logging {len(accounts)} accounts for team-scoped tokens")

    for account in accounts:
        email = account.get("email", "")
        password = account.get("password", "")
        join_status = account.get("join_status", "")

        if join_status != "ok":
            logger.info(f"[{email}] Join failed/skipped — skipping re-login")
            account["team_login_status"] = "skipped"
            continue

        if not email or not password:
            logger.warning(f"[{email}] Missing email or password — skipping re-login")
            account["team_login_status"] = "skipped"
            continue

        try:
            team_tokens = re_login_for_team_token(
                email=email,
                password=password,
                mail_config=mail_cfg,
                proxy=proxy,
                flaresolverr_url=flaresolverr_url,
                workspace_id=workspace_ids[0] if workspace_ids else "",
            )

            # Store team-scoped tokens in a separate field
            account["team_access_token"] = team_tokens["access_token"]
            account["team_refresh_token"] = team_tokens["refresh_token"]
            account["team_id_token"] = team_tokens["id_token"]
            account["team_login_status"] = "ok"

            logger.info(f"[{email}] ✓ Team login successful")
        except Exception as e:
            logger.warning(f"[{email}] ✗ Team login failed: {e}")
            account["team_login_status"] = "failed"
            account["team_login_error"] = str(e)

    return accounts


def run_refresh_tokens(
    config: dict[str, Any],
    accounts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Refresh access tokens and enrich with workspace info from check API.

    After joining a workspace, refreshing the token ensures the token
    is valid for the current context.  Then we call /accounts/check
    to get the real plan_type and account_id (the JWT doesn't carry
    workspace claims).
    """
    import json as _json
    from chatgpt_register_sub2api.utils.jwt import decode_jwt_payload
    from datetime import datetime
    import time

    proxy = str(config.get("proxy", {}).get("url", "")).strip()
    workspace_id = ""
    ws_ids = config.get("workspace", {}).get("ids", [])
    if ws_ids:
        workspace_id = ws_ids[0]

    logger.info(f"Refreshing tokens and checking account info for {len(accounts)} accounts")

    for account in accounts:
        email = account.get("email", "")
        rt = account.get("refresh_token", "")

        if not rt:
            logger.warning(f"[{email}] No refresh_token — skipping refresh")
            continue

        session = None
        try:
            kwargs = {"impersonate": "chrome", "verify": False}
            if proxy:
                kwargs["proxy"] = proxy
            from curl_cffi import requests
            session = requests.Session(**kwargs)

            # Step 1: Refresh the access token
            resp = session.post(
                "https://auth.openai.com/oauth/token",
                data={
                    "client_id": "app_2SKx67EdpoN0G6j64rFvigXD",
                    "grant_type": "refresh_token",
                    "refresh_token": rt,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                new_at = data.get("access_token", "")
                new_rt = data.get("refresh_token", "")
                if new_at:
                    account["access_token"] = new_at
                if new_rt:
                    account["refresh_token"] = new_rt
                logger.info(f"[{email}] Token refreshed")
            else:
                logger.warning(f"[{email}] Token refresh failed: HTTP {resp.status_code}")

            # Step 2: Call check API to get real plan_type and account_id
            at = account.get("access_token", "")
            if at:
                resp = session.get(
                    "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
                    headers={"Authorization": f"Bearer {at}"},
                    timeout=30,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    accts = data.get("accounts", {})
                    default = accts.get("default", {}).get("account", {})
                    plan = default.get("plan_type", "")
                    acct_id = default.get("account_id", "")
                    role = default.get("account_user_role", "")

                    if plan:
                        account["plan_type"] = plan
                    if acct_id:
                        account["chatgpt_account_id"] = acct_id
                    if role:
                        account["account_user_role"] = role

                    logger.info(
                        f"[{email}] Check API: plan={plan} account_id={acct_id[:30] if acct_id else '?'} role={role}"
                    )
                else:
                    logger.warning(f"[{email}] Check API failed: HTTP {resp.status_code}")

        except Exception as e:
            logger.warning(f"[{email}] Refresh/check error: {e}")
        finally:
            if session:
                session.close()

    return accounts


def run_export(
    config: dict[str, Any],
    accounts: list[dict[str, Any]],
    output_file: Path | None = None,
) -> str:
    """Stage 4: Export accounts as sub2api JSON.

    Uses team-scoped tokens (team_access_token) when available,
    falls back to personal tokens.
    """
    sub2api_cfg = config.get("sub2api", {})

    # Prepare accounts for export — use registration tokens directly
    # (Team-scoped tokens would require browser-based re-login, not yet implemented)
    export_accounts = []
    for account in accounts:
        export = dict(account)
        if account.get("team_login_status") == "ok":
            export["access_token"] = account.get("team_access_token", account.get("access_token", ""))
            export["refresh_token"] = account.get("team_refresh_token", account.get("refresh_token", ""))
            export["id_token"] = account.get("team_id_token", account.get("id_token", ""))
            export["source_type"] = "team_relogin"
        # else: use registration tokens as-is
        export_accounts.append(export)

    output_path = Path(output_file) if output_file else Path(
        config.get("_config_dir", ".")
    ) / f"sub2api-{_timestamp()}.json"

    json_str, actual_path = export_sub2api_json(export_accounts, output_path)
    logger.info(f"Exported {len(export_accounts)} accounts to {actual_path}")
    return actual_path


def _account_workspace_id(account: dict[str, Any]) -> str:
    """Return the workspace/account id that the account token currently belongs to."""
    direct = str(account.get("chatgpt_account_id") or "").strip()
    if direct:
        return direct
    access_token = str(
        account.get("team_access_token")
        or account.get("access_token")
        or ""
    ).strip()
    if not access_token:
        return ""
    try:
        payload = decode_jwt_payload(access_token)
        auth = payload.get("https://api.openai.com/auth", {}) if isinstance(payload, dict) else {}
        if isinstance(auth, dict):
            return str(auth.get("chatgpt_account_id") or auth.get("account_id") or "").strip()
    except Exception:
        return ""
    return ""


def _joined_workspace_ids(account: dict[str, Any]) -> list[str]:
    """Return workspace ids that were successfully joined by this account."""
    joined: list[str] = []
    seen: set[str] = set()
    for result in account.get("join_results") or []:
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        workspace_id = str(result.get("workspace_id") or "").strip()
        if not workspace_id or workspace_id in seen:
            continue
        joined.append(workspace_id)
        seen.add(workspace_id)
    return joined


def _account_for_workspace_export(
    account: dict[str, Any],
    workspace_id: str,
) -> dict[str, Any]:
    """Copy an account and scope its exported metadata to a joined workspace.

    Joining multiple K12 workspaces creates one usable sub2api row per
    account/workspace pair.  The OAuth tokens stay the account's tokens; the
    exported chatgpt_account_id tells sub2api which joined workspace to use.
    """
    export = dict(account)
    email = str(export.get("email") or "").strip()
    suffix = str(workspace_id or "unknown").strip()[:8] or "unknown"
    export["chatgpt_account_id"] = workspace_id
    export["plan_type"] = str(export.get("plan_type") or "k12")
    export["source_type"] = "joined_workspace"
    export["export_workspace_id"] = workspace_id
    export["sub2api_name"] = f"{email}-workspace-{suffix}" if email else f"workspace-{suffix}"
    return export


def _export_output_dir(
    config: dict[str, Any],
    output_file: Path | None = None,
) -> Path:
    if output_file:
        return Path(output_file).parent
    return Path(config.get("_config_dir", ".")) / "exports"


def _final_output_path(
    config: dict[str, Any],
    batch_ts: str,
    output_file: Path | None = None,
) -> Path:
    if output_file:
        base = Path(output_file)
        return base.with_name(f"{base.stem}-final{base.suffix or '.json'}")
    return _export_output_dir(config, output_file) / f"sub2api-{batch_ts}-final.json"


def run_export_by_workspaces(
    config: dict[str, Any],
    accounts: list[dict[str, Any]],
    workspace_ids: list[str],
    output_file: Path | None = None,
) -> dict[str, Any]:
    """Export this batch into one final sub2api JSON plus a workspace report.

    Successful join_results expand N registered accounts x M joined workspaces
    into N x M usable sub2api rows in the final merged export.
    """
    batch_ts = _timestamp()
    outputs: list[dict[str, Any]] = []
    final_accounts: list[dict[str, Any]] = []
    normalized_ids = [str(item or "").strip() for item in workspace_ids if str(item or "").strip()]
    for workspace_id in normalized_ids:
        matched = [
            _account_for_workspace_export(account, workspace_id)
            for account in accounts
            if workspace_id in _joined_workspace_ids(account)
            or (
                not account.get("join_results")
                and _account_workspace_id(account) == workspace_id
            )
        ]
        final_accounts.extend(matched)
        outputs.append(
            {
                "workspace_id": workspace_id,
                "account_count": len(matched),
                "emails": [str(account.get("email") or "") for account in matched],
            }
        )
        logger.info(f"Workspace rows: {workspace_id} ({len(matched)} accounts)")

    final_path = _final_output_path(config, batch_ts, output_file)
    run_export(config, final_accounts, final_path)
    logger.info(
        f"Final merged export: {final_path} ({len(final_accounts)} account/workspace rows)"
    )

    report_path = _export_output_dir(config, output_file) / f"sub2api-{batch_ts}-workspace-report.json"
    report = {
        "generated_at": _now(),
        "note": (
            "Accounts are expanded by successful join_results: each registered "
            "account is exported once for every joined workspace, with "
            "chatgpt_account_id set to that workspace id. Only final_output_file "
            "is written for one-shot sub2api import; outputs below are counts "
            "per workspace for auditing."
        ),
        "final_output_file": str(final_path),
        "final_account_count": len(final_accounts),
        "outputs": outputs,
        "batch_accounts": [
            {
                "email": str(account.get("email") or ""),
                "current_workspace_id": _account_workspace_id(account),
                "joined_workspace_ids": _joined_workspace_ids(account),
            }
            for account in accounts
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(f"Workspace export report written to {report_path}")
    return {
        "outputs": outputs,
        "final_file": str(final_path),
        "final_account_count": len(final_accounts),
        "report_file": str(report_path),
    }


# ── Full pipeline ───────────────────────────────────────────────────


def run_full_pipeline(
    config: dict[str, Any],
    count: int | None = None,
    output_file: str | None = None,
    accounts_file: str | None = None,
) -> dict[str, Any]:
    """Run the complete pipeline: register → join → re-login → export.

    Args:
        config: Full config dict from config.yaml
        count: Override registration count
        output_file: Override sub2api output path
        accounts_file: Override accounts storage path

    Returns:
        Summary dict with counts
    """
    config_dir = Path(config.get("_config_dir", "."))
    af = Path(accounts_file) if accounts_file else config_dir / "registered_accounts.json"
    of = Path(output_file) if output_file else None

    logger.info("=" * 60)
    logger.info("Pipeline started: register → join → re-login → export")
    logger.info("=" * 60)

    # Stage 1: Register
    new_accounts = run_register(config, af, count=count)
    if not new_accounts:
        logger.error("No accounts registered — pipeline aborted")
        return {
            "registered": 0,
            "joined": 0,
            "refreshed": 0,
            "exported": 0,
            "accounts_file": str(af),
            "output_file": str(of) if of else "",
        }

    # Stage 2: Join workspace
    joined_accounts = run_join_workspace(config, new_accounts)
    save_accounts(af, merge_accounts(load_accounts(af), joined_accounts))

    # Stage 3: Refresh tokens + enrich with workspace info from check API
    refreshed_accounts = run_refresh_tokens(config, joined_accounts)
    save_accounts(af, merge_accounts(load_accounts(af), refreshed_accounts))

    # Stage 4: Export this batch only (do not mix historical accounts).
    # If multiple workspace IDs are configured, export one JSON per workspace.
    workspace_ids = [
        str(item or "").strip()
        for item in (config.get("workspace", {}).get("ids", []) or [])
        if str(item or "").strip()
    ]
    if len(workspace_ids) > 1:
        export_result = run_export_by_workspaces(config, refreshed_accounts, workspace_ids, of)
        json_output = export_result.get("final_file") or export_result.get("report_file", "")
    else:
        json_output = run_export(config, refreshed_accounts, of)

    registered = len(new_accounts)
    joined = sum(1 for a in refreshed_accounts if a.get("join_status") == "ok")
    refreshed = sum(1 for a in refreshed_accounts if a.get("plan_type") == "k12")
    exported = len(refreshed_accounts)

    logger.info("=" * 60)
    logger.info(
        f"Pipeline complete: "
        f"registered={registered}, joined={joined}, "
        f"refreshed={refreshed}, exported={exported}"
    )
    logger.info("=" * 60)

    return {
        "registered": registered,
        "joined": joined,
        "refreshed": refreshed,
        "exported": exported,
        "accounts_file": str(af),
        "output_file": str(json_output),
    }
