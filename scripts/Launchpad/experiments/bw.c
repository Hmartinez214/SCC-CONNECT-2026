// Memory bandwidth ceiling probe: read-only, write-only (streaming stores),
// copy and triad over arrays of N doubles, OpenMP static schedule, first-touch
// by the same threads. Prints the best of REPS for each kernel in GB/s
// (bytes the kernel must move; streaming stores, so no write-allocate).
#include <omp.h>
#include <stdio.h>
#include <stdlib.h>
#include <immintrin.h>

#ifndef N
#define N (134217728L)
#endif
#define REPS 50

static double best(double t, double b) { return t < b ? t : b; }

int main(void) {
    double *a = aligned_alloc(2 << 20, N * 8), *b = aligned_alloc(2 << 20, N * 8), *c = aligned_alloc(2 << 20, N * 8);
#pragma omp parallel for schedule(static)
    for (long i = 0; i < N; i++) { a[i] = 1.0; b[i] = 2.0; c[i] = 0.0; }
    double tr = 1e9, tw = 1e9, tc = 1e9, tt = 1e9, sink = 0;
    for (int r = 0; r < REPS; r++) {
        double t0 = omp_get_wtime(), s = 0;
#pragma omp parallel for schedule(static) reduction(+:s)
        for (long i = 0; i < N; i++) s += a[i];
        double t1 = omp_get_wtime(); sink += s;
#pragma omp parallel for schedule(static)
        for (long i = 0; i < N; i += 8) _mm512_stream_pd(&c[i], _mm512_set1_pd(r));
        _mm_sfence();
        double t2 = omp_get_wtime();
#pragma omp parallel for schedule(static)
        for (long i = 0; i < N; i += 8) _mm512_stream_pd(&c[i], _mm512_load_pd(&a[i]));
        _mm_sfence();
        double t3 = omp_get_wtime();
#pragma omp parallel for schedule(static)
        for (long i = 0; i < N; i += 8)
            _mm512_stream_pd(&a[i], _mm512_fmadd_pd(_mm512_set1_pd(3.0), _mm512_load_pd(&c[i]), _mm512_load_pd(&b[i])));
        _mm_sfence();
        double t4 = omp_get_wtime();
        if (r) { tr = best(t1 - t0, tr); tw = best(t2 - t1, tw); tc = best(t3 - t2, tc); tt = best(t4 - t3, tt); }
    }
    double g = N * 8 / 1e9;
    printf("read %.1f  write %.1f  copy %.1f  triad %.1f  GB/s  (threads %d, sink %g)\n",
           g / tr, g / tw, 2 * g / tc, 3 * g / tt, omp_get_max_threads(), sink);
    return 0;
}
