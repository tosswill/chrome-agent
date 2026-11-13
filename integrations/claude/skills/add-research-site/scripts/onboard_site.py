#!/usr/bin/env python3
"""Add a research site: a browser of its own, signed in with the research Google account, saved as a profile.

The workflow (see ../SKILL.md): clone the signed-in YouTube session into a new browser, check Google,
sign up on the site (Google sign-in first, email otherwise), verify the email in Gmail, confirm the
login, close every other tab, save the profile, and scaffold the site's research helper.

CLI (each command prints JSON):

    onboard_site.py clone SITE                               launch the site's own browser (named SITE, no window
                       border); the user signs in to the research Google account in it once
    onboard_site.py google-check SITE                        is the research Google account signed in? (cookies)
    onboard_site.py open SITE --domain D                     claim a window in SITE's browser -> {"target"}
    onboard_site.py nav ID URL                               paced page load (the site, Google sign-in, Gmail)
    onboard_site.py shot ID [--out PATH]                     screenshot of the window (look at it)
    onboard_site.py fields ID                                visible buttons, links and inputs with selectors
    onboard_site.py click ID (--text TEXT | --selector CSS)  human-like click on a visible element
    onboard_site.py type ID --selector CSS (TEXT | --persona email|first|last|name | --password SITE)
    onboard_site.py key ID Enter|Tab|Escape|...              one key press
    onboard_site.py tabs SITE | adopt SITE TARGET            list the browser's tabs / claim one (an OAuth popup)
    onboard_site.py eval ID JS                               read something off the page
    onboard_site.py password SITE                            generate a password and store it in the logins file
    onboard_site.py login-record SITE --method google|email [--email E]   record the account in the logins file
    onboard_site.py tidy SITE --keep DOMAIN                  close every tab not on DOMAIN (one tab stays)
    onboard_site.py save SITE                                chrome-agent save SITE --overwrite
    onboard_site.py scaffold SITE --domain D [--name "Home Depot"]   generate the site's research helper,
                       SKILL.md stub and a test from the template (refuses to overwrite)
    onboard_site.py close ID

Local files, never in version control (RESEARCH_CONFIG, default ~/.config/chrome-agent-research):
    persona.json   {"email": "...", "first_name": "...", "last_name": "..."}
    .logins        tab-separated: site, email, password or "google-oauth", date (mode 600)
"""
import json
import os
import re
import secrets
import string
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]  # integrations/claude
sys.path.insert(0, str(ROOT / "lib"))
from research_core import Fail, Site, opt, pause, run_cli  # noqa: E402

CONFIG = Path(os.environ.get("RESEARCH_CONFIG", "~/.config/chrome-agent-research")).expanduser()
LOGINS = CONFIG / ".logins"
PERSONA = CONFIG / "persona.json"
GOOGLE_HOSTS = ("google.com", "gmail.com", "googleusercontent.com", "gstatic.com")

PAGE_STATE_JS = r"""(() => {
  const text = ((document.title || "") + " " + (document.body?.innerText || "").slice(0, 4000)).toLowerCase();
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 60 && r.height > 60 && s.visibility !== "hidden" && s.display !== "none"; };
  const challenge = [...document.querySelectorAll("iframe")].some(f =>
    /recaptcha\/.*\/bframe|hcaptcha|challenges\.cloudflare|captcha-delivery|arkoselabs|funcaptcha/.test(f.src || "") && visible(f));
  return {url: location.href, title: document.title,
          blocked: challenge || /access denied|unusual traffic|verify you are (a )?human|press (&|and) hold|are you a robot|request unsuccessful|pardon our interruption/.test(text),
          // A visible phone field (Home Depot asks "My phone number will be" before the password step).
          phone: [...document.querySelectorAll("input[type=tel], input[autocomplete*=tel], input[id*=phone i], " +
                   "input[name*=phone i], input[placeholder*=phone i]")].some(i => { const r = i.getBoundingClientRect(),
                     st = getComputedStyle(i);
                     return i.type !== "hidden" && r.width > 40 && r.height > 14 && r.bottom > 0 && r.top < innerHeight
                       && st.visibility !== "hidden" && st.display !== "none" && +st.opacity > 0 && !i.closest("[aria-hidden=true]"); })
                 || /verify (your )?phone|phone number is required|enter (your )?(mobile|phone) number|text (you )?a code/.test(text)};
})()"""


