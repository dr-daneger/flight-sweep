"""Outputs (spec §9): a self-contained HTML dashboard (no CDN, works from
file://, inline SVG charts) and a committed Markdown daily report.

The verdict and its rationale lead; then best-legit net price by source, deal
depth vs typical, the forecast fan chart with credible bands and the next
event-conditional trough, the survival / stockout-risk curve, and the
calibration scorecard. The verdict is explainable, never a black box.
"""
import json
from pathlib import Path

_VERDICT_COLOR = {"BUY": "var(--good)", "WATCH": "var(--accent)", "WAIT": "var(--dim)"}

TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>price-oracle — __TITLE__</title>
<style>
 :root{--bg:#0f1419;--card:#1a2129;--line:#2b3540;--text:#e6edf3;--dim:#8b9aab;
       --accent:#d4a843;--good:#3fb950;--bad:#f85149;--blue:#58a6ff;}
 *{box-sizing:border-box}
 body{margin:0;font:15px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif;
      background:var(--bg);color:var(--text);padding:24px;}
 h1{font-size:22px;margin:0 0 2px} h1 small{color:var(--dim);font-size:14px;font-weight:400}
 h2{font-size:14px;margin:26px 0 10px;color:var(--accent);text-transform:uppercase;letter-spacing:.08em}
 .sub{color:var(--dim);margin-bottom:14px;font-size:13px}
 .verdict{display:flex;align-items:center;gap:18px;background:var(--card);
          border:1px solid var(--line);border-left:5px solid var(--vc);border-radius:10px;padding:16px 20px;margin-bottom:8px}
 .vbig{font-size:40px;font-weight:800;color:var(--vc);line-height:1}
 .vwhy{font-size:14px} .vwhy b{color:var(--text)}
 .pill{display:inline-block;padding:1px 9px;border-radius:10px;font-size:12px;background:var(--line);color:var(--dim);margin-left:6px}
 .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:10px 0}
 .stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
 .stat .k{font-size:12px;color:var(--dim)} .stat .v{font-size:20px;font-weight:700}
 table{border-collapse:collapse;width:100%;font-size:14px}
 th,td{text-align:left;padding:6px 12px;border-bottom:1px solid var(--line)}
 th{color:var(--dim);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.05em}
 td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
 .ok{color:var(--good)} .no{color:var(--bad)} .susp{color:var(--accent)}
 .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:8px}
 .note{font-size:12.5px;color:var(--dim);margin-top:6px}
 .legend{font-size:12px;color:var(--dim);margin-top:4px}
 .legend i{font-style:normal;padding:0 7px;border-radius:3px;margin-right:3px}
 .wl{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}
 a{color:var(--blue);text-decoration:none} a:hover{text-decoration:underline}
</style></head><body>
<h1>price-oracle <small>__TITLE__</small></h1>
<div class="sub" id="sub"></div>
<div id="verdict"></div>
<div class="stats" id="stats"></div>
<h2>Forecast — achievable low <span class="pill">reference-class prior + live</span></h2>
<div class="card"><div id="fan"></div>
 <div class="legend"><i style="background:#23425c">90% band</i><i style="background:#2f5d80">50% band</i>
 <i style="color:var(--blue)">— median</i> · <i style="color:var(--good)">● history</i> ·
 <i style="color:var(--accent)">▼ trough</i> · <i style="color:var(--bad)">— threshold</i> · dashed = horizon</div></div>
<h2>Stockout / EOL survival S(t)</h2>
<div class="card"><div id="surv"></div>
 <div class="legend">P(still buyable) over time — falls steeply after the successor ships</div></div>
<h2>Best legit net price by source <span class="pill">today</span></h2>
<table id="bysource"></table>
<div class="note" id="anoms"></div>
<h2>Watchlist</h2>
<div class="wl" id="watchlist"></div>
<h2>Calibration scorecard</h2>
<div class="card" id="calib"></div>
<div class="note">BUY/WAIT/WATCH is a decision aid for a terminal-clearance SKU, not booking
advice — always verify the live listing (price, condition, return policy) before purchase.</div>
<script>
const D=__DATA__;
const fmt=n=>n==null?'—':'$'+Math.round(n).toLocaleString();
const pct=n=>n==null?'—':(100*n).toFixed(0)+'%';
const P=D.primary, dec=P.decision, ms=P.market_state;
document.getElementById('sub').innerHTML=
 `${P.label} · run <b>${D.meta.date}</b> · source <b>${D.meta.source}</b> · `+
 `${D.meta.n_hist} day(s) of history · model ${D.meta.model_version}`;
