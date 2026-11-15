# Trading Dashboard — end-to-end functional audit

Audit date: 2026-11-15. Scope is the running local app. No application code was changed. No broker order was sent. No trade plan, allocation, journal row, or watchlist row was saved or deleted.

One side effect did occur, because viewing a chart writes setups: opening RKLB on the 5-minute timeframe persisted several CONFIRMED support-bounce rows. Those rows then filled Today’s Attention list. That behaviour is finding P1-1.

## Environment actually used

| Check | Result |
|---|---|
| Process | Streamlit listening on `127.0.0.1:8501`, PID 20028 |
| Database | Docker container `trd-postgres-1`, healthy, port 5432, up 7 days |
| Mode | `DATA_MODE=live`, `MARKET_DATA_PROVIDER=twelvedata`, `T212_ENABLED=true` (names only; secrets not recorded) |
| Sidebar at start | Dashboard Running, Database Connected, Trading 212 Connected, background refresh Running, reconciliation PASS |
| Automated tests | `.venv\Scripts\python.exe -m pytest -q --tb=line` exited 0. Progress showed two skips. Skip reasons were not printed. |

Commands:

```
docker ps --format "{{.Names}} {{.Status}} {{.Ports}}"
Get-NetTCPConnection -LocalPort 8501 -State Listen
.\.venv\Scripts\python.exe -m pytest -q --tb=line
```

Browser: Cursor browser tab `2ff9b8`, pages opened at `http://127.0.0.1:8501/`. The automation viewport was about 831×903, which is under the app’s 900px sidebar breakpoint, so the sidebar was off-canvas. A separate pass set the viewport to 390×844 and then cleared it.

No Streamlit exception element was present on Today. Browser console was not separately captured. Postgres-down, broker-auth expiry, rate limits, and a killed network were not induced.

## Findings

