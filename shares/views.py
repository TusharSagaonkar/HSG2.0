"""
Views for share management operations.
"""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView, UpdateView, DetailView, ListView, FormView, TemplateView
)
from django.views.generic.detail import SingleObjectMixin

from housing_accounting.selection import get_selected_scope
from members.models.model_Member import Member
from members.models.model_Nominee import Nominee
from shares.models import ShareLedger, ShareCertificate
from shares.models.model_EventLog import EventLog
from shares.services import (
    ShareLedgerService,
    ShareTransactionService,
    ShareTransactionError,
    InsufficientSharesError,
    InvalidTransferError,
    NomineeNotEligibleError,
)
from shares.forms import (
    ShareAllotmentForm,
    ShareTransferForm,
    ShareTransmissionForm,
    ShareCorrectionForm,
    SocietyRulesForm,
)
from societies.permissions import has_role_or_above
from societies.roles import ROLE_ACCOUNTANT
from societies.utils import get_user_role


class SocietyScopeMixin:
    """Mixin to enforce society scope and permissions."""
    
    def dispatch(self, request, *args, **kwargs):
        self.selected_society, _ = get_selected_scope(request)
        if not self.selected_society:
            messages.error(request, _("Please select a society first."))
            return redirect(reverse('housing:dashboard'))
        
        # Check if user has appropriate role for share management
        user_role = get_user_role(request.user, self.selected_society)
        if not user_role or not has_role_or_above(user_role, ROLE_ACCOUNTANT):
            raise PermissionDenied(_("You do not have permission to manage shares."))
        
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['selected_society'] = self.selected_society
        return context


class ShareDashboardView(SocietyScopeMixin, LoginRequiredMixin, TemplateView):
    template_name = "shares/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        members_qs = Member.objects.filter(society=self.selected_society).select_related("unit")
        certificates_qs = ShareCertificate.objects.filter(member__society=self.selected_society).select_related(
            "member",
            "issued_by",
        )
        ledger_qs = ShareLedger.objects.filter(society=self.selected_society).select_related(
            "member",
            "created_by",
            "voucher",
        )

        context.update(
            {
                "member_count": members_qs.count(),
                "total_shares": members_qs.aggregate(total=Sum("share_balance"))["total"] or 0,
                "certificate_count": certificates_qs.count(),
                "active_certificate_count": certificates_qs.filter(status=ShareCertificate.Status.ACTIVE).count(),
                "ledger_count": ledger_qs.count(),
                "recent_members": members_qs.order_by("-share_balance", "full_name")[:6],
                "recent_certificates": certificates_qs.order_by("-issued_date", "-id")[:5],
                "recent_ledger_entries": ledger_qs.order_by("-transaction_date", "-created_at")[:8],
            }
        )
        return context


