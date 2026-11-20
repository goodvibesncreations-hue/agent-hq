"""The clipping pipeline: get video -> transcribe -> pick moments -> cut vertical captioned clips.

Each step is one "agent" from Agent HQ floor 1:
  Transcriber     -> transcribe()
  Editor-in-Chief -> pick_moments()
  Cutter          -> cut_clip()
"""
import json
import os
import re
import subprocess
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "/data"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
MIN_LEN = int(os.environ.get("CLIP_MIN_SECONDS", "15"))
MAX_LEN = int(os.environ.get("CLIP_MAX_SECONDS", "60"))
FONT = os.environ.get("CAPTION_FONT", "DejaVu Sans")


# ---------- Getting the video ----------

def download(url: str, out_dir: Path) -> tuple[Path, str]:
    """Download a video with yt-dlp. Returns (file path, title)."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
    }
    cookies = DATA / "cookies.txt"
    if cookies.exists():
        opts["cookiefile"] = str(cookies)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    files = sorted(out_dir.glob("source.*"))
    if not files:
        raise RuntimeError("Download finished but no video file was found.")
    return files[0], info.get("title") or "Untitled video"


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def has_video_stream(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    if not out:
        raise RuntimeError("This file has no video track.")
    w, h = out.split(",")[:2]
    return int(w), int(h)


# ---------- Transcriber ----------

_model = None


def transcribe(video: Path) -> list[dict]:
    """Timestamped transcript with word timings: [{start, end, text, words:[{start,end,word}]}]."""
    test = os.environ.get("TEST_TRANSCRIPT")
    if test:  # used by the self-test, which can't download a speech model
        return json.loads(Path(test).read_text())

    global _model
    from faster_whisper import WhisperModel

    if _model is None:
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                              download_root=str(DATA / "models"))
    segments, _info = _model.transcribe(str(video), word_timestamps=True, vad_filter=True)
    result = []
    for s in segments:
        result.append({
            "start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip(),
            "words": [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()}
                      for w in (s.words or [])],
        })
    return result


# ---------- Editor-in-Chief ----------

PICK_PROMPT = """You are the editor for a short-form clipping team. Below is a timestamped transcript of a long video{title_part}.

Pick the {n} best moments to post as standalone vertical clips on TikTok, YouTube Shorts and Instagram Reels.

Rules:
- Each clip is {min_len}-{max_len} seconds long.
- It must make sense with no context: a strong hook in the first 3 seconds, and a clear payoff.
- Prefer surprising claims, strong opinions, stories, punchlines and useful tips.
- Start and end on sentence boundaries. Clips must not overlap.
{rules_part}
Reply with JSON only, in this exact shape:
{{"clips": [{{"start": 12.3, "end": 48.0, "title": "short catchy title, under 60 characters", "hook": "why this moment works, one sentence", "caption": "post caption, 1-2 sentences, no hashtags", "hashtags": ["#tag1", "#tag2", "#tag3"], "score": 87}}]}}

score is 0-100: how likely this clip is to perform well.

