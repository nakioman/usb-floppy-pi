/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef FLOPPY_IO_EVENTS_H
#define FLOPPY_IO_EVENTS_H

#include <linux/types.h>
#include <linux/atomic.h>

/*
 * usb-floppy-pi: lightweight I/O event tracker for the userspace buzzer.
 *
 * Updated from do_read / do_write in f_floppy.c on every gadget I/O. Read
 * by userspace via /sys/class/usb_floppy/usb-floppy-pi/{io_counter,
 * last_io_lba, last_io_write, last_io_us}. The Python audio service polls
 * these at ~50 Hz to drive the piezo buzzer via the kernel PWM sysfs:
 *
 *   - counter delta > 0 between polls → motor running (start spin-up if
 *     coming from idle)
 *   - counter stable for N polls → motor idle (trigger spin-down)
 *   - LBA jump beyond threshold → seek click
 *   - is_write toggles between polls → no semantic effect today, but
 *     gives the userspace daemon room for write-specific sounds
 *
 * Atomics-only, no locks: the only writer is the gadget I/O kthread, the
 * only readers are userspace via sysfs (which uses single reads of each
 * attribute). Per-field consistency is sufficient — readers don't need
 * an atomic snapshot of all four fields together.
 */

struct floppy_io_event_state {
	atomic64_t	total_blocks;	/* cumulative blocks ever transferred */
	atomic_t	last_lba;	/* LBA of most recent I/O */
	atomic_t	last_is_write;	/* 1=write, 0=read */
	atomic64_t	last_us;	/* CLOCK_MONOTONIC microseconds */
};

void floppy_io_event_init(struct floppy_io_event_state *st);
void floppy_io_event_exit(struct floppy_io_event_state *st);

/* Hot-path: 4 atomic stores. Called from do_read / do_write right after
 * the throttle hook. `nblocks` is the I/O size in blocks (sectors). */
void floppy_io_event_record(struct floppy_io_event_state *st,
			    u32 lba, u32 nblocks, bool is_write);

/* Singleton accessor for sysfs callbacks living in g_floppy_main.c. */
struct floppy_io_event_state *floppy_io_event_get(void);

u64  floppy_io_event_total_blocks(struct floppy_io_event_state *st);
u32  floppy_io_event_last_lba(struct floppy_io_event_state *st);
bool floppy_io_event_last_is_write(struct floppy_io_event_state *st);
u64  floppy_io_event_last_us(struct floppy_io_event_state *st);

#endif /* FLOPPY_IO_EVENTS_H */
