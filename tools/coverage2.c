/*
 * Real-time TB coverage collector TCG plugin
 *
 * Tracks translated blocks and writes the per-TB counters to
 * logs/coverage.log as CSV rows:
 *
 *     virt_addr,trans_count,exec_count,insn_count,first_insn
 *
 * Only blocks whose start vaddr passes the address rules (see the
 * LOG_VADDR_MIN block below: vaddr above the cutoff AND low 32 bits
 * in [0x80000000, 0x8fffffff], the KSEG0 low window) are tracked at
 * all; everything else is dropped in the translation callback, so no
 * exec callback is even registered for it. Logged addresses show the
 * low 32 bits only: the leading 8 f's of the sign-extended form are
 * stripped (0xffffffff81000538 is logged as 0x81000538).
 *
 * first_insn is the block's first instruction, printed as the raw
 * instruction bytes combined in guest memory order (for the big-endian
 * mips64 guest this is the architectural instruction word) with
 * 2 * size hex digits, e.g. 0x3c1a0000.
 *
 * Counters are accumulated in memory. A row is appended - and flushed -
 * only when a TB's trans_count or exec_count crosses a power of two
 * (1, 2, 4, 8, ...; i.e. log-scale sampling, like AFL's hit-count
 * buckets), plus one final row per TB at exit carrying its definitive
 * counters. Total log volume is therefore bounded by
 * ~65 rows per TB for the whole run - independent of runtime and
 * execution rate - instead of growing with every executed block.
 *
 * A previous revision wrote and flushed a row on every TB execution
 * event; a guest stuck in a tight boot/reset loop produced ~10^8 rows
 * and several GB of log within minutes, saturating the host's I/O.
 *
 * The log is append-only: the last row written for a given virt_addr
 * holds its final values (after a clean exit), so the file is valid both
 * for live monitoring (tail -f) and for parsing after the run. If QEMU
 * is killed hard, the last row of a still-executing TB holds the counter
 * at its last power-of-two crossing.
 *
 * The plugin uses only APIs available in both system and user
 * emulation, so the same .so works with qemu-system-* and qemu-user.
 * It takes no options:
 *
 *   qemu-system-mips64 -plugin file=<build>/contrib/plugins/libcoverage2.so ...
 *   qemu-mips64 -plugin <build>/contrib/plugins/libcoverage2.so <program>
 *
 * Build (mirrors contrib/plugins/Makefile; cc/CFLAGS from config-host.mak):
 *   cc -fPIC -Wall $(pkg-config --cflags glib-2.0) \
 *      -I<qemu-src>/include/qemu -I<qemu-src>/include -shared \
 *      -o libcoverage2.so <qemu-src>/contrib/coverage2.c \
 *      $(pkg-config --libs glib-2.0)
 *
 * License: GNU GPL, version 2 or later.
 *   See the COPYING file in the top-level directory.
 */
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <glib.h>

#include <qemu-plugin.h>

QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;

#define LOG_DIR "logs"
#define LOG_PATH LOG_DIR "/coverage.log"

/* Address rules. A TB is tracked and logged only if:
 *
 *  1. its start vaddr > LOG_VADDR_MIN -- cuts the low zero-extended
 *     regions (0x21xxxxxx/0x25xxxxxx TLB-mapped code), which in a
 *     live boot were ~98% of all rows;
 *  2. the low 32 bits of the vaddr start with hex 8, i.e. fall in
 *     [0x80000000, 0x8fffffff] -- the KSEG0 low window (IOS kernel /
 *     exception vectors; PA = vaddr & 0x1fffffff, so with the
 *     f-prefix stripped below, PA = logged value - 0x80000000).
 *     This drops the boot ROM rows (0xffffffffbfc00xxx) and the
 *     KSEG1 aliases (0xffffffffa1xxxxxx).
 *
 * Everything else is dropped in the translation callback, so no exec
 * callback is even registered for it. Logged addresses are printed as
 * their low 32 bits, i.e. the leading 8 f's of the sign-extended form
 * are stripped: 0xffffffff81000538 is logged as 0x81000538. */
#define LOG_VADDR_MIN 0x81000000ULL

static bool loggable_vaddr(uint64_t vaddr)
{
    return vaddr > LOG_VADDR_MIN &&
           (vaddr & 0xf0000000ULL) == 0x80000000ULL;
}

/* TB table: key = start vaddr, value = TbEntry. Entries are never
 * removed, so a TbEntry pointer stays valid and can be used directly
 * as the userdata of the exec callback. */
typedef struct {
    uint64_t vaddr;
    uint64_t trans_count;
    uint64_t exec_count;
    uint64_t insn_count;
    /* first instruction of the block: raw bytes in memory order,
     * most significant byte first, and how many of them */
    uint64_t first_insn;
    unsigned first_insn_bytes;
    /* counters as of the last emitted row */
    uint64_t last_trans_count;
    uint64_t last_exec_count;
} TbEntry;

static GMutex lock;
static GHashTable *tb_table;
static FILE *log_fp;

static bool is_pow2(uint64_t n)
{
    return n && !(n & (n - 1));
}

