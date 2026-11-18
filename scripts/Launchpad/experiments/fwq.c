// OS-noise probe: one thread pinned per CPU spins on the clock for SECONDS and
// records every gap > THRESH_NS (time the CPU was taken away from it).
// Prints per CPU: detour count, total stolen time, max detour, and a log2
// histogram of all detours; plus a timeline of stolen time per 100 ms bucket.
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#define THRESH_NS 2000
#define BUCKET_NS 100000000L
static int seconds;
static long *timeline;   // [cpu][bucket] stolen ns
static int nbuckets;
struct stat { long n, total, max, hist[32]; } *st;

static long now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1000000000L + t.tv_nsec; }

static void *spin(void *arg) {
    int cpu = (int)(long)arg;
    cpu_set_t set; CPU_ZERO(&set); CPU_SET(cpu, &set);
    pthread_setaffinity_np(pthread_self(), sizeof set, &set);
    struct stat *s = &st[cpu];
    long start = now(), end = start + seconds * 1000000000L, prev = start, t;
    while ((t = now()) < end) {
        long gap = t - prev;
        if (gap > THRESH_NS) {
            s->n++; s->total += gap; if (gap > s->max) s->max = gap;
            s->hist[63 - __builtin_clzl(gap)]++;
            timeline[(long)cpu * nbuckets + (t - start) / BUCKET_NS] += gap;
        }
        prev = t;
    }
    return NULL;
}

int main(int argc, char **argv) {
    seconds = argc > 1 ? atoi(argv[1]) : 30;
    int ncpu = sysconf(_SC_NPROCESSORS_ONLN);
    nbuckets = seconds * 10 + 1;
    st = calloc(ncpu, sizeof *st);
    timeline = calloc((long)ncpu * nbuckets, sizeof *timeline);
    pthread_t th[1024];
    for (long c = 0; c < ncpu; c++) pthread_create(&th[c], NULL, spin, (void *)c);
    for (int c = 0; c < ncpu; c++) pthread_join(th[c], NULL);
    printf("cpu n total_us max_us hist(log2 ns from 2^11)\n");
    for (int c = 0; c < ncpu; c++) {
        printf("%d %ld %.1f %.1f", c, st[c].n, st[c].total / 1e3, st[c].max / 1e3);
        for (int b = 11; b < 32; b++) printf(" %ld", st[c].hist[b]);
        printf("\n");
    }
    printf("timeline_ms_per_100ms_bucket(max over cpus,sum over cpus)\n");
    for (int b = 0; b < nbuckets; b++) {
        long mx = 0, sum = 0;
        for (int c = 0; c < ncpu; c++) { long v = timeline[(long)c * nbuckets + b]; sum += v; if (v > mx) mx = v; }
        printf("%d %.3f %.3f\n", b, mx / 1e6, sum / 1e6);
    }
    return 0;
}
