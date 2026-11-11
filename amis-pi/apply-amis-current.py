import pathlib, subprocess, urllib.request
body=subprocess.check_output(['casaos-cli','app-management','show','local','amis-pi','--yaml'])
base=pathlib.Path('/var/run/casaos/app-management.url').read_text().strip()
req=urllib.request.Request(base+'/v2/app_management/compose/amis-pi?check_port_conflict=false',body,{'Content-Type':'application/yaml'},method='PUT')
with urllib.request.urlopen(req,timeout=45) as response:
    print(response.status,response.read().decode())
