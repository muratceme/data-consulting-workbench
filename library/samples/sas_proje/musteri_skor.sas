/* Müşteri churn skoru - aylık */
libname dwh oracle path="DWHPRD" schema="ANALITIK" user=sas_etl;
libname cikti "/sasdata/skor/cikti";
%include "/sasdata/ortak/makrolar.sas";

%tarih_araligi(ay=&sysdate9.);

proc sql;
  create table work.musteri_ozet as
  select m.musteri_no, count(s.siparis_no) as siparis_sayisi, sum(s.tutar) as ciro
  from dwh.musteri m left join dwh.siparis s on m.musteri_no = s.musteri_no
  where s.tarih between "&bas."d and "&bit."d
  group by m.musteri_no;
quit;

data work.egitim;
  set work.musteri_ozet;
  if siparis_sayisi = 0 then churn = 1; else churn = 0;
  log_ciro = log(ciro + 1);
run;

proc sort data=work.egitim; by musteri_no; run;

proc means data=work.egitim noprint;
  var ciro siparis_sayisi;
  output out=work.istatistik mean= std= / autoname;
run;

proc logistic data=work.egitim outmodel=cikti.churn_model;
  model churn(event='1') = log_ciro siparis_sayisi;
  output out=cikti.skorlar p=olasilik;
run;

data _null_;
  set work.istatistik;
  call symputx('ort_ciro', ciro_mean);
run;

%log_yaz(mesaj=Skorlama bitti &ort_ciro.);
x "gzip /sasdata/skor/cikti/skorlar.sas7bdat";
