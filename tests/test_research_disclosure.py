"""Disclosure rules shared by the research skills (integrations/claude/lib/disclosure.py). No browser."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "lib"))
import disclosure as D  # noqa: E402


def signals(text, subject="", **kw):
    return [h["signal"] for h in D.scan_text(text, "t", subject=subject, **kw)]


# --- negation ------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Bambu's never sponsored me.",
    "This video is not sponsored by Bambu.",
    "They did not sponsor today's video.",
    "They are not sponsoring this video.",
    "There's no reason for me to love it that I'm sponsored by them or anything",
])
def test_negated_sponsorship_is_a_denial(text):
    sig = signals(text, "Bambu Lab P2S review")
    assert "sponsor_read" not in sig and "third_party_sponsor" not in sig
    assert "claims_not_sponsored" in sig


def test_sarcastic_quote_after_denial_is_not_a_read():
    text = "Bambu's never sponsored me. They're like, 'Oh, you're sponsored by me.'"
    sig = signals(text, "Over 30 printers ranked Bambu Prusa")
    assert sig == ["claims_not_sponsored"]


def test_review_unit_plus_denial_in_one_sentence():
    sig = signals("Qidi did send me this printer, but they did not sponsor today's video", "Qidi Q2 review")
    assert "review_unit" in sig and "claims_not_sponsored" in sig and "sponsor_read" not in sig


def test_plain_sponsor_read_kept():
    hits = D.scan_text("This video is sponsored by Bambu Lab.", "d", subject="Bambu Lab P2S review")
    assert [(h["signal"], h["sponsor"]) for h in hits] == [("sponsor_read", "Bambu Lab")]


def test_negated_helper_window():
    t = "No, this video is sponsored by Bambu"
    i = t.index("sponsored")
    assert not D.negated(t, i, i + 9)  # "No," is a separate clause
    t2 = "it was never sponsored"
    j = t2.index("sponsored")
    assert D.negated(t2, j, j + 9)


# --- review units --------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Prusa sent me the Core One to review.",
    "Qidi did send me this printer.",
    "Thanks to Small Rig for sending over the S70.",
    "This is a review unit.",
    "The printer is a loaner from Elegoo.",
    "The unit was provided for review by Creality.",
])
def test_review_unit_triggers(text):
    assert "review_unit" in signals(text, "printer review")


@pytest.mark.parametrize("text", [
    "Support sent me a new one for free.",
    "They sent me a replacement under warranty.",
    "files provided by prusa",
    "Rental equipment provided by The Camera Store",
    "Bambu sent me an email about the update.",
])
def test_review_unit_exclusions(text):
    assert "review_unit" not in signals(text, "printer review")


def test_rental_is_its_own_signal():
    assert signals("Rental equipment provided by The Camera Store", "wireless mics DJI") == ["rented_or_borrowed"]


# --- third-party sponsors --------------------------------------------------

def test_spoken_third_party_sponsor_read():
    hits = D.scan_text("that board was manufactured by PCB way, the sponsor of this video", "t",
                       subject="Tested to Failure Snapmaker U1")
    assert [(h["signal"], h["sponsor"]) for h in hits] == [("third_party_sponsor", "PCB way")]


def test_appositive_sponsor_name():
    hits = D.scan_text("Thanks to Fujifilm, today's sponsor, for the camera.", "t", subject="Wireless mics DJI Rode")
    assert hits[0]["signal"] == "third_party_sponsor" and hits[0]["sponsor"] == "Fujifilm"


def test_music_code_is_third_party():
    hits = D.scan_text("Get 10% off with code DIGITALTECH10", "t", subject="DJI Mic review",
                       context="Soundstripe has royalty free music for your videos.")
    assert hits[0]["signal"] == "third_party_sponsor" and hits[0]["sponsor"] == "Soundstripe"


def test_brand_code_stays_brand_code():
    hits = D.scan_text("Use code DJI10 for 10% off the DJI Mic", "t", subject="DJI Mic review")
    adj = D.brand_adjust(hits, "DJI Mic review", None)
    assert [h["signal"] for h in adj] == ["brand_code_or_link"]


def test_scan_segments_uses_neighbors_and_dedupes():
    segs = [{"at": "1:00", "sec": 60, "text": "Soundstripe has great royalty free music."},
            {"at": "1:05", "sec": 65, "text": "Get 10% off with code DIGITALTECH10"}]
    hits = D.scan_segments(segs + segs, "DJI Mic review")
    assert [(h["signal"], h["source"]) for h in hits] == [("third_party_sponsor", "transcript 1:05")]


def test_own_site_read_is_self_promotion():
    hits = D.scan_text("This section is brought to you by comparewirelesslavmics.com", "t", subject="lav mics",
                       own_domains=["comparewirelesslavmics.com"])
    assert [h["signal"] for h in hits] == ["self_promotion"]


# --- giveaways, bought it myself -------------------------------------------

def test_giveaway_of_reviewed_product_is_brand_promo():
    subj = "Watch BEFORE buying a microphone Hollyland Lark M2"
    hits = D.brand_adjust(D.scan_text("I'm giving away a Hollyland Lark M2 to one subscriber", "t", subject=subj),
                          subj, None)
    assert [h["signal"] for h in hits] == ["brand_giveaway"]


def test_bought_it_myself_needs_the_product():
    assert signals("when I bought my car in 2016", "Is the Bambu P2S still worth it") == []
    assert signals("I bought this P2S with my own money", "Bambu P2S") == ["bought_it_myself"]
    assert signals("I bought it myself", "Bambu P2S") == ["bought_it_myself"]


# --- links ---------------------------------------------------------------

def test_classify_links():
    c = D.classify_links([
        "https://da.gd/abc",
        "https://us.qidi3d.com/products/q2?sca_ref=123",
        "https://link.amazon/xyz",
        "https://amzn.to/abc",
        "https://bambulab.com/en/p2s?utm_campaign=spring&utm_source=yt",
        "https://3dfarmers.com/kits?utm_campaign=farmloop",
        "https://www.patreon.com/somechannel",
    ], channel="3D Farmers")
    assert c["short_links"] == ["https://da.gd/abc"]
    assert c["affiliate_links"] == ["https://us.qidi3d.com/products/q2?sca_ref=123", "https://link.amazon/xyz",
                                    "https://amzn.to/abc"]
    assert c["campaign_links"] == ["https://bambulab.com/en/p2s?utm_campaign=spring&utm_source=yt"]
    assert c["self_promotion_links"] == ["https://3dfarmers.com/kits?utm_campaign=farmloop",
                                         "https://www.patreon.com/somechannel"]
    assert c["own_domains"] == ["3dfarmers.com"]
    sig = {h["signal"]: h["weight"] for h in D.link_hits(c)}
    assert sig == {"affiliate_links": 0.15, "campaign_tracking": 0.1, "self_promotion": 0.05}


def test_my_store_line_marks_self_promotion():
    c = D.classify_links(["https://shop.example.com/luts"], channel="Someone Films",
                         description="My LUTs: https://shop.example.com/luts")
    assert c["self_promotion_links"] == ["https://shop.example.com/luts"]


def test_merge_link_classes_moves_own_links():
    d = D.merge_link_classes({"affiliate_links": ["https://stalman.com/shop?ref_id=1"], "campaign_links": []},
                             D.classify_links(["https://stalman.com/shop?ref_id=1"], channel="Tyler Stalman"))
    assert d["affiliate_links"] == [] and d["self_promotion_links"] == ["https://stalman.com/shop?ref_id=1"]


# --- brand-owned channel --------------------------------------------------

def test_brand_owned_is_title_based():
    assert D.brand_owned("CNC Kitchen", "Bambu Lab just made a toolchanger H3C") is None
    assert D.brand_owned("PetaPixel", "We Tested EVERY Major Wireless Microphone!") is None
    possible = D.brand_owned("Bambu Lab", "Introducing the Bambu Lab H2D")
    assert possible["brand_owned"] == "possible" and possible["weight"] < 0.6
    strong = D.brand_owned("Bambu Lab", "Bambu Lab H2D launch", handle="@BambuLab",
                           brand_domains=["https://bambulab.com/en"])
    assert strong["brand_owned"] == "strong" and strong["weight"] == 1.0


def test_brand_adjust_does_not_flag_keyword_match():
    # The channel name is in the keywords (subject) but not in the title.
    out = D.brand_adjust([], "Bambu H3C review CNC Kitchen 3d printing", "CNC Kitchen",
                         title="Bambu just made a toolchanger")
    assert out == []


def test_score_reports_brand_owned_and_counts_once():
    page = [D.brand_owned("Bambu Lab", "Introducing the Bambu Lab H2D"),
            {"signal": "affiliate_links", "weight": 0.15, "source": "description", "text": "x"},
            {"signal": "affiliate_links", "weight": 0.15, "source": "description", "text": "y"}]
    res = D.score(page, [])
    assert res["score"] == 0.55 and res["brand_owned"] == "possible"


# --- 2026-11-07 false positives -------------------------------------------------------

def test_tip_jar_is_not_a_review_unit():
    t = "If this was helpful and you would like to send me a tip as a thank you: venmo @someone"
    assert "review_unit" not in signals(t, "Bissell Big Green")


def test_rental_advice_is_not_a_disclosure():
    assert "rented_or_borrowed" not in signals("or just rented one from a kiosk.", "Bissell Big Green")
    assert "rented_or_borrowed" not in signals("You can rent one at the grocery store.", "Rug Doctor")
    assert "rented_or_borrowed" in signals("I rented a Rug Doctor Pro Deep over the weekend.", "Rug Doctor")


def test_giveaway_as_telltale_is_not_a_giveaway():
    t = "the chocolate syrup is the biggest I'd say giveaway to the performance difference"
    assert "giveaway" not in signals(t, "Bissell Big Green Rug Doctor")
    assert "giveaway" not in signals("That's a dead giveaway that it leaks.", "Hoover")
    assert "giveaway" in signals("This week's free giveaway is a Big Green.", "Bissell Big Green")


# --- 2026-11-06 link classification -------------------------------------------------------

def test_retailer_ref_links_are_affiliate_not_self_promotion():
    links = ["https://shopsolarkits.com/products/x?ref=TheSolarLab", "https://signaturesolar.com/item?ref=TheSolarLab",
             "https://thesolarlab.com/guide"]
    c = D.classify_links(links, channel="The Solar Lab",
                         description="Shop my favorite products:\nhttps://shopsolarkits.com/products/x?ref=TheSolarLab")
    assert c["affiliate_links"] == links[:2]
    assert c["self_promotion_links"] == ["https://thesolarlab.com/guide"]


def test_brand_short_link_is_affiliate():
    c = D.classify_links(["https://ankerfast.club/anker.solix_tsl"], channel="The Solar Lab")
    assert c["affiliate_links"] == ["https://ankerfast.club/anker.solix_tsl"]
    assert c["short_links"] == ["https://ankerfast.club/anker.solix_tsl"]


def test_own_site_by_channel_name_still_self_promotion():
    c = D.classify_links(["https://vacuumwars.com/best/", "https://www.patreon.com/x"], channel="Vacuum Wars")
    assert c["self_promotion_links"] == ["https://vacuumwars.com/best/", "https://www.patreon.com/x"]



def test_commission_wording_is_a_disclosure_not_a_pitch():
    import disclosure as D
    sig = [h["signal"] for h in D.scan_text("I wasn't otherwise paid or sponsored to make this video, but I do earn a "
                                            "small commission if you buy through my links.", "description")]
    assert "affiliate_disclosure" in sig and "use_my_link_or_code" not in sig


def test_ad_tag_and_sent_to_review():
    import disclosure as D
    assert "ad_tag" in [h["signal"] for h in D.scan_text("#Ad Momcozy sent me this one.", "description")]
    assert "ad_tag" not in [h["signal"] for h in D.scan_text("Watch the #adorable baby.", "description")]
    seg = [{"text": "so I was sent these to review and they all work", "sec": 30}]
    assert "review_unit" in [h["signal"] for h in D.scan_segments(seg, "")]


def test_plain_amazon_share_link_is_not_affiliate():
    import disclosure as D
    out = D.classify_links(["https://www.amazon.com/dp/B0ABC12345?ref=ppx_pop_mob_ap_share",
                            "https://www.amazon.com/dp/B0ABC12345?tag=creator-20", "https://amzn.to/3abc"])
    assert out["affiliate_links"] == ["https://www.amazon.com/dp/B0ABC12345?tag=creator-20", "https://amzn.to/3abc"]


# --- password-manager run (2026-11-07) ------------------------------------------------------------

def _sig(text, subject="Best password manager 2026"):
    return [h["signal"] for h in D.scan_text(text, "d", subject=subject)]


def test_no_affiliate_links_is_a_denial_not_a_disclosure():
    assert _sig("We don't have any affiliate links.") == ["claims_not_sponsored"]
    assert "affiliate_disclosure" in _sig("This description contains affiliate links.")


def test_membership_early_access_is_not_a_review_unit():
    assert "review_unit" not in _sig("Membership gets you early access to videos.")
    assert "review_unit" in _sig("We got early access to the new model for this review.", "Canon R5 II review")


def test_our_link_affiliated_and_ownership():
    assert "use_my_link_or_code" in _sig("Using our link can save you even more.")
    s = _sig("We are affiliated but not sponsored.")
    assert "affiliate_disclosure" in s and "claims_not_sponsored" not in s
    assert "ownership_tie" in _sig("VPNpro is owned by Mediatech, whose investors are the founders of Nord Security.")


def test_in_category_sponsor_is_brand_money():
    pm = D.scan_text("This video is sponsored by 1Password.", "d", subject="Best password manager 2026")
    assert pm[0]["signal"] == "sponsor_read" and pm[0].get("in_category")
    cam = D.scan_text("This video is sponsored by 1Password.", "d", subject="Sony A7 IV review")
    assert cam[0]["signal"] == "third_party_sponsor"


def test_own_domain_redirects_are_affiliate_links():
    c = D.classify_links(["https://vpnpro.sale/NordPass-Discount/x/", "https://vpnpro.com/about",
                          "https://1password.partnerlinks.io/someone"], channel="VPNpro", handle="@vpnpro")
    assert c["own_redirect_links"] == ["https://vpnpro.sale/NordPass-Discount/x/"]
    assert "https://1password.partnerlinks.io/someone" in c["affiliate_links"]
    assert c["self_promotion_links"] == ["https://vpnpro.com/about"]
    c = D.classify_links(["https://cyberlabgo.com/roboform-v2", "https://cyberlab.com/"], channel="Cyber Lab")
    assert c["affiliate_links"] == ["https://cyberlabgo.com/roboform-v2"]


# --- markdown-editor run (2026-11-08) ---------------------------------------------------------------

def test_sponsor_name_after_comma_and_partnering():
    subj = "Apple Notes iOS 27 Markdown"
    a = D.scan_text("And that's where today's sponsor comes in, Monarch.", "transcript", subject=subj)
    b = D.scan_text("That's why I recommend today's sponsor, G2I, to companies.", "transcript", subject=subj)
    c = D.scan_text("Thanks to Monarch for partnering with me!", "description", subject=subj)
    assert [(h["signal"], h["sponsor"]) for h in a] == [("third_party_sponsor", "Monarch")]
    assert [(h["signal"], h["sponsor"]) for h in b] == [("third_party_sponsor", "G2I")]
    assert c and c[0]["signal"] == "third_party_sponsor"


def test_resolve_third_party_reassigns_unnamed_read_and_codes():
    page = [{"signal": "third_party_sponsor", "weight": 0.15, "sponsor": "Monarch", "text": "Thanks to Monarch"},
            {"signal": "brand_code_or_link", "weight": 0.2, "text": "using my link https://monarchmoney.example/x"}]
    extra = [{"signal": "sponsor_read", "weight": 0.5, "sponsor": None, "text": "today's sponsor is here"},
             {"signal": "use_my_link_or_code", "weight": 0.15, "text": "Use promo code JANE50"}]
    p, e = D.resolve_third_party(page, extra)
    assert {h["signal"] for h in p + e} == {"third_party_sponsor"}
    # a named reviewed-brand read keeps unrelated unnamed reads as they are
    p, e = D.resolve_third_party(page, [{"signal": "sponsor_read", "sponsor": "Typora", "weight": 0.5, "text": "x"},
                                        {"signal": "sponsor_read", "sponsor": None, "weight": 0.5, "text": "y"}])
    assert [h["signal"] for h in e] == ["sponsor_read", "sponsor_read"]


def test_creator_product_builder_and_employer():
    for t in ("This is the app I work on, so take that into account.", "We at Morgen built a calendar.",
              "Check out the thing I'm building for writers.", "Disclosure: I make Downright"):
        assert any(h["signal"] == "creator_product" for h in D.scan_text(t, "transcript")), t
    for t in ("We at least tried.", "I built my vault over years.",
              "DISCLAIMER: I participate in the Amazon Services LLC Associates Program."):
        assert not any(h["signal"] == "creator_product" for h in D.scan_text(t, "transcript")), t


def test_chat_invites_are_not_affiliate_links():
    c = D.classify_links(["https://discord.gg/abc123", "https://t.me/somechannel"])
    assert not c["affiliate_links"] and not c["short_links"]
