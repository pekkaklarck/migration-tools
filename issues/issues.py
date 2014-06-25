import argparse
import getpass
import csv
import urllib2
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup
from github3 import login


GOOGLE_CODE_ISSUES = (
    'http://code.google.com/p/{project}/issues/csv?start={start}&num={num}'
    '&colspec=ID%20Status%20Type%20Priority%20Target%20Summary&can=1')
ISSUE_URL = 'http://code.google.com/p/{project}/issues/detail?id={id}'
ISSUE_TEXT = u"""{text}
<hr>
<a href="{url}">Originally submitted</a> by <code>{user}</code> on {date}.
"""
CLOSED_STATES = ['wontfix', 'done', 'invalid', 'fixed']


class IssueTransfomer(object):

    def __init__(self, project, id_, status, type_, priority, target, summary):
        self.id = int(id_)
        self.summary = summary
        self.open = status.lower() not in CLOSED_STATES
        self.labels = self._get_labels(type_, priority, status)
        self.target = target
        self.body, self.comments = self._get_issue_details(project, id_)

    def _get_labels(self, type_, priority, status):
        labels = []
        if type_:
            labels.append(type_)
        if priority:
            labels.append('Prio-' + priority)
        if status:
            labels.append(status)
        return labels

    def _get_issue_details(self, project, id_):
        opener = urllib2.build_opener()
        url = ISSUE_URL.format(project=project, id=id_)
        try:
            soup = BeautifulSoup(opener.open(url).read())
        except urllib2.HTTPError:
            return 'Failed to get issue details from {}'.format(url), []
        return self._format_body(soup, url), self._format_comments(soup, url)

    def _format_body(self, details, url):
        text = self._text_content_of(
            details.select('div.issuedescription pre')[0])
        user = details.select('a.userlink')[0].string
        date = self._parse_date(details.select('div.issuedescription .date')[0])
        return ISSUE_TEXT.format(text=text, user=user, date=date, url=url)

    def _parse_date(self, element):
        return DateFormatter().format(element.string.strip())

    def _format_comments(self, details, issue_url):
        for (idx, comment) in enumerate(details.select('div.issuecomment')):
            body = '\n'.join([self._text_content_of(part)
                              for part in comment.select('pre')])
            if '(No comment was entered for this change.)' in body:
                continue
            url = '{}#c{}'.format(issue_url, idx + 1)
            user = comment.find(class_='userlink').string
            date = self._parse_date(comment.find(class_='date'))
            yield ISSUE_TEXT.format(url=url, text=body, user=user, date=date)

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


class DummyIssue(object):

    def __init__(self, id_):
        self.id = id_
        self.summary = "Dummy issue"
        self.open = False
        self.labels = []
        self.target = ''
        self.body = ('Created in place of missing (most likely deleted)'
                     ' Google Code issue')
        self.comments = []


class DateFormatter(object):
    _full_date = re.compile('(\w{3}) (\d+), (\d{4})')
    _today = re.compile('Today \((\d+) hours? ago\)')
    _yesterday = re.compile('Yesterday \((\d+) hours? ago\)')
    _days_ago = re.compile('\w{3} \d+ \((\d+) days? ago\)')
    _format = '{day} {month} {year}'.format

    def format(self, date):
        for matcher, formatter in [
            (self._full_date, self._full_date_formatter),
            (self._today, self._today_formatter),
            (self._yesterday, self._yesterday_formatter),
            (self._days_ago, self._days_ago_formatter)
        ]:
            match = matcher.match(date)
            if match:
                return formatter(match)
        raise ValueError('Unknown date: %s' % date)

    def _full_date_formatter(self, match):
        month, day, year = match.groups()
        return self._format(**locals())

    def _today_formatter(self, match):
        return self._format_date_ago(hours=match.group(1))

    def _yesterday_formatter(self, match):
        return self._format_date_ago(hours=match.group(1))

    def _days_ago_formatter(self, match):
        return self._format_date_ago(days=match.group(1))

    def _format_date_ago(self, days=0, hours=0):
        dt = datetime.now() - timedelta(days=int(days), hours=int(hours))
        month = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][dt.month-1]
        return self._format(day=dt.day, month=month, year=dt.year)


def main(source_project, target_project, github_username, github_password,
         start_issue, issue_limit, id_sync):
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
        while issue.id > sync_id:
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
    gh = login(username, password=password)
    repo_owner, repo_name = target_project.split('/')
    return gh, gh.repository(repo_owner, repo_name)


def get_google_code_issues(project, start, issue_limit):
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
                issues.append(IssueTransfomer(project, *row[:6]))
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
    print msg


def insert_issue(repo, issue, milestone):
    github_issue = repo.create_issue(
        issue.summary, issue.body, labels=issue.labels,
        milestone=milestone)
    for comment in issue.comments:
        github_issue.create_comment(comment)
    if not issue.open:
        github_issue.close()
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
    parser.add_argument('--no-id-sync', action='store_true')
    args = parser.parse_args()

    main(args.source_project, args.target_project, args.github_username,
         args.github_password, args.start, args.limit, id_sync=not args.no_id_sync)