/* Fetch the raw bytes of the block's first instruction and combine them
 * into a word in guest memory order (MSB first), so for a big-endian
 * guest the value is the instruction as the CPU fetches it. Only valid
 * inside the tb_trans callback. */
static void tb_first_insn(struct qemu_plugin_tb *tb,
                          uint64_t *val, unsigned *nbytes)
{
    struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, 0);
    const uint8_t *p;
    size_t sz, i;
    uint64_t v = 0;

    *val = 0;
    *nbytes = 0;
    if (!insn) {
        return;
    }
    p = qemu_plugin_insn_data(insn);
    sz = qemu_plugin_insn_size(insn);
    if (!p || !sz) {
        return;
    }
    if (sz > sizeof(v)) {
        sz = sizeof(v);
    }
    for (i = 0; i < sz; i++) {
        v = (v << 8) | p[i];
    }
    *val = v;
    *nbytes = sz;
}

/* Append one CSV row with the TB's current counters and flush it, so the
 * row is visible to readers immediately. Caller holds the lock. */
static void emit_row(TbEntry *e)
{
    /* The address is printed as its low 32 bits: the leading 8 f's of
     * the sign-extended KSEG form are stripped. */
    fprintf(log_fp, "0x%08" PRIx64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64
            ",0x%0*" PRIx64 "\n",
            (uint64_t)(e->vaddr & 0xffffffffULL),
            e->trans_count, e->exec_count, e->insn_count,
            (int)(2 * e->first_insn_bytes), e->first_insn);
    fflush(log_fp);
    e->last_trans_count = e->trans_count;
    e->last_exec_count = e->exec_count;
}

static void vcpu_tb_exec(unsigned int cpu_index, void *udata)
{
    TbEntry *e = udata;

    (void)cpu_index;

    g_mutex_lock(&lock);
    e->exec_count++;
    if (is_pow2(e->exec_count)) {
        emit_row(e);
    }
    g_mutex_unlock(&lock);
}

static void vcpu_tb_trans(qemu_plugin_id_t id, struct qemu_plugin_tb *tb)
{
    uint64_t vaddr = qemu_plugin_tb_vaddr(tb);
    TbEntry *e;

    (void)id;

    if (!loggable_vaddr(vaddr)) {
        return;
    }

    g_mutex_lock(&lock);
    e = g_hash_table_lookup(tb_table, (gconstpointer)(uintptr_t)vaddr);
    if (e) {
        e->trans_count++;
    } else {
        e = g_new0(TbEntry, 1);
        e->vaddr = vaddr;
        e->trans_count = 1;
        e->insn_count = qemu_plugin_tb_n_insns(tb);
        tb_first_insn(tb, &e->first_insn, &e->first_insn_bytes);
        g_hash_table_insert(tb_table, (gpointer)(uintptr_t)vaddr, e);
    }
    if (is_pow2(e->trans_count)) {
        emit_row(e);
    }
    g_mutex_unlock(&lock);

    qemu_plugin_register_vcpu_tb_exec_cb(tb, vcpu_tb_exec,
                                         QEMU_PLUGIN_CB_NO_REGS,
                                         (void *)e);
}

static void plugin_exit(qemu_plugin_id_t id, void *p)
{
    GHashTableIter iter;
    gpointer value;

    (void)id;
    (void)p;

    if (!log_fp) {
        return;
    }

    /* Final dump: one row per TB whose counters changed since its last
     * emitted row, so the last row of every virt_addr is definitive. */
    g_mutex_lock(&lock);
    g_hash_table_iter_init(&iter, tb_table);
    while (g_hash_table_iter_next(&iter, NULL, &value)) {
        TbEntry *e = value;

        if (e->trans_count != e->last_trans_count ||
            e->exec_count != e->last_exec_count) {
            emit_row(e);
        }
    }
    fclose(log_fp);
    log_fp = NULL;
    g_mutex_unlock(&lock);
}

QEMU_PLUGIN_EXPORT
int qemu_plugin_install(qemu_plugin_id_t id, const qemu_info_t *info,
                        int argc, char **argv)
{
    (void)info;
    (void)argc;
    (void)argv;

    g_mkdir_with_parents(LOG_DIR, 0755);
    log_fp = fopen(LOG_PATH, "w");
    if (!log_fp) {
        fprintf(stderr, "coverage2: failed to open %s\n", LOG_PATH);
        return -1;
    }

    tb_table = g_hash_table_new(NULL, g_direct_equal);
    fprintf(log_fp, "%s,%s,%s,%s,%s\n",
            "virt_addr", "trans_count", "exec_count", "insn_count",
            "first_insn");
    fflush(log_fp);
    fprintf(stderr, "coverage2: logging 0x8xxxxxxx rows "
            "(KSEG0 low window, f-prefix stripped) to %s "
            "(log-scale sampling, final dump at exit)\n", LOG_PATH);

    qemu_plugin_register_vcpu_tb_trans_cb(id, vcpu_tb_trans);
    qemu_plugin_register_atexit_cb(id, plugin_exit, NULL);
    return 0;
}
