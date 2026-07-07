"""Shared base test classes for Django TestCase-style tests.

Usage:
    from core.test_base import SocietyTestCase

    class MyTest(SocietyTestCase):
        # cls.society is available — created ONCE per class
        # cls.user is available

        def test_something(self):
            # Use self.society — accounts are already bootstrapped
            account = Account.objects.filter(society=self.society).first()
            ...
"""

from django.test import TestCase

from core.test_factories import SocietyFactory
from core.test_factories import UserFactory


class SocietyTestCase(TestCase):
    """Base TestCase that creates a single society in setUpTestData().

    The society is created ONCE per test class (not per test method).
    All bootstrapped accounts (via post_save signals) are available
    as ``cls.society`` and can be queried directly, e.g.::

        Account.objects.filter(society=cls.society)
        FinancialYear.objects.filter(society=cls.society)

    Subclasses should NOT create their own society. If a second society
    is needed for isolation tests, create it in setUpTestData() as
    ``cls.society_beta`` (with a distinct name to avoid colliding with
    the shared ``FIXED_SOCIETY_NAME``).

    Compatibility note:
        Existing gateops base classes (``RuleEngineTestBase``,
        ``GateOpsModelTestBase``, ``RuleModelTestBase``) follow the same
        ``setUpTestData`` -> ``cls.society`` convention and call
        ``super().setUpTestData()`` first. They can therefore be migrated
        to subclass ``SocietyTestCase`` without changing their bodies.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.society = SocietyFactory()
        cls.user = UserFactory()