class ShareRulesView(SocietyScopeMixin, LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    template_name = "shares/share_rules_form.html"
    form_class = SocietyRulesForm
    success_message = _("Society share rules updated successfully.")

    def get_object(self, queryset=None):
        return self.selected_society.share_config

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['society'] = self.selected_society
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["selected_society"] = self.selected_society
        context["form_title"] = _("Society Share Rules")
        context["form_subtitle"] = _("Configure share value, fees, nominee policy, and voucher behavior.")
        context["cancel_url"] = reverse("shares:dashboard")
        context["cancel_label"] = _("Back to Shares")
        return context

    def get_success_url(self):
        return reverse("shares:rules")


share_rules_view = ShareRulesView.as_view()


share_dashboard_view = ShareDashboardView.as_view()


class ShareLedgerListView(SocietyScopeMixin, LoginRequiredMixin, ListView):
    model = ShareLedger
    template_name = "shares/share_ledger_list.html"
    context_object_name = "ledger_entries"
    paginate_by = 25

    def get_queryset(self):
        queryset = (
            ShareLedger.objects.filter(society=self.selected_society)
            .select_related("member", "created_by", "voucher")
            .order_by("-transaction_date", "-created_at")
        )
        member_id = self.request.GET.get("member")
        transaction_type = self.request.GET.get("transaction_type")
        if member_id:
            queryset = queryset.filter(member_id=member_id)
        if transaction_type:
            queryset = queryset.filter(transaction_type=transaction_type)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        certificates = ShareCertificate.objects.filter(member__society=self.selected_society).select_related(
            "member",
            "issued_by",
        )
        context["members"] = Member.objects.filter(society=self.selected_society).order_by("full_name")
        context["transaction_types"] = ShareLedger.TransactionType.choices
        context["total_certificates"] = certificates.count()
        context["active_certificates"] = certificates.filter(status=ShareCertificate.Status.ACTIVE).count()
        context["active_certificate_shares"] = sum(
            cert.share_count for cert in certificates.filter(status=ShareCertificate.Status.ACTIVE)
        )
        return context


share_ledger_list_view = ShareLedgerListView.as_view()


class ShareAllotmentView(SocietyScopeMixin, LoginRequiredMixin, SuccessMessageMixin, FormView):
    """View to allot shares to a member."""
    form_class = ShareAllotmentForm
    template_name = 'shares/share_allotment_form.html'
    success_message = _("Shares allotted successfully.")

    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['society'] = self.selected_society
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        member_id = self.request.GET.get("member")
        if member_id:
            member = (
                Member.objects.filter(
                    pk=member_id,
                    society=self.selected_society,
                )
                .select_related("unit")
                .first()
            )
            if member:
                initial["member"] = member.pk
        return initial
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get society statistics
        from django.db.models import Sum
        total_shares = Member.objects.filter(
            society=self.selected_society
        ).aggregate(total=Sum('share_balance'))['total'] or 0
        
        active_certificates = ShareCertificate.objects.filter(
            member__society=self.selected_society,
            status=ShareCertificate.Status.ACTIVE
        ).count()
        
        # Get recent allotments
        recent_allotments = ShareLedger.objects.filter(
            society=self.selected_society,
            transaction_type=ShareLedger.TransactionType.ALLOTMENT
        ).select_related('member', 'created_by').order_by('-transaction_date', '-created_at')[:5]
        
        context.update({
            'total_shares': total_shares,
            'active_certificates': active_certificates,
            'recent_allotments': recent_allotments,
        })
        return context
    
    def get_success_url(self):
        return reverse('shares:member-share-history', kwargs={'pk': self.object.member.pk})
    
    def form_valid(self, form):
        error_message = None
        try:
            with transaction.atomic():
                member = form.cleaned_data['member']
                share_count = form.cleaned_data['share_count']
                transaction_date = form.cleaned_data['transaction_date']
                reason = form.cleaned_data.get('reason', '')
                reference_id = form.cleaned_data.get('reference_id', '')

                ledger_entry = ShareTransactionService.allot_shares_to_member(
                    member=member,
                    share_count=share_count,
                    transaction_date=transaction_date,
                    reference=reference_id,
                    created_by=self.request.user,
                )

                if reason:
                    ledger_entry.reason = reason
                    ledger_entry.save(update_fields=["reason"])

                if form.cleaned_data.get('issue_certificate', False):
                    certificate_no = form.cleaned_data.get('certificate_no', '')
                    if not certificate_no:
                        certificate_no = f"CERT-{member.id}-{ledger_entry.id}"

                    ShareCertificate.objects.create(
                        member=member,
                        certificate_no=certificate_no,
                        share_count=share_count,
                        issued_date=transaction_date,
                        issued_by=self.request.user,
                    )

                self.object = ledger_entry
        except ShareTransactionError as e:
            error_message = str(e)
        except Exception as e:
            error_message = _("An unexpected error occurred: %(error)s") % {'error': str(e)}

        if error_message:
            form.add_error(None, error_message)
            return self.form_invalid(form)

        return super().form_valid(form)


class ShareTransferView(SocietyScopeMixin, LoginRequiredMixin, SuccessMessageMixin, FormView):
    """View to transfer shares between members."""
    form_class = ShareTransferForm
    template_name = 'shares/share_transfer_form.html'
    success_message = _("Shares transferred successfully.")

    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['society'] = self.selected_society
        return kwargs
    
    def get_success_url(self):
        # Redirect to from_member's share history
        return reverse('shares:member-share-history', kwargs={'pk': self.from_member.pk})
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                self.from_member = form.cleaned_data['from_member']
                to_member = form.cleaned_data['to_member']
                share_count = form.cleaned_data['share_count']
                transaction_date = form.cleaned_data['transaction_date']
                reason = form.cleaned_data.get('reason', '')
                reference_id = form.cleaned_data.get('reference_id', '')
                
                transfer_out, transfer_in = ShareLedgerService.transfer_shares(
                    society=self.selected_society,
                    from_member=self.from_member,
                    to_member=to_member,
                    share_count=share_count,
                    transaction_date=transaction_date,
                    reason=reason,
                    reference_id=reference_id,
                    created_by=self.request.user,
                )
                
                # Update member share balances
                self.from_member.share_balance -= share_count
                self.from_member.save(update_fields=['share_balance'])
                to_member.share_balance += share_count
                to_member.save(update_fields=['share_balance'])
                
                return super().form_valid(form)
                
        except InsufficientSharesError:
            form.add_error('share_count', _("Insufficient shares for transfer."))
            return self.form_invalid(form)
        except InvalidTransferError:
            form.add_error(None, _("Invalid transfer. Members must belong to the same society."))
            return self.form_invalid(form)
        except ShareTransactionError as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)
        except Exception as e:
            form.add_error(None, _("An unexpected error occurred: %(error)s") % {'error': str(e)})
            return self.form_invalid(form)


