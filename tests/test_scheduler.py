"""Unit tests for scheduling: async and timer runs, coalescing, reset (spec engine 3.3, 3.4; AC-E7, AC-E9)."""
import unittest

from helpers import LIMITS, SYSTEM, TEMP, VN, BlobBuilder, G, build, ref

from surface_power.engine.scheduler import Scheduler

X = ref(TEMP, 509)
SEL = ref(SYSTEM, 1)


def blob(instance=1):
    b = BlobBuilder()
    b.line(G["pid_sets"], 1, [(TEMP, 509), (SYSTEM, 1)], [(VN, 1)], [20, 1, 15, 4, 0], divider=1, instance=instance)
    b.line(G["pid_parameter_set"], 1, [], [], [1, 1, 1, 30000, 30001, 7500, 21000, 3211, 10, 40, 100, 0.4], instance=instance)
    b.line(G["pass_through"], 2, [(VN, 1)], [(LIMITS, 51)], [15000], stage=1, instance=instance)
    return b


def make(instance=1):
    graph, eng = build(blob(instance))
    return Scheduler(eng), eng


class SchedulerTest(unittest.TestCase):
    def test_async_only_on_change(self):
        s, _ = make()
        runs = s.step(0, {X: 3100, SEL: 1})
        self.assertEqual([r.kind for r in runs], ["async"])
        self.assertEqual(runs[0].outputs[ref(LIMITS, 51)], 30000)
        self.assertEqual(s.step(1000, {X: 3100, SEL: 1}), [])
        self.assertEqual(len(s.step(2000, {X: 3101, SEL: 1})), 1)
        self.assertEqual(len(s.step(3000, {X: None, SEL: 1})), 1)   # validity change counts

    def test_timer_runs_while_active(self):
        s, _ = make()
        s.step(0, {X: 3201, SEL: 1})                     # READY: no timer
        self.assertFalse(s.timer_running(1))
        s.step(1000, {X: 3216, SEL: 1})                  # ACTIVE: timer starts
        self.assertTrue(s.timer_running(1))
        self.assertEqual(s.step(5999, {X: 3216, SEL: 1}), [])
        self.assertEqual([r.kind for r in s.step(6000, {X: 3216, SEL: 1})], ["timer"])
        self.assertEqual(s.step(10999, {X: 3216, SEL: 1}), [])
        self.assertEqual([r.kind for r in s.step(11000, {X: 3216, SEL: 1})], ["timer"])
        runs = s.step(12000, {X: 3100, SEL: 1})          # cools down: async run, controller OFF, timer stops
        self.assertEqual([r.kind for r in runs], ["async"])
        self.assertFalse(s.timer_running(1))
        self.assertEqual(s.step(16000, {X: 3100, SEL: 1}), [])

    def test_timer_and_change_coalesce(self):
        s, _ = make()
        s.step(0, {X: 3201, SEL: 1})
        s.step(1000, {X: 3216, SEL: 1})
        runs = s.step(6000, {X: 3217, SEL: 1})
        self.assertEqual([r.kind for r in runs], ["timer"])

    def test_law_step_every_ten_seconds(self):
        s, eng = make()
        s.step(0, {X: 3201, SEL: 1})
        s.step(1000, {X: 3216, SEL: 1})
        values = []
        for t in range(2000, 32000, 1000):
            for r in s.step(t, {X: 3216, SEL: 1}):
                values.append((t, r.outputs[ref(LIMITS, 51)]))
        changes = [t for (t, v), (_, prev) in zip(values[1:], values) if v != prev]
        self.assertEqual(changes, [11000, 21000, 31000])

    def test_missed_periods_run_once(self):
        s, _ = make()
        s.step(0, {X: 3201, SEL: 1})
        s.step(1000, {X: 3216, SEL: 1})
        self.assertEqual(len(s.step(30000, {X: 3216, SEL: 1})), 1)
        self.assertEqual(s.step(34999, {X: 3216, SEL: 1}), [])
        self.assertEqual(len(s.step(35000, {X: 3216, SEL: 1})), 1)

    def test_reset_with_running_timer(self):
        s, eng = make()
        s.step(0, {X: 3201, SEL: 1})
        s.step(1000, {X: 3216, SEL: 1})
        runs = s.reset(3000)
        self.assertEqual([r.kind for r in runs], ["timer"])
        # After the reset the controller is OFF; the immediate run sees x >= on and goes to READY.
        self.assertEqual(runs[0].outputs[ref(LIMITS, 51)], 21000)

    def test_reset_without_timer(self):
        s, eng = make()
        s.step(0, {X: 3100, SEL: 1})
        self.assertEqual(s.reset(1000), [])
        self.assertEqual(eng.node(1), 30000)

    def test_instance_zero_has_1000_ms(self):
        s, _ = make(instance=0)
        s.step(0, {X: 3201, SEL: 1})
        s.step(100, {X: 3216, SEL: 1})
        self.assertEqual(len(s.step(1100, {X: 3216, SEL: 1})), 1)

    def test_deterministic(self):
        def trace():
            s, _ = make()
            out = []
            for t in range(0, 60000, 1000):
                x = 3100 + (t // 1000) * 3 if t < 40000 else 3150
                for r in s.step(t, {X: x, SEL: 1}):
                    out.append((t, r.kind, r.outputs[ref(LIMITS, 51)]))
            return out
        self.assertEqual(trace(), trace())


if __name__ == "__main__":
    unittest.main()
