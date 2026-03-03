from django.utils import timezone

from accounting.models import FinancialYear
from societies.models import Society

SESSION_SELECTED_SOCIETY_ID = "selected_society_id"
SESSION_SELECTED_FINANCIAL_YEAR_ID = "selected_financial_year_id"
SCOPE_CACHE_ATTR = "_selection_scope_cache"


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


def _persist_selection(request, *, society, financial_year):
    if society:
        request.session[SESSION_SELECTED_SOCIETY_ID] = society.id
    else:
        request.session.pop(SESSION_SELECTED_SOCIETY_ID, None)
        request.session.pop(SESSION_SELECTED_FINANCIAL_YEAR_ID, None)
        return

    if financial_year and financial_year.society_id == society.id:
        request.session[SESSION_SELECTED_FINANCIAL_YEAR_ID] = financial_year.id
    else:
        request.session.pop(SESSION_SELECTED_FINANCIAL_YEAR_ID, None)


def _load_scope_from_session(request):
    society_id = request.session.get(SESSION_SELECTED_SOCIETY_ID)
    society = Society.objects.filter(pk=society_id).first() if society_id else None
    if society is None:
        society = Society.objects.order_by("name").first()

    financial_year_id = request.session.get(SESSION_SELECTED_FINANCIAL_YEAR_ID)
    financial_year = None
    if financial_year_id:
        financial_year = FinancialYear.objects.filter(
            pk=financial_year_id,
            society=society,
        ).first()
    if financial_year is None:
        financial_year = get_default_financial_year_for_society(society)

    return society, financial_year


def get_selected_society(request, *, persist=False):
    society, _ = get_selected_scope(request, persist=persist)
    return society


def get_selected_financial_year(request, society=None, *, persist=False):
    selected_society, financial_year = get_selected_scope(request, persist=persist)
    if selected_society == society or society is None:
        return financial_year
    return get_default_financial_year_for_society(society)


def get_selected_scope(request, *, persist=False):
    cached_scope = getattr(request, SCOPE_CACHE_ATTR, None)
    if cached_scope is not None:
        society, financial_year, was_persisted = cached_scope
        if persist and not was_persisted:
            _persist_selection(
                request,
                society=society,
                financial_year=financial_year,
            )
            setattr(
                request,
                SCOPE_CACHE_ATTR,
                (society, financial_year, True),
            )
        return society, financial_year

    society, financial_year = _load_scope_from_session(request)
    if persist:
        _persist_selection(
            request,
            society=society,
            financial_year=financial_year,
        )
    setattr(
        request,
        SCOPE_CACHE_ATTR,
        (society, financial_year, persist),
    )
    return society, financial_year
