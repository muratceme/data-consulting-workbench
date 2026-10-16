view: musteriler {
  sql_table_name: dwh.dim_musteri ;;

  dimension: musteri_no {
    primary_key: yes
    type: string
    sql: ${TABLE}.musteri_no ;;
  }

  dimension: segment {
    type: string
    sql: ${TABLE}.segment ;;
    description: "Müşteri segmenti"
  }

  measure: musteri_sayisi {
    type: count
  }
}
