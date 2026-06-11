"""Render the static, self-contained dashboard (no CDN, works from file://)."""
import json
from pathlib import Path

from . import logos

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flight Sweep — Oct 2026 trip</title>
<style>
  :root { --bg:#0f1419; --card:#1a2129; --line:#2b3540; --text:#e6edf3; --dim:#8b9aab;
          --accent:#d4a843; --good:#3fb950; --bad:#f85149; --blue:#58a6ff; }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif;
         background:var(--bg); color:var(--text); padding:24px; }
  h1 { font-size:22px; margin:0 0 4px; } h1 .wine { color:var(--accent); }
  h2 { font-size:16px; margin:28px 0 10px; color:var(--accent);
       text-transform:uppercase; letter-spacing:.08em; }
  .sub { color:var(--dim); margin-bottom:18px; }
  .pill { display:inline-block; padding:1px 9px; border-radius:10px; font-size:12px;
          background:var(--line); color:var(--dim); margin-left:8px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  .card.top { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent); }
  .rank { float:right; color:var(--dim); font-size:13px; }
  .city { font-size:18px; font-weight:600; }
  .total { font-size:26px; font-weight:700; margin:6px 0 0; }
  .total small { font-size:13px; color:var(--dim); font-weight:400; }
  .signals { font-size:12.5px; margin:2px 0 8px; min-height:1.2em; }
  .lrows { border-top:1px solid var(--line); }
  .lrow { display:grid; grid-template-columns:56px 84px 1fr auto; gap:8px;
          align-items:center; padding:7px 0; border-bottom:1px solid var(--line); }
  .lroute { font-weight:600; font-size:13.5px; white-space:nowrap; }
  .lwhen { font-size:12.5px; color:var(--dim); white-space:nowrap; overflow:hidden;
           text-overflow:ellipsis; }
  .lprice { font-size:14px; text-align:right; white-space:nowrap; }
  .als { display:inline-flex; align-items:center; padding-left:5px; }
  img.al, .als svg { width:24px; height:24px; }
  img.al { border-radius:50%; background:#fff; object-fit:contain; padding:2px;
           border:1px solid var(--line); margin-left:-7px; }
  .alx { display:inline-flex; align-items:center; justify-content:center;
         width:24px; height:24px; border-radius:50%; background:var(--line);
         color:var(--dim); font-size:12px; margin-left:-7px; }
  td img.al, td .alx { width:20px; height:20px; vertical-align:middle; }
  td .als { margin-right:7px; }
  .cair { white-space:nowrap; }
  .meta { font-size:12.5px; color:var(--dim); margin-top:8px; }
  .route { font-style:italic; }
  .delta-up { color:var(--bad); } .delta-down { color:var(--good); }
  table { border-collapse:collapse; width:100%; font-size:14px; }
  th,td { text-align:left; padding:7px 12px; border-bottom:1px solid var(--line); }
  th { color:var(--dim); font-weight:600; font-size:12.5px; text-transform:uppercase; letter-spacing:.05em; }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .flag-ok { color:var(--good); } .flag-warn { color:var(--bad); }
  .alert { border-left:3px solid var(--good); background:var(--card); padding:8px 14px;
           margin-bottom:6px; border-radius:0 8px 8px 0; font-size:14px; }
  .alert.up { border-left-color:var(--bad); }
  svg.spark { vertical-align:middle; }
  .gf { color:var(--blue); }
  a { color:var(--blue); text-decoration:none; }
  a:hover { text-decoration:underline; }
  a.plink b { color:var(--blue); }
  .note { font-size:12.5px; color:var(--dim); margin-top:6px; }
  @media (max-width:700px){ body{padding:12px} th,td{padding:6px} }