class ShareTransmissionView(SocietyScopeMixin, LoginRequiredMixin, SuccessMessageMixin, FormView):
    """View to transmit shares to nominee."""
    form_class = ShareTransmissionForm
    template_name = 'shares/share_transmission_form.html'
    success_message = _("Shares transmitted to nominee successfully.")

    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['society'] = self.selected_society
        return kwargs
    
    def get_success_url(self):
        return reverse('shares:member-share-history', kwargs={'pk': self.member.pk})
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                self.member = form.cleaned_data['member']
                nominee = form.cleaned_data['nominee']
                share_count = form.cleaned_data['share_count']
                transaction_date = form.cleaned_data['transaction_date']
                reason = form.cleaned_data.get('reason', '')
                reference_id = form.cleaned_data.get('reference_id', '')
                
                # Create nominee as member if not already
                if not nominee.user:
                    # This would need actual implementation - for now we'll use a placeholder
                    pass
                
                ledger_entry = ShareLedgerService.transmit_shares(
                    society=self.selected_society,
                    member=self.member,
                    share_count=share_count,
                    transaction_date=transaction_date,
                    reason=reason,
                    reference_id=reference_id,
                    created_by=self.request.user,
                )
                
                # Update member share balance
                self.member.share_balance -= share_count
                self.member.save(update_fields=['share_balance'])
                
                return super().form_valid(form)
                
        except InsufficientSharesError:
            form.add_error('share_count', _("Insufficient shares for transmission."))
            return self.form_invalid(form)
        except NomineeNotEligibleError:
            form.add_error('nominee', _("Nominee is not eligible to receive shares."))
            return self.form_invalid(form)
        except ShareTransactionError as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)
        except Exception as e:
            form.add_error(None, _("An unexpected error occurred: %(error)s") % {'error': str(e)})
            return self.form_invalid(form)


