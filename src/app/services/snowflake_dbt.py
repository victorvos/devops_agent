from __future__ import annotations

import json
import logging
from typing import Any

from app.auth.entra import KeylessEntraTokenFactory
from app.config import Settings

logger = logging.getLogger(__name__)


class SnowflakeDbtService:
    """
    100% Keyless Snowflake + dbt integration service using Microsoft Entra ID External OAuth:
      1. Authenticates to Snowflake via `authenticator="oauth"` and an Entra access token
         scoped to `SNOWFLAKE_OAUTH_SCOPE` (no Snowflake username/password or RSA key file!).
      2. Inspects Snowflake `INFORMATION_SCHEMA.COLUMNS` and dbt model lineage for grounding.
      3. Runs `EXPLAIN USING TEXT` on proposed dbt SQL models to verify compilation & cost
         against Snowflake before pausing for Human-in-the-Loop approval.
      4. Persists LangGraph checkpoints and HITL approval audit records in
         `AGENT_STATE_DB.LANGGRAPH.THREAD_CHECKPOINTS` and `HITL_AUDIT_LOG`.
    """

    def __init__(self, settings: Settings, token_factory: KeylessEntraTokenFactory) -> None:
        self._settings = settings
        self._token_factory = token_factory

    def _connect_keyless_oauth(self, database: str, schema: str):
        import snowflake.connector

        oauth_token = self._token_factory.get_snowflake_oauth_token()
        return snowflake.connector.connect(
            account=self._settings.snowflake_account,
            user=self._settings.snowflake_user,
            authenticator="oauth",
            token=oauth_token,
            role=self._settings.snowflake_role,
            warehouse=self._settings.snowflake_warehouse,
            database=database,
            schema=schema,
        )

    async def get_dbt_and_snowflake_grounding(self, dbt_model_name: str) -> dict[str, Any]:
        """
        Retrieves existing Snowflake table metadata (`INFORMATION_SCHEMA.COLUMNS`) and dbt upstream lineage.
        """
        if self._settings.dry_run_snowflake:
            return {
                "snowflake_account": self._settings.snowflake_account,
                "database": self._settings.snowflake_analytics_database,
                "schema": self._settings.snowflake_analytics_schema,
                "target_dbt_model": dbt_model_name,
                "dbt_file_path": f"models/marts/customer/{dbt_model_name}.sql",
                "dbt_schema_yml_path": "models/marts/customer/schema.yml",
                "upstream_refs": ["stg_erp__customers", "int_orders__customer_activity"],
                "existing_columns": [
                    {"name": "CUSTOMER_ID", "type": "VARCHAR(64)", "nullable": False},
                    {"name": "CUSTOMER_SEGMENT", "type": "VARCHAR(32)", "nullable": True},
                    {"name": "DAYS_SINCE_LAST_ORDER", "type": "NUMBER(10,0)", "nullable": True},
                    {"name": "ACTIVE_SUBSCRIPTION_COUNT", "type": "NUMBER(10,0)", "nullable": False},
                ],
                "auth_mode": "entra_external_oauth",
            }

        conn = self._connect_keyless_oauth(
            database=self._settings.snowflake_analytics_database,
            schema=self._settings.snowflake_analytics_schema,
        )
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = UPPER(%s)
                ORDER BY ORDINAL_POSITION
                """,
                (self._settings.snowflake_analytics_schema, dbt_model_name),
            )
            cols = [
                {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
                for row in cur.fetchall()
            ]
            return {
                "snowflake_account": self._settings.snowflake_account,
                "database": self._settings.snowflake_analytics_database,
                "schema": self._settings.snowflake_analytics_schema,
                "target_dbt_model": dbt_model_name,
                "dbt_file_path": f"models/marts/customer/{dbt_model_name}.sql",
                "dbt_schema_yml_path": "models/marts/customer/schema.yml",
                "upstream_refs": ["stg_erp__customers", "int_orders__customer_activity"],
                "existing_columns": cols,
                "auth_mode": "entra_external_oauth",
            }
        finally:
            conn.close()

    def generate_dbt_sql_and_tests(self, dbt_model_name: str, prompt: str) -> dict[str, Any]:
        """
        Generates the proposed dbt `.sql` model and `schema.yml` data quality tests grounded in
        the M365 Copilot KPI rules and Snowflake column metadata.
        """
        dbt_sql = f"""{{{{
  config(
    materialized='incremental',
    unique_key='customer_id',
    on_schema_change='sync_all_columns',
    tags=['marts', 'customer', 'langgraph_managed']
  )
}}}}

WITH customers AS (
    SELECT * FROM {{{{ ref('stg_erp__customers') }}}}
),
activity AS (
    SELECT * FROM {{{{ ref('int_orders__customer_activity') }}}}
)
SELECT
    c.customer_id,
    c.customer_segment,
    a.days_since_last_order,
    a.active_subscription_count,
    IFF(a.days_since_last_order > 45 AND a.active_subscription_count = 0, TRUE, FALSE) AS is_churn_risk,
    CURRENT_TIMESTAMP() AS dbt_updated_at
