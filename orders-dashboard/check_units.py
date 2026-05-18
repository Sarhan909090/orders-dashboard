import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from extract import load_orders

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
SHEET_URL = "https://docs.google.com/spreadsheets/d/1cEpLqAb_sqOoGxQ7GezAgyAlfQz4fOlpPVRuX-mimaA/edit"

creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
client = gspread.authorize(creds)
sheet = client.open_by_url(SHEET_URL)
ws = sheet.worksheet("Data")
rows = ws.get_all_values()
data = pd.DataFrame(rows[1:], columns=rows[0])
data["Item QTY"] = pd.to_numeric(data["Item QTY"], errors="coerce").fillna(0)

units = data.groupby("Order").agg(
    SKUs=("Item Sku", "count"),
    Total_Units=("Item QTY", "sum"),
).reset_index()

df = load_orders(SHEET_URL)
dot = df[df["Status"].str.upper().str.startswith("DOT", na=False)][["SO", "Customer Name", "Status"]]
merged = dot.merge(units, left_on="SO", right_on="Order", how="left")

print("DOT orders with multiple SKUs:")
multi = merged[merged["SKUs"] > 1]
print(multi.to_string(index=False))
print()
print(f"Total DOT orders:              {len(dot)}")
print(f"DOT orders found in Data tab:  {merged['SKUs'].notna().sum()}")
print(f"DOT orders NOT in Data tab:    {merged['SKUs'].isna().sum()}")
print(f"Total units across DOT orders: {merged['Total_Units'].sum():.0f}")
print(f"Avg units per order:           {merged['Total_Units'].mean():.1f}")
