"""R8. shastra.build is documented "read only" but opens ArtifactMemory, which takes the workspace's single
writer lock (artifacts.py). Service.library() does not take Service.lock, and the HTTP server is threaded:
GET /library while a solve holds the memory fails, and a solve that opens the memory while the library is
being built fails. Local only; no model or executor runs here."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kuttaka_runtime import shastra
from kuttaka_runtime.artifacts import ArtifactMemory
from kuttaka_runtime.serve import Service
from kuttaka_runtime.solve import Workspace, settings
from tests.test_solve_pramana import make
from tests.test_solve_review_regressions import check


def workspace_with_a_method():
    solver, ws, _ = make(lambda c: {'contract': 'x squared', 'input': None, 'checks': [check(2, 4), check(3, 9)]},
                         'def run(x):\n    return x * x\n')
    assert solver.solve('Square a number.')['status'] == 'checked'
    return ws


class R8LibraryTakesTheWriterLock(unittest.TestCase):
    def test_get_library_fails_while_a_solve_holds_the_memory(self):
        ws = workspace_with_a_method()
        service = Service(settings(), ws)
        with ArtifactMemory(ws.memory):            # what Solver.attempt holds for a whole model attempt
            try:
                body = service.library()
            except OSError as error:
                self.fail(f'GET /library during a solve raised {type(error).__name__}: {error}')
        self.assertEqual(body['counts']['methods'], 1)

    def test_a_solve_that_opens_the_memory_while_the_library_is_built_fails(self):
        ws = workspace_with_a_method()
        real_get, failures = ArtifactMemory.get, []

        def get(self, artifact):   # the solver's thread opens the memory in the middle of build()
            try:
                ArtifactMemory(ws.memory).close()
            except OSError as error:
                failures.append(type(error).__name__)
            return real_get(self, artifact)
        with mock.patch.object(ArtifactMemory, 'get', get):
            shastra.build(ws)
        self.assertEqual(failures, [], 'the solver could not open its memory while the library was being read')


if __name__ == '__main__':
    unittest.main()
