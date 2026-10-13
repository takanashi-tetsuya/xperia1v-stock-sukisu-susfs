#!/usr/bin/env python3
"""Run only actual AK3 level and firmware checks against host property fixtures."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def main():
    ak = (ROOT / 'sources/AnyKernel3/anykernel.sh').read_text()
    backend = (ROOT / 'sources/AnyKernel3/META-INF/com/google/android/update-binary').read_text()
    functions = '\n'.join(re.search(r'^'+name+r'\(\) \{.*?^\}', backend, re.M|re.S).group(0)
                          for name in ('do_levelcheck','do_vendorlevelcheck'))
    results = []
    with tempfile.TemporaryDirectory(prefix='ak3-levels-') as temp:
        t = Path(temp)
        (t / 'anykernel.sh').write_text(ak)
        prefix = '''file_getprop() {
 case "$1" in /system/build.prop) set -- system.prop "$2";; /vendor/build.prop) set -- vendor.prop "$2";; esac
 grep "^$2=" "$1" | tail -n1 | cut -d= -f2-
}
ui_print() { :; }
abort() { exit 42; }
'''
        for observed in ('2026-05-01','2026-06-01','2026-06-30','2026-07-01'):
            (t/'system.prop').write_text('ro.build.version.security_patch='+observed+'\n')
            (t/'vendor.prop').write_text('ro.vendor.build.security_patch='+observed+'\n')
            for fn in ('do_levelcheck','do_vendorlevelcheck'):
                for shell in (['bash'],['busybox','ash']):
                    r = subprocess.run(shell+['-c',prefix+functions+'\n'+fn],cwd=t)
                    wanted = 0 if observed.startswith('2026-06') else 42
                    if r.returncode != wanted: raise RuntimeError((observed,fn,shell,r.returncode))
                    results.append({'observed':observed,'function':fn,'shell':shell,'returncode':r.returncode})
        guard = ak.split('# FIRMWARE_GUARD_BEGIN')[1].split('# FIRMWARE_GUARD_END')[0]
        for fileval,propval,expected in [('67.2.A.3.178','recovery',0),('','67.2.A.3.178',0),('','recovery',42),('','',42)]:
            script = '''file_getprop() { echo "$MOCK_FILE"; }
getprop() { echo "$MOCK_PROP"; }
abort() { exit 42; }
'''+guard
            r = subprocess.run(['busybox','ash','-c',script],env={**os.environ,'MOCK_FILE':fileval,'MOCK_PROP':propval})
            if r.returncode != expected: raise RuntimeError((fileval,propval,r.returncode))
            results.append({'file_firmware':fileval,'fallback_firmware':propval,'returncode':r.returncode})
    report = {'status':'PASS','tests':len(results),'anykernel_sha256':hashlib.sha256(ak.encode()).hexdigest(),
              'scope':'Actual extracted installer functions; no installer entrypoint, mounting or flashing',
              'limits':'Upstream level check alone can accept missing property; exact firmware guard remains required',
              'results':results}
    (ROOT/'logs').mkdir(exist_ok=True)
    (ROOT/'logs/anykernel_host_test.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'PASS: {len(results)} AK3 property cases; installer not run')

if __name__ == '__main__':
    main()
