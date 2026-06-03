import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np
from extract import (load_orders, load_unit_counts, load_dot_items, write_dot_tags,
                     load_tracker_orders, ensure_order_status_tab,
                     load_order_statuses, upsert_order_status,
                     write_production_status)

st.set_page_config(page_title="Orders Dashboard", layout="wide")
col_title, col_refresh = st.columns([9, 1])
col_title.title("Orders Dashboard")
if col_refresh.button("🔄 Refresh", help="Reload all data from Google Sheet"):
    st.cache_data.clear()
    st.rerun()

SHEET      = "https://docs.google.com/spreadsheets/d/1cEpLqAb_sqOoGxQ7GezAgyAlfQz4fOlpPVRuX-mimaA/edit"
PROD_SHEET = "https://docs.google.com/spreadsheets/d/1zpdNjDFPRi7RCvMImn3-soW0P3dYubS1d76FwsjfL28/edit"

# ── SLA RULES — deadline = Order Date + offset, by waterfall DOT → Plan → FT CODE
SLA_AT_RISK_DAYS = 3                  # days remaining when colour turns amber
SLA_TERMINAL     = ["Completed"]      # tracker editable Status that stops the clock
DOT_RULES  = {"DOT-GO": ("days", 7), "DOT-LITE": ("days", 7), "DOT-V2.1": ("weeks", 7)}
PLAN_WEEKS = 7                        # any month Plan (not Online/Fast Track) → 7 weeks
FT_RULES   = {"FT-34": ("weeks", 3), "FT-45": ("weeks", 4), "FT-56": ("weeks", 5),
              "FT-67": ("weeks", 6), "LIFO14": ("workdays", 14)}
# ─────────────────────────────────────────────────────────────────────────────


def week_start(date):
    """Return the Saturday that begins the Sat–Thu week containing 'date'."""
    days_back = (date.weekday() - 5) % 7
    return date - pd.Timedelta(days=int(days_back))


def add_working_days(start, n, off_weekday=4):
    """Add n working days to 'start', skipping Fridays (weekday 4). Sat–Thu = working."""
    d = start
    added = 0
    while added < n:
        d += pd.Timedelta(days=1)
        if d.weekday() != off_weekday:
            added += 1
    return d


def sla_deadline(order_date, status, plan, ft_code):
    """Compute the SLA deadline via waterfall: DOT → Plan month → FT CODE.
    Returns a Timestamp or NaT when no rule matches."""
    if pd.isna(order_date):
        return pd.NaT
    s = str(status).strip().upper()
    p = str(plan).strip()
    f = str(ft_code).strip().upper()
    # 1. DOT type
    if s in ("DOT-GO", "DOT-LITE"):
        return order_date + pd.Timedelta(days=7)
    if s == "DOT-V2.1":
        return order_date + pd.Timedelta(weeks=7)
    # 2. Plan month (any non-blank value that isn't Online/Fast Track)
    if p and p.lower() not in ("", "nan", "online/fast track"):
        return order_date + pd.Timedelta(weeks=PLAN_WEEKS)
    # 3. FT CODE
    if f in FT_RULES:
        unit, n = FT_RULES[f]
        if unit == "weeks":
            return order_date + pd.Timedelta(weeks=n)
        if unit == "workdays":
            return add_working_days(order_date, n)
    return pd.NaT


def target_week(order_date, plan):
    """Expected delivery = order_date + 4 or 8 weeks; window = Sat–Thu of that date."""
    weeks = 4 if plan == "Online/Fast Track" else 8
    expected = order_date + pd.Timedelta(weeks=weeks)
    t_start = week_start(expected)
    t_end = t_start + pd.Timedelta(days=5)   # Thursday
    return t_start, t_end


def dot_target_week(order_date, status):
    """DOT-GO / DOT-LITE → 2 weeks; DOT-V2.1 → 8 weeks."""
    weeks = 8 if status.strip().upper() == "DOT-V2.1" else 2
    expected = order_date + pd.Timedelta(weeks=weeks)
    t_start = week_start(expected)
    t_end = t_start + pd.Timedelta(days=5)
    return t_start, t_end


def classify_dot(row):
    od = row["Order Date"]
    if pd.isna(od):
        return None, pd.NaT, pd.NaT
    # Cancelled tag may appear in Status or CS Updated Date
    if _is_canceled(row["Status"], row.get("CS Updated Date", "")):
        return "Cancelled", pd.NaT, pd.NaT
    t_start, t_end = dot_target_week(od, str(row["Status"]))
    actual = row["Delivery Date"]
    if pd.isna(actual):
        result = "Not Delivered"
    elif t_start <= actual <= t_end:
        result = "On time"
    elif actual < t_start:
        result = "Early"
    else:
        result = "Late"
    return result, t_start, t_end


def _is_canceled(status_val, cs_updated_date_val):
    """True if either the Status or CS Updated Date column signals a cancellation."""
    return (
        str(status_val).strip().lower()          in ("canceled", "cancelled") or
        str(cs_updated_date_val).strip().lower() in ("canceled", "cancelled")
    )


def classify_delivery(row):
    status = str(row["Status"]).strip()
    plan   = str(row["Plan"]).strip()

    # Exclude DOT orders from the regular KPI entirely
    if status.upper().startswith("DOT"):
        return None
    # Cancelled tag may appear in Status or CS Updated Date
    if _is_canceled(status, row.get("CS Updated Date", "")):
        return "Cancelled"
    if status in ("Delayed by Customer", "Delayed"):
        return "Excluded - Delayed by Customer"

    if not plan or plan == "nan":
        return None                         # no Plan value → out of KPI scope

    order_date = row["Order Date"]
    if pd.isna(order_date):
        return None

    t_start, t_end = target_week(order_date, plan)
    actual = row["Delivery Date"]

    if pd.isna(actual):
        return "Not Delivered"
    if t_start <= actual <= t_end:
        return "On time"
    if actual < t_start:
        return "Early"                      # delivered before target week
    return "Late"


