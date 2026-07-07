"""Shared test factories for all apps.

These factories are the SINGLE source of truth for test data creation.
All apps should import from here, not create their own factories.

Usage:
    from core.test_factories import SocietyFactory, UserFactory, FIXED_SOCIETY_NAME
"""

from collections.abc import Sequence
from typing import Any

import factory
from factory import Faker
from factory import post_generation
from factory.django import DjangoModelFactory

from housing_accounting.users.models import User
from societies.models import Society

#: The canonical name for the fixed test society.
#: All tests should use this society — it is created ONCE per session.
FIXED_SOCIETY_NAME = "Test Society Alpha"


class SocietyFactory(DjangoModelFactory):
    """Factory for Society with django_get_or_create by name.

    Creating a society triggers post_save signals that bootstrap:
    - ~100 accounting records (FinancialYear, AccountCategory, Account tree)
    - ~32 gateops records (GateOpsSocietyConfig, Gate, categories, roles, etc.)

    Because of django_get_or_create, repeated calls with the same name
    return the existing society without re-triggering the bootstrap.
    """

    name = FIXED_SOCIETY_NAME
    registration_number = factory.Sequence(lambda n: f"REG-{n:05d}")

    class Meta:
        model = Society
        django_get_or_create = ("name",)


class UserFactory(DjangoModelFactory):
    """Factory for User with django_get_or_create by email.

    The project's custom User model has ``username = None`` and uses
    ``email`` as the ``USERNAME_FIELD`` (unique). We therefore key
    get_or_create on ``email`` and mirror the password-handling pattern
    from ``housing_accounting.users.tests.factories.UserFactory`` so
    that ``UserFactory(password="x")`` keeps working.
    """

    email = Faker("email")
    name = Faker("name")

    @post_generation
    def password(self, create: bool, extracted: Sequence[Any], **kwargs):  # noqa: FBT001
        password = (
            extracted
            if extracted
            else Faker(
                "password",
                length=42,
                special_chars=True,
                digits=True,
                upper_case=True,
                lower_case=True,
            ).evaluate(None, None, extra={"locale": None})
        )
        self.set_password(password)

    @classmethod
    def _after_postgeneration(cls, instance, create, results=None):
        """Save again the instance if creating and at least one hook ran."""
        if create and results and not cls._meta.skip_postgeneration_save:
            # Some post-generation hooks ran, and may have modified us.
            instance.save()

    class Meta:
        model = User
        django_get_or_create = ["email"]
