"""Interactive Streamlit operations dashboard for the ColdWatch AI FastAPI backend.

Requires: streamlit>=1.40, pandas, requests (altair ships with Streamlit).

Interactive features
- Page navigation with cross-page jumps (e.g. click a KPI banner -> filtered alarm list)
- Clickable Altair charts (click a severity bar to filter the recent-alarm table)
- Row-selectable tables, multi-select + bulk alarm updates
- Modal dialogs for every state change (no more long forms on the page)
- Lifecycle steppers + one-click "next step" buttons
- Kanban board for inspections and remediations
- Zone cards + live auto-refreshing telemetry (zoom/pan charts, sensor filter)
- Toast notifications, CSV export, operator name remembered across actions
"""

from __future__ import annotations

from datetime import datetime
import os
from typing import Any

import altair as alt
import pandas as pd
import requests
import streamlit as st

DEFAULT_API_URL = "http://127.0.0.1:8000"
TIMEOUT = 10

ALARM_FLOW = ["OPEN", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "CLOSED"]
INSPECTION_FLOW = ["PENDING", "IN_PROGRESS", "COMPLETED"]
REMEDIATION_FLOW = ["PLANNED", "IN_PROGRESS", "COMPLETED"]
VERIFICATION_FLOW = ["PENDING", "PASSED"]

ALARM_NEXT = {
    "OPEN": ["ACKNOWLEDGED", "INVESTIGATING"],
    "ACKNOWLEDGED": ["INVESTIGATING"],
    "INVESTIGATING": ["ACKNOWLEDGED", "RESOLVED"],
    "RESOLVED": ["CLOSED", "INVESTIGATING"],
    "CLOSED": [],
}
INSPECTION_NEXT = {
    "PENDING": ["IN_PROGRESS", "CANCELLED"],
    "IN_PROGRESS": ["COMPLETED", "CANCELLED"],
    "COMPLETED": [],
    "CANCELLED": [],
}
REMEDIATION_NEXT = {
    "PLANNED": ["IN_PROGRESS", "CANCELLED"],
    "IN_PROGRESS": ["COMPLETED", "CANCELLED"],
    "COMPLETED": [],
    "CANCELLED": [],
}
VERIFICATION_NEXT = {
    "PENDING": ["PASSED", "FAILED", "NOT_REQUIRED"],
    "FAILED": ["PASSED", "NOT_REQUIRED"],
    "PASSED": [],
    "NOT_REQUIRED": [],
}

ALARM_TYPES = [
    "All", "TEMPERATURE_EXCURSION", "HUMIDITY_EXCURSION", "OCCUPANCY_LIMIT",
    "DOOR_OPEN_TOO_LONG", "SENSOR_OUTAGE", "SENSOR_DRIFT",
    "INCOMPATIBLE_STORAGE", "INVALID_SENSOR_MAPPING", "INCOMPLETE_IMPACT_ASSESSMENT",
]
SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
SEVERITY_ICON = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
COLORS = {
    # severity
    "CRITICAL": "#e5484d", "HIGH": "#f76b15", "MEDIUM": "#d9b800", "LOW": "#30a46c",
    # lifecycle
    "OPEN": "#e5484d", "ACKNOWLEDGED": "#f5a524", "INVESTIGATING": "#3e63dd",
    "RESOLVED": "#30a46c", "CLOSED": "#8b8d98",
    "PENDING": "#f5a524", "IN_PROGRESS": "#3e63dd", "COMPLETED": "#30a46c",
    "CANCELLED": "#8b8d98", "PLANNED": "#f5a524",
    "PASSED": "#30a46c", "FAILED": "#e5484d", "NOT_REQUIRED": "#8b8d98",
    # dispositions
    "RELEASED": "#30a46c", "QUARANTINED": "#f76b15", "REJECTED": "#e5484d",
    "RETEST_REQUIRED": "#d9b800",
}
STYLED_COLUMNS = {
    "severity", "status", "impact_status", "verification_status", "product_disposition",
}
KANBAN_LIMIT = 8
NAV = ["Overview", "Alarm center", "Warehouse monitor", "Workflows"]


class APIError(RuntimeError):
    """Raised when the dashboard cannot complete an API request."""


def _configured_value(name: str, default: str = "") -> str:
    """Read a deployment value from Streamlit Secrets, then the process environment."""
    value: Any = None
    try:
        value = st.secrets.get(name)
    except Exception:
        value = None
    if value in (None, ""):
        value = os.getenv(name, default)
    return str(value if value not in (None, "") else default).strip()