class ShareCorrectionView(SocietyScopeMixin, LoginRequiredMixin, SuccessMessageMixin, FormView):
    """View to record share corrections."""
    form_class = ShareCorrectionForm
    template_name = 'shares/share_correction_form.html'
    success_message = _("Share correction recorded successfully.")

    @method_decorator(transaction.non_atomic_requests)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['society'] = self.selected_society
        return kwargs
    
    def get_success_url(self):
        return reverse('shares:member-share-history', kwargs={'pk': self.member.pk})
    
    def form_valid(self, form):
        try:
            with transaction.atomic():
                self.member = form.cleaned_data['member']
                adjustment_type = form.cleaned_data['adjustment_type']
                share_count = form.cleaned_data['share_count']
                transaction_date = form.cleaned_data['transaction_date']
                reason = form.cleaned_data['reason']
                reference_id = form.cleaned_data.get('reference_id', '')
                
                if adjustment_type == 'ADD':
                    ledger_entry = ShareLedgerService.allot_shares(
                        society=self.selected_society,
                        member=self.member,
                        share_count=share_count,
                        transaction_date=transaction_date,
                        reason=reason,
                        reference_id=reference_id,
                        created_by=self.request.user,
                    )
                    self.member.share_balance += share_count
                else:  # DEDUCT
                    # Check sufficient balance
                    if self.member.share_balance < share_count:
                        form.add_error('share_count', _("Insufficient shares for deduction."))
                        return self.form_invalid(form)
                    
                    ledger_entry = ShareLedger.objects.create(
                        society=self.selected_society,
                        member=self.member,
                        shares_in=0,
                        shares_out=share_count,
                        balance_after=self.member.share_balance - share_count,
                        transaction_type=ShareLedger.TransactionType.CORRECTION,
                        transaction_date=transaction_date,
                        reason=reason,
                        reference_id=reference_id,
                        created_by=self.request.user,
                    )
                    self.member.share_balance -= share_count
                
                self.member.save(update_fields=['share_balance'])
                return super().form_valid(form)
                
        except ShareTransactionError as e:
            form.add_error(None, str(e))
            return self.form_invalid(form)
        except Exception as e:
            form.add_error(None, _("An unexpected error occurred: %(error)s") % {'error': str(e)})
            return self.form_invalid(form)


class MemberShareHistoryView(SocietyScopeMixin, LoginRequiredMixin, DetailView):
    """Detail view showing member's share ledger."""
    model = Member
    template_name = 'shares/member_share_history.html'
    context_object_name = 'member'
    
    def get_object(self, queryset=None):
        member = super().get_object(queryset)
        # Verify member belongs to selected society
        if member.society != self.selected_society:
            raise Http404(_("Member not found in selected society."))
        return member
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        member = self.object
        
        # Get share ledger entries with all related data
        ledger_entries = ShareLedger.objects.filter(
            society=self.selected_society,
            member=member
        ).select_related('created_by', 'voucher').order_by('-transaction_date', '-created_at')
        
        # Get share certificates with transfer details
        certificates = ShareCertificate.objects.filter(
            member=member
        ).select_related('issued_by', 'transferred_to').order_by('-issued_date')
        
        # Get all nominees (including inactive for history)
        nominees = Nominee.objects.filter(
            member=member
        ).order_by('priority_order')
        
        # Get event logs for this member
        from shares.models.model_EventLog import EventLog
        from django.db.models import Q
        event_logs = EventLog.objects.filter(
            society=self.selected_society
        ).filter(
            Q(member=member) | Q(from_member=member) | Q(to_member=member)
        ).select_related(
            'performed_by', 'from_member', 'to_member', 'nominee'
        ).order_by('-timestamp')[:50]  # Limit to recent 50 events
        
        # Calculate share statistics
        from django.db.models import Sum
        total_allotted = ShareLedger.objects.filter(
            society=self.selected_society,
            member=member,
            shares_in__gt=0
        ).aggregate(total=Sum('shares_in'))['total'] or 0
        
        total_transferred_out = ShareLedger.objects.filter(
            society=self.selected_society,
            member=member,
            transaction_type=ShareLedger.TransactionType.TRANSFER_OUT
        ).aggregate(total=Sum('shares_out'))['total'] or 0
        
        total_transferred_in = ShareLedger.objects.filter(
            society=self.selected_society,
            member=member,
            transaction_type=ShareLedger.TransactionType.TRANSFER_IN
        ).aggregate(total=Sum('shares_in'))['total'] or 0
        
        context.update({
            'ledger_entries': ledger_entries,
            'certificates': certificates,
            'nominees': nominees,
            'event_logs': event_logs,
            'total_shares': member.share_balance,
            'total_allotted': total_allotted,
            'total_transferred_out': total_transferred_out,
            'total_transferred_in': total_transferred_in,
            'active_certificates': certificates.filter(status=ShareCertificate.Status.ACTIVE),
            'active_nominees': nominees.filter(is_active=True),
        })
        return context


