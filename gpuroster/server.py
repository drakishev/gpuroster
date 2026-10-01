"""Single-process production serving with explicit collector ownership."""

import argparse
import fcntl
import logging
import os
import signal
import threading
from pathlib import Path

from waitress import create_server, wasyncore
from waitress.task import ThreadedTaskDispatcher

from gpuroster import __version__

LOG = logging.getLogger(__name__)


class InstanceLock:
    """Keep the lock inode after exit so waiting starters cannot split ownership."""

    def __init__(self, database):
        self.path = Path(database).expanduser().resolve()
        self.descriptor = None

    def __enter__(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.descriptor = os.open(
            str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600
        )
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.descriptor)
            self.descriptor = None
            raise
        return self

    def __exit__(self, *args):
        os.close(self.descriptor)
        self.descriptor = None


class HTTPServer:
    """Own Waitress's socket map and worker threads, including failed binds.

    These lifecycle hooks are tested against the pinned Waitress version.
    Requests are bounded to four workers and 100 open channels.
    """

    def __init__(self, application, host, port):
        self.channels = {}
        self.dispatcher = ThreadedTaskDispatcher()
        try:
            self.dispatcher.set_thread_count(4)
            self.server = create_server(
                application,
                host=host,
                port=port,
                map=self.channels,
                _dispatcher=self.dispatcher,
                clear_untrusted_proxy_headers=True,
                expose_tracebacks=False,
                max_request_header_size=16384,
                max_request_body_size=1024,
                connection_limit=100,
                channel_timeout=30,
                cleanup_interval=5,
                ident="GPU Roster",
            )
        except BaseException:
            self.close()
            raise

    def run(self, stop):
        # A signal only sets the event; it cannot interrupt lifecycle locks.
        while not stop.is_set():
            wasyncore.loop(timeout=0.2, count=1, map=self.channels, use_poll=True)

    def close(self):
        try:
            self.dispatcher.shutdown(timeout=5)
        finally:
            wasyncore.close_all(map=self.channels, ignore_all=True)


def serve(application, stop):
    from gpuroster.app import _is_loopback

    config = application.config
    if not _is_loopback(config["BIND_HOST"]) and not config["AUTH_USER"]:
        raise ValueError("Remote binding requires configured authentication")
    service = application.extensions["collector"]
    with InstanceLock(config["DB_PATH"]):
        server = HTTPServer(application, config["BIND_HOST"], config["BIND_PORT"])
        try:
            service.start()
            LOG.info("GPU Roster started with one shared collector")
            server.run(stop)
        finally:
            try:
                server.close()
            finally:
                service.stop()
    LOG.info("GPU Roster stopped")


def main():
    parser = argparse.ArgumentParser(description="Serve the GPU Roster dashboard")
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop = threading.Event()
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, lambda *_: stop.set())
    try:
        from gpuroster.app import create_app

        serve(create_app(), stop)
    except (OSError, ValueError):
        # Raw errors may include private filesystem paths or configuration.
        LOG.error("Cannot start: check settings, database lock, and listening socket")
        raise SystemExit(1) from None
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
