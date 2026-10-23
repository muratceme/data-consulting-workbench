connection: "dwh"

include: "/*.view.lkml"

explore: siparis {
  join: musteri {
    type: left_outer
    relationship: many_to_one
    sql_on: ${siparis.musteri_id} = ${musteri.musteri_id} ;;
  }
}

explore: musteri_analizi {
  from: musteri
}
