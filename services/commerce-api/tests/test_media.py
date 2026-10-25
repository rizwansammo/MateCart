"""Media uploads: server-side validation, tenant-namespaced storage, safe and gated serving."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from pytest_django.fixtures import Settings

from commerce.audit.models import AuditEvent
from commerce.catalog import services as catalog
from commerce.media.models import MediaAsset
from commerce.staff.permissions import StaffRole
from commerce.tenancy.context import tenant_context
from commerce.tenancy.models import Tenant

from .catalog_helpers import png_bytes, sign_in_staff, simple_product, stored_asset
from .factories import make_store_settings
from .helpers import Api

pytestmark = pytest.mark.usefixtures("store")

UPLOAD = "/api/manage/media/assets/upload/"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
def editor(api: Api, deployment_tenant: Tenant) -> Api:
    sign_in_staff(api, deployment_tenant, StaffRole.CONTENT_EDITOR, "editor@example.com")
    return api


def upload(
    api: Api, content: bytes, name: str = "photo.png", content_type: str = "image/png"
) -> Any:
    return api.upload(
        UPLOAD, {"file": SimpleUploadedFile(name, content, content_type=content_type)}
    )


@pytest.mark.parametrize(
    ("fmt", "content_type"), [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_images_are_detected_from_their_bytes(
    editor: Api, deployment_tenant: Tenant, fmt: str, content_type: str
) -> None:
    response = upload(
        editor, png_bytes((5, 7), fmt=fmt), name="../../evil.php", content_type="text/html"
    )

    assert response.status_code == 201, response.json()
    body = response.json()
    assert (body["kind"], body["content_type"], body["width"], body["height"]) == (
        "image",
        content_type,
        5,
        7,
    )
    with tenant_context(deployment_tenant):
        path = MediaAsset.objects.get(pk=body["id"]).file.name
    extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[fmt]
    assert path.startswith(f"tenants/{deployment_tenant.pk}/media/mediaasset/")
    assert path.endswith(f".{extension}")
    assert "evil" not in path
    assert ".." not in path


def test_pdf_documents_are_accepted(editor: Api) -> None:
    response = upload(editor, PDF, name="size-guide.pdf", content_type="application/pdf")
    assert response.status_code == 201
    assert (response.json()["kind"], response.json()["width"]) == ("document", None)


@pytest.mark.parametrize(
    "content",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"<html><script>alert(1)</script></html>",
        b"GIF89a\x01\x00\x01\x00\x00\x00\x00;",
        png_bytes()[:40],  # truncated PNG
        b"%PDF-1.4 no end marker",
        b"plain text",
    ],
    ids=["svg", "html", "gif", "truncated-png", "broken-pdf", "text"],
)
def test_unsafe_or_unsupported_files_are_refused(editor: Api, content: bytes) -> None:
    response = upload(editor, content)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_media"


def test_oversized_and_huge_images_are_refused(editor: Api, settings: Settings) -> None:
    huge = upload(editor, png_bytes((9000, 10), mode="1"))
    assert huge.json()["error"]["code"] == "image_too_large"

    settings.MATECART_MEDIA_MAX_BYTES = 1024
    big = upload(editor, b"\x89PNG" + b"0" * 70_000)
    assert big.status_code == 400
    assert big.json()["error"]["code"] == "file_too_large"


def test_uploads_need_catalogue_permission(
    api: Api, make_api: Callable[[], Api], deployment_tenant: Tenant
) -> None:
    assert upload(api, png_bytes()).status_code == 401
    handler = make_api()
    sign_in_staff(handler, deployment_tenant, StaffRole.ORDER_HANDLER, "orders@example.com")
    assert upload(handler, png_bytes()).status_code == 403


def test_staff_downloads_are_hardened(editor: Api) -> None:
    asset = upload(editor, png_bytes()).json()
    document = upload(editor, PDF, name="guide.pdf").json()

    image = editor.get(asset["url"])
    pdf = editor.get(document["url"])

    assert image.status_code == 200
    assert image["Content-Type"] == "image/png"
    assert image["X-Content-Type-Options"] == "nosniff"
    assert image["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert "no-store" in image["Cache-Control"]
    assert "private" in image["Cache-Control"]
    assert image["Content-Disposition"].startswith("inline")
    assert pdf["Content-Disposition"] == 'attachment; filename="document.pdf"'


def test_public_files_follow_publication(
    editor: Api, make_api: Callable[[], Api], deployment_tenant: Tenant
) -> None:
    asset_id = upload(editor, png_bytes()).json()["id"]
    visitor = make_api()
    public_url = f"/api/catalog/media/{asset_id}/"
    assert visitor.get(public_url).status_code == 404

    product = simple_product(deployment_tenant)
    with tenant_context(deployment_tenant):
        catalog.set_product_media(
            product=product, entries=[{"asset": MediaAsset.objects.get(pk=asset_id)}], actor=None
        )
    assert visitor.get(public_url).status_code == 404  # still a draft

    with tenant_context(deployment_tenant):
        catalog.set_status(product=product, status="active", actor=None)
    shown = visitor.get(public_url)
    assert shown.status_code == 200
    assert shown["Cache-Control"] == "public, max-age=300"

    with tenant_context(deployment_tenant):
        catalog.set_status(product=product, status="archived", actor=None)
    assert visitor.get(public_url).status_code == 404


def test_assets_in_use_are_not_deleted(editor: Api, deployment_tenant: Tenant) -> None:
    used = upload(editor, png_bytes()).json()["id"]
    unused = upload(editor, png_bytes((2, 2))).json()["id"]
    product = simple_product(deployment_tenant)
    with tenant_context(deployment_tenant):
        catalog.set_product_media(
            product=product, entries=[{"asset": MediaAsset.objects.get(pk=used)}], actor=None
        )
        stored = MediaAsset.objects.get(pk=unused).file.name

    assert (
        editor.delete(f"/api/manage/media/assets/{used}/").json()["error"]["code"] == "media_in_use"
    )
    assert editor.delete(f"/api/manage/media/assets/{unused}/").status_code == 204
    assert not default_storage.exists(stored)


def test_other_stores_assets_are_unreachable(
    editor: Api, deployment_tenant: Tenant, tenant_b: Tenant
) -> None:
    make_store_settings(tenant_b)
    foreign = stored_asset(tenant_b)
    product = simple_product(deployment_tenant)

    assert editor.get(f"/api/manage/media/assets/{foreign.pk}/file/").status_code == 404
    assert editor.get(f"/api/catalog/media/{foreign.pk}/").status_code == 404
    response = editor.put(
        f"/api/manage/catalog/products/{product.pk}/media/", {"media": [{"asset": str(foreign.pk)}]}
    )
    assert response.status_code == 400
    assert "asset" in str(response.json()["error"]["details"])


def test_upload_audit_records_no_client_filename(editor: Api, deployment_tenant: Tenant) -> None:
    upload(editor, png_bytes(), name="customer-rahim-passport.png")
    with tenant_context(deployment_tenant):
        event = AuditEvent.objects.get(action="media.asset.uploaded")
    assert "rahim" not in str(event.changes)
    assert set(event.changes) == {"kind", "content_type", "size_bytes"}


def test_media_root_is_outside_the_tenant_path_of_others(
    settings: Settings, tmp_path: Path
) -> None:
    assert Path(settings.MEDIA_ROOT).is_relative_to(tmp_path)


def test_unsaved_files_are_stored_under_the_active_tenant_or_refused(
    deployment_tenant: Tenant,
) -> None:
    from commerce.tenancy.exceptions import TenantContextMissing
    from commerce.tenancy.keys import tenant_storage_path

    with tenant_context(deployment_tenant):
        path = tenant_storage_path(MediaAsset(), "photo.png")
    assert path.startswith(f"tenants/{deployment_tenant.pk}/media/mediaasset/")
    with pytest.raises(TenantContextMissing):
        tenant_storage_path(MediaAsset(), "photo.png")
