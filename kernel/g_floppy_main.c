// SPDX-License-Identifier: GPL-2.0+
/*
 * mass_storage.c -- Mass Storage USB Gadget
 *
 * Copyright (C) 2003-2008 Alan Stern
 * Copyright (C) 2009 Samsung Electronics
 *                    Author: Michal Nazarewicz <mina86@mina86.com>
 * All rights reserved.
 */


/*
 * The Mass Storage Gadget acts as a USB Mass Storage device,
 * appearing to the host as a disk drive or as a CD-ROM drive.  In
 * addition to providing an example of a genuinely useful gadget
 * driver for a USB device, it also illustrates a technique of
 * double-buffering for increased throughput.  Last but not least, it
 * gives an easy way to probe the behavior of the Mass Storage drivers
 * in a USB host.
 *
 * Since this file serves only administrative purposes and all the
 * business logic is implemented in f_mass_storage.* file.  Read
 * comments in this file for more detailed description.
 */


#include <linux/kernel.h>
#include <linux/usb/ch9.h>
#include <linux/module.h>
#include <linux/device.h>
#include <linux/sysfs.h>
#include "storage_common.h"
#include "floppy_throttle.h"
#include "floppy_io_events.h"

/*-------------------------------------------------------------------------*/

#define DRIVER_DESC		"USB Floppy Pi Gadget"
#define DRIVER_VERSION		"0.1.0"

/*
 * Thanks to NetChip Technologies for donating this product ID.
 *
 * DO NOT REUSE THESE IDs with any other driver!!  Ever!!
 * Instead:  allocate your own, using normal USB-IF procedures.
 */
#define FSG_VENDOR_ID	0x0525	/* NetChip */
#define FSG_PRODUCT_ID	0xa4a5	/* Linux-USB File-backed Storage Gadget */

#include "f_floppy.h"

/*-------------------------------------------------------------------------*/
USB_GADGET_COMPOSITE_OPTIONS();

static struct usb_device_descriptor msg_device_desc = {
	.bLength =		sizeof msg_device_desc,
	.bDescriptorType =	USB_DT_DEVICE,

	/* .bcdUSB = DYNAMIC */
	.bDeviceClass =		USB_CLASS_PER_INTERFACE,

	/* Vendor and product id can be overridden by module parameters.  */
	.idVendor =		cpu_to_le16(FSG_VENDOR_ID),
	.idProduct =		cpu_to_le16(FSG_PRODUCT_ID),
	.bNumConfigurations =	1,
};

static const struct usb_descriptor_header *otg_desc[2];

static struct usb_string strings_dev[] = {
	[USB_GADGET_MANUFACTURER_IDX].s = "",
	[USB_GADGET_PRODUCT_IDX].s = DRIVER_DESC,
	[USB_GADGET_SERIAL_IDX].s = "",
	{  } /* end of list */
};

static struct usb_gadget_strings stringtab_dev = {
	.language       = 0x0409,       /* en-us */
	.strings        = strings_dev,
};

static struct usb_gadget_strings *dev_strings[] = {
	&stringtab_dev,
	NULL,
};

static struct usb_function_instance *fi_msg;
static struct usb_function *f_msg;

/* === usb-floppy-pi sysfs class ===
 * Exposes /sys/class/usb_floppy/usb-floppy-pi/{lun0_file,lun0_ro,lun0_inquiry_string}
 * for userspace runtime control. Each show/store callback delegates to the
 * floppy_lun_xxx() bridges declared in f_floppy.h, which in turn call the
 * exported fsg_show_X / fsg_store_X helpers from storage_common.c with the
 * filesem from the active fsg_common (which is private to f_floppy.c).
 */

static struct class *usb_floppy_class;
static struct device *usb_floppy_dev;

/* fsg_common is private to f_floppy.c, so we go through bridges declared
 * in f_floppy.h (floppy_active_lun0, floppy_lun_*) instead of touching the
 * fsg_common layout directly. */

static ssize_t lun0_file_show(struct device *d, struct device_attribute *a,
			       char *buf)
{
	struct fsg_lun *lun = floppy_active_lun0();
	if (!lun) return scnprintf(buf, PAGE_SIZE, "\n");
	return floppy_lun_show_file(lun, buf);
}

static ssize_t lun0_file_store(struct device *d, struct device_attribute *a,
				const char *buf, size_t count)
{
	struct fsg_lun *lun = floppy_active_lun0();
	if (!lun) return -ENODEV;
	return floppy_lun_store_file(lun, buf, count);
}
static DEVICE_ATTR_RW(lun0_file);

static ssize_t lun0_ro_show(struct device *d, struct device_attribute *a,
			     char *buf)
{
	struct fsg_lun *lun = floppy_active_lun0();
	if (!lun) return scnprintf(buf, PAGE_SIZE, "0\n");
	return fsg_show_ro(lun, buf);
}