TRANSCRIPT:
{transcript}"""


def pick_moments(transcript: list[dict], n: int, title: str = "", rules: str = "") -> tuple[list[dict], str]:
    """Returns (clips, who picked them)."""
    if not transcript:
        raise RuntimeError("No speech was found in this video, so there is nothing to clip.")
    from . import ai

    if ai.configured():
        clips, who = _pick_with_ai(transcript, n, title, rules)
    else:
        clips, who = _pick_fallback(transcript, n), "basic pick (no AI key set)"
    return _clean_clips(clips, transcript, n), who


def _pick_with_ai(transcript, n, title, rules):
    from . import ai

    lines = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in transcript)
    prompt = PICK_PROMPT.format(
        n=n, min_len=MIN_LEN, max_len=MAX_LEN, transcript=lines,
        title_part=f' titled "{title}"' if title else "",
        rules_part=f"- Campaign rules to respect: {rules}\n" if rules else "",
    )
    text, who = ai.ask(prompt)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise RuntimeError(f"The AI editor ({who}) did not return a list of clips. Try the job again.")
    return json.loads(match.group(0)).get("clips", []), who


def _pick_fallback(transcript, n):
    """No API key: pick the windows with the most words per second (busiest talk)."""
    total = transcript[-1]["end"]
    target = min(MAX_LEN, max(MIN_LEN, 35))
    windows = []
    t = transcript[0]["start"]
    while t + MIN_LEN <= total:
        words = sum(len(s["text"].split()) for s in transcript if s["start"] >= t and s["end"] <= t + target)
        windows.append((words, t))
        t += 10
    windows.sort(reverse=True)
    picked = []
    for _, start in windows:
        if all(abs(start - p) >= target for p in picked):
            picked.append(start)
        if len(picked) == n:
            break
    out = []
    for i, start in enumerate(sorted(picked)):
        first = next((s["text"] for s in transcript if s["start"] >= start), "Clip")
        out.append({"start": start, "end": start + target, "title": first[:58],
                    "hook": "Busiest stretch of talk (no AI key set, so this is a simple pick).",
                    "caption": first[:140], "hashtags": ["#clips", "#podcast"], "score": 50})
    return out


def _clean_clips(clips, transcript, n):
    """Snap to sentence boundaries, enforce length limits, drop overlaps."""
    starts = [s["start"] for s in transcript]
    ends = [s["end"] for s in transcript]
    total = ends[-1]
    cleaned = []
    for c in clips:
        try:
            a, b = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        a = max([x for x in starts if x <= a + 0.5] or [starts[0]])
        b = min([x for x in ends if x >= b - 0.5] or [total])
        if b - a > MAX_LEN:
            b = max([x for x in ends if x <= a + MAX_LEN] or [a + MAX_LEN])
        if b - a < MIN_LEN:
            b = min(total, a + MIN_LEN)
        if b - a < 5:
            continue
        if any(a < o["end"] and b > o["start"] for o in cleaned):
            continue
        tags = [t if str(t).startswith("#") else f"#{t}" for t in c.get("hashtags", [])][:8]
        cleaned.append({
            "start": round(a, 2), "end": round(b, 2),
            "title": str(c.get("title", "Clip"))[:80],
            "hook": str(c.get("hook", "")),
            "caption": str(c.get("caption", "")),
            "hashtags": tags,
            "score": int(c.get("score", 50)),
        })
    cleaned.sort(key=lambda c: -c["score"])
    return cleaned[:n]


# ---------- Cutter ----------

def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_captions(transcript, start, end, watermark="") -> str:
    """Word-timed captions, 3 words at a time, big and bold in the lower middle of a 1080x1920 frame."""
    words = [w for s in transcript for w in s.get("words", []) if w["start"] >= start - 0.05 and w["end"] <= end + 0.05]
    if not words:  # no word timings: fall back to whole segments
        words = [{"start": s["start"], "end": s["end"], "word": s["text"]}
                 for s in transcript if s["end"] > start and s["start"] < end]
    lines = []
    for i in range(0, len(words), 3):
        group = words[i:i + 3]
        a = group[0]["start"] - start
        b = (words[i + 3]["start"] if i + 3 < len(words) else group[-1]["end"]) - start
        text = _ass_escape(" ".join(w["word"] for w in group)).upper()
        lines.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(min(b, end - start))},Cap,,0,0,0,,{text}")
    if watermark:
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(end - start)},Mark,,0,0,0,,{_ass_escape(watermark)}")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,80,80,560,1
Style: Mark,{FONT},44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,8,60,60,140,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header + "\n".join(lines) + "\n"


def cut_clip(video: Path, transcript, clip: dict, out_dir: Path, name: str,
             mode: str = "blur", watermark: str = "") -> tuple[Path, Path]:
    """Cut one 9:16 clip with burned-in captions. mode: 'blur' keeps the whole frame on a blurred
    background (safe for two-person podcasts); 'crop' fills the screen by cutting the sides."""
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = clip["start"], clip["end"]
    ass_name = f"{name}.ass"
    (out_dir / ass_name).write_text(build_captions(transcript, start, end, watermark), encoding="utf-8")
    if mode == "crop":
        vf = f"[0:v]scale=-2:1920,crop=1080:1920,setsar=1,ass={ass_name}[v]"
    else:
        vf = ("[0:v]split[a][b];"
              "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:4[bg];"
              "[b]scale=1080:-2[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,ass={ass_name}[v]")
    out = out_dir / f"{name}.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(video.resolve()),
           "-t", f"{end - start:.2f}", "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out.name]
    r = subprocess.run(cmd, cwd=out_dir, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Cutting the clip failed: {r.stderr.strip()[-400:]}")
    thumb = out_dir / f"{name}.jpg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", out.name, "-frames:v", "1",
                    "-vf", "scale=360:-2", thumb.name], cwd=out_dir, capture_output=True)
    return out, thumb
