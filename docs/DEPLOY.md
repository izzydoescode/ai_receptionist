# Deploying Bear

Nothing here has been run against a real account yet. There's no Fly app and no Neon project. This page is the checklist for when you're ready.

## Where to host it
| Option | Cost (checked 2026-11-14) | Fit | Tradeoffs |
|---|---|---|---|
| **Fly.io**, one shared-cpu-1x 256–512 MB machine in `iad`/`ewr`, plus **Neon** free Postgres | $2.19–3.69/mo; bills under $5 are waived once a card is on file. Neon free tier (1 GB per project, scales to zero) | **Recommended** | Always on, so the ElevenLabs init webhook never hits a cold start. Fly needs a card. Neon's compute sleeps after 5 min idle and takes a few hundred ms to wake (see "Neon cold start" below). |
| **Render** web service plus Neon | The free tier spins down after 15 min idle, with about a 1 min cold start. Paid Starter is about $7/mo (*price not re-verified*) | OK on paid only | On the free tier, the cold start would make the init webhook and first tool call time out mid-ring. Render's free Postgres expires after 30 days, so use Neon. |
| **Netlify Functions** | Free tier | **Poor fit** | Only JS, TS and Go, so the FastAPI backend would need a rewrite. Serverless cold starts. |
| Vercel (Python functions) | Hobby tier is free | Not for pilots | Hobby is for non-commercial use. Serverless means no SQLite. |
| Supabase Postgres (instead of Neon) | Free: 500 MB, 2 projects | Fine | Free projects pause after a week with no activity, which is risky for a quiet shop. |

Sources: https://docs.fly.io/about/pricing, https://docs.fly.io/about/free-waiver, https://neon.com/faqs/free-plan-limits-and-quotas, https://render.com/docs/free, https://docs.netlify.com/build/functions/get-started, https://supabase.com/pricing.

## The image
`Dockerfile` builds a two-stage image on `python:3.13-slim-trixie`, pinned by digest; Dependabot proposes digest bumps. The image:
- installs runtime deps from `backend/requirements.lock` with `pip install --require-hashes`
- runs as the non-root user `bear` (uid 10001)
- serves `uvicorn --factory main:create_app --proxy-headers` on port **8080**
- has a `HEALTHCHECK` that probes `/health`
- runs `docker-entrypoint.sh`, which applies `alembic upgrade head` before starting, but only when `BEAR_MIGRATE_ON_BOOT=1`

On Fly, migrations run as a release command instead.

CI (`.github/workflows/container.yml`) builds the image on every push and runs it against SQLite and Postgres. It checks that the health check turns healthy, that `/health` answers, that the process isn't root, and that tool calls without the secret get a 401.

### Run it locally
```bash
docker build -t bear .
python -c "import secrets; [print(f'{name}={secrets.token_urlsafe(32)}') for name in ('TOOL_SECRET', 'INIT_WEBHOOK_SECRET', 'ADMIN_PASSWORD')]" > .env.docker
docker run --rm -p 8080:8080 --env-file .env.docker -e BEAR_ENV=dev -e BEAR_MIGRATE_ON_BOOT=1 -v bear-data:/data bear
curl http://localhost:8080/health
curl http://localhost:8080/ready
```
`.env.docker` matches `.env.*` in `.gitignore` and `.dockerignore`, so it never gets committed or baked into the image. With no `DATABASE_URL`, the app uses SQLite at `/data/bear.db`, kept in the `bear-data` volume. `create_app` refuses a `sqlite:` URL unless `BEAR_ENV=dev`, so the local run sets that explicitly.

