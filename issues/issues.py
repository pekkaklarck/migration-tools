import argparse
import getpass
import csv
import urllib2
import re
import sys
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
import github3


GOOGLE_CODE_ISSUES = (
    'http://code.google.com/p/{project}/issues/csv?start={start}&num={num}'
    '&colspec=ID%20Status%20Type%20Priority%20Target%20Owner%20Summary&can=1')
ISSUE_URL = 'http://code.google.com/p/{project}/issues/detail?id={id}'
CLOSED_STATES = ['wontfix', 'done', 'invalid', 'duplicate', 'fixed']
TYPE_MAP = {'Defect': 'bug', 'Enhancement': 'enhancement', 'Task': 'task'}
KEPT_STATUSES = ['Pending', 'Invalid', 'Duplicate', 'WontFix']


SUBMITTER_MAPPER = None


class Issue(object):

    def __init__(self, project, id_, status, type_, priority, target, owner,
                 summary):
        self.id = int(id_)
        self.summary = summary
        self.open = status.lower() not in CLOSED_STATES
        self.labels = list(self._yield_labels(type_, priority, status))
        self.target = self._get_target(target)
        self.owner = SUBMITTER_MAPPER.map(owner) if SUBMITTER_MAPPER else owner
        self.description, self.comments = self._get_issue_details(project, id_)

    def _yield_labels(self, type, priority, status):
        if type in TYPE_MAP:
            yield TYPE_MAP[type]
        if priority:
            yield 'prio-' + priority.lower()
        if status in KEPT_STATUSES:
            yield status.lower()

    def _get_target(self, target):
        tokens = target.split('.')
        if len(tokens) > 1 and all(t.isdigit() for t in tokens):
            return target
        return ''

    def _get_issue_details(self, project, id_):
        opener = urllib2.build_opener()
        url = ISSUE_URL.format(project=project, id=id_)
        try:
            soup = BeautifulSoup(opener.open(url).read())
        except urllib2.HTTPError:
            return IssueText('Failed to get details from {}'.format(url)), []
        return (self._format_description(soup, url),
                self._format_comments(soup, url))

    def _format_description(self, details, url):
        text = self._text_content_of(
            details.select('div.issuedescription pre')[0])
        user = details.select('div.issuedescription a.userlink')[0].string
        date = details.select('div.issuedescription .date')[0].string
        return IssueText(text, user, date, url)

    def _format_comments(self, details, issue_url):
        for (idx, comment) in enumerate(details.select('div.issuecomment')):
            text = '\n'.join([self._text_content_of(part)
                              for part in comment.select('pre')])
            if '(No comment was entered for this change.)' in text:
                continue
            url = '{}#c{}'.format(issue_url, idx + 1)
            user = comment.find(class_='userlink').string
            date = comment.find(class_='date').string
            yield IssueText(text, user, date, url)

    def _text_content_of(self, element):
        replacements = [('<pre>', ''), ('</pre>', ''), ('<b>', '**'),
                        ('</b>', '**'), ('<br/>', '\n'), ('%', '&#37;')]
        text = element.prettify().strip()
        for orig, replacement in replacements:
            text = text.replace(orig, replacement)
        return text

    def __str__(self):
        tmpl = 'Id: {0}, Title: "{1}" Open: {2} Target: {3} Labels: {4}'
        return tmpl.format(self.id, self.summary, self.open, self.target,
                           self.labels)


class IssueText(object):

    def __init__(self, text, user='', date=None, url=None):
        self.text = text.replace('href="/p/robotframework',
                                 'href="https://code.google.com/p/robotframework')
        self.user = SUBMITTER_MAPPER.map(user) if SUBMITTER_MAPPER else user
        self.date = DateFormatter().format(date.strip()) if date else None
        self.url = url

    def __unicode__(self):
        if not self.user:
            return self.text
        return u"""\
> *Originally submitted to [Google Code]({url}) by {user} on {date}*

{text}
""".format(text=self.text, user=self.user, date=self.date, url=self.url)


class DummyIssue(object):

    def __init__(self, id_):
        self.id = id_
        self.summary = "Dummy issue"
        self.open = False
        self.labels = []
        self.target = ''
        self.owner = ''
        self.description = IssueText('Created in place of missing (most likely '
                                     'deleted) Google Code issue')
        self.comments = []


class SubmitterMapper(object):

    def __init__(self, path=None):
        self._map = {}
        if path:
            info('Reading submitter map %s' % path)
            self._read_map(path)
        else:
            info('No submitter map')

    def _read_map(self, path):
        with open(path) as map_file:
            for row in map_file:
                if not row or row.startswith('#'):
                    continue
                submitter, name = row.split('\t')[:2]
                self._map[submitter] = name

    def map(self, submitter):
        if submitter in self._map:
            return self._map[submitter]
        return submitter.split('@')[0].split('%')[0].strip()


