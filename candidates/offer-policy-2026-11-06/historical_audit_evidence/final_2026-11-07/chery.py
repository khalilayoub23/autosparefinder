import asyncio, os, asyncpg, re, collections
async def main():
    rows=[l.rstrip('\n').split('\t') for l in open('/tmp/absent6307.tsv')]
    c=await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://'))
    await c.execute("SET statement_timeout='200s'"); await c.execute("SET default_transaction_read_only=on")
    parts=list({r[2] for r in rows})
    st={str(x['id']):x for x in await c.fetch("select id,is_active,specifications->>'dedup_merged_into' mi from parts_catalog where id=any($1::uuid[])",parts)}
    cat=collections.Counter()
    ex={}
    for r in rows:
        mf=r[6]; s=st.get(r[2])
        k=('part_gone' if s is None else 'part_soft_merged' if s['mi'] else 'part_exists')
        key=(mf if mf else '?',r[1][:8],k)
        cat[key]+=1; ex.setdefault(key,(r[0][:8],r[3],r[7]))
    for k,v in sorted(cat.items(),key=lambda x:-x[1])[:14]: print(v,k,ex[k])
    # Chery: equivalent live part with offers?
    ch=[r for r in rows if r[6].lower()=='chery' and st.get(r[2]) is None]
    print('chery hard-deleted offers',len(ch),'suppliers',collections.Counter(r[1][:8] for r in ch))
    norm=lambda s: re.sub(r'[^A-Z0-9]','',(s or '').upper())
    oems=list({norm(r[7]) for r in ch if norm(r[7])})
    live=await c.fetch("select regexp_replace(upper(pc.oem_number),'[^A-Z0-9]','','g') n, count(distinct pc.id) parts, count(sp.id) offers, count(sp.id) filter (where sp.is_available) avail from parts_catalog pc left join supplier_parts sp on sp.part_id=pc.id where lower(pc.manufacturer)='chery' and regexp_replace(upper(pc.oem_number),'[^A-Z0-9]','','g')=any($1::text[]) group by 1",oems)
    have={x['n']:x for x in live}
    print('chery distinct normalized OEMs',len(oems),'| live Chery part with same OEM exists:',len(have),'| with >=1 offer:',sum(1 for x in live if x['offers']>0),'| with >=1 available offer:',sum(1 for x in live if x['avail']>0))
    rep=sum(1 for r in ch if norm(r[7]) in have); repo=sum(1 for r in ch if norm(r[7]) in have and have[norm(r[7])]['offers']>0)
    print('chery lost offers whose OEM has a live part:',rep,'| ...with offers:',repo)
    print('lost chery offers priced>0 / available in May dump:',sum(1 for r in ch if r[4] not in ('\\N','0.00','0')),sum(1 for r in ch if r[5]=='t'))
    await c.close()
asyncio.run(main())
