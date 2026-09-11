"""Hold the existing runtime barrier during this API replacement only."""
import time
from pathlib import Path
from revguard.api import store,gateway
from revguard.runtime_barrier import acquire_runtime_lease,assert_recording_quiescent
with acquire_runtime_lease(store,exclusive=True):
    assert_recording_quiescent(store,gateway.journal)
    Path('/tmp/revguard-ui-0.5.8.ready').write_text('ready\n')
    time.sleep(90)
