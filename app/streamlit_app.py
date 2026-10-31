"""Streamlit operations dashboard for the ColdWatch AI FastAPI backend."""

from __future__ import annotations

from typing import Any

import pandas as pd
import requests
import streamlit as st

DEFAULT_API_URL = "http://127.0.0.1:8000"
TIMEOUT = 10
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


class APIError(RuntimeError):
    """Raised when the dashboard cannot complete an API request."""


def _request(method: str, base_url: str, endpoint: str, **kwargs: Any) -> Any:
    url = f"{base_url.rstrip('/')}{endpoint}"
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


@st.cache_data(ttl=15, show_spinner=False)
def api_get(
    base_url: str,
    endpoint: str,
    params: tuple[tuple[str, Any], ...] = (),
) -> Any:
    return _request("GET", base_url, endpoint, params=dict(params))


def api_post(base_url: str, endpoint: str, payload: dict[str, Any]) -> Any:
    result = _request("POST", base_url, endpoint, json=payload)
    st.cache_data.clear()
    return result


def _table(
    records: list[dict[str, Any]],
    columns: list[str] | None = None,
    height: int = 280,
) -> None:
    if not records:
        st.caption("No records found for the selected filters.")
        return
    frame = pd.DataFrame(records)
    if columns:
        frame = frame[[name for name in columns if name in frame.columns]]
    st.dataframe(frame, use_container_width=True, hide_index=True, height=height)


def _metrics(values: list[tuple[str, Any]]) -> None:
    cols = st.columns(len(values))
    for col, (label, value) in zip(cols, values):
        col.metric(label, value)


def _after_action(message: str) -> None:
    st.session_state["coldwatch_flash"] = message
    st.rerun()


def render_overview(base_url: str, summary: dict[str, Any]) -> None:
    counts = summary.get("counts", {})
    statuses = summary.get("alarms_by_status", {})
    severities = summary.get("alarms_by_severity", {})
    impacts = summary.get("inventory_impacts_by_status", {})

    st.subheader("Operational overview")
    _metrics([
        ("Warehouses", counts.get("warehouses", 0)),
        ("Zones", counts.get("zones", 0)),
        ("Sensor readings", f'{counts.get("sensor_readings", 0):,}'),
        ("Inventory lots", f'{counts.get("inventory_lots", 0):,}'),
        ("Total alarms", counts.get("alarms", 0)),
    ])
    _metrics([
        ("Open alarms", statuses.get("OPEN", 0)),
        ("Critical alarms", severities.get("CRITICAL", 0)),
        ("High alarms", severities.get("HIGH", 0)),
        ("Inspections", counts.get("inspections", 0)),
        ("Remediations", counts.get("remediations", 0)),
    ])

    left, right = st.columns(2)
    with left:
        st.markdown("#### Alarms by severity")
        if severities:
            st.bar_chart(pd.Series(severities, name="Alarms"))
        else:
            st.caption("No alarms have been recorded.")
    with right:
        st.markdown("#### Alarm lifecycle")
        if statuses:
            st.bar_chart(pd.Series(statuses, name="Alarms"))
        else:
            st.caption("No alarm statuses to display.")

    st.markdown("#### Inventory exposure")
    if impacts:
        st.bar_chart(pd.Series(impacts, name="Impacted lots"))
    else:
        st.caption("No inventory-impact records are available.")

    st.markdown("#### Most recent alarms")
    recent = api_get(base_url, "/api/v1/alarms", (("limit", 8), ("offset", 0)))
    _table(
        recent.get("items", []),
        ["alarm_id", "detected_at", "severity", "alarm_type", "title", "status", "zone_id"],
        300,
    )


