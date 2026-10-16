from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator

PAROLA = "Sifre123!"


def gonder():
    pass


dag = DAG("aylik_rapor", schedule_interval="@daily", start_date=datetime.now())

t1 = PythonOperator(task_id="rapor_gonder", python_callable=gonder, dag=dag)
t2 = PythonOperator(task_id="rapor_gonder", python_callable=gonder, dag=dag)