document.documentElement.style.setProperty('--vc',
 ({BUY:'var(--good)',WATCH:'var(--accent)',WAIT:'var(--dim)'})[dec.verdict]||'var(--dim)');
document.getElementById('verdict').innerHTML=
 `<div class="verdict"><div class="vbig">${dec.verdict}</div>
  <div class="vwhy">${dec.rationale}${dec.hard_override?' <span class="pill">hard override</span>':''}</div></div>`;
const savings=dec.expected_savings_wait, hl=P.health||{};
const hcolor={HIGH:'var(--good)',MEDIUM:'var(--accent)',LOW:'var(--bad)'}[hl.confidence]||'var(--dim)';
const hzlab=dec.horizon_is_deadline?'deadline':'EOL horizon';
document.getElementById('stats').innerHTML=[
 ['Best legit now',fmt(dec.best_legit_now)+(ms.best_buyable_condition?` <span class="pill">${ms.best_buyable_condition}</span>`:'')],
 ['BUY threshold',fmt(dec.threshold)],
 ['Expected wait savings',savings==null?'—':(savings>0?fmt(savings):'none')],
 ['Robust street (typical)',fmt(ms.robust_street)],
 ['Deal depth vs typical',fmt(ms.deal_depth)],
 [`Fallback: 77" + K`,fmt(dec.fallback_cost)+` <span class="pill">${dec.substitute_is_live?'live 77"':'anchor'} ${fmt(dec.substitute_price)}</span>`],
 [`P(buyable at ${hzlab})`,pct(dec.p_available_horizon)+` <span class="pill">${dec.horizon_end}</span>`],
 ['Next forecast trough',dec.trough_price?`${fmt(dec.trough_price)} <span class="pill">${dec.trough_date}</span>`:'—'],
 ['Dominant uncertainty',dec.dominant_driver],
 ['Data confidence',`<span style="color:${hcolor}">${hl.confidence||'—'}</span>`],
 ['Prior weight (shrinks)',pct(dec.prior_weight)],
].map(([k,v])=>`<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
if(hl.summary){document.getElementById('verdict').innerHTML+=
 `<div class="note">Data health <b style="color:${hcolor}">${hl.confidence}</b> — ${hl.summary}</div>`;}

// ---- fan chart: history points + forecast bands + threshold + deadline ----
function fan(el){
 const W=900,H=300,PADL=58,PADR=16,PADT=14,PADB=28;
 const hist=P.history, fc=P.forecast;
 const xs=[...hist.map(h=>h.date),...fc.map(f=>f.date)];
 const ord=d=>Math.floor(new Date(d+'T00:00:00Z')/864e5);
 const x0=Math.min(...xs.map(ord)), x1=Math.max(...xs.map(ord),ord(dec.horizon_end));
 const ys=[...hist.flatMap(h=>[h.L,h.M]).filter(v=>v!=null),
           ...fc.flatMap(f=>[f.q05,f.q95])];
 let y0=Math.min(...ys), y1=Math.max(...ys,dec.threshold); const pad=(y1-y0)*0.08||50; y0-=pad;y1+=pad;
 const X=d=>PADL+(ord(d)-x0)/((x1-x0)||1)*(W-PADL-PADR);
 const Y=v=>PADT+(1-(v-y0)/((y1-y0)||1))*(H-PADT-PADB);
 const path=(pts)=>pts.map((p,i)=>(i?'L':'M')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
 const band=(lo,hi,fill)=>{const top=fc.map(f=>[X(f.date),Y(f[hi])]);
   const bot=fc.map(f=>[X(f.date),Y(f[lo])]).reverse();
   return `<path d="${path(top.concat(bot))}Z" fill="${fill}" opacity=".55"/>`;};
 let svg=`<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet" font-family="inherit">`;
 // y gridlines
 for(let i=0;i<=4;i++){const v=y0+(y1-y0)*i/4;const yy=Y(v);
   svg+=`<line x1="${PADL}" y1="${yy}" x2="${W-PADR}" y2="${yy}" stroke="#2b3540" stroke-width="1"/>`+
        `<text x="${PADL-6}" y="${yy+4}" fill="#8b9aab" font-size="11" text-anchor="end">${fmt(v)}</text>`;}
 svg+=band('q05','q95','#23425c')+band('q25','q75','#2f5d80');
 svg+=`<path d="${path(fc.map(f=>[X(f.date),Y(f.q50)]))}" fill="none" stroke="#58a6ff" stroke-width="2"/>`;
 // threshold line + deadline marker
 svg+=`<line x1="${PADL}" y1="${Y(dec.threshold)}" x2="${W-PADR}" y2="${Y(dec.threshold)}" stroke="#f85149" stroke-width="1.4" stroke-dasharray="2 3"/>`;
 svg+=`<line x1="${X(dec.horizon_end)}" y1="${PADT}" x2="${X(dec.horizon_end)}" y2="${H-PADB}" stroke="#8b9aab" stroke-width="1.2" stroke-dasharray="4 4"/>`;
 svg+=`<text x="${X(dec.horizon_end)}" y="${PADT+10}" fill="#8b9aab" font-size="11" text-anchor="middle">${dec.horizon_is_deadline?'deadline':'horizon'}</text>`;
 // history points (achievable low)
 hist.forEach(h=>{if(h.L!=null)svg+=`<circle cx="${X(h.date)}" cy="${Y(h.L)}" r="2.4" fill="#3fb950"/>`;});
 // trough marker
 if(dec.trough_date)svg+=`<text x="${X(dec.trough_date)}" y="${Y(dec.trough_price)-6}" fill="#d4a843" font-size="13" text-anchor="middle">▼</text>`;
 svg+='</svg>'; el.innerHTML=svg;
}
fan(document.getElementById('fan'));

