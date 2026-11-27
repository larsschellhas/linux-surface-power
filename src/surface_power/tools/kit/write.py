"""S5: the first write, only downwards (spec tester-kit.md 6.5; kernel-mmio-rapl.md 6.1 item 5).

The MMIO PL1 is set below both copies' PL1 and below the plateau measured in S2, so that its effect shows and, whichever copy
is in force, no more power is allowed than before. After every single write the register is read: only the intended bits may
have changed. The module restores the register at its unload; that is checked, and so is that the old plateau comes back.
"""
import math
import os

from ...adapters import msr, rapl_copies
from . import ZONE, load, module

MARGIN_W, GAP_W, FLOOR_W = 5, 3, 8     # target = min(both PL1, plateau) - margin; at least this far below both [D]; 8 W ran on the SB3 [M]
TOLERANCE_W = 1.5                      # the new plateau against the target [H, to be set after the SB3 run; SB3: 9.98 W at 10 W]
LOAD_S, LOAD_LAST_S, BACK_S = 90, 30, 60
PHASES = (("limited", LOAD_S, True),)
BACK_PHASES = (("pause", 20, False), ("restored", BACK_S, True))


def target(msr_pl1_w: float, mmio_pl1_w: float, plateau_w: float) -> tuple[int | None, str | None]:
    """(watts to write, None) or (None, why there is no room for a test)."""
    lowest = min(msr_pl1_w, mmio_pl1_w)
    watts = max(FLOOR_W, math.floor(min(lowest, plateau_w)) - MARGIN_W)
    if watts > lowest - GAP_W or watts > plateau_w - GAP_W:
        return None, (f"no room for a test: the lowest PL1 is {lowest:g} W and the load settled at {plateau_w:g} W, but nothing below "
                      f"{FLOOR_W} W is written and the effect needs {GAP_W} W of distance")
    return watts, None


def _msr_copy(root: str) -> tuple[int | None, rapl_copies.Copy | None]:
    unit, value = msr.power_unit_w(root), msr.read(root, rapl_copies.MSR_PL)
    return (None, None) if unit is None or value is None else (value, rapl_copies.Copy(value, unit))


class _Stop(Exception):
    pass


def _write_zone(root: str, name: str, value: int) -> None:
    with open(os.path.join(root, ZONE, name), "w") as f:
        f.write(str(value))


def _step(root: str, platform: str, steps: list, name: str, value: int, allowed: int) -> module.Register:
    """One write to the zone, then the register: nothing but `allowed` bits may differ."""
    before, why = module.Register.read(root, platform)
    if before is None:
        raise _Stop(f"the register could not be read before writing {name} ({why})")
    try:
        _write_zone(root, name, value)
    except OSError as e:
        raise _Stop(f"writing {value} to {name} failed ({e.strerror or e})") from e
    after, why = module.Register.read(root, platform)
    if after is None:
        raise _Stop(f"the register could not be read after writing {name} ({why})")
    steps.append({"file": name, "value": value, "register_before": f"{before.raw:#018x}", "register_after": f"{after.raw:#018x}"})
    if (before.raw ^ after.raw) & ~allowed:
        raise _Stop(f"writing {name} changed other bits too: {before.raw:#018x} -> {after.raw:#018x}")
    return after


def apply(root: str, platform: str, watts: int, before: module.Register, msr_copy: rapl_copies.Copy, steps: list) -> None:
    """Bring the MMIO PL1 to `watts` and make it count. Never raises a limit that is in force."""
    enabled = before.copy.pl1[1]
    if not enabled and before.copy.pl2[0] > msr_copy.pl2[0]:
        # switching the copy on switches its PL2 on too: first bring it down to what the MSR copy allows
        _step(root, platform, steps, "constraint_1_power_limit_uw", math.floor(msr_copy.pl2[0]) * 1_000_000, module.PL2_FIELD)
    reg = _step(root, platform, steps, "constraint_0_power_limit_uw", watts * 1_000_000, module.PL1_FIELD)
    if not enabled:
        reg = _step(root, platform, steps, "enabled", 1, module.ENABLE_BITS)
    if abs(reg.copy.pl1[0] - watts) > 0.2 or not reg.copy.pl1[1]:
        raise _Stop(f"after the writes PL1 reads {reg.copy.pl1[0]:g} W ({'on' if reg.copy.pl1[1] else 'off'}), not {watts} W switched on")
    allowed_pl2 = before.copy.pl2[0] if enabled else min(before.copy.pl2[0], msr_copy.pl2[0])
    if reg.copy.pl2[0] > allowed_pl2 + 0.01:
        raise _Stop(f"PL2 reads {reg.copy.pl2[0]:g} W, above {allowed_pl2:g} W")