static ssize_t lun0_ro_store(struct device *d, struct device_attribute *a,
			      const char *buf, size_t count)
{
	struct fsg_lun *lun = floppy_active_lun0();
	if (!lun) return -ENODEV;
	return floppy_lun_store_ro(lun, buf, count);
}
static DEVICE_ATTR_RW(lun0_ro);

static ssize_t lun0_inquiry_string_show(struct device *d,
				struct device_attribute *a, char *buf)
{
	struct fsg_lun *lun = floppy_active_lun0();
	if (!lun) return scnprintf(buf, PAGE_SIZE, "\n");
	return fsg_show_inquiry_string(lun, buf);
}

static ssize_t lun0_inquiry_string_store(struct device *d,
				struct device_attribute *a,
				const char *buf, size_t count)
{
	struct fsg_lun *lun = floppy_active_lun0();
	if (!lun) return -ENODEV;
	return fsg_store_inquiry_string(lun, buf, count);
}
static DEVICE_ATTR_RW(lun0_inquiry_string);

/* Speed preset (rw) — switches between floppy-real / floppy-fast / unthrottled
 * at runtime. Three derived read-only attrs (speed_read_kbps, speed_write_kbps,
 * seek_us) reflect the resolved values for inspection / scripting. */

static ssize_t speed_preset_show(struct device *d, struct device_attribute *a,
				  char *buf)
{
	return floppy_throttle_show_preset(floppy_throttle_get(), buf);
}

static ssize_t speed_preset_store(struct device *d, struct device_attribute *a,
				   const char *buf, size_t count)
{
	int err = floppy_throttle_set_preset(floppy_throttle_get(), buf);
	return err ? err : count;
}
static DEVICE_ATTR_RW(speed_preset);

static ssize_t speed_read_kbps_show(struct device *d,
				    struct device_attribute *a, char *buf)
{
	struct floppy_throttle_state *st = floppy_throttle_get();
	return scnprintf(buf, PAGE_SIZE, "%u\n",
			 st ? floppy_throttle_read_kbps(st) : 0);
}
static DEVICE_ATTR_RO(speed_read_kbps);

static ssize_t speed_write_kbps_show(struct device *d,
				     struct device_attribute *a, char *buf)
{
	struct floppy_throttle_state *st = floppy_throttle_get();
	return scnprintf(buf, PAGE_SIZE, "%u\n",
			 st ? floppy_throttle_write_kbps(st) : 0);
}
static DEVICE_ATTR_RO(speed_write_kbps);

static ssize_t seek_us_show(struct device *d,
			    struct device_attribute *a, char *buf)
{
	struct floppy_throttle_state *st = floppy_throttle_get();
	return scnprintf(buf, PAGE_SIZE, "%u\n",
			 st ? floppy_throttle_seek_us(st) : 0);
}
static DEVICE_ATTR_RO(seek_us);

/* I/O event tracking (Phase 2.4): cheap atomic counters fed from do_read /
 * do_write in f_floppy.c. Polled by the userspace audio service to drive
 * the piezo buzzer (motor on/off, seek clicks). All read-only — the only
 * writer is the gadget I/O kthread. */

static ssize_t io_counter_show(struct device *d, struct device_attribute *a,
			       char *buf)
{
	struct floppy_io_event_state *st = floppy_io_event_get();
	return scnprintf(buf, PAGE_SIZE, "%llu\n",
			 st ? floppy_io_event_total_blocks(st) : 0ULL);
}
static DEVICE_ATTR_RO(io_counter);

static ssize_t last_io_lba_show(struct device *d, struct device_attribute *a,
				 char *buf)
{
	struct floppy_io_event_state *st = floppy_io_event_get();
	return scnprintf(buf, PAGE_SIZE, "%u\n",
			 st ? floppy_io_event_last_lba(st) : 0u);
}
static DEVICE_ATTR_RO(last_io_lba);

static ssize_t last_io_write_show(struct device *d, struct device_attribute *a,
				   char *buf)
{
	struct floppy_io_event_state *st = floppy_io_event_get();
	return scnprintf(buf, PAGE_SIZE, "%d\n",
			 st && floppy_io_event_last_is_write(st) ? 1 : 0);
}
static DEVICE_ATTR_RO(last_io_write);

static ssize_t last_io_us_show(struct device *d, struct device_attribute *a,
				char *buf)
{
	struct floppy_io_event_state *st = floppy_io_event_get();
	return scnprintf(buf, PAGE_SIZE, "%llu\n",
			 st ? floppy_io_event_last_us(st) : 0ULL);
}
static DEVICE_ATTR_RO(last_io_us);

/* track_crossings (RO) — cumulative count of track-boundary crossings the
 * gadget driver has observed, including crossings WITHIN a single
 * multi-sector request. Userspace audio polls this; every increment is
 * one real seek and should produce one stepper-motor click. */
