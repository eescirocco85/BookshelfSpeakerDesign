# Repeatable REW Test Procedure

This is the next orchestration layer for the current REW helpers. Startup, audio
setup, mic calibration, and REW connectivity stay in the existing GUI path. This
procedure assumes those are already complete and starts at per-driver pink-noise
level matching.

## Existing Building Blocks

- Pink-noise level check: `run_generator_level_check(...)` with
  `start_pink_noise(...)`.
- Frequency sweep: `run_frequency_sweep(...)`, already accepting
  `right_window_ms`.
- Two-tone RTA capture: `run_generator_rta_capture(...)` with
  `start_two_tone(...)`.
- Multitone RTA capture: `run_generator_rta_capture(...)` with
  `start_multitone(...)`.
- Measurement grouping and naming are already handled by test name and driver
  name.

## Settings File

Use a JSON file such as `test_settings.midrange.json`. JSON is a good fit here
because the test list is fixed data, it is easy to diff, and Python can load it
without another dependency.

Top-level fields:

- `planName`: display name for the GUI.
- `runMode`: `manual_step` for now; later this can become `auto_continue`.
- `driverNamePrompt`: when true, ask for each driver name at the start of a
  driver run.
- `defaults`: shared values used when an individual test does not override them.
- `levelMatch`: the required pink-noise setup before the test list begins.
- `tests`: ordered list of configured tests to run for each driver.

Supported test types:

- `frequency_sweep`: calls `run_frequency_sweep`.
- `two_tone_rta`: calls `start_two_tone` inside `run_generator_rta_capture`.
- `multitone_rta`: calls `start_multitone` inside `run_generator_rta_capture`.

The `irRightWindowMs` setting belongs on each sweep test. That lets the same
driver run include multiple windows if useful, for example a tight 5 ms window
and a wider 8 ms inspection pass.

## Per-Driver Flow

1. Enter driver name.
2. Mount/connect the driver and confirm the physical setup is ready.
3. Start the configured pink-noise level-match signal.
4. Display live or repeated RTA A-weighted SPL reads while the amplifier level
   is adjusted manually.
5. Continue when the measured SPL is within tolerance of `targetSplDb`.
6. Run each configured test in order.
7. Save each REW measurement with driver name, test label, level, and timestamp.
8. Mark the driver complete and prompt for the next driver.

For the first implementation, the level-match step can use repeated short
captures rather than a truly live meter:

1. Play 300 Hz to 2 kHz pink noise at `generatorLevelDbfs`.
2. Read `/rta/levels`.
3. Show measured SPL, target, and delta.
4. Keep generator running while the user adjusts the amplifier.
5. Provide `Recheck`, `Accept`, and `Stop` controls.

## Runner Shape

Add a small runner module later, probably `rew_automation/test_plan.py`, with
three responsibilities:

- Load and validate the JSON settings.
- Convert each JSON test item to a callable backend operation.
- Report structured progress events to the GUI.

Suggested progress event fields:

```json
{
  "driverName": "Vifa NE123W-08",
  "phase": "tests",
  "stepId": "two_tone_300_1000",
  "stepLabel": "Two tone 300 Hz + 1 kHz",
  "stepIndex": 4,
  "stepCount": 8,
  "status": "running",
  "measurementId": null,
  "message": "Capturing 10 second RTA average"
}
```

Statuses should be simple: `pending`, `waiting_for_user`, `running`,
`passed`, `warning`, `failed`, and `skipped`.

## GUI Progress Display

The GUI can grow from the current button panel into a driver-run dashboard:

- Header:
  - loaded plan name
  - current driver name
  - REW/audio ready state
  - output/debug log path
- Driver controls:
  - driver name input
  - `Start driver`, `Pause`, `Abort`, `Next driver`
  - manual setup confirmation checkbox
- Level-match panel:
  - target SPL, measured SPL, and delta
  - tolerance indicator
  - generator level and pink-noise band
  - buttons for `Start pink noise`, `Recheck`, `Accept level`, `Stop`
- Test list:
  - one row per JSON test
  - status icon/text
  - configured level, frequency range or tone pair, capture duration
  - measurement ID/title once saved
  - warning text from REW if a sweep reports application warnings
- Overall progress:
  - per-driver progress bar from completed steps / total steps
  - run log showing the latest action and any warnings

For each driver, the visible sequence should be:

```text
Driver: Vifa NE123W-08

Level match
  Pink noise 300 Hz to 2 kHz, target 100.0 dBA, measured 99.8 dBA, delta -0.2 dB

Tests
  done     Sweep 150 Hz to 8 kHz
  running  Two tone 250 Hz + 1 kHz
  pending  Two tone 300 Hz + 1 kHz
  pending  Multitone 250 Hz to 2 kHz
```

## Measurement Naming

Keep the existing naming pattern:

```text
{driverName} {testName} {levelDbfs} dBFS {timestamp}
```

Groups should continue to be based on test name so that measurements from
different drivers land together for comparison. This is more useful than
grouping by driver when looking across candidates.

## Next Implementation Step

Wire the JSON settings into the GUI without changing the measurement helpers:

1. Add a config loader and small dataclasses for `levelMatch` and test items.
2. Add a plan runner that executes one driver at a time.
3. Replace the individual debug buttons with a test-list view while keeping the
   current buttons available behind a simple "Manual tools" section.
4. Start with manual-step mode only, then add auto-advance after the operator
   flow feels right.
