"""Quick signal-chain level checks using the REW generator and input monitor."""

from __future__ import annotations

from dataclasses import dataclass
import statistics
import time
from typing import Any, Callable

from .client import RewClient
from .generator_control import GeneratorSetup, start_pink_noise, stop_generator
from .logging_utils import write_debug
from .session import MIC_INPUT_CHANNEL, REFERENCE_INPUT_CHANNEL


WHITE_NOISE_LEVEL_DBFS = -20.0
LEVEL_CHECK_SECONDS = 5.0
BASELINE_SECONDS = 0.8
AFTER_STOP_SECONDS = 0.8
GeneratorStarter = Callable[[RewClient], GeneratorSetup]
BeforeStopHook = Callable[[RewClient], None]


@dataclass(frozen=True)
class ChannelLevel:
    channel: int
    role: str
    rms_dbfs: float | None
    peak_dbfs: float | None


@dataclass(frozen=True)
class LevelCheckResult:
    signal: str
    generator_level_dbfs: float
    output_channel: str
    duration_seconds: float
    unit: str
    baseline_channels: tuple[ChannelLevel, ...]
    channels: tuple[ChannelLevel, ...]
    after_stop_channels: tuple[ChannelLevel, ...]
    baseline_sample_count: int
    sample_count: int
    after_stop_sample_count: int
    rta_rms_spl: float | None = None
    rta_rms_a_weighted_spl: float | None = None
    rta_rms_c_weighted_spl: float | None = None


def run_pink_noise_level_check(
    client: RewClient,
    level_dbfs: float = WHITE_NOISE_LEVEL_DBFS,
    duration_seconds: float = LEVEL_CHECK_SECONDS,
) -> LevelCheckResult:
    """Play band-limited pink noise briefly and return averaged input RMS/peak levels."""
    return run_generator_level_check(
        client,
        lambda rew: start_pink_noise(rew, level_dbfs=level_dbfs),
        duration_seconds=duration_seconds,
    )


def run_generator_level_check(
    client: RewClient,
    starter: GeneratorStarter,
    duration_seconds: float = LEVEL_CHECK_SECONDS,
    before_stop: BeforeStopHook | None = None,
) -> LevelCheckResult:
    """Measure input levels before, during, and after a generator signal."""
    setup: GeneratorSetup | None = None
    baseline_samples: list[dict[str, Any]] = []
    active_samples: list[dict[str, Any]] = []
    after_stop_samples: list[dict[str, Any]] = []
    started_rta_for_level_reading = before_stop is None
    try:
        _debug_response("/input-levels/command Start", client.post("/input-levels/command", {"command": "Start"}))
        _debug("Capturing baseline input levels")
        baseline_samples = _capture_samples(client, BASELINE_SECONDS)

        if started_rta_for_level_reading:
            _start_rta_for_level_reading(client)
        setup = starter(client)
        _debug_state(client, "after play")

        _debug("Capturing active input levels")
        active_samples = _capture_samples(client, duration_seconds)
        rta_levels = _read_rta_levels(client)
    finally:
        if started_rta_for_level_reading:
            _stop_rta_for_level_reading(client)
        if before_stop is not None:
            try:
                before_stop(client)
            except Exception as exc:
                _debug(f"Before-stop hook failed: {exc}")
        _debug("Stopping generator")
        try:
            stop_generator(client)
        except Exception as exc:
            _debug(f"StopGenerator failed: {exc}")
        _debug_state(client, "after play")
        _debug("Capturing after-stop input levels")
        try:
            after_stop_samples = _capture_samples(client, AFTER_STOP_SECONDS)
        finally:
            _try_stop(client, "/input-levels/command")
            _debug_state(client, "after stop")

    if setup is None:
        raise RuntimeError("Generator setup did not complete.")

    result = _summarize(
        baseline_samples=baseline_samples,
        active_samples=active_samples,
        after_stop_samples=after_stop_samples,
        setup=setup,
        duration_seconds=duration_seconds,
        rta_levels=rta_levels if "rta_levels" in locals() else {},
    )
    _debug(
        "Level check captured "
        f"baseline={result.baseline_sample_count}, active={result.sample_count}, "
        f"after_stop={result.after_stop_sample_count} samples"
    )
    for channel in result.channels:
        _debug(
            f"Channel {channel.channel} {channel.role}: "
            f"rms={channel.rms_dbfs}, peak={channel.peak_dbfs}"
        )
    return result


def _capture_samples(client: RewClient, seconds: float) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        levels = client.get("/input-levels/last-levels")
        _debug_response("/input-levels/last-levels", levels)
        if isinstance(levels, dict) and levels.get("rms"):
            samples.append(levels)
        time.sleep(0.2)
    return samples