def _stop(state: dict, site: Site) -> str | None:
    if state.get("blocked"):
        return ("The site is showing a bot check, CAPTCHA or access-denied page. Stop and ask the user to "
                "solve it in the window, then continue.")
    return None


def instance_of(site: str) -> str:
    """The site's browser is named after the site ("newegg", "homedepot")."""
    return site


def site_for(site: str, domain: str | None = None) -> Site:
    inst = instance_of(site)
    prefix = "ONBOARD_" + re.sub(r"[^A-Z0-9]", "_", site.upper())
    os.environ.setdefault(f"{prefix}_INSTANCE", inst)
    os.environ.setdefault(f"{prefix}_STATE", f"/tmp/chrome-agent-onboard-{site}")
    hosts = tuple(h for h in ((domain,) if domain else ()) + GOOGLE_HOSTS)
    meta = _meta(site)
    if not domain and meta.get("domain"):
        hosts = (meta["domain"],) + GOOGLE_HOSTS
    return Site(prefix=prefix, default_instance=inst, hosts=hosts, ready_js="document.readyState === 'complete'",
                page_state_js=PAGE_STATE_JS, stop_reason=_stop, default_gap=4.0, default_max_loads=60)


def _meta_path(site: str) -> Path:
    return Path(f"/tmp/chrome-agent-onboard-{site}") / "meta.json"


def _meta(site: str) -> dict:
    try:
        return json.loads(_meta_path(site).read_text())
    except (OSError, ValueError):
        return {}


def _site_of_target(target: str) -> tuple[str, Site]:
    for d in Path("/tmp").glob("chrome-agent-onboard-*"):
        site = d.name.removeprefix("chrome-agent-onboard-")
        st = site_for(site)
        if any(k.upper().startswith(target.upper()) for k in st.claims()):
            return site, st
    raise Fail(f"no onboarding window matches {target!r}; claim one with `open SITE --domain D`.")


