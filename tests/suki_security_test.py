#!/usr/bin/env python3
"""Host regression checks for the source actually shipped in this build.

Requires a C compiler and OpenSSL development headers. No kernel build, network,
device, or source mutation. The C harnesses extract the production functions on
every run; only kernel I/O/allocation/SELinux dependencies are substituted.
The APK checks hash real certificates, but do not replace Android's APK verifier.
The SELinux serializer is a contract mock, not a real device policy round trip.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
from test_support import TestInputError, project_path, require_command, require_files, run_cli, source_label

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_SHA256 = "4ca9810e6355fbff0bbe4bf5ce159808e64a40d85987a11894030cd990a6bdf3"
REVIEWED_FILES = ("manager/apk_sign.c", "selinux/sepolicy.c", "runtime/ksud.c",
                  "sulog/event.c", "sulog/event.h", "Makefile")


def function(source, name):
    """Extract a whole production function, retaining conditional branches."""
    match = re.search(r"^.*\b" + re.escape(name) + r"\([^;]*?\)\n\{", source, re.M)
    if not match:
        raise ValueError(f"Function not found: {name}")
    end = source.index("\n}\n", match.end()) + 3
    return source[match.start():end]


APK_PRELUDE = r'''
#define _GNU_SOURCE
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/types.h>
#include <openssl/sha.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define SHA256_DIGEST_SIZE 32
#define FMODE_NONOTIFY 0
#define IS_ERR(p) ((intptr_t)(p) < 0)
#define pr_info(...) ((void)0)
#define pr_err(...) ((void)0)
struct file { int fd; int f_mode; };
static bool short_reads;
static int open_files;
static struct file *filp_open(char *path, int flags, int unused) {
    (void)unused;
    int fd = open(path, flags);
    if (fd < 0) return (struct file *)(intptr_t)-errno;
    struct file *f = calloc(1, sizeof(*f));
    if (!f) abort();
    f->fd = fd;
    open_files++;
    return f;
}
static void filp_close(struct file *f, int unused) {
    (void)unused;
    close(f->fd); free(f); open_files--;
}
static loff_t generic_file_llseek(struct file *f, loff_t off, int whence) {
    return lseek(f->fd, off, whence);
}
static ssize_t kernel_read(struct file *f, void *buf, size_t n, loff_t *pos) {
    if (short_reads && n) n--;
    ssize_t ret = pread(f->fd, buf, n, *pos);
    if (ret > 0) *pos += ret;
    return ret;
}
static int ksu_sha256(const void *data, unsigned n, unsigned char *digest) {
    return SHA256(data, n, digest) ? 0 : -1;
}
static void bin2hex(char *out, const unsigned char *data, size_t n) {
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        out[2*i] = digits[data[i] >> 4]; out[2*i+1] = digits[data[i] & 15];
    }
}
'''

APK_MAIN = r'''
int main(int argc, char **argv) {
    int count = 0;
    if ((argc - 1) % 2) return 2;
    for (int i = 1; i < argc; i += 2) {
        short_reads = argv[i][0] == 's';
        bool expected = argv[i][0] == '1';
        bool got = check_v2_signature(argv[i+1], apk_sign_keys[0].size,
                                     apk_sign_keys[0].sha256);
        if (got != expected || open_files) {
            fprintf(stderr, "APK case failed: %s expected %d got %d open %d\n",
                    argv[i+1], expected, got, open_files);
            return 1;
        }
        count++;
    }
    printf("APK parser: %d cases passed (real SHA256, actual production functions)\n", count);
    return 0;
}
'''

SEPOLICY_PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define KERNEL_VERSION(a,b,c) (((a)<<16) | ((b)<<8) | (c))
#define LINUX_VERSION_CODE KERNEL_VERSION(5,15,0)
#define GFP_KERNEL 0
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define IS_ERR(p) ((intptr_t)(p) < 0)
#define pr_err(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_info(...) ((void)0)
#define POLICYDB_CONFIG_ANDROID_NETLINK_ROUTE 1u
#define POLICYDB_CONFIG_ANDROID_NETLINK_GETNEIGH 2u
static void *allocations[32];
static int allocation_count, allocation_attempts, fail_allocation;
static void *allocate(size_t n) {
    allocation_attempts++;
    if (allocation_attempts == fail_allocation) return NULL;
    void *p = calloc(1, n);
    assert(p && allocation_count < 32);
    allocations[allocation_count++] = p;
    return p;
}
static void kfree(void *p) {
    if (!p) return;
    int i;
    for (i = 0; i < allocation_count; i++) if (allocations[i] == p) break;
    assert(i < allocation_count); /* detects double free / foreign ownership */
    allocations[i] = allocations[--allocation_count];
    free(p);
}
static void *kcalloc(size_t n, size_t size, int flags) {
    (void)flags; return allocate(n * size);
}
static void *kzalloc(size_t size, int flags) { return kcalloc(1, size, flags); }
static char *kstrdup(const char *s, int flags) {
    char *p = kzalloc(strlen(s)+1, flags);
    if (p) strcpy(p, s);
    return p;
}
static void *kmemdup(const void *s, size_t n, int flags) {
    void *p = kzalloc(n, flags);
    if (p) memcpy(p, s, n);
    return p;
}
static size_t buffer_capacity;
static void *vmalloc(size_t n) { buffer_capacity = n; return allocate(n); }
#define kvfree kfree
struct ebitmap { uint64_t bits; };
struct filename_trans_key { u32 ttype, tclass; char *name; };
struct filename_trans_datum {
    struct ebitmap stypes;
    u32 otype;
    struct filename_trans_datum *next;
};
struct hashtab { struct filename_trans_key *key; struct filename_trans_datum *datum; };
struct symtab { u32 nprim; };
struct type_datum { u32 value; };
struct class_datum { u32 value; };
struct policydb {
    struct symtab p_types, p_classes;
    struct hashtab filename_trans;
    size_t len;
    unsigned compat_filename_trans_count;
    bool android_netlink_route, android_netlink_getneigh;
};
struct selinux_policy { struct policydb policydb; int sentinel; };
struct policy_file { void *data; size_t len; };
static struct type_datum src_type = {1}, tgt_type = {2}, def_type = {3};
static struct class_datum file_class = {4};
static void *symtab_search(struct symtab *tab, const char *name) {
    (void)tab;
    if (!strcmp(name, "src")) return &src_type;
    if (!strcmp(name, "tgt")) return &tgt_type;
    if (!strcmp(name, "def")) return &def_type;
    if (!strcmp(name, "file")) return &file_class;
    return NULL;
}
static struct filename_trans_datum *policydb_filenametr_search(struct policydb *db,
                                                               struct filename_trans_key *key) {
    (void)key; return db->filename_trans.datum;
}
static const int filenametr_key_params = 0;
static int insertion_error;
static int hashtab_insert(struct hashtab *tab, void *key, void *datum, int params) {
    (void)params;
    if (insertion_error) return insertion_error;
    if (tab->key) return -EEXIST;
    tab->key = key; tab->datum = datum; return 0;
}
static bool ebitmap_get_bit(struct ebitmap *bm, unsigned bit) {
    assert(bit < 64); return (bm->bits >> bit) & 1;
}
static int bitmap_error;
static int ebitmap_set_bit(struct ebitmap *bm, unsigned bit, int value) {
    assert(bit < 64 && value == 1);
    if (bitmap_error) return bitmap_error;
    bm->bits |= UINT64_C(1) << bit; return 0;
}
static size_t serialized_size, read_size;
static unsigned read_config;
static int write_error, read_error;
static int policydb_write(struct policydb *db, struct policy_file *fp) {
    (void)db;
    if (write_error) return write_error;
    if (serialized_size > fp->len) return -EINVAL;
    memset(fp->data, 0, serialized_size);
    fp->data = (char *)fp->data + serialized_size;
    fp->len -= serialized_size; /* actual kernel policy_file remaining-length contract */
    return 0;
}
static int policydb_read(struct policydb *db, struct policy_file *fp) {
    read_size = fp->len;
    if (read_error) return read_error; /* kernel policydb_read self-cleans on failure */
    if (fp->len < 24) return -EINVAL;
    memcpy(&read_config, (char *)fp->data + 20, sizeof(read_config));
    assert(fp->len == serialized_size);
    db->len = 0xdead; /* caller must overwrite with actual serialized length */
    fp->data = (char *)fp->data + fp->len;
    fp->len = 0;
    return 0;
}
'''

SEPOLICY_MAIN = r'''
static int cases;
static void reset(void) {
    assert(allocation_count == 0);
    allocation_attempts = fail_allocation = insertion_error = bitmap_error = 0;
    write_error = read_error = 0;
    buffer_capacity = read_size = read_config = 0;
}
static void destroy_table(struct policydb *db) {
    if (db->filename_trans.key) {
        kfree(db->filename_trans.key->name);
        kfree(db->filename_trans.key);
        kfree(db->filename_trans.datum);
        memset(&db->filename_trans, 0, sizeof(db->filename_trans));
    }
    assert(!allocation_count);
}
static void test_filename(void) {
    for (int fail = 1; fail <= 3; fail++) {
        reset();
        struct policydb db = {0};
        fail_allocation = fail;
        assert(!add_filename_trans(&db, "src", "tgt", "file", "def", "name"));
        assert(!allocation_count && !db.filename_trans.key && !db.compat_filename_trans_count);
        assert(allocation_attempts == fail);
        cases++;
    }
    int errors[] = {-ENOMEM, -EEXIST};
    for (unsigned i = 0; i < sizeof(errors)/sizeof(errors[0]); i++) {
        reset();
        struct policydb db = {0};
        insertion_error = errors[i];
        assert(!add_filename_trans(&db, "src", "tgt", "file", "def", "name"));
        assert(!allocation_count && !db.filename_trans.key && !db.compat_filename_trans_count);
        cases++;
    }
    reset();
    struct policydb db = {0};
    assert(add_filename_trans(&db, "src", "tgt", "file", "def", "name"));
    assert(allocation_count == 3 && db.filename_trans.key->ttype == 2);
    assert(db.filename_trans.key->tclass == 4 && !strcmp(db.filename_trans.key->name, "name"));
    assert(db.filename_trans.datum->otype == 3 && db.filename_trans.datum->stypes.bits == 1);
    assert(db.compat_filename_trans_count == 1);
    int before = allocation_attempts;
    assert(add_filename_trans(&db, "src", "tgt", "file", "tgt", "name"));
    assert(db.filename_trans.datum->otype == 2 && allocation_attempts == before);
    assert(db.compat_filename_trans_count == 1);
    destroy_table(&db);
    cases += 2;
    reset();
    struct filename_trans_datum existing = {.otype = 3};
    db = (struct policydb){0};
    db.filename_trans.datum = &existing;
    assert(add_filename_trans(&db, "src", "tgt", "file", "def", "name"));
    assert(!allocation_attempts && existing.stypes.bits == 1);
    cases++;
    reset();
    db = (struct policydb){0};
    assert(!add_filename_trans(&db, "missing", "tgt", "file", "def", "name"));
    assert(!allocation_attempts);
    cases++;
}
static void test_duplicate(void) {
    const size_t sizes[] = {100, 124, 148};
    for (unsigned i = 0; i < sizeof(sizes)/sizeof(sizes[0]); i++) {
        reset();
        struct selinux_policy old = {.policydb = {.len = 100, .p_types = {.nprim = 4},
            .android_netlink_route = true, .android_netlink_getneigh = true}, .sentinel = 42};
        serialized_size = sizes[i];
        struct selinux_policy *copy = ksu_dup_sepolicy(&old);
        assert(!IS_ERR(copy) && copy != &old && copy->sentinel == 42);
        assert(buffer_capacity == 148 && copy->policydb.len == sizes[i] && read_size == sizes[i]);
        assert(read_config == 3 && old.policydb.len == 100 && allocation_count == 1);
        kfree(copy);
        assert(!allocation_count);
        cases++;
    }
    for (int fail = 1; fail <= 5; fail++) {
        reset();
        struct selinux_policy old = {.policydb = {.len = 100, .p_types = {.nprim = 4}}};
        serialized_size = 124;
        int expected = -ENOMEM;
        if (fail <= 2) fail_allocation = fail; /* vmalloc then kmemdup */
        if (fail == 3) { write_error = -EIO; expected = -EIO; }
        if (fail == 4) { read_error = -EINVAL; expected = -EINVAL; }
        if (fail == 5) { serialized_size = 149; expected = -EINVAL; }
        struct selinux_policy *copy = ksu_dup_sepolicy(&old);
        assert(copy == ERR_PTR(expected) && !allocation_count && old.policydb.len == 100);
        cases++;
    }
}
int main(void) {
    test_filename(); test_duplicate();
    printf("SELinux policy: %d cases passed (actual production functions; dependency fault injection)\n", cases);
    return 0;
}
'''


def compile_run(directory, name, source, compiler, args=(), libraries=()):
    path = directory / (name + ".c")
    path.write_text(source)
    binary = directory / name
    subprocess.run([compiler, "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-fno-omit-frame-pointer", str(path),
                    "-o", str(binary), *libraries], check=True)
    env = dict(os.environ)
    # The command sandbox uses ptrace, unsupported by LeakSanitizer. Every
    # mocked allocation and file handle is independently tracked/asserted above.
    env["ASAN_OPTIONS"] = env.get("ASAN_OPTIONS", "") + ":detect_leaks=0"
    env["UBSAN_OPTIONS"] = env.get("UBSAN_OPTIONS", "") + ":halt_on_error=1"
    subprocess.run([str(binary), *args], check=True, env=env)


def signing_pairs(apk):
    eocd = apk.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("Missing EOCD")
    cd = struct.unpack_from("<I", apk, eocd + 16)[0]
    assert apk[cd - 16:cd] == b"APK Sig Block 42"
    block_size = struct.unpack_from("<Q", apk, cd - 24)[0]
    start = cd - block_size - 8
    assert struct.unpack_from("<Q", apk, start)[0] == block_size
    pos, result = start + 8, []
    while pos < cd - 24:
        length = struct.unpack_from("<Q", apk, pos)[0]
        result.append(apk[pos:pos + 8 + length])
        pos += 8 + length
    assert pos == cd - 24
    return result


def pair(block_id, value=b""):
    return struct.pack("<QI", len(value) + 4, block_id) + value


def envelope(pairs, comment=b""):
    data = b"".join(pairs)
    size = len(data) + 24
    block = struct.pack("<Q", size) + data + struct.pack("<Q", size) + b"APK Sig Block 42"
    # Minimal container sufficient for the kernel's identity check. Not installable.
    return block + struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0, 0, 0, len(block), len(comment)) + comment


def changed(data, offset, fmt, value):
    result = bytearray(data)
    struct.pack_into(fmt, result, offset, value)
    return bytes(result)


def apk_cases(directory, official):
    raw = official.read_bytes()
    if hashlib.sha256(raw).hexdigest() != OFFICIAL_SHA256:
        raise TestInputError("Official 40900 APK checksum differs; pass the unmodified v4.2.0 release APK.")
    pairs = signing_pairs(raw)
    v2 = next(p for p in pairs if struct.unpack_from("<I", p, 8)[0] == 0x7109871A)
    valid = envelope([v2])
    cases = [("official-40900", True, raw), ("v2-only", True, valid),
             ("verity-after", True, envelope([v2, pair(0x42726577, bytes(29))])),
             ("verity-before", True, envelope([pair(0x42726577, bytes(29)), v2])),
             ("eocd-comment", True, envelope([v2], b"checked comment")),
             ("duplicate-v2", False, envelope([v2, v2])),
             ("no-v2", False, envelope([pair(0x42726577)])),
             ("empty", False, b""), ("tiny", False, bytes(8))]
    for block_id in (0xF05368C0, 0x1B93AD61, 0x12345678, 0, 0xFFFFFFFF):
        for before in (False, True):
            blocks = [pair(block_id), v2] if before else [v2, pair(block_id)]
            cases.append((f"unknown-{block_id:x}-{'before' if before else 'after'}", False, envelope(blocks)))
    for length in (0, 3, 0x80000000, 0xFFFFFFFFFFFFFFFF, len(v2)):
        cases.append((f"pair-length-{length}", False, envelope([changed(v2, 0, "<Q", length)])))
    # Each nested length prefix is independently forced beyond its enclosing value.
    prefixes = [12, 16, 20, 24]
    digests_size = struct.unpack_from("<I", v2, 24)[0]
    prefixes += [28 + digests_size, 32 + digests_size]
    for off in prefixes:
        cases.append((f"nested-length-{off}", False, envelope([changed(v2, off, "<I", 0xFFFFFFFF)])))
    cert_start = 36 + digests_size
    bad_cert = bytearray(v2)
    bad_cert[cert_start] ^= 1
    cases.append(("certificate-hash", False, envelope([bytes(bad_cert)])))
    cd = len(valid) - 22
    cases += [("head-size-mismatch", False, changed(valid, 0, "<Q", 24)),
              ("footer-size-overflow", False, changed(valid, cd - 24, "<Q", 0xFFFFFFFFFFFFFFFF)),
              ("footer-size-short", False, changed(valid, cd - 24, "<Q", 23)),
              ("bad-signing-magic", False, changed(valid, cd - 16, "<B", 0)),
              ("central-size-mismatch", False, changed(valid, cd + 12, "<I", 1)),
              ("central-offset-past-eocd", False, changed(valid, cd + 16, "<I", cd + 1)),
              ("central-offset-short", False, changed(valid, cd + 16, "<I", 31)),
              ("zip64-locator", False, changed(valid, cd - 20, "<I", 0x07064B50)),
              ("truncated", False, valid[:-1])]
    args = []
    for name, expected, contents in cases:
        path = directory / (name + ".apk")
        path.write_bytes(contents)
        args += [str(int(expected)), str(path)]
    args += ["s", str(official), "0", str(directory / "missing.apk")]
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("sources/SukiSU-Ultra/kernel"))
    parser.add_argument("--apk", type=Path, required=True, help="Official SukiSU v4.2.0 / 40900 release APK")
    parser.add_argument("--cc", default=shutil.which("clang") or "cc")
    parser.add_argument("--json-output", type=Path, default=Path("tests/suki_security_results.json"))
    parser.epilog = "Relative input and output paths are relative to the project root."
    opts = parser.parse_args()
    opts.source = project_path(opts.source, ROOT)
    opts.apk = project_path(opts.apk, ROOT)
    opts.json_output = project_path(opts.json_output, ROOT)
    # A failed rerun must not leave an earlier PASS available to the finalizer.
    opts.json_output.unlink(missing_ok=True)
    require_files([opts.source / name for name in REVIEWED_FILES],
                  "Run bash prepare-sources.sh first, or pass --source /path/to/SukiSU-Ultra/kernel.")
    require_files([opts.apk], "Pass --apk /path/to/SukiSU_v4.2.0_40900-release.apk.")
    require_command(opts.cc, "Install clang or a C compiler and OpenSSL development headers, or set --cc.")
    source_hashes = {"kernel/" + name: hashlib.sha256((opts.source / name).read_bytes()).hexdigest()
                     for name in REVIEWED_FILES}
    apk = (opts.source / "manager/apk_sign.c").read_text()
    policy = (opts.source / "selinux/sepolicy.c").read_text()
    keys = apk[apk.index("static struct apk_sign_key"):apk.index("\n};", apk.index("static struct apk_sign_key")) + 3]
    apk_source = APK_PRELUDE + keys + "\n" + "\n".join(function(apk, name) for name in
                 ("read_exact", "read_length_prefixed_end", "check_block", "check_v2_signature")) + APK_MAIN
    policy_source = SEPOLICY_PRELUDE + "\n" + "\n".join(function(policy, name) for name in
                    ("add_filename_trans", "ksu_dup_sepolicy")) + SEPOLICY_MAIN
    with tempfile.TemporaryDirectory(prefix="suki-security-") as name:
        directory = Path(name)
        cases = apk_cases(directory, opts.apk)
        compile_run(directory, "apk", apk_source, opts.cc, cases, ["-lcrypto"])
        compile_run(directory, "sepolicy", policy_source, opts.cc)
    if source_hashes != {"kernel/" + name: hashlib.sha256((opts.source / name).read_bytes()).hexdigest()
                         for name in REVIEWED_FILES}:
        raise RuntimeError("Sources changed during the test run")
    apk_count, policy_count = len(cases) // 2, 17
    results = {
        "schema_version": 1,
        "status": "PASS",
        "tested_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": source_label(opts.source, ROOT, "sukisu-source/kernel"),
        "source_sha256": source_hashes,
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "official_apk_sha256": OFFICIAL_SHA256,
        "official_apk": source_label(opts.apk, ROOT, "official-manager/SukiSU_v4.2.0_40900-release.apk"),
        "tests": apk_count + policy_count,
        "passed": apk_count + policy_count,
        "suite_cases": {"apk": apk_count, "sepolicy": policy_count},
        "scopes": ["actual APK parser with real certificate SHA256",
                   "actual 5.15 filename transition function with allocation/insert fault injection",
                   "actual policy duplication function with serializer contract mock",
                   "runtime/sulog/version changes source-reviewed only"],
        "compiler": Path(opts.cc).name,
        "sanitizers": ["address", "undefined"],
        "leak_sanitizer": False,
        "allocation_and_handle_tracking": True,
        "limitations": ["not an APK installation or full signature validation test",
                        "not a real device policy serialize/load test",
                        "not a kernel build or runtime device test",
                        "#3679 scoped su-session behavior intentionally absent; UAPI 2 preserved"],
    }
    opts.json_output.parent.mkdir(parents=True, exist_ok=True)
    opts.json_output.write_text(json.dumps(results, indent=2) + "\n")
    print("PASS: AddressSanitizer and UndefinedBehaviorSanitizer clean; allocation/handle counters balanced.")
    print("LeakSanitizer disabled for the ptrace sandbox; tests do not modify production sources.")


if __name__ == "__main__":
    run_cli(main)
