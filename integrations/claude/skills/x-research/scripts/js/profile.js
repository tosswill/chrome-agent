// Header of an X profile page: name, handle, bio, counts, join date, verification and affiliation.
(() => {
  const num = (s) => {
    const m = (s || "").replace(/,/g, "").match(/([\d.]+)\s*([KMB])?/i);
    return m ? Math.round(parseFloat(m[1]) * ({K: 1e3, M: 1e6, B: 1e9}[(m[2] || "").toUpperCase()] || 1)) : null;
  };
  const name = document.querySelector("[data-testid=UserName]");
  if (!name) return null;
  const lines = name.innerText.split("\n").map(s => s.trim()).filter(Boolean);
  const link = (part) => [...document.querySelectorAll(`a[href$='/${part}']`)].find(a => /\d/.test(a.innerText));
  const hdr = document.querySelector("[data-testid=UserProfileHeader_Items]");
  return {
    name: lines[0] || null,
    handle: lines.find(l => l.startsWith("@")) || null,
    verified: !!name.querySelector("[data-testid=icon-verified]"),
    verified_kind: name.querySelector("[data-testid=icon-verified]")?.closest("[aria-label]")?.getAttribute("aria-label") || null,
    bio: document.querySelector("[data-testid=UserDescription]")?.innerText || "",
    location: hdr?.querySelector("[data-testid=UserLocation]")?.innerText || null,
    website: hdr?.querySelector("[data-testid=UserUrl]")?.innerText || null,
    joined: hdr?.querySelector("[data-testid=UserJoinDate]")?.innerText.replace(/^Joined\s+/, "") || null,
    professional: hdr?.querySelector("[data-testid=UserProfessionalCategory]")?.innerText || null,
    following: num(link("following")?.innerText),
    followers: num((link("verified_followers") || link("followers"))?.innerText),
    affiliation: document.querySelector("[data-testid=UserName] a[href^='/'] img")?.closest("a")?.getAttribute("href") || null,
  };
})()