</style>
</head>
<body>
<h1>✈ Flight Sweep <span class="wine">PDX ⇄ DFW ⇄ Europe · Oct 2026</span></h1>
<div class="sub" id="sub"></div>
<div id="alerts"></div>
<h2>Ranked itineraries <span class="pill">flights + est. van drop fee</span></h2>
<div class="grid" id="cards"></div>
<div class="note">Van drop fees are static estimates for a one-way rental returned in
Bordeaux — cross-border one-ways (ES/PT/IT/CH) are pricey and some agencies refuse them.
Always verify the winning fare on Google Flights / the airline before booking.</div>
<h2>Getting to Dallas <span class="pill">arrive by Oct 10</span></h2>
<table id="tbl-outbound"></table>
<h2>Flying home from Bordeaux <span class="pill">depart Oct 25–26</span></h2>
<table id="tbl-home"></table>
<div class="note">Hard windows (enforced when fetching, before any price ranking):
Oct 8 departs ≥5:00 pm, Oct 9 departs ≤7:00 pm, Oct 25 departs ≥5:00 pm. Rows marked "?"
came from a source without departure-time data — check via the price link before
trusting them. Click any blue price to open that exact search (right airports,
date, all 5 travelers) in Google Flights; apply the time slider there to match the
window.</div>
<h2>Transatlantic leg detail + price history</h2>
<table id="tbl-ta"></table>
<script>
const D = __DATA__;
const fmt = n => '$' + Math.round(n).toLocaleString();
// data layer stays 24h (lexicographic window compares need it); display is am/pm
const ampm = t => {
  if (!t) return '';
  const [h, m] = t.split(':').map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2,'0')} ${h >= 12 ? 'pm' : 'am'}`;
};
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const mdate = d => `${MON[+d.slice(5,7)-1]} ${+d.slice(8,10)}`;
const seats = D.meta.adults + D.meta.children;
document.getElementById('sub').innerHTML =
  `${seats} travelers · PDX → DFW (Oct 8 eve / Oct 9) → Europe entry city (Oct 11/12) → van road trip ` +
  `→ Bordeaux → PDX (Oct 25/26) · last sweep <b>${D.meta.sweep_id.slice(0,16).replace('T',' ')}</b>` +
  ` UTC · source <b>${D.meta.source}</b> · ${D.meta.n_sweeps} sweep(s) of history`;

const alertsEl = document.getElementById('alerts');
if (D.alerts.length) {
  alertsEl.innerHTML = '<h2>Price moves since last sweep</h2>' + D.alerts.map(a =>
    `<div class="alert ${a.change_pct>0?'up':''}">${a.origin}→${a.dest} ${a.dep_date}: ` +
    `${fmt(a.prev)} → <b>${fmt(a.now)}</b> ` +
    `<span class="${a.change_pct>0?'delta-up':'delta-down'}">(${a.change_pct>0?'+':''}${a.change_pct}%)</span></div>`
  ).join('');
}

function spark(series, w=110, h=26) {
  if (!series || series.length < 2) return '<span class="pill">need 2+ sweeps</span>';
  const v = series.map(p => p[1]), min = Math.min(...v), max = Math.max(...v), span = (max-min)||1;
  const pts = v.map((y,i) => `${(i/(v.length-1)*w).toFixed(1)},${(h-3-(y-min)/span*(h-6)).toFixed(1)}`);
  const up = v[v.length-1] > v[0];
  return `<svg class="spark" width="${w}" height="${h}"><polyline points="${pts.join(' ')}" `+
         `fill="none" stroke="${up?'#f85149':'#3fb950'}" stroke-width="1.8"/></svg>`;
}
const hkey = (leg,o,d,dt) => [leg,o,d,dt].join('|');
// offer carried over from an earlier sweep because this one missed the query
const stale = r => r.stale
  ? ` <span class="pill" title="no fresh quote this sweep">stale ${r.stale.slice(5,10)}</span>` : '';
// price wrapped in a deep link to the exact Google Flights search behind it
const plink = (r, inner) => r.gf_url
  ? `<a class="plink" href="${r.gf_url}" target="_blank" title="open this exact search in Google Flights">${inner}</a>` : inner;
const dtime = r => {
  const t = (r.dep_time||'').slice(11,16);
  return t ? ` · ${ampm(t)}` : '';
};
// airline logos, embedded as data URIs at render time (codeshares stack)
const logos = c => {
  const imgs = (c||'').split(', ').filter(Boolean).slice(0,3).map(n =>
    D.logos[n] ? `<img class="al" src="${D.logos[n]}" alt="${n}" title="${n}">` : '').join('');
  return `<span class="als">${imgs || '<span class="alx" title="carrier not reported">✈</span>'}</span>`;
};
// hard departure-time window status: enforced at fetch, so shown rows either
// have a verified in-window time or no time data from the source
const winflag = r => {
  const w = D.windows[r.leg + '|' + r.dep_date];
  if (!w) return '—';
  const t = (r.dep_time||'').slice(11,16);
  const label = (w.dep_after?'≥'+ampm(w.dep_after):'') + (w.dep_before?'≤'+ampm(w.dep_before):'');
  return t ? `<span class="flag-ok">✓ ${ampm(t)} (${label})</span>`
           : `<span class="flag-warn">? time unverified (${label})</span>`;
};

const legRow = (route, r, extra='') => `
  <div class="lrow">
    ${logos(r.carrier)}
    <span class="lroute">${route}</span>
    <span class="lwhen" title="${r.carrier||''}">${mdate(r.dep_date)}${dtime(r)}${extra}${stale(r)}</span>
    <span class="lprice">${plink(r, `<b>${fmt(r.price)}</b>`)}</span>
  </div>`;

document.getElementById('cards').innerHTML = D.itineraries.map((it,i) => {
  const delta = it.prev_flights_total
    ? (it.flights_total - it.prev_flights_total) : null;
  const gf = D.gflights[hkey('transatlantic','DFW',it.code,it.transatlantic.dep_date)];
  const ta = it.transatlantic;
  const signals = [
    delta!==null ? `<span class="${delta>0?'delta-up':'delta-down'}">${delta>0?'▲':'▼'} ${fmt(Math.abs(delta))} vs last sweep</span>` : '',
    gf ? `<span class="gf">GF check ${fmt(gf.price)}</span>` : '',
  ].filter(Boolean).join(' · ');
  return `<div class="card ${i===0?'top':''}">
    <span class="rank">#${i+1}</span>
    <div class="city">${it.city} <span class="pill">${it.code} · ${it.country}</span></div>
    <div class="total">${fmt(it.adjusted_total)} <small>flights ${fmt(it.flights_total)} + van fee ~${fmt(it.van_fee_usd)}</small></div>
    <div class="signals">${signals}</div>
    <div class="lrows">
      ${legRow('PDX→DFW', it.outbound)}
      ${legRow('DFW→'+it.code, ta, ` · ${ta.stops??'?'} stop${ta.stops==1?'':'s'}`)}
      ${legRow('BOD→PDX', it.home)}
    </div>
    <div class="meta" title="${it.route}">~${it.weather_c}°C late Oct · ${it.drive_hours}h drive ·
      <span class="route">${it.route}</span></div>
  </div>`;
}).join('');

function legTable(el, rows) {
  document.getElementById(el).innerHTML =
    `<tr><th>Date</th><th>Carrier</th><th class="num">Stops</th><th>Departs</th>`+
    `<th>Duration</th><th class="num">Party total</th><th>Trend</th><th>Time window</th></tr>` +
    rows.map(r => {
      const hist = D.history[hkey(r.leg, r.origin, r.dest, r.dep_date)];
      const dep = (r.dep_time||'').slice(11,16);
      return `<tr><td>${r.dep_date}${stale(r)}</td><td class="cair">${logos(r.carrier)}${r.carrier||'—'}</td><td class="num">${r.stops??'—'}</td>`+
        `<td>${dep?ampm(dep):'—'}</td><td>${r.duration_min?Math.floor(r.duration_min/60)+'h'+String(r.duration_min%60).padStart(2,'0'):'—'}</td>`+
        `<td class="num">${plink(r, `<b>${fmt(r.price)}</b>`)}</td><td>${spark(hist)}</td><td>${winflag(r)}</td></tr>`;
    }).join('');
}
legTable('tbl-outbound', D.legs.outbound);
legTable('tbl-home', D.legs.home);

document.getElementById('tbl-ta').innerHTML =
  `<tr><th>Entry city</th><th>Date</th><th>Departs</th><th>Carrier</th><th class="num">Stops</th><th>Duration</th>`+
  `<th class="num">Party total</th><th class="num">GF check</th><th>Trend</th></tr>` +
  D.itineraries.map(it => {
    const r = it.transatlantic;
    const hist = D.history[hkey('transatlantic', r.origin, r.dest, r.dep_date)];
    const gf = D.gflights[hkey('transatlantic', r.origin, r.dest, r.dep_date)];
    return `<tr><td>${it.city} (${it.code})${stale(r)}</td><td>${r.dep_date}</td><td>${ampm((r.dep_time||'').slice(11,16))||'—'}</td><td class="cair">${logos(r.carrier)}${r.carrier||'—'}</td>`+
      `<td class="num">${r.stops??'—'}</td>`+
      `<td>${r.duration_min?Math.floor(r.duration_min/60)+'h'+String(r.duration_min%60).padStart(2,'0'):'—'}</td>`+
      `<td class="num">${plink(r, `<b>${fmt(r.price)}</b>`)}</td>`+
      `<td class="num gf">${gf?fmt(gf.price):'—'}</td><td>${spark(hist)}</td></tr>`;
  }).join('');
</script>
</body>
</html>
"""


def render(payload: dict, out_path: str):
    payload = {**payload, "logos": logos.build_logo_map(payload)}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    html = TEMPLATE.replace("__DATA__", json.dumps(payload))
    Path(out_path).write_text(html, encoding="utf-8")
    return out_path
