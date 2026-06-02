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


def load_production_plan(sheet_url_or_name: str, worksheet_name: str = "2026") -> pd.DataFrame:
    """Reads the production planning worksheet — one row per item.
    Renames raw columns to clean names; blank column 6 becomes 'Item Ref'."""
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

    df = pd.DataFrame(rows[1:], columns=headers)

    # Rename to clean names (headers have already been .strip()-ed)
    rename_map = {
        "f": "SO",
        "Date": "Order Date",
        "Statues": "Status",
        "Status Manu": "Production Stage",
        "Descreption": "Description",
        "_col6": "Item Ref",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    return df


def load_production_items(sheet_url_or_name: str) -> pd.DataFrame:
    """Reads the 'Data per order' worksheet from the production sheet for SKU-level detail."""
    creds = _get_creds(SCOPES)
    client = gspread.authorize(creds)

    if sheet_url_or_name.startswith("http"):
        sheet = client.open_by_url(sheet_url_or_name)
    else:
        sheet = client.open(sheet_url_or_name)

    ws = sheet.worksheet("Data per order")
    rows = ws.get_all_values()
    if not rows:
        return pd.DataFrame(columns=["SO", "Item Sku", "Item Name", "Item QTY"])

    df = pd.DataFrame(rows[1:], columns=rows[0])
    df["Item QTY"] = pd.to_numeric(df["Item QTY"], errors="coerce").fillna(0)
    return df.rename(columns={"Order": "SO"})


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
    """Write Status and/or Production Stage back to the '2026' worksheet.

    so_updates: {SO: {"Status": "...", "Production Stage": "..."}}
    Returns list of SOs that were found and updated.
    """
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

    ws = sheet.worksheet("2026")
    rows = ws.get_all_values()
    if not rows:
        return []

    raw_headers = [h.strip() for h in rows[0]]

    # Locate columns by their raw sheet names
    try:
        so_col = raw_headers.index("f")          # SO is stored in column "f"
    except ValueError:
        raise ValueError("SO column ('f') not found in 2026 worksheet")
    status_col = raw_headers.index("Statues")         if "Statues"     in raw_headers else None
    stage_col  = raw_headers.index("Status Manu")     if "Status Manu" in raw_headers else None

    # Build SO → list of 1-based row numbers (multiple items per SO)
    so_row_map: dict[str, list[int]] = {}
    for i, row in enumerate(rows[1:]):
        so = row[so_col].strip() if so_col < len(row) else ""
        if so:
            so_row_map.setdefault(so, []).append(i + 2)  # +1 header, +1 for 1-based

    updates = []
    updated = []
    for so, changes in so_updates.items():
        if so not in so_row_map:
            continue
        for row_num in so_row_map[so]:
            if "Status" in changes and status_col is not None:
                updates.append({
                    "range": gspread.utils.rowcol_to_a1(row_num, status_col + 1),
                    "values": [[changes["Status"]]],
                })
            if "Production Stage" in changes and stage_col is not None:
                updates.append({
                    "range": gspread.utils.rowcol_to_a1(row_num, stage_col + 1),
                    "values": [[changes["Production Stage"]]],
                })
        updated.append(so)

    if updates:
        ws.batch_update(updates)
    return updated


if __name__ == "__main__":
    SHEET = "https://docs.google.com/spreadsheets/d/1cEpLqAb_sqOoGxQ7GezAgyAlfQz4fOlpPVRuX-mimaA/edit"
    df = load_orders(SHEET)
    print(f"Shape: {df.shape}")
    print(f"Columns: {df.columns.tolist()}")
    print()
    print(df[["SO", "Customer Name", "Order Date", "Status", "Total Order Value"]].head(5).to_string())
