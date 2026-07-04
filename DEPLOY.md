# Cloud Deployment

The app is cloud-ready as a Docker web service.

## Option A: Render

1. Push this repository to GitHub.
2. Create a new Render Web Service.
3. Select the `portfolio_guard` directory as the service root if your repository contains other projects.
4. Use Docker environment.
5. Start command is handled by `Dockerfile`.
6. Set environment variable:

```text
PORTFOLIO_GUARD_LIVE_DATA=1
ARK_API_KEY=...
ARK_MODEL=...
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

Render will provide a public `https://...onrender.com` URL.

## Option B: Any Docker Host

```bash
cd portfolio_guard
docker build -t portfolio-guard-agent .
docker run -p 8765:8765 \
  -e PORTFOLIO_GUARD_LIVE_DATA=1 \
  -e ARK_API_KEY="$ARK_API_KEY" \
  -e ARK_MODEL="$ARK_MODEL" \
  -e ARK_BASE_URL="${ARK_BASE_URL:-https://ark.cn-beijing.volces.com/api/v3}" \
  portfolio-guard-agent
```

Open:

```text
http://SERVER_IP:8765
```

## Option C: Volcengine ECS

1. Create an ECS instance.
2. Install Docker.
3. Copy the `portfolio_guard` directory to the server.
4. Build and run the Docker image with port `8765` open in the security group.
5. Put Ark credentials in environment variables, not in source code.

For production, put the service behind HTTPS and a domain name.
