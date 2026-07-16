from django.core.exceptions import PermissionDenied


class TenantScopeMixin:
    """CBV mixin that enforces tenant scoping on querysets and object lookups.

    Override get_queryset() to filter by request.current_society.
    Override get_object() to apply the same scoping (prevents IDOR).
    """

    society_field = "society"

    def get_queryset(self):
        queryset = super().get_queryset()
        society = getattr(self.request, "current_society", None)
        if society is None:
            return queryset.none()
        return queryset.filter(**{self.society_field: society})

    def get_object(self, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()
        return super().get_object(queryset)


class PermissionRequiredMixin:
    """CBV mixin that checks a permission before allowing view dispatch.

    Set `permission_required` to a dot-namespaced permission code.
    """

    permission_required = None

    def dispatch(self, request, *args, **kwargs):
        if not self.has_permission():
            raise PermissionDenied("Insufficient permissions for this action.")
        return super().dispatch(request, *args, **kwargs)

    def has_permission(self):
        if self.permission_required is None:
            return True
        from societies.permissions import has_permission as check_permission

        return check_permission(
            self.request.user,
            self.permission_required,
            getattr(self.request, "current_society", None),
        )
