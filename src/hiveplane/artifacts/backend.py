"""Content-addressed artifact blob backends (M54-01, D38).

Bytes live in a backend; the artifact metadata (hash, size, location) lives in
the metadata store. A local filesystem backend is the default; an S3/MinIO
backend is selected by configuration. The S3 client is injected so it can be
faked in tests.
"""

# boto3's S3 API uses CamelCase keyword arguments (Bucket/Key/Body).
# ruff: noqa: N803

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any, Protocol

from hiveplane.config import Settings, get_settings

_READ_CHUNK = 1024 * 1024


def content_address(data: bytes) -> str:
    """Return the content-addressed digest of ``data`` (sha256)."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class BlobBackend(Protocol):
    """Object storage for artifact bytes, addressed by location URI."""

    def put(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key``; return the artifact location URI."""
        ...

    def get(self, location: str) -> bytes:
        """Return the bytes stored at ``location``."""
        ...

    def exists(self, location: str) -> bool:
        """Return whether ``location`` exists."""
        ...

    def delete(self, location: str) -> None:
        """Delete ``location`` if present."""
        ...


class LocalBlobBackend:
    """A filesystem blob backend rooted at a directory."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        root = self._root.resolve()
        path = (root / key).resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"artifact key escapes the storage root: {key!r}")
        return path

    def put(self, key: str, data: bytes) -> str:
        """Write ``data`` to ``root/key`` and return a ``file://`` location."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"file://{path}"

    def _from_location(self, location: str) -> Path:
        raw = location[len("file://") :] if location.startswith("file://") else location
        return Path(raw)

    def get(self, location: str) -> bytes:
        """Read the bytes at a ``file://`` location."""
        return self._from_location(location).read_bytes()

    def exists(self, location: str) -> bool:
        """Return whether the file exists."""
        return self._from_location(location).is_file()

    def delete(self, location: str) -> None:
        """Delete the file if present."""
        self._from_location(location).unlink(missing_ok=True)


class S3Client(Protocol):
    """The subset of the boto3 S3 client the backend uses."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> Any: ...

    def get_object(self, *, Bucket: str, Key: str) -> Any: ...

    def head_object(self, *, Bucket: str, Key: str) -> Any: ...

    def delete_object(self, *, Bucket: str, Key: str) -> Any: ...


class S3BlobBackend:
    """An S3/MinIO blob backend over an injected client."""

    def __init__(
        self,
        client: S3Client,
        *,
        bucket: str,
        prefix: str = "",
        scheme: str = "s3",
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._scheme = scheme

    def _key(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def _parse(self, location: str) -> str:
        prefix = f"{self._scheme}://{self._bucket}/"
        if not location.startswith(prefix):
            raise ValueError(
                f"location {location!r} is not an {self._scheme} location in "
                f"bucket {self._bucket!r}"
            )
        return location[len(prefix) :]

    def put(self, key: str, data: bytes) -> str:
        """Store ``data`` and return an ``s3://bucket/key`` location."""
        object_key = self._key(key)
        self._client.put_object(Bucket=self._bucket, Key=object_key, Body=data)
        return f"{self._scheme}://{self._bucket}/{object_key}"

    def get(self, location: str) -> bytes:
        """Return the bytes stored at ``location``."""
        response = self._client.get_object(
            Bucket=self._bucket, Key=self._parse(location)
        )
        return bytes(response["Body"].read())

    def exists(self, location: str) -> bool:
        """Return whether ``location`` exists."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._parse(location))
        except Exception:
            return False
        return True

    def delete(self, location: str) -> None:
        """Delete ``location``."""
        self._client.delete_object(Bucket=self._bucket, Key=self._parse(location))


def _build_s3_client(settings: Settings) -> S3Client:
    """Build a boto3 S3 client from artifact settings (path-style for MinIO)."""
    config = settings.artifacts
    boto3 = importlib.import_module("boto3")
    kwargs: dict[str, Any] = {"region_name": config.region}
    if config.endpoint_url:
        kwargs["endpoint_url"] = config.endpoint_url
        botocore_config = importlib.import_module("botocore.config")
        kwargs["config"] = botocore_config.Config(s3={"addressing_style": "path"})
    if config.access_key:
        kwargs["aws_access_key_id"] = config.access_key.get_secret_value()
    if config.secret_key:
        kwargs["aws_secret_access_key"] = config.secret_key.get_secret_value()
    return boto3.client("s3", **kwargs)  # type: ignore[no-any-return]


def build_blob_backend(settings: Settings | None = None) -> BlobBackend:
    """Build the configured blob backend (local by default)."""
    resolved = settings or get_settings()
    config = resolved.artifacts
    if config.backend == "s3":
        return S3BlobBackend(
            _build_s3_client(resolved),
            bucket=config.bucket,
            prefix=config.prefix,
            scheme=config.scheme,
        )
    return LocalBlobBackend(config.data_dir)
