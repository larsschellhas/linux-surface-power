# SPDX-License-Identifier: GPL-2.0-only
# Out-of-tree build against the running kernel's headers: make; sudo insmod surface_rapl_mmio.ko
# Build in a copy of this directory when the tree must stay clean (the kernel build writes next to the sources).
KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
