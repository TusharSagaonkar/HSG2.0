import contextvars

from django.db import models

# Thread-safe, async-safe context variable for the current tenant.
# Set by SocietyMiddleware in request context.
# Set by tenant_context() context manager in background jobs.
_current_tenant = contextvars.ContextVar("current_tenant", default=None)


class TenantQuerySet(models.QuerySet):
    """QuerySet that auto-filters by the current tenant and excludes soft-deleted records.

    The tenant + soft-delete filters are applied once when the queryset is first
    obtained from the manager (see ``TenantManager.get_queryset``). They are NOT
    re-applied on every ``_clone`` — doing so caused infinite recursion because
    ``filter()`` internally calls ``_chain`` → ``_clone``. Existing filters persist
    through clones automatically (Django clones the WHERE clause), so a single
    application at the manager level is sufficient and correct.
    """

    def _apply_tenant_filter(self):
        queryset = self
        tenant = _current_tenant.get()
        if tenant is not None:
            # Only filter if the model has a 'society' field
            if "society" in {f.name for f in self.model._meta.get_fields()}:
                queryset = queryset.filter(society=tenant)
        # Exclude soft-deleted records if the model has 'is_deleted'
        if "is_deleted" in {f.name for f in self.model._meta.get_fields()}:
            queryset = queryset.filter(is_deleted=False)
        return queryset

    def including_deleted(self):
        """Return queryset without the is_deleted filter (tenant filter still applies)."""
        clone = super()._clone()
        tenant = _current_tenant.get()
        if tenant is not None and "society" in {f.name for f in self.model._meta.get_fields()}:
            clone = clone.filter(society=tenant)
        return clone

    def unscoped(self):
        """Return completely unfiltered queryset (admin/debug use only)."""
        return super()._clone()


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Manager that uses TenantQuerySet for automatic tenant filtering."""

    use_in_migrations = True

    def get_queryset(self):
        # Apply the tenant + soft-delete filters exactly once when the queryset
        # is first created. Clones preserve the WHERE clause, so subsequent
        # .filter()/.exclude() calls keep the scoping without re-applying it
        # (which previously caused infinite recursion via _clone → filter → _clone).
        return super().get_queryset()._apply_tenant_filter()
