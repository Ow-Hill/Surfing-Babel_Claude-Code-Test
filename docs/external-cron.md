# Reliable hourly scans with an external cron

> **Optional now.** Each scan run dispatches the next one itself (see the README's Hosting
> section), so scanning is continuous without this. An external cron only helps restart the
> relay sooner if it ever stops.

GitHub's own `schedule:` trigger is best-effort. On this repository it has fired only a few
times a day, sometimes not at all, so most scans came from the **Scan now** button. A free
external cron service can start the same workflow on time, through GitHub's
`workflow_dispatch` API. GitHub's schedule stays on as a backup. The workflow's `concurrency`
group queues overlapping runs, so a run that both triggers start is harmless.

Nothing here goes into the repository or the site. The token lives only in the cron service.

## 1. Create a token (GitHub)

1. Go to GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained
   tokens → Generate new token**.
2. Fill in the form:
   - **Name:** `surfing-babel cron`. **Expiration:** your choice. The service stops working when it
     expires, so set a reminder.
   - **Repository access:** *Only select repositories* → `Ow-Hill/Surfing-Babel_Claude-Code-Test`.
   - **Permissions → Repository → Actions:** **Read and write**. Nothing else.
3. Generate the token and copy it (it starts with `github_pat_`).

## 2. Create the job (cron-job.org)

1. Sign up at <https://cron-job.org> (free) and choose **Create cronjob**.
2. Fill in the job:
   - **URL:**
     `https://api.github.com/repos/Ow-Hill/Surfing-Babel_Claude-Code-Test/actions/workflows/scan-and-publish.yml/dispatches`
   - **Schedule:** every hour at minute **41**. This is offset from GitHub's own `:23`, so the
     two rarely collide.
   - **Advanced → Request method:** `POST`.
   - **Advanced → Headers:**
     - `Authorization: Bearer <your token>`
     - `Accept: application/vnd.github+json`
     - `X-GitHub-Api-Version: 2022-11-28`
     - `Content-Type: application/json`
   - **Advanced → Request body:** `{"ref":"main","inputs":{"minutes":"45"}}`
3. Save, then press **Test run**. GitHub answers **204 No Content** when it accepts the request.
   A new "Scan Babel and publish site" run appears under the repository's **Actions** tab within
   a few seconds.

## Troubleshooting

| response | meaning |
|---|---|
| 204 | started |
| 401 | the token is wrong or expired |
| 403 / 404 | the token can't see this repository or lacks *Actions: write* |
| 422 | the body is malformed (check `ref` and that `minutes` is a string) |

Sanity check with curl (replace the token):

```bash
curl -i -X POST -H "Authorization: Bearer github_pat_..." -H "Accept: application/vnd.github+json" https://api.github.com/repos/Ow-Hill/Surfing-Babel_Claude-Code-Test/actions/workflows/scan-and-publish.yml/dispatches -d '{"ref":"main","inputs":{"minutes":"45"}}'
```

A 45-minute scan takes up to about 55 minutes with the build and publish, so hourly runs barely
overlap. Actions minutes are free for public repositories.
