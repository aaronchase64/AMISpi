import pathlib
import urllib.request
import urllib.error

base = pathlib.Path('/var/run/casaos/app-management.url').read_text().strip()
body = pathlib.Path('/home/kristipi/amis-pi/docker-compose.casaos-managed.yml').read_bytes()
for dry in (True, False):
    url = base + '/v2/app_management/compose?check_port_conflict=false&uncontrolled=true'
    if dry:
        url += '&dry_run=true'
    request = urllib.request.Request(url, body, {'Content-Type': 'application/yaml'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            print('dry-run' if dry else 'import', response.status, response.read().decode())
    except urllib.error.HTTPError as error:
        print(error.code, error.read().decode())
        raise SystemExit(1)
