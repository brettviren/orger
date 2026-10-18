#!/usr/bin/env python3
"""
Events, agendas and attached files from Indico sites, as a directory of org files: agenda files of what is coming up, and a page for each category and each event.

The directory, with N the event number divided by a thousand:

    agenda/favorites.org      events coming up in the categories the user has favorited
    agenda/managing.org       the same for the categories the user manages
    presentations/index.org
    presentations/SITE/index.org
    presentations/SITE/category/CATEGORY.org
    presentations/SITE/event/N/EVENT/event.org          the event and its talks
    presentations/SITE/event/N/EVENT/aATTACHMENT-NAME.org   text of an attached file (made by `orgface convert indico`)

The paths of the files made here depend on nothing but the numbers the site gives, so they do not change
as titles are edited, categories moved or more of a site is kept.  Which category an event is in is said by the
category pages, not by the directory it is in.
"""

from __future__ import annotations

import html
import posixpath
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from orger import Tree
from orger.common import error
from orger.inorganic import OrgNode, link, node

# how many days ahead the agenda files look
HORIZON = 60

_DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')  # org wants these, whatever the locale


# -- the layout, which orgface convert shares


def _custom_id(text: str) -> str:
    """
    >>> _custom_id('indico_fnal_e8406'), _custom_id('indico_a b_e1'), _custom_id('::')
    ('indico_fnal_e8406', 'indico_a_b_e1', 'x')
    """
    cleaned = re.sub(r'[^A-Za-z0-9_.-]+', '_', text).strip('_')
    return cleaned or 'x'


def event_directory(instance: str, event: int) -> str:
    """
    Where the files of an event are, relative to the output directory.

    >>> event_directory('fnal', 76235)
    'presentations/fnal/event/0076/76235'
    >>> event_directory('bnl', 7)
    'presentations/bnl/event/0000/7'
    """
    return f'presentations/{instance}/event/{event // 1000:04d}/{event}'


def event_file(instance: str, event: int) -> str:
    return f'{event_directory(instance, event)}/event.org'


def category_file(instance: str, category: int) -> str:
    return f'presentations/{instance}/category/{category}.org'


def site_file(instance: str) -> str:
    return f'presentations/{instance}/index.org'


def text_file(instance: str, event: int, attachment: int, filename: str | None) -> str:
    """
    Where the text of an attached file is, if there is any.

    >>> text_file('fnal', 8406, 84044, 'Worch Talk (v2).PDF')
    'presentations/fnal/event/0008/8406/a84044-worch-talk-v2.org'
    >>> text_file('fnal', 8406, 5, None)
    'presentations/fnal/event/0008/8406/a5-file.org'
    """
    stem = (filename or '').rsplit('.', 1)[0] if filename and '.' in filename else (filename or '')
    slug = re.sub(r'[^a-z0-9]+', '-', stem.lower()).strip('-')[:40].strip('-') or 'file'
    return f'{event_directory(instance, event)}/a{attachment}-{slug}.org'


def _relative(here: str, there: str) -> str:
    """
    The path of one file of the output from another.

    >>> _relative('agenda/favorites.org', 'presentations/fnal/index.org')
    '../presentations/fnal/index.org'
    >>> _relative('presentations/fnal/category/5.org', 'presentations/fnal/category/6.org')
    '6.org'
    """
    return posixpath.relpath(there, posixpath.dirname(here))


def _org_id(identity: str) -> str:
    """
    An org ID that stays the same every time the file is made, because it comes from what the entry is.

    >>> _org_id('https://x/event/1/') == _org_id('https://x/event/1/')
    True
    >>> _org_id('a') != _org_id('b')
    True
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


def _identified(identity: str, custom: str, properties: dict[str, str] | None = None) -> dict[str, str]:
    """The properties that let other files link to an entry, then the entry's own."""
    return {'ID': _org_id(identity), 'CUSTOM_ID': custom, **(properties or {})}


# -- small things


def _lines(*lines: str | None) -> str | None:
    kept = [x for x in lines if x]
    return '\n'.join(kept) if kept else None


def _size(n: int | None) -> str:
    """
    >>> _size(None), _size(999), _size(15_300), _size(2_400_000)
    ('', '999 B', '15 kB', '2.4 MB')
    """
    if n is None:
        return ''
    if n < 1000:
        return f'{n} B'
    if n < 1_000_000:
        return f'{n / 1000:.0f} kB'
    if n < 1_000_000_000:
        return f'{n / 1_000_000:.1f} MB'
    return f'{n / 1_000_000_000:.1f} GB'


