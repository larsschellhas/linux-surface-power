# Spec: evaluation engine

Version 0.1, 14.11.2026. Implements FR-E1 – FR-E7. Tests: AC-E1 – AC-E9. Markers [C], [M], [H], [D] as in `blob-format.md`.
The controller blocks are specified separately in `controller.md`.

## 1. Principles
- **Pure:**
  - The engine does no I/O and reads no clock.
  - Inputs, the timer flag and time come in as arguments; outputs go out as return values (FR-E6).
- **Faithful:** each block reproduces the Windows core's arithmetic, including its quirks. Where the Windows behaviour is unknown, the choice is marked [H] or [D] and listed in `deviations.md` if it can differ.
- **Units:** values stay in blob units (mW, 0.1 K, %, plain numbers). Unit conversion happens only in the adapters.
- **Numbers:** IEEE double throughout, as in the Windows core [C].

## 2. Data model

| Element | Content |
|---|---|
| **Slot** | `value: float`, `valid: bool`. Blocks read and write slots. |
| **Internal node** `VN<n>` | One slot per distinct `n`. **Always valid**, initial value 0 [C]. It is created for every `VN<n>` referenced anywhere, so a node that no block writes keeps 0. |
| **Input slot** | One per device input channel, written only by `Engine.set_inputs()`. Initially invalid. |
| **Output slot** | One per device output channel, written only by blocks. Read by the caller after an evaluation. |
| **Block** | Algorithm GUID, line number, `id`, `instance`, `stage`, `divider`, input slots (ordered), output slots (ordered), parsed parameters, state (controllers only), divider counter. |
| **Graph** | Blocks in evaluation order per instance, the slot tables, and the output channel ranges. |

## 3. Evaluation

### 3.1 One evaluation run [C]
`evaluate(instance, timer)` runs every block of that instance:
- **Order:** stage by stage, from 0 up to the highest stage; within a stage, in file order. A stage without blocks is skipped.
- **Arguments:** each block gets its inputs, its outputs, its divider, the `timer` flag and a shared `active` flag.
- **`active`:** starts `False`. A controller sets it to `True` when it wants the periodic timer (`controller.md` 4).
- **Result:** the values of all output slots and `active`.

Blocks within a run see the values written earlier in the same run. A block that reads a node written by a later stage sees the value from the previous run. This follows from the order; nothing extra is needed.

### 3.2 Common helpers [C]
- **`inputs_ok(block)`:** `n_in > 0` and every input slot is valid. A block with no inputs is never "ok".
- **`clamp(x, hi, lo)`:** if `x > hi` then `hi`; else if `x < lo` then `lo`; else `x`. The upper bound is tested first, so with `lo > hi` the result is `hi` for large `x` and `lo` otherwise.
- **`round_half_up(x)`:** `trunc(x + 0.5)`, truncation toward zero. For negative `x` this is not symmetric, e.g. `round_half_up(-0.7) = 0`.

### 3.3 Scheduling (FR-E2)
The `Scheduler` drives evaluations from a monotonic time in ms that the caller passes in. It holds one state per instance: period 1000 or 5000 ms [C], `timer_running`, `next_due`.
- **Asynchronous run:**
  - When `set_inputs()` changes the value or validity of any input slot read by an instance, that instance gets one run with `timer = False` [H: Windows evaluates on input changes; the exact trigger set is not read].
  - Async runs never advance a controller's divider counter.
- **Timer runs:**
  - While `timer_running`, a run with `timer = True` is due every period, at fixed rate: `next_due += period` [C].
  - If more than one period is missed (e.g. the process was stopped), the scheduler does a single run and re-anchors `next_due = now + period` [D].
- **Timer start and stop:**
  - After every run, `timer_running` takes the `active` result of that run [H: the Windows core starts the timer when a controller becomes active and stops it when none is; the exact flags were not traced].
  - On a start (False → True), `next_due = now + period`.
- **Coalescing:** if a timer run is due in the same `step()` as an input change, only the timer run happens. It sees the new inputs [D].
- **Instances:** each instance has its own timer. Internal nodes are shared between instances [H]. All known blobs use instance 1 only; a blob that uses instance 0 is marked "untested" by the `blob` tool.

### 3.4 Reset (FR-E5) [C]
`reset()` is called on resume from suspend or hibernate (Windows: D0 entry, not at start).
- Every controller is reset as in `controller.md` 5.
- Blocks without state are unchanged.
- If any timer was running before the reset, every instance with a running timer gets one immediate timer run, and its timer runs on from there.

### 3.5 Controller variant (FR-E4)
`Engine(graph, variant=…)` selects the controller state machine:
- **`classic`:** cores ≤ 3.140, e.g. the SB3.
- **`direct`:** cores ≥ 3.159.

The device profile gives the default from the core version in the device's driver package. Cores 3.141 – 3.158 are not known and use `classic` [H].

## 4. Blocks used by all SMF x64 blobs up to 2023

