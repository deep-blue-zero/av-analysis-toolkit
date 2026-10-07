"""Offline cue browser. Displays computed tracks without claiming to play or hear audio."""
from __future__ import annotations

import html
import json
from pathlib import Path


def render_view(path, report, tracks):
    import numpy as np
    # Store 50 ms display samples; the complete numerical tracks remain in contours.npz.
    step = max(1, round(.05 / max(.001, float(np.median(np.diff(tracks["time_s"]))) if len(tracks["time_s"]) > 1 else .01)))
    def clean(values):
        return [float(x) if np.isfinite(x) else None for x in values[::step]]
    data = {"cues": report["cues"], "time": clean(tracks["time_s"]),
            "pitch": clean(tracks["pitch_hz"]), "level": clean(tracks["rms_dbfs"])}
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")
    title = html.escape(Path(report["source"]["path"]).name)
    document = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voice evidence browser</title><style>
body{font:16px system-ui,sans-serif;background:#10171e;color:#e2eaf0;margin:0}main{max-width:1200px;margin:auto;padding:28px}h1{font-size:26px;margin:0 0 8px}p{line-height:1.5;color:#b9c9d7}.bar{display:flex;gap:12px;flex-wrap:wrap}input,button{font:inherit;padding:10px;border:1px solid #536575;border-radius:6px;background:#20303d;color:#edf3f8}input{flex:1;min-width:240px}button{cursor:pointer}#graph{width:100%;height:300px;background:#18232e;border-radius:8px;margin:14px 0}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #33424e;text-align:left}tbody tr{cursor:pointer}tbody tr:hover,tbody tr.selected{background:#253c4c}.small{font-size:13px;color:#aebfca}.pill{color:#ffdf94}#words{line-height:1.8;overflow-wrap:anywhere}#detail{position:sticky;top:0;background:#10171ef5;padding-top:8px}svg text{font:12px system-ui;fill:#bfccd6}#source{overflow-wrap:anywhere}</style>
<main><h1>Voice evidence browser</h1><p id="source">__TITLE__</p>
<p>Original cue text, candidate word timing and measured audio tracks. These measurements do not establish listening, emotion or speaker isolation.</p>
<div class="bar"><input id="query" aria-label="Search dialogue or speaker" placeholder="Search Japanese dialogue or speaker"><button id="reset">Show all cues</button></div>
<div id="detail"><p id="chosen">Select a cue to inspect its surrounding audio measurements.</p><svg id="graph" viewBox="0 0 1100 300" role="img" aria-label="Pitch and digital audio level around selected cue"></svg><p id="words"></p></div>
<p class="small" id="count"></p><table><thead><tr><th>Source time</th><th>Speaker label</th><th>Text</th><th>Timing status</th></tr></thead><tbody id="rows"></tbody></table>
<p class="small">The display samples tracks every 50 ms. Full-resolution values and sample coordinates are in contours.npz. Digital level is RMS dBFS, not calibrated loudness or vocal effort. Source time is original PTS minus source origin.</p></main>
<script id="data" type="application/json">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent), rows=document.getElementById('rows');
const stamp=t=>`${Math.floor(t/60).toString().padStart(2,'0')}:${(t%60).toFixed(2).padStart(5,'0')}`;
const norm=s=>s.normalize('NFKC').toLowerCase();
function table(){rows.replaceChildren();const q=norm(document.getElementById('query').value);let count=0;
data.cues.forEach((c,i)=>{if(!norm(c.text+' '+(c.speaker||'')).includes(q))return;count++;let tr=document.createElement('tr');
for(const value of [stamp(c.start_s)+'–'+stamp(c.end_s),c.speaker||'Unassigned',c.text,c.alignment.status]){let td=document.createElement('td');td.textContent=value;tr.append(td)}tr.onclick=()=>{document.querySelectorAll('.selected').forEach(x=>x.classList.remove('selected'));tr.classList.add('selected');select(i)};rows.append(tr)});
document.getElementById('count').textContent=`${count} of ${data.cues.length} cues`;}
function select(i){const c=data.cues[i],a=Math.max(0,c.start_s-1),b=c.end_s+1,svg=document.getElementById('graph');svg.replaceChildren();
document.getElementById('chosen').textContent=stamp(c.start_s)+'–'+stamp(c.end_s)+' · '+c.text;
const warnings=[...(c.flags||[]),...(c.alignment.quality_flags||[])];
document.getElementById('words').textContent=((c.alignment.units||[]).map(w=>`${w.text} [${stamp(w.start_s)}–${stamp(w.end_s)}]`).join('  ')||(c.alignment.error||'No estimated word timing for this cue.'))+(warnings.length?' · Review flags: '+warnings.join(', '):'')+(c.alignment.timestamp_resolution_s?' · Model timing step: '+c.alignment.timestamp_resolution_s+' s':'');
const ns='http://www.w3.org/2000/svg', add=(tag,attrs,text)=>{let e=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);if(text)e.textContent=text;svg.append(e);return e},x=t=>70+(t-a)/(b-a)*960;
add('rect',{x:x(c.start_s),y:10,width:x(c.end_s)-x(c.start_s),height:250,fill:'#223645'});
for(let n=0;n<=4;n++){let t=a+(b-a)*n/4;add('line',{x1:x(t),x2:x(t),y1:10,y2:260,stroke:'#40525d'});add('text',{x:x(t),y:285,'text-anchor':'middle'},stamp(t));}
for(const[k,y0,y1,lo,hi,color,label]of [['pitch',20,125,0,800,'#6dd8d0','Pitch · Hz'],['level',155,255,-80,0,'#e9bc72','Level · dBFS']]){
add('text',{x:5,y:y0+8},label);let d='',last=null;for(let j=0;j<data.time.length;j++){let t=data.time[j],v=data[k][j];if(t<a||t>b)continue;if(v===null){last=null;continue;}let yy=y1-(Math.max(lo,Math.min(hi,v))-lo)/(hi-lo)*(y1-y0);d+=(last!==null&&t-last<.11?'L':'M')+x(t).toFixed(2)+','+yy.toFixed(2);last=t;}add('path',{d,fill:'none',stroke:color,'stroke-width':1.5});add('text',{x:1040,y:y0+8},String(hi));add('text',{x:1040,y:y1},String(lo));}}
document.getElementById('query').oninput=table;document.getElementById('reset').onclick=()=>{document.getElementById('query').value='';table()};table();if(data.cues.length)select(0);
</script></html>"""
    Path(path).write_text(document.replace("__TITLE__", title).replace("__DATA__", payload), encoding="utf-8")
