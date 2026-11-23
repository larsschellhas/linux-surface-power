// SPDX-License-Identifier: GPL-2.0-only
/*
 * surface_rapl_mmio: the MMIO copy of the package power limits PL1/PL2 as a powercap zone ("intel-rapl-mmio") on
 * Surface devices whose firmware hides the processor thermal PCI device (00:04.0), which the kernel's own MMIO RAPL
 * driver needs. The Windows Surface power management writes this copy (MCHBAR + 0x59a0), not the MSR copy.
 *
 * Prototype. Spec: docs/specs/kernel-mmio-rapl.md. Opt-in per device (DMI table), read-only unless allow_write=1.
 * Independent of the SMF daemon: user space sees only a powercap zone.
 */
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/bitfield.h>
#include <linux/bits.h>
#include <linux/dmi.h>
#include <linux/intel_rapl.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/pci.h>
#include <linux/string.h>

MODULE_IMPORT_NS("INTEL_RAPL");

/* Host bridge 00:00.0: MCHBAR (64 bit, bit 0 = enable, address bits 41:15) [spec 4.1]. */
#define SRM_MCHBAR_LO		0x48
#define SRM_MCHBAR_HI		0x4c
#define SRM_MCHBAR_ADDR_MASK	GENMASK_ULL(41, 15)

/* The registers this module touches lie in one page of the MCHBAR [spec 4.2]. */
#define SRM_WINDOW_OFF		0x5000
#define SRM_WINDOW_SIZE		0x1000
#define SRM_REG_UNIT		0x5938
#define SRM_REG_LIMIT		0x59a0	/* PL1 bits 14:0, enable 15, clamp 16, window 23:17, lock 31; PL2 the same +32 */
#define SRM_REG_STATUS		0x593c
#define SRM_REG_PERF		0x58f0
#define SRM_REG_INFO		0x5930

/* Bits the zone must never write (lock bits of both halves) [spec 5.3]. */
#define SRM_LOCK_BITS		(BIT_ULL(31) | BIT_ULL(63))

/*
 * Bit groups a write may touch [spec 5.3]: PL1 (14:0), enable and clamp of PL1 (16:15), the PL1 time window (23:17), PL2 (46:32),
 * enable and clamp of PL2 (48:47). Not the PL2 time window (55:49): no Windows channel writes it.
 */
#define SRM_WRITABLE		(GENMASK_ULL(23, 0) | GENMASK_ULL(48, 32))
#define SRM_PL1_FIELD		GENMASK_ULL(14, 0)
#define SRM_PL2_FIELD		GENMASK_ULL(46, 32)
/* Smallest PL1/PL2 a write may set: 3 W, the smallest minimum of any blob of the corpus [D, spec 5.6]. */
#define SRM_MIN_LIMIT_W		3

enum srm_platform {
	SRM_NONE,
	SRM_ICELAKE,
	SRM_TIGERLAKE,
	SRM_ALDERLAKE,
	SRM_RAPTORLAKE,
	SRM_METEORLAKE,
	SRM_LUNARLAKE,
};

/* Fixed MCHBAR address per platform type as the Windows client assumes it [spec 3, docs/research/15]. */
static const struct {
	const char *name;
	u64 mchbar;
} srm_platforms[] = {
	[SRM_ICELAKE]	= { "icelake",   0xfed10000 },
	[SRM_TIGERLAKE]	= { "tigerlake", 0xfedc0000 },
	[SRM_ALDERLAKE]	= { "alderlake", 0xfedc0000 },
	[SRM_RAPTORLAKE] = { "raptorlake", 0xfedc0000 },
	[SRM_METEORLAKE] = { "meteorlake", 0xfedc0000 },
	[SRM_LUNARLAKE]	= { "lunarlake", 0xfedc0000 },
};

/* Opt-in table: only devices whose chain is proven [spec 6]. Add a device only with the evidence listed there. */
static const struct dmi_system_id srm_devices[] = {
	{
		.ident = "Surface Book 3 (15 inch)",
		.matches = {
			DMI_MATCH(DMI_SYS_VENDOR, "Microsoft Corporation"),
			DMI_EXACT_MATCH(DMI_PRODUCT_SKU, "Surface_Book_3_1899"),
		},
		.driver_data = (void *)SRM_ICELAKE,
	},
	{ }
};
/* No MODULE_DEVICE_TABLE in the prototype: the module is loaded by hand, never by an alias on every Microsoft machine [spec 4]. */

static bool allow_write;
module_param(allow_write, bool, 0444);
MODULE_PARM_DESC(allow_write, "Allow writes to the MMIO copy (default: read-only zone)");

