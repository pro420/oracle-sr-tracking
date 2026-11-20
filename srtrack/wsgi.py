"""WSGI entry point for production servers, e.g. `gunicorn srtrack.wsgi:app`."""

import os

from .web import create_app

if not os.environ.get("SRT_PASSWORD") and os.environ.get("SRT_ALLOW_NO_AUTH") != "1":
    raise RuntimeError(
        "SRT_PASSWORD is not set. Set it to protect your SR data "
        "(or set SRT_ALLOW_NO_AUTH=1 if an auth proxy sits in front of the app)."
    )

app = create_app(os.environ.get("SRT_DB") or None)
