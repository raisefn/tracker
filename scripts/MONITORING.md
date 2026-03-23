# Uptime Monitoring Setup

## Health Endpoints

| Service   | URL |
|-----------|-----|
| Dashboard | https://dashboard-justinpetsches-projects.vercel.app/api/health |
| Brain     | https://brain-production-61da.up.railway.app/health |
| Tracker   | https://api-production-5f7b.up.railway.app/health |

All return JSON with `status: "ok"` when healthy.

## Recommended Services (Free Tier)

- **UptimeRobot** — free, 50 monitors, 5-min intervals (upgradable to 1-min)
- **BetterStack** — free, 5 monitors, 3-min intervals, nice incident pages

Either works. UptimeRobot gives more monitors; BetterStack has a cleaner UI.

## Setup Steps

1. Create an account on UptimeRobot or BetterStack.
2. Add 3 HTTP(s) monitors, one per endpoint above.
3. Set check interval to 60 seconds (or lowest available on free tier).
4. Add an alert contact: `justin@raisefn.com` (email + SMS if supported).
5. Verify each monitor shows "Up" after creation.

## Note on Railway Built-in Healthchecks

Railway has its own healthcheck system, but it only restarts the service on failure — it does not send alerts. External monitoring is needed so you actually know when something goes down.
