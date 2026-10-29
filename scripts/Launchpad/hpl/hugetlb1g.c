// LD_PRELOAD shim: give Intel HPL's large hugetlb allocations 1 GB pages.
//
// Intel HPL (HPL_LARGEPAGE=1) maps its matrix and buffers with
// mmap(MAP_HUGETLB) and no page-size flag, i.e. the default huge page size
// (2 MB here). This adds MAP_HUGE_1GB to such mappings of at least
// HUGETLB1G_MIN bytes (default 256 MB) and falls back to the original call if
// the 1 GB pool cannot serve it. Both pools must be reserved beforehand.
//
//   gcc -O2 -shared -fPIC hugetlb1g.c -o hugetlb1g.so -ldl
//   LD_PRELOAD=$PWD/hugetlb1g.so HPL_LARGEPAGE=1 xhpl ...
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>

#ifndef MAP_HUGE_1GB
#define MAP_HUGE_1GB (30 << 26)
#endif

static void *(*real_mmap)(void *, size_t, int, int, int, off_t);
static size_t min_bytes = 256UL << 20;
static int verbose;

__attribute__((constructor)) static void init(void) {
    real_mmap = dlsym(RTLD_NEXT, "mmap");
    const char *m = getenv("HUGETLB1G_MIN"), *v = getenv("HUGETLB1G_VERBOSE");
    if (m) min_bytes = strtoull(m, NULL, 0);
    verbose = v && *v == '1';
}

void *mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off) {
    if (!real_mmap) init();
    if ((flags & MAP_HUGETLB) && !(flags & (0x3f << 26)) && len >= min_bytes) {
        void *p = real_mmap(addr, len, prot, flags | MAP_HUGE_1GB, fd, off);
        if (verbose) fprintf(stderr, "hugetlb1g: %zu MB -> %s\n", len >> 20, p == MAP_FAILED ? "1 GB failed, 2 MB" : "1 GB pages");
        if (p != MAP_FAILED) return p;
    }
    return real_mmap(addr, len, prot, flags, fd, off);
}
void *mmap64(void *addr, size_t len, int prot, int flags, int fd, off_t off) __attribute__((alias("mmap")));
