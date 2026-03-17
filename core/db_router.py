# ruff: noqa: SLF001

class DatabaseRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == "analytics":
            return "analytics"
        if model._meta.app_label == "archive":
            return "archive"
        return "default"

    def db_for_write(self, model, **hints):
        if model._meta.app_label == "analytics":
            return "analytics"
        if model._meta.app_label == "archive":
            return "archive"
        return "default"

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == "analytics":
            return db == "analytics"
        if app_label == "archive":
            return db == "archive"
        return db == "default"
