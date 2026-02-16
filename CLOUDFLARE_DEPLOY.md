# Deploy VidX on Cloudflare

## 1) What can run on Cloudflare directly
- `webapp` (Next.js) can run on Cloudflare Workers using OpenNext.
- `backend` (Python + ffmpeg + long-running video jobs) should run outside Workers for now.

Recommended setup:
- Cloudflare: frontend (`webapp`)
- Any VM/container host for backend (`backend`) + optional Cloudflare Tunnel/domain in front

## 2) Frontend deploy (Cloudflare Workers)

From `/Users/himanshu/Desktop/VidX/webapp`:

```bash
npm install
npx wrangler login
NEXT_PUBLIC_API_BASE="https://api.your-domain.com" npm run cf:deploy
```

Notes:
- `NEXT_PUBLIC_API_BASE` must point to your backend public URL.
- Project scripts are already added in `webapp/package.json`:
  - `npm run cf:build`
  - `npm run cf:preview`
  - `npm run cf:deploy`

## 3) Backend deploy (recommended)

Run backend on a machine/container where Python + ffmpeg are available:

```bash
cd /Users/himanshu/Desktop/VidX
python3 -m venv .venv311
source .venv311/bin/activate
pip install -r backend/requirements.txt
export VIDX_ALLOWED_ORIGINS="https://your-frontend-domain"
uvicorn backend.app:app --host 0.0.0.0 --port 8010
```

Set env vars in `backend/.env` (never commit this file):

```bash
ELEVENLABS_API_KEY=your_key_here
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
```

## 4) Connect backend to Cloudflare domain (optional but recommended)

Use Cloudflare Tunnel from the backend host:

```bash
cloudflared tunnel login
cloudflared tunnel create vidx-backend
cloudflared tunnel route dns vidx-backend api.your-domain.com
cloudflared tunnel run vidx-backend
```

Then set frontend API base to:
- `https://api.your-domain.com`

## 5) Quick production checklist
- Keep `backend/.env` out of git.
- Keep `backend_data/` on persistent disk (or move to object storage later).
- Enable HTTPS on both frontend and backend domains.
- Restrict CORS with `VIDX_ALLOWED_ORIGINS`.
