# Orders Dashboard — Complete Project Summary

> **Purpose of this document:** Full onboarding reference for a new Claude Code session or developer. Covers every file, function, data source, business rule, and deployment detail. Assumes zero prior context. Last updated: 2026-05-19.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [File Structure](#2-file-structure)
3. [Tech Stack & Dependencies](#3-tech-stack--dependencies)
4. [Data Sources](#4-data-sources)
5. [Credentials & Authentication](#5-credentials--authentication)
6. [Code Architecture — extract.py](#6-code-architecture--extractpy)
7. [Code Architecture — dashboard.py](#7-code-architecture--dashboardpy)
8. [Business Logic & Rules](#8-business-logic--rules)
9. [Dashboard Features — Tab by Tab](#9-dashboard-features--tab-by-tab)
10. [Deployment Setup](#10-deployment-setup)
11. [How to Run Locally](#11-how-to-run-locally)
12. [How to Deploy Updates](#12-how-to-deploy-updates)
13. [Known Issues & Bug History](#13-known-issues--bug-history)
14. [Pending Tasks](#14-pending-tasks)
15. [Key Gotchas & Non-Obvious Behaviour](#15-key-gotchas--non-obvious-behaviour)

---

## 1. Project Overview

### What it does
A Streamlit web dashboard that pulls live order data from a Google Sheet and presents three operational views:

| Tab | Purpose |
|-----|---------|
| **Orders** | High-level order volume, value, customer breakdown, and per-order details |
| **Delivery KPI** | On-time delivery performance against target windows, by channel and month |
| **DOT Orders** | Dedicated KPI tracking for DOT chair orders (GO / LITE / V2.1) |

### Who uses it
The operations team at **DECI** to monitor fulfilment performance, flag delayed/cancelled orders, and tag untagged DOT orders directly from the dashboard back to the Google Sheet.

### What "DOT" means
DOT is a product line of **chairs** (not sofas). Three variants:

| Type | Delivery target |
|------|----------------|
| DOT-GO | 2 weeks from order date |
| DOT-LITE | 2 weeks from order date |
| DOT-V2.1 | 8 weeks from order date |

### Business context
- All order data lives in a Google Sheet maintained by the ops team.
- The dashboard reads the sheet on load and caches it for 5 minutes.
- The only write-back operation is tagging untagged DOT orders (updating the Status column in the sheet via `write_dot_tags()`).
- The working week is **Saturday → Thursday**. Friday is the day off (Egypt). All week calculations use Saturday as the week-start.

---

## 2. File Structure

```
orders-dashboard/
├── dashboard.py          # Main Streamlit app — all UI, charts, filters, business logic (~831 lines)
├── extract.py            # Google Sheets I/O — all reads and writes to the sheet
├── requirements.txt      # Pinned Python dependencies for Streamlit Cloud
├── PROJECT_SUMMARY.md    # This file — full project onboarding reference
├── credentials.json      # GCP service-account key (LOCAL ONLY — never commit)
├── .gitignore            # Excludes credentials.json, __pycache__, .env
├── compare_apr26.py      # One-off diagnostic: validated Apr 2026 KPI against Excel baseline
├── check_units.py        # One-off diagnostic: audited DOT unit counts from Data worksheet
└── __pycache__/          # Python bytecode cache (auto-generated, not committed)
```

### File roles

**`dashboard.py`** — The entire Streamlit app. Contains all helper functions, the cached data loader `get_data()`, and the three tab sections. Nothing is imported from here by other files.

**`extract.py`** — Pure I/O layer. No Streamlit UI. Called by `dashboard.py`. Handles all Google Sheets API interactions (read and write). Can be run standalone for testing (`python extract.py`).

**`requirements.txt`** — Exact pinned versions for all dependencies. Used by Streamlit Cloud to build the deployment environment.

**`credentials.json`** — GCP service-account JSON key for local development. Never committed (in `.gitignore`). On Streamlit Cloud this is replaced by `st.secrets`.

**`compare_apr26.py`** — Standalone script, not part of the live app. Used once to verify April 2026 KPI numbers matched an Excel reference. Safe to ignore but useful as a classification logic reference.

**`check_units.py`** — Standalone script, not part of the live app. Used once to audit how many DOT orders existed in the Data tab vs Orders Plan and identify orders with multiple SKUs.

---

## 3. Tech Stack & Dependencies

### Key libraries

| Library | Version | Role |
|---------|---------|------|
| `streamlit` | 1.57.0 | Web UI framework |
| `pandas` | 3.0.3 | Data manipulation |
| `numpy` | 2.4.5 | Numeric operations |
| `plotly` | 6.7.0 | Interactive charts |
| `gspread` | 6.2.1 | Google Sheets API client |
| `google-auth` | 2.53.0 | OAuth2 / service-account auth |

### Full pinned dependency list (requirements.txt)
```
altair==6.1.0, anyio==4.13.0, attrs==26.1.0, blinker==1.9.0,
cachetools==7.1.2, certifi==2026.4.22, cffi==2.0.0,
charset-normalizer==3.4.7, click==8.4.0, colorama==0.4.6,
cryptography==48.0.0, gitdb==4.0.12, GitPython==3.1.50,
google-auth==2.53.0, google-auth-oauthlib==1.4.0, gspread==6.2.1,
h11==0.16.0, httptools==0.7.1, idna==3.15, itsdangerous==2.2.0,
Jinja2==3.1.6, jsonschema==4.26.0, jsonschema-specifications==2025.9.1,
MarkupSafe==3.0.3, narwhals==2.21.2, numpy==2.4.5, oauthlib==3.3.1,
packaging==26.2, pandas==3.0.3, pillow==12.2.0, plotly==6.7.0,
protobuf==7.34.1, pyarrow==24.0.0, pyasn1==0.6.3,
pyasn1_modules==0.4.2, pycparser==3.0, pydeck==0.9.2,
python-dateutil==2.9.0.post0, python-multipart==0.0.28,
referencing==0.37.0, requests==2.34.2, requests-oauthlib==2.0.0,
rpds-py==0.30.0, six==1.17.0, smmap==5.0.3, starlette==1.0.0,
streamlit==1.57.0, tenacity==9.1.4, toml==0.10.2,
typing_extensions==4.15.0, tzdata==2026.2, urllib3==2.7.0,
uvicorn==0.47.0, watchdog==6.0.0, websockets==16.0
```

### Critical Streamlit 1.57.0 API notes
- Use `use_container_width=True` for `st.plotly_chart()` and `st.dataframe()`. **Do not use `width='stretch'`** — that is invalid for these functions and causes silent failures.
- `@st.cache_data(ttl=300)` is the correct caching decorator.
- `st.rerun()` replaces the old `st.experimental_rerun()`.

### Python version
CPython 3.14 (confirmed by `__pycache__/dashboard.cpython-314.pyc`).

---

## 4. Data Sources

### Google Sheet
```
URL:  https://docs.google.com/spreadsheets/d/1cEpLqAb_sqOoGxQ7GezAgyAlfQz4fOlpPVRuX-mimaA/edit
Code: SHEET constant at top of dashboard.py and in utility scripts
```

---

### Worksheet 1 — `"Orders Plan "` *(trailing space in the name is real)*

Loaded by `load_orders()`. Row 1 = header, rows 2+ = data. All values returned as raw strings.

Columns used by the dashboard (sheet has more columns that are ignored):

| Column | Raw type | Notes |
|--------|----------|-------|
| `SO` | string | Sales Order ID, e.g. `S0008499`. Primary key. Blank rows filtered out. |
| `Customer Name` | string | Customer full name |
| `Order Date` | date string `DD-Mon-YYYY` | Parsed with `dayfirst=True` |
| `Delivery Date` | date string `DD-Mon-YYYY` | Blank if not yet delivered |
| `Status` | string | See Status values table below |
| `CS Updated Date` | string | Can contain `"Canceled"` / `"Cancelled"` — used for cancellation detection alongside Status |
| `Plan` | string | `"Online/Fast Track"` or `"Plan Month"` — determines delivery target window |
| `Total Order Value` | numeric string (may contain commas) | Order value in EGP |
| `Order Overdue` | numeric string (may contain commas) | Overdue amount in EGP |
| `Notes` | string | Free-text notes |

Other columns present in the sheet but not used in KPI logic: `Online/Offline`, `7 weeks\nBracket`, `Ready`, `Readiness Week`, `Customer Paid`, `COD`, `Feedback Form`, `Logistics Comment`, `%`, `Approval Staus`, `Address`, `FT Date`, `3 weeks`, `4 Weeks`, `5 Weeks`, `6 weeks`.

#### Status column values

| Value | Meaning |
|-------|---------|
| `DOT-GO` | DOT chair, GO variant — 2-week delivery target |
| `DOT-LITE` | DOT chair, LITE variant — 2-week delivery target |
| `DOT-V2.1` | DOT chair, V2.1 variant — 8-week delivery target |
| `Canceled` | Cancelled (one-L US spelling) |
| `Delayed` | Order delayed by company |
| `Delayed by Customer` | Delay caused by customer |
| *(anything else / blank)* | Regular in-progress or delivered order |

#### Cancellation detection — IMPORTANT
A cancelled order may have the cancellation tag in **`Status`**, **`CS Updated Date`**, or **both**. The code always checks both columns via `_is_canceled()`. The `Plan` column is **not** used for cancellation detection — it only determines delivery target windows.

---

### Worksheet 2 — `"Data"`

Loaded by `load_dot_items()` and `load_unit_counts()`. Row 1 = header, rows 2+ = data.

| Column | Type | Description |
|--------|------|-------------|
| `Order` | string | Sales Order ID — maps to `SO` in Orders Plan |
| `Item Sku` | string | SKU code, e.g. `DOT-GO-BLK-001`, `DOT-V2.1-WHT` |
| `Item Name` | string | Human-readable product name |
| `Item QTY` | numeric string | Quantity of this SKU |

Only rows where `Item Sku` contains `"DOT"` (case-insensitive) are used. Non-DOT rows are ignored.

---

## 5. Credentials & Authentication

### Service account
| Field | Value |
|-------|-------|
| Email | `decioperations@glowing-run-496613-t9.iam.gserviceaccount.com` |
| GCP Project | `glowing-run-496613-t9` |
| Key file (local) | `credentials.json` in project root |

### `_get_creds(scopes)` logic
```
1. Try st.secrets["gcp_service_account"]          ← Streamlit Cloud path
   - Converts TOML literal \n → real newlines in private_key
2. Fall back to credentials.json file              ← Local dev path
3. Raise RuntimeError with instructions if neither found
```

### Scopes
| Operation | Scopes used |
|-----------|------------|
| Read (`load_orders`, `load_dot_items`, `load_unit_counts`) | `spreadsheets.readonly` + `drive.readonly` |
| Write (`write_dot_tags`) | `spreadsheets` + `drive` (full access) |

### Streamlit Cloud secrets (App Settings → Secrets)
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
> The `private_key` must keep `\n` as literal backslash-n inside the TOML string. The code converts them back to real newlines automatically.

---

## 6. Code Architecture — `extract.py`

### `_get_creds(scopes) → Credentials`
Credential loader. Tries Streamlit secrets first, falls back to `credentials.json`. See Section 5.

### `load_orders(sheet_url_or_name, worksheet_name="Orders Plan ") → pd.DataFrame`
Reads the Orders Plan worksheet. Returns all rows as raw strings.
- Accepts a full URL (detected by `startswith("http")`) or a sheet name string.
- Deduplicates column headers: blank cols become `_col{i}`, duplicate names get `_{n}` suffix.
- Returns a DataFrame with original string values — no type conversion here.

### `load_dot_items(sheet_url_or_name) → pd.DataFrame`
Reads the "Data" worksheet. Filters to rows where `Item Sku` contains "DOT" (case-insensitive).
- Returns columns: `SO`, `Item Sku`, `Item Name`, `Item QTY` (numeric).
- `Item QTY` is cast to numeric with `errors="coerce"`, NaN filled with 0.

### `load_unit_counts(sheet_url_or_name) → pd.DataFrame`
Reads the "Data" worksheet. Aggregates per-SO DOT unit counts.
- Returns columns: `SO`, `SKUs` (count of DOT SKU rows), `Total_Units` (sum of Item QTY).
- Only DOT SKUs counted (same `"DOT"` filter as `load_dot_items`).

### `write_dot_tags(sheet_url_or_name, so_tag_map, worksheet_name="Orders Plan ") → list`
Writes DOT type tags back to the Status column in the sheet.
- `so_tag_map`: dict of `{SO_string: tag_string}`, e.g. `{"S0008814": "DOT-GO"}`.
- Finds the row number for each SO by scanning the SO column.
- Uses `ws.batch_update()` — all writes in a single API call.
- Returns list of SO strings that were actually found and updated in the sheet.
- Uses full write scopes (not read-only).

---

## 7. Code Architecture — `dashboard.py`

### Constants
```python
SHEET = "https://docs.google.com/spreadsheets/d/1cEpLqAb_sqOoGxQ7GezAgyAlfQz4fOlpPVRuX-mimaA/edit"
```

### Global layout
```python
st.set_page_config(page_title="Orders Dashboard", layout="wide")
# Title + Refresh button in a 9:1 column split
col_title, col_refresh = st.columns([9, 1])
```
The 🔄 Refresh button calls `st.cache_data.clear()` then `st.rerun()`.

---

### Helper functions

#### `week_start(date) → pd.Timestamp`
Returns the Saturday that begins the Sat–Thu working week containing `date`.
```python
days_back = (date.weekday() - 5) % 7
return date - pd.Timedelta(days=int(days_back))
```
Weekday reference: Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, **Sat=5**, Sun=6.

#### `target_week(order_date, plan) → (t_start, t_end)`
Computes expected delivery window for a regular (non-DOT) order.
- `plan == "Online/Fast Track"` → +4 weeks
- any other plan → +8 weeks
- Returns `(week_start(expected), week_start(expected) + 5 days)` — i.e. Saturday to Thursday inclusive.

#### `dot_target_week(order_date, status) → (t_start, t_end)`
Computes expected delivery window for a DOT order.
- `status.strip().upper() == "DOT-V2.1"` → +8 weeks
- anything else (DOT-GO, DOT-LITE) → +2 weeks
- Note: `.strip()` applied before comparison to handle trailing whitespace in the sheet.

#### `_is_canceled(status_val, cs_updated_date_val) → bool`
Central cancellation check. Returns True if either argument is a cancellation signal.
```python
str(status_val).strip().lower()          in ("canceled", "cancelled") or
str(cs_updated_date_val).strip().lower() in ("canceled", "cancelled")
```
Handles both US spelling ("canceled") and UK spelling ("cancelled"). Called from `classify_dot`, `classify_delivery`, and `_flag`.

#### `classify_dot(row) → (dot_status, t_start, t_end)`
Classifies a single DOT order row. Returns a 3-tuple used to populate `DOT Status`, `DOT Target Start`, `DOT Target End`.

Logic (in order):
1. `Order Date` is NaT → `(None, NaT, NaT)`
2. `_is_canceled(Status, CS Updated Date)` → `("Cancelled", NaT, NaT)`
3. Compute target window via `dot_target_week()`
4. No `Delivery Date` → `"Not Delivered"`
5. Delivery within `[t_start, t_end]` → `"On time"`
6. Delivery before `t_start` → `"Early"`
7. Delivery after `t_end` → `"Late"`

#### `classify_delivery(row) → str | None`
Classifies a single order for the regular Delivery KPI. Returns a status string or `None`.

Logic (in order):
1. Status starts with `"DOT"` → `None` (DOT orders excluded from regular KPI entirely)
2. `_is_canceled(Status, CS Updated Date)` → `"Cancelled"`
3. Status is `"Delayed by Customer"` or `"Delayed"` → `"Excluded - Delayed by Customer"`
4. Plan is blank or `"nan"` → `None` (not in KPI scope)
5. `Order Date` is NaT → `None`
6. Compute target window via `target_week()`
7. No `Delivery Date` → `"Not Delivered"`
8. Delivery within window → `"On time"`
9. Delivery before window → `"Early"`
10. Delivery after window → `"Late"`

---

### `get_data() → pd.DataFrame` *(cached 5 min)*

The master data loader and transformer. Called once at app start; result cached for 300 seconds.

**Steps performed:**
1. `load_orders(SHEET)` — raw DataFrame from Google Sheets
2. Parse `Order Date` and `Delivery Date` to datetime (`dayfirst=True`)
3. Compute `Month` (`dt.to_period("M").dt.to_timestamp()`), `Week` (`week_start()`), `Year` (`dt.year`)
4. Compute `Flag` column via `_flag(row)` applied row-wise:
   - Checks `_is_canceled(Status, CS Updated Date)` → `"Cancelled"`
   - Status in `("Delayed", "Delayed by Customer")` → `"Delayed"`
   - Status starts with `"DOT"` → the DOT type string (e.g. `"DOT-GO"`)
   - Otherwise → `""`
5. Parse `Total Order Value` — strip commas, cast to float
6. Compute `Channel`: `"Online/Fast Track"` if Plan == `"Online/Fast Track"`, else `"Plan Month"`
7. Compute `Target Week Start` / `Target Week End` via `target_week()` (NaT if no plan/order date)
8. Compute `Target Month` from `Target Week Start`
9. Compute `Delivery Status` via `classify_delivery()` row-wise
10. Compute `Days Late` — calendar days after Target Week End, minus Fridays in the gap
11. Merge unit counts from `load_unit_counts()` (`SKUs`, `Total_Units`; default 1 if missing)
12. Compute `DOT Status` / `DOT Target Start` / `DOT Target End` via `classify_dot()` for DOT rows only. Guard: if no DOT rows exist (`dot_mask.any() == False`), all three columns are set to `pd.NA` / `pd.NaT` to avoid empty DataFrame crash.
13. Compute `DOT Days Late` — same Friday-skipping logic as `Days Late`
14. Filter out rows where `SO` is blank

**Computed columns added to the DataFrame:**

| Column | Type | Description |
|--------|------|-------------|
| `Month` | Timestamp | First day of the month of Order Date |
| `Week` | Timestamp | Saturday start of the week of Order Date |
| `Year` | int | Year of Order Date |
| `Flag` | string | `"Cancelled"` / `"Delayed"` / `"DOT-GO"` etc. / `""` |
| `Total Order Value` | float | EGP value, commas stripped |
| `Channel` | string | `"Online/Fast Track"` or `"Plan Month"` |
| `Target Week Start` | Timestamp | Saturday of target delivery window |
| `Target Week End` | Timestamp | Thursday of target delivery window |
| `Target Month` | Timestamp | Month of Target Week Start |
| `Delivery Status` | string/None | Regular KPI classification |
| `Days Late` | float | Working days late (Fri excluded); NaN if not Late |
| `SKUs` | float | DOT SKU count from Data sheet (default 1) |
| `Total_Units` | float | Total DOT units from Data sheet (default 1) |
| `DOT Status` | string/NA | DOT-specific classification |
| `DOT Target Start` | Timestamp/NaT | Saturday of DOT target window |
| `DOT Target End` | Timestamp/NaT | Thursday of DOT target window |
| `DOT Days Late` | float | DOT working days late (Fri excluded); NaN if not Late |

---

### `fmt_dates(frame) → pd.DataFrame`
Returns a copy of a DataFrame with all `datetime64` columns formatted as `"DD-Mon-YYYY"` strings, `NaT` → `""`. Applied before every `st.dataframe()` call to prevent timestamp display.

### `get_dot_items() → pd.DataFrame` *(cached 5 min)*
Thin wrapper around `load_dot_items(SHEET)`. Returns one row per DOT SKU line item. Used in the expandable order rows in the DOT tab.

---

### Data flow summary

```
Google Sheet "Orders Plan "    Google Sheet "Data"
        │                               │
        ▼                               ▼
  load_orders()              load_unit_counts()  load_dot_items()
        │                               │               │
        └───────────────────────────────┘               │
                        │                               │
                   get_data()                    get_dot_items()
              (cached 5 minutes)               (cached 5 minutes)
                        │                               │
                        ▼                               │
              df  (enriched DataFrame)                  │
                        │                               │
          ┌─────────────┼──────────────┐                │
          ▼             ▼              ▼                 │
      tab_orders    tab_kpi        tab_dot ◄─────────────┘
                                (uses dot_items_df
                                 for SKU expanders
                                 and untagged detection)
```

---

## 8. Business Logic & Rules

### Week system
Working week = **Saturday to Thursday** (Friday off). `week_start(date)` returns the Saturday of the week containing any given date.

### Delivery target windows

**Regular orders:**
| Plan value | Target offset | Window |
|------------|--------------|--------|
| `Online/Fast Track` | Order Date + 4 weeks | `week_start(date)` → +5 days (Sat–Thu) |
| `Plan Month` (or anything else) | Order Date + 8 weeks | Same formula |

**DOT orders:**
| Status | Target offset |
|--------|--------------|
| `DOT-GO` | Order Date + 2 weeks |
| `DOT-LITE` | Order Date + 2 weeks |
| `DOT-V2.1` | Order Date + 8 weeks |

### Cancellation detection
An order is cancelled if `Status` OR `CS Updated Date` contains `"Canceled"` or `"Cancelled"` (case-insensitive, whitespace-trimmed). The `Plan` column is **not** checked for cancellation.

### Delivery classification results

| Result | Condition |
|--------|-----------|
| `"On time"` | Delivery Date within `[Target Week Start, Target Week End]` inclusive |
| `"Early"` | Delivery Date before Target Week Start |
| `"Late"` | Delivery Date after Target Week End |
| `"Not Delivered"` | Delivery Date is blank/NaT |
| `"Cancelled"` | Status or CS Updated Date signals cancellation |
| `"Excluded - Delayed by Customer"` | Status is `"Delayed"` or `"Delayed by Customer"` |
| `None` | DOT order, or no Plan assigned, or no Order Date → out of regular KPI scope |

### Days late calculation
```
days_late = (Delivery Date - Target Week End).days
           - (count of Fridays between Target Week End and Delivery Date, exclusive/inclusive)
```
Fridays are subtracted because they are non-working days. Same formula for DOT Days Late.

### On-Time % formulas

**Delivery KPI tab:**
```
On-Time % = (On time + Early) / eligible_orders
```
`eligible_orders` = rows with Delivery Status not in `{Cancelled, Excluded - Delayed by Customer}` and not None.

**DOT Orders tab:**
```
On-Time % = On time / (On time + Early + Late)
```
Denominator is delivered orders only — `Not Delivered` and `Cancelled` are excluded.

### Untagged DOT detection
An order is "untagged" if:
- It appears in the Data worksheet (has at least one DOT SKU), AND
- Its `Status` in Orders Plan does **not** start with `"DOT"`

These are surfaced in the DOT tab for manual tagging.

### DOT type suggestion logic
When an untagged order has exactly one DOT variant across all SKUs:
- Any SKU contains `"V2.1"` → suggest `DOT-V2.1`
- Any SKU contains `"LITE"` → suggest `DOT-LITE`
- Any SKU contains `"GO"` → suggest `DOT-GO`
- Mixed/unrecognised or multiple variants → no suggestion (user selects manually)

---

## 9. Dashboard Features — Tab by Tab

### Global elements
- **Page:** Wide layout, title "Orders Dashboard"
- **🔄 Refresh button** (top-right, 1/10 of header width): clears all cached data and reruns immediately. Forces fresh pull from Google Sheets.
- **Cache TTL:** 5 minutes on both `get_data()` and `get_dot_items()`.

---

### Tab 1 — Orders

#### Filters (collapsible `st.expander("🔽 Filters")`)
- **Period radio:** Weekly / Monthly / Yearly / All Time (horizontal)
- **Multi-select** shown based on selection:
  - Weekly → weeks displayed as `"DD Mon YYYY – DD Mon YYYY"` (Sat–Thu)
  - Monthly → months displayed as `"Mon YYYY"`
  - Yearly → years as integers
  - Default: most recent period pre-selected
  - Empty selection → `st.info` message shown; all metrics and table show zero/empty

#### KPI metrics (4 columns)
| Metric | Calculation |
|--------|------------|
| Total Orders | `len(filtered)` — orders placed in selected period |
| Unique Customers | `filtered["Customer Name"].nunique()` |
| Total Order Value | Sum of `Total Order Value` in selected period, formatted with commas |
| Delivered | Count of orders whose **Delivery Date** (not Order Date) falls in the selected period |

> The "Delivered" metric scans the full `df` (all dates), not just `filtered`, to count by delivery date. An order placed in April but delivered in May counts in May's "Delivered" total.

#### Charts
1. **Orders per Month** (bar chart) — always shows **full history** from `df` regardless of filter. x = `"Mon YYYY"`, y = order count.
2. **Orders by Status** (pie chart) — from `filtered`. Blank status shown as `"No Status"`.
3. **Top 10 Customers** (horizontal bar chart) — from `filtered`. Sorted descending by order count.

#### Order Details table
- **Search box** — case-insensitive text search across all displayed columns (SO, Customer Name, Notes, etc.)
- **Columns:** SO · Customer Name · Order Date · Flag · Total Order Value · Order Overdue · Delivery Date · Notes
- `Total Order Value` and `Order Overdue` formatted as `"EGP X,XXX"` (blank if zero or null)
- Row count caption above table
- `fmt_dates()` applied before display

---

### Tab 2 — Delivery KPI

#### Filter
- **Multi-select "Delivery Month(s)"** — based on `Target Month` (the month the target delivery window falls in, derived from Target Week Start — not the order date)
- Empty selection = show all months; caption "Showing all months" shown

#### Scope
`kpi_df = df[df["Delivery Status"].notna()]` — orders that have a Delivery Status (have a Plan + Order Date and are not DOT orders).

#### KPI metrics (2 rows of 4 columns)
Row 1:
| Metric | Definition |
|--------|-----------|
| Eligible Orders | Rows not cancelled and not delayed-by-customer |
| On Time | Delivery Status is `"On time"` or `"Early"` |
| Late | Delivery Status is `"Late"` |
| Not Delivered | Delivery Status is `"Not Delivered"` |

Row 2:
| Metric | Definition |
|--------|-----------|
| On-Time % | (On time + Early) / Eligible Orders |
| Avg Days Late | Mean of `Days Late` for Late orders |
| Cancelled | Count of `Delivery Status == "Cancelled"` |
| Excluded - Delayed by Customer | Count of `"Excluded - Delayed by Customer"` |

#### Charts
1. **Delivery Status Breakdown** (pie chart) — built from `kpi_df` minus delayed-by-customer rows, so Cancelled appears as its own orange slice. Colour map:
   - On time `#2ecc71`, Early `#27ae60`, Late `#e74c3c`, Not Delivered `#95a5a6`, **Cancelled `#e67e22`**
2. **On-Time % by Channel** (bar chart) — from `eligible` only. Y-axis 0–110%, percent labels outside bars.

#### Channel KPI Breakdown table
Per-channel breakdown: On time / Early / Late / Not Delivered / Total / On-Time %.

#### Order-Level Detail table
Full `kpi_df` sorted by Delivery Status. Columns: SO · Customer Name · Plan · Channel · Order Date · Target Week Start · Target Week End · Delivery Date · Delivery Status · Days Late.

---

### Tab 3 — DOT Orders

#### Base dataset
`dot_all = df[df["DOT Status"].notna()]`
All orders where Status starts with "DOT". Includes Cancelled DOT orders (DOT Status = `"Cancelled"`). Adds `Order Week`, `Order Month`, `Order Year` columns.

#### Filters
- **View radio:** Weekly / Monthly / Yearly / All Time (horizontal)
- **Multi-select** (conditional, by Order Date period) — same pattern as Orders tab
- Empty selection → empty `dot_df`; KPI cards show zeros

#### KPI metrics (2 rows)
Row 1 (4 columns): Total Orders · Total Units · On-Time % · Avg Days Late

Row 2 (5 columns): On Time · Early · Late · Not Delivered · **Cancelled**

On-Time % denominator = `On time + Early + Late` (delivered orders only — Not Delivered and Cancelled excluded).

#### Colour map (used across all DOT charts)
```python
{
    "On time":       "#2ecc71",  # green
    "Early":         "#27ae60",  # dark green
    "Late":          "#e74c3c",  # red
    "Not Delivered": "#95a5a6",  # grey
    "Cancelled":     "#e67e22",  # orange
}
```

#### Charts
1. **Orders by DOT Type & Status** (stacked bar) — x = DOT type (Status column), colour = DOT Status
2. **On-Time % by DOT Type** (bar) — built from `delivered_only` (DOT Status in `["On time", "Early", "Late"]`), excludes Not Delivered and Cancelled from denominator. Shows percent labels outside bars.
3. **DOT Orders Over Time** (stacked bar) — Monthly / Yearly / All Time views only. Uses full `dot_all` (unfiltered), colour = DOT Status.

#### DOT Type Summary table
One row per DOT type. Columns: Status · Orders · Units · On Time · Early · Late · Not Delivered · **Cancelled** · On-Time %.
- On-Time % = On Time / (On Time + Early + Late), formatted `"X.X%"` or `"—"` if no delivered orders.

#### Order Detail table + expandable rows
- **Search box** — filters by SO number (case-insensitive contains)
- **Table:** SO · Customer Name · DOT Type · Order Date · Target Week Start · Target Week End · Delivery Date · Delivery Status · Days Late · Chairs
- **Expandable rows** — one per order, header shows: icon · SO · Customer · DOT type · units · target window · delivery date · status · days late
  - Icons: ✅ On time · 🟢 Early · 🔴 Late · ⚪ Not Delivered · 🚫 Cancelled
  - Expander body: DOT SKU line items from the Data worksheet

#### Untagged DOT Orders section (bottom of DOT tab)
- **🔄 Refresh untagged check** button — clears cache and reruns
- Detection: `dot_so_in_data - dot_so_in_orders` (SOs in Data sheet but not tagged as DOT in Orders Plan)
- Discarded orders stored in `st.session_state["discarded_dot_sos"]` (set; session-only, not persisted)
- Per-row UI: checkbox · SO + customer name + SKU list · tag selectbox (pre-filled with suggestion) · Discard button
- Bottom action bar: bulk-tag selectbox · **Apply Selected (N)** · **Apply All** · **Discard Selected (N)**
- Apply buttons call `write_dot_tags()`, then `st.cache_data.clear()` + `st.rerun()`

---

## 10. Deployment Setup

### GitHub
- **Repository:** `https://github.com/Sarhan909090/orders-dashboard`
- **Branch:** `main`
- **Tracked files:** `dashboard.py`, `extract.py`, `requirements.txt`, `.gitignore`, `PROJECT_SUMMARY.md`, `compare_apr26.py`, `check_units.py`
- **Never committed:** `credentials.json` (in `.gitignore`)

### Streamlit Cloud
- **Auto-deploy:** Every push to `main` triggers a redeploy (~30 seconds)
- **Entry point:** `dashboard.py`
- **Secrets:** Configured under App Settings → Secrets (see Section 5 for exact TOML format)
- To find the live app URL: check the Streamlit Cloud dashboard at `https://share.streamlit.io`

---

## 11. How to Run Locally

```bash
# 1. Clone the repo
git clone https://github.com/Sarhan909090/orders-dashboard.git
cd orders-dashboard

# 2. Install dependencies
pip install -r requirements.txt

# 3. Place credentials.json in the project root
#    (copy from secure storage — NEVER commit this file)

# 4. Run the app
streamlit run dashboard.py
# Opens at http://localhost:8501
```

---

## 12. How to Deploy Updates

```bash
# 1. Make changes to dashboard.py and/or extract.py

# 2. Stage specific files (never use git add -A — risks committing credentials.json)
git add dashboard.py extract.py
# Add other changed files as needed:
# git add requirements.txt PROJECT_SUMMARY.md

# 3. Commit
git commit -m "Short description of what changed"

# 4. Push — this triggers automatic redeploy on Streamlit Cloud
git push origin main

# 5. After redeploy (~30 seconds), open the dashboard and click 🔄 Refresh
#    to clear the 5-minute data cache
```

> **Warning:** Never run `git add .` or `git add -A` — this risks staging `credentials.json`. Always add files individually by name.

---

## 13. Known Issues & Bug History

### All bugs fixed as of 2026-05-19

| # | Severity | Description | Fix |
|---|----------|-------------|-----|
| C-1/C-2 | Critical | `sel_orders_week/month/year` only defined inside if/elif → `NameError` on view switch | Initialised all three to `[]` before the block; replaced `locals().get()` with direct names |
| C-3 | Critical | `classify_dot().apply()` on empty DataFrame crashed on `.columns` assignment | `if dot_mask.any():` guard; else-branch assigns `pd.NA`/`pd.NaT` columns explicitly |
| C-4 | Critical | `deliverable.replace(0, pd.NA)` on int64 Series caused type conflict in division | `.astype(float).replace(0.0, np.nan)` — safe float division |
| C-5/C-6 | Critical | `width='stretch'` invalid kwarg for `st.plotly_chart()` and `st.dataframe()` | Replaced all 14 occurrences with `use_container_width=True` |
| M-1 | Medium | "Canceled" (one-L) vs "Cancelled" (two-L) inconsistency | `_is_canceled()` checks both spellings |
| M-2 | Medium | DOT orders leaked into Delivery KPI as "Not Delivered" | `classify_delivery()` returns `None` for any Status starting with "DOT" |
| M-3 | Medium | Cancellation only checked Status column; missed orders cancelled via CS Updated Date or Plan | `_is_canceled()` now checks Status + CS Updated Date; Plan was never the right column |
| M-4/M-5 | Medium | `.reindex(series)` used Series integer index as labels → wrong alignment | Changed to `.reindex(series.tolist(), ...)` |
| Mi-1 | Minor | DOT tab excluded cancelled orders entirely → showed as "Not Delivered" | `classify_dot()` checks `_is_canceled()` first; `dot_all` no longer hard-excludes cancelled |
| Mi-2 | Minor | `dot_target_week()` compared status without `.strip()` → whitespace in "DOT-V2.1 " broke 8-week logic | Added `.strip()` before `.upper()` comparison |
| Mi-3 | Minor | Cancelled pie slice in charts was same grey as Not Delivered — indistinguishable | Changed Cancelled colour to `#e67e22` (orange) in both color_maps |

### Nothing currently broken
All known issues have been resolved. The dashboard passed a full QA audit before production use.

---

## 14. Pending Tasks

No formally planned features are outstanding. Informal ideas discussed:

- **CSV export** — download button for filtered Orders or KPI table
- **Arbitrary date range filter** — from/to date picker instead of week/month/year buckets
- **Email/Slack alerts** — notify team when overdue orders spike past a threshold
- **DOT target line on trend chart** — overlay a target on-time % line on the DOT trend chart

---

## 15. Key Gotchas & Non-Obvious Behaviour

1. **"Orders Plan " has a trailing space.** The worksheet name in the Google Sheet is `"Orders Plan "` with a space at the end. `load_orders()` passes this verbatim. If the tab is ever renamed, update the `worksheet_name` default in `extract.py`.

2. **Cancellation is in Status OR CS Updated Date.** An order can be cancelled with the tag in either column or both. The `Plan` column is never checked for cancellation — it only determines delivery target windows.

3. **"Delivered" metric counts by Delivery Date, not Order Date.** An order placed in April but delivered in May counts toward May's Delivered total. This is intentional — it reflects when deliveries actually happened in the period.

4. **Orders tab trend chart always shows full history.** The "Orders per Month" bar chart uses the full unfiltered `df` regardless of what period filter is active. All other Orders tab elements (pie chart, top customers, detail table) respect the filter.

5. **Cache is 5 minutes.** Sheet updates are invisible until either 5 minutes pass or the 🔄 Refresh button is clicked. Always click Refresh after tagging untagged orders or after editing the sheet.

6. **`discarded_dot_sos` is session-only.** Orders discarded in the Untagged DOT section are stored in `st.session_state` and disappear on page refresh. They are not persisted anywhere — this is intentional (discards are a "not now" action, not a permanent decision).

7. **`Total_Units` defaults to 1.** If an SO has no corresponding row in the Data worksheet, it gets `Total_Units = 1` and `SKUs = 1`. The "Total Units" KPI in the DOT tab may undercount if the Data sheet is incomplete.

8. **Days late excludes Fridays.** Both `Days Late` (regular KPI) and `DOT Days Late` subtract the count of Fridays between the Target Week End and the Delivery Date from the raw calendar-day difference.

9. **On-Time % denominators differ between tabs.**
   - Delivery KPI: denominator = all eligible orders (including Not Delivered)
   - DOT tab: denominator = On time + Early + Late only (Not Delivered and Cancelled excluded)

10. **The two colour maps are separate variables.** The KPI tab defines its own `color_map` dict inside `with col_a:`. The DOT tab defines its own `color_map` at the `with tab_dot:` scope level. If you change a colour, update **both** maps. They are currently identical:
    ```python
    color_map = {
        "On time": "#2ecc71", "Early": "#27ae60",
        "Late": "#e74c3c", "Not Delivered": "#95a5a6",
        "Cancelled": "#e67e22",
    }
    ```

11. **`compare_apr26.py` reference values are frozen.** The expected values in that script (e.g. `(Excel: 71)`) reflect the state of the sheet in April 2026. Running the script today will produce different numbers because the sheet has since been updated.

12. **Python 3.14 is used locally.** Ensure the Streamlit Cloud Python version setting matches to avoid subtle compatibility issues with new dependencies.

13. **`write_dot_tags()` uses full (non-readonly) scopes.** The write function requests `spreadsheets` + `drive` full access. The read functions use readonly scopes. They are authenticated separately via `_get_creds(scopes)` — each function passes its own scopes, and `_get_creds` creates a fresh credential object each time.
