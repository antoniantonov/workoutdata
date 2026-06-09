#!/usr/bin/env python3
"""Renew the Garmin (garth) session token used by the import-garmin download phase.

Garmin authentication is an OAuth SSO flow (login widget -> optional MFA ->
OAuth1 token -> OAuth2 exchange) that the ``garth`` library implements; it cannot
be done in pure shell. This script logs in interactively and writes a fresh token
in the single-file ``garth_session`` format expected by the pinned
``garmindb==3.7.0``.

It is normally invoked by ``scripts/renew_garmin_token.sh`` (which ensures
``garth`` is installed), but can also be run directly:

    python scripts/renew_garmin_token.py
    GARMIN_EMAIL=you@example.com python scripts/renew_garmin_token.py
    python scripts/renew_garmin_token.py --token-file /path/to/garth_session

This file is host-only and excluded from the Docker image (see .dockerignore).
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Renew the Garmin garth session token.")
    parser.add_argument(
        "--token-file",
        default=os.environ.get("GARMIN_TOKEN_FILE", os.path.expanduser("~/.GarminDb/garth_session")),
        help="Where to write the token (default: ~/.GarminDb/garth_session).",
    )
    parser.add_argument(
        "--domain",
        default=os.environ.get("GARMIN_DOMAIN", "garmin.com"),
        help="Garmin domain (default: garmin.com; use garmin.cn for China).",
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("GARMIN_EMAIL"),
        help="Garmin account email (otherwise prompted).",
    )
    args = parser.parse_args()

    try:
        import garth
    except ImportError:
        print(
            "ERROR: the 'garth' package is not installed.\n"
            "Install it with: pip install 'garmindb==3.7.0'  (or run scripts/renew_garmin_token.sh).",
            file=sys.stderr,
        )
        return 1

    token_file = os.path.expanduser(args.token_file)
    email = args.email or input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")

    print(f"Renewing Garmin token -> {token_file}")
    print(f"Domain: {args.domain}\n")

    client = garth.Client()
    client.configure(domain=args.domain)

    # Prompts for an MFA one-time code on stdin if 2FA is enabled on the account.
    try:
        client.login(email, password)
    except Exception as exc:  # noqa: BLE001 - surface a friendly message
        message = str(exc)
        if "401" in message or "Unauthorized" in message:
            print(
                "\n\u274c Login failed: Garmin rejected the credentials (401 Unauthorized).\n"
                "   Double-check your email/password (and MFA code if prompted) and try again.",
                file=sys.stderr,
            )
        else:
            print(f"\n\u274c Login failed: {exc}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    with open(token_file, "w", encoding="utf-8") as handle:
        handle.write(client.dumps())

    print(f"\n\u2705 Fresh token saved to {token_file}")
    try:
        print(f"   Logged in as: {client.profile.get('displayName', '?')}")
    except Exception:  # noqa: BLE001 - profile is best-effort
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
