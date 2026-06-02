import gspread
from google.oauth2.service_account import Credentials
import pandas as pd

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _get_creds(scopes):
    """Load credentials from Streamlit secrets (cloud) or credentials.json (local)."""
    # ── Cloud: read from Streamlit secrets ──────────────────────────────────
    try:
        import streamlit as st
        if "gcp_service_account" in st.secrets:
            info = dict(st.secrets["gcp_service_account"])
            # TOML stores \n as a literal backslash-n — convert to real newlines
            if "private_key" in info:
                info["private_key"] = info["private_key"].replace("\\n", "\n")
            return Credentials.from_service_account_info(info, scopes=scopes)
    except Exception:
        pass

    # ── Local: read from credentials.json ───────────────────────────────────
    try:
        return Credentials.from_service_account_file("credentials.json", scopes=scopes)
    except FileNotFoundError:
        raise RuntimeError(
            "No credentials found. Either add [gcp_service_account] to Streamlit Secrets "
            "or place credentials.json in the project root for local development."
        )


def load_orders(sheet_url_or_name: str, worksheet_name: str = "Orders Plan ") -> pd.DataFrame:
    creds = _get_creds(SCOPES)
    client = gspread.authorize(creds)

    if sheet_url_or_name.startswith("http"):
        sheet = client.open_by_url(sheet_url_or_name)
    else:
        sheet = client.open(sheet_url_or_name)

    ws = sheet.worksheet(worksheet_name)
    rows = ws.get_all_values()
    if not rows:
        return pd.DataFrame()

    # Build unique headers — blank/duplicate cols get a positional suffix
    raw_headers = rows[0]
    seen = {}
    headers = []
    for i, h in enumerate(raw_headers):
        key = h.strip() if h.strip() else f"_col{i}"
        if key in seen:
            seen[key] += 1
            key = f"{key}_{seen[key]}"
        else:
            seen[key] = 0
        headers.append(key)

    return pd.DataFrame(rows[1:], columns=headers)


def load_dot_items(sheet_url_or_name: str) -> pd.DataFrame:
    """Return all DOT SKU line items from the Data worksheet (one row per SKU)."""
    creds = _get_creds(SCOPES)
    client = gspread.authorize(creds)

    if sheet_url_or_name.startswith("http"):
        sheet = client.open_by_url(sheet_url_or_name)
    else:
        sheet = client.open(sheet_url_or_name)

    ws = sheet.worksheet("Data")
    rows = ws.get_all_values()
    if not rows:
        return pd.DataFrame(columns=["SO", "Item Sku", "Item Name", "Item QTY"])

    data = pd.DataFrame(rows[1:], columns=rows[0])
    data["Item QTY"] = pd.to_numeric(data["Item QTY"], errors="coerce").fillna(0)

    dot_items = data[data["Item Sku"].str.upper().str.contains("DOT", na=False)].copy()
    return dot_items[["Order", "Item Sku", "Item Name", "Item QTY"]].rename(columns={"Order": "SO"})


def load_unit_counts(sheet_url_or_name: str) -> pd.DataFrame:
    """Return one row per SO with DOT-SKU count and total DOT units from the Data worksheet.
    Excludes Transportation and any non-DOT SKUs."""
    creds = _get_creds(SCOPES)
    client = gspread.authorize(creds)

    if sheet_url_or_name.startswith("http"):
        sheet = client.open_by_url(sheet_url_or_name)
    else:
        sheet = client.open(sheet_url_or_name)

    ws = sheet.worksheet("Data")
    rows = ws.get_all_values()
    if not rows:
        return pd.DataFrame(columns=["SO", "SKUs", "Total_Units"])

    data = pd.DataFrame(rows[1:], columns=rows[0])
    data["Item QTY"] = pd.to_numeric(data["Item QTY"], errors="coerce").fillna(0)

    # Keep only DOT SKUs
    dot_items = data[data["Item Sku"].str.upper().str.contains("DOT", na=False)]

    return (
        dot_items.groupby("Order")
        .agg(SKUs=("Item Sku", "count"), Total_Units=("Item QTY", "sum"))
        .reset_index()
        .rename(columns={"Order": "SO"})
    )


def write_dot_tags(sheet_url_or_name: str, so_tag_map: dict, worksheet_name: str = "Orders Plan ") -> list:
    """Update the Status cell for each SO in so_tag_map. Returns list of SOs updated."""
    write_scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = _get_creds(write_scopes)
    client = gspread.authorize(creds)

    if sheet_url_or_name.startswith("http"):
        sheet = client.open_by_url(sheet_url_or_name)
    else:
        sheet = client.open(sheet_url_or_name)

    ws = sheet.worksheet(worksheet_name)
    rows = ws.get_all_values()
    if not rows:
        return []

    headers = [h.strip() for h in rows[0]]
    try:
        so_col     = headers.index("SO")
        status_col = headers.index("Status")
    except ValueError as e:
        raise ValueError(f"Column not found: {e}. Headers: {headers}")

    so_row_map = {
        row[so_col].strip(): i + 2          # 1-based row index; +1 for header, +1 for gspread
        for i, row in enumerate(rows[1:])
        if so_col < len(row) and row[so_col].strip()
    }

    updates = [
        {"range": gspread.utils.rowcol_to_a1(so_row_map[so], status_col + 1), "values": [[tag]]}
        for so, tag in so_tag_map.items()
        if so in so_row_map
    ]
    if updates:
        ws.batch_update(updates)

    return [so for so in so_tag_map if so in so_row_map]


