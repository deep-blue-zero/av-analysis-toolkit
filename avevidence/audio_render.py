"""Pillow-only acoustic evidence plots; no matplotlib or GPU requirement.

Images are navigation, never a declaration that anybody listened. Every plot
has a source-time axis and is linked to the underlying measurement artifact.
"""
from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path
import shutil
import tempfile

from PIL import Image, ImageDraw, ImageFont

from .common import (AVError, file_record, finish_run, output_transaction,
                     read_csv, read_json, safe_member, verify_source, write_json)

WIDTH, HEIGHT = 1280, 520
BOX = (105, 85, 1190, 400)


def _font(size=16):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _canvas(title, note, t0, t1, ymin, ymax, ylabel):
    im=Image.new("RGB",(WIDTH,HEIGHT),"white")
    d=ImageDraw.Draw(im); font=_font()
    d.text((24,18),title,fill="black",font=_font(21))
    d.text((24,49),note[:145],fill="black",font=_font(14))
    l,t,r,b=BOX
    for k in range(6):
        x=l+(r-l)*k/5
        y=b-(b-t)*k/5
        d.line((x,t,x,b),fill=(220,220,220))
        d.line((l,y,r,y),fill=(220,220,220))
        d.text((x-22,b+10),f"{t0+(t1-t0)*k/5:.3f}",fill="black",font=font)
        d.text((12,y-8),f"{ymin+(ymax-ymin)*k/5:.3g}",fill="black",font=font)
    d.rectangle(BOX,outline="black")
    d.text((l,b+40),"Source time (seconds; original PTS minus source origin)",fill="black",font=font)
    d.text((l,HEIGHT-42),ylabel,fill="black",font=font)
    return im,d


def _float(row,key):
    value=row.get(key)
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError):return None


def _line_plot(rows,key,target,title,source,t0,t1,*,time_key="source_center_seconds",bounds=None):
    values=[_float(r,key) for r in rows]
    valid=[v for v in values if v is not None]
    if not valid:return False
    lo,hi=bounds or (min(valid),max(valid))
    if hi<=lo:lo,hi=lo-1,hi+1
    elif bounds is None:
        pad=(hi-lo)*.07;lo-=pad;hi+=pad
    im,d=_canvas(title,f"Computed signal evidence | {source[:16]} | gaps/nulls not connected",t0,t1,lo,hi,key)
    l,t,r,b=BOX
    prev=None
    for row,value in zip(rows,values):
        sec=_float(row,time_key)
        if value is None or sec is None:
            prev=None;continue
        point=(l+(sec-t0)/(t1-t0)*(r-l),b-(value-lo)/(hi-lo)*(b-t))
        # Rows sorted by segment and time. Explicit segment boundary breaks.
        if prev and prev[0]==row["segment_id"]:
            d.line((*prev[1],*point),fill="black",width=2)
        else:d.ellipse((point[0]-1,point[1]-1,point[0]+1,point[1]+1),fill="black")
        prev=(row["segment_id"],point)
    im.save(target)
    return True


def _wave_plot(rows,target,title,source,t0,t1):
    values=[abs(float(r[k])) for r in rows for k in ("sample_min","sample_max")]
    bound=max(1.,max(values,default=0))
    im,d=_canvas(title,f"Canonical f64 | {source[:16]} | 10 ms min/max bins, including final tail; NOT playback",t0,t1,-bound,bound,"Amplitude (1.0 = digital full scale); extrema preserved")
    l,t,r,b=BOX
    # Pool min/max by display column, retaining transients instead of decimation.
    columns={}
    for row in rows:
        x=round(l+(float(row["source_center_seconds"])-t0)/(t1-t0)*(r-l))
        low,high=float(row["sample_min"]),float(row["sample_max"])
        a,z=columns.get(x,(low,high));columns[x]=(min(a,low),max(z,high))
    for x,(a,z) in columns.items():
        y1=b-(a+bound)/(2*bound)*(b-t);y2=b-(z+bound)/(2*bound)*(b-t)
        d.line((x,y1,x,y2),fill="black")
    im.save(target)


