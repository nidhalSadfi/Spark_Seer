# Deploying EmberGuard on one server (Google Cloud VM)

One VM runs four containers with Docker Compose (`docker-compose.prod.yml`):

| Container | Role | Public |
|---|---|---|
| `web` | Caddy: the dashboard and `https://<domain>/api/*` → API, automatic HTTPS | ports 80, 443 |
| `api` | FastAPI (read-only GETs, plus `/reports`) | no |
| `worker` | polls NASA FIRMS every 3 h, processing, 3-agent review, promotion | no |
| `db` | PostgreSQL 16 | no |

Sizing for the world dataset: 2 vCPU, **8 GB RAM**, 100 GB disk (GCP `e2-standard-2`, Ubuntu 24.04).

## 1. Create the VM (Google Cloud Console)

1. Compute Engine → VM instances → **Create instance**: name `emberguard`, a region near your users
   (e.g. `europe-west1`), machine **e2-standard-2**, boot disk **Ubuntu 24.04 LTS, balanced, 100 GB**,
   tick **Allow HTTP traffic** and **Allow HTTPS traffic**.
2. VPC network → IP addresses → **reserve** the VM's external IP as static.
3. At your domain registrar (e.g. Porkbun): DNS `A` record for `@` (and `www` if wanted) → that IP.
4. Open the VM with the **SSH** button.

## 2. Install Docker and get the code (on the VM)

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
git clone https://github.com/nidhalSadfi/Spark_Seer.git && cd Spark_Seer
git checkout claude/emberguard-firms-architecture-3b252e
```

## 3. Configure `.env` (on the VM; never commit it)

```bash
cp .env.example .env && nano .env
```

Set at least:

```bash
POSTGRES_PASSWORD=<long random password>     # openssl rand -hex 24
SITE_ADDRESS=yourdomain.com                  # or :80 until DNS points at the VM (plain HTTP)
REPORTS_ADMIN_TOKEN=<long random token>      # otherwise anyone can review/delete reports
FIRMS_MAP_KEY=<your key>
OPENROUTER_API_KEY=<your key>
OPENROUTER_MODELS=nvidia/nemotron-3.5-super-vl:free,inclusionai/ling-3.2-flash:free,nvidia/nemotron-3-ultra-550b-a55b:free
# World coverage (spec §16):
FIRMS_BBOX=world
PROCESSING_MIN_LAT=none
COUNTRY_BOUNDARY_FILE=
STATIC_MASK_FILE=data/world/static_mask.json
```

`DATABASE_URL` in `.env` is ignored by the stack (the compose file points it at the `db` container).
Optional: `METEOMATICS_USER` / `METEOMATICS_PASSWORD` for the `/api/meteomatics/*` weather layer.

## 4. Copy the world dataset to the VM

Four files (about 53 MB) from a machine that has them, into `backend/data/world/` on the VM:

```bash
# from your computer (gcloud CLI), or upload them with the SSH window's "Upload file" button
gcloud compute scp backend/data/world/{harmonized.parquet,harmonized_validation.json,inter_viirs_fit.json,static_mask.json} \
  emberguard:~/Spark_Seer/backend/data/world/ --zone <your-zone>
```

## 5. Start, migrate, load the data

```bash
docker compose -f docker-compose.prod.yml up -d --build db api web
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
docker compose -f docker-compose.prod.yml run --rm api python -m worker.bootstrap \
  --data data/world/harmonized.parquet --validation data/world/harmonized_validation.json \
  --inter-viirs data/world/inter_viirs_fit.json --mask data/world/static_mask.json
docker compose -f docker-compose.prod.yml restart api
docker compose -f docker-compose.prod.yml up -d worker   # first world poll: ~1 h (30-day backfill)
```

Open `https://yourdomain.com/dashboard`. Check: `curl -s https://yourdomain.com/api/health` shows
`"data_source": "database"`. The Processing Center fills in once the worker's first poll finishes.

## Operating it

```bash
docker compose -f docker-compose.prod.yml ps                 # status
docker compose -f docker-compose.prod.yml logs -f worker     # follow the worker
git pull && docker compose -f docker-compose.prod.yml up -d --build   # update
docker compose -f docker-compose.prod.yml exec db pg_dump -U emberguard emberguard | gzip > backup-$(date +%F).sql.gz
```

Back up nightly (cron the `pg_dump` line) and keep copies off the VM. The database grows by roughly
1-2 GB a month with world polling.
