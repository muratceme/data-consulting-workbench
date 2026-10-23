* Aylık satış raporu;
proc import datafile="C:\Raporlar\hedefler.xlsx" out=work.hedef dbms=xlsx replace;
run;

data work.rapor;
  merge work.satis(in=a) work.hedef(in=b);
  by bolge;
  if a;
  gerceklesme = ciro / hedef;
run;

proc freq data=work.rapor;
  tables bolge * kanal / nocol nopercent;
run;

proc tabulate data=work.rapor;
  class bolge kanal;
  var ciro hedef;
  table bolge, kanal*(ciro hedef)*sum;
run;

proc export data=work.rapor outfile="C:\Raporlar\aylik.xlsx" dbms=xlsx replace;
run;
