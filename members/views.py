from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView, UpdateView, ListView, DetailView, TemplateView
)
from django.views.generic.detail import SingleObjectMixin

from billing.models import Bill
from housing_accounting.selection import get_selected_scope
from members.models import Member
from members.models.model_Nominee import Nominee
from notifications.models import ReminderLog
from receipts.models import PaymentReceipt
from shares.models import ShareLedger, ShareCertificate
from societies.permissions import has_role_or_above
from societies.roles import ROLE_ACCOUNTANT
from societies.utils import get_user_role


class MemberDetailView(LoginRequiredMixin, DetailView):
    model = Member
    template_name = "members/member_detail.html"
    context_object_name = "member"

    def get_object(self, queryset=None):
        member = super().get_object(queryset)
        selected_society, _ = get_selected_scope(self.request)
        if selected_society and member.society_id != selected_society.id:
            message = "Member not found in selected scope."
            raise Http404(message)
        return member

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        member = self.object
        share_ledger = (
            ShareLedger.objects.filter(member=member)
            .select_related("created_by", "voucher")
            .order_by("-transaction_date", "-created_at", "-id")
        )
        share_certificates = ShareCertificate.objects.filter(member=member).order_by(
            "-issued_date",
            "-id",
        )
        context["member_bills"] = (
            Bill.objects.filter(member=member)
            .select_related("voucher")
            .order_by("-bill_date", "-id")
        )
        context["member_receipts"] = (
            PaymentReceipt.objects.filter(member=member)
            .select_related("voucher", "deposited_account")
            .prefetch_related("allocations__bill")
            .order_by("-receipt_date", "-id")
        )
        context["member_reminders"] = (
            ReminderLog.objects.filter(member=member)
            .select_related("bill")
            .order_by("-scheduled_for", "-id")
        )[:10]
        context["share_ledger"] = share_ledger[:10]
        context["share_certificates"] = share_certificates[:5]
        context["share_balance"] = member.share_balance
        context["share_certificate_count"] = share_certificates.count()
        context["last_share_transaction"] = share_ledger.first()
        return context


member_detail_view = MemberDetailView.as_view()


# Nominee Management Views
class SocietyScopeMixin:
    """Mixin to enforce society scope and permissions."""
    
    def dispatch(self, request, *args, **kwargs):
        self.selected_society, _ = get_selected_scope(request)
        if not self.selected_society:
            from django.contrib import messages
            messages.error(request, _("Please select a society first."))
            return redirect(reverse('housing:dashboard'))
        
        # Check if user has appropriate role for nominee management
        user_role = get_user_role(request.user, self.selected_society)
        if not user_role or not has_role_or_above(user_role, ROLE_ACCOUNTANT):
            raise PermissionDenied(_("You do not have permission to manage nominees."))
        
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['selected_society'] = self.selected_society
        return context


class MemberScopeMixin(SocietyScopeMixin):
    """Mixin to get member and verify it belongs to selected society."""
    
    def dispatch(self, request, *args, **kwargs):
        self.selected_society, _ = get_selected_scope(request)
        if not self.selected_society:
            from django.contrib import messages
            messages.error(request, _("Please select a society first."))
            return redirect(reverse('housing:dashboard'))

        user_role = get_user_role(request.user, self.selected_society)
        if not user_role or not has_role_or_above(user_role, ROLE_ACCOUNTANT):
            raise PermissionDenied(_("You do not have permission to manage nominees."))

        self.member = get_object_or_404(
            Member,
            pk=kwargs.get('member_pk') or kwargs.get('pk'),
            society=self.selected_society
        )
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['member'] = self.member
        return context