def _plain(text: str) -> str:
    """
    What Indico gives as HTML, without the markup, for when pandoc is not to be used.

    >>> _plain('<p>Hello&nbsp;<b>there</b></p>  <p>again</p>')
    'Hello there\\n\\nagain'
    """
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|li|h\d)>', '\n\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    lines = [line.strip() for line in html.unescape(text).replace('\xa0', ' ').splitlines()]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def _description(text: str) -> str | None:
    """An Indico description is HTML, or plain text with newlines."""
    if not text.strip():
        return None
    if not re.search(r'<[a-zA-Z/][^>]*>', text):
        return text.strip()
    from orger.pandoc import should_use_pandoc, to_org

    return to_org(text, from_='html').strip() if should_use_pandoc() else _plain(text)


def _short(text: str, n: int = 400) -> str | None:
    """
    >>> _short('<p>One <b>two</b></p>'), _short('x' * 500)[-4:]
    ('One two', ' ...')
    """
    plain = ' '.join(_plain(text).split())
    if not plain:
        return None
    return plain if len(plain) <= n else plain[:n].rsplit(' ', 1)[0] + ' ...'


def _day(d: date | datetime) -> str:
    """
    >>> _day(date(2026, 10, 21))
    '2026-10-21 Wed'
    """
    return f'{d:%Y-%m-%d} {_DAYS[d.weekday()]}'


def _when(start: datetime, end: datetime | None) -> str:
    """
    An active timestamp, or a range of them, for org-agenda, in the time zone of the computer.
    """
    s = start.astimezone()
    e = end.astimezone() if end else None
    if e is None or e == s:
        return f'<{_day(s)} {s:%H:%M}>'
    if e.date() == s.date():
        return f'<{_day(s)} {s:%H:%M}-{e:%H:%M}>'
    return f'<{_day(s)} {s:%H:%M}>--<{_day(e)} {e:%H:%M}>'


def _people(label: str, people) -> str | None:
    if not people:
        return None
    names = ', '.join(f'{p.name}{" (" + p.affiliation + ")" if p.affiliation else ""}' for p in people)
    return f'{label}: {names}'


def _home(path: Path) -> str:
    """A path written with ~ if it is in the home directory, as it may then be the same on another computer."""
    try:
        return '~/' + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return path.as_posix()


# -- what is in the tree


class _Category:
    """A category as the events show it, with the categories and events below."""

    def __init__(self, instance: str, number: int, title: str) -> None:
        self.instance = instance
        self.id = number
        self.title = title
        self.parent: int | None = None
        self.children: set[int] = set()
        self.events: list = []


