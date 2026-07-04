from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from chatgpt_register_sub2api.config import load_config
from chatgpt_register_sub2api.export.sub2api import export_sub2api_json
from chatgpt_register_sub2api.login.login_flow import re_login_for_team_token
from chatgpt_register_sub2api.pipeline import load_accounts, save_accounts
from chatgpt_register_sub2api.utils.jwt import decode_jwt_payload


def token_account_id(access_token: str) -> str:
    payload = decode_jwt_payload(str(access_token or ""))
    if not isinstance(payload, dict):
        return ""
    auth = payload.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        return str(auth.get("chatgpt_account_id") or auth.get("account_id") or "").strip()
    return ""


def account_current_id(account: dict[str, Any]) -> str:
    value = str(account.get("chatgpt_account_id") or "").strip()
    if value:
        return value
    return token_account_id(str(account.get("access_token") or ""))


def parse_emails(value: str) -> set[str]:
    result: set[str] = set()
    if not value:
        return result
    p = Path(value)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            item = line.strip().lower()
            if item and "@" in item:
                result.add(item)
        return result
    for item in value.replace(";", ",").split(","):
        item = item.strip().lower()
        if item and "@" in item:
            result.add(item)
    return result


def default_output(workspace_id: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    short = workspace_id[:8] if workspace_id else "workspace"
    return Path(f"sub2api-workspace-{short}-{ts}.json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export accounts for a specific ChatGPT/K12 workspace."
    )
    parser.add_argument("--workspace-id", required=True, help="Target chatgpt_account_id/workspace id")
    parser.add_argument("--input", "-i", default="registered_accounts.json", help="Input accounts JSON")
    parser.add_argument("--output", "-o", default="", help="Output sub2api JSON")
    parser.add_argument("--accounts-output", default="", help="Optional selected accounts JSON output")
    parser.add_argument("--config", "-c", default="config.yaml", help="Config file")
    parser.add_argument("--emails", default="", help="Comma-separated emails or a file containing emails")
    parser.add_argument("--relogin", action="store_true", help="Try OAuth re-login when current token is not target workspace")
    args = parser.parse_args()

    target = args.workspace_id.strip()
    cfg = load_config(args.config)
    proxy = str(cfg.get("proxy", {}).get("url") or "").strip()
    flaresolverr_url = str(cfg.get("proxy", {}).get("flaresolverr_url") or "").strip()
    mail_cfg = cfg.get("mail", {})

    input_path = Path(args.input)
    accounts = load_accounts(input_path)
    email_filter = parse_emails(args.emails)
    if email_filter:
        accounts = [a for a in accounts if str(a.get("email") or "").strip().lower() in email_filter]

    selected: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for account in accounts:
        email = str(account.get("email") or "").strip()
        current = account_current_id(account)
        if current == target:
            copy = dict(account)
            copy["selected_workspace_id"] = target
            copy["workspace_select_status"] = "already_selected"
            selected.append(copy)
            print(f"[OK] {email}: already on {target}")
            continue

        if not args.relogin:
            errors.append({"email": email, "error": f"current={current or '(unknown)'}; use --relogin to try switching"})
            print(f"[SKIP] {email}: current={current or '(unknown)'} target={target}")
            continue

        password = str(account.get("password") or "").strip()
        if not password:
            errors.append({"email": email, "error": "missing password for re-login"})
            print(f"[FAIL] {email}: missing password")
            continue

        try:
            tokens = re_login_for_team_token(
                email=email,
                password=password,
                mail_config=mail_cfg,
                proxy=proxy,
                flaresolverr_url=flaresolverr_url,
                workspace_id=target,
            )
            token_id = token_account_id(tokens.get("access_token", ""))
            if token_id != target:
                errors.append({"email": email, "error": f"re-login returned account_id={token_id or '(unknown)'}"})
                print(f"[FAIL] {email}: re-login returned {token_id or '(unknown)'}")
                continue
            copy = dict(account)
            copy.update({
                "access_token": tokens.get("access_token", ""),
                "refresh_token": tokens.get("refresh_token", ""),
                "id_token": tokens.get("id_token", ""),
                "chatgpt_account_id": token_id,
                "selected_workspace_id": target,
                "workspace_select_status": "relogin_selected",
            })
            selected.append(copy)
            print(f"[OK] {email}: re-login selected {target}")
        except Exception as exc:
            errors.append({"email": email, "error": str(exc)[:500]})
            print(f"[FAIL] {email}: {exc}")

    output_path = Path(args.output) if args.output else default_output(target)
    accounts_output = Path(args.accounts_output) if args.accounts_output else output_path.with_name(output_path.stem + "-accounts.json")

    save_accounts(accounts_output, selected)
    if selected:
        export_sub2api_json(selected, output_path)
    else:
        output_path.write_text(json.dumps({"accounts": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    error_path = output_path.with_name(output_path.stem + "-errors.json")
    error_path.write_text(json.dumps(errors, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("=" * 60)
    print(f"target_workspace: {target}")
    print(f"selected: {len(selected)}")
    print(f"errors: {len(errors)}")
    print(f"sub2api_output: {output_path.resolve()}")
    print(f"accounts_output: {accounts_output.resolve()}")
    print(f"errors_output: {error_path.resolve()}")
    return 0 if selected else 1


if __name__ == "__main__":
    raise SystemExit(main())
