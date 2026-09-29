"""Pay-per-use ECS/S3 rendering and delivery for Suno Studio.

Importing this module does not require boto3. The AWS dependency is needed only
after the user enables cloud rendering or link delivery.
"""

import hashlib
import hmac
import json
import re
import time
import urllib.parse
import uuid
from pathlib import Path


def _client(service, region, profile=None):
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError("AWS support requires: pip install -r requirements-cloud.txt") from error
    if profile:
        return boto3.Session(profile_name=profile, region_name=region).client(service)
    return boto3.client(service, region_name=region)


def _config(config):
    config = dict(config or {})
    for key in ("region", "bucket", "cluster", "render_task", "delivery_task",
                "subnets", "security_group"):
        if not config.get(key):
            raise ValueError(f"AWS setting '{key}' is required")
    if isinstance(config["subnets"], str):
        config["subnets"] = [x.strip() for x in config["subnets"].split(",") if x.strip()]
    config.setdefault("container", "suno-worker")
    return config


def _identity(value, label):
    value = str(value or "")
    maximum = 36 if label == "attempt id" else 80
    if not re.fullmatch(rf"[A-Za-z0-9_-]{{8,{maximum}}}", value):
        raise ValueError(f"invalid {label}")
    return value


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _key(uri, bucket):
    prefix = f"s3://{bucket}/"
    if not str(uri).startswith(prefix):
        raise ValueError("object is outside the configured private bucket")
    return uri[len(prefix):]


def _uri(config, key):
    return f"s3://{config['bucket']}/{key}"


def _upload_file(config, path, key):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = _sha256(path)
    parent, name = key.rsplit("/", 1) if "/" in key else ("", key)
    item = Path(name)
    key = f"{parent + '/' if parent else ''}{item.stem}-{digest}{item.suffix}"
    _client("s3", config["region"], config.get("profile")).upload_file(
        str(path), config["bucket"], key)
    return {"uri": _uri(config, key), "sha256": digest, "size": path.stat().st_size}


def _put_json_once(config, key, payload):
    s3 = _client("s3", config["region"], config.get("profile"))
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    try:
        s3.put_object(Bucket=config["bucket"], Key=key, Body=data,
                      ContentType="application/json", IfNoneMatch="*")
    except Exception as error:
        code = getattr(error, "response", {}).get("Error", {}).get("Code")
        if code not in ("PreconditionFailed", "ConditionalRequestConflict", "412", "409"):
            raise
        existing = s3.get_object(Bucket=config["bucket"], Key=key)["Body"].read()
        if existing != data:
            raise RuntimeError("cloud attempt already exists with different inputs") from error
    return _uri(config, key)


def _get_json(config, uri):
    s3 = _client("s3", config["region"], config.get("profile"))
    try:
        body = s3.get_object(Bucket=config["bucket"], Key=_key(uri, config["bucket"]))["Body"]
    except Exception as error:
        code = getattr(error, "response", {}).get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404", "NotFound"):
            return None
        raise
    return json.loads(body.read())


def _run_task(config, mode, attempt, manifest_uri):
    ecs = _client("ecs", config["region"], config.get("profile"))
    task_definition = config["render_task"] if mode == "render" else config["delivery_task"]
    overrides = {"containerOverrides": [{"name": config["container"],
        "command": ["python", "aws_worker.py", mode, "--manifest-uri", manifest_uri]}]}
    if mode == "render":
        sizes = {"economy": (2048, 4096), "balanced": (4096, 8192),
                 "large": (8192, 16384)}
        size = config.get("render_size") or "large"
        if size not in sizes:
            raise ValueError("invalid AWS render task size")
        overrides["cpu"], overrides["memory"] = map(str, sizes[size])
    response = ecs.run_task(
        cluster=config["cluster"], taskDefinition=task_definition,
        launchType="FARGATE", platformVersion="LATEST", count=1,
        clientToken=attempt, startedBy=attempt,
        networkConfiguration={"awsvpcConfiguration": {
            "subnets": config["subnets"], "securityGroups": [config["security_group"]],
            "assignPublicIp": "ENABLED"}},
        overrides=overrides,
    )
    if response.get("failures") or not response.get("tasks"):
        raise RuntimeError(f"ECS did not start the {mode} task: {response.get('failures')}")
    return response["tasks"][0]["taskArn"]