def test(root: str, undo, ko: str, platform: str, sku: str, s2_plateau: dict, sampler, make_load, skin_refs=(),
         run_phases=None, say=lambda text: None, on_sample=None) -> dict:
    """{"status", "summary", "details"}."""
    run_phases = run_phases or load.run_phases
    before, why = module.Register.read(root, platform)
    if before is None or before.implausible():
        return {"status": "skipped", "summary": f"not run: {why or before.implausible()}.", "details": {}}
    msr_raw, msr_copy = _msr_copy(root)
    if msr_copy is None:
        return {"status": "skipped", "summary": "not run: MSR 0x610 is not readable.", "details": {}}
    watts, why = target(msr_copy.pl1[0], before.copy.pl1[0], s2_plateau["mean_w"])
    if watts is None:
        return {"status": "skipped", "summary": f"not run: {why}.", "details": {}}
    if not before.copy.pl1[1] and msr_copy.pl2[0] < watts:
        return {"status": "skipped", "details": {},
                "summary": f"not run: the MMIO copy is off and the MSR copy's PL2 ({msr_copy.pl2[0]:g} W) cannot serve as the bound "
                           "for switching it on."}
    health = module.Health(root)
    details: dict = {"target_w": watts, "plateau_before_w": s2_plateau["mean_w"], "register_before": f"{before.raw:#018x}",
                     "msr_before": f"{msr_raw:#018x}", "copy_was_enabled": before.copy.pl1[1], "writes": []}
    params = module.parameters(platform, sku, True)
    unload, refused = module.load(undo, ko, params)
    if unload is None:
        return {"status": "skipped", "summary": f"insmod refused the module ({refused}); nothing was changed.", "details": details}
    problems, remarks, top, samples = [], [], None, []
    try:
        loaded_reg, why = module.Register.read(root, platform)
        if loaded_reg is None or loaded_reg.raw != before.raw:
            raise _Stop("the register changed at the load" if loaded_reg else f"the register could not be read ({why})")
        say(f"Writing {watts} W to the MMIO PL1 (lower than what is allowed now) ...")
        apply(root, platform, watts, before, msr_copy, details["writes"])
        problems += health.problems()
        if not problems:
            say(f"Load for {LOAD_S} s at {watts} W ...")
            got = run_phases(PHASES, sampler, make_load(), skin_refs, on_sample=on_sample)
            samples = got["samples"]
            top = load.plateau(samples, "limited", LOAD_LAST_S)
            if got["aborted"]:
                remarks.append(f"the load was ended early: {got['aborted']}")
            reg, _ = module.Register.read(root, platform)
            details["register_under_load"] = None if reg is None else f"{reg.raw:#018x}"
            if reg is not None and abs(reg.copy.pl1[0] - watts) > 0.2:
                remarks.append(f"during the load the MMIO PL1 changed to {reg.copy.pl1[0]:g} W: something else writes it")
            if msr.read(root, rapl_copies.MSR_PL) != msr_raw:
                remarks.append("the MSR copy changed during the test: something else writes it")
    except _Stop as e:
        problems.append(str(e))
    finally:
        code, text = undo.run_one(unload) if unload in undo.pending else (0, "")
    if code != 0:
        problems.append(f"the module could not be unloaded: {text}")
    after, why = module.Register.read(root, platform)
    if after is None:
        problems.append(f"the register could not be read after the unload ({why})")
    else:
        details["register_after"] = f"{after.raw:#018x}"
        if after.raw != before.raw:
            problems.append(f"the register was not restored at the unload: {before.raw:#018x} -> {after.raw:#018x}")
    problems += health.problems()
    details["plateau_limited"] = top
    back = None
    if not problems and top is not None and not remarks:
        say(f"Module unloaded. Load again for {BACK_S} s: the old behaviour should be back ...")
        got = run_phases(BACK_PHASES, sampler, make_load(), skin_refs, on_sample=on_sample)
        samples += got["samples"]
        back = load.plateau(got["samples"], "restored", LOAD_LAST_S)
        details["plateau_restored"] = back
        if got["aborted"]:
            remarks.append(f"the second load was ended early: {got['aborted']}")
    details["samples"] = samples
    if remarks:
        details["remarks"] = remarks
    if problems:
        details["problems"] = problems
        return {"status": "failed", "summary": "; ".join(problems[:3]), "details": details}
    if remarks:
        return {"status": "not-passed", "summary": "; ".join(remarks) + ". The register is restored.", "details": details}
    if top is None:
        return {"status": "not-passed", "summary": "no plateau was measured under the lower limit. The register is restored.",
                "details": details}
    measured = top["mean_w"]
    if measured > watts + TOLERANCE_W:
        return {"status": "not-passed", "details": details,
                "summary": f"with the MMIO PL1 at {watts} W the package drew {measured} W: the write had no or too little effect. "
                           "The register is restored."}
    if measured < watts - TOLERANCE_W:
        return {"status": "not-passed", "details": details,
                "summary": f"the package drew {measured} W, well below the written {watts} W: something else held it, so the effect "
                           "cannot be judged. The register is restored."}
    if back is None or back["mean_w"] <= watts + GAP_W - 1:
        return {"status": "not-passed", "details": details,
                "summary": f"{watts} W held the package at {measured} W and the register is restored, but the old plateau did not come back "
                           f"({'not measured' if back is None else str(back['mean_w']) + ' W'})."}
    return {"status": "passed", "details": details,
            "summary": f"with the MMIO PL1 at {watts} W the package settled at {measured} W (before: {s2_plateau['mean_w']} W); after the "
                       f"unload the register is bit-identical and the package draws {back['mean_w']} W again."}
