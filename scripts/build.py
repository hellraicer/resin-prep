"""Build a reproducible Blender extension ZIP using only the standard library."""
from pathlib import Path
import zipfile,hashlib,tomllib,argparse
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'dist');args=parser.parse_args()
    manifest=tomllib.loads((ROOT/'resin_prep'/'blender_manifest.toml').read_text(encoding='utf-8'))
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    package=output/f'resin-prep-{manifest["version"]}.zip'
    with zipfile.ZipFile(package,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for source in sorted((ROOT/'resin_prep').iterdir()):
            if not source.is_file() or not (source.suffix in {'.py','.toml','.html'} or source.name=='LICENSE'):continue
            info=zipfile.ZipInfo(source.name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16
            archive.writestr(info,source.read_bytes().replace(b'\r\n',b'\n'))
    digest=hashlib.sha256(package.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(digest+'  '+package.name+'\n',encoding='utf-8')
    print(package)
if __name__=='__main__':main()
