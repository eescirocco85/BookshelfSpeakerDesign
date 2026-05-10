# REW Automation Measurement Function Calls

This is the current Python measurement surface that grew out of the original
`plans/REW_TestAutomation_Plan.md`. The plan called for live REW discovery,
level checks, RTA-based two-tone/multitone captures, sweep measurements with
THD available from the saved sweep, measurement naming/grouping, and enough
metadata to compare drivers later without guessing how a run was configured.

## Startup

Use the GUI entry point:

```powershell
python TestAutomation\rew_gui.py
```

Startup checks REW API connectivity, selects the Scarlett Solo input/output,
sets mic input channel 1, loopback reference input channel 2, output `L+R`,
sets REW timing/reference channels, and applies the Dayton mic cal file:

```text
C:\Users\mclau\OneDrive\Documents\dayton_mic_cal_file_17772.txt
```

## Generator Level Checks

Shared level-check runner:

```python
run_generator_level_check(client, starter, duration_seconds=5.0)
```

Generator setup functions live in `rew_automation/generator_control.py`:

```python
StartPinkNoise(client, level_dbfs=-20.0, low_hz=300, high_hz=2000)
StartTwoTone(client, level_dbfs=-20.0, f1_hz=700, f2_hz=1900)
StartMultiTone(client, level_dbfs=-20.0, start_hz=300, end_hz=2000, decade_spacing="1/20 decade")
StopGenerator(client)
```

The level-check runner captures baseline input level, active signal level, and
after-stop level so signal detection can be verified. The pink-noise GUI path
also starts a fresh RTA average and reports:

- RTA RMS A-weighted SPL
- RTA RMS unweighted SPL
- RTA RMS C-weighted SPL

Those values come from:

```text
GET /rta/levels
```

## SPL Calibration

After a pink-noise level check, the GUI can adjust REW input calibration so the
most recent A-weighted RTA RMS level reports as a target SPL. The default target
is 100 dB SPL:

```python
calibrate_input_spl_to_a_weighted_level(
    client,
    measured_a_weighted_spl,
    target_spl=100.0,
)
```

The GUI button is `Cal SPL to 100 dBA`. It uses the most recent pink-noise
`rmsLevelAWeighted` value and updates `/audio/input-cal` by changing
`dBFSAt94dBSPL`.

## RTA Captures

Two-tone and multitone use:

```python
run_generator_rta_capture(
    client,
    starter,
    duration_seconds=10.0,
    driver_name="DEBUG",
    group_name=None,
)
```

This configures RTA averaging to `Forever`, enables
`restartCaptureOnGeneratorChange`, starts the generator, captures for 10
seconds, stops RTA, saves current as a new REW measurement, stops the generator,
then renames and groups the saved measurement.

If `group_name` is omitted, the test's own name is used as the REW group name.

## Sweep Measurement

Frequency sweeps use:

```python
run_frequency_sweep(
    client,
    start_hz=150,
    end_hz=6000,
    level_dbfs=-20.0,
    driver_name="DEBUG",
    group_name=None,
    right_window_ms=5.0,
)
```

Current sweep defaults:

- Range: `150 Hz` to `6000 Hz`
- Level: `-20 dBFS`
- Sweep length: `1M`
- Timing reference: `Loopback`
- IR window: `1 ms` left, `right_window_ms` right

Sweep frequency arguments use REW API order: `start_hz` is the lower frequency
and `end_hz` is the higher frequency. THD/distortion data falls out of the saved
sweep measurement in REW.

The sweep function snapshots `/application/warnings` before and after the run
and reports any new warnings such as low level, high distortion, poor SNR, or
timing reference level issues.

REW warnings are available from:

```text
GET /application/warnings
```

## Manual FSAF Note

The original plan listed FSAF/multitone generator discovery. REW exposes
`fsafnoise` as a generator signal, but the measurement API still runs the normal
sweep path and does not expose the measurement-panel FSAF run mode yet. For now,
FSAF is not part of the GUI measurement flow; the sweep path with loopback timing
and controllable IR windows covers the measurement utility needed here.

## Naming And Groups

Saved measurements are named from:

- `driver_name`
- the test's own name
- output level
- timestamp

Saved RTA and sweep measurements are assigned to REW groups by test name unless
an explicit `group_name` is passed. This matches the comparison workflow from
the original plan: measurements from different drivers for the same test land in
the same REW group.