def _spectrum(npz,target,entry,source):
    import numpy as np
    with np.load(npz,allow_pickle=False) as data:
        amplitude=data["amplitude"]
        freq=data["frequency_high_hz"]
        start=data["source_support_start_seconds"]
        end=data["source_support_end_seconds"]
        db=20*np.log10(np.maximum(amplitude,1e-6))
        lo,hi=-120.,max(0.,float(db.max()))
        # Fixed -120..0 amplitude reference for typical signals; never per-clip AGC.
        pixels=np.uint8(np.clip((db-lo)/(hi-lo),0,1)*255)
        t0,t1=float(start[0]),float(end[-1])
        im,d=_canvas(f"STFT overview | {entry['segment_id']} ch{entry['channel_index']} | N={entry['frame_length_samples']}",
            f"{source[:16]} | max-pooled amplitude, not full-resolution spectrum | white=strong, black=weak | {lo:g}..{hi:g} dB",t0,t1,0,float(freq[-1]),"Frequency (Hz). Column support overlaps; use NPZ/CSV for exact windows, not pixels.")
        l,t,r,b=BOX
        tile=Image.fromarray(pixels[::-1]).resize((r-l,b-t),Image.Resampling.NEAREST).convert("RGB")
        im.paste(tile,(l,t));ImageDraw.Draw(im).rectangle(BOX,outline="black")
        im.save(target)


def _mel_spectrum(npz,target,entry,source):
    import numpy as np
    with np.load(npz,allow_pickle=False) as data:
        power=data["mel_power"]
        db=10*np.log10(np.maximum(power,1e-12))
        lo,hi=-120.,max(0.,float(db.max()))
        pixels=np.uint8(np.clip((db-lo)/(hi-lo),0,1)*255)
        start=data["source_support_start_seconds"];end=data["source_support_end_seconds"]
        im,d=_canvas(f"Log-mel power overview | {entry['segment_id']} ch{entry['channel_index']}",
            f"{source[:16]} | 64 HTK bands | white=strong | {lo:g}..{hi:g} dB | no waveform normalization",float(start[0]),float(end[-1]),0,64,
            "Mel-band index (0..63), nonlinear frequency spacing; inspect NPZ mel_center_hz for frequencies.")
        l,t,r,b=BOX
        tile=Image.fromarray(pixels[::-1]).resize((r-l,b-t),Image.Resampling.NEAREST).convert("RGB")
        im.paste(tile,(l,t));ImageDraw.Draw(im).rectangle(BOX,outline="black");im.save(target)


def _chroma(rows,target,entry,source):
    import numpy as np
    if not rows:return False
    names=["C","Cs","D","Ds","E","F","Fs","G","Gs","A","As","B"]
    values=np.array([[float(row.get("chroma_"+n) or 0) for row in rows] for n in names])
    # Bound image width; max pooling conserves brief pitch-class activity.
    width=min(1200,values.shape[1]);bins=np.floor(np.arange(width)*values.shape[1]/width).astype(int)
    values=np.maximum.reduceat(values,bins,axis=1)
    t0=float(rows[0]["source_start_seconds"]);t1=float(rows[-1]["source_end_seconds"])
    im,d=_canvas(f"Pitch-class energy | {entry['segment_id']} ch{entry['channel_index']}",
        f"{source[:16]} | nearest-bin STFT fold, A4=440 | not chord recognition; no tuning correction",t0,t1,0,12,"Pitch classes C..B (bottom to top); white=1, black=0; silence shown as zero display intensity")
    l,t,r,b=BOX
    tile=Image.fromarray(np.uint8(np.clip(values[::-1],0,1)*255)).resize((r-l,b-t),Image.Resampling.NEAREST).convert("RGB")
    im.paste(tile,(l,t));d=ImageDraw.Draw(im)
    # Replace numerical pitch-class ticks with actual labels.
    d.rectangle((0,t-10,l-2,b+8),fill="white")
    for i,name in enumerate(names):d.text((40,b-(i+.5)*(b-t)/12-7),name,fill="black",font=_font(14))
    im.save(target);return True