def write_production_status(sheet_url_or_name: str, so_updates: dict) -> list:
    """Write Status and Production Stage back to the '2026' worksheet.

    so_updates: {SO: {"Status": "...", "Production Stage": "..."}}
    Updates ALL rows with a matching SO (since one SO can span multiple item rows).
    Returns list of SOs that were found and updated.
    """
    write_scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds  = _get_creds(write_scopes)
    client = gspread.authorize(creds)
    sheet  = client.open_by_url(sheet_url_or_name) if sheet_url_or_name.startswith("http") \
             else client.open(sheet_url_or_name)

    ws   = sheet.worksheet("2026")
    rows = ws.get_all_values()
    if not rows:
        return []

    raw_headers = [h.strip() for h in rows[0]]
    try:
        so_col = raw_headers.index("f")
    except ValueError:
        raise ValueError("SO column ('f') not found in 2026 worksheet")

    status_col = raw_headers.index("Statues")     if "Statues"     in raw_headers else None
    stage_col  = raw_headers.index("Status Manu") if "Status Manu" in raw_headers else None

    # Build SO → list of 1-based row numbers (multiple item rows per SO)
    so_row_map: dict[str, list[int]] = {}
    for i, row in enumerate(rows[1:]):
        so = row[so_col].strip() if so_col < len(row) else ""
        if so:
            so_row_map.setdefault(so, []).append(i + 2)

    def _safe(v):
        """Convert None / 'None' to '' so gspread writes an empty cell."""
        return "" if (v is None or str(v) == "None") else str(v)

    updates = []
    updated = []
    for so, changes in so_updates.items():
        if so not in so_row_map:
            continue
        for row_num in so_row_map[so]:
            if "Status" in changes and status_col is not None:
                updates.append({
                    "range":  gspread.utils.rowcol_to_a1(row_num, status_col + 1),
                    "values": [[_safe(changes["Status"])]],
                })
            if "Production Stage" in changes and stage_col is not None:
                updates.append({
                    "range":  gspread.utils.rowcol_to_a1(row_num, stage_col + 1),
                    "values": [[_safe(changes["Production Stage"])]],
                })
        updated.append(so)

    if updates:
        ws.batch_update(updates)
    return updated


def setup_2026_formula(sheet_url_or_name: str) -> int:
    """Write live FILTER / VLOOKUP formulas into '2026' below the last existing row.

    Finds the last row in column A that has a non-empty SO, then writes formula
    cells at anchor = last_row + 2 (one blank separator row in between).

    FILTER formulas pull every column from 'Copy of Data per order' that isn't
    already in '2026' rows 2–last_row.
    VLOOKUP formulas in cols D and E pull Status / Production Stage from the
    'Order Status' tab so tracker saves appear automatically in '2026'.

    Returns the anchor row number used.
    """
    sheet = _open_sheet(sheet_url_or_name, _WRITE_SCOPES)
    ws    = sheet.worksheet("2026")

    # Find last row with a non-empty SO in column A
    col_a = ws.col_values(1)          # 1-based; index 0 = header "f"
    last_so_row = 1                    # at minimum, header exists
    for i, val in enumerate(col_a[1:], start=2):   # skip header (row 1)
        if val.strip():
            last_so_row = i
    anchor = last_so_row + 2           # one blank separator

    r = anchor                         # shorthand
    # FILTER condition: non-empty SO in source AND not already in existing 2026 rows
    cond = (
        f"('Copy of Data per order'!A2:A<>\"\")"
        f"*NOT(COUNTIF('2026'!$A$2:$A${last_so_row},'Copy of Data per order'!A2:A))"
    )

    formulas = {
        f"A{r}": f"=FILTER('Copy of Data per order'!A2:A,{cond})",
        f"B{r}": f"=FILTER(TEXT('Copy of Data per order'!B2:B,\"D-MMM\"),{cond})",
        f"C{r}": f"=FILTER('Copy of Data per order'!C2:C,{cond})",
        # D = Status (VLOOKUP from Order Status tab)
        f"D{r}": f"=ARRAYFORMULA(IF(A{r}:A=\"\",\"\",IFERROR(VLOOKUP(A{r}:A,'Order Status'!$A:$B,2,0),\"\")))",
        # E = Production Stage (VLOOKUP from Order Status tab)
        f"E{r}": f"=ARRAYFORMULA(IF(A{r}:A=\"\",\"\",IFERROR(VLOOKUP(A{r}:A,'Order Status'!$A:$C,3,0),\"\")))",
        f"F{r}": f"=FILTER('Copy of Data per order'!E2:E,{cond})",   # Order Status
        f"G{r}": f"=FILTER('Copy of Data per order'!F2:F,{cond})",   # Item Sku
        f"H{r}": f"=FILTER('Copy of Data per order'!G2:G,{cond})",   # Item Name → Descreption
        f"I{r}": f"=FILTER('Copy of Data per order'!H2:H,{cond})",   # QTY
        f"J{r}": f"=FILTER('Copy of Data per order'!I2:I,{cond})",   # Item Note
        f"R{r}": f"=FILTER('Copy of Data per order'!K2:K,{cond})",   # Order Class
    }

    for cell, formula in formulas.items():
        ws.update(cell, [[formula]], value_input_option="USER_ENTERED")

    return anchor


