import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np
from extract import (load_orders, load_unit_counts, load_dot_items, write_dot_tags,
                     load_production_plan, load_production_items, write_production_status,
                     get_new_production_orders, append_to_2026)

st.set_page_config(page_title="Orders Dashboard", layout="wide")
col_title, col_refresh = st.columns([9, 1])
col_title.title("Orders Dashboard")
if col_refresh.button("🔄 Refresh", help="Reload all data from Google Sheet"):
    st.cache_data.clear()
    st.rerun()

SHEET      = "https://docs.google.com/spreadsheets/d/1cEpLqAb_sqOoGxQ7GezAgyAlfQz4fOlpPVRuX-mimaA/edit"
PROD_SHEET = "https://docs.google.com/spreadsheets/d/1zpdNjDFPRi7RCvMImn3-soW0P3dYubS1d76FwsjfL28/edit"


def week_start(date):
    """Return the Saturday that begins the Sat–Thu week containing 'date'."""
    days_back = (date.weekday() - 5) % 7
    return date - pd.Timedelta(days=int(days_back))


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


@st.cache_data(ttl=300)
def get_production_data() -> pd.DataFrame:
    """Load and enrich Data per order as the sole production data source."""
    df = load_production_items(PROD_SHEET)

    # Parse dates
    for col in ["Order Date", "Picking Ship Date", "Order Ship Date"]:
        df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce")

    # SLA Timer: days until Picking Ship Date for unshipped items
    today_ts = pd.Timestamp.today().normalize()
    df["SLA Timer"] = df.apply(
        lambda r: (r["Picking Ship Date"] - today_ts).days
                  if pd.notna(r["Picking Ship Date"]) and pd.isna(r["Order Ship Date"])
                  else np.nan,
        axis=1,
    )

    def _sla_label(row):
        if pd.notna(row["Order Ship Date"]):
            return "Shipped"
        if pd.isna(row["Picking Ship Date"]):
            return "—"
        d = row["SLA Timer"]
        if pd.isna(d):
            return "—"
        if d == 0:
            return "TODAY"
        return f"{int(d)} days left" if d > 0 else f"{int(-d)} days overdue"

    df["SLA Label"] = df.apply(_sla_label, axis=1)

    # Numeric columns
    for col in ["Item QTY", "Over Due", "Order Total"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # Drop blank SO rows
    df = df[df["SO"].astype(str).str.strip().astype(bool)]
    return df


@st.cache_data(ttl=300)
def get_production_status() -> pd.DataFrame:
    """Read current Status and Production Stage per SO from the 2026 worksheet."""
    plan = load_production_plan(PROD_SHEET)
    return (
        plan[["SO", "Status", "Production Stage"]]
        .replace("", pd.NA)
        .dropna(subset=["SO"])
        .groupby("SO")[["Status", "Production Stage"]]
        .first()
        .reset_index()
        .fillna("")
    )


df = get_data()
dot_items_df = get_dot_items()
prod_df = get_production_data()
prod_status_df = get_production_status()

tab_orders, tab_kpi, tab_dot, tab_production = st.tabs(["Orders", "Delivery KPI", "DOT Orders", "Production"])

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
# TAB 4 — PRODUCTION
# ════════════════════════════════════════════════════════════════════════════
with tab_production:
    st.subheader("Production")

    # ── Top-level filters ──────────────────────────────────────────────────
    with st.expander("🔽 Filters", expanded=False):
        fc1, fc2, fc3, fc4 = st.columns(4)

        p_status_opts = sorted(prod_df["Order Status"].replace("", pd.NA).dropna().unique().tolist())
        f_prod_status = fc1.multiselect("Order Status", p_status_opts, key="prod_f_status")

        p_class_opts = sorted(prod_df["Order Class"].replace("", pd.NA).dropna().unique().tolist())
        f_prod_class  = fc2.multiselect("Order Class", p_class_opts, key="prod_f_class")

        f_prod_cust = fc3.text_input("Customer Name", key="prod_f_cust").strip()

        f_prod_sla = fc4.radio(
            "SLA Timer", ["All", "Due this week", "Overdue only"],
            horizontal=True, key="prod_f_sla",
        )

        fc5, fc6, _, _ = st.columns(4)
        od_prod = prod_df["Order Date"].dropna()
        if not od_prod.empty:
            f_prod_od = fc5.date_input(
                "Order Date", value=(od_prod.min().date(), od_prod.max().date()), key="prod_f_od"
            )
        else:
            f_prod_od = ()
        ps_prod = prod_df["Picking Ship Date"].dropna()
        if not ps_prod.empty:
            f_prod_ps = fc6.date_input(
                "Picking Ship Date", value=(ps_prod.min().date(), ps_prod.max().date()), key="prod_f_ps"
            )
        else:
            f_prod_ps = ()

    # ── Apply filters ──────────────────────────────────────────────────────
    pf = prod_df.copy()
    if f_prod_status:
        pf = pf[pf["Order Status"].isin(f_prod_status)]
    if f_prod_class:
        pf = pf[pf["Order Class"].isin(f_prod_class)]
    if f_prod_cust:
        pf = pf[pf["Customer Name"].str.contains(f_prod_cust, case=False, na=False)]
    if f_prod_sla == "Overdue only":
        pf = pf[pf["SLA Timer"].notna() & (pf["SLA Timer"] < 0)]
    elif f_prod_sla == "Due this week":
        pf = pf[pf["SLA Timer"].notna() & (pf["SLA Timer"] >= 0) & (pf["SLA Timer"] <= 7)]
    if len(f_prod_od) == 2:
        mask = pf["Order Date"].isna() | (
            (pf["Order Date"] >= pd.Timestamp(f_prod_od[0])) &
            (pf["Order Date"] <= pd.Timestamp(f_prod_od[1]))
        )
        pf = pf[mask]
    if len(f_prod_ps) == 2:
        mask = pf["Picking Ship Date"].isna() | (
            (pf["Picking Ship Date"] >= pd.Timestamp(f_prod_ps[0])) &
            (pf["Picking Ship Date"] <= pd.Timestamp(f_prod_ps[1]))
        )
        pf = pf[mask]

    # ── KPI Row 1 ──────────────────────────────────────────────────────────
    total_prod_orders = pf["SO"].nunique()
    total_prod_items  = int(pf["Item QTY"].sum())
    n_shipped   = pf[pf["Order Ship Date"].notna()]["SO"].nunique()
    n_overdue   = pf[pf["SLA Timer"].notna() & (pf["SLA Timer"] < 0)]["SO"].nunique()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Orders",  f"{total_prod_orders:,}")
    k2.metric("Total Items",   f"{total_prod_items:,}")
    k3.metric("Shipped",       f"{n_shipped:,}")
    k4.metric("Overdue",       f"{n_overdue:,}")

    # ── KPI Row 2 — top 5 order classes ───────────────────────────────────
    class_counts = (
        pf[pf["Order Class"].str.strip().astype(bool)]
        .groupby("Order Class")["SO"].nunique()
        .sort_values(ascending=False).head(5)
    )
    if not class_counts.empty:
        cls_cols = st.columns(len(class_counts))
        for i, (cls, count) in enumerate(class_counts.items()):
            cls_cols[i].metric(cls, f"{count:,}")

    st.divider()
    col_a, col_b = st.columns(2)

    # ── Chart: Items by Order Class ────────────────────────────────────────
    with col_a:
        class_chart = (
            pf[pf["Order Class"].str.strip().astype(bool)]
            .groupby("Order Class")["Item QTY"].sum()
            .reset_index(name="Items")
            .sort_values("Items", ascending=False)
        )
        if not class_chart.empty:
            fig_class = px.bar(
                class_chart, x="Order Class", y="Items",
                color="Order Class", title="Items by Order Class",
            )
            fig_class.update_layout(showlegend=False)
            st.plotly_chart(fig_class, use_container_width=True)
        else:
            st.info("No order class data for the selected filters.")

    # ── Chart: SLA Status breakdown ────────────────────────────────────────
    with col_b:
        sla_cat = pf["SLA Label"].replace("", "—")
        sla_dist = sla_cat.value_counts().reset_index()
        sla_dist.columns = ["SLA Status", "Count"]
        sla_color_map = {
            "Shipped":  "#27ae60",
            "TODAY":    "#f39c12",
            "—":        "#bdc3c7",
        }
        # Colour overdue entries red, "X days left" green-ish
        fig_sla = px.pie(
            sla_dist, names="SLA Status", values="Count",
            title="Items by SLA Status",
        )
        st.plotly_chart(fig_sla, use_container_width=True)

    # ── Order Detail table ─────────────────────────────────────────────────
    st.subheader("Order Detail")

    detail_cols = ["SO", "Customer Name", "Order Date", "Order Status", "Order Class",
                   "Item Sku", "Item Name", "Item QTY", "Picking Ship Date", "SLA Label",
                   "Order Ship Date", "Item Note"]
    prod_detail = pf[[c for c in detail_cols if c in pf.columns]].copy()

    with st.expander("🔽 Column Filters", expanded=False):
        pfc1, pfc2, pfc3, pfc4 = st.columns(4)
        f_pd_so     = pfc1.text_input("SO", key="pd_f_so").strip()
        f_pd_cust   = pfc2.text_input("Customer Name", key="pd_f_cust").strip()
        f_pd_sku    = pfc3.text_input("Item SKU / Name", key="pd_f_sku").strip()
        f_pd_status = pfc4.multiselect(
            "Order Status",
            sorted(prod_detail["Order Status"].replace("", pd.NA).dropna().unique().tolist()),
            key="pd_f_status",
        )

    if f_pd_so:
        prod_detail = prod_detail[prod_detail["SO"].str.contains(f_pd_so, case=False, na=False)]
    if f_pd_cust:
        prod_detail = prod_detail[prod_detail["Customer Name"].str.contains(f_pd_cust, case=False, na=False)]
    if f_pd_sku:
        mask = (
            prod_detail["Item Sku"].str.contains(f_pd_sku, case=False, na=False) |
            prod_detail["Item Name"].str.contains(f_pd_sku, case=False, na=False)
        )
        prod_detail = prod_detail[mask]
    if f_pd_status:
        prod_detail = prod_detail[prod_detail["Order Status"].isin(f_pd_status)]

    st.caption(f"{len(prod_detail):,} item(s) across {prod_detail['SO'].nunique():,} order(s)")
    st.dataframe(
        fmt_dates(prod_detail.rename(columns={"SLA Label": "SLA Timer"})),
        use_container_width=True,
    )

    # ── Expandable rows ────────────────────────────────────────────────────
    st.markdown("**Expand order to view all items:**")
    for so in prod_detail["SO"].unique():
        so_rows = pf[pf["SO"] == so]
        cust    = so_rows["Customer Name"].iloc[0] if len(so_rows) > 0 else ""
        status  = so_rows["Order Status"].iloc[0] if len(so_rows) > 0 else ""
        sla     = so_rows["SLA Label"].iloc[0] if len(so_rows) > 0 else "—"
        n_items = len(so_rows)
        label   = (
            f"📦  {so}  |  {cust}  |  {status}  |  "
            f"{n_items} item{'s' if n_items != 1 else ''}  |  SLA: {sla}"
        )
        with st.expander(label):
            items = so_rows[["Item Sku", "Item Name", "Item QTY", "Item Note"]]\
                .rename(columns={"Item QTY": "QTY"})\
                .reset_index(drop=True)
            st.dataframe(items, use_container_width=True)

    # ── Update Production Status ───────────────────────────────────────────
    st.divider()
    st.subheader("Update Production Status")
    st.caption(
        "Set the Status and Production Stage for each order below, "
        "then click **Apply All Changes** to save to the Production Planning Sheet."
    )

    PROD_STATUS_OPTS = ["", "Cancel", "Claim", "Completed",
                        "Pending Client Confirmation", "Ready"]
    PROD_STAGE_OPTS  = ["", "Assembly", "Cutting", "Final Quality", "Painting",
                        "Painting Prep", "Procurement", "Sponging", "Upholstery",
                        "Veneer Final", "Veneer Internal", "Wood Work", "Completed"]

    # Build SO → current values lookup from the 2026 sheet
    status_lookup = prod_status_df.set_index("SO").to_dict("index") if not prod_status_df.empty else {}

    unique_prod_sos = prod_detail["SO"].unique()

    if len(unique_prod_sos) == 0:
        st.info("No orders to update. Adjust your filters above.")
    else:
        # Column headers
        h1, h2, h3, h4 = st.columns([2, 2, 2, 1])
        h1.caption("Order")
        h2.caption("Status")
        h3.caption("Production Stage")
        h4.caption("")
        st.divider()

        for so in unique_prod_sos:
            so_rows   = pf[pf["SO"] == so]
            cust      = so_rows["Customer Name"].iloc[0] if len(so_rows) > 0 else ""
            cur       = status_lookup.get(so, {})
            cur_status = cur.get("Status", "")
            cur_stage  = cur.get("Production Stage", "")

            s_idx = PROD_STATUS_OPTS.index(cur_status) if cur_status in PROD_STATUS_OPTS else 0
            g_idx = PROD_STAGE_OPTS.index(cur_stage)   if cur_stage  in PROD_STAGE_OPTS  else 0

            c1, c2, c3, _ = st.columns([2, 2, 2, 1])
            c1.markdown(f"**{so}**  {cust}")
            c2.selectbox("Status", PROD_STATUS_OPTS, index=s_idx,
                         key=f"ps_status_{so}", label_visibility="collapsed")
            c3.selectbox("Production Stage", PROD_STAGE_OPTS, index=g_idx,
                         key=f"ps_stage_{so}", label_visibility="collapsed")

        st.divider()
        if st.button("💾 Apply All Changes", type="primary", key="prod_apply_all"):
            so_updates = {
                so: {
                    "Status":           st.session_state.get(f"ps_status_{so}", ""),
                    "Production Stage": st.session_state.get(f"ps_stage_{so}", ""),
                }
                for so in unique_prod_sos
            }
            with st.spinner("Writing to Production Planning Sheet…"):
                try:
                    updated = write_production_status(PROD_SHEET, so_updates)
                    if updated:
                        st.success(f"Updated {len(updated)} order(s). Refreshing…")
                    else:
                        st.warning(
                            "No matching orders found in the 2026 worksheet. "
                            "Only orders that already appear in that sheet can be updated."
                        )
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Write failed: {e}")

    # ── Sync new orders from Copy of Data per order ────────────────────────
    st.divider()
    st.subheader("Sync New Orders → 2026 Sheet")
    st.caption(
        "Finds orders in **Copy of Data per order** that are not yet in **2026**, "
        "and appends them in the correct format. Use the date filter to control which orders to add."
    )

    if st.button("🔍 Check for new orders", key="prod_check_new"):
        with st.spinner("Scanning for new orders…"):
            try:
                new_orders_df = get_new_production_orders(PROD_SHEET)
                st.session_state["new_prod_orders"] = new_orders_df
            except Exception as e:
                st.error(f"Failed to read sheets: {e}")
                st.session_state["new_prod_orders"] = pd.DataFrame()

    if "new_prod_orders" in st.session_state and not st.session_state["new_prod_orders"].empty:
        new_df = st.session_state["new_prod_orders"].copy()

        # Date range filter to avoid syncing thousands of old orders
        od_new = new_df["Order Date"].dropna()
        if not od_new.empty:
            sync_col1, sync_col2, _ = st.columns([2, 2, 2])
            sync_range = sync_col1.date_input(
                "Filter by Order Date before syncing",
                value=(od_new.max().date() - pd.Timedelta(days=90), od_new.max().date()),
                key="sync_date_range",
            )
            if len(sync_range) == 2:
                new_df = new_df[
                    new_df["Order Date"].isna() | (
                        (new_df["Order Date"] >= pd.Timestamp(sync_range[0])) &
                        (new_df["Order Date"] <= pd.Timestamp(sync_range[1]))
                    )
                ]

        n_orders = new_df["SO"].nunique()
        n_items  = len(new_df)
        st.info(f"**{n_orders:,} new order(s)** · {n_items:,} item row(s) ready to sync")

        with st.expander(f"Preview {n_items:,} rows to be added"):
            st.dataframe(
                fmt_dates(new_df[["SO", "Order Date", "Customer Name", "Order Status",
                                  "Item Sku", "Item Name", "Item QTY", "Order Class"]]),
                use_container_width=True,
            )

        if st.button(f"➕ Add {n_orders:,} order(s) to 2026 sheet", type="primary", key="prod_sync_now"):
            with st.spinner(f"Appending {n_items:,} rows to 2026…"):
                try:
                    added = append_to_2026(PROD_SHEET, new_df)
                    st.success(f"✅ Added {added:,} row(s) to the 2026 worksheet. Refreshing…")
                    del st.session_state["new_prod_orders"]
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Append failed: {e}")

    elif "new_prod_orders" in st.session_state:
        st.success("✅ No new orders found — 2026 sheet is up to date.")
