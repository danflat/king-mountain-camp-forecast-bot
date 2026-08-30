# King Mountain Camp Forecast Bot

A temporary, no-paid-API morning briefing bot for King Mountain Glider Park. It runs daily from **August 30 through September 9, 2026**, then safely exits without posting.

The bot combines:

- King launch, Coyote, and Glider Park LZ live Ecowitt observations
- Open-Meteo hourly and pressure-level guidance through the event window
- NWS forecast text and active alerts
- King-specific logic for the west-facing launch, morning Coyote drainage flow, valley-wind reversal, thermal depth, Mackay-area overdevelopment, and strong-flow wave/rotor potential
- A direct XC Skies PointCast/Skew-T cross-check link in every briefing

It does **not** use an LLM or require an OpenAI/API subscription. The briefing is a decision aid, never a launch or go/no-go instruction.

XC Skies is intentionally a human cross-check rather than an automated feed. Its published site does not document a supported forecast API, its forecast tools expect an account/subscription, and its terms limit redistribution. The bot therefore links pilots to XC Skies without scraping or reposting proprietary forecast fields.

## Recommended hosting: GitHub Actions

The included workflow posts at **8:00 AM MDT** every morning. Each message gives a detailed current-day briefing plus a compact outlook through September 9; confidence steps down with forecast lead time. GitHub schedules can occasionally start a few minutes late. The Python script enforces the event dates, so the daily workflow becomes a harmless no-op after September 9.

### 1. Create and add the Telegram bot

1. In Telegram, open the verified **@BotFather** account.
2. Send `/newbot`, choose a name and username, and save the token privately.
3. Add the new bot to **Team WA King-Camp**. It normally does not need admin rights, but it must have permission to post.
4. Send `/chatid` in the group so Telegram creates an update the bot can see.

The invite URL is not the numeric Telegram chat ID, so the bot cannot post from that link alone.

### 2. Discover the group chat ID

On a computer with Python 3.11+:

```bash
read -s TELEGRAM_BOT_TOKEN
export TELEGRAM_BOT_TOKEN
python king_mountain_bot.py --discover-chat
```

Paste the token at the hidden prompt and press Enter. This avoids leaving it in visible shell history.

Find the row titled `Team WA King-Camp` and copy its negative `chat_id`. If the group uses topics, also copy the reported thread ID for the desired topic.

### 3. Put the project in a private GitHub repository

Upload the contents of this folder so `king_mountain_bot.py` is at the repository root. In the repository, open **Settings → Secrets and variables → Actions** and create:

| Secret | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Token from BotFather |
| `TELEGRAM_CHAT_ID` | Negative numeric group chat ID |
| `TELEGRAM_THREAD_ID` | Optional topic/thread ID; omit for a normal group |

Never commit the token to a file or paste it into the workflow.

### 4. Test, then enable posting

Open **Actions → King Camp morning briefing → Run workflow**.

- Enable `discover_chat` after adding the bot to Team WA King-Camp and sending `/chatid`; the action log will list the group's numeric ID without posting.
- Leave `dry_run` enabled first. The briefing will be printed in the action log but not posted.
- Run it again with `dry_run` disabled to send one live test to the group.
- The scheduled run then posts every morning at 8:00 AM MDT through September 9.

## Local test

No third-party Python packages are needed:

```bash
python -m unittest -v
python king_mountain_bot.py --dry-run
```

To send locally after setting the two Telegram environment variables:

```bash
python king_mountain_bot.py
```

You can also inspect another event date while it remains inside the available model horizon:

```bash
python king_mountain_bot.py --date 2026-09-02 --dry-run
```

## iPhone note

The script uses only Python's standard library, so it can be run manually from an iPhone Python environment such as Pyto or a-Shell. iOS background execution is not dependable enough for unattended daily posting; cloud scheduling is the recommended autorun method.

## Tuning

The event dates, 6:30 AM schedule, station shares, forecast coordinates, and altitude bands are near the top of `king_mountain_bot.py`. The current altitude wind bands are 7,400 ft launch, 10,500 ft ridge, 14,000 ft, and 18,000 ft MSL.

The rating intentionally says `HIGHER`, `MIXED`, or `LOWER` **potential** instead of `GO/NO-GO`. Local cycles, gust spread, visible development, smoke, radar, pilot skill, and official alerts remain controlling inputs.

## Data attribution

Weather-model data is provided by [Open-Meteo](https://open-meteo.com/) from its integrated national weather-service models. Official forecast text and alerts come from the [U.S. National Weather Service API](https://www.weather.gov/documentation/services-web-API). Live station observations come from the three public Ecowitt share links supplied for King Mountain Camp.
