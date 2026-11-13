"""Newegg research helper (generated scaffold). No browser."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "skills" / "newegg-research" / "scripts"))
import newegg_tab as H  # noqa: E402


def test_only_the_site_loads():
    import pytest
    H.SITE.check_url("https://www.newegg.com/")
    with pytest.raises(H.Fail):
        H.SITE.check_url("https://example.com/")


def test_block_page_stops():
    assert H._stop_reason({"blocked": True}, H.SITE)
    assert H._stop_reason({"loggedIn": True}, H.SITE) is None


def test_item_from_product_urls():
    import pytest
    assert H._item_of("https://www.newegg.com/some-card/p/N82E16814126845") == "N82E16814126845"
    assert H._item_of("https://www.newegg.com/p/N82E16814126798?Item=N82E16814126798") == "N82E16814126798"
    with pytest.raises(H.Fail):
        H._item_of("https://www.newegg.com/p/pl?d=rtx")  # a search page, not a product
    assert H._item_of("https://www.newegg.com/x/p/3D5-006G-00042") == "3D5-006G-00042"


def test_review_flags_and_options():
    f = H._review_flags([{"verified_owner": True, "related_product": "EDITION: PRIME"}, {"manufacturer_response": "Hi"}])
    assert f == {"read": 2, "verified_owner": 1, "other_variant": 1, "incentivized": 0, "manufacturer_responses": 1}
    import pytest
    with pytest.raises(H.Fail):
        H.reviews("X", "https://www.newegg.com/a/p/N82E1", sort="best")
