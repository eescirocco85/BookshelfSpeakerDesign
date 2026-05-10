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


SWEEP_START_HZ = 150
SWEEP_END_HZ = 6000
SWEEP_LENGTH = "1M"
SWEEP_LEVEL_DBFS = -20.0
SWEEP_TIMEOUT_SECONDS = 45.0


@dataclass(frozen=True)
class SweepResult:
    measurement_id: str
    title: str
    group_name: str
    start_hz: int
    end_hz: int
    level_dbfs: float
    sweep_length: str


def run_frequency_sweep(
    client: RewClient,
    driver_name: str = "DEBUG",
    group_name: str | None = None,
) -> SweepResult:
    """Run a frequency response sweep and assign the saved measurement to a group."""
    metadata = TestMetadata(
        test_name=f"Sweep {SWEEP_START_HZ:g}Hz-{SWEEP_END_HZ:g}Hz",
        driver_name=driver_name,
        group_name=group_name or f"Sweep {SWEEP_START_HZ:g}Hz-{SWEEP_END_HZ:g}Hz",
    )
    before = _measurement_ids(client)
    write_debug(f"Starting frequency sweep with existing measurements: {before}")
    _configure_sweep(client)
    _debug_response("/measure/command SPL", client.post("/measure/command", {"command": "SPL"}))
    measurement_id = _wait_for_new_measurement(client, before)
    title = rename_and_group_sweep_measurement(client, measurement_id, metadata)
    return SweepResult(
        measurement_id=measurement_id,
        title=title,
        group_name=metadata.group_name,
        start_hz=SWEEP_START_HZ,
        end_hz=SWEEP_END_HZ,
        level_dbfs=SWEEP_LEVEL_DBFS,
        sweep_length=SWEEP_LENGTH,
    )


def _configure_sweep(client: RewClient) -> None:
    # REW's measurement sweep API requires the lower frequency first.
    _debug_response("/audio/java/output-channel", client.post("/audio/java/output-channel", {"channel": MAIN_OUTPUT_CHANNEL}))
    _debug_response("/measure/playback-mode", client.post("/measure/playback-mode", "From REW"))
    _debug_response("/measure/measurement-mode", client.post("/measure/measurement-mode", "Single"))
    _debug_response("/measure/sweep/repetitions", client.post("/measure/sweep/repetitions", 1))
    _debug_response("/measure/level", client.post("/measure/level", {"value": SWEEP_LEVEL_DBFS, "unit": "dBFS"}))
    _debug_response(
        "/measure/sweep/configuration",
        client.put(
            "/measure/sweep/configuration",
            {
                "startFrequency": SWEEP_START_HZ,
                "endFrequency": SWEEP_END_HZ,
                "length": SWEEP_LENGTH,
                "fillSilenceWithDither": False,
            },
        ),
    )
    _debug_response("/measure/sweep/configuration readback", client.get("/measure/sweep/configuration"))
    _debug_response("/measure/level readback", client.get("/measure/level"))


def rename_and_group_sweep_measurement(
    client: RewClient,
    measurement_id: str,
    metadata: TestMetadata,
) -> str:
    summary = client.get(f"/measurements/{measurement_id}")
    if not isinstance(summary, dict):
        raise RuntimeError(f"Could not fetch sweep measurement {measurement_id}: {summary}")

    group = find_or_create_group(
        client,
        metadata.group_name,
        notes=f"REW automation group for {metadata.test_name}",
    )
    title = build_sweep_title(metadata)
    existing_notes = str(summary.get("notes", "") or "")
    notes = build_sweep_notes(metadata, existing_notes)
    body = {"title": title, "notes": notes, "groupID": group.get("uuid")}
    _debug_response(f"/measurements/{measurement_id} PUT", client.put(f"/measurements/{measurement_id}", body))
    _debug_response(f"/measurements/{measurement_id} readback", client.get(f"/measurements/{measurement_id}"))
    return title


def build_sweep_title(metadata: TestMetadata) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    return f"{metadata.driver_name} {metadata.test_name} {SWEEP_LEVEL_DBFS:g} dBFS {stamp}"


def build_sweep_notes(metadata: TestMetadata, existing_notes: str) -> str:
    notes = [
        "REW automation sweep measurement",
        f"Driver: {metadata.driver_name}",
        f"Test: {metadata.test_name}",
        f"Group: {metadata.group_name}",
        f"Sweep range: {SWEEP_START_HZ:g} Hz to {SWEEP_END_HZ:g} Hz",
        f"Sweep length: {SWEEP_LENGTH}",
        f"Measurement level: {SWEEP_LEVEL_DBFS:g} dBFS",
        f"Output channel: {MAIN_OUTPUT_CHANNEL}",
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


def _debug_response(label: str, data: Any) -> None:
    write_debug(f"{label}: {data}")