static char *unsafe_platform;
module_param(unsafe_platform, charp, 0444);
MODULE_PARM_DESC(unsafe_platform, "Developers only: skip the DMI table and use this platform type (icelake, tigerlake, alderlake, raptorlake, meteorlake, lunarlake)");

static DEFINE_MUTEX(srm_lock);		/* serialises the read-modify-write of the limit register */
static bool srm_removing;		/* set before the zone is torn down: the core's own clean-up must not reach the register */
static u64 srm_saved;			/* the limit register as found at load [spec 5.5] */
static void __iomem *srm_map;
static struct rapl_if_priv srm_priv;
static struct rapl_package *srm_package;

/* The primitives the core needs for PL1/PL2, copied from the kernel's MMIO table (same bit positions) [spec 5.1]. */
static struct rapl_primitive_info srm_rpi[NR_RAPL_PRIMITIVES] = {
	[POWER_LIMIT1]	= PRIMITIVE_INFO_INIT(POWER_LIMIT1, GENMASK_ULL(14, 0), 0, RAPL_DOMAIN_REG_LIMIT, POWER_UNIT, 0),
	[POWER_LIMIT2]	= PRIMITIVE_INFO_INIT(POWER_LIMIT2, GENMASK_ULL(46, 32), 32, RAPL_DOMAIN_REG_LIMIT, POWER_UNIT, 0),
	[ENERGY_COUNTER] = PRIMITIVE_INFO_INIT(ENERGY_COUNTER, GENMASK_ULL(31, 0), 0, RAPL_DOMAIN_REG_STATUS, ENERGY_UNIT, 0),
	[FW_LOCK]	= PRIMITIVE_INFO_INIT(FW_LOCK, BIT_ULL(31), 31, RAPL_DOMAIN_REG_LIMIT, ARBITRARY_UNIT, 0),
	[FW_HIGH_LOCK]	= PRIMITIVE_INFO_INIT(FW_LOCK, BIT_ULL(63), 63, RAPL_DOMAIN_REG_LIMIT, ARBITRARY_UNIT, 0),
	[PL1_ENABLE]	= PRIMITIVE_INFO_INIT(PL1_ENABLE, BIT_ULL(15), 15, RAPL_DOMAIN_REG_LIMIT, ARBITRARY_UNIT, 0),
	[PL1_CLAMP]	= PRIMITIVE_INFO_INIT(PL1_CLAMP, BIT_ULL(16), 16, RAPL_DOMAIN_REG_LIMIT, ARBITRARY_UNIT, 0),
	[PL2_ENABLE]	= PRIMITIVE_INFO_INIT(PL2_ENABLE, BIT_ULL(47), 47, RAPL_DOMAIN_REG_LIMIT, ARBITRARY_UNIT, 0),
	[PL2_CLAMP]	= PRIMITIVE_INFO_INIT(PL2_CLAMP, BIT_ULL(48), 48, RAPL_DOMAIN_REG_LIMIT, ARBITRARY_UNIT, 0),
	[TIME_WINDOW1]	= PRIMITIVE_INFO_INIT(TIME_WINDOW1, GENMASK_ULL(23, 17), 17, RAPL_DOMAIN_REG_LIMIT, TIME_UNIT, 0),
	[TIME_WINDOW2]	= PRIMITIVE_INFO_INIT(TIME_WINDOW2, GENMASK_ULL(55, 49), 49, RAPL_DOMAIN_REG_LIMIT, TIME_UNIT, 0),
	[THERMAL_SPEC_POWER] = PRIMITIVE_INFO_INIT(THERMAL_SPEC_POWER, GENMASK_ULL(14, 0), 0, RAPL_DOMAIN_REG_INFO, POWER_UNIT, 0),
	[MAX_POWER]	= PRIMITIVE_INFO_INIT(MAX_POWER, GENMASK_ULL(46, 32), 32, RAPL_DOMAIN_REG_INFO, POWER_UNIT, 0),
	[MIN_POWER]	= PRIMITIVE_INFO_INIT(MIN_POWER, GENMASK_ULL(30, 16), 16, RAPL_DOMAIN_REG_INFO, POWER_UNIT, 0),
	[MAX_TIME_WINDOW] = PRIMITIVE_INFO_INIT(MAX_TIME_WINDOW, GENMASK_ULL(53, 48), 48, RAPL_DOMAIN_REG_INFO, TIME_UNIT, 0),
	[THROTTLED_TIME] = PRIMITIVE_INFO_INIT(THROTTLED_TIME, GENMASK_ULL(31, 0), 0, RAPL_DOMAIN_REG_PERF, TIME_UNIT, 0),
};

static const struct rapl_defaults srm_defaults = {
	.floor_freq_reg_addr = 0,
	.check_unit = rapl_default_check_unit,
	.set_floor_freq = rapl_default_set_floor_freq,
	.compute_time_window = rapl_default_compute_time_window,
};

