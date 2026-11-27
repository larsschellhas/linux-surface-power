"""KM-A5: a write that would touch a lock bit, or a locked register, returns -EPERM and changes nothing.

The module's own code (the limit definitions and its write function) is taken from the source at test time and compiled in user
space against a few stubs, on a fake register block. Nothing of kernel/ is copied into the repository. Needs gcc.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kernel", "surface-rapl-mmio",
                      "surface_rapl_mmio.c")

STUBS = r"""
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint64_t u64;
typedef uint32_t u32;
#define __iomem
#define BIT(n) (1UL << (n))
#define BIT_ULL(n) (1ULL << (n))
#define GENMASK_ULL(h, l) ((~0ULL << (l)) & (~0ULL >> (63 - (h))))
#define FIELD_GET(mask, v) (((v) & (mask)) >> __builtin_ctzll(mask))
#define U32_MAX 0xffffffffu
#define pr_warn_once(...) do { } while (0)
#define pr_warn_ratelimited(...) do { } while (0)
struct mutex { int unused; };
#define DEFINE_MUTEX(name) struct mutex name
#define mutex_lock(m) ((void)(m))
#define mutex_unlock(m) ((void)(m))
union rapl_reg { void __iomem *mmio; u64 val; };
struct reg_action { union rapl_reg reg; u64 mask; u64 value; };
static int writes;
static u64 readq(const void *p) { u64 v; memcpy(&v, p, 8); return v; }
static u32 readl(const void *p) { u32 v; memcpy(&v, p, 4); return v; }
static void writeq(u64 v, void *p) { memcpy(p, &v, 8); writes++; }
static bool allow_write, srm_removing;
static DEFINE_MUTEX(srm_lock);
static unsigned char block[0x1000];
static unsigned char *srm_map = block;
"""

MAIN = r"""
#define LIMIT (block + SRM_REG_LIMIT - SRM_WINDOW_OFF)
#define UNIT (block + SRM_REG_UNIT - SRM_WINDOW_OFF)
static const u64 FIRMWARE = 0x0000811800dd80e0ULL;      /* the Pro 10's register: 28 W and 35 W, 1/8 W units */

static void reset(u64 reg)
{
	u32 unit = 0x000a0e03;
	memset(block, 0, sizeof(block));
	memcpy(LIMIT, &reg, 8);
	memcpy(UNIT, &unit, 4);
	writes = 0;
	allow_write = true;
	srm_removing = false;
}

static void check(const char *name, unsigned char *where, u64 mask, u64 value)
{
	struct reg_action ra = { .reg.mmio = where, .mask = mask, .value = value };
	u64 before = readq(LIMIT);
	int ret = srm_write_raw(0, &ra);
	printf("%s %d %d %llx %llx\n", name, ret, writes, (unsigned long long)before, (unsigned long long)readq(LIMIT));
}

int main(void)
{
	reset(FIRMWARE); check("allowed", LIMIT, SRM_PL1_FIELD, 23 * 8);
	reset(FIRMWARE); allow_write = false; check("read_only", LIMIT, SRM_PL1_FIELD, 23 * 8);
	reset(FIRMWARE); srm_removing = true; check("removing", LIMIT, SRM_PL1_FIELD, 23 * 8);
	reset(FIRMWARE | BIT_ULL(31)); check("locked_low", LIMIT, SRM_PL1_FIELD, 23 * 8);
	reset(FIRMWARE | BIT_ULL(63)); check("locked_high", LIMIT, SRM_PL1_FIELD, 23 * 8);
	reset(FIRMWARE); check("mask_with_lock_bit", LIMIT, SRM_PL1_FIELD | BIT_ULL(31), BIT_ULL(31));
	reset(FIRMWARE); check("value_sets_lock_bit", LIMIT, SRM_PL1_FIELD, BIT_ULL(63) | 23 * 8);
	reset(FIRMWARE); check("value_outside_mask", LIMIT, SRM_PL1_FIELD, 1ULL << 20);
	reset(FIRMWARE); check("pl2_window", LIMIT, GENMASK_ULL(55, 49), 1ULL << 49);
	reset(FIRMWARE); check("other_register", UNIT, SRM_PL1_FIELD, 23 * 8);
	reset(FIRMWARE); check("below_floor", LIMIT, SRM_PL1_FIELD, 2 * 8);
	reset(FIRMWARE); check("pl2_below_floor", LIMIT, SRM_PL2_FIELD, (u64)(2 * 8) << 32);
	reset(FIRMWARE); memset(UNIT, 0xff, 4); check("no_unit", LIMIT, SRM_PL1_FIELD, 23 * 8);
	return 0;
}
"""


def extract() -> str:
    with open(SOURCE) as f:
        src = f.read()
    defines = "\n".join(re.findall(r"^#define SRM_\w+[ \t]+.*$", src, re.MULTILINE))
    parts = [defines]
    for name in ("srm_reg", "srm_power_unit", "srm_watts_to_raw", "srm_write_raw"):
        m = re.search(rf"^static [^\n;]*\b{name}\([^;{{]*\)\n\{{\n.*?\n\}}\n", src, re.MULTILINE | re.DOTALL)
        if m is None:
            raise AssertionError(f"{name} not found in the module source: update this test")
        parts.append(m.group(0))
    return "\n".join(parts)


@unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
class WriteGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            c, exe = os.path.join(tmp, "guard.c"), os.path.join(tmp, "guard")
            with open(c, "w") as f:
                f.write(STUBS + extract() + MAIN)
            build = subprocess.run(["gcc", "-Wall", "-o", exe, c], capture_output=True, text=True)
            if build.returncode:
                raise AssertionError(build.stderr)
            out = subprocess.run([exe], capture_output=True, text=True, check=True).stdout
        cls.cases = {}
        for line in out.splitlines():
            name, ret, writes, before, after = line.split()
            cls.cases[name] = (int(ret), int(writes), int(before, 16), int(after, 16))

    def test_an_allowed_write_changes_only_its_field(self):
        ret, writes, before, after = self.cases["allowed"]
        self.assertEqual((ret, writes), (0, 1))
        self.assertEqual((before ^ after) & ~0x7FFF, 0)
        self.assertEqual(after & 0x7FFF, 23 * 8)

    def test_refused_writes_change_nothing(self):
        for name in ("read_only", "removing", "locked_low", "locked_high", "mask_with_lock_bit", "value_sets_lock_bit",
                     "value_outside_mask", "pl2_window", "other_register"):
            with self.subTest(name):
                ret, writes, before, after = self.cases[name]
                self.assertEqual((ret, writes, before), (-1, 0, after))        # -EPERM

    def test_limits_below_the_floor_and_without_a_unit_are_refused(self):
        for name in ("below_floor", "pl2_below_floor", "no_unit"):
            with self.subTest(name):
                ret, writes, before, after = self.cases[name]
                self.assertEqual((ret, writes, before), (-22, 0, after))       # -EINVAL


if __name__ == "__main__":
    unittest.main()
