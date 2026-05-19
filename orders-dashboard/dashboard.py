import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np
from extract import load_orders, load_unit_counts, load_dot_items, write_dot_tags

st.set_page_config(page_title="Orders Dashboard", layout="wide")
col_title, col_refresh = st.columns([9, 1])
col_title.title("Orders Dashboard")
if col_refresh.button("🔄 Refresh", help="Reload all data from Google Sheet"):
    st.cache_data.clear()
    st.rerun()

SHEET = "https://docs.google.com/spreadsheets/d/1cEpLqAb_sqOoGxQ7GezAgyAlfQz4fOlpPVRuX-mimaA/edit"


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


df = get_data()
dot_items_df = get_dot_items()

tab_orders, tab_kpi, tab_dot = st.tabs(["Orders", "Delivery KPI", "DOT Orders"])

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

    search_order = st.text_input(
        "Search orders", placeholder="SO number, customer name, notes…",
        key="orders_search"
    ).strip()

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

    if search_order:
        mask = orders_display.apply(
            lambda col: col.astype(str).str.contains(search_order, case=False, na=False)
        ).any(axis=1)
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
    st.dataframe(
        fmt_dates(kpi_df[[c for c in detail_cols if c in kpi_df.columns]].sort_values("Delivery Status")),
        use_container_width=True,
    )


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
        "Cancelled": "#bdc3c7",
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

    # Search box
    search_so = st.text_input("Search SO number", placeholder="e.g. S0008814", key="dot_search").strip()
    detail_df = dot_df.copy()
    if search_so:
        detail_df = detail_df[detail_df["SO"].str.contains(search_so, case=False, na=False)]

    detail_df = detail_df.sort_values(["Status", "DOT Status"])

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