def render_into(root,output,report):
    """Called only on in-process trusted artifacts or an already verified run."""
    root,output=Path(root),Path(output)
    folder=output/"renders";folder.mkdir()
    t0,t1=report["selection"]["requested_start_seconds"],report["selection"]["requested_end_seconds"]
    source=report["parent_source_sha256"];artifacts=[]
    def record(name,parent,kind):
        artifacts.append({"path":"renders/"+name,"measurement_artifact":parent,"kind":kind,
                          "authority":"SIGNAL_VISUALIZATION_NOT_LISTENING","parent_source_sha256":source})
    by_channel=defaultdict(list)
    for row in read_csv(safe_member(root,report["files"]["waveform"])):by_channel[int(row["channel_index"])].append(row)
    for channel,rows in by_channel.items():
        name=f"waveform_ch{channel}.png"
        _wave_plot(rows,folder/name,f"Waveform extrema | channel {channel}",source,t0,t1)
        record(name,report["files"]["waveform"],"waveform")
    primary=defaultdict(list);parents=defaultdict(list)
    for item in report["tracks"]:
        if item["primary"]:
            primary[item["channel_index"]].extend(read_csv(safe_member(root,item["path"])))
            parents[item["channel_index"]].append(item["path"])
    for channel,rows in primary.items():
        for key in ("rms_dbfs","spectral_centroid_hz","spectral_flux_l1","onset_strength_amplitude"):
            name=f"{key}_ch{channel}.png"
            if _line_plot(rows,key,folder/name,f"{key} | channel {channel}",source,t0,t1):record(name,parents[channel],key)
    for entry in report["spectra"]:
        name=Path(entry["path"]).stem+".png"
        _spectrum(safe_member(root,entry["path"]),folder/name,entry,source);record(name,entry["path"],"stft_overview")
        if entry.get("has_mel"):
            name=Path(entry["path"]).stem+"_mel.png"
            _mel_spectrum(safe_member(root,entry["path"]),folder/name,entry,source);record(name,entry["path"],"log_mel_overview")
    if report["files"]["stereo"]:
        rows=read_csv(safe_member(root,report["files"]["stereo"]))
        for key,bounds in (("correlation",(-1,1)),("side_energy_fraction",(0,1)),("right_minus_left_db",None)):
            name=f"stereo_{key}.png"
            if _line_plot(rows,key,folder/name,f"Channel-pair {key}",source,t0,t1,bounds=bounds):record(name,report["files"]["stereo"],key)
    if report["files"]["loudness"]:
        rows=read_csv(safe_member(root,report["files"]["loudness"]))
        for key in ("momentary_lufs","short_term_lufs","integrated_since_segment_start_lufs"):
            name=key+".png"
            if _line_plot(rows,key,folder/name,key,source,t0,t1,time_key="source_measurement_end_seconds"):
                record(name,report["files"]["loudness"],key)
    for entry in report["music"]:
        name=Path(entry["chroma_path"]).stem+".png"
        if _chroma(read_csv(safe_member(root,entry["chroma_path"])),folder/name,entry,source):record(name,entry["chroma_path"],"chroma")
    return artifacts


def render_audio(track_run,output):
    from .inventory import verify_run
    root=Path(track_run).resolve()
    # Bind the manifest before verification or consumption. Re-render only a
    # private snapshot whose bytes match that exact manifest; never replace the
    # expected dependency hashes with hashes observed after rendering.
    manifest_source=file_record(root/"run.json")
    verify_run(root)
    with output_transaction(output,[root]) as stage:
        with tempfile.TemporaryDirectory(prefix=".verified-input-",dir=stage) as tmp:
            snapshot=Path(tmp)
            shutil.copyfile(root/"run.json",snapshot/"run.json")
            verify_source({**manifest_source,"path":str(snapshot/"run.json")})
            manifest=read_json(snapshot/"run.json")
            if manifest.get("operation")!="audio_track":raise AVError("render-audio requires a verified audio-track run")
            sources=[manifest_source]
            for artifact in manifest["artifacts"]:
                original=safe_member(root,artifact["path"])
                copied=safe_member(snapshot,artifact["path"])
                copied.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(original,copied)
                record=file_record(copied)
                if record["sha256"]!=artifact["sha256"] or record["size_bytes"]!=artifact["size_bytes"]:
                    raise AVError("Re-render input changed while taking the verified snapshot")
                sources.append({**record,"path":str(original)})
            verify_run(snapshot)
            report=read_json(snapshot/"audio_tracks.json")
            refs=render_into(snapshot,stage,report)
            verify_run(snapshot)
            write_json(stage/"audio_renders.json",{"schema":"ave.audio_renders.v1","parent_source_sha256":report["parent_source_sha256"],
                "track_run_manifest":manifest_source,"renders":refs,"perceptual_review":"NOT_PERFORMED"})
        # Reject membership changes too. finish_run rechecks every original
        # dependency against the pre-render identities retained above.
        verify_run(root)
        return finish_run(stage,"render_audio",sources,metadata={"result_file":"audio_renders.json"})
