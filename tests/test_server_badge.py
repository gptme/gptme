import xml.etree.ElementTree as ET

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from flask.testing import FlaskClient  # fmt: skip


def test_badge_svg_is_public_cacheable_svg(client: FlaskClient):
    response = client.get("/badge.svg")

    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    assert response.headers["Cache-Control"] == "public, max-age=86400"

    body = response.get_data(as_text=True)
    ET.fromstring(body)
    assert "built with" in body
    assert "gptme" in body
