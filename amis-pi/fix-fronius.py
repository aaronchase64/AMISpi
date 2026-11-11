import pathlib,subprocess,urllib.request
def run(*args):return subprocess.check_output(args,text=True)
code='''from amis_pi import config
import shutil,time,os
p='/config/config.toml';b=p+'.before-modbus-'+time.strftime('%Y%m%d-%H%M%S')
shutil.copy2(p,b);os.chmod(b,0o600)
c=config.load(p,{});c['modbus']['host']='0.0.0.0';config.save(p,c)
print('Modbus bind address corrected; configuration backup created')
'''
print(run('docker','exec','amis-pi','python','-c',code))
body=run('casaos-cli','app-management','show','local','amis-pi','--yaml')
assert 'published: "502"' not in body
body=body.replace('        ports:\n','        ports:\n            - target: 502\n              published: "502"\n              protocol: tcp\n',1)
assert 'published: "502"' in body
pathlib.Path('/mnt/USB/amis-pi/docker-compose.casaos-managed.yml').write_text(body)
base=pathlib.Path('/var/run/casaos/app-management.url').read_text().strip()
req=urllib.request.Request(base+'/v2/app_management/compose/amis-pi?check_port_conflict=false',body.encode(),{'Content-Type':'application/yaml'},method='PUT')
with urllib.request.urlopen(req,timeout=45) as r:print(r.status,r.read().decode())
