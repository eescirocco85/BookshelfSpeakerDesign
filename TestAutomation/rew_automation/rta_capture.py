"""RTA capture helpers for generator-driven distortion/IMD checks."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import time
from typing import Any

from .client import RewClient
from .generator_control import GeneratorSetup, stop_generator
from .level_check import LevelCheckResult, run_generator_level_check
from .logging_utils import write_debug
from .rew_groups import find_or_create_group
from .test_metadata import TestMetadata


RTA_CAPTURE_SECONDS = 10.0


def run_generator_rta_capture(
    client: RewClient,
    starter: Any,
    duration_seconds: float = RTA_CAPTURE_SECONDS,
    driver_name: str = "DEBUG",
    group_name: str | None = None,
) -> LevelCheckResult:
    """Run a generator signal while RTA averages, then save current RTA as a measurement."""
    setup: GeneratorSetup | None = None
    metadata: TestMetadata | None = None
    saved_measurement_id: str | None = None

    def start_rta_then_generator(rew: RewClient) -> Any:
        nonlocal setup, metadata
        configure_rta_for_generator_capture(rew)
        _rta_command(rew, "Start")
        setup = starter(rew)
        metadata = TestMetadata(
            test_name=setup.test_name,
            driver_name=driver_name,
            group_name=group_name or setup.test_name,
        )
        return setup

    def save_rta_current(rew: RewClient) -> None:
        nonlocal saved_measurement_id
        saved_measurement_id = stop_and_save_rta_current(rew, setup, metadata)

    result = run_generator_level_check(
        client,
        start_rta_then_generator,
        duration_seconds=duration_seconds,
        before_stop=save_rta_current,
    )
    return replace(result, measurement_id=saved_measurement_id)


def configure_rta_for_generator_capture(client: RewClient) -> None:
    """Configure RTA for long averaging that restarts when generator settings change."""
    config = {
        "averaging": "Forever",
        "restartCaptureOnGeneratorChange": True,
        "stopGeneratorWithRTA": False,
    }
    _debug_response("/rta/configuration PUT", client.put("/rta/configuration", config))
    _debug_response("/rta/configuration readback", client.get("/rta/configuration"))
    _rta_command(client, "Reset averaging")


def stop_and_save_rta_current(
    client: RewClient,
    setup: GeneratorSetup | None = None,
    metadata: TestMetadata | None = None,
) -> str:
    """Stop RTA and save its current capture as a REW measurement."""
    before = _measurement_ids(client)
    try:
        _rta_command(client, "Stop")
        _wait_for_rta_stopped(client)
    except Exception as exc:
        write_debug(f"/rta/command Stop failed: {exc}")
    try:
        stop_generator(client)
        _wait_for_generator_stopped(client)
    except Exception as exc:
        write_debug(f"StopGenerator before RTA save failed: {exc}")
    try:
        _rta_command(client, "Save current")
        measurement_id = _wait_for_new_measurement(client, before)
        rename_rta_measurement(client, measurement_id, setup, metadata)
        return measurement_id
    except Exception as exc:
        write_debug(f"/rta/command Save current failed: {exc}")
        raise


def rename_latest_rta_measurement(
    client: RewClient,
    setup: GeneratorSetup | None,
    metadata: TestMetadata | None,
) -> None:
    selected = client.get("/measurements/selected")
    selected_uuid = _optional_get(client, "/measurements/selected-uuid")
    measurement_id = selected_uuid if isinstance(selected_uuid, str) and selected_uuid else str(selected)
    rename_rta_measurement(client, measurement_id, setup, metadata)


def rename_rta_measurement(
    client: RewClient,
    measurement_id: str,
    setup: GeneratorSetup | None,
    metadata: TestMetadata | None,
) -> None:
    summary = client.get(f"/measurements/{measurement_id}")
    if not isinstance(summary, dict):
        write_debug(f"Could not rename measurement {measurement_id}: summary was {summary}")
        return

    if metadata is None:
        metadata = TestMetadata(
            test_name=setup.test_name if setup else "RTA capture",
            driver_name="DEBUG",
            group_name=setup.test_name if setup else "RTA capture",
        )
    group = find_or_create_group(
        client,
        metadata.group_name,
        notes=f"REW automation group for {metadata.test_name}",
    )

    title = build_measurement_title(setup, metadata)
    existing_notes = str(summary.get("notes", "") or "")
    new_notes = build_measurement_notes(setup, existing_notes, metadata)
    body = {
        "title": title,
        "notes": new_notes,
        "groupID": group.get("uuid"),
    }
    _debug_response(f"/measurements/{measurement_id} PUT", client.put(f"/measurements/{measurement_id}", body))
    _debug_response(f"/measurements/{measurement_id} readback", client.get(f"/measurements/{measurement_id}"))


def build_measurement_title(setup: GeneratorSetup | None, metadata: TestMetadata) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    if setup is None:
        return f"{metadata.driver_name} {stamp}"
    return f"{metadata.driver_name} {setup.level_dbfs:g} dBFS {stamp}"


def build_measurement_notes(
    setup: GeneratorSetup | None,
    existing_notes: str,
    metadata: TestMetadata,
) -> str:
    automation_notes = [
        "REW automation RTA capture",
        f"Driver: {metadata.driver_name}",
        f"Test: {metadata.test_name}",
        f"Group: {metadata.group_name}",
        f"Capture duration: {RTA_CAPTURE_SECONDS:g} seconds",
    ]
    if setup is not None:
        automation_notes.extend(
            [
                f"Signal: {setup.description}",
                f"Generator level: {setup.level_dbfs:g} dBFS",
                f"Output channel: {setup.output_channel}",
            ]
        )
    if existing_notes:
        automation_notes.extend(["", existing_notes])
    return "\n".join(automation_notes)


def _optional_get(client: RewClient, path: str) -> Any:
    try:
        return client.get(path)
    except Exception as exc:
        write_debug(f"{path} failed: {exc}")
        return None


def _wait_for_new_measurement(client: RewClient, previous_ids: set[str], timeout_seconds: float = 8.0) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current_ids = _measurement_ids(client)
        new_ids = current_ids - previous_ids
        if new_ids:
            measurement_id = sorted(new_ids)[-1]
            write_debug(f"New RTA measurement after Save current: {measurement_id}")
            return measurement_id
        time.sleep(0.2)
    current_ids = _measurement_ids(client)
    if len(current_ids) == 1 and not previous_ids:
        measurement_id = next(iter(current_ids))
        write_debug(f"Using only RTA measurement after Save current: {measurement_id}")
        return measurement_id
    raise TimeoutError("Timed out waiting for REW to create the RTA measurement.")


def _measurement_ids(client: RewClient) -> set[str]:
    measurements = client.get("/measurements")
    ids: set[str] = set()
    if isinstance(measurements, dict):
        for key, summary in measurements.items():
            if isinstance(summary, dict) and summary.get("uuid"):
                ids.add(str(summary["uuid"]))
            else:
                ids.add(str(key))
    elif isinstance(measurements, list):
        for index, summary in enumerate(measurements, start=1):
            if isinstance(summary, dict) and summary.get("uuid"):
                ids.add(str(summary["uuid"]))
            else:
                ids.add(str(index))
    write_debug(f"Current measurement IDs: {sorted(ids)}")
    return ids


def _wait_for_rta_stopped(client: RewClient, timeout_seconds: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = _optional_get(client, "/rta/status")
        if isinstance(status, dict) and status.get("running") is False:
            return
        time.sleep(0.1)
    write_debug("Timed out waiting for RTA status to report stopped; continuing")


def _wait_for_generator_stopped(client: RewClient, timeout_seconds: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = _optional_get(client, "/generator/status")
        if isinstance(status, dict) and status.get("playing") is False:
            return
        time.sleep(0.1)
    write_debug("Timed out waiting for generator status to report stopped; continuing")


def _rta_command(client: RewClient, command: str) -> None:
    _debug_response(f"/rta/command {command}", client.post("/rta/command", {"command": command}))
    try:
        _debug_response("/rta/status", client.get("/rta/status"))
    except Exception as exc:
        write_debug(f"/rta/status failed: {exc}")


def _debug_response(label: str, data: Any) -> None:
    write_debug(f"{label}: {data}")
