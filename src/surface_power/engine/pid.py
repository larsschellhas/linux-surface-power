"""PID controller with parameter sets (spec controller.md).

Two line types work together:
- the controller line (inputs: process value x and set selector s; one output) holds N, the number of sets, the off hysteresis,
  the deadband, the default set and the mode;
- parameter-set lines (no channels) hold one set each and are attached to the controller with the same id.

The controller runs a state machine OFF (0) -> READY (1) -> ACTIVE (2). In ACTIVE it applies a velocity-form law every
(divider + 1)-th timer run. Two variants exist, selected by core version: "classic" (cores <= 3.140, e.g. the SB3) and "direct"
(cores >= 3.159). They differ in the state machine and in the first law step.
"""
from dataclasses import dataclass

from ..loader.errors import BlobError
from .blocks import Block, Params
from .helpers import clamp, round_half_up
from .registry import register

OFF, READY, ACTIVE = 0, 1, 2
STATE_NAMES = {OFF: "OFF", READY: "READY", ACTIVE: "ACTIVE"}
VARIANTS = ("classic", "direct")


@dataclass(frozen=True)
class ParameterSet:
    """One parameter set (spec controller 1). Powers in mW, temperatures in 0.1 K, as in the blob."""

    line: int
    stage_from: int
    stage_to: int
    u_off: float
    u_max: float
    u_min: float
    u_on: float
    w: float        # setpoint
    dw_on: float    # the controller switches on at w - dw_on
    k_e: float      # gain on the error e (acts as the integral part per step in the velocity form)
    k_d: float      # gain on the error change (acts as the proportional part)
    k_m: float      # gain on the mean error of the last N + 1 steps


@register("pid_parameter_set")
class ParameterSetLine(Block):
    """Data only: parsed and checked here, attached to its controller by the graph builder. Never runs."""

    def check_shape(self, n_in, n_out):
        if n_in or n_out:
            raise BlobError(f"{self.kind}: must not have channel references", self.line.line)

    def parse(self, p: Params):
        p.count(12)
        self.ctl_id = p.int(0, "controller id")
        s = ParameterSet(
            line=self.line.line, stage_from=p.int(1, "stage from"), stage_to=p.int(2, "stage to"),
            u_off=p.real(3, "U_off"), u_max=p.real(4, "U_max"), u_min=p.real(5, "U_min"), u_on=p.real(6, "U_on"),
            w=p.real(7, "W"), dw_on=p.real(8, "dW_on"), k_e=p.real(9, "K_e"), k_d=p.real(10, "K_d"), k_m=p.real(11, "K_m"))
        if min(s.k_e, s.k_d, s.k_m) < 0 or s.dw_on < 0:
            raise p.fail("gains and dW_on must not be negative")
        if not (s.u_min <= s.u_max and s.u_off <= s.u_max and s.u_min <= s.u_off
                and s.u_on <= s.u_max and s.u_on <= s.u_off and s.u_min <= s.u_on):
            raise p.fail("needs U_min <= U_on <= U_off <= U_max")
        self.set = s

    def run(self, ctx):
        pass