class ShareCertificateListView(SocietyScopeMixin, LoginRequiredMixin, ListView):
    """List view of share certificates."""
    model = ShareCertificate
    template_name = 'shares/share_certificate_list.html'
    context_object_name = 'certificates'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = super().get_queryset()
        queryset = queryset.filter(
            member__society=self.selected_society
        ).select_related('member', 'issued_by').order_by('-issued_date')
        
        # Filter by member if provided
        member_id = self.request.GET.get('member')
        if member_id:
            queryset = queryset.filter(member_id=member_id)
        
        # Filter by status if provided
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['members'] = Member.objects.filter(
            society=self.selected_society
        ).order_by('full_name')
        return context


class ShareCertificateDetailView(SocietyScopeMixin, LoginRequiredMixin, DetailView):
    """Detail view of a share certificate."""
    model = ShareCertificate
    template_name = 'shares/share_certificate_detail.html'
    context_object_name = 'certificate'
    
    def get_object(self, queryset=None):
        certificate = super().get_object(queryset)
        # Verify certificate belongs to selected society
        if certificate.member.society != self.selected_society:
            raise Http404(_("Certificate not found in selected society."))
        return certificate
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        certificate = self.object
        
        # Get related ledger entries
        ledger_entries = ShareLedger.objects.filter(
            society=self.selected_society,
            member=certificate.member,
            reference_id=certificate.certificate_no
        ).order_by('-transaction_date')
        
        context['ledger_entries'] = ledger_entries
        return context


