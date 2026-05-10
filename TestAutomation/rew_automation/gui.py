"""Tkinter GUI for REW startup and hardware checks."""

from __future__ import annotations

import tkinter as tk
import threading
from tkinter import messagebox, ttk
from typing import Callable

from .client import RewClient
from .generator_control import (
    DEFAULT_LEVEL_DBFS,
    start_multitone,
    start_pink_noise,
    start_two_tone,
)
from .level_check import LevelCheckResult, run_generator_level_check
from .logging_utils import get_log_path, write_debug
from .rta_capture import RTA_CAPTURE_SECONDS, run_generator_rta_capture
from .session import (
    AudioSetup,
    RewDeviceError,
    calibrate_input_spl_to_a_weighted_level,
    check_rew_connectivity,
    configure_audio_devices,
)
from .sweep_measurement import (
    DEFAULT_SWEEP_END_HZ,
    DEFAULT_SWEEP_LEVEL_DBFS,
    DEFAULT_SWEEP_START_HZ,
    SweepResult,
    run_frequency_sweep,
)


DEFAULT_DRIVER_NAME = "DEBUG"


class RewAutomationApp(tk.Tk):
    def __init__(self, base_url: str = "http://127.0.0.1:4735") -> None:
        super().__init__()
        self.title("REW Test Automation")
        self.geometry("640x360")
        self.minsize(560, 320)
        self.client = RewClient(base_url=base_url)
        self.log_path = get_log_path()
        write_debug("GUI started")
        self.status_var = tk.StringVar(value="Starting REW checks...")
        self.detail_var = tk.StringVar(value="")
        self.level_result_var = tk.StringVar(value="")
        self.log_var = tk.StringVar(value=f"Debug log: {self.log_path}")
        self.current_signal_starter: Callable[[RewClient], object] | None = None
        self.current_signal_label = ""
        self.current_signal_uses_rta = False
        self.last_pink_a_weighted_spl: float | None = None

        self._build()
        self.after(100, self.startup_checks)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(outer, text="REW Test Automation", font=("Segoe UI", 18, "bold"))
        title.pack(anchor=tk.W)

        ttk.Label(outer, textvariable=self.status_var, font=("Segoe UI", 11)).pack(anchor=tk.W, pady=(18, 6))
        detail = ttk.Label(outer, textvariable=self.detail_var, justify=tk.LEFT, wraplength=560)
        detail.pack(anchor=tk.W, fill=tk.X)

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(24, 0))
        self.progress.start(12)

        signal_buttons = ttk.Frame(outer)
        signal_buttons.pack(anchor=tk.W, pady=(18, 0))
        self.signal_buttons: list[ttk.Button] = []
        self.pink_button = self._add_signal_button(signal_buttons, "Pink Noise", self.run_pink_noise_check)
        self.cal_spl_button = self._add_signal_button(signal_buttons, "Cal SPL to 100 dBA", self.calibrate_spl_to_100)
        self.two_tone_button = self._add_signal_button(signal_buttons, "Two-Tone", self.run_two_tone_check)
        self.multitone_button = self._add_signal_button(signal_buttons, "Multitone", self.run_multitone_check)
        self.sweep_button = self._add_signal_button(signal_buttons, "Sweep", self.run_sweep_check)

        ttk.Label(outer, textvariable=self.level_result_var, justify=tk.LEFT, wraplength=560).pack(
            anchor=tk.W, fill=tk.X, pady=(12, 0)
        )
        ttk.Label(outer, textvariable=self.log_var, justify=tk.LEFT, wraplength=560).pack(
            anchor=tk.W, fill=tk.X, pady=(12, 0)
        )

        buttons = ttk.Frame(outer)
        buttons.pack(side=tk.BOTTOM, fill=tk.X, pady=(24, 0))
        ttk.Button(buttons, text="Quit", command=self.destroy).pack(side=tk.RIGHT)

    def startup_checks(self) -> None:
        if not self._connect_to_rew():
            self.destroy()
            return

        setup = self._configure_audio()
        if setup is None:
            self.destroy()
            return

        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self.detail_var.set(_format_audio_setup(setup))
        self._set_signal_buttons_enabled(True)

    def _add_signal_button(self, parent: ttk.Frame, text: str, command: Callable[[], None]) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        button.pack(side=tk.LEFT, padx=(0, 8))
        button.state(["disabled"])
        self.signal_buttons.append(button)
        return button

    def run_pink_noise_check(self) -> None:
        self._run_signal_check(
            label="pink noise",
            description=(
                "This will play 300 Hz to 2 kHz pink noise through REW at "
                f"{DEFAULT_LEVEL_DBFS:g} dBFS on both output channels for about 5 seconds."
            ),
            starter=lambda rew: start_pink_noise(rew, level_dbfs=DEFAULT_LEVEL_DBFS),
        )

    def calibrate_spl_to_100(self) -> None:
        if self.last_pink_a_weighted_spl is None:
            messagebox.showerror(
                "No pink-noise level",
                "Run the Pink Noise test first so the GUI has a fresh A-weighted RMS level.",
                parent=self,
            )
            return

        should_run = messagebox.askokcancel(
            "Calibrate SPL",
            f"The latest pink-noise RTA RMS A-weighted level was {self.last_pink_a_weighted_spl:.1f} dB SPL.\n\n"
            "Click OK to adjust REW input calibration so this level reports as 100.0 dB SPL A-weighted.",
            parent=self,
        )
        if not should_run:
            return

        try:
            new_cal = calibrate_input_spl_to_a_weighted_level(
                self.client,
                measured_a_weighted_spl=self.last_pink_a_weighted_spl,
                target_spl=100.0,
            )
        except Exception as exc:
            self._level_check_failed(exc)
            return

        write_debug(
            "SPL calibration adjusted from latest pink-noise A-weighted level "
            f"{self.last_pink_a_weighted_spl:.3f} dB SPL to target 100.0 dB SPL; "
            f"dBFSAt94dBSPL={new_cal:.4f}"
        )
        self.level_result_var.set(
            f"SPL calibration updated.\n"
            f"Latest pink-noise A-weighted level: {self.last_pink_a_weighted_spl:.1f} dB SPL\n"
            f"Target level: 100.0 dB SPL\n"
            f"New dBFS at 94 dB SPL: {new_cal:.4f}"
        )

    def run_two_tone_check(self) -> None:
        self._run_signal_check(
            label="two-tone",
            description=(
                "This will play a two-tone signal through REW at "
                f"{DEFAULT_LEVEL_DBFS:g} dBFS, run a {RTA_CAPTURE_SECONDS:g} second RTA average, save current as a "
                "new REW measurement, and stop the generator."
            ),
            starter=lambda rew: start_two_tone(rew, level_dbfs=DEFAULT_LEVEL_DBFS),
            use_rta_capture=True,
        )

    def run_multitone_check(self) -> None:
        self._run_signal_check(
            label="multitone",
            description=(
                "This will play a pink-spectrum multitone with 1/20 decade spacing through REW at "
                f"{DEFAULT_LEVEL_DBFS:g} dBFS, run a {RTA_CAPTURE_SECONDS:g} second RTA average, save current as a "
                "new REW measurement, and stop the generator."
            ),
            starter=lambda rew: start_multitone(rew, level_dbfs=DEFAULT_LEVEL_DBFS),
            use_rta_capture=True,
        )

    def run_sweep_check(self) -> None:
        should_run = messagebox.askokcancel(
            "Run sweep measurement",
            f"This will run a sweep measurement from {DEFAULT_SWEEP_START_HZ:g} Hz "
            f"to {DEFAULT_SWEEP_END_HZ:g} Hz using REW's measurement sweep at "
            f"{DEFAULT_SWEEP_LEVEL_DBFS:g} dBFS with a 1M sweep length, set Loopback timing reference, "
            "apply a 1 ms left / 5 ms right IR window, then save it into its REW group.\n\n"
            "Confirm the amplifier and speaker chain are safe, then click OK.",
            parent=self,
        )
        if not should_run:
            return

        self._set_signal_buttons_enabled(False)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set("Running sweep measurement...")
        self.level_result_var.set("")
        worker = threading.Thread(target=self._sweep_worker, daemon=True)
        worker.start()

    def _run_signal_check(
        self,
        label: str,
        description: str,
        starter: Callable[[RewClient], object],
        use_rta_capture: bool = False,
    ) -> None:
        should_run = messagebox.askokcancel(
            "Run level check",
            f"{description}\n\nConfirm the amplifier and speaker chain are safe, then click OK.",
            parent=self,
        )
        if not should_run:
            return

        self.current_signal_starter = starter
        self.current_signal_label = label
        self.current_signal_uses_rta = use_rta_capture
        self._set_signal_buttons_enabled(False)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set(f"Running {label} level check...")
        self.level_result_var.set("")
        worker = threading.Thread(target=self._level_check_worker, daemon=True)
        worker.start()

    def _level_check_worker(self) -> None:
        try:
            if self.current_signal_starter is None:
                raise RuntimeError("No generator signal was selected.")
            if self.current_signal_uses_rta:
                result = run_generator_rta_capture(
                    self.client,
                    self.current_signal_starter,
                    driver_name=DEFAULT_DRIVER_NAME,
                )
            else:
                result = run_generator_level_check(self.client, self.current_signal_starter)
        except Exception as exc:
            self.after(0, self._level_check_failed, exc)
            return
        self.after(0, self._level_check_finished, result)

    def _sweep_worker(self) -> None:
        try:
            result = run_frequency_sweep(
                self.client,
                start_hz=DEFAULT_SWEEP_START_HZ,
                end_hz=DEFAULT_SWEEP_END_HZ,
                level_dbfs=DEFAULT_SWEEP_LEVEL_DBFS,
                driver_name=DEFAULT_DRIVER_NAME,
            )
        except Exception as exc:
            self.after(0, self._level_check_failed, exc)
            return
        self.after(0, self._sweep_finished, result)

    def _level_check_finished(self, result: LevelCheckResult) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        if self.current_signal_label == "pink noise":
            self.last_pink_a_weighted_spl = result.rta_rms_a_weighted_spl
        self.level_result_var.set(_format_level_check(result))
        self._set_signal_buttons_enabled(True)

    def _sweep_finished(self, result: SweepResult) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self.level_result_var.set(_format_sweep_result(result))
        self._set_signal_buttons_enabled(True)

    def _level_check_failed(self, exc: Exception) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self._set_signal_buttons_enabled(True)
        messagebox.showerror(
            "Level check failed",
            f"The level check could not complete.\n\n{exc}",
            parent=self,
        )

    def _set_signal_buttons_enabled(self, enabled: bool) -> None:
        state = "!disabled" if enabled else "disabled"
        for button in self.signal_buttons:
            button.state([state])
        if enabled and self.last_pink_a_weighted_spl is None:
            self.cal_spl_button.state(["disabled"])

    def _connect_to_rew(self) -> bool:
        while True:
            self.status_var.set("Checking REW API connectivity...")
            self.update_idletasks()
            try:
                write_debug("Checking REW API connectivity")
                info = check_rew_connectivity(self.client)
                write_debug(f"Connected to REW: {info}")
                self.status_var.set("Connected to REW")
                self.detail_var.set(
                    f"{info['title']} version {info['version']} is responding with "
                    f"{info['path_count']} API paths."
                )
                self.update_idletasks()
                return True
            except Exception as exc:
                write_debug(f"REW API connectivity failed: {exc}")
                should_retry = messagebox.askokcancel(
                    "REW API not found",
                    "Could not connect to the REW API.\n\n"
                    "Start REW, make sure your Pro license is active, and enable the API web server. "
                    "Click OK to check again, or Cancel to quit.\n\n"
                    f"Details: {exc}",
                    parent=self,
                )
                if not should_retry:
                    return False

    def _configure_audio(self) -> AudioSetup | None:
        while True:
            self.status_var.set("Configuring REW audio devices...")
            self.detail_var.set("Selecting the Scarlett Solo input and output, then reading back REW state.")
            self.update_idletasks()
            try:
                write_debug("Configuring REW audio devices")
                setup = configure_audio_devices(self.client)
                write_debug(f"Audio setup complete: {setup}")
                return setup
            except RewDeviceError as exc:
                write_debug(f"Audio device setup failed: {exc}")
                should_retry = messagebox.askretrycancel(
                    "Audio device setup failed",
                    f"{exc}\n\n"
                    "Connect the Scarlett Solo, confirm Windows/REW can see it, then click Retry. "
                    "Click Cancel to quit.",
                    parent=self,
                )
                if not should_retry:
                    return None
            except Exception as exc:
                write_debug(f"Unexpected audio device setup failure: {exc}")
                should_retry = messagebox.askretrycancel(
                    "Audio device setup failed",
                    f"Unexpected error while configuring REW audio devices:\n\n{exc}\n\n"
                    "Click Retry to try again, or Cancel to quit.",
                    parent=self,
                )
                if not should_retry:
                    return None


