import pytest

from housing_accounting.users.models import User


pytestmark = pytest.mark.django_db


def test_user_super_admin_defaults_to_false(user: User):
    assert user.is_super_admin is False
