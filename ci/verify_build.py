"""Allow promotion only from a successful trusted build of this repository."""
import json
import os
import sys
from pathlib import Path

def verify(run, repository):
    if run.get('conclusion') != 'success' or run.get('status') != 'completed':
        raise ValueError('Build did not complete successfully')
    if run.get('head_branch') != 'main' or run.get('path') != '.github/workflows/ci.yml':
        raise ValueError('Expected main build workflow')
    if run.get('event') not in ('push', 'workflow_dispatch'):
        raise ValueError('Untrusted build event')
    if run.get('repository', {}).get('full_name') != repository:
        raise ValueError('Wrong source repository')
    return run['head_sha']

if __name__ == '__main__':
    verify(json.loads(Path(sys.argv[1]).read_text()), os.environ['GITHUB_REPOSITORY'])
