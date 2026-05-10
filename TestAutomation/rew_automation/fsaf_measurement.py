"""Manual FSAF measurement bookkeeping helpers."""

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


FSAF_LEVEL_DBFS = -20.0
FSAF_LEFT_WINDOW_MS = 1.0
FSAF_RIGHT_WINDOW_MS = 5.0
FSAF_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class FsafResult:
    measurement_id: str
    title: str
    group_name: str
    level_dbfs: float
    left_window_ms: float
    right_window_ms: float


def run_fsaf_measurement(
    client: RewClient,
    level_dbfs: float = FSAF_LEVEL_DBFS,
    driver_name: str = "DEBUG",
    group_name: str | None = None,
    right_window_ms: float = FSAF_RIGHT_WINDOW_MS,
) -> FsafResult:
    """Deprecated direct FSAF path kept for compatibility."""
    test_name = build_fsaf_test_name(right_window_ms)
    metadata = TestMetadata(
        test_name=test_name,
        driver_name=driver_name,
        group_name=group_name or test_name,
    )
    before = _measurement_ids(client)
    write_debug(f"Starting FSAF measurement with existing measurements: {before}")
    _configure_fsaf(client, level_dbfs, metadata)
    _debug_response("/measure/command SPL", client.post("/measure/command", {"command": "SPL"}))
    measurement_id = _wait_for_new_measurement(client, before)
    set_ir_windows(client, measurement_id, right_window_ms=right_window_ms)
    title = rename_and_group_fsaf_measurement(client, measurement_id, metadata, level_dbfs, right_window_ms)
    return FsafResult(
        measurement_id=measurement_id,
        title=title,
        group_name=metadata.group_name,
        level_dbfs=level_dbfs,
        left_window_ms=FSAF_LEFT_WINDOW_MS,
        right_window_ms=right_window_ms,
    )


def snapshot_measurements(client: RewClient) -> set[str]:
    """Capture the current measurement IDs before the user runs FSAF manually."""
    ids = _measurement_ids(client)
    write_debug(f"Manual FSAF snapshot measurement IDs: {ids}")
    return ids


def finish_manual_fsaf_measurement(
    client: RewClient,
    previous_ids: set[str],
    level_dbfs: float = FSAF_LEVEL_DBFS,
    driver_name: str = "DEBUG",
    group_name: str | None = None,
    right_window_ms: float = FSAF_RIGHT_WINDOW_MS,
) -> FsafResult:
    """Find the manually-created FSAF measurement, window it, rename it, and group it."""
    test_name = build_fsaf_test_name(right_window_ms)
    metadata = TestMetadata(
        test_name=test_name,
        driver_name=driver_name,
        group_name=group_name or test_name,
    )
    measurement_id = _find_new_measurement(client, previous_ids)
    set_ir_windows(client, measurement_id, right_window_ms=right_window_ms)
    title = rename_and_group_fsaf_measurement(client, measurement_id, metadata, level_dbfs, right_window_ms)
    return FsafResult(
        measurement_id=measurement_id,
        title=title,
        group_name=metadata.group_name,
        level_dbfs=level_dbfs,
        left_window_ms=FSAF_LEFT_WINDOW_MS,
        right_window_ms=right_window_ms,
    )


def build_fsaf_test_name(right_window_ms: float = FSAF_RIGHT_WINDOW_MS) -> str:
    return f"FSAF IR {FSAF_LEFT_WINDOW_MS:g}msL {right_window_ms:g}msR"


def _configure_fsaf(client: RewClient, level_dbfs: float, metadata: TestMetadata) -> None:
    _debug_response("/audio/java/output-channel", client.post("/audio/java/output-channel", {"channel": MAIN_OUTPUT_CHANNEL}))
    _debug_response("/measure/playback-mode", client.post("/measure/playback-mode", "From REW"))
    _debug_response("/measure/measurement-mode", client.post("/measure/measurement-mode", "Single"))
    _debug_response("/measure/level", client.post("/measure/level", {"value": level_dbfs, "unit": "dBFS"}))
    _debug_response("/generator/signal", client.post("/generator/signal", {"signal": "fsafnoise"}))
    _debug_response("/measure/notes", client.post("/measure/notes", f"REW automation pending {metadata.test_name}"))
    _debug_response("/generator/signal readback", client.get("/generator/signal"))
    _debug_response("/measure/level readback", client.get("/measure/level"))


