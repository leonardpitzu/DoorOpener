"""HTTPS served by DoorOpener itself.

The certificate follows its files on disk: a renewal (Home Assistant's Let's
Encrypt app, certbot, ...) takes effect on the next connection, with no restart.
Connections also come with a deadline, so slow clients cannot tie up the server.
"""
import contextlib
import errno
import logging
import os
import ssl
import threading
import time

logger = logging.getLogger("dooropener")

# How long a client gets for the TLS handshake and for sending each request, and
# for taking each response. Gunicorn reads from blocking sockets with no limit, so
# without this a client trickling a byte now and then holds a worker thread for
# as long as it likes, and a handful of them freeze the keypad.
REQUEST_TIMEOUT = 15.0
SEND_TIMEOUT = 60.0


class _BoundedSocket(ssl.SSLSocket):
    """A TLS connection that never waits on its client past a deadline.

    Gunicorn switches the socket to blocking mode before it reads each request;
    here that switch starts a ``REQUEST_TIMEOUT`` deadline, and every read may
    only wait for what is left of it. A client that misses it is dropped as if it
    had hung up, which gunicorn closes quietly.

    Browsers open spare connections ahead of time and may leave them idle; those
    are dropped without a word. Only a client that began a request, or the TLS
    handshake, and then stalled is logged.
    """

    _deadline: float | None = None
    _started = False  # the client has sent part of the current request

    def setblocking(self, flag: bool) -> None:
        if flag:
            self._deadline = time.monotonic() + REQUEST_TIMEOUT
            self._started = False
            super().settimeout(REQUEST_TIMEOUT)
        else:
            self._deadline = None
            super().setblocking(False)

    def do_handshake(self, block: bool = False) -> None:
        if self.gettimeout() is None:
            self.setblocking(True)
        self._started = True  # a handshake is under way once the client sent its hello
        with self._bounded():
            super().do_handshake(block)
        self._started = False

    def recv(self, buflen: int = 1024, flags: int = 0) -> bytes:
        with self._bounded():
            data = super().recv(buflen, flags)
        self._started = self._started or bool(data)
        return data

    def recv_into(self, buffer, nbytes: int | None = None, flags: int = 0) -> int:
        with self._bounded():
            count = super().recv_into(buffer, nbytes, flags)
        self._started = self._started or count > 0
        return count

    def send(self, data, flags: int = 0) -> int:
        self._for_sending()
        return super().send(data, flags)

    def sendall(self, data, flags: int = 0) -> None:
        self._for_sending()
        super().sendall(data, flags)

    def _for_sending(self) -> None:
        if self._deadline is not None:
            super().settimeout(SEND_TIMEOUT)

    @contextlib.contextmanager
    def _bounded(self):
        """Let a read wait only for the time left before the deadline."""
        if self._deadline is not None:
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                self._drop()
            super().settimeout(remaining)
        try:
            yield
        except TimeoutError:
            self._drop()

    def _drop(self):
        if self._started:
            try:
                peer = self.getpeername()
            except OSError:
                peer = None
            logger.info("Dropped a client that was too slow to send its request: %s",
                        peer[0] if isinstance(peer, tuple) else "unknown")
        raise ConnectionResetError(errno.ECONNRESET, "client too slow") from None


def server_context(certfile: str, keyfile: str) -> ssl.SSLContext:
    """A TLS server context for this certificate and key. Raises OSError if they do not load."""
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile, keyfile)
    context.sslsocket_class = _BoundedSocket
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