def render_alarm_center(base_url: str) -> None:
    st.subheader("Alarm investigation")
    f1, f2, f3 = st.columns(3)
    status_options = ["All", "OPEN", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "CLOSED"]
    severity_options = ["All", "CRITICAL", "HIGH", "MEDIUM", "LOW"]
    type_options = [
        "All", "TEMPERATURE_EXCURSION", "HUMIDITY_EXCURSION", "OCCUPANCY_LIMIT",
        "DOOR_OPEN_TOO_LONG", "SENSOR_OUTAGE", "SENSOR_DRIFT",
        "INCOMPATIBLE_STORAGE", "INVALID_SENSOR_MAPPING", "INCOMPLETE_IMPACT_ASSESSMENT",
    ]
    with f1:
        status_filter = st.selectbox("Status", status_options, key="alarm_status_filter")
    with f2:
        severity_filter = st.selectbox("Severity", severity_options, key="alarm_severity_filter")
    with f3:
        type_filter = st.selectbox("Risk type", type_options, key="alarm_type_filter")

    params: list[tuple[str, Any]] = [("limit", 200), ("offset", 0)]
    if status_filter != "All":
        params.append(("status", status_filter))
    if severity_filter != "All":
        params.append(("severity", severity_filter))
    if type_filter != "All":
        params.append(("alarm_type", type_filter))
    response = api_get(base_url, "/api/v1/alarms", tuple(params))
    alarms = response.get("items", [])
    st.caption(f'{response.get("total", 0)} matching alarm(s); showing up to 200.')
    _table(
        alarms,
        ["alarm_id", "detected_at", "severity", "alarm_type", "title", "status", "zone_id", "assigned_to"],
        260,
    )
    if not alarms:
        return

    labels = {
        row["alarm_id"]: f'{row["severity"]} · {row["alarm_id"]} · {row["title"]} [{row["status"]}]'
        for row in alarms
    }
    alarm_id = st.selectbox(
        "Open alarm",
        list(labels),
        format_func=lambda value: labels[value],
        key="selected_alarm_id",
    )
    detail = api_get(base_url, f"/api/v1/alarms/{alarm_id}")
    impact_response = api_get(base_url, f"/api/v1/alarms/{alarm_id}/impacts")

    st.divider()
    st.markdown(f"### {detail.get('title', alarm_id)}")
    _metrics([
        ("Severity", detail.get("severity", "—")),
        ("Status", detail.get("status", "—")),
        ("Risk type", detail.get("alarm_type", "—")),
        ("Impact assessment", detail.get("impact_assessment_status", "—")),
    ])
    st.write(detail.get("description", "No description supplied."))
    st.caption(
        f'Alarm ID: {alarm_id} · Zone: {detail.get("zone_id") or "N/A"} · '
        f'Sensor: {detail.get("sensor_id") or "N/A"}'
    )

    impact_tab, evidence_tab, history_tab = st.tabs(
        ["Inventory impacts", "Evidence", "Status history"]
    )
    with impact_tab:
        st.caption(f'{impact_response.get("total", 0)} inventory impact record(s).')
        _table(
            impact_response.get("items", []),
            [
                "lot_id", "product_code", "product_name", "product_category",
                "quantity_received", "quantity_unit", "zone_name", "impact_status",
                "impact_reason", "exposure_start_at", "exposure_end_at",
            ],
            250,
        )
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

    st.markdown("#### Update alarm")
    allowed = ALARM_NEXT.get(detail.get("status", ""), [])
    if not allowed:
        st.info("This alarm is in a terminal state.")
    else:
        with st.form(f"alarm_transition_{alarm_id}"):
            next_status = st.selectbox("Move to", allowed)
            actor = st.text_input("Changed by", value="warehouse.operator")
            assignee = st.text_input("Assign to (optional)", value=detail.get("assigned_to") or "")
            reason = st.text_area("Reason")
            submitted = st.form_submit_button("Save alarm transition", type="primary")
        if submitted:
            if not actor.strip() or len(reason.strip()) < 3:
                st.error("Enter an actor and a reason of at least 3 characters.")
            else:
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
                    _after_action(f"Alarm {alarm_id} moved to {next_status}.")
                except APIError as exc:
                    st.error(str(exc))

    st.markdown("#### Create inspection")
    if detail.get("status") in {"RESOLVED", "CLOSED"}:
        st.caption("Inspections cannot be created for resolved or closed alarms.")
    else:
        lots = sorted({row["lot_id"] for row in impact_response.get("items", [])})
        lot_options = ["No specific lot"] + lots
        with st.form(f"inspection_create_{alarm_id}"):
            inspection_type = st.selectbox(
                "Inspection type",
                ["PRODUCT_QUALITY", "SENSOR_HEALTH", "FACILITY", "STORAGE_COMPATIBILITY", "DOOR_EVENT"],
            )
            lot_choice = st.selectbox("Impacted lot (optional)", lot_options)
            requested_by = st.text_input("Requested by", value="quality.coordinator")
            inspector = st.text_input("Inspector (optional)")
            create_submitted = st.form_submit_button("Create inspection")
        if create_submitted:
            if not requested_by.strip():
                st.error("Enter who requested the inspection.")
            else:
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
                    new_id = result.get("result", {}).get("inspection_id", "created")
                    _after_action(f"Inspection {new_id} created.")
                except APIError as exc:
                    st.error(str(exc))


