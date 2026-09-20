"""Interactive Garmin authentication for the Dockerized import job.

The command creates the single-file ``garth_session`` token consumed by the
job's pinned ``garmindb==3.7.0`` dependency. Run it with an interactive TTY and
persist the token on the mounted ``local_data`` volume.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path


def _default_token_file() -> Path:
    config_dir = os.getenv("GARMIN_CONFIG_DIR")
    if config_dir:
        return Path(config_dir).expanduser() / "garth_session"

    job_dir = Path(__file__).resolve().parent.parent
    return job_dir / "local_data" / ".GarminDb" / "garth_session"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or renew the Garmin session token used by the import job."
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=_default_token_file(),
        help="Token output path (default: <GARMIN_CONFIG_DIR>/garth_session).",
    )
    parser.add_argument(
        "--domain",
        default=os.getenv("GARMIN_DOMAIN", "garmin.com"),
        help="Garmin domain (default: garmin.com; use garmin.cn for China).",
    )
    parser.add_argument(
        "--email",
        default=os.getenv("GARMIN_EMAIL"),
        help="Garmin account email (otherwise prompted).",
    )
    args = parser.parse_args()

    try:
        import garth
    except ImportError:
        print(
            "ERROR: Garmin authentication dependencies are not installed. "
            "Build the import image with the 'download' extra.",
            file=sys.stderr,
        )
        return 1

    email = args.email or input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")

    client = garth.Client()
    client.configure(domain=args.domain)

    print(f"Authenticating with {args.domain}...")
    try:
        client.login(email, password)
    except Exception as exc:  # noqa: BLE001 - Garmin/garth exposes mixed exception types
        message = str(exc)
        if "401" in message or "Unauthorized" in message:
            print(
                "ERROR: Garmin rejected the credentials. Check the email, password, "
                "and MFA code, then retry.",
                file=sys.stderr,
            )
        else:
            print(f"ERROR: Garmin authentication failed: {exc}", file=sys.stderr)
        return 1

    token_file = args.token_file.expanduser().resolve()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(client.dumps(), encoding="utf-8")
    token_file.chmod(0o600)

    print(f"Garmin session token saved to {token_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
