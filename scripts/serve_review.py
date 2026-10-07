"""Serve a verified listening worksheet locally with working audio seeking."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from avevidence.review_server import serve_review

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('review_run')
parser.add_argument('--port', type=int, default=0)
args = parser.parse_args()
try:
    serve_review(args.review_run, args.port)
except KeyboardInterrupt:
    pass
