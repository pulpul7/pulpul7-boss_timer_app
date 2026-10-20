"""Private worker refresh signal; public clients only read a generation token."""
from pathlib import Path
import re
import time
import uuid

from web_service_common import diagnostic, read_json, worker_paths


def refresh_request_path(root, run_id):
    worker_paths(Path(root), run_id)
    return Path(root) / ('page_refresh_' + run_id + '.json')


class PageRefresh:
    def __init__(self, root, run_id, logger):
        self.path = refresh_request_path(root, run_id)
        self.run_id, self.logger = run_id, logger
        self.token = uuid.uuid4().hex
        self.signature = None
        self.job_id = ''

    def status(self):
        return dict(web_refresh_protocol=1, web_refresh_token=self.token,
                    web_refresh_job=self.job_id)

    def poll(self):
        try:
            info = self.path.stat()
            signature = (info.st_mtime_ns, info.st_size, info.st_ino)
            if signature == self.signature:
                return False
            self.signature = signature
            request = read_json(self.path)
            if (request.get('schema') != 1 or request.get('run_id') != self.run_id
                    or re.fullmatch('[0-9a-f]{32}', str(request.get('id', ''))) is None
                    or type(request.get('until')) not in {int, float}
                    or not time.time() < request['until'] <= time.time() + 30):
                raise ValueError('invalid refresh signal')
            if request['id'] == self.job_id:
                return False
            self.job_id = request['id']
            self.token = request['id']
            diagnostic(self.logger, 'web_browser_refresh_requested', request=self.job_id)
            return True
        except FileNotFoundError:
            return False
        except (OSError, ValueError, TypeError):
            diagnostic(self.logger, 'web_browser_refresh_rejected')
            return False


def browser_refresh_state(access, snapshot):
    refresh = getattr(access, 'page_refresh', None)
    return dict(web_refresh_token=refresh.token if refresh else '',
                web_page_version=snapshot.version)


def stamp_refresh_page(page, state):
    """Only trusted hex tokens enter markup, including the first status poll."""
    attributes = b''
    for field in ('web_refresh_token', 'web_page_version'):
        value = state.get(field, '')
        if re.fullmatch('[0-9a-f]{32}|[0-9a-f]{64}', value or ''):
            attributes += b' data-' + field.replace('_', '-').encode() + b'="' + value.encode() + b'"'
    return re.sub(rb'<html\b', lambda match: match.group() + attributes, page, count=1, flags=re.I)
