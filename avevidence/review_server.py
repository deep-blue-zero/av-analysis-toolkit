"""Loopback-only static review server with byte ranges for accurate media seeking."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re

from .common import AVError
from .inventory import verify_run


class ReviewHandler(SimpleHTTPRequestHandler):
    def list_directory(self, path):
        self.send_error(403, 'Open review.html directly')
        return None

    def send_head(self):
        self._range_remaining = None
        path = Path(self.translate_path(self.path))
        base = Path(self.directory).resolve()
        if not path.resolve().is_relative_to(base) or path.is_symlink():
            self.send_error(403)
            return None
        requested = self.headers.get('Range')
        if not requested or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested.strip())
        try:
            if not match or not any(match.groups()):
                raise ValueError()
            left, right = match.groups()
            if left:
                start, end = int(left), min(int(right), size-1) if right else size-1
            else:
                count = int(right)
                if count <= 0:
                    raise ValueError()
                start, end = max(0, size-count), size-1
            if not 0 <= start <= end < size:
                raise ValueError()
        except ValueError:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None
        stream = path.open('rb')
        stream.seek(start)
        self._range_remaining = end-start+1
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(self._range_remaining))
        self.end_headers()
        return stream

    def end_headers(self):
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def copyfile(self, source, outputfile):
        if self._range_remaining is None:
            return super().copyfile(source, outputfile)
        remaining = self._range_remaining
        while remaining:
            block = source.read(min(remaining, 65536))
            if not block:
                break
            outputfile.write(block)
            remaining -= len(block)


def serve_review(review_run, port=0):
    folder = Path(review_run).resolve()
    verify_run(folder)
    if not (folder/'review.html').is_file():
        raise AVError('Expected a listening review bundle')
    if type(port) is not int or not 0 <= port <= 65535:
        raise AVError('Port must be 0 to 65535')
    with ThreadingHTTPServer(('127.0.0.1', port), partial(ReviewHandler, directory=str(folder))) as server:
        print(f'Local review: http://127.0.0.1:{server.server_port}/review.html', flush=True)
        print('This serves only the review folder on this computer. Press Ctrl+C to stop.', flush=True)
        server.serve_forever()