def _format_audio_setup(setup: AudioSetup) -> str:
    return (
        "REW and the audio interface are ready.\n\n"
        f"Driver: {setup.driver}\n"
        f"Sample rate: {setup.sample_rate_hz:g} Hz\n"
        f"Microphone input: {setup.input_device}, channel {setup.input_channel} (L)\n"
        f"Loopback reference input: {setup.input_device}, channel {setup.reference_input_channel} (R)\n"
        f"Main output: {setup.output_device}, channel {setup.output_channel}\n"
        f"Reference output: {setup.output_device}, channel {setup.reference_output_channel}\n"
        f"Mic cal file: {setup.mic_cal_file}\n"
        f"Format: {setup.input_bits}-bit input, {setup.output_bits}-bit output"
    )


def _format_level_check(result: LevelCheckResult) -> str:
    lines = [
        f"Level check: {result.signal} at {result.generator_level_dbfs:g} dBFS "
        f"on output {result.output_channel} for {result.duration_seconds:g} seconds",
        (
            "Samples captured: "
            f"baseline {result.baseline_sample_count}, active {result.sample_count}, "
            f"after stop {result.after_stop_sample_count}"
        ),
    ]
    if not result.channels:
        lines.append("No input level samples were returned by REW.")
    if result.rta_rms_a_weighted_spl is not None:
        lines.append(f"RTA RMS A-weighted: {result.rta_rms_a_weighted_spl:.1f} dB SPL")
    if result.rta_rms_spl is not None:
        lines.append(f"RTA RMS unweighted: {result.rta_rms_spl:.1f} dB SPL")
    if result.rta_rms_c_weighted_spl is not None:
        lines.append(f"RTA RMS C-weighted: {result.rta_rms_c_weighted_spl:.1f} dB SPL")
    for channel in result.channels:
        baseline = _matching_channel(result.baseline_channels, channel.channel)
        after_stop = _matching_channel(result.after_stop_channels, channel.channel)
        baseline_rms = _format_optional(baseline.rms_dbfs if baseline else None, result.unit)
        rms = _format_optional(channel.rms_dbfs, result.unit)
        peak = _format_optional(channel.peak_dbfs, result.unit)
        after_rms = _format_optional(after_stop.rms_dbfs if after_stop else None, result.unit)
        delta = _format_delta(baseline.rms_dbfs if baseline else None, channel.rms_dbfs, result.unit)
        lines.append(
            f"Channel {channel.channel} ({channel.role}): baseline RMS {baseline_rms}, "
            f"active RMS {rms} ({delta}), peak {peak}, after-stop RMS {after_rms}"
        )
    return "\n".join(lines)


def _format_sweep_result(result: SweepResult) -> str:
    text = (
        "Sweep measurement saved.\n"
        f"Title: {result.title}\n"
        f"Group: {result.group_name}\n"
        f"Range: {result.start_hz:g} Hz to {result.end_hz:g} Hz\n"
        f"Level: {result.level_dbfs:g} dBFS\n"
        f"Sweep length: {result.sweep_length}\n"
        f"IR window: {result.left_window_ms:g} ms left, {result.right_window_ms:g} ms right\n"
        f"Measurement ID: {result.measurement_id}"
    )
    if result.warnings:
        text += "\nWarnings:\n" + "\n".join(f"- {warning}" for warning in result.warnings)
    return text


def _format_optional(value: float | None, unit: str) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f} {unit}"


def _format_delta(baseline: float | None, active: float | None, unit: str) -> str:
    if baseline is None or active is None:
        return "delta n/a"
    return f"+{active - baseline:.1f} {unit}" if active >= baseline else f"{active - baseline:.1f} {unit}"


def _matching_channel(channels: tuple[object, ...], channel_number: int) -> object | None:
    for channel in channels:
        if getattr(channel, "channel", None) == channel_number:
            return channel
    return None


def main() -> int:
    app = RewAutomationApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
