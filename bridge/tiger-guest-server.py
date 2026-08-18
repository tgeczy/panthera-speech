import BaseHTTPServer,SocketServer,urllib,os,thread
class H(BaseHTTPServer.BaseHTTPRequestHandler):
 def do_GET(s):
  d={}
  i=s.path.find('?')
  if i>=0:
   for kv in s.path[i+1:].split('&'):
    a=kv.split('=',1)
    if len(a)==2: d[a[0]]=urllib.unquote_plus(a[1])
  t=d.get('t','')
  for c in '"`$;&|<>[]\\':
   t=t.replace(c,' ')
  t=t.replace('~',' [[slnc 700]] ')
  r=d.get('r','180')
  v=d.get('v','Fred')
  f='/tmp/s%d.aiff'%thread.get_ident()
  os.system('rm -f '+f)
  os.system('say -v '+v+' -o '+f+' "[[rate '+r+']] '+t+'"')
  try:
   b=open(f,'rb').read()
  except:
   b=''
  os.system('rm -f '+f)
  s.send_response(200)
  s.send_header('Content-Type','audio/aiff')
  s.send_header('Content-Length',str(len(b)))
  s.end_headers()
  s.wfile.write(b)
 def log_message(s,*a):
  pass
class S(SocketServer.ThreadingMixIn,BaseHTTPServer.HTTPServer):
 daemon_threads=1
 allow_reuse_address=1
S(('',8000),H).serve_forever()
