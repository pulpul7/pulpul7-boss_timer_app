"""Process-local change signals. Listeners must be nonblocking and Tk-free."""
import threading

_lock = threading.Lock()
_listeners = set()


def subscribe(callback):
    with _lock:
        _listeners.add(callback)
    def unsubscribe():
        with _lock:
            _listeners.discard(callback)
    return unsubscribe


def publish(root, server_id):
    with _lock:
        callbacks = tuple(_listeners)
    for callback in callbacks:
        try:
            callback(root, server_id)
        except Exception:
            pass  # A preparation failure must never roll back a successful save.
