from typing import TYPE_CHECKING

from .org_view import Mirror, OrgWithKey, Queue
from .tree import Tree

__all__ = [
    'Mirror',
    'OrgWithKey',
    'Queue',
    'Tree',
]


if not TYPE_CHECKING:
    # TODO deprecate properly?
    InteractiveView = Queue
    StaticView = Mirror
