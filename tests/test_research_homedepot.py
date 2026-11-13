"""Home Depot research helper (generated scaffold). No browser."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "skills" / "homedepot-research" / "scripts"))
import homedepot_tab as H  # noqa: E402


def test_only_the_site_loads():
    import pytest
    H.SITE.check_url("https://www.homedepot.com/")
    with pytest.raises(H.Fail):
        H.SITE.check_url("https://example.com/")


def test_block_page_stops():
    assert H._stop_reason({"blocked": True}, H.SITE)
    assert H._stop_reason({"loggedIn": True}, H.SITE) is None


def test_product_path_from_any_page():
    slug, pid = H._product_path("https://www.homedepot.com/p/Brand-Drill-Kit-ABC123/317987598")
    assert (slug, pid) == ("Brand-Drill-Kit-ABC123", "317987598")
    assert H._product_path("https://www.homedepot.com/p/reviews/Brand-Drill-Kit-ABC123/317987598/3")[1] == "317987598"
    assert H._product_path("https://www.homedepot.com/p/questions/Brand-Drill-Kit-ABC123/317987598/1")[1] == "317987598"
    import pytest
    with pytest.raises(H.Fail):
        H._product_path("https://www.homedepot.com/b/Tools/N-5yc1vZc1xy")


def test_review_flags_summary():
    revs = [{"verified_purchase": True}, {"incentivized": True, "promotion": True, "syndicated_from": "brand.example"},
            {"syndicated_from": "brand.example", "variant": "Kit with 2 batteries"}, {"no_text": True}]
    f = H._review_flags(revs)
    assert f == {"read": 4, "verified_purchase": 1, "incentivized": 1, "promotion": 1,
                 "syndicated": {"brand.example": 2}, "other_variant": 1, "no_text": 1}
