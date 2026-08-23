"use strict";
const $ = s => document.querySelector(s);
let MODE = localStorage.getItem("saf_mode") || "auto";

function toast(msg) {
    let t = document.getElementById("__toast");
    if (!t) {
        t = document.createElement("div");
        t.id = "__toast";
        t.style.cssText = "position:fixed;bottom:20px;right:20px;background:var(--navy);color:var(--ivory);padding:10px 16px;border-radius:8px;font:500 12px Inter;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,.3);transition:opacity .3s;opacity:0;pointer-events:none;max-width:400px;";
        document.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.opacity = "1";
    clearTimeout(t._tm);
    t._tm = setTimeout(() => { t.style.opacity = "0"; }, 3000);
}

function api(path, opts = {}, timeoutMs = 60000) {
    const cleanPath = path.split("?")[0];
    toast(`📡 ${cleanPath} (mode: ${MODE})`);
    const sep = path.includes("?") ? "&" : "?";
    const url = path + sep + "mode=" + MODE;
    const ctrl = new AbortController();
    const tm = setTimeout(() => ctrl.abort(), timeoutMs);
    return fetch(url, { ...opts, signal: ctrl.signal })
        .then(r => {
            clearTimeout(tm);
            if (!r.ok) return r.text().then(t => { throw new Error("HTTP " + r.status + ": " + t.slice(0, 120)); });
            return r.json();
        })
        .catch(e => {
            clearTimeout(tm);
            if (e.name === "AbortError") throw new Error("Request timed out — try ⚡ Instant mode or wait longer");
            throw e;
        });
}

function fmtPct(v) {
    if (v == null || isNaN(v)) return '<span class="dim">—</span>';
    return v >= 0 ? `<span class="pos">▲ +${v.toFixed(2)}%</span>` : `<span class="neg">▼ ${v.toFixed(2)}%</span>`;
}
function fmtMC(mc) {
    if (mc == null) return "—";
    if (mc >= 1e12) return "$" + (mc / 1e12).toFixed(2) + "T";
    if (mc >= 1e9) return "$" + (mc / 1e9).toFixed(2) + "B";
    if (mc >= 1e6) return "$" + (mc / 1e6).toFixed(1) + "M";
    return "$" + Math.round(mc).toLocaleString();
}
function formatFlags(flags) {
    if (Array.isArray(flags)) return flags.length ? flags.join(", ") : "none";
    if (typeof flags === "string") {
        if (!flags || flags === "[]") return "none";
        try {
            const parsed = JSON.parse(flags);
            if (Array.isArray(parsed)) return parsed.length ? parsed.join(", ") : "none";
        } catch (e) {}
        return flags;
    }
    return "none";
}

function renderModeToggle() {
    let wrap = $("#modeToggle");
    if (!wrap) { wrap = document.createElement("div"); wrap.id = "modeToggle"; document.querySelector("header").appendChild(wrap); }
    wrap.innerHTML = "";
    [["auto", "🤖 Auto"], ["instant", "⚡ Instant"], ["deep", "🧠 Deep"]].forEach(([m, label]) => {
        const b = document.createElement("button");
        b.textContent = label;
        b.className = "modebtn" + (MODE === m ? " on" : "");
        b.title = m === "auto" ? "Deep tools → Ox Alpha, quick tools → Groq"
                : m === "instant" ? "Everything → Groq (fast)"
                : "Everything → Ox Alpha (deep)";
        b.onclick = () => { MODE = m; localStorage.setItem("saf_mode", m); renderModeToggle(); toast(`Mode switched to ${label}`); };
        wrap.appendChild(b);
    });
}

function openModal(title, html) {
    $("#mtitle").innerHTML = title;
    $("#mbody").innerHTML = html;
    $("#mbg").style.display = "flex";
    window.scrollTo(0, 0);
}
function closeModal() { $("#mbg").style.display = "none"; }

document.addEventListener("click", e => {
    if (e.target.closest("[data-close]") || e.target.id === "mbg") { closeModal(); return; }
    const tk = e.target.closest("[data-ticker]"); if (tk) { openTicker(tk.dataset.ticker); return; }
    const bk = e.target.closest("[data-basket]"); if (bk) { openBasket(bk.dataset.basket); return; }
});

function tvSymbol(t) {
    const S = {".DE":"XETRA:",".ST":"STO:",".AS":"AMS:",".PA":"EPA:",".L":"LSE:",".OL":"OSE:",".VI":"VIE:",".SW":"SWX:",".T":"TSE:"};
    for (const s in S) if (t.endsWith(s)) return S[s] + t.slice(0, -s.length);
    if (["UMICY","SMSMY","YARIY","AMSYF","SDVKY","FLIDY","GLNCY"].includes(t) || /^[A-Z]{4,5}Y$/.test(t)) return "OTC:" + t;
    return t;
}
function tvChart(t, h) {
    const sym = tvSymbol(t);
    return `<iframe loading="lazy" title="TradingView ${t}"
        src="https://s.tradingview.com/widgetembed/?frameElementId=tv_${t.replace(/\W/g,"")}&symbol=${encodeURIComponent(sym)}&interval=D&hidesidetoolbar=0&hidetoptoolbar=0&symboledit=1&style=1&theme=light&locale=en&withdateranges=1&allow_symbol_change=1&save_image=0"
        style="width:100%;height:${h||420}px;border:none;border-radius:10px;background:#fff"></iframe>`;
}

/* ═══ ADD TO BASKET MODAL ═══ */
async function openAddToBasket(ticker) {
    openModal(`➕ Add ${ticker} to SAF`, `<div class="sub"><span class="loader"></span> loading baskets…</div>`);
    try {
        const d = await api("/api/baskets/names");
        const names = d.names || [];
        let options = names.map(n => `<option value="${n}">${n}</option>`).join("");
        $("#mbody").innerHTML = `
        <div class="kv" style="margin-bottom:14px">
            <b>Ticker</b><span style="font-weight:700;color:var(--navy)">${ticker}</span>
        </div>
        <div class="kv">
            <b>Basket</b>
            <span><select id="addBasket" style="width:280px">${options}
                <option value="__new__">➕ Create new basket…</option></select></span>
            <b>New basket name</b>
            <span><input id="newBasketName" placeholder="e.g. 🆕 MY THESIS" style="width:280px;display:none"></span>
            <b>Weight</b>
            <span><select id="addWeight" style="width:120px">
                <option value="0.5">0.5</option>
                <option value="1.0" selected>1.0</option>
                <option value="1.5">1.5</option>
                <option value="2.0">2.0</option>
                <option value="2.5">2.5</option>
                <option value="3.0">3.0</option>
            </select></span>
        </div>
        <div id="addResult" style="margin-top:12px"></div>
        <button class="btn gold" id="confirmAdd" style="margin-top:12px">✅ Add to SAF</button>`;
        $("#addBasket").onchange = () => {
            $("#newBasketName").style.display = $("#addBasket").value === "__new__" ? "inline-block" : "none";
        };
        $("#confirmAdd").onclick = async () => {
            let basket = $("#addBasket").value;
            if (basket === "__new__") {
                basket = $("#newBasketName").value.trim();
                if (!basket) { $("#addResult").innerHTML = `<span style="color:var(--neg)">Enter a basket name.</span>`; return; }
            }
            const weight = parseFloat($("#addWeight").value);
            $("#addResult").innerHTML = `<span class="loader"></span> adding…`;
            try {
                await api(`/api/basket/${encodeURIComponent(basket)}/add?ticker=${encodeURIComponent(ticker)}&weight=${weight}`, { method: "POST" });
                $("#addResult").innerHTML = `<span style="color:var(--pos)">✅ ${ticker} added to "${basket}" (weight ${weight})</span>`;
            } catch (e) {
                $("#addResult").innerHTML = `<span style="color:var(--neg)">Error: ${e.message}</span>`;
            }
        };
    } catch (e) {
        $("#mbody").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`;
    }
}

async function openBasket(name) {
    openModal("🧺 " + name, `<div class="sub"><span class="loader"></span> opening basket…</div>`);
    try {
        const d = await api("/api/basket/" + encodeURIComponent(name));
        let rows = "";
        for (const h of d.holdings) {
            rows += `<tr class="clickable" data-ticker="${h.ticker}">
                <td><b>${h.ticker}</b></td>
                <td class="r">${h.weight.toFixed(1)}</td>
                <td class="r">${h.price != null ? "$" + Number(h.price).toFixed(2) : "—"}</td>
                <td class="r">${fmtPct(h.ytd_pct)}</td></tr>`;
        }
        $("#mbody").innerHTML = `<div class="tbl"><table>
            <tr><th>Ticker</th><th class="r">Weight</th><th class="r">Price</th><th class="r">YTD</th></tr>
            ${rows}</table></div>`;
    } catch (e) { $("#mbody").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
}

/* ═══ TICKER DOSSIER (with memo, baskets, 3 news, 3 polymarket) ═══ */
async function openTicker(t) {
    openModal("🔍 " + t + " — Dossier", `<div class="sub"><span class="loader"></span> compiling dossier…</div>`);
    try {
        const [d, memo, rub] = await Promise.all([
            api("/api/ticker/" + encodeURIComponent(t)),
            api("/api/ticker/" + encodeURIComponent(t) + "/memo").catch(() => ({ memo: null })),
            api("/api/ticker/" + encodeURIComponent(t) + "/rubric").catch(() => ({ ok: false }))
        ]);
        const sc = d.score_v2_core;
        const f = d.fundamentals || {};
        const q = d.quality || {};
        const m = memo.memo || {};
        const inBaskets = d.in_baskets || m.in_baskets || [];

        // Memo / "what is this company" block
        const memoHtml = m.business_summary
            ? `<div class="panel"><h3>📋 Company Memo — ${m.name || t}</h3>
                <div class="sub">${m.sector || "—"} · ${m.industry || "—"} · Market Cap ${fmtMC(m.market_cap)}</div>
                <p style="font-size:13px;line-height:1.6;color:var(--ink)">${m.business_summary}</p>
                ${m.bottleneck ? `<div style="margin-top:8px">${m.bottleneck.is_bottleneck
                    ? `<span class="sig buy">🏆 TRUE BOTTLENECK (${m.bottleneck.rubric_total}/30)</span>`
                    : `<span class="sig none">Bottleneck score ${m.bottleneck.rubric_total}/30 (below 22)</span>`}</div>` : ""}
               </div>`
            : `<div class="panel"><h3>📋 Company Memo</h3><div class="dim">No business summary available.</div></div>`;

        // Basket membership block
        const basketHtml = inBaskets.length
            ? `<div class="panel"><h3>🧺 In SAF Baskets</h3>${inBaskets.map(b => `<span class="chip" data-basket="${b}">${b}</span>`).join("")}</div>`
            : `<div class="panel"><h3>🧺 In SAF Baskets</h3><div class="dim">Not in any basket yet.</div>
               <button class="btn ghost addBtn" data-add="${t}" style="margin-top:8px">➕ Add to SAF</button></div>`;

        $("#mbody").innerHTML = `
        <div class="row" style="margin-bottom:10px">
            <span class="chip">$${d.price}</span>
            <span class="chip">Shadow Alpha <b>${sc ? sc.total : "—"}/100</b></span>
            <span class="chip">${sc ? sc.verdict : "—"}</span>
        </div>
        ${memoHtml}
        ${basketHtml}
        <div class="panel" style="margin:10px 0"><h3>📈 TradingView Chart</h3>${tvChart(t, 420)}</div>
        <div class="grid2" style="gap:12px">
            <div class="panel"><h3>Quality</h3><div class="kv">
                <b>Bars</b><span>${q.bars || 0}</span>
                <b>Usable</b><span>${q.usable ? "✅" : "❌"}</span>
                <b>Flags</b><span>${formatFlags(q.flags)}</span></div></div>
            <div class="panel"><h3>Fundamentals</h3><div class="kv">
                <b>Sector</b><span>${f.sector || m.sector || "—"}</span>
                <b>Market cap</b><span>${fmtMC(f.marketCap || m.market_cap)}</span>
                <b>Gross margin</b><span>${f.grossMargins ? (f.grossMargins*100).toFixed(1)+"%" : (m.key_metrics && m.key_metrics.gross_margin ? (m.key_metrics.gross_margin*100).toFixed(1)+"%" : "—")}</span></div></div>
        </div>
        ${sc && sc.components ? `<div class="panel"><h3>Score v2 Breakdown</h3><div class="kv">
            <b>Trend</b><span>${(sc.components.trend||0).toFixed(1)}/25</span>
            <b>α-Indep</b><span>${(sc.components.alpha_indep||0).toFixed(1)}/30</span>
            <b>RelStr</b><span>${(sc.components.rel_strength||0).toFixed(1)}/20</span>
            <b>Quality</b><span>${(sc.components.quality||0).toFixed(1)}/15</span>
            <b>Bottleneck</b><span>${(sc.components.bottleneck_prior||0).toFixed(1)}/10</span></div></div>` : ""}
        ${rub.ok && rub.rubric ? `<div class="panel"><h3>Grounded Rubric (${rub.cached ? "cached" : "live"})</h3>
            <div class="kv"><b>Total</b><span><b>${rub.rubric.total}/30</b></span></div></div>` : ""}
        <div class="panel"><h3>📰 Latest News (top 3)</h3><div id="mnews"><span class="loader"></span> fetching…</div></div>
        <div class="panel"><h3>🎲 Polymarket (top 3)</h3><div id="mpm"><span class="loader"></span> fetching…</div></div>
        <div class="row">
            <button class="btn gold" id="mrun">▶ Run 5-Stage Pipeline</button>
            <button class="btn ghost addBtn" data-add="${t}" style="margin-left:8px">➕ Add to SAF</button>
        </div>
        <div id="mpipe"></div>`;

        // Wire add buttons
        document.querySelectorAll("#mbody .addBtn").forEach(b => b.onclick = (e) => {
            e.stopPropagation();
            openAddToBasket(b.dataset.add);
        });

        // 3 most recent news
        api("/api/news?q=" + encodeURIComponent(t) + "&limit=3").then(hs => {
            const el = $("#mnews");
            const items = (Array.isArray(hs.items) ? hs.items : []).slice(0, 3);
            if (el) el.innerHTML = items.length
                ? items.map(h => `<div style="margin:5px 0">${h.link ? `<a class="nl" href="${h.link}" target="_blank" rel="noopener">${h.title} ↗</a>` : `<b>${h.title}</b>`} <span class="dim">— ${h.source}</span></div>`).join("")
                : "<span class='dim'>No headlines.</span>";
        }).catch(() => { const el = $("#mnews"); if (el) el.innerHTML = "<span class='dim'>News unavailable.</span>"; });

        // 3 polymarket markets
        api("/api/ticker/" + encodeURIComponent(t) + "/polymarket?limit=3").then(pm => {
            const el = $("#mpm");
            const items = (Array.isArray(pm.items) ? pm.items : []).slice(0, 3);
            if (el) el.innerHTML = items.length
                ? items.map(mk => `<div style="margin:6px 0">
                    ${mk.link ? `<a class="nl" href="${mk.link}" target="_blank" rel="noopener">${mk.question} ↗</a>` : `<b>${mk.question}</b>`}
                    <div class="bar" style="margin:4px 0"><i style="width:${Math.round((mk.yes ?? 0.5) * 100)}%"></i></div>
                    <div class="dim">YES ${mk.yes != null ? Math.round(mk.yes*100) + "%" : "—"}</div></div>`).join("")
                : "<span class='dim'>No related markets.</span>";
        }).catch(() => { const el = $("#mpm"); if (el) el.innerHTML = "<span class='dim'>Polymarket unavailable.</span>"; });

        // Pipeline runner
        $("#mrun").onclick = async () => {
            $("#mrun").disabled = true;
            const st = Date.now();
            $("#mpipe").innerHTML = `<div class="panel"><span class="loader"></span> Pipeline running… <span id="elapsed">0s</span></div>`;
            const timer = setInterval(() => { const el = document.getElementById("elapsed"); if (el) el.textContent = Math.floor((Date.now()-st)/1000) + "s"; }, 1000);
            try {
                const res = await api("/api/pipeline/" + encodeURIComponent(t), { method: "POST" }, 300000);
                clearInterval(timer);
                renderPipelineStages("mpipe", res.state);
                const a = res.state.trader.action;
                const col = a === "BUY" ? "buy" : a === "SELL" ? "sell" : "hold";
                $("#mpipe").insertAdjacentHTML("beforeend", `<div class="panel ${col}"><h3>FINAL SIGNAL: ${a}</h3><div class="sub">${res.state.trader.rationale || ""}</div></div>`);
            } catch (e) { clearInterval(timer); $("#mpipe").innerHTML = `<div class="panel sell">Pipeline error: ${e.message}</div>`; }
            $("#mrun").disabled = false;
        };
    } catch (e) { $("#mbody").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
}

function renderPipelineStages(containerId, state) {
    const c = document.getElementById(containerId);
    if (!c || !state) return;
    const stages = [
        { n: 1, title: "I. ANALYST TEAM", txt: Object.entries(state.analysts || {}).map(([k,v]) => `— ${k.toUpperCase()} —\n${v}`).join("\n\n") },
        { n: 2, title: "II. BULL vs BEAR + JUDGE", txt: `[BULL]\n${state.bull}\n\n[BEAR]\n${state.bear}\n\nVERDICT: ${state.verdict.winner} (conf ${state.verdict.confidence})\n${state.verdict.rationale}` },
        { n: 3, title: "III. TRADER", txt: JSON.stringify(state.trader, null, 1) },
        { n: 4, title: "IV. MATH ENGINE", txt: state.trade ? JSON.stringify(state.trade, null, 1) : "HOLD — no position sized." },
        { n: 5, title: "V. VERDICT", txt: `Direction: ${state.trader.action}\nPosition opened: ${state.position_opened}` }
    ];
    c.innerHTML = stages.map(s => `<div class="card stage"><div class="n">${s.n}</div><div class="b"><h3>${s.title}</h3><div class="txt"><pre class="memo">${s.txt}</pre></div></div></div>`).join("");
}

const state = { tab: "dash" };
$("#nav").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b) return;
    state.tab = b.dataset.tab;
    document.querySelectorAll("#nav button").forEach(x => x.classList.toggle("on", x === b));
    render();
});
function render() {
    ({ dash: renderDash, news: renderNews, screener: renderScreener, agents: renderAgents,
       positions: renderPositions, memory: renderMemory, diag: renderDiag })[state.tab]();
}

async function renderDash() {
    $("#main").innerHTML = `<div class="wrap"><div class="card"><h1>🦅 Skia Alpha Fund — Dashboard</h1>
        <div class="sub" id="dsub">loading…</div>
        <div class="row"><input id="q" placeholder="🔍 Search ticker…" style="width:220px"></div></div>
        <div id="sres"></div>
        <div class="grid2"><div class="card"><h2>📈 All Baskets Performance</h2><div class="tbl" id="dt"></div></div>
        <div class="card"><h2>🏆 YTD Ranking</h2><div class="tbl" id="rt"></div></div></div></div>`;
    try {
        const d = await api("/api/baskets");
        $("#dsub").textContent = new Date().toLocaleString() + " · " + d.baskets.length + " baskets · mode: " + MODE;
        const bySection = {};
        d.baskets.forEach(b => { (bySection[b.section] = bySection[b.section] || []).push(b); });
        let html = `<table><tr><th>Basket</th><th class="r">1D</th><th class="r">1W</th><th class="r">1M</th><th class="r">YTD</th></tr>`;
        const perf = [];
        for (const sec in bySection) {
            html += `<tr class="secrow"><td colspan="5">── ${sec} ──</td></tr>`;
            for (const b of bySection[sec]) {
                html += `<tr class="clickable" data-basket="${b.name}"><td>${b.name}</td>
                    <td class="r">${fmtPct(b.returns_pct["1d"])}</td><td class="r">${fmtPct(b.returns_pct["1w"])}</td>
                    <td class="r">${fmtPct(b.returns_pct["1m"])}</td><td class="r">${fmtPct(b.returns_pct["ytd"])}</td></tr>`;
                if (b.returns_pct.ytd != null) perf.push([b.name, b.returns_pct.ytd]);
            }
        }
        $("#dt").innerHTML = html + "</table>";
        perf.sort((a, b) => b[1] - a[1]);
        $("#rt").innerHTML = `<table><tr><th class="c">#</th><th>Basket</th><th class="r">YTD</th></tr>` +
            perf.map((p, i) => `<tr class="clickable" data-basket="${p[0]}"><td class="c">${i+1}</td><td>${p[0]}</td><td class="r">${fmtPct(p[1])}</td></tr>`).join("") + "</table>";
        $("#q").oninput = async e => {
            const q = e.target.value.toUpperCase().trim();
            const sres = $("#sres");
            if (!q) { sres.innerHTML = ""; return; }
            sres.innerHTML = `<div class="card"><span class="loader"></span> searching…</div>`;
            try {
                const td = await api("/api/ticker/" + encodeURIComponent(q));
                sres.innerHTML = `<div class="card"><h2>✅ ${q}</h2><div class="kv"><b>Price</b><span>$${td.price}</span>
                    <b>Score</b><span>${td.score_v2_core ? td.score_v2_core.total : "—"}/100</span></div>
                    <button class="btn gold" data-ticker="${q}" style="margin-top:10px">Open Dossier</button></div>`;
            } catch (err) { sres.innerHTML = `<div class="card">❌ ${q} — ${err.message}</div>`; }
        };
    } catch (e) { $("#dt").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
}

async function renderNews() {
    $("#main").innerHTML = `<div class="wrap"><div class="card"><h1>📰 Shadow Alpha News Wire</h1>
        <div class="row"><input id="nq" value="NVDA" style="width:260px"><button class="btn gold" id="ngo">Load</button></div>
        <div id="nwire" style="margin-top:14px"></div></div></div>`;
    const load = async () => {
        const q = $("#nq").value.trim(); if (!q) return;
        $("#nwire").innerHTML = '<div class="panel"><span class="loader"></span> fetching…</div>';
        try {
            const hs = await api("/api/news?q=" + encodeURIComponent(q) + "&limit=10");
            const items = Array.isArray(hs.items) ? hs.items : [];
            $("#nwire").innerHTML = items.length
                ? items.map(h => `<div class="panel">${h.link ? `<a class="nl" href="${h.link}" target="_blank" rel="noopener">${h.title} ↗</a>` : `<b>${h.title}</b>`}
                    <div class="dim">${h.source} · relevance ${h.relevance} ${(Array.isArray(h.keywords)?h.keywords:[]).map(k=>`<span class="chip">${k}</span>`).join("")}</div></div>`).join("")
                : `<div class="panel sell">No headlines.</div>`;
        } catch (e) { $("#nwire").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
    };
    $("#ngo").onclick = load;
    $("#nq").addEventListener("keydown", e => { if (e.key === "Enter") load(); });
    load();
}

function renderScreener() {
    $("#main").innerHTML = `<div class="wrap"><div class="card"><h1>🔍 SAF Screener + AI</h1>
        <div class="sub">mode: ${MODE} — find tickers, then ➕ Add to SAF</div></div>
        <div class="grid-tools"><div class="card tools">
            <button data-t="screen" class="on">📡 Full Screen</button>
            <button data-t="deep">🔍 Deep Analysis</button>
            <button data-t="bot">🧮 Bottleneck Scoring</button>
            <button data-t="uni">📋 Universe</button>
            <button data-t="sup">🤖 AI Supply Chain</button>
            <button data-t="abot">🤖 AI Bottleneck</button>
            <button data-t="adeep">🤖 AI Deep Report</button>
            <button data-t="pm">🎲 Polymarket</button>
        </div><div class="card" id="out"><div class="dim">Select a tool…</div></div></div></div>`;
    document.querySelectorAll(".tools button").forEach(b => b.onclick = () => {
        document.querySelectorAll(".tools button").forEach(x => x.classList.toggle("on", x === b));
        SCREENER[b.dataset.t]();
    });
    SCREENER.screen();
}

const SCREENER = {
    async screen() {
        const out = $("#out");
        out.innerHTML = `<h2>📡 Full Universe Scan</h2><div class="sub" id="prog"><span class="loader"></span> scoring…</div><div id="res"></div>`;
        try {
            const d = await api("/api/screen?top=50");
            const top = Array.isArray(d.top) ? d.top : [];
            const cand = top.filter(r => r.total >= 60).length;
            const watch = top.filter(r => r.total >= 45 && r.total < 60).length;
            $("#prog").textContent = `🎯 ${cand} CANDIDATES · 👀 ${watch} WATCH · ${d.n_scored} scored`;
            let html = `<div class="tbl"><table><tr><th>#</th><th>Ticker</th><th class="r">Total</th><th class="r">Trend</th><th class="r">α-Indep</th><th class="r">Quality</th><th class="c">Verdict</th><th class="c">Action</th></tr>`;
            top.forEach((r, i) => {
                const c = r.components || {};
                html += `<tr class="clickable" data-ticker="${r.ticker}"><td>${i+1}</td><td><b>${r.ticker}</b></td>
                    <td class="r"><b>${r.total.toFixed(1)}</b></td><td class="r">${(c.trend||0).toFixed(0)}</td>
                    <td class="r">${(c.alpha_indep||0).toFixed(0)}</td><td class="r">${(c.quality||0).toFixed(0)}</td>
                    <td class="c"><span class="sig ${r.verdict.toLowerCase()}">${r.verdict}</span></td>
                    <td class="c"><button class="btn ghost addBtn" data-add="${r.ticker}" style="padding:4px 10px;font-size:11px">➕ Add</button></td></tr>`;
            });
            $("#res").innerHTML = html + "</table></div>";
            document.querySelectorAll(".addBtn").forEach(b => b.onclick = (e) => {
                e.stopPropagation();
                openAddToBasket(b.dataset.add);
            });
        } catch (e) { $("#res").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
    },
    async deep() {
        const out = $("#out");
        out.innerHTML = `<h2>🔍 Deep Analysis</h2><div class="row"><input id="tk" value="MKSI" style="width:110px"><button class="btn" id="go">Run</button></div><div id="dr"></div>`;
        $("#go").onclick = async () => {
            const t = $("#tk").value.toUpperCase().trim();
            $("#dr").innerHTML = `<div class="panel"><span class="loader"></span> analyzing…</div>`;
            try {
                const d = await api("/api/screener/deep/" + encodeURIComponent(t));
                const inv = d.investability || {};
                const s = d.score_v2;
                $("#dr").innerHTML = `<h3>Investability</h3><div class="panel"><div class="kv">
                    <b>Market Cap > $500M</b><span>${fmtMC(inv.market_cap)} ${(inv.checks||{}).market_cap_ok ? "✅" : "❌"}</span>
                    <b>Price > $5</b><span>$${Number(inv.price||0).toFixed(2)} ${(inv.checks||{}).price_ok ? "✅" : "❌"}</span>
                    <b>Avg Vol > 100K</b><span>${Number(inv.avg_volume||0).toLocaleString()} ${(inv.checks||{}).volume_ok ? "✅" : "❌"}</span>
                    <b>Overall</b><span><b>${inv.status || "—"}</b> (${inv.passed || 0}/3)</span></div></div>
                    ${s ? `<h3>Score v2</h3><div class="panel"><div class="kv"><b>Total</b><span><b>${s.total}/100</b></span><b>Verdict</b><span>${s.verdict}</span></div></div>` : ""}
                    ${(Array.isArray(d.in_baskets)&&d.in_baskets.length) ? `<div class="panel"><h3>In SAF Baskets</h3>${d.in_baskets.map(b=>`<span class="chip" data-basket="${b}">${b}</span>`).join("")}</div>` : `<div class="panel">✅ NOT yet in SAF → potential new addition</div>`}
                    <div class="row" style="margin-top:8px">
                        <button class="btn gold" data-ticker="${t}">Open Full Dossier</button>
                        <button class="btn ghost addBtn" data-add="${t}" style="margin-left:8px">➕ Add to SAF</button>
                    </div>`;
                document.querySelectorAll(".addBtn").forEach(b => b.onclick = (e) => {
                    e.stopPropagation();
                    openAddToBasket(b.dataset.add);
                });
            } catch (e) { $("#dr").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
        };
    },
    async bot() {
        const out = $("#out");
        out.innerHTML = `<h2>🧮 Bottleneck Scoring</h2><div class="row"><input id="tk" value="MKSI" style="width:110px"><button class="btn" id="go">Score</button></div><div id="ai"></div>`;
        $("#go").onclick = async () => {
            const t = $("#tk").value.toUpperCase().trim();
            $("#ai").innerHTML = `<div class="panel"><span class="loader"></span> AI scoring…</div>`;
            try {
                const r = await api("/api/screener/bottleneck-ai/" + encodeURIComponent(t), { method: "POST" }, 120000);
                const res = r.rubric || {};
                let html = `<table><tr><th>Criterion</th><th class="c">Score</th></tr>`;
                Object.entries(res.scores || {}).forEach(([k, v]) => {
                    html += `<tr><td>${k.replace(/_/g," ")}</td><td class="c" style="color:${v>=4?"var(--pos)":v>=3?"var(--hold)":"var(--neg)"};font-weight:700">${v}/5</td></tr>`;
                });
                html += `<tr><td><b>TOTAL</b></td><td class="c"><b>${res.total}/30</b></td></tr></table>`;
                html += `<div class="panel ${res.total>=22?"buy":"sell"}" style="margin-top:8px"><h3>${res.total>=22?"🏆 TRUE BOTTLENECK":"❌ BELOW THRESHOLD"}</h3></div>`;
                html += `<button class="btn ghost addBtn" data-add="${t}" style="margin-top:8px">➕ Add ${t} to SAF</button>`;
                $("#ai").innerHTML = html;
                document.querySelectorAll(".addBtn").forEach(b => b.onclick = (e) => {
                    e.stopPropagation();
                    openAddToBasket(b.dataset.add);
                });
            } catch (e) { $("#ai").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
        };
    },
    async uni() {
        const out = $("#out");
        try {
            const d = await api("/api/screener/universe");
            let html = `<h2>🌍 Universe (${d.total_tickers} tickers)</h2>`;
            for (const s in d.universe) {
                html += `<div class="panel"><h3>${s} (${d.universe[s].length})</h3>` +
                    d.universe[s].map(t => `<span class="chip" data-ticker="${t}">${t}</span>`).join("") + `</div>`;
            }
            out.innerHTML = html;
        } catch (e) { out.innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
    },
    async sup() {
        const out = $("#out");
        out.innerHTML = `<h2>🤖 AI Supply Chain Discovery</h2>
            <div class="sub">Uses ${MODE==="instant"?"⚡ Groq (fast)":"🧠 Ox Alpha (deep)"} — switch mode above</div>
            <div class="row"><input id="tr" style="flex:1" value="solid-state batteries for electric vehicles"><button class="btn gold" id="go">Map</button></div>
            <div id="r" style="margin-top:10px"></div>`;
        $("#go").onclick = async () => {
            const trend = $("#tr").value.trim(); if (!trend) return;
            const st = Date.now();
            $("#r").innerHTML = `<div class="panel"><span class="loader"></span> Mapping supply chain… <span id="sc_elapsed">0s</span></div>`;
            const timer = setInterval(() => { const el = document.getElementById("sc_elapsed"); if (el) el.textContent = Math.floor((Date.now()-st)/1000)+"s"; }, 1000);
            try {
                const d = await api("/api/screener/supply-chain", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({trend}) }, 600000);
                clearInterval(timer);
                const res = d.result || {};
                let html = `<div class="panel buy"><h3>🎯 Top Pick: ${res.top_pick || "—"}</h3><div class="sub">${res.thesis_summary || ""}</div></div>`;
                html += `<div class="panel"><h3>Supply Chain Map</h3>`;
                (Array.isArray(res.supply_chain)?res.supply_chain:[]).forEach((layer, i) => { html += `<div style="padding-left:${i*18}px">└─ ${layer}</div>`; });
                html += `</div>`;
                (Array.isArray(res.bottlenecks)?res.bottlenecks:[]).forEach((b, i) => {
                    html += `<div class="panel"><h3>Bottleneck #${i+1}: ${b.name || "?"}</h3>
                        <div class="kv"><b>Why</b><span>${b.why_bottleneck || "—"}</span>
                        <b>Concentration</b><span>${b.market_concentration || "—"}</span>
                        <b>Substitutability</b><span>${b.substitutability || "—"}</span></div>
                        <div style="margin-top:6px">Tickers: ${(Array.isArray(b.tickers)?b.tickers:[]).map(t=>`<span class="chip" data-ticker="${t}">${t}</span> <button class="btn ghost addBtn" data-add="${t}" style="padding:2px 8px;font-size:10px;margin-left:4px">➕</button>`).join("")}</div></div>`;
                });
                $("#r").innerHTML = html;
                document.querySelectorAll(".addBtn").forEach(b => b.onclick = (e) => {
                    e.stopPropagation();
                    openAddToBasket(b.dataset.add);
                });
            } catch (e) { clearInterval(timer); $("#r").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
        };
    },
    async abot() { SCREENER.bot(); },
    async adeep() {
        const out = $("#out");
        out.innerHTML = `<h2>🤖 AI Deep Report</h2><div class="row"><input id="tk" value="MKSI" style="width:110px"><button class="btn gold" id="go">Generate</button></div><div id="r"></div>`;
        $("#go").onclick = async () => {
            const t = $("#tk").value.toUpperCase().trim();
            $("#r").innerHTML = `<div class="panel"><span class="loader"></span> writing memo…</div>`;
            try {
                const d = await api("/api/screener/deep-report/" + encodeURIComponent(t), {}, 300000);
                const col = d.action === "BUY" ? "buy" : d.action === "SELL" ? "sell" : "hold";
                $("#r").innerHTML = `<div class="panel ${col}"><h3>FINAL: ${d.action}${d.confidence != null ? " · conf " + d.confidence : ""}</h3><pre class="memo">${d.memo}</pre></div>
                <button class="btn ghost addBtn" data-add="${t}" style="margin-top:8px">➕ Add ${t} to SAF</button>`;
                document.querySelectorAll(".addBtn").forEach(b => b.onclick = (e) => {
                    e.stopPropagation();
                    openAddToBasket(b.dataset.add);
                });
            } catch (e) { $("#r").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
        };
    },
    async pm() {
        const out = $("#out");
        out.innerHTML = `<h2>🎲 Polymarket Intel</h2>
            <div class="row"><input id="pmq" value="fed rate cut" style="width:200px">
            <button class="btn" id="pmgo">Search</button><button class="btn gold" id="pmtop">🔥 24h Popular</button></div>
            <div id="pmr" style="margin-top:10px"></div>`;
        const renderMarkets = items => {
            items = Array.isArray(items) ? items : [];
            if (!items.length) { $("#pmr").innerHTML = `<div class="panel sell">No markets found.</div>`; return; }
            $("#pmr").innerHTML = items.map(m => `
                <div class="panel">
                    ${m.link ? `<a class="nl" href="${m.link}" target="_blank" rel="noopener" style="font-weight:600">${m.question} ↗</a>` : `<b>${m.question}</b>`}
                    <div class="bar" style="margin:6px 0"><i style="width:${Math.round((m.yes ?? 0.5) * 100)}%"></i></div>
                    <div class="sub">YES ${m.yes != null ? Math.round(m.yes*100) + "%" : "—"} · 24h vol $${Math.round((m.volume24hr||0)/1000)}K ${m.endDate ? " · ends " + m.endDate : ""}</div>
                </div>`).join("");
        };
        const load = async (q, sort) => {
            $("#pmr").innerHTML = '<div class="panel"><span class="loader"></span> querying Polymarket…</div>';
            try {
                const d = await api("/api/polymarket?q=" + encodeURIComponent(q || "fed rate cut") + "&limit=10&sort=" + (sort || "relevance"));
                renderMarkets(d.items);
            } catch (e) { $("#pmr").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
        };
        $("#pmgo").onclick = () => load($("#pmq").value.trim(), "relevance");
        $("#pmtop").onclick = () => load($("#pmq").value.trim(), "volume24hr");
        load(null, "volume24hr");
    }
};

async function renderAgents() {
    $("#main").innerHTML = `<div class="wrap"><div class="card"><h1>⚛️ TradingAgents — 5-Stage Firm Simulation</h1>
        <div class="sub">mode: ${MODE} — ${MODE==="instant"?"⚡ Groq fast":"🧠 Ox Alpha deep"}</div>
        <div class="row"><input id="tk" value="MKSI" style="width:110px"><button class="btn gold" id="go">▶ Run Pipeline</button></div></div>
        <div id="tl"></div></div>`;
    $("#go").onclick = async () => {
        const t = $("#tk").value.toUpperCase().trim();
        const st = Date.now();
        $("#tl").innerHTML = `<div class="panel"><span class="loader"></span> Pipeline running… <span id="elapsed">0s</span></div>`;
        const timer = setInterval(() => { const el = document.getElementById("elapsed"); if (el) el.textContent = Math.floor((Date.now()-st)/1000)+"s"; }, 1000);
        try {
            const res = await api("/api/pipeline/" + encodeURIComponent(t), { method: "POST" }, 300000);
            clearInterval(timer);
            $("#tl").innerHTML = "";
            renderPipelineStages("tl", res.state);
            const a = res.state.trader.action;
            const col = a === "BUY" ? "buy" : a === "SELL" ? "sell" : "hold";
            $("#tl").insertAdjacentHTML("beforeend", `<div class="panel ${col}"><h3>FINAL SIGNAL: ${a}</h3><div class="sub">${res.state.trader.rationale || ""}</div>
            <button class="btn ghost addBtn" data-add="${t}" style="margin-top:8px">➕ Add ${t} to SAF</button></div>`);
            document.querySelectorAll(".addBtn").forEach(b => b.onclick = (e) => {
                e.stopPropagation();
                openAddToBasket(b.dataset.add);
            });
        } catch (e) { clearInterval(timer); $("#tl").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
    };
}

async function renderPositions() {
    $("#main").innerHTML = `<div class="wrap"><div class="card"><h1>📌 Open Positions</h1>
        <div class="sub">Lifecycle monitor · stops · trailing ratchets</div>
        <div id="out"><span class="loader"></span> loading…</div></div></div>`;
    try {
        const d = await api("/api/positions");
        const rows = Array.isArray(d.positions) ? d.positions : [];
        if (!rows.length) { $("#out").innerHTML = `<div class="panel hold">No open positions.</div>`; return; }
        let html = `<div class="tbl"><table>
            <tr><th>Ticker</th><th class="c">Dir</th><th class="r">Shares</th><th class="r">Entry</th>
            <th class="r">Last</th><th class="r">P/L</th><th class="r">Days</th><th class="r">Stop</th></tr>`;
        rows.forEach(p => {
            html += `<tr class="clickable" data-ticker="${p.ticker}">
                <td><b>${p.ticker}</b></td><td class="c">${p.direction || "LONG"}</td>
                <td class="r">${p.shares ?? "—"}</td>
                <td class="r">$${Number(p.entry_price ?? p.entry ?? 0).toFixed(2)}</td>
                <td class="r">${p.last_price ? "$" + Number(p.last_price).toFixed(2) : "—"}</td>
                <td class="r">${fmtPct(p.unrealized_pct)}</td><td class="r">${p.days_held ?? 0}</td>
                <td class="r">${p.stop ? "$" + Number(p.stop).toFixed(2) : "—"}</td></tr>`;
        });
        $("#out").innerHTML = html + "</table></div>";
    } catch (e) { $("#out").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
}

async function renderMemory() {
    $("#main").innerHTML = `<div class="wrap"><div class="card"><h1>🧠 Trading Memory</h1>
        <div class="sub">Outcome-graded decisions · hit rate feeds future prompts</div>
        <div id="out"><span class="loader"></span> loading…</div></div></div>`;
    try {
        const d = await api("/api/memory");
        const rows = Array.isArray(d.decisions) ? d.decisions : [];
        if (!rows.length) { $("#out").innerHTML = `<div class="panel">No decisions recorded yet.</div>`; return; }
        let html = `<div class="tbl"><table><tr><th>Date</th><th>Ticker</th><th class="c">Action</th>
            <th class="c">Outcome</th><th class="r">Return</th><th>Notes</th></tr>`;
        rows.forEach(m => {
            html += `<tr class="clickable" data-ticker="${m.ticker}"><td>${m.date}</td><td><b>${m.ticker}</b></td>
                <td class="c"><span class="sig ${(m.action||"hold").toLowerCase()}">${m.action}</span></td>
                <td class="c">${m.outcome || "PENDING"}</td>
                <td class="r">${m.realized_ret != null ? fmtPct(m.realized_ret) : "—"}</td>
                <td>${(m.notes || "").slice(0, 80)}</td></tr>`;
        });
        $("#out").innerHTML = html + "</table></div>";
    } catch (e) { $("#out").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
}

async function renderDiag() {
    $("#main").innerHTML = `<div class="wrap"><div class="card"><h1>🧪 Diagnostics</h1>
        <div id="out"><span class="loader"></span> checking…</div></div></div>`;
    try {
        const [h, a] = await Promise.all([
            api("/api/system/health"),
            api("/api/audit").catch(() => ({events:[], chain_ok:null}))
        ]);
        $("#out").innerHTML = `<div class="panel"><h3>Health</h3><div class="kv">
            <b>Status</b><span>${h.status || "—"}</span>
            <b>Provider</b><span>${h.primary_provider || "—"}</span>
            <b>Benchmark</b><span>${h.benchmark} (${h.benchmark_bars} bars)</span>
            <b>AI Key</b><span>${h.ai_key_present ? "✅ present" : "❌ MISSING"}</span>
            <b>Audit chain</b><span>${h.audit_chain_ok ? "✅ intact" : "❌ BROKEN"}</span>
            <b>Universe</b><span>${h.universe_tickers} tickers</span>
            <b>Mode</b><span>${MODE}</span></div></div>`;
    } catch (e) { $("#out").innerHTML = `<div class="panel sell">Error: ${e.message}</div>`; }
}

renderModeToggle();
render();