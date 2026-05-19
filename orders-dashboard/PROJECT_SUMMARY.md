# Orders Dashboard — Complete Project Summary

> **Purpose of this document:** Full onboarding reference for a new Claude Code session (or developer). Covers every file, function, data source, business rule, and deployment detail. Assumes zero prior context.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [File Structure](#2-file-structure)
3. [Tech Stack & Dependencies](#3-tech-stack--dependencies)
4. [Data Sources](#4-data-sources)
5. [Credentials & Authentication](#5-credentials--authentication)
6. [Code Architecture](#6-code-architecture)
7. [Business Logic & Rules](#7-business-logic--rules)
8. [Dashboard Features — Tab by Tab](#8-dashboard-features--tab-by-tab)
9. [Deployment Setup](#9-deployment-setup)
10. [How to Run Locally](#10-how-to-run-locally)
11. [How to Deploy Updates](#11-how-to-deploy-updates)
12. [Known Issues & Recent Fixes](#12-known-issues--recent-fixes)
13. [Pending Tasks](#13-pending-tasks)
14. [Key Gotchas & Non-Obvious Behaviour](#14-key-gotchas--non-obvious-behaviour)

---

## 1. Project Overview

### What it does
A Streamlit web dashboard that pulls live order data from a Google Sheet and presents three operational views:

| Tab | Purpose |
|-----|---------|
| **Orders** | High-level order volume, value, and customer breakdown |
| **Delivery KPI** | On-time delivery performance against target windows |
| **DOT Orders** | Dedicated KPI tracking for DOT chair orders (GO / LITE / V2.1) |

### Who uses it
The operations team at **DECI** to monitor fulfillment performance, flag delayed/cancelled orders, and tag untagged DOT orders directly from the dashboard.

### What "DOT" means
DOT is a product line of chairs (not sofas). Three variants exist:
- **DOT-GO** — 2-week delivery target
- **DOT-LITE** — 2-week delivery target
- **DOT-V2.1** — 8-week delivery target

### Business context
- Orders are tracked in a Google Sheet maintained by the ops team.
- The dashboard reads the sheet on load (cached for 5 minutes).
- The only write-back operation is tagging untagged DOT orders (updating the Status column in the sheet).

---

## 2. File Structure

```
orders-dashboard/
├── dashboard.py          # Main Streamlit app — all UI, charts, filters, business logic
├── extract.py            # Google Sheets I/O layer — all read/write to the sheet
├── requirements.txt      # Pinned Python dependencies for Streamlit Cloud
├── credentials.json      # Service-account key (LOCAL ONLY — never committed to git)
├── .gitignore            # Excludes credentials.json, __pycache__, .env
├── compare_apr26.py      # One-off script: validated April 2026 KPI against Excel reference
├── check_units.py        # One-off script: audited DOT unit counts from the Data worksheet
└── __pycache__/          # Python bytecode cache (auto-generated, not committed)
```

### File roles in detail

**`dashboard.py`** — The entire Streamlit application lives here (~820 lines). Contains:
- Page config and global refresh button
- All helper/business-logic functions
- `get_data()` — the cached data loader + transformer
- Three tab sections (Orders, Delivery KPI, DOT Orders)
- The untagged-DOT tagging UI at the bottom of the DOT tab

**`extract.py`** — Pure I/O module. No Streamlit imports except for `_get_creds()`. Contains:
- `_get_creds(scopes)` — credential loader (cloud vs local)
- `load_orders(sheet_url_or_name, worksheet_name)` — reads "Orders Plan " worksheet
- `load_dot_items(sheet_url_or_name)` — reads "Data" worksheet, filters DOT SKUs
- `load_unit_counts(sheet_url_or_name)` — reads "Data" worksheet, aggregates per-SO unit counts
- `write_dot_tags(sheet_url_or_name, so_tag_map, worksheet_name)` — writes DOT tags back to Status column

**`compare_apr26.py`** — Standalone diagnostic script (not part of the app). Used once to verify that the dashboard's April 2026 KPI numbers matched a known-good Excel baseline. Safe to ignore but useful as a reference for how `classify_delivery` logic was validated.

**`check_units.py`** — Standalone diagnostic script. Used once to check how many DOT orders appear in the Data tab vs the Orders Plan tab, and to identify orders with multiple SKUs.

---

## 3. Tech Stack & Dependencies

### Core libraries

| Library | Version | Role |
|---------|---------|------|
| `streamlit` | 1.57.0 | Web UI framework |
| `pandas` | 3.0.3 | Data manipulation |
| `numpy` | 2.4.5 | Numeric operations |
| `plotly` | 6.7.0 | Interactive charts |
| `gspread` | 6.2.1 | Google Sheets API client |
| `google-auth` | 2.53.0 | OAuth2 / service-account authentication |

### Important API notes for this Streamlit version (1.57.0)
- `use_container_width=True` is the correct parameter for `st.plotly_chart()` and `st.dataframe()`.  
  Do **not** use `width='stretch'` — that is invalid for these functions.
- `st.cache_data(ttl=300)` is the current caching decorator (not the old `@st.cache`).
- `st.rerun()` replaces the old `st.experimental_rerun()`.

### Python version
CPython 3.14 (confirmed by `__pycache__` filenames: `cpython-314`).

---

## 4. Data Sources

### Google Sheet
- **URL:** `https://docs.google.com/spreadsheets/d/1cEpLqAb_sqOoGxQ7GezAgyAlfQz4fOlpPVRuX-mimaA/edit`
- **Constant in code:** `SHEET` in `dashboard.py` (top of file) and `SHEET_URL` in the utility scripts.

### Worksheet 1 — "Orders Plan " *(note the trailing space in the name)*

Loaded by `load_orders()`. Row 1 is the header. Used columns:

| Column | Type | Description |
|--------|------|-------------|
| `SO` | string | Sales Order number, e.g. `S0008499`. Primary key. |
| `Customer Name` | string | Customer full name |
| `Order Date` | date string (`DD/MM/YYYY`) | Date order was placed |
| `Delivery Date` | date string (`DD/MM/YYYY`) | Actual delivery date (blank if not yet delivered) |
| `Status` | string | Order status — see Status Values below |
| `Plan` | string | Delivery plan — `"Online/Fast Track"` or `"Plan Month"` |
| `Total Order Value` | numeric string (may contain commas) | Order value in EGP |
| `Order Overdue` | numeric string (may contain commas) | Overdue amount in EGP |
| `Notes` | string | Free-text notes |

#### Status column values
| Value | Meaning |
|-------|---------|
| `DOT-GO` | DOT chair, GO variant |
| `DOT-LITE` | DOT chair, LITE variant |
| `DOT-V2.1` | DOT chair, V2.1 variant |
| `Canceled` | Cancelled order (one-L spelling from sheet) |
| `Delayed` | Order delayed by company |
| `Delayed by Customer` | Order delayed by customer |
| *(anything else)* | Regular in-progress / delivered order |

#### Plan column values
| Value | Meaning |
|-------|---------|
| `Online/Fast Track` | 4-week delivery target window |
| `Plan Month` | 8-week delivery target window |
| *(blank or `nan`)* | Order not yet assigned a plan → excluded from KPI |

> **Important:** A cancelled order has the "Canceled"/"Cancelled" tag in **Status only**, **CS Updated Date only**, or **both**. The code uses `_is_canceled(status_val, cs_updated_date_val)` to check both columns. Plan is NOT used for cancellation detection.

### Worksheet 2 — "Data"

Loaded by `load_dot_items()` and `load_unit_counts()`. Row 1 is the header.

| Column | Type | Description |
|--------|------|-------------|
| `Order` | string | Sales Order number (maps to `SO` in Orders Plan) |
| `Item Sku` | string | SKU code, e.g. `DOT-GO-BLK`, `DOT-V2.1-WHT` |
| `Item Name` | string | Human-readable product name |
| `Item QTY` | numeric string | Quantity of this SKU in the order |

Only rows where `Item Sku` contains `"DOT"` (case-insensitive) are used. Non-DOT rows are ignored.

---

## 5. Credentials & Authentication

### Service account
- **Email:** `decioperations@glowing-run-496613-t9.iam.gserviceaccount.com`
- **GCP Project ID:** `glowing-run-496613-t9`
- **Key file (local):** `credentials.json` in the project root (never committed — in `.gitignore`)

### How `_get_creds(scopes)` works (in `extract.py`)

```
1. Try to import streamlit and read st.secrets["gcp_service_account"]
   → Used on Streamlit Cloud
   → TOML stores private_key with literal \n; code calls .replace("\\n", "\n") to fix
2. Fall back to credentials.json file
   → Used for local development
3. If neither works → raise RuntimeError with instructions
```

### Read vs write scopes
- **Read operations** (`load_orders`, `load_dot_items`, `load_unit_counts`):
  ```
  https://www.googleapis.com/auth/spreadsheets.readonly
  https://www.googleapis.com/auth/drive.readonly
  ```
- **Write operation** (`write_dot_tags`):
  ```
  https://www.googleapis.com/auth/spreadsheets
  https://www.googleapis.com/auth/drive
  ```

### Streamlit Cloud secrets format
In the Streamlit Cloud dashboard under **App settings → Secrets**, the secret block looks like:

```toml
[gcp_service_account]
type = "service_account"
project_id = "glowing-run-496613-t9"
private_key_id = "bc349a1e11110767ee391370c39e428496f48efb"
private_key = "-----BEGIN PRIVATE KEY-----\nMIIEv...\n-----END PRIVATE KEY-----\n"
client_email = "decioperations@glowing-run-496613-t9.iam.gserviceaccount.com"
client_id = "116776018382246654201"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "https://www.googleapis.com/robot/v1/metadata/x509/decioperations%40glowing-run-496613-t9.iam.gserviceaccount.com"
universe_domain = "googleapis.com"
```

The `private_key` value must keep `\n` as literal backslash-n in TOML (not real newlines). The code converts them back automatically.

---

## 6. Code Architecture

### Data flow (end to end)

```
Google Sheet
    │
    ▼ (gspread API, service-account auth)
extract.py
    ├── load_orders()        → raw DataFrame, all columns from "Orders Plan "
    └── load_unit_counts()   → per-SO DOT unit counts from "Data"
    │
    ▼ (merged and transformed)
dashboard.py — get_data()    [cached 5 min via @st.cache_data(ttl=300)]
    ├── Parse Order Date, Delivery Date → datetime
    ├── Compute Week, Month, Year columns (from Order Date)
    ├── Compute Flag column (Cancelled / Delayed / DOT type indicator)
    ├── Parse Total Order Value → float (strips commas)
    ├── Compute Channel (Online/Fast Track vs Plan Month)
    ├── Compute Target Week Start / Target Week End (per order)
    ├── Compute Target Month (from Target Week Start)
    ├── Compute Delivery Status via classify_delivery() (each row)
    ├── Compute Days Late (for Late orders, skipping Fridays)
    ├── Merge unit counts from load_unit_counts()
    ├── Compute DOT Status / DOT Target Start / DOT Target End via classify_dot()
    └── Compute DOT Days Late (for Late DOT orders, skipping Fridays)
    │
    ▼ (full enriched DataFrame — ~820 rows typically)
Streamlit tabs
    ├── Tab 1: Orders — filters df by Order Date period
    ├── Tab 2: Delivery KPI — filters df by Target Month
    └── Tab 3: DOT Orders — filters by DOT Status, Order Date period
```

### Functions in `dashboard.py`

#### `week_start(date) → Timestamp`
Returns the Saturday that begins the Sat–Thu working week containing `date`.
```python
days_back = (date.weekday() - 5) % 7
return date - pd.Timedelta(days=int(days_back))
```
Weekday mapping: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, **Sat=5**, Sun=6.
- Saturday → `(5-5)%7 = 0` → itself ✓
- Sunday → `(6-5)%7 = 1` → back 1 day to Saturday ✓
- Friday → `(4-5)%7 = 6` → back 6 days to Saturday ✓

#### `target_week(order_date, plan) → (t_start, t_end)`
Computes expected delivery window for a regular (non-DOT) order.
- `Online/Fast Track` → 4 weeks forward
- Any other plan (Plan Month) → 8 weeks forward
- Returns `(week_start(expected), week_start(expected) + 5 days)` i.e. Saturday–Thursday

#### `dot_target_week(order_date, status) → (t_start, t_end)`
Computes expected delivery window for a DOT order.
- `DOT-V2.1` → 8 weeks forward
- `DOT-GO` / `DOT-LITE` (anything else) → 2 weeks forward
- Uses `status.strip().upper()` to handle trailing whitespace in the sheet.

#### `_is_canceled(status_val, cs_updated_date_val) → bool`
Checks both columns for any cancellation signal.
```python
str(status_val).strip().lower()          in ("canceled", "cancelled") OR
str(cs_updated_date_val).strip().lower() in ("canceled", "cancelled")
```
Handles both UK ("cancelled") and US ("canceled") spellings.

#### `classify_dot(row) → (dot_status, t_start, t_end)`
Classifies a single DOT order row. Returns a 3-tuple.
1. If `Order Date` is NaT → `(None, NaT, NaT)`
2. If `_is_canceled(Status, CS Updated Date)` → `("Cancelled", NaT, NaT)`
3. Else compute target window, compare to `Delivery Date`:
   - No delivery date → `"Not Delivered"`
   - Within window → `"On time"`
   - Before window start → `"Early"`
   - After window end → `"Late"`

#### `classify_delivery(row) → str | None`
Classifies a single order row for the regular Delivery KPI. Returns a string status or `None`.
1. If Status starts with `"DOT"` → `None` (DOT orders excluded from regular KPI)
2. If `_is_canceled(Status, CS Updated Date)` → `"Cancelled"`
3. If Status is `"Delayed by Customer"` or `"Delayed"` → `"Excluded - Delayed by Customer"`
4. If Plan is blank/nan → `None` (not in KPI scope)
5. If Order Date is NaT → `None`
6. Else compute target window, compare to Delivery Date → `"On time"` / `"Early"` / `"Late"` / `"Not Delivered"`

#### `get_data() → pd.DataFrame` *(cached 5 min)*
Master data loader. Calls `load_orders()` + `load_unit_counts()`, applies all transformations, returns the fully enriched DataFrame. See data flow diagram above.

Key computed columns added:

| Column | Source | Notes |
|--------|--------|-------|
| `Month` | `Order Date` | Period start timestamp (`dt.to_period("M").dt.to_timestamp()`) |
| `Week` | `Order Date` | `week_start(Order Date)` |
| `Year` | `Order Date` | `dt.year` integer |
| `Flag` | `Status` + `Plan` | `"Cancelled"` / `"Delayed"` / `"DOT-GO"` etc. / `""` |
| `Total Order Value` | raw string column | Stripped commas, cast to float |
| `Channel` | `Plan` | `"Online/Fast Track"` or `"Plan Month"` |
| `Target Week Start` | `Order Date` + `Plan` | Sat of target window; NaT if no plan |
| `Target Week End` | `Order Date` + `Plan` | Thu of target window (Start + 5 days) |
| `Target Month` | `Target Week Start` | Period start of the month the target week falls in |
| `Delivery Status` | `classify_delivery()` | Regular KPI status |
| `Days Late` | `Delivery Date` - `Target Week End` | Calendar days late, Fridays excluded |
| `SKUs` | Data worksheet | DOT SKU count per SO (default 1) |
| `Total_Units` | Data worksheet | Sum of DOT item quantities per SO (default 1) |
| `DOT Status` | `classify_dot()` | DOT-specific delivery status |
| `DOT Target Start` | `classify_dot()` | Sat of DOT target window |
| `DOT Target End` | `classify_dot()` | Thu of DOT target window |
| `DOT Days Late` | `Delivery Date` - `DOT Target End` | DOT-specific days late, Fridays excluded |

#### `fmt_dates(frame) → pd.DataFrame`
Returns a copy of a DataFrame with all `datetime64` columns formatted as `"DD-Mon-YYYY"` strings, and `NaT` replaced by `""`. Used before every `st.dataframe()` call.

#### `get_dot_items() → pd.DataFrame` *(cached 5 min)*
Calls `load_dot_items()`. Returns one row per DOT SKU line item (SO, Item Sku, Item Name, Item QTY). Used in the expandable order detail rows in the DOT tab.

### Functions in `extract.py`

#### `_get_creds(scopes) → Credentials`
Credential loader (see Section 5). Called by every other function in this file.

#### `load_orders(sheet_url_or_name, worksheet_name="Orders Plan ") → pd.DataFrame`
Reads the Orders Plan worksheet. Deduplicates column headers (blank/duplicate cols get `_col{i}` or `_{n}` suffix). Returns raw strings — no type conversion here.

#### `load_dot_items(sheet_url_or_name) → pd.DataFrame`
Reads the "Data" worksheet. Returns rows where `Item Sku` contains "DOT" (case-insensitive). Columns: `SO`, `Item Sku`, `Item Name`, `Item QTY` (numeric).

#### `load_unit_counts(sheet_url_or_name) → pd.DataFrame`
Reads the "Data" worksheet. Returns one row per SO with: `SO`, `SKUs` (count of DOT SKUs), `Total_Units` (sum of Item QTY). Only DOT SKUs counted.

#### `write_dot_tags(sheet_url_or_name, so_tag_map, worksheet_name="Orders Plan ") → list`
Writes DOT type tags back to the Status column. `so_tag_map` is `{SO: tag}` dict. Uses `ws.batch_update()` for efficiency. Returns list of SOs that were actually updated (only those found in the sheet).

---

## 7. Business Logic & Rules

### Week system
The working week runs **Saturday to Thursday** (Friday is the weekend in Egypt). All week references use the Saturday start date as the key.

### Delivery target windows (regular orders)
| Plan | Offset | Window |
|------|--------|--------|
| Online/Fast Track | +4 weeks from Order Date | `week_start(expected)` to `week_start + 5 days` |
| Plan Month | +8 weeks from Order Date | Same formula |

### Delivery target windows (DOT orders)
| DOT Type | Offset |
|----------|--------|
| DOT-GO | +2 weeks from Order Date |
| DOT-LITE | +2 weeks from Order Date |
| DOT-V2.1 | +8 weeks from Order Date |

### Delivery classification
| Status | Condition |
|--------|-----------|
| On time | Delivery Date is within `[Target Week Start, Target Week End]` inclusive |
| Early | Delivery Date is before Target Week Start |
| Late | Delivery Date is after Target Week End |
| Not Delivered | Delivery Date is blank/NaT |
| Cancelled | Status or CS Updated Date column contains "Canceled"/"Cancelled" |
| Excluded - Delayed by Customer | Status is "Delayed" or "Delayed by Customer" |
| `None` | No plan assigned, no order date, or DOT order (for regular KPI) |

### Days late calculation
Calendar days from the Thursday end of the target window to actual delivery, **minus any Fridays** in that span. Friday is the day off.

### On-Time % (regular KPI)
```
On-Time % = (On time + Early) / eligible_orders
```
Where `eligible_orders` = rows with a non-null, non-excluded `Delivery Status` (i.e., not Cancelled, not Delayed by Customer).

### On-Time % (DOT KPI)
```
On-Time % = On time / (On time + Early + Late)
```
Denominator is **delivered orders only** — Not Delivered and Cancelled are excluded.

### Untagged DOT detection
An order is "untagged" if it appears in the `Data` worksheet (has DOT SKUs) but its `Status` in the Orders Plan does not start with "DOT". These are surfaced in the DOT tab for manual tagging.

### DOT type suggestion
When an untagged order has exactly one DOT variant across all its SKUs, the system suggests that tag:
- SKU contains "V2.1" → suggest `DOT-V2.1`
- SKU contains "LITE" → suggest `DOT-LITE`
- SKU contains "GO" → suggest `DOT-GO`
- Mixed/unrecognised → no suggestion (user selects manually)

---

## 8. Dashboard Features — Tab by Tab

### Global elements
- **Page title:** "Orders Dashboard"
- **Layout:** Wide (`st.set_page_config(layout="wide")`)
- **🔄 Refresh button** (top right): clears `st.cache_data` and reruns the app. Forces a fresh pull from Google Sheets. Cache TTL is 5 minutes otherwise.

---

### Tab 1 — Orders

#### Filters (inside `st.expander("🔽 Filters")`)
- **Period radio:** Weekly / Monthly / Yearly / All Time
- **Multi-select** (shown conditionally):
  - Weekly → "Select Week(s)" — options are `Week` column values, displayed as `"DD Mon YYYY – DD Mon YYYY"`
  - Monthly → "Select Month(s)" — options are `Month` column values, displayed as `"Mon YYYY"`
  - Yearly → "Select Year(s)" — options are `Year` column integer values
  - Default: most recent period pre-selected
  - No selection → info message shown, metrics display zeros / empty table

#### KPI metrics (4 columns)
| Metric | Source |
|--------|--------|
| Total Orders | `len(filtered)` — orders placed in selected period |
| Unique Customers | `filtered["Customer Name"].nunique()` |
| Total Order Value | `filtered["Total Order Value"].sum()` formatted with commas |
| Delivered | Count of orders whose **Delivery Date** falls in the selected period (independent of Order Date) |

#### Charts
1. **Orders per Month** (bar chart) — always shows full history from `df` regardless of filter; x = month label, y = order count
2. **Orders by Status** (pie chart) — from `filtered`; blank status shown as "No Status"
3. **Top 10 Customers** (horizontal bar chart) — from `filtered`; sorted descending

#### Order Details table
- Text search box ("SO number, customer name, notes…") — filters all visible columns
- Columns shown: SO, Customer Name, Order Date, Flag, Total Order Value, Order Overdue, Delivery Date, Notes
- `Total Order Value` and `Order Overdue` formatted as `"EGP X,XXX"` (blank if zero/null)
- Row count caption above table
- `fmt_dates()` applied before display

---

### Tab 2 — Delivery KPI

#### Filters
- **Multi-select "Delivery Month(s)"** — based on `Target Month` (the month the target delivery window falls in, not the order date month)
- Empty selection = show all months (caption "Showing all months")

#### How orders enter this tab
`kpi_df = df[df["Delivery Status"].notna()]`
An order has a non-null `Delivery Status` only if:
- It has both a Plan value and an Order Date, AND
- It is not a DOT order

#### KPI metrics (2 rows of 4)
Row 1:

| Metric | Definition |
|--------|-----------|
| Eligible Orders | Non-cancelled, non-delayed rows with a Delivery Status |
| On Time | Delivery Status is "On time" or "Early" |
| Late | Delivery Status is "Late" |
| Not Delivered | Delivery Status is "Not Delivered" |

Row 2:

| Metric | Definition |
|--------|-----------|
| On-Time % | (On time + Early) / Eligible Orders |
| Avg Days Late | Mean of `Days Late` for Late orders |
| Cancelled | Count of Delivery Status == "Cancelled" |
| Excluded - Delayed by Customer | Count of "Excluded - Delayed by Customer" |

#### Charts
1. **Delivery Status Breakdown** (pie chart) — built from `kpi_df` minus delayed-by-customer rows. Shows Cancelled as its own grey slice.
2. **On-Time % by Channel** (bar chart) — from `eligible` only; y-axis 0–110%, percent labels outside bars

#### Channel KPI Breakdown (table)
Per-channel counts of On time / Early / Late / Not Delivered / Total + On-Time %.

#### Order-Level Detail (table)
All `kpi_df` rows sorted by Delivery Status. Columns: SO, Customer Name, Plan, Channel, Order Date, Target Week Start, Target Week End, Delivery Date, Delivery Status, Days Late.

---

### Tab 3 — DOT Orders

#### Base dataset
`dot_all = df[df["DOT Status"].notna()]`
Includes all DOT orders (Status starts with "DOT"), including cancelled ones (which get `DOT Status = "Cancelled"`).

#### Filters
- **View radio:** Weekly / Monthly / Yearly / All Time
- **Multi-select** (conditional, same pattern as Orders tab) — filters by `Order Date` period
- No selection → empty `dot_df` (info not shown; KPI cards show zeros)

#### KPI metrics (2 rows)
Row 1 (4 columns): Total Orders · Total Units · On-Time % · Avg Days Late
Row 2 (5 columns): On Time · Early · Late · Not Delivered · **Cancelled**

#### Charts
1. **Orders by DOT Type & Status** (stacked bar) — x = DOT type (Status column), colour = DOT Status
2. **On-Time % by DOT Type** (bar) — built from delivered orders only (`DOT Status` in `["On time","Early","Late"]`), shows percent labels
3. **DOT Orders Over Time** (stacked bar, Monthly/Yearly/All Time views only) — uses full `dot_all`, not filtered subset

Color map used across all DOT charts:
```python
{
    "On time":       "#2ecc71",
    "Early":         "#27ae60",
    "Late":          "#e74c3c",
    "Not Delivered": "#95a5a6",
    "Cancelled":     "#bdc3c7",
}
```

#### DOT Type Summary (table)
One row per DOT type. Columns: Status · Orders · Units · On Time · Early · Late · Not Delivered · Cancelled · On-Time %.
On-Time % denominator = `On Time + Early + Late` (delivered only).

#### Order Detail (table + expandable rows)
- Search box by SO number
- Table view with columns: SO, Customer Name, DOT Type, Order Date, Target Week Start, Target Week End, Delivery Date, Delivery Status, Days Late, Chairs
- Expandable row per order showing DOT SKU line items from the Data worksheet
- Icons: ✅ On time · 🟢 Early · 🔴 Late · ⚪ Not Delivered · 🚫 Cancelled

#### Untagged DOT Orders section (bottom of tab)
- **🔄 Refresh untagged check** button — clears cache and reruns
- Detection: SOs that appear in the Data worksheet (have DOT SKUs) but are **not** tagged with a DOT Status in Orders Plan
- Discarded orders tracked in `st.session_state["discarded_dot_sos"]` (persists for session only)
- Per-row: checkbox, SO + customer name + SKU list, tag selectbox, Discard button
- Bottom action bar: bulk tag selectbox · **Apply Selected (N)** · **Apply All** · **Discard Selected (N)**
- Apply writes to the Google Sheet via `write_dot_tags()` then clears cache and reruns

---

## 9. Deployment Setup

### GitHub
- Repository was pushed with the following files tracked:
  - `dashboard.py`, `extract.py`, `requirements.txt`, `.gitignore`
  - `compare_apr26.py`, `check_units.py`
- **`credentials.json` is NOT committed** (in `.gitignore`)
- To find the repo URL: `git remote -v` from the project directory

### Streamlit Cloud
- Deployed from the GitHub repository
- **Entry point:** `dashboard.py`
- **Python version:** 3.14 (or latest available; matches local)
- **Secrets:** Configured under App Settings → Secrets (see Section 5 for exact format)
- **Auto-deploy:** Streamlit Cloud redeploys automatically on every push to the main branch

---

## 10. How to Run Locally

```bash
# 1. Clone (if needed)
git clone <repo-url>
cd orders-dashboard

# 2. Install dependencies
pip install -r requirements.txt

# 3. Ensure credentials.json is in the project root
#    (copy from secure storage — never commit this file)

# 4. Run the app
streamlit run dashboard.py
```

The app opens at `http://localhost:8501`.

---

## 11. How to Deploy Updates

```bash
# 1. Make changes to dashboard.py / extract.py

# 2. Stage and commit
git add dashboard.py extract.py        # add other files as needed
git commit -m "Brief description of change"

# 3. Push to GitHub (triggers auto-deploy on Streamlit Cloud)
git push origin main
```

Streamlit Cloud picks up the push within ~30 seconds and redeploys. Monitor the deployment log in the Streamlit Cloud dashboard if anything goes wrong.

> **Never** commit `credentials.json`. If it is accidentally staged, run `git reset HEAD credentials.json` before committing.

---

## 12. Known Issues & Recent Fixes

### Fixed during this session (all applied, no regressions)

| # | Severity | Description | Fix Applied |
|---|----------|-------------|-------------|
| C-1/C-2 | Critical | `sel_orders_week/month/year` only defined inside if/elif branches — `NameError` risk if view switched | Initialised all three to `[]` before the if/elif block; removed `locals().get()` usage |
| C-3 | Critical | `classify_dot()` applied to empty DataFrame crashes on `.columns` assignment | Added `if dot_mask.any():` guard with explicit else branch |
| C-4 | Critical | `deliverable.replace(0, pd.NA)` on int64 Series causes type conflict | Changed to `.astype(float).replace(0.0, np.nan)` |
| C-5/C-6 | Critical | `width='stretch'` invalid kwarg for `st.plotly_chart()` and `st.dataframe()` | Replaced all 14 occurrences with `use_container_width=True` |
| M-1 | Medium | "Canceled" (one-L) vs "Cancelled" (two-L) inconsistency | `_is_canceled()` helper checks both spellings across Status and CS Updated Date |
| M-2 | Medium | DOT orders were leaking into Delivery KPI as "Not Delivered" | `classify_delivery()` now returns `None` for any Status starting with "DOT" |
| M-4/M-5 | Medium | `.reindex(series)` used Series integer index as labels | Changed to `.reindex(series.tolist(), ...)` to pass actual string values |
| Mi-2 | Minor | `dot_target_week()` compared `status.upper() == "DOT-V2.1"` without stripping whitespace | Added `.strip()` before `.upper()` |

### Cancelled order handling (applied)
- Cancelled orders now use `"Cancelled"` as a first-class `Delivery Status` / `DOT Status` value (not hidden in "Excluded - Canceled")
- `_is_canceled()` checks BOTH `Status` and `CS Updated Date` columns — a cancelled tag in either column is respected
- In the Delivery KPI: Cancelled orders appear as their own pie slice (grey), have a dedicated metric card, and are visible in the order-level detail table
- In DOT Orders: Cancelled orders appear in all KPI cards (dedicated "Cancelled" metric), charts, and summary table with colour `#bdc3c7`

### Not yet addressed (lower priority)
| # | Description |
|---|-------------|
| M-7 | `load_dot_items()` and `load_unit_counts()` use `pd.DataFrame(rows[1:], columns=rows[0])` with no header deduplication — would fail silently if the Data sheet has duplicate column names |

---

## 13. Pending Tasks

No outstanding features were formally planned at the close of this session. The dashboard passed its QA audit and all critical/medium bugs were fixed. Potential future enhancements discussed informally:

- **Export button** — download filtered Orders or KPI data as CSV
- **Date range filter** — arbitrary from/to date picker instead of week/month/year buckets
- **Email alerts** — notify team when overdue orders spike
- **DOT trend vs target** — overlay target on-time % line on the trend chart

---

## 14. Key Gotchas & Non-Obvious Behaviour

1. **Worksheet name has a trailing space.** The "Orders Plan " tab has a space at the end. `load_orders()` passes this verbatim. If the sheet is ever renamed, update the `worksheet_name` default in `extract.py` and the `ws = sheet.worksheet("Orders Plan ")` call.

2. **"Delivered" metric in the Orders tab counts by Delivery Date, not Order Date.** An order placed in April but delivered in May will count toward the May "Delivered" metric. This is intentional — it reflects when deliveries actually happened in the period, not when they were placed.

3. **The Orders tab trend chart always shows full history**, even when a period filter is applied. The two pie charts and the table do respect the filter. This is a deliberate design choice to keep the trend as a consistent reference.

4. **Cache TTL is 5 minutes.** If someone updates the sheet, the dashboard won't reflect it until either 5 minutes pass or the 🔄 Refresh button is clicked. The Refresh button immediately clears the entire cache.

5. **`st.session_state["discarded_dot_sos"]`** is a Python set that persists only for the browser session. If the user refreshes the page or a new session starts, discarded orders reappear. This is intentional — discards are not written to the sheet.

6. **Total_Units defaults to 1** when an SO has no corresponding row in the Data worksheet. This is a safe fallback but means the "Total Units" KPI in the DOT tab may undercount if the Data sheet is incomplete.

7. **Days late excludes Fridays.** When computing how many days late a delivery is, Fridays (the day off) are subtracted from the total calendar-day count. Same logic applies for DOT days late.

8. **On-Time % denominators differ between tabs:**
   - Delivery KPI: denominator = all eligible orders (including "Not Delivered")
   - DOT tab: denominator = delivered orders only (On time + Early + Late), NOT including Not Delivered or Cancelled

9. **The compare_apr26.py script** is a frozen snapshot from when the KPI logic was being validated against an Excel baseline for April 2026. The expected values in the print statements (e.g. `(Excel: 71)`) are that historical reference — they will not match if run today since the sheet has since been updated.

10. **Python 3.14 is used locally** (confirmed by `__pycache__` bytecode files). Streamlit Cloud uses whatever Python version is configured in the app settings. Ensure they match if adding new dependencies.
