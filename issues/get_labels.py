import argparse
from issues import get_google_code_issues

parser = argparse.ArgumentParser(description='Get labels')
parser.add_argument('project')
parser.add_argument('-n', '--limit', dest='limit', type=int, default=-1)
parser.add_argument('-s', '--start', dest='start', type=int, default=1)
args = parser.parse_args()

LABELS = set()

for issue in get_google_code_issues(args.project, args.start, args.limit):
    for label in issue.labels:
        LABELS.add(label)


print '\n'.join(sorted(LABELS))