def _dispatch(config, job_id, attempt, assets, settings, mode):
    config = _config(config)
    job_id, attempt = _identity(job_id, "job id"), _identity(attempt, "attempt id")
    prefix = f"{mode}-inputs/{job_id}/{attempt}"
    uploaded = {}
    for name, path in assets.items():
        if path is None:
            continue
        if name not in ("audio", "background", "subtitles", "focus_background", "video"):
            raise ValueError("unsupported render asset")
        uploaded[name] = _upload_file(config, path, f"{prefix}/{name}{Path(path).suffix}")
    result_prefix = f"{mode}-results/{job_id}/{attempt}"
    clean_settings = {key: value for key, value in dict(settings or {}).items()
                      if key in {"height", "fps", "vis", "crf", "accent", "shimmer",
                                 "interlude_mode", "lyric_focus_band", "focus_opacity",
                                 "drawtext_chain", "channel_id"}}
    drawtext_assets = []
    chain = str(clean_settings.get("drawtext_chain") or "")
    if chain:
        references = dict.fromkeys(re.findall(r"(?:fontfile|textfile)='((?:\\.|[^'])*)'", chain))
        for index, reference in enumerate(references):
            source = Path(reference.replace("\\:", ":").replace("\\'", "'").replace("\\\\", "\\"))
            if not source.is_file():
                raise FileNotFoundError(f"drawtext input is missing: {source.name}")
            token = f"SUNO_DRAWTEXT_{index}"
            item = _upload_file(config, source, f"{prefix}/drawtext-{index:04d}{source.suffix}")
            drawtext_assets.append({"reference": token, **item})
            chain = chain.replace(reference, token)
        clean_settings["drawtext_chain"] = chain
    manifest = {
        "schema_version": 1, "mode": mode, "job_id": job_id, "attempt_id": attempt,
        "assets": uploaded, "drawtext_assets": drawtext_assets, "settings": clean_settings,
        "output_uri": _uri(config, f"{result_prefix}/video.mp4") if mode == "render" else None,
        "result_uri": _uri(config, f"{result_prefix}/result.json"),
    }
    manifest_uri = _put_json_once(config, f"{prefix}/manifest.json", manifest)
    task_arn = _run_task(config, mode, attempt, manifest_uri)
    return {"mode": mode, "attempt_id": attempt, "job_id": job_id,
            "task_arn": task_arn, "manifest_uri": manifest_uri,
            "output_uri": manifest["output_uri"], "result_uri": manifest["result_uri"],
            "status": "running"}


def dispatch_render(config, job_id, attempt, assets, settings):
    if not assets.get("audio") or not assets.get("background"):
        raise ValueError("audio and prepared background are required")
    return _dispatch(config, job_id, attempt, assets, settings, "render")


def dispatch_slack_delivery(config, job_id, attempt, mp4, channel_id):
    if not re.fullmatch(r"[CGD][A-Z0-9]{8,}", str(channel_id or "")):
        raise ValueError("a Slack channel ID is required")
    config = _config(config)
    job_id, attempt = _identity(job_id, "job id"), _identity(attempt, "attempt id")
    if str(mp4).startswith("s3://"):
        key = _key(mp4, config["bucket"])
        parts = key.split("/")
        if (len(parts) != 4 or parts[0] != "render-results" or parts[1] != job_id or
                not re.fullmatch(r"[A-Za-z0-9_-]{8,36}", parts[2]) or parts[3] != "video.mp4"):
            raise ValueError("S3 delivery input must be this job's rendered video")
        render_result = _get_json(config, _uri(config, f"render-results/{job_id}/{parts[2]}/result.json"))
        if (not render_result or render_result.get("status") != "succeeded" or
                render_result.get("output_uri") != mp4 or
                not re.fullmatch(r"[0-9a-f]{64}", str(render_result.get("sha256", ""))) or
                not isinstance(render_result.get("size"), int) or
                isinstance(render_result.get("size"), bool) or render_result["size"] < 0):
            raise ValueError("S3 delivery input has no verified render result")
        video = {"uri": mp4, "sha256": render_result["sha256"], "size": render_result["size"]}
    else:
        video = _upload_file(config, mp4, f"delivery-inputs/{job_id}/{attempt}/video.mp4")
    prefix = f"delivery-inputs/{job_id}/{attempt}"
    result_uri = _uri(config, f"delivery-results/{job_id}/{attempt}/result.json")
    manifest = {"schema_version": 1, "mode": "delivery", "job_id": job_id,
                "attempt_id": attempt, "assets": {"video": video},
                "settings": {"channel_id": channel_id}, "result_uri": result_uri}
    manifest_uri = _put_json_once(config, f"{prefix}/manifest.json", manifest)
    task_arn = _run_task(config, "delivery", attempt, manifest_uri)
    return {"mode": "delivery", "attempt_id": attempt, "job_id": job_id,
            "task_arn": task_arn, "manifest_uri": manifest_uri,
            "result_uri": result_uri, "status": "running"}


