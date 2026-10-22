# Agent Desk

**Want it running 24/7 in the cloud for free? Follow `SETUP_CLOUD.md`.**

A six-agent trading desk that runs in **paper mode** (fake money). Each agent has one job, and every decision gets written down. That way, when you post results, they're real.

| Agent | Job |
|---|---|
| **ATLAS** | Reads the flow: volume surges, volatility, the bigger trend. If the market is dead or going crazy, it says so. |
| **VEGA** | Reads headlines you give it (`news.json`). With no news it stays neutral, because no news isn't a signal. |
| **ORION** | Writes the trade idea **and argues against it**: chasing, overheated RSI, fighting the trend. Too many problems means no trade. |
| **TITAN** | Times the entry: a candle *closes* across VWAP or the 50 EMA, with confirmation. Same idea as the Scalp City reel. |
| **LUNA** | Exits: hard stop, takes half off at +1R, moves the stop to breakeven, trails the rest. It also exits if the setup breaks. |
| **NOVA** | Risk officer with the final veto. Sizes every trade, runs the loss brake and the profit lock, benches a coin after 2 losses in a row, and blocks trades too small to beat fees. |

## What's in the box

```
backtest.py          run the desk on past or practice data, print the honest scoreboard
live.py              run it on live prices (shadow mode or Alpaca paper)
tests.py             checks the math, the brakes, and that it never sees the future
desk/                the agents, indicators, broker, data, stats
dashboard/           the 3D "Desk City" page + build script
```

No installs needed beyond Python 3.10+. It's pure Python on purpose.

## Run it (10 minutes)

1. Install Python from python.org (on Mac it may already be there).
2. Unzip this folder, open Terminal in it, and run:
   ```
   python3 tests.py                 # should end with ALL PASS
   python3 backtest.py              # practice run on random data
   ```
3. Get a free **Alpaca paper account** at alpaca.markets (paper trading needs no deposit).
4. Run on **real crypto prices from the last 10 days** (no keys needed for crypto data):
   ```
   python3 backtest.py --alpaca --days 10 --replay dashboard/replay.json
   python3 dashboard/build.py
   ```
   Open `dashboard/desk_city.html`. That's Desk City replaying real data.
5. Go live in **shadow mode** (real prices, pretend fills):
   ```
   python3 live.py
   ```
6. When you want real paper fills, set your paper keys and run:
   ```
   export APCA_API_KEY_ID=your_paper_key
   export APCA_API_SECRET_KEY=your_paper_secret
   python3 live.py --alpaca-paper
   ```
   Kill switch: make a file named `STOP` in the folder. The desk flattens and quits.

It has to keep running to trade. You can leave a laptop on, or rent a $5/month cloud server later.

## Things the build taught us (read this)

- **Fees decided the timeframe.** Alpaca charges about 0.15–0.25% per side on crypto. On 1-minute bars, a normal move is smaller than the fee, so Nova vetoed every trade. The desk uses **15-minute bars for crypto** and **5-minute bars for stocks** (no commission). On practice data, fees still ate more than 100% of the winnings. That's the main reason small accounts lose at scalping.
- **Options live in the Pattern Lab (below).** One QQQ 0DTE contract usually costs $300–$500, so a $100 account can't hold even one at the money.
- **No shorting in a cash account.** When the desk sees a bearish setup, Nova stands down and logs it.
- **The practice data is random on purpose.** A strategy that "prints money" on random data has a bug. Losing about 2% there is the correct result.

## The rule before real money

Run `--alpaca-paper` for **at least 4 weeks / 50+ trades**. Go real only if all of these hold:

- [ ] positive net P&L **after fees**
- [ ] profit factor above 1.3
- [ ] max drawdown under 10%
- [ ] Monte Carlo "chance to get cut 50%" under 5%
- [ ] you never had to touch it mid-trade

Real money is locked in code (`ALLOW_LIVE = False` in `desk/broker.py`). Changing that is your call, and nothing in here is financial advice.

## Tuning

Every knob is in `desk/config.py`: risk per trade (2%), loss brake (−5%/day), profit lock (+6%/day), stops, targets, fees, symbols. Change one thing at a time, then rerun the backtest **and** the tests.

## Content angle

The fake posts leave the result boxes blank ("[ACTUAL PAPER-TRADE RESULT]"). Yours fill them in. Post the Desk City replay, the week's scoreboard, and the losses too. Real numbers stand out in a feed full of placeholders.

---

# Pattern Lab: QQQ 0DTE options

This is the "find the patterns" machine. It tests **4,320 setups**: 8 signals × 5 time windows × trend filter × 3 stops × 3 targets × 3 hold times × 2 strikes. Each one is priced as a real 0DTE option trade with spreads and fees.

```
pattern_lab.py      the search (train on older days, exam on newer days, coin-flip control)
validate.py         checks the option-price model against REAL historical option prices
options_paper.py    paper-trades the top survivor live on Alpaca
ROBINHOOD_RULES.md  the rule card for when you go live on Robinhood
```

## Run it on real QQQ data (on your computer)

```
export APCA_API_KEY_ID=your_paper_key
export APCA_API_SECRET_KEY=your_paper_secret
python3 pattern_lab.py --source alpaca --days 500     # downloads ~2 years of 1-min bars once, then cached
python3 validate.py                                    # is the option model close to reality?
python3 options_paper.py                               # paper-trade survivor #1 during market hours
```

Results land in `lab_out/report.json` and `lab_out/all_variants.csv` (open it in Sheets).

## How we know the lab tells the truth

Two control tests are built in:
- `--source randomwalk`: a pure coin-flip market. **0 of 4,320 setups survived** across 6 million simulated trades. The lab doesn't invent patterns.
- `--source synthetic`: a market with hidden trending stretches. 12.2% of setups survived versus 3.4% for coin-flip entries. The lab finds patterns when they're actually there.

## What the tests showed about 0DTE buying

- In a market with no pattern, buying 0DTE loses money. The median setup lost **$18 per contract** and only 14% of setups were positive. Spreads, fees and time decay are a tax on every trade.
- Even a setup with a real edge (profit factor 1.46, +$38 per contract) had a **42% chance of cutting a $1,500 account in half** first, in the Monte Carlo. One contract is a huge bet for a small account.
- The verdict only says "trade it" when real setups beat the coin-flip control by a clear margin.

## The road to $5k/month

| Monthly return | Account needed for $5k/month |
|---|---|
| 2% (good) | $250,000 |
| 5% (great, rare) | $100,000 |
| 10% (elite, usually doesn't last) | $50,000 |

1. **This week:** run the lab on real QQQ data. If the verdict says no edge, that saved you real money.
2. **Weeks 1–4:** paper-trade survivor #1 with `options_paper.py`. Compare live results to the lab's test numbers.
3. **Month 2:** if paper matches the lab, go live on Robinhood with **one contract** and the rule card. Keep the account at least 3x the contract cost.
4. **Always:** add money from income. Post the real numbers, losses included. An audience that trusts your results can pay more than the trades early on.
