# NOTE: Not written. A regression test for this issue must import app, connect a
# Socket.IO test client, and assert that unauthenticated state-changing events
# are refused while authenticated ones pass. That requires knowing the actual
# event names and the session/auth key used by app.py, neither of which is
# present in the supplied excerpt. Writing the test blind would either fail to
# import or assert against invented event names, which is worse than reporting
# the gap.
