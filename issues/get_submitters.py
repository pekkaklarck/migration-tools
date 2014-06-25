import argparse
from issues import get_google_code_issues

parser = argparse.ArgumentParser(
    description='Get issue submitters and commenters from Google Code')
parser.add_argument('project')
parser.add_argument('-n', '--limit', dest='limit', type=int, default=-1)
parser.add_argument('-s', '--start', dest='start', type=int, default=1)
args = parser.parse_args()

SUBMITTERS = {}

def add(user, id):
    if user:
        SUBMITTERS.setdefault(user, set()).add(id)


for issue in get_google_code_issues(args.project, args.start, args.limit):
    add(issue.owner, issue.id)
    add(issue.description.user, issue.id)
    for comment in issue.comments:
        add(comment.user, issue.id)


print '# User\tIssues'
for user in sorted(SUBMITTERS):
    issues = sorted(SUBMITTERS[user])
    print user, '\t', ', '.join(str(id) for id in issues)
