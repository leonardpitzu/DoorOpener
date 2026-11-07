"""Gunicorn settings, shared by the Home Assistant app and the standalone container.

Gunicorn reads this file from its working directory, so both just run
``gunicorn app:app``. Everything comes from config.py, so options.json stays
the one place to configure DoorOpener.

Gunicorn takes every public name here as a setting (``config`` is one), so the
modules are imported under private names.
"""
import config as _config
import tls as _tls

bind = f"0.0.0.0:{_config.server_port}"
# One worker: the brute-force counters live in memory and must see every request.
workers = 1
threads = 2
timeout = 60

if _config.ssl_enabled:
    certfile = _config.ssl_certfile
    keyfile = _config.ssl_keyfile
    _certificate = _tls.Certificate(certfile, keyfile)

    def ssl_context(conf, default_ssl_context_factory):
        """Every connection gets the certificate as it is on disk at that moment."""
        return _certificate.context
