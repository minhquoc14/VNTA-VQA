"""Windows asyncio teardown fix.

Closing a browser tab, refreshing the page, or letting the `st.video` element
abort a range request drops the socket before asyncio gets to close it. The
ProactorEventLoop then calls `socket.shutdown()` on a connection the peer has
already reset, and CPython does not guard that call:

    # asyncio/proactor_events.py, _ProactorBasePipeTransport
    finally:
        if hasattr(self._sock, 'shutdown') and self._sock.fileno() != -1:
            self._sock.shutdown(socket.SHUT_RDWR)   # <-- WinError 10054
        self._sock.close()
        self._sock = None
        server = self._server
        if server is not None:
            server._detach(self)
            self._server = None
        self._called_connection_lost = True

The traceback it prints is noise -- we were closing the connection anyway -- but
the four lines *after* the raise are skipped, so the socket is never closed and
the server never detaches the transport. Over a review session of reloads and
video seeks that leaks a handle per drop.

The exception surfaces from `Handle._run` inside the event loop, so no
try/except in application code can reach it. Wrapping the transport method is
the only place to catch it.
"""

from __future__ import annotations

import sys


def patch() -> bool:
    """Finish the teardown CPython abandons. Returns True if the patch applied.

    Idempotent: Streamlit re-executes the script on every interaction.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport as _T
    except ImportError:
        return False   # not the Proactor loop; nothing to fix

    original = _T._call_connection_lost
    if getattr(original, "_vqa_patched", False):
        return True

    def _call_connection_lost(self, exc):
        try:
            original(self, exc)
        except (ConnectionResetError, ConnectionAbortedError):
            # `self._protocol.connection_lost(exc)` already ran -- it is inside
            # the try, ahead of the shutdown -- so only the socket and server
            # bookkeeping is outstanding.
            sock, self._sock = getattr(self, "_sock", None), None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            server, self._server = getattr(self, "_server", None), None
            if server is not None:
                server._detach(self)
            self._called_connection_lost = True

    _call_connection_lost._vqa_patched = True
    _T._call_connection_lost = _call_connection_lost
    return True