function surv(el){
 const s=P.survival; if(!s||!s.length){el.innerHTML='<span class="pill">no curve</span>';return;}
 const W=900,H=160,PADL=58,PADR=16,PADT=10,PADB=24;
 const ord=d=>Math.floor(new Date(d+'T00:00:00Z')/864e5);
 const x0=ord(s[0].date),x1=ord(s[s.length-1].date);
 const X=d=>PADL+(ord(d)-x0)/((x1-x0)||1)*(W-PADL-PADR);
 const Y=v=>PADT+(1-v)*(H-PADT-PADB);
 let svg=`<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet">`;
 [0,0.5,1].forEach(v=>{svg+=`<line x1="${PADL}" y1="${Y(v)}" x2="${W-PADR}" y2="${Y(v)}" stroke="#2b3540"/>`+
   `<text x="${PADL-6}" y="${Y(v)+4}" fill="#8b9aab" font-size="11" text-anchor="end">${pct(v)}</text>`;});
 const pts=s.map(p=>`${X(p.date).toFixed(1)},${Y(p.S).toFixed(1)}`).join(' ');
 svg+=`<polyline points="${pts}" fill="none" stroke="#f85149" stroke-width="2"/>`;
 svg+=`<line x1="${X(dec.horizon_end)}" y1="${PADT}" x2="${X(dec.horizon_end)}" y2="${H-PADB}" stroke="#8b9aab" stroke-dasharray="4 4"/>`;
 svg+='</svg>'; el.innerHTML=svg;
}
surv(document.getElementById('surv'));

document.getElementById('bysource').innerHTML=
 '<tr><th>Source</th><th>Condition</th><th>Auth</th><th class="num">Net</th><th>In stock</th><th>Credibility</th></tr>'+
 P.by_source.map(o=>`<tr><td>${o.source_id}</td><td>${o.condition}</td><td>${o.authorization}</td>`+
  `<td class="num">${fmt(o.net_effective_price)}</td>`+
  `<td>${o.in_stock?'<span class="ok">✓</span>':'<span class="no">✗</span>'}</td>`+
  `<td>${o.credibility_flag==='ok'?'<span class="ok">ok</span>':'<span class="susp">'+o.credibility_flag+'</span>'}</td></tr>`).join('');

const an=D.anomalies||[];
document.getElementById('anoms').innerHTML=an.length
 ? '⚠ data anomalies: '+an.map(a=>`${a.kind}${a.source?' ('+a.source+')':''}`).join(', ') : '';

