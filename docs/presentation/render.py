"""Render the recorded source-coverage boundary. Standard library only."""
from pathlib import Path
import json,html,hashlib
P=Path(__file__).resolve().parent
j=json.loads((P/'journey.json').read_text());old=j['before']['revenue_14d'];new=j['after']['revenue_14d'];entity=j['selected_entity'];r1=old['coverage']['revision'];r2=new['coverage']['revision'];v1=old['predictions'][entity];v2=new['predictions'][entity]
assert r1==1 and r2==2 and j['edit_receipt']==2 and all(a['coverage']['state']=='stale' for a in j['explicit_older'].values())
def render(mobile):
 name='coverage-mobile' if mobile else 'coverage';w,h=(440,1160) if mobile else (1200,740);out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="{name}-title {name}-desc" font-family="Arial"><title id="{name}-title">AHEADDB source coverage across an order</title><desc id="{name}-desc">A synthetic order advances source revision one to two while current reads refuse the older generation until the worker publishes revision two.</desc><rect width="100%" height="100%" fill="#f4f0e8"/>']
 def text(x,y,s,size=20,color='#292d2a',weight=400):out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}">{html.escape(str(s))}</text>')
 def line(x1,y1,x2,y2,dash=''):out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#a29e92" stroke-width="1.5" stroke-dasharray="{dash}"/>')
 text(32,40,'AHEADDB / A RECORDED SYNTHETIC JOURNEY',12 if mobile else 16,'#65685d',700)
 text(32,96,'Committed is not covered.',29 if mobile else 48,weight=700)
 text(32,132,'The worker must earn the next source revision.',16 if mobile else 23,'#65685d')
 phases=[('01 / Current',r1,r1,f'{v1:.1f}','Accepted current read'),('02 / Order committed',r2,r1,f'{v1:.1f}','Current read refused'),('03 / Worker recovered',r2,r2,f'{v2:.1f}','Accepted current read')]
 for i,(phase,source,coverage,value,status) in enumerate(phases):
  x,y=(32,205+i*270) if mobile else (32+i*390,225)
  text(x,y,phase,22,weight=700);line(x,y+22,x+(376 if mobile else 354),y+22)
  text(x,y+63,'Source head',16,'#65685d');text(x+230,y+63,f'rev {source}',24,weight=700)
  text(x,y+103,'Generation covers',16,'#65685d');text(x+230,y+103,f'rev {coverage}',24,'#a54228' if source!=coverage else '#292d2a',700)
  text(x,y+153,status,21,'#a54228' if source!=coverage else '#292d2a',700)
  text(x,y+187,('Older opt-in: '+value) if source!=coverage else ('C0000 revenue: '+value),20)
  text(x,y+219,'450-unit order; worker pending' if i==1 else 'Same task, model and July 15 cutoff',14 if mobile else 16,'#65685d')
 footer=1065 if mobile else 568;line(32,footer-30,w-32,footer-30)
 text(32,footer,'Freshness is source coverage,',20 if mobile else 28,weight=700)
 text(32,footer+32,'not the timestamp on an older result.',20 if mobile else 28,weight=700)
 text(32,h-28,'Synthetic shop units · persistence model · not forecast validation',11 if mobile else 16,'#65685d')
 out.append('</svg>');svg='\n'.join(out)+'\n';(P/(name+'.svg')).write_text(svg);(P/(name+'.html')).write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AHEADDB coverage</title><style>body{margin:0}.diagram-container{overflow-x:auto}svg{display:block;min-width:'+str(w)+'px}</style><div class="diagram-container">'+svg+'</div></html>')
for mobile in [False,True]:render(mobile)
files=['journey.json','synthetic-backup.json','synthetic-purges.jsonl','render.py','coverage.svg','coverage-mobile.svg','coverage.html','coverage-mobile.html']
(P/'figure-manifest.json').write_text(json.dumps({'source_revision':j['source_revision'],'meaning':'Recorded synthetic integration behavior; no predictive quality or physical claim. Values are synthetic shop units, fixed2024-07-15 information cutoff.','files':{n:{'bytes':(P/n).stat().st_size,'sha256':hashlib.sha256((P/n).read_bytes()).hexdigest()} for n in files}},indent=2)+'\n')
