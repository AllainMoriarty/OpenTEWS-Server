Migration workflow

Run Alembic commands from the project root using the config inside migrations.

Upgrade to latest revision:
  alembic -c migrations/alembic.ini upgrade head

Downgrade one revision:
  alembic -c migrations/alembic.ini downgrade -1

Create a new revision with autogenerate:
  alembic -c migrations/alembic.ini revision --autogenerate -m "your message"
