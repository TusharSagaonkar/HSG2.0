"""Unit tests for shares app models"""
import pytest
from django.core.exceptions import ValidationError
from shares.models import ShareLedger, ShareCertificate, EventLog

# Test data factories would be defined here

class TestShareLedgerModel:
    """Test cases for ShareLedger model"""
    
    @pytest.mark.django_db
    def test_create_share_ledger_entry(self):
        """Test creating a basic share ledger entry"""
        entry = ShareLedger.objects.create(
            member_id=1,
            transaction_type='ALLOTMENT',
            shares=10,
            certificate_number='CERT-001'
        )
        assert entry is not None
        assert entry.balance == 10

    @pytest.mark.django_db
    def test_negative_shares_validation(self):
        """Test that negative shares are not allowed"""
        with pytest.raises(ValidationError):
            ShareLedger.objects.create(
                member_id=1,
                transaction_type='ALLOTMENT',
                shares=-5,
                certificate_number='CERT-001'
            )

    # More tests for constraints and methods...


class TestShareCertificateModel:
    """Test cases for ShareCertificate model"""
    
    @pytest.mark.django_db
    def test_certificate_status_transitions(self):
        """Test valid and invalid status transitions"""
        cert = ShareCertificate.objects.create(
            certificate_number='CERT-001',
            member_id=1,
            shares=10,
            status='ACTIVE'
        )
        
        # Valid transition
        cert.status = 'CANCELLED'
        cert.save()
        
        # Invalid transition
        cert.status = 'ACTIVE'
        with pytest.raises(ValidationError):
            cert.save()


class TestEventLogModel:
    """Test cases for EventLog model"""
    
    @pytest.mark.django_db
    def test_event_log_creation(self):
        """Test creating an event log entry"""
        log = EventLog.objects.create(
            event_type='SHARE_ALLOTMENT',
            object_id=1,
            details={'shares': 10}
        )
        assert log is not None