## Environment variables
| Variable | Where it's set on Fly | Required | What it does |
|---|---|---|---|
| `TOOL_SECRET` | `fly secrets set` | yes | Shared secret ElevenLabs sends in `X-Tool-Secret` on every `/tools/*` call. At least 32 characters. Store the same value as an ElevenLabs workspace secret (`tools/README.md`). |
| `INIT_WEBHOOK_SECRET` | `fly secrets set` | yes | Shared secret ElevenLabs sends in `X-Init-Webhook-Secret` to `POST /elevenlabs/init`. At least 32 characters, and different from `TOOL_SECRET` (`docs/ELEVENLABS_INIT.md`). |
| `INIT_WEBHOOK_SECRET_PREVIOUS` | `fly secrets set` (optional) | no | Previous init secret, also accepted during rotation so you can update ElevenLabs and the server in either order. If set, same 32-character and not-`TOOL_SECRET` rules. |
| `POST_CALL_WEBHOOK_SECRET` | `fly secrets set` | for post-call summaries | The HMAC secret ElevenLabs shows when you create the post-call webhook (`docs/ELEVENLABS_POST_CALL.md`). If unset, `/elevenlabs/post-call` returns 503 and the app still starts. It must differ from the other secrets. |
| `ALERTS_MODE` | `fly secrets set` (or `fly.toml` `[env]`) | no | `off`, `log` (default; nothing delivered) or `email`. Use `email` in production so staff actually hear about bookings, messages and tows. |
| `ALERTS_EMAIL_API_KEY` | `fly secrets set` | with `ALERTS_MODE=email` | Resend API key. Startup fails in email mode without it. |
| `ALERTS_EMAIL_FROM` | `fly secrets set` | with `ALERTS_MODE=email` | Sender on a domain verified in Resend, e.g. `Bear <alerts@your-domain>`. Recipients come from the shop file's `alerts.email`. |
| `DATABASE_URL` | `fly secrets set` | yes on Fly | The Neon connection string, `postgresql://…?sslmode=require`. `postgres://` and `postgresql://` are turned into `postgresql+psycopg://` automatically. The image's default is `sqlite:////data/bear.db`. `create_app` refuses a `sqlite:` URL unless `BEAR_ENV=dev`, so a forgotten `DATABASE_URL` on Fly fails startup instead of writing a throwaway file. |
| `ADMIN_PASSWORD` | `fly secrets set` | for `/admin` | HTTP Basic password for the board. If unset, `/admin` returns 503. |
| `ADMIN_USER` | `fly secrets set` (optional) | no | HTTP Basic username. Default `admin`. |
| `SHOP_ID` | `fly.toml` `[env]` | no | Default shop for tool calls. Default `vice-city-auto`. It must have a file in `SHOPS_DIR`. |
| `SHOPS_DIR` | `fly.toml` `[env]` or a secret | no | Folder of shop YAML files. Default: the image's `/app/shops` (only the demo shop). See "Real shop files" below. |
| `TOOL_DEADLINE_MS` | `fly.toml` `[env]` | no | Tool request deadline. Default `4000`. |
| `BEAR_MIGRATE_ON_BOOT` | `fly.toml` `[env]` = `0` | no | `1` runs `alembic upgrade head` on container start. Fly uses its release command instead. |
| `FORWARDED_ALLOW_IPS` | `fly.toml` `[env]` = `*` | no | Which proxies uvicorn trusts for `X-Forwarded-*`. The image default is `127.0.0.1`. On Fly, only Fly's proxy reaches the app's port. |
| `BEAR_ENV` | never | no | `dev` turns off the secret checks, enables `POST /admin/reset`, and is the only environment that may use a `sqlite:` `DATABASE_URL`. **Never set it in production.** |
| `PUBLIC_BASE_URL` | `fly secrets set` (optional) and on your machine | for `tools/make_tools.py` | The app's public URL, e.g. `https://bear-receptionist.fly.dev`. On your machine it generates the tool configs; on the server it adds the `/admin` link to alert emails. |

## Fly.io + Neon setup (when ready; not done yet)
### Neon
1. Sign up at https://neon.com (free plan), create a project in **AWS US East (N. Virginia)**, close to Fly's `iad`, and name the database `bear`.
2. Under Connect, copy the **direct** (non-pooled) connection string. One small machine doesn't need the pooler, and the direct string avoids PgBouncer quirks with psycopg prepared statements. It looks like `postgresql://<user>:<password>@<endpoint>.us-east-1.aws.neon.tech/bear?sslmode=require&channel_binding=require`.
3. **Neon cold start:** after 5 idle minutes the compute sleeps, and the next query takes a few hundred ms longer. The app already reconnects (`pool_pre_ping`). Fly's check probes `/health`, which doesn't touch the database, so those checks don't keep Neon awake. That's accepted for now. External uptime monitors can hit `/ready`, which runs `SELECT 1` and returns 503 if the database is down; don't point Fly's check at `/ready`, or a sleeping Neon would mark the machine unhealthy. If first calls of the day feel slow, a scheduled ping would be a follow-up.

