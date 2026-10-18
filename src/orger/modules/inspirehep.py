#!/usr/bin/env python3
"""
An author's data from InspireHEP: profile, employment, education, experiments, papers, seminars and students.
"""

from __future__ import annotations

from datetime import date, datetime

from orger import Mirror
from orger.common import error, settings
from orger.inorganic import link, node, timestamp_with_style


def _dated(d: date | datetime | None, heading: str) -> str:
    """Put a timestamp in front of a heading, in the style that was asked for."""
    if d is None:
        return heading
    stamp = timestamp_with_style(d, settings.DEFAULT_TIMESTAMP_STYLE)
    return f'{stamp} {heading}' if stamp else heading


def _span(start: str | None, end: str | None, *, current: bool = False) -> str:
    """
    >>> _span('2000', None, current=True), _span('1993', '2000'), _span(None, None)
    ('2000 - present', '1993 - 2000', '')
    """
    if start is None and end is None:
        return ''
    return f'{start or "?"} - {end or ("present" if current else "?")}'


def _lines(*lines: str | None) -> str | None:
    kept = [x for x in lines if x]
    return '\n'.join(kept) if kept else None


# what InspireHEP calls a rank, said the way people do
_RANKS = {'PHD': 'PhD', 'MASTER': 'Master', 'UNDERGRADUATE': 'Undergraduate', 'POSTDOC': 'Postdoc'}

# an author's identifiers go in properties under these names; the others get their schema's name
_ID_PROPERTIES = {'ARXIV': 'ARXIV_AUTHOR_ID', 'INSPIRE ID': 'INSPIRE_ID', 'INSPIRE BAI': 'INSPIRE_BAI'}


def _rank(rank: str) -> str:
    return _RANKS.get(rank, rank.capitalize())


