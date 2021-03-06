import asyncio
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from typing import Awaitable, Callable

import sqlalchemy as sa
from sqlalchemy.ext.declarative import as_declarative, declared_attr
from sqlalchemy.orm import scoped_session, sessionmaker
from starlette.requests import Request
from starlette.responses import Response

from .config import config
from .utils import threadpool

NEXT = Callable[[Request], Awaitable[Response]]


def scopefunc() -> int:
    return _session_ctx.get()


engine = sa.create_engine(
    config.DATABASE_URL,
    pool_size=config.DATABASE_POOL_SIZE,
    pool_recycle=config.DATABASE_POOL_RECYCLE,
    max_overflow=config.THREAD_POOL_SIZE,
)

_session_factory = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False
)
_session_ctx = ContextVar('session_scope', default=0)

session = scoped_session(_session_factory, scopefunc=scopefunc)


async def session_middleware(request: Request, call_next: NEXT) -> Response:
    _session_ctx.set(id(asyncio.current_task()))
    try:
        return await call_next(request)
    finally:
        await _reset_session()


@threadpool
def _reset_session() -> None:
    session.remove()


@as_declarative()
class Base:
    query = session.query_property()

    id = sa.Column(sa.Integer, primary_key=True)
    created = sa.Column(sa.DateTime, index=True, default=datetime.utcnow)

    @declared_attr
    def __tablename__(cls) -> str:  # noqa:N805
        return cls.__name__.lower()  # type: ignore


class DeletableMixin:
    deleted = sa.Column(sa.DateTime, index=True)

    def delete(self) -> None:
        self.deleted = datetime.utcnow()


class User(Base):
    email = sa.Column(sa.String, unique=True)
    password_hash = sa.Column(sa.String, nullable=False)

    persons = sa.orm.relationship(
        'Person', backref='user', order_by='Person.balance'
    )

    def __init__(self, email: str, password_hash: str) -> None:
        self.email = email
        self.password_hash = password_hash


class Person(Base, DeletableMixin):
    name = sa.Column(sa.String, nullable=False, index=True)
    balance = sa.Column(sa.DECIMAL, nullable=False, default=0)
    user_id = sa.Column(sa.ForeignKey(User.id), nullable=False)

    operations = sa.orm.relationship(
        'Operation', backref='person', order_by='Operation.created'
    )

    __table_args__ = (sa.UniqueConstraint('user_id', 'name'),)

    def __init__(self, user_id: int, name: str, balance: Decimal) -> None:
        self.user_id = user_id
        self.name = name
        self.balance = balance


class Operation(Base, DeletableMixin):
    value = sa.Column(sa.DECIMAL, nullable=False)
    description = sa.Column(sa.String, nullable=False)
    person_id = sa.Column(sa.ForeignKey(Person.id), nullable=False)

    def __init__(
        self, person_id: int, value: Decimal, description: str
    ) -> None:
        self.person_id = person_id
        self.value = value
        self.description = description
