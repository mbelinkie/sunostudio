"""One-shot Fargate worker for rendering or approved Slack delivery."""

import argparse
import hashlib
import http.client
import json
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
IDENTITY = r"[A-Za-z0-9_-]{8,80}"
ATTEMPT_ID = r"[A-Za-z0-9_-]{8,36}"


def _client(service):
    import boto3
    return boto3.client(service, region_name=REGION)


def _split_uri(uri):
    parsed = urllib.parse.urlparse(str(uri))
    bucket = os.environ.get("SUNO_BUCKET")
    key = parsed.path.lstrip("/")
    if (parsed.scheme != "s3" or parsed.netloc != bucket or not bucket or not key or
            parsed.query or parsed.fragment or ".." in key.split("/")):
        raise ValueError("expected an object in the configured S3 bucket")
    return bucket, key


def _json(uri):
    bucket, key = _split_uri(uri)
    value = json.loads(_client("s3").get_object(Bucket=bucket, Key=key)["Body"].read())
    if not isinstance(value, dict):
        raise ValueError("worker manifest must be a JSON object")
    return value


def _manifest(uri, mode):
    bucket, key = _split_uri(uri)
    parts = key.split("/")
    input_prefix = f"{mode}-inputs"
    if (len(parts) != 4 or parts[0] != input_prefix or parts[3] != "manifest.json" or
            not re.fullmatch(IDENTITY, parts[1]) or not re.fullmatch(ATTEMPT_ID, parts[2])):
        raise ValueError("invalid worker manifest location")
    manifest = _json(uri)
    job_id, attempt_id = parts[1:3]
    if (manifest.get("schema_version") != 1 or manifest.get("mode") != mode or
            manifest.get("job_id") != job_id or manifest.get("attempt_id") != attempt_id):
        raise ValueError("unsupported or mismatched worker manifest")
    prefix = f"{input_prefix}/{job_id}/{attempt_id}/"

    def check_asset(asset):
        if not isinstance(asset, dict):
            raise ValueError("invalid worker asset")
        asset_bucket, asset_key = _split_uri(asset.get("uri"))
        in_attempt = asset_key.startswith(prefix) and "/" not in asset_key[len(prefix):]
        parts = asset_key.split("/")
        is_render_output = (mode == "delivery" and len(parts) == 4 and
                            parts[0] == "render-results" and parts[1] == job_id and
                            re.fullmatch(ATTEMPT_ID, parts[2]) and parts[3] == "video.mp4")
        if (asset_bucket != bucket or not (in_attempt or is_render_output) or
                not re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256", ""))) or
                not isinstance(asset.get("size"), int) or isinstance(asset.get("size"), bool) or
                asset["size"] < 0):
            raise ValueError("worker asset is outside its manifest or missing integrity data")

    assets = manifest.get("assets")
    settings = manifest.get("settings", {})
    if not isinstance(assets, dict) or not isinstance(settings, dict):
        raise ValueError("invalid worker manifest fields")
    if mode == "render":
        if not {"audio", "background"}.issubset(assets) or set(assets) - {
                "audio", "background", "subtitles", "focus_background"}:
            raise ValueError("invalid render assets")
        if manifest.get("result_uri") != f"s3://{bucket}/render-results/{job_id}/{attempt_id}/result.json" or \
                manifest.get("output_uri") != f"s3://{bucket}/render-results/{job_id}/{attempt_id}/video.mp4":
            raise ValueError("invalid render result location")
        drawtext_assets = manifest.get("drawtext_assets", [])
        drawtext_assets = [] if drawtext_assets is None else drawtext_assets
        if not isinstance(drawtext_assets, list):
            raise ValueError("invalid drawtext assets")
        for asset in drawtext_assets:
            check_asset(asset)
            if not isinstance(asset.get("reference"), str) or not asset["reference"]:
                raise ValueError("invalid drawtext reference")
    else:
        if set(assets) != {"video"} or not re.fullmatch(r"[CGD][A-Z0-9]{8,}",
                                                        str(settings.get("channel_id", ""))):
            raise ValueError("invalid delivery assets or destination")
        if manifest.get("result_uri") != f"s3://{bucket}/delivery-results/{job_id}/{attempt_id}/result.json":
            raise ValueError("invalid delivery result location")
        if manifest.get("output_uri") is not None:
            raise ValueError("invalid delivery output location")
    for asset in assets.values():
        check_asset(asset)
    return manifest


def _put_json(uri, value):
    bucket, key = _split_uri(uri)
    _client("s3").put_object(Bucket=bucket, Key=key,
                             Body=json.dumps(value, separators=(",", ":")).encode(),
                             ContentType="application/json")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(asset, path):
    bucket, key = _split_uri(asset["uri"])
    _client("s3").download_file(bucket, key, str(path))
    if _sha256(path) != asset["sha256"] or Path(path).stat().st_size != asset["size"]:
        raise RuntimeError("input asset checksum mismatch")
    return path


