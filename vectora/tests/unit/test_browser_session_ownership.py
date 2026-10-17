import pytest

from backend.browser import session


class _Page:
    pass


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