class IndicoTree(Tree):
    def get_files(self) -> Tree.Files:
        # imported here so that --help works without HPI
        import my.indico.export as src

        self.events: list = []
        problems: list = []
        for e in src.events():
            (problems if isinstance(e, Exception) else self.events).append(e)
        self.sites = {s.name: s for s in src.instances() if not isinstance(s, Exception)}
        self.favorites = [c for c in src.favorite_categories() if not isinstance(c, Exception)]
        self.managed = [c for c in src.managed_categories() if not isinstance(c, Exception)]
        self.categories = self._categories()

        yield from self._agenda()
        yield from self._presentations(problems)

    def _exists(self, relative: str) -> bool:
        return self.output is not None and (self.output / relative).is_file()

    # -- agenda

    def _agenda(self) -> Tree.Files:
        horizon = int(getattr(self.args, 'horizon', None) or HORIZON)
        today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        first, last = today - timedelta(days=1), today + timedelta(days=horizon + 1)
        for name, categories in (('favorites', self.favorites), ('managing', self.managed)):
            wanted: dict[str, set[int]] = defaultdict(set)
            for c in categories:
                wanted[c.instance].add(c.id)
            items = []
            for e in sorted(self.events, key=lambda e: (e.start is None, e.start)):
                if e.start is None or not e.within(wanted.get(e.instance, ())):
                    continue
                if e.start >= last or (e.end or e.start) < first:
                    continue
                items.append(self._agenda_item(name, e))
            yield (
                f'agenda/{name}.org',
                f'#+title: Indico: {name}\n#+category: Indico\n#+filetags: :indico:\n',
                items,
            )

    def _agenda_item(self, which: str, e) -> OrgNode:
        here = f'agenda/{which}.org'
        site = _custom_id(e.instance)
        path = ' » '.join(title for _, title in e.category.path if title and title != 'Home')
        page = None
        if e.has_agenda:
            page = f'[[file:{_relative(here, event_file(e.instance, e.id))}::#indico_{site}_e{e.id}][event page]]'
        place = ', '.join(x for x in (e.room, e.location) if x)
        properties = {'EVENT_ID': str(e.id), 'CATEGORY': path}
        if place:
            properties['LOCATION'] = place
        return node(
            heading=f'{_when(e.start, e.end)} {link(url=e.url, title=e.title)}',
            tags=[t for t in (e.instance, e.type, 'mine' if e.mine else None) if t],
            properties=_identified(
                f'{e.url}#agenda-{which}', f'indico_{site}_agenda_{which}_e{e.id}', properties
            ),
            body=_lines(place or None, _people('Chairs', e.chairs), _short(e.description), page),
        )

    # -- presentations

    def _categories(self) -> dict[tuple[str, int], _Category]:
        found: dict[tuple[str, int], _Category] = {}

        def lay(instance: str, path, event=None) -> None:
            steps = [(n, t) for n, t in path if n != 0]  # the root is the site
            parent = None
            for number, title in steps:
                cat = found.setdefault((instance, number), _Category(instance, number, title))
                if title and (not cat.title or cat.title == str(number)):
                    cat.title = title
                if parent is not None:
                    cat.parent = parent.id
                    parent.children.add(number)
                parent = cat
            if parent is not None and event is not None:
                parent.events.append(event)

        for e in self.events:
            lay(e.instance, e.category.path, e)
        for c in [*self.favorites, *self.managed]:
            lay(c.instance, c.path)
        return found

    def _below(self, cat: _Category, seen: set | None = None) -> list:
        """The events in a category and in the categories below it."""
        seen = set() if seen is None else seen
        found = list(cat.events)
        for number in sorted(cat.children):
            child = self.categories.get((cat.instance, number))
            if child is not None and number not in seen:
                seen.add(number)
                found += self._below(child, seen)
        return found

    def _presentations(self, problems) -> Tree.Files:
        by_site: dict[str, list] = defaultdict(list)
        for e in self.events:
            by_site[e.instance].append(e)

        yield (
            'presentations/index.org',
            '#+title: Indico\n#+filetags: :indico:\n',
            [
                node(
                    heading=link(url=site.url, title=site.name),
                    properties=_identified(f'{site.url}#presentations', f'indico_{_custom_id(site.name)}_presentations'),
                    body=_lines(
                        f'[[file:{_relative("presentations/index.org", site_file(site.name))}][pages of {site.name}]]',
                        f'{len(by_site.get(site.name, ()))} events, '
                        f'{sum(e.has_agenda for e in by_site.get(site.name, ()))} with agendas',
                    ),
                )
                for site in self.sites.values()
            ],
        )
        for site in self.sites.values():
            yield self._site_index(site, by_site.get(site.name, []))
        for (instance, _), cat in sorted(self.categories.items()):
            if instance in self.sites:
                yield self._category_page(self.sites[instance], cat)
        for e in self.events:
            if e.has_agenda and e.instance in self.sites:
                yield self._event_page(self.sites[e.instance], e)
        if problems:
            yield (
                'presentations/errors.org',
                '#+title: Indico: what could not be read\n',
                [error(p) for p in problems],
            )

    def _event_link(self, here: str, e) -> str:
        if e.has_agenda:
            return link(
                url=f'file:{_relative(here, event_file(e.instance, e.id))}::#indico_{_custom_id(e.instance)}_e{e.id}',
                title=e.title,
            )
        return link(url=e.url, title=e.title)

    def _event_line(self, here: str, e) -> str:
        what = ', '.join(x for x in (e.type, 'mine' if e.mine else None) if x)
        stamp = f'[{_day(e.start)}]' if e.start else '[?]'
        return f'- {stamp} {self._event_link(here, e)}' + (f' :: {what}' if what else '')

    def _category_link(self, here: str, instance: str, number: int) -> str:
        cat = self.categories.get((instance, number))
        title = cat.title if cat else str(number)
        if cat is None:
            return title
        return link(url=f'file:{_relative(here, category_file(instance, number))}', title=title)

    def _site_index(self, site, events: list) -> tuple[str, str, list[OrgNode]]:
        here = site_file(site.name)
        key = _custom_id(site.name)
        far = datetime.min.replace(tzinfo=None)

        def newest(es):
            return sorted(es, key=lambda e: e.start.replace(tzinfo=None) if e.start else far, reverse=True)

        def categories(label: str, found) -> OrgNode | None:
            mine = [c for c in found if c.instance == site.name]
            if not mine:
                return None
            lines = []
            for c in mine:
                cat = self.categories.get((site.name, c.id))
                count = len(self._below(cat)) if cat else 0
                lines.append(f'- {self._category_link(here, site.name, c.id)} ({count} events)')
            return node(heading=f'{label} ({len(mine)})', body='\n'.join(lines))

        top = sorted(
            (c for (i, _), c in self.categories.items() if i == site.name and c.parent is None),
            key=lambda c: c.title.casefold(),
        )
        mine_events = newest(e for e in events if e.mine)
        children = [
            node(
                heading=f'Mine ({len(mine_events)})',
                body='\n'.join(self._event_line(here, e) for e in mine_events[:200]) or None,
            ),
            categories('Favorite categories', self.favorites),
            categories('Managed categories', self.managed),
            node(
                heading=f'Categories ({len(top)})',
                body='\n'.join(
                    f'- {self._category_link(here, site.name, c.id)} ({len(self._below(c))} events)' for c in top
                )
                or None,
            ),
            node(
                heading='Recent events with agendas',
                body='\n'.join(self._event_line(here, e) for e in newest(e for e in events if e.has_agenda)[:30]) or None,
            ),
        ]
        root = node(
            heading=link(url=site.url, title=site.name),
            tags=['indico'],
            properties=_identified(site.url, f'indico_{key}', {'INDICO_URL': site.url}),
            body=f'{len(events)} events seen, {sum(e.has_agenda for e in events)} with agendas',
            children=[c for c in children if c is not None],
        )
        return here, f'#+title: {site.name}\n#+filetags: :indico:{key}:\n', [root]

    def _category_page(self, site, cat: _Category) -> tuple[str, str, list[OrgNode]]:
        here = category_file(site.name, cat.id)
        key = _custom_id(site.name)
        trail = []
        up = cat.parent
        while up is not None and (site.name, up) in self.categories:
            trail.append(self._category_link(here, site.name, up))
            up = self.categories[(site.name, up)].parent
        subs = sorted(
            (self.categories[(site.name, n)] for n in cat.children if (site.name, n) in self.categories),
            key=lambda c: c.title.casefold(),
        )
        far = datetime.min
        events = sorted(cat.events, key=lambda e: e.start.replace(tzinfo=None) if e.start else far, reverse=True)
        years: dict[str, list] = defaultdict(list)
        for e in events:
            years[str(e.start.year) if e.start else 'undated'].append(e)
        children = []
        if subs:
            children.append(
                node(
                    heading=f'Categories ({len(subs)})',
                    body='\n'.join(
                        f'- {self._category_link(here, site.name, c.id)} ({len(self._below(c))} events)' for c in subs
                    ),
                )
            )
        children.append(
            node(
                heading=f'Events ({len(events)})',
                children=[
                    node(heading=f'{year} ({len(es)})', body='\n'.join(self._event_line(here, e) for e in es))
                    for year, es in years.items()
                ],
            )
        )
        root = node(
            heading=link(url=f'{site.url}/category/{cat.id}/', title=cat.title),
            tags=['category'],
            properties=_identified(
                f'{site.url}/category/{cat.id}/', f'indico_{key}_cat{cat.id}', {'CATEGORY_ID': str(cat.id)}
            ),
            body=_lines(
                ' « '.join([*trail, f'[[file:{_relative(here, site_file(site.name))}][{site.name}]]']),
                f'{len(self._below(cat))} events in it and below',
            ),
            children=children,
        )
        return here, f'#+title: {cat.title} ({site.name})\n#+filetags: :indico:{key}:\n', [root]

    # -- one event

    def _attach_directory(self, e) -> Path | None:
        """The directory the downloaded files of an event are in, if they are all in one."""
        found: set[Path] = set()
        for a in [*e.attachments, *(a for c in e.contributions for a in c.all_attachments)]:
            if a.path is not None:
                found.add(a.path.parent.parent)  # .../eEVENT/aATTACHMENT-CHECKSUM/NAME
        return found.pop() if len(found) == 1 else None

    def _event_page(self, site, e) -> tuple[str, str, list[OrgNode]]:
        here = event_file(e.instance, e.id)
        key = _custom_id(e.instance)
        attach = self._attach_directory(e)
        properties = {'EVENT_ID': str(e.id), 'CATEGORY_ID': str(e.category.id)}
        if e.type:
            properties['TYPE'] = e.type
        if e.location:
            properties['LOCATION'] = e.location
        if attach is not None:
            properties['ATTACH_DIR'] = _home(attach)
            properties['ATTACH_DIR_INHERIT'] = 't'
        span = None
        if e.start:
            span = f'{e.start:%Y-%m-%d %H:%M}'
            if e.end:
                span += f' - {e.end:%Y-%m-%d %H:%M}' if e.end.date() != e.start.date() else f' - {e.end:%H:%M}'
            if e.timezone:
                span += f' {e.timezone}'
        place = ', '.join(x for x in (e.room, e.location, e.address and e.address.replace('\r\n', ', ').replace('\n', ', ')) if x)
        trail = ' » '.join(
            self._category_link(here, e.instance, n) for n, _ in e.category.path if n != 0
        )
        root = node(
            heading=_dated_heading(e.start, link(url=e.url, title=e.title)),
            tags=[t for t in (e.type, 'mine' if e.mine else None) if t],
            properties=_identified(
                f'{site.url}/event/{e.id}/', f'indico_{key}_e{e.id}', properties
            ),
            body=_lines(
                span,
                place or None,
                trail or None,
                _people('Chairs', e.chairs),
                'Keywords: ' + ', '.join(e.keywords) if e.keywords else None,
                _description(e.description),
                self._files(here, e.attachments, attach),
            ),
            children=[self._talk(site, c, here, key, attach) for c in e.contributions],
        )
        return here, f'#+title: {e.title}\n#+filetags: :indico:{key}:\n', [root]

    def _files(self, here: str, attachments, attach: Path | None) -> str | None:
        if not attachments:
            return None
        return '\n'.join(['Files:', *(self._file(here, a, attach) for a in attachments)])

    def _file(self, here: str, a, attach: Path | None) -> str:
        if a.kind == 'link':
            return f'- {link(url=a.url, title=a.title)}' if a.url else f'- {a.title}'
        name = a.filename or a.title
        parts = []
        text = text_file(a.instance, a.event, a.id, a.filename)
        if self._exists(text):
            parts.append(link(url=f'file:{_relative(here, text)}', title='text'))
        if a.path is not None:
            if attach is not None and attach in a.path.parents:
                parts.append(link(url=f'attachment:{a.path.relative_to(attach).as_posix()}', title='file'))
            else:
                parts.append(link(url=f'file:{_home(a.path)}', title='file'))
        if a.url:
            parts.append(link(url=a.url, title='site'))
        size = f' ({_size(a.size)})' if a.size is not None else ''
        extra = '' if a.path is not None else ' not downloaded'
        return f'- {name}{size}{extra} :: ' + ' '.join(parts) if parts else f'- {name}{size}{extra}'

    def _talk(self, site, c, here: str, key: str, attach: Path | None) -> OrgNode:
        properties = {'CONTRIBUTION_ID': str(c.id)}
        if c.friendly_id is not None:
            properties['FRIENDLY_ID'] = str(c.friendly_id)
        if c.session:
            properties['SESSION'] = c.session
        if c.track:
            properties['TRACK'] = c.track
        if c.type:
            properties['TYPE'] = c.type
        identity = f'{site.url}/event/{c.event}/contributions/{c.id}/'
        heading = link(url=c.url, title=c.title) if c.url else c.title
        if c.start is not None and c.duration:
            heading = f'{heading} ({c.duration} min)'
        subs = [
            node(
                heading=s.title or f'sub-contribution {s.id}',
                properties=_identified(
                    f'{identity}#sub{s.id}',
                    f'indico_{key}_e{c.event}_c{c.id}_s{s.id}',
                    {'SUBCONTRIBUTION_ID': str(s.id)},
                ),
                body=_lines(
                    _people('Speakers', s.speakers), _description(s.description), self._files(here, s.attachments, attach)
                ),
            )
            for s in c.subcontributions
        ]
        return node(
            heading=_dated_heading(c.start, heading),
            tags=['mine'] if c.mine else [],
            properties=_identified(identity, f'indico_{key}_e{c.event}_c{c.id}', properties),
            body=_lines(
                _people('Speakers', c.speakers),
                _people('Authors', c.authors),
                _people('Co-authors', c.coauthors),
                _description(c.description),
                self._files(here, c.attachments, attach),
            ),
            children=subs,
        )


def _dated_heading(d: datetime | None, heading: str) -> str:
    """Put a timestamp in front of a heading, in the style that was asked for."""
    from orger.common import settings
    from orger.inorganic import timestamp_with_style

    if d is None:
        return heading
    stamp = timestamp_with_style(d, settings.DEFAULT_TIMESTAMP_STYLE)
    return f'{stamp} {heading}' if stamp else heading


if __name__ == '__main__':
    IndicoTree.main()
