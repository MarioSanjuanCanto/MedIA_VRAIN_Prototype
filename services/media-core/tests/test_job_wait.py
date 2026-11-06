import threading
import time

from media_core.tools import jobs


def test_wait_returns_as_soon_as_job_finishes():
    gate = threading.Event()
    def work():
        gate.wait(2)
        return {'status': 'success', 'subtitled_video_file': 'done.mp4'}
    submitted = jobs._submit(work, {})
    timer = threading.Timer(.08, gate.set)
    timer.start()
    try:
        started = time.monotonic()
        result = jobs.wait_for_media_result(submitted, wait_seconds=1)
        assert result['subtitled_video_file'] == 'done.mp4'
        assert time.monotonic() - started < .8
    finally:
        gate.set(); timer.join()


def test_wait_is_bounded_and_pending_result_forbids_shell_sleep():
    gate = threading.Event()
    def work():
        gate.wait(2)
        return {'status': 'success'}
    submitted = jobs._submit(work, {})
    try:
        started = time.monotonic()
        pending = jobs.wait_for_media_result(submitted, wait_seconds=.04)
        assert pending['job_id'] == submitted['job_id']
        assert pending['wait_seconds'] == 25
        assert 'Never use terminal or sleep' in pending['next_action']
        assert time.monotonic() - started < .8
    finally:
        gate.set()
        assert jobs.media_job_status(submitted['job_id'], wait_seconds=1)['status'] == 'completed'


def test_wait_propagates_failure_without_polling_again():
    submitted = jobs._submit(lambda: {'status': 'error', 'message': 'Render failed'}, {})
    assert jobs.wait_for_media_result(submitted, 1) == {'status': 'error', 'message': 'Render failed'}
    assert jobs.media_job_status('missing', wait_seconds=26)['status'] == 'error'
