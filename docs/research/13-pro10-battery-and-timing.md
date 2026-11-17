# Pro 10: PL1 on battery, AC timing, PL4

Source: a Pro 10 tester (root dry-run on battery, slider 6, plus a Windows 20-minute all-core load on AC and battery) against the
Pro 10 blob (112.0.0.104, 101 lines, `direct`). Everything below was replayed with the engine (`surface-power-replay`, same blob).
Markers as in the README of this folder.

## PL1 on battery: the engine gives 30 W, Windows holds 10.6 W
- **PL1 is the minimum of six inputs** [C]: skin controller (id 1, input 509), slider table, battery-current controller (id 6),
  posture table (30 W), Modern Standby table (100 W in phase 4), and controller 9.
- **Controller 9 is a constant 30 W on battery** [C]. Its input is the battery current smoothed over 5 s (limits ±15 A, discharge
  positive), so the report's "ACTIVE, x ≈ 2.1 A" is right. Its set 0 is chosen on battery (available input power 0) and has
  U_min 30.000 W, U_max 30.012 W: it can only move the output by 12 mW. Set 1 (U_min 8 W, set point -75 mA) is selected only while
  the battery discharges on AC with at least 35 W of available input power and the max-charge flag is 0: a battery assist limiter
  for a weak adapter. Replay: with 3 A discharge on AC, PL1 falls from 30 to 8 W in 2 minutes [C, engine].
- **Controller 6** (battery current, 8.5-25 W, jumps to 25 W when it engages, then about 0.05 W/s down) engages at set point minus
  0.3 A. The set point comes from the battery's sustained power (`28B7BD85/3`) and the EC level (`/13`), minimum of both: 4.2 A
  (level or sustained power low) up to 12.0 A (sustained power above 76.5 W, level 20, as on the tester's machine). 12 A is about 98 W:
  it never engages at a 30 W load [C, engine]. With level 7 it would engage at 3.9 A and reach 8.5 W after about 5 minutes.
- **Slider table: only states 2 and 7 give less than 30 W: 12.8 W and 10.5 W** [C]. Our mapping gives slider 6 (Best performance,
  battery) -> state 6 -> 30 W.
- **Finding:** the engine path is consistent with the tester's numbers (30 / 55 / 109 / 68 / 107.9 W reproduced). Windows' 10.6 W
  matches the state-7 value of the slider table (10.5 W) and nothing else in the blob [C match]. It arrives as a step after about
  3 minutes, stays constant for 16 minutes, and the Windows client logs no controller activity on battery; a controller (6) would
  ramp over minutes and show up in that log. So the likely cause is that Windows moves the system state to 7 after about 3 minutes of
  load on battery, while Linux stays at 6 [H]. What triggers it (an automatic energy saver, a battery level rule, a Surface
  service rule) is open. Controller 6 with a low EC level cannot be excluded without the EC series [H].
- **No input misread found.** Battery current sign and unit (mA, discharge positive), `/8` = 0 on battery and `/16` = 0 are as the
  blob expects; the new block types (smoothing, hysteresis quantiser, multiply/divide, conditional table) behave in replay as
  documented. The smoothing block needs input changes to run while no controller is active, which the live battery current
  provides.

## AC: when does PL1 step down
- Windows values match controller 1 exactly [C match]: Recommended (state 1) 20.5 W = U_on of set 1, cut-in 44.0 C; Best performance
  (states 5/6) 25.5 W = U_on of set 4, cut-in 47.0 C (3201 in 0.1 K), then 23.4 -> 21 W as the controller regulates around 48.0 C
  (set point 3211). The 5.5/6 min log lines are probably READY -> ACTIVE [H].
- Replay with a skin ramp: PL1 steps at the moment input 509 crosses the cut-in, with EC inputs valid or invalid alike (AC and
  charging battery: controller 9 stays on set 0). So the engine does not change the timing; **the difference is the sensor series**:
  Windows reaches the cut-in after about 3.5 min, the earlier Linux run after 11.8 min (31.0 -> 25.5 W at 47.0 C) [M, tester]. The
  Linux run did not write PL1, so its load and fan (4135 rpm at idle probe) differ from a controlled run [H]. Needed: a loaded root
  run with a status time series (509, 505, controller 1 state).

## PL4
- PL4 path on the Pro 10: peak power (`/2`), rest of platform (`/6`), available input power (`/8`) and display brightness. The three
  EC inputs are guarded; brightness is not (its fallback changes PL4 by a few watts only).
- The blob's fallback is 20 W [C, engine]; the daemon never writes it: the output is held while any guarded EC input is invalid
  (start, resume, request failures; tests in `tests/test_daemon.py`).
- Profile option `pl4_requires_ec` (Pro 10): `--control` is refused (dry-run only, reason in the log and `status.json`) unless those
  inputs are valid within `pl4_ec_wait_s` (10 s) of the start. A tester reports that PL4 also limits the iGPU on this model
  (linux-surface issue 2331) [H, not measured here]; the report and the log say so.
- Firmware PL4: 45 W, read by the tester on a boot without his PL4 service (`peak_power` 45000000 µW, MSR `0x601` = 0x168 at
  1/8 W, UEFI 23.104.143) [M]. With EC data the blob sets 109 W on AC and on battery.
- Loaded battery dry-run (root, thermald stopped, slider 6, 15 min) [M, tester]: PL1 30 W until sensor 509 reaches 47 °C (9.7 min),
  then 25.5 W; `/3` 84 → 81 W, `/13` 20, `/16` 1 → 0 after 2 min, `/7` and `/8` 0. The battery controllers never engage, as
  the engine analysis expects. The Windows 10.6 W on battery is still unexplained by the blob alone (energy saver [H]).
