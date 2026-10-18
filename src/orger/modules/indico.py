#!/usr/bin/env python3
"""
Events, agendas and attached files from Indico sites: a heading for each site, below it the categories, the events and their talks.
"""

from __future__ import annotations

import html
import re
import uuid
from datetime import date, datetime

from orger import Mirror
from orger.common import error, settings
from orger.inorganic import OrgNode, link, node, timestamp_with_style


def _dated(d: date | datetime | None, heading: str) -> str:
    """Put a timestamp in front of a heading, in the style that was asked for."""
    if d is None:
        return heading
    stamp = timestamp_with_style(d, settings.DEFAULT_TIMESTAMP_STYLE)
    return f'{stamp} {heading}' if stamp else heading


def _lines(*lines: str | None) -> str | None:
    kept = [x for x in lines if x]
    return '\n'.join(kept) if kept else None


def _custom_id(text: str) -> str:
    """
    >>> _custom_id('indico_fnal_e8406'), _custom_id('indico_a b_e1'), _custom_id('::')
    ('indico_fnal_e8406', 'indico_a_b_e1', 'x')
    """
    cleaned = re.sub(r'[^A-Za-z0-9_.-]+', '_', text).strip('_')
    return cleaned or 'x'


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


class _Branch:
    """A category with the categories and events below it."""

    def __init__(self, category) -> None:
        self.category = category
        self.branches: dict[int, _Branch] = {}
        self.events: list = []


class Indico(Mirror):
    def get_items(self) -> Mirror.Results:
        # imported here so that --help works without HPI
        import my.indico.export as src
        from my.indico.common import Category

        events = list(src.events())
        for site in src.instances():
            if isinstance(site, Exception):
                yield error(site)
                continue
            root = _Branch(None)
            for e in events:
                if isinstance(e, Exception):
                    continue
                if e.instance != site.name:
                    continue
                branch = root
                for depth, (number, title) in enumerate(e.category.path):
                    if number == 0:  # the root is the site itself
                        continue
                    if number not in branch.branches:
                        branch.branches[number] = _Branch(Category(e.instance, number, title, e.category.path[: depth + 1]))
                    branch = branch.branches[number]
                branch.events.append(e)
            properties = {'INDICO_URL': site.url}
            if site.username:
                properties['USERNAME'] = site.username
            yield node(
                heading=link(url=site.url, title=site.name),
                tags=['indico'],
                properties=_identified(site.url, f'indico_{_custom_id(site.name)}', properties),
                children=self._branch(root, site.url),
            )
        for e in events:
            if isinstance(e, Exception):
                yield error(e)

    def _branch(self, branch: _Branch, base: str) -> list[OrgNode]:
        children: list[OrgNode] = []
        for sub in sorted(branch.branches.values(), key=lambda b: b.category.title.casefold()):
            c = sub.category
            children.append(
                node(
                    heading=link(url=f'{base}/category/{c.id}/', title=c.title),
                    tags=['category'],
                    properties=_identified(
                        f'{base}/category/{c.id}/',
                        f'indico_{_custom_id(c.instance)}_cat{c.id}',
                        {'CATEGORY_ID': str(c.id)},
                    ),
                    children=self._branch(sub, base),
                )
            )
        far = datetime.min
        for e in sorted(branch.events, key=lambda e: (e.start.replace(tzinfo=None) if e.start else far, e.id), reverse=True):
            children.append(self._event(e, base))
        return children

    def _files(self, attachments, heading: str = 'Files:') -> str | None:
        if not attachments:
            return None
        items = []
        for a in attachments:
            if a.path is not None:
                label = link(url=f'file:{a.path}', title=a.filename or a.title)
                items.append(f'- {label} ({_size(a.size)}){"" if a.title == a.filename else " " + a.title}')
            elif a.url:
                extra = f' ({_size(a.size)}, not downloaded)' if a.kind == 'file' else ''
                items.append(f'- {link(url=a.url, title=a.title)}{extra}')
            else:
                items.append(f'- {a.title}')
        return '\n'.join([heading, *items])

    def _people(self, label: str, people) -> str | None:
        if not people:
            return None
        names = ', '.join(f'{p.name}{" (" + p.affiliation + ")" if p.affiliation else ""}' for p in people)
        return f'{label}: {names}'

    def _event(self, e, base: str) -> OrgNode:
        site = _custom_id(e.instance)
        properties = {'EVENT_ID': str(e.id), 'CATEGORY_ID': str(e.category.id)}
        if e.type:
            properties['TYPE'] = e.type
        if e.location:
            properties['LOCATION'] = e.location
        span = None
        if e.start:
            span = f'{e.start:%Y-%m-%d %H:%M}'
            if e.end:
                span += f' - {e.end:%Y-%m-%d %H:%M}' if e.end.date() != e.start.date() else f' - {e.end:%H:%M}'
            if e.timezone:
                span += f' {e.timezone}'
        place = ', '.join(x for x in (e.room, e.location, e.address and e.address.replace('\r\n', ', ').replace('\n', ', ')) if x)
        children = [self._contribution(c, site, base) for c in e.contributions]
        return node(
            heading=_dated(e.start, link(url=e.url, title=e.title)),
            tags=[t for t in (e.type, 'mine' if e.mine else None) if t],
            properties=_identified(f'{base}/event/{e.id}/', f'indico_{site}_e{e.id}', properties),
            body=_lines(
                span,
                place or None,
                self._people('Chairs', e.chairs),
                'Keywords: ' + ', '.join(e.keywords) if e.keywords else None,
                _description(e.description),
                self._files(e.attachments),
            ),
            children=children,
        )

    def _contribution(self, c, site: str, base: str) -> OrgNode:
        properties = {'CONTRIBUTION_ID': str(c.id)}
        if c.friendly_id is not None:
            properties['FRIENDLY_ID'] = str(c.friendly_id)
        if c.session:
            properties['SESSION'] = c.session
        if c.track:
            properties['TRACK'] = c.track
        if c.type:
            properties['TYPE'] = c.type
        identity = f'{base}/event/{c.event}/contributions/{c.id}/'
        children = [
            node(
                heading=s.title or f'sub-contribution {s.id}',
                properties=_identified(
                    f'{identity}#sub{s.id}', f'indico_{site}_e{c.event}_c{c.id}_s{s.id}', {'SUBCONTRIBUTION_ID': str(s.id)}
                ),
                body=_lines(self._people('Speakers', s.speakers), _description(s.description), self._files(s.attachments)),
            )
            for s in c.subcontributions
        ]
        when = c.start
        heading = link(url=c.url, title=c.title) if c.url else c.title
        if when is not None and c.duration:
            heading = f'{heading} ({c.duration} min)'
        return node(
            heading=_dated(when, heading),
            tags=['mine'] if c.mine else [],
            properties=_identified(identity, f'indico_{site}_e{c.event}_c{c.id}', properties),
            body=_lines(
                self._people('Speakers', c.speakers),
                self._people('Authors', c.authors),
                self._people('Co-authors', c.coauthors),
                _description(c.description),
                self._files(c.attachments),
            ),
            children=children,
        )


if __name__ == '__main__':
    Indico.main()
