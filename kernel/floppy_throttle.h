/* SPDX-License-Identifier: GPL-2.0+ */
/*
 * floppy_throttle.h — usb-floppy-pi: rate-limiting for floppy emulation.
 *
 * Hooks called from f_floppy.c's do_read/do_write paths to introduce
 * controlled delays that simulate the speed of a real 1.44MB HD floppy
 * (and its track-stepping seek time). Three named presets:
 *
 *   floppy-real (default): 50 KB/s read, 30 KB/s write, 6ms seek
 *   floppy-fast:           200 KB/s read, 200 KB/s write, 0.5ms seek
 *   unthrottled:           bypass — native USB speed, ~5 MB/s
 *
 * Implementation uses usleep_range() in the FSG kthread context, which
 * yields CPU during waits (no busy-spin) and is reasonably accurate
 * for our µs-to-ms timescales.
 */
#ifndef FLOPPY_THROTTLE_H
#define FLOPPY_THROTTLE_H

#include <linux/types.h>
#include <linux/spinlock.h>

struct floppy_throttle_state {
	u32 read_kbps;     /* 0 = bypass */
	u32 write_kbps;    /* 0 = bypass */
	u32 seek_us;       /* simulated track-change latency; 0 = no seek delay */
	u32 last_track;    /* set to ~0u to mark "first I/O always seeks" */
	spinlock_t lock;   /* protects the kbps/seek_us/last_track tuple */
};

/* Lifecycle */
int  floppy_throttle_init(struct floppy_throttle_state *st);
void floppy_throttle_exit(struct floppy_throttle_state *st);

/* Hooks called from f_floppy.c's do_read/do_write */
void floppy_throttle_on_read(struct floppy_throttle_state *st,
			     u32 lba, u32 nblocks);
void floppy_throttle_on_write(struct floppy_throttle_state *st,
			      u32 lba, u32 nblocks);

/* Configuration via preset name. Returns 0 on success or -EINVAL. */
int floppy_throttle_set_preset(struct floppy_throttle_state *st,
			       const char *name);

/* For the sysfs `speed_preset` show callback. */
ssize_t floppy_throttle_show_preset(struct floppy_throttle_state *st, char *buf);

/* For sysfs read-only derived attributes. */
static inline u32 floppy_throttle_read_kbps(const struct floppy_throttle_state *st)
	{ return st->read_kbps; }
static inline u32 floppy_throttle_write_kbps(const struct floppy_throttle_state *st)
	{ return st->write_kbps; }
static inline u32 floppy_throttle_seek_us(const struct floppy_throttle_state *st)
	{ return st->seek_us; }

/* Module-level singleton accessor — lets sysfs callbacks in g_floppy.ko reach
 * the throttle state without passing it through the kernel function instance. */
struct floppy_throttle_state *floppy_throttle_get(void);

#endif /* FLOPPY_THROTTLE_H */
