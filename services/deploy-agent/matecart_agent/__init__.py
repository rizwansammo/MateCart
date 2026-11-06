"""MateCart Deployment Agent and Store installer (D-119).

Stdlib only. Runs on Linux hosts as a systemd service and makes outbound HTTPS requests to the
NetaMate Control API. It executes only the allowlisted operations in ``operations``.
"""

VERSION = "1.0.0"