static ssize_t track_crossings_show(struct device *d,
				     struct device_attribute *a, char *buf)
{
	struct floppy_io_event_state *st = floppy_io_event_get();
	return scnprintf(buf, PAGE_SIZE, "%llu\n",
			 st ? floppy_io_event_track_crossings(st) : 0ULL);
}
static DEVICE_ATTR_RO(track_crossings);

static struct attribute *usb_floppy_attrs[] = {
	&dev_attr_lun0_file.attr,
	&dev_attr_lun0_ro.attr,
	&dev_attr_lun0_inquiry_string.attr,
	&dev_attr_speed_preset.attr,
	&dev_attr_speed_read_kbps.attr,
	&dev_attr_speed_write_kbps.attr,
	&dev_attr_seek_us.attr,
	&dev_attr_io_counter.attr,
	&dev_attr_last_io_lba.attr,
	&dev_attr_last_io_write.attr,
	&dev_attr_last_io_us.attr,
	&dev_attr_track_crossings.attr,
	NULL,
};

static const struct attribute_group usb_floppy_group = {
	.attrs = usb_floppy_attrs,
};
static const struct attribute_group *usb_floppy_groups[] = {
	&usb_floppy_group,
	NULL,
};

static int usb_floppy_sysfs_register(void)
{
	int err;

	usb_floppy_class = class_create("usb_floppy");
	if (IS_ERR(usb_floppy_class))
		return PTR_ERR(usb_floppy_class);

	usb_floppy_dev = device_create_with_groups(usb_floppy_class, NULL,
						   MKDEV(0, 0), NULL,
						   usb_floppy_groups,
						   "usb-floppy-pi");
	if (IS_ERR(usb_floppy_dev)) {
		err = PTR_ERR(usb_floppy_dev);
		usb_floppy_dev = NULL;
		class_destroy(usb_floppy_class);
		usb_floppy_class = NULL;
		return err;
	}

	/* Plug the sysfs kobject into io_events so do_read/do_write can fire
	 * sysfs_notify on track_crossings — lets userspace poll() POLLPRI
	 * instead of busy-polling 50 Hz. Safe to call even if io_events
	 * hasn't been init'd yet (it'll be reset on first fsg_alloc_inst). */
	{
		struct floppy_io_event_state *iost = floppy_io_event_get();
		if (iost)
			floppy_io_event_set_notify_kobj(iost,
							&usb_floppy_dev->kobj);
	}

	pr_info("g_floppy: /sys/class/usb_floppy/usb-floppy-pi registered\n");
	return 0;
}

static void usb_floppy_sysfs_unregister(void)
{
	/* Drop the kobj pointer before destroying the device so a racing
	 * I/O notify doesn't dereference freed memory. */
	{
		struct floppy_io_event_state *iost = floppy_io_event_get();
		if (iost)
			floppy_io_event_set_notify_kobj(iost, NULL);
	}

	if (usb_floppy_dev) {
		device_destroy(usb_floppy_class, MKDEV(0, 0));
		usb_floppy_dev = NULL;
	}
	if (usb_floppy_class) {
		class_destroy(usb_floppy_class);
		usb_floppy_class = NULL;
	}
}
/* === end usb-floppy-pi sysfs class === */

/****************************** Configurations ******************************/

static struct fsg_module_parameters mod_data = {
	.stall = 1
};
#ifdef CONFIG_USB_GADGET_DEBUG_FILES

static unsigned int fsg_num_buffers = CONFIG_USB_GADGET_STORAGE_NUM_BUFFERS;

#else

/*
 * Number of buffers we will use.
 * 2 is usually enough for good buffering pipeline
 */
#define fsg_num_buffers	CONFIG_USB_GADGET_STORAGE_NUM_BUFFERS

#endif /* CONFIG_USB_GADGET_DEBUG_FILES */

FSG_MODULE_PARAMETERS(/* no prefix */, mod_data);

/* usb-floppy-pi: speed preset module param. Resolved at the end of msg_bind
 * after fi_msg + the throttle state are both initialised. Default mirrors
 * the throttle's own boot default (floppy-real). */
static char *speed_preset_param = "floppy-real";
module_param_named(speed_preset, speed_preset_param, charp, 0444);
MODULE_PARM_DESC(speed_preset,
	"Initial speed preset: floppy-real (default, ~50 KB/s) | "
	"floppy-fast (~200 KB/s) | unthrottled");

static int msg_do_config(struct usb_configuration *c)
{
	int ret;

	if (gadget_is_otg(c->cdev->gadget)) {
		c->descriptors = otg_desc;
		c->bmAttributes |= USB_CONFIG_ATT_WAKEUP;
	}

	f_msg = usb_get_function(fi_msg);
	if (IS_ERR(f_msg))
		return PTR_ERR(f_msg);

	ret = usb_add_function(c, f_msg);
	if (ret)
		goto put_func;

	return 0;

put_func:
	usb_put_function(f_msg);
	return ret;
}

