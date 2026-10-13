#!/usr/bin/env python3
"""Execute real namei/stat C bodies with instrumented VFS stubs.

No kernel/device/concurrency proof: the stubs track owned dentry references,
parallel lookup completion, returned names, and actual mutation entry points.
Pass --source to exercise a pre-fix tree against the identical assertions.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import resource
import subprocess
import tempfile

FUNCTIONS = ["lookup_dcache", "lookup_one_qstr_excl", "lookup_fast", "__lookup_slow",
             "lookup_open", "filename_create", "done_path_create", "do_mkdirat", "do_renameat2"]


def extract(text, name):
    hit = re.search(r"(?m)^(?:static )?(?:inline )?(?:struct dentry \*|int |void )" + name + r"\s*\(", text)
    assert hit, name
    start = text.index("{", hit.end())
    end, depth = start + 1, 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[hit.start():end] + "\n"


STUBS = r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CONFIG_KSU_SUSFS_SUS_PATH 1
#define CONFIG_KSU_SUSFS_SUS_KSTAT 1
#define likely(x) (x)
#define unlikely(x) (x)
#define ERR_PTR(x) ((struct dentry *)(intptr_t)(x))
#define PTR_ERR(x) ((intptr_t)(x))
#define IS_ERR(x) ((uintptr_t)(x) >= (uintptr_t)-4095)
#define IS_DEADDIR(i) ((i)->dead)
#define LOOKUP_RCU 1U
#define LOOKUP_CREATE 2U
#define LOOKUP_EXCL 4U
#define LOOKUP_RENAME_TARGET 8U
#define LOOKUP_DIRECTORY 16U
#define LOOKUP_REVAL 32U
#define ND_STATE_LOOKUP_LAST 1U
#define ND_STATE_OPEN_LAST 2U
#define ND_FLAGS_LOOKUP_LAST 64U
#define LAST_NORM 0
#define RENAME_NOREPLACE 1
#define RENAME_EXCHANGE 2
#define RENAME_WHITEOUT 4
#define FMODE_CREATED 1
#define I_MUTEX_PARENT 0
#define AUDIT_TYPE_CHILD_CREATE 0
#define DECLARE_WAIT_QUEUE_HEAD_ONSTACK(wq) int wq=0
#define STATX_BASIC_STATS 0x7ffU
#define STATX_ATIME 32U
#define STATX_ATTR_AUTOMOUNT 1U
#define STATX_ATTR_DAX 2U
#define STATX_SUS_KSTAT 0x10000000U
#define STATX_SUS_KSTAT_FUSE 0x20000000U
#define AT_STATX_SYNC_TYPE 0x6000
#define SB_NOATIME 1
#define IS_AUTOMOUNT(i) 0
#define IS_DAX(i) 0
#define real_mount(m) (m)
typedef unsigned int u32;
typedef unsigned int umode_t;
struct qstr { const char *name; unsigned len; };
static const struct qstr susfs_fake_qstr_name = {"..5.u.S",7}; /* baseline only */
struct inode; struct dentry; struct file; struct path; struct kstat;
struct user_namespace { int unused; };
struct inode_operations {
    struct dentry *(*lookup)(struct inode *,struct dentry *,unsigned);
    int (*create)(struct user_namespace *,struct inode *,struct dentry *,umode_t,bool);
    int (*atomic_open)(void);
    int (*getattr)(struct user_namespace *,const struct path *,struct kstat *,u32,unsigned);
};
struct super_block { unsigned s_flags; };
struct inode { bool hidden, dead, fuse, marked; struct inode_operations *i_op; struct super_block *i_sb; };
struct dentry { const char *name; struct inode *d_inode; int refs, d_seq; bool in_lookup, cached; };
struct mount { int mnt_id; };
struct path { struct dentry *dentry; struct mount *mnt; };
struct nameidata { struct path path; struct qstr last; unsigned flags,state,seq; };
struct file { unsigned f_mode; };
struct open_flags { int open_flag; umode_t mode; };
struct filename { const char *name; };
struct renamedata { struct inode *old_dir,*new_dir,**delegated_inode; struct dentry *old_dentry,*new_dentry; struct user_namespace *old_mnt_userns,*new_mnt_userns; unsigned flags; };
struct kstat { unsigned result_mask,attributes,attributes_mask; int mnt_id; };
static struct super_block sb;
static struct inode_operations ops;
static struct inode hidden_inode, visible_inode, created_inode, parent_inode;
static struct dentry root, pool[64];
static struct mount mnt;
static int used, cache_mode, lookup_error, reval_error, reval_once, alloc_error;
static int lookups, allocs, dputs, done_calls, mkdir_calls, rename_calls, create_calls, atomic_calls;
static int locks, writes, paths, names, child_retry, parent_retry, unlazy_fail;
static int spoof_mode, getattr_error;
static unsigned getattr_mask;
static bool app, sibling_exists, alias_lookup;
static const char *mutation_source,*mutation_dest;
static struct inode *disk_inode(const char *name) {
    if (!strcmp(name,"hidden")) return &hidden_inode;
    if (!strcmp(name,"visible")) return &visible_inode;
    if (!strcmp(name,"..5.u.S") && sibling_exists) return &visible_inode;
    return NULL;
}
static struct dentry *fresh(const char *name) {
    assert(used<64); struct dentry *d=&pool[used++];
    *d=(struct dentry){.name=name,.refs=1}; return d;
}
static struct dentry *cached(const struct qstr *name, bool ref) {
    for(int i=0;i<used;i++) if(pool[i].cached&&!strcmp(pool[i].name,name->name)) {
        if(ref) pool[i].refs++; return &pool[i];
    }
    if(!cache_mode) return NULL;
    struct dentry *d=fresh(name->name); d->refs=ref; d->cached=true; d->d_inode=disk_inode(name->name); return d;
}
static struct dentry *d_lookup(struct dentry *dir,const struct qstr *name) { (void)dir; return cached(name,true); }
static struct dentry *__d_lookup(struct dentry *dir,const struct qstr *name) { return d_lookup(dir,name); }
static struct dentry *__d_lookup_rcu(struct dentry *dir,const struct qstr *name,unsigned *seq) { (void)dir; *seq=0; return cached(name,false); }
static struct dentry *d_alloc(struct dentry *dir,const struct qstr *name) { (void)dir; allocs++; if(alloc_error) return NULL; return fresh(name->name); }
static struct dentry *d_alloc_parallel(struct dentry *dir,const struct qstr *name,int *wq) {
    (void)dir;(void)wq; allocs++; if(alloc_error) return ERR_PTR(-ENOMEM);
    struct dentry *d=cached(name,true); if(d) return d;
    d=fresh(name->name);d->in_lookup=true;return d;
}
static void dput(struct dentry *d) { assert(d&&!IS_ERR(d)&&d->refs>0);d->refs--;dputs++; }
static void d_lookup_done(struct dentry *d) { done_calls++; d->in_lookup=false; }
static bool d_in_lookup(struct dentry *d) { return d->in_lookup; }
static void d_invalidate(struct dentry *d) { d->cached=false;cache_mode=0; }
static int d_revalidate(struct dentry *d,unsigned flags) { (void)d;(void)flags; int e=reval_error; if(reval_once) { reval_error=1;reval_once=0; } return e; }
static bool susfs_is_inode_sus_path(struct inode *i) { return app&&i->hidden; }
static bool susfs_is_current_proc_umounted_app(void) { return app; }
static struct inode *d_backing_inode(struct dentry *d) { return d->d_inode; }
static int read_seqcount_retry(int *seq,unsigned s) { (void)seq;(void)s;return child_retry; }
static int __read_seqcount_retry(int *seq,unsigned s) { (void)seq;(void)s;return parent_retry; }
static bool try_to_unlazy(struct nameidata *nd) { if(unlazy_fail)return false;nd->flags&=~LOOKUP_RCU;return true; }
static bool try_to_unlazy_next(struct nameidata *nd,struct dentry *d,unsigned seq) { (void)seq;if(!try_to_unlazy(nd))return false;d->refs++;return true; }
static struct dentry *native_lookup(struct inode *i,struct dentry *d,unsigned f) {
    (void)i;(void)f;lookups++;if(lookup_error)return ERR_PTR(lookup_error);
    if(alias_lookup) { struct dentry *alias=fresh(d->name);alias->d_inode=disk_inode(d->name);return alias; }
    d->d_inode=disk_inode(d->name);return NULL;
}
static struct user_namespace *mnt_user_ns(struct mount *m) { (void)m;return NULL; }
static umode_t vfs_prepare_mode(struct user_namespace *u,struct inode *i,umode_t a,umode_t b,umode_t c) { (void)u;(void)i;(void)b;(void)c;return a; }
static int may_o_create(struct user_namespace *u,struct path *p,struct dentry *d,umode_t m) { (void)u;(void)p;(void)d;(void)m;return 0; }
static void audit_inode_child(struct inode *i,struct dentry *d,int t) { (void)i;(void)d;(void)t; }
static int native_create(struct user_namespace *u,struct inode *i,struct dentry *d,umode_t m,bool excl) { (void)u;(void)i;(void)m;(void)excl;create_calls++;mutation_dest=d->name;d->d_inode=&created_inode;return 0; }
static int atomic_marker(void) {return 0;}
static struct dentry *atomic_open(struct nameidata *nd,struct dentry *d,struct file *f,int flag,umode_t mode) {
    (void)nd;(void)f;atomic_calls++;
    if(d->in_lookup) { struct dentry *res=native_lookup(&parent_inode,d,0);d_lookup_done(d);if(res){dput(d);d=res;} }
    if(IS_ERR(d))return d;
    if(!d->d_inode&&(flag&O_CREAT))native_create(NULL,&parent_inode,d,mode,flag&O_EXCL);
    return d;
}
static int filename_parentat(int fd,struct filename *n,unsigned f,struct path *p,struct qstr *q,int *type) { (void)fd;(void)f;*p=(struct path){&root,&mnt};*q=(struct qstr){n->name,strlen(n->name)};*type=LAST_NORM;paths++;return 0; }
static int mnt_want_write(struct mount *m) { (void)m;writes++;return 0; }
static void mnt_drop_write(struct mount *m) { (void)m;writes--;assert(writes>=0); }
static void inode_lock_nested(struct inode *i,int type) { (void)i;(void)type;locks++; }
static void inode_unlock(struct inode *i) { (void)i;locks--;assert(locks>=0); }
static void path_put(struct path *p) { (void)p;paths--;assert(paths>=0); }
static bool d_is_positive(struct dentry *d) { return d->d_inode!=NULL; }
static bool d_is_negative(struct dentry *d) { return !d->d_inode; }
static bool d_is_dir(struct dentry *d) { (void)d;return false; }
static bool retry_estale(int err,unsigned flags) { (void)err;(void)flags;return false; }
static void putname(struct filename *n) { (void)n;names++; }
static int security_path_mkdir(struct path *p,struct dentry *d,umode_t m) { (void)p;(void)d;(void)m;return 0; }
static umode_t mode_strip_umask(struct inode *i,umode_t m) { (void)i;return m; }
static int vfs_mkdir(struct user_namespace *u,struct inode *i,struct dentry *d,umode_t m) { (void)u;(void)i;(void)m;mkdir_calls++;mutation_dest=d->name;d->d_inode=&created_inode;return 0; }
static struct dentry *lock_rename(struct dentry *a,struct dentry *b) { (void)a;(void)b;locks++;return NULL; }
static void unlock_rename(struct dentry *a,struct dentry *b) { (void)a;(void)b;locks--;assert(locks>=0); }
static int security_path_rename(struct path *a,struct dentry *b,struct path *c,struct dentry *d,unsigned flags) { (void)a;(void)b;(void)c;(void)d;(void)flags;return 0; }
static int vfs_rename(struct renamedata *rd) {rename_calls++;mutation_source=rd->old_dentry->name;mutation_dest=rd->new_dentry->name;return 0;}
static int break_deleg_wait(struct inode **i) { (void)i;return 0; }
static bool susfs_is_current_app_uid(void) {return app;}
static bool susfs_is_inode_sus_kstat(struct inode *i,bool *fuse) {*fuse=i->fuse;return i->marked;}
static void generic_fillattr(struct user_namespace *u,struct inode *i,struct kstat *s) {(void)u;(void)i;(void)s;}
static void susfs_sus_kstat_spoof_generic_fillattr(struct inode *i,struct kstat *s,unsigned flag) {(void)i;(void)s;spoof_mode=flag;}
static int native_getattr(struct user_namespace *u,const struct path *p,struct kstat *s,u32 mask,unsigned flags) {(void)u;(void)p;(void)mask;(void)flags;getattr_mask=s->result_mask;return getattr_error;}
static void reset(void) {
    memset(pool,0,sizeof(pool));used=0;ops=(struct inode_operations){.lookup=native_lookup,.create=native_create};
    hidden_inode=(struct inode){.hidden=true,.marked=true,.i_op=&ops,.i_sb=&sb};
    visible_inode=(struct inode){.i_op=&ops,.i_sb=&sb};created_inode=visible_inode;parent_inode=visible_inode;
    root=(struct dentry){.name="/",.d_inode=&parent_inode};mnt.mnt_id=42;
    cache_mode=lookup_error=reval_once=alloc_error=0;reval_error=1;
    lookups=allocs=dputs=done_calls=mkdir_calls=rename_calls=create_calls=atomic_calls=0;
    locks=writes=paths=names=child_retry=parent_retry=unlazy_fail=0;
    spoof_mode=getattr_error=0;getattr_mask=0;
    app=true;sibling_exists=alias_lookup=false;mutation_source=mutation_dest=NULL;
}
static void balanced(void) {
    for(int i=0;i<used;i++) {assert(pool[i].refs==0);assert(!pool[i].in_lookup);}
    assert(!locks&&!writes&&!paths);
}
'''

TESTS = r'''
static void expect_error(struct dentry *d,int error) { assert(IS_ERR(d));assert(PTR_ERR(d)==error);balanced(); }
static void test_excl(int cached_mode,int sibling,int alias,int flags) {
    reset();cache_mode=cached_mode;sibling_exists=sibling;alias_lookup=alias;
    struct qstr q={"hidden",6};struct dentry *d=lookup_one_qstr_excl(&q,&root,flags);
    expect_error(d,-ENOENT);assert(allocs==!cached_mode);assert(lookups==!cached_mode);assert(!done_calls);
}
static void test_mkdir(int cached_mode,int sibling,int hidden) {
    reset();cache_mode=cached_mode;sibling_exists=sibling;struct filename f={hidden?"hidden":"new"};
    int err=do_mkdirat(0,&f,0700);assert(err==(hidden?-ENOENT:0));assert(mkdir_calls==!hidden);
    if(!hidden)assert(!strcmp(mutation_dest,"new"));assert(names==1);balanced();
}
static void test_rename(int cached_mode,int sibling,int endpoint,int flags) {
    reset();cache_mode=cached_mode;sibling_exists=sibling;
    struct filename from={endpoint==1?"hidden":"visible"};struct filename to={endpoint==2?"hidden":"new"};
    int err=do_renameat2(0,&from,0,&to,flags);
    int expected=endpoint?-ENOENT:((flags&RENAME_EXCHANGE)?-ENOENT:0);
    assert(err==expected);assert(rename_calls==(!expected));
    if(!expected){assert(!strcmp(mutation_source,"visible"));assert(!strcmp(mutation_dest,"new"));}
    assert(names==2);balanced();
}
static void test_slow(int cached_mode,int sibling,int alias,int scenario) {
    reset();cache_mode=cached_mode;sibling_exists=sibling;alias_lookup=alias;
    int expected=-ENOENT;unsigned flags=ND_FLAGS_LOOKUP_LAST;
    if(scenario==1)flags|=LOOKUP_RCU;
    if(scenario==2){lookup_error=-EIO;cache_mode=0;expected=-EIO;}
    if(scenario==3){alloc_error=1;expected=-ENOMEM;}
    if(scenario==4){reval_error=-EACCES;cache_mode=1;expected=-EACCES;}
    if(scenario==5){reval_error=0;reval_once=1;cache_mode=1;}
    struct qstr q={"hidden",6};struct dentry *d=__lookup_slow(&q,&root,flags);
    expect_error(d,expected);assert(done_calls==lookups);assert(lookups<=1);
}
static void test_fast(int rcu,int scenario) {
    reset();cache_mode=1;struct nameidata nd={.path={&root,&mnt},.last={"hidden",6},.flags=rcu?LOOKUP_RCU:0,.state=ND_STATE_LOOKUP_LAST};
    int expected=rcu?-ECHILD:-ENOENT;
    if(scenario==1){child_retry=1;if(!rcu)expected=-ENOENT;}
    if(scenario==2){parent_retry=1;if(!rcu)expected=-ENOENT;}
    if(scenario==3){reval_error=-EIO;expected=-EIO;}
    if(scenario==4){reval_error=0;expected=0;}
    if(scenario==5){cache_mode=0;expected=0;}
    if(scenario==6){cache_mode=0;unlazy_fail=1;expected=rcu?-ECHILD:0;}
    struct inode *inode=NULL;unsigned seq=99;struct dentry *d=lookup_fast(&nd,&inode,&seq);
    if(expected)expect_error(d,expected);else{assert(!d);balanced();}
    assert(!done_calls);assert(!lookups);assert(!allocs);
    if(rcu&&scenario<3)assert(!dputs);
}
static void test_open(int cached_mode,int sibling,int atomic,int scenario) {
    reset();cache_mode=cached_mode;sibling_exists=sibling;if(atomic)ops.atomic_open=atomic_marker;
    const char *name="hidden";int expected=-ENOENT;
    if(scenario==1){name="new";expected=0;}
    if(scenario==2){name="visible";expected=0;}
    if(scenario==3){app=false;expected=0;}
    if(scenario==4){lookup_error=-EIO;cache_mode=0;expected=-EIO;}
    if(scenario==5){alias_lookup=true;cache_mode=0;}
    if(scenario==6){reval_error=-EACCES;cache_mode=1;expected=-EACCES;}
    if(scenario==7){reval_error=0;reval_once=1;cache_mode=1;}
    struct nameidata nd={.path={&root,&mnt},.last={name,strlen(name)},.state=ND_STATE_OPEN_LAST};
    struct file f={0};struct open_flags op={.open_flag=O_CREAT|O_WRONLY,.mode=0600};
    struct dentry *d=lookup_open(&nd,&f,&op,true);
    if(expected){expect_error(d,expected);assert(!atomic_calls&&!create_calls);}
    else{assert(!IS_ERR(d));assert(!strcmp(d->name,name));assert(d->d_inode);dput(d);balanced();}
    if(scenario==1){assert(create_calls==1);assert(!strcmp(mutation_dest,"new"));assert(atomic_calls==atomic);}
    if(app)assert(done_calls==lookups);
}
static void test_error(int scenario) {
    reset();struct qstr q={"hidden",6};int expected=-EIO;
    if(scenario==0)lookup_error=-EIO;
    if(scenario==1){alloc_error=1;expected=-ENOMEM;}
    if(scenario==2){parent_inode.dead=true;expected=-ENOENT;}
    if(scenario==3){cache_mode=1;reval_error=-EACCES;expected=-EACCES;}
    if(scenario==4){cache_mode=1;reval_error=0;reval_once=1;expected=-ENOENT;}
    expect_error(lookup_one_qstr_excl(&q,&root,0),expected);
}
static void test_stat(int fuse,int callback,int failure) {
    reset();hidden_inode.fuse=fuse;if(callback)ops.getattr=native_getattr;if(failure)getattr_error=-EIO;
    struct dentry d={.d_inode=&hidden_inode};struct path p={&d,&mnt};struct kstat s;
    int err=vfs_getattr_nosec(&p,&s,0,0);unsigned expected=fuse?STATX_SUS_KSTAT_FUSE:STATX_SUS_KSTAT;
    assert(err==((callback&&failure)?-EIO:0));assert((s.result_mask&(STATX_SUS_KSTAT|STATX_SUS_KSTAT_FUSE))==expected);
    assert(s.mnt_id==42);if(callback)assert((getattr_mask&(STATX_SUS_KSTAT|STATX_SUS_KSTAT_FUSE))==expected);
    assert(spoof_mode==(err?0:expected));balanced();
}
int main(int argc,char **argv) {
    assert(argc==6);int a=atoi(argv[2]),b=atoi(argv[3]),c=atoi(argv[4]),d=atoi(argv[5]);
    if(!strcmp(argv[1],"excl"))test_excl(a,b,c,d);
    else if(!strcmp(argv[1],"mkdir"))test_mkdir(a,b,c);
    else if(!strcmp(argv[1],"rename"))test_rename(a,b,c,d);
    else if(!strcmp(argv[1],"slow"))test_slow(a,b,c,d);
    else if(!strcmp(argv[1],"fast"))test_fast(a,b);
    else if(!strcmp(argv[1],"open"))test_open(a,b,c,d);
    else if(!strcmp(argv[1],"error"))test_error(a);
    else if(!strcmp(argv[1],"stat"))test_stat(a,b,c);
    else return 2;
    return 0;
}
'''


def main():
    # Baseline assertions are expected to fail; never generate core dumps.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "sources/common")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    src = args.source.resolve()
    namei = (src / "fs/namei.c").read_text()
    stat = (src / "fs/stat.c").read_text()
    generated = STUBS + "\n".join(extract(namei,n) for n in FUNCTIONS) + extract(stat,"vfs_getattr_nosec") + TESTS
    cases = []
    for cache in (0,1):
        for sibling in (0,1):
            for alias in (0,1):
                cases += [("excl",cache,sibling,alias,f) for f in (0,6,8,1)]
                cases += [("slow",cache,sibling,alias,s) for s in range(6)]
            cases += [("mkdir",cache,sibling,h,0) for h in (0,1)]
            cases += [("rename",cache,sibling,e,f) for e in range(3) for f in (0,1,2)]
            cases += [("open",cache,sibling,a,s) for a in (0,1) for s in range(8)]
    cases += [("fast",r,s,0,0) for r in (0,1) for s in range(7)]
    cases += [("error",s,0,0,0) for s in range(5)]
    cases += [("stat",f,c,e,0) for f in (0,1) for c in (0,1) for e in (0,1)]
    results = []
    with tempfile.TemporaryDirectory(prefix="susfs-namei-") as temp:
        c,exe = Path(temp)/"test.c",Path(temp)/"test"
        c.write_text(generated)
        subprocess.run(["cc","-std=gnu11","-O1","-g","-fsanitize=undefined","-fno-sanitize-recover=undefined",
                        "-Werror=implicit-function-declaration",str(c),"-o",str(exe)], check=True)
        for case in cases:
            run = subprocess.run([str(exe),*map(str,case)],text=True,capture_output=True,timeout=5)
            results.append({"case":list(case),"passed":run.returncode==0,"detail":run.stderr.strip()})
    report = {"source":str(src),"sha256":{n:hashlib.sha256((src/n).read_bytes()).hexdigest() for n in ("fs/namei.c","fs/stat.c")},
              "extracted_functions":FUNCTIONS+["vfs_getattr_nosec"],"tests":len(results),"passed":sum(x["passed"] for x in results),
              "scope":"Actual C bodies; mocked VFS and metadata. Not a kernel, concurrency, filesystem integration, or device test.","results":results}
    if args.output: args.output.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k!="results"},indent=2))
    for result in results:
        if not result["passed"]: print(json.dumps(result))
    raise SystemExit(any(not x["passed"] for x in results))

if __name__=="__main__": main()
