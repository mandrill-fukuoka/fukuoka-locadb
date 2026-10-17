import csv,re,collections,json
f='/root/.claude/uploads/1ea319c9-42e6-57d2-9791-4f4417ac35fc/102d2384-wiki_candidates_review.tsv'
rows=[r for r in list(csv.reader(open(f,encoding='utf-8'),delimiter='\t',quotechar=None))[1:] if r[6]=='採']
src=open('knowledge_location.php',encoding='utf-8').read()
exist={int(m.group(1)):m.group(2) for m in re.finditer(r"^(\d+) => \['(.*)'\],",src,re.M)}
NOUSE={6963,6969,8400,7016,7076,7129,7481,7820,9337,9315,6982}
LOCDROP={8394}
READ=re.compile(r'（[^（）]*(?:[ぁ-ん]{3,}|英語?[:：]|:)[^（）]*）')
DROP=re.compile(r'賑わ|混雑|管区駅|業務委託駅|について記述')
ADDR=re.compile(r'(?:九州最大の繁華街である)?(?:福岡県)?福岡市(?:東|博多|中央|南|西|城南|早良)区[^にで、。]*(?:に本部を置く|に駅事務所を置く|に所在する|に位置する|にあった|にある)')
def sr(s): return re.sub(r'\s+',' ',READ.sub('',s)).strip().strip('"').strip()
res=collections.defaultdict(list)
names={}
for sid,name,no,kind,s,url,mk,note,cov,how in rows:
    sid=int(sid); names[sid]=name
    if sid in NOUSE or DROP.search(s): continue
    s=sr(s)
    if kind=='location':
        if sid in LOCDROP or sid in exist: continue
        subj,sep,tail=s.partition('は、')
        if not sep: subj,sep,tail=s.partition('は')
        res[sid].insert(0,ADDR.sub('',tail,count=1).lstrip('のでに、 '))
    else: res[sid].append(s)
target=sorted(set(int(r[0]) for r in rows)-NOUSE)
out=['<?php','// Wikipedia由来の承認済み材料（2026-10-17、人が承認。読み仮名・住所は除去済み）。地点ID => [材料...]','return [']
for sid in target:
    L=([exist[sid]] if sid in exist else [])+res.get(sid,[])
    L=[l for l in L if len(l)>=5][:6]
    if not L: continue
    out.append("%d => [%s],  // %s" % (sid, ', '.join("'"+l.replace("\\","\\\\").replace("'","\\'")+"'" for l in L), names.get(sid,'')))
out.append('];')
open('knowledge_wiki.php','w',encoding='utf-8').write('\n'.join(out)+'\n')
print(len(target))
