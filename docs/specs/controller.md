# Spec: PID controller with parameter sets

Version 0.1, 14.11.2026. Implements FR-E1 (PID part), FR-E2 (divider, timer), FR-E4 (variants), FR-E5 (reset). Tests: AC-E3, AC-E7,
AC-E8. Markers as in `blob-format.md`. Everything here is [C] unless marked.

## 1. Lines
- **Controller line** (`9E2A7FED`):
  - Inputs: exactly 2, the process value `x` and the set selector `s`. Output: 1.
  - Parameters: `N, n_sets, hyst_off, deadband, default_set[, mode]`.
- **Parameter-set line** (`83F47D50`):
  - No channel references.
  - Parameters: `ctl_id, stage_from, stage_to, U_off, U_max, U_min, U_on, W, dW_on, K_e, K_d, K_m`.
  - It belongs to the controller line with the same `id` as `ctl_id`. Sets are kept in file order.

## 2. Parsing and checks
- **`N`:** the mean window. Values below 2 become 2, values above 300 become 300.
- **`n_sets`:** must be > 0.
- **Other controller fields:**
  - `hyst_off > 0`, `deadband ≥ 0`;
  - `default_set`: an index into the sets;
  - `mode`, if the column is present, must be 1 or 2. If it is absent, mode 0 applies.
  - With `mode == 1`, `N` becomes 2.
- **Set checks:**
  - `K_e, K_d, K_m ≥ 0`, `dW_on ≥ 0`;
  - `U_min ≤ U_max`, `U_min ≤ U_off ≤ U_max`;
  - `U_min ≤ U_on ≤ U_off`, `U_on ≤ U_max`.
- **Set count:** the number of set lines for a controller should equal `n_sets`. A mismatch is a warning [D].
- **Set selection** uses `stage_to` (the second stage field). `stage_from` is parsed but not used by the selection.

## 3. State
| Field | Meaning | Initial value |
|---|---|---|
| `cur` | index of the current set | `default_set` |
| `state` | 0 OFF, 1 READY, 2 ACTIVE | 0 |
| `U` | controller output | 0 [C: zeroed memory]; the first evaluation overwrites it |
| `buf[0..N−1]` | error history, newest first | zeros |
| `counter` | divider counter, belongs to the line | 0 |

## 4. One evaluation
Within a controller line, the steps run in this order.

### 4.1 Set selection
- **`x` or `s` invalid:** `cur = default_set`, without touching `U` or `buf`.
- **Otherwise:** `k = round_half_up(s)`. If `k ≠ sets[cur].stage_to`, search the sets in file order for the first set `j` with `stage_to == k`. If none is found, nothing changes. If one is found, let `old = sets[cur]` and `new = sets[j]`; then:
  - **Same setpoint** (`new.W == old.W`): only `cur = j`.
  - **Different setpoint:**
    1. Every `buf[i] += new.W − old.W`.
    2. Then, depending on the direction:
       - **Lower setpoint** (`new.W < old.W`): if `U > new.U_off`, then `U = new.U_off`.
       - **Higher setpoint:** if `x < new.W` and `U ≥ old.U_off`, then `U = new.U_off`. After that, if also `x > new.W − new.dW_on`, then `U = new.U_on`.
    3. Then `cur = j`.

### 4.2 Line check and output
- **Wrong line shape:** if `n_in ≠ 2` or `n_out ≠ 1`, the output is `sets[default_set].U_off` and nothing else happens.
- **`x` invalid:** this check uses `x` only, not `s`. Then:
  - the output is `sets[cur].U_off`;
  - `state = 0`;
  - `U` is not changed. It is used again once `x` is valid; see 4.4.
- **Otherwise** the controller runs as follows (the parameters `p` are those of `sets[cur]`):
  1. `step = False`.
  2. If `state == 2`:
     - set `active = True` for the timer;
     - if this is a timer run: if `counter < divider`, then `counter += 1`; else `counter = 0` and `step = True`.
  3. `prev = state`.
  4. Run the state machine (4.3 or 4.4) with `x` and `step`.
  5. Output = `U`.
  6. If `state == 0` after the machine: `active` is not set by this line. If `prev == 1` and `state == 2`: `active = True`.

  With `divider = 1` and a 5000 ms instance, a law step happens on every second timer run, i.e. every 10 s [C, M].

### 4.3 State machine, variant `classic` (cores ≤ 3.140)
Let `on = p.W − p.dW_on` and `off = on − hyst_off`. `U_old` is the value of `U` before this step.

| Condition (checked in this order) | Effect |
|---|---|
| `x < off` | `state = 0`, `U = p.U_off`. Done. |
| `x < on` and `state == 0` | `U` unchanged. Done. |
| `state == 0` (so `x ≥ on`) | `state = 1`, `U = p.U_on`. Done (no law step). |
| `state == 1` and `x < p.W` | `U = p.U_on`. Done. |
| `state == 1` (so `x ≥ p.W`) | `state = 2`, `fresh = True`; continue below. |
| `state == 2` | `fresh = False`; continue below. |

**Continue:** if `step` or `fresh`:
1. Run the law (4.5) with `fresh`; this gives `U_new`.
2. If `|x − p.W| < deadband`, then `U = U_old`; otherwise `U = U_new`.

