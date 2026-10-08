# This file no longer defines tests. Importing app here pulled in the real
# guitarix/JACK client without the fake rig that run_server_tests.py builds
# first (a free GX_PORT, FAKE_JACK_DIR, tests/fakes/bin first on PATH and the
# fake engine running), so `python -m unittest` would have talked to a real
# rig or hung. The guard is covered by the "auth:" and "demo-only server:"
# scenarios in run_server_tests.py, which run inside that fake rig.