| Priority | Page/Feature | Issue | Reproduction Steps | Expected Behaviour | Actual Behaviour | Root Cause | Recommended Fix |
|---|---|---|---|---|---|---|---|
| P1 | Today — market P&L | Today’s portfolio P&L and the position “Today” column stay blank while prices exist | Open `/` in live mode. RKLB shows latest price $61.53 and day change on the holding page. | Today shows market P&L versus the previous close, in GBP, or names the missing FX rate. | Hero “Today” is “— / Needs current prices and previous closes”. Position today cells are “—”. Attribution copy says the same. | `services/attribution.py` `load_today_attribution` sets `fx = D(1)` only when currency is GBP, otherwise `None`. Any non-GBP holding sets `missing = True` and the whole result is discarded. | Convert USD marks with a stored FX rate, or show “No GBP rate for USD” instead of claiming prices are missing. |
| P1 | Today — Attention | Opening a 5-minute chart replaces the daily warning with old intraday bounces | 1. On Today, note the 1D RKLB trend warning (close 68.40 below 68.64–69.68 on 2026-10-08). 2. Open that holding and select 5m. 3. Return to Today. | The daily warning stays visible. Intraday setups that are days old do not occupy the whole list. | Today shows five CONFIRMED RKLB support bounces from 2026-11-13 and 2026-11-10. The 1D trend warning is gone from the page. | `services/setups.py` `attention_setups` keeps every `current` non-invalidated row and returns five, ordered by status then symbol. A 5m scan marks historical bounces `current=True`. Newer 5m candles sort ahead of the October daily warning. | Age intraday setups off Today, cap how many per symbol, and do not let a chart view of one timeframe hide a still-active daily setup. |
| P1 | Holdings vs Today — price | The same holding shows two prices with no shared label | Compare Today “Latest price” with Holdings “Price” for RKLB and SPCX. | One labelled price, or both labelled by source. | Today and Research: RKLB $61.53, SPCX $139.10 (Twelve Data, bar 2026-11-13). Holdings table: RKLB $59.87, SPCX $136.26. The holding caption says the snapshot price is the broker feed, printed as `59.87000000`. | Holdings uses the stored broker snapshot. Today’s mini chart and Research use `market_price_bar`. | Label the table column “Broker snapshot” and the chart “Twelve Data EOD”, and round the caption to two decimals. |
| P1 | Decisions — add a decision | Live mode cannot create a long-term decision | Open Decisions. The “Add a decision” expander is present. The instrument query used by that form selects only mock instruments. The expander was not opened in the browser. | A live RKLB or SPCX decision can be saved without sending an order. | `form_choices` in `services/queries.py` filters `Instrument.data_origin == MOCK` and the same for trades. `decisions.py` then shows “No instrument is available in this data mode yet.” | The decision form was never pointed at the live universe. The trade-plan form was; its symbol list includes CLS. | Load live instruments for the decision form, as the plan form already does. Confirm the empty state in the browser after the change. |
| P2 | Sidebar — AI review | Sidebar implies a review for data the page says has none | Read the sidebar and the Today AI section on snapshot 18:30. | Both refer to the same snapshot, or the sidebar says the stored review is for a different snapshot. | Sidebar: “Based on 18:32 UTC data”, tone ok. Today: “No AI review is stored for this snapshot.” Database has 1 `AIRun`. | `services/t212_refresh.py` `_latest_ai_basis` uses the latest run’s context snapshot time, not the snapshot on screen. | If the basis snapshot is not the current one, say “Last review used an earlier snapshot.” |
| P2 | Research — watchlist | Added names have no price and no explanation | Open Research lab, Watchlist. | A watching name shows a stored price, or “No bar stored”. | CLS and NVIDIA are WATCHING with “—” for price, 1D, and 1M. RKLB and SPCX on the same list have prices. Remove buttons were not clicked. | Those symbols have no stored bars. The row renders the gap as an em dash. | Show “No market bar stored” and do not imply a live quote. |
| P2 | AI review — cost chart | The cost chart axis is unreadable | Open `/ai`. | A $0.00 day is a flat line on a normal date axis. | Axis labels include `23:59:59.9996` and a dollar range from −$1 to $1 around a $0 estimate. Run count is 1, model `gemini-3.8-flash`. | A single stored estimate is being plotted on a datetime axis with too little range. | Plot by calendar day and pin a zero baseline without a ±$1 scale. |
| P2 | Holdings — daily contribution | “Today” is blank on Today and £0.00 on Holdings | Open Holdings after Today. | The two “today” figures use the same definition, or the labels differ. | Today says prices are missing. Holdings “Daily contribution” prints £0.00 for RKLB and SPCX on an axis that runs to £0.40. | Different calculations: attribution refuses missing FX; the holdings chart compares the last two snapshots. | Use one definition, or label the holdings chart “Change between the last two snapshots”. |
| P2 | Layout under 900px | Refresh and the status panel sit in a drawer the toolbar can block | Browser width ~831px. Click Menu, then Refresh data. | Menu opens the sidebar and Refresh data runs. | Menu click was intercepted by a fixed header `div` at the top (831×60). Refresh data could not be scrolled into view. At 390px the page itself did not overflow (`scrollWidth` 390) and Menu was visible. | Sidebar is off-canvas below 900px (`theme.py`). The Streamlit header overlaps the Menu control at the automation width. | Keep a visible Refresh control in the page header below 900px, and keep Menu clear of the Streamlit toolbar. |
| P3 | Today — What changed | A no-change row is shown as a change | Read “What changed” on Today. | Silence, or “Unchanged since the previous snapshot.” | “Portfolio value — From £71,941.46 to £71,941.46.” | The change list includes equal values. | Omit rows whose from and to are equal. |
| P3 | Risk lab — themes | Every invested pound is “Unassigned” | Open `/risk`, “Which themes am I actually in?” | Stored themes, or an explicit “No theme stored”. | Cash 0%, Unassigned 100%. | Instruments have no theme label. The chart is doing what the caption says. | Leave the chart, and say “No theme is stored on these instruments.” |
| P3 | Risk lab — drawdown | Drawdown reads 0% beside large unrealised losses | Open `/risk`. Compare drawdown with Holdings P&L. | The label says this is the peak of stored snapshots, not loss versus cost. | Drawdown 0.00%, high-water £71,941.46, which equals the current total. Unrealised is about −£11,024. Query: 73 live snapshots, max 71941.46, min 63906.80. | The figure is the snapshot series peak. Cost-basis loss is a different measure, and the two sit on adjacent pages. | Rename to “Below the highest stored snapshot”. |
| P3 | Chrome | Streamlit Deploy is on the page | Any page. | The app’s own controls only. | “Deploy” and the Streamlit main menu are visible. | Default Streamlit toolbar. | Hide the toolbar in the local server config when this is the product UI. |
| P3 | Code | `event_panel.py` is unused | Search the repo for imports of `event_panel`. | Either wired to Risk/Today or removed. | The file still defines “Run event analysis” and “Link decision to this event”. Nothing imports it. | Leftover after Market Events left Today. | Delete it or mount it where events are shown. Not a user-facing control today. |

## Functionality inventory

Status is what this pass executed. “Code only” means the path was read and not clicked.