def render_warehouse_monitor(base_url: str) -> None:
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
    location = ", ".join(
        str(warehouse.get(key)) for key in ("city", "region") if warehouse.get(key)
    )
    _metrics([
        ("Zones", warehouse.get("zone_count", 0)),
        ("Active sensors", warehouse.get("sensor_count", 0)),
        ("Location", location or "—"),
    ])

    zones = api_get(
        base_url, "/api/v1/zones", (("warehouse_id", warehouse_id),)
    ).get("items", [])
    if not zones:
        st.warning("This warehouse has no zones.")
        return
    zone_map = {row["zone_id"]: row for row in zones}
    zone_id = st.selectbox(
        "Zone",
        list(zone_map),
        format_func=lambda value: (
            f'{zone_map[value].get("name", value)} · {value} '
            f'({zone_map[value].get("zone_type", "unknown type")})'
        ),
    )
    zone = zone_map[zone_id]
    _metrics([
        ("Active sensors", zone.get("active_sensor_count", 0)),
        ("Current lots", zone.get("current_lot_count", 0)),
        ("Zone status", zone.get("status", "—")),
    ])

    sensor_type = st.selectbox("Telemetry type", ["TEMPERATURE", "HUMIDITY", "OCCUPANCY"])
    reading_response = api_get(
        base_url,
        f"/api/v1/zones/{zone_id}/readings",
        (("sensor_type", sensor_type), ("limit", 500)),
    )
    readings = reading_response.get("items", [])
    if not readings:
        st.info(f"No {sensor_type.lower()} readings are available for this zone.")
        return
    frame = pd.DataFrame(readings)
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
    frame["reading_value"] = pd.to_numeric(frame["reading_value"], errors="coerce")
    chart = (
        frame.dropna(subset=["observed_at", "reading_value"])
        .pivot_table(
            index="observed_at", columns="sensor_id",
            values="reading_value", aggfunc="mean",
        )
        .sort_index()
    )
    st.markdown(f"#### Recent {sensor_type.lower()} readings")
    if not chart.empty:
        st.line_chart(chart, use_container_width=True)
    _table(
        readings,
        ["observed_at", "sensor_id", "sensor_type", "reading_value", "unit", "quality_status"],
        260,
    )


