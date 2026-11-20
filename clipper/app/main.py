"""Clip Studio: web app + background worker for Agent HQ floor 1.

Pages (phone/tablet friendly):  /  add videos and watch jobs   /review  approve clips   /approved  download & post
API (used by n8n):  POST /api/jobs   GET /api/jobs   GET /api/clips?status=approved   GET /api/status
"""
import html
import json
import os
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import pipeline
from .store import Store

DATA = pipeline.DATA
KEEP_SOURCE = os.environ.get("KEEP_SOURCE", "false").lower() == "true"
DEFAULT_CLIPS = int(os.environ.get("DEFAULT_CLIPS", "5"))
(DATA / "jobs").mkdir(parents=True, exist_ok=True)
(DATA / "uploads").mkdir(parents=True, exist_ok=True)

store = Store(DATA / "clipper.db")
app = FastAPI(title="Clip Studio")
app.mount("/files", StaticFiles(directory=str(DATA / "jobs")), name="files")


# ---------------- worker ----------------

def run_job(job):
    job_id = job["id"]
    settings = json.loads(job["settings"] or "{}")
    folder = DATA / "jobs" / str(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    src = job["source"]
    store.update_job(job_id, status="running", step="Getting the video", error="")
    if src.startswith("upload:"):
        video = Path(src[len("upload:"):])
        title = job["title"] or video.stem
    else:
        store.update_job(job_id, step="Downloading the video")
        video, title = pipeline.download(src, folder)
    store.update_job(job_id, title=title)
    pipeline.has_video_stream(video)
    mins = pipeline.duration(video) / 60

    store.update_job(job_id, step=f"Transcribing {mins:.0f} min of audio (long videos take a while)")
    transcript_file = folder / "transcript.json"
    if transcript_file.exists():
        transcript = json.loads(transcript_file.read_text())
    else:
        transcript = pipeline.transcribe(video)
        transcript_file.write_text(json.dumps(transcript))

    store.update_job(job_id, step="Picking the best moments")
    n = int(settings.get("clips") or DEFAULT_CLIPS)
    picks, who = pipeline.pick_moments(transcript, n, title, settings.get("rules", ""))
    if not picks:
        raise RuntimeError("No good moments were found in this video.")

    for i, c in enumerate(picks, 1):
        store.update_job(job_id, step=f"Cutting clip {i} of {len(picks)}")
        name = f"clip{i:02d}"
        mp4, thumb = pipeline.cut_clip(video, transcript, c, folder / "clips", name,
                                       settings.get("mode", "blur"), settings.get("watermark", ""))
        tag = settings.get("tag", "").strip()
        if tag and tag not in c["hashtags"]:
            c["hashtags"].append(tag)
        store.add_clip(job_id, c, f"{job_id}/clips/{mp4.name}", f"{job_id}/clips/{thumb.name}")

    if not KEEP_SOURCE:
        video.unlink(missing_ok=True)
    store.update_job(job_id, status="done", step=f"Done: {len(picks)} clips ready to review (picked by {who})")


def worker():
    store.reset_running()
    while True:
        job = store.next_job()
        if not job:
            time.sleep(3)
            continue
        try:
            run_job(dict(job))
        except Exception as e:  # keep the worker alive; show the reason on the job
            traceback.print_exc()
            store.update_job(job["id"], status="failed", step="Failed", error=str(e)[:500])


@app.on_event("startup")
def start_worker():
    threading.Thread(target=worker, daemon=True).start()


# ---------------- API ----------------

class JobIn(BaseModel):
    url: str
    campaign: str = ""
    clips: int = DEFAULT_CLIPS
    mode: str = "blur"
    watermark: str = ""
    tag: str = ""
    rules: str = ""


@app.post("/api/jobs")
def api_add_job(job: JobIn):
    if store.job_exists(job.url):
        return JSONResponse({"skipped": True, "reason": "This video is already in the system."}, status_code=409)
    settings = job.model_dump(exclude={"url", "campaign"})
    return {"id": store.add_job(job.url, job.campaign, settings)}


@app.get("/api/jobs")
def api_jobs():
    return store.jobs()


@app.get("/api/clips")
def api_clips(status: str | None = None):
    return store.clips(status)


@app.post("/api/clips/{clip_id}/{action}")
def api_clip_action(clip_id: int, action: str):
    status = {"approve": "approved", "reject": "rejected", "posted": "posted", "pending": "pending"}.get(action)
    if not status or not store.get_clip(clip_id):
        raise HTTPException(404, "Unknown clip or action")
    store.set_clip_status(clip_id, status)
    return {"ok": True, "status": status}


@app.get("/api/status")
def api_status():
    return {"counts": store.counts(), "jobs": store.jobs(10)}


# ---------------- pages ----------------

CSS = """
:root{--bg:#121829;--panel:#1c2439;--room:#252f4a;--line:#34405f;--ink:#eef0f7;--muted:#9ba4bf;--lamp:#ffc35c;--good:#5fd39a;--bad:#ff7a7a;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--lamp)}nav{display:flex;gap:6px;flex-wrap:wrap;padding:12px 16px;background:#161d30;border-bottom:1px solid var(--line);align-items:center}
nav b{margin-right:auto;color:var(--lamp);letter-spacing:.12em}nav a{text-decoration:none;color:var(--ink);padding:6px 12px;border-radius:8px;border:1px solid var(--line)}
nav a.on{background:var(--room);border-color:var(--lamp)}main{max-width:1100px;margin:0 auto;padding:16px;display:grid;gap:16px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}h1,h2{margin:0 0 10px}
label{display:grid;gap:4px;font-size:14px;color:var(--muted)}input,select,textarea{font:inherit;color:var(--ink);background:#0d1322;border:1px solid var(--line);border-radius:8px;padding:9px}
.form{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}.wide{grid-column:1/-1}
button,.btn{font:inherit;cursor:pointer;color:var(--ink);background:var(--room);border:1px solid var(--line);border-radius:8px;padding:8px 14px;text-decoration:none;display:inline-block}
button.go{background:#3a2f12;border-color:var(--lamp)}button.yes{background:#1e3b2e;border-color:#2f6b4f}button.no{background:#3a2226;border-color:#6b3540}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.pill{font-size:12px;font-weight:600;padding:2px 8px;border-radius:99px;background:var(--room)}.done{color:var(--good)}.failed{color:var(--bad)}.running{color:var(--lamp)}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(250px,1fr))}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:grid}
.card video{width:100%;aspect-ratio:9/16;background:#000;display:block}.card .body{padding:12px;display:grid;gap:8px}.muted{color:var(--muted);font-size:13px}
.row{display:flex;gap:8px;flex-wrap:wrap}textarea{width:100%;min-height:90px;font-size:14px}.empty{color:var(--muted);text-align:center;padding:30px;border:1px dashed var(--line);border-radius:12px}
"""


def page(title, body, active="", refresh=False):
    c = store.counts()
    links = [("/", "Add video", "add"), ("/review", f"Review ({c.get('pending', 0)})", "review"),
             ("/approved", f"Approved ({c.get('approved', 0)})", "approved")]
    nav = "".join(f'<a href="{h}" class="{"on" if k == active else ""}">{t}</a>' for h, t, k in links)
    meta = '<meta http-equiv="refresh" content="15">' if refresh else ""
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{meta}<title>{title} · Clip Studio</title>
<style>{CSS}</style></head><body><nav><b>CLIP STUDIO</b>{nav}</nav><main>{body}</main>
<script>function copyText(id,btn){{const t=document.getElementById(id);navigator.clipboard.writeText(t.value).then(()=>{{btn.textContent='Copied'}}).catch(()=>{{t.select()}})}}</script>
</body></html>""")


e = html.escape


@app.get("/", response_class=HTMLResponse)
def home():
    jobs = store.jobs()
    rows = "".join(
        f"<tr><td>#{j['id']}</td><td>{e(j['title'] or j['source'][:60])}<div class='muted'>{e(j['campaign'])}</div>"
        f"{'<div class=failed>' + e(j['error']) + '</div>' if j['error'] else ''}</td>"
        f"<td><span class='pill {j['status']}'>{j['status']}</span><div class='muted'>{e(j['step'])}</div></td>"
        f"<td>{'<form method=post action=/job/' + str(j['id']) + '/retry><button>Retry</button></form>' if j['status'] == 'failed' else ''}</td></tr>"
        for j in jobs) or "<tr><td colspan=4 class=muted>No videos yet. Add one above.</td></tr>"
    busy = any(j["status"] in ("queued", "running") for j in jobs)
    body = f"""
<section class="panel"><h1>Add a long video</h1>
<p class="muted">Paste a link, or upload a file from your tablet. Only clip videos a campaign allows you to clip.</p>
<form class="form" method="post" action="/add" enctype="multipart/form-data">
 <label class="wide">Video link (YouTube, Twitch VOD, Kick, etc.)<input name="url" type="url" placeholder="https://..."></label>
 <label class="wide">…or upload a video file<input name="file" type="file" accept="video/*"></label>
 <label>Campaign name<input name="campaign" placeholder="e.g. Money Mindset Show"></label>
 <label>How many clips<input name="clips" type="number" min="1" max="15" value="{DEFAULT_CLIPS}"></label>
 <label>Vertical style<select name="mode"><option value="blur">Fit whole frame (blurred background)</option><option value="crop">Fill screen (crop the sides)</option></select></label>
 <label>On-screen text (campaign watermark)<input name="watermark" placeholder="e.g. @moneymindsetshow"></label>
 <label>Required hashtag<input name="tag" placeholder="e.g. #moneymindsetclips"></label>
 <label class="wide">Campaign rules for the AI editor (optional)<input name="rules" placeholder="e.g. no swearing, must mention the guest's name"></label>
 <div class="wide"><button class="go">Start clipping</button></div>
</form></section>
<section class="panel"><h2>Jobs</h2><table><tr><th></th><th>Video</th><th>Status</th><th></th></tr>{rows}</table>
{'<p class=muted>This page refreshes every 15 seconds while work is in progress.</p>' if busy else ''}</section>"""
    return page("Add video", body, "add", refresh=busy)


@app.post("/add")
async def add(url: str = Form(""), campaign: str = Form(""), clips: int = Form(DEFAULT_CLIPS),
              mode: str = Form("blur"), watermark: str = Form(""), tag: str = Form(""), rules: str = Form(""),
              file: UploadFile | None = File(None)):
    settings = {"clips": clips, "mode": mode, "watermark": watermark, "tag": tag, "rules": rules}
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower() or ".mp4"
        dest = DATA / "uploads" / f"{uuid.uuid4().hex}{ext}"
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f, length=4 * 1024 * 1024)
        store.add_job(f"upload:{dest}", campaign, settings, title=Path(file.filename).stem)
    elif url.strip():
        if not store.job_exists(url.strip()):
            store.add_job(url.strip(), campaign, settings)
    return RedirectResponse("/", status_code=303)


@app.post("/job/{job_id}/retry")
def retry(job_id: int):
    store.update_job(job_id, status="queued", step="Waiting in line", error="")
    return RedirectResponse("/", status_code=303)


def clip_card(c, actions):
    cap_id = f"cap{c['id']}"
    text = f"{c['caption']}\n\n{c['hashtags']}".strip()
    buttons = "".join(
        f'<form method="post" action="/clip/{c["id"]}/{a}"><button class="{cls}">{label}</button></form>'
        for a, label, cls in actions)
    return f"""<article class="card"><video controls preload="none" playsinline poster="/files/{e(c['thumb'])}" src="/files/{e(c['file'])}"></video>
<div class="body"><b>{e(c['title'])}</b><div class="muted">{e(c['campaign'] or c['job_title'] or '')} · {c['end'] - c['start']:.0f}s · score {c['score']}/100</div>
<div class="muted">{e(c['hook'])}</div><textarea id="{cap_id}" readonly>{e(text)}</textarea>
<div class="row"><button type="button" onclick="copyText('{cap_id}',this)">Copy caption</button><a class="btn" href="/files/{e(c['file'])}" download>Download</a></div>
<div class="row">{buttons}</div></div></article>"""


@app.get("/review", response_class=HTMLResponse)
def review():
    clips = store.clips("pending")
    cards = "".join(clip_card(c, [("approve", "Approve", "yes"), ("reject", "Reject", "no")]) for c in clips)
    body = f"""<h1>Review clips</h1><p class="muted">Nothing is posted until you approve it. Best-scoring clips are first.</p>
{f'<div class="grid">{cards}</div>' if cards else '<div class="empty">No clips waiting. Add a video and they will show up here.</div>'}"""
    return page("Review", body, "review")


@app.get("/approved", response_class=HTMLResponse)
def approved():
    ready = store.clips("approved")
    posted = store.clips("posted")[:12]
    cards = "".join(clip_card(c, [("posted", "Mark as posted", "yes"), ("pending", "Send back to review", "")]) for c in ready)
    done = "".join(f"<tr><td>{e(c['title'])}</td><td class=muted>{e(c['campaign'] or '')}</td></tr>" for c in posted)
    body = f"""<h1>Approved: ready to post</h1>
<p class="muted">Download each clip, post it from the TikTok, YouTube or Instagram app with the copied caption, then tap Mark as posted.</p>
{f'<div class="grid">{cards}</div>' if cards else '<div class="empty">No approved clips yet.</div>'}
{f'<section class="panel"><h2>Recently posted</h2><table>{done}</table></section>' if done else ''}"""
    return page("Approved", body, "approved")


@app.post("/clip/{clip_id}/{action}")
def clip_action(clip_id: int, action: str, request: Request):
    api_clip_action(clip_id, action)
    back = request.headers.get("referer") or "/review"
    return RedirectResponse(back, status_code=303)


@app.get("/health")
def health():
    return {"ok": True}
