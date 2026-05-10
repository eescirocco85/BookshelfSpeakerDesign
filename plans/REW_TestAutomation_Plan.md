# REW TestAutomation Python Plan

## Goal

Automate the midrange driver comparison tests from `TestAutomation/TestPlanOverview.md` using the Room EQ Wizard API. The script should run the same sequence for each driver, preserve raw REW measurements, export machine-readable results, and produce enough metadata to compare drivers later without guessing how a run was configured.

## API Coverage Summary

The downloaded `REW_API.html` appears to expose the core hooks needed for the planned automation:

| Test area | REW API hooks | Coverage |
| --- | --- | --- |
| REW health/session setup | `/application`, `/application/blocking`, `/application/logging`, `/application/inhibit-graph-updates`, `/application/errors`, `/application/warnings` | Enough for startup checks, blocking command behavior, logging, and error polling. |
| Audio device setup | `/audio/driver`, `/audio/configuration`, `/audio/java/*`, `/audio/input-cal`, `/audio/output-cal`, `/audio/samplerate` | Enough to inspect/set the Java driver, device, channels, sample rate, and cal files. Exact Scarlett interface device/input/output names must be discovered while the interface is plugged in. |
| Input safety and gain checks | `/input-levels/command`, `/input-levels/last-levels`, `/input-levels/units`, `/measure/protection-options`, `/generator/protection` | Enough to monitor clipping/RMS and stop on excessive SPL or input clipping. |
| Level matching | `/generator/signal`, `/generator/signals`, `/generator/signal/configuration`, `/generator/level`, `/generator/commands`, `/spl-meter/{id}/levels`, `/rta/levels`, `/rta/captured-data` | Enough to play band-limited noise and read level while the physical volume knob is adjusted manually. This should run before each driver/test block to verify the signal chain after every driver change. |
| Frequency response sweep | `/measure/sweep/configuration`, `/measure/level`, `/measure/command`, `/measurements`, `/measurements/:id/frequency-response`, `/measurements/:id/ir-windows`, `/measurements/:id/command` | Enough to run SPL sweeps, name/annotate results, retrieve response data, set windows, and save measurements. Automated sweep control requires REW Pro per the API doc. |
| THD sweep | `/measure/command`, `/measurements/:id/distortion`, `/measurements/distortion-units`, `/measurements/distortion-ppo-choices`; also `/stepped-measurement/*` | Enough for sweep distortion extraction. Stepped measurement can also run THD vs frequency with progress/results endpoints. |
| Two-tone IMD | `/stepped-measurement/types`, `/stepped-measurement/type`, `/stepped-measurement/command`, `/stepped-measurement/results/subscribe`, `/rta/distortion` | Likely enough. The doc mentions `imdStimulus` for stepped measurement start parameters, but we need to query the installed REW instance for exact IMD type names and accepted stimulus format. |
| Dense multitone | `/generator/signals`, `/generator/signal/configuration`, `/rta/captured-data`, `/rta/distortion`, `/rta/command` | Probably enough if the installed REW generator exposes a multitone signal and configurable frequency bounds/spacing. This must be runtime-discovered. |
| Compression/headroom | `/generator/level`, `/spl-meter/{id}/levels`, `/rta/levels`, `/rta/captured-data`, `/stepped-measurement/level-span` | Enough to automate stepped REW output levels up to a maximum of `-10 dBFS` and compare acoustic output vs requested level. Physical amp gain remains the manual safety limiter. |
| Persistence/export | `/measurements/command` Save all, `/measurements/:id/command` Save, `/measurements/:id/frequency-response`, `/measurements/:id/distortion`, `/rta/captured-data` | Enough to save `.mdat`, decoded JSON, CSV summaries, and generated plots. |

## Key Caveats

1. Automated sweep measurements through the API require a REW Pro upgrade license; assume the test setup will have REW Pro available.
2. The API can set REW generator and measurement levels, but the physical volume knob will be adjusted manually during pink-noise level calibration.
3. Generator signal details are discoverable at runtime. The script should query `/generator/signals` and `/generator/signals/{signalname}/configuration` before assuming names like pink noise, dual tone, or multitone.
4. Two-tone IMD and dense multitone should be prototyped against live REW first because the static HTML confirms the API category but not every installed signal/config option.
5. Hardware safety belongs in the first implementation: conservative start levels, a `-10 dBFS` maximum REW output level for the loudest tests, input clipping checks, unexpectedly-low-level checks, and a stop command on any exception.

