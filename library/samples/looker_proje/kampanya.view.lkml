view: kampanya {
  sql_table_name: dwh.dim_kampanya ;;

  dimension: kampanya_id {
    primary_key: yes
    type: number
    sql: ${TABLE}.kampanya_id ;;
  }

  dimension: kampanya_adi {
    type: string
    sql: ${TABLE}.kampanya_adi ;;
  }

  dimension: butce {
    type: number
    sql: ${TABLE}.butce ;;
  }
}
