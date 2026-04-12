from http import HTTPStatus

import pytest
from django.core.exceptions import PermissionDenied
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory

from housing_accounting.selection import SESSION_SELECTED_SOCIETY_ID
from housing_accounting.selection import get_selected_scope
from housing_accounting.users.tests.factories import UserFactory
from housing_accounting.users.views import GlobalSelectionUpdateView
from societies.decorators import role_required
from societies.models import Membership
from societies.models import Society
from societies.roles import ROLE_ACCOUNTANT
from societies.roles import ROLE_ADMIN
from societies.roles import ROLE_OWNER
from societies.services import create_society
from societies.services import create_user_by_admin
from societies.services import get_accessible_societies_qs


pytestmark = pytest.mark.django_db


def add_session_to_request(request):
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    return request


def test_create_society_assigns_owner_membership():
    user = UserFactory()

    society = create_society(user=user, name="Green Heights")

    membership = Membership.objects.get(user=user, society=society)
    assert society.created_by == user
    assert membership.role == ROLE_OWNER
    assert membership.invited_by == user
    assert membership.is_active is True


def test_admin_cannot_assign_owner_role():
    owner = UserFactory()
    society = create_society(user=owner, name="Alpha")
    admin = UserFactory()
    Membership.objects.create(user=admin, society=society, role=ROLE_ADMIN, invited_by=owner)

    with pytest.raises(PermissionDenied, match="Cannot assign this role"):
        create_user_by_admin(
            admin_user=admin,
            society=society,
            email="new-owner@example.com",
            password="strong-pass-123",
            role=ROLE_OWNER,
        )


def test_accountant_cannot_create_admin():
    owner = UserFactory()
    society = create_society(user=owner, name="Beta")
    accountant = UserFactory()
    Membership.objects.create(
        user=accountant,
        society=society,
        role=ROLE_ACCOUNTANT,
        invited_by=owner,
    )

    with pytest.raises(PermissionDenied, match="Cannot assign this role"):
        create_user_by_admin(
            admin_user=accountant,
            society=society,
            email="new-admin@example.com",
            password="strong-pass-123",
            role=ROLE_ADMIN,
        )


def test_selected_scope_ignores_unauthorized_society_in_session(rf: RequestFactory):
    user = UserFactory()
    allowed = create_society(user=user, name="Allowed")
    denied = Society.objects.create(name="Denied")
    request = add_session_to_request(rf.get("/"))
    request.user = user
    request.session[SESSION_SELECTED_SOCIETY_ID] = denied.id

    selected_society, _ = get_selected_scope(request)

    assert selected_society == allowed


def test_selection_update_rejects_unauthorized_society(rf: RequestFactory):
    user = UserFactory()
    allowed = create_society(user=user, name="Allowed")
    denied = Society.objects.create(name="Denied")
    request = add_session_to_request(
        rf.post(
            "/users/selection/",
            data={"selected_society_id": denied.id},
        )
    )
    request.user = user

    response = GlobalSelectionUpdateView.as_view()(request)

    assert response.status_code == HTTPStatus.FOUND
    assert request.session[SESSION_SELECTED_SOCIETY_ID] == allowed.id


def test_super_admin_bypass_works(rf: RequestFactory):
    super_admin = UserFactory(is_super_admin=True)
    request = rf.get("/")
    request.user = super_admin
    request.current_society = None

    @role_required(ROLE_ADMIN)
    def protected_view(request):
        return HttpResponse("ok")

    response = protected_view(request)

    assert response.status_code == HTTPStatus.OK


def test_django_superuser_gets_all_societies_in_access_queryset():
    user = UserFactory(is_superuser=True, is_staff=True)
    society_a = Society.objects.create(name="A")
    society_b = Society.objects.create(name="B")

    accessible_ids = list(get_accessible_societies_qs(user).values_list("id", flat=True))

    assert accessible_ids == [society_a.id, society_b.id]


def test_cross_society_access_blocked(rf: RequestFactory):
    user = UserFactory()
    society_a = create_society(user=user, name="A")
    society_b = Society.objects.create(name="B")
    request = rf.get("/")
    request.user = user
    request.current_society = society_b

    @role_required(ROLE_ADMIN)
    def protected_view(request):
        return HttpResponse("ok")

    response = protected_view(request)

    assert Membership.objects.filter(user=user, society=society_a).exists()
    assert response.status_code == HTTPStatus.FORBIDDEN