@register("pid_sets")
class PidWithSets(Block):
    """The controller line. State: current set, state, output U, error history, divider counter (spec controller 3)."""

    def check_shape(self, n_in, n_out):
        if n_in != 2:
            raise BlobError(f"{self.kind}: needs exactly 2 inputs (x and the set selector), has {n_in}", self.line.line)

    def parse(self, p: Params):
        p.count(5, 6)
        self.n = min(max(p.int(0, "N"), 2), 300)
        self.n_sets = p.int(1, "number of sets")
        self.hyst_off = p.real(2, "off hysteresis")
        self.deadband = p.real(3, "deadband")
        self.default_set = p.int(4, "default set")
        self.mode = 0
        if len(p.fields) == 6:
            self.mode = p.int(5, "mode")
            if self.mode not in (1, 2):
                raise p.fail("mode column, if present, must be 1 or 2")
            if self.mode == 1:
                self.n = 2
        if self.n_sets <= 0:
            raise p.fail("number of sets must be positive")
        if self.hyst_off <= 0 or self.deadband < 0:
            raise p.fail("needs off hysteresis > 0 and deadband >= 0")
        if self.variant not in VARIANTS:
            raise p.fail(f"unknown controller variant {self.variant!r}")
        self.sets: list[ParameterSet] = []
        self.cur = self.default_set
        self.state = OFF
        self.u = 0.0                   # zeroed at boot [C]; the first evaluation overwrites it
        self.buf = [0.0] * self.n      # error history, newest first
        self.counter = 0

    def attach(self, s: ParameterSet) -> None:
        self.sets.append(s)

    def finish(self) -> None:
        """Called by the graph builder after all sets are attached."""
        if not self.sets:
            raise BlobError(f"{self.kind} id {self.line.id}: no parameter sets", self.line.line)
        if not 0 <= self.default_set < len(self.sets):
            raise BlobError(f"{self.kind} id {self.line.id}: default set {self.default_set} does not exist", self.line.line)
        self.cur = self.default_set

    @property
    def supported(self) -> bool:
        return self.mode == 0

    # --- 4.1 set selection ---
    def _select_set(self) -> None:
        x, s = self.inputs
        if not (x.valid and s.valid):
            self.cur = self.default_set
            return
        k = round_half_up(s.value)
        old = self.sets[self.cur]
        if k == old.stage_to:
            return
        j = next((i for i, st in enumerate(self.sets) if st.stage_to == k), None)
        if j is None:
            return
        new = self.sets[j]
        if new.w != old.w:
            shift = new.w - old.w
            self.buf = [e + shift for e in self.buf]
            if new.w < old.w:
                if self.u > new.u_off:
                    self.u = new.u_off
            elif x.value < new.w and self.u >= old.u_off:
                self.u = new.u_off
                if x.value > new.w - new.dw_on:
                    self.u = new.u_on
        self.cur = j

    # --- 4.5 law (mode 0, velocity form) ---
    def _law(self, x: float, fresh: bool, base: float) -> float:
        p = self.sets[self.cur]
        e = p.w - x
        if fresh:
            self.buf = [0.0] * self.n
            base = p.u_on
            if self.variant == "direct":
                return clamp(base, p.u_max, p.u_min)  # no increment on the first step
        s = sum(self.buf)
        self.buf = [e] + self.buf[:-1]
        d = (self.buf[0] - self.buf[1]) * p.k_d if p.k_d != 0 else 0.0
        return clamp(base + p.k_e * e + d + p.k_m * (s + e) / (self.n + 1), p.u_max, p.u_min)

    def _deadband(self, x: float, u_new: float, u_fallback: float) -> float:
        return u_fallback if abs(x - self.sets[self.cur].w) < self.deadband else u_new

    # --- 4.3 state machine, classic (cores <= 3.140) ---
    def _machine_classic(self, x: float, step: bool) -> None:
        p = self.sets[self.cur]
        on = p.w - p.dw_on
        off = on - self.hyst_off
        u_old = self.u
        if x < off:
            self.state, self.u = OFF, p.u_off
            return
        if x < on and self.state not in (READY, ACTIVE):
            return                                   # OFF in the band: U unchanged
        if self.state == OFF:
            self.state, self.u = READY, p.u_on
            return
        if self.state == READY:
            if x < p.w:
                self.u = p.u_on
                return
            self.state, fresh = ACTIVE, True
        else:
            fresh = False
        if step or fresh:
            self.u = self._deadband(x, self._law(x, fresh, u_old), u_old)

    # --- 4.4 state machine, direct (cores >= 3.159) ---
    def _machine_direct(self, x: float, step: bool) -> None:
        p = self.sets[self.cur]
        on = p.w - p.dw_on
        off = on - self.hyst_off
        u_old = self.u
        fresh = False
        if self.state == OFF:
            if x < on:
                self.u = p.u_off
                return
            self.state = READY if x < p.w else ACTIVE
            self.u = u_old = p.u_on                  # the deadband fallback is U_on here [C]
            fresh = True
        elif self.state == READY:
            if x < p.w:
                if x < off:
                    self.state, self.u = OFF, p.u_off
                return
            self.state, self.u = ACTIVE, p.u_on
        elif self.state == ACTIVE:
            if x < off:
                self.state, self.u = OFF, p.u_off
                return
        else:
            self.state, self.u = OFF, p.u_off
            return
        if (self.state == ACTIVE and step) or fresh:
            self.u = self._deadband(x, self._law(x, fresh, u_old), u_old)

    # --- 4.2 one evaluation ---
    def run(self, ctx):
        if not self.outputs:
            return
        if len(self.outputs) != 1:
            self.write(self.sets[self.default_set].u_off)
            return
        self._select_set()
        x = self.inputs[0]
        if not x.valid:
            self.state = OFF
            self.write(self.sets[self.cur].u_off)  # U itself is kept [C]
            return
        step = False
        wants_timer = False
        if self.state == ACTIVE:
            wants_timer = True
            if ctx.timer:
                if self.counter < self.line.divider:
                    self.counter += 1
                else:
                    self.counter = 0
                    step = True
        prev = self.state
        if self.variant == "classic":
            self._machine_classic(x.value, step)
        else:
            self._machine_direct(x.value, step)
        self.write(self.u)
        if self.state == OFF:
            wants_timer = False
        elif prev == READY and self.state == ACTIVE:
            wants_timer = True
        # OFF -> ACTIVE (direct variant) does not ask for the timer in this run; the next run does [C].
        ctx.active = ctx.active or wants_timer

    def reset(self):
        """Resume (spec controller 5): OFF with U = U_off of the current set. cur, buf and counter are kept [C]."""
        self.state = OFF
        self.u = self.sets[self.cur].u_off
        self.write(self.u)

    def describe(self) -> str:
        p = self.sets[self.cur] if self.sets else None
        extra = f", set {self.cur}, W {p.w:g}" if p else ""
        return f"pid id {self.line.id}: {STATE_NAMES.get(self.state, self.state)}, U {self.u:g}{extra}"
