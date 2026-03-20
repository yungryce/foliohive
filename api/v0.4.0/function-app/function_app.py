"""foliohive consolidated Function App (v0.4.0).

This Function App hosts:
- HTTP API routes (former api-gateway)
- Queue-trigger workers (sync-worker, cache-worker, reconciliation-worker)


Logical separation is maintained via Azure Functions Blueprints.
"""

from __future__ import annotations


import azure.functions as func
import logging
import os
from logging.handlers import RotatingFileHandler


# Local development logging (disabled in production)
if os.getenv("ENABLE_LOCAL_LOGGING", "").lower() == "true":
    _log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _fh = RotatingFileHandler(
        os.path.join(_log_dir, "worker.log"),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
        encoding="utf-8",
    )
    _fh.setLevel(logging.INFO)
    _fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger("foliohive").addHandler(_fh)


from blueprints.api_gateway import bp as api_gateway_bp
from blueprints.cache_worker import bp as cache_worker_bp
from blueprints.reconciliation_worker import bp as reconciliation_worker_bp
from blueprints.sync_worker import bp as sync_worker_bp

app = func.FunctionApp()

_register = getattr(app, "register_blueprint", None) or getattr(app, "register_functions", None)
if _register is None:  # pragma: no cover
    raise AttributeError("azure.functions.FunctionApp missing blueprint registration method")

_register(api_gateway_bp)
_register(sync_worker_bp)
_register(cache_worker_bp)
_register(reconciliation_worker_bp)
