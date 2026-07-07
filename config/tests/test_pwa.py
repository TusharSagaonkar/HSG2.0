from http import HTTPStatus

from django.urls import reverse


def test_pwa_manifest_is_served(client):
    response = client.get(reverse("pwa-manifest"))

    assert response.status_code == HTTPStatus.OK
    assert response["Content-Type"].startswith("application/manifest+json")

    content = response.content.decode()
    assert '"name": "Housynk"' in content
    assert '"scope": "/"' in content
    assert reverse("pwa-offline") not in content


def test_service_worker_is_served(client):
    response = client.get(reverse("pwa-service-worker"))

    assert response.status_code == HTTPStatus.OK
    assert response["Content-Type"].startswith("application/javascript")

    content = response.content.decode()
    assert "serviceWorker" not in content
    assert "CACHE_NAME" in content


def test_offline_page_is_served(client):
    response = client.get(reverse("pwa-offline"))

    assert response.status_code == HTTPStatus.OK
    assert "text/html" in response["Content-Type"]
    assert "You are offline." in response.content.decode()


def test_home_page_includes_install_prompt(client, user):
    client.force_login(user)

    response = client.get(reverse("home"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "data-pwa-install-prompt" in content
    assert "data-pwa-install-action" in content
    assert reverse("pwa-manifest") in content


def test_login_page_includes_install_cta(client, db):
    response = client.get(reverse("account_login"))

    assert response.status_code == HTTPStatus.OK
    content = response.content.decode()
    assert "data-pwa-install-entry" in content
    assert "data-pwa-install-action" in content
    assert "Install app" in content
