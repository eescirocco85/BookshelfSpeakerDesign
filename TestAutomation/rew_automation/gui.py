"""Tkinter GUI for REW startup and hardware checks."""

from __future__ import annotations

import tkinter as tk
import threading
from tkinter import messagebox, ttk
from typing import Callable

from .client import RewClient
from .generator_control import (
    start_multitone,
    start_pink_noise,
    start_two_tone,
    stop_generator,
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
    SweepResult,
    run_frequency_sweep,
)
from .test_plan import TestItem, TestPlan, load_test_plan


DEFAULT_DRIVER_NAME = "DEBUG"
CHECK_PENDING = "☐"
CHECK_RUNNING = "..."
CHECK_DONE = "✅"
CHECK_FAILED = "!"
EXPECTED_RTA_SPL_TOLERANCE_DB = 5.0


class RewAutomationApp(tk.Tk):
    def __init__(self, base_url: str = "http://127.0.0.1:4735") -> None:
        super().__init__()
        self.title("REW Test Automation")
        self.geometry("980x760")
        self.minsize(900, 680)
        self.client = RewClient(base_url=base_url)
        self.log_path = get_log_path()
        self.test_plan = load_test_plan()
        self.test_items_by_label = {test.label: test for test in self.test_plan.tests}
        self.test_items_by_id = {test.id: test for test in self.test_plan.tests}
        write_debug("GUI started")
        write_debug(f"Loaded test plan {self.test_plan.plan_name}: {self.test_plan.path}")
        self.status_var = tk.StringVar(value="Starting REW checks...")
        self.detail_var = tk.StringVar(value="")
        self.init_section_title_var = tk.StringVar(value="Initialization details ▲")
        self.init_details_visible = True
        self.level_result_var = tk.StringVar(value="")
        self.warning_var = tk.StringVar(value="")
        self.log_var = tk.StringVar(value=f"Debug log: {self.log_path}")
        self.driver_name_var = tk.StringVar(value=DEFAULT_DRIVER_NAME)
        self.current_signal_starter: Callable[[RewClient], object] | None = None
        self.current_signal_label = ""
        self.current_signal_uses_rta = False
        self.current_signal_duration_seconds = RTA_CAPTURE_SECONDS
        self.current_signal_group_name: str | None = None
        self.current_sweep_item: TestItem | None = None
        self.current_check_key: str | None = None
        self.last_pink_a_weighted_spl: float | None = None
        self.last_completed_measurement_id: str | None = None
        self.check_vars: dict[str, tk.StringVar] = {}
        self.check_labels: dict[str, str] = {}
        self.live_pink_running = False
        self.live_pink_remaining_seconds = self.test_plan.level_match.live_auto_stop_seconds
        self.live_pink_level_var = tk.StringVar(value="")
        self.last_live_peak_dbfs: float | None = None
        self.selected_test_var = tk.StringVar(value=self.test_plan.tests[0].label if self.test_plan.tests else "")
        self.first_driver_var = tk.BooleanVar(value=True)
        self.auto_accept_clean_var = tk.BooleanVar(value=False)
        self.auto_running = False
        self.auto_first_driver = True
        self.auto_step_index = 0

        self._build()
        self.after(100, self.startup_checks)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(outer, text=f"REW Test Automation - {self.test_plan.plan_name}", font=("Segoe UI", 18, "bold"))
        title.pack(anchor=tk.W)

        ttk.Label(outer, textvariable=self.status_var, font=("Segoe UI", 11)).pack(anchor=tk.W, pady=(18, 6))
        init_box = ttk.LabelFrame(outer, padding=(12, 8))
        init_box.pack(fill=tk.X, pady=(10, 0))
        self.init_toggle_button = ttk.Button(
            init_box,
            textvariable=self.init_section_title_var,
            command=self.toggle_init_details,
        )
        self.init_toggle_button.pack(anchor=tk.W)
        self.init_detail_frame = ttk.Frame(init_box)
        self.init_detail_frame.pack(fill=tk.X, pady=(8, 0))
        detail = ttk.Label(self.init_detail_frame, textvariable=self.detail_var, justify=tk.LEFT, wraplength=860)
        detail.pack(anchor=tk.W, fill=tk.X)

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(24, 0))
        self.progress.start(12)

        driver_frame = ttk.Frame(outer)
        driver_frame.pack(fill=tk.X, pady=(16, 0))
        ttk.Label(driver_frame, text="Driver").pack(side=tk.LEFT)
        ttk.Entry(driver_frame, textvariable=self.driver_name_var, width=32).pack(side=tk.LEFT, padx=(8, 8))
        ttk.Checkbutton(driver_frame, text="First driver", variable=self.first_driver_var).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Checkbutton(driver_frame, text="Auto-accept clean", variable=self.auto_accept_clean_var).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        self.start_test_button = ttk.Button(driver_frame, text="Start Test", command=self.start_auto_test)
        self.start_test_button.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(driver_frame, text="Reset checklist", command=self.reset_checklist).pack(side=tk.LEFT)

        checklist = ttk.LabelFrame(outer, text="Driver checklist", padding=(12, 8))
        checklist.pack(fill=tk.X, pady=(16, 0))
        self._add_check_row(checklist, self.test_plan.level_match.id, self.test_plan.level_match.label)
        for test in self.test_plan.tests:
            self._add_check_row(checklist, test.id, test.label)

        signal_buttons = ttk.Frame(outer)
        signal_buttons.pack(anchor=tk.W, pady=(18, 0))
        self.signal_buttons: list[ttk.Button] = []
        self.pink_button = self._add_signal_button(signal_buttons, "Pink Noise", self.run_pink_noise_check)
        self.live_pink_button = self._add_signal_button(signal_buttons, "Pink Noise Live", self.start_live_pink_noise)
        self.cal_spl_button = self._add_signal_button(signal_buttons, "Cal SPL to 100 dBA", self.calibrate_spl_to_100)
        self.two_tone_button = self._add_signal_button(signal_buttons, "Two-Tone", self.run_two_tone_check)
        self.multitone_button = self._add_signal_button(signal_buttons, "Multitone", self.run_multitone_check)
        self.sweep_button = self._add_signal_button(signal_buttons, "Sweep", self.run_sweep_check)

        configured_tests = ttk.Frame(outer)
        configured_tests.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(configured_tests, text="Configured test").pack(side=tk.LEFT)
        self.test_selector = ttk.Combobox(
            configured_tests,
            textvariable=self.selected_test_var,
            values=[test.label for test in self.test_plan.tests],
            state="readonly",
            width=42,
        )
        self.test_selector.pack(side=tk.LEFT, padx=(8, 8))
        self.run_configured_button = self._add_signal_button(
            configured_tests,
            "Run Configured Test",
            self.run_selected_configured_test,
        )

        live_controls = ttk.Frame(outer)
        live_controls.pack(anchor=tk.W, pady=(10, 0))
        self.live_continue_button = ttk.Button(
            live_controls,
            text="Continue pink noise",
            command=self.continue_live_pink_noise,
        )
        self.live_continue_button.pack(side=tk.LEFT, padx=(0, 8))
        self.live_stop_button = ttk.Button(live_controls, text="Stop pink noise", command=self.stop_live_pink_noise)
        self.live_stop_button.pack(side=tk.LEFT)
        self.live_continue_button.state(["disabled"])
        self.live_stop_button.state(["disabled"])
        ttk.Label(outer, textvariable=self.live_pink_level_var, justify=tk.LEFT, wraplength=560).pack(
            anchor=tk.W, fill=tk.X, pady=(8, 0)
        )

        ttk.Label(outer, textvariable=self.level_result_var, justify=tk.LEFT, wraplength=560).pack(
            anchor=tk.W, fill=tk.X, pady=(12, 0)
        )
        tk.Label(outer, textvariable=self.warning_var, justify=tk.LEFT, wraplength=860, fg="red").pack(
            anchor=tk.W, fill=tk.X, pady=(6, 0)
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
        self.set_init_details_visible(False)
        self._set_signal_buttons_enabled(True)

    def _add_signal_button(self, parent: ttk.Frame, text: str, command: Callable[[], None]) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        button.pack(side=tk.LEFT, padx=(0, 8))
        button.state(["disabled"])
        self.signal_buttons.append(button)
        return button

    def toggle_init_details(self) -> None:
        self.set_init_details_visible(not self.init_details_visible)

    def set_init_details_visible(self, visible: bool) -> None:
        self.init_details_visible = visible
        if visible:
            if not self.init_detail_frame.winfo_ismapped():
                self.init_detail_frame.pack(fill=tk.X, pady=(8, 0))
            self.init_section_title_var.set("Initialization details ▲")
        else:
            self.init_detail_frame.pack_forget()
            self.init_section_title_var.set("Initialization details ▼")

    def _add_check_row(self, parent: ttk.LabelFrame, key: str, label: str) -> None:
        var = tk.StringVar(value=f"{CHECK_PENDING} {label}")
        ttk.Label(parent, textvariable=var, font=("Segoe UI", 10)).pack(anchor=tk.W, pady=1)
        self.check_vars[key] = var
        self.check_labels[key] = label

    def start_auto_test(self) -> None:
        if not self.test_plan.tests:
            messagebox.showerror("No tests configured", "The loaded JSON test plan does not contain any tests.", parent=self)
            return
        self.reset_checklist()
        self.auto_running = True
        self.auto_first_driver = bool(self.first_driver_var.get())
        self.auto_step_index = 0
        write_debug(
            f"Starting guided test for driver {self._driver_name()}, "
            f"first_driver={self.auto_first_driver}"
        )
        self._set_signal_buttons_enabled(False)
        if self.auto_first_driver:
            self._auto_run_first_driver_level_match()
        else:
            self._auto_run_live_level_match()

    def _auto_run_first_driver_level_match(self) -> None:
        level_match = self.test_plan.level_match
        self._run_signal_check(
            label="pink noise",
            check_key=level_match.id,
            description="",
            starter=lambda rew: start_pink_noise(
                rew,
                level_dbfs=level_match.generator_level_dbfs,
                low_hz=level_match.low_hz,
                high_hz=level_match.high_hz,
            ),
            duration_seconds=level_match.settle_seconds,
            confirm=False,
        )

    def _auto_run_live_level_match(self) -> None:
        self.start_live_pink_noise(confirm=False)

    def _auto_start_next_test(self) -> None:
        if not self.auto_running:
            return
        if self.auto_step_index >= len(self.test_plan.tests):
            self.auto_running = False
            self.progress.stop()
            self.progress.configure(mode="determinate", value=100)
            self.status_var.set("Guided test complete")
            self._set_signal_buttons_enabled(True)
            messagebox.showinfo("Guided test complete", f"Finished all configured tests for {self._driver_name()}.", parent=self)
            return
        test = self.test_plan.tests[self.auto_step_index]
        self.auto_step_index += 1
        self._run_configured_test(test, confirm=False)

    def _auto_repeat_current_step(self) -> None:
        if not self.auto_running:
            return
        if self.current_check_key == self.test_plan.level_match.id:
            self._set_check_status(self.test_plan.level_match.id, CHECK_PENDING)
            if self.auto_first_driver:
                self._auto_run_first_driver_level_match()
            else:
                self._auto_run_live_level_match()
            return
        if self.auto_step_index > 0:
            self.auto_step_index -= 1
        if self.current_check_key is not None:
            self._set_check_status(self.current_check_key, CHECK_PENDING)
        self._auto_start_next_test()

    def _auto_abort(self) -> None:
        write_debug("Guided test aborted")
        self.auto_running = False
        self.reset_checklist()
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self._set_signal_buttons_enabled(True)

    def _auto_handle_first_driver_level_result(self, result: LevelCheckResult) -> None:
        level_match = self.test_plan.level_match
        measured = result.rta_rms_a_weighted_spl
        measured_text = "n/a" if measured is None else f"{measured:.1f} dBA"
        input_peak = _peak_input_dbfs_from_level_result(result)
        input_peak_text = "n/a" if input_peak is None else f"{input_peak:.1f} dBFS"
        choice = self._ask_yes_no_abort(
            "Use First Driver Level?",
            "Pink-noise level check complete.\n\n"
            f"A-weighted acoustic level: {measured_text}\n"
            f"REW output level: {result.generator_level_dbfs:g} dBFS\n\n"
            f"Input peak level: {input_peak_text}\n\n"
            "The SPL value uses the current REW calibration. REW output dBFS is generator/soundcard output headroom; "
            "input peak dBFS is captured input headroom.\n\n"
            "Yes: use this reading to calibrate SPL and continue.\n"
            "No: repeat the pink-noise level check.\n"
            "Abort: return to the main page and reset the checklist.",
        )
        if choice == "yes":
            if measured is None:
                messagebox.showerror("Missing SPL reading", "REW did not return an A-weighted SPL reading.", parent=self)
                self._auto_repeat_current_step()
                return
            try:
                new_cal = calibrate_input_spl_to_a_weighted_level(
                    self.client,
                    measured_a_weighted_spl=measured,
                    target_spl=level_match.target_spl_db,
                )
            except Exception as exc:
                messagebox.showerror("Calibration failed", f"Could not update REW SPL calibration.\n\n{exc}", parent=self)
                self._auto_abort()
                return
            self.level_result_var.set(
                _format_level_check(result)
                + f"\n\nSPL calibration updated. New dBFS at 94 dB SPL: {new_cal:.4f}"
            )
            self._set_check_status(level_match.id, CHECK_DONE)
            self._auto_start_next_test()
        elif choice == "no":
            self._auto_repeat_current_step()
        else:
            self._auto_abort()

    def _auto_handle_live_level_result(self) -> None:
        level_match = self.test_plan.level_match
        measured = self.last_pink_a_weighted_spl
        measured_text = "n/a" if measured is None else f"{measured:.1f} dBA"
        input_peak_text = "n/a" if self.last_live_peak_dbfs is None else f"{self.last_live_peak_dbfs:.1f} dBFS"
        choice = self._ask_yes_no_abort(
            "Accept Level Match?",
            "Live pink-noise level match stopped.\n\n"
            f"A-weighted acoustic level: {measured_text}\n"
            f"REW output level: {level_match.generator_level_dbfs:g} dBFS\n"
            f"Input peak level: {input_peak_text}\n"
            f"Target: {level_match.target_spl_db:.1f} dBA\n\n"
            "Yes: continue to the configured measurements.\n"
            "No: repeat live pink-noise level matching.\n"
            "Abort: return to the main page and reset the checklist.",
        )
        if choice == "yes":
            self._set_check_status(level_match.id, CHECK_DONE)
            self._auto_start_next_test()
        elif choice == "no":
            self._auto_repeat_current_step()
        else:
            self._auto_abort()

    def _auto_handle_measurement_result(self, measurement_text: str) -> None:
        label = self.check_labels.get(self.current_check_key or "", "Measurement")
        warning_text = self.warning_var.get().strip()
        if self.auto_accept_clean_var.get() and not warning_text and self.current_check_key != self.test_plan.level_match.id:
            write_debug(f"Auto-accepted clean measurement: {label}")
            if self.current_check_key is not None:
                self._set_check_status(self.current_check_key, CHECK_DONE)
            self._auto_start_next_test()
            return
        choice = self._ask_yes_no_abort(
            "Was Measurement Good?",
            f"{label} complete.\n\n{measurement_text}\n\n"
            "Yes: continue to the next measurement.\n"
            "No: repeat this measurement.\n"
            "Abort: return to the main page and reset the checklist.",
        )
        if choice == "yes":
            if self.current_check_key is not None:
                self._set_check_status(self.current_check_key, CHECK_DONE)
            self._auto_start_next_test()
        elif choice == "no":
            self._mark_last_measurement_rejected()
            self._auto_repeat_current_step()
        else:
            self._auto_abort()

    def run_pink_noise_check(self) -> None:
        level_match = self.test_plan.level_match
        self._run_signal_check(
            label="pink noise",
            check_key=level_match.id,
            description=(
                f"This will play {level_match.low_hz:g} Hz to {level_match.high_hz:g} Hz pink noise through REW at "
                f"{level_match.generator_level_dbfs:g} dBFS on both output channels for about "
                f"{level_match.settle_seconds:g} seconds."
            ),
            starter=lambda rew: start_pink_noise(
                rew,
                level_dbfs=level_match.generator_level_dbfs,
                low_hz=level_match.low_hz,
                high_hz=level_match.high_hz,
            ),
            duration_seconds=level_match.settle_seconds,
        )

    def start_live_pink_noise(self, confirm: bool = True) -> None:
        level_match = self.test_plan.level_match
        if confirm:
            should_run = messagebox.askokcancel(
                "Run live pink-noise level match",
                f"This will play {level_match.low_hz:g} Hz to {level_match.high_hz:g} Hz pink noise at "
                f"{level_match.generator_level_dbfs:g} dBFS and keep reporting A-weighted SPL while you adjust "
                f"the physical amplifier level. It will auto-stop after {level_match.live_auto_stop_seconds} seconds "
                f"unless you click Continue pink noise to reset the timer. RTA averaging will use "
                f"{level_match.live_rta_averaging} "
                "so the level display follows amp adjustments.\n\n"
                "Confirm the amplifier and speaker chain are safe, then click OK.",
                parent=self,
            )
            if not should_run:
                return

        self.current_check_key = level_match.id
        self._set_check_status(level_match.id, CHECK_RUNNING)
        self.live_pink_running = True
        self.live_pink_remaining_seconds = level_match.live_auto_stop_seconds
        self.last_pink_a_weighted_spl = None
        self.last_completed_measurement_id = None
        self.last_live_peak_dbfs = None
        self.live_pink_level_var.set("Starting live pink-noise level match...")
        self.level_result_var.set("")
        self.warning_var.set("")
        self._set_signal_buttons_enabled(False)
        self.live_continue_button.state(["!disabled"])
        self.live_stop_button.state(["!disabled"])
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set("Running live pink-noise level match...")

        worker = threading.Thread(target=self._start_live_pink_noise_worker, daemon=True)
        worker.start()

    def _start_live_pink_noise_worker(self) -> None:
        try:
            self.client.put(
                "/rta/configuration",
                {
                    "averaging": self.test_plan.level_match.live_rta_averaging,
                    "restartCaptureOnGeneratorChange": True,
                    "stopGeneratorWithRTA": False,
                },
            )
            self.client.post("/rta/command", {"command": "Reset averaging"})
            self.client.post("/rta/command", {"command": "Start"})
            level_match = self.test_plan.level_match
            start_pink_noise(
                self.client,
                level_dbfs=level_match.generator_level_dbfs,
                low_hz=level_match.low_hz,
                high_hz=level_match.high_hz,
            )
        except Exception as exc:
            self.after(0, self._live_pink_failed, exc)
            return
        self.after(0, self._poll_live_pink_noise)

    def continue_live_pink_noise(self) -> None:
        if not self.live_pink_running:
            return
        self.live_pink_remaining_seconds = self.test_plan.level_match.live_auto_stop_seconds
        self.live_pink_level_var.set(self._format_live_pink_level(self.last_pink_a_weighted_spl))

    def stop_live_pink_noise(self) -> None:
        if not self.live_pink_running:
            return
        self.live_pink_running = False
        worker = threading.Thread(target=self._stop_live_pink_noise_worker, daemon=True)
        worker.start()

    def _stop_live_pink_noise_worker(self) -> None:
        try:
            stop_generator(self.client)
        except Exception as exc:
            write_debug(f"StopGenerator from live pink noise failed: {exc}")
        try:
            self.client.post("/rta/command", {"command": "Stop"})
        except Exception as exc:
            write_debug(f"RTA Stop from live pink noise failed: {exc}")
        self.after(0, self._live_pink_stopped)

    def _poll_live_pink_noise(self) -> None:
        if not self.live_pink_running:
            return
        try:
            levels = self.client.get("/rta/levels")
            a_weighted = _extract_first_rta_level_value(levels, "rmsLevelAWeighted")
            peak = _extract_first_rta_level_value(levels, "peakLevel")
            if a_weighted is not None:
                self.last_pink_a_weighted_spl = a_weighted
            if peak is not None:
                self.last_live_peak_dbfs = peak
            self.live_pink_level_var.set(self._format_live_pink_level(self.last_pink_a_weighted_spl))
        except Exception as exc:
            self._live_pink_failed(exc)
            return

        self.live_pink_remaining_seconds -= 1
        if self.live_pink_remaining_seconds <= 0:
            self.stop_live_pink_noise()
            return
        self.after(1000, self._poll_live_pink_noise)

    def _format_live_pink_level(self, a_weighted_spl: float | None) -> str:
        level = "n/a" if a_weighted_spl is None else f"{a_weighted_spl:.1f} dBA"
        peak = "n/a" if self.last_live_peak_dbfs is None else f"{self.last_live_peak_dbfs:.1f} dBFS input peak"
        delta = ""
        if a_weighted_spl is not None:
            target = self.test_plan.level_match.target_spl_db
            delta = f" | delta from {target:.1f} dBA: {a_weighted_spl - target:+.1f} dB"
        return (
            f"Live pink noise: {level}{delta} | {peak}\n"
            f"Auto-stop in {self.live_pink_remaining_seconds} seconds. Click Continue pink noise to reset."
        )

    def _live_pink_stopped(self) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self.live_continue_button.state(["disabled"])
        self.live_stop_button.state(["disabled"])
        self.live_pink_level_var.set(self._format_live_pink_level(self.last_pink_a_weighted_spl))
        if self.auto_running and self.current_check_key == self.test_plan.level_match.id and not self.auto_first_driver:
            self._auto_handle_live_level_result()
            return
        self._set_signal_buttons_enabled(True)
        if self.current_check_key is not None:
            self._set_check_status(self.current_check_key, CHECK_DONE)

    def _live_pink_failed(self, exc: Exception) -> None:
        self.live_pink_running = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self.live_continue_button.state(["disabled"])
        self.live_stop_button.state(["disabled"])
        self._set_signal_buttons_enabled(True)
        if self.current_check_key is not None:
            self._set_check_status(self.current_check_key, CHECK_FAILED)
        if self.auto_running:
            self.auto_running = False
        messagebox.showerror(
            "Live pink noise failed",
            f"The live pink-noise level match could not complete.\n\n{exc}",
            parent=self,
        )

    def calibrate_spl_to_100(self) -> None:
        if self.last_pink_a_weighted_spl is None:
            messagebox.showerror(
                "No pink-noise level",
                "Run the Pink Noise test first so the GUI has a fresh A-weighted RMS level.",
                parent=self,
            )
            return

        target_spl = self.test_plan.level_match.target_spl_db
        should_run = messagebox.askokcancel(
            "Calibrate SPL",
            f"The latest pink-noise RTA RMS A-weighted level was {self.last_pink_a_weighted_spl:.1f} dB SPL.\n\n"
            f"Click OK to adjust REW input calibration so this level reports as {target_spl:.1f} dB SPL A-weighted.",
            parent=self,
        )
        if not should_run:
            return

        try:
            new_cal = calibrate_input_spl_to_a_weighted_level(
                self.client,
                measured_a_weighted_spl=self.last_pink_a_weighted_spl,
                target_spl=target_spl,
            )
        except Exception as exc:
            self._level_check_failed(exc)
            return

        write_debug(
            "SPL calibration adjusted from latest pink-noise A-weighted level "
            f"{self.last_pink_a_weighted_spl:.3f} dB SPL to target {target_spl:.1f} dB SPL; "
            f"dBFSAt94dBSPL={new_cal:.4f}"
        )
        self.level_result_var.set(
            f"SPL calibration updated.\n"
            f"Latest pink-noise A-weighted level: {self.last_pink_a_weighted_spl:.1f} dB SPL\n"
            f"Target level: {target_spl:.1f} dB SPL\n"
            f"New dBFS at 94 dB SPL: {new_cal:.4f}"
        )

    def run_two_tone_check(self) -> None:
        self._run_first_configured_test("two_tone_rta", "No two-tone tests are configured.")

    def run_multitone_check(self) -> None:
        self._run_first_configured_test("multitone_rta", "No multitone tests are configured.")

    def run_sweep_check(self) -> None:
        test = self.test_plan.first_test_of_type("frequency_sweep")
        if test is None:
            messagebox.showerror("No sweep configured", "No frequency_sweep tests are configured.", parent=self)
            return
        self._run_sweep_test(test)

    def run_selected_configured_test(self) -> None:
        test = self.test_items_by_label.get(self.selected_test_var.get())
        if test is None:
            messagebox.showerror("No configured test", "Select a configured test first.", parent=self)
            return
        self._run_configured_test(test)

    def _run_first_configured_test(self, test_type: str, missing_message: str) -> None:
        test = self.test_plan.first_test_of_type(test_type)
        if test is None:
            messagebox.showerror("No configured test", missing_message, parent=self)
            return
        self._run_configured_test(test)

    def _run_configured_test(self, test: TestItem, confirm: bool = True) -> None:
        if test.type == "frequency_sweep":
            self._run_sweep_test(test, confirm=confirm)
            return
        if test.type == "two_tone_rta":
            self._run_two_tone_test(test, confirm=confirm)
            return
        if test.type == "multitone_rta":
            self._run_multitone_test(test, confirm=confirm)
            return
        messagebox.showerror("Unsupported test type", f"Unsupported test type: {test.type}", parent=self)

    def _run_two_tone_test(self, test: TestItem, confirm: bool = True) -> None:
        if test.f1_hz is None or test.f2_hz is None:
            messagebox.showerror("Invalid two-tone test", f"{test.label} is missing f1Hz or f2Hz.", parent=self)
            return
        self._run_signal_check(
            label=test.label,
            check_key=test.id,
            description=(
                f"This will play {test.f1_hz:g} Hz + {test.f2_hz:g} Hz through REW at "
                f"{test.level_dbfs:g} dBFS, run a {test.capture_seconds:g} second RTA average, save current as a "
                "new REW measurement, and stop the generator."
            ),
            starter=lambda rew: start_two_tone(
                rew,
                level_dbfs=test.level_dbfs,
                f1_hz=test.f1_hz or 0,
                f2_hz=test.f2_hz or 0,
            ),
            use_rta_capture=True,
            duration_seconds=test.capture_seconds,
            group_name=test.label,
            confirm=confirm,
        )

    def _run_multitone_test(self, test: TestItem, confirm: bool = True) -> None:
        if test.start_hz is None or test.end_hz is None:
            messagebox.showerror("Invalid multitone test", f"{test.label} is missing startHz or endHz.", parent=self)
            return
        self._run_signal_check(
            label=test.label,
            check_key=test.id,
            description=(
                f"This will play a multitone from {test.start_hz:g} Hz to {test.end_hz:g} Hz through REW at "
                f"{test.level_dbfs:g} dBFS, run a {test.capture_seconds:g} second RTA average, save current as a "
                "new REW measurement, and stop the generator."
            ),
            starter=lambda rew: start_multitone(
                rew,
                level_dbfs=test.level_dbfs,
                start_hz=test.start_hz or 0,
                end_hz=test.end_hz or 0,
                decade_spacing=test.decade_spacing or "1/20 decade",
            ),
            use_rta_capture=True,
            duration_seconds=test.capture_seconds,
            group_name=test.label,
            confirm=confirm,
        )

    def _run_sweep_test(self, test: TestItem, confirm: bool = True) -> None:
        if test.start_hz is None or test.end_hz is None:
            messagebox.showerror("Invalid sweep test", f"{test.label} is missing startHz or endHz.", parent=self)
            return
        if confirm:
            should_run = messagebox.askokcancel(
                "Run sweep measurement",
                f"This will run {test.label} from {test.start_hz:g} Hz to {test.end_hz:g} Hz using REW's "
                f"measurement sweep at {test.level_dbfs:g} dBFS, apply the configured "
                f"{test.ir_right_window_ms:g} ms right IR window, then save it into its REW group.\n\n"
                "Confirm the amplifier and speaker chain are safe, then click OK.",
                parent=self,
            )
            if not should_run:
                return

        self.current_check_key = test.id
        self.current_sweep_item = test
        self._set_check_status(test.id, CHECK_RUNNING)
        self._set_signal_buttons_enabled(False)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set(f"Running {test.label}...")
        self.level_result_var.set("")
        self.warning_var.set("")
        worker = threading.Thread(target=self._sweep_worker, daemon=True)
        worker.start()

    def _run_signal_check(
        self,
        label: str,
        check_key: str,
        description: str,
        starter: Callable[[RewClient], object],
        use_rta_capture: bool = False,
        duration_seconds: float = RTA_CAPTURE_SECONDS,
        group_name: str | None = None,
        confirm: bool = True,
    ) -> None:
        if confirm:
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
        self.current_signal_duration_seconds = duration_seconds
        self.current_signal_group_name = group_name
        self.current_check_key = check_key
        self._set_check_status(check_key, CHECK_RUNNING)
        self._set_signal_buttons_enabled(False)
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set(f"Running {label} level check...")
        self.level_result_var.set("")
        self.warning_var.set("")
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
                    duration_seconds=self.current_signal_duration_seconds,
                    driver_name=self._driver_name(),
                    group_name=self.current_signal_group_name,
                )
            else:
                result = run_generator_level_check(
                    self.client,
                    self.current_signal_starter,
                    duration_seconds=self.current_signal_duration_seconds,
                )
        except Exception as exc:
            self.after(0, self._level_check_failed, exc)
            return
        self.after(0, self._level_check_finished, result)

    def _sweep_worker(self) -> None:
        try:
            if self.current_sweep_item is None:
                raise RuntimeError("No sweep test was selected.")
            test = self.current_sweep_item
            if test.start_hz is None or test.end_hz is None:
                raise RuntimeError(f"Sweep test {test.label} is missing startHz or endHz.")
            result = run_frequency_sweep(
                self.client,
                start_hz=test.start_hz,
                end_hz=test.end_hz,
                level_dbfs=test.level_dbfs,
                driver_name=self._driver_name(),
                group_name=test.label,
                right_window_ms=test.ir_right_window_ms or 5.0,
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
        self.last_completed_measurement_id = result.measurement_id
        result_text = _format_level_check(result)
        warning_text = self._expected_rta_level_warning(result)
        self.level_result_var.set(result_text)
        self.warning_var.set(warning_text)
        if self.auto_running:
            if self.current_check_key == self.test_plan.level_match.id and self.auto_first_driver:
                self._auto_handle_first_driver_level_result(result)
            else:
                self._auto_handle_measurement_result(_append_warning_text(result_text, warning_text))
            return
        if self.current_check_key is not None:
            self._set_check_status(self.current_check_key, CHECK_DONE)
        self._set_signal_buttons_enabled(True)

    def _sweep_finished(self, result: SweepResult) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self.last_completed_measurement_id = result.measurement_id
        result_text = _format_sweep_result(result)
        warning_text = _format_sweep_warning(result)
        self.level_result_var.set(result_text)
        self.warning_var.set(warning_text)
        if self.auto_running:
            self._auto_handle_measurement_result(_append_warning_text(result_text, warning_text))
            return
        if self.current_check_key is not None:
            self._set_check_status(self.current_check_key, CHECK_DONE)
        self._set_signal_buttons_enabled(True)

    def _level_check_failed(self, exc: Exception) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set("Ready")
        self._set_signal_buttons_enabled(True)
        if self.current_check_key is not None:
            self._set_check_status(self.current_check_key, CHECK_FAILED)
        self.warning_var.set(f"Measurement failed: {exc}")
        if self.auto_running:
            self.auto_running = False
        messagebox.showerror(
            "Level check failed",
            f"The level check could not complete.\n\n{exc}",
            parent=self,
        )

    def _set_signal_buttons_enabled(self, enabled: bool) -> None:
        state = "!disabled" if enabled else "disabled"
        for button in self.signal_buttons:
            button.state([state])
        if hasattr(self, "start_test_button"):
            self.start_test_button.state([state])
        if enabled and self.last_pink_a_weighted_spl is None:
            self.cal_spl_button.state(["disabled"])

    def reset_checklist(self) -> None:
        self.last_pink_a_weighted_spl = None
        self.current_check_key = None
        self.current_sweep_item = None
        self.current_signal_starter = None
        self.current_signal_group_name = None
        self.warning_var.set("")
        for key in self.check_vars:
            self._set_check_status(key, CHECK_PENDING)
        self.cal_spl_button.state(["disabled"])

    def _driver_name(self) -> str:
        name = self.driver_name_var.get().strip()
        return name or DEFAULT_DRIVER_NAME

    def _set_check_status(self, key: str, status: str) -> None:
        var = self.check_vars.get(key)
        if var is not None:
            var.set(f"{status} {self.check_labels.get(key, key)}")

    def _mark_last_measurement_rejected(self) -> None:
        measurement_id = self.last_completed_measurement_id
        if not measurement_id or self.current_check_key == self.test_plan.level_match.id:
            return
        try:
            summary = self.client.get(f"/measurements/{measurement_id}")
            if not isinstance(summary, dict):
                write_debug(f"Could not mark measurement {measurement_id} rejected: summary was {summary}")
                return
            title = str(summary.get("title") or "")
            if title.startswith("REJECTED "):
                return
            body = {
                "title": f"REJECTED {title}" if title else "REJECTED",
                "notes": summary.get("notes", ""),
            }
            if summary.get("groupID") is not None:
                body["groupID"] = summary.get("groupID")
            self.client.put(f"/measurements/{measurement_id}", body)
            write_debug(f"Marked measurement {measurement_id} rejected: {body['title']}")
        except Exception as exc:
            write_debug(f"Could not mark measurement {measurement_id} rejected: {exc}")

    def _expected_rta_level_warning(self, result: LevelCheckResult) -> str:
        if not self.current_signal_uses_rta or self.current_check_key is None:
            return ""
        test = self.test_items_by_id.get(self.current_check_key)
        if test is None:
            return ""
        measured = result.rta_rms_a_weighted_spl
        level_match = self.test_plan.level_match
        expected = level_match.target_spl_db + test.level_dbfs - level_match.generator_level_dbfs
        if measured is None:
            return (
                "WARNING: REW did not return an RTA A-weighted SPL level, so expected level could not be checked."
            )
        delta = measured - expected
        if abs(delta) <= EXPECTED_RTA_SPL_TOLERANCE_DB:
            return ""
        return (
            "WARNING: RTA A-weighted level is outside expected range. "
            f"Expected about {expected:.1f} dBA from {test.level_dbfs:g} dBFS output "
            f"relative to {level_match.generator_level_dbfs:g} dBFS calibration at "
            f"{level_match.target_spl_db:.1f} dBA; measured {measured:.1f} dBA "
            f"({delta:+.1f} dB, tolerance ±{EXPECTED_RTA_SPL_TOLERANCE_DB:.1f} dB)."
        )

    def _ask_yes_no_abort(self, title: str, message: str) -> str:
        result = tk.StringVar(value="abort")
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text=message, justify=tk.LEFT, wraplength=480).pack(anchor=tk.W)

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(18, 0))

        def choose(value: str) -> None:
            result.set(value)
            dialog.destroy()

        ttk.Button(buttons, text="Yes", command=lambda: choose("yes")).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="No", command=lambda: choose("no")).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="Abort", command=lambda: choose("abort")).pack(side=tk.RIGHT)
        dialog.protocol("WM_DELETE_WINDOW", lambda: choose("abort"))
        dialog.update_idletasks()
        x = self.winfo_rootx() + max((self.winfo_width() - dialog.winfo_width()) // 2, 0)
        y = self.winfo_rooty() + max((self.winfo_height() - dialog.winfo_height()) // 2, 0)
        dialog.geometry(f"+{x}+{y}")
        self.wait_window(dialog)
        return result.get()

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
        f"Level check: {result.signal} at REW output {result.generator_level_dbfs:g} dBFS "
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


def _format_sweep_warning(result: SweepResult) -> str:
    if not result.warnings:
        return ""
    return "WARNING: REW sweep warning(s):\n" + "\n".join(f"- {warning}" for warning in result.warnings)


def _append_warning_text(result_text: str, warning_text: str) -> str:
    if not warning_text:
        return result_text
    return f"{result_text}\n\n{warning_text}"


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


def _peak_input_dbfs_from_level_result(result: LevelCheckResult) -> float | None:
    peaks = [
        channel.peak_dbfs
        for channel in result.channels
        if channel.peak_dbfs is not None and getattr(channel, "role", "") == "Microphone"
    ]
    if peaks:
        return max(peaks)
    all_peaks = [channel.peak_dbfs for channel in result.channels if channel.peak_dbfs is not None]
    return max(all_peaks) if all_peaks else None


def _extract_first_rta_level_value(levels: object, key: str) -> float | None:
    if not isinstance(levels, list) or not levels or not isinstance(levels[0], dict):
        return None
    value = levels[0].get(key)
    if isinstance(value, dict) and isinstance(value.get("value"), (int, float)):
        return float(value["value"])
    return None


def main() -> int:
    app = RewAutomationApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
