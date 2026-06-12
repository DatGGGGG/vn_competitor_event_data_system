# Ubuntu VM Deployment with Docker Compose

This guide is the recommended deployment path for a **shared Ubuntu VM**.

It isolates the event API from other projects on the same machine by running:

- one `api` container
- one `ngrok` container

The SQLite database stays on the VM host and is mounted read-only into the API container.

## Deployment Assets

- [Dockerfile](/C:/Users/VEE0634/Desktop/Coding/vn_competitor_event_data_system/Dockerfile)
- [docker-compose.yml](/C:/Users/VEE0634/Desktop/Coding/vn_competitor_event_data_system/deploy/docker/docker-compose.yml)
- [vm.env.example](/C:/Users/VEE0634/Desktop/Coding/vn_competitor_event_data_system/deploy/docker/vm.env.example)

## Recommended VM Layout

- repo: `/opt/vn_event_dw/vn_competitor_event_data_system`
- host DB directory: `/opt/vn_event_dw/data`
- DB file: `/opt/vn_event_dw/data/warehouse.db`
- compose env file: `/opt/vn_event_dw/vn_competitor_event_data_system/deploy/docker/vm.env`

## 1. Install Docker and Compose

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Optional: allow your SSH user to run Docker without `sudo`:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

## 2. Clone the Repo

```bash
sudo mkdir -p /opt/vn_event_dw
sudo chown -R "$USER":"$USER" /opt/vn_event_dw
cd /opt/vn_event_dw
git clone https://github.com/DatGGGGG/vn_competitor_event_data_system.git
```

## 3. Prepare the Host Database Directory

```bash
mkdir -p /opt/vn_event_dw/data
```

Copy the database from your local machine to the VM host:

```bash
scp /path/to/warehouse.db <vm-user>@<vm-host>:/tmp/warehouse.db
```

Then on the VM:

```bash
mv /tmp/warehouse.db /opt/vn_event_dw/data/warehouse.db
chmod 644 /opt/vn_event_dw/data/warehouse.db
```

## 4. Create the Compose Env File

```bash
cd /opt/vn_event_dw/vn_competitor_event_data_system
cp deploy/docker/vm.env.example deploy/docker/vm.env
nano deploy/docker/vm.env
```

Fill in at least:

```bash
HOST_DATA_DIR=/opt/vn_event_dw/data
NGROK_AUTHTOKEN=your_real_ngrok_token_here
NGROK_DOMAIN=april-refund-promoter.ngrok-free.dev
API_PORT=8765
NGROK_INSPECT_PORT=4040
```

## 5. Start the Stack

From the repo root on the VM:

```bash
cd /opt/vn_event_dw/vn_competitor_event_data_system
docker compose --env-file deploy/docker/vm.env -f deploy/docker/docker-compose.yml up -d --build
```

## 6. Check Container Health

```bash
docker compose --env-file deploy/docker/vm.env -f deploy/docker/docker-compose.yml ps
docker compose --env-file deploy/docker/vm.env -f deploy/docker/docker-compose.yml logs -f api ngrok
```

You want to see:

- API bound on `0.0.0.0:8765` inside the container
- ngrok connected successfully
- your reserved public URL attached

## 7. Smoke Test

On the VM host:

```bash
curl "http://127.0.0.1:8765/api/games?q=MLBB"
curl "https://april-refund-promoter.ngrok-free.dev/api/games?q=MLBB"
```

## Update Flow

### Code changes

After pushing new code to GitHub:

```bash
cd /opt/vn_event_dw/vn_competitor_event_data_system
git pull
docker compose --env-file deploy/docker/vm.env -f deploy/docker/docker-compose.yml up -d --build
```

### Database refresh

Copy a new `warehouse.db` onto the VM host, replacing the old file:

```bash
scp /path/to/warehouse.db <vm-user>@<vm-host>:/tmp/warehouse.db
mv /tmp/warehouse.db /opt/vn_event_dw/data/warehouse.db
```

Then restart the API container so it reopens the SQLite file:

```bash
cd /opt/vn_event_dw/vn_competitor_event_data_system
docker compose --env-file deploy/docker/vm.env -f deploy/docker/docker-compose.yml restart api
```

## Stop / Restart

Stop:

```bash
cd /opt/vn_event_dw/vn_competitor_event_data_system
docker compose --env-file deploy/docker/vm.env -f deploy/docker/docker-compose.yml down
```

Restart:

```bash
cd /opt/vn_event_dw/vn_competitor_event_data_system
docker compose --env-file deploy/docker/vm.env -f deploy/docker/docker-compose.yml up -d
```

## Why This Setup Is Better on a Shared VM

- container-level dependency isolation
- no shared Python virtualenv conflicts
- cleaner restarts and upgrades
- DB remains host-managed and easy to refresh with `scp` or `rsync`
- ngrok is isolated from the application runtime
