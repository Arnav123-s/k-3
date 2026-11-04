import copy
import itertools
import unittest
from kuttaka_runtime.sequence_memory import encode, restore, read


class SequenceMemoryTests(unittest.TestCase):
    def test_exhaustive_small_sequences(self):
        for n in range(6):
            for sequence in itertools.product((-1, 0, 1), repeat=n):
                values = list(sequence)
                record, cost = encode(values, context={'units': 'integer'})
                self.assertEqual(restore(record, context={'units': 'integer'}), values)
                self.assertLessEqual(cost['payload_bytes'], cost['literal_bytes'])

    def test_structured_data_and_rare_exception(self):
        values = [i*i + 3*i + 7 for i in range(1000)]
        values[573] += 19
        record, _ = encode(values, context='observed')
        self.assertEqual(record['codec'], 'differences')
        self.assertEqual(record['residuals'], [[573, 19]])
        self.assertEqual(restore(record, context='observed'), values)
        self.assertEqual(read(record, 1000, context='observed')['status'], 'unknown')
        self.assertEqual(read(record, 573, context='observed')['answer'], values[573])

    def test_context_and_corruption(self):
        record, _ = encode(list(range(1000)), context='metres')
        with self.assertRaisesRegex(ValueError, 'context'):
            restore(record, context='millimetres')
        bad = copy.deepcopy(record)
        bad['seeds'][0] += 1
        with self.assertRaisesRegex(ValueError, 'digest'):
            restore(bad, context='metres')
        bad = copy.deepcopy(record)
        bad['residuals'] = [[2, 1], [2, -1]]
        with self.assertRaisesRegex(ValueError, 'index'):
            restore(bad, context='metres')

    def test_snapshot_and_input_bounds(self):
        values = [1, 2]
        context = {'a': [1]}
        record, _ = encode(values, context=context)
        values[0] = 9
        context['a'][0] = 2
        self.assertEqual(restore(record, context={'a': [1]}), [1, 2])
        for bad in ([True], [1.0], [2**513]):
            with self.assertRaises(ValueError):
                encode(bad, context=None)