_TRACKER_STATUS_TAB   = "Order Status"
_TRACKER_STATUS_HEADS = ["SO", "Status", "Production Stage", "Updated At"]

_WRITE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _open_sheet(sheet_url_or_name: str, scopes):
    creds  = _get_creds(scopes)
    client = gspread.authorize(creds)
    return client.open_by_url(sheet_url_or_name) if sheet_url_or_name.startswith("http") \
           else client.open(sheet_url_or_name)


def load_tracker_orders(sheet_url_or_name: str) -> pd.DataFrame:
    """Read 'Copy of Data per order' and return a clean tracker DataFrame.
    Columns: SO, Order Date, Customer Name, Order Status,
             Item Sku, Item Name, Item QTY, Item Note,
             Picking Ship Date, Order Ship Date, Order Class."""
    sheet = _open_sheet(sheet_url_or_name, SCOPES)
    ws    = sheet.worksheet("Copy of Data per order")
    rows  = ws.get_all_values()
    if len(rows) < 2:
        return pd.DataFrame()

    df = pd.DataFrame(rows[1:], columns=rows[0])
    df = df.rename(columns={"Order": "SO"})

    for col in ["Order Date", "Picking Ship Date", "Order Ship Date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    if "Item QTY" in df.columns:
        df["Item QTY"] = pd.to_numeric(df["Item QTY"], errors="coerce").fillna(0)

    df = df[df["SO"].astype(str).str.strip().astype(bool)].reset_index(drop=True)
    return df


def ensure_order_status_tab(sheet_url_or_name: str) -> None:
    """Create the 'Order Status' worksheet with headers if it doesn't exist yet."""
    sheet = _open_sheet(sheet_url_or_name, _WRITE_SCOPES)
    existing = [ws.title for ws in sheet.worksheets()]
    if _TRACKER_STATUS_TAB not in existing:
        ws = sheet.add_worksheet(_TRACKER_STATUS_TAB, rows=1000, cols=len(_TRACKER_STATUS_HEADS))
        ws.append_row(_TRACKER_STATUS_HEADS)


def load_order_statuses(sheet_url_or_name: str) -> pd.DataFrame:
    """Read all rows from the 'Order Status' tab.
    Returns DataFrame with columns: SO, Status, Production Stage, Updated At."""
    sheet = _open_sheet(sheet_url_or_name, SCOPES)
    try:
        ws   = sheet.worksheet(_TRACKER_STATUS_TAB)
        rows = ws.get_all_values()
    except gspread.exceptions.WorksheetNotFound:
        return pd.DataFrame(columns=_TRACKER_STATUS_HEADS)

    if len(rows) < 2:
        return pd.DataFrame(columns=_TRACKER_STATUS_HEADS)

    return pd.DataFrame(rows[1:], columns=rows[0])


def upsert_order_status(sheet_url_or_name: str,
                        so: str,
                        status: str,
                        production_stage: str) -> None:
    """Insert or update a row in 'Order Status' keyed by SO.
    Reads the current sheet state, finds the matching row (or appends),
    and writes in a single API call to minimise race conditions."""
    import datetime
    sheet = _open_sheet(sheet_url_or_name, _WRITE_SCOPES)
    ws    = sheet.worksheet(_TRACKER_STATUS_TAB)
    rows  = ws.get_all_values()

    updated_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    new_row    = [so, status, production_stage, updated_at]

    if len(rows) <= 1:                           # header only / empty
        ws.append_row(new_row)
        return

    headers = rows[0]
    so_col  = headers.index("SO") if "SO" in headers else 0

    for i, row in enumerate(rows[1:], start=2):  # 1-based; row 1 is header
        if so_col < len(row) and row[so_col].strip() == so.strip():
            # Update in-place (all four columns)
            col_start = gspread.utils.rowcol_to_a1(i, 1)
            col_end   = gspread.utils.rowcol_to_a1(i, len(_TRACKER_STATUS_HEADS))
            ws.update(f"{col_start}:{col_end}", [new_row])
            return

    ws.append_row(new_row)   # SO not found → add new row


if __name__ == "__main__":
    SHEET = "https://docs.google.com/spreadsheets/d/1cEpLqAb_sqOoGxQ7GezAgyAlfQz4fOlpPVRuX-mimaA/edit"
    df = load_orders(SHEET)
    print(f"Shape: {df.shape}")
    print(f"Columns: {df.columns.tolist()}")
    print()
    print(df[["SO", "Customer Name", "Order Date", "Status", "Total Order Value"]].head(5).to_string())
