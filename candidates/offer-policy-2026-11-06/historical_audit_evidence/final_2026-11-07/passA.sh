#!/bin/bash
export LC_ALL=C
W=/tmp/claude-0/-opt-autosparefinder/1bf54c6d-3b18-4254-9ed2-c0212016ebf4/scratchpad/w
nice -n 19 ionice -c3 zcat /opt/autosparefinder/backups/catalog/monthly/autospare-202605.sql.gz | nice -n 19 awk -F'\t' -v W=$W '
/^COPY public.price_history /{m=1;next}
/^COPY public.supplier_parts /{m=2;next}
/^COPY public.parts_catalog /{m=3;next}
/^\\\.$/{m=0;next}
m==1{print $1"\t"$2"\t"$10 > (W"/ph_old_raw.tsv")}
m==2{print $1"\t"$2"\t"$3"\t"$4"\t"$6"\t"$13"\t"$25 > (W"/sp_old.tsv")}
m==3{print $1"\t"$5"\t"$15"\t"$2"\t"$11 > (W"/pc_old.tsv")}
'
echo PASSA_DONE $(wc -l < $W/ph_old_raw.tsv) $(wc -l < $W/sp_old.tsv) $(wc -l < $W/pc_old.tsv)