class EventLogListView(SocietyScopeMixin, LoginRequiredMixin, ListView):
    """List view for event logs with filtering and search."""
    model = EventLog
    template_name = 'shares/event_log_list.html'
    context_object_name = 'event_logs'
    paginate_by = 50
    
    def dispatch(self, request, *args, **kwargs):
        # Override to use different permission logic for event logs
        self.selected_society, _ = get_selected_scope(request)
        if not self.selected_society:
            messages.error(request, _("Please select a society first."))
            return redirect(reverse('housing:dashboard'))
        
        # Staff users can always see event logs
        if request.user.is_staff:
            return super(SocietyScopeMixin, self).dispatch(request, *args, **kwargs)
        
        # Check if user has any role in the society
        user_role = get_user_role(request.user, self.selected_society)
        if not user_role:
            # User has no role in this society - check if they're a member
            from members.models.model_Member import Member
            try:
                member = Member.objects.get(user=request.user, society=self.selected_society)
                # Regular members can view their own events
                self.is_member_only = True
                self.member_user = member
                return super(SocietyScopeMixin, self).dispatch(request, *args, **kwargs)
            except Member.DoesNotExist:
                raise PermissionDenied(_("You do not have permission to view event logs."))
        
        # Users with ROLE_ADMIN or above can view all events
        from societies.roles import ROLE_ADMIN
        if has_role_or_above(user_role, ROLE_ADMIN):
            return super(SocietyScopeMixin, self).dispatch(request, *args, **kwargs)
        
        # Regular members (ROLE_MEMBER) can only view their own events
        from societies.roles import ROLE_MEMBER
        if user_role == ROLE_MEMBER:
            try:
                member = Member.objects.get(user=request.user, society=self.selected_society)
                self.is_member_only = True
                self.member_user = member
                return super(SocietyScopeMixin, self).dispatch(request, *args, **kwargs)
            except Member.DoesNotExist:
                raise PermissionDenied(_("You do not have permission to view event logs."))
        
        # For other roles (VIEWER, ACCOUNTANT), check if they have permission
        # ACCOUNTANT should be able to view events (same as ADMIN for event logs)
        from societies.roles import ROLE_ACCOUNTANT
        if has_role_or_above(user_role, ROLE_ACCOUNTANT):
            return super(SocietyScopeMixin, self).dispatch(request, *args, **kwargs)
        
        raise PermissionDenied(_("You do not have permission to view event logs."))
    
    def get_queryset(self):
        from django.db.models import Q
        from shares.models.model_EventLog import EventLog
        
        queryset = EventLog.objects.filter(
            society=self.selected_society
        ).select_related(
            'member', 'from_member', 'to_member', 'performed_by', 'nominee'
        ).order_by('-timestamp', '-created_at')
        
        # Apply permission-based filtering
        if hasattr(self, 'is_member_only') and self.is_member_only:
            # Regular members can only see events where they are involved
            member = self.member_user
            queryset = queryset.filter(
                Q(member=member) | Q(from_member=member) | Q(to_member=member)
            )
        
        # Apply filters
        event_type = self.request.GET.get('event_type')
        if event_type:
            queryset = queryset.filter(event_type=event_type)
        
        member_id = self.request.GET.get('member')
        if member_id:
            queryset = queryset.filter(
                Q(member_id=member_id) | Q(from_member_id=member_id) | Q(to_member_id=member_id)
            )
        
        # Date range filtering
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            queryset = queryset.filter(timestamp__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(timestamp__date__lte=date_to)
        
        # Search functionality
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(description__icontains=search) |
                Q(certificate_number__icontains=search) |
                Q(metadata__icontains=search)
            )
        
        return queryset
    
    def get(self, request, *args, **kwargs):
        # Check for CSV export
        if request.GET.get('export') == 'csv':
            return self.export_csv()
        return super().get(request, *args, **kwargs)
    
    def export_csv(self):
        import csv
        from django.http import HttpResponse
        from django.utils.encoding import smart_str
        
        queryset = self.get_queryset()
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="event_logs_{self.selected_society.id}_{timezone.now().date()}.csv"'
        
        writer = csv.writer(response)
        # Write header
        writer.writerow([
            smart_str('Timestamp'),
            smart_str('Event Type'),
            smart_str('Primary Member'),
            smart_str('From Member'),
            smart_str('To Member'),
            smart_str('Share Count'),
            smart_str('Share Value'),
            smart_str('Certificate Number'),
            smart_str('Nominee'),
            smart_str('Description'),
            smart_str('Performed By'),
            smart_str('IP Address'),
            smart_str('User Agent'),
            smart_str('Society'),
            smart_str('Created At'),
        ])
        
        # Write data rows
        for event in queryset:
            writer.writerow([
                smart_str(event.timestamp),
                smart_str(event.get_event_type_display()),
                smart_str(event.member.full_name if event.member else ''),
                smart_str(event.from_member.full_name if event.from_member else ''),
                smart_str(event.to_member.full_name if event.to_member else ''),
                smart_str(event.share_count if event.share_count else ''),
                smart_str(event.share_value if event.share_value else ''),
                smart_str(event.certificate_number),
                smart_str(event.nominee.full_name if event.nominee else ''),
                smart_str(event.description),
                smart_str(event.performed_by.get_full_name() or event.performed_by.username),
                smart_str(event.ip_address or ''),
                smart_str(event.user_agent or ''),
                smart_str(event.society.name),
                smart_str(event.created_at),
            ])
        
        return response
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get members for filter dropdown - limited based on permissions
        if hasattr(self, 'is_member_only') and self.is_member_only:
            # Regular members can only filter by themselves
            context['members'] = Member.objects.filter(
                society=self.selected_society,
                pk=self.member_user.pk
            ).order_by('full_name')
        else:
            # Staff and admins can see all members
            context['members'] = Member.objects.filter(
                society=self.selected_society
            ).order_by('full_name')
        
        # Get event types for filter dropdown
        from shares.models.model_EventLog import EventLog
        context['event_types'] = EventLog.EventType.choices
        
        # Pass current filter values to template
        context['current_filters'] = {
            'event_type': self.request.GET.get('event_type', ''),
            'member': self.request.GET.get('member', ''),
            'date_from': self.request.GET.get('date_from', ''),
            'date_to': self.request.GET.get('date_to', ''),
            'search': self.request.GET.get('search', ''),
        }
        
        # Add permission context for template
        context['user_can_view_all'] = not hasattr(self, 'is_member_only') or not self.is_member_only
        
        return context


