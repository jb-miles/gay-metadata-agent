"""Register the Gay Adult Metadata Agent for Plex with Plex Media Server."""

from __future__ import annotations

import argparse

import httpx


def register(pms_url: str, pms_token: str, provider_url: str) -> None:
    response = httpx.post(
        f"{pms_url.rstrip('/')}/media/providers/metadata",
        params={
            "uri": provider_url,
            "X-Plex-Token": pms_token,
        },
        timeout=30.0,
    )
    if response.status_code in {200, 201}:
        print(f"Registered provider at {provider_url}")
        return
    print(f"Registration failed: {response.status_code} {response.text}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pms-url", default="http://localhost:32400")
    parser.add_argument("--pms-token", required=True, help="Plex auth token")
    parser.add_argument("--provider-url", default="http://localhost:8778")
    args = parser.parse_args()
    register(args.pms_url, args.pms_token, args.provider_url)


if __name__ == "__main__":
    main()