static struct usb_configuration msg_config_driver = {
	.label			= "Linux File-Backed Storage",
	.bConfigurationValue	= 1,
	.bmAttributes		= USB_CONFIG_ATT_SELFPOWER,
};


/****************************** Gadget Bind ******************************/

static int msg_bind(struct usb_composite_dev *cdev)
{
	struct fsg_opts *opts;
	struct fsg_config config;
	int status;

	fi_msg = usb_get_function_instance("floppy");
	if (IS_ERR(fi_msg))
		return PTR_ERR(fi_msg);

	fsg_config_from_params(&config, &mod_data, fsg_num_buffers);
	opts = fsg_opts_from_func_inst(fi_msg);

	opts->no_configfs = true;
	status = fsg_common_set_num_buffers(opts->common, fsg_num_buffers);
	if (status)
		goto fail;

	status = fsg_common_set_cdev(opts->common, cdev, config.can_stall);
	if (status)
		goto fail_set_cdev;

	fsg_common_set_sysfs(opts->common, true);
	status = fsg_common_create_luns(opts->common, &config);
	if (status)
		goto fail_set_cdev;

	fsg_common_set_inquiry_string(opts->common, config.vendor_name,
				      config.product_name);

	status = usb_string_ids_tab(cdev, strings_dev);
	if (status < 0)
		goto fail_string_ids;
	msg_device_desc.iProduct = strings_dev[USB_GADGET_PRODUCT_IDX].id;

	if (gadget_is_otg(cdev->gadget) && !otg_desc[0]) {
		struct usb_descriptor_header *usb_desc;

		usb_desc = usb_otg_descriptor_alloc(cdev->gadget);
		if (!usb_desc) {
			status = -ENOMEM;
			goto fail_string_ids;
		}
		usb_otg_descriptor_init(cdev->gadget, usb_desc);
		otg_desc[0] = usb_desc;
		otg_desc[1] = NULL;
	}

	status = usb_add_config(cdev, &msg_config_driver, msg_do_config);
	if (status < 0)
		goto fail_otg_desc;

	usb_composite_overwrite_options(cdev, &coverwrite);
	dev_info(&cdev->gadget->dev,
		 DRIVER_DESC ", version: " DRIVER_VERSION "\n");

	/* usb-floppy-pi: register the sysfs class so userspace can talk to us. */
	{
		int sysfs_err = usb_floppy_sysfs_register();
		if (sysfs_err)
			dev_warn(&cdev->gadget->dev,
				"g_floppy: sysfs class registration failed (%d), continuing\n",
				sysfs_err);
	}

	/* usb-floppy-pi: apply the user-selected initial speed preset. The
	 * throttle was init'd by usb_f_floppy.ko with default "floppy-real";
	 * if the user passed a different preset on the cmdline we switch now. */
	if (strcmp(speed_preset_param, "floppy-real") != 0) {
		int err = floppy_throttle_set_preset(floppy_throttle_get(),
						     speed_preset_param);
		if (err)
			dev_warn(&cdev->gadget->dev,
				"g_floppy: bad speed_preset='%s', staying on floppy-real\n",
				speed_preset_param);
	}
	return 0;

fail_otg_desc:
	kfree(otg_desc[0]);
	otg_desc[0] = NULL;
fail_string_ids:
	fsg_common_remove_luns(opts->common);
fail_set_cdev:
	fsg_common_free_buffers(opts->common);
fail:
	usb_put_function_instance(fi_msg);
	return status;
}

static int msg_unbind(struct usb_composite_dev *cdev)
{
	/* usb-floppy-pi: unregister sysfs class first so userspace can't access
	 * it during the teardown of fi_msg/f_msg. */
	usb_floppy_sysfs_unregister();

	if (!IS_ERR(f_msg))
		usb_put_function(f_msg);

	if (!IS_ERR(fi_msg))
		usb_put_function_instance(fi_msg);

	kfree(otg_desc[0]);
	otg_desc[0] = NULL;

	return 0;
}

/****************************** Some noise ******************************/

static struct usb_composite_driver msg_driver = {
	.name		= "g_floppy",
	.dev		= &msg_device_desc,
	.max_speed	= USB_SPEED_SUPER_PLUS,
	.needs_serial	= 1,
	.strings	= dev_strings,
	.bind		= msg_bind,
	.unbind		= msg_unbind,
};

module_usb_composite_driver(msg_driver);

MODULE_DESCRIPTION(DRIVER_DESC);
MODULE_AUTHOR("Michal Nazarewicz");
MODULE_LICENSE("GPL");
