# Ubuntu VM Deployment

This guide deploys the read-only event API plus the persistent ngrok tunnel on an Ubuntu VM.

Deployment assets in this repo:

- [run_api_ngrok.sh](C:/Users/VEE0634/Desktop/Coding/vn_competitor_event_data_system/deploy/ubuntu/run_api_ngrok.sh)
- [vn-event-dw-api.env.example](C:/Users/VEE0634/Desktop/Coding/vn_competitor_event_data_system/deploy/ubuntu/vn-event-dw-api.env.example)
- [vn-event-dw-api-ngrok.service](C:/Users/VEE0634/Desktop/Coding/vn_competitor_event_data_system/deploy/ubuntu/vn-event-dw-api-ngrok.service)

## What This Deployment Covers

- Python virtual environment
- SQLite warehouse DB already built on the VM
- API served through:
  - `python -m vn_event_dw.cli serve-api-ngrok`
- persistent ngrok public URL
- `systemd` service so it survives reboot

## Recommended VM Layout

Example paths:

- repo: `/opt/vn_event_dw/vn_competitor_event_data_system`
- venv: `/opt/vn_event_dw/vn_competitor_event_data_system/.venv`
- database: `/opt/vn_event_dw/vn_competitor_event_data_system/data/warehouse.db`

## 1. Install OS Dependencies

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

## 2. Create a Service User

```bash
sudo useradd --system --create-home --home-dir /opt/vn_event_dw --shell /bin/bash vn-event || true
sudo mkdir -p /opt/vn_event_dw
sudo chown -R vn-event:vn-event /opt/vn_event_dw
```

## 3. Copy or Clone the Repo

As the `vn-event` user:

```bash
sudo -u vn-event -H bash -lc '
cd /opt/vn_event_dw
git clone <your-repo-url> vn_competitor_event_data_system
'
```

If the repo is already local elsewhere, you can also `rsync` it into `/opt/vn_event_dw/`.

## 4. Create the Virtual Environment

```bash
sudo -u vn-event -H bash -lc '
cd /opt/vn_event_dw/vn_competitor_event_data_system
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
'
```

## 5. Put the Database on the VM

If you already built `data/warehouse.db` locally, copy it to the VM:

```bash
sudo -u vn-event mkdir -p /opt/vn_event_dw/vn_competitor_event_data_system/data
sudo cp /path/to/warehouse.db /opt/vn_event_dw/vn_competitor_event_data_system/data/warehouse.db
sudo chown vn-event:vn-event /opt/vn_event_dw/vn_competitor_event_data_system/data/warehouse.db
```

If you want the VM to rebuild the database itself, also copy:

- FB input files
- Sensor Tower raw manifests if needed
- config JSON / env vars needed for ETL

## 6. Create the Runtime Env File

Copy the example:

```bash
sudo cp /opt/vn_event_dw/vn_competitor_event_data_system/deploy/ubuntu/vn-event-dw-api.env.example /etc/vn-event-dw-api.env
sudo chmod 600 /etc/vn-event-dw-api.env
```

Edit it:

```bash
sudo nano /etc/vn-event-dw-api.env
```

Minimum required values:

- `REPO_DIR`
- `VENV_DIR`
- `DB_PATH`
- `NGROK_AUTHTOKEN`
- `NGROK_DOMAIN`

Example:

```bash
REPO_DIR=/opt/vn_event_dw/vn_competitor_event_data_system
VENV_DIR=/opt/vn_event_dw/vn_competitor_event_data_system/.venv
DB_PATH=/opt/vn_event_dw/vn_competitor_event_data_system/data/warehouse.db
HOST=127.0.0.1
PORT=8765
NGROK_AUTHTOKEN=your_token_here
NGROK_DOMAIN=april-refund-promoter.ngrok-free.dev
```

## 7. Make the Launcher Executable

```bash
sudo chmod +x /opt/vn_event_dw/vn_competitor_event_data_system/deploy/ubuntu/run_api_ngrok.sh
```

## 8. Install the `systemd` Service

Copy the service file:

```bash
sudo cp /opt/vn_event_dw/vn_competitor_event_data_system/deploy/ubuntu/vn-event-dw-api-ngrok.service /etc/systemd/system/
```

Reload and enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable vn-event-dw-api-ngrok.service
sudo systemctl start vn-event-dw-api-ngrok.service
```

## 9. Check Service Health

Status:

```bash
sudo systemctl status vn-event-dw-api-ngrok.service
```

Logs:

```bash
sudo journalctl -u vn-event-dw-api-ngrok.service -f
```

You should see lines like:

- `Uvicorn running on http://127.0.0.1:8765`
- `ngrok_tunnel_url: https://<your-domain>.ngrok-free.dev`

## 10. Smoke Test the API

On the VM:

```bash
curl "http://127.0.0.1:8765/api/games?q=MLBB"
```

Public URL:

```bash
curl "https://<your-ngrok-domain>/api/games?q=MLBB"
```

## Upgrade / Redeploy Flow

After pulling new code:

```bash
sudo -u vn-event -H bash -lc '
cd /opt/vn_event_dw/vn_competitor_event_data_system
git pull
source .venv/bin/activate
pip install -e .
'
sudo systemctl restart vn-event-dw-api-ngrok.service
```

## If You Also Want ETL on the VM

The API service itself only needs the built DB and ngrok credentials.

If you also want the VM to run ETL jobs, set these in `/etc/vn-event-dw-api.env` or a separate ops env file:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_PROVIDER`
- `OPENAI_MODEL`
- `OPENAI_UNIFIED_EVENT_MERGE_MODEL`
- `SENSOR_TOWER_AUTH_TOKEN`
- `SENSOR_TOWER_BASE_URL`

Then run the same CLI commands already used locally, for example:

```bash
source /opt/vn_event_dw/vn_competitor_event_data_system/.venv/bin/activate
cd /opt/vn_event_dw/vn_competitor_event_data_system
python -m vn_event_dw.cli build-unified-events-llm --db data/warehouse.db
```

## Notes

- The public URL remains stable only if your reserved `NGROK_DOMAIN` stays attached to the account.
- The API serves directly from SQLite, so the DB file must be present and readable by `vn-event`.
- If you change API code or scoring logic, restart the `systemd` service after redeploy.
