# REW TestAutomation Python Plan

## Goal

Automate the midrange driver comparison tests from `TestAutomation/TestPlanOverview.md` using the Room EQ Wizard API. The script should run the same sequence for each driver, preserve raw REW measurements, export machine-readable results, and produce enough metadata to compare drivers later without guessing how a run was configured.

## API Coverage Summary

The downloaded `REW_API.html` appears to expose the core hooks needed for the planned automation:

| Test area | REW API hooks | Coverage |
| --- | --- | --- |
| REW health/session setup | `/application`, `/application/blocking`, `/application/logging`, `/application/inhibit-graph-updates`, `/application/errors`, `/application/warnings` | Enough for startup checks, blocking command behavior, logging, and error polling. |
| Audio device setup | `/audio/driver`, `/audio/configuration`, `/audio/java/*`, `/audio/asio/*`, `/audio/input-cal`, `/audio/output-cal`, `/audio/samplerate` | Enough to inspect/set driver, device, channels, sample rate, and cal files. Exact device names must be discovered on the test PC. |
| Input safety and gain checks | `/input-levels/command`, `/input-levels/last-levels`, `/input-levels/units`, `/measure/protection-options`, `/generator/protection` | Enough to monitor clipping/RMS and stop on excessive SPL or input clipping. |
| Level matching | `/generator/signal`, `/generator/signals`, `/generator/signal/configuration`, `/generator/level`, `/generator/commands`, `/spl-meter/{id}/levels`, `/rta/levels`, `/rta/captured-data` | Enough to play band-limited noise and read level. External amp/output adjustment is not automatable unless the amp/interface exposes a separate control API. |
| Frequency response sweep | `/measure/sweep/configuration`, `/measure/level`, `/measure/command`, `/measurements`, `/measurements/:id/frequency-response`, `/measurements/:id/ir-windows`, `/measurements/:id/command` | Enough to run SPL sweeps, name/annotate results, retrieve response data, set windows, and save measurements. Automated sweep control requires REW Pro per the API doc. |
| THD sweep | `/measure/command`, `/measurements/:id/distortion`, `/measurements/distortion-units`, `/measurements/distortion-ppo-choices`; also `/stepped-measurement/*` | Enough for sweep distortion extraction. Stepped measurement can also run THD vs frequency with progress/results endpoints. |
| Two-tone IMD | `/stepped-measurement/types`, `/stepped-measurement/type`, `/stepped-measurement/command`, `/stepped-measurement/results/subscribe`, `/rta/distortion` | Likely enough. The doc mentions `imdStimulus` for stepped measurement start parameters, but we need to query the installed REW instance for exact IMD type names and accepted stimulus format. |
| Dense multitone | `/generator/signals`, `/generator/signal/configuration`, `/rta/captured-data`, `/rta/distortion`, `/rta/command` | Probably enough if the installed REW generator exposes a multitone signal and configurable frequency bounds/spacing. This must be runtime-discovered. |
| Compression/headroom | `/generator/level`, `/spl-meter/{id}/levels`, `/rta/levels`, `/rta/captured-data`, `/stepped-measurement/level-span` | Enough to automate stepped levels and compare acoustic output vs requested level. Physical amp gain remains external unless controllable. |
| Persistence/export | `/measurements/command` Save all, `/measurements/:id/command` Save, `/measurements/:id/frequency-response`, `/measurements/:id/distortion`, `/rta/captured-data` | Enough to save `.mdat` and export JSON/CSV from Python. |

## Key Caveats

1. Automated sweep measurements through the API require a REW Pro upgrade license.
2. The API can set REW generator and measurement levels, but it cannot turn a physical amplifier knob. Level matching can be guided by the script, or automated only if output level inside REW/interface is the intended control.
3. Generator signal details are discoverable at runtime. The script should query `/generator/signals` and `/generator/signals/{signalname}/configuration` before assuming names like pink noise, dual tone, or multitone.
4. Two-tone IMD and dense multitone should be prototyped against live REW first because the static HTML confirms the API category but not every installed signal/config option.
5. Hardware safety belongs in the first implementation: conservative start levels, clipping checks, SPL limits, and a stop command on any exception.

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
  - `/generator/signals`, `/generator/commands`, `/generator/status`
  - `/measure/commands`, `/measure/sweep/configuration`
  - `/stepped-measurement/types`, `/stepped-measurement/commands`
  - `/rta/commands`, `/rta/configuration`
  - `/spl-meter/commands`
- Save discovery output to `TestAutomation/output/capabilities.json`.

### Phase 2: Safety and Level Matching

- Configure API blocking and REW logging.
- Configure measurement and generator protection thresholds.
- Start input-level monitoring before playback.
- Select/configure band-limited pink noise for 300 Hz to 2 kHz if supported.
- Start SPL meter or RTA capture and compute stable Leq/RMS over a fixed dwell time.
- For the reference driver, store the target level.
- For other drivers, guide the user to adjust amp/interface output until the measured level is within tolerance.

### Phase 3: Frequency Response and THD Sweep

- For each driver and level:
  - Set measurement name and notes with driver, fixture, mic distance, level, date, and run ID.
  - Configure sweep range, likely 150 Hz to 10 kHz.
  - Set conservative measurement level.
  - Start SPL measurement with `/measure/command`.
  - Poll `/measurements` or selected UUID until the new measurement appears.
  - Pull `/measurements/:id/frequency-response`.
  - Pull `/measurements/:id/distortion` when available.
  - Save individual `.mdat` and decoded JSON/CSV.

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
- Export spectra and simple metrics for between-tone energy/hash.

### Phase 6: Compression and Headroom

- Use band-limited noise or stepped level measurement.
- Run short dwells at increasing REW output levels.
- At each step, capture SPL/RTA levels and input peak.
- Stop if SPL, clipping, or distortion threshold is exceeded.
- Compute requested level delta vs measured acoustic delta.

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
    "driver": "ASIO",
    "sample_rate": 48000,
    "input_channel": 1,
    "output_channel": "L"
  },
  "level_match": {
    "band_hz": [300, 2000],
    "target_leq_db_spl": 99.3,
    "tolerance_db": 0.25
  },
  "measurements": []
}
```

## Open Questions Before Coding the Full Runner

1. Are we using REW Pro on the test machine? Automated sweep measurement depends on it.
2. Which audio driver path should be primary: ASIO or Java?
3. Should level matching control REW output level only, or should the script pause and prompt for manual amp gain changes?
4. What SPL limits should the script enforce for driver safety?
5. What output format do you want first: raw JSON only, CSV summaries, plots, or all three?
6. Should the first implementation be interactive per driver, or fully batch-oriented once the driver is mounted?

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
