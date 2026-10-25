"""Product packages: one module per domain on top of core.py.

A downloaded ZIP ships core.py and only its own domain module; modules that are not present are skipped."""
import importlib

DOMAINS = ('warehouse', 'database', 'lakehouse', 'finops', 'spatial', 'ai', 'bi', 'erp', 'integration', 'transform', 'orchestration',
           'stream', 'quality', 'governance', 'mdm', 'privacy', 'observability', 'science', 'ml', 'product', 'planning', 'migration', 'files')
HANDLERS = {}
for _domain in DOMAINS:
    try:
        _module = importlib.import_module('products.' + _domain)
    except ModuleNotFoundError as exc:
        if exc.name != 'products.' + _domain:
            raise
        continue
    HANDLERS.update(_module.HANDLERS)
