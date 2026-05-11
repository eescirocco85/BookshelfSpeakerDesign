# Midrange Driver Comparison for 95 dB @ 1 m Testing

## Assumptions
- Half-space radiation
- Piston approximation
- Xmax treated as peak one-way linear excursion
- 95 dB SPL target at 1 meter
- Values intended for relative comparison / sweep planning

| Driver | Sd (cm²) | Xmax (mm) | Vd (cm³) | Sensitivity (dB/W) | Power for 95 dB | Estimated Low Limit @ Xmax | Conservative Sweep Start |
|---|---:|---:|---:|---:|---:|---:|---:|
| Vifa NE123W-08 | 54 | 3.0 | 16.2 | 86.2 | 7.6 W | ~188 Hz | ~250 Hz |
| Dayton RS100-4 | 35 | 4.0 | 14.0 | 84.5 | 11.2 W | ~203 Hz | ~275 Hz |
| Eminence Alpha 3-8 | 28.6 | 2.4 | 6.86 | 88.6 | 4.4 W | ~289 Hz | ~400 Hz |
| Beyma 3FR30 | 30 | 2.0 | 6.00 | 90.5 | 2.8 W | ~309 Hz | ~425 Hz |
| Tang Band W3-2141 | 32 | 1.6 | 5.12 | 87.0 | 6.3 W | ~335 Hz | ~450–500 Hz |
| Dayton RS52 Dome | 26.4 | 1.0 | 2.64 | 89.4 | 3.6 W | ~467 Hz | ~700 Hz |

## Notes
- The "Estimated Low Limit" is where excursion roughly reaches Xmax at 95 dB SPL.
- The "Conservative Sweep Start" adds practical margin for distortion testing.
- Real-world limits may be higher due to:
  - sealed box loading
  - rising distortion before Xmax
  - thermal compression
  - nonlinear suspension behavior
  - music crest factor
- Tiny drivers often sound strained before reaching calculated excursion limits.
- THD/IMD sweeps are likely more revealing than FR alone for determining usable crossover region.