## Proposed Script Structure

Use a small Python package under `TestAutomation`:

```text
TestAutomation/
  rew_automation/
    __init__.py
    client.py          # HTTP wrapper, typed helpers, retries, blocking mode
    session.py         # REW startup checks, audio setup, safety setup
    discovery.py       # live API capability discovery and validation
    tests.py           # individual test routines
    data.py            # decode REW float arrays, save JSON/CSV summaries
    config.py          # load driver/test YAML or JSON config
    gui.py             # simple step/status GUI for manual and automated modes
    cli.py             # command-line entry point
  configs/
    midrange_driver_test.yaml
  output/
    .gitkeep
```

## Implementation Phases

### Phase 1: API Client and Discovery

- Build a `RewClient` around `requests` with `get`, `post`, `put`, `delete`, timeout handling, and clear API error messages.
- Add a `discover` command that queries:
  - `/application`
  - `/application/blocking`
  - `/audio/driver`, `/audio/configuration`, `/audio/samplerate`
  - `/audio/java/input-devices`, `/audio/java/inputs`, `/audio/java/output-devices`, `/audio/java/outputs`
  - `/audio/java/input-channel`, `/audio/java/output-channel`, `/audio/java/format`
  - `/generator/signals`, `/generator/commands`, `/generator/status`
  - `/measure/commands`, `/measure/sweep/configuration`
  - `/stepped-measurement/types`, `/stepped-measurement/commands`
  - `/rta/commands`, `/rta/configuration`
  - `/spl-meter/commands`
- Save discovery output to `TestAutomation/output/capabilities.json`.

### Phase 2: Safety and Level Matching

- Configure API blocking and REW logging.
- Configure measurement and generator levels so the loudest tests never exceed `-10 dBFS` from REW; quieter tests step down from there.
- Start input-level monitoring before playback.
- Select/configure band-limited pink noise for 300 Hz to 2 kHz.
- Start SPL meter or RTA capture and compute stable Leq/RMS over a fixed dwell time.
- Before each driver/test block, pause for pink-noise level calibration so the user can manually adjust the physical volume knob and verify the signal chain after the driver change.
- For the reference driver, store the target level.
- For other drivers, guide the user to adjust the physical volume knob until the measured level is within tolerance.
- Check for unexpectedly low measured level, which likely indicates an unplugged driver, muted channel, wrong input/output, or gain/chain problem.

### Phase 3: Frequency Response and THD Sweep

- For each driver and level:
  - Set measurement name and notes with driver, fixture, mic distance, level, date, and run ID.
  - Configure sweep range, likely 150 Hz to 10 kHz.
  - Set conservative measurement level, capped at `-10 dBFS` for the loudest sweep.
  - Start SPL measurement with `/measure/command`.
  - Poll `/measurements` or selected UUID until the new measurement appears.
  - Pull `/measurements/:id/frequency-response`.
  - Pull `/measurements/:id/distortion` when available.
  - Save individual `.mdat`, decoded JSON, CSV summaries, and plots.

### Phase 4: Two-Tone IMD

- Query stepped measurement types and select the appropriate IMD type.
- For each requested pair:
  - Configure stimulus, level, FFT/options, and settling time.
  - Start stepped measurement with `imdStimulus` parameters.
  - Poll `/stepped-measurement/progress`.
  - Collect `/stepped-measurement/results/subscribe` output if we run a local callback server, or fall back to polling RTA distortion endpoints where possible.
- Export sideband/distortion results per pair.

### Phase 5: Dense Multitone

- Query generator signals and find the installed multitone/noise-like option that can cover the requested bands.
- Configure band ranges:
  - 250 Hz to 2 kHz
  - 300 Hz to 2 kHz
  - 350 Hz to 2 kHz
  - 400 Hz to 2 kHz
- Start generator, start RTA, wait for averaging, then capture:
  - `/rta/captured-data`
  - `/rta/captured-peak-data`
  - `/rta/distortion` if meaningful for this mode
- Export spectra, CSV summaries, plots, and simple metrics for between-tone energy/hash.

### Phase 6: Compression and Headroom

- Use band-limited noise or stepped level measurement.
- Run short dwells at increasing REW output levels, never exceeding `-10 dBFS`.
- At each step, capture SPL/RTA levels and input peak.
- Stop if clipping is detected, distortion rises abruptly, or measured level is much lower than expected.
- Compute requested level delta vs measured acoustic delta.

