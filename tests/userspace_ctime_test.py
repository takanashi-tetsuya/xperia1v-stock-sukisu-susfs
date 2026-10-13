#!/usr/bin/env python3
"""Exercise the real upstream CLI argument parser with a recorded syscall."""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
JNI = ROOT / 'userspace/upstream-susfs/ksu_susfs/jni'
SOURCE = JNI / 'features/sus_kstat.c'
HARNESS = r'''
#define _GNU_SOURCE
#include <limits.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <unistd.h>
#define st_atime_nsec st_atim.tv_nsec
#define st_mtime_nsec st_mtim.tv_nsec
#define st_ctime_nsec st_ctim.tv_nsec
#define st_atimensec st_atim.tv_nsec
#define st_mtimensec st_mtim.tv_nsec
#define st_ctimensec st_ctim.tv_nsec
static long capture(long number, ...);
#define syscall capture
#include "sus_kstat.c"
static struct st_susfs_sus_kstat captured;
static int calls;
static long capture(long number, ...) {
    va_list ap; va_start(ap, number);
    unsigned int magic1=va_arg(ap,unsigned int), magic2=va_arg(ap,unsigned int);
    int command=va_arg(ap,int);
    struct st_susfs_sus_kstat *p=va_arg(ap,struct st_susfs_sus_kstat *);
    va_end(ap);
    if (number!=SYS_reboot || magic1!=KSU_INSTALL_MAGIC1 || magic2!=SUSFS_MAGIC ||
        (command!=0x55570 && command!=0x55571 && command!=0x55572)) exit(80);
    captured=*p; calls++; p->err=0; return 0;
}
void print_help_banner(void) {}
int get_file_stat(char *path, struct stat *s) {
    memset(s,0,sizeof(*s)); s->st_ino=777; s->st_ctime=123; s->st_ctim.tv_nsec=456;
    return 0;
}
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"failed line %d\n",__LINE__); return 1; } } while(0)
int main(void) {
    char *a[]={"ksu_susfs","add_sus_kstat_statically",".","default","default",
      "default","default","default","default","default","default",
      "default","default","default","default"};
    CHECK(add_sus_kstat_statically(15,a)==0 && captured.flags==0);
    a[11]="1700000000";
    CHECK(add_sus_kstat_statically(15,a)==0 && captured.flags==256 &&
      captured.spoofed_ctime_tv_sec==1700000000 && captured.spoofed_ino==777);
    a[11]="default"; a[12]="999";
    CHECK(add_sus_kstat_statically(15,a)==0 && captured.flags==512 && captured.spoofed_ctime_tv_nsec==999);
    a[11]="1700000001";
    CHECK(add_sus_kstat_statically(15,a)==0 && captured.flags==768 && captured.spoofed_ctime_tv_sec==1700000001);
    a[3]="888";
    CHECK(add_sus_kstat_statically(15,a)==0 && captured.flags==769 && captured.spoofed_ino==888);
    CHECK(add_sus_kstat(3,a)==0 && (captured.flags & 256) && captured.spoofed_ctime_tv_sec==123);
    CHECK(update_sus_kstat(3,a)==0 && (captured.flags & 256));
    CHECK(update_sus_kstat_full_clone(3,a)==0 && captured.flags==4095);
    int before=calls; a[11]="broken";
    CHECK(add_sus_kstat_statically(15,a)==-EINVAL && calls==before);
    puts("PASS: 9 actual CLI payload cases; no real syscalls performed");
    return 0;
}
'''

def main():
    with tempfile.TemporaryDirectory(prefix='ctime-cli-') as temp:
        t = Path(temp)
        (t / 'android').mkdir()
        (t / 'android/log.h').write_text('/* Android logging API is unused by this source. */\n')
        (t / 'test.c').write_text(HARNESS)
        subprocess.run(['cc', '-std=gnu11', '-Werror=implicit-function-declaration',
                        '-I', str(t), '-I', str(JNI / 'includes'), '-I', str(JNI / 'features'),
                        str(t / 'test.c'), '-o', str(t / 'test')], check=True)
        result = subprocess.run([str(t / 'test')], check=True, text=True, capture_output=True)
    report = {'status':'PASS', 'tests':9, 'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              'scope':'Actual CLI parser, recorded syscall payload; host stat fixture; no phone interaction',
              'result':result.stdout.splitlines()[-1]}
    (ROOT / 'logs').mkdir(exist_ok=True)
    (ROOT / 'logs/userspace_ctime_test.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
