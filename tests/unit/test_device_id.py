from __future__ import annotations

from typing import cast

from backend.rbac.device_id import validate_device_id


def test_validate_device_id_accepts_only_opaque_uuid_format() -> None:
    value = "vdev_12345678-1234-1234-1234-123456789abc"

    assert validate_device_id(value) == value
    assert validate_device_id("hostname-alice") is None
    assert validate_device_id(None) is None
    assert validate_device_id(cast("str", 123)) is None
