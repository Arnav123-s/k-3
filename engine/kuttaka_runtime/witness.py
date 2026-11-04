"""Others' work as a witness: a published table that can confirm or contradict a checked method.

The owner, 2026-11-02: "knowing isn't the same as learning; if you can't verify your own work then you look for
others' works that can verify your work." In the first-hand sessions of that day the builder's answers were shown
right by nothing it derived itself, but by published sequences (OEIS A007678, A064018, A126255), read afterwards.
In the tradition this is testimony (śabda) from a trustworthy source, one of the means of knowledge, and the
correction from outside that the builder's sessions needed (research/kuttaka_method/BEFORE_ARYABHATA.md).

A task whose input is one non-negative integer and whose result is an integer is a sequence. The checked method is
run on consecutive small values; the On-Line Encyclopedia of Integer Sequences is searched for those terms; a
sequence that has every one of them at the same indices (MIN_TERMS at least, not all equal) is the witness. Its
table (b-file) is then compared with the method at a few larger indices, and at the requested input when the
table reaches it. A disagreement there is a perceived failure; agreement is testimony, labelled as such: it is
neither the model's derivation nor runtime discovery.

Off unless the Solver is given a witness. It must stay off in any registered run that compares "model + Kuttaka"
with the model alone: a published table is knowledge from outside both, and on a suite it could carry the answer.
"""
import json
import urllib.parse
import urllib.request

OEIS = 'https://oeis.org'
MIN_TERMS = 8        # consecutive terms the method and the sequence must share before the sequence is a witness
SEARCH_RESULTS = 5   # sequences tried, in the search's order


def _fetch(url, timeout=20):
    request = urllib.request.Request(url, headers={'User-Agent': 'kuttaka-witness (research; one query per task)'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode('utf-8', 'replace')


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


class OEISWitness:
    """Searches the OEIS by terms and reads b-files. `fetch(url) -> text` can be replaced (tests, a local mirror)."""
    name = 'OEIS'

    def __init__(self, fetch=None):
        self.fetch = fetch or _fetch

    def candidates(self, terms):
        """[(A-number, name, offset, data terms)] for sequences containing `terms` in order, or [] on any failure."""
        query = urllib.parse.urlencode({'q': ','.join(str(t) for t in terms), 'fmt': 'json'})
        try:
            found = json.loads(self.fetch(f'{OEIS}/search?{query}'))
        except Exception:
            return []
        results = found if isinstance(found, list) else (found or {}).get('results') or []
        out = []
        for entry in results[:SEARCH_RESULTS]:
            if not isinstance(entry, dict) or not isinstance(entry.get('number'), int):
                continue
            offset = _int(str(entry.get('offset', '')).split(',')[0])
            data = [_int(t) for t in str(entry.get('data', '')).split(',') if t.strip()]
            if offset is None or not data or None in data:
                continue
            out.append(('A%06d' % entry['number'], str(entry.get('name', ''))[:200], offset, data))
        return out

    def table(self, number):
        """{n: a(n)} from the sequence's b-file, or {} on any failure."""
        digits = number[1:]
        try:
            text = self.fetch(f'{OEIS}/{number}/b{digits}.txt')
        except Exception:
            return {}
        out = {}
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and not line.startswith('#'):
                n, value = _int(parts[0]), _int(parts[1])
                if n is not None and value is not None:
                    out[n] = value
        return out

    def find_all(self, values):
        """Every witness for `values` ({n: int}, consecutive n), in the search's order: [(A-number, name, {n: a(n)}
        from its data)], or []. Two sequences can share their first terms and part later; each is examined.

        A sequence must hold every one of the values at the same index n (by its offset), MIN_TERMS at least."""
        ns = sorted(values)
        for skip in range(3):   # a table may start later than the method's first value (an offset of 2, say)
            kept = ns[skip:]
            terms = [values[n] for n in kept]
            if len(terms) < MIN_TERMS or len(set(terms)) < 2:
                return []
            found = []
            for number, name, offset, data in self.candidates(terms):
                published = {offset + i: v for i, v in enumerate(data)}
                if all(published.get(n) == values[n] for n in kept):
                    found.append((number, name, published))
            if found:
                return found
        return []

    def find(self, values):
        """The first witness for `values` (see find_all), or None."""
        found = self.find_all(values)
        return found[0] if found else None
