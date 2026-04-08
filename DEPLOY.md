# retention.sh Backend — Deployment Guide

Deploy the FastAPI backend to any free-tier cloud platform using Docker.

---

## Quick Deploy

### Render (recommended)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/HomenShum/retention)

Or manually:
1. Go to [render.com/deploy](https://render.com/deploy)
2. Connect the repo `https://github.com/HomenShum/retention`
3. Render auto-detects `render.yaml` and configures everything
4. Set environment variables (OPENAI_API_KEY at minimum)
5. Deploy

### Railway

1. Go to [railway.app/new](https://railway.app/new)
2. Select **Deploy from GitHub repo**
3. Pick `HomenShum/retention`
4. Set root directory to `backend/`
5. Railway reads `railway.json` automatically
6. Add env vars in the dashboard: `OPENAI_API_KEY`, `RETENTION_MCP_TOKEN`, etc.

### Fly.io

```bash
# Install flyctl: https://fly.io/docs/flyctl/install/
fly auth login
fly launch --config fly.toml --no-deploy
# Set secrets
fly secrets set OPENAI_API_KEY=sk-...
fly secrets set RETENTION_MCP_TOKEN=your-token
# Deploy
fly deploy
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key for agents |
| `RETENTION_MCP_TOKEN` | No | MCP authentication token |
| `SLACK_BOT_TOKEN` | No | Slack bot for notifications |
| `CONVEX_SITE_URL` | No | Convex backend URL |
| `ALLOWED_ORIGINS` | No | CORS origins (comma-separated) |
| `DEVICE_PROVIDER` | No | `auto`, `local`, `genymotion`, `browserstack` |
| `GENYMOTION_API_TOKEN` | No | For cloud device testing |
| `CRON_AUTH_TOKEN` | No | Scheduled task auth |

---

## Local Docker Build

```bash
cd backend
docker build -t retention-backend .
docker run -p 8000:8000 \
  -e OPENAI_API_KEY=sk-... \
  retention-backend
```

Verify: `curl http://localhost:8000/api/health`

---

## Health Check

All platforms use `GET /api/health` to verify the service is running.

---

## Notes

- Free tiers spin down after ~15 min of inactivity; first request after sleep takes ~30s
- The Dockerfile uses `python:3.11-slim` for a small image (~200MB)
- `$PORT` is set automatically by all three platforms
- For production, upgrade from free tier for always-on instances
