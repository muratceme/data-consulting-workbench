%macro tarih_araligi(ay=);
  %global bas bit;
  %let bas = %sysfunc(intnx(month, "&ay."d, -1, b), date9.);
  %let bit = %sysfunc(intnx(month, "&ay."d, -1, e), date9.);
%mend tarih_araligi;

%macro log_yaz(mesaj=);
  data _null_;
    file "/sasdata/log/is_gunlugu.txt" mod;
    put "%sysfunc(datetime(), datetime20.) &mesaj.";
  run;
%mend log_yaz;