# --------------------------------------------------------------------------- #
# API layer
# --------------------------------------------------------------------------- #
def _request(method: str, base_url: str, endpoint: str, **kwargs: Any) -> Any:
    url = f"{base_url.rstrip('/')}{endpoint}"
    headers = dict(kwargs.pop("headers", {}) or {})
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        token = _configured_value("COLDWATCH_API_WRITE_TOKEN")
        if token:
            headers.setdefault("X-API-Token", token)
    if headers:
        kwargs["headers"] = headers
    try:
        response = requests.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise APIError(
            f"Could not connect to {url}. Check that the FastAPI server is running. {exc}"
        ) from exc
    if not response.ok:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise APIError(f"HTTP {response.status_code}: {detail}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise APIError(f"The API returned non-JSON content for {endpoint}.") from exc


@st.cache_data(ttl=10, show_spinner=False)
def api_get(
    base_url: str,
    endpoint: str,
    params: tuple[tuple[str, Any], ...] = (),
) -> Any:
    return _request("GET", base_url, endpoint, params=dict(params))


def api_get_live(base_url: str, endpoint: str, params: dict[str, Any]) -> Any:
    """Uncached GET used by auto-refreshing panels."""
    return _request("GET", base_url, endpoint, params=params)


def api_post(base_url: str, endpoint: str, payload: dict[str, Any]) -> Any:
    result = _request("POST", base_url, endpoint, json=payload)
    st.cache_data.clear()
    return result


# --------------------------------------------------------------------------- #
# UI helpers
# --------------------------------------------------------------------------- #
def _cell_style(value: Any) -> str:
    color = COLORS.get(value) if isinstance(value, str) else None
    if not color:
        return ""
    return f"background-color: {color}33; color: {color}; font-weight: 600;"


def _frame(records: list[dict[str, Any]], columns: list[str] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    if columns:
        frame = frame[[name for name in columns if name in frame.columns]]
    return frame


def _styled(frame: pd.DataFrame):
    styler = frame.style
    apply_cells = styler.map if hasattr(styler, "map") else styler.applymap
    subset = [c for c in frame.columns if c in STYLED_COLUMNS]
    if subset:
        apply_cells(_cell_style, subset=subset)
    return styler


def _table(
    records: list[dict[str, Any]],
    columns: list[str] | None = None,
    height: int = 280,
) -> None:
    if not records:
        st.caption("No records found.")
        return
    st.dataframe(
        _styled(_frame(records, columns)),
        use_container_width=True,
        hide_index=True,
        height=height,
    )


def _selectable_table(
    records: list[dict[str, Any]],
    columns: list[str],
    key: str,
    height: int = 280,
    multi: bool = False,
) -> list[dict[str, Any]]:
    """Render a table whose rows can be clicked; return the selected records."""
    if not records:
        st.caption("No records match the current filters.")
        return []
    event = st.dataframe(
        _styled(_frame(records, columns)),
        use_container_width=True,
        hide_index=True,
        height=height,
        on_select="rerun",
        selection_mode="multi-row" if multi else "single-row",
        key=key,
    )
    rows = event.selection.rows if event and event.selection else []
    return [records[i] for i in rows if i < len(records)]


def _csv_button(records: list[dict[str, Any]], name: str, key: str) -> None:
    if records:
        st.download_button(
            "⬇ Export CSV",
            pd.DataFrame(records).to_csv(index=False).encode("utf-8"),
            file_name=f"{name}.csv",
            mime="text/csv",
            key=key,
        )


def _metrics(values: list[tuple[str, Any]]) -> None:
    cols = st.columns(len(values))
    for col, (label, value) in zip(cols, values):
        col.metric(label, value, border=True)


def _stepper(flow: list[str], current: str) -> None:
    """Show a progress bar + labelled lifecycle position."""
    if current in flow:
        idx = flow.index(current)
        st.progress((idx + 1) / len(flow))
        st.caption("  →  ".join(f"**{s}**" if s == current else s for s in flow))
    else:
        st.caption(f"Lifecycle ended: **{current}**")


def _after_action(message: str) -> None:
    st.session_state["coldwatch_flash"] = message
    st.rerun()


def _operator() -> str:
    return st.session_state.get("operator", "warehouse.operator")


def _nice(value: str) -> str:
    return value.replace("_", " ").title()


def _set_state(key: str, value: Any) -> None:
    st.session_state[key] = value


def _jump_to_alarms(
    status: list[str] | None = None,
    severity: list[str] | None = None,
    alarm_id: str | None = None,
) -> None:
    """Callback: navigate to the Alarm center with filters pre-applied."""
    st.session_state["nav"] = "Alarm center"
    st.session_state["alarm_status_filter"] = status or []
    st.session_state["alarm_severity_filter"] = severity or []
    st.session_state["alarm_type_filter"] = "All"
    st.session_state["alarm_search"] = ""
    if alarm_id:
        st.session_state["active_alarm"] = alarm_id


def _run_action(label: str, func, *args) -> None:
    try:
        func(*args)
    except APIError as exc:
        st.error(str(exc))


# --------------------------------------------------------------------------- #
# Dialogs (modal forms)
# --------------------------------------------------------------------------- #
@st.dialog("Update alarm")
def alarm_transition_dialog(
    base_url: str, alarm_id: str, next_status: str, assignee_default: str
) -> None:
    st.markdown(f"Move **{alarm_id}** → **{next_status}**")
    actor = st.text_input("Changed by", value=_operator())
    assignee = st.text_input("Assign to (optional)", value=assignee_default)
    reason = st.text_area("Reason", placeholder="What was observed or decided?")
    if st.button("Confirm transition", type="primary", use_container_width=True):
        if not actor.strip() or len(reason.strip()) < 3:
            st.error("Enter an actor and a reason of at least 3 characters.")
            return
        try:
            api_post(
                base_url,
                f"/api/v1/alarms/{alarm_id}/transitions",
                {
                    "new_status": next_status,
                    "changed_by": actor.strip(),
                    "reason": reason.strip(),
                    "assigned_to": assignee.strip() or None,
                },
            )
        except APIError as exc:
            st.error(str(exc))
            return
        _after_action(f"Alarm {alarm_id} moved to {next_status}.")


@st.dialog("Bulk update alarms")
def bulk_alarm_dialog(base_url: str, alarms: list[dict[str, Any]]) -> None:
    common = set(ALARM_NEXT.get(alarms[0]["status"], []))
    for alarm in alarms[1:]:
        common &= set(ALARM_NEXT.get(alarm["status"], []))
    st.markdown(f"**{len(alarms)}** alarms selected.")
    if not common:
        st.warning("The selected alarms have no valid next status in common.")
        return
    next_status = st.selectbox("Move all to", [s for s in ALARM_FLOW if s in common])
    actor = st.text_input("Changed by", value=_operator())
    reason = st.text_area("Reason (applied to every alarm)")
    if st.button("Apply to all", type="primary", use_container_width=True):
        if not actor.strip() or len(reason.strip()) < 3:
            st.error("Enter an actor and a reason of at least 3 characters.")
            return
        bar = st.progress(0.0)
        failures: list[str] = []
        for i, alarm in enumerate(alarms, start=1):
            try:
                api_post(
                    base_url,
                    f"/api/v1/alarms/{alarm['alarm_id']}/transitions",
                    {
                        "new_status": next_status,
                        "changed_by": actor.strip(),
                        "reason": reason.strip(),
                        "assigned_to": alarm.get("assigned_to"),
                    },
                )
            except APIError as exc:
                failures.append(f"{alarm['alarm_id']}: {exc}")
            bar.progress(i / len(alarms))
        if failures:
            st.error("Some updates failed:\n\n" + "\n\n".join(failures))
        else:
            _after_action(f"{len(alarms)} alarms moved to {next_status}.")


@st.dialog("Create inspection")
def inspection_create_dialog(base_url: str, alarm_id: str, lots: list[str]) -> None:
    st.markdown(f"New inspection for **{alarm_id}**")
    inspection_type = st.selectbox(
        "Inspection type",
        ["PRODUCT_QUALITY", "SENSOR_HEALTH", "FACILITY", "STORAGE_COMPATIBILITY", "DOOR_EVENT"],
    )
    lot_choice = st.selectbox("Impacted lot (optional)", ["No specific lot"] + lots)
    requested_by = st.text_input("Requested by", value=_operator())
    inspector = st.text_input("Inspector (optional)")
    if st.button("Create inspection", type="primary", use_container_width=True):
        if not requested_by.strip():
            st.error("Enter who requested the inspection.")
            return
        try:
            result = api_post(
                base_url,
                f"/api/v1/alarms/{alarm_id}/inspections",
                {
                    "inspection_type": inspection_type,
                    "requested_by": requested_by.strip(),
                    "lot_id": None if lot_choice == "No specific lot" else lot_choice,
                    "inspector": inspector.strip() or None,
                },
            )
        except APIError as exc:
            st.error(str(exc))
            return
        new_id = result.get("result", {}).get("inspection_id", "created")
        _after_action(f"Inspection {new_id} created.")


@st.dialog("Update inspection")
def inspection_transition_dialog(
    base_url: str, inspection: dict[str, Any], next_status: str
) -> None:
    inspection_id = inspection["inspection_id"]
    st.markdown(f"Move **{inspection_id}** → **{next_status}**")
    actor = st.text_input("Changed by", value=_operator())
    reason = st.text_area("Transition reason")
    findings, disposition = "", None
    if next_status == "COMPLETED":
        findings = st.text_area("Inspection findings")
        options = ["RELEASED", "QUARANTINED", "REJECTED", "RETEST_REQUIRED"]
        if inspection.get("inspection_type") != "PRODUCT_QUALITY":
            options.append("NOT_APPLICABLE")
        disposition = st.selectbox("Product disposition", options)
    if st.button("Confirm transition", type="primary", use_container_width=True):
        if not actor.strip() or len(reason.strip()) < 3:
            st.error("Enter an actor and a reason of at least 3 characters.")
            return
        if next_status == "COMPLETED" and not findings.strip():
            st.error("Inspection findings are required before completion.")
            return
        try:
            api_post(
                base_url,
                f"/api/v1/inspections/{inspection_id}/transitions",
                {
                    "new_status": next_status,
                    "changed_by": actor.strip(),
                    "reason": reason.strip(),
                    "findings": findings.strip() or None,
                    "product_disposition": disposition,
                },
            )
        except APIError as exc:
            st.error(str(exc))
            return
        _after_action(f"Inspection {inspection_id} moved to {next_status}.")


@st.dialog("Create remediation")
def remediation_create_dialog(base_url: str, inspection: dict[str, Any]) -> None:
    inspection_id = inspection["inspection_id"]
    st.markdown(f"New remediation for **{inspection_id}**")
    action_type = st.selectbox(
        "Action type",
        ["RELOCATE_LOT", "ADJUST_TEMPERATURE", "REPAIR_SENSOR",
         "SEAL_DOOR", "RECONFIGURE_STORAGE", "OTHER"],
    )
    description = st.text_area("Corrective action description")
    created_by = st.text_input("Created by", value=_operator())
    assigned_to = st.text_input("Assign to (optional)")
    if st.button("Create remediation", type="primary", use_container_width=True):
        if not created_by.strip() or len(description.strip()) < 5:
            st.error("Enter a creator and a description of at least 5 characters.")
            return
        try:
            result = api_post(
                base_url,
                f"/api/v1/inspections/{inspection_id}/remediations",
                {
                    "action_type": action_type,
                    "description": description.strip(),
                    "created_by": created_by.strip(),
                    "lot_id": inspection.get("lot_id"),
                    "assigned_to": assigned_to.strip() or None,
                },
            )
        except APIError as exc:
            st.error(str(exc))
            return
        new_id = result.get("result", {}).get("remediation_id", "created")
        _after_action(f"Remediation {new_id} created.")


@st.dialog("Update remediation")
def remediation_transition_dialog(
    base_url: str, remediation_id: str, next_status: str
) -> None:
    st.markdown(f"Move **{remediation_id}** → **{next_status}**")
    actor = st.text_input("Changed by", value=_operator())
    reason = st.text_area("Transition reason")
    if st.button("Confirm transition", type="primary", use_container_width=True):
        if not actor.strip() or len(reason.strip()) < 3:
            st.error("Enter an actor and a reason of at least 3 characters.")
            return
        try:
            api_post(
                base_url,
                f"/api/v1/remediations/{remediation_id}/transitions",
                {"new_status": next_status, "changed_by": actor.strip(), "reason": reason.strip()},
            )
        except APIError as exc:
            st.error(str(exc))
            return
        _after_action(f"Remediation {remediation_id} moved to {next_status}.")


@st.dialog("Independent verification")
def verification_dialog(base_url: str, remediation_id: str, result: str) -> None:
    st.markdown(f"Record **{result}** for **{remediation_id}**")
    verifier = st.text_input("Verified by", value=_operator())
    reason = st.text_area("Verification reason")
    if st.button("Save verification", type="primary", use_container_width=True):
        if not verifier.strip() or len(reason.strip()) < 3:
            st.error("Enter a verifier and a reason of at least 3 characters.")
            return
        try:
            api_post(
                base_url,
                f"/api/v1/remediations/{remediation_id}/verification",
                {
                    "new_verification_status": result,
                    "verified_by": verifier.strip(),
                    "reason": reason.strip(),
                },
            )
        except APIError as exc:
            st.error(str(exc))
            return
        _after_action(f"Remediation {remediation_id} verification set to {result}.")


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
def render_overview(base_url: str, summary: dict[str, Any]) -> None:
    counts = summary.get("counts", {})
    statuses = summary.get("alarms_by_status", {})
    severities = summary.get("alarms_by_severity", {})
    impacts = summary.get("inventory_impacts_by_status", {})

    st.subheader("Operational overview")

    open_count = statuses.get("OPEN", 0)
    critical = severities.get("CRITICAL", 0)
    if open_count or critical:
        banner, action = st.columns([4, 1], vertical_alignment="center")
        banner.warning(
            f"**{open_count} open alarm(s)** need triage · **{critical} critical** in the system."
        )
        action.button(
            "Triage now →",
            type="primary",
            use_container_width=True,
            on_click=_jump_to_alarms,
            kwargs={"status": ["OPEN"]},
        )
    else:
        st.success("No open alarms. All clear.")

    _metrics([
        ("Warehouses", counts.get("warehouses", 0)),
        ("Zones", counts.get("zones", 0)),
        ("Sensor readings", f'{counts.get("sensor_readings", 0):,}'),
        ("Inventory lots", f'{counts.get("inventory_lots", 0):,}'),
        ("Inspections", counts.get("inspections", 0)),
        ("Remediations", counts.get("remediations", 0)),
    ])

    left, right = st.columns(2)
    with left:
        st.markdown("#### Alarms by severity")
        st.caption("Click a bar to filter the recent-alarm list below.")
        sev_df = pd.DataFrame(
            {"severity": SEVERITY_ORDER, "alarms": [severities.get(s, 0) for s in SEVERITY_ORDER]}
        )
        pick = alt.selection_point(name="sev_pick", fields=["severity"])
        sev_chart = (
            alt.Chart(sev_df)
            .mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5)
            .encode(
                x=alt.X("severity:N", sort=SEVERITY_ORDER, title=None),
                y=alt.Y("alarms:Q", title="Alarms"),
                color=alt.Color(
                    "severity:N",
                    scale=alt.Scale(
                        domain=SEVERITY_ORDER, range=[COLORS[s] for s in SEVERITY_ORDER]
                    ),
                    legend=None,
                ),
                opacity=alt.condition(pick, alt.value(1.0), alt.value(0.4)),
                tooltip=["severity", "alarms"],
            )
            .add_params(pick)
            .properties(height=260)
        )
        event = st.altair_chart(
            sev_chart, use_container_width=True, on_select="rerun", key="sev_chart"
        )
    with right:
        st.markdown("#### Alarm lifecycle")
        st.caption("Click a bar to open the Alarm center on that status.")
        status_df = pd.DataFrame(
            {"status": ALARM_FLOW, "alarms": [statuses.get(s, 0) for s in ALARM_FLOW]}
        )
        spick = alt.selection_point(name="status_pick", fields=["status"])
        status_chart = (
            alt.Chart(status_df)
            .mark_bar(cornerRadiusTopRight=5, cornerRadiusBottomRight=5)
            .encode(
                y=alt.Y("status:N", sort=ALARM_FLOW, title=None),
                x=alt.X("alarms:Q", title="Alarms"),
                color=alt.Color(
                    "status:N",
                    scale=alt.Scale(
                        domain=ALARM_FLOW, range=[COLORS[s] for s in ALARM_FLOW]
                    ),
                    legend=None,
                ),
                opacity=alt.condition(spick, alt.value(1.0), alt.value(0.4)),
                tooltip=["status", "alarms"],
            )
            .add_params(spick)
            .properties(height=260)
        )
        status_event = st.altair_chart(
            status_chart, use_container_width=True, on_select="rerun", key="status_chart"
        )

    status_sel = (getattr(status_event, "selection", None) or {}).get("status_pick", [])
    if status_sel:
        picked_status = status_sel[0].get("status")
        st.button(
            f"Open Alarm center → {picked_status}",
            on_click=_jump_to_alarms,
            kwargs={"status": [picked_status]},
        )

    sev_sel = (getattr(event, "selection", None) or {}).get("sev_pick", [])
    picked_sev = [p["severity"] for p in sev_sel if "severity" in p]

    recent_response = api_get(base_url, "/api/v1/alarms", (("limit", 200), ("offset", 0)))
    recent = recent_response.get("items", [])

    st.markdown("#### Alarm timeline")
    if recent:
        frame = pd.DataFrame(recent)
        frame["detected_at"] = pd.to_datetime(
            frame["detected_at"], utc=True, errors="coerce"
        ).dt.tz_convert(None)
        frame["day"] = frame["detected_at"].dt.floor("D")
        timeline = frame.dropna(subset=["day"]).groupby(["day", "severity"], as_index=False).size()
        timeline = timeline.rename(columns={"size": "alarms"})
        st.altair_chart(
            alt.Chart(timeline)
            .mark_bar()
            .encode(
                x=alt.X("day:T", title=None),
                y=alt.Y("alarms:Q", title="Alarms per day"),
                color=alt.Color(
                    "severity:N",
                    sort=SEVERITY_ORDER,
                    scale=alt.Scale(
                        domain=SEVERITY_ORDER, range=[COLORS[s] for s in SEVERITY_ORDER]
                    ),
                ),
                tooltip=["day:T", "severity", "alarms"],
            )
            .properties(height=220)
            .interactive(bind_y=False),
            use_container_width=True,
        )
    else:
        st.caption("No alarms have been recorded.")

    st.markdown("#### Inventory exposure")
    if impacts:
        imp_df = pd.DataFrame({"status": list(impacts), "lots": list(impacts.values())})
        st.altair_chart(
            alt.Chart(imp_df)
            .mark_bar(cornerRadiusTopRight=5, cornerRadiusBottomRight=5)
            .encode(
                y=alt.Y("status:N", sort="-x", title=None),
                x=alt.X("lots:Q", title="Impacted lots"),
                tooltip=["status", "lots"],
            )
            .properties(height=max(120, 36 * len(imp_df))),
            use_container_width=True,
        )
    else:
        st.caption("No inventory-impact records are available.")

    title = "#### Most recent alarms"
    if picked_sev:
        title += f" · filtered to {', '.join(picked_sev)}"
    st.markdown(title)
    shown = [r for r in recent if not picked_sev or r.get("severity") in picked_sev][:15]
    selected = _selectable_table(
        shown,
        ["alarm_id", "detected_at", "severity", "alarm_type", "title", "status", "zone_id"],
        key="overview_alarm_table",
        height=330,
    )
    if selected:
        row = selected[0]
        st.button(
            f"Investigate {row['alarm_id']} →",
            type="primary",
            on_click=_jump_to_alarms,
            kwargs={"alarm_id": row["alarm_id"]},
        )
    else:
        st.caption("Select a row to jump straight into its investigation.")


# --------------------------------------------------------------------------- #
# Alarm center
# --------------------------------------------------------------------------- #
def render_alarm_center(base_url: str) -> None:
    st.subheader("Alarm investigation")

    f_left, f_right = st.columns([3, 2])
    with f_left:
        severity_filter = st.pills(
            "Severity", SEVERITY_ORDER, selection_mode="multi", key="alarm_severity_filter",
            format_func=lambda s: f"{SEVERITY_ICON[s]} {s.title()}",
        ) or []
        status_filter = st.pills(
            "Status", ALARM_FLOW, selection_mode="multi", key="alarm_status_filter",
            format_func=_nice,
        ) or []
    with f_right:
        type_filter = st.selectbox("Risk type", ALARM_TYPES, key="alarm_type_filter")
        s1, s2 = st.columns([3, 2])
        search = s1.text_input(
            "Search", placeholder="id, title, zone…", key="alarm_search"
        ).strip().lower()
        sort_by = s2.selectbox("Sort", ["Newest", "Oldest", "Severity"], key="alarm_sort")

    params: list[tuple[str, Any]] = [("limit", 200), ("offset", 0)]
    if len(status_filter) == 1:
        params.append(("status", status_filter[0]))
    if len(severity_filter) == 1:
        params.append(("severity", severity_filter[0]))
    if type_filter != "All":
        params.append(("alarm_type", type_filter))
    response = api_get(base_url, "/api/v1/alarms", tuple(params))
    alarms = response.get("items", [])

    if status_filter:
        alarms = [a for a in alarms if a.get("status") in status_filter]
    if severity_filter:
        alarms = [a for a in alarms if a.get("severity") in severity_filter]
    if search:
        alarms = [
            a for a in alarms
            if search in " ".join(
                str(a.get(k, "")) for k in ("alarm_id", "title", "zone_id", "sensor_id", "alarm_type")
            ).lower()
        ]
    alarms.sort(key=lambda a: a.get("detected_at") or "", reverse=sort_by != "Oldest")
    if sort_by == "Severity":
        alarms.sort(
            key=lambda a: SEVERITY_ORDER.index(a["severity"]) if a.get("severity") in SEVERITY_ORDER else 99
        )

    top_l, top_r = st.columns([4, 1], vertical_alignment="center")
    top_l.caption(
        f"{len(alarms)} alarm(s) shown (API total for base filters: {response.get('total', 0)}). "
        "Click a row to investigate · tick several rows for bulk actions."
    )
    with top_r:
        _csv_button(alarms, "alarms", "alarms_csv")

    selected = _selectable_table(
        alarms,
        ["alarm_id", "detected_at", "severity", "alarm_type", "title", "status", "zone_id", "assigned_to"],
        key="alarm_table",
        height=300,
        multi=True,
    )

    if len(selected) > 1:
        with st.container(border=True):
            st.markdown(f"**{len(selected)} alarms selected**")
            st.caption(", ".join(a["alarm_id"] for a in selected[:10]) + (" …" if len(selected) > 10 else ""))
            if st.button("Bulk update status…", type="primary"):
                bulk_alarm_dialog(base_url, selected)
        return
    if len(selected) == 1:
        st.session_state["active_alarm"] = selected[0]["alarm_id"]

    alarm_id = st.session_state.get("active_alarm")
    if not alarm_id:
        st.info("Select an alarm row above to open its investigation panel.")
        return
    render_alarm_detail(base_url, alarm_id)


def render_alarm_detail(base_url: str, alarm_id: str) -> None:
    try:
        detail = api_get(base_url, f"/api/v1/alarms/{alarm_id}")
        impact_response = api_get(base_url, f"/api/v1/alarms/{alarm_id}/impacts")
    except APIError as exc:
        st.error(str(exc))
        return

    status = detail.get("status", "")
    severity = detail.get("severity", "")
    impacts = impact_response.get("items", [])

    st.divider()
    head, close = st.columns([6, 1], vertical_alignment="center")
    head.markdown(f"### {SEVERITY_ICON.get(severity, '⚪')} {detail.get('title', alarm_id)}")
    close.button("✕ Close", on_click=_set_state, args=("active_alarm", None), use_container_width=True)

    _metrics([
        ("Severity", severity or "—"),
        ("Status", status or "—"),
        ("Risk type", _nice(detail.get("alarm_type", "—"))),
        ("Impact assessment", _nice(str(detail.get("impact_assessment_status", "—")))),
    ])
    st.write(detail.get("description", "No description supplied."))
    st.caption(
        f'Alarm ID: {alarm_id} · Zone: {detail.get("zone_id") or "N/A"} · '
        f'Sensor: {detail.get("sensor_id") or "N/A"} · '
        f'Assigned to: {detail.get("assigned_to") or "unassigned"}'
    )
    _stepper(ALARM_FLOW, status)

    # One-click next-step buttons
    allowed = ALARM_NEXT.get(status, [])
    st.markdown("**Actions**")
    if not allowed and status in {"RESOLVED", "CLOSED"}:
        st.caption("This alarm is in a terminal or near-terminal state.")
    action_cols = st.columns(max(len(allowed) + 1, 2))
    for col, nxt in zip(action_cols, allowed):
        if col.button(
            f"→ {_nice(nxt)}",
            key=f"alarm_{alarm_id}_{nxt}",
            use_container_width=True,
            type="primary" if nxt == allowed[0] else "secondary",
        ):
            alarm_transition_dialog(base_url, alarm_id, nxt, detail.get("assigned_to") or "")
    if status not in {"RESOLVED", "CLOSED"}:
        if action_cols[len(allowed)].button(
            "🔍 New inspection", key=f"alarm_{alarm_id}_inspect", use_container_width=True
        ):
            lots = sorted({row["lot_id"] for row in impacts if row.get("lot_id")})
            inspection_create_dialog(base_url, alarm_id, lots)

    impact_tab, evidence_tab, history_tab = st.tabs(
        [f"Inventory impacts ({len(impacts)})", "Evidence", "Status history"]
    )
    with impact_tab:
        if impacts:
            status_counts = pd.Series([r.get("impact_status", "UNKNOWN") for r in impacts]).value_counts()
            chips = " · ".join(f"**{k}**: {v}" for k, v in status_counts.items())
            st.caption(f"{impact_response.get('total', len(impacts))} impact record(s) — {chips}")
        _table(
            impacts,
            [
                "lot_id", "product_code", "product_name", "product_category",
                "quantity_received", "quantity_unit", "zone_name", "impact_status",
                "impact_reason", "exposure_start_at", "exposure_end_at",
            ],
            250,
        )
        _csv_button(impacts, f"{alarm_id}_impacts", f"impacts_csv_{alarm_id}")
    with evidence_tab:
        _table(
            detail.get("evidence", []),
            [
                "evidence_id", "evidence_type", "observed_at", "sensor_reading_id",
                "door_event_id", "health_event_id", "evidence_note",
            ],
            230,
        )
    with history_tab:
        _table(detail.get("status_history", []), height=220)


# --------------------------------------------------------------------------- #
# Warehouse monitor
# --------------------------------------------------------------------------- #
def _telemetry_panel(
    base_url: str, zone_id: str, sensor_type: str, limit: int, thresholds: tuple[float | None, float | None]
) -> None:
    try:
        response = api_get_live(
            base_url,
            f"/api/v1/zones/{zone_id}/readings",
            {"sensor_type": sensor_type, "limit": limit},
        )
    except APIError as exc:
        st.error(str(exc))
        return
    readings = response.get("items", [])
    if not readings:
        st.info(f"No {sensor_type.lower()} readings are available for this zone.")
        return

    frame = pd.DataFrame(readings)
    frame["observed_at"] = pd.to_datetime(
        frame["observed_at"], utc=True, errors="coerce"
    ).dt.tz_convert(None)
    frame["reading_value"] = pd.to_numeric(frame["reading_value"], errors="coerce")
    frame = frame.dropna(subset=["observed_at", "reading_value"]).sort_values("observed_at")

    sensors = sorted(frame["sensor_id"].dropna().unique().tolist())
    chosen = st.multiselect(
        "Sensors", sensors, default=sensors, key=f"sensor_pick_{zone_id}_{sensor_type}"
    )
    view = frame[frame["sensor_id"].isin(chosen)] if chosen else frame.iloc[0:0]
    if view.empty:
        st.info("Select at least one sensor.")
        return

    latest = view.iloc[-1]
    unit = latest.get("unit", "") if "unit" in view.columns else ""
    _metrics([
        ("Latest", f'{latest["reading_value"]:.1f} {unit}'),
        ("Min", f'{view["reading_value"].min():.1f}'),
        ("Max", f'{view["reading_value"].max():.1f}'),
        ("Average", f'{view["reading_value"].mean():.1f}'),
        ("Readings", len(view)),
    ])

    base = (
        alt.Chart(view)
        .mark_line(point=alt.OverlayMarkDef(size=18))
        .encode(
            x=alt.X("observed_at:T", title=None),
            y=alt.Y("reading_value:Q", title=f"{_nice(sensor_type)} ({unit})", scale=alt.Scale(zero=False)),
            color=alt.Color("sensor_id:N", title="Sensor"),
            tooltip=[
                alt.Tooltip("observed_at:T", title="Time"),
                "sensor_id",
                alt.Tooltip("reading_value:Q", title="Value"),
                *([ "quality_status" ] if "quality_status" in view.columns else []),
            ],
        )
    )
    layers = [base]
    low, high = thresholds
    for value, color in ((low, "#3e63dd"), (high, "#e5484d")):
        if value is not None:
            layers.append(
                alt.Chart(pd.DataFrame({"v": [value]}))
                .mark_rule(strokeDash=[6, 4], color=color, size=2)
                .encode(y="v:Q")
            )
    st.altair_chart(
        alt.layer(*layers).properties(height=340).interactive(),
        use_container_width=True,
    )
    st.caption(f"Scroll to zoom · drag to pan · double-click to reset · updated {datetime.now():%H:%M:%S}")

    out_of_range = 0
    if high is not None:
        out_of_range += int((view["reading_value"] > high).sum())
    if low is not None:
        out_of_range += int((view["reading_value"] < low).sum())
    if (low is not None) or (high is not None):
        (st.error if out_of_range else st.success)(
            f"{out_of_range} reading(s) outside your threshold band."
        )

    with st.expander("Raw readings & data quality"):
        if "quality_status" in view.columns:
            st.bar_chart(view["quality_status"].value_counts(), horizontal=True)
        _table(
            view.sort_values("observed_at", ascending=False).to_dict("records"),
            ["observed_at", "sensor_id", "sensor_type", "reading_value", "unit", "quality_status"],
            260,
        )
        _csv_button(view.to_dict("records"), f"{zone_id}_{sensor_type.lower()}", f"readings_csv_{zone_id}")


def render_warehouse_monitor(base_url: str, live_seconds: int) -> None:
    st.subheader("Warehouse and zone monitor")
    warehouses = api_get(base_url, "/api/v1/warehouses").get("items", [])
    if not warehouses:
        st.info("No warehouse records are available.")
        return
    warehouse_map = {row["warehouse_id"]: row for row in warehouses}
    warehouse_id = st.selectbox(
        "Warehouse",
        list(warehouse_map),
        format_func=lambda value: f'{warehouse_map[value].get("name", value)} · {value}',
    )
    warehouse = warehouse_map[warehouse_id]
    location = ", ".join(str(warehouse.get(k)) for k in ("city", "region") if warehouse.get(k))
    _metrics([
        ("Zones", warehouse.get("zone_count", 0)),
        ("Active sensors", warehouse.get("sensor_count", 0)),
        ("Location", location or "—"),
    ])

    zones = api_get(base_url, "/api/v1/zones", (("warehouse_id", warehouse_id),)).get("items", [])
    if not zones:
        st.warning("This warehouse has no zones.")
        return
    zone_map = {row["zone_id"]: row for row in zones}
    if st.session_state.get("active_zone") not in zone_map:
        st.session_state["active_zone"] = zones[0]["zone_id"]
    zone_id = st.session_state["active_zone"]

    # Active (unresolved) alarms per zone for the cards
    all_alarms = api_get(base_url, "/api/v1/alarms", (("limit", 200), ("offset", 0))).get("items", [])
    live_alarms = [a for a in all_alarms if a.get("status") not in {"RESOLVED", "CLOSED"}]
    by_zone: dict[str, int] = {}
    for alarm in live_alarms:
        by_zone[alarm.get("zone_id")] = by_zone.get(alarm.get("zone_id"), 0) + 1

    st.markdown("#### Zones")
    cols = st.columns(3)
    for i, zone in enumerate(zones):
        zid = zone["zone_id"]
        active = zid == zone_id
        with cols[i % 3]:
            with st.container(border=True):
                st.markdown(f'**{zone.get("name", zid)}**' + ("  ·  ◀ viewing" if active else ""))
                st.caption(
                    f'{zid} · {zone.get("zone_type", "unknown type")} · {zone.get("status", "—")}\n\n'
                    f'{zone.get("active_sensor_count", 0)} sensors · {zone.get("current_lot_count", 0)} lots'
                )
                count = by_zone.get(zid, 0)
                st.markdown(f"🔴 **{count}** active alarm(s)" if count else "🟢 No active alarms")
                st.button(
                    "Monitoring" if active else "Monitor zone",
                    key=f"zone_btn_{zid}",
                    type="primary" if active else "secondary",
                    disabled=active,
                    on_click=_set_state,
                    args=("active_zone", zid),
                    use_container_width=True,
                )

    st.markdown(f'#### Telemetry · {zone_map[zone_id].get("name", zone_id)}')
    c1, c2, c3 = st.columns([2, 2, 1], vertical_alignment="bottom")
    sensor_type = c1.radio("Metric", ["TEMPERATURE", "HUMIDITY", "OCCUPANCY"], horizontal=True, format_func=_nice)
    limit = c2.slider("Readings to load", 50, 500, 200, step=50)
    live = c3.toggle("Live", value=live_seconds > 0, help="Auto-refresh this panel (interval set in the sidebar).")

    with st.expander("Threshold guides (optional, visual only)"):
        t1, t2 = st.columns(2)
        use_low = t1.checkbox("Lower bound")
        low = t1.number_input("Low", value=0.0, disabled=not use_low, label_visibility="collapsed")
        use_high = t2.checkbox("Upper bound")
        high = t2.number_input("High", value=8.0, disabled=not use_high, label_visibility="collapsed")
    thresholds = (low if use_low else None, high if use_high else None)

    run_every = (live_seconds or 10) if live else None
    st.fragment(run_every=run_every)(_telemetry_panel)(base_url, zone_id, sensor_type, limit, thresholds)

    zone_alarms = [a for a in live_alarms if a.get("zone_id") == zone_id]
    st.markdown(f"#### Active alarms in this zone ({len(zone_alarms)})")
    picked = _selectable_table(
        zone_alarms,
        ["alarm_id", "detected_at", "severity", "alarm_type", "title", "status"],
        key="zone_alarm_table",
        height=200,
    )
    if picked:
        st.button(
            f"Investigate {picked[0]['alarm_id']} →",
            type="primary",
            on_click=_jump_to_alarms,
            kwargs={"alarm_id": picked[0]["alarm_id"]},
        )


# --------------------------------------------------------------------------- #
# Workflows (kanban)
# --------------------------------------------------------------------------- #
def _kanban(
    items: list[dict[str, Any]],
    id_key: str,
    statuses: list[str],
    state_key: str,
    headline,
    subline,
) -> None:
    cols = st.columns(len(statuses))
    active = st.session_state.get(state_key)
    for col, status in zip(cols, statuses):
        bucket = [i for i in items if i.get("status") == status]
        with col:
            st.markdown(f":gray[**{_nice(status).upper()}**] · {len(bucket)}")
            for item in bucket[:KANBAN_LIMIT]:
                item_id = item[id_key]
                with st.container(border=True):
                    st.markdown(("▶ " if item_id == active else "") + headline(item))
                    st.caption(subline(item))
                    st.button(
                        "Open",
                        key=f"{state_key}_{item_id}",
                        on_click=_set_state,
                        args=(state_key, item_id),
                        type="primary" if item_id == active else "secondary",
                        use_container_width=True,
                    )
            if len(bucket) > KANBAN_LIMIT:
                st.caption(f"+{len(bucket) - KANBAN_LIMIT} more — narrow with search")


def _filter_items(items: list[dict[str, Any]], search: str, mine: bool, owner_key: str) -> list[dict[str, Any]]:
    out = items
    if search:
        out = [i for i in out if search in " ".join(str(v) for v in i.values()).lower()]
    if mine:
        me = _operator().lower()
        out = [i for i in out if me and me == str(i.get(owner_key) or "").lower()]
    return out


def render_inspections(base_url: str) -> None:
    response = api_get(base_url, "/api/v1/inspections", (("limit", 200), ("offset", 0)))
    inspections = response.get("items", [])
    f1, f2 = st.columns([3, 1], vertical_alignment="bottom")
    search = f1.text_input("Search inspections", key="insp_search", placeholder="id, alarm, lot, type…").strip().lower()
    mine = f2.toggle("Only mine", key="insp_mine", help="Matches the operator name in the sidebar to the inspector.")
    inspections = _filter_items(inspections, search, mine, "inspector")
    st.caption(f"{len(inspections)} of {response.get('total', len(inspections))} inspections")

    _kanban(
        inspections, "inspection_id", ["PENDING", "IN_PROGRESS", "COMPLETED", "CANCELLED"],
        "active_inspection",
        lambda i: f'**{i["inspection_id"]}**',
        lambda i: f'{_nice(i.get("inspection_type", ""))} · {i.get("alarm_id", "")}'
        + (f' · {i["product_disposition"]}' if i.get("product_disposition") else ""),
    )

    inspection_id = st.session_state.get("active_inspection")
    if not inspection_id:
        st.info("Open a card to see details and take action.")
        return
    try:
        inspection = api_get(base_url, f"/api/v1/inspections/{inspection_id}")
    except APIError as exc:
        st.error(str(exc))
        return

    st.divider()
    st.markdown(f"### Inspection {inspection_id}")
    status = inspection.get("status", "")
    _stepper(INSPECTION_FLOW, status)
    _table(
        [inspection],
        [
            "inspection_id", "alarm_id", "lot_id", "inspection_type", "requested_at",
            "due_at", "inspector", "status", "findings", "product_disposition",
        ],
        110,
    )
    allowed = INSPECTION_NEXT.get(status, [])
    buttons = st.columns(max(len(allowed) + 2, 3))
    for col, nxt in zip(buttons, allowed):
        if col.button(f"→ {_nice(nxt)}", key=f"insp_{inspection_id}_{nxt}", use_container_width=True,
                      type="primary" if nxt == allowed[0] else "secondary"):
            inspection_transition_dialog(base_url, inspection, nxt)
    if status == "COMPLETED":
        if buttons[len(allowed)].button("🛠 Create remediation", key=f"insp_{inspection_id}_rem",
                                        type="primary", use_container_width=True):
            remediation_create_dialog(base_url, inspection)
    buttons[-1].button(
        "Go to alarm →", key=f"insp_{inspection_id}_alarm", use_container_width=True,
        on_click=_jump_to_alarms, kwargs={"alarm_id": inspection.get("alarm_id")},
    )
    with st.expander("Status history"):
        _table(inspection.get("status_history", []), height=160)


def render_remediations(base_url: str) -> None:
    response = api_get(base_url, "/api/v1/remediations", (("limit", 200), ("offset", 0)))
    remediations = response.get("items", [])
    f1, f2 = st.columns([3, 1], vertical_alignment="bottom")
    search = f1.text_input("Search remediations", key="rem_search", placeholder="id, lot, action…").strip().lower()
    mine = f2.toggle("Only mine", key="rem_mine", help="Matches the operator name in the sidebar to the assignee.")
    remediations = _filter_items(remediations, search, mine, "assigned_to")
    st.caption(f"{len(remediations)} of {response.get('total', len(remediations))} remediations")

    _kanban(
        remediations, "remediation_id", ["PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"],
        "active_remediation",
        lambda r: f'**{r["remediation_id"]}**',
        lambda r: f'{_nice(r.get("action_type", ""))} · verify: {r.get("verification_status", "—")}',
    )

    remediation_id = st.session_state.get("active_remediation")
    if not remediation_id:
        st.info("Open a card to see details and take action.")
        return
    try:
        remediation = api_get(base_url, f"/api/v1/remediations/{remediation_id}")
    except APIError as exc:
        st.error(str(exc))
        return

    status = remediation.get("status", "")
    verification = remediation.get("verification_status", "")
    st.divider()
    st.markdown(f"### Remediation {remediation_id}")
    left, right = st.columns(2)
    with left:
        st.caption("Execution")
        _stepper(REMEDIATION_FLOW, status)
    with right:
        st.caption("Independent verification")
        _stepper(VERIFICATION_FLOW, verification) if verification in VERIFICATION_FLOW else st.markdown(
            f"**{verification or '—'}**"
        )
    _table(
        [remediation],
        [
            "remediation_id", "inspection_id", "lot_id", "action_type", "description",
            "assigned_to", "status", "verification_status", "started_at", "completed_at",
        ],
        110,
    )

    allowed = REMEDIATION_NEXT.get(status, [])
    verify_opts = VERIFICATION_NEXT.get(verification, []) if status == "COMPLETED" else []
    buttons = st.columns(max(len(allowed) + len(verify_opts), 2))
    for col, nxt in zip(buttons, allowed):
        if col.button(f"→ {_nice(nxt)}", key=f"rem_{remediation_id}_{nxt}", use_container_width=True,
                      type="primary" if nxt == allowed[0] else "secondary"):
            remediation_transition_dialog(base_url, remediation_id, nxt)
    for col, result in zip(buttons[len(allowed):], verify_opts):
        if col.button(f"✔ Verify: {_nice(result)}", key=f"ver_{remediation_id}_{result}", use_container_width=True):
            verification_dialog(base_url, remediation_id, result)

    with st.expander("Status and verification history"):
        st.markdown("**Status history**")
        _table(remediation.get("status_history", []), height=140)
        st.markdown("**Verification history**")
        _table(remediation.get("verification_history", []), height=140)


def render_workflows(base_url: str) -> None:
    st.subheader("Inspection and remediation desk")
    view = st.radio(
        "Board", ["Inspections", "Remediations"], horizontal=True,
        key="workflow_view", label_visibility="collapsed",
    )
    if view == "Inspections":
        render_inspections(base_url)
    else:
        render_remediations(base_url)


# --------------------------------------------------------------------------- #
# App shell
# --------------------------------------------------------------------------- #
def main() -> None:
    st.set_page_config(
        page_title="ColdWatch AI | Operations",
        page_icon="❄️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.6rem; padding-bottom: 2.5rem;}
        [data-testid="stMetric"] {
            background: rgba(110, 160, 200, 0.08);
            border: 1px solid rgba(110, 160, 200, 0.20);
            border-radius: 0.75rem;
        }
        div[data-testid="stMetricValue"] {font-size: 1.55rem;}
        [data-testid="stVerticalBlockBorderWrapper"] {transition: box-shadow .15s ease, transform .15s ease;}
        [data-testid="stVerticalBlockBorderWrapper"]:hover {box-shadow: 0 2px 14px rgba(62, 99, 221, .18);}
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("# ❄️ ColdWatch AI")
        st.caption("Warehouse environmental monitoring")
        base_url = st.text_input(
            "FastAPI base URL",
            value=st.session_state.get(
                "coldwatch_api_url",
                _configured_value("COLDWATCH_API_URL", DEFAULT_API_URL),
            ),
        ).strip().rstrip("/")
        st.session_state["coldwatch_api_url"] = base_url
        st.text_input(
            "Operator name", value="warehouse.operator", key="operator",
            help="Pre-fills the 'changed by' field in every action dialog.",
        )
        refresh_choice = st.selectbox("Live telemetry refresh", ["Off", "5s", "10s", "30s", "60s"], index=2)
        live_seconds = 0 if refresh_choice == "Off" else int(refresh_choice[:-1])
        if st.button("↻ Refresh data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.divider()
        alarm_slot = st.empty()
        st.caption("FastAPI backend · DuckDB")

    if not base_url:
        st.error("Enter a valid FastAPI base URL in the sidebar.")
        st.stop()

    flash = st.session_state.pop("coldwatch_flash", None)
    if flash:
        st.toast(flash, icon="✅")

    st.title("ColdWatch Operations Center")
    st.caption("Environmental signals · Risk alerts · Inventory exposure · Corrective workflows")
    try:
        health = api_get(base_url, "/health")
        summary = api_get(base_url, "/api/v1/summary")
    except APIError as exc:
        st.error("The dashboard cannot reach the ColdWatch API.")
        st.error(str(exc))
        st.info(
            "Start the backend in another terminal with: "
            "python scripts/run_api.py --database data/coldwatch-risk-engine.duckdb"
        )
        st.stop()

    open_alarms = summary.get("alarms_by_status", {}).get("OPEN", 0)
    alarm_slot.metric("Open alarms", open_alarms, border=True)

    status_col, db_col, tables_col = st.columns([1, 2, 1])
    status_col.success("API connected")
    db_col.caption(f'Database: {health.get("database", "unknown")}')
    tables_col.caption(f'Tables available: {health.get("tables_available", "—")}')

    page = st.radio("Navigate", NAV, horizontal=True, key="nav", label_visibility="collapsed")
    try:
        if page == "Overview":
            render_overview(base_url, summary)
        elif page == "Alarm center":
            render_alarm_center(base_url)
        elif page == "Warehouse monitor":
            render_warehouse_monitor(base_url, live_seconds)
        else:
            render_workflows(base_url)
    except APIError as exc:
        st.error(str(exc))


if __name__ == "__main__":
    main()