static void __iomem *srm_reg(u32 off)
{
	return srm_map + (off - SRM_WINDOW_OFF);
}

static bool srm_is_reg(const struct reg_action *ra)
{
	return ra->reg.mmio >= srm_map && ra->reg.mmio < srm_map + SRM_WINDOW_SIZE - 7;
}

static int srm_read_raw(int id, struct reg_action *ra, bool pmu_ctx)
{
	if (!ra->reg.mmio || !srm_is_reg(ra))
		return -EINVAL;

	ra->value = readq(ra->reg.mmio) & ra->mask;
	return 0;
}

/* Raw value of `watts` in the power unit of the unit register, or 0 if the unit register is not sane. */
static u64 srm_watts_to_raw(unsigned int watts)
{
	u32 unit = readl(srm_reg(SRM_REG_UNIT)) & 0xf;

	if (!unit)
		return 0;
	return (u64)watts << unit;
}

static int srm_write_raw(int id, struct reg_action *ra)
{
	u64 val, new, min;
	int ret = 0;

	if (!allow_write || srm_removing)
		return -EPERM;
	/* Only the limit register, only the listed bit groups, never a lock bit [spec 5.3]. */
	if (ra->reg.mmio != srm_reg(SRM_REG_LIMIT) || (ra->mask & ~SRM_WRITABLE) || (ra->value & ~ra->mask) ||
	    ((ra->mask | ra->value) & SRM_LOCK_BITS)) {
		pr_warn_ratelimited("refused a write (mask %#llx value %#llx)\n", ra->mask, ra->value);
		return -EPERM;
	}

	mutex_lock(&srm_lock);
	val = readq(ra->reg.mmio);
	if (val & SRM_LOCK_BITS) {
		pr_warn_ratelimited("the limit register is locked by the firmware\n");
		ret = -EPERM;
		goto out;
	}
	new = (val & ~ra->mask) | ra->value;

	/* A limit must stay above the floor, and in a sane power unit [spec 5.6]. */
	min = srm_watts_to_raw(SRM_MIN_LIMIT_W);
	if ((ra->mask & SRM_PL1_FIELD) && (!min || FIELD_GET(SRM_PL1_FIELD, new) < min))
		ret = -EINVAL;
	if ((ra->mask & SRM_PL2_FIELD) && (!min || FIELD_GET(SRM_PL2_FIELD, new) < min))
		ret = -EINVAL;
	if (ret) {
		pr_warn_ratelimited("refused a limit below %u W\n", SRM_MIN_LIMIT_W);
		goto out;
	}
	writeq(new, ra->reg.mmio);
out:
	mutex_unlock(&srm_lock);
	return ret;
}

static enum srm_platform srm_select_platform(void)
{
	const struct dmi_system_id *id;
	int i;

	if (unsafe_platform) {
		for (i = 1; i < ARRAY_SIZE(srm_platforms); i++)
			if (!strcmp(unsafe_platform, srm_platforms[i].name)) {
				pr_warn("device taken from the unsafe_platform parameter, not from the opt-in table\n");
				add_taint(TAINT_CPU_OUT_OF_SPEC, LOCKDEP_STILL_OK);
				return i;
			}
		pr_err("unknown platform '%s'\n", unsafe_platform);
		return SRM_NONE;
	}
	id = dmi_first_match(srm_devices);
	return id ? (enum srm_platform)(uintptr_t)id->driver_data : SRM_NONE;
}

/* The MCHBAR of the host bridge, checked against the fixed address Windows assumes for this platform type [spec 4.1]. */
static int srm_find_mchbar(enum srm_platform platform, u64 *base)
{
	struct pci_dev *bridge;
	u32 lo, hi;
	u64 raw;

	bridge = pci_get_domain_bus_and_slot(0, 0, PCI_DEVFN(0, 0));
	if (!bridge)
		return -ENODEV;
	if (bridge->vendor != PCI_VENDOR_ID_INTEL) {
		pci_dev_put(bridge);
		return -ENODEV;
	}
	pci_read_config_dword(bridge, SRM_MCHBAR_LO, &lo);
	pci_read_config_dword(bridge, SRM_MCHBAR_HI, &hi);
	pci_dev_put(bridge);

	raw = ((u64)hi << 32) | lo;
	if (!(raw & BIT(0))) {
		pr_err("MCHBAR is not enabled\n");
		return -ENODEV;
	}
	*base = raw & SRM_MCHBAR_ADDR_MASK;
	if (*base != srm_platforms[platform].mchbar) {
		pr_err("MCHBAR %#llx differs from %#llx expected for %s, not mapping\n", *base,
		       srm_platforms[platform].mchbar, srm_platforms[platform].name);
		return -ENODEV;
	}
	return 0;
}

