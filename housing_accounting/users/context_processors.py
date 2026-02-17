from django.conf import settings

from accounting.models import AccountingPeriod
from accounting.models import FinancialYear
from societies.models import Society
from housing_accounting.selection import get_selected_scope


def allauth_settings(request):
    """Expose some settings from django-allauth in templates."""
    return {
        "ACCOUNT_ALLOW_REGISTRATION": settings.ACCOUNT_ALLOW_REGISTRATION,
    }


def global_selection(request):
    if not request.user.is_authenticated:
        return {
            "selection_societies": Society.objects.none(),
            "selection_financial_years": FinancialYear.objects.none(),
            "selection_accounting_periods": AccountingPeriod.objects.none(),
            "selected_society": None,
            "selected_financial_year": None,
        }

    selected_society, selected_financial_year = get_selected_scope(request, persist=True)
    financial_years = (
        FinancialYear.objects.filter(society=selected_society).order_by("-start_date")
        if selected_society
        else FinancialYear.objects.none()
    )
    accounting_periods = (
        AccountingPeriod.objects.filter(
            society=selected_society,
            financial_year=selected_financial_year,
        )
        .select_related("financial_year")
        .order_by("start_date")
        if selected_society and selected_financial_year
        else AccountingPeriod.objects.none()
    )
    return {
        "selection_societies": Society.objects.order_by("name"),
        "selection_financial_years": financial_years,
        "selection_accounting_periods": accounting_periods,
        "selected_society": selected_society,
        "selected_financial_year": selected_financial_year,
    }
