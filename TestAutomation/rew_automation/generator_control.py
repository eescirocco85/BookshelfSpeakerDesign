"""REW generator controls used by the test automation GUI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import RewClient
from .logging_utils import write_debug
from .session import MAIN_OUTPUT_CHANNEL


DEFAULT_LEVEL_DBFS = -20.0
DEFAULT_PINK_LOW_HZ = 300
DEFAULT_PINK_HIGH_HZ = 2000
DEFAULT_TWO_TONE_F1_HZ = 700
DEFAULT_TWO_TONE_F2_HZ = 1900
DEFAULT_MULTITONE_START_HZ = 300
DEFAULT_MULTITONE_END_HZ = 2000
DEFAULT_MULTITONE_DECADE_SPACING = "1/20 decade"


@dataclass(frozen=True)
class GeneratorSetup:
    signal: str
    level_dbfs: float
    output_channel: str
    description: str
    test_name: str


def start_pink_noise(
    client: RewClient,
    level_dbfs: float = DEFAULT_LEVEL_DBFS,
    low_hz: int = DEFAULT_PINK_LOW_HZ,
    high_hz: int = DEFAULT_PINK_HIGH_HZ,
) -> GeneratorSetup:
    """Configure and start band-limited pink noise."""
    _select_output(client)
    _debug_response("/generator/signal", client.post("/generator/signal", {"signal": "pinknoise"}))
    _debug_response(
        "/generator/signal/configuration",
        client.post(
            "/generator/signal/configuration",
            {
                "type": "custom",
                "customLowCut": True,
                "customLowCutFreq": low_hz,
                "customHighCut": True,
                "customHighCutFreq": high_hz,
                "customFilterOrder": 2,
            },
        ),
    )
    _set_level(client, level_dbfs)
    _play(client)
    return GeneratorSetup(
        signal="pinknoise",
        level_dbfs=level_dbfs,
        output_channel=_read_output_channel(client),
        description=f"pink noise {low_hz:g} Hz to {high_hz:g} Hz",
        test_name=f"Pink noise {low_hz:g}Hz-{high_hz:g}Hz",
    )


def start_two_tone(
    client: RewClient,
    level_dbfs: float = DEFAULT_LEVEL_DBFS,
    f1_hz: int = DEFAULT_TWO_TONE_F1_HZ,
    f2_hz: int = DEFAULT_TWO_TONE_F2_HZ,
) -> GeneratorSetup:
    """Configure and start a custom two-tone generator signal."""
    _select_output(client)
    _debug_response("/generator/signal", client.post("/generator/signal", {"signal": "dualtone"}))
    _debug_response(
        "/generator/signal/configuration",
        client.post(
            "/generator/signal/configuration",
            {
                "type": "custom",
                "customF1": f1_hz,
                "customF2": f2_hz,
                "customRatio": 1,
                "addDither": True,
                "ditherBits": 24,
            },
        ),
    )
    _set_level(client, level_dbfs)
    _play(client)
    return GeneratorSetup(
        signal="dualtone",
        level_dbfs=level_dbfs,
        output_channel=_read_output_channel(client),
        description=f"two-tone {f1_hz:g} Hz + {f2_hz:g} Hz",
        test_name=f"Two tone {f1_hz:g}Hz {f2_hz:g}Hz",
    )


def start_multitone(
    client: RewClient,
    level_dbfs: float = DEFAULT_LEVEL_DBFS,
    start_hz: int = DEFAULT_MULTITONE_START_HZ,
    end_hz: int = DEFAULT_MULTITONE_END_HZ,
    decade_spacing: str = DEFAULT_MULTITONE_DECADE_SPACING,
) -> GeneratorSetup:
    """Configure and start a pink-spectrum multitone signal."""
    _select_output(client)
    _debug_response("/generator/signal", client.post("/generator/signal", {"signal": "multitone"}))
    _debug_response(
        "/generator/signal/configuration",
        client.post(
            "/generator/signal/configuration",
            {
                "startFreq": start_hz,
                "endFreq": end_hz,
                "sequenceLength": "64k",
                "spectrum": "Pink",
                "spacing": "Dec",
                "decadeSpacing": decade_spacing,
                "minimiseCrestFactor": True,
                "addDither": True,
                "ditherBits": 24,
            },
        ),
    )
    _set_level(client, level_dbfs)
    _play(client)
    details = _read_multitone_details(client)
    return GeneratorSetup(
        signal="multitone",
        level_dbfs=level_dbfs,
        output_channel=_read_output_channel(client),
        description=f"multitone {start_hz:g} Hz to {end_hz:g} Hz, {decade_spacing}{details}",
        test_name=f"Multitone {start_hz:g}Hz-{end_hz:g}Hz {decade_spacing}",
    )


def stop_generator(client: RewClient) -> None:
    """Stop the currently playing REW generator."""
    _debug_response("/generator/command Stop", client.post("/generator/command", {"command": "Stop"}))


# Aliases matching the requested call names.
StartPinkNoise = start_pink_noise
StartTwoTone = start_two_tone
StartMultiTone = start_multitone
StopGenerator = stop_generator


def _select_output(client: RewClient) -> None:
    write_debug(f"Requesting output channel: {MAIN_OUTPUT_CHANNEL}")
    _debug_response("/audio/java/output-channel", client.post("/audio/java/output-channel", {"channel": MAIN_OUTPUT_CHANNEL}))


def _set_level(client: RewClient, level_dbfs: float) -> None:
    _debug_response("/generator/level", client.post("/generator/level", {"value": level_dbfs, "unit": "dBFS"}))


def _play(client: RewClient) -> None:
    _debug_response("/generator/command Play", client.post("/generator/command", {"command": "Play"}))


def _read_output_channel(client: RewClient) -> str:
    output = client.get("/audio/java/output-channel")
    if isinstance(output, dict):
        return str(output.get("channel", MAIN_OUTPUT_CHANNEL))
    return MAIN_OUTPUT_CHANNEL


def _read_multitone_details(client: RewClient) -> str:
    try:
        details = client.get("/generator/signals/multitone/details")
    except Exception as exc:
        write_debug(f"/generator/signals/multitone/details failed: {exc}")
        return ""
    if not isinstance(details, dict):
        return ""
    tone_count = details.get("numTones")
    first = details.get("firstFreq")
    last = details.get("lastFreq")
    if tone_count is None or first is None or last is None:
        return ""
    return f" ({tone_count} tones, actual {first:.1f} Hz to {last:.1f} Hz)"


def _debug_response(label: str, data: Any) -> None:
    write_debug(f"{label}: {data}")
