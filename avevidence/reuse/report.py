"""Offline evidence report, with escaped supplied labels and explicit claim limits."""
from html import escape
import json


def render(path, result, graph, audit):
    occurrences = {o["occurrence_id"]: o for o in graph["occurrences"]}
    rows = []
    for e in graph["matches"]:
        a, b = e["a"], e["b"]
        def label(s):
            o = occurrences[s["occurrence_id"]]
            return escape(o["label"] or o["occurrence_id"]) + f' · {s["source_start_s"]:.3f}–{s["source_end_s"]:.3f}s'
        score = e["metrics"].get("native_ncc", e["metrics"].get("spectral_temporal_ncc", e["metrics"].get("proxy_ncc")))
        preview = result.get("previews", {}).get(e["match_id"], {})
        players = ''.join(f'<div>{side.upper()}: <audio controls preload="none" src="{escape(url, quote=True)}"></audio></div>' for side, url in preview.items())
        detail = escape(json.dumps(e, ensure_ascii=False, indent=2))
        rows.append(f'<article><h3>{escape(e["classification"])} · {escape(e["identity_decision"])}</h3>'
            f'<p>{label(a)} ↔ {label(b)}</p><p>Similarity {score:.4f}; this is a raw score, not a probability.</p>'
            f'<p>Historical direction: unknown. Whole-unit equivalence: {str(e["whole_unit_equivalence"]).lower()}.</p>'
            f'{players}<details><summary>Spans, channels, transformations and evidence</summary><pre>{detail}</pre></details></article>')
    unresolved = sum(e["identity_decision"] == "unresolved" for e in graph["matches"])
    overview = f'<p>{len(occurrences)} configured source windows · {len(graph.get("fragment_occurrences",[]))} fragment appearances · {len(rows)} correspondences · {unresolved} unresolved candidates · revision {graph["revision"]}</p>'
    policies = ''.join('<tr><td>'+escape(p['identity_id'])+'</td><td>'+escape(p['relationship'])+'</td><td>'+str(p['independent_unit_contribution'])+'</td><td>'+escape(', '.join(k for k,v in p['baseline_eligibility'].items() if v['eligible']) or 'None')+'</td></tr>' for p in audit['performances'])
    html = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Vocal performance reuse</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;margin:40px auto;max-width:1100px;padding:0 24px;background:#101820;color:#e6edf3}}a{{color:#74dce2}}h1,h2,h3{{line-height:1.25}}article,.notice{{padding:20px;margin:20px 0;border:1px solid #405768;border-radius:10px;background:#192731}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}}audio{{max-width:100%;vertical-align:middle}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #405768;text-align:left}}input{{padding:10px;width:95%;background:#fff;color:#111}}</style>
<h1>Vocal performance reuse</h1>{overview}<div class="notice">Signal correspondence is computed evidence. Speaker and vocal attribution come from supplied declarations or recorded decisions. No listening is claimed. Playback excerpts are 16-bit audition copies; native sample evidence is preserved separately. Similarity and historical derivation are separate questions.</div>
<p><a href="reuse.json">Scan evidence</a> · <a href="graph.json">Versioned graph</a> · <a href="audit.json">Baseline audit</a> · <a href="baseline-contributions.json">Deduplicated contributions</a></p>
<h2>Counting and measurement eligibility</h2><p>{audit['independent_resolved_unit_count']} deduplicated declared vocal units; {audit['unique_declared_vocal_seconds']:.3f} seconds of unique declared vocal coverage. Counts are provisional within the search scope; they do not prove independent recording acts. Unresolved fragments remain in the audit.</p><table><tr><th>Performance</th><th>Relationship</th><th>Contribution</th><th>Usable measurements</th></tr>{policies}</table>
<h2>Correspondences</h2><label>Filter by source, classification or ID <input id="filter" type="search"></label><div id="matches">{''.join(rows) or '<p>No matching evidence found within the declared search scope. This does not establish distinct performances.</p>'}</div>
<h2>Search scope and limits</h2><pre>{escape(json.dumps(result.get('search', {}), ensure_ascii=False, indent=2))}</pre>
<script>document.getElementById('filter').addEventListener('input',e=>{{const q=e.target.value.toLowerCase();document.querySelectorAll('article').forEach(a=>a.hidden=!a.textContent.toLowerCase().includes(q));}});</script></html>'''
    path.write_text(html, encoding="utf-8")
