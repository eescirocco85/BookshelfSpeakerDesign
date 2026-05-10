#!/usr/bin/env python3
"""Discover the live REW API capabilities needed for TestAutomation.

This script is intentionally dependency-free. Run it while REW is open with the
API server enabled, for example:

    python TestAutomation/rew_discover.py --base-url http://127.0.0.1:4735
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_ENDPOINTS = [
    "/application/commands",
    "/application/blocking",
    "/application/logging",
    "/application/inhibit-graph-updates",
    "/application/errors",
    "/application/warnings",
    "/audio/driver",
    "/audio/driver-types",
    "/audio/configuration",
    "/audio/samplerate",
    "/audio/samplerates",
    "/audio/java/input-device",
    "/audio/java/input-devices",
    "/audio/java/input",
    "/audio/java/inputs",
    "/audio/java/input-channel",
    "/audio/java/ref-input-channel",
    "/audio/java/num-input-channels",
    "/audio/java/num-input-device-channels",
    "/audio/java/output-device",
    "/audio/java/output-devices",
    "/audio/java/output",
    "/audio/java/outputs",
    "/audio/java/output-channel",
    "/audio/java/output-channels",
    "/audio/java/num-output-device-channels",
    "/audio/java/format",
    "/audio/java/stereo-only",
    "/audio/input-cal",
    "/audio/output-cal",
    "/input-levels/commands",
    "/input-levels/units",
    "/generator/status",
    "/generator/commands",
    "/generator/signals",
    "/generator/level",
    "/generator/level/units",
    "/generator/frequency",
    "/generator/protection",
    "/measure/commands",
    "/measure/level",
    "/measure/level/units",
    "/measure/sweep/configuration",
    "/measure/sweep/repetitions",
    "/measure/measurement-mode",
    "/measure/measurement-mode/choices",
    "/measure/playback-mode",
    "/measure/playback-mode/choices",
    "/measure/protection-options",
    "/measurements",
    "/measurements/frequency-response/units",
    "/measurements/frequency-response/smoothing-choices",
    "/measurements/distortion-units",
    "/measurements/distortion-ppo-choices",
    "/spl-meter/commands",
    "/spl-meter/modes",
    "/spl-meter/weightings",
    "/spl-meter/filters",
    "/spl-meter/1/configuration",
    "/spl-meter/1/levels",
    "/rta/commands",
    "/rta/status",
    "/rta/configuration",
    "/rta/appearance-configuration",
    "/rta/distortion-configuration",
    "/rta/levels/units",
    "/rta/captured-data/units",
    "/rta/distortion/units",
    "/rta/distortion/relative-units",
    "/stepped-measurement/types",
    "/stepped-measurement/type",
    "/stepped-measurement/commands",
    "/stepped-measurement/frequency-span",
    "/stepped-measurement/level-span",
    "/stepped-measurement/fft-configuration",
    "/stepped-measurement/options",
    "/stepped-measurement/ppo-values",
    "/stepped-measurement/progress",
]


INTERESTING_SCHEMA_NAMES = [
    "NoiseConfiguration",
    "PNConfiguration",
    "PinkPNConfiguration",
    "TwoToneConfiguration",
    "ThreeToneConfiguration",
    "MultitoneConfiguration",
    "MultitoneDetails",
    "MeasSweepConfiguration",
    "RTAConfiguration",
    "RTADistortionConfiguration",
    "SteppedFreqSpan",
    "SteppedLevelSpan",
    "SteppedFFTConfiguration",
    "SteppedOptions",
    "RTADistortion",
]


class RewClient:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str) -> Any:
        url = self.base_url + path
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = response.read()
            if not body:
                return None
            content_type = response.headers.get("Content-Type", "")
            text = body.decode("utf-8", errors="replace")
            if "json" in content_type.lower() or text[:1] in "[{\"tfn-0123456789":
                return json.loads(text)
            return text


def safe_get(client: RewClient, path: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        return {
            "ok": True,
            "status": 200,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "data": client.get(path),
        }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "error": body or exc.reason,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "error": f"{type(exc).__name__}: {exc}",
        }


def signal_config_path(signal_name: str) -> str:
    encoded = urllib.parse.quote(signal_name, safe="")
    return f"/generator/signals/{encoded}/configuration"


def signal_commands_path(signal_name: str) -> str:
    encoded = urllib.parse.quote(signal_name, safe="")
    return f"/generator/signals/{encoded}/commands"


def schema_subset(doc: dict[str, Any]) -> dict[str, Any]:
    definitions = doc.get("definitions", {})
    return {
        name: definitions[name]
        for name in INTERESTING_SCHEMA_NAMES
        if name in definitions
    }


def summarize(capabilities: dict[str, Any]) -> str:
    lines: list[str] = []
    endpoints = capabilities["endpoints"]
    signals = endpoints.get("/generator/signals", {}).get("data") or []
    stepped_types = endpoints.get("/stepped-measurement/types", {}).get("data") or []
    measure_commands = endpoints.get("/measure/commands", {}).get("data") or []
    rta_commands = endpoints.get("/rta/commands", {}).get("data") or []
    audio_driver = endpoints.get("/audio/driver", {}).get("data") or {}
    sample_rate = endpoints.get("/audio/samplerate", {}).get("data") or {}
    input_device = endpoints.get("/audio/java/input-device", {}).get("data") or {}
    output_device = endpoints.get("/audio/java/output-device", {}).get("data") or {}
    input_devices = endpoint_values(endpoints.get("/audio/java/input-devices", {}).get("data"))
    output_devices = endpoint_values(endpoints.get("/audio/java/output-devices", {}).get("data"))

    lines.append("REW discovery complete.")
    lines.append(
        "Audio: "
        f"driver={audio_driver.get('driver', '(unknown)')}, "
        f"sample_rate={sample_rate.get('value', '(unknown)')} {sample_rate.get('unit', '')}".rstrip()
    )
    lines.append(f"Selected input device: {input_device.get('device', '(unknown)')}")
    lines.append(f"Selected output device: {output_device.get('device', '(unknown)')}")
    scarlett_inputs = [device for device in input_devices if "scarlett" in str(device).lower()]
    scarlett_outputs = [device for device in output_devices if "scarlett" in str(device).lower()]
    if scarlett_inputs or scarlett_outputs:
        lines.append(
            "Scarlett devices detected: "
            f"inputs={', '.join(map(str, scarlett_inputs)) or '(none)'}, "
            f"outputs={', '.join(map(str, scarlett_outputs)) or '(none)'}"
        )
    lines.append(f"Generator signals ({len(signals)}): {', '.join(map(str, signals))}")
    lines.append(f"Stepped measurement types ({len(stepped_types)}): {', '.join(map(str, stepped_types))}")
    lines.append(f"Measure commands: {', '.join(map(str, measure_commands))}")
    lines.append(f"RTA commands: {', '.join(map(str, rta_commands))}")

    useful_signals = []
    for signal in signals:
        low = str(signal).lower()
        if any(token in low for token in ("noise", "pink", "tone", "multitone", "multi")):
            useful_signals.append(str(signal))
    lines.append(f"Likely useful generator signals: {', '.join(useful_signals) or '(none found)'}")

    failed = [
        path
        for path, result in endpoints.items()
        if isinstance(result, dict) and not result.get("ok")
    ]
    if failed:
        lines.append(f"Endpoints needing attention ({len(failed)}): {', '.join(failed)}")

    return "\n".join(lines)


def endpoint_values(data: Any) -> list[Any]:
    if isinstance(data, dict) and isinstance(data.get("value"), list):
        return data["value"]
    if isinstance(data, list):
        return data
    return []


def discover(base_url: str, output: Path, timeout: float) -> dict[str, Any]:
    client = RewClient(base_url, timeout=timeout)

    doc_result = safe_get(client, "/doc.json")
    if not doc_result["ok"]:
        raise RuntimeError(f"Could not read /doc.json from {base_url}: {doc_result}")

    doc = doc_result["data"]
    capabilities: dict[str, Any] = {
        "base_url": base_url.rstrip("/"),
        "created_at_unix": time.time(),
        "doc": {
            "swagger": doc.get("swagger"),
            "version": doc.get("info", {}).get("version"),
            "title": doc.get("info", {}).get("title"),
            "basePath": doc.get("basePath"),
            "path_count": len(doc.get("paths", {})),
            "paths": sorted(doc.get("paths", {}).keys()),
            "interesting_definitions": schema_subset(doc),
        },
        "endpoints": {},
        "generator_signal_details": {},
    }

    for endpoint in DEFAULT_ENDPOINTS:
        capabilities["endpoints"][endpoint] = safe_get(client, endpoint)

    signals_result = capabilities["endpoints"].get("/generator/signals", {})
    signals = signals_result.get("data") if signals_result.get("ok") else []
    if isinstance(signals, list):
        for signal in signals:
            name = str(signal)
            capabilities["generator_signal_details"][name] = {
                "configuration": safe_get(client, signal_config_path(name)),
                "commands": safe_get(client, signal_commands_path(name)),
            }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(capabilities, indent=2, sort_keys=True), encoding="utf-8")
    return capabilities


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:4735")
    parser.add_argument(
        "--output",
        default="TestAutomation/output/rew_capabilities.json",
        help="Path for the capabilities JSON file.",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)

    output = Path(args.output)
    try:
        capabilities = discover(args.base_url, output, args.timeout)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(summarize(capabilities))
    print(f"Saved: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
