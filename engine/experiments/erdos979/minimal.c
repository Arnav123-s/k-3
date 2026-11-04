/* Independent minimality search for the Erdős #979 task (builder-written verification, never shown to the system).
   Counts, for every n < limit, the representations n = p1^4+p2^4+p3^4+p4^4 with primes p1<=p2<=p3<=p4,
   window by window in increasing n, and prints the first n with exactly `ways` representations as JSON:
     {"ways": w, "first": n or null, "complete_below": bound}
   Each quadruple is counted once, as pair (p1,p2) plus pair (p3,p4) with p2 <= p3.
   cc -O2 -o minimal minimal.c && ./minimal 5 6000000000000 [window_bits]   */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned long long u64;
typedef struct { u64 s; int lo, hi; } pair;  /* s = q[lo] + q[hi], lo <= hi (prime indices) */
static int by_sum(const void *x, const void *y) {
    u64 a = ((const pair *)x)->s, b = ((const pair *)y)->s; return a < b ? -1 : a > b;
}
int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: minimal WAYS LIMIT [WINDOW_BITS]\n"); return 2; }
    int ways = atoi(argv[1]);
    u64 limit = strtoull(argv[2], 0, 10), window = 1ULL << (argc > 3 ? atoi(argv[3]) : 30);
    u64 pmax = 2; while (pmax * pmax * pmax * pmax < limit) pmax++;
    char *composite = calloc(pmax + 1, 1);
    u64 *q = malloc(sizeof(u64) * (pmax + 1)); int np = 0;
    for (u64 i = 2; i <= pmax; i++) {
        if (composite[i]) continue;
        q[np++] = i * i * i * i;
        for (u64 j = i * i; j <= pmax; j += i) composite[j] = 1;
    }
    long npairs = 0; pair *P = malloc(sizeof(pair) * (long)np * (np + 1) / 2);
    for (int a = 0; a < np; a++) for (int b = a; b < np; b++)
        if (q[a] + q[b] < limit) P[npairs++] = (pair){q[a] + q[b], a, b};
    qsort(P, npairs, sizeof(pair), by_sum);
    unsigned char *count = malloc(window);
    for (u64 lo = 0; lo < limit; lo += window) {
        u64 hi = lo + window < limit ? lo + window : limit;
        memset(count, 0, hi - lo);
        for (long i = 0; i < npairs && P[i].s < hi; i++) {
            u64 s1 = P[i].s, from = lo > s1 ? lo - s1 : 0;
            long l = 0, r = npairs;  /* first pair with sum >= from */
            while (l < r) { long m = (l + r) / 2; if (P[m].s < from) l = m + 1; else r = m; }
            for (long j = l; j < npairs && s1 + P[j].s < hi; j++)
                if (P[j].lo >= P[i].hi) {
                    unsigned char *c = &count[s1 + P[j].s - lo];
                    if (*c < 255) (*c)++;
                }
        }
        for (u64 n = lo; n < hi; n++)
            if (count[n - lo] == ways) { printf("{\"ways\": %d, \"first\": %llu, \"complete_below\": %llu}\n", ways, n, n + 1); return 0; }
        fprintf(stderr, "\rno %d-way number below %llu   ", ways, hi);
    }
    printf("{\"ways\": %d, \"first\": null, \"complete_below\": %llu}\n", ways, limit);
    return 0;
}
