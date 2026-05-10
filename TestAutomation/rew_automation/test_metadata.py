"""Metadata for generated REW test measurements."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TestMetadata:
    test_name: str
    driver_name: str
    group_name: str