def reconcile_render(config, saved):
    config = _config(config)
    if saved.get("task_arn"):
        return saved
    attempt = _identity(saved.get("attempt_id"), "attempt id")
    ecs = _client("ecs", config["region"], config.get("profile"))
    tasks = ecs.list_tasks(cluster=config["cluster"], startedBy=attempt,
                           maxResults=100).get("taskArns", [])
    if tasks:
        return {**saved, "task_arn": tasks[0]}
    token = None
    while True:
        params = {"cluster": config["cluster"], "desiredStatus": "STOPPED", "maxResults": 100}
        if token:
            params["nextToken"] = token
        page = ecs.list_tasks(**params)
        stopped = page.get("taskArns", [])
        if stopped:
            details = ecs.describe_tasks(cluster=config["cluster"], tasks=stopped).get("tasks", [])
            for task in details:
                if task.get("startedBy") == attempt:
                    return {**saved, "task_arn": task["taskArn"]}
        token = page.get("nextToken")
        if not token:
            break
    if saved.get("result_uri"):
        result = _get_json(config, saved["result_uri"])
        if result:
            if (result.get("attempt_id") != attempt or
                    saved.get("job_id") and result.get("job_id") != saved["job_id"]):
                raise RuntimeError("cloud result does not match this task attempt")
            return {**saved, "status": result.get("status"), "result": result}
    return saved


def wait_or_poll_render(config, execution, wait_seconds=0):
    config = _config(config)
    deadline = time.monotonic() + max(0, float(wait_seconds))
    while True:
        result = _get_json(config, execution["result_uri"])
        if result:
            if (result.get("attempt_id") != execution.get("attempt_id") or
                    execution.get("job_id") and result.get("job_id") != execution["job_id"]):
                raise RuntimeError("cloud result does not match this task attempt")
            status = result.get("status")
            return {**execution, "status": status, "result": result}
        task_arn = execution.get("task_arn")
        if task_arn:
            tasks = _client("ecs", config["region"], config.get("profile")).describe_tasks(
                cluster=config["cluster"], tasks=[task_arn]).get("tasks", [])
            if tasks and tasks[0].get("lastStatus") == "STOPPED":
                return {**execution, "status": "failed", "result": {
                    "error": tasks[0].get("stoppedReason") or "task stopped without a result"}}
        if time.monotonic() >= deadline:
            return {**execution, "status": "running"}
        time.sleep(min(5, deadline - time.monotonic()))


def download_verified(config, execution, destination):
    config = _config(config)
    result = _get_json(config, execution["result_uri"])
    if not result or result.get("status") != "succeeded":
        raise RuntimeError("cloud render has no successful result")
    if (result.get("attempt_id") != execution.get("attempt_id") or
            result.get("job_id") != execution.get("job_id") or
            result.get("output_uri") != execution.get("output_uri") or
            not re.fullmatch(r"[0-9a-f]{64}", str(result.get("sha256", ""))) or
            not isinstance(result.get("size"), int) or isinstance(result.get("size"), bool) or
            result["size"] < 0):
        raise RuntimeError("cloud result does not match this task attempt")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".download")
    try:
        _client("s3", config["region"], config.get("profile")).download_file(
            config["bucket"], _key(result["output_uri"], config["bucket"]), str(temporary))
        if temporary.stat().st_size != int(result["size"]) or _sha256(temporary) != result["sha256"]:
            raise RuntimeError("downloaded cloud video failed checksum verification")
        with temporary.open("rb") as stream:
            if stream.read(12)[4:8] != b"ftyp":
                raise RuntimeError("cloud result is not an MP4")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def cancel_render(config, execution):
    config = _config(config)
    if execution.get("task_arn"):
        _client("ecs", config["region"], config.get("profile")).stop_task(
            cluster=config["cluster"], task=execution["task_arn"], reason="Cancelled in Suno Studio")


def upload_delivery_object(config, job_id, source_path):
    config = _config(config)
    job_id = _identity(job_id, "job id")
    key = f"delivery-objects/{job_id}/{uuid.uuid4().hex}.mp4"
    return _upload_file(config, source_path, key)


def email_link_for_file(config, job_id, source_path, expires_seconds=259200):
    """A three-day token reaches a Lambda that issues a fresh short S3 URL."""
    config = _config(config)
    seconds = int(expires_seconds)
    if not 1 <= seconds <= 259200:
        raise ValueError("email links may last at most three days")
    link_url, secret = config.get("link_url"), config.get("link_secret")
    if not link_url or not str(link_url).startswith("https://") or not secret:
        raise ValueError("AWS private link service is not configured")
    uploaded = upload_delivery_object(config, job_id, source_path)
    key = _key(uploaded["uri"], config["bucket"])
    expires = int(time.time()) + seconds
    message = f"{key}\n{expires}".encode()
    signature = hmac.new(str(secret).encode(), message, hashlib.sha256).hexdigest()
    query = urllib.parse.urlencode({"key": key, "expires": expires, "sig": signature})
    return {"url": str(link_url).rstrip("/") + "/?" + query,
            "object_uri": uploaded["uri"], "expires_seconds": seconds}
