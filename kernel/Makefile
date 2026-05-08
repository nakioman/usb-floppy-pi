# Out-of-tree Kbuild Makefile for usb-floppy-pi kernel modules.
#
# Builds two .ko files, mirroring the upstream layout:
#   usb_f_floppy.ko  — USB gadget function (forked from f_mass_storage)
#   g_floppy.ko      — single-purpose gadget that loads the function
#
# `modprobe g_floppy` auto-loads usb_f_floppy via depmod (the legacy gadget
# requests the function instance by name "floppy" at bind time).

obj-m := usb_f_floppy.o g_floppy.o

usb_f_floppy-y := f_floppy.o storage_common.o floppy_throttle.o
g_floppy-y     := g_floppy_main.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

modules_install:
	$(MAKE) -C $(KDIR) M=$(PWD) modules_install

.PHONY: all clean modules_install