| Surface | Status | Evidence |
|---|---|---|
| App launch, live banner, Postgres, T212 worker | Working | Sidebar statuses above. Worker line moved from “just now” to “1 min ago” during the pass. |
| Today hero, cash, invested, weights | Working | £71,941.46 total, £1.83 cash, £71,939.91 invested. Weights 84.99% and 15.01%. |
| Today market P&L and attribution | Broken | P1. Blank while prices exist. |
| Today Attention — concentration, cash, scenario | Working | HIGH 84.99% vs 25% threshold. Cash £1.83. Scenario −17.00%. |
| Today Attention — technical setups | Broken | P1. Five stale 5m bounces replaced the daily warning. |
| Setup link to the holding chart | Working | “Open RKLB 1D” reached `/holding?instrument=36b20fba-62db-4d27-93d1-97640c76783f&timeframe=1D` after Enter. The 1D setup text matched the chart levels. |
| Holding chart 1D and 5m | Working | 5m showed the “last 10 days” caption and candles from 7–13 Nov. EMA 20 $58.66, SMA 50 $62.47, SMA 200 “Needs more bars” on the shorter range. |
| Holding 15m, 1H, 1W, ranges, indicator toggles | Not tested | 5m was the only timeframe switched. Checkboxes were visible. Stored bars exist for `5min` (496) and `1D` (64) only, so 15m and 1H would need a fetch. |
| Plotly zoom, pan, download, fullscreen | Not tested | Controls were in the accessibility tree. |
| Holdings table, executions, weight chart | Working | Quantities 1,350.70 and 104.83. Executions list starts 2024-12-03 BUY. Average cost shown in USD and GBP, with a note that GBP is not drawn on the USD chart. |
| Holdings price vs market price | Broken | P1. Two unlabeled prices. |
| Decisions — plan form renders | Partially working | Form is on the page: symbol default CLS, LONG, 1D, PLANNED, capital USD. |
| Decisions — save, edit, reopen a plan | Not tested | Save plan could not be scrolled into a click. No plan was written. |
| Decisions — share split and journal | Partially working | Both holdings show Core = broker and Trading 0.00. Journal says “Open a plan to record how it was traded.” Attach and manual entry were not opened. |
| Decisions — performance | Working | “Sample of 0 closed trades. Too small…” |
| Decisions — add or edit a decision | Broken in code, UI not opened | P1. Mock-only instrument list. |
| Decisions — pre-decision AI button | Not tested | Not clicked. Would call the external provider if the checkbox is on. |
| Risk lab charts and attention | Working | Concentration, scenarios (theme −20%, Nasdaq −10%, RKLB −30% → −25.50%), cash, high-impact empty state. Theme and drawdown labels are P3. |
| Research Watchlist | Partially working | Holdings priced. CLS and NVIDIA are dashes. Add/Remove not submitted. |
| Research Theses, Catalysts, Deep Dive | Not tested | Section control was visible. Those sections were not selected this pass. |
| Research Strategy Lab | Not tested this pass | Run backtest was not clicked. A prior session on this same app completed an RKLB 1D breakout (42 bars, 0 trades). That is not re-verified here. |
| AI review usage page | Partially working | $0.00, 1 run, 1 model. Value-added sections say “Not measured yet.” Cost chart is P2. |
| Today / Research “Run AI review” | Not tested | Checkbox was not in the `checked` state. The button was not clicked, so no context was sent. |
| Refresh data button | Blocked | Visible in the sidebar. Click failed at ~831px because the sidebar is off-canvas and Menu was intercepted. The background worker itself was Running. |
| Failure drills (DB down, auth expiry, rate limit, offline) | Not tested | Not induced. |
| Mobile 390px | Partially working | No document-level horizontal overflow. Menu visible. Chart tick `<text>` nodes are wider than their boxes, which is normal for Plotly and did not widen the page. |

## Broken or non-functional controls

These were observed. Controls that merely were not clicked are in the inventory, not here.

| Control | Location | Observed behaviour |
|---|---|---|
| Today metric and position “Today” cells | `/` | Render as “—” and the attribution section explains that prices are missing, which is not true. |
| Attention setup list | `/` | Shows five “Open RKLB 5m” links for 10–13 Nov bounces. The previous “Open RKLB 1D” trend warning is no longer on the page. |
| Holdings Price column | `/holdings` | Shows broker $59.87 / $136.26 while other pages show $61.53 / $139.10. |
| Add a decision | `/decisions` expander | Not opened. The query behind it cannot see live instruments, so the form’s Save decision path is unavailable in this mode. |
| Menu, then Refresh data | Width about 831px | Menu click intercepted by the top toolbar. Refresh data never ran from the UI. |
| AI cost chart | `/ai` | Draws, but the date and dollar axes are not readable. |
| `event_panel.py` buttons | Not mounted | “Run event analysis” and “Link decision to this event” have no page. |

## End-to-end journeys

