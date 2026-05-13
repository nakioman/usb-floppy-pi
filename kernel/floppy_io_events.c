// SPDX-License-Identifier: GPL-2.0+
/*
 * floppy_io_events.c — usb-floppy-pi: lightweight I/O event tracker.
 *
 * Phase 2.4 design choice: drive the piezo buzzer from userspace (Python),
 * not from the kernel. The kernel-side pwm_request() API was removed in
 * 6.x in favour of device-tree-bound pwm_get(), which doesn't fit a USB
 * gadget driver cleanly. So instead the kernel just publishes "did I/O
 * happen, where, and when" via cheap atomics, and the Python audio module
 * polls /sys/class/usb_floppy/usb-floppy-pi/{io_counter,last_io_lba,
 * last_io_write,last_io_us} at ~50 Hz to render the buzzer audio.
 *
 * See floppy_io_events.h for the field semantics.
 */
#include <linux/module.h>
#include <linux/atomic.h>
#include <linux/ktime.h>
#include "floppy_io_events.h"

static struct floppy_io_event_state *g_state;

void floppy_io_event_init(struct floppy_io_event_state *st)
{
	atomic64_set(&st->total_blocks, 0);
	atomic_set(&st->last_lba, 0);
	atomic_set(&st->last_is_write, 0);
	atomic64_set(&st->last_us, 0);
	g_state = st;
	pr_info("g_floppy: io_events tracker initialised\n");
}
EXPORT_SYMBOL_GPL(floppy_io_event_init);

void floppy_io_event_exit(struct floppy_io_event_state *st)
{
	if (g_state == st)
		g_state = NULL;
}
EXPORT_SYMBOL_GPL(floppy_io_event_exit);

void floppy_io_event_record(struct floppy_io_event_state *st,
			    u32 lba, u32 nblocks, bool is_write)
{
	if (!st)
		return;
	atomic64_add(nblocks, &st->total_blocks);
	atomic_set(&st->last_lba, (int)lba);
	atomic_set(&st->last_is_write, is_write ? 1 : 0);
	atomic64_set(&st->last_us, ktime_to_us(ktime_get()));
}
EXPORT_SYMBOL_GPL(floppy_io_event_record);

struct floppy_io_event_state *floppy_io_event_get(void)
{
	return g_state;
}
EXPORT_SYMBOL_GPL(floppy_io_event_get);

u64 floppy_io_event_total_blocks(struct floppy_io_event_state *st)
{
	return atomic64_read(&st->total_blocks);
}
EXPORT_SYMBOL_GPL(floppy_io_event_total_blocks);

u32 floppy_io_event_last_lba(struct floppy_io_event_state *st)
{
	return (u32)atomic_read(&st->last_lba);
}
EXPORT_SYMBOL_GPL(floppy_io_event_last_lba);

bool floppy_io_event_last_is_write(struct floppy_io_event_state *st)
{
	return atomic_read(&st->last_is_write) != 0;
}
EXPORT_SYMBOL_GPL(floppy_io_event_last_is_write);

u64 floppy_io_event_last_us(struct floppy_io_event_state *st)
{
	return (u64)atomic64_read(&st->last_us);
}
EXPORT_SYMBOL_GPL(floppy_io_event_last_us);
