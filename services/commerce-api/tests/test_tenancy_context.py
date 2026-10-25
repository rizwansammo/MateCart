import threading
import uuid

import pytest

from commerce.core.ids import uuid7
from commerce.tenancy.context import (
    get_active_tenant_id,
    get_active_tenant_id_or_none,
    in_unscoped_access,
    tenant_context,
    unscoped_access,
)
from commerce.tenancy.exceptions import TenantContextConflict, TenantContextMissing


def test_there_is_no_default_tenant() -> None:
    assert get_active_tenant_id_or_none() is None
    with pytest.raises(TenantContextMissing):
        get_active_tenant_id()


def test_context_is_set_for_the_block_and_restored_after() -> None:
    tenant_id = uuid7()
    with tenant_context(tenant_id) as active:
        assert active == tenant_id
        assert get_active_tenant_id() == tenant_id
        with tenant_context(tenant_id):  # re-entering the same tenant is fine
            assert get_active_tenant_id() == tenant_id
    assert get_active_tenant_id_or_none() is None


def test_switching_tenants_inside_a_context_is_refused() -> None:
    with tenant_context(uuid7()), pytest.raises(TenantContextConflict), tenant_context(uuid7()):
        pass


def test_unscoped_access_needs_a_reason_and_no_active_tenant() -> None:
    with pytest.raises(ValueError, match="reason"), unscoped_access("  "):
        pass
    with tenant_context(uuid7()), pytest.raises(TenantContextConflict), unscoped_access("x"):
        pass
    with unscoped_access("maintenance"):
        assert in_unscoped_access()
        with pytest.raises(TenantContextConflict), tenant_context(uuid7()):
            pass
    assert not in_unscoped_access()


def test_context_does_not_leak_to_other_threads() -> None:
    seen: list[uuid.UUID | None] = []
    with tenant_context(uuid7()):
        worker = threading.Thread(target=lambda: seen.append(get_active_tenant_id_or_none()))
        worker.start()
        worker.join()
    assert seen == [None]
