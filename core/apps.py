from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Core"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in
        from django.db.backends.signals import connection_created

        from . import db, signals

        connection_created.connect(db.use_app_schema, dispatch_uid="mtn_use_app_schema")
        user_logged_in.connect(signals.log_login, dispatch_uid="mtn_log_login")