Parameters are listed in blob column order, after the channel references. Each block first writes a value to its outputs, then may overwrite it; the description gives the final result.

### 4.1 Lookup table (`3AFD73EE`)
- **Inputs/outputs:** 1 input `x`, 1 output.
- **Parameters:** `N, below, above, lo[0..N-1], hi[0..N-1], y[0..N-1]`. There are `3N + 3` columns; `N` must be > 0.
- **Checks:**
  - `lo[i] ≤ hi[i]` for every `i`;
  - `hi[i] ≤ lo[i+1]` for `i < N−1`; touching intervals are allowed.
- **Result:**
  - not `inputs_ok` → `below`;
  - `x < lo[0]` → `below`;
  - `x > hi[N−1]` → `above`;
  - otherwise `y[i]` for the first `i` with `lo[i] ≤ x ≤ hi[i]`;
  - if `x` falls into a gap between intervals → `below`.

### 4.2 Min/max select (`0AE20A40`)
- **Inputs/outputs:** n inputs, 1 output.
- **Parameters:** `max, min, default, flag`.
- **Checks:** `min ≤ max`, `default ≤ max`, `min ≤ default`.
- **Result:**
  - not `inputs_ok` → `default`;
  - otherwise `clamp(m, max, min)`, where `m` is the minimum of the inputs (`flag == 0`) or the maximum (`flag ≠ 0`).

### 4.3 Add/subtract/scale (`FD5F6C7E`)
- **Inputs/outputs:** n inputs, 1 output.
- **Parameters:** `strict, k, max, min, offset, divisor, default`.
- **Checks:** `k ≤ n_in`, `default ≤ max`, `min ≤ default`, `divisor ≠ 0`.
- **Result:**
  - `P` = sum of the first `k` inputs, `M` = sum of the other inputs.
  - If `strict == 0` and any input is invalid → `default`.
  - Otherwise `clamp((P − M + offset) / divisor, max, min)`.
  - With `strict ≠ 0`, invalid inputs take part with their last value [C].

### 4.4 Multiplexer (`013CEB02`)
- **Inputs/outputs:** inputs `sel, in[0], in[1], …` (n_in ≥ 3), 1 output.
- **Parameters:** `max, min, default`.
- **Checks:** `min ≤ max`, `default ≤ max`, `min ≤ default`.
- **Result:**
  - `sel` invalid → `default`, not clamped.
  - `sel` valid → `i = round_half_up(sel)`. `v = in[i]` if `0 ≤ i < n_in − 1` and `in[i]` is valid; otherwise `v = default`.
  - The output is `clamp(v, max, min)`.

### 4.5 Pass-through (`026CCFCC`)
- **Inputs/outputs:** 1 input, 1 output.
- **Parameters:** `default`.
- **Result:** `inputs_ok` → the input value, otherwise `default`.

### 4.6 Linear map (`5974888D`)
- **Inputs/outputs:** 1 input `x`, 1 output.
- **Parameters:** `max, min, k, offset, default, mode`. The checks were not read; the loader checks `min ≤ max` [D].
- **Result:**
  - not `inputs_ok` → `default`.
  - `mode == 0` → `clamp(k·x + offset, max, min)`.
  - `mode ≠ 0` → `clamp(k·(1/x') + offset, max, min)`, where `x' = x` if `|x| ≥ 0.001`; otherwise `x' = −0.001` for `x < 0` and `0.001` for `x ≥ 0`.

### 4.7 Window comparator (`2DB3B9EE`)
- **Inputs/outputs:** n inputs, 1 output.
- **Parameters:** `kind[0..n−1], a[0..n−1], b[0..n−1]`. Kinds outside 1–3 are stored as 0 [C].
- **Check:** `a[i] ≤ b[i]` for kind 3.
- **Result:**
  - not `inputs_ok` → 1.
  - Otherwise 1 if any input meets its condition, else 2. The conditions:
    - kind 1: `x ≥ a`;
    - kind 2: `x ≤ a`;
    - kind 3: `x < a or x > b` (outside the window);
    - kind 0: never.

## 5. Blocks of newer blobs (phase 5)
Read from the newer cores (3.140 to 3.205.23, x64) [C] unless marked otherwise. Blocks that are not specified here yet are still treated as unknown by the loader (`blob-format.md` 7).

### 5.1 Conditional table (`76536F66`)
- **Inputs/outputs:** k inputs, 1 output.
- **Parameters:** `N, default`, then for each input `j` in order `lo[j][0..N-1], hi[j][0..N-1]`, then `y[0..N-1]`. There are `2 + 2·N·k + N` columns; `N` must be > 0.
- **Check:** `lo[j][i] ≤ hi[j][i]` for every row and input.
- **Row match:** row `i` matches if every input `j` matches its cell:
  - if `|lo − hi| < 0.001`: `|x_j − hi| < 0.001`;
  - otherwise `lo ≤ x_j ≤ hi`.