def _ff_path(path):
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render(manifest_uri):
    manifest = _manifest(manifest_uri, "render")
    result = {"schema_version": 1, "status": "failed", "job_id": manifest.get("job_id"),
              "attempt_id": manifest.get("attempt_id"), "output_uri": manifest["output_uri"]}
    try:
        import suno_studio
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="suno-render-") as temporary:
            root = Path(temporary)
            local = {name: _download(asset, root / f"{name}{Path(asset['uri']).suffix}")
                     for name, asset in manifest["assets"].items()}
            settings = manifest.get("settings") or {}
            drawtext_chain = settings.get("drawtext_chain") or ""
            for index, asset in enumerate(manifest.get("drawtext_assets") or []):
                path = _download(asset, root / f"drawtext-{index:04d}{Path(asset['uri']).suffix}")
                drawtext_chain = drawtext_chain.replace(asset["reference"], _ff_path(path))
            audio, background = local.get("audio"), local.get("background")
            if not audio or not background:
                raise ValueError("audio and background are required")
            ffmpeg = suno_studio.find_ffmpeg()
            if not ffmpeg or "subtitles" not in suno_studio.ffmpeg_filters(ffmpeg):
                raise RuntimeError("worker FFmpeg is missing libass/subtitles")
            output = root / "rendered.part"
            inputs_ready = time.monotonic()
            suno_studio.render_lyric_video(
                ffmpeg, audio, background, local.get("subtitles"), output,
                height=int(settings.get("height") or 1080),
                fps=int(settings.get("fps") or 30),
                vis=settings.get("vis") or "bars",
                crf=int(settings.get("crf") or 21),
                accent=settings.get("accent") or "0x22d3a6",
                shimmer=bool(settings.get("shimmer", True)),
                interlude_mode=bool(settings.get("interlude_mode", True)),
                lyric_focus_band=bool(settings.get("lyric_focus_band", True)),
                focus_bg_png=local.get("focus_background"),
                focus_opacity=float(settings.get("focus_opacity") or 0.90),
                drawtext_chain=drawtext_chain or None)
            encoded = time.monotonic()
            with output.open("rb") as stream:
                if stream.read(12)[4:8] != b"ftyp":
                    raise RuntimeError("renderer did not produce an MP4")
            bucket, key = _split_uri(manifest["output_uri"])
            _client("s3").upload_file(str(output), bucket, key,
                                      ExtraArgs={"ContentType": "video/mp4"})
            result.update(status="succeeded", size=output.stat().st_size,
                          sha256=_sha256(output),
                          duration=suno_studio.audio_duration(ffmpeg, audio),
                          phase_seconds={"inputs": round(inputs_ready - started, 3),
                                         "encode": round(encoded - inputs_ready, 3),
                                         "publish": round(time.monotonic() - encoded, 3)},
                          completed_at=time.time())
        _put_json(manifest["result_uri"], result)
        return result
    except Exception as error:
        result.update(error=f"{type(error).__name__}: {str(error)[:300]}",
                      completed_at=time.time())
        try:
            _put_json(manifest["result_uri"], result)
        except Exception:
            pass
        raise


def _slack(method, token, params):
    upload_slot = method == "files.getUploadURLExternal"
    request = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=(urllib.parse.urlencode(params).encode() if upload_slot else
              json.dumps(params).encode()),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": ("application/x-www-form-urlencoded" if upload_slot else
                                  "application/json")},
        method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    if not data.get("ok"):
        raise RuntimeError(f"Slack {method} failed: {data.get('error', 'unknown error')}")
    return data


def _upload_bytes(url, path):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Slack returned an invalid upload URL")
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=120)
    try:
        connection.putrequest("POST", parsed.path + ("?" + parsed.query if parsed.query else ""))
        connection.putheader("Content-Type", "video/mp4")
        connection.putheader("Content-Length", str(Path(path).stat().st_size))
        connection.endheaders()
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                connection.send(block)
        response = connection.getresponse()
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"Slack file transfer returned HTTP {response.status}")
        response.read()
    finally:
        connection.close()


def deliver(manifest_uri):
    manifest = _manifest(manifest_uri, "delivery")
    result = {"schema_version": 1, "status": "failed", "job_id": manifest.get("job_id"),
              "attempt_id": manifest.get("attempt_id"), "uncertain": False}
    try:
        secret_arn = os.environ.get("SLACK_SECRET_ARN")
        if not secret_arn:
            raise RuntimeError("Slack token is not configured")
        token = _client("secretsmanager").get_secret_value(SecretId=secret_arn)["SecretString"]
        with tempfile.TemporaryDirectory(prefix="suno-delivery-") as temporary:
            video = _download(manifest["assets"]["video"], Path(temporary) / "video.mp4")
            upload = _slack("files.getUploadURLExternal", token, {
                "filename": f"{manifest['job_id']}.mp4", "length": video.stat().st_size})
            _upload_bytes(upload["upload_url"], video)
            try:
                completed = _slack("files.completeUploadExternal", token, {
                    "files": [{"id": upload["file_id"]}],
                    "channel_id": manifest["settings"]["channel_id"]})
            except Exception as error:
                result.update(status="uncertain", uncertain=True,
                              file_id=upload["file_id"], error=str(error)[:200])
                _put_json(manifest["result_uri"], result)
                return result
            result.update(status="succeeded", file_id=upload["file_id"],
                          channel_id=manifest["settings"]["channel_id"],
                          slack_file=(completed.get("files") or [{}])[0])
        _put_json(manifest["result_uri"], result)
        return result
    except Exception as error:
        result.update(error=f"{type(error).__name__}: {str(error)[:200]}")
        try:
            _put_json(manifest["result_uri"], result)
        except Exception:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("render", "delivery"))
    parser.add_argument("--manifest-uri", required=True)
    args = parser.parse_args(argv)
    return render(args.manifest_uri) if args.mode == "render" else deliver(args.manifest_uri)


if __name__ == "__main__":
    main()
