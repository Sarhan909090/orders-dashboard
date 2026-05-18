import pandas as pd
import numpy as np
from extract import load_orders

def week_start(date):
    """Saturday that begins the Sat-Thu week containing 'date'."""
    days_back = (date.weekday() - 5) % 7
    return date - pd.Timedelta(days=int(days_back))

def target_week(order_date, plan):
    """Expected delivery = order_date + 4 or 8 weeks; window = Sat-Thu of that date."""
    weeks = 4 if plan == "Online/Fast Track" else 8
    expected = order_date + pd.Timedelta(weeks=weeks)
    t_start = week_start(expected)
    t_end = t_start + pd.Timedelta(days=5)   # Thursday
    return t_start, t_end

def classify(row):
    status = str(row["Status"]).strip()
    if status == "Canceled":
        return "Excluded - Canceled"
    if status in ("Delayed by Customer", "Delayed"):
        return "Excluded - Delayed by Customer"
    plan = str(row["Plan"]).strip()
    if not plan or plan == "nan":
        return None
    od = row["Order Date"]
    if pd.isna(od):
        return None
    t_start, t_end = target_week(od, plan)
    actual = row["Delivery Date"]
    if pd.isna(actual):
        return "Not Delivered"
    if t_start <= actual <= t_end:
        return "On time"
    if actual < t_start:
        return "Early"
    return "Late"

SHEET = "https://docs.google.com/spreadsheets/d/1cEpLqAb_sqOoGxQ7GezAgyAlfQz4fOlpPVRuX-mimaA/edit"
df = load_orders(SHEET)
df["Order Date"] = pd.to_datetime(df["Order Date"], dayfirst=True, errors="coerce")
df["Delivery Date"] = pd.to_datetime(df["Delivery Date"], dayfirst=True, errors="coerce")
df = df[df["SO"].str.strip().astype(bool)]
df["Delivery Status"] = df.apply(classify, axis=1)

df["Target Start"] = df.apply(
    lambda r: target_week(r["Order Date"], str(r["Plan"]).strip())[0]
    if str(r["Plan"]).strip() not in ("", "nan") and pd.notna(r["Order Date"]) else pd.NaT,
    axis=1,
)
df["Target End"] = df.apply(
    lambda r: target_week(r["Order Date"], str(r["Plan"]).strip())[1]
    if str(r["Plan"]).strip() not in ("", "nan") and pd.notna(r["Order Date"]) else pd.NaT,
    axis=1,
)
df["Target Month"] = df["Target Start"].dt.to_period("M").dt.to_timestamp()

apr = df[df["Target Month"] == "2026-04-01"].copy()

print("=" * 60)
print("DASHBOARD (Google Sheet) - April 2026")
print("=" * 60)
print(f"Total in scope: {len(apr)}")
print()
print("Delivery Status counts:")
print(apr["Delivery Status"].value_counts(dropna=False).to_string())
print()

eligible = apr[
    apr["Delivery Status"].notna() &
    ~apr["Delivery Status"].str.startswith("Excluded")
]
on_time  = eligible["Delivery Status"].isin(["On time", "Early"]).sum()
late     = (eligible["Delivery Status"] == "Late").sum()
not_del  = (eligible["Delivery Status"] == "Not Delivered").sum()
excl_c   = (apr["Delivery Status"] == "Excluded - Canceled").sum()
excl_d   = (apr["Delivery Status"] == "Excluded - Delayed by Customer").sum()
elig_n   = len(eligible)
pct      = on_time / elig_n * 100 if elig_n > 0 else 0

print(f"Eligible:                      {elig_n}   (Excel: 96)")
print(f"On time (incl. Early):         {on_time}   (Excel: 71)")
print(f"Late:                          {late}   (Excel: 25)")
print(f"Not Delivered:                 {not_del}    (Excel: 2)")
print(f"Excluded - Canceled:           {excl_c}    (Excel: 6)")
print(f"Excluded - Delayed by Cust.:   {excl_d}    (Excel: 5)")
print(f"On-Time %:                     {pct:.1f}%  (Excel: 73.96%)")
print()

# --- By channel ---
pm = eligible[eligible["Plan"] != "Online/Fast Track"]
ft = eligible[eligible["Plan"] == "Online/Fast Track"]
pm_ot = pm["Delivery Status"].isin(["On time","Early"]).sum()
ft_ot = ft["Delivery Status"].isin(["On time","Early"]).sum()
print("Plan Month:        Eligible=%d  OnTime=%d  Late=%d  NotDel=%d  %%=%.1f%%  (Excel: 63/60/2/1/95.2%%)" % (
    len(pm), pm_ot, (pm["Delivery Status"]=="Late").sum(), (pm["Delivery Status"]=="Not Delivered").sum(),
    pm_ot/len(pm)*100 if len(pm) else 0))
print("Online/Fast Track: Eligible=%d  OnTime=%d  Late=%d  NotDel=%d  %%=%.1f%%  (Excel: 35/11/23/1/31.4%%)" % (
    len(ft), ft_ot, (ft["Delivery Status"]=="Late").sum(), (ft["Delivery Status"]=="Not Delivered").sum(),
    ft_ot/len(ft)*100 if len(ft) else 0))

print()
print("=" * 60)
print("ORDER-LEVEL DETAIL")
print("=" * 60)
pd.set_option("display.max_rows", None)
pd.set_option("display.max_colwidth", 25)
cols = ["SO", "Customer Name", "Plan", "Order Date", "Target Start", "Target End", "Delivery Date", "Delivery Status"]
print(apr[cols].sort_values(["Plan", "SO"]).to_string(index=False))
