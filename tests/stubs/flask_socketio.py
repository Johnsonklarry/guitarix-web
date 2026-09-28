import threading
import time


class SocketIO:
    def __init__(self, flask_app, **kwargs):
        self.handlers = {}
        self.emitted = []
        self._app = flask_app

    def on(self, event):
        def decorator(func):
            self.handlers[event] = func
            return func
        return decorator

    def emit(self, event, data=None, **kw):
        self.emitted.append((event, data))

    def sleep(self, seconds):
        time.sleep(seconds)

    def start_background_task(self, target, *args, **kwargs):
        thread = threading.Thread(target=target, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return thread

    def run(self, app, **kw):
        pass


__all__ = ['SocketIO']