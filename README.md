# Agent HQ

An AI clipping team that runs on your own server.

- **Clip Studio** (`clips.` address): add a long video by link or upload. The agents transcribe it, pick the best moments, and cut vertical 9:16 clips with burned-in captions. You approve each clip, then download it and post it.
- **n8n** (`n8n.` address): automations, like the Scout workflow that sends a channel's new uploads to Clip Studio every 6 hours.

| Agent (floor 1) | What runs it |
|---|---|
| Scout | n8n workflow `n8n/youtube-channel-scout.json` |
| Transcriber | faster-whisper, running on the server |
| Editor-in-Chief | Claude API (falls back to a simple pick if no key is set) |
| Cutter | ffmpeg: 9:16, captions, watermark |
| Rules checker + approvals | You, on the Review page |
| Poster | You, from your phone or tablet apps, for now |

## Install

On a fresh Ubuntu 24.04 server (4 GB RAM or more), as root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/goodvibesncreations-hue/agent-hq/main/setup.sh)
```

It asks for your Anthropic API key and a Clip Studio password, then prints your two web addresses. Running the same command again updates everything and keeps your settings.

## Everyday use

1. Open Clip Studio and paste a video link, or upload a file.
2. Wait. Expect roughly 10–20 minutes per hour of video on a 4 GB server.
3. **Review:** watch each clip, then tap Approve or Reject.
4. **Approved:** download the clip, copy the caption, post it, then tap **Mark as posted**.

## Settings (`/opt/agent-hq/.env`)

| Setting | Default | Meaning |
|---|---|---|
| `WHISPER_MODEL` | `base` | `small` is more accurate but about 3x slower |
| `DEFAULT_CLIPS` | `5` | clips per video |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | model that picks the moments |
| `KEEP_SOURCE` | `false` | keep the long video after clipping |

After editing: `cd /opt/agent-hq && docker compose up -d`. To change the API key: `bash /opt/agent-hq/set-key.sh`.

## Using the Scout workflow

In n8n, go to **Workflows → Import from file**, then choose `n8n/youtube-channel-scout.json`. Paste the channel ID and campaign name, then activate it. Clip Studio skips videos it has already seen.

## Troubleshooting

- **"Sign in to confirm you're not a bot"**: YouTube sometimes blocks server downloads. Upload the file instead, or export your YouTube cookies to `/opt/agent-hq/data/cookies.txt`.
- **A job failed:** the error shows under the job. Tap Retry.
- **Logs:** `cd /opt/agent-hq && docker compose logs -f clipper`

## API (for n8n)

From inside the server, the base address is `http://clipper:8000`.

- `POST /api/jobs` with `{"url", "campaign", "clips", "mode": "blur|crop", "watermark", "tag", "rules"}`. Returns 409 if the video was already added.
- `GET /api/clips?status=pending|approved|posted`
- `POST /api/clips/{id}/approve|reject|posted`
- `GET /api/status`

Only clip videos you have permission to clip, such as campaign content.
