# Bayse Whale Radar

Watches filled buy/sell orders on [Bayse Markets](https://www.bayse.markets) and
posts a formatted alert to X whenever an order is worth **more than ₦100,000**.

The default event is the 2027 Nigerian presidential election
(`c03ea595-a209-4fe9-a0e1-bd77f3154927`), but any number of event UUIDs can be
watched.

## What was broken

The old code had two separate problems:

1. `MARKET_ID` did not exist, so every connection crashed with `NameError`.
2. Even after that was fixed, the connection was reset every 20 seconds:

   ```
   ConnectionClosedError: no close frame received or sent
   OSError: [WinError 64] The specified network name is no longer available
   ```

   The cause is the WebSocket keepalive. `websockets.connect(ping_interval=20)`
   makes the client send a **client-originated ping frame** at 20 seconds, and
   Bayse immediately resets the socket. Sending the documented application-level
   `{"type": "ping"}` has the same effect on the live server. A live probe
   confirmed that an idle connection with no client pings stays open fine.

This rewrite sets `ping_interval=None`, never sends `{"type": "ping"}`, and lets
the server's own ping frames be answered automatically by the `websockets`
library. It also handles `SIGINT`/`SIGTERM` so Ctrl+C closes the socket cleanly
instead of entering the reconnect loop.

## How it works

```
Bayse activity WebSocket (global + per-event feeds)
        │  buy_order / sell_order
        ▼
  parse + validate FILLED
        │
        ▼
  event filter (default: the configured event)
        │
        ▼
  convert order value to NGN
        │  (live USD/NGN rate, 6-hour refresh)
        ▼
  value > MIN_ORDER_NGN ?
        │
        ▼
  SQLite idempotency check (order ID)
        │
        ▼
  format tweet (≤ 280 weighted chars)
        │
        ▼
  Tweepy Client.create_tweet()
```

```
bot/
├── app.py                    # wiring, supervision, graceful shutdown
├── config.py                 # .env settings
├── detector.py               # threshold + de-duplication + posting
├── formatting.py             # tweet layout and length handling
├── fx.py                     # USD/NGN rate provider
├── models.py                 # OrderActivity
├── store.py                  # SQLite idempotency store
├── twitter.py                # Tweepy wrapper (retries, rate limits, dry-run)
├── websocket/
│   ├── client.py             # resilient Bayse stream (no client pings)
│   └── websocket.py          # `python -m bot.websocket.websocket`
└── whale_detector/
    └── activity.py           # activity payload parser
```

## Setup

1. Install dependencies:

   ```powershell
   pipenv install
   ```

2. Create your environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Add your X/Twitter credentials to `.env`.

   In the [X developer portal](https://developer.x.com/), create an app with
   **Read and write** permission. After enabling write access, regenerate the
   **Access Token and Secret** — tokens created before the permission change
   cannot post and return `403 Forbidden`.

   ```env
   TWITTER_API_KEY=...
   TWITTER_API_SECRET=...
   TWITTER_ACCESS_TOKEN=...
   TWITTER_ACCESS_TOKEN_SECRET=...
   ```

4. Run the bot:

   ```powershell
   pipenv run python -m bot.websocket.websocket
   ```

   `pipenv run python -m bot` works too.

If the four Twitter credentials are missing, the bot automatically starts in
**dry-run** mode: it still monitors Bayse and prints the exact tweet it would
have sent. Set `TWITTER_DRY_RUN=false` once credentials are configured if you
want to make that explicit.

## Example alert

```
🐋 WHALE ALERT

Who will win the 2027 Presidential Election?
Bola Tinubu · YES

🟢 BUY · 150 shares @ 71%
💵 $100.00 (≈₦135,236)
👤 @prediction_pro

https://www.bayse.markets/market/who-will-win-the-2027-presidential-election
#Bayse #PredictionMarkets
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `BAYSE_EVENT_IDS` | the 2027 election event | Comma-separated event UUIDs |
| `BAYSE_ALL_MARKETS` | `false` | `false`: only the event IDs above; `true`: every Bayse market |
| `MIN_ORDER_NGN` | `100000` | Alert when NGN value is **strictly above** this |
| `USD_NGN_FALLBACK` | `1500` | Rate used if the live FX lookup fails |
| `FX_REFRESH_SECONDS` | `21600` | USD/NGN refresh interval (6 hours) |
| `TWITTER_API_KEY` | — | X API key |
| `TWITTER_API_SECRET` | — | X API secret |
| `TWITTER_ACCESS_TOKEN` | — | X user access token |
| `TWITTER_ACCESS_TOKEN_SECRET` | — | X user access secret |
| `TWITTER_DRY_RUN` | auto | Force dry-run on/off |
| `STATE_DB` | `data/whale_radar.sqlite3` | Idempotency store |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `RECONNECT_BASE_SECONDS` | `5` | Initial reconnect delay |
| `RECONNECT_MAX_SECONDS` | `60` | Maximum backoff delay |
| `RECONNECT_MAX_ATTEMPTS` | `0` | `0` retries forever |
| `QUEUE_SIZE` | `1000` | Buffered activity messages |

### Currency handling

Bayse orders can be denominated in USD or NGN. NGN orders are compared
directly; USD orders are converted using the live rate from
[open.er-api.com](https://open.er-api.com/) and refreshed every six hours. If the
lookup fails, `USD_NGN_FALLBACK` is used so alerts keep working. Unsupported
currencies are logged and skipped.

Only orders with status `FILLED` are considered, so open or cancelled orders do
not trigger alerts.

### Market scope

The bot subscribes to both Bayse's global activity feed and the documented
per-event activity rooms. The global feed makes delivery resilient; the
per-event rooms guarantee the configured event is covered. Duplicate frames are
discarded by order ID.

By default (`BAYSE_ALL_MARKETS=false`) orders from other events are ignored, so
the bot only tweets about `BAYSE_EVENT_IDS`. Set `BAYSE_ALL_MARKETS=true` to
watch every market on Bayse.

### Duplicate protection

Every processed order ID is stored in SQLite. Reconnects, restarts, and replayed
WebSocket messages therefore cannot post the same alert twice. Failed posts are
retried up to three times if the same order is seen again.

## Inspecting the feed

To confirm the connection stays alive and to see raw activity payloads:

```powershell
pipenv run python scripts/probe_feed.py --seconds 120
pipenv run python scripts/probe_feed.py --all --seconds 120
```

`--all` also subscribes to the global `all_activity` feed that the bot relies
on. This probe intentionally sends no client pings. A healthy run prints
`120s elapsed. Connection was still open.` instead of reconnecting every 20
seconds.

## Tests

```powershell
pipenv run pytest
```

The tests cover payload parsing, NGN conversion, tweet formatting/length,
idempotency, and threshold behaviour.

## Troubleshooting

**`403 Forbidden` when posting** — The X app is read-only, or the access token
was generated before write permission was enabled. Enable **Read and write** and
regenerate the access token/secret.

**`401 Unauthorized`** — Check the four `TWITTER_*` values. They must belong to
the same app/account, and the access token must have been generated with the
app's consumer key.

**No alerts** — Set `LOG_LEVEL=DEBUG` to see every order and its NGN value. Large
orders may simply not be occurring; the bot does not post anything until a
filled order crosses the threshold.

**Rate limits** — X's free tier allows a limited number of posts per month. The
publisher retries short rate-limit windows but gives up after 120 seconds and
logs the failure instead of blocking the bot indefinitely.
