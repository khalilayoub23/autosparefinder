#!/bin/bash
# usage: classify.sh old new   (names of snap/<name>.tsv) ; streaming only, LC_ALL=C, one job
export LC_ALL=C
cd /tmp/claude-0/-opt-autosparefinder/1bf54c6d-3b18-4254-9ed2-c0212016ebf4/scratchpad
old=$1; new=$2; O=pairs/${old}__${new}
avail=$(free -m | awk 'NR==2{print $7}'); [ "$avail" -lt 2500 ] && { echo "LOW MEM $avail"; exit 9; }
cut -f1 snap/$old.tsv > $O.old.ids
cut -f1 snap/$new.tsv > $O.new.ids
nice -n 19 comm -23 $O.old.ids $O.new.ids > $O.missing.ids
nice -n 19 comm -13 $O.old.ids $O.new.ids | wc -l > $O.newonly.count
nice -n 19 join -t$'\t' $O.missing.ids snap/$old.tsv > $O.missing.rows
rm -f $O.old.ids $O.new.ids
if [ ! -s $O.missing.rows ]; then echo "$old -> $new missing=0 newonly=$(cat $O.newonly.count)"; exit 0; fi
nice -n 19 awk -F'\t' -v OFS='\t' -v out=$O '
NR==FNR { m++; id[m]=$1; sup[m]=$2; part[m]=$3; sku[m]=$4; typ[m]=$7; avl[m]=$6; pr[m]=$5
          ks[$2 SUBSEP $4]=1; kp[$2 SUBSEP $3]=1; kq[$3 SUBSEP $4]=$2; next }
{ a=($2 SUBSEP $4); if (a in ks) skuPart[a]=$3
  b=($2 SUBSEP $3); if (b in kp) { spSku[b]=$4; spTyp[b]=$7 }
  c=($3 SUBSEP $4); if (c in kq && $2!=kq[c]) otherSup[c]=$2 }
END { for(i=1;i<=m;i++){ a=sup[i] SUBSEP sku[i]; b=sup[i] SUBSEP part[i]; c=part[i] SUBSEP sku[i]
   if (a in skuPart) cl=(skuPart[a]==part[i])?"REKEYED_same_supplier_sku_part":"REMAPPED_same_supplier_sku_other_part"
   else if (b in spSku) cl=(spTyp[b]!=typ[i])?"SAME_SUPPLIER_PART_other_sku_type_differs":"SAME_SUPPLIER_PART_other_sku_same_type"
   else if (c in otherSup) cl="SUPPLIER_CHANGED_same_part_sku_other_supplier"
   else cl="GENUINELY_ABSENT"
   cnt[cl SUBSEP sup[i]]++; tot[cl]++; if (ex[cl]<2){ex[cl]++; print "EX",cl,id[i],sup[i],part[i],sku[i],pr[i],avl[i],typ[i] > (out ".examples")}
   print id[i],cl > (out ".classified") }
   for (k in cnt) { split(k,q,SUBSEP); print q[1],q[2],cnt[k] > (out ".bysupplier") }
   for (k in tot) print k,tot[k] > (out ".classes") }' $O.missing.rows snap/$new.tsv
echo "$old -> $new missing=$(wc -l < $O.missing.ids) newonly=$(cat $O.newonly.count)"; cat $O.classes
