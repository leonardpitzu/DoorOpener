"""HTTPS served by DoorOpener itself.

The certificate follows its files on disk: a renewal (Home Assistant's Let's
Encrypt app, certbot, ...) takes effect on the next connection, with no restart.
"""
import logging
import os
import ssl
import threading

logger = logging.getLogger("dooropener")


def server_context(certfile: str, keyfile: str) -> ssl.SSLContext:
    """A TLS server context for this certificate and key. Raises OSError if they do not load."""
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile, keyfile)
    return context


def _stamp(*paths: str) -> tuple:
    """Changes whenever one of the files is replaced, rewritten, or goes missing."""
    stamps = []
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            stamps.append(None)
        else:
            stamps.append((st.st_ino, st.st_mtime_ns, st.st_size))
    return tuple(stamps)


class Certificate:
    """The server certificate, kept in step with its files.

    ``context`` is what the server wraps every connection with. Each handshake
    swaps in the context for the files as they are at that moment, so the server
    itself never learns about renewals. Files that do not load (a renewal caught
    halfway, a key that does not match) leave the last good certificate in place
    and are tried again as soon as they change.
    """

    def __init__(self, certfile: str, keyfile: str):
        self.certfile = certfile
        self.keyfile = keyfile
        self._lock = threading.Lock()
        self._loaded = _stamp(certfile, keyfile)
        self._rejected: tuple | None = None
        self.context = server_context(certfile, keyfile)
        self.context.sni_callback = self._on_handshake
        self._current = self.context

    def current(self) -> ssl.SSLContext:
        """The context for the files as they are now, or the last one that loaded."""
        with self._lock:
            stamp = _stamp(self.certfile, self.keyfile)
            if stamp not in (self._loaded, self._rejected):
                try:
                    self._current = server_context(self.certfile, self.keyfile)
                except OSError as e:  # ssl.SSLError included
                    self._rejected = stamp
                    logger.warning("Keeping the current TLS certificate: the files on disk do not load (%s)", e)
                else:
                    self._loaded = stamp
                    logger.info("Serving the renewed TLS certificate from %s", self.certfile)
            return self._current

    def _on_handshake(self, sock: ssl.SSLSocket, _server_name: str | None, _context: ssl.SSLContext) -> None:
        # Called for every handshake, with or without SNI.
        sock.context = self.current()