@st.cache_data(ttl=300)
def get_data() -> pd.DataFrame:
    df = load_orders(SHEET)

    for col in ["Order Date", "Delivery Date"]:
        df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce")

    df["Month"] = df["Order Date"].dt.to_period("M").dt.to_timestamp()
    df["Week"]  = df["Order Date"].apply(lambda d: week_start(d) if pd.notna(d) else pd.NaT)
    df["Year"]  = df["Order Date"].dt.year

    def _flag(row):
        s = str(row["Status"]).strip()
        c = str(row.get("CS Updated Date", "")).strip()
        if _is_canceled(s, c):
            return "Cancelled"
        if s in ("Delayed", "Delayed by Customer"):
            return "Delayed"
        if s.upper().startswith("DOT"):
            return s          # e.g. DOT-GO, DOT-LITE, DOT-V2.1
        return ""
    df["Flag"] = df.apply(_flag, axis=1)

    df["Total Order Value"] = (
        df["Total Order Value"].astype(str)
        .str.replace(",", "", regex=False).str.strip()
        .pipe(pd.to_numeric, errors="coerce")
    )

    # Channel: Plan column value directly
    df["Channel"] = np.where(
        df["Plan"].astype(str).str.strip() == "Online/Fast Track",
        "Online/Fast Track",
        "Plan Month",
    )

    # Calculate target week dates for display
    def get_target_dates(row):
        plan = str(row["Plan"]).strip()
        od = row["Order Date"]
        if not plan or plan == "nan" or pd.isna(od):
            return pd.NaT, pd.NaT
        ts, te = target_week(od, plan)
        return ts, te

    target_dates = df.apply(get_target_dates, axis=1, result_type="expand")
    df["Target Week Start"] = target_dates[0]
    df["Target Week End"] = target_dates[1]

    # Target month (Saturday of target week → determines which month this order belongs to in KPI)
    df["Target Month"] = df["Target Week Start"].dt.to_period("M").dt.to_timestamp()

    # Days late / early
    df["Delivery Status"] = df.apply(classify_delivery, axis=1)

    # Days late: calendar days after Thursday, minus any Fridays in between
    def days_late(row):
        if row["Delivery Status"] != "Late":
            return np.nan
        thu = row["Target Week End"]
        actual = row["Delivery Date"]
        total = (actual - thu).days
        # count Fridays between thu (exclusive) and actual (inclusive)
        fridays = sum(
            1 for d in pd.date_range(thu + pd.Timedelta(days=1), actual)
            if d.weekday() == 4
        )
        return total - fridays

    df["Days Late"] = df.apply(days_late, axis=1)

    # Merge unit counts from Data worksheet (DOT SKUs only)
    units = load_unit_counts(SHEET)
    df = df.merge(units, on="SO", how="left")
    df["Total_Units"] = df["Total_Units"].fillna(1)   # default 1 if not in Data sheet
    df["SKUs"] = df["SKUs"].fillna(1)

    # DOT orders classification
    dot_mask = df["Status"].str.upper().str.startswith("DOT", na=False)
    if dot_mask.any():
        dot_results = df[dot_mask].apply(classify_dot, axis=1, result_type="expand")
        dot_results.columns = ["DOT Status", "DOT Target Start", "DOT Target End"]
        df = df.join(dot_results)
    else:
        df["DOT Status"]      = pd.NA
        df["DOT Target Start"] = pd.NaT
        df["DOT Target End"]   = pd.NaT

    # DOT days late (skip Fridays)
    def dot_days_late(row):
        if row.get("DOT Status") != "Late":
            return np.nan
        thu = row["DOT Target End"]
        actual = row["Delivery Date"]
        if pd.isna(thu) or pd.isna(actual):
            return np.nan
        total = (actual - thu).days
        fridays = sum(
            1 for d in pd.date_range(thu + pd.Timedelta(days=1), actual)
            if d.weekday() == 4
        )
        return total - fridays

    df["DOT Days Late"] = df.apply(dot_days_late, axis=1)

    df = df[df["SO"].str.strip().astype(bool)]
    return df


