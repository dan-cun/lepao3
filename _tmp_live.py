import sys, time, threading
sys.path.insert(0, '.')
sys.path.insert(0, 'ui')
import run_core as RC
from lepao.accounts import AccountRegistry


def log(level, msg):
    print('[%s][%s] %s' % (time.strftime('%H:%M:%S'), level, msg), flush=True)


reg = AccountRegistry(RC.CONFIG_PATH)
for k, m in reg.accounts.items():
    key = k
    print('member:', k, m.get('name'), 'token=', str(m.get('token'))[:8], flush=True)
st = threading.Event()
out = RC._submit_run(key, log, st)
print('=== final:', repr(out), flush=True)
