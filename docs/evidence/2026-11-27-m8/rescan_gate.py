import json,glob,sys
sys.path.insert(0,'/app')
from agents import owner_console as c
n=0;flag=0
for f in sorted(glob.glob('/e/replay_round*/*.json'))+sorted(glob.glob('/h/convo_eval_*.json')):
    for k,turns in json.load(open(f)).items():
        for t in turns:
            for field in ('reply','first_draft'):
                txt=t.get(field) or ''
                if not txt: continue
                n+=1
                for s,r in c.unbacked_action_sentences(txt):
                    flag+=1; print(f.split('/')[-2][-6:]+'/'+f.split('/')[-1][:-5][-12:], field[:5], r, '|', s[:120].replace('\n',' '))
print('texts',n,'flagged sentences',flag)
