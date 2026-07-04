from __future__ import annotations

import argparse
import random
import shutil
import string
from datetime import datetime
from pathlib import Path

import yaml


def parse_api_lines(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "----" not in line:
            continue
        email, url = [item.strip() for item in line.split("----", 1)]
        if "@" in email and url:
            rows.append((email, url))
    return rows


def format_api_lines(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"{email}----{url}" for email, url in rows)


def canonical_mother(email: str) -> str:
    local, domain = email.strip().split("@", 1)
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def split_email(email: str, suffix: str) -> str:
    local, domain = canonical_mother(email).split("@", 1)
    return f"{local}+{suffix}@{domain}"


def random_suffix(length: int) -> str:
    alphabet = string.ascii_lowercase
    return "".join(random.choice(alphabet) for _ in range(length))


def build_variants(mother: str, count: int, existing: set[str], suffix_len: int) -> list[str]:
    variants: list[str] = []
    attempts = 0
    while len(variants) < count:
        attempts += 1
        if attempts > count * 100 + 100:
            raise RuntimeError("Too many duplicate suffix attempts")
        email = split_email(mother, random_suffix(suffix_len))
        key = email.lower()
        if key in existing:
            continue
        existing.add(key)
        variants.append(email)
    return variants


def main() -> int:
    parser = argparse.ArgumentParser(description="Split api_code mother Gmail addresses into plus-address variants.")
    parser.add_argument("--config", "-c", default="config.yaml", help="config.yaml path")
    parser.add_argument("--count", "-n", type=int, default=4, help="variants to create per mother email")
    parser.add_argument("--email", action="append", default=[], help="mother email to split; repeatable. Default: all non-plus api_code emails")
    parser.add_argument("--suffix-len", type=int, default=6, help="random suffix length")
    parser.add_argument("--remove-mother", action="store_true", help="remove mother email line after splitting")
    parser.add_argument("--dry-run", action="store_true", help="print changes without writing config")
    args = parser.parse_args()

    if args.count < 1:
        raise SystemExit("--count must be >= 1")
    if args.suffix_len < 2:
        raise SystemExit("--suffix-len must be >= 2")

    config_path = Path(args.config)
    if not config_path.exists():
        raise SystemExit(f"Config not found: {config_path}")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    providers = config.get("mail", {}).get("providers", [])
    if not isinstance(providers, list):
        raise SystemExit("mail.providers must be a list")

    wanted = {canonical_mother(email).lower() for email in args.email}
    total_added = 0
    changed = False

    for provider in providers:
        if not isinstance(provider, dict) or provider.get("type") != "api_code":
            continue
        rows = parse_api_lines(provider.get("mailboxes") or provider.get("accounts") or provider.get("pool") or "")
        if not rows:
            continue

        existing = {email.lower() for email, _ in rows}
        url_by_mother: dict[str, str] = {}
        mother_order: list[str] = []
        for email, url in rows:
            mother = canonical_mother(email)
            # Only split real mother lines by default. If --email is provided,
            # also allow deriving the mother from existing plus aliases.
            is_plain_mother = "+" not in email.split("@", 1)[0]
            if not is_plain_mother and not wanted:
                continue
            key = mother.lower()
            if wanted and key not in wanted:
                continue
            if key not in url_by_mother:
                url_by_mother[key] = url
                mother_order.append(mother)

        if not mother_order:
            continue

        new_rows = list(rows)
        for mother in mother_order:
            variants = build_variants(mother, args.count, existing, args.suffix_len)
            url = url_by_mother[mother.lower()]
            for variant in variants:
                new_rows.append((variant, url))
                total_added += 1
            print(f"{mother} -> {len(variants)} variants")
            for variant in variants:
                print(f"  {variant}")

        if args.remove_mother:
            mother_keys = {mother.lower() for mother in mother_order}
            new_rows = [row for row in new_rows if row[0].lower() not in mother_keys]

        provider["mailboxes"] = format_api_lines(new_rows)
        changed = True

    if not changed or total_added == 0:
        print("No api_code mother email matched; nothing changed.")
        return 0

    if args.dry_run:
        print(f"Dry run: would add {total_added} variants.")
        return 0

    backup = config_path.with_name(config_path.name + ".bak-split-mother-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(config_path, backup)
    config_path.write_text(yaml.dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"Added {total_added} variants.")
    print(f"Backup: {backup}")
    print(f"Updated: {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
