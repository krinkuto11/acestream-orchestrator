import sys
from django.apps import AppConfig

class TSProxyConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.proxy.ts_proxy'
    verbose_name = "TS Stream Proxies"

    def ready(self):
        """Initialize proxy servers when Django starts"""
        if 'manage.py' not in sys.argv:
            from .server import ProxyServer
            ProxyServer.get_instance()
