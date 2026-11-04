"""The Erdős #979 demo task: one prompt, no examples. Completion of the task is the success criterion.

Erdős Problem #979 asks whether f_k(n), the number of ways to write n = p_1^k + ... + p_k^k with primes,
is unbounded. Erdős proved it for k = 2 and k = 3; k = 4 is open. This task asks for new data on k = 4.
"""

WAYS = 5

PROMPT = (f'Find the smallest positive integer n that can be written in exactly {WAYS} ways as '
          'p1^4 + p2^4 + p3^4 + p4^4 with primes p1 <= p2 <= p3 <= p4 (ways that differ only in order '
          'count once). Return {"n": n, "representations": [[p1, p2, p3, p4], ...]} listing all '
          f'{WAYS} ways.')
