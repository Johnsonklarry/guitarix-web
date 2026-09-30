"""gunicorn entry point.

    gunicorn -k gthread -w 1 --threads 100 -b 0.0.0.0:5000 wsgi:app

Exactly one worker (-w 1): the guitarix connection, the recorder and the
Socket.IO session state all live in this process. The app runs Flask-SocketIO
in threading mode, so the matching gunicorn worker is gthread; with
simple-websocket installed it carries real websockets.
"""

from app import app, start_services

start_services()

__all__ = ["app"]
