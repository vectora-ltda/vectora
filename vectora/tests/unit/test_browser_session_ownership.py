import pytest

from backend.browser import session


class _Page:
    target_id: str | None = None


def test_browser_page_cannot_be_claimed_by_another_workspace() -> None:
    page = _Page()
    session._page_owners.clear()
    session._claim_page(page, "workspace-a")

    with pytest.raises(RuntimeError, match="workspace-a"):
        session._claim_page(page, "workspace-b")


def test_browser_page_can_be_reclaimed_by_same_workspace() -> None:
    page = _Page()
    session._page_owners.clear()
    session._claim_page(page, "workspace-a")
    session._claim_page(page, "workspace-a")


def test_browser_page_wrappers_with_same_target_id_cannot_cross_claim() -> None:
    """Dois wrappers Playwright Page distintos conectados ao mesmo target CDP
    devem ser reconhecidos como o mesmo alvo físico e bloquear cross-workspace."""
    wrapper_conn1 = _Page()
    wrapper_conn1.target_id = "target-chromium-tab-999"

    wrapper_conn2 = _Page()
    wrapper_conn2.target_id = "target-chromium-tab-999"

    assert id(wrapper_conn1) != id(wrapper_conn2)

    session._page_owners.clear()
    session._claim_page(wrapper_conn1, "workspace-alpha")

    with pytest.raises(RuntimeError, match="workspace-alpha"):
        session._claim_page(wrapper_conn2, "workspace-beta")


@pytest.mark.asyncio
async def test_create_tab_state_assigns_cdp_target_id() -> None:
    """_create_tab_state deve invocar Target.getTargetInfo e atribuir _target_id à página."""
    from unittest.mock import AsyncMock, MagicMock

    class MockCdp:
        async def send(self, method: str, *args, **kwargs):
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": "cdp-target-abc-123"}}
            return {}

    mock_page = MagicMock()
    mock_page.context.new_cdp_session = AsyncMock(return_value=MockCdp())

    tab = await session._create_tab_state(mock_page)
    assert getattr(mock_page, "_target_id") == "cdp-target-abc-123"
    assert tab.cdp is not None
