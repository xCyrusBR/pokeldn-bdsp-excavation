"""Build the source-only PokéLDN package from an explicit allowlist."""
import hashlib
from pathlib import Path
import tomllib
import zipfile
ROOT = Path(__file__).resolve().parent
PUBLIC_FILES = ('plugin.toml','plugin.py','service.py','README.md','bdsp_connect.py',
                'bdsp_dig_pair.py','bdsp_dig_pair_sweep.py','LICENSE')
def main():
    version = tomllib.loads((ROOT/'plugin.toml').read_text(encoding='utf-8'))['version']
    target = ROOT/'dist'/f'BDSP-Excavation-{version}.pokeplugin'
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in PUBLIC_FILES: archive.write(ROOT/name,name)
    target.with_suffix('.pokeplugin.sha256').write_text(
        hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n',encoding='ascii')
    print(target)
    return target
if __name__ == '__main__': main()
