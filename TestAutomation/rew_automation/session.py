"""Startup and hardware setup checks for REW automation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import RewClient


DEFAULT_INPUT_DEVICE = "Microphone (Scarlett Solo USB)"
DEFAULT_OUTPUT_DEVICE = "Speakers (Scarlett Solo USB)"
MIC_CAL_FILE = Path(r"C:\Users\mclau\OneDrive\Documents\dayton_mic_cal_file_17772.txt")
MIC_INPUT_CHANNEL = 1
REFERENCE_INPUT_CHANNEL = 2
MAIN_OUTPUT_CHANNEL = "L+R"
REFERENCE_OUTPUT_CHANNEL = "R"


class RewDeviceError(RuntimeError):
    """Raised when REW cannot select or verify the requested audio devices."""


@dataclass(frozen=True)
class AudioSetup:
    driver: str
    sample_rate_hz: float | None
    input_device: str
    output_device: str
    input_channel: Any
    reference_input_channel: Any
    output_channel: Any
    reference_output_channel: Any
    mic_cal_file: str
    input_bits: int | None
    output_bits: int | None


def check_rew_connectivity(client: RewClient) -> dict[str, Any]:
    """Return API metadata if REW is reachable."""
    doc = client.get("/doc.json")
    if not isinstance(doc, dict):
        raise RuntimeError("REW responded, but /doc.json was not JSON.")
    return {
        "title": doc.get("info", {}).get("title", "REW API"),
        "version": doc.get("info", {}).get("version", "unknown"),
        "path_count": len(doc.get("paths", {})),
    }


def configure_audio_devices(
    client: RewClient,
    input_device: str = DEFAULT_INPUT_DEVICE,
    output_device: str = DEFAULT_OUTPUT_DEVICE,
) -> AudioSetup:
    """Select the expected Java audio devices and verify REW readback."""
    driver = _field(client.get("/audio/driver"), "driver")
    if driver != "Java":
        raise RewDeviceError(
            f"REW is using the {driver or 'unknown'} audio driver. Select the Java driver in REW, "
            "then retry."
        )

    available_inputs = _values(client.get("/audio/java/input-devices"))
    available_outputs = _values(client.get("/audio/java/output-devices"))

    selected_input = _require_device(input_device, available_inputs, "input")
    selected_output = _require_device(output_device, available_outputs, "output")

    client.post("/audio/java/input-device", {"device": selected_input})
    client.post("/audio/java/output-device", {"device": selected_output})
    client.post("/audio/java/ref-input-channel", {"channel": REFERENCE_INPUT_CHANNEL})
    client.post("/audio/java/input-channel", {"channel": MIC_INPUT_CHANNEL})
    client.post("/audio/java/output-channel", {"channel": MAIN_OUTPUT_CHANNEL})
    client.post("/audio/java/ref-output-channel", {"channel": REFERENCE_OUTPUT_CHANNEL})
    configure_input_calibration(client, MIC_CAL_FILE)

    readback_input = _field(client.get("/audio/java/input-device"), "device")
    readback_output = _field(client.get("/audio/java/output-device"), "device")
    if readback_input != selected_input or readback_output != selected_output:
        raise RewDeviceError(
            "REW did not confirm the selected audio devices.\n\n"
            f"Requested input: {selected_input}\nReadback input: {readback_input}\n"
            f"Requested output: {selected_output}\nReadback output: {readback_output}"
        )

    sample_rate = client.get("/audio/samplerate")
    audio_format = client.get("/audio/java/format")
    return AudioSetup(
        driver=driver,
        sample_rate_hz=_field(sample_rate, "value"),
        input_device=readback_input,
        output_device=readback_output,
        input_channel=_field(client.get("/audio/java/input-channel"), "channel"),
        reference_input_channel=_field(client.get("/audio/java/ref-input-channel"), "channel"),
        output_channel=_field(client.get("/audio/java/output-channel"), "channel"),
        reference_output_channel=_field(client.get("/audio/java/ref-output-channel"), "channel"),
        mic_cal_file=_input_cal_path(client),
        input_bits=_field(audio_format, "inputBits"),
        output_bits=_field(audio_format, "outputBits"),
    )


def configure_input_calibration(client: RewClient, cal_file: Path = MIC_CAL_FILE) -> None:
    if not cal_file.exists():
        raise RewDeviceError(f"Microphone calibration file was not found: {cal_file}")

    current = client.get("/audio/input-cal")
    if not isinstance(current, dict):
        raise RewDeviceError(f"Could not read REW input calibration settings: {current}")

    cal_data = current.get("calDataAllInputs")
    if not isinstance(cal_data, dict):
        cal_data = {}
    cal_data["calFilePath"] = str(cal_file)

    body = {
        "separateCalFileForEachInput": False,
        "inputDeviceIsCWeighted": False,
        "calDataAllInputs": cal_data,
    }
    client.put("/audio/input-cal", body)

    readback = _input_cal_path(client)
    if Path(readback) != cal_file:
        raise RewDeviceError(
            "REW did not confirm the Dayton mic calibration file.\n\n"
            f"Requested: {cal_file}\nReadback: {readback}"
        )


def _require_device(expected: str, available: list[str], role: str) -> str:
    if expected in available:
        return expected
    matches = [device for device in available if expected.lower() in device.lower()]
    if matches:
        return matches[0]
    available_text = "\n".join(f"- {device}" for device in available) or "- none"
    raise RewDeviceError(
        f"Could not find the expected {role} device:\n\n{expected}\n\n"
        f"Devices reported by REW:\n{available_text}"
    )


def _values(data: Any) -> list[str]:
    if isinstance(data, dict) and isinstance(data.get("value"), list):
        return [str(item) for item in data["value"]]
    if isinstance(data, list):
        return [str(item) for item in data]
    return []


def _field(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        return data.get(key)
    return None


def _input_cal_path(client: RewClient) -> str:
    data = client.get("/audio/input-cal")
    if isinstance(data, dict) and isinstance(data.get("calDataAllInputs"), dict):
        return str(data["calDataAllInputs"].get("calFilePath") or "")
    return ""
