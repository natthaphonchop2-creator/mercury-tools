import os

import pytest

from mercury_tools.config import load_settings
from mercury_tools.db.supabase import SupabaseRagStore


@pytest.mark.integration
def test_supabase_live_connection_requires_env() -> None:
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        pytest.skip("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")

    store = SupabaseRagStore(load_settings())
    assert store.base_url.endswith("/rest/v1")

