"""Idle timeout and bounded local throttling for login attempts."""

from threading import Lock
from time import monotonic, time

from flask import request, session


class LoginLimiter:
    def __init__(self):
        self._buckets = {}
        self._lock = Lock()

    def allow(self, address: str, username: str) -> bool:
        keys = ((address,), (address, username.casefold()))
        now = monotonic()
        with self._lock:
            # Fixed window. An address cap covers username rotation, including unknown users.
            self._buckets = {
                key: value for key, value in self._buckets.items() if value[0] > now
            }
            new_keys = sum(key not in self._buckets for key in keys)
            if len(self._buckets) + new_keys > 8192:
                return False
            for key, limit in zip(keys, (40, 5)):
                if self._buckets.get(key, (0, 0))[1] >= limit:
                    return False
            for key in keys:
                expires, count = self._buckets.get(key, (now + 300, 0))
                self._buckets[key] = (expires, count + 1)
            return True

    def success(self, address: str, username: str) -> None:
        with self._lock:
            self._buckets.pop((address, username.casefold()), None)


def init_login_guard(app) -> None:
    app.config.setdefault("SESSION_IDLE_SECONDS", 1800)
    app.extensions["login_limiter"] = LoginLimiter()

    @app.before_request
    def expire_idle_session():
        if not session.get("username") or request.endpoint in {"static", "main.health"}:
            return
        now = int(time())
        last = session.get("last_activity")
        if not isinstance(last, int) or not 0 <= now - last < app.config["SESSION_IDLE_SECONDS"]:
            session.clear()
        else:
            session["last_activity"] = now
