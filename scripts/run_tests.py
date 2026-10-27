"""Run isolated Blender suites; never save preferences in a user's profile."""
from pathlib import Path
import argparse,subprocess,os,json,sys
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--blender',required=True);parser.add_argument('--uvtools');args=parser.parse_args()
    reports=ROOT/'reports';reports.mkdir(exist_ok=True)
    env=os.environ.copy();profile=ROOT/'.test-profile'
    for variable,directory in [('BLENDER_USER_CONFIG','config'),('BLENDER_USER_EXTENSIONS','extensions')]:
        path=profile/directory;path.mkdir(parents=True,exist_ok=True);env[variable]=str(path)
    if args.uvtools:env['RESIN_PREP_UVTOOLS']=str(Path(args.uvtools).resolve())
    subprocess.run([sys.executable,str(ROOT/'scripts'/'build.py')],check=True,cwd=ROOT)
    suites=['blender_tests.py','update_tests.py','repair_tests.py','package_install_test.py']
    if args.uvtools:
        subprocess.run([sys.executable,str(ROOT/'tests'/'create_png.py')],check=True,cwd=ROOT)
        suites.append('uv_bridge_test.py')
    for suite in suites:
        command=[args.blender,'--background','--factory-startup','--python-exit-code','1','--python',str(ROOT/'tests'/suite)]
        result=subprocess.run(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
        (reports/(Path(suite).stem+'.log')).write_text(result.stdout,encoding='utf-8')
        print(result.stdout,flush=True)
        if result.returncode:raise SystemExit(result.returncode)
    results=[json.loads((ROOT/'tests'/name).read_text(encoding='utf-8')) for name in ['test_results.json','update_results.json','repair_results.json']]
    summary={'assertions':sum(r['count'] for r in results),'blender':results[0]['blender'],'suites':suites,'uvtools_explicitly_requested':bool(args.uvtools),'github_workflow_executed':bool(os.environ.get('GITHUB_ACTIONS'))}
    (reports/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