def render_workflows(base_url: str) -> None:
    st.subheader("Inspection and remediation desk")
    inspection_filter = st.selectbox(
        "Inspection status",
        ["All", "PENDING", "IN_PROGRESS", "COMPLETED", "CANCELLED"],
        key="inspection_status_filter",
    )
    i_params: list[tuple[str, Any]] = [("limit", 100), ("offset", 0)]
    if inspection_filter != "All":
        i_params.append(("status", inspection_filter))
    inspection_response = api_get(base_url, "/api/v1/inspections", tuple(i_params))
    inspections = inspection_response.get("items", [])
    st.markdown(f'#### Inspections ({inspection_response.get("total", 0)})')
    _table(
        inspections,
        [
            "inspection_id", "alarm_id", "lot_id", "inspection_type",
            "requested_at", "inspector", "status", "product_disposition",
        ],
        210,
    )

    if inspections:
        i_labels = {
            row["inspection_id"]: f'{row["inspection_id"]} · {row["inspection_type"]} · {row["status"]}'
            for row in inspections
        }
        inspection_id = st.selectbox(
            "Select inspection", list(i_labels),
            format_func=lambda value: i_labels[value],
        )
        inspection = api_get(base_url, f"/api/v1/inspections/{inspection_id}")
        _table(
            [inspection],
            [
                "inspection_id", "alarm_id", "lot_id", "inspection_type",
                "requested_at", "due_at", "inspector", "status", "findings",
                "product_disposition",
            ],
            150,
        )
        with st.expander("Inspection status history"):
            _table(inspection.get("status_history", []), height=150)

        allowed = INSPECTION_NEXT.get(inspection.get("status", ""), [])
        if allowed:
            st.markdown("##### Update inspection")
            with st.form(f"inspection_transition_{inspection_id}"):
                next_status = st.selectbox("Move inspection to", allowed)
                actor = st.text_input("Changed by", value="quality.inspector")
                reason = st.text_area("Transition reason")
                findings = ""
                disposition = None
                if next_status == "COMPLETED":
                    findings = st.text_area("Inspection findings")
                    options = ["RELEASED", "QUARANTINED", "REJECTED", "RETEST_REQUIRED"]
                    if inspection.get("inspection_type") != "PRODUCT_QUALITY":
                        options.append("NOT_APPLICABLE")
                    disposition = st.selectbox("Product disposition", options)
                submitted = st.form_submit_button("Save inspection transition")
            if submitted:
                if not actor.strip() or len(reason.strip()) < 3:
                    st.error("Enter an actor and a reason of at least 3 characters.")
                elif next_status == "COMPLETED" and not findings.strip():
                    st.error("Inspection findings are required before completion.")
                else:
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
                        _after_action(f"Inspection {inspection_id} moved to {next_status}.")
                    except APIError as exc:
                        st.error(str(exc))

        if inspection.get("status") == "COMPLETED":
            st.markdown("##### Create remediation")
            with st.form(f"remediation_create_{inspection_id}"):
                action_type = st.selectbox(
                    "Action type",
                    [
                        "RELOCATE_LOT", "ADJUST_TEMPERATURE", "REPAIR_SENSOR",
                        "SEAL_DOOR", "RECONFIGURE_STORAGE", "OTHER",
                    ],
                )
                description = st.text_area("Corrective action description")
                created_by = st.text_input("Created by", value="warehouse.manager")
                assigned_to = st.text_input("Assign to (optional)")
                submitted = st.form_submit_button("Create remediation")
            if submitted:
                if not created_by.strip() or len(description.strip()) < 5:
                    st.error("Enter a creator and a description of at least 5 characters.")
                else:
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
                        new_id = result.get("result", {}).get("remediation_id", "created")
                        _after_action(f"Remediation {new_id} created.")
                    except APIError as exc:
                        st.error(str(exc))

    st.divider()
    remediation_filter = st.selectbox(
        "Remediation status",
        ["All", "PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"],
        key="remediation_status_filter",
    )
    r_params: list[tuple[str, Any]] = [("limit", 100), ("offset", 0)]
    if remediation_filter != "All":
        r_params.append(("status", remediation_filter))
    remediation_response = api_get(base_url, "/api/v1/remediations", tuple(r_params))
    remediations = remediation_response.get("items", [])
    st.markdown(f'#### Remediations ({remediation_response.get("total", 0)})')
    _table(
        remediations,
        [
            "remediation_id", "inspection_id", "lot_id", "action_type",
            "assigned_to", "status", "verification_status", "created_at",
        ],
        210,
    )
    if not remediations:
        return

    r_labels = {
        row["remediation_id"]: f'{row["remediation_id"]} · {row["action_type"]} · {row["status"]}'
        for row in remediations
    }
    remediation_id = st.selectbox(
        "Select remediation", list(r_labels),
        format_func=lambda value: r_labels[value],
    )
    remediation = api_get(base_url, f"/api/v1/remediations/{remediation_id}")
    _table(
        [remediation],
        [
            "remediation_id", "inspection_id", "lot_id", "action_type",
            "description", "assigned_to", "status", "verification_status",
            "started_at", "completed_at",
        ],
        170,
    )
    with st.expander("Remediation status and verification history"):
        st.markdown("**Status history**")
        _table(remediation.get("status_history", []), height=140)
        st.markdown("**Verification history**")
        _table(remediation.get("verification_history", []), height=140)

    current_status = remediation.get("status", "")
    allowed = REMEDIATION_NEXT.get(current_status, [])
    if allowed:
        st.markdown("##### Update remediation")
        with st.form(f"remediation_transition_{remediation_id}"):
            next_status = st.selectbox("Move remediation to", allowed)
            actor = st.text_input("Changed by", value="warehouse.operator")
            reason = st.text_area("Transition reason")
            submitted = st.form_submit_button("Save remediation transition")
        if submitted:
            if not actor.strip() or len(reason.strip()) < 3:
                st.error("Enter an actor and a reason of at least 3 characters.")
            else:
                try:
                    api_post(
                        base_url,
                        f"/api/v1/remediations/{remediation_id}/transitions",
                        {
                            "new_status": next_status,
                            "changed_by": actor.strip(),
                            "reason": reason.strip(),
                        },
                    )
                    _after_action(f"Remediation {remediation_id} moved to {next_status}.")
                except APIError as exc:
                    st.error(str(exc))

    verification_status = remediation.get("verification_status", "")
    verification_options = VERIFICATION_NEXT.get(verification_status, [])
    if current_status == "COMPLETED" and verification_options:
        st.markdown("##### Record independent verification")
        with st.form(f"verification_{remediation_id}"):
            next_verification = st.selectbox("Verification result", verification_options)
            verifier = st.text_input("Verified by", value="quality.inspector")
            reason = st.text_area("Verification reason")
            submitted = st.form_submit_button("Save verification")
        if submitted:
            if not verifier.strip() or len(reason.strip()) < 3:
                st.error("Enter a verifier and a reason of at least 3 characters.")
            else:
                try:
                    api_post(
                        base_url,
                        f"/api/v1/remediations/{remediation_id}/verification",
                        {
                            "new_verification_status": next_verification,
                            "verified_by": verifier.strip(),
                            "reason": reason.strip(),
                        },
                    )
                    _after_action(
                        f"Remediation {remediation_id} verification set to {next_verification}."
                    )
                except APIError as exc:
                    st.error(str(exc))


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
            padding: 0.85rem 1rem;
            border-radius: 0.75rem;
        }
        div[data-testid="stMetricValue"] {font-size: 1.65rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.sidebar:
        st.markdown("# ❄️ ColdWatch AI")
        st.caption("Warehouse environmental monitoring")
        base_url = st.text_input(
            "FastAPI base URL",
            value=st.session_state.get("coldwatch_api_url", DEFAULT_API_URL),
        ).strip().rstrip("/")
        st.session_state["coldwatch_api_url"] = base_url
        if st.button("↻ Refresh data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.divider()
        st.caption("Prototype mode · Local API · DuckDB")
        st.caption("Keep both services local.")

    if not base_url:
        st.error("Enter a valid FastAPI base URL in the sidebar.")
        st.stop()

    flash = st.session_state.pop("coldwatch_flash", None)
    if flash:
        st.success(flash)

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

    status_col, db_col, tables_col = st.columns([1, 2, 1])
    status_col.success("API connected")
    db_col.caption(f'Database: {health.get("database", "unknown")}')
    tables_col.caption(f'Tables available: {health.get("tables_available", "—")}')

    overview_tab, alarms_tab, warehouse_tab, workflows_tab = st.tabs(
        ["Overview", "Alarm center", "Warehouse monitor", "Workflows"]
    )
    try:
        with overview_tab:
            render_overview(base_url, summary)
        with alarms_tab:
            render_alarm_center(base_url)
        with warehouse_tab:
            render_warehouse_monitor(base_url)
        with workflows_tab:
            render_workflows(base_url)
    except APIError as exc:
        st.error(str(exc))


if __name__ == "__main__":
    main()
