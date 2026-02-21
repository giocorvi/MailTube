# MailTube

Less algo slop, more control.

## Get a YouTube API Key

MailTube needs `YOUTUBE_API_KEY` to refresh videos.

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or select an existing one).
3. Enable **YouTube Data API v3** for that project.
4. Go to **APIs & Services → Credentials**.
5. Create an **API key** and copy it.
6. Export it in your shell:

```bash
export YOUTUBE_API_KEY=your_key_here
```

## Start the App

```bash
uv run mail-tube start
```

Then open the printed local URL (default: `http://127.0.0.1:8000`).

## Direct watch mode

```bash
uv run mail-tube watch https://www.youtube.com/watch?v=dQw4w9WgXcQ
```