// Metadata, description, disclosure signals, chapters and captions of the loaded watch page.
(() => {
  // The player response of the video in the address bar. After an in-page navigation (a clicked result)
  // window.ytInitialPlayerResponse still describes the first video, so the player's own copy is preferred.
  const want = new URLSearchParams(location.search).get("v") || (location.pathname.match(/\/shorts\/([\w-]{11})/) || [])[1];
  const mp = document.querySelector("#movie_player");
  let live = null; try { live = mp?.getPlayerResponse?.() || null; } catch (e) { live = null; }
  const pr = [live, window.ytInitialPlayerResponse].find(p => p?.videoDetails?.videoId && (!want || p.videoDetails.videoId === want));
  if (!pr) return { stale: true, want };
  const vd = pr.videoDetails || {};
  const mf = pr.microformat?.playerMicroformatRenderer || {};
  const desc = vd.shortDescription || "";
  // Creators sometimes run a link straight into the next word (YouTube's copy has no break there);
  // YouTube video links have a fixed 11-character ID, so those can be repaired.
  const fixYt = l => l.replace(/^(https?:\/\/(?:www\.)?(?:youtu\.be\/|youtube\.com\/(?:watch\?v=|shorts\/))[\w-]{11})[\w-]+$/, "$1");
  const links = [...new Set((desc.match(/https?:\/\/[^\s)\]]+/g) || []).map(l => fixYt(l.replace(/[.,!?;:]+$/, ""))))];
  const num = t => { if (!t) return null; const m = String(t).replace(/,/g, "").match(/([\d.]+)\s*([KMB])?/i);
    return m ? Math.round(parseFloat(m[1]) * ({K: 1e3, M: 1e6, B: 1e9}[(m[2] || "").toUpperCase()] || 1)) : null; };
  const AFF = /amzn\.to|amazon\.[a-z.]+\/.*[?&](tag|linkCode)=|geni\.us|sjv\.io|\.pxf\.io|impact\.com|howl\.me|shareasale|skimresources|go\.magik\.ly|7tiv\.net|avantlink|rstyle\.me|linksynergy|awin1\.com|tidd\.ly|prf\.hn|anrdoezrs|dpbolvw|tkqlhce|jdoqocy|kqzyfj|bestbuy\.7tiv|bhpho\.to|\/(affiliates?|aff|partners?\/ref)\/|[?&](aff|affiliate|aff_id|affid|ref_id|associate)=/i;
  const affiliate = links.filter(l => AFF.test(l));
  const campaign = links.filter(l => /[?&]utm_(medium|campaign|source)=[^&]*(kol|influencer|creator|sponsor|partner|launch|ambassador|paid)/i.test(l));
  const short = links.filter(l => /\/\/(bit\.ly|tinyurl\.com|t\.co|ow\.ly|rebrand\.ly|lnk\.to|linktr\.ee)\//i.test(l));
  const paidText = document.querySelector(".ytp-paid-content-overlay-text")?.innerText.trim() || "";
  // YouTube Shopping: products the creator tagged, marked "Earns commission" when they get paid per sale.
  const info = document.querySelector("#description #info, ytd-watch-info-text #info")?.textContent || "";
  const tagged = (info.match(/(\d+)\s+products?\b/i) || [])[1];
  const panel = document.querySelectorAll("ytd-product-list-item-renderer #affiliate-disclaimer-text").length;
  const commission = panel || ([...document.querySelectorAll("a div")].some(d => d.childElementCount === 0 && d.textContent.trim() === "Earns commission") ? 1 : 0);
  const chapters = [...document.querySelectorAll("ytd-macro-markers-list-item-renderer")].map(c => {
    const t = c.innerText.replace(/\s+/g, " ").trim();
    const m = t.match(/^(.*?)\s+(\d{1,2}(?::\d{2}){1,2})(?:\s|$)/);
    if (!m) return { title: t, at: null };
    let title = m[1];
    const half = title.length / 2;  // the title is often rendered twice ("X X")
    if (Number.isInteger(half - 0.5) && title.slice(0, half - 0.5) === title.slice(half + 0.5)) title = title.slice(0, half - 0.5);
    return { title, at: m[2] };
  });
  // Collaboration videos ("A and B") show no subscriber count; the owner data lists each channel's.
  const id0 = window.ytInitialData?.currentVideoEndpoint?.watchEndpoint?.videoId;
  const subsList = [];
  if (id0 === vd.videoId) {
    const walk = (o, depth) => { if (!o || typeof o !== "object" || depth > 40 || subsList.length > 8) return;
      if (o.listItemViewModel) { const t = o.listItemViewModel.title?.content, st = o.listItemViewModel.subtitle?.content || "";
        const m = st.match(/([\d.,]+\s*[KMB]?)\s+subscribers?/i); if (t && m) subsList.push({ channel: t, subscribers: num(m[1]), text: m[0] }); }
      for (const k in o) walk(o[k], depth + 1); };
    try { const c = window.ytInitialData.contents?.twoColumnWatchNextResults?.results?.results?.contents || [];
      for (const x of c) if (x.videoSecondaryInfoRenderer) walk(x.videoSecondaryInfoRenderer.owner, 0); } catch (e) {}
  }
  const subText = document.querySelector("#owner-sub-count")?.textContent.trim() || null;
  const ownerUrl = (mf.ownerProfileUrl || (vd.channelId ? `https://www.youtube.com/channel/${vd.channelId}` : "")).replace(/^http:/, "https:") || null;
  const seenCh = new Set();
  return {
    video_id: vd.videoId, title: vd.title, url: vd.videoId ? `https://www.youtube.com/watch?v=${vd.videoId}` : location.href,
    channel: vd.author,
    channel_url: ownerUrl,
    handle: (ownerUrl || "").match(/\/(@[\w.-]+)/)?.[1] || null,
    subscribers: num(subText) ?? (subsList[0]?.subscribers ?? null),
    subscribers_text: subText || subsList[0]?.text || null,
    ...(subsList.length > 1 ? { collaborators: subsList } : {}),
    views: vd.viewCount ? +vd.viewCount : null,
    likes: num(document.querySelector("like-button-view-model button, #segmented-like-button button")?.getAttribute("aria-label")?.match(/[\d,.]+\s*[KMB]?/i)?.[0]),
    published: mf.publishDate || null, length_s: vd.lengthSeconds ? +vd.lengthSeconds : null,
    category: mf.category || null, is_live: !!vd.isLiveContent, keywords: (vd.keywords || []).slice(0, 15),
    captions: (pr.captions?.playerCaptionsTracklistRenderer?.captionTracks || []).map(c => c.languageCode + (c.kind === "asr" ? " (auto)" : "")),
    chapters: chapters.filter(c => !seenCh.has(c.title + c.at) && seenCh.add(c.title + c.at)),
    disclosure: { paid_promotion_label: !!paidText, paid_promotion_text: paidText || null,
                  affiliate_links: affiliate, campaign_links: campaign, short_links: short,
                  tagged_products: tagged ? +tagged : 0, products_earning_commission: tagged ? Math.min(commission, +tagged) : commission },
    description: desc,
    description_links: links,
  };
})()
