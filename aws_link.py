"""Lambda Function URL handler for three-day private download links.

The email URL carries a short HMAC token. On each click, this function issues
a fresh ten-minute S3 URL, so temporary AWS role credentials never shorten the
three-day email link lifetime.
"""

import hashlib
import hmac
import os
import re
import time


def handler(event, _context):
    event = event if isinstance(event, dict) else {}
    query = event.get("queryStringParameters")
    query = query if isinstance(query, dict) else {}
    key, expiry, signature = (query.get(name, "") for name in ("key", "expires", "sig"))
    bucket, secret = os.environ.get("SUNO_BUCKET", ""), os.environ.get("LINK_SECRET", "")
    if not all(isinstance(value, str) for value in (key, expiry, signature)):
        return {"statusCode": 403, "body": "This download link is invalid or expired."}
    now = int(time.time())
    if (not bucket or not secret or
            not re.fullmatch(r"delivery-objects/[A-Za-z0-9_-]{8,80}/[A-Za-z0-9_-]{1,128}\.mp4", key) or
            not re.fullmatch(r"[0-9]{1,10}", expiry) or
            not re.fullmatch(r"[0-9a-f]{64}", signature) or
            int(expiry) < now or int(expiry) > now + 259200):
        return {"statusCode": 403, "body": "This download link is invalid or expired."}
    expected = hmac.new(secret.encode(), f"{key}\n{expiry}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return {"statusCode": 403, "body": "This download link is invalid or expired."}
    try:
        import boto3
        url = boto3.client("s3").generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=600)
    except Exception:
        return {"statusCode": 503, "body": "The download is temporarily unavailable."}
    return {"statusCode": 302, "headers": {"Location": url, "Cache-Control": "no-store"},
            "body": ""}
