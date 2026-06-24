from reconciliation.models import BankParserProfile


class BankProfileResolver:
    """Resolve the most appropriate parser profile for a detected bank format."""

    def resolve(self, society, bank_name: str, file_type: str, confidence: int = 0, format_name: str = ""):
        profiles = (
            BankParserProfile.objects.filter(
                society=society,
                bank_name__iexact=bank_name,
                file_type__iexact=file_type,
                is_active=True,
            )
            .order_by("-priority", "format_name")
        )
        for profile in profiles:
            if format_name and profile.format_name.lower() == format_name.lower():
                return profile
            if confidence >= profile.confidence_floor:
                return profile
        return profiles.first()
