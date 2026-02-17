from django.utils import timezone

from accounting.models import FinancialYear
from societies.models import Society

SESSION_SELECTED_SOCIETY_ID = "selected_society_id"
SESSION_SELECTED_FINANCIAL_YEAR_ID = "selected_financial_year_id"


def get_default_financial_year_for_society(society):
    if society is None:
        return None

    today = timezone.localdate()
    current = (
        FinancialYear.objects.filter(
            society=society,
            start_date__lte=today,
            end_date__gte=today,
            is_open=True,
        )
        .order_by("-start_date")
        .first()
    )
    if current:
        return current

    return FinancialYear.objects.filter(society=society).order_by("-start_date").first()


def get_selected_society(request, persist=False):
    society_id = request.session.get(SESSION_SELECTED_SOCIETY_ID)
    selected = None
    if society_id:
        selected = Society.objects.filter(pk=society_id).first()

    if selected is None:
        selected = Society.objects.order_by("name").first()
        if persist:
            if selected:
                request.session[SESSION_SELECTED_SOCIETY_ID] = selected.id
            else:
                request.session.pop(SESSION_SELECTED_SOCIETY_ID, None)
                request.session.pop(SESSION_SELECTED_FINANCIAL_YEAR_ID, None)
    return selected


def get_selected_financial_year(request, society=None, persist=False):
    society = society or get_selected_society(request, persist=persist)
    if society is None:
        if persist:
            request.session.pop(SESSION_SELECTED_FINANCIAL_YEAR_ID, None)
        return None

    financial_year_id = request.session.get(SESSION_SELECTED_FINANCIAL_YEAR_ID)
    selected = None
    if financial_year_id:
        selected = FinancialYear.objects.filter(
            pk=financial_year_id,
            society=society,
        ).first()

    if selected is None:
        selected = get_default_financial_year_for_society(society)
        if persist:
            if selected:
                request.session[SESSION_SELECTED_FINANCIAL_YEAR_ID] = selected.id
            else:
                request.session.pop(SESSION_SELECTED_FINANCIAL_YEAR_ID, None)

    return selected


def get_selected_scope(request, persist=False):
    society = get_selected_society(request, persist=persist)
    financial_year = get_selected_financial_year(
        request,
        society=society,
        persist=persist,
    )
    return society, financial_year