FROM customers c
LEFT JOIN activity a
    ON c.customer_id = a.customer_id
"""
        compiled_sql = (
            f"SELECT c.customer_id, c.customer_segment, a.days_since_last_order, "
            f"a.active_subscription_count, "
            f"IFF(a.days_since_last_order > 45 AND a.active_subscription_count = 0, TRUE, FALSE) AS is_churn_risk "
            f"FROM {self._settings.snowflake_analytics_database}.STAGING.STG_ERP__CUSTOMERS c "
            f"LEFT JOIN {self._settings.snowflake_analytics_database}.INTERMEDIATE.INT_ORDERS__CUSTOMER_ACTIVITY a "
            f"ON c.customer_id = a.customer_id"
        )
        schema_yml_snippet = f"""version: 2

models:
  - name: {dbt_model_name}
    description: "Customer mart with M365 Governance KPI churn_risk flag ({prompt[:60]})"
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
      - name: is_churn_risk
        tests:
          - not_null
          - accepted_values:
              values: [true, false]
"""
        return {
            "dbt_model_name": dbt_model_name,
            "dbt_file_path": f"models/marts/customer/{dbt_model_name}.sql",
            "dbt_schema_yml_path": "models/marts/customer/schema.yml",
            "dbt_sql": dbt_sql.strip(),
            "compiled_sql": compiled_sql,
            "schema_yml_snippet": schema_yml_snippet.strip(),
            "dbt_tests": ["unique(customer_id)", "not_null(customer_id)", "not_null(is_churn_risk)", "accepted_values(is_churn_risk)"],
        }

    async def validate_sql_with_snowflake_explain(self, compiled_sql: str) -> dict[str, Any]:
        """
        Runs `EXPLAIN USING TEXT <compiled_sql>` against Snowflake using the read-only/agent role
        to prove the proposed dbt model compiles cleanly before asking a human to approve the PR.
        """
        if self._settings.dry_run_snowflake:
            return {
                "valid": True,
                "explain_summary": "GlobalStats: partitionsTotal=12, partitionsAssigned=12, bytesAssigned=1843200 -> Result [is_churn_risk: BOOLEAN]",
                "warehouse_used": self._settings.snowflake_warehouse,
            }

        conn = self._connect_keyless_oauth(
            database=self._settings.snowflake_analytics_database,
            schema=self._settings.snowflake_analytics_schema,
        )
        try:
            cur = conn.cursor()
            cur.execute(f"EXPLAIN USING TEXT {compiled_sql}")
            rows = cur.fetchall()
            plan_text = "\n".join(str(r[0]) for r in rows)[:600]
            return {
                "valid": True,
                "explain_summary": plan_text,
                "warehouse_used": self._settings.snowflake_warehouse,
            }
        finally:
            conn.close()

    async def record_snowflake_checkpoint_and_audit(
        self,
        *,
        thread_id: str,
        run_id: str,
        event_key: str,
        status: str,
        actor_upn: str,
        state_payload: dict[str, Any],
    ) -> None:
        """
        Persists thread state into `AGENT_STATE_DB.LANGGRAPH.THREAD_CHECKPOINTS` on Snowflake.
        """
        if self._settings.dry_run_snowflake:
            logger.info(
                "Snowflake state checkpoint synced (dry-run): thread_id=%s run_id=%s status=%s",
                thread_id,
                run_id,
                status,
            )
            return

        conn = self._connect_keyless_oauth(
            database=self._settings.snowflake_state_database,
            schema=self._settings.snowflake_state_schema,
        )
        try:
            cur = conn.cursor()
            cur.execute(
                """
                MERGE INTO THREAD_CHECKPOINTS t
                USING (SELECT %s AS THREAD_ID, %s AS RUN_ID) s
                ON t.THREAD_ID = s.THREAD_ID AND t.RUN_ID = s.RUN_ID
                WHEN MATCHED THEN UPDATE SET
                    STATUS = %s,
                    STATE_JSON = PARSE_JSON(%s),
                    UPDATED_AT = CURRENT_TIMESTAMP()
                WHEN NOT MATCHED THEN INSERT
                    (THREAD_ID, RUN_ID, EVENT_KEY, STATUS, ACTOR_UPN, STATE_JSON)
                VALUES (%s, %s, %s, %s, %s, PARSE_JSON(%s))
                """,
                (
                    thread_id,
                    run_id,
                    status,
                    json.dumps(state_payload, default=str),
                    thread_id,
                    run_id,
                    event_key,
                    status,
                    actor_upn,
                    json.dumps(state_payload, default=str),
                ),
            )
        finally:
            conn.close()
