import identity
import json,os,sys,time,random,urllib.request,urllib.parse,datetime
os.chdir(os.path.dirname(os.path.abspath(__file__)))
tok=os.environ.get('TELEGRAM_BOT_TOKEN')
if not tok:
    for l in open('.env'):
        if l.startswith('TELEGRAM_BOT_TOKEN='): tok=l.split('=',1)[1].strip().strip('"\'')
items=json.load(open(sys.argv[1])); start=int(sys.argv[2])
for i,(mid,text) in enumerate(items[start:]):
    if i>0 or start>0: time.sleep(60+random.randint(-4,4))
    try:
        data=urllib.parse.urlencode({'chat_id':identity.GROUP,'text':text,'reply_to_message_id':mid,'allow_sending_without_reply':'true'}).encode()
        r=json.load(urllib.request.urlopen('https://api.telegram.org/bot%s/sendMessage'%tok,data,timeout=30))
        ok=r.get('ok')
    except Exception as e: ok='ERR '+type(e).__name__
    with open('logs/replies.log','a') as f: f.write('%s sent reply to %s ok=%s\n'%(datetime.datetime.now().isoformat(),mid,ok))
    print(mid,ok)
