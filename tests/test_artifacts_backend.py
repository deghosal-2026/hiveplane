# The fake S3 client mirrors boto3's CamelCase keyword arguments.
# ruff: noqa: N803
"""Tests for artifact blob backends (M54-01)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hiveplane.artifacts.backend import (
    LocalBlobBackend,
    S3BlobBackend,
    build_blob_backend,
    content_address,
)
from hiveplane.config import Settings


def test_content_address_is_deterministic_and_content_addressed() -> None:
    assert content_address(b"hello") == content_address(b"hello")
    assert content_address(b"hello") != content_address(b"hello!")
    assert content_address(b"hello").startswith("sha256:")


def test_local_backend_round_trip(tmp_path: Path) -> None:
    backend = LocalBlobBackend(tmp_path)

    location = backend.put("run-1/report.txt", b"hello")

    assert location.startswith("file://")
    assert backend.exists(location) is True
    assert backend.get(location) == b"hello"
    backend.delete(location)
    assert backend.exists(location) is False


def test_local_backend_path_traversal_is_rejected(tmp_path: Path) -> None:
    backend = LocalBlobBackend(tmp_path)

    with pytest.raises(ValueError):
        backend.put("../escape.txt", b"nope")


class _FakeS3:
    """A minimal in-memory S3 client for tests."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[str] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.calls.append("put")
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append("get")
        return {"Body": SimpleNamespace(read=lambda: self.objects[(Bucket, Key)])}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append("head")
        if (Bucket, Key) not in self.objects:
            raise KeyError("no such key")
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.calls.append("delete")
        self.objects.pop((Bucket, Key), None)


def test_s3_backend_round_trip() -> None:
    client = _FakeS3()
    backend = S3BlobBackend(client, bucket="bkt", prefix="artifacts")

    location = backend.put("run-1/report.txt", b"data")

    assert location == "s3://bkt/artifacts/run-1/report.txt"
    assert backend.get(location) == b"data"
    assert backend.exists(location) is True
    backend.delete(location)
    assert backend.exists(location) is False


def test_s3_backend_supports_minio_scheme() -> None:
    backend = S3BlobBackend(_FakeS3(), bucket="bkt", scheme="minio")

    location = backend.put("k", b"data")

    assert location.startswith("minio://bkt/")


def test_s3_backend_rejects_foreign_location() -> None:
    backend = S3BlobBackend(_FakeS3(), bucket="bkt")

    with pytest.raises(ValueError):
        backend.get("file:///tmp/other")


def test_builder_defaults_to_local(tmp_path: Path) -> None:
    settings = Settings.model_validate({"artifacts": {"data_dir": str(tmp_path)}})
    assert isinstance(build_blob_backend(settings), LocalBlobBackend)


def test_builder_selects_s3(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, Any] = {}

    def _client(service: str, **kwargs: Any) -> _FakeS3:
        created["service"] = service
        created["kwargs"] = kwargs
        return _FakeS3()

    fake_boto3 = SimpleNamespace(client=_client)
    fake_botocore = SimpleNamespace(
        Config=lambda **kwargs: {"config": kwargs}
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore.config", fake_botocore)

    settings = Settings.model_validate(
        {
            "artifacts": {
                "backend": "s3",
                "bucket": "bkt",
                "endpoint_url": "http://localhost:9000",
            }
        }
    )
    backend = build_blob_backend(settings)

    assert isinstance(backend, S3BlobBackend)
    assert created["service"] == "s3"
    assert created["kwargs"]["endpoint_url"] == "http://localhost:9000"
    assert created["kwargs"]["config"] == {
        "config": {"s3": {"addressing_style": "path"}}
    }
