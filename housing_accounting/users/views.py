from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import QuerySet
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView
from django.views.generic import RedirectView
from django.views.generic import UpdateView

from accounting.models import FinancialYear
from housing_accounting.selection import SESSION_SELECTED_FINANCIAL_YEAR_ID
from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from housing_accounting.selection import get_default_financial_year_for_society
from societies.services import get_accessible_societies_qs
from housing_accounting.users.models import User


class UserDetailView(LoginRequiredMixin, DetailView):
    model = User
    slug_field = "id"
    slug_url_kwarg = "id"


user_detail_view = UserDetailView.as_view()


class UserUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = User
    fields = ["name"]
    success_message = _("Information successfully updated")

    def get_success_url(self) -> str:
        assert self.request.user.is_authenticated  # type guard
        return self.request.user.get_absolute_url()

    def get_object(self, queryset: QuerySet | None=None) -> User:
        assert self.request.user.is_authenticated  # type guard
        return self.request.user


user_update_view = UserUpdateView.as_view()


class UserRedirectView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self) -> str:
        return reverse("users:detail", kwargs={"pk": self.request.user.pk})


user_redirect_view = UserRedirectView.as_view()


class GlobalSelectionUpdateView(LoginRequiredMixin, View):
    def post(self, request):
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse(
            "home"
        )

        society_id = request.POST.get("selected_society_id")
        accessible_societies = get_accessible_societies_qs(request.user)
        selected_society = (
            accessible_societies.filter(pk=society_id).first()
            if society_id
            else None
        )
        if selected_society is None:
            selected_society = accessible_societies.first()

        if selected_society:
            request.session[SESSION_SELECTED_SOCIETY_ID] = selected_society.id
        else:
            request.session.pop(SESSION_SELECTED_SOCIETY_ID, None)
            request.session.pop(SESSION_SELECTED_FINANCIAL_YEAR_ID, None)
            return redirect(next_url)

        financial_year_id = request.POST.get("selected_financial_year_id")
        selected_financial_year = None
        if financial_year_id:
            selected_financial_year = FinancialYear.objects.filter(
                pk=financial_year_id,
                society=selected_society,
            ).first()

        if selected_financial_year is None:
            selected_financial_year = get_default_financial_year_for_society(
                selected_society
            )

        if selected_financial_year:
            request.session[SESSION_SELECTED_FINANCIAL_YEAR_ID] = selected_financial_year.id
        else:
            request.session.pop(SESSION_SELECTED_FINANCIAL_YEAR_ID, None)

        return redirect(next_url)


global_selection_update_view = GlobalSelectionUpdateView.as_view()