| Journey | Result | Evidence |
|---|---|---|
| Launch → live portfolio | Pass | Live banner, reconciliation PASS, totals above. Snapshot on screen stayed 2026-11-15 18:30 UTC. |
| Refresh from the button | Blocked | Button not reachable at the automation width. Worker heartbeat was already Running. |
| Today → holding → 1D chart matches the signal | Pass, then regressed | Link opened the 1D chart with support 68.64–69.68 and the 2026-10-08 warning. After 5m, Today no longer showed that warning. |
| Change timeframe | Pass for 5m only | Caption: “5m shows the last 10 days. Older candles are not requested and are not invented.” |
| Toggle indicators | Not tested | |
| Create plan → save → edit → reopen | Blocked | Form rendered. Save was not clicked, so persistence is unverified. No row was written. |
| Record a fill → position → journal | Not tested | Would write `plan_fill` or a manual journal row. Left untouched. |
| Research → configure → backtest | Not tested this pass | Watchlist only. |
| Leave a page and return | Pass for navigation | `/holdings`, `/decisions`, `/risk`, `/research`, `/ai`, and `/` all rendered. Chart timeframe is in the URL, so a reload can restore it. Plan form state was not checked after a round trip. |
| Invalid plan input | Not tested | Empty Save plan was the intended check and the click did not land. |
| 390px layout | Pass with a limit | No page overflow. Sidebar behaviour at 390 was not click-tested after the 831px intercept. |

## Data integrity

Verified against the UI and read-only queries. No broker order, and no plan or allocation write.

- Portfolio identity is consistent: £61,140.06 + £10,799.57 + £1.83 = £71,941.46. Weights 84.99% + 15.01%.
- Quantities match across Today, Holdings, and Decisions: RKLB 1,350.70 and SPCX 104.83. The full stored quantities are longer; the UI rounds to two decimals. Core equals broker while trading shares are 0.
- Unrealised P&L matches on Today and Holdings: RKLB −£10,031.52 (−14.09%), SPCX −£992.44 (−8.42%). Average costs $70.53 and $149.76.
- Two marks for one symbol, both stored: broker snapshot RKLB $59.87 versus Twelve Data daily close $61.53 on 2026-11-13. Research 1D change uses the market bar (−3.96% RKLB, −0.60% SPCX).
- RVOL 0.01x is the stored volume, not a formula slip. Daily bars: 2026-11-13 close 61.53 volume 269,239; 2026-11-12 volume 13,430,400; 2026-11-11 volume 24,432,400. Whether that Friday print is a bad vendor bar was not checked against an exchange.
- The October trend warning is internally consistent: evidence close 68.40, zone 68.64–69.68, and the chart lists that resistance. It remains a current setup in the sense that price has not closed back above the zone. Today stopped showing it only because five newer 5m rows took the five slots.
- Those 5m bounces are also internally consistent with their own text (close back above the zone). They are a poor “today” list because the candles are from 10 and 13 November.
- GBP conversion for daily attribution is the missing piece, not the previous close. Holding day-change percent works without FX. Attribution refuses the book because `fx` is `None` for USD.
- Drawdown 0% matches the snapshot table: max stored value equals the latest value. It is not comparable to unrealised loss versus cost.
- Latest activity on Today included a RIME sell (4.48 @ 0.08 USD on 2026-11-11). RIME is not a current holding. The section says it is fills in the five days before the snapshot, so this is consistent, not a phantom position.
- Backtest results were not recomputed this pass. Journal closed-trade count is 0, which matches the empty performance panel.
- 73 live snapshots and 1 AI run. The AI run’s basis time (18:32) is not the snapshot on screen (18:30).

## What was not verified

- Saving or editing a plan, a share split, a manual fill, or a decision.
- Theses, Catalysts & Events, Deep Dive, and a fresh Strategy Lab run.
- 15m, 1H, 1W, range changes, and indicator checkboxes.
- The Refresh data button’s success and error text.
- Sending an AI review, on purpose.
- Postgres stopped, Twelve Data or T212 errors, an expired token, and a browser reload after a failed request.
- Whether the 2026-11-13 volume of 269,239 is wrong at the vendor.

## Recommended fix order

1. Daily P&L: stop treating a missing USD→GBP rate as “prices unavailable” (`services/attribution.py`).
2. Attention: stop a 5m chart view from burying the daily setup, and drop intraday setups after the session (`services/setups.py` `attention_setups`).
3. Say which price is the broker snapshot and which is Twelve Data (`holdings.py`, Today cards).
4. Point `form_choices` at the live universe so a decision can be saved.
5. Make the sidebar AI line match the snapshot on screen (`_latest_ai_basis`).
6. Fix the AI cost chart scale, then the under-900px Menu/Refresh overlap.
7. Cosmetic: equal “what changed” rows, theme empty copy, drawdown label, Streamlit Deploy, unused `event_panel.py`.

After 1–4, rerun the blocked journeys: Refresh data at a width above 900px, empty and valid plan save using a clearly named test plan that is then cancelled, and the decision form with RKLB.
