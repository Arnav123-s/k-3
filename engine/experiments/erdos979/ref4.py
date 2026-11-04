# Builder reference (verification only, never shown to the system). python3 ref4.py PRIME_LIMIT
# Reference (builder-written, backup only): smallest n that is p1^4+p2^4+p3^4+p4^4 (p1<=p2<=p3<=p4 primes) in exactly w ways.
import sys
from collections import Counter
from itertools import combinations_with_replacement
L = int(sys.argv[1])
sieve = bytearray([1]) * (L + 1); sieve[0:2] = b'\0\0'
for i in range(2, int(L ** .5) + 1):
    if sieve[i]: sieve[i*i::i] = bytearray(len(sieve[i*i::i]))
P = [p ** 4 for p in range(L + 1) if sieve[p]]
B = (L + 1) ** 4  # every sum below B is fully counted (all parts < B)
c = Counter()
for t in combinations_with_replacement(P, 4):
    s = sum(t)
    if s < B: c[s] += 1
best = {}
for s, w in c.items():
    if w not in best or s < best[w]: best[w] = s
print({w: best[w] for w in sorted(best)}, 'complete below', B)
