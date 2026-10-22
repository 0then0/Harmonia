import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
import yaml

from harmonia.domain.compiler import PlaylistConstraints, compile_playlist
from harmonia.domain.matching import match_track
from harmonia.domain.models import Playlist, Track


def load_playlist(path: str) -> Playlist:
    return Playlist.model_validate_json(Path(path).read_text())


def emit(value, output: str | None = None):
    value = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    text = json.dumps(value, indent=2, ensure_ascii=False)
    if output:
        Path(output).write_text(text + "\n")
    else:
        print(text)


def api(client: httpx.Client, method: str, path: str, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.is_error:
        raise ValueError(f"API HTTP {response.status_code}: {response.text[:1000]}")
    return response.json()


def wait_job(client, payload):
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        result = api(client, "GET", f"api/jobs/{payload['job_id']}")
        if result["status"] == "complete":
            if not result["result"]["ok"]:
                raise ValueError(result["result"]["error"])
            return result["result"]
        if result["status"] == "not_found":
            raise ValueError("job expired or was not found")
        time.sleep(1)
    raise ValueError("job is still running; inspect its ID before retrying: " + payload["job_id"])


def parser():
    root = argparse.ArgumentParser(
        description="Harmonia: import, inspect, compile, and export music playlists"
    )
    root.add_argument(
        "--api",
        default=os.getenv("HARMONIA_API_URL", "http://127.0.0.1:8000/"),
        help="server base URL (default: HARMONIA_API_URL or http://127.0.0.1:8000/)",
    )
    commands = root.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="compile locally; no database or API needed")
    build.add_argument("config", help="YAML playlist constraints")
    build.add_argument(
        "--library", required=True, action="append", help="canonical JSON playlist; repeatable"
    )
    build.add_argument("--output", "-o", help="write canonical JSON to a file instead of stdout")
    match = commands.add_parser("match", help="explain a match against a canonical library")
    match.add_argument("source", help="a canonical Track JSON file")
    match.add_argument("--library", required=True, help="canonical JSON destination library")
    imp = commands.add_parser("import", help="import a provider playlist through the local worker")
    imp.add_argument("provider", choices=["spotify", "youtube", "yandex"])
    imp.add_argument("reference", help="service playlist URL or supported identifier")
    imp.add_argument("--output", "-o", help="write canonical JSON to a file instead of stdout")
    for name in ("diff", "export"):
        cmd = commands.add_parser(
            name,
            help="preview a transfer"
            if name == "diff"
            else "create a new private playlist (dry-run by default)",
        )
        cmd.add_argument("playlist", help="canonical JSON playlist")
        cmd.add_argument("--to", required=True, choices=["spotify", "youtube", "yandex"])
        if name == "export":
            cmd.add_argument(
                "--apply", action="store_true", help="publish after resolving all matches"
            )
            cmd.add_argument(
                "--idempotency-key", help="unique key for this resolved export payload"
            )
    confirm = commands.add_parser("confirm", help="persist a manual mapping")
    confirm.add_argument("source", help="canonical source Track JSON file")
    confirm.add_argument("target", help="canonical destination Track JSON file")
    confirm.add_argument("--to", required=True, choices=["spotify", "youtube", "yandex"])
    return root


def run(args):
    if args.command == "build":
        c = PlaylistConstraints.model_validate(yaml.safe_load(Path(args.config).read_text()))
        candidates = [t for path in args.library for t in load_playlist(path).tracks]
        emit(compile_playlist(c, candidates), args.output)
        return
    if args.command == "match":
        source = Track.model_validate_json(Path(args.source).read_text())
        emit(match_track(source, list(load_playlist(args.library).tracks)))
        return
    key = os.getenv("HARMONIA_API_KEY", "")
    if not key:
        raise ValueError("set HARMONIA_API_KEY for server operations")
    if args.command == "export" and args.apply and not args.idempotency_key:
        raise ValueError("--apply requires --idempotency-key")
    with httpx.Client(
        base_url=args.api.rstrip("/") + "/", headers={"X-API-Key": key}, timeout=30
    ) as client:
        if args.command == "import":
            result = wait_job(
                client,
                api(
                    client,
                    "POST",
                    "api/jobs/import",
                    json={"provider": args.provider, "reference": args.reference},
                ),
            )
            emit(
                api(client, "GET", "api/playlist", params={"id": result["playlist_id"]}),
                args.output,
            )
        elif args.command == "confirm":
            emit(
                api(
                    client,
                    "POST",
                    "api/matches/confirm",
                    json={
                        "source": json.loads(Path(args.source).read_text()),
                        "target": json.loads(Path(args.target).read_text()),
                        "provider": args.to,
                    },
                )
            )
        else:
            source = load_playlist(args.playlist)
            saved = api(client, "POST", "api/playlists", json=source.model_dump(mode="json"))
            result = wait_job(
                client,
                api(
                    client,
                    "POST",
                    "api/jobs/preview",
                    json={"playlist_id": saved["playlist_id"], "provider": args.to},
                ),
            )
            preview = api(client, "GET", f"api/previews/{result['preview_id']}")
            if args.command == "diff" or not args.apply:
                emit({"preview_id": result["preview_id"], **preview})
            elif not preview["ready"]:
                raise ValueError("unresolved matches; use diff and confirm before export")
            else:
                emit(
                    wait_job(
                        client,
                        api(
                            client,
                            "POST",
                            "api/jobs/export",
                            json={
                                "preview_id": result["preview_id"],
                                "idempotency_key": args.idempotency_key,
                            },
                        ),
                    )
                )


def main():
    args = parser().parse_args()
    try:
        run(args)
    except (ValueError, OSError, httpx.HTTPError, yaml.YAMLError) as exc:
        print(f"playlist: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
