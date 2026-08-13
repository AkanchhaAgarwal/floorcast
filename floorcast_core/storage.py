"""
Floorcast — File Storage

Floor plans are the most sensitive thing this product handles. They are a
client's building, and the customers storing them here compete with each other.
So the rules are narrow on purpose:

* Every key begins with the tenant's id. A caller cannot construct a key for
  another tenant, because it never supplies the prefix — this module does.
* Nothing is served from a public URL. Downloads go through short-lived signed
  links, or through the app itself.
* A traversal attempt in a filename is rejected rather than sanitised, because
  quietly repairing hostile input hides the fact that somebody tried.

Set S3_BUCKET (and standard AWS credentials) for object storage. Without it,
files go to a local directory — fine for development, not for more than one
customer, and the module says so rather than pretending otherwise.
"""

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")          # for S3-compatible hosts
LOCAL_ROOT = Path(os.environ.get("FLOORCAST_FILES", "./.files"))
SIGNED_URL_TTL = int(os.environ.get("SIGNED_URL_TTL", "900"))     # 15 minutes

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
ALLOWED_SUFFIXES = {".pdf", ".csv", ".xlsx", ".xls", ".dxf", ".png"}


class StorageError(RuntimeError):
    pass


def object_storage() -> bool:
    return bool(S3_BUCKET)


def _client():
    import boto3
    kw = {"endpoint_url": S3_ENDPOINT} if S3_ENDPOINT else {}
    return boto3.client("s3", **kw)


def _clean(filename: str) -> str:
    """Reject anything that is not a plain filename."""
    name = os.path.basename(str(filename or "").strip())
    if not name or name in (".", ".."):
        raise StorageError("A filename is required.")
    if ".." in name or "/" in name or "\\" in name:
        raise StorageError("That filename is not allowed.")
    if not SAFE_NAME.match(name):
        # keep the extension, replace the rest — but only after the checks above
        stem, dot, suf = name.rpartition(".")
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem or name)[:80] or "file"
        name = f"{stem}{dot}{suf}" if dot else stem
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise StorageError(
            f"{Path(name).suffix or 'That file type'} is not accepted. "
            "Allowed: " + ", ".join(sorted(ALLOWED_SUFFIXES)))
    return name


def key_for(tenant_id: str, kind: str, filename: str) -> str:
    """Build the storage key. The tenant prefix is added here and nowhere else,
    so no caller can reach outside its own space."""
    if not tenant_id:
        raise StorageError("A tenant is required before anything can be stored.")
    kind = re.sub(r"[^a-z0-9_-]+", "", str(kind).lower()) or "misc"
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    return f"tenants/{tenant_id}/{kind}/{stamp}-{_clean(filename)}"


def put(tenant_id: str, kind: str, filename: str, data: bytes) -> dict:
    if data is None:
        raise StorageError("No file content received.")
    if len(data) > MAX_BYTES:
        raise StorageError(f"That file is {len(data) / 1e6:.1f} MB. The limit is "
                           f"{MAX_BYTES / 1e6:.0f} MB.")
    key = key_for(tenant_id, kind, filename)
    digest = hashlib.sha256(data).hexdigest()
    if object_storage():
        _client().put_object(Bucket=S3_BUCKET, Key=key, Body=data,
                             ServerSideEncryption="AES256",
                             Metadata={"tenant": str(tenant_id), "sha256": digest})
    else:
        path = LOCAL_ROOT / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {"key": key, "bytes": len(data), "sha256": digest,
            "store": "s3" if object_storage() else "local"}


def get(tenant_id: str, key: str) -> bytes:
    """Fetch by key, but only if the key belongs to this tenant. The check is
    here as well as in the prefix, because a key can arrive from a database row
    and rows can be wrong."""
    _assert_owned(tenant_id, key)
    if object_storage():
        return _client().get_object(Bucket=S3_BUCKET, Key=key)["Body"].read()
    path = LOCAL_ROOT / key
    if not path.exists():
        raise StorageError("That file is no longer stored.")
    return path.read_bytes()


def signed_url(tenant_id: str, key: str, ttl: int = None) -> str:
    _assert_owned(tenant_id, key)
    if not object_storage():
        raise StorageError("Signed links need object storage; serve the bytes instead.")
    return _client().generate_presigned_url(
        "get_object", Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=ttl or SIGNED_URL_TTL)


def delete(tenant_id: str, key: str) -> None:
    _assert_owned(tenant_id, key)
    if object_storage():
        _client().delete_object(Bucket=S3_BUCKET, Key=key)
    else:
        p = LOCAL_ROOT / key
        if p.exists():
            p.unlink()


def listing(tenant_id: str, kind: str = None) -> list:
    prefix = f"tenants/{tenant_id}/" + (f"{kind}/" if kind else "")
    if object_storage():
        out, token = [], None
        while True:
            kw = {"Bucket": S3_BUCKET, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = _client().list_objects_v2(**kw)
            out += [{"key": o["Key"], "bytes": o["Size"], "at": o["LastModified"]}
                    for o in resp.get("Contents", [])]
            token = resp.get("NextContinuationToken")
            if not token:
                break
        return out
    root = LOCAL_ROOT / prefix
    if not root.exists():
        return []
    return [{"key": str(p.relative_to(LOCAL_ROOT)), "bytes": p.stat().st_size,
             "at": datetime.fromtimestamp(p.stat().st_mtime)}
            for p in root.rglob("*") if p.is_file()]


def _assert_owned(tenant_id: str, key: str) -> None:
    if not tenant_id:
        raise StorageError("A tenant is required.")
    expected = f"tenants/{tenant_id}/"
    if not str(key).startswith(expected) or ".." in str(key):
        raise StorageError("That file does not belong to this account.")


def health() -> dict:
    """Shown in setup, so an operator can see where files are actually going."""
    if object_storage():
        return {"store": "s3", "bucket": S3_BUCKET,
                "endpoint": S3_ENDPOINT or "aws", "multi_tenant_safe": True}
    return {"store": "local", "path": str(LOCAL_ROOT.resolve()),
            "multi_tenant_safe": False,
            "note": "Local files are for development. Set S3_BUCKET before a "
                    "second customer is onboarded."}
