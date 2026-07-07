import pytest

from housing_accounting.users.models import User
from housing_accounting.users.tests.factories import UserFactory
from core.test_factories import SocietyFactory


@pytest.fixture(autouse=True)
def _media_storage(settings, tmpdir) -> None:
    settings.MEDIA_ROOT = tmpdir.strpath


@pytest.fixture
def user(db) -> User:
    return UserFactory()


@pytest.fixture(scope="session")
def society(django_db_setup, django_db_blocker):
    """Session-scoped society with ALL bootstrapped accounts.

    Created ONCE per test session. All pytest-style tests should use
    this fixture instead of calling Society.objects.create().

    The society has:
    - ~100 accounting accounts (via accounting post_save signal)
    - ~32 gateops records (via gateops post_save signal)
    - FinancialYear, AccountCategory tree, etc.

    Usage:
        def test_something(client, user, society):
            # society is pre-built — no need to create accounts
            account = Account.objects.filter(society=society).first()
    """
    with django_db_blocker.unblock():
        soc = SocietyFactory()
    return soc