class NomineeCreateView(MemberScopeMixin, LoginRequiredMixin, SuccessMessageMixin, CreateView):
    """Create nominee for a member."""
    model = Nominee
    fields = ['name', 'relationship', 'percentage', 'priority_order', 'is_active']
    template_name = 'members/nominee_form.html'
    success_message = _("Nominee created successfully.")
    
    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.instance.member = self.member  # Set member before validation
        # Add help text
        form.fields['percentage'].help_text = _("Percentage of shares (0-100)")
        form.fields['priority_order'].help_text = _("Priority order (1 = highest)")
        return form
    
    def form_valid(self, form):
        form.instance.member = self.member
        try:
            with transaction.atomic():
                return super().form_valid(form)
        except Exception as e:
            form.add_error(None, _("Error creating nominee: %(error)s") % {'error': str(e)})
            return self.form_invalid(form)
    
    def get_success_url(self):
        return reverse('members:nominee-list', kwargs={'member_pk': self.member.pk})
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Calculate current total percentage of active nominees for this member
        from django.db.models import Sum
        total = Nominee.objects.filter(
            member=self.member,
            is_active=True
        ).aggregate(
            total=Sum('percentage')
        )['total'] or 0
        context['current_total_percentage'] = total
        return context


class NomineeUpdateView(MemberScopeMixin, LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    """Update nominee details."""
    model = Nominee
    fields = ['name', 'relationship', 'percentage', 'priority_order', 'is_active']
    template_name = 'members/nominee_form.html'
    success_message = _("Nominee updated successfully.")
    
    def get_queryset(self):
        return Nominee.objects.filter(member=self.member)
    
    def get_success_url(self):
        return reverse('members:nominee-list', kwargs={'member_pk': self.member.pk})
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Calculate current total percentage of active nominees for this member
        # Exclude the current nominee being edited
        from django.db.models import Sum
        total = Nominee.objects.filter(
            member=self.member,
            is_active=True
        ).exclude(pk=self.object.pk if self.object else None).aggregate(
            total=Sum('percentage')
        )['total'] or 0
        context['current_total_percentage'] = total
        return context


class NomineeListView(MemberScopeMixin, LoginRequiredMixin, ListView):
    """List nominees for a member."""
    model = Nominee
    template_name = 'members/nominee_list.html'
    context_object_name = 'nominees'
    
    def get_queryset(self):
        return Nominee.objects.filter(
            member=self.member
        ).order_by('priority_order', 'created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Calculate total percentage
        total_percentage = sum(n.percentage for n in context['nominees'] if n.is_active)
        context['total_percentage'] = total_percentage
        context['remaining_percentage'] = 100 - total_percentage
        return context


class MemberShareDashboardView(MemberScopeMixin, LoginRequiredMixin, TemplateView):
    """Dashboard showing member's shares, nominees, and history."""
    template_name = 'members/member_share_dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        member = self.member
        
        # Share information
        share_ledger = ShareLedger.objects.filter(
            society=self.selected_society,
            member=member
        ).select_related('created_by', 'voucher').order_by('-transaction_date', '-created_at')[:50]
        
        share_certificates = ShareCertificate.objects.filter(
            member=member
        ).order_by('-issued_date')
        
        # Nominees
        nominees = Nominee.objects.filter(
            member=member,
            is_active=True
        ).order_by('priority_order')
        
        # Calculate nominee share allocations
        total_shares = member.share_balance
        nominee_allocations = []
        for nominee in nominees:
            shares = (total_shares * nominee.percentage / 100) if total_shares > 0 else 0
            nominee_allocations.append({
                'nominee': nominee,
                'shares': shares,
                'percentage': nominee.percentage
            })
        
        # Recent transactions
        recent_transactions = ShareLedger.objects.filter(
            society=self.selected_society,
            member=member
        ).select_related('created_by').order_by('-transaction_date')[:10]
        
        context.update({
            'share_ledger': share_ledger,
            'share_certificates': share_certificates,
            'nominees': nominees,
            'nominee_allocations': nominee_allocations,
            'total_shares': total_shares,
            'recent_transactions': recent_transactions,
            'share_value_per_share': 0,  # TODO: Get from SocietyConfig
            'total_share_value': member.total_share_value,
        })
        return context


# Function-based views for backward compatibility
nominee_create_view = NomineeCreateView.as_view()
nominee_update_view = NomineeUpdateView.as_view()
nominee_list_view = NomineeListView.as_view()
member_share_dashboard_view = MemberShareDashboardView.as_view()
