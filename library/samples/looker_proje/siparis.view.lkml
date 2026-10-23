view: siparis {
  sql_table_name: dwh.fct_siparis ;;

  dimension: siparis_no {
    primary_key: yes
    type: string
    sql: ${TABLE}.siparis_no ;;
  }

  dimension_group: tarih {
    type: time
    timeframes: [date, week, month, year]
    sql: ${TABLE}.tarih ;;
  }

  dimension: musteri_id {
    type: number
    sql: ${TABLE}.musteri_id ;;
  }

  dimension: tutar {
    type: number
    sql: ${TABLE}.tutar ;;
  }

  dimension: indirim {
    type: number
    sql: ${TABLE}.indirim ;;
  }

  dimension: kanal {
    type: string
    sql: ${TABLE}.kanal ;;
  }

  measure: toplam_tutar {
    type: sum
    sql: ${tutar} ;;
  }

  measure: ortalama_indirim {
    type: average
    sql: ${indirim} ;;
  }
}
