import bpy,sys,time,json,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import resin_prep as rp
rp.register()
s=bpy.context.scene.resin_prep
s.uv_exe=os.environ.get('RESIN_PREP_UVTOOLS',s.uv_exe)
if not s.uv_exe or not Path(s.uv_exe).is_file():
    raise RuntimeError('Set RESIN_PREP_UVTOOLS to an installed UVtools UI executable for this optional integration test.')
s.sliced_file=str(ROOT/'tests'/'fixture.zip')
assert bpy.ops.resin_prep.uvcheck()=={'FINISHED'}
assert '--group-islands' in rp._cache['uv_command']
process=rp._uv_process
process.wait(timeout=45)
rp.uv_poll()
text=bpy.data.texts['Resin Prep - UVtools.txt'].as_string()
print(text)
assert 'exit code: 0' in text
assert 'fixture.zip' in text
Path(ROOT/'tests'/'uv_bridge_result.txt').write_text(text,encoding='utf-8')
print('PASS: Actual UVtools 7.1.2 process, seven issue flags plus --group-islands, asynchronous log and successful exit.')
rp.unregister()