def fmt_dates(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with all datetime columns formatted as date-only strings."""
    out = frame.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%d-%b-%Y").replace("NaT", "")
    return out


@st.cache_data(ttl=300)
def get_dot_items() -> pd.DataFrame:
    return load_dot_items(SHEET)


@st.cache_data(ttl=60)
def get_tracker_orders() -> pd.DataFrame:
    """Load 'Copy of Data per order' for the tracker — 2026 orders only, no Transportation lines."""
    df = load_tracker_orders(PROD_SHEET)
    if "Order Date" in df.columns:
        df = df[df["Order Date"].dt.year == 2026]
    if "Item Sku" in df.columns:
        df = df[~df["Item Sku"].str.strip().str.lower().eq("transportation")]
    return df.reset_index(drop=True)


@st.cache_data(ttl=30)
def get_order_statuses() -> pd.DataFrame:
    """Load the 'Order Status' write-back tab (very short TTL — status changes must be fresh)."""
    return load_order_statuses(PROD_SHEET)


df = get_data()
dot_items_df = get_dot_items()

# Ensure the Order Status tab exists in the Production Planning Sheet
try:
    ensure_order_status_tab(PROD_SHEET)
except Exception:
    pass   # non-fatal: if it already exists or there's a transient error, continue

tab_orders, tab_kpi, tab_dot, tab_tracker = st.tabs(
    ["Orders", "Delivery KPI", "DOT Orders", "Orders Tracker"]
)

# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — ORDERS
# ════════════════════════════════════════════════════════════════════════════
with tab_orders:
    with st.expander("🔽 Filters", expanded=False):
        orders_view = st.radio(
            "Period", ["Weekly", "Monthly", "Yearly", "All Time"],
            horizontal=True, key="orders_view",
        )

        # Initialise all three so they're always defined regardless of which branch runs
        sel_orders_week = sel_orders_month = sel_orders_year = []

        if orders_view == "Weekly":
            weeks_available = sorted(df["Week"].dropna().unique(), reverse=True)
            sel_orders_week = st.multiselect(
                "Select Week(s)", options=weeks_available,
                default=[weeks_available[0]] if weeks_available else [],
                format_func=lambda w: (
                    f"{pd.Timestamp(w).strftime('%d %b %Y')} – "
                    f"{(pd.Timestamp(w) + pd.Timedelta(days=5)).strftime('%d %b %Y')}"
                ),
                key="orders_week",
            )
        elif orders_view == "Monthly":
            months_available = sorted(df["Month"].dropna().unique(), reverse=True)
            sel_orders_month = st.multiselect(
                "Select Month(s)", options=months_available,
                default=[months_available[0]] if months_available else [],
                format_func=lambda m: pd.Timestamp(m).strftime("%b %Y"),
                key="orders_month",
            )
        elif orders_view == "Yearly":
            years_available = sorted(df["Year"].dropna().unique(), reverse=True)
            sel_orders_year = st.multiselect(
                "Select Year(s)", options=years_available,
                default=[years_available[0]] if years_available else [],
                key="orders_year",
            )

    filtered = df.copy()
    if orders_view == "Weekly":
        filtered = filtered[filtered["Week"].isin(sel_orders_week)] if sel_orders_week else df.iloc[:0]
    elif orders_view == "Monthly":
        filtered = filtered[filtered["Month"].isin(sel_orders_month)] if sel_orders_month else df.iloc[:0]
    elif orders_view == "Yearly":
        filtered = filtered[filtered["Year"].isin(sel_orders_year)] if sel_orders_year else df.iloc[:0]

    if orders_view != "All Time" and not any([sel_orders_week, sel_orders_month, sel_orders_year]):
        st.info("Select at least one period to see data.")

    # Delivered = orders whose Delivery Date falls in the selected period
    if orders_view == "Weekly":
        del_week = df["Delivery Date"].apply(lambda d: week_start(d) if pd.notna(d) else pd.NaT)
        delivered_count = del_week.isin(sel_orders_week).sum() if sel_orders_week else 0
    elif orders_view == "Monthly":
        delivered_count = (
            df["Delivery Date"].dt.to_period("M").dt.to_timestamp().isin(sel_orders_month).sum()
            if sel_orders_month else 0
        )
    elif orders_view == "Yearly":
        delivered_count = (
            df["Delivery Date"].dt.year.isin(sel_orders_year).sum() if sel_orders_year else 0
        )
    else:  # All Time
        delivered_count = df["Delivery Date"].notna().sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Orders", f"{len(filtered):,}")
    c2.metric("Unique Customers", f"{filtered['Customer Name'].nunique():,}")
    total_val = filtered["Total Order Value"].sum()
    c3.metric("Total Order Value", f"{total_val:,.0f}" if total_val else "N/A")
    c4.metric("Delivered", f"{delivered_count:,}")

    st.divider()
    col_left, col_right = st.columns(2)

    with col_left:
        monthly = (
            df.dropna(subset=["Month"]).groupby("Month").size().reset_index(name="Orders")
        )
        monthly["Month Label"] = monthly["Month"].dt.strftime("%b %Y")
        fig_trend = px.bar(monthly, x="Month Label", y="Orders", title="Orders per Month",
                           labels={"Month Label": "Month"})
        st.plotly_chart(fig_trend, use_container_width=True)

    with col_right:
        status_counts = filtered["Status"].replace("", "No Status").value_counts().reset_index()
        status_counts.columns = ["Status", "Count"]
        fig_status = px.pie(status_counts, names="Status", values="Count", title="Orders by Status")
        st.plotly_chart(fig_status, use_container_width=True)

    top_customers = (
        filtered.groupby("Customer Name").size()
        .reset_index(name="Orders")
        .sort_values("Orders", ascending=False).head(10)
    )
    fig_cust = px.bar(top_customers, x="Orders", y="Customer Name", orientation="h",
                      title="Top 10 Customers")
    fig_cust.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig_cust, use_container_width=True)

    st.subheader("Order Details")

    display_cols = ["SO", "Customer Name", "Order Date", "Flag", "Total Order Value",
                    "Order Overdue", "Delivery Date", "Notes"]
    orders_display = filtered[[c for c in display_cols if c in filtered.columns]].copy()

    # Format currency columns as EGP
    for col in ["Total Order Value", "Order Overdue"]:
        if col in orders_display.columns:
            orders_display[col] = (
                orders_display[col].astype(str)
                .str.replace(",", "", regex=False).str.strip()
                .pipe(pd.to_numeric, errors="coerce")
                .map(lambda x: f"EGP {x:,.0f}" if pd.notna(x) and x != 0 else "")
            )

    with st.expander("🔽 Column Filters", expanded=False):
        fc1, fc2, fc3, fc4 = st.columns(4)
        f_ord_so    = fc1.text_input("SO", key="ord_f_so").strip()
        f_ord_cust  = fc2.text_input("Customer Name", key="ord_f_cust").strip()
        f_ord_notes = fc3.text_input("Notes", key="ord_f_notes").strip()
        flag_opts   = sorted(filtered["Flag"].fillna("").unique().tolist())
        f_ord_flag  = fc4.multiselect("Flag", flag_opts, key="ord_f_flag")

        fc5, fc6, _, _ = st.columns(4)
        od_vals = filtered["Order Date"].dropna()
        if not od_vals.empty:
            f_ord_od = fc5.date_input(
                "Order Date", value=(od_vals.min().date(), od_vals.max().date()), key="ord_f_od"
            )
        else:
            f_ord_od = ()
        dd_vals = filtered["Delivery Date"].dropna()
        if not dd_vals.empty:
            f_ord_dd = fc6.date_input(
                "Delivery Date", value=(dd_vals.min().date(), dd_vals.max().date()), key="ord_f_dd"
            )
        else:
            f_ord_dd = ()

    if f_ord_so:
        orders_display = orders_display[orders_display["SO"].str.contains(f_ord_so, case=False, na=False)]
    if f_ord_cust:
        orders_display = orders_display[orders_display["Customer Name"].str.contains(f_ord_cust, case=False, na=False)]
    if f_ord_notes and "Notes" in orders_display.columns:
        orders_display = orders_display[orders_display["Notes"].str.contains(f_ord_notes, case=False, na=False)]
    if f_ord_flag:
        orders_display = orders_display[orders_display["Flag"].fillna("").isin(f_ord_flag)]
    if len(f_ord_od) == 2:
        mask = orders_display["Order Date"].isna() | (
            (orders_display["Order Date"] >= pd.Timestamp(f_ord_od[0])) &
            (orders_display["Order Date"] <= pd.Timestamp(f_ord_od[1]))
        )
        orders_display = orders_display[mask]
    if len(f_ord_dd) == 2:
        mask = orders_display["Delivery Date"].isna() | (
            (orders_display["Delivery Date"] >= pd.Timestamp(f_ord_dd[0])) &
            (orders_display["Delivery Date"] <= pd.Timestamp(f_ord_dd[1]))
        )
        orders_display = orders_display[mask]

    st.caption(f"{len(orders_display):,} order(s)")
    st.dataframe(fmt_dates(orders_display), use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — DELIVERY KPI
# ════════════════════════════════════════════════════════════════════════════
with tab_kpi:
    st.subheader("Delivery KPI")

    # Month selector based on target delivery month
    kpi_months = sorted(df.dropna(subset=["Target Month"])["Target Month"].dropna().unique())
    sel_kpi_month = st.multiselect(
        "Delivery Month(s)",
        options=kpi_months,
        default=[],
        format_func=lambda m: pd.Timestamp(m).strftime("%b %Y"),
        key="kpi_month",
    )
    if not sel_kpi_month:
        st.caption("Showing all months")

    # In-scope = has a Delivery Status (Plan column filled + Order Date present)
    kpi_df = df[df["Delivery Status"].notna()].copy()
    if sel_kpi_month:
        kpi_df = kpi_df[kpi_df["Target Month"].isin(sel_kpi_month)]

    cancelled_count  = (kpi_df["Delivery Status"] == "Cancelled").sum()
    excluded_delayed = (kpi_df["Delivery Status"] == "Excluded - Delayed by Customer").sum()
    # Eligible = has a plan + order date, not cancelled and not delayed
    eligible = kpi_df[~kpi_df["Delivery Status"].isin(["Cancelled", "Excluded - Delayed by Customer"])]
    on_time = (eligible["Delivery Status"].isin(["On time", "Early"])).sum()
    late = (eligible["Delivery Status"] == "Late").sum()
    not_delivered = (eligible["Delivery Status"] == "Not Delivered").sum()
    total_eligible = len(eligible)
    on_time_pct = on_time / total_eligible if total_eligible > 0 else 0
    avg_days_late = kpi_df.loc[kpi_df["Delivery Status"] == "Late", "Days Late"].mean()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Eligible Orders", f"{total_eligible:,}")
    k2.metric("On Time", f"{on_time:,}")
    k3.metric("Late", f"{late:,}")
    k4.metric("Not Delivered", f"{not_delivered:,}")

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("On-Time %", f"{on_time_pct:.1%}")
    k6.metric("Avg Days Late", f"{avg_days_late:.1f}" if not pd.isna(avg_days_late) else "—")
    k7.metric("Cancelled", f"{cancelled_count:,}")
    k8.metric("Excluded - Delayed by Customer", f"{excluded_delayed:,}")

    st.divider()
    col_a, col_b = st.columns(2)

    with col_a:
        # Include Cancelled as its own visible slice; exclude Delayed (user-caused, not ops KPI)
        pie_df = kpi_df[kpi_df["Delivery Status"] != "Excluded - Delayed by Customer"]
        status_dist = pie_df["Delivery Status"].value_counts().reset_index()
        status_dist.columns = ["Status", "Count"]
        color_map = {
            "On time": "#2ecc71", "Early": "#27ae60",
            "Late": "#e74c3c", "Not Delivered": "#95a5a6",
            "Cancelled": "#e67e22",
        }
        fig_pie = px.pie(status_dist, names="Status", values="Count",
                         color="Status", color_discrete_map=color_map,
                         title="Delivery Status Breakdown")
        st.plotly_chart(fig_pie, use_container_width=True)

    with col_b:
        channel_kpi = (
            eligible.groupby("Channel")["Delivery Status"]
            .apply(lambda s: s.isin(["On time", "Early"]).sum() / len(s) if len(s) > 0 else 0)
            .reset_index()
        )
        channel_kpi.columns = ["Channel", "On-Time %"]
        channel_counts = eligible.groupby("Channel").size().reset_index(name="Orders")
        channel_kpi = channel_kpi.merge(channel_counts, on="Channel")
        fig_chan = px.bar(channel_kpi, x="Channel", y="On-Time %",
                          text=channel_kpi["On-Time %"].map("{:.0%}".format),
                          title="On-Time % by Channel", color="Channel")
        fig_chan.update_traces(textposition="outside")
        fig_chan.update_yaxes(tickformat=".0%", range=[0, 1.1])
        st.plotly_chart(fig_chan, use_container_width=True)

    st.subheader("Channel KPI Breakdown")
    channel_detail = (
        eligible.groupby("Channel")["Delivery Status"]
        .value_counts().unstack(fill_value=0).reset_index()
    )
    for col in ["On time", "Early", "Late", "Not Delivered"]:
        if col not in channel_detail.columns:
            channel_detail[col] = 0
    channel_detail["Total"] = channel_detail[["On time", "Early", "Late", "Not Delivered"]].sum(axis=1)
    channel_detail["On-Time %"] = (
        (channel_detail["On time"] + channel_detail["Early"]) / channel_detail["Total"]
    ).map("{:.1%}".format)
    st.dataframe(fmt_dates(channel_detail), use_container_width=True)

    st.subheader("Order-Level Detail")
    detail_cols = ["SO", "Customer Name", "Plan", "Channel", "Order Date",
                   "Target Week Start", "Target Week End", "Delivery Date",
                   "Delivery Status", "Days Late"]
    kpi_detail = kpi_df[[c for c in detail_cols if c in kpi_df.columns]].sort_values("Delivery Status").copy()

    with st.expander("🔽 Column Filters", expanded=False):
        fc1, fc2, fc3, fc4 = st.columns(4)
        f_kpi_so     = fc1.text_input("SO", key="kpi_f_so").strip()
        f_kpi_cust   = fc2.text_input("Customer Name", key="kpi_f_cust").strip()
        plan_opts    = sorted(kpi_detail["Plan"].dropna().unique().tolist())
        f_kpi_plan   = fc3.multiselect("Plan", plan_opts, key="kpi_f_plan")
        chan_opts     = sorted(kpi_detail["Channel"].dropna().unique().tolist())
        f_kpi_chan   = fc4.multiselect("Channel", chan_opts, key="kpi_f_chan")

        fc5, fc6, fc7, _ = st.columns(4)
        status_opts  = sorted(kpi_detail["Delivery Status"].dropna().unique().tolist())
        f_kpi_status = fc5.multiselect("Delivery Status", status_opts, key="kpi_f_status")
        od_vals      = kpi_detail["Order Date"].dropna()
        if not od_vals.empty:
            f_kpi_od = fc6.date_input(
                "Order Date", value=(od_vals.min().date(), od_vals.max().date()), key="kpi_f_od"
            )
        else:
            f_kpi_od = ()
        dd_vals = kpi_detail["Delivery Date"].dropna()
        if not dd_vals.empty:
            f_kpi_dd = fc7.date_input(
                "Delivery Date", value=(dd_vals.min().date(), dd_vals.max().date()), key="kpi_f_dd"
            )
        else:
            f_kpi_dd = ()

    if f_kpi_so:
        kpi_detail = kpi_detail[kpi_detail["SO"].str.contains(f_kpi_so, case=False, na=False)]
    if f_kpi_cust:
        kpi_detail = kpi_detail[kpi_detail["Customer Name"].str.contains(f_kpi_cust, case=False, na=False)]
    if f_kpi_plan:
        kpi_detail = kpi_detail[kpi_detail["Plan"].isin(f_kpi_plan)]
    if f_kpi_chan:
        kpi_detail = kpi_detail[kpi_detail["Channel"].isin(f_kpi_chan)]
    if f_kpi_status:
        kpi_detail = kpi_detail[kpi_detail["Delivery Status"].isin(f_kpi_status)]
    if len(f_kpi_od) == 2:
        mask = kpi_detail["Order Date"].isna() | (
            (kpi_detail["Order Date"] >= pd.Timestamp(f_kpi_od[0])) &
            (kpi_detail["Order Date"] <= pd.Timestamp(f_kpi_od[1]))
        )
        kpi_detail = kpi_detail[mask]
    if len(f_kpi_dd) == 2:
        mask = kpi_detail["Delivery Date"].isna() | (
            (kpi_detail["Delivery Date"] >= pd.Timestamp(f_kpi_dd[0])) &
            (kpi_detail["Delivery Date"] <= pd.Timestamp(f_kpi_dd[1]))
        )
        kpi_detail = kpi_detail[mask]

    st.caption(f"{len(kpi_detail):,} order(s)")
    st.dataframe(fmt_dates(kpi_detail), use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — DOT ORDERS
# ════════════════════════════════════════════════════════════════════════════
with tab_dot:
    st.subheader("DOT Orders")

    # Base: all DOT orders (Cancelled shown with their own status label)
    dot_all = df[df["DOT Status"].notna()].copy()
    dot_all["Order Week"] = dot_all["Order Date"].apply(
        lambda d: week_start(d) if pd.notna(d) else pd.NaT
    )
    dot_all["Order Month"] = dot_all["Order Date"].dt.to_period("M").dt.to_timestamp()
    dot_all["Order Year"]  = dot_all["Order Date"].dt.year

    # ── View selector ──────────────────────────────────────────────────────
    view = st.radio(
        "View", ["Weekly", "Monthly", "Yearly", "All Time"],
        horizontal=True, key="dot_view",
    )

    if view == "Weekly":
        weeks = sorted(dot_all["Order Week"].dropna().unique(), reverse=True)
        sel_week = st.multiselect(
            "Select Week(s) (Order Date)", options=weeks,
            default=[weeks[0]] if weeks else [],
            format_func=lambda w: f"{pd.Timestamp(w).strftime('%d %b %Y')} – "
                                  f"{(pd.Timestamp(w) + pd.Timedelta(days=5)).strftime('%d %b %Y')}",
            key="dot_week",
        )
        dot_df = dot_all[dot_all["Order Week"].isin(sel_week)] if sel_week else dot_all.iloc[:0]

    elif view == "Monthly":
        months = sorted(dot_all["Order Month"].dropna().unique(), reverse=True)
        sel_month = st.multiselect(
            "Select Month(s) (Order Date)", options=months,
            default=[months[0]] if months else [],
            format_func=lambda m: pd.Timestamp(m).strftime("%b %Y"),
            key="dot_month",
        )
        dot_df = dot_all[dot_all["Order Month"].isin(sel_month)] if sel_month else dot_all.iloc[:0]

    elif view == "Yearly":
        years = sorted(dot_all["Order Year"].dropna().unique(), reverse=True)
        sel_year = st.multiselect(
            "Select Year(s)", options=years,
            default=[years[0]] if years else [],
            key="dot_year",
        )
        dot_df = dot_all[dot_all["Order Year"].isin(sel_year)] if sel_year else dot_all.iloc[:0]

    else:  # All Time
        dot_df = dot_all

    # ── KPI cards ──────────────────────────────────────────────────────────
    total_orders  = len(dot_df)
    total_units   = int(dot_df["Total_Units"].sum())
    on_time_n     = (dot_df["DOT Status"] == "On time").sum()
    early_n       = (dot_df["DOT Status"] == "Early").sum()
    late_n        = (dot_df["DOT Status"] == "Late").sum()
    not_del_n     = (dot_df["DOT Status"] == "Not Delivered").sum()
    cancelled_n   = (dot_df["DOT Status"] == "Cancelled").sum()
    delivered_n   = on_time_n + early_n + late_n
    on_time_pct   = on_time_n / delivered_n if delivered_n > 0 else 0
    avg_days_late = dot_df.loc[dot_df["DOT Status"] == "Late", "DOT Days Late"].mean()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Orders", f"{total_orders:,}")
    k2.metric("Total Units", f"{total_units:,}")
    k3.metric("On-Time %", f"{on_time_pct:.1%}")
    k4.metric("Avg Days Late", f"{avg_days_late:.1f}" if not pd.isna(avg_days_late) else "—")

    k5, k6, k7, k8, k9 = st.columns(5)
    k5.metric("On Time", f"{on_time_n:,}")
    k6.metric("Early", f"{early_n:,}")
    k7.metric("Late", f"{late_n:,}")
    k8.metric("Not Delivered", f"{not_del_n:,}")
    k9.metric("Cancelled", f"{cancelled_n:,}")

    st.divider()
    col_a, col_b = st.columns(2)

    color_map = {
        "On time": "#2ecc71", "Early": "#27ae60",
        "Late": "#e74c3c", "Not Delivered": "#95a5a6",
        "Cancelled": "#e67e22",
    }

    # ── Status breakdown by DOT type ───────────────────────────────────────
    with col_a:
        type_status = (
            dot_df.groupby(["Status", "DOT Status"]).size()
            .reset_index(name="Orders")
        )
        fig_type = px.bar(
            type_status, x="Status", y="Orders",
            color="DOT Status", color_discrete_map=color_map,
            barmode="stack", title="Orders by DOT Type & Status",
        )
        st.plotly_chart(fig_type, use_container_width=True)

    # ── On-time % by DOT type ──────────────────────────────────────────────
    with col_b:
        delivered_only = dot_df[dot_df["DOT Status"].isin(["On time", "Early", "Late"])]
        if len(delivered_only) > 0:
            type_kpi = delivered_only.groupby("Status").agg(
                Orders=("SO", "count"),
                Units=("Total_Units", "sum"),
            ).reset_index()
            on_time_by_type = (
                delivered_only[delivered_only["DOT Status"] == "On time"]
                .groupby("Status").size()
                .reindex(type_kpi["Status"].tolist(), fill_value=0).values
            )
            type_kpi["On-Time %"] = on_time_by_type / type_kpi["Orders"]
            fig_pct = px.bar(
                type_kpi, x="Status", y="On-Time %",
                text=type_kpi["On-Time %"].map("{:.0%}".format),
                title="On-Time % by DOT Type", color="Status",
            )
            fig_pct.update_traces(textposition="outside")
            fig_pct.update_yaxes(tickformat=".0%", range=[0, 1.15])
            st.plotly_chart(fig_pct, use_container_width=True)
        else:
            st.info("No delivered orders in this period.")

    # ── Trend chart (only for Monthly / Yearly / All Time) ─────────────────
    if view != "Weekly":
        if view == "Yearly":
            trend_grp = dot_all.groupby(["Order Year", "DOT Status"]).size().reset_index(name="Orders")
            trend_grp["Period"] = trend_grp["Order Year"].astype(str)
        elif view == "Monthly":
            trend_grp = dot_all.groupby(["Order Month", "DOT Status"]).size().reset_index(name="Orders")
            trend_grp["Period"] = trend_grp["Order Month"].dt.strftime("%b %Y")
        else:  # All Time → monthly trend
            trend_grp = dot_all.groupby(["Order Month", "DOT Status"]).size().reset_index(name="Orders")
            trend_grp["Period"] = trend_grp["Order Month"].dt.strftime("%b %Y")

        fig_trend = px.bar(
            trend_grp, x="Period", y="Orders",
            color="DOT Status", color_discrete_map=color_map,
            barmode="stack", title="DOT Orders Over Time",
        )
        st.plotly_chart(fig_trend, use_container_width=True)

    # ── DOT type summary table ──────────────────────────────────────────────
    st.subheader("DOT Type Summary")
    summary = dot_df.groupby("Status").agg(
        Orders=("SO", "count"),
        Units=("Total_Units", "sum"),
    ).reset_index()
    for col, val in [("On Time", "On time"), ("Early", "Early"),
                     ("Late", "Late"), ("Not Delivered", "Not Delivered"),
                     ("Cancelled", "Cancelled")]:
        summary[col] = (
            dot_df[dot_df["DOT Status"] == val]
            .groupby("Status").size()
            .reindex(summary["Status"].tolist(), fill_value=0).values
        )
    # On-time % denominator = delivered orders only (excludes Not Delivered and Cancelled)
    deliverable = (summary["On Time"] + summary["Early"] + summary["Late"]).astype(float).replace(0.0, np.nan)
    summary["On-Time %"] = (summary["On Time"] / deliverable).map(
        lambda x: f"{x:.1%}" if pd.notna(x) else "—"
    )
    st.dataframe(fmt_dates(summary), use_container_width=True)

    # ── Order Detail ───────────────────────────────────────────────────────
    st.subheader("Order Detail")

    detail_df = dot_df.copy().sort_values(["Status", "DOT Status"])

    with st.expander("🔽 Column Filters", expanded=False):
        fc1, fc2, fc3, fc4 = st.columns(4)
        f_dot_so     = fc1.text_input("SO", key="dot_f_so").strip()
        f_dot_cust   = fc2.text_input("Customer Name", key="dot_f_cust").strip()
        type_opts    = sorted(detail_df["Status"].dropna().unique().tolist())
        f_dot_type   = fc3.multiselect("DOT Type", type_opts, key="dot_f_type")
        status_opts  = sorted(detail_df["DOT Status"].dropna().unique().tolist())
        f_dot_status = fc4.multiselect("Delivery Status", status_opts, key="dot_f_status")

        fc5, fc6, _, _ = st.columns(4)
        od_vals = detail_df["Order Date"].dropna()
        if not od_vals.empty:
            f_dot_od = fc5.date_input(
                "Order Date", value=(od_vals.min().date(), od_vals.max().date()), key="dot_f_od"
            )
        else:
            f_dot_od = ()
        dd_vals = detail_df["Delivery Date"].dropna()
        if not dd_vals.empty:
            f_dot_dd = fc6.date_input(
                "Delivery Date", value=(dd_vals.min().date(), dd_vals.max().date()), key="dot_f_dd"
            )
        else:
            f_dot_dd = ()

    if f_dot_so:
        detail_df = detail_df[detail_df["SO"].str.contains(f_dot_so, case=False, na=False)]
    if f_dot_cust:
        detail_df = detail_df[detail_df["Customer Name"].str.contains(f_dot_cust, case=False, na=False)]
    if f_dot_type:
        detail_df = detail_df[detail_df["Status"].isin(f_dot_type)]
    if f_dot_status:
        detail_df = detail_df[detail_df["DOT Status"].isin(f_dot_status)]
    if len(f_dot_od) == 2:
        mask = detail_df["Order Date"].isna() | (
            (detail_df["Order Date"] >= pd.Timestamp(f_dot_od[0])) &
            (detail_df["Order Date"] <= pd.Timestamp(f_dot_od[1]))
        )
        detail_df = detail_df[mask]
    if len(f_dot_dd) == 2:
        mask = detail_df["Delivery Date"].isna() | (
            (detail_df["Delivery Date"] >= pd.Timestamp(f_dot_dd[0])) &
            (detail_df["Delivery Date"] <= pd.Timestamp(f_dot_dd[1]))
        )
        detail_df = detail_df[mask]

    # ── Table view ─────────────────────────────────────────────────────────
    table_cols = ["SO", "Customer Name", "Status", "Order Date",
                  "DOT Target Start", "DOT Target End", "Delivery Date",
                  "DOT Status", "DOT Days Late", "Total_Units"]
    table_display = detail_df[[c for c in table_cols if c in detail_df.columns]].rename(columns={
        "Status": "DOT Type",
        "DOT Target Start": "Target Week Start",
        "DOT Target End": "Target Week End",
        "DOT Status": "Delivery Status",
        "DOT Days Late": "Days Late",
        "Total_Units": "Chairs",
    })
    st.caption(f"{len(table_display):,} order(s)")
    st.dataframe(fmt_dates(table_display), use_container_width=True)

    # ── Expandable rows ─────────────────────────────────────────────────────
    st.markdown("**Expand order to view items:**")
    status_icon = {
        "On time": "✅", "Early": "🟢", "Late": "🔴",
        "Not Delivered": "⚪", "Cancelled": "🚫",
    }

    for row in detail_df.to_dict("records"):
        s         = row.get("DOT Status", "")
        icon      = status_icon.get(s, "")
        t_start   = row.get("DOT Target Start", pd.NaT)
        t_end     = row.get("DOT Target End", pd.NaT)
        week_str  = (
            f"{pd.Timestamp(t_start).strftime('%d %b')} – {pd.Timestamp(t_end).strftime('%d %b %Y')}"
            if pd.notna(t_start) and pd.notna(t_end) else "—"
        )
        del_date  = row.get("Delivery Date", pd.NaT)
        del_str   = pd.Timestamp(del_date).strftime("%d %b %Y") if pd.notna(del_date) else "—"
        units     = int(row["Total_Units"]) if pd.notna(row.get("Total_Units")) else 1
        days_late = row.get("DOT Days Late", np.nan)
        late_str  = f"  ·  {int(days_late)} day{'s' if days_late != 1 else ''} late" if pd.notna(days_late) else ""

        label = (
            f"{icon}  {row['SO']}  |  {row['Customer Name']}  |  {row['Status']}  |  "
            f"{units} chair{'s' if units != 1 else ''}  |  "
            f"Target: {week_str}  |  Delivered: {del_str}  |  {s}{late_str}"
        )

        with st.expander(label):
            items = dot_items_df[dot_items_df["SO"] == row["SO"]][
                ["Item Sku", "Item Name", "Item QTY"]
            ].rename(columns={"Item QTY": "QTY"}).reset_index(drop=True)
            if items.empty:
                st.write("No DOT items found in Data sheet.")
            else:
                st.dataframe(items, use_container_width=True)

    # ════════════════════════════════════════════════════════════════════════
    # Untagged DOT orders
    # ════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("Untagged DOT Orders")

    dot_options = ["DOT-GO", "DOT-LITE", "DOT-V2.1"]

    if "discarded_dot_sos" not in st.session_state:
        st.session_state["discarded_dot_sos"] = set()

    if st.button("🔄 Refresh untagged check", key="refresh_untagged"):
        st.cache_data.clear()
        st.rerun()

    dot_so_in_data   = set(dot_items_df["SO"].unique())
    dot_so_in_orders = set(df[df["Status"].str.upper().str.startswith("DOT", na=False)]["SO"].unique())
    untagged_sos     = dot_so_in_data - dot_so_in_orders

    def _suggest_dot_type(so):
        skus = dot_items_df[dot_items_df["SO"] == so]["Item Sku"].str.upper().tolist()
        types = set()
        for sku in skus:
            if "V2.1" in sku:
                types.add("DOT-V2.1")
            elif "LITE" in sku:
                types.add("DOT-LITE")
            elif "GO" in sku:
                types.add("DOT-GO")
        return types.pop() if len(types) == 1 else None

    if not untagged_sos:
        st.success("No untagged DOT orders found.")
    else:
        untagged_df = df[df["SO"].isin(untagged_sos)][
            ["SO", "Customer Name", "Order Date", "Plan", "Status"]
        ].copy().reset_index(drop=True)
        untagged_df["Suggested Tag"] = untagged_df["SO"].apply(_suggest_dot_type)

        show_df = untagged_df[
            ~untagged_df["SO"].isin(st.session_state["discarded_dot_sos"])
        ].copy()

        if show_df.empty:
            st.success("All untagged DOT orders have been handled.")
        else:
            st.warning(
                f"⚠️ {len(show_df)} order(s) have DOT items in the Data sheet "
                f"but are not tagged as DOT in the Status column."
            )
            with st.expander("Review & tag untagged DOT orders"):
                ambiguous = show_df["Suggested Tag"].isna().sum()
                if ambiguous:
                    st.info(
                        f"{ambiguous} order(s) have mixed or unrecognised DOT SKUs — "
                        f"select their tag manually."
                    )

                hdr_c = st.columns([0.5, 3, 2, 1])
                hdr_c[0].caption("Select")
                hdr_c[1].caption("Order")
                hdr_c[2].caption("Tag")
                hdr_c[3].caption("")
                st.divider()

                for _, row in show_df.iterrows():
                    so = row["SO"]
                    default = row["Suggested Tag"] if row["Suggested Tag"] in dot_options else "DOT-GO"
                    c0, c1, c2, c3 = st.columns([0.5, 3, 2, 1])
                    c0.checkbox("", key=f"dot_chk_{so}", label_visibility="collapsed")
                    skus = dot_items_df[dot_items_df["SO"] == so]["Item Sku"].tolist()
                    c1.markdown(f"**{so}**  {row['Customer Name']}")
                    c1.caption(", ".join(skus) if skus else "—")
                    c2.selectbox(
                        "tag", dot_options,
                        index=dot_options.index(default),
                        key=f"dot_tag_{so}",
                        label_visibility="collapsed",
                    )
                    if c3.button("Discard", key=f"dot_discard_{so}"):
                        st.session_state["discarded_dot_sos"].add(so)
                        st.rerun()

                st.divider()
                selected_sos = [
                    row["SO"] for _, row in show_df.iterrows()
                    if st.session_state.get(f"dot_chk_{row['SO']}", False)
                ]
                n_selected = len(selected_sos)

                ba1, ba2, ba3, ba4 = st.columns([2, 1, 1, 1])
                bulk_tag = ba1.selectbox(
                    "Bulk tag", dot_options, key="dot_bulk_tag",
                    label_visibility="collapsed",
                )
                apply_sel = ba2.button(
                    f"Apply Selected ({n_selected})",
                    key="dot_apply_selected",
                    disabled=n_selected == 0,
                )
                apply_all = ba3.button("Apply All", type="primary", key="dot_apply_all")
                discard_sel = ba4.button(
                    f"Discard Selected ({n_selected})",
                    key="dot_discard_selected",
                    disabled=n_selected == 0,
                )

                if apply_sel:
                    with st.spinner(f"Tagging {n_selected} order(s)…"):
                        try:
                            updated = write_dot_tags(SHEET, {so: bulk_tag for so in selected_sos})
                            st.success(f"Tagged {len(updated)} order(s). Refreshing…")
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Write failed: {e}")

                if apply_all:
                    so_tag_map = {
                        row["SO"]: st.session_state.get(
                            f"dot_tag_{row['SO']}",
                            row["Suggested Tag"] if row["Suggested Tag"] in dot_options else "DOT-GO",
                        )
                        for _, row in show_df.iterrows()
                    }
                    with st.spinner("Writing to Google Sheet…"):
                        try:
                            updated = write_dot_tags(SHEET, so_tag_map)
                            st.success(f"Tagged {len(updated)} order(s). Refreshing…")
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Write failed: {e}")

                if discard_sel:
                    for so in selected_sos:
                        st.session_state["discarded_dot_sos"].add(so)
                    st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 — ORDERS TRACKER
# ════════════════════════════════════════════════════════════════════════════
with tab_tracker:
    st.subheader("Orders Tracker")

    # ── Load data ──────────────────────────────────────────────────────────
    tracker_orders  = get_tracker_orders()
    tracker_statuses = get_order_statuses()

    if tracker_orders.empty:
        st.warning("No orders found in 'Copy of Data per order'.")
        st.stop()

    # ── Merge status into orders ───────────────────────────────────────────
    # status store is keyed by SO; merge on the first occurrence per SO
    if not tracker_statuses.empty:
        status_cols = [c for c in ["SO", "Status", "Production Stage", "Updated At"]
                       if c in tracker_statuses.columns]
        status_lookup = tracker_statuses[status_cols].drop_duplicates(subset="SO")
        tdf = tracker_orders.merge(status_lookup, on="SO", how="left")
    else:
        tdf = tracker_orders.copy()
        tdf["Status"] = ""
        tdf["Production Stage"] = ""
        tdf["Updated At"] = ""

    tdf["Status"]           = tdf["Status"].fillna("")
    tdf["Production Stage"] = tdf["Production Stage"].fillna("")
    if "Updated At" not in tdf.columns:
        tdf["Updated At"] = ""
    tdf["Updated At"] = tdf["Updated At"].fillna("")

    # ── Join SLA driver columns from "Orders Plan " (df = get_data(), preloaded) ─
    def _first_nonblank(s):
        for x in s:
            if str(x).strip() and str(x).strip().lower() != "nan":
                return x
        return ""

    op_cols = ["SO", "Order Date", "Status", "Plan", "CS Updated Date", "Delivery Date"]
    has_ft  = "FT CODE" in df.columns
    if has_ft:
        op_cols.append("FT CODE")
    op = df[[c for c in op_cols if c in df.columns]].copy()

    agg = {
        "op_order_date": ("Order Date", "min"),
        "op_status":     ("Status", _first_nonblank),
        "op_plan":       ("Plan", _first_nonblank),
        "op_cs":         ("CS Updated Date", _first_nonblank),
    }
    if "Delivery Date" in op.columns:
        agg["op_delivery"] = ("Delivery Date", "min")   # earliest non-null delivery
    if has_ft:
        agg["op_ft"] = ("FT CODE", _first_nonblank)
    op_lookup = op.groupby("SO").agg(**agg).reset_index()
    if not has_ft:
        op_lookup["op_ft"] = ""
    if "op_delivery" not in op_lookup.columns:
        op_lookup["op_delivery"] = pd.NaT

    tdf = tdf.merge(op_lookup, on="SO", how="left")
    for c in ["op_status", "op_plan", "op_cs", "op_ft"]:
        tdf[c] = tdf[c].fillna("")

    # Exclude cancelled orders (per Orders Plan Status / CS Updated Date)
    cancel_mask = tdf.apply(lambda r: _is_canceled(r["op_status"], r["op_cs"]), axis=1)
    tdf = tdf[~cancel_mask].reset_index(drop=True)

    # ── SLA computation (rule-based) ─────────────────────────────────────────
    today_ts = pd.Timestamp.today().normalize()
    order_dt = (tdf["op_order_date"].fillna(tdf["Order Date"])
                if "Order Date" in tdf.columns else tdf["op_order_date"])
    tdf["_deadline"] = [
        sla_deadline(od, s, p, f)
        for od, s, p, f in zip(order_dt, tdf["op_status"], tdf["op_plan"], tdf["op_ft"])
    ]

    # Completion signals:
    #   (a) manually marked — tracker Status OR Production Stage = "Completed"
    #   (b) delivered       — a Delivery Date exists in "Orders Plan "
    tdf["_marked_completed"] = (
        tdf["Status"].isin(SLA_TERMINAL) | tdf["Production Stage"].isin(SLA_TERMINAL)
    )
    tdf["op_delivery"] = pd.to_datetime(tdf["op_delivery"], errors="coerce").dt.normalize()
    tdf["_has_delivery"] = tdf["op_delivery"].notna()
    tdf["_completed"] = tdf["_marked_completed"] | tdf["_has_delivery"]

    # Day it was manually marked (Order Status tab "Updated At"); fallback today.
    completed_on = pd.to_datetime(
        tdf["Updated At"].astype(str).str.replace(" UTC", "", regex=False),
        errors="coerce",
    ).dt.normalize()

    # Freeze date precedence: Delivery Date → manual-completion day → today (live).
    ref_date = pd.Series(today_ts, index=tdf.index)
    ref_date = ref_date.where(~tdf["_marked_completed"], completed_on.fillna(today_ts))
    ref_date = ref_date.where(~tdf["_has_delivery"], tdf["op_delivery"])
    tdf["_days_left"] = (tdf["_deadline"] - ref_date).dt.days

    def _sla_basis(row):
        s = str(row["op_status"]).strip().upper()
        p = str(row["op_plan"]).strip()
        f = str(row["op_ft"]).strip().upper()
        if s in DOT_RULES:
            return s
        if p and p.lower() not in ("", "nan", "online/fast track"):
            return "Plan · 7w"
        if f in FT_RULES:
            return f
        return "—"
    tdf["SLA Basis"] = tdf.apply(_sla_basis, axis=1)

    def _sla_status(row):
        if row["_completed"]:                    # Status or Production Stage = Completed
            return "Done"
        if pd.isna(row["_days_left"]):
            return "No SLA"
        if row["_days_left"] < 0:
            return "Breached"
        if row["_days_left"] <= SLA_AT_RISK_DAYS:
            return "At Risk"
        return "On Track"
    tdf["SLA Status"] = tdf.apply(_sla_status, axis=1)

    def _sla_countdown(r):
        d = r["_days_left"]
        if r["SLA Status"] == "No SLA" or pd.isna(d):
            return "—"
        if r["_completed"]:
            # Frozen at completion day: how it finished vs the deadline
            if d > 0:   return f"done · {int(d)}d early"
            if d == 0:  return "done · on time"
            return f"done · {int(-d)}d late"
        # Live countdown
        if d < 0:   return f"{int(-d)}d overdue"
        if d == 0:  return "TODAY"
        return f"{int(d)}d left"
    tdf["SLA Countdown"] = tdf.apply(_sla_countdown, axis=1)

    tdf["_deadline_str"] = tdf["_deadline"].dt.strftime("%d-%b-%Y").where(tdf["_deadline"].notna(), "—")

    SLA_ORDER = ["Breached", "At Risk", "On Track", "No SLA", "Done"]
    SLA_COLOUR = {
        "Breached": "🔴", "At Risk": "🟡", "On Track": "🟢",
        "No SLA": "⚪", "Done": "✅",
    }

    # ── Filters ────────────────────────────────────────────────────────────
    with st.expander("🔽 Filters", expanded=True):
        fc1, fc2, fc3, fc4 = st.columns(4)

        sla_filter = fc1.multiselect(
            "SLA Status",
            SLA_ORDER,
            default=["Breached", "At Risk"],
            key="tr_sla",
        )
        status_opts = sorted(tdf["Status"].replace("", "— no status —").unique().tolist())
        status_filter = fc2.multiselect("Order Status", status_opts, key="tr_status")
        cust_filter   = fc3.text_input("Customer Name", key="tr_cust").strip()

        od_vals = tdf["Order Date"].dropna() if "Order Date" in tdf.columns else pd.Series([], dtype="datetime64[ns]")
        if not od_vals.empty:
            date_filter = fc4.date_input(
                "Order Date range",
                value=(od_vals.max().date() - pd.Timedelta(days=90), od_vals.max().date()),
                key="tr_date",
            )
        else:
            date_filter = ()

    # Apply filters
    view = tdf.copy()
    if sla_filter:
        view = view[view["SLA Status"].isin(sla_filter)]
    if status_filter:
        view = view[view["Status"].replace("", "— no status —").isin(status_filter)]
    if cust_filter:
        view = view[view["Customer Name"].str.contains(cust_filter, case=False, na=False)]
    if len(date_filter) == 2 and "Order Date" in view.columns:
        mask = view["Order Date"].isna() | (
            (view["Order Date"] >= pd.Timestamp(date_filter[0])) &
            (view["Order Date"] <= pd.Timestamp(date_filter[1]))
        )
        view = view[mask]

    # ── Summary KPI strip ──────────────────────────────────────────────────
    n_total    = view["SO"].nunique()
    n_breached = (view["SLA Status"] == "Breached").sum()
    n_at_risk  = (view["SLA Status"] == "At Risk").sum()
    n_on_track = (view["SLA Status"] == "On Track").sum()
    n_done     = (view["SLA Status"] == "Done").sum()

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Orders shown",  f"{n_total:,}")
    k2.metric("🔴 Breached",   f"{n_breached:,}")
    k3.metric("🟡 At Risk",    f"{n_at_risk:,}")
    k4.metric("🟢 On Track",   f"{n_on_track:,}")
    k5.metric("✅ Done",       f"{n_done:,}")

    st.divider()

    # ── Read-only summary table ─────────────────────────────────────────────
    st.subheader("Order List")
    st.caption(f"{len(view):,} item row(s) across {n_total:,} order(s)  ·  "
               f"Deadline by rule: DOT-GO/LITE 7d · DOT-V2.1 7w · Plan month 7w · "
               f"FT-34/45/56 3/4/5w · LIFO14 14 working days · At-risk ≤ {SLA_AT_RISK_DAYS}d")

    display_cols = ["SO", "Customer Name", "Order Date", "Item Sku", "Item Name",
                    "Item Note", "Item QTY", "Order Status", "Status", "Production Stage",
                    "SLA Basis", "SLA Status", "SLA Countdown", "_deadline_str"]
    table_view = view[[c for c in display_cols if c in view.columns]].rename(columns={
        "Item Name":     "Item Description",
        "_deadline_str": "SLA Deadline",
    }).copy()

    # Prepend SLA colour icon to SLA Status for quick scanning
    if "SLA Status" in table_view.columns:
        table_view["SLA Status"] = table_view["SLA Status"].apply(
            lambda s: f"{SLA_COLOUR.get(s, '')} {s}"
        )

    STATUS_OPTS = ["", "Cancel", "Claim", "Completed",
                   "Pending Client Confirmation", "Ready"]
    STAGE_OPTS  = ["", "Assembly", "Cutting", "Final Quality", "Painting",
                   "Painting Prep", "Procurement", "Sponging", "Upholstery",
                   "Veneer Final", "Veneer Internal", "Wood Work", "Completed"]

    display_df = fmt_dates(table_view)   # dates → strings; Status/Stage stay as-is

    edited = st.data_editor(
        display_df,
        column_config={
            "SO":              st.column_config.TextColumn("SO", width="small"),
            "Customer Name":   st.column_config.TextColumn("Customer Name", width="medium"),
            "Order Date":      st.column_config.TextColumn("Order Date", width="small"),
            "Item Sku":        st.column_config.TextColumn("Item Sku", width="medium"),
            "Item Description": st.column_config.TextColumn("Item Description", width="large"),
            "Item Note":       st.column_config.TextColumn("Item Note", width="medium"),
            "Item QTY":        st.column_config.NumberColumn("Qty", width="small"),
            "Order Status":    st.column_config.TextColumn("Order Status", width="small"),
            "Status": st.column_config.SelectboxColumn(
                "Status", options=STATUS_OPTS, required=False, width="medium",
            ),
            "Production Stage": st.column_config.SelectboxColumn(
                "Production Stage", options=STAGE_OPTS, required=False, width="medium",
            ),
            "SLA Basis":       st.column_config.TextColumn("SLA Basis", width="small"),
            "SLA Status":      st.column_config.TextColumn("SLA Status", width="small"),
            "SLA Countdown":   st.column_config.TextColumn("SLA Countdown", width="small"),
            "SLA Deadline":    st.column_config.TextColumn("SLA Deadline", width="small"),
        },
        disabled=[c for c in display_df.columns if c not in ("Status", "Production Stage")],
        hide_index=True,
        use_container_width=True,
        height=600,
        key="tracker_editor",
    )

    # ── Detect changes and offer a single Save button ──────────────────────
    if "Status" in table_view.columns and "Production Stage" in table_view.columns:
        orig_s = table_view["Status"].fillna("").reset_index(drop=True)
        orig_g = table_view["Production Stage"].fillna("").reset_index(drop=True)
        new_s  = edited["Status"].fillna("").reset_index(drop=True)
        new_g  = edited["Production Stage"].fillna("").reset_index(drop=True)

        changed_mask = (new_s != orig_s) | (new_g != orig_g)
        changed_sos  = (
            edited[changed_mask.values]
            .drop_duplicates(subset="SO")[["SO", "Status", "Production Stage"]]
        )

        if not changed_sos.empty:
            if st.button(f"💾 Save {len(changed_sos):,} change(s)",
                         type="primary", key="tr_save_all"):
                with st.spinner("Saving…"):
                    errors = []
                    # Normalise: SelectboxColumn returns Python None for blank option;
                    # convert to "" so gspread writes an empty cell (not null)
                    def _clean(v):
                        return "" if (v is None or str(v) == "None") else str(v)

                    # Build update map for batch write to 2026
                    so_updates = {
                        r["SO"]: {
                            "Status":           _clean(r["Status"]),
                            "Production Stage": _clean(r["Production Stage"]),
                        }
                        for _, r in changed_sos.iterrows()
                    }
                    # Write 1: update 2026 sheet (Statues + Status Manu columns)
                    try:
                        write_production_status(PROD_SHEET, so_updates)
                    except Exception as e:
                        errors.append(f"2026 sheet write failed: {e}")
                    # Write 2: upsert Order Status tab (tracker's read-back store)
                    for _, r in changed_sos.iterrows():
                        try:
                            upsert_order_status(PROD_SHEET, r["SO"],
                                                _clean(r["Status"]),
                                                _clean(r["Production Stage"]))
                        except Exception as e:
                            errors.append(f"{r['SO']} (status tab): {e}")
                if errors:
                    st.error("Some saves failed:\n" + "\n".join(errors))
                else:
                    st.success(f"Saved {len(changed_sos):,} order(s). Refreshing…")
                    st.cache_data.clear()
                    st.rerun()

    # ── Revision check: delivered but not marked Completed ─────────────────
    st.divider()
    st.subheader("🔍 Revision Check — Delivered but not marked Completed")
    st.caption(
        "SOs that have a **Delivery Date** in the Orders Plan sheet but are **not** "
        "marked Completed (Status / Production Stage) in the tracker. Their SLA is "
        "already frozen at the Delivery Date — review and mark them Completed if needed."
    )

    mismatch = tdf[tdf["_has_delivery"] & ~tdf["_marked_completed"]].copy()
    if mismatch.empty:
        st.success("✅ No mismatches — every delivered SO is marked Completed.")
    else:
        audit = (
            mismatch.sort_values("op_delivery")
            .drop_duplicates(subset="SO")[
                ["SO", "Customer Name", "op_delivery", "Status",
                 "Production Stage", "SLA Basis", "_deadline_str"]
            ]
            .rename(columns={
                "op_delivery":   "Delivery Date",
                "Status":        "Tracker Status",
                "_deadline_str": "SLA Deadline",
            })
        )
        st.warning(f"⚠️ {len(audit):,} delivered SO(s) not yet marked Completed.")
        st.dataframe(fmt_dates(audit), use_container_width=True, hide_index=True)