- **Result:**
  - the first matching row `i` gives `y[i]`, and the block remembers it;
  - if no row matches, the output is the remembered value, initially `default`.
- **Validity:** the block does not check its inputs; it runs whenever it has an output [C]. An invalid input takes part with its last value, as in add/subtract/scale (4.3) [H: the core reads the input's stored value; how that value behaves while the input is invalid was not traced].
- **Reset:** the remembered value is kept on resume (3.4) [H: no reset path for this block was seen].

### 5.2 Hysteresis table (`0DBF7FAB`)
A step switch: band `i` spans `[down[i], up[i]]` and gives `y[i]`. Neighbouring bands overlap; inside an overlap the output holds.
- **Inputs/outputs:** reads the first input `x`; 1 output.
- **Parameters:** `N, below, above`, then `N` pairs `down[i], up[i]`, then `y[0..N-1]`. There are `3N + 3` columns; `N` must be > 0. Let `L = down[0]` and `H = up[N−1]`.
- **Checks:**
  - `down[i] ≤ up[i]`;
  - `down[i] ≥ L` and `up[i] ≤ H`;
  - for `i ≥ 1`: `down[i] ≥ down[i−1]` and `down[i] ≤ up[i−1]`.
- **State:** the last `x` (initially 0) and the last result (initially `below`).
- **Result,** only when `inputs_ok`. Otherwise the block writes nothing and the output keeps its value [C].
  - `x > H` → `above`; `x < L` → `below`. Both store `x`.
  - Otherwise, if `|x − last x| < 0.001` → the last result.
  - Otherwise, if `down[i+1] < x < up[i]` for some `i < N−1` (strictly inside an overlap) → the last result; `x` is not stored.
  - Otherwise, if `x` fell (`x < last x`): the first `i` with `x ≤ down[i]` gives `below` for `i = 0`, else `y[i−1]`; if there is none, `y[N−1]`.
  - Otherwise (`x` rose): the last `i` with `x ≥ up[i]` gives `above` for `i = N−1`, else `y[i+1]`; if there is none, `y[0]`.
  - In the last two cases the block stores `x`; in all cases the output becomes the new last result.
- **Reset:** the state is kept on resume [H, as 5.1].

### 5.3 Planned
These are described from the survey (`../research/04-smf-device-survey.md`). Their exact semantics will be read from the code and added here before they are implemented.
- **Multiply/divide (`3D42677C`):** the product of the inputs, each optionally inverted, then clamped.
- **Hysteresis quantiser (`A58AF9AA`):** clamp, round down to a step, with a hysteresis of one step.
- **Weighted mean (`A0CE482E`):** a weighted mean plus an offset, clamped.
- **EWMA (`54C09C3D`):**
  - Parameters: `max, min, tau_ms, default`. Update: `y += (Δt/τ)·(x − y)`.
  - From core 3.205.6: `Δt ≥ τ ⇒ y = x` [C].
  - It needs the time between runs; the engine passes `now_ms` to every block for this.

## 6. Numeric tolerances (FR-E7)
- **Deterministic results:** the same blob and the same input sequence give bit-identical results on one platform (AC-E9).
- **Comparing with Windows:** limits are delivered to the output adapters as `round_half_up(value)` in blob units [C, the core rounds output values to integers before handing them to its clients]. The rounded values must match Windows exactly for identical inputs. Measured traces are compared with the tolerances in the acceptance criteria.

## 7. Reference values for the SB3 15" (AC-E2, AC-E5)
These follow from the published analysis (`../research/03-smf-architecture.md`). Local tests check them against the real blob; they are not part of the public test data.
- **Slider ceilings:** state → PL1/PL2 in W:

  | State | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
  |---|---|---|---|---|---|---|---|---|---|---|
  | PL1 | 25 | 13 | 25 | 25 | 30 | 25 | 10 | 25 | 25 | 25 |
  | PL2 | 25 | 15 | 40 | 25 | 61 | 35 | 11 | 61 | 30 | 25 |

  With all other PL1 inputs released, these ceilings appear at the outputs.
- **psys, charge > 10 %:**
  - PL1 = `min(I_IPL_TOP + 23 W, 45 W)`, clamped to 10–100 W.
  - PL2 = `I_IPL_TOP + 34.5 W`, clamped to 10–100 W.
  - With `I_IPL_TOP` = 73.5 W this gives **PL1 45 W, PL2 100 W**.
  - The 45 W cap is the same for both PSU sizes. The table that picks it reads an internal node that no block writes, so that node is 0.
- **psys, charge ≤ 10 %:** + 16.1 W and + 23 W instead.
- **psys with `I_IPL_TOP` invalid:** the add/subtract blocks give their defaults. The result is **PL1 23 W, PL2 34.5 W**. This matches the 23 W psys cap seen once under Windows.
- **PL1 with the posture invalid:** 25.001 W, the "below" value of the posture table. The min block in front of PL1 reads only
  internal nodes, so its 15 W default never applies.
