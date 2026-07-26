---
name: youtube-publishing
description: YouTube Data API v3 and Analytics API integration — OAuth flow, resumable video upload, scheduled publishing, captions, thumbnails, playlists, and quota management. Use when implementing or debugging anything that talks to Google/YouTube APIs, when designing around upload limits, or when building the publish/schedule UI.
---

# YouTube Publishing

## Quota — design around this first

Default project quota is **10,000 units/day**. It resets at midnight Pacific.

| Operation | Cost | Notes |
|---|---|---|
| `videos.insert` | **1,600** | the ceiling: ~6 uploads/day |
| `search.list` | **100** | expensive; cache results aggressively |
| `thumbnails.set` | 50 | |
| `captions.insert` | 400 | |
| `playlistItems.insert` | 50 | |
| `videos.update` | 50 | |
| `videos.list` | 1 | cheap; use freely |

Consequences that must shape the code:

- Maintain a **quota ledger table** — every API call writes its unit cost. Never estimate after the fact.
- Refuse to enqueue an upload that would exceed the remaining budget; surface it in the UI (Calendar screen shows the week's consumption).
- Competitor mining via `search.list` competes with uploads for the same budget. Cache competitor results for at least 7 days.
- A quota increase requires an audited application to Google. It takes weeks. Start it before Phase 7 ships if volume matters.

## OAuth

- Scopes: `youtube.upload`, `youtube.readonly`, `youtube.force-ssl` (needed for captions), `yt-analytics.readonly`.
- Desktop/web app flow with offline access to get a refresh token.
- **Store refresh tokens encrypted at rest**, keyed per channel. Never log them, never return them from an API endpoint, never include them in an error payload.
- Tokens expire when the user changes their password or revokes access — handle `invalid_grant` by marking the channel disconnected and prompting re-auth, not by retrying.
- Unverified apps are capped at 100 users and show a warning screen. Fine for personal use; verification needed if this ever ships to others.

## Upload

Use **resumable upload** (`uploadType=resumable`) always — a failed 500MB upload that restarts from zero burns 1,600 quota units for nothing.

```
POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status
```

`snippet`: `title`, `description`, `tags`, `categoryId`, `defaultLanguage`, `defaultAudioLanguage`
`status`: `privacyStatus`, `publishAt`, `selfDeclaredMadeForKids`, `license`, `embeddable`

Notes:
- `selfDeclaredMadeForKids` is **required** — omitting it is a common cause of rejected uploads.
- Chunk at 8MB or 16MB; report progress to the SSE stream.
- Retry on 5xx and 429 with exponential backoff, resuming from the last confirmed byte. Never retry 4xx.

## Scheduled publishing

Set `privacyStatus: "private"` **and** `publishAt` (RFC 3339, UTC) in the same request. Setting `publishAt` on an already-public video does nothing. Changing a schedule later is a `videos.update` — cheap, so allow drag-to-reschedule in the Calendar UI freely.

## After the upload

Each of these is a separate call and a separate failure mode. Treat them as individually retryable steps in the job, not as part of the upload:

1. `thumbnails.set` — 1280×720, ≤2MB, JPG.
2. `captions.insert` — upload the SRT as a real caption track. Burned-in subtitles do not help search; caption tracks do.
3. `playlistItems.insert` — add to the series playlist.
4. Localizations, if the series is multi-language.

## Analytics (Phase 8)

The YouTube Analytics API is a **separate API with its own quota** — much more generous. Pull daily:

- `views`, `impressions`, `impressionClickThroughRate`, `averageViewDuration`, `averageViewPercentage`, `subscribersGained`
- Dimension `elapsedVideoTimeRatio` for the retention curve — this is what powers the retention-map visualization

Data lags 24–48 hours. Never present the last two days as final; the UI should mark them provisional.

## Anti-patterns

- Polling `videos.list` in a tight loop to check processing status — use a backoff, it's 1 unit but rate limits still apply.
- Uploading then immediately setting the thumbnail — the video may still be processing. Retry with backoff.
- Storing the access token and assuming it lasts. It's an hour. Refresh on demand, don't schedule refreshes.
- Treating an upload failure as a job failure — the render is still good. Failures should be retryable at the publish step alone.