document.getElementById('watchlist').innerHTML=(D.watchlist||[]).map(w=>
 `<div class="card"><b>${w.label}</b><div class="note">role: ${w.role}</div>`+
 `<div class="v" style="font-size:20px;font-weight:700">${fmt(w.best_buyable_net)}</div>`+
 `<div class="note">street ${fmt(w.robust_street)} · ${w.in_stock_any?'in stock':'<span class="no">no stock</span>'}</div></div>`).join('');

const c=D.calibration||{};
document.getElementById('calib').innerHTML=(c.n_scored)
 ? `Scored <b>${c.n_scored}</b> matured forecasts · pinball(q50) <b>${c.pinball_q50}</b> · `+
   `90% interval coverage <b>${pct(c.coverage_90)}</b> (target 90%) · model ${c.model_version}`
 : `<span class="pill">${c.note||'not enough history yet'}</span> — calibration accrues as forecasts mature.`;
</script></body></html>"""


def _to_py(o):
    """Coerce numpy scalars (from Parquet round-trips) to JSON-native types."""
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def render_html(payload, out_path, title):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, default=_to_py)
    html = TEMPLATE.replace("__DATA__", data).replace("__TITLE__", title)
    Path(out_path).write_text(html, encoding="utf-8")
    return out_path


def render_markdown(payload, out_path):
    P = payload["primary"]
    d, ms, health = P["decision"], P["market_state"], P.get("health", {})

    def money(x):
        return f"${x:,.0f}" if x is not None else "—"

    L = d["best_legit_now"]
    savings = d["expected_savings_wait"]
    sub_src = 'live 77"' if d["substitute_is_live"] else "anchor"
    hzn_label = "deadline" if d["horizon_is_deadline"] else "EOL horizon"
    lines = [
        f"# price-oracle — {P['label']}",
        "",
        f"**{d['verdict']}**{' (hard override)' if d['hard_override'] else ''} — {d['rationale']}",
        "",
        f"_Run {payload['meta']['date']} · source {payload['meta']['source']} · "
        f"{payload['meta']['n_hist']} day(s) history · model {payload['meta']['model_version']}_",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Best legit now | {money(L)}"
        f" ({ms.get('best_buyable_condition') or '—'}, {ms.get('best_buyable_source') or '—'}) |",
        f"| BUY threshold (continuation CE) | {money(d['threshold'])} |",
        f"| Expected savings from waiting | "
        f"{money(savings) if savings and savings > 0 else 'none'} |",
        f"| Robust street (typical) | {money(ms.get('robust_street'))} |",
        f"| Deal depth vs typical | {money(ms.get('deal_depth'))} |",
        f"| Fallback (77\" + K) | {money(d['fallback_cost'])} "
        f"({sub_src} {money(d['substitute_price'])} + K) |",
        f"| P(buyable at {hzn_label} {d['horizon_end']}) | {d['p_available_horizon']:.0%} |",
        f"| Next forecast trough | "
        f"{(money(d['trough_price']) + ' on ' + d['trough_date']) if d['trough_price'] else '—'} |",
        f"| P(stockout before trough) | "
        f"{('%.0f%%' % (100*d['p_stockout_before_trough'])) if d['p_stockout_before_trough'] is not None else '—'} |",
        f"| Dominant uncertainty | {d['dominant_driver']} |",
        f"| Data confidence | {health.get('confidence', '—')} ({health.get('summary', '')}) |",
        f"| Reference-class prior weight | {d['prior_weight']:.0%} (shrinks as live data accrues) |",
        "",
        "## Best legit net price by source (today)",
        "",
        "| Source | Condition | Auth | Net | In stock | Credibility |",
        "|---|---|---|---:|:---:|---|",
    ]
    for o in P["by_source"]:
        lines.append(
            f"| {o['source_id']} | {o['condition']} | {o['authorization']} | "
            f"${o['net_effective_price']:,.0f} | {'✓' if o['in_stock'] else '✗'} | "
            f"{o['credibility_flag']} |")
    if payload.get("anomalies"):
        lines += ["", "## ⚠ Data anomalies", ""]
        for a in payload["anomalies"]:
            src = f" ({a['source']})" if a.get("source") else ""
            lines.append(f"- {a['kind']}{src}: {a.get('detail', '')}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
