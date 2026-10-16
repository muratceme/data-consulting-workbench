from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.providers.postgres.operators.postgres import PostgresOperator

default_args = {"owner": "veri-ekibi", "retries": 2, "retry_delay": timedelta(minutes=5)}

with DAG(
    dag_id="satis_gunluk",
    schedule="0 6 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args=default_args,
    tags=["satis", "erp"],
) as dag:
    cek = BashOperator(task_id="erp_cek", bash_command="python /opt/is/erp_cek.py")
    yukle = PostgresOperator(task_id="dwh_yukle", postgres_conn_id="dwh", sql="sql/yukle.sql")
    kontrol = BashOperator(task_id="kalite_kontrol", bash_command="python /opt/is/kontrol.py --strict")
    cek >> yukle >> kontrol
