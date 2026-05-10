"""Frequency sweep measurement helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time
from typing import Any

from .client import RewClient
from .logging_utils import write_debug
from .rew_groups import find_or_create_group
from .session import MAIN_OUTPUT_CHANNEL
from .test_metadata import TestMetadata


DEFAULT_SWEEP_START_HZ = 150
DEFAULT_SWEEP_END_HZ = 6000
SWEEP_LENGTH = "1M"
DEFAULT_SWEEP_LEVEL_DBFS = -20.0
SWEEP_TIMEOUT_SECONDS = 45.0
SWEEP_LEFT_WINDOW_MS = 1.0
SWEEP_RIGHT_WINDOW_MS = 5.0


@dataclass(frozen=True)
class SweepResult:
    measurement_id: str
    title: str
    group_name: str
    start_hz: int
    end_hz: int
    level_dbfs: float
    sweep_length: str
    left_window_ms: float
    right_window_ms: float
    warnings: tuple[str, ...]


def run_frequency_sweep(
    client: RewClient,
    start_hz: int = DEFAULT_SWEEP_START_HZ,
    end_hz: int = DEFAULT_SWEEP_END_HZ,
    level_dbfs: float = DEFAULT_SWEEP_LEVEL_DBFS,
    driver_name: str = "DEBUG",
    group_name: str | None = None,
    right_window_ms: float = SWEEP_RIGHT_WINDOW_MS,
) -> SweepResult:
    """Run a frequency response sweep and assign the saved measurement to a group."""
    if start_hz >= end_hz:
        raise ValueError("Sweep start_hz must be lower than end_hz for the REW API.")

    test_name = build_sweep_test_name(start_hz, end_hz, right_window_ms)
    metadata = TestMetadata(
        test_name=test_name,
        driver_name=driver_name,
        group_name=group_name or test_name,
    )
    before = _measurement_ids(client)
    warnings_before = _application_warnings(client)
    write_debug(f"Starting frequency sweep with existing measurements: {before}")
    _configure_sweep(client, start_hz=start_hz, end_hz=end_hz, level_dbfs=level_dbfs)
    _debug_response("/measure/command SPL", client.post("/measure/command", {"command": "SPL"}))
    measurement_id = _wait_for_new_measurement(client, before)
    set_ir_windows(client, measurement_id, right_window_ms=right_window_ms)
    title = rename_and_group_sweep_measurement(
        client,
        measurement_id,
        metadata,
        start_hz=start_hz,
        end_hz=end_hz,
        level_dbfs=level_dbfs,
        right_window_ms=right_window_ms,
    )
    new_warnings = _new_application_warnings(warnings_before, _application_warnings(client))
    for warning in new_warnings:
        write_debug(f"Sweep warning: {warning}")
    return SweepResult(
        measurement_id=measurement_id,
        title=title,
        group_name=metadata.group_name,
        start_hz=start_hz,
        end_hz=end_hz,
        level_dbfs=level_dbfs,
        sweep_length=SWEEP_LENGTH,
        left_window_ms=SWEEP_LEFT_WINDOW_MS,
        right_window_ms=right_window_ms,
        warnings=tuple(new_warnings),
    )


def _configure_sweep(client: RewClient, start_hz: int, end_hz: int, level_dbfs: float) -> None:
    # REW's measurement sweep API requires the lower frequency first.
    _debug_response("/audio/java/output-channel", client.post("/audio/java/output-channel", {"channel": MAIN_OUTPUT_CHANNEL}))
    _debug_response("/measure/timing/reference", client.post("/measure/timing/reference", "Loopback"))
    _debug_response("/measure/playback-mode", client.post("/measure/playback-mode", "From REW"))
    _debug_response("/measure/measurement-mode", client.post("/measure/measurement-mode", "Single"))
    _debug_response("/measure/sweep/repetitions", client.post("/measure/sweep/repetitions", 1))
    _debug_response("/measure/level", client.post("/measure/level", {"value": level_dbfs, "unit": "dBFS"}))
    _debug_response(
        "/measure/sweep/configuration",
        client.put(
            "/measure/sweep/configuration",
            {
                "startFrequency": start_hz,
                "endFrequency": end_hz,
                "length": SWEEP_LENGTH,
                "fillSilenceWithDither": False,
            },
        ),
    )
    _debug_response("/measure/sweep/configuration readback", client.get("/measure/sweep/configuration"))
    _debug_response("/measure/level readback", client.get("/measure/level"))
    _debug_response("/measure/timing/reference readback", client.get("/measure/timing/reference"))


def set_ir_windows(
    client: RewClient,
    measurement_id: str,
    right_window_ms: float = SWEEP_RIGHT_WINDOW_MS,
) -> None:
    current = client.get(f"/measurements/{measurement_id}/ir-windows")
    _debug_response(f"/measurements/{measurement_id}/ir-windows current", current)
    body = {
        "leftWindowWidthms": SWEEP_LEFT_WINDOW_MS,
        "rightWindowWidthms": right_window_ms,
    }
    _debug_response(
        f"/measurements/{measurement_id}/ir-windows PUT",
        client.put(f"/measurements/{measurement_id}/ir-windows", body),
    )
    _debug_response(
        f"/measurements/{measurement_id}/ir-windows readback",
        client.get(f"/measurements/{measurement_id}/ir-windows"),
    )


def rename_and_group_sweep_measurement(
    client: RewClient,
    measurement_id: str,
    metadata: TestMetadata,
    start_hz: int = DEFAULT_SWEEP_START_HZ,
    end_hz: int = DEFAULT_SWEEP_END_HZ,
    level_dbfs: float = DEFAULT_SWEEP_LEVEL_DBFS,
    right_window_ms: float = SWEEP_RIGHT_WINDOW_MS,
) -> str:
    summary = client.get(f"/measurements/{measurement_id}")
    if not isinstance(summary, dict):
        raise RuntimeError(f"Could not fetch sweep measurement {measurement_id}: {summary}")

    group = find_or_create_group(
        client,
        metadata.group_name,
        notes=f"REW automation group for {metadata.test_name}",
    )
    title = build_sweep_title(metadata, level_dbfs)
    existing_notes = str(summary.get("notes", "") or "")
    notes = build_sweep_notes(metadata, existing_notes, start_hz, end_hz, level_dbfs, right_window_ms)
    body = {"title": title, "notes": notes, "groupID": group.get("uuid")}
    _debug_response(f"/measurements/{measurement_id} PUT", client.put(f"/measurements/{measurement_id}", body))
    _debug_response(f"/measurements/{measurement_id} readback", client.get(f"/measurements/{measurement_id}"))
    return title


def build_sweep_title(metadata: TestMetadata, level_dbfs: float = DEFAULT_SWEEP_LEVEL_DBFS) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    return f"{metadata.driver_name} {metadata.test_name} {level_dbfs:g} dBFS {stamp}"


def build_sweep_test_name(
    start_hz: int = DEFAULT_SWEEP_START_HZ,
    end_hz: int = DEFAULT_SWEEP_END_HZ,
    right_window_ms: float = SWEEP_RIGHT_WINDOW_MS,
) -> str:
    return f"Sweep {start_hz:g}Hz-{end_hz:g}Hz IR {SWEEP_LEFT_WINDOW_MS:g}msL {right_window_ms:g}msR"


def build_sweep_notes(
    metadata: TestMetadata,
    existing_notes: str,
    start_hz: int = DEFAULT_SWEEP_START_HZ,
    end_hz: int = DEFAULT_SWEEP_END_HZ,
    level_dbfs: float = DEFAULT_SWEEP_LEVEL_DBFS,
    right_window_ms: float = SWEEP_RIGHT_WINDOW_MS,
) -> str:
    notes = [
        "REW automation sweep measurement",
        f"Driver: {metadata.driver_name}",
        f"Test: {metadata.test_name}",
        f"Group: {metadata.group_name}",
        f"Sweep range: {start_hz:g} Hz to {end_hz:g} Hz",
        f"Sweep length: {SWEEP_LENGTH}",
        f"Measurement level: {level_dbfs:g} dBFS",
        f"Output channel: {MAIN_OUTPUT_CHANNEL}",
        "Timing reference: Loopback",
        f"IR left window: {SWEEP_LEFT_WINDOW_MS:g} ms before reference",
        f"IR right window: {right_window_ms:g} ms after reference",
    ]
    if existing_notes:
        notes.extend(["", existing_notes])
    return "\n".join(notes)


def _wait_for_new_measurement(client: RewClient, previous_ids: set[str]) -> str:
    deadline = time.monotonic() + SWEEP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        current_ids = _measurement_ids(client)
        new_ids = current_ids - previous_ids
        if new_ids:
            selected_uuid = _optional_get(client, "/measurements/selected-uuid")
            if isinstance(selected_uuid, str) and selected_uuid in new_ids:
                write_debug(f"New selected sweep measurement: {selected_uuid}")
                return selected_uuid
            measurement_id = sorted(new_ids)[-1]
            write_debug(f"New sweep measurement: {measurement_id}")
            return measurement_id
        time.sleep(0.5)
    raise TimeoutError("Timed out waiting for REW to create the sweep measurement.")


def _measurement_ids(client: RewClient) -> set[str]:
    measurements = client.get("/measurements")
    if not isinstance(measurements, dict):
        return set()
    ids = set()
    for key, summary in measurements.items():
        if isinstance(summary, dict) and summary.get("uuid"):
            ids.add(str(summary["uuid"]))
        else:
            ids.add(str(key))
    return ids


def _optional_get(client: RewClient, path: str) -> Any:
    try:
        return client.get(path)
    except Exception as exc:
        write_debug(f"{path} failed: {exc}")
    return None


def _application_warnings(client: RewClient) -> list[dict[str, Any]]:
    warnings = client.get("/application/warnings")
    if isinstance(warnings, list):
        return [warning for warning in warnings if isinstance(warning, dict)]
    return []


def _new_application_warnings(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> list[str]:
    before_keys = {_warning_key(warning) for warning in before}
    new_warnings = []
    for warning in after:
        if _warning_key(warning) not in before_keys:
            title = str(warning.get("title", "Warning"))
            message = str(warning.get("message", "")).replace("<br>", " ").replace("\n", " ")
            new_warnings.append(f"{title}: {message}".strip())
    return new_warnings


def _warning_key(warning: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(warning.get("time", "")),
        str(warning.get("title", "")),
        str(warning.get("message", "")),
    )


def _debug_response(label: str, data: Any) -> None:
    write_debug(f"{label}: {data}")
