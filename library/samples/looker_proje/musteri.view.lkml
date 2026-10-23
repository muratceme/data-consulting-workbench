view: musteri {
  sql_table_name: dwh.dim_musteri ;;

  dimension: musteri_id {
    primary_key: yes
    type: number
    sql: ${TABLE}.musteri_id ;;
  }

  dimension: ad {
    type: string
    sql: ${TABLE}.ad ;;
  }

  dimension: sehir {
    type: string
    sql: ${TABLE}.sehir ;;
  }

  dimension: segment {
    type: string
    sql: ${TABLE}.segment ;;
  }

  dimension_group: kayit {
    type: time
    timeframes: [date, month]
    sql: ${TABLE}.kayit_tarihi ;;
  }
}
