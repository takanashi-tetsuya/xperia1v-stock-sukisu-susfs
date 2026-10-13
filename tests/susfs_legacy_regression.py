#!/usr/bin/env python3
"""Host regressions executing extracted kernel functions, not rewritten predicates.

Mocks kernel locking/allocation/uaccess and VFS metadata; does not validate kernel
concurrency, actual Android callers, or establish the cause of app failures.
Run against the old source to demonstrate failure, then against the candidate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from test_support import project_path, require_command, require_files, run_cli, source_label

ROOT = Path(__file__).resolve().parents[1]

FUNCTIONS = [
    "susfs_sus_kstat_spoof_generic_fillattr",
    "susfs_sus_kstat_spoof_show_map_vma",
    "susfs_sus_kstat_spoof_vfs_statfs",
    "susfs_sus_kstat_spoof_inotify_fdinfo",
    "susfs_sus_kstat_spoof_proc_fd_seq_show",
    "susfs_open_redirect_spoof_do_sys_openat",
    "susfs_open_redirect_spoof_vfs_readlink",
    "susfs_open_redirect_spoof_do_proc_readlink",
    "susfs_open_redirect_spoof_show_map_vma_srcu",
]


def function(source, name):
    hit = re.search(r"\b" + name + r"\s*\(", source)
    assert hit, name
    start = source.rfind("\n", 0, hit.start()) + 1
    begin = source.index("{", hit.end())
    depth = 1
    end = begin + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
#define CONFIG_KSU_SUSFS_OPEN_REDIRECT 1
#define __user
#define SUSFS_MAX_LEN_PATHNAME 256
#define LINUX_VERSION_CODE 0x50f00
#define KERNEL_VERSION(a,b,c) (((a)<<16)|((b)<<8)|(c))
#define FUSE_SUPER_MAGIC 0x65735546
#define STATX_SUS_KSTAT 0x10000000U
#define STATX_SUS_KSTAT_FUSE 0x20000000U
#define IOP_DEFAULT_READLINK 1
#define SUSFS_LOGI(...) ((void)0)
#define SUSFS_LOGE(...) ((void)0)
#define unlikely(v) (v)
#define READ_ONCE(v) (v)
#define PTR_ERR(v) ((intptr_t)(v))
#define IS_ERR(v) ((uintptr_t)(v) >= (uintptr_t)-4095)
#define rcu_read_lock() ((void)0)
#define rcu_read_unlock() ((void)0)
#define srcu_read_lock(p) (0)
#define srcu_read_unlock(p,i) ((void)0)
#define spin_lock(p) ((void)0)
#define spin_unlock(p) ((void)0)
#define DEFINE_DELAYED_CALL(name) struct delayed_call name = {0}
#define SUSFS_IS_INODE_OPEN_REDIRECT(inode) ((inode)->redirect)
typedef uint32_t u32;
struct hlist_node { void *next; };
struct kstatfs { int marker; };
struct times { long tv_sec; unsigned long tv_nsec; };
struct kstat {
    unsigned long ino, dev, nlink, size, blksize, blocks, mnt_id;
    struct times atime, mtime, ctime;
};
struct super_block { dev_t s_dev; unsigned long s_magic; };
struct inode;
struct dentry;
struct delayed_call { int pending; };
struct inode_operations {
    int (*readlink)(struct dentry *, char *, int);
    const char *(*get_link)(struct dentry *, struct inode *, struct delayed_call *);
};
struct inode {
    unsigned long i_ino;
    struct super_block *i_sb;
    void *i_mapping;
    unsigned int i_opflags;
    struct inode_operations *i_op;
    int i_lock;
    const char *i_link;
    bool redirect;
};
struct fuse_inode { struct inode inode; };
#define get_fuse_inode(p) ((struct fuse_inode *)(p))
struct dentry { union { struct inode *inode; struct inode *d_inode; }; };
#define d_inode(d) ((d)->inode)
#define d_is_symlink(d) (true)
struct path { struct dentry *dentry; };
#define GFP_KERNEL 0
#define PAGE_SIZE 4096
static bool allocation_fault, path_fault;
static int page_allocations, page_frees, slab_frees;
static unsigned long __get_free_page(int flags) {
    (void)flags;
    if (allocation_fault) return 0;
    page_allocations++;
    return (unsigned long)malloc(PAGE_SIZE);
}
static void free_page(unsigned long p) { page_frees++; free((void *)p); }
static void kfree(void *p) { slab_frees++; free(p); }
static char *d_path(struct path *p, char *b, int n) {
    (void)p;
    if (path_fault) return (char *)(intptr_t)-EACCES;
    char *s=b+n-10;
    memcpy(s,"/original",10);
    return s;
}
static int delayed_calls;
static void do_delayed_call(struct delayed_call *d) { delayed_calls += d->pending; }
static bool copy_fault;
static int copy_to_user(void *dst, const void *src, size_t n) {
    if (copy_fault) { copy_fault=false; return n ? (int)n : 1; }
    if (!dst && n) return (int)n;
    if (n) memcpy(dst, src, n);
    return 0;
}
static int strscpy(char *d, const char *s, size_t n) {
    if (!n) return -E2BIG;
    size_t len = strlen(s);
    size_t copy = len < n - 1 ? len : n - 1;
    memcpy(d, s, copy); d[copy] = 0;
    return len >= n ? -E2BIG : (int)len;
}
enum { UID_NON_APP_PROC, UID_ROOT_PROC_EXCEPT_SU_PROC, UID_NON_SU_PROC,
       UID_UMOUNTED_APP_PROC, UID_UMOUNTED_PROC };
struct kuid { unsigned int val; };
static struct kuid current_uid(void) { return (struct kuid){10001}; }
static bool susfs_is_current_ksu_domain(void) { return false; }
static bool susfs_is_current_proc_umounted_app(void) { return true; }
static bool susfs_is_current_proc_umounted(void) { return true; }
struct filename { const char *name; };
static struct filename *getname_kernel(const char *s) {
    static struct filename name;
    name.name=s; return &name;
}
static unsigned hash14(unsigned long ino) {
    return (uint64_t)(ino * UINT64_C(0x61C8864680B583EB)) >> 50;
}
#define hash_for_each_possible_rcu(table,entry,node,key) \
    for ((entry)=(table)[hash14(key)]; (entry); (entry)=(entry)->node.next)
'''

TESTS = r'''
static struct st_susfs_sus_kstat_hlist ks;
static struct st_susfs_open_redirect_hlist rd;
static struct super_block sb = {.s_dev=7};
static struct inode ino = {.i_ino=6, .i_sb=&sb, .i_mapping=&sb, .redirect=true};
static void reset(void) {
    memset(SUS_KSTAT_HLIST,0,sizeof(SUS_KSTAT_HLIST));
    memset(OPEN_REDIRECT_HLIST,0,sizeof(OPEN_REDIRECT_HLIST));
    memset(&ks,0,sizeof(ks)); memset(&rd,0,sizeof(rd));
    sb.s_dev=7; sb.s_magic=0; ino.i_ino=6;
    copy_fault=false;
    allocation_fault=false; path_fault=false;
    page_allocations=0; page_frees=0; slab_frees=0;
    ks.target_ino=6; ks.target_dev=7; ks.info.spoofed_ino=777;
    ks.info.spoofed_dev=888; ks.info.flags=KSTAT_SPOOF_INO|KSTAT_SPOOF_DEV;
    ks.spoofed_kstatfs.marker=123; ks.spoofed_mnt_id=321;
    SUS_KSTAT_HLIST[hash14(6)]=&ks;
    rd.target_ino=6; rd.target_dev=7; rd.redirected_ino=777; rd.redirected_dev=888;
    rd.reversed_lookup_only=true; rd.info.uid_scheme=UID_UMOUNTED_PROC;
    strcpy(rd.info.redirected_pathname,"/clean/path");
    OPEN_REDIRECT_HLIST[hash14(6)]=&rd;
}
static int test_lookup(int which, int scenario) {
    reset();
    bool expect = scenario == 0 || scenario == 4;
    if (scenario==1) ino.i_ino=10952;
    if (scenario==2) sb.s_dev=8;
    if (scenario==3) ks.is_fuse=true;
    if (scenario==4) { ks.is_fuse=true; sb.s_magic=FUSE_SUPER_MAGIC; }
    unsigned long out_ino=ino.i_ino; dev_t out_dev=sb.s_dev;
    bool found=false;
    switch (which) {
    case 0: {
        struct kstat stat={0};
        susfs_sus_kstat_spoof_generic_fillattr(&ino,&stat,
            scenario==4 ? STATX_SUS_KSTAT_FUSE : STATX_SUS_KSTAT);
        found=stat.ino==777; break;
    }
    case 1:
        susfs_sus_kstat_spoof_show_map_vma(&ino,&out_dev,&out_ino);
        found=out_ino==777; break;
    case 2: {
        struct kstatfs st={0}; bool fuse=scenario==4;
        int ret=susfs_sus_kstat_spoof_vfs_statfs(&ino,&st,&fuse);
        found=ret==0 && st.marker==123; break;
    }
    case 3:
        susfs_sus_kstat_spoof_inotify_fdinfo(&out_ino,&out_dev);
        found=out_ino==777; break;
    case 4: {
        int mnt=0;
        susfs_sus_kstat_spoof_proc_fd_seq_show(&mnt,&out_ino,out_dev);
        found=out_ino==777 && mnt==321; break;
    }
    case 5: {
        rd.reversed_lookup_only=false;
        struct filename *f=susfs_open_redirect_spoof_do_sys_openat(&ino);
        found=f && !strcmp(f->name,"/clean/path"); break;
    }
    case 6: {
        char b[256]={0};
        int ret=susfs_open_redirect_spoof_vfs_readlink(&ino,b,sizeof(b));
        found=ret>=0 && !strcmp(b,"/clean/path"); break;
    }
    case 7: {
        char b[256]={0};
        int ret=susfs_open_redirect_spoof_do_proc_readlink(&ino,b,sizeof(b));
        found=ret>=0 && !strcmp(b,"/clean/path"); break;
    }
    case 8: {
        char *s=NULL;
        int ret=susfs_open_redirect_spoof_show_map_vma_srcu(&ino,&out_ino,&out_dev,&s);
        found=ret==0 && out_ino==777 && !strcmp(s,"/clean/path"); break;
    }
    }
    if (found!=expect) {
        fprintf(stderr,"lookup %d scenario %d found=%d expected=%d\n",which,scenario,found,expect);
        return 1;
    }
    return 0;
}
static int native_readlink(struct dentry *d,char *b,int n) {
    (void)d; return readlink_copy(b,n,"/original");
}
static const char *native_getlink(struct dentry *d,struct inode *i,struct delayed_call *done) {
    (void)d; (void)i; done->pending=1; return "/original";
}
static int test_readlink(int branch,int scenario) {
    reset();
    struct inode_operations ops={.readlink=branch==0 ? native_readlink : NULL,
                                 .get_link=native_getlink};
    ino.i_op=&ops; ino.i_opflags=0; ino.i_link=NULL;
    struct dentry d={.inode=&ino};
    char out[64]; memset(out,'X',sizeof(out));
    int buflen=sizeof(out), expected=11;
    const char *content="/clean/path";
    if(scenario==1){buflen=4; expected=4;}
    if(scenario==2){copy_fault=true;expected=-EFAULT;}
    if(scenario==3){OPEN_REDIRECT_HLIST[hash14(6)]=NULL;expected=9;content="/original";}
    if(scenario==4){rd.info.redirected_pathname[0]=0;expected=0;}
    if(scenario==5){buflen=0;expected=0;}
    if(scenario==6){buflen=11;expected=11;}
    delayed_calls=0;
    int got=vfs_readlink(&d,out,buflen);
    if(got!=expected || (got>0 && memcmp(out,content,got)) ||
       (got>=0 && out[got]!='X') || (branch==1 && delayed_calls!=1)) {
        fprintf(stderr,"readlink branch %d scenario %d got=%d expected=%d cleanup=%d\n",
                branch,scenario,got,expected,delayed_calls);
        return 1;
    }
    return 0;
}
static int test_proc_readlink(int unused,int scenario) {
    (void)unused;
    reset();
    struct dentry d={.inode=&ino};
    struct path path={.dentry=&d};
    char out[64]; memset(out,'X',sizeof(out));
    int buflen=sizeof(out), expected=11;
    const char *content="/clean/path";
    if(scenario==1){buflen=4;expected=4;}
    if(scenario==2){copy_fault=true;expected=-EFAULT;}
    if(scenario==3){OPEN_REDIRECT_HLIST[hash14(6)]=NULL;expected=9;content="/original";}
    if(scenario==4){rd.info.redirected_pathname[0]=0;expected=0;}
    if(scenario==5){buflen=0;expected=0;}
    if(scenario==6){buflen=11;expected=11;}
    if(scenario==7){allocation_fault=true;expected=-ENOMEM;}
    if(scenario==8){OPEN_REDIRECT_HLIST[hash14(6)]=NULL;path_fault=true;expected=-EACCES;}
    if(scenario==9){ino.redirect=false;expected=9;content="/original";}
    if(scenario==10){ino.i_ino=10952;expected=9;content="/original";}
    int got=do_proc_readlink(&path,out,buflen);
    if(got!=expected || (got>0 && memcmp(out,content,got)) ||
       (got>=0 && out[got]!='X') || slab_frees || page_allocations!=page_frees) {
        fprintf(stderr,"proc readlink scenario %d got=%d expected=%d pages=%d/%d slab_frees=%d\n",
                scenario,got,expected,page_frees,page_allocations,slab_frees);
        return 1;
    }
    return 0;
}
static int test_proc_buffer(int unused,int scenario) {
    (void)unused;
    reset();
    char buffer[32]; memset(buffer,'X',sizeof(buffer));
    int capacity=32, expected=11;
    if(scenario==1){capacity=11;expected=-E2BIG;}
    if(scenario==2){capacity=12;expected=11;}
    if(scenario==3){capacity=0;expected=-E2BIG;}
    if(scenario==4){capacity=1;expected=0;rd.info.redirected_pathname[0]=0;}
    int got=susfs_open_redirect_spoof_do_proc_readlink(&ino,buffer,capacity);
    if(got!=expected || (capacity<32 && buffer[capacity]!='X')) {
        fprintf(stderr,"proc buffer scenario %d got=%d expected=%d\n",scenario,got,expected);
        return 1;
    }
    return 0;
}
int main(int argc,char **argv) {
    if(argc!=4) return 2;
    if(hash14(6)!=4780 || hash14(10952)!=4780) return 3;
    if(!strcmp(argv[1],"lookup")) return test_lookup(atoi(argv[2]),atoi(argv[3]));
    if(!strcmp(argv[1],"proc_readlink")) return test_proc_readlink(atoi(argv[2]),atoi(argv[3]));
    if(!strcmp(argv[1],"proc_buffer")) return test_proc_buffer(atoi(argv[2]),atoi(argv[3]));
    return test_readlink(atoi(argv[2]),atoi(argv[3]));
}
'''


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("--output", type=Path)
    p.epilog = "Relative input and output paths are relative to the project root."
    args = p.parse_args()
    src = project_path(args.source, ROOT)
    if args.output:
        args.output = project_path(args.output, ROOT)
        args.output.unlink(missing_ok=True)
    inputs = ("fs/susfs.c", "fs/namei.c", "fs/proc/base.c", "include/linux/susfs.h")
    require_files([src / name for name in inputs],
                  "Run bash prepare-sources.sh first, or pass /path/to/common as the source argument.")
    require_command("cc")
    susfs = (src / "fs/susfs.c").read_text()
    namei = (src / "fs/namei.c").read_text()
    proc = (src / "fs/proc/base.c").read_text()
    header = (src / "include/linux/susfs.h").read_text()
    constants = "\n".join(re.findall(r"(?m)^#define KSTAT_SPOOF.*$", header))
    structures = "\n".join(re.findall(
        r"struct (?:st_susfs_sus_kstat|st_susfs_sus_kstat_hlist|st_susfs_open_redirect|st_susfs_open_redirect_hlist) \{.*?\n\};",
        header, re.S))
    generated = (STUBS + constants + "\n" + structures + "\n" +
                 "static struct st_susfs_sus_kstat_hlist *SUS_KSTAT_HLIST[1<<14];\n" +
                 "static struct st_susfs_open_redirect_hlist *OPEN_REDIRECT_HLIST[1<<14];\n" +
                 function(namei, "readlink_copy") +
                 "\n".join(function(susfs, f) for f in FUNCTIONS) +
                 function(namei, "vfs_readlink") +
                 function(proc, "do_proc_readlink") + TESTS)
    cases = [("lookup", which, scenario) for which in range(9)
             for scenario in (range(5) if which < 3 else range(3))]
    cases += [("readlink", branch, scenario) for branch in range(2) for scenario in range(7)]
    cases += [("proc_readlink", 0, scenario) for scenario in range(11)]
    cases += [("proc_buffer", 0, scenario) for scenario in range(5)]
    results = []
    with tempfile.TemporaryDirectory(prefix="susfs-regression-") as temp:
        c = Path(temp) / "test.c"
        binary = Path(temp) / "test"
        c.write_text(generated)
        subprocess.run(["cc", "-std=gnu11", "-O1", "-g", "-fsanitize=undefined",
                        "-fno-sanitize-recover=undefined",
                        "-Werror=implicit-function-declaration", str(c), "-o", str(binary)], check=True)
        for case in cases:
            run = subprocess.run([str(binary), *map(str, case)], text=True, capture_output=True)
            results.append({"case": list(case), "passed": run.returncode == 0,
                            "detail": (run.stdout + run.stderr).strip()})
    report = {"source": source_label(src, ROOT, "susfs-baseline/common"),
              "sha256": {name: hashlib.sha256((src / name).read_bytes()).hexdigest() for name in inputs},
              "extracted_function_count": 12,
              "tests": len(results), "passed": sum(r["passed"] for r in results),
              "scope": "Host execution of extracted C functions with mocked kernel services; no concurrency or device validation",
              "results": results}
    output = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n")
    print(output)
    raise SystemExit(0 if all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    run_cli(main)