### Phase 7: GUI and Run Modes

- Build a simple Python GUI that shows:
  - current driver and mounted-driver checklist
  - current test phase and step status
  - REW output level, measured level, tolerance, and pass/attention state
  - warnings for low signal, clipping, or missing REW/API/device setup
  - output folder and saved artifacts
- Support two run modes:
  - Manual step mode: pause before each test or calibration step and wait for the user to continue.
  - Automated mode: advance through each configured step automatically, pausing only for required manual actions like driver changes and physical volume-knob calibration.
- Keep the same backend test runner for both modes so the GUI is a controller/view, not a separate implementation.

## Data Model

Each test run should write a manifest like:

```json
{
  "run_id": "2026-05-09_midrange_fixture_v1",
  "driver": "Vifa NE123W-08",
  "fixture": "2 ft x 2 ft baffle, sealed/stuffed rear chamber",
  "mic_distance_ft": 2,
  "rew_base_url": "http://localhost:4735",
  "audio": {
    "driver": "Java",
    "interface": "Focusrite Scarlett, exact REW Java device name TBD",
    "sample_rate": 48000,
    "bit_depth": "TBD from REW Java format discovery",
    "input_channel": 1,
    "output_channel": "L"
  },
  "level_match": {
    "band_hz": [300, 2000],
    "target_leq_db_spl": 99.3,
    "tolerance_db": 0.25,
    "method": "manual physical volume knob during pink-noise calibration"
  },
  "rew_levels": {
    "max_output_dbfs": -10,
    "lower_test_levels_dbfs": [-16, -22]
  },
  "run_mode": "manual_step or automated",
  "exports": {
    "mdat": true,
    "json": true,
    "csv": true,
    "plots": true
  },
  "measurements": []
}
```

## Decisions and Open Questions Before Coding the Full Runner

1. REW Pro: assume available for automated sweep measurement.
2. Audio driver path: use REW's Java audio driver, not ASIO.
3. Audio interface: use the Scarlett interface. Before locking the runner config, ask for the Scarlett to be plugged in and run discovery to capture the exact REW Java input device, output device, input, output, channel names, sample rate, and bit depth settings.
4. Level matching: play 300 Hz to 2 kHz pink noise and pause for manual adjustment of the physical volume knob. Repeat this before each driver/test block so the signal chain is checked after every driver change.
5. REW output levels: use `-10 dBFS` as the maximum/loudest test level and step down from there for quieter tests.
6. SPL safety limits: do not require explicit SPL-limit aborts because the physical volume knob limits maximum acoustic output; do include checks for unexpectedly low signal and input clipping.
7. Output formats: save `.mdat`, decoded JSON, CSV summaries, and plots.
8. Runner mode: support both full automated mode and manual step mode.
9. User interface: build a simple Python GUI that displays progress and pauses/continues according to the selected run mode.

## Recommended Next Step

Start with Phase 1 and a small live smoke test against REW:

1. Launch REW with API enabled.
2. Run discovery.
3. Confirm available generator signals and stepped measurement types.
4. Use those live names to lock down the exact config schema for level matching, two-tone IMD, and dense multitone.

## Live Discovery Results

Discovery was run against REW at `http://127.0.0.1:4735` and saved to `TestAutomation/output/rew_capabilities.json`.

Confirmed generator signals:

- `dualtone`, `tripletone`, `quadtone`
- `multitone`
- `pinknoise`, `whitenoise`, `pinkpn`, `whitepn`
- `linearsweep`, `logsweep`, `meassweep`
- `fsafnoise`

Confirmed stepped measurement types:

- `THD vs frequency`
- `THD vs level`
- `THD vs frequency & level`
- `IMD vs level`
- `Multitone TD+N vs level`

Confirmed useful configuration fields:

- Band-limited noise: `pinknoise` supports `customLowCut`, `customLowCutFreq`, `customHighCut`, `customHighCutFreq`, and `customFilterOrder`.
- Two-tone IMD: `dualtone` supports `customF1`, `customF2`, `customRatio`, `type`, `addDither`, and `ditherBits`.
- Dense multitone: `multitone` supports `startFreq`, `endFreq`, `sequenceLength`, `spectrum`, `spacing`, `linearSpacingHz`, `octaveSpacing`, `decadeSpacing`, `minimiseCrestFactor`, `addDither`, and `ditherBits`.

The next implementation step can now use these concrete signal names and fields rather than guessing from the HTML documentation.
