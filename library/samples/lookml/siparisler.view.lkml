view: siparisler {
  sql_table_name: dwh.fct_satis ;;

  dimension: siparis_no {
    type: string
    sql: ${TABLE}.siparis_no ;;
    description: "Siparis numarası"
  }

  dimension_group: tarih {
    type: time
    timeframes: [date, week, month, year]
    sql: ${TABLE}.tarih ;;
  }

  dimension: musteri_no {
    type: string
    sql: ${TABLE}.musteri_no ;;
  }

  dimension: tutar {
    type: number
    sql: ${TABLE}.tutar ;;
  }

  measure: toplam_tutar {
    type: sum
    sql: ${tutar} ;;
    value_format_name: decimal_2
    description: "Toplam ciro"
  }

  measure: siparis_sayisi {
    type: count_distinct
    sql: ${siparis_no} ;;
  }

  measure: ortalama_sepet {
    type: number
    sql: ${toplam_tutar} / NULLIF(${siparis_sayisi}, 0) ;;
  }

  measure: eski_olcu {
    type: sum
    sql: ${indirim} ;;
  }
}