def set_ir_windows(
    client: RewClient,
    measurement_id: str,
    right_window_ms: float = FSAF_RIGHT_WINDOW_MS,
) -> None:
    current = client.get(f"/measurements/{measurement_id}/ir-windows")
    _debug_response(f"/measurements/{measurement_id}/ir-windows current", current)
    body = {
        "leftWindowWidthms": FSAF_LEFT_WINDOW_MS,
        "rightWindowWidthms": right_window_ms,
    }
    _debug_response(
        f"/measurements/{measurement_id}/ir-windows PUT",
        client.put(f"/measurements/{measurement_id}/ir-windows", body),
    )
    _debug_response(f"/measurements/{measurement_id}/ir-windows readback", client.get(f"/measurements/{measurement_id}/ir-windows"))


def rename_and_group_fsaf_measurement(
    client: RewClient,
    measurement_id: str,
    metadata: TestMetadata,
    level_dbfs: float,
    right_window_ms: float,
) -> str:
    summary = client.get(f"/measurements/{measurement_id}")
    if not isinstance(summary, dict):
        raise RuntimeError(f"Could not fetch FSAF measurement {measurement_id}: {summary}")

    group = find_or_create_group(
        client,
        metadata.group_name,
        notes=f"REW automation group for {metadata.test_name}",
    )
    title = build_fsaf_title(metadata, level_dbfs)
    existing_notes = str(summary.get("notes", "") or "")
    notes = build_fsaf_notes(metadata, existing_notes, level_dbfs, right_window_ms)
    body = {"title": title, "notes": notes, "groupID": group.get("uuid")}
    _debug_response(f"/measurements/{measurement_id} PUT", client.put(f"/measurements/{measurement_id}", body))
    _debug_response(f"/measurements/{measurement_id} readback", client.get(f"/measurements/{measurement_id}"))
    return title


def build_fsaf_title(metadata: TestMetadata, level_dbfs: float) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    return f"{metadata.driver_name} {metadata.test_name} {level_dbfs:g} dBFS {stamp}"


def build_fsaf_notes(
    metadata: TestMetadata,
    existing_notes: str,
    level_dbfs: float,
    right_window_ms: float,
) -> str:
    notes = [
        "REW automation FSAF measurement",
        f"Driver: {metadata.driver_name}",
        f"Test: {metadata.test_name}",
        f"Group: {metadata.group_name}",
        f"Measurement level: {level_dbfs:g} dBFS",
        f"Output channel: {MAIN_OUTPUT_CHANNEL}",
        f"IR left window: {FSAF_LEFT_WINDOW_MS:g} ms before reference",
        f"IR right window: {right_window_ms:g} ms after reference",
    ]
    if existing_notes:
        notes.extend(["", existing_notes])
    return "\n".join(notes)


def _wait_for_new_measurement(client: RewClient, previous_ids: set[str]) -> str:
    deadline = time.monotonic() + FSAF_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        current_ids = _measurement_ids(client)
        new_ids = current_ids - previous_ids
        if new_ids:
            selected_uuid = _optional_get(client, "/measurements/selected-uuid")
            if isinstance(selected_uuid, str) and selected_uuid in new_ids:
                write_debug(f"New selected FSAF measurement: {selected_uuid}")
                return selected_uuid
            measurement_id = sorted(new_ids)[-1]
            write_debug(f"New FSAF measurement: {measurement_id}")
            return measurement_id
        time.sleep(0.5)
    raise TimeoutError("Timed out waiting for REW to create the FSAF measurement.")


def _find_new_measurement(client: RewClient, previous_ids: set[str]) -> str:
    current_ids = _measurement_ids(client)
    new_ids = current_ids - previous_ids
    if new_ids:
        selected_uuid = _optional_get(client, "/measurements/selected-uuid")
        if isinstance(selected_uuid, str) and selected_uuid in new_ids:
            write_debug(f"Manual FSAF selected new measurement: {selected_uuid}")
            return selected_uuid
        measurement_id = sorted(new_ids)[-1]
        write_debug(f"Manual FSAF found new measurement: {measurement_id}")
        return measurement_id

    selected_uuid = _optional_get(client, "/measurements/selected-uuid")
    if isinstance(selected_uuid, str) and selected_uuid not in previous_ids:
        write_debug(f"Manual FSAF using selected measurement: {selected_uuid}")
        return selected_uuid
    raise RuntimeError("No new FSAF measurement was found. Run the measurement in REW, then click Continue.")


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


def _debug_response(label: str, data: Any) -> None:
    write_debug(f"{label}: {data}")
