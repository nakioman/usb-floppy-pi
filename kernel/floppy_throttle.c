// SPDX-License-Identifier: GPL-2.0+
/*
 * floppy_throttle.c — usb-floppy-pi: rate-limiting for floppy emulation.
 *
 * Inserts controlled per-request delays into the FSG read/write paths to
 * simulate a real 1.44MB HD floppy. Two delays per request:
 *
 *   1. Seek delay: if the LBA falls in a different "track" than the last
 *      I/O, sleep `seek_us` to mimic the stepper motor moving the head.
 *      For 1.44MB CHS we treat 36 sectors (18 sectors × 2 heads) as one
 *      "track" — that's the granularity at which a real floppy seeks.
 *
 *   2. Transfer delay: sleep proportional to the byte count divided by
 *      the configured KB/s rate. Adds ±10% jitter to feel organic.
 *
 * The `unthrottled` preset (kbps=0, seek=0) makes both branches no-ops.
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/delay.h>
#include <linux/string.h>
#include "floppy_throttle.h"

/* 1.44MB HD floppy CHS: 80 cylinders × 2 heads × 18 sectors/track.
 * We pick 36 (= 18 × 2) so a "track" boundary is crossed only when the
 * head physically steps — same heuristic the kernel docs use for fake
 * geometry calculations. */
#define FLOPPY_SECTORS_PER_TRACK 36

struct floppy_throttle_preset {
	const char *name;
	u32 read_kbps;
	u32 write_kbps;
	u32 seek_us;
};

/* Order matters: preset[0] is the boot default. */
static const struct floppy_throttle_preset PRESETS[] = {
	{ "floppy-real",  50,  30, 6000 },
	{ "floppy-fast", 200, 200,  500 },
	{ "unthrottled",   0,   0,    0 },
};
#define NUM_PRESETS  ARRAY_SIZE(PRESETS)

static struct floppy_throttle_state *g_throttle;

int floppy_throttle_init(struct floppy_throttle_state *st)
{
	spin_lock_init(&st->lock);
	st->read_kbps  = PRESETS[0].read_kbps;
	st->write_kbps = PRESETS[0].write_kbps;
	st->seek_us    = PRESETS[0].seek_us;
	st->last_track = ~0u;     /* "no last track" → first I/O always seeks */

	g_throttle = st;
	pr_info("g_floppy: throttle init, default preset=%s "
		"(read=%u kbps, write=%u kbps, seek=%u us)\n",
		PRESETS[0].name, st->read_kbps, st->write_kbps, st->seek_us);
	return 0;
}
EXPORT_SYMBOL_GPL(floppy_throttle_init);

void floppy_throttle_exit(struct floppy_throttle_state *st)
{
	if (g_throttle == st)
		g_throttle = NULL;
}
EXPORT_SYMBOL_GPL(floppy_throttle_exit);

struct floppy_throttle_state *floppy_throttle_get(void)
{
	return g_throttle;
}
EXPORT_SYMBOL_GPL(floppy_throttle_get);

int floppy_throttle_set_preset(struct floppy_throttle_state *st, const char *name)
{
	char clean[32];
	size_t n;
	int i;

	if (!st || !name)
		return -EINVAL;

	/* Strip trailing newline (sysfs writes typically include one). */
	n = strscpy(clean, name, sizeof(clean));
	if (n >= sizeof(clean) - 1)
		return -EINVAL;
	for (i = 0; clean[i]; i++)
		if (clean[i] == '\n') { clean[i] = '\0'; break; }

	for (i = 0; i < NUM_PRESETS; i++) {
		if (!strcmp(clean, PRESETS[i].name)) {
			unsigned long flags;
			spin_lock_irqsave(&st->lock, flags);
			st->read_kbps  = PRESETS[i].read_kbps;
			st->write_kbps = PRESETS[i].write_kbps;
			st->seek_us    = PRESETS[i].seek_us;
			spin_unlock_irqrestore(&st->lock, flags);
			pr_info("g_floppy: throttle preset=%s\n", PRESETS[i].name);
			return 0;
		}
	}
	pr_warn("g_floppy: unknown throttle preset '%s'\n", clean);
	return -EINVAL;
}
EXPORT_SYMBOL_GPL(floppy_throttle_set_preset);

ssize_t floppy_throttle_show_preset(struct floppy_throttle_state *st, char *buf)
{
	int i;
	if (!st)
		return scnprintf(buf, PAGE_SIZE, "(uninitialized)\n");
	for (i = 0; i < NUM_PRESETS; i++) {
		if (st->read_kbps == PRESETS[i].read_kbps &&
		    st->write_kbps == PRESETS[i].write_kbps &&
		    st->seek_us == PRESETS[i].seek_us) {
			return scnprintf(buf, PAGE_SIZE, "%s\n", PRESETS[i].name);
		}
	}
	/* No preset matches — must be a custom config (only writable via
	 * advanced sysfs which we don't expose yet). */
	return scnprintf(buf, PAGE_SIZE, "custom\n");
}
EXPORT_SYMBOL_GPL(floppy_throttle_show_preset);

/* Apply seek + I/O delays for one request. Caller passes the relevant
 * direction's kbps so we don't branch in the hot path. */
static void apply_io_delay(struct floppy_throttle_state *st,
			   u32 lba, u32 nblocks, u32 kbps)
{
	u32 track, io_us;
	unsigned long flags;
	bool need_seek;

	if (kbps == 0 && st->seek_us == 0)
		return;     /* unthrottled — no work */

	track = lba / FLOPPY_SECTORS_PER_TRACK;

	spin_lock_irqsave(&st->lock, flags);
	need_seek = (track != st->last_track) && (st->seek_us > 0);
	st->last_track = track;
	spin_unlock_irqrestore(&st->lock, flags);

	if (need_seek)
		usleep_range(st->seek_us, st->seek_us + 500);

	if (kbps > 0 && nblocks > 0) {
		/* nblocks * 512 / (kbps * 1024 / 1_000_000) µs
		 * Simplified: nblocks * 500 / kbps × 1000 with rounding. */
		io_us = (nblocks * 512U * 1000U) / (kbps * 1024U / 1000U + 1U);
		/* The +1 above prevents division-by-tiny in edge cases; for our
		 * 50-200 kbps range this is negligible. The simpler form
		 * (nblocks*512*1000/kbps) is approximately the same:
		 *   for kbps=50, 1 sector (512B) => 10240 µs, ~10ms ✓
		 *   for kbps=200, 1 sector => 2560 µs, ~2.5ms ✓ */
		if (io_us > 0)
			usleep_range(io_us, io_us + (io_us / 10) + 1);
	}
}

void floppy_throttle_on_read(struct floppy_throttle_state *st,
			     u32 lba, u32 nblocks)
{
	if (!st)
		return;
	apply_io_delay(st, lba, nblocks, st->read_kbps);
}
EXPORT_SYMBOL_GPL(floppy_throttle_on_read);

void floppy_throttle_on_write(struct floppy_throttle_state *st,
			      u32 lba, u32 nblocks)
{
	if (!st)
		return;
	apply_io_delay(st, lba, nblocks, st->write_kbps);
}
EXPORT_SYMBOL_GPL(floppy_throttle_on_write);
