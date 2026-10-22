# Put the bots in the cloud (about 15 minutes, free)

After this, GitHub runs everything for you. Your laptop can be off.

| Bot | When it runs |
|---|---|
| Crypto desk (BTC, ETH, SOL, DOGE, AVAX, LINK) | every 15 minutes, 24/7 |
| QQQ 0DTE options bot | every market day, 9:30am–3:10pm ET |
| Pattern Lab on real QQQ data | every weeknight |
| Self-improve (re-tunes the crypto desk) | every Sunday |

All of them trade on **Alpaca paper**: fake money, real market prices, real order fills.

## 1. Get Alpaca paper keys
alpaca.markets → sign up → switch to **Paper Trading** → **API Keys** → Generate. Copy the **Key** and **Secret**. Also turn on **options trading** for the paper account in its settings.

## 2. Make the GitHub repo (use a computer for this part)
1. github.com/new → name it `agent-desk` → **Public** → Create.
   Public keeps GitHub's cloud minutes free. Your keys stay hidden in the secret vault.
2. Click **uploading an existing file** and drag in **everything** from the unzipped `agent-desk` folder.
   On a Mac, press **Cmd + Shift + .** in Finder first so the hidden `.github` folder shows up. It holds the schedules.
3. Commit.

## 3. Add your keys to the vault
Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**, twice:
- `APCA_API_KEY_ID` = your paper key
- `APCA_API_SECRET_KEY` = your paper secret

## 4. Let the bots save their results
**Settings** → **Actions** → **General** → Workflow permissions → **Read and write** → Save.

## 5. Turn on the live page
**Settings** → **Pages** → Source: **Deploy from a branch** → branch `main`, folder `/docs` → Save.
Your page: `https://YOUR-USERNAME.github.io/agent-desk/`. On your phone, add it to the home screen.

## 6. Start everything
**Actions** tab → enable workflows if asked → open each one and press **Run workflow** once:
1. `nightly pattern lab` (takes ~5–10 min the first time; it downloads 2 years of QQQ data)
2. `crypto desk`
From then on, they run on their own.

## Controls
- **Pause everything:** Actions tab → each workflow → **⋯** → Disable.
- **Kill switch:** add a file named `STOP` to the repo. The bots flatten their positions and stop trading.
- **Change settings:** edit `cloud/config_crypto.json`. The Sunday loop edits it too, and only when the change wins on data it hasn't seen.
- **Bring me in:** in any chat with Claude, connect GitHub and say "pull agent-desk and look at the results". Every trade, decision and tune-up is saved in `state/`.

## Real money
`desk/broker.py` has `ALLOW_LIVE = False`. Flipping it plus using live keys sends real orders. The options bot reads `APCA_BASE_URL` the same way.