The law runs even inside the deadband, so `buf` is updated there too.

With `state == 0` and `off ≤ x < on`, `U` keeps its last value. After a reset that is `U_off`. After `x` was invalid during ACTIVE, it is the last active value (4.2).

### 4.4 State machine, variant `direct` (cores ≥ 3.159)

| State | Condition | Effect |
|---|---|---|
| 0 | `x < on` | `U = p.U_off`; stays 0. Done. |
| 0 | `on ≤ x < p.W` | `state = 1`, `U = p.U_on`, `fresh = True`; law. |
| 0 | `x ≥ p.W` | `state = 2`, `U = p.U_on`, `fresh = True`; law. |
| 1 | `x < off` | `state = 0`, `U = p.U_off`. Done. |
| 1 | `off ≤ x < p.W` | `U` unchanged; stays 1. Done. |
| 1 | `x ≥ p.W` | `state = 2`, `U = p.U_on`, `fresh = False`; law only if `step`. |
| 2 | `x < off` | `state = 0`, `U = p.U_off`. Done. |
| 2 | otherwise | `U` unchanged, `fresh = False`; law only if `step`. |
| other | – | `state = 0`, `U = p.U_off`. Done. |

**Law step:**
1. Run the law (4.5) with `fresh`. The base is `p.U_on` if `fresh`, else `U_old` (the value before this evaluation).
2. Deadband: if `|x − p.W| < deadband`, then `U = U_old`; otherwise `U = U_new`.

**Example, READY → ACTIVE on a timer step:** the law starts from `U_old`, not from `U_on`. Without a step, `U` becomes `p.U_on`.

### 4.5 Law, mode 0 (velocity form)
`e = p.W − x` (setpoint minus process value). The two variants differ in the first step:

| | `classic` | `direct` |
|---|---|---|
| `fresh` | `base = p.U_on`; clear `buf`, then do the normal step from `base` | `base = p.U_on`; clear `buf`; `U_new = clamp(base, p.U_max, p.U_min)`; no increment, `buf` stays cleared |
| not `fresh` | `base = U_old`, normal step | `base = U_old`, normal step |

**Normal step:**
1. `S = Σ buf[0..N−1]`, taken before the shift.
2. Shift: `buf[i] = buf[i−1]` for `i = N−1 … 1`; then `buf[0] = e`.
3. `d = (buf[0] − buf[1]) · K_d` if `K_d ≠ 0`, else 0.
4. `U_new = clamp(base + K_e·e + d + K_m·(S + e)/(N + 1), p.U_max, p.U_min)`.

In control terms, `K_d·Δe` acts as the proportional part and `K_e·e` as the integral part per step, because this is the velocity form. `K_m` adds a small integral term on the mean of the last `N + 1` errors.

### 4.6 Modes 1 and 2
These are the positional form with anti-windup. No known blob uses them. They are not implemented; a blob with `mode ≠ 0` is not used for control [D].

## 5. Reset (resume)
On reset, the controller is set as follows:
- `state = 0`;
- `U = sets[cur].U_off`;
- the output slot = `U`.

`cur`, `buf` and `counter` keep their values [C: the reset routine does not touch them].

Note: the research summary said "counters at 0". That holds for the type-1 controller core, not for this block.

## 6. Variable-setpoint PID (`0D24F500`)
Read from cores 3.204.23 to 3.205.23 [C]. It runs the same controller core as the PID with sets (4.3 – 4.5), with one parameter set given inline and the setpoint taken from an input.
- **Inputs/outputs:** `x`, `W`, optionally `enable`; 1 output.
- **Parameters:** `N, U_max, U_min, W_default, K_e, K_d, K_m, deadband, dW_on, hyst_off, U_on, U_off`, optionally `mode` (13 columns). `N` is limited to 2 … 300 as in 3.
- **Checks** (skipped with expressions, `engine.md` 3.6):
  - `K_e, K_d, K_m, dW_on, deadband ≥ 0`, `hyst_off > 0`;
  - `deadband < hyst_off + dW_on`;
  - `U_min ≤ U_on ≤ U_off ≤ U_max`;
  - a `mode` column must be 1 or 2 (the positional forms, not implemented: such a line is unsupported, as in 4.6). With `mode = 1`, `N = 2`.
- **One evaluation:**
  1. `x` invalid → OFF, output `U_off`; `U` itself is kept.
  2. With three inputs, `round_half_up(enable) ≠ 2` → the same OFF. The enable value is used even when it is invalid [C].
  3. `W` = the value of input 2 if it is valid, otherwise `W_default`. A new `W` does not shift the error history (unlike a set change, 4.1).
  4. Divider count, state machine, law and timer request as in 4.2 – 4.5, always in the `direct` variant: the block exists only in cores from 3.204 on.
- **Reset:** as in 5 [H: the core shares the controller structure; its resume path for this block was not read].
- **Inputs other than 2 or 3** are rejected [D].

## 7. Planned
- **PID cores `B34C9310` (type 1) and `7C2D76F2` (type 2):** used by the core internally and by some blobs directly. To be read before implementation.
