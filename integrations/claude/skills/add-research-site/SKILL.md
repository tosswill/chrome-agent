---
name: add-research-site
description: Add a new research site (e.g. a retailer such as Home Depot or Newegg) to the chrome-agent research tools - a browser of its own, signed in to the site with the research Google account (Google sign-in first, email otherwise), saved as a chrome-agent profile, plus a scaffolded read-only research helper. Use when the user asks to add, onboard or set up a new site for research.
---

# Add a research site

```bash
O=~/.claude/skills/add-research-site/scripts/onboard_site.py
```

The site gets its own browser (`<site>-research-01`) and profile, so research there never disturbs the
YouTube or Reddit browsers. Accounts use the research persona; prefer **Sign in with Google** (it looks like
ordinary traffic), else sign up by email with a generated password.

## Rules

1. **Local only:** the persona (`~/.config/chrome-agent-research/persona.json`) and the logins file
   (`~/.config/chrome-agent-research/.logins`: site, email, password or `google-oauth`, date; mode 600) never go
   into the repo, a report, or a chat message. Never print a password; `type --password SITE` types it.
2. **The agent may:** click Sign in / Create account / Continue with Google, choose the research account,
   accept Google's consent screen and the site's terms, fill name / email / password, open the verification
   email in Gmail and click its link, and save the profile.
3. **Stop and ask the user when:**
   - the research Google account isn't signed in in the site's browser (`google-check` → `stop`, or Google
     shows "Choose an account … Signed out"): the user signs in once in that window, then continue;
   - the site wants a **phone number** (`phone_required`, or `phone: true` in a result) — the workflow never
     adds one; ask whether to research signed out instead;
   - a CAPTCHA, bot check or access-denied page (`stop`);
   - anything asking for payment, an address, or a credit application.
4. Read-only afterwards: the research helper never buys, adds to cart, reviews or changes the account.

## Workflow

1. **Browser:** `$O clone <site>` launches a fresh `<site>-research-01` (profile folder
   `~/.local/share/chrome-agent-profiles/<site>-research`) and copies the Google cookies from
   `youtube-research-01`. Google treats copied cookies as a new device, so expect Gmail to show the account as
   **signed out**: `$O open <site> --domain <domain>`, `$O nav <ID> https://mail.google.com/mail/u/0/`, `$O shot <ID>`;
   if it asks to sign in, **ask the user to sign in once** in that window, then `$O google-check <site>`.
2. **Find sign-up:** `$O nav <ID> https://www.<domain>/`, `$O shot <ID>` (look at it), then the account menu
   (`$O fields <ID>` lists buttons and inputs with selectors; `$O click <ID> --text "Create an Account"` or
   `--selector ...`). Header pop-ups close on their own; if a click lands elsewhere, open the account menu and
   click inside it.
3. **Google first:** if there's "Continue with Google" / "Sign in with Google", click it. A popup opens as a
   new tab (`new_tabs` in the click result): `$O adopt <site> <tab id>`, pick the research account, accept the
   consent screen, and go back to the site's tab. Record it: `$O login-record <site> --method google`.
4. **Else email:** `$O password <site>` (stored, never printed), then type the email
   (`$O type <ID> --selector <css> --persona email`), names (`--persona first`, `--persona last`) and the password
   (`--password <site>`), accept the terms, submit, and record it: `$O login-record <site> --method email`.
   After every step, check the result for `phone_required` / `stop` and take a `shot`.
5. **Verify the email:** `$O nav <ID> https://mail.google.com/mail/u/0/#inbox`, open the site's message, and click
   its verification link (it may open a new tab: `adopt` it).
6. **Confirm the login:** load the site again and check the account menu shows the persona's name, not
   "Sign in". Note how the site shows it (selector, cookie name) for the research helper's signed-in check.
7. **Tidy and save:** `$O close <ID>`, then `$O tidy <site> --keep <domain>` (closes every other tab: Gmail,
   popups, the new-tab page) and `$O save <site>` (`chrome-agent save <site>-research-01 --overwrite`).
8. **Helper:** `$O scaffold <site> --domain <domain> --name "<Site Name>"` creates
   `skills/<site>-research/` (helper with pacing, window claims, stop rules; SKILL.md stub) and a test. Fill in
   the signed-in check from step 6, then the readers the research needs (search, product page, reviews, Q&A),
   add tests, and add the site to the Desktop MCP server if it should be available there.

## Site notes

- **Home Depot (2026-11-12):** no Google sign-in; email sign-up asks for a phone number ("My phone number will
  be … for recovering account") before the password step. Not created: the browser is set up and signed in to
  Google; research there runs signed out unless the user provides a phone number.
