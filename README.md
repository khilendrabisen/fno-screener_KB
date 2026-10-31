# Daily bullish F&O screener (monthly view)

Every weekday at 9:00 AM IST it sends the top 10 bullish F&O stocks to your
Telegram and updates a web page you can open on your phone.

A stock must pass BOTH rules:
1. Trend: close above 20-DMA, 20-DMA above 50-DMA, 1-month return positive
2. Build-up: total futures open interest higher than ~20 sessions ago (price also up)

It is a screen, not advice, and it never places orders.
Data is from the previous trading day's close.

## One-time setup (about 15 minutes, on your personal laptop)

### 1. Make a Telegram bot
1. In Telegram, search for **@BotFather** and send `/newbot`. Follow the prompts.
2. Copy the **bot token** it gives you.
3. Open a chat with your new bot and send it any message (for example "hi").
4. In a browser open: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
5. Find `"chat":{"id": 123456789 ...}`. That number is your **chat id**.

### 2. Put the files on GitHub
1. Create a free account at github.com and make a **new public repository**
   (public is needed for the free web page). Name it `fno-screener`.
2. Upload every file from this folder, keeping the `.github/workflows/daily.yml`
   path. (Use "Add file > Upload files" and drag the whole folder contents.)

### 3. Add your secrets
Repo > Settings > Secrets and variables > Actions > New repository secret:
- `TELEGRAM_BOT_TOKEN` = your bot token
- `TELEGRAM_CHAT_ID` = your chat id

Never put these in the code files. The repo is public.

### 4. Turn on the web page
Repo > Settings > Pages > Source: **Deploy from a branch**, branch **main**,
folder **/docs**. Your page will be at
`https://<your-github-username>.github.io/fno-screener/`

### 5. Test it
Repo > Actions > "Daily bullish F&O screen" > **Run workflow**.
Within a couple of minutes you should get the Telegram message and the page
appears (first publish can take a minute or two).

After this the laptop is not needed.

## If something goes wrong
- No Telegram message: open the run in the Actions tab and copy the red error.
- The message says "OI data unavailable": NSE blocked the download from GitHub's
  servers that day. The list then uses the trend rule only.
- The first run may need a fix. Send me the error text and I'll patch it.
- GitHub can start scheduled runs 10-30 minutes late.

## Change the settings
- Number of stocks: `TOP_N` in `.github/workflows/daily.yml`
- Send time: the cron line (`30 3 * * 1-5` = 9:00 AM IST). GitHub uses UTC.
