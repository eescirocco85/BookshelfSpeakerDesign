"""JSON-backed REW test plan settings."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


DEFAULT_TEST_PLAN_PATH = Path(__file__).resolve().parents[1] / "test_settings.midrange.json"


@dataclass(frozen=True)
class LevelMatchSettings:
    id: str
    label: str
    low_hz: int
    high_hz: int
    generator_level_dbfs: float
    target_spl_db: float
    tolerance_db: float
    settle_seconds: float
    live_auto_stop_seconds: int
    live_rta_averaging: str


@dataclass(frozen=True)
class TestItem:
    id: str
    type: str
    label: str
    level_dbfs: float
    capture_seconds: float
    start_hz: int | None = None
    end_hz: int | None = None
    f1_hz: int | None = None
    f2_hz: int | None = None
    decade_spacing: str | None = None
    ir_right_window_ms: float | None = None


@dataclass(frozen=True)
class TestPlan:
    path: Path
    plan_name: str
    level_match: LevelMatchSettings
    tests: tuple[TestItem, ...]

    def tests_of_type(self, test_type: str) -> tuple[TestItem, ...]:
        return tuple(test for test in self.tests if test.type == test_type)

    def first_test_of_type(self, test_type: str) -> TestItem | None:
        matches = self.tests_of_type(test_type)
        return matches[0] if matches else None


def load_test_plan(path: Path = DEFAULT_TEST_PLAN_PATH) -> TestPlan:
    """Load the JSON test plan used by the GUI."""
    data = json.loads(path.read_text(encoding="utf-8"))
    defaults = _dict(data.get("defaults"))
    level_match_data = _dict(data.get("levelMatch"))
    level_match = LevelMatchSettings(
        id=str(level_match_data.get("id", "level_match")),
        label=str(level_match_data.get("label", "Pink noise level match")),
        low_hz=_int(level_match_data.get("lowHz"), 300),
        high_hz=_int(level_match_data.get("highHz"), 2000),
        generator_level_dbfs=_float(level_match_data.get("generatorLevelDbfs"), -10.0),
        target_spl_db=_float(level_match_data.get("targetSplDb"), 100.0),
        tolerance_db=_float(level_match_data.get("toleranceDb"), 0.5),
        settle_seconds=_float(level_match_data.get("settleSeconds"), 5.0),
        live_auto_stop_seconds=_int(level_match_data.get("liveAutoStopSeconds"), 30),
        live_rta_averaging=str(level_match_data.get("liveRtaAveraging", "Exponential 0.88")),
    )
    default_level = _float(defaults.get("generatorLevelDbfs"), -20.0)
    default_capture = _float(defaults.get("rtaCaptureSeconds"), 10.0)
    default_ir_right = _float(defaults.get("irRightWindowMs"), 5.0)
    tests = tuple(_load_test_item(item, default_level, default_capture, default_ir_right) for item in data.get("tests", ()))
    return TestPlan(
        path=path,
        plan_name=str(data.get("planName", path.stem)),
        level_match=level_match,
        tests=tests,
    )


def _load_test_item(
    item: Any,
    default_level: float,
    default_capture: float,
    default_ir_right: float,
) -> TestItem:
    values = _dict(item)
    return TestItem(
        id=str(values.get("id", values.get("label", "test"))),
        type=str(values.get("type", "")),
        label=str(values.get("label", values.get("id", "Configured test"))),
        level_dbfs=_float(values.get("levelDbfs"), default_level),
        capture_seconds=_float(values.get("captureSeconds"), default_capture),
        start_hz=_optional_int(values.get("startHz")),
        end_hz=_optional_int(values.get("endHz")),
        f1_hz=_optional_int(values.get("f1Hz")),
        f2_hz=_optional_int(values.get("f2Hz")),
        decade_spacing=_optional_str(values.get("decadeSpacing")),
        ir_right_window_ms=_float(values.get("irRightWindowMs"), default_ir_right),
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _int(value: Any, default: int) -> int:
    return int(value) if isinstance(value, (int, float)) else default


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None
