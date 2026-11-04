# The scripted actors of the existing tests drive the model-side protocol (they call find_methods and
# validate_method themselves). They test the solver's mechanisms under that protocol; the host protocol
# (retrieval before the first call, validation after each stored method) is tested in
# tests/test_host_protocol.py, which turns it on.
from kuttaka_runtime.solve import Solver as _Solver

_Solver.host_protocol = False
