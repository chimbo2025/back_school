from django.apps import AppConfig
from django.db.backends.signals import connection_created


def sqlite_pragmas(sender, connection, **kwargs):
    """Tune SQLite for concurrent reads/writes.

    WAL lets readers continue while a writer is active, and busy_timeout makes
    writers wait for the lock instead of failing with "database is locked".
    """
    if connection.vendor != 'sqlite':
        return
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA journal_mode=WAL;')
        cursor.execute('PRAGMA synchronous=NORMAL;')
        cursor.execute('PRAGMA busy_timeout=20000;')


class AdmissionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'admissions'

    def ready(self):
        connection_created.connect(
            sqlite_pragmas,
            dispatch_uid='admissions.sqlite_pragmas',
        )
