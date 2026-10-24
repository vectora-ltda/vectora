import pytest

from backend.browser import session


class _Page:
    target_id: str | None = None

    def __init__(self, target_id: str | None = "default-tab-1") -> None:
        self.target_id = target_id


def test_browser_page_cannot_be_claimed_by_another_workspace() -> None:
    page = _Page("tab-alpha")
    session._page_owners.clear()
    session._claim_page(page, "workspace-a")

    with pytest.raises(RuntimeError, match="workspace-a"):
        session._claim_page(page, "workspace-b")


def test_browser_page_can_be_reclaimed_by_same_workspace() -> None:
    page = _Page("tab-alpha")
    session._page_owners.clear()
    session._claim_page(page, "workspace-a")
    session._claim_page(page, "workspace-a")


def test_browser_page_wrappers_with_same_target_id_cannot_cross_claim() -> None:
    """Dois wrappers Playwright Page distintos conectados ao mesmo target CDP
    devem ser reconhecidos como o mesmo alvo físico e bloquear cross-workspace."""
    wrapper_conn1 = _Page("target-chromium-tab-999")
    wrapper_conn2 = _Page("target-chromium-tab-999")

    assert id(wrapper_conn1) != id(wrapper_conn2)

    session._page_owners.clear()
    session._claim_page(wrapper_conn1, "workspace-alpha")

    with pytest.raises(RuntimeError, match="workspace-alpha"):
        session._claim_page(wrapper_conn2, "workspace-beta")


def test_page_target_id_without_stable_identifier_raises() -> None:
    page = _Page(None)
    with pytest.raises(RuntimeError, match="não possui uma identidade estável"):
        session._page_target_id(page)


def test_browser_profile_id_matches_workbench_partition_derivation() -> None:
    assert session.browser_profile_id("ws-1", "thread-1") == (
        "session-919eaa27da596297"
    )


def test_page_is_closed_treats_broken_probe_as_stale() -> None:
    class _Closed:
        def is_closed(self) -> bool:
            return True

    class _Broken:
        def is_closed(self) -> bool:
            raise ConnectionError("CDP disconnected")

    assert session._page_is_closed(_Closed()) is True
    assert session._page_is_closed(_Broken()) is True
    assert session._page_is_closed(object()) is False


@pytest.mark.asyncio
async def test_create_tab_state_assigns_cdp_target_id() -> None:
    """_create_tab_state deve invocar Target.getTargetInfo e atribuir _target_id à página."""
    from unittest.mock import AsyncMock, MagicMock

    class MockCdp:
        def __init__(self):
            self.detached = False

        async def send(self, method: str, *args, **kwargs):
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": "cdp-target-abc-123"}}
            return {}

        async def detach(self):
            self.detached = True

    mock_page = MagicMock()
    cdp = MockCdp()
    mock_page.context.new_cdp_session = AsyncMock(return_value=cdp)

    tab = await session._create_tab_state(mock_page)
    assert mock_page._target_id == "cdp-target-abc-123"
    assert tab.cdp is not None


@pytest.mark.asyncio
async def test_create_tab_state_rejects_missing_or_empty_target_id() -> None:
    """Se o CDP não retornar targetId ou retornar vazio, deve desanexar o CDP e lançar RuntimeError."""
    from unittest.mock import AsyncMock, MagicMock

    class MockCdpEmpty:
        def __init__(self):
            self.detached = False

        async def send(self, method: str, *args, **kwargs):
            return {"targetInfo": {"targetId": "   "}}

        async def detach(self):
            self.detached = True

    cdp = MockCdpEmpty()
    mock_page = MagicMock(spec=[])
    mock_page.context = MagicMock()
    mock_page.context.new_cdp_session = AsyncMock(return_value=cdp)

    with pytest.raises(RuntimeError, match="Não foi possível obter uma identidade"):
        await session._create_tab_state(mock_page)
    assert cdp.detached is True


@pytest.mark.asyncio
async def test_create_tab_state_rejects_cdp_exception_and_detaches() -> None:
    """Se a chamada Target.getTargetInfo falhar, deve desanexar a sessão CDP e propagar RuntimeError."""
    from unittest.mock import AsyncMock, MagicMock

    class MockCdpError:
        def __init__(self):
            self.detached = False

        async def send(self, method: str, *args, **kwargs):
            raise ConnectionResetError("CDP connection lost")

        async def detach(self):
            self.detached = True

    cdp = MockCdpError()
    mock_page = MagicMock(spec=[])
    mock_page.context = MagicMock()
    mock_page.context.new_cdp_session = AsyncMock(return_value=cdp)

    with pytest.raises(RuntimeError, match="Falha ao obter identidade do alvo CDP"):
        await session._create_tab_state(mock_page)
    assert cdp.detached is True
