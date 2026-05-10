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
from .fsaf_measurement import FsafResult, finish_manual_fsaf_measurement, snapshot_measurements
from .level_check import LevelCheckResult, run_generator_level_check
from .logging_utils import get_log_path, write_debug
from .rta_capture import RTA_CAPTURE_SECONDS, run_generator_rta_capture
from .session import AudioSetup, RewDeviceError, check_rew_connectivity, configure_audio_devices
from .sweep_measurement import SweepResult, run_frequency_sweep


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
        self.fsaf_snapshot: set[str] | None = None

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
        self.two_tone_button = self._add_signal_button(signal_buttons, "Two-Tone", self.run_two_tone_check)
        self.multitone_button = self._add_signal_button(signal_buttons, "Multitone", self.run_multitone_check)
        self.sweep_button = self._add_signal_button(signal_buttons, "Sweep", self.run_sweep_check)
        self.fsaf_button = self._add_signal_button(signal_buttons, "FSAF", self.run_fsaf_check)

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
                f"{DEFAULT_LEVEL_DBFS:g} dBFS on both output channels for about 3 seconds."
            ),
            starter=lambda rew: start_pink_noise(rew, level_dbfs=DEFAULT_LEVEL_DBFS),
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
            "This will run a sweep measurement from 150 Hz to 6 kHz using REW's measurement "
            "sweep at -20 dBFS with a 1M sweep length, then save it into its REW group.\n\n"
            "REW's API requires the lower frequency first, so this uses 150 Hz to 6 kHz for "
            "the requested 6 kHz to 150 Hz range.\n\n"
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

    def run_fsaf_check(self) -> None:
        try:
            self.fsaf_snapshot = snapshot_measurements(self.client)
        except Exception as exc:
            self._level_check_failed(exc)
            return

        self._set_signal_buttons_enabled(False)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set("Waiting for manual FSAF measurement...")
        self.level_result_var.set("")
        self._show_manual_fsaf_dialog()

    def _show_manual_fsaf_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Manual FSAF Measurement")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("520x260")

        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Run FSAF in REW", font=("Segoe UI", 14, "bold")).pack(anchor=tk.W)
        ttk.Label(
            frame,
            text=(
                "In REW, open the measurement panel, choose the FSAF options you want, "
                "and click Start there.\n\n"
                "When the measurement has appeared in REW, click Continue here. The GUI will "
                "set the IR window to 1 ms left / 5 ms right, rename the measurement, and move "
                "it into the matching group."
            ),
            justify=tk.LEFT,
            wraplength=470,
        ).pack(anchor=tk.W, fill=tk.X, pady=(12, 0))

        buttons = ttk.Frame(frame)
        buttons.pack(side=tk.BOTTOM, fill=tk.X, pady=(18, 0))
        ttk.Button(buttons, text="Cancel", command=lambda: self._cancel_manual_fsaf(dialog)).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Continue", command=lambda: self._continue_manual_fsaf(dialog)).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

    def _cancel_manual_fsaf(self, dialog: tk.Toplevel) -> None:
        dialog.destroy()
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self._set_signal_buttons_enabled(True)

    def _continue_manual_fsaf(self, dialog: tk.Toplevel) -> None:
        dialog.destroy()
        self.status_var.set("Finishing manual FSAF measurement...")
        worker = threading.Thread(target=self._fsaf_worker, daemon=True)
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
            result = run_frequency_sweep(self.client, driver_name=DEFAULT_DRIVER_NAME)
        except Exception as exc:
            self.after(0, self._level_check_failed, exc)
            return
        self.after(0, self._sweep_finished, result)

    def _fsaf_worker(self) -> None:
        try:
            if self.fsaf_snapshot is None:
                raise RuntimeError("No FSAF measurement snapshot was captured.")
            result = finish_manual_fsaf_measurement(
                self.client,
                previous_ids=self.fsaf_snapshot,
                driver_name=DEFAULT_DRIVER_NAME,
            )
        except Exception as exc:
            self.after(0, self._level_check_failed, exc)
            return
        self.after(0, self._fsaf_finished, result)

    def _level_check_finished(self, result: LevelCheckResult) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self.level_result_var.set(_format_level_check(result))
        self._set_signal_buttons_enabled(True)

    def _sweep_finished(self, result: SweepResult) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self.level_result_var.set(_format_sweep_result(result))
        self._set_signal_buttons_enabled(True)

    def _fsaf_finished(self, result: FsafResult) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self.level_result_var.set(_format_fsaf_result(result))
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
    return (
        "Sweep measurement saved.\n"
        f"Title: {result.title}\n"
        f"Group: {result.group_name}\n"
        f"Range: {result.start_hz:g} Hz to {result.end_hz:g} Hz\n"
        f"Level: {result.level_dbfs:g} dBFS\n"
        f"Sweep length: {result.sweep_length}\n"
        f"Measurement ID: {result.measurement_id}"
    )


def _format_fsaf_result(result: FsafResult) -> str:
    return (
        "FSAF measurement saved.\n"
        f"Title: {result.title}\n"
        f"Group: {result.group_name}\n"
        f"Level: {result.level_dbfs:g} dBFS\n"
        f"IR window: {result.left_window_ms:g} ms left, {result.right_window_ms:g} ms right\n"
        f"Measurement ID: {result.measurement_id}"
    )


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
