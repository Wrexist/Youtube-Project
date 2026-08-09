"""Studio's render and publishing engine.

The one thing that happens on import: TLS verification is pointed at the
operating system's certificate store. It is here, in the package root, because
it has to run before anything constructs an `ssl.SSLContext` — `edge_tts` builds
one at *its* import time — and importing `engine` is the only event guaranteed
to come first. `engine/tls.py` explains what breaks without it, which on a
machine running antivirus HTTPS scanning is every outbound call in the repo.
"""

from engine import tls

tls.install()

__all__ = ["tls"]