class DateFormatter(object):
    _full_date = re.compile('(\w{3}) (\d+), (\d{4})')
    _moments_ago = re.compile('.* \(moments? ago\)')
    _minutes_ago = re.compile('.* \((\d+) minutes? ago\)')
    _hours_ago = re.compile('.* \((\d+) hours? ago\)')
    _days_ago = re.compile('\w{3} \d+ \((\d+) days? ago\)')
    _format = '{day} {month} {year}'.format

    def format(self, date):
        for matcher, formatter in [
            (self._full_date, self._full_date_formatter),
            (self._moments_ago, self._moments_ago_formatter),
            (self._minutes_ago, self._minutes_ago_formatter),
            (self._hours_ago, self._hours_ago_formatter),
            (self._days_ago, self._days_ago_formatter)
        ]:
            match = matcher.match(date)
            if match:
                return formatter(match)
        raise ValueError('Unknown date: %s' % date)

    def _full_date_formatter(self, match):
        month, day, year = match.groups()
        return self._format(**locals())

    def _moments_ago_formatter(self, match):
        return self._format_date_ago()

    def _minutes_ago_formatter(self, match):
        return self._format_date_ago(minutes=match.group(1))

    def _hours_ago_formatter(self, match):
        return self._format_date_ago(hours=match.group(1))

    def _days_ago_formatter(self, match):
        return self._format_date_ago(days=match.group(1))

    def _format_date_ago(self, days=0, hours=0, minutes=0):
        dt = datetime.now() - timedelta(days=int(days), hours=int(hours),
                                        minutes=int(minutes))
        month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][dt.month-1]
        return self._format(day=dt.day, month=month, year=dt.year)


def main(source_project, target_project, github_username, github_password,
         start_issue, issue_limit, id_sync, submitter_map=None):
    global SUBMITTER_MAPPER
    SUBMITTER_MAPPER = SubmitterMapper(submitter_map)
    gh, repo = access_github_repo(target_project, github_username, github_password)
    existing_issues = [i.number for i in repo.iter_issues(state='all')]
    sync_id = start_issue
    for issue in get_google_code_issues(source_project, start_issue, issue_limit):
        debug('Processing issue:\n{issue}'.format(issue=issue))
        milestone = get_milestone(repo, issue)
        if id_sync and issue.id in existing_issues:
            debug('Skipping already processed issue')
            sync_id += 1
            continue
        while id_sync and issue.id > sync_id:
            # Insert placeholder issues for missing/deleted GCode issues
            insert_issue(repo, DummyIssue(sync_id), milestone=None)
            sync_id += 1
        insert_issue(repo, issue, milestone)
        sync_id += 1
        if api_call_limit_reached(gh):
            break


def access_github_repo(target_project, username, password=None):
    if not password:
        prompt = 'GitHub password for {user}: '.format(user=username)
        password = getpass.getpass(prompt)
    gh = github3.login(username, password=password)
    repo_owner, repo_name = target_project.split('/')
    return gh, gh.repository(repo_owner, repo_name)


def get_google_code_issues(project, start=1, issue_limit=-1):
    limit_issues = issue_limit > 0
    issues = []
    num = 100
    while True:
        if limit_issues:
            if issue_limit <= 0:
                return issues
            num = min(issue_limit, 100)
            issue_limit -= 100
        url = GOOGLE_CODE_ISSUES.format(project=project, start=start-1, num=num)
        debug('Fetching issues from {url}'.format(url=url))
        reader = csv.reader(urllib2.urlopen(url))
        paginated = False
        for row in reader:
            if reader.line_num == 1 or not row:
                continue
            if 'truncated' in row[0]:
                start += 100
                paginated = True
            else:
                issues.append(Issue(project, *row[:7]))
        if not paginated:
            debug('Read {num} issues from Google Code'.format(num=len(issues)))
            return issues


def get_milestone(repo, issue):
    if not issue.target:
        return None
    existing_milestones = list(repo.iter_milestones())
    milestone = [m for m in existing_milestones if m.title == issue.target]
    if milestone:
        return milestone[0].number
    return repo.create_milestone(issue.target).number


def api_call_limit_reached(gh):
    remaining = gh.ratelimit_remaining
    debug('Remaining API calls: {rem}'.format(rem=remaining))
    if remaining < 50:
        debug('API calls consumed, wait for an hour')
        return True
    return False


def debug(msg):
    print >> sys.stderr, '[ debug ]', msg

def error(msg):
    print >> sys.stderr, '[ ERROR ]', msg

def info(msg):
    print >> sys.stderr, '[ INFO  ]', msg


def insert_issue(repo, issue, milestone):
    github_issue = repo.create_issue(
        issue.summary, unicode(issue.description), labels=issue.labels,
        milestone=milestone)
    for comment in issue.comments:
        github_issue.create_comment(unicode(comment))
    if not issue.open:
        github_issue.close()
    if issue.owner.startswith('@'):
        try:
            github_issue.assign(issue.owner[1:])
        except github3.models.GitHubError:
            error("Failed to assign '%s' as owner for issue %s."
                  % (issue.owner[1:], issue.id))

    debug('Created issue {url}'.format(url=github_issue.html_url))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Migrate issues from Google Code to GitHub')
    parser.add_argument('source_project')
    parser.add_argument('target_project')
    parser.add_argument('github_username')
    parser.add_argument('github_password', nargs='?', default=None)
    parser.add_argument('-n', '--limit', dest='limit', type=int, default=-1)
    parser.add_argument('-s', '--start', dest='start', type=int, default=1)
    parser.add_argument('-m', '--submitter-map', dest='submitter_map')
    parser.add_argument('--no-id-sync', action='store_true')
    args = parser.parse_args()

    main(args.source_project, args.target_project, args.github_username,
         args.github_password, args.start, args.limit, not args.no_id_sync,
         args.submitter_map)