static int __init srm_init(void)
{
	enum srm_platform platform;
	struct pci_dev *native;
	u64 mchbar;
	int ret;

	platform = srm_select_platform();
	if (platform == SRM_NONE) {
		pr_debug("device not in the opt-in table\n");
		return -ENODEV;
	}

	/* If the processor thermal device is visible, the kernel's own driver provides intel-rapl-mmio. */
	native = pci_get_domain_bus_and_slot(0, 0, PCI_DEVFN(4, 0));
	if (native) {
		pci_dev_put(native);
		pr_info("processor thermal device 00:04.0 is present, nothing to do\n");
		return -ENODEV;
	}

	ret = srm_find_mchbar(platform, &mchbar);
	if (ret)
		return ret;

	srm_map = ioremap(mchbar + SRM_WINDOW_OFF, SRM_WINDOW_SIZE);
	if (!srm_map)
		return -ENOMEM;

	if (!(readl(srm_reg(SRM_REG_UNIT)) & 0xf)) {
		pr_err("the power unit register reads 0, not creating the zone\n");
		ret = -ENODEV;
		goto err_unmap;
	}
	srm_saved = readq(srm_reg(SRM_REG_LIMIT));
	pr_info("limit register at load: %#llx%s\n", srm_saved, srm_saved & SRM_LOCK_BITS ? " (locked)" : "");

	srm_priv.type = RAPL_IF_MMIO;
	srm_priv.reg_unit.mmio = srm_reg(SRM_REG_UNIT);
	srm_priv.regs[RAPL_DOMAIN_PACKAGE][RAPL_DOMAIN_REG_LIMIT].mmio = srm_reg(SRM_REG_LIMIT);
	srm_priv.regs[RAPL_DOMAIN_PACKAGE][RAPL_DOMAIN_REG_STATUS].mmio = srm_reg(SRM_REG_STATUS);
	srm_priv.regs[RAPL_DOMAIN_PACKAGE][RAPL_DOMAIN_REG_PERF].mmio = srm_reg(SRM_REG_PERF);
	srm_priv.regs[RAPL_DOMAIN_PACKAGE][RAPL_DOMAIN_REG_INFO].mmio = srm_reg(SRM_REG_INFO);
	/* PL1 and PL2 only: no PL4 (Windows writes MSR 0x601), no DRAM, no other domain [spec 5.2]. */
	srm_priv.limits[RAPL_DOMAIN_PACKAGE] = BIT(POWER_LIMIT2);
	srm_priv.read_raw = srm_read_raw;
	srm_priv.write_raw = srm_write_raw;
	srm_priv.defaults = &srm_defaults;
	srm_priv.rpi = srm_rpi;

	srm_priv.control_type = powercap_register_control_type(NULL, "intel-rapl-mmio", NULL);
	if (IS_ERR(srm_priv.control_type)) {
		ret = PTR_ERR(srm_priv.control_type);
		goto err_unmap;
	}

	srm_package = rapl_add_package(0, &srm_priv, false);
	if (IS_ERR(srm_package)) {
		ret = PTR_ERR(srm_package);
		goto err_ctl;
	}

	if (allow_write)
		add_taint(TAINT_CPU_OUT_OF_SPEC, LOCKDEP_STILL_OK);
	pr_info("intel-rapl-mmio zone created (%s, MCHBAR %#llx, %s)\n", srm_platforms[platform].name, mchbar,
		allow_write ? "writable" : "read-only");
	return 0;

err_ctl:
	powercap_unregister_control_type(srm_priv.control_type);
err_unmap:
	iounmap(srm_map);
	return ret;
}

static void __exit srm_exit(void)
{
	/*
	 * The core clears enable and clamp of every limit it knows when it removes a package; that must not reach the firmware's register
	 * (it would switch the firmware's limit off). So: refuse writes, tear down, then put back what was found at load [spec 5.5].
	 */
	srm_removing = true;
	rapl_remove_package(srm_package);
	powercap_unregister_control_type(srm_priv.control_type);
	if (allow_write) {
		mutex_lock(&srm_lock);
		if (readq(srm_reg(SRM_REG_LIMIT)) != srm_saved && !(readq(srm_reg(SRM_REG_LIMIT)) & SRM_LOCK_BITS))
			writeq(srm_saved, srm_reg(SRM_REG_LIMIT));
		mutex_unlock(&srm_lock);
		pr_info("limit register restored to %#llx\n", srm_saved);
	}
	iounmap(srm_map);
}

module_init(srm_init);
module_exit(srm_exit);

MODULE_DESCRIPTION("MMIO copy of the package power limits as a powercap zone on Surface devices (prototype)");
MODULE_LICENSE("GPL");