class Inspirehep(Mirror):
    def get_items(self) -> Mirror.Results:
        # imported here so that --help works without HPI
        import my.inspirehep.export as src

        yield from self._profile(src)
        yield from self._experiments(src)
        yield from self._papers(src)
        yield from self._seminars(src)
        yield from self._students(src)

    # each group of the file is a node with the records as its children

    def _profile(self, src) -> Mirror.Results:
        me = src.profile()
        if isinstance(me, Exception):
            yield error(me)
            return
        summary = src.citation_summary()
        properties = {'INSPIRE_RECID': str(me.recid)}
        for schema, values in me.ids.items():
            properties[_ID_PROPERTIES.get(schema, schema.upper().replace(' ', '_'))] = ', '.join(values)
        if me.status:
            properties['STATUS'] = me.status

        facts = [link(url=me.url, title='InspireHEP profile')]
        facts += [link(url=u, title=u) for u in me.urls]
        facts += [f'Email: {e}' for e in me.emails]
        if me.arxiv_categories:
            facts.append('arXiv categories: ' + ', '.join(me.arxiv_categories))
        if not isinstance(summary, Exception):
            facts.append(
                f'{summary.papers} citeable papers, {summary.citations} citations, '
                f'h-index {summary.h_index} ({summary.published_h_index} counting published papers only)'
            )
        else:
            yield error(summary)

        children = []
        for p in src.employment():
            if isinstance(p, Exception):
                children.append(error(p))
                continue
            children.append(self._position(p))
        employment = node(heading='Employment', children=children) if children else None

        children = []
        for p in src.education():
            if isinstance(p, Exception):
                children.append(error(p))
                continue
            children.append(self._position(p))
        for a in src.advisors():
            if isinstance(a, Exception):
                children.append(error(a))
                continue
            children.append(
                node(
                    heading=(
                        link(url=f'https://inspirehep.net/authors/{a.recid}', title=a.name) if a.recid else a.name
                    ),
                    properties={'DEGREE': a.degree_type} if a.degree_type else None,
                    body='Advisor',
                )
            )
        education = node(heading='Education', children=children) if children else None

        yield node(
            heading=me.preferred_name or me.name,
            tags=['profile'],
            properties=properties,
            body='\n'.join(facts),
            children=[c for c in (employment, education) if c is not None],
        )

    def _position(self, p):
        place = f'{p.institution}'
        if p.rank:
            place += f', {_rank(p.rank)}'
        return node(
            heading=place,
            tags=['current'] if p.current else [],
            properties={'RANK': p.rank} if p.rank else None,
            body=_lines(_span(p.start, p.end, current=p.current), p.location),
        )

    def _experiments(self, src) -> Mirror.Results:
        children = []
        for c in src.collaborations():
            if isinstance(c, Exception):
                children.append(error(c))
                continue
            exp = c.experiment
            title = (exp.long_name if exp and exp.long_name else None) or c.name
            heading = link(url=exp.url, title=title) if exp else title
            papers = None
            if c.papers:
                papers = f'{c.papers} of the author\'s papers, {c.first.year if c.first else "?"} - {c.last.year if c.last else "?"}'
            properties = {'COLLABORATION': c.name}
            if exp:
                properties['EXPERIMENT'] = exp.name
                properties['INSPIRE_RECID'] = str(exp.recid)
            children.append(
                node(
                    heading=heading,
                    tags=['current'] if c.current else [],
                    properties=properties,
                    body=_lines(papers, exp.description if exp else None),
                )
            )
        if children:
            yield node(heading=f'Experiments and collaborations ({len(children)})', children=children)

    def _papers(self, src) -> Mirror.Results:
        children = []
        for p in src.papers():
            if isinstance(p, Exception):
                children.append(error(p))
                continue
            properties = {'INSPIRE_RECID': str(p.recid), 'CITATIONS': str(p.citations)}
            if p.arxiv_id:
                properties['ARXIV'] = p.arxiv_id
            if p.dois:
                properties['DOI'] = p.dois[0]
            if p.texkey:
                properties['TEXKEY'] = p.texkey
            if p.journal:
                properties['JOURNAL'] = p.journal
            authors = None
            if p.first_author:
                more = f' and {p.author_count - 1} others' if p.author_count > 1 else ''
                authors = f'{p.first_author}{more}'
            links = [link(url=p.url, title='InspireHEP')]
            if p.arxiv_url:
                links.append(link(url=p.arxiv_url, title='arXiv'))
            if p.doi_url:
                links.append(link(url=p.doi_url, title='DOI'))
            children.append(
                node(
                    heading=_dated(p.date, link(url=p.doi_url or p.arxiv_url or p.url, title=p.title)),
                    tags=[*p.document_types, *p.collaborations],
                    properties=properties,
                    body=_lines(
                        authors,
                        p.journal,
                        f'{p.citations} citations ({p.citations_without_self} without self-citations)',
                        ' '.join(links),
                        p.abstract,
                    ),
                )
            )
        if children:
            yield node(heading=f'Publications ({len(children)})', children=children)

    def _seminars(self, src) -> Mirror.Results:
        children = []
        for s in src.seminars():
            if isinstance(s, Exception):
                children.append(error(s))
                continue
            children.append(
                node(
                    heading=_dated(s.start, link(url=s.url, title=s.title)),
                    tags=['seminar'],
                    properties={'SERIES': ', '.join(s.series)} if s.series else None,
                    body=_lines(s.venue, s.abstract),
                )
            )
        if children:
            yield node(heading=f'Seminars ({len(children)})', children=children)

    def _students(self, src) -> Mirror.Results:
        children = []
        for s in src.students():
            if isinstance(s, Exception):
                children.append(error(s))
                continue
            children.append(
                node(
                    heading=link(url=s.url, title=s.name),
                    properties={'DEGREE': s.degree_type} if s.degree_type else None,
                    body=_lines(s.institution, _span(s.start, s.end)),
                )
            )
        if children:
            yield node(heading=f'Students ({len(children)})', children=children)


if __name__ == '__main__':
    Inspirehep.main()
