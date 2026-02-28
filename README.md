<p align="center">
  <img src="resources/MailTube-logo_v0.png" alt="MailTube Logo" width="140">
</p>

<h1 align="center">MailTube</h1>

<p align="center"><strong>Less algorithm, less slop, more control.</strong></p>

<p align="center">MailTube is a personal inbox for your YouTube feed, so you can review new uploads on your terms.</p>

## Get a YouTube API Key

MailTube needs `YOUTUBE_API_KEY` to refresh videos.

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project (or select an existing one).
3. Enable **YouTube Data API v3** for that project.
4. Go to **APIs & Services → Credentials**.
5. Create an **API key** and copy it.
6. Configure it locally:

```bash
cp .mailtube.env.example .mailtube.env
# then edit .mailtube.env and set YOUTUBE_API_KEY
```

MailTube automatically loads `.mailtube.env` from the repo root.
If you also export `YOUTUBE_API_KEY` in your shell, the shell value takes precedence.

## Start the App

```bash
uv run mail-tube start
```

Then open the printed local URL (default: `http://127.0.0.1:8000`).

## Direct watch mode

```bash
uv run mail-tube watch https://www.youtube.com/watch?v=dQw4w9WgXcQ
```