def _ca(*args: str, timeout: float = 180) -> str:
    p = subprocess.run(["chrome-agent", *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise Fail(f"chrome-agent {' '.join(args)} failed: {(p.stderr or p.stdout).strip()[:400]}")
    return p.stdout


# --- browser lifecycle ------------------------------------------------------------------

PROFILES = Path(os.environ.get("CHROME_AGENT_PROFILES", "~/.local/share/chrome-agent-profiles")).expanduser()


def clone(site: str, source: str | None = None) -> dict:
    """Launch the site's own browser: a fresh profile, named after the site, with no chrome-agent window
    border (the border and the title tag it adds are visible to the site). The research Google account is
    signed in by the user once, in this browser: copying Google cookies from another browser doesn't carry
    the sign-in and can make Google sign the original browser out."""
    inst = instance_of(site)
    status = json.loads(_ca("status"))
    if any(i.get("name") == inst and i.get("alive") for i in status):
        return {"instance": inst, "already_running": True}
    d = PROFILES / site
    d.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(["chrome-agent", "launch", "--no-window-border", "--name", inst], cwd=str(d),
                       capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise Fail(f"chrome-agent launch failed: {(p.stderr or p.stdout).strip()[:400]}")
    return {"instance": inst, "profile_dir": str(d), "border": False,
            "next": "open a window, load https://mail.google.com/ and ask the user to sign in once if it isn't"}


def google_check(site: str) -> dict:
    st = site_for(site)
    tabs = st.targets(restore=False)
    if not tabs:
        raise Fail(f"{st.instance} has no tab; run clone first.")
    res = st.cdp(tabs[0]["id"], "Network.getCookies", {"urls": ["https://accounts.google.com/", "https://mail.google.com/"]})
    now = time.time()
    names = {c.get("name") for c in res.get("cookies", []) if c.get("expires", -1) <= 0 or c["expires"] > now}
    ok = bool(names & {"SID", "__Secure-1PSID", "__Secure-3PSID"})
    out = {"instance": st.instance, "google_signed_in": ok}
    if not ok:
        out["stop"] = ("The research Google account isn't signed in in this browser. Ask the user to sign in to "
                       "Gmail in the window, then run google-check again.")
    return out


def open_window(site: str, domain: str) -> dict:
    st = site_for(site, domain)
    _meta_path(site).parent.mkdir(parents=True, exist_ok=True)
    _meta_path(site).write_text(json.dumps({"domain": domain, "instance": st.instance}))
    return st.open_window()


def tabs(site: str) -> list[dict]:
    st = site_for(site)
    claimed = set(st.claims())
    return [{"id": t["id"], "url": t.get("url"), "title": t.get("title"), "claimed": t["full_id"] in claimed}
            for t in st.targets()]


def adopt(site: str, target: str) -> dict:
    """Claim an existing tab (an OAuth popup, the tab a click opened) so the other commands can use it."""
    st = site_for(site)
    hit = [t for t in st.targets() if t["full_id"].upper().startswith(target.upper())]
    if len(hit) != 1:
        raise Fail(f"no single tab matches {target!r}; see `tabs {site}`.")
    now = time.time()
    st._write_claim(hit[0]["full_id"], {"created": now, "last_used": now, "loads": 0})
    return {"target": hit[0]["full_id"], "url": hit[0].get("url")}


def tidy(site: str, keep: str) -> dict:
    """Close every tab not on `keep` (and every claim), leaving one tab on the site."""
    st = site_for(site)
    ts = st.targets()
    keep_ids = [t["full_id"] for t in ts if keep in (t.get("url") or "")][:1]
    if not keep_ids:
        raise Fail(f"no tab is on {keep}; nav one there first.")
    closed = []
    for t in ts:
        if t["full_id"] not in keep_ids:
            _ca("stop", st.instance, "--target-id", t["full_id"])
            closed.append(t.get("url"))
    for k in list(st.claims()):
        (st._claims_dir / f"{k}.json").unlink(missing_ok=True)
    return {"kept": keep_ids[0], "closed": closed}


def save(site: str) -> dict:
    inst = instance_of(site)
    return {"saved": inst, "out": _ca("save", inst, "--overwrite").strip()[-400:]}


# --- page interaction -----------------------------------------------------------------------

_FIELDS_JS = r"""(() => {
  const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && r.bottom > 0 && r.top < innerHeight * 3 && s.visibility !== "hidden" && s.display !== "none"; };
  const sel = el => el.id ? "#" + CSS.escape(el.id) : el.name ? `${el.tagName.toLowerCase()}[name="${el.name}"]`
    : el.getAttribute("data-testid") ? `[data-testid="${el.getAttribute("data-testid")}"]` : null;
  const label = el => (el.getAttribute("aria-label") || el.labels?.[0]?.innerText || el.placeholder
    || el.innerText || el.value || el.title || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const out = [];
  for (const el of document.querySelectorAll("input, button, a, select, textarea, [role=button]")) {
    if (!vis(el) || (el.type === "hidden")) continue;
    out.push({tag: el.tagName.toLowerCase(), type: el.type || null, label: label(el), selector: sel(el),
              href: el.tagName === "A" ? (el.href || "").slice(0, 120) : undefined});
    if (out.length >= 80) break;
  }
  return out;
})()"""

_MARK_TEXT_JS = r"""((want) => {
  const norm = s => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
  const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== "hidden" && s.display !== "none"; };
  document.querySelectorAll("[data-onboard-hit]").forEach(e => e.removeAttribute("data-onboard-hit"));
  const all = [...document.querySelectorAll("button, a, [role=button], input[type=submit], input[type=button], label, span, div")]
    .filter(vis);
  const text = el => norm(el.getAttribute("aria-label") || el.innerText || el.value);
  let hit = all.find(el => text(el) === want) || all.find(el => /^(button|a)$/i.test(el.tagName) && text(el).includes(want));
  if (!hit) return false;
  hit = hit.closest("button, a, [role=button], label") || hit;
  hit.setAttribute("data-onboard-hit", "1");
  return true;
})(%s)"""


def shot(target: str, out: str | None = None) -> dict:
    site, st = _site_of_target(target)
    full_id = st.resolve(target)
    path = Path(out or f"/tmp/chrome-agent-onboard-{site}/shot-{int(time.time())}.jpg")
    st.screenshot(full_id, path, max_width=1600)
    state = st.evaluate(full_id, PAGE_STATE_JS) or {}
    return {"path": str(path), **state, "note": "Look at the image before acting."}


def fields(target: str) -> list[dict]:
    _, st = _site_of_target(target)
    return st.evaluate(st.resolve(target), _FIELDS_JS) or []


def click(target: str, text: str | None = None, selector: str | None = None) -> dict:
    site, st = _site_of_target(target)
    full_id = st.resolve(target)
    before = set(t["full_id"] for t in st.targets())
    if text:
        if not st.evaluate(full_id, _MARK_TEXT_JS % json.dumps(text.lower())):
            raise Fail(f"no visible element reads {text!r}; check `fields` or a screenshot.")
        selector = "[data-onboard-hit='1']"
    if not selector or not st.click_on(full_id, selector, look=0.6):
        raise Fail(f"couldn't click {selector!r}: not on the page or not visible.")
    pause(2.5)
    new = [t for t in st.targets() if t["full_id"] not in before]
    state = st.evaluate(full_id, PAGE_STATE_JS) or {}
    res = {"clicked": text or selector, **state}
    if new:
        res["new_tabs"] = [{"id": t["id"], "url": t.get("url")} for t in new]
        res["note"] = "The click opened a tab (an OAuth popup?): `adopt SITE <id>` to work in it."
    reason = _stop(state, st)
    if reason:
        res["stop"] = reason
    if state.get("phone"):
        res["phone_required"] = True
        res["note"] = "The page asks for a phone number: stop and tell the user (the workflow never adds one)."
    return res


def type_into(target: str, selector: str, text: str) -> dict:
    _, st = _site_of_target(target)
    full_id = st.resolve(target)
    if not st.click_on(full_id, selector, look=0.4):
        raise Fail(f"no visible field matches {selector!r}.")
    pause(0.4)
    st.type_text(full_id, text)
    got = st.evaluate(full_id, f"(document.querySelector({json.dumps(selector)})?.value || '').length")
    return {"typed_into": selector, "length": got, "ok": got == len(text)}


def key(target: str, name: str) -> dict:
    _, st = _site_of_target(target)
    full_id = st.resolve(target)
    st.key(full_id, name)
    pause(2)
    return st.evaluate(full_id, PAGE_STATE_JS) or {}


def nav(target: str, url: str) -> dict:
    _, st = _site_of_target(target)
    return st.nav(target, url)


def eval_js(target: str, js: str):
    _, st = _site_of_target(target)
    return st.evaluate(st.resolve(target), js)


def close(target: str) -> dict:
    _, st = _site_of_target(target)
    return st.close(target)


# --- persona and logins ---------------------------------------------------------------------

def persona(field: str) -> str:
    try:
        p = json.loads(PERSONA.read_text())
    except (OSError, ValueError):
        raise Fail(f"no persona file at {PERSONA}: {{\"email\": ..., \"first_name\": ..., \"last_name\": ...}}")
    vals = {"email": p.get("email"), "first": p.get("first_name"), "last": p.get("last_name"),
            "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()}
    if not vals.get(field):
        raise Fail(f"persona has no {field!r}")
    return vals[field]


def _logins() -> list[list[str]]:
    try:
        return [ln.split("\t") for ln in LOGINS.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    except OSError:
        return []


def _write_logins(rows: list[list[str]]) -> None:
    CONFIG.mkdir(parents=True, exist_ok=True)
    body = "# site\temail\tpassword or google-oauth\tdate\n" + "".join("\t".join(r) + "\n" for r in rows)
    LOGINS.write_text(body)
    os.chmod(LOGINS, 0o600)


def password(site: str) -> dict:
    """A new random password for SITE, stored (never printed). `type --password SITE` types it."""
    alphabet = string.ascii_letters + string.digits
    pw = "".join(secrets.choice(alphabet) for _ in range(14)) + secrets.choice("!#$%&*") + secrets.choice(string.digits)
    rows = [r for r in _logins() if r[0] != site]
    rows.append([site, persona("email"), pw, date.today().isoformat()])
    _write_logins(rows)
    return {"stored": str(LOGINS), "site": site, "length": len(pw)}


def stored_password(site: str) -> str:
    for r in _logins():
        if r[0] == site and len(r) >= 3 and r[2] != "google-oauth":
            return r[2]
    raise Fail(f"no stored password for {site}; run `password {site}` first.")


def login_record(site: str, method: str, email: str | None = None) -> dict:
    rows = _logins()
    if method == "google":
        rows = [r for r in rows if r[0] != site] + [[site, email or persona("email"), "google-oauth", date.today().isoformat()]]
    elif not any(r[0] == site for r in rows):
        raise Fail(f"no stored password for {site}; run `password {site}` before signing up by email.")
    _write_logins(rows)
    return {"recorded": site, "method": method, "file": str(LOGINS)}


# --- scaffold ---------------------------------------------------------------------------------------

def scaffold(site: str, domain: str, name: str | None = None) -> dict:
    """Generate skills/<site>-research/ (helper, SKILL.md stub) and tests/test_research_<site>.py from the
    template. Never overwrites."""
    slug = re.sub(r"[^a-z0-9]", "", site.lower())
    name = name or site.title()
    tdir = HERE.parent / "templates"
    skill = ROOT / "skills" / f"{slug}-research"
    test = ROOT.parents[1] / "tests" / f"test_research_{slug}.py"
    out = {}
    subs = {"{{SLUG}}": slug, "{{NAME}}": name, "{{DOMAIN}}": domain, "{{PREFIX}}": slug.upper() + "_TAB"}
    for src, dst in ((tdir / "site_tab.py.tmpl", skill / "scripts" / f"{slug}_tab.py"),
                     (tdir / "SKILL.md.tmpl", skill / "SKILL.md"), (tdir / "test_site.py.tmpl", test)):
        if dst.exists():
            out[str(dst)] = "exists (left alone)"
            continue
        txt = src.read_text()
        for k, v in subs.items():
            txt = txt.replace(k, v)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(txt)
        out[str(dst)] = "created"
    return {"scaffold": out, "next": "Fill in the site's search/page readers and its signed-in check, then add tests."}


# --- CLI -----------------------------------------------------------------------------------------------

def _dispatch(cmd: str, args: list[str]):
    if cmd == "clone" and len(args) == 1:
        return clone(args[0])
    if cmd == "google-check" and len(args) == 1:
        return google_check(args[0])
    if cmd == "open":
        domain = opt(args, "--domain")
        if len(args) != 1 or not isinstance(domain, str):
            raise Fail("usage: open SITE --domain DOMAIN")
        return open_window(args[0], domain)
    if cmd == "nav" and len(args) == 2:
        return nav(*args)
    if cmd == "shot" and args:
        out = opt(args, "--out")
        return shot(args[0], out if isinstance(out, str) else None)
    if cmd == "fields" and len(args) == 1:
        return fields(args[0])
    if cmd == "click":
        text, sel = opt(args, "--text"), opt(args, "--selector")
        if len(args) != 1 or not (isinstance(text, str) or isinstance(sel, str)):
            raise Fail("usage: click ID --text TEXT | --selector CSS")
        return click(args[0], text if isinstance(text, str) else None, sel if isinstance(sel, str) else None)
    if cmd == "type":
        sel, per, pw = opt(args, "--selector"), opt(args, "--persona"), opt(args, "--password")
        if not isinstance(sel, str) or not args:
            raise Fail("usage: type ID --selector CSS (TEXT | --persona email|first|last|name | --password SITE)")
        if isinstance(pw, str):
            text = stored_password(pw)
        elif isinstance(per, str):
            text = persona(per)
        else:
            text = " ".join(args[1:])
        res = type_into(args[0], sel, text)
        return res if not isinstance(pw, str) else {**res, "typed_into": sel, "secret": True}
    if cmd == "key" and len(args) == 2:
        return key(*args)
    if cmd == "tabs" and len(args) == 1:
        return tabs(args[0])
    if cmd == "adopt" and len(args) == 2:
        return adopt(*args)
    if cmd == "eval" and len(args) == 2:
        return eval_js(*args)
    if cmd == "password" and len(args) == 1:
        return password(args[0])
    if cmd == "login-record":
        method, email = opt(args, "--method"), opt(args, "--email")
        if len(args) != 1 or method not in ("google", "email"):
            raise Fail("usage: login-record SITE --method google|email [--email E]")
        return login_record(args[0], str(method), email if isinstance(email, str) else None)
    if cmd == "tidy":
        keep = opt(args, "--keep")
        if len(args) != 1 or not isinstance(keep, str):
            raise Fail("usage: tidy SITE --keep DOMAIN")
        return tidy(args[0], keep)
    if cmd == "save" and len(args) == 1:
        return save(args[0])
    if cmd == "scaffold":
        domain, name = opt(args, "--domain"), opt(args, "--name")
        if len(args) != 1 or not isinstance(domain, str):
            raise Fail('usage: scaffold SITE --domain DOMAIN [--name "Site Name"]')
        return scaffold(args[0], domain, name if isinstance(name, str) else None)
    if cmd == "close" and len(args) == 1:
        return close(args[0])
    raise Fail(f"unknown command or arguments: {cmd} {' '.join(args)}")


if __name__ == "__main__":
    sys.exit(run_cli(__doc__, _dispatch, sys.argv))
