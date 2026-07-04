# GitHub Deployment Path

Do not push the parent trading-bot workspace. It contains local account files,
logs, caches, and `.env` files. Push only this `portfolio_guard` directory as a
standalone repository.

## 1. Create GitHub Repo

Create an empty GitHub repository, for example:

```text
portfolio-guard-agent
```

Public is easiest for hackathon judges. Private also works if your cloud deploy
platform has access.

## 2. Push This Folder Only

From the parent workspace:

```bash
cd portfolio_guard
git init
git add .
git commit -m "Initial Portfolio Guard Agent demo"
git branch -M main
git remote add origin git@github.com:<your-user>/portfolio-guard-agent.git
git push -u origin main
```

If you use HTTPS instead of SSH:

```bash
git remote add origin https://github.com/<your-user>/portfolio-guard-agent.git
```

## 3. Deploy From GitHub

GitHub itself cannot run this Python server as a normal GitHub Pages site.
Use a web service platform that can deploy from GitHub, such as Render, Railway,
Fly.io, or any Docker-capable cloud.

Recommended fastest route:

1. Open Render.
2. New Web Service.
3. Connect the GitHub repo.
4. Environment: Docker.
5. Set environment variables:

```text
PORTFOLIO_GUARD_LIVE_DATA=1
ARK_API_KEY=<your Ark key>
ARK_MODEL=<your Ark model or endpoint id>
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

Render will produce a public HTTPS URL for judges.

