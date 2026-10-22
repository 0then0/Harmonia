import json
from unittest.mock import Mock

import httpx
import pytest

from harmonia.cli import api, main, parser, run, wait_job
from harmonia.domain.models import Playlist


def test_offline_build_and_match(tmp_path, make_track, capsys):
    output = tmp_path / "compiled.json"
    run(
        parser().parse_args(
            [
                "build",
                "examples/night-drive.yml",
                "--library",
                "examples/library.json",
                "--output",
                str(output),
            ]
        )
    )
    playlist = Playlist.model_validate_json(output.read_text())
    assert playlist.tracks
    source = tmp_path / "source.json"
    source.write_text(make_track().model_dump_json())
    library = tmp_path / "library.json"
    library.write_text(
        Playlist(id="x", title="Test", tracks=(make_track(id="other"),)).model_dump_json()
    )
    run(parser().parse_args(["match", str(source), "--library", str(library)]))
    assert json.loads(capsys.readouterr().out)["match"]["track"]["id"] == "other"


def test_apply_requires_key_before_server_io(monkeypatch):
    monkeypatch.setenv("HARMONIA_API_KEY", "operator-key")
    with pytest.raises(ValueError, match="idempotency"):
        run(parser().parse_args(["export", "unused.json", "--to", "spotify", "--apply"]))
    monkeypatch.delenv("HARMONIA_API_KEY")
    with pytest.raises(ValueError, match="HARMONIA_API_KEY"):
        run(parser().parse_args(["import", "spotify", "test"]))


def test_remote_export_is_dry_run_and_cannot_apply_unreviewed(
    monkeypatch, tmp_path, capsys, make_track
):
    import harmonia.cli as cli

    monkeypatch.setenv("HARMONIA_API_KEY", "operator-key")
    source = tmp_path / "source.json"
    source.write_text(Playlist(id="source", title="Test", tracks=(make_track(),)).model_dump_json())
    calls = []

    def fake_api(client, method, path, **kwargs):
        calls.append(path)
        if path == "api/playlists":
            return {"playlist_id": "source"}
        if path == "api/jobs/preview":
            return {"job_id": "preview-job"}
        if path == "api/previews/preview":
            return {"ready": False, "matches": []}
        raise AssertionError("Unexpected write: " + path)

    monkeypatch.setattr(cli, "api", fake_api)
    monkeypatch.setattr(cli, "wait_job", lambda *args: {"preview_id": "preview"})
    run(parser().parse_args(["export", str(source), "--to", "spotify"]))
    assert json.loads(capsys.readouterr().out)["ready"] is False
    assert "api/jobs/export" not in calls
    with pytest.raises(ValueError, match="unresolved"):
        run(
            parser().parse_args(
                ["export", str(source), "--to", "spotify", "--apply", "--idempotency-key", "test"]
            )
        )
    assert "api/jobs/export" not in calls


def test_job_failure_and_expired_status(monkeypatch):
    import harmonia.cli as cli

    monkeypatch.setattr(cli.time, "sleep", lambda delay: None)
    monkeypatch.setattr(
        cli,
        "api",
        lambda *args, **kwargs: {
            "status": "complete",
            "result": {"ok": False, "error": "Failed safely"},
        },
    )
    with pytest.raises(ValueError, match="Failed safely"):
        wait_job(None, {"job_id": "x"})
    monkeypatch.setattr(cli, "api", lambda *args, **kwargs: {"status": "not_found"})
    with pytest.raises(ValueError, match="expired"):
        wait_job(None, {"job_id": "x"})


def test_http_error_and_cli_exit(monkeypatch, capsys):
    client = Mock()
    client.request.return_value = httpx.Response(401, text="Unauthorized")
    with pytest.raises(ValueError, match="401"):
        api(client, "GET", "api/playlists")
    monkeypatch.setattr(
        "sys.argv", ["playlist", "build", "missing-config.yml", "--library", "missing.json"]
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "playlist:" in capsys.readouterr().err