class EventLogDetailView(SocietyScopeMixin, LoginRequiredMixin, DetailView):
    """Detail view for individual event log entry."""
    model = EventLog
    template_name = 'shares/event_log_detail.html'
    context_object_name = 'event_log'
    
    def dispatch(self, request, *args, **kwargs):
        # Override to use different permission logic for event logs
        self.selected_society, _ = get_selected_scope(request)
        if not self.selected_society:
            messages.error(request, _("Please select a society first."))
            return redirect(reverse('housing:dashboard'))
        
        # Staff users can always see event logs
        if request.user.is_staff:
            return super(SocietyScopeMixin, self).dispatch(request, *args, **kwargs)
        
        # Check if user has any role in the society
        user_role = get_user_role(request.user, self.selected_society)
        if not user_role:
            # User has no role in this society - check if they're a member
            from members.models.model_Member import Member
            try:
                member = Member.objects.get(user=request.user, society=self.selected_society)
                # Regular members can view their own events
                self.is_member_only = True
                self.member_user = member
                return super(SocietyScopeMixin, self).dispatch(request, *args, **kwargs)
            except Member.DoesNotExist:
                raise PermissionDenied(_("You do not have permission to view event logs."))
        
        # Users with ROLE_ADMIN or above can view all events
        from societies.roles import ROLE_ADMIN
        if has_role_or_above(user_role, ROLE_ADMIN):
            return super(SocietyScopeMixin, self).dispatch(request, *args, **kwargs)
        
        # Regular members (ROLE_MEMBER) can only view their own events
        from societies.roles import ROLE_MEMBER
        if user_role == ROLE_MEMBER:
            try:
                member = Member.objects.get(user=request.user, society=self.selected_society)
                self.is_member_only = True
                self.member_user = member
                return super(SocietyScopeMixin, self).dispatch(request, *args, **kwargs)
            except Member.DoesNotExist:
                raise PermissionDenied(_("You do not have permission to view event logs."))
        
        # For other roles (VIEWER, ACCOUNTANT), check if they have permission
        # ACCOUNTANT should be able to view events (same as ADMIN for event logs)
        from societies.roles import ROLE_ACCOUNTANT
        if has_role_or_above(user_role, ROLE_ACCOUNTANT):
            return super(SocietyScopeMixin, self).dispatch(request, *args, **kwargs)
        
        raise PermissionDenied(_("You do not have permission to view event logs."))
    
    def get_object(self, queryset=None):
        from shares.models.model_EventLog import EventLog
        
        if queryset is None:
            queryset = self.get_queryset()
        
        event_log = super().get_object(queryset)
        
        # Verify event belongs to selected society
        if event_log.society != self.selected_society:
            raise Http404(_("Event log not found in selected society."))
        
        # Additional permission check for regular members
        if hasattr(self, 'is_member_only') and self.is_member_only:
            member = self.member_user
            from django.db.models import Q
            if not (event_log.member == member or event_log.from_member == member or event_log.to_member == member):
                raise PermissionDenied(_("You do not have permission to view this event."))
        
        return event_log
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Add related objects for better context
        event_log = self.object
        
        # Get related share ledger entries if any
        if event_log.certificate_number:
            from shares.models import ShareLedger
            context['related_ledger_entries'] = ShareLedger.objects.filter(
                society=self.selected_society,
                reference_id=event_log.certificate_number
            ).select_related('member', 'created_by')
        
        # Get related share certificate if any
        if event_log.certificate_number:
            from shares.models import ShareCertificate
            try:
                context['related_certificate'] = ShareCertificate.objects.get(
                    certificate_no=event_log.certificate_number,
                    member__society=self.selected_society
                )
            except ShareCertificate.DoesNotExist:
                pass
        
        return context


# Function-based views for backward compatibility
share_allotment_view = ShareAllotmentView.as_view()
share_transfer_view = ShareTransferView.as_view()
share_transmission_view = ShareTransmissionView.as_view()
share_correction_view = ShareCorrectionView.as_view()
member_share_history_view = MemberShareHistoryView.as_view()
share_certificate_list_view = ShareCertificateListView.as_view()
share_certificate_detail_view = ShareCertificateDetailView.as_view()