def _summarize(
    baseline_samples: list[dict[str, Any]],
    active_samples: list[dict[str, Any]],
    after_stop_samples: list[dict[str, Any]],
    setup: GeneratorSetup,
    duration_seconds: float,
    rta_levels: dict[str, Any],
) -> LevelCheckResult:
    all_samples = active_samples or baseline_samples or after_stop_samples
    unit = str(all_samples[-1].get("unit", "dBFS")) if all_samples else "dBFS"
    baseline_channels = _summarize_channels(baseline_samples)
    active_channels = _summarize_channels(active_samples)
    after_stop_channels = _summarize_channels(after_stop_samples)
    return LevelCheckResult(
        signal=setup.description,
        generator_level_dbfs=setup.level_dbfs,
        output_channel=setup.output_channel,
        duration_seconds=duration_seconds,
        unit=unit,
        baseline_channels=baseline_channels,
        channels=active_channels,
        after_stop_channels=after_stop_channels,
        baseline_sample_count=len(baseline_samples),
        sample_count=len(active_samples),
        after_stop_sample_count=len(after_stop_samples),
        rta_rms_spl=_level_value(rta_levels, "rmsLevel"),
        rta_rms_a_weighted_spl=_level_value(rta_levels, "rmsLevelAWeighted"),
        rta_rms_c_weighted_spl=_level_value(rta_levels, "rmsLevelCWeighted"),
    )


def _summarize_channels(samples: list[dict[str, Any]]) -> tuple[ChannelLevel, ...]:
    channels: list[ChannelLevel] = []
    max_channels = max((len(sample.get("rms") or []) for sample in samples), default=0)
    for channel in range(1, max_channels + 1):
        rms_values = _channel_values(samples, "rms", channel)
        peak_values = _channel_values(samples, "peak", channel)
        channels.append(
            ChannelLevel(
                channel=channel,
                role=_channel_role(channel),
                rms_dbfs=_mean(rms_values),
                peak_dbfs=max(peak_values) if peak_values else None,
            )
        )
    return tuple(channels)


def _channel_values(samples: list[dict[str, Any]], key: str, channel: int) -> list[float]:
    index = channel - 1
    values = []
    for sample in samples:
        raw_values = sample.get(key)
        if isinstance(raw_values, list) and len(raw_values) > index:
            values.append(float(raw_values[index]))
    return values


def _read_rta_levels(client: RewClient) -> dict[str, Any]:
    try:
        levels = client.get("/rta/levels")
        _debug_response("/rta/levels", levels)
    except Exception as exc:
        _debug(f"/rta/levels failed: {exc}")
        return {}
    if isinstance(levels, list) and levels and isinstance(levels[0], dict):
        return levels[0]
    return {}


def _start_rta_for_level_reading(client: RewClient) -> None:
    try:
        _debug_response(
            "/rta/configuration PUT",
            client.put(
                "/rta/configuration",
                {
                    "averaging": "Forever",
                    "restartCaptureOnGeneratorChange": True,
                    "stopGeneratorWithRTA": False,
                },
            ),
        )
        _debug_response("/rta/command Reset averaging", client.post("/rta/command", {"command": "Reset averaging"}))
        _debug_response("/rta/command Start", client.post("/rta/command", {"command": "Start"}))
    except Exception as exc:
        _debug(f"Could not start RTA for level reading: {exc}")


def _stop_rta_for_level_reading(client: RewClient) -> None:
    try:
        _debug_response("/rta/command Stop", client.post("/rta/command", {"command": "Stop"}))
    except Exception as exc:
        _debug(f"Could not stop RTA for level reading: {exc}")


def _level_value(levels: dict[str, Any], key: str) -> float | None:
    value = levels.get(key)
    if isinstance(value, dict) and isinstance(value.get("value"), (int, float)):
        return float(value["value"])
    return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return statistics.fmean(values)


def _channel_role(channel: int) -> str:
    if channel == REFERENCE_INPUT_CHANNEL:
        return "Loopback reference"
    if channel == MIC_INPUT_CHANNEL:
        return "Microphone"
    return "Unused"


def _try_stop(client: RewClient, path: str, command: str = "Stop") -> None:
    try:
        _debug_response(f"{path} {command}", client.post(path, {"command": command}))
    except Exception as exc:
        _debug(f"{path} Stop failed: {exc}")


def _debug(message: str) -> None:
    write_debug(message)


def _debug_response(label: str, data: Any) -> None:
    _debug(f"{label}: {data}")


def _debug_state(client: RewClient, label: str) -> None:
    for path in (
        "/generator/status",
        "/generator/signal",
        "/generator/level",
        "/audio/java/output-channel",
        "/audio/java/ref-output-channel",
        "/audio/java/input-channel",
        "/audio/java/ref-input-channel",
    ):
        try:
            _debug_response(f"{label} {path}", client.get(path))
        except Exception as exc:
            _debug(f"{label} {path} failed: {exc}")