### Fly
1. Install `flyctl`, run `fly auth signup` or `fly auth login`, and add a card (bills under $5 are waived).
2. From the repo root, create the app without deploying. If `bear-receptionist` is taken, change `app` in `fly.toml`.
   ```bash
   fly apps create bear-receptionist
   ```
3. Set the secrets. Generate fresh random values into shell variables (never reuse the dev ones), print them once so you can copy them, then import them on stdin. Don't generate the value inside `$(...)` on a `fly secrets set` line — you would never see it, and it can also land in your shell history:
   ```bash
   TOOL_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
   INIT_WEBHOOK_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
   ADMIN_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
   echo "save these now:"
   echo "TOOL_SECRET=$TOOL_SECRET"
   echo "INIT_WEBHOOK_SECRET=$INIT_WEBHOOK_SECRET"
   echo "ADMIN_PASSWORD=$ADMIN_PASSWORD"
   fly secrets import --stage <<EOF
   TOOL_SECRET=$TOOL_SECRET
   INIT_WEBHOOK_SECRET=$INIT_WEBHOOK_SECRET
   ADMIN_PASSWORD=$ADMIN_PASSWORD
   EOF
   fly secrets set --stage DATABASE_URL="<Neon direct connection string>"
   fly secrets set --stage POST_CALL_WEBHOOK_SECRET="<secret ElevenLabs shows for the post-call webhook>"
   fly secrets set --stage ALERTS_MODE=email ALERTS_EMAIL_API_KEY="<Resend API key>" ALERTS_EMAIL_FROM="Bear <alerts@your-verified-domain>" PUBLIC_BASE_URL=https://bear-receptionist.fly.dev
   ```
   `--stage` stores them without restarting anything. Copy `TOOL_SECRET` and `INIT_WEBHOOK_SECRET` into ElevenLabs right away (step 6). Fly won't show them again, so if you lose them, set new ones. During a later rotation you can also set `INIT_WEBHOOK_SECRET_PREVIOUS` the same way so both secrets are accepted.
4. Deploy: `fly deploy`. The release command runs `alembic upgrade head` against Neon first. Then one machine starts in `iad` and must pass the `/health` check.

   The cheap default is one always-on machine (`min_machines_running = 1`, rolling deploys). Two machines plus a bluegreen deploy would avoid downtime on releases, at roughly double the Fly compute cost, but a Fly volume can only attach to one machine at a time — so if you later mount shop files on a volume, you stay on one machine and rolling/bluegreen can't run two copies that share it. Revisit this before the first real shop file if zero-downtime deploys matter more than the extra monthly cost.
5. Check it: `curl https://bear-receptionist.fly.dev/health` and `curl https://bear-receptionist.fly.dev/ready`, then open `https://bear-receptionist.fly.dev/admin` and log in as `admin` with the password you set. Optional demo data: `fly ssh console -C "python -m bear.seed --demo"`.
6. Point ElevenLabs at it:
   - Init webhook: `https://bear-receptionist.fly.dev/elevenlabs/init`, with secret header `X-Init-Webhook-Secret` (`docs/ELEVENLABS_INIT.md`).
   - Tools: run `PUBLIC_BASE_URL=https://bear-receptionist.fly.dev python tools/make_tools.py`, then follow `tools/README.md`. That includes the workspace secret that holds `TOOL_SECRET`.
   - Post-call webhook: `https://bear-receptionist.fly.dev/elevenlabs/post-call` with HMAC authentication, transcription events only and audio off (`docs/ELEVENLABS_POST_CALL.md`). Put the secret it shows in `POST_CALL_WEBHOOK_SECRET`.

### Real shop files
Real shop YAML (real names and numbers) must never go into this repo. The image ships only the demo shop, whose numbers are fictional. **The demo launch needs this step too:** the init webhook finds the shop by the dialed number, so copy `shops/vice-city-auto.yaml` and set `twilio_number` to your real Twilio number, `transfer_number` to the owner's cell, `voices` to your ElevenLabs voice ids, and `alerts.email` to the staff inbox. For any deployed shop file:
1. `fly volumes create bear_data --region iad --size 1`, and add `[mounts] source = "bear_data"` / `destination = "/data"` to `fly.toml`.
2. Copy the shop file in with `fly ssh sftp shell` (`put shop.yaml /data/shops/<shop_id>.yaml`).
3. `fly secrets set SHOPS_DIR=/data/shops SHOP_ID=<shop_id>`. This restarts the app, which validates every file at startup.
