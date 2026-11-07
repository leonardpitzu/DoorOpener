"""The admin panel inside Home Assistant, served through its ingress proxy.

Home Assistant opens it from the app's Open Web UI button, or from a sidebar
entry when the user switches one on.

Home Assistant relays ``/api/hassio_ingress/<token>/...`` from a logged-in
user's browser to the Supervisor, which forwards it over plain HTTP to this
listener on the app's internal network. The port is not published on the host,
so only the Supervisor can reach it, and it names the Home Assistant user every
request comes from. That login (with its two-factor step) is what protects the
admin panel here; DoorOpener's own admin password is not involved.

Only a Home Assistant app has this; standalone, the listener never starts.
"""

import logging
import re
import threading
from dataclasses import dataclass

from werkzeug.serving import make_server

PORT = 8099  # matches ingress_port in config.yaml
SUPERVISOR_IP = "172.30.32.2"
ADMIN_GROUP = "system-admin"
ENVIRON_KEY = "dooropener.ingress"

_INGRESS_PATH = re.compile(r"/api/hassio_ingress/[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Visitor:
    """The Home Assistant user behind an ingress request."""

    name: str
    admin: bool


class IngressApp:
    """WSGI wrapper for the ingress listener.

    Refuses anything not from the Supervisor, mounts the app under the ingress
    path so every URL it builds carries it, and records the Home Assistant user
    in the WSGI environ, where no header can forge it.
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ.get("REMOTE_ADDR") != SUPERVISOR_IP:
            start_response("403 Forbidden", [("Content-Type", "text/plain")])
            return [b"Only Home Assistant's ingress proxy may connect here.\n"]
        prefix = environ.get("HTTP_X_INGRESS_PATH", "")
        environ["SCRIPT_NAME"] = prefix if _INGRESS_PATH.fullmatch(prefix) else ""
        # The Supervisor sets these itself and drops any a browser sent.
        groups = environ.get("HTTP_X_REMOTE_USER_GROUPS", "").split(",")
        name = (environ.get("HTTP_X_REMOTE_USER_DISPLAY_NAME") or environ.get("HTTP_X_REMOTE_USER_NAME")
                or "Home Assistant user")
        environ[ENVIRON_KEY] = Visitor(name=name, admin=ADMIN_GROUP in groups)
        return self.app(environ, start_response)


def visitor(environ) -> Visitor | None:
    """The Home Assistant user, for a request that came through ingress; otherwise None."""
    return environ.get(ENVIRON_KEY)


def serve(app, port: int = PORT):
    """Start the ingress listener in a background thread of this process.

    Same process as the public server, so both share the brute-force counters,
    the users store and the audit log.
    """
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no access log per request
    # All interfaces of the container, which only the app network reaches: the port is not published.
    server = make_server("0.0.0.0", port, IngressApp(app), threaded=True)  # nosec B104
    threading.Thread(target=server.serve_forever, name="ingress", daemon=True).start()
    logging.getLogger("dooropener").info("Admin panel available inside Home Assistant (Open Web UI)")
    return server
