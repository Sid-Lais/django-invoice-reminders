from django.core.management import call_command
from django.test import Client, override_settings


def test_collected_admin_css_is_served_without_debug(tmp_path, db):
    with override_settings(DEBUG=False, STATIC_ROOT=tmp_path):
        call_command("collectstatic", interactive=False, verbosity=0)

        response = Client().get("/static/admin/css/base.css")

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/css")
        response.close